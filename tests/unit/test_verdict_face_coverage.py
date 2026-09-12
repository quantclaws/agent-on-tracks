"""Behavior coverage for ``ExecVerdictMixin``: criteria-pack failure emission,
M2 diagnosis envelope/streak/forensics scans, SHIELD_FIX manifest+anchor
guards and the verify-final identity/discussion-anchor resolution.
"""

from __future__ import annotations

from types import SimpleNamespace

from tracks.executor import verdict_face
from tracks.executor.verdict_face import ExecVerdictMixin


class _Cmd:
    command_id = "CMD-V"
    params: dict = {}


class _Store:
    def __init__(self, events=()):
        self.events_list = list(events)

    def events(self, _run_id):
        return list(self.events_list)

    def write_audit_blob(self, payload):
        return "blob-ref"


class _Host(ExecVerdictMixin):
    def __init__(self, tmp_path, events=()):
        self.repo = tmp_path / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.run_id = "RUN"
        self.store = _Store(events)
        self.emitted: list = []
        self._frozen = SimpleNamespace(payload={"candidate_sha": "f" * 40})

    def _emit(self, event, payload=None, **kwargs):
        self.emitted.append((event, payload, kwargs))

    def _latest_event(self, _etype):
        return self._frozen

    def _dirty_files(self):
        return set()

    def _publish_verify_final_verdict(self, *args, **kwargs):
        self.emitted.append(("verify_final", args, kwargs))

    def _advance_verify_chain(self, cmd, sha):
        self.emitted.append(("advance", sha))


def test_emit_criteria_pack_failure_emits_outcome_and_verdict(tmp_path):
    host = _Host(tmp_path)
    ExecVerdictMixin._emit_criteria_pack_failure(
        host,
        _Cmd(),
        "T-1",
        {"role": "prism"},
        {"criteria_pack": {"name": "assigned"}},
        {"status": "done", "self_report": "s", "criteria_pack": {"name": "got"}},
        SimpleNamespace(current_attempt=0),
    )
    assert [e[0] for e in host.emitted] == ["outcome.received", "verdict.failed"]
    assert host.emitted[1][1]["check"] == "criteria_pack_mismatch"


def test_m2_diagnosis_envelope_forensics_and_unknown_streak(tmp_path):
    host = _Host(tmp_path)
    host._latest_forensics_ref = lambda task_id: "pkg.json"
    host._diagnose_unknown_streak = lambda task_id: False
    evidence, check, reason = ExecVerdictMixin._m2_diagnosis_envelope(
        host, "T-1", {"raw": 1}, "impl_defect", "r"
    )
    assert "forensic package: pkg.json" in evidence
    assert (check, reason) == ("impl_defect", "r")

    host._diagnose_unknown_streak = lambda task_id: True
    evidence, check, reason = ExecVerdictMixin._m2_diagnosis_envelope(
        host, "T-1", "e", "unknown", "r"
    )
    assert check == "diagnosis_exhausted"
    assert "repeated unknown attribution" in reason


def test_diagnose_unknown_streak_scans_events(tmp_path):
    host = _Host(tmp_path)
    assert host._diagnose_unknown_streak(None) is False

    host.store = None
    assert host._diagnose_unknown_streak("T-1") is False

    events = [
        SimpleNamespace(
            type="verdict.failed", payload={"task_id": "T-1", "check": "unknown"}
        ),
        SimpleNamespace(
            type="verdict.failed", payload={"task_id": "T-2", "check": "unknown"}
        ),
        SimpleNamespace(type="verdict.passed", payload={"task_id": "T-1"}),
    ]
    host = _Host(tmp_path, events)
    assert host._diagnose_unknown_streak("T-1") is True


def test_latest_forensics_ref_scans_events(tmp_path):
    host = _Host(tmp_path)
    assert host._latest_forensics_ref(None) is None

    host.store = None
    assert host._latest_forensics_ref("T-1") is None

    events = [
        SimpleNamespace(
            type="verdict.failed",
            payload={
                "task_id": "T-1",
                "evidence": '{"forensics_ref": "pkg-1"}',
            },
        ),
        SimpleNamespace(
            type="verdict.failed",
            payload={"task_id": "T-1", "evidence": "{not json"},
        ),
        SimpleNamespace(
            type="verdict.failed",
            payload={"task_id": "T-1", "evidence": ["not-a-str"]},
        ),
        SimpleNamespace(
            type="verdict.failed",
            payload={"task_id": "T-2", "evidence": '{"forensics_ref": "other"}'},
        ),
    ]
    host = _Host(tmp_path, events)
    assert host._latest_forensics_ref("T-1") == "pkg-1"


def test_shield_fix_manifest_mismatch_branches(tmp_path):
    host = _Host(tmp_path)
    assert host._shield_fix_manifest_mismatch({"artifact_manifest": {}}, []) is None

    matching = {
        "artifact_manifest": {"include": [{"path": "tests/unit/test_a.py"}]}
    }
    assert host._shield_fix_manifest_mismatch(
        matching, ["tests/unit/test_a.py"]
    ) is None

    mismatch = host._shield_fix_manifest_mismatch(
        matching, ["tests/unit/test_b.py"]
    )
    assert "do not match observed tests/ files" in mismatch

    host._dirty_files = lambda: {"tests/unit/test_b.py"}
    (host.repo / "tests" / "unit").mkdir(parents=True)
    (host.repo / "tests" / "unit" / "test_b.py").write_text("", encoding="utf-8")
    assert host._shield_fix_manifest_mismatch(
        {"artifact_manifest": {"include": [{"path": "tests/unit/test_b.py"}]}},
        ["tests/unit/test_b.py"],
    ) is None


