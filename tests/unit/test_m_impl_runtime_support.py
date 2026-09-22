from __future__ import annotations

import hashlib as hashlib
import json as json
from pathlib import Path as Path

import pytest as pytest

from tests.unit.helpers import RGR_GREEN_DIFF as RGR_GREEN_DIFF
from tests.unit.helpers import RGR_RED_DIFF as RGR_RED_DIFF
from tests.unit.helpers import git_repo as git_repo
from tests.unit.helpers import git_strip as _git
from tests.unit.helpers import m_impl_docs as _docs
from tests.unit.helpers import m_impl_graph as _graph
from tests.unit.helpers import m_impl_started_task as _started_task_store
from tests.unit.helpers import m_impl_store as _store
from tests.unit.helpers import m_impl_task as _task
from tracks import paths as paths
from tracks.baseline import m_impl_baseline_digest as m_impl_baseline_digest
from tracks.baseline import m_impl_baseline_missing as m_impl_baseline_missing
from tracks.executor.executor import Executor as Executor
from tracks.executor.m_impl_runtime import (
    _m_impl_red_classification_error as _m_impl_red_classification_error,
)
from tracks.executor.m_impl_runtime import (
    _m_impl_red_classifications as _m_impl_red_classifications,
)
from tracks.executor.rgr import create_green_commit as create_green_commit
from tracks.executor.rgr import create_red_ref as create_red_ref
from tracks.executor.rgr import red_base_sha as red_base_sha
from tracks.executor.rgr import verify_lineage as verify_lineage
from tracks.executor.worktree import cleanup_worktree as cleanup_worktree
from tracks.executor.worktree import create_gate_worktree as create_gate_worktree
from tracks.kernel import decide as decide
from tracks.kernel.events import Command as Command
from tracks.store import Store as Store


def _repo(tmp_path: Path) -> Path:
    """Repo fixture with a committed .tracks/ gitignore (M-IMPL docs repo)."""
    return git_repo(tmp_path, gitignore=True)

def _contract(repo: Path) -> None:
    """Current atomic project contract (D-41): ``-q`` collect output is the
    nodeid-per-line inventory SELECT_TASK parses, and run_selected expands
    {nodes}/{result} for the per-task selected executions."""
    contract = paths.project_toml_path(paths.tracks_home(repo))
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text(
        "[integration]\nframework='pytest'\npaths=['tests/integration/']\n"
        "collect='.venv/bin/python -m pytest --collect-only -q tests/integration/'\n"
        "run='.venv/bin/python -m pytest tests/integration/ --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\n"
        "run_selected='.venv/bin/python -m pytest {nodes} --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\ncwd='.'\n\n"
        "[e2e]\nframework='pytest'\npaths=['tests/e2e/']\n"
        "collect='.venv/bin/python -m pytest --collect-only -q tests/e2e/'\n"
        "run='.venv/bin/python -m pytest tests/e2e/ --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\n"
        "run_selected='.venv/bin/python -m pytest {nodes} --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\ncwd='.'\n\n"
        "[unit]\nframework='pytest'\npaths=['tests/unit/']\n"
        "collect='.venv/bin/python -m pytest --collect-only -q tests/unit/'\n"
        "run='.venv/bin/python -m pytest tests/unit/ --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\n"
        "run_selected='.venv/bin/python -m pytest {nodes} --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\ncwd='.'\n\n"
        "[nightly]\nschedule='0 3 * * *'\nworkflow='.github/workflows/nightly.yml'\n"
        "job='nightly-regression'\nlayers=['unit', 'integration', 'e2e']\n"
        "purpose='scheduled FULL-suite regression'\n",
        encoding="utf-8",
    )
    (repo / "tests" / "integration").mkdir(parents=True)
    (repo / "tests" / "e2e").mkdir(parents=True)
    (repo / "tests" / "assets").mkdir(parents=True)

def _executor(repo: Path, store: Store) -> Executor:
    return Executor(store, repo, "RUN")

class _RecordingBackend:
    def __init__(self, result):
        self.result = result
        self.assignments = []

    def act(self, role, substate, doc, doc_path, assignment=None, worktree=None):
        self.assignments.append((role, substate, assignment))
        return dict(self.result)

