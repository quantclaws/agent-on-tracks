"""Hotfix M-IMPL runtime contracts (FR-0245, IF-HOTFIX-008).

T-004 RED anchors:

- AC-FR0245-02 (scenario B stale reconcile, interfaces §1a/§1g): the BASELINE
  digest of a dev-scenario hotfix run binds the active release branch HEAD;
  when the active branch advances the re-freeze reports
  ``baseline.frozen(status="stale", scenario_branch_head=<new head>)`` which
  routes through the existing NEEDS_ATTENTION path. The post-release scenario
  keeps ``scenario_branch_head`` None (base=main, independent of the active
  run).
- AC-FR0245-01 (isolated fix branch, interfaces §4a): ``red.checkpointed``
  carries the RGR trailers with ``Tracks-Issue`` = the hotfix issue, and the
  checkpoint never advances the fix branch or the active release branch.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.helpers import (
    RGR_RED_DIFF,
    git_repo,
)
from tests.unit.helpers import (
    ev as _ev,
)
from tests.unit.helpers import (
    git_strip as _git,
)
from tests.unit.helpers import (
    m_impl_docs as _docs,
)
from tests.unit.helpers import (
    m_impl_graph as _graph,
)
from tests.unit.helpers import (
    m_impl_store as _store,
)
from tests.unit.helpers import (
    m_impl_task as _task,
)
from tracks import paths
from tracks.executor.executor import Executor
from tracks.kernel import apply
from tracks.kernel.events import Command
from tracks.kernel.machine import State
from tracks.store import Store


def _contract(repo: Path) -> None:
    contract = paths.project_toml_path(paths.tracks_home(repo))
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text(
        "[integration]\nframework='pytest'\npaths=['tests/integration/']\n"
        "collect='pytest --collect-only tests/integration'\n"
        "run='pytest tests/integration --junitxml={result}'\n"
        "run_selected='pytest {nodes} --junitxml={result}'\ncwd='.'\n\n"
        "[e2e]\nframework='pytest'\npaths=['tests/e2e/']\n"
        "collect='pytest --collect-only tests/e2e'\n"
        "run='pytest tests/e2e --junitxml={result}'\n"
        "run_selected='pytest {nodes} --junitxml={result}'\ncwd='.'\n\n"
        "[unit]\nframework='pytest'\npaths=['tests/unit/']\n"
        "collect='pytest --collect-only tests/unit'\n"
        "run='pytest tests/unit --junitxml={result}'\n"
        "run_selected='pytest {nodes} --junitxml={result}'\ncwd='.'\n\n"
        "[nightly]\nschedule='0 3 * * *'\nworkflow='.github/workflows/nightly.yml'\n"
        "job='nightly-regression'\nlayers=['unit', 'integration', 'e2e']\n"
        "purpose='scheduled FULL-suite regression'\n",
        encoding="utf-8",
    )
    (repo / "tests" / "integration").mkdir(parents=True)
    (repo / "tests" / "e2e").mkdir(parents=True)
    (repo / "tests" / "assets").mkdir(parents=True)


def _hotfix_repo(tmp_path: Path, issue: int) -> tuple[Path, str]:
    """Repo with the dev-scenario branch layout: an active ``releases/v0.5``
    branch carrying feature work, and ``fix/{issue}`` branched from its HEAD
    and checked out (complete_hotfix_entry side effects). Returns the repo and
    the active release branch HEAD at entry time."""
    repo = git_repo(tmp_path, gitignore=True)
    _contract(repo)
    _git(repo, "checkout", "-b", "releases/v0.5")
    (repo / "feature.txt").write_text("feature work\n", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-m", "feature progress on release branch")
    active_head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", f"fix/{issue}")
    return repo, active_head


def _hotfix_store(repo: Path, *, scenario: str, issue: int, base: str) -> Store:
    """Store seeded as a hotfix run entering M-IMPL BASELINE: entry events
    (hotfix.requested / branch.created with the entry base), the M-DESIGN
    checkpoint bound to the fix branch head, approval + issue evidence."""
    store = _store(repo)
    version = f"v0.5-hotfix-{issue}"
    store.append("RUN", version, "hotfix.requested", {"issue": issue, "scenario": scenario})
    store.append(
        "RUN",
        version,
        "branch.created",
        {
            "branch_name": f"fix/{issue}",
            "base": base,
            "commit_sha": _git(repo, "rev-parse", "HEAD"),
        },
    )
    store.append("RUN", version, "approval.recorded", {"digest": "approval-1", "actor": "human"})
    store.append("RUN", version, "issues.created", {"mapping": {"FR-0245": issue}})
    return store


def _last_frozen(store: Store) -> dict:
    frozen = [ev.payload for ev in store.events("RUN") if ev.type == "baseline.frozen"]
    assert frozen, "expected at least one baseline.frozen event"
    return frozen[-1]


def _routed(payload: dict):
    """Feed the executor's baseline.frozen payload through the kernel's public
    ``apply`` seam: AC-FR0245-02 requires the scenario-B stale reconcile to
    ride the EXISTING NEEDS_ATTENTION route (kernel/m_impl base routing), so
    the hotfix payload must not need a new route to reach it."""
    return apply(State(run_id="RUN"), _ev(1, "baseline.frozen", payload))


def test_hotfix_dev_freeze_binds_active_branch_head_and_reconciles_stale(tmp_path):
    repo, active_head = _hotfix_repo(tmp_path, issue=83)
    _docs(repo)
    store = _hotfix_store(repo, scenario="dev", issue=83, base="releases/v0.5")
    executor = Executor(store, repo, "RUN")
    command = Command("freeze_baseline", command_id="C-BASE-HF")

    executor._do_freeze_baseline(command, store.state("RUN"), None, False)
    payload = _last_frozen(store)
    assert payload["status"] == "current"
    assert payload["scenario_branch_head"] == active_head
    assert _routed(payload).substate == "PLANNING"
    frozen_digest = payload["digest"]

    # Scenario B: the active release branch advances while the fix run holds
    # its baseline; a re-freeze must reconcile against the new HEAD and report
    # stale (flow.md §16.3.3, interfaces §1g).
    _git(repo, "checkout", "releases/v0.5")
    (repo / "progress.txt").write_text("more feature work\n", encoding="utf-8")
    _git(repo, "add", "progress.txt")
    _git(repo, "commit", "-m", "active release branch advances")
    advanced_head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "fix/83")

    executor._do_freeze_baseline(command, store.state("RUN"), None, False)
    payload = _last_frozen(store)
    assert payload["status"] == "stale"
    assert payload["scenario_branch_head"] == advanced_head
    assert payload["digest"] != frozen_digest
    assert _routed(payload).substate == "NEEDS_ATTENTION"


def test_hotfix_post_release_freeze_keeps_no_scenario_branch_head(tmp_path):
    """post-release has base=main and no active-branch digest input (§1g):
    scenario_branch_head stays None even when a release branch exists."""
    repo = git_repo(tmp_path, gitignore=True)
    _contract(repo)
    _git(repo, "checkout", "-b", "releases/v0.5")
    (repo / "feature.txt").write_text("feature work\n", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-m", "feature progress on release branch")
    _git(repo, "checkout", "-b", "fix/84", "main")
    _docs(repo)
    store = _hotfix_store(repo, scenario="post-release", issue=84, base="main")
    executor = Executor(store, repo, "RUN")

    executor._do_freeze_baseline(
        Command("freeze_baseline", command_id="C-BASE-HF-PR"),
        store.state("RUN"),
        None,
        False,
    )
    payload = _last_frozen(store)
    assert payload["status"] == "current"
    assert payload["scenario_branch_head"] is None
    assert _routed(payload).substate == "PLANNING"


def test_hotfix_red_checkpoint_trailers_carry_issue_and_no_branch_leak(tmp_path):
    """AC-FR0245-01 (interfaces §4a): red.checkpointed exposes the RGR
    trailers with Tracks-Issue = the hotfix issue; the checkpoint keeps the
    fix branch head, the active branch head, and the checked-out branch
    untouched (no leak onto the active branch)."""
    repo, active_head = _hotfix_repo(tmp_path, issue=83)
    _docs(repo)
    store = _hotfix_store(repo, scenario="dev", issue=83, base="releases/v0.5")
    task = _task()
    task["issue_number"] = 83
    _graph(store, task)
    store.append(
        "RUN",
        "v0.5-hotfix-83",
        "task.started",
        {
            "task_id": task["task_id"],
            "task": task,
            "manifest": {
                "task_id": task["task_id"],
                "allowed_paths": ["tracks/app.py", "tests/unit/test_app.py"],
                "forbidden_paths": [".tracks/projects/**"],
            },
        },
    )
    store.append(
        "RUN",
        "v0.5-hotfix-83",
        "outcome.received",
        {
            "role": "devon",
            "status": "done",
            "phase": "red",
            "changed_paths": ["tests/unit/test_app.py"],
            "commands": [],
            "manifest_compliance": True,
            "pre_identity": "pre",
            "post_identity": "post",
            "implemented_if_ids": [],
            "results": [{"classification": "assertion_failure"}],
            "diff_ref": RGR_RED_DIFF,
        },
    )
    executor = Executor(store, repo, "RUN")

    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R-HF"),
        store.state("RUN"),
        None,
        False,
    )
    red = [ev.payload for ev in store.events("RUN") if ev.type == "red.checkpointed"]
    assert len(red) == 1
    assert red[0]["trailers"]["Tracks-Issue"] == "83"
    assert _git(repo, "rev-parse", "releases/v0.5") == active_head
    assert _git(repo, "rev-parse", "fix/83") == active_head
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "fix/83"
