"""Behavior coverage for the v0.6 hotfix mixin/helpers (``hotfix_face``).

Drives ``ExecHotfixMixin`` directly over a real Store + git repo, with only
the external issue/completion seams stubbed: entry PRECHECK, anchor
validation (NO_ANCHOR / manual / Sage), entry completion fail-closed,
boundary restoration and the entry-journey printer.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit.helpers import git as _git
from tests.unit.helpers import git_repo, git_strip
from tracks.executor import hotfix_face
from tracks.executor.hotfix import HostIssue, PrecheckReport
from tracks.executor.hotfix_face import ExecHotfixMixin
from tracks.kernel.events import Command
from tracks.kernel.machine import State
from tracks.store import Store


class _Host(ExecHotfixMixin):
    def __init__(self, tmp_path: Path, *, run_id: str = "RUN", version: str = "v0.6"):
        self.repo = tmp_path / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.store = Store(tmp_path / ".tracks")
        (self.store.home / "projects").mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.version = version
        self.emitted: list[tuple] = []
        self.issued: list[tuple] = []
        self._vdir_path = tmp_path / "vdir"
        self._vdir_path.mkdir(parents=True, exist_ok=True)
        self._head_value = "main"

    def _emit(self, event, payload=None, **kwargs):
        self.emitted.append((event, payload, kwargs))
        self.store.append(
            self.run_id,
            self.version,
            event,
            payload or {},
            command_id=kwargs.get("command_id"),
            task_id=kwargs.get("task_id"),
        )

    def issue(self, cmd, command_id=None):
        self.issued.append((cmd, command_id))

    def _vdir(self) -> Path:
        return self._vdir_path

    def _head(self) -> str:
        return self._head_value


def _cmd(command_id: str = "C-1", **params) -> Command:
    return Command(kind="hotfix_cmd", params=dict(params), command_id=command_id)


# ---------------------------------------------------------------------------
# module-level helpers
# ---------------------------------------------------------------------------


class _FakeStore:
    def __init__(self, *, state=None, events=(), rows=()):
        self._state = state
        self._events = list(events)
        self.conn = SimpleNamespace(execute=lambda *a, **k: SimpleNamespace(fetchall=lambda: list(rows)))

    def state(self, run_id):
        return self._state

    def events(self, run_id):
        return list(self._events)


def test_hotfix_approved_versions_filters_empty_rows():
    store = _FakeStore(rows=[("v0.6",), (None,), ("v0.5",), ("",)])
    assert hotfix_face._hotfix_approved_versions(store) == {"v0.6", "v0.5"}


def test_hotfix_run_branch_prefers_hotfix_then_release_then_event():
    hotfix = _FakeStore(state=SimpleNamespace(hotfix_issue=42, version="v0.6"))
    assert hotfix_face._hotfix_run_branch(hotfix, "R") == "fix/42"
    release = _FakeStore(state=SimpleNamespace(hotfix_issue=None, version="v0.6"))
    assert hotfix_face._hotfix_run_branch(release, "R") == "releases/v0.6"
    event = SimpleNamespace(type="branch.created", payload={"branch_name": "feature/x"})
    recorded = _FakeStore(state=SimpleNamespace(hotfix_issue=None, version=None), events=[event])
    assert hotfix_face._hotfix_run_branch(recorded, "R") == "feature/x"
    no_branch = _FakeStore(
        state=SimpleNamespace(hotfix_issue=None, version=None),
        events=[SimpleNamespace(type="branch.created", payload={})],
    )
    assert hotfix_face._hotfix_run_branch(no_branch, "R") is None


def test_resolve_run_version_branches():
    assert hotfix_face._resolve_run_version(SimpleNamespace(version="v0.6")) == "v0.6"
    assert (
        hotfix_face._resolve_run_version(
            SimpleNamespace(version=None, hotfix_issue=7, hotfix_target_version="v0.6")
        )
        == "v0.6-hotfix-7"
    )
    assert (
        hotfix_face._resolve_run_version(
            SimpleNamespace(version=None, hotfix_issue=7, hotfix_target_version=None)
        )
        == "hotfix-7"
    )
    assert (
        hotfix_face._resolve_run_version(
            SimpleNamespace(version=None, hotfix_issue=None, hotfix_target_version=None)
        )
        == ""
    )


def test_hotfix_corpus_paths_lists_version_docs(tmp_path: Path):
    home = tmp_path / ".tracks"
    projects = home / "projects"
    (projects / "v0.5").mkdir(parents=True)
    (projects / "v0.5" / "spec.md").write_text("s", encoding="utf-8")
    (projects / "v0.5" / "acceptance.md").write_text("a", encoding="utf-8")
    (projects / "v0.6").mkdir()
    (projects / "v0.6" / "spec.md").write_text("s2", encoding="utf-8")
    (projects / "notes").mkdir()
    corpus = hotfix_face._hotfix_corpus_paths(home)
    assert corpus == [
        str(projects / "v0.5" / "spec.md"),
        str(projects / "v0.5" / "acceptance.md"),
        str(projects / "v0.6" / "spec.md"),
    ]


def test_precheck_hotfix_report_collects_inputs(tmp_path: Path, monkeypatch):
    repo = git_repo(tmp_path)
    store = Store(tmp_path / ".tracks")
    store.append("RUN", "v0.6", "stage.entered", {"stage": "M-IMPL"})
    store.append("RUN", "v0.6", "branch.created", {"branch_name": "feature/x"})
    store.append("OTHER", "v0.5", "approval.recorded", {"digest": "d", "actor": "a"})
    (tmp_path / ".tracks" / "projects" / "v0.5").mkdir(parents=True)

    issue = HostIssue(1, "title", "body", ("bug",))
    monkeypatch.setattr(
        hotfix_face, "select_issue_backend", lambda repo_arg, version: SimpleNamespace(fetch_issue=lambda n: issue)
    )
    captured = {}

    def _fake_precheck(issue_arg, scenario, branches, active_branch, projects, approved):
        captured.update(
            issue=issue_arg,
            scenario=scenario,
            branches=branches,
            active_branch=active_branch,
            projects=projects,
            approved=approved,
        )
        return SimpleNamespace(status="pass")

    monkeypatch.setattr(hotfix_face, "precheck_hotfix", _fake_precheck)
    report, fetched = hotfix_face.precheck_hotfix_report(repo, store, 1, "post-release")
    assert fetched == issue
    assert report.status == "pass"
    assert captured["active_branch"] == "releases/v0.6"  # active run's release branch
    assert captured["scenario"] == "post-release"
    assert captured["approved"] == {"v0.5"}
    assert captured["projects"] == repo / ".tracks" / "projects"


def test_precheck_hotfix_report_fetch_failure_yields_none(tmp_path: Path, monkeypatch):
    repo = git_repo(tmp_path)
    store = Store(tmp_path / ".tracks")
    store.append("RUN", "v0.6", "stage.entered", {"stage": "M-IMPL"})

    def _boom(repo_arg, version):
        def _fetch(n):
            raise hotfix_face.GithubIssuesError("network", "down")

        return SimpleNamespace(fetch_issue=_fetch)

    monkeypatch.setattr(hotfix_face, "select_issue_backend", _boom)
    monkeypatch.setattr(hotfix_face, "precheck_hotfix", lambda *a: SimpleNamespace(status="rejected"))
    report, issue = hotfix_face.precheck_hotfix_report(repo, store, 1, "post-release")
    assert issue is None
    assert report.status == "rejected"


def test_hotfix_event_detail_renders_each_type():
    detail = hotfix_face._hotfix_event_detail
    assert detail(SimpleNamespace(type="hotfix.requested", run_id="R", payload={"issue": 3, "scenario": "dev"})) == (
        "run R hotfix.requested (issue=3, scenario=dev)"
    )
    assert "REJECTED (bad)" in detail(
        SimpleNamespace(type="triage.prechecked", run_id="R", payload={"status": "rejected", "reason": "bad"})
    )
    with_next = detail(
        SimpleNamespace(
            type="triage.prechecked",
            run_id="R",
            payload={"status": "rejected", "reason": "bad", "next": "retry"},
        )
    )
    assert with_next.endswith("next: retry")
    assert detail(
        SimpleNamespace(
            type="triage.prechecked", run_id="R", payload={"issue": 3, "issue_type": "bug"}
        )
    ) == "run R triage.prechecked (issue=3 type=bug)"
    assert detail(
        SimpleNamespace(type="anchor.validated", run_id="R", payload={"acs": ["AC-1", "AC-2"]})
    ) == "run R anchor.validated (AC-1, AC-2)"
    assert detail(SimpleNamespace(type="stage.entered", run_id="R", payload={"stage": "M-DESIGN"})) == (
        "run R stage.entered(M-DESIGN)"
    )
    assert detail(
        SimpleNamespace(type="backlog.recorded", run_id="R", payload={"issue": 3, "decision": "feature_route"})
    ) == "run R backlog.recorded (issue=3 decision=feature_route)"
    assert detail(SimpleNamespace(type="other", run_id="R", payload={})) is None


def test_hotfix_state_line_includes_hotfix_fields():
    plain = simple_state()
    assert hotfix_face._hotfix_state_line(plain) == (
        "stage=M-IMPL substate=RED status=active awaiting=-"
    )
    hotfix = simple_state(hotfix_issue=9, hotfix_scenario="dev")
    line = hotfix_face._hotfix_state_line(hotfix)
    assert "branch=fix/9" in line and "scenario=dev" in line and "issue=9" in line


def simple_state(**kwargs):
    base = {
        "stage": "M-IMPL",
        "substate": "RED",
        "status": "active",
        "awaiting": None,
        "hotfix_issue": None,
        "hotfix_scenario": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_last_triage_reason_reads_latest_rejected():
    events = [
        SimpleNamespace(type="triage.prechecked", payload={"status": "rejected", "reason": "old"}),
        SimpleNamespace(type="triage.prechecked", payload={"status": "pass"}),
        SimpleNamespace(type="triage.prechecked", payload={"status": "rejected", "reason": "new"}),
    ]
    store = _FakeStore(events=events)
    assert hotfix_face._last_triage_reason(store, "R") == "new"
    assert hotfix_face._last_triage_reason(_FakeStore(events=[]), "R") == ""


def test_hotfix_entry_output_rejected_prints_reason(capsys):
    events = [
        SimpleNamespace(type="hotfix.requested", run_id="RUN", payload={"issue": 4, "scenario": "dev"}),
        SimpleNamespace(
            type="triage.prechecked", run_id="RUN", payload={"status": "rejected", "reason": "not_bug"}
        ),
    ]
    state = SimpleNamespace(status="completed", terminal_state="rejected", hotfix_issue=4)
    store = _FakeStore(state=state, events=events)
    assert hotfix_face.hotfix_entry_output(store, "RUN") == 1
    captured = capsys.readouterr()
    assert "hotfix.requested (issue=4, scenario=dev)" in captured.out
    assert "REJECTED (not_bug)" in captured.err


def test_hotfix_entry_output_awaiting_and_state_line(capsys):
    events = [
        SimpleNamespace(type="hotfix.requested", run_id="RUN", payload={"issue": 4, "scenario": "dev"})
    ]
    awaiting = SimpleNamespace(
        status="awaiting_human", terminal_state=None, awaiting="hotfix_triage", hotfix_issue=4
    )
    assert hotfix_face.hotfix_entry_output(_FakeStore(state=awaiting, events=events), "RUN") == 0
    assert "awaiting=awaiting_human origin=hotfix-triage issue=4" in capsys.readouterr().out

    plain = simple_state()
    assert hotfix_face.hotfix_entry_output(_FakeStore(state=plain, events=events), "RUN") == 0
    assert "stage=M-IMPL substate=RED status=active awaiting=-" in capsys.readouterr().out


def test_hotfix_feature_route_records_backlog_and_completes(tmp_path: Path):
    repo = git_repo(tmp_path)
    store = Store(tmp_path / ".tracks")
    store.append("RUN", "v0.6", "hotfix.requested", {"issue": 4, "scenario": "dev"})
    rc = hotfix_face.hotfix_feature_route(repo, store, "RUN")
    assert rc == 0
    types = [ev.type for ev in store.events("RUN")]
    assert types[-3:] == ["human.anchor", "backlog.recorded", "run.completed"]
    anchor = next(ev for ev in store.events("RUN") if ev.type == "human.anchor")
    assert anchor.payload["mode"] == "feature_route"
    assert anchor.payload["issue"] == 4
    assert store.state("RUN").terminal_state == "feature_route"


# ---------------------------------------------------------------------------
# mixin: materialization
# ---------------------------------------------------------------------------


def test_valid_hotfix_unit_rows_requires_interfaces_registry(tmp_path: Path):
    host = _Host(tmp_path)
    plan = host._vdir() / "test-plan.md"
    plan.write_text("plan", encoding="utf-8")
    assert host._valid_hotfix_unit_rows([{"if_ids": ["IF-1"]}]) is False
    interfaces = host._vdir() / "interfaces.md"
    interfaces.write_text("## 5. IF Registry\n\n- IF-IMPL-001\n", encoding="utf-8")
    assert host._valid_hotfix_unit_rows([{"if_ids": ["IF-IMPL-001"]}]) is True
    assert host._valid_hotfix_unit_rows([{"if_ids": ["IF-OTHER-9"]}]) is False


def test_materialize_hotfix_assignment_passthrough_and_mdesign(tmp_path: Path):
    host = _Host(tmp_path)
    params = {"substate": "GREEN", "role": "devon"}
    assert host._materialize_hotfix_assignment(State(stage="M-IMPL"), params) is params
    state = State(
        stage="M-DESIGN",
        hotfix_issue=7,
        hotfix_target_version="v0.6",
        hotfix_anchor_acs=["AC-FR0001-01@v0.5"],
    )
    out = host._materialize_hotfix_assignment(state, {"assignment": {"x": 1}})
    assignment = out["assignment"]
    assert assignment["x"] == 1
    assert assignment["anchor_acs"] == ["AC-FR0001-01@v0.5"]
    assert assignment["target_version"] == "v0.6"
    assert assignment["hotfix_issue"] == 7
    assert assignment["baseline_doc_paths"][0].endswith("v0.6/story.md")
    assert len(assignment["baseline_doc_paths"]) == 6


def test_materialize_hotfix_mdesign_without_target_skips_paths(tmp_path: Path):
    host = _Host(tmp_path)
    state = State(stage="M-DESIGN", hotfix_issue=7, hotfix_target_version=None)
    out = host._materialize_hotfix_mdesign_assignment(state, {})
    assert "baseline_doc_paths" not in out["assignment"]


def test_materialize_sage_assignment_with_issue_and_hints(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    issue = HostIssue(7, "Bug title", "### 版本\n\nv0.6\n\n### 对应 FR/NFR\n\nFR-0001\n", ("bug",))
    host._hotfix_issue_corpus = lambda n: issue
    state = State(stage="M-HOTFIX-TRIAGE", hotfix_issue=7, hotfix_target_version="v0.6", hotfix_scenario=None)
    out = host._materialize_hotfix_assignment(
        state, {"substate": "SAGE_TRIAGE", "role": "sage", "assignment": {}}
    )
    assignment = out["assignment"]
    assert assignment["issue"] == {"title": "Bug title", "body": issue.body, "labels": ["bug"]}
    assert assignment["anchor_hints"]["version"] == "v0.6"
    assert assignment["anchor_hints"]["fr_nfr"] == ["FR-0001"]
    assert assignment["scenario"] == "post-release"  # default
    assert assignment["target_version"] == "v0.6"
    assert "corpus" in assignment


def test_materialize_sage_assignment_without_issue(tmp_path: Path):
    host = _Host(tmp_path)
    host._hotfix_issue_corpus = lambda n: None
    state = State(stage="M-HOTFIX-TRIAGE", hotfix_issue=7, hotfix_scenario="dev")
    out = host._materialize_hotfix_assignment(
        state, {"substate": "SAGE_TRIAGE", "role": "sage"}
    )
    assert out["assignment"]["issue"] is None
    assert out["assignment"]["anchor_hints"] == {}
    assert out["assignment"]["scenario"] == "dev"


def test_hotfix_issue_corpus_fetch_failure_returns_none(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)

    def _boom(repo, version):
        raise hotfix_face.GithubIssuesError("network", "down")

    monkeypatch.setattr(hotfix_face, "select_issue_backend", _boom)
    assert host._hotfix_issue_corpus(1) is None


# ---------------------------------------------------------------------------
# mixin: PRECHECK / anchor validation
# ---------------------------------------------------------------------------


def test_do_precheck_hotfix_reconcile_short_circuits(tmp_path: Path):
    host = _Host(tmp_path)
    host.store.append("RUN", "v0.6", "triage.prechecked", {"status": "pass"})
    host._do_precheck_hotfix(_cmd(), State(hotfix_issue=7), "T", reconcile=True)
    assert host.emitted == []  # already persisted: no new emission


@pytest.mark.parametrize(
    "status,expect_completed",
    [("pass", False), ("rejected", True)],
)
def test_do_precheck_hotfix_emits_verdict(tmp_path: Path, monkeypatch, status, expect_completed):
    host = _Host(tmp_path)
    issue = SimpleNamespace(is_bug=True, labels=("bug",))
    monkeypatch.setattr(
        hotfix_face,
        "precheck_hotfix_report",
        lambda repo, store, number, scenario: (
            PrecheckReport(status, None if status == "pass" else "not_bug", None, "v0.6", "main"),
            issue,
        ),
    )
    host._do_precheck_hotfix(_cmd("C-9"), State(hotfix_issue=7, hotfix_scenario="dev"), "T-1", reconcile=False)
    payload = host.emitted[0][1]
    assert payload["status"] == status
    assert payload["issue_type"] == "bug"
    assert payload["target_version"] == "v0.6"
    completed = [event for event, _p, _k in host.emitted if event == "run.completed"]
    assert (completed != []) is expect_completed


def test_do_precheck_hotfix_issue_label_type_fallbacks(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    report = PrecheckReport("pass", None, None, None, None)
    for issue, expected in (
        (SimpleNamespace(is_bug=False, labels=("enhancement",)), "enhancement"),
        (SimpleNamespace(is_bug=False, labels=()), None),
        (None, None),
    ):
        host.emitted.clear()
        monkeypatch.setattr(
            hotfix_face, "precheck_hotfix_report", lambda *a, _i=issue: (report, _i)
        )
        host._do_precheck_hotfix(_cmd(), State(hotfix_issue=7), "T", False)
        assert host.emitted[0][1]["issue_type"] == expected


def test_anchor_input_from_event_variants():
    pick = lambda ev: ExecHotfixMixin._anchor_input_from_event(None, ev)  # noqa: E731
    assert pick(SimpleNamespace(type="human.anchor", payload={"mode": "feature_route"})) is None
    assert pick(SimpleNamespace(type="human.anchor", payload={"mode": "manual", "acs": ["A"]})) == (
        ["A"],
        "human",
        [],
    )
    assert pick(SimpleNamespace(type="outcome.received", payload={"role": "sage", "outcome": "no_anchor"})) == (
        [],
        "no_anchor",
        [],
    )
    assert pick(
        SimpleNamespace(
            type="outcome.received",
            payload={"role": "sage", "acs": ["A"], "rationale_refs": ["R"]},
        )
    ) == (["A"], "sage", ["R"])
    assert pick(SimpleNamespace(type="outcome.received", payload={"role": "devon"})) is None
    assert pick(SimpleNamespace(type="other", payload={})) is None


def test_anchor_inputs_human_overrides_sage_history(tmp_path: Path):
    host = _Host(tmp_path)
    host.store.append(
        "RUN", "v0.6", "outcome.received", {"role": "sage", "acs": ["SAGE"]}
    )
    host.store.append("RUN", "v0.6", "human.anchor", {"mode": "manual", "acs": ["HUMAN"]})
    assert host._anchor_inputs(State()) == (["HUMAN"], "human", [])


def test_anchor_inputs_falls_back_to_state(tmp_path: Path):
    host = _Host(tmp_path)
    state = State(hotfix_anchor_acs=["AC-FR0001-01@v0.5"])
    assert host._anchor_inputs(state) == (["AC-FR0001-01@v0.5"], "sage", [])


def test_do_validate_anchor_reconcile_and_no_anchor(tmp_path: Path):
    host = _Host(tmp_path)
    host._do_validate_anchor(
        _cmd(), State(hotfix_anchor_validated=True), "T", reconcile=True
    )
    assert host.emitted == []
    host._do_validate_anchor(
        _cmd(), State(hotfix_anchor_acs=[], current_attempt=0), "T", reconcile=False
    )
    event, payload, _ = host.emitted[0]
    assert event == "verdict.failed"
    assert payload["reason"].startswith("NO_ANCHOR")
    assert payload["attempt"] == 3


def test_do_validate_anchor_no_anchor_source(tmp_path: Path):
    host = _Host(tmp_path)
    host._anchor_inputs = lambda state: ([], "no_anchor", [])
    host._do_validate_anchor(_cmd(), State(), "T", False)
    assert host.emitted[0][1]["check"] == "anchor_invalid"


def test_do_validate_anchor_valid_emits_anchor_validated(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    host._anchor_inputs = lambda state: (["AC-FR0001-01@v0.5"], "sage", ["R-1"])
    monkeypatch.setattr(hotfix_face, "parse_anchor_refs", lambda acs: [("AC-FR0001-01", "v0.5")])
    monkeypatch.setattr(hotfix_face, "validate_anchor_refs", lambda refs, projects: (True, []))
    state = State(current_attempt=2)
    host._do_validate_anchor(_cmd(), state, "T", False)
    event, payload, _ = host.emitted[0]
    assert event == "anchor.validated"
    assert payload["acs"] == ["AC-FR0001-01@v0.5"]
    assert payload["source"] == "sage"
    assert payload["attempt"] == 2
    assert payload["rationale_refs"] == ["R-1"]


@pytest.mark.parametrize("source,current_attempt,expected", [("human", 1, 3), ("sage", 1, 2)])
def test_do_validate_anchor_invalid_attempt_budget(tmp_path, monkeypatch, source, current_attempt, expected):
    host = _Host(tmp_path)
    host._anchor_inputs = lambda state: (["AC-X@v0.5"], source, [])
    monkeypatch.setattr(hotfix_face, "parse_anchor_refs", lambda acs: [("AC-X", "v0.5")])
    monkeypatch.setattr(hotfix_face, "validate_anchor_refs", lambda refs, projects: (False, ["not found"]))
    host._do_validate_anchor(_cmd(), State(current_attempt=current_attempt), "T", False)
    payload = host.emitted[0][1]
    assert payload["check"] == "anchor_invalid"
    assert payload["attempt"] == expected
    assert payload["reason"] == "not found"


def test_do_validate_anchor_value_error_is_invalid(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    host._anchor_inputs = lambda state: (["BROKEN"], "sage", [])

    def _raise(acs):
        raise ValueError("bad ref")

    monkeypatch.setattr(hotfix_face, "parse_anchor_refs", _raise)
    host._do_validate_anchor(_cmd(), State(current_attempt=0), "T", False)
    assert host.emitted[0][1]["reason"] == "bad ref"


# ---------------------------------------------------------------------------
# mixin: entry completion / boundary
# ---------------------------------------------------------------------------


_COMPLETE_RESULT = {
    "branch_name": "fix/7",
    "base": "main",
    "commit_sha": "abc",
    "target_version": "v0.6",
    "baseline_digest": "d",
    "anchor_acs": ["AC-FR0001-01@v0.5"],
    "baseline_doc_paths": ["p"],
}


def test_do_complete_hotfix_entry_reconcile_and_success(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    host.store.append("RUN", "v0.6", "baseline.inherited", {})
    host._do_complete_hotfix_entry(_cmd(), State(hotfix_issue=7), "T", reconcile=True)
    assert host.emitted == []
    (tmp_path / "two").mkdir()
    host2 = _Host(tmp_path / "two")
    monkeypatch.setattr(hotfix_face, "complete_hotfix_entry", lambda *a: dict(_COMPLETE_RESULT))
    state = State(
        hotfix_issue=7,
        hotfix_scenario="post-release",
        hotfix_target_version="v0.6",
        hotfix_anchor_acs=["AC-FR0001-01@v0.5"],
    )
    host2._do_complete_hotfix_entry(_cmd(), state, "T", False)
    assert [event for event, _p, _k in host2.emitted] == [
        "branch.created",
        "baseline.inherited",
        "stage.entered",
    ]
    assert host2.emitted[2][1] == {"stage": "M-DESIGN"}


def test_do_complete_hotfix_entry_failure_completes_rejected(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)

    def _boom(*args):
        raise RuntimeError("branch exists")

    monkeypatch.setattr(hotfix_face, "complete_hotfix_entry", _boom)
    host._do_complete_hotfix_entry(_cmd(), State(hotfix_issue=7), "T", False)
    assert host.emitted[0][0] == "run.completed"
    assert host.emitted[0][1]["terminal_state"] == "rejected"


def test_hotfix_boundary_completion_non_hotfix(tmp_path: Path):
    host = _Host(tmp_path)
    assert host._hotfix_boundary_completion(State(hotfix_issue=None)) == {
        "terminal_state": "boundary"
    }


def test_hotfix_boundary_completion_restores_entry_branch(tmp_path: Path):
    host = _Host(tmp_path)
    state = State(hotfix_issue=7)
    host._next_suspended_run = lambda: None
    host._hotfix_entry_base = lambda: "feature/base"
    host._restore_hotfix_entry_branch = lambda branch: True
    assert host._hotfix_boundary_completion(state) == {
        "terminal_state": "boundary",
        "restored_branch": "feature/base",
    }


def test_hotfix_boundary_completion_restores_suspended_run(tmp_path: Path):
    repo = git_repo(tmp_path)
    _git(repo, "branch", "feature/suspended")
    host = _Host(tmp_path)
    host.repo = repo
    host._next_suspended_run = lambda: ("OTHER", "feature/suspended")
    payload = host._hotfix_boundary_completion(State(hotfix_issue=7))
    assert payload == {
        "terminal_state": "boundary",
        "restored_active_run": "OTHER",
        "restored_branch": "feature/suspended",
    }
    assert git_strip(repo, "rev-parse", "--abbrev-ref", "HEAD") == "feature/suspended"


def test_hotfix_boundary_completion_suspended_without_branch(tmp_path: Path):
    host = _Host(tmp_path)
    host._next_suspended_run = lambda: ("OTHER", None)
    payload = host._hotfix_boundary_completion(State(hotfix_issue=7))
    assert payload["restored_active_run"] == "OTHER"
    assert payload["restored_branch"] is None


def test_restore_hotfix_entry_branch_noop_and_failure(tmp_path: Path):
    repo = git_repo(tmp_path)
    host = _Host(tmp_path)
    host.repo = repo
    assert host._restore_hotfix_entry_branch(None) is True
    assert host._restore_hotfix_entry_branch("main") is True
    assert host._restore_hotfix_entry_branch("does-not-exist") is False


def test_restore_hotfix_entry_branch_keeps_delta_artifacts(tmp_path: Path):
    repo = git_repo(tmp_path)
    _git(repo, "checkout", "-b", "feature/base")
    (repo / "base-flavor.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "base-flavor.txt")
    _git(repo, "commit", "-m", "base commit")
    _git(repo, "checkout", "main")
    host = _Host(tmp_path)
    host.repo = repo
    host._head_value = "main"
    artifact = host._vdir() / "taskgraph" / "tasks.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("delta", encoding="utf-8")
    assert host._restore_hotfix_entry_branch("feature/base") is True
    assert git_strip(repo, "rev-parse", "--abbrev-ref", "HEAD") == "feature/base"
    assert artifact.read_text(encoding="utf-8") == "delta"


def test_hotfix_entry_base_reads_branch_created(tmp_path: Path):
    host = _Host(tmp_path)
    assert host._hotfix_entry_base() is None
    host.store.append("RUN", "v0.6", "branch.created", {"base": "main"})
    assert host._hotfix_entry_base() == "main"


def test_next_suspended_run_selects_latest_live_run(tmp_path: Path):
    host = _Host(tmp_path)
    host.store.append("RUN", "v0.6", "stage.entered", {"stage": "M-IMPL"})
    host.store.append("OTHER", "v0.6", "stage.entered", {"stage": "M-IMPL"})
    host.store.append("OTHER", "v0.6", "branch.created", {"branch_name": "feature/other"})
    host.store.append("DONE", "v0.6", "stage.entered", {"stage": "M-IMPL"})
    host.store.append("DONE", "v0.6", "run.completed", {"terminal_state": "released"})
    for rid, ts in (("OTHER", "2026-01-01T00:00:02+00:00"), ("DONE", "2026-01-01T00:00:03+00:00")):
        host.store.conn.execute("UPDATE runs SET updated_ts=? WHERE run_id=?", (ts, rid))
    host.store.conn.commit()
    assert host._next_suspended_run() == ("OTHER", "releases/v0.6")