def _structured_outcome(
    phase: str,
    changed: list[str],
    r_identity=None,
    classification: str | None = None,
    verdict: str | None = None,
    diff_ref: str | None = None,
):
    outcome = {
        "role": "devon",
        "status": "done",
        "phase": phase,
        "changed_paths": changed,
        "commands": [],
        "manifest_compliance": True,
        "pre_identity": "pre",
        "post_identity": "post",
        "implemented_if_ids": [],
        **({"r_identity": r_identity} if r_identity else {}),
        **({"diff_ref": diff_ref} if diff_ref is not None else {}),
    }
    if classification is not None:
        outcome["results"] = [{"classification": classification}]
    if verdict is not None:
        outcome["verdict"] = verdict
    return outcome

_RGR_GREEN_DIFF = (
    "diff --git a/tracks/app.py b/tracks/app.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/tracks/app.py\n"
    "@@ -0,0 +1 @@\n"
    "+IMPLEMENTED = True\n"
)

def _run_red_gate(executor: Executor, store: Store, outcome: dict):
    store.append("RUN", "v0.5", "outcome.received", outcome)
    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "RED_GATE"}, command_id="C-GATE"),
        store.state("RUN"),
        None,
        False,
    )
    return list(store.events("RUN"))[-1]

def _checkpoint_red(
    executor: Executor,
    store: Store,
    *,
    changed_paths=None,
    classification="assertion_failure",
    verdict="assertion_failure",
    diff_ref=RGR_RED_DIFF,
    command_id="C-R",
):
    """Record the canonical RED outcome and checkpoint it through Runtime."""
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome(
            "red",
            changed_paths if changed_paths is not None else ["tests/unit/test_app.py"],
            classification=classification,
            verdict=verdict,
            diff_ref=diff_ref,
        ),
    )
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id=command_id),
        store.state("RUN"),
        None,
        False,
    )

def _impl_only_started_store(repo: Path, task: dict) -> Store:
    """task.started manifest whose allowed_paths grants impl files only.

    Mirrors T-014 of run 01KZTHE7: test_refs are frozen integration/e2e
    suites, so no test path enters allowed_paths - yet Devon's RED evidence
    is a failing test that must live in tests/unit/ (run 01KZTHE7 seq 993
    rejected it as "outside manifest").
    """
    _docs(repo)
    # project.toml [layout.devon] is the writable set the RED evidence check
    # consults to recognise Devon test dirs (tests/unit/).
    paths.project_toml_path(paths.tracks_home(repo)).parent.mkdir(
        parents=True, exist_ok=True
    )
    paths.project_toml_path(paths.tracks_home(repo)).write_text(
        "[unit]\nframework='pytest'\npaths=['tests/unit/']\n"
        "collect='pytest --collect-only tests/unit'\n"
        "run='pytest tests/unit --junitxml={result}'\n"
        "run_selected='pytest {nodes} --junitxml={result}'\ncwd='.'\n\n"
        "[integration]\nframework='pytest'\npaths=['tests/integration/']\n"
        "collect='pytest --collect-only tests/integration'\n"
        "run='pytest tests/integration --junitxml={result}'\n"
        "run_selected='pytest {nodes} --junitxml={result}'\ncwd='.'\n\n"
        "[e2e]\nframework='pytest'\npaths=['tests/e2e/']\n"
        "collect='pytest --collect-only tests/e2e'\n"
        "run='pytest tests/e2e --junitxml={result}'\n"
        "run_selected='pytest {nodes} --junitxml={result}'\ncwd='.'\n\n"
        "[nightly]\nschedule='0 3 * * *'\nworkflow='.github/workflows/nightly.yml'\n"
        "job='nightly-regression'\nlayers=['unit', 'integration', 'e2e']\n"
        "purpose='scheduled FULL-suite regression'\n\n"
        "[layout]\n\n"
        "[layout.devon]\nwritable=['tracks/', 'tests/unit/']\n",
        encoding="utf-8",
    )
    store = _store(repo)
    _graph(store, task)
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {
            "task_id": task["task_id"],
            "task": task,
            "manifest": {
                "task_id": task["task_id"],
                "allowed_paths": ["tracks/app.py"],
                "forbidden_paths": ["tests/integration/**", "tests/e2e/**"],
            },
        },
    )
    return store

_OUTSIDE_MANIFEST_DIFF = (
    "diff --git a/tooling/admin.py b/tooling/admin.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/tooling/admin.py\n"
    "@@ -0,0 +1 @@\n"
    "+ADMIN = True\n"
)

_SECRET_LINE_DIFF = (
    "diff --git a/tracks/app.py b/tracks/app.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/tracks/app.py\n"
    "@@ -0,0 +1 @@\n"
    '+SECRET = "sk-0123456789abcdef0123456789abcdef01234567"\n'
)