def _shield_state():
    return SimpleNamespace(
        stage="M-IMPL",
        substate="SHIELD_FIX",
        current_attempt=0,
        current_task_id="T-1",
    )


def test_emit_shield_commit_no_diff_and_rejections(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(
        verdict_face, "git", lambda *a, **k: SimpleNamespace(stdout="")
    )
    assert host._emit_shield_commit(
        {"status": "done"}, "shield", _shield_state(), _Cmd(), "T-1"
    ) is False
    assert host.emitted[-1][1]["reason"] == "shield_fix_no_diff"

    host = _Host(tmp_path)
    monkeypatch.setattr(
        verdict_face, "git", lambda *a, **k: SimpleNamespace(stdout=" M tests/x.py\n")
    )
    host._shield_fix_rejected = lambda *a: True
    assert host._emit_shield_commit(
        {"status": "done"}, "shield", _shield_state(), _Cmd(), "T-1"
    ) is False

    host = _Host(tmp_path)
    host._shield_fix_rejected = lambda *a: False
    host._commit_shield_fix = lambda *a: False
    assert host._emit_shield_commit(
        {"status": "done"}, "shield", _shield_state(), _Cmd(), "T-1"
    ) is False


def test_shield_fix_rejected_manifest_and_anchor(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    host._shield_fix_manifest_mismatch = lambda result, changed: "include mismatch"
    assert host._shield_fix_rejected({}, _shield_state(), _Cmd(), "T-1", ["tests/x.py"]) is True
    assert host.emitted[-1][1]["check"] == "manifest"

    host = _Host(tmp_path)
    host._shield_fix_manifest_mismatch = lambda result, changed: None
    monkeypatch.setattr(
        "tracks.checks.anchor_lint.anchor_static_violations",
        lambda path: ["anchor violation"],
    )
    assert host._shield_fix_rejected(
        {}, _shield_state(), _Cmd(), "T-1", ["tests/unit/test_x.py"]
    ) is True
    assert host.emitted[-1][1]["check"] == "test_defect"

    host = _Host(tmp_path)
    host._shield_fix_manifest_mismatch = lambda result, changed: None
    monkeypatch.setattr(
        "tracks.checks.anchor_lint.anchor_static_violations", lambda path: []
    )
    assert host._shield_fix_rejected(
        {}, _shield_state(), _Cmd(), "T-1", ["tests/unit/test_x.py"]
    ) is False


def test_commit_shield_fix_failure(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(verdict_face, "git", lambda *a, **k: SimpleNamespace())
    proc = SimpleNamespace(returncode=1, stderr="hook", stdout="")
    monkeypatch.setattr(
        verdict_face, "_scoped_commit_if_staged", lambda *a, **k: proc
    )
    assert host._commit_shield_fix(_shield_state(), _Cmd(), "T-1") is False
    assert host.emitted[-1][1]["reason"] == "shield_fix_commit_failed"


def test_dispatch_log_start_docs_objective(capsys):
    state = SimpleNamespace(stage="M-IMPL")
    ExecVerdictMixin._dispatch_log_start({"docs": ["a.md", "b.md"]}, state)
    assert "a.md, b.md" in capsys.readouterr().err

    ExecVerdictMixin._dispatch_log_start({"doc": "c.md"}, state)
    assert "c.md" in capsys.readouterr().err


def test_publish_prism_verdict_verify_stage(tmp_path):
    host = _Host(tmp_path)
    host._publish_prism_verdict(
        "pass", None, False, "rid", SimpleNamespace(stage="M-VERIFY"), _Cmd(), "T-1"
    )
    assert host.emitted[0][0] == "verify_final"


def test_verify_final_identity_candidate_resolution(tmp_path):
    host = _Host(tmp_path)
    identity = host._verify_final_identity(
        "pass", {}, {"assignment": {"candidate_sha": "f" * 40}}, _Cmd()
    )
    assert identity.error is False
    assert identity.candidate_sha == "f" * 40

    identity = host._verify_final_identity(
        "pass", {}, {}, SimpleNamespace(params={"candidate_sha": "f" * 40})
    )
    assert identity.error is False


def test_discussion_ref_anchors_branches(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    assert host._discussion_ref_anchors("not-a-dict", set()) is False

    outside = tmp_path / "outside.md"
    outside.write_text("x", encoding="utf-8")
    assert host._discussion_ref_anchors(
        {"file": str(outside), "thread_id": "t1", "token": {}, "finding_id": "F1"},
        {"F1"},
    ) is False

    missing = {
        "file": "tests/missing.md",
        "thread_id": "t1",
        "token": {},
        "finding_id": "F1",
    }
    assert host._discussion_ref_anchors(missing, {"F1"}) is False

    (host.repo / "tests").mkdir(parents=True)
    doc = host.repo / "tests" / "doc.md"
    doc.write_text("thread body", encoding="utf-8")
    ref = {"file": "tests/doc.md", "thread_id": "t1", "token": {}, "finding_id": "F1"}

    monkeypatch.setattr(
        verdict_face, "locate", lambda *a: SimpleNamespace(status="ambiguous")
    )
    assert host._discussion_ref_anchors(ref, {"F1"}) is False

    monkeypatch.setattr(
        verdict_face, "locate", lambda *a: SimpleNamespace(status="unique")
    )
    monkeypatch.setattr(
        verdict_face,
        "parse_threads",
        lambda text: [
            SimpleNamespace(thread_id="t1", status="closed", initiator="prism")
        ],
    )
    assert host._discussion_ref_anchors(ref, {"F1"}) is False

    monkeypatch.setattr(
        verdict_face,
        "parse_threads",
        lambda text: [
            SimpleNamespace(thread_id="t1", status="open", initiator="prism")
        ],
    )
    assert host._discussion_ref_anchors(ref, {"F1"}) is True
