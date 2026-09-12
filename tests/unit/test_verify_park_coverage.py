"""Behavior coverage for the M-VERIFY park chain (``ExecVerifyParkMixin``).

Drives the decide()->None park evidence chain, freeze/re-freeze idempotency,
CI/security stops, repair-round budget and known-issue two-drive
registration, per-task waiver refs, preview assembly and the preview/regen
gates directly on a bare mixin host (FR-0286, FR-0287, IF-VERIFY-001/004,
IF-KNOWNISSUE-001, IF-RELEASE-002).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tests.unit.test_security_policy_identity import _contract, _entry, _scan
from tracks.executor import verify_park
from tracks.executor.m_verify import CandidateIdentity, FreezeBlocked
from tracks.executor.security import security_policy_digest
from tracks.executor.verify_park import ExecVerifyParkMixin
from tracks.kernel.events import Command
from tracks.kernel.machine import State


class _FakeStore:
    def __init__(self, events=(), state=None, home=None):
        self._events = list(events)
        self._state = state or State(run_id="RUN")
        self.home = home or Path("/tmp")
        self.blobs = {}
        self.appended = []

    def events(self, run_id):
        return list(self._events)

    def state(self, run_id):
        return self._state

    def append(self, event, payload=None, **kwargs):
        self._events.append(_ev(len(self._events) + 1, event, payload or {}))
        self.appended.append((event, payload))

    def write_audit_blob(self, payload):
        ref = f"blob-{len(self.blobs)}"
        self.blobs[ref] = payload
        return ref


class _Host(ExecVerifyParkMixin):
    def __init__(self, tmp_path: Path, *, events=(), state=None):
        self.repo = tmp_path / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.run_id = "RUN"
        self.store = _FakeStore(events, state, home=tmp_path / "home")
        self.emitted: list[tuple] = []
        self.calls: dict = {}

    def _emit(self, event, payload=None, **kwargs):
        self.emitted.append((event, payload, kwargs))
        self.store.append(event, payload or {}, **kwargs)

    def _latest_event(self, event_type):
        hits = [e for e in self.store.events(self.run_id) if e.type == event_type]
        return hits[-1] if hits else None

    def _next_event_seq(self):
        return 99

    def _emit_full_f_judgment(self, cmd, sha):
        self.calls.setdefault("full_f", []).append(sha)

    def _run_contract_gates(self, cmd, sha, state, resume=False):
        return self.calls.get("contract_gates", ("CONTRACT", "DIGEST"))

    def _verify_stop_class(self, sha):
        return "gate"

    def _assess_security_with_review(self, sha, contract, digest):
        return self.calls.get(
            "security_payload",
            {"status": "passed", "scans": [{"id": "s1", "status": "passed"}]},
        )

    def _readback_ci_binding(self, cmd, sha, ci):
        return self.calls.get("ci_payload", {"api_verified": True})

    def _repair_rewalk_allowed(self, frozen_sha):
        return self.calls.get("rewalk", False)

    def _load_or_default_contract(self, cmd, sha):
        return self.calls.get("contract", ("CONTRACT", "DIGEST", "source"))



def _ev(seq, type, payload=None, version=None):
    return SimpleNamespace(seq=seq, type=type, payload=payload or {}, version=version)


def _cmd(command_id="C-1", **params):
    return Command(kind="verify_cmd", params=dict(params), command_id=command_id)


def _m_impl_parked():
    return State(stage="M-IMPL", substate="DIAGNOSE", awaiting="escalation", current_attempt=1)


def _event_types(host):
    return [event for event, _ in host.store.appended]


# ---------------------------------------------------------------------------
# _verify_chain_park_evidence
# ---------------------------------------------------------------------------


def test_chain_requires_parked_m_impl_state(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(host, "_park_freeze_candidate", lambda cmd: None)
    host._verify_chain_park_evidence(State(stage="M-IMPL", substate="DIAGNOSE"))
    assert host.emitted == []
    host._verify_chain_park_evidence(_m_impl_parked())
    assert host.emitted == []  # freeze unresolved


def test_chain_stops_when_freeze_none(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(host, "_park_freeze_candidate", lambda cmd: None)
    host._verify_chain_park_evidence(_m_impl_parked())
    assert _event_types(host) == []


def test_chain_contract_refusal_routes_gate(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    routed = []
    monkeypatch.setattr(host, "_park_freeze_candidate", lambda cmd: "SHA")
    monkeypatch.setattr(host, "_run_contract_gates", lambda *a, **k: (None, None))
    monkeypatch.setattr(host, "_park_stop_reason", lambda event_type: "reason:local_gate.failed")
    monkeypatch.setattr(
        host,
        "_park_repair_route",
        lambda cmd, sha, kind, reason, registration_kind=None: routed.append(
            (sha, kind, reason, registration_kind)
        )
        or True,
    )
    monkeypatch.setattr(host, "_park_origin_repair_route", lambda *a: False)
    host._verify_chain_park_evidence(_m_impl_parked())
    assert routed == [("SHA", "gate", "reason:local_gate.failed", None)]
    assert host.calls.get("full_f") == ["SHA"]


def test_chain_ci_stop_returns_after_origin(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(host, "_park_freeze_candidate", lambda cmd: "SHA")
    monkeypatch.setattr(host, "_park_observe_ci", lambda *a: False)
    host._verify_chain_park_evidence(_m_impl_parked())
    assert "preview" not in host.calls


def test_chain_security_failed_routes_cve(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    routed = []
    monkeypatch.setattr(host, "_park_freeze_candidate", lambda cmd: "SHA")
    monkeypatch.setattr(host, "_park_observe_ci", lambda *a: True)
    monkeypatch.setattr(host, "_park_assess_security", lambda *a: False)
    monkeypatch.setattr(host, "_park_stop_payload", lambda event_type: {"status": "failed"})
    monkeypatch.setattr(host, "_park_stop_reason", lambda event_type: "reason:security.assessed")
    monkeypatch.setattr(
        host,
        "_park_repair_route",
        lambda cmd, sha, kind, reason, registration_kind=None: routed.append(
            (sha, kind, reason, registration_kind)
        )
        or True,
    )
    monkeypatch.setattr(host, "_park_origin_repair_route", lambda *a: False)
    host._verify_chain_park_evidence(_m_impl_parked())
    assert routed == [("SHA", "cve", "reason:security.assessed", "security_finding")]
    assert "preview" not in host.calls


def test_chain_security_unknown_stops_silently(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(host, "_park_freeze_candidate", lambda cmd: "SHA")
    monkeypatch.setattr(host, "_park_observe_ci", lambda *a: True)
    monkeypatch.setattr(host, "_park_assess_security", lambda *a: False)
    monkeypatch.setattr(host, "_park_stop_payload", lambda event_type: {})
    monkeypatch.setattr(host, "_park_origin_repair_route", lambda *a: False)
    host._verify_chain_park_evidence(_m_impl_parked())
    assert "preview" not in host.calls


def test_chain_green_runs_preview(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(host, "_park_freeze_candidate", lambda cmd: "SHA")
    monkeypatch.setattr(host, "_park_observe_ci", lambda *a: True)
    monkeypatch.setattr(host, "_park_assess_security", lambda *a: True)
    monkeypatch.setattr(host, "_park_origin_repair_route", lambda *a: False)
    previews = []
    monkeypatch.setattr(
        host, "_park_preview", lambda cmd, sha, contract, digest=None: previews.append((sha, contract, digest))
    )
    host._verify_chain_park_evidence(_m_impl_parked())
    assert previews == [("SHA", "DIGEST", None)]


# ---------------------------------------------------------------------------
# freeze
# ---------------------------------------------------------------------------


def test_try_freeze_success_and_blocked(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    identity = CandidateIdentity("sha", True, "main")
    monkeypatch.setattr(verify_park.m_verify, "freeze_candidate", lambda repo: identity)
    assert host._try_freeze(_cmd()) is identity

    monkeypatch.setattr(
        verify_park.m_verify,
        "freeze_candidate",
        lambda repo: (_ for _ in ()).throw(FreezeBlocked("dirty")),
    )
    assert host._try_freeze(_cmd()) is None
    assert host.emitted[-1][0] == "attention.required"
    assert host.emitted[-1][1]["reason"] == "dirty_tree"


def test_park_freeze_first_bind(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(
        host, "_try_freeze", lambda cmd: CandidateIdentity("NEW", True, "main")
    )
    assert host._park_freeze_candidate(_cmd()) == "NEW"
    frozen = host.emitted[-1]
    assert frozen[0] == "candidate.frozen"
    assert frozen[1]["frozen_at_seq"] == 99


def test_park_freeze_try_freeze_none_returns_none(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(host, "_try_freeze", lambda cmd: None)
    assert host._park_freeze_candidate(_cmd()) is None
    assert host.emitted == []


def test_park_freeze_same_sha_is_idempotent(tmp_path, monkeypatch):
    events = [_ev(1, "candidate.frozen", {"candidate_sha": "SAME"})]
    host = _Host(tmp_path, events=events)
    monkeypatch.setattr(
        host, "_try_freeze", lambda cmd: CandidateIdentity("SAME", True, "main")
    )
    assert host._park_freeze_candidate(_cmd()) == "SAME"
    assert host.emitted == []


def test_park_freeze_drift_without_repair_marks_stale(tmp_path, monkeypatch):
    events = [_ev(1, "candidate.frozen", {"candidate_sha": "OLD"})]
    host = _Host(tmp_path, events=events)
    monkeypatch.setattr(
        host, "_try_freeze", lambda cmd: CandidateIdentity("NEW", True, "main")
    )
    assert host._park_freeze_candidate(_cmd()) is None
    assert host.emitted[-1][0] == "candidate.stale"
    assert host.emitted[-1][1]["reason"] == "head_moved"


def test_park_freeze_drift_with_repair_stales_and_refreezes(tmp_path, monkeypatch):
    events = [_ev(1, "candidate.frozen", {"candidate_sha": "OLD"})]
    host = _Host(tmp_path, events=events)
    host.calls["rewalk"] = True
    monkeypatch.setattr(
        host, "_try_freeze", lambda cmd: CandidateIdentity("NEW", True, "main")
    )
    monkeypatch.setattr(
        verify_park._repair,
        "mark_fix_new_candidate",
        lambda run_id, old: {
            "event": "evidence.staled",
            "reason": "fix_new_candidate",
            "old_candidate": old,
        },
    )
    assert host._park_freeze_candidate(_cmd()) == "NEW"
    assert [e[0] for e in host.emitted] == [
        "candidate.stale",
        "evidence.staled",
        "candidate.frozen",
    ]


def test_park_freeze_drift_skips_already_staled(tmp_path, monkeypatch):
    events = [
        _ev(1, "candidate.frozen", {"candidate_sha": "OLD"}),
        _ev(
            2,
            "evidence.staled",
            {"reason": "fix_new_candidate", "old_candidate": "OLD"},
        ),
    ]
    host = _Host(tmp_path, events=events)
    host.calls["rewalk"] = True
    monkeypatch.setattr(
        host, "_try_freeze", lambda cmd: CandidateIdentity("NEW", True, "main")
    )
    assert host._park_freeze_candidate(_cmd()) == "NEW"
    assert [e[0] for e in host.emitted] == ["candidate.stale", "candidate.frozen"]


# ---------------------------------------------------------------------------
# CI / security steps
# ---------------------------------------------------------------------------


def test_park_observe_ci_seen_and_fresh(tmp_path):
    seen = [_ev(1, "ci.run_observed", {"candidate_sha": "S", "api_verified": True})]
    host = _Host(tmp_path, events=seen)
    assert host._park_observe_ci(_cmd(), "S", SimpleNamespace(ci={})) is True

    seen[0].payload["api_verified"] = False
    assert host._park_observe_ci(_cmd(), "S", SimpleNamespace(ci={})) is False

    host.calls["ci_payload"] = None
    assert host._park_observe_ci(_cmd(), "NEW", SimpleNamespace(ci={})) is False

    host.calls["ci_payload"] = {"api_verified": True}
    assert host._park_observe_ci(_cmd(), "NEW", SimpleNamespace(ci={})) is True
    assert host.emitted[-1][0] == "ci.run_observed"


def test_park_assess_security_seen_and_fresh(tmp_path):
    contract = _contract(_scan("s1"))
    policy = security_policy_digest(contract)
    review = _ev(1, "prism.verdict", {
        "candidate_sha": "S", "policy_digest": policy, "scope": "security", "verdict": "pass",
    })
    review.command_id = "review"
    assessment = _ev(2, "security.assessed", {
        "candidate_sha": "S", "status": "passed", "policy_digest": policy,
        "contract_digest": "D", "review_command_id": "review", "scans": [_entry("s1")],
    })
    host = _Host(tmp_path, events=[review, assessment])
    host.calls["security_payload"] = {"status": "failed", "scans": []}
    assert host._park_assess_security(_cmd(), "S", contract, "D") is True
    assert not host.emitted
    assessment.payload["status"] = "failed"
    assert host._park_assess_security(_cmd(), "S", contract, "D") is False

    host.calls["security_payload"] = {
        "status": "passed",
        "scans": [{"id": "s1", "status": "passed"}],
    }
    assert host._park_assess_security(_cmd(), "NEW", contract, "D") is True
    payload = host.emitted[-1][1]
    assert payload["findings"] == [{"id": "s1", "status": "passed", "scan_id": "s1"}]


# ---------------------------------------------------------------------------
# stop payload / budgets / candidate guards
# ---------------------------------------------------------------------------


def test_park_stop_payload_and_reason(tmp_path):
    host = _Host(tmp_path)
    assert host._park_stop_payload("nope") == {}
    host.store._events = [_ev(1, "x", "not-a-dict")]
    assert host._park_stop_payload("x") == {}
    host.store._events = [_ev(1, "x", {"reason": "boom"})]
    assert host._park_stop_reason("x") == "boom"


def test_park_repair_budget_used(tmp_path):
    events = [_ev(1, "repair.round_started", {}), _ev(2, "other", {})]
    host = _Host(tmp_path, events=events)
    assert host._park_repair_budget_used() == 1


def test_park_candidate_seen_guards(tmp_path):
    events = [
        _ev(1, "known_issue.registered", {"candidate_sha": "A", "task_id": "T1"}),
        _ev(2, "known_issue.registered", {"candidate_sha": "A"}),
    ]
    host = _Host(tmp_path, events=events)
    assert host._park_candidate_seen(("other",), "A") is False
    assert host._park_candidate_seen(("known_issue.registered",), "B") is False
    assert host._park_candidate_seen(("known_issue.registered",), "A") is True
    assert host._park_candidate_seen(("known_issue.registered",), "A", "T1") is True
    assert host._park_candidate_seen(("known_issue.registered",), "A", "T2") is True  # legacy covers

    events = [_ev(1, "known_issue.registered", {"candidate_sha": "A", "task_id": "T1"})]
    host = _Host(tmp_path, events=events)
    assert host._park_candidate_seen(("known_issue.registered",), "A", "T2") is False
    assert host._park_candidate_seen(("known_issue.registered",), "A", "T1") is True


def test_park_current_task_id_and_next_number(tmp_path):
    events = [
        _ev(1, "ledger.opened", {"task_id": "T-LEDGER"}),
        _ev(2, "task.started", {"task_id": "T-START"}),
    ]
    host = _Host(tmp_path, events=events)
    assert host._park_current_task_id() == "T-START"

    host.store._events = [_ev(1, "ledger.opened", {"task_id": "T-LEDGER"})]
    assert host._park_current_task_id() == "T-LEDGER"
    host.store._events = []
    assert host._park_current_task_id() == ""

    host.store._events = [
        _ev(1, "known_issue.registered", {"issue_number": 105}),
        _ev(2, "known_issue.registered", {"issue_number": True}),
    ]
    assert host._park_next_known_issue_number() == 106
    host.store._events = []
    assert host._park_next_known_issue_number() == 100


def test_park_task_waiver_refs_and_markers(tmp_path):
    test_file = tmp_path / "repo" / "tests" / "unit" / "test_x.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "# AC-FR0001-01 TRACKS-TRACE\n# AC-FR0002-01 TRACKS-TRACE\n",
        encoding="utf-8",
    )
    events = [
        _ev(
            1,
            "task.started",
            {"task_id": "T1", "task": {"ac_refs": ["AC-FR0001-01"]}},
        ),
        _ev(
            2,
            "full.executed",
            {
                "failed_nodes": [
                    "tests/unit/test_x.py::test_a",
                    "tests/unit/test_y.py::test_b",
                ]
            },
        ),
    ]
    host = _Host(tmp_path, events=events)
    acs, nodes = host._park_task_waiver_refs("T1")
    assert acs == ["AC-FR0001-01"]
    assert nodes == ["tests/unit/test_x.py::test_a"]

    assert host._park_task_waiver_refs("T2") == (
        [],
        ["tests/unit/test_x.py::test_a", "tests/unit/test_y.py::test_b"],
    )
    assert host._file_test_markers("") == set()
    assert host._file_test_markers("tests/unit/missing.py") == set()
    assert host._file_test_markers("tests/unit/test_x.py") == {"AC-FR0001-01", "AC-FR0002-01"}


def test_waived_nodes_direct_and_ac_markers(tmp_path):
    test_file = tmp_path / "repo" / "tests" / "unit" / "test_x.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("# AC-FR0001-01 TRACKS-TRACE\n", encoding="utf-8")
    events = [
        _ev(
            1,
            "known_issue.registered",
            {
                "kind": "behavior",
                "node_refs": ["tests/unit/direct.py::test_d"],
                "ac_refs": ["AC-FR0001-01"],
            },
        )
    ]
    host = _Host(tmp_path, events=events)
    waived = host._waived_nodes(
        ["tests/unit/direct.py::test_d", "tests/unit/test_x.py::test_a"]
    )
    assert waived == {"tests/unit/direct.py::test_d", "tests/unit/test_x.py::test_a"}

    host.store._events = []
    assert host._waived_nodes(["a::b"]) == set()


def test_park_prism_attribution(tmp_path):
    host = _Host(tmp_path)
    assert host._park_prism_attribution() == {}
    host.store._events = [_ev(1, "verdict.failed", {})]
    assert host._park_prism_attribution() == {"attribution_unchanged": True}
    host.store._events = [_ev(1, "prism.verdict", {})]
    assert host._park_prism_attribution() == {"attribution_unchanged": True}


# ---------------------------------------------------------------------------
# repair route
# ---------------------------------------------------------------------------


def test_repair_route_unclassified_returns_false(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(verify_park._repair, "classify_defect", lambda *a: {"failed_class": True})
    assert host._park_repair_route(_cmd(), "S", "bogus", "r") is False


def test_repair_route_known_issue_seen_returns_false(tmp_path, monkeypatch):
    events = [_ev(1, "known_issue.registered", {"candidate_sha": "S", "task_id": "T"})]
    host = _Host(tmp_path, events=events)
    monkeypatch.setattr(verify_park._repair, "classify_defect", lambda *a: {"defect_class": "behavior", "owner": "devon", "discipline": "red_first"})
    monkeypatch.setattr(host, "_park_current_task_id", lambda: "T")
    assert host._park_repair_route(_cmd(), "S", "behavior", "r") is False


def test_repair_route_budget_exhausted_registers(tmp_path, monkeypatch):
    events = [_ev(i, "repair.round_started", {}) for i in range(1, 4)]
    host = _Host(tmp_path, events=events)
    monkeypatch.setattr(verify_park._repair, "classify_defect", lambda *a: {"defect_class": "behavior", "owner": "devon", "discipline": "red_first"})
    monkeypatch.setattr(host, "_park_current_task_id", lambda: "T1")
    registered = {}

    def _known(cmd, sha, kind, reason, used, budget, task_id=None):
        registered.update(kind=kind, used=used, budget=budget, task_id=task_id)

    monkeypatch.setattr(host, "_park_known_issue", _known)
    assert host._park_repair_route(_cmd(), "S", "behavior", "r") is False
    assert registered == {"kind": "behavior", "used": 3, "budget": 3, "task_id": "T1"}


def test_repair_route_opens_round(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(verify_park._repair, "classify_defect", lambda *a: {"defect_class": "behavior", "owner": "devon", "discipline": "red_first"})
    monkeypatch.setattr(
        verify_park._repair,
        "open_repair_round",
        lambda run_id, classification, budget: {"event": "repair.round_started", "round": 0},
    )
    monkeypatch.setattr(host, "_park_current_task_id", lambda: "")
    assert host._park_repair_route(_cmd(), "S", "behavior", "why") is True
    payload = host.emitted[-1][1]
    assert payload["round"] == 1
    assert payload["reason"] == "why"
    assert payload["repair_route"]["budget_remaining"] == 2

    host.emitted.clear()
    assert host._park_repair_route(_cmd(), "S", "behavior", "") is True
    assert "reason" not in host.emitted[-1][1]


def test_origin_repair_route_paths(tmp_path, monkeypatch):
    events = [_ev(1, "repair.round_started", {"candidate_sha": "S"})]
    host = _Host(tmp_path, events=events)
    monkeypatch.setattr(host, "_park_current_task_id", lambda: "T")
    known = {}
    monkeypatch.setattr(
        host,
        "_park_known_issue",
        lambda cmd, sha, kind, reason, used, budget, task_id=None: known.update(
            kind=kind, used=used, task_id=task_id
        ),
    )
    state = State(current_attempt=3)
    assert host._park_origin_repair_route(_cmd(), "S", state) is False
    assert known["used"] == 3 and known["kind"] == "behavior"

    host.store._events = [
        _ev(1, "known_issue.registered", {"candidate_sha": "S", "task_id": "T"})
    ]
    assert host._park_origin_repair_route(_cmd(), "S", state) is False

    host.store._events = []
    assert host._park_origin_repair_route(_cmd(), "S", state) is False

    host.store._events = [
        _ev(1, "task.started", {"task_id": "T"}),
        _ev(2, "verdict.failed", {"check": "impl_defect", "reason": "bad"}),
    ]
    monkeypatch.setattr(host, "_park_repair_route", lambda *a, **k: True)
    assert host._park_origin_repair_route(_cmd(), "S", state) is True


# ---------------------------------------------------------------------------
# known issue registration
# ---------------------------------------------------------------------------


def test_park_known_issue_guards_and_pending(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    host.store._events = [
        _ev(1, "known_issue.rejected", {"candidate_sha": "S", "task_id": "T"})
    ]
    monkeypatch.setattr(host, "_park_current_task_id", lambda: "T")
    host._park_known_issue(_cmd(), "S", "security_finding", "r", 3, 3)
    assert host.emitted == []

    host.store._events = []
    monkeypatch.setattr(
        verify_park._repair, "judge_irreparable", lambda used, budget, attribution: False
    )
    host._park_known_issue(_cmd(), "S", "behavior", "r", 3, 3)
    assert host.emitted == []

    monkeypatch.setattr(
        verify_park._repair, "judge_irreparable", lambda used, budget, attribution: True
    )
    assert host._park_known_issue(_cmd(), "S", "behavior", "r", 3, 3) is None
    assert host.emitted[-1][0] == "known_issue.pending"

    assert host._park_pending_known_issue(_cmd(), "S", "T", "r", [], []) is False


def test_register_parked_known_issue_paths(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    registered = {
        "event": "known_issue.registered",
        "issue_number": 100,
        "url": "https://x",
    }
    monkeypatch.setattr(verify_park._repair, "register_known_issue", lambda repo, attrib, sha: registered)
    persisted, regen = [], []
    monkeypatch.setattr(
        host, "_persist_known_issue_mapping", lambda reg, task, sha: persisted.append(sha)
    )
    monkeypatch.setattr(
        host, "_regen_preview_after_known_issue", lambda cmd, sha: regen.append(sha)
    )
    host._register_parked_known_issue(
        _cmd(), "S", "behavior", "reason", "T", {"item_or_ac": "AC-1"}, ["AC-2"], []
    )
    assert persisted == ["S"] and regen == ["S"]

    rejected = {"event": "known_issue.rejected", "issue_number": None}
    monkeypatch.setattr(verify_park._repair, "register_known_issue", lambda *a: rejected)
    host._register_parked_known_issue(_cmd(), "S", "security_finding", "r", None, {}, [], [])
    assert len(persisted) == 1

    captured = {}

    def _register(repo, attrib, sha):
        captured.update(attrib)
        return rejected

    monkeypatch.setattr(verify_park._repair, "register_known_issue", _register)
    host._register_parked_known_issue(
        _cmd(), "S", "behavior", "fallback-reason", None, {}, [], []
    )
    assert captured["item_or_ac"] == "fallback-reason"

    host._register_parked_known_issue(
        _cmd(), "S", "behavior", "r", None, {"classification": "cls"}, [], []
    )
    assert captured["item_or_ac"] == "cls"


def test_park_known_issue_registers_when_pending_second_drive(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(host, "_park_current_task_id", lambda: "T")
    monkeypatch.setattr(
        verify_park._repair, "judge_irreparable", lambda used, budget, attribution: True
    )
    monkeypatch.setattr(host, "_park_pending_known_issue", lambda *a: False)
    registered = []
    monkeypatch.setattr(
        host,
        "_register_parked_known_issue",
        lambda *a, **k: registered.append(a),
    )
    host._park_known_issue(_cmd(), "S", "behavior", "r", 3, 3)
    assert registered and registered[0][1] == "S"


def test_persist_known_issue_mapping(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    seen = {}
    monkeypatch.setattr(
        verify_park,
        "persist_issue_mapping",
        lambda repo, item, mapping: seen.update(item=item, mapping=mapping),
    )
    host._persist_known_issue_mapping(
        {"issue_number": 7}, "T", "SHA"
    )
    assert seen["item"] == "known_issue:7"
    assert seen["mapping"]["api_verified"] is False
    assert seen["mapping"]["authoritative"] is False


def test_regen_preview_after_known_issue(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    previews = []
    monkeypatch.setattr(
        host, "_park_preview", lambda cmd, sha, contract, digest=None: previews.append((sha, contract, digest))
    )
    host._regen_preview_after_known_issue(_cmd(), "S")
    assert previews == []

    host.store._events = [
        _ev(1, "release.previewed", {"candidate_sha": "S"}),
    ]
    host._regen_preview_after_known_issue(_cmd(), "S")
    assert previews == [("S", "", None)]


# ---------------------------------------------------------------------------
# journey / preview
# ---------------------------------------------------------------------------


def test_release_journey(tmp_path):
    host = _Host(tmp_path)
    assert host._release_journey(
        SimpleNamespace(hotfix_scenario="post-release"), _cmd()
    ) == "post_release"
    assert host._release_journey(SimpleNamespace(hotfix_scenario="dev"), _cmd()) == "dev"
    assert host._release_journey(
        SimpleNamespace(hotfix_scenario="custom"), _cmd()
    ) == "custom"
    assert host._release_journey(SimpleNamespace(hotfix_scenario=None), _cmd(journey="j")) == "j"


def test_park_preview_contract_resolution(tmp_path):
    host = _Host(tmp_path)
    assert host._park_preview_contract(_cmd(), "S", SimpleNamespace(), "D") == (
        SimpleNamespace(),
        "D",
    )
    host.calls["contract"] = (None, None, "src")
    assert host._park_preview_contract(_cmd(), "S", "DIGEST", None) == (None, None)
    host.calls["contract"] = ("C", "D", "src")
    assert host._park_preview_contract(_cmd(), "S", "DIGEST", None) == ("C", "D")


def test_park_preview_facts(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(verify_park, "_contract_table", lambda contract: {})
    contract = SimpleNamespace(version=SimpleNamespace(patch_line="0.1"))
    facts = host._park_preview_facts(
        _cmd(), "S", contract, [], None, SimpleNamespace(version="v0.1")
    )
    assert facts is not None

    host.store._events = [_ev(1, "story.requested", {})]
    state = SimpleNamespace(version=None)
    assert host._park_preview_facts(_cmd(), "S", contract, host.store._events, None, state) is not None


def test_park_preview_full_flow(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    state = SimpleNamespace(version="v0.1")
    host.store._state = state
    monkeypatch.setattr(
        verify_park._repair,
        "list_known_issues_for_preview",
        lambda run_id, events: [],
    )
    contract = SimpleNamespace(version=SimpleNamespace(patch_line="0.1"))
    host.calls["contract"] = (contract, "D", "src")
    monkeypatch.setattr(
        verify_park, "_contract_table", lambda contract: {}
    )
    monkeypatch.setattr(verify_park, "version_facts", lambda version, run_id: {})
    monkeypatch.setattr(
        verify_park, "complete_version_facts", lambda repo, facts, patch, needs_n: ({}, None)
    )
    assert host._park_preview_facts(_cmd(), "S", contract, [], None, state) == {}

    monkeypatch.setattr(
        verify_park, "assemble_preview", lambda *a, **k: {"candidate_sha": "S"}
    )
    host._park_preview(_cmd(), "S", contract, "D")
    assert host.emitted[-1][0] == "release.previewed"
    assert host.emitted[-1][1]["blob_ref"].startswith(".tracks/runtime/blobs/")

    host.store.blobs.clear()
    assert host._park_preview(_cmd(), "S", contract, "D") is None


def test_park_preview_skip_paths(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(verify_park, "_contract_table", lambda contract: {})
    monkeypatch.setattr(verify_park, "version_facts", lambda version, run_id: {})
    state = SimpleNamespace(version="v0.1")
    host.store._state = state

    monkeypatch.setattr(
        verify_park, "complete_version_facts", lambda *a, **k: (None, "census_failed")
    )
    contract = SimpleNamespace(version=SimpleNamespace(patch_line="0.1"))
    host._park_preview(_cmd(), "S", contract, "D")
    assert host.emitted[-1][0] == "attention.required"
    assert host.emitted[-1][1]["reason"] == "census_failed"

    monkeypatch.setattr(
        verify_park, "complete_version_facts", lambda *a, **k: ({}, None)
    )
    monkeypatch.setattr(verify_park, "assemble_preview", lambda *a, **k: None)
    assert host._park_preview(_cmd(), "S", contract, "D") is None

    monkeypatch.setattr(verify_park, "assemble_preview", lambda *a, **k: {"x": 1})
    host.store._events = [
        _ev(1, "release.previewed", {"candidate_sha": "S"}),
    ]
    monkeypatch.setattr(verify_park, "preview_inputs_match", lambda last, preview: True)
    monkeypatch.setattr(verify_park, "preview_blob_matches", lambda home, last: True)
    assert host._park_preview(_cmd(), "S", contract, "D") is None

    monkeypatch.setattr(host, "_park_preview_contract", lambda *a: (None, None))
    assert host._park_preview(_cmd(), "S", "DIGEST", None) is None


def test_park_preview_blob_write_failure_is_silent(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(verify_park, "_contract_table", lambda contract: {})
    monkeypatch.setattr(verify_park, "version_facts", lambda version, run_id: {})
    monkeypatch.setattr(
        verify_park, "complete_version_facts", lambda *a, **k: ({}, None)
    )
    monkeypatch.setattr(verify_park, "assemble_preview", lambda *a, **k: {"x": 1})
    monkeypatch.setattr(host.store, "write_audit_blob", lambda payload: "")
    contract = SimpleNamespace(version=SimpleNamespace(patch_line="0.1"))
    host._park_preview(_cmd(), "S", contract, "D")
    assert host.emitted == []


def test_park_preview_changed(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    assert host._park_preview_changed([], "S", {}) is True

    events = [_ev(1, "release.previewed", {"candidate_sha": "S"})]
    monkeypatch.setattr(verify_park, "preview_inputs_match", lambda last, preview: True)
    monkeypatch.setattr(verify_park, "preview_blob_matches", lambda home, last: True)
    assert host._park_preview_changed(events, "S", {}) is False
    monkeypatch.setattr(verify_park, "preview_blob_matches", lambda home, last: False)
    assert host._park_preview_changed(events, "S", {}) is True
    monkeypatch.setattr(verify_park, "preview_inputs_match", lambda last, preview: False)
    assert host._park_preview_changed(events, "S", {}) is True


def test_release_version_facts_and_fail_block(tmp_path):
    host = _Host(tmp_path)
    facts = host._release_version_facts(SimpleNamespace(version="v0.1"))
    assert isinstance(facts, dict)
    host._fail_verify_block(_cmd(), "S", "reason", "detail")
    assert [e[0] for e in host.emitted] == ["host_contract.invalid", "local_gate.failed"]
    assert host.emitted[0][1]["reason"] == "reason"