def _immutable_r_checkpoint(repo: Path, task: dict, *, attempt: int = 1) -> dict:
    """Create a real immutable R commit containing the task's exact unit node
    (``tests/unit/test_app.py::test_app``, via the canonical RGR_RED_DIFF) and
    return its ``red.checkpointed`` payload. D-41: SELECT_TASK expands
    test_refs against R, so a fake sha would fail closed before any gate."""
    ref = create_red_ref(
        repo=str(repo),
        run_id="RUN",
        task_id=task["task_id"],
        attempt=attempt,
        test_diff=RGR_RED_DIFF,
        base_sha=_git(repo, "rev-parse", "HEAD"),
    )
    return {
        "ref": ref.ref,
        "r_sha": ref.sha,
        "task_id": task["task_id"],
        "attempt": attempt,
    }

def _write_if_mapped_integration_test(root) -> Path:
    """Materialize the integration node the shared test-plan §8 row maps to
    IF-IMPL-001 (target ``tests/integration/test_app.py``); SELECT_TASK unions
    it into the selected set and executes it through contract run_selected."""
    target = Path(root) / "tests" / "integration" / "test_app.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def test_app():\n    assert True\n", encoding="utf-8")
    return target

def _selection_failure_evidence(payload: dict) -> dict:
    """Parse the actionable TaskSelectionFailure evidence JSON: the failed
    GREEN verdict must carry selection_id/outcomes_ref/failed_nodes."""
    evidence = json.loads(payload.get("evidence") or "{}")
    assert {"selection_id", "outcomes_ref", "failed_nodes"} <= set(evidence), (
        f"selected-test failure evidence lacks actionable identity fields: {evidence}"
    )
    assert evidence["selection_id"]
    assert evidence["outcomes_ref"].startswith(".tracks/runtime/blobs/")
    assert evidence["failed_nodes"], "a failed selection must name its failed nodes"
    return evidence

def _gate_manifest(task, *, unit_commands=None):
    """Manifest with the assigned Runtime unit command (executor schema)."""
    return {
        "task_id": task["task_id"],
        "allowed_paths": ["tracks/app.py", "tests/unit/test_app.py"],
        "forbidden_paths": [".tracks/projects/**"],
        "unit_commands": list(unit_commands or [".venv/bin/python -m pytest -n 4 tests/unit"]),
    }

def _green_outcome(task, r_sha, *, agent_cwd=None, diff=None):
    outcome = _structured_outcome(
        "green",
        ["tracks/app.py"],
        r_identity=r_sha,
        diff_ref=diff or RGR_GREEN_DIFF,
    )
    outcome["commands"] = [
        {
            "cmd": ".venv/bin/python -m pytest -n 4 tests/unit",
            "result": "pass",
            **({"cwd": agent_cwd} if agent_cwd is not None else {}),
        }
    ]
    outcome["results"] = [{"classification": "pass"}]
    return outcome

def _write_failing_unit_test(repo: Path, *, passes: bool = False) -> Path:
    unit = repo / "tests" / "unit" / "test_app.py"
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(
        "def test_app():\n    assert True\n" if passes else "def test_app():\n    assert False\n",
        encoding="utf-8",
    )
    return unit

def _green_gate_with_r_checkpoint(
    repo, store, executor, *, changed_paths=None, r_sha=None, diff_ref=None
):
    """Checkpoint R from a genuine RED outcome, then stage a GREEN outcome
    (with the R-frozen test materialized as a passing test so the Runtime unit
    re-execution succeeds and the gate reaches the regression decision). The
    caller runs the gate and drives additional working-tree setup first."""
    _checkpoint_red(executor, store)
    red = [e for e in store.events("RUN") if e.type == "red.checkpointed"]
    resolved = r_sha or (red[-1].payload["r_sha"] if red else None)
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    outcome = _structured_outcome(
        "green",
        changed_paths or ["tracks/app.py"],
        r_identity=resolved,
        diff_ref=diff_ref if diff_ref is not None else RGR_GREEN_DIFF,
    )
    outcome["commands"] = [
        {"cmd": ".venv/bin/python -m pytest -n 4 tests/unit", "result": "pass"}
    ]
    outcome["results"] = [{"classification": "pass"}]
    store.append("RUN", "v0.5", "outcome.received", outcome)
    (repo / "tracks").mkdir(parents=True, exist_ok=True)
    (repo / "tracks" / "app.py").write_text('IMPLEMENTED_IF = "IF-IMPL-001"\n', encoding="utf-8")
    _write_failing_unit_test(repo, passes=True)
    return resolved

_TASK_REVIEW_FLAWS = (
    "scope_overflow",
    "lineage",
    "secret_line",
    "missing_ac",
    "budget_overflow",
)

def _task_review_scenario(repo, flaw):
    """Shared TASK_REVIEW fixture reaching the review gate with one observable
    defect in the Runtime-authoritative candidate/lineage state.

    The Green commit is real (create_green_commit on the immutable R commit's
    derived base B) and its diff/trailers carry exactly the named flaw:
    scope_overflow -> actual G diff is only an outside-manifest path;
    secret_line -> actual G diff is only a secret-shaped added line on an
    allowed path; missing_ac -> actual G has an empty combined provenance
    trailer; lineage -> actual G carries a wrong Tracks-R; budget_overflow ->
    budget history only (G otherwise valid)."""
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome(
            "red",
            ["tests/unit/test_app.py"],
            classification="assertion_failure",
            verdict="assertion_failure",
            diff_ref=RGR_RED_DIFF,
        ),
    )
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R"),
        store.state("RUN"),
        None,
        False,
    )
    r_sha = [e for e in store.events("RUN") if e.type == "red.checkpointed"][-1].payload["r_sha"]
    base_sha = red_base_sha(str(repo), r_sha)
    assert base_sha is not None, "immutable R commit must resolve its base B"
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    if flaw == "scope_overflow":
        impl_diff = _OUTSIDE_MANIFEST_DIFF
    elif flaw == "secret_line":
        impl_diff = _SECRET_LINE_DIFF
    else:
        impl_diff = RGR_GREEN_DIFF
    store.append("RUN", "v0.5", "outcome.received", _green_outcome(task, r_sha, diff=impl_diff))
    store.append("RUN", "v0.5", "verdict.passed", {"check": "green"})
    if flaw == "budget_overflow":
        # Inflate the task's OWN failure count beyond its budget (=3). The
        # budget is per-task (run 01M2QTJB T-008): unattributed or other
        # tasks' verdict.failed events must not consume this budget.
        for attempt in range(1, 5):
            store.append(
                "RUN",
                "v0.5",
                "verdict.failed",
                {"check": "budget", "attempt": attempt},
                task_id=task["task_id"],
            )
    trailer_r = "0" * 40 if flaw == "lineage" else r_sha
    ac_refs = [] if flaw == "missing_ac" else ["AC-FR0001-01", "FR-0001"]
    g = create_green_commit(
        repo=str(repo),
        run_id="RUN",
        task_id=task["task_id"],
        attempt=1,
        impl_diff=impl_diff,
        base_sha=base_sha,
        r_sha=trailer_r,
        issue_number=task["issue_number"],
        ac_refs=ac_refs,
    )
    store.append(
        "RUN",
        "v0.5",
        "green.committed",
        {
            "g_sha": g.sha,
            "task_id": task["task_id"],
            "attempt": 1,
            "r_sha": r_sha,
            "base_sha": base_sha,
            "trailers": g.trailers,
        },
    )
    _git(repo, "reset", "--hard", g.sha)
    store.append(
        "RUN",
        "v0.5",
        "refactor.no_change",
        {"task_id": task["task_id"], "reason": "no improvements"},
    )
    return executor, store

def _add_lint_section(repo: Path, check: str) -> None:
    """(Re)write the [lint] section -- replace, never append: a duplicate
    TOML table would make the whole contract unloadable."""
    contract = paths.project_toml_path(paths.tracks_home(repo))
    text = contract.read_text(encoding="utf-8")
    marker = text.find("\n[lint]\n")
    if marker != -1:
        text = text[:marker]
    contract.write_text(text + f"\n[lint]\ncheck = '{check}'\n", encoding="utf-8")

def _fake_linter(repo: Path, *, exit_code: int, output: str) -> str:
    script = repo / "fake_lint.sh"
    script.write_text(
        f"#!/bin/sh\nprintf '%s\\n' {output!r}\nexit {exit_code}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return "./fake_lint.sh"

def _red_gate_lint_store(repo: Path):
    store, task = _started_task_store(repo)
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {"task_id": task["task_id"], "task": task, "manifest": _gate_manifest(task)},
    )
    return store, task

def _runtime(tmp_path) -> Executor:
    """Bare executor for the pure ref-resolution helpers (no event history
    needed: _expand_unit_refs/_acceptance_anchors/_integration_row_targets
    are inventory-in/selection-out functions)."""
    repo = _repo(tmp_path)
    store = Store(paths.tracks_home(repo))
    return Executor(store, repo, "RUN")

_PLAN_WITH_TWO_FLOWS = (
    "# Test plan\n\n## 8. AC Coverage\n\n"
    "| AC id | layer | test | IF |\n|---|---|---|---|\n"
    "| AC-FR0001-01 | integration | "
    "`tests/integration/test_app.py::test_flow_a` + "
    "`tests/integration/test_app.py::test_flow_b` | IF-IMPL-001 |\n"
)

def _b83_task(tid: str, scope: str, ref: str) -> dict:
    return {
        **_task(ref),
        "task_id": tid,
        "scope_boundary": scope,
    }


__all__ = [
    name
    for name in globals()
    if name not in {"__name__", "__file__", "__package__", "__loader__", "__spec__", "__cached__", "__builtins__", "__all__"}
]


def test_task_budget_counts_only_own_failures(tmp_path):
    """Live defect (run 01M2QTJB T-008, 2026-09-20): the budget check had no
    task filter — a busy stretch of OTHER tasks' verdict.failed events
    fail-closed the next task's review, and each budget rejection itself
    emitted another counted failure (snowball). Per-task: only the task's
    own post-retry failures consume its budget; cross-task noise does not
    (Prism budget-R1: call the production function, no source inspection)."""
    from types import SimpleNamespace

    from tracks.executor.m_impl_green import _task_review_failure

    task = SimpleNamespace(task_id="T-008", budget=1)
    base_events = [
        {"seq": 1, "type": "human.retry", "task_id": None, "payload": {}},
        # Other tasks' busy stretch: three failures, none of them ours.
        {"seq": 2, "type": "verdict.failed", "task_id": "T-909", "payload": {}},
        {"seq": 3, "type": "verdict.failed", "task_id": None, "payload": {}},
        {"seq": 4, "type": "verdict.failed", "task_id": None,
         "payload": {"task_id": "T-907"}},
    ]
    # g_sha=None -> the no-change review path: diff checks vacuous, only the
    # budget check runs (the exact live shape that tripped T-008).
    assert _task_review_failure(None, "RUN", base_events, task, None, None,
                                {}, [], "T-008", 1) is None
    # One OWN failure at budget=1 stays within (1 > 1 is False)...
    within = [*base_events,
              {"seq": 5, "type": "verdict.failed", "task_id": "T-008", "payload": {}}]
    assert _task_review_failure(None, "RUN", within, task, None, None,
                                {}, [], "T-008", 1) is None
    # ...a second OWN failure trips it.
    tripped = [*within,
               {"seq": 6, "type": "verdict.failed", "task_id": "T-008", "payload": {}}]
    result = _task_review_failure(None, "RUN", tripped, task, None, None,
                                  {}, [], "T-008", 1)
    assert result is not None and result["check"] == "budget"


def test_impl_defect_echo_guard_superseded_by_later_green_pass(tmp_path):
    """Live defect (run 01M2QTJB T-019, 2026-09-22): gate failed impl_defect,
    Devon fixed inside the attempt, the gate re-ran and PASSED -- and
    commit_green still refused on the stale impl_defect, sending the run
    into a self-referential DIAGNOSE loop. A task's green pass recorded
    after an impl_defect supersedes it for the commit guard."""
    from types import SimpleNamespace

    class _Ev:
        def __init__(self, seq, type, payload):
            self.seq, self.type, self.payload = seq, type, payload

    events = [
        _Ev(10, "verdict.failed", {"check": "impl_defect", "task_id": "T-9", "attempt": 1}),
        _Ev(20, "verdict.passed", {"check": "green", "task_id": "T-9"}),
    ]
    host = SimpleNamespace(store=SimpleNamespace(events=lambda _r: events), run_id="RUN")
    from tracks.executor.m_impl_green import MImplGreenMixin

    # The green pass at seq 20 supersedes the impl_defect at seq 10.
    assert MImplGreenMixin._attempt_impl_defect_recorded(host, "T-9", 1, 0) is False

    # An impl_defect AFTER the latest green pass still blocks (real order).
    events.append(_Ev(30, "verdict.failed", {"check": "impl_defect", "task_id": "T-9", "attempt": 1}))
    assert MImplGreenMixin._attempt_impl_defect_recorded(host, "T-9", 1, 0) is True

    # A green pass after THAT clears it again.
    events.append(_Ev(40, "verdict.passed", {"check": "green", "task_id": "T-9"}))
    assert MImplGreenMixin._attempt_impl_defect_recorded(host, "T-9", 1, 0) is False

    # Other tasks' green passes never interfere.
    events.append(_Ev(50, "verdict.passed", {"check": "green", "task_id": "T-8"}))
    assert MImplGreenMixin._attempt_impl_defect_recorded(host, "T-9", 1, 0) is False
