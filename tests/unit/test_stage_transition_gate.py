"""Backward-compatible M-TEST -> M-IMPL transition gating (v0.5).

``_NEXT_STAGE`` stays declarative (M-TEST -> M-IMPL); the version feature
gate lives at transition execution in ``_do_write_frontmatter``. Historical
v0.1/v0.4 runs complete at the M-TEST boundary; v0.5+ runs enter M-IMPL.
Version capability comparisons are numeric, never lexicographic (v0.10).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.helpers import (
    git_repo as _repo,
)
from tracks import paths
from tracks.executor.executor import Executor
from tracks.executor.version import (
    M_IMPL_FEATURE_VERSION,
    _version_tuple,
    version_at_least,
)
from tracks.kernel.events import Command
from tracks.store import Store


def _store_at_m_test_exit(repo: Path, version: str) -> Store:
    """Store whose run is at M-TEST EXIT (trace passed, test.committed,
    stage not exited). The mandatory ``story.requested`` event seeds the run's
    version capability for the executor."""
    store = Store(paths.tracks_home(repo))
    store.append("RUN", version, "story.requested", {"raw_chars": 5})
    store.append("RUN", version, "stage.entered", {"stage": "M-TEST"})
    store.append("RUN", version, "verdict.passed",
                 {"check": "trace", "detail": "closure verified"})
    store.append("RUN", version, "test.committed",
                 {"commit_sha": "abc", "test_count": 1})
    return store


def _exit_m_test(store: Store, repo: Path) -> list:
    """Run the executor's write_frontmatter for M-TEST EXIT and return events."""
    ex = Executor(store, repo, "RUN")
    ex.issue(Command(kind="write_frontmatter", params={"stage": "M-TEST"}))
    return list(store.events("RUN"))


def _of(events, event_type):
    return [e for e in events if e.type == event_type]


def _of_stage(events, event_type, stage):
    return [e for e in events if e.type == event_type
            and e.payload.get("stage") == stage]


# -- version capability helper (numeric, deterministic) ----------------------

@pytest.mark.parametrize("version,minimum,expected", [
    ("v0.1", "v0.5", False),
    ("v0.4", "v0.5", False),
    ("v0.5", "v0.5", True),
    ("v0.10", "v0.5", True),
    ("v0.6", "v0.5", True),
    ("v1.0", "v0.5", True),
    ("v0.10", "v0.9", True),
])
def test_version_at_least(version, minimum, expected):
    assert version_at_least(version, minimum) is expected


def test_version_at_least_numeric_not_lexicographic():
    """v0.10 must compare greater than v0.5 (numeric, not string order)."""
    assert version_at_least("v0.10", "v0.5") is True
    assert version_at_least("v0.10", "v0.10") is True
    assert version_at_least("v0.9", "v0.10") is False


def test_version_tuple_parses_validated_convention():
    assert _version_tuple("v0.1") == (0, 1)
    assert _version_tuple("v0.4") == (0, 4)
    assert _version_tuple("v0.5") == (0, 5)
    assert _version_tuple("v0.10") == (0, 10)


@pytest.mark.parametrize("malformed", [
    "0.5", "v0", "v0.5.1", "v0.5-beta", "0.5.0", "", "  ", "v0,5",
])
def test_malformed_version_tuples_to_none(malformed):
    assert _version_tuple(malformed) is None


@pytest.mark.parametrize("malformed", [
    "0.5", "v0", "v0.5.1", "v0.5-beta", "", None, 5,
])
def test_malformed_version_fails_closed(malformed):
    """A malformed version never unlocks the v0.5 capability: it compares
    below every minimum, matching the historical pre-v0.5 behavior."""
    assert version_at_least(malformed, M_IMPL_FEATURE_VERSION) is False


# -- M-TEST EXIT: historical runs complete at the boundary -------------------

@pytest.mark.parametrize("version", ["v0.1", "v0.4"])
def test_pre_v05_m_test_exit_ends_at_boundary(tmp_path, version):
    """v0.1/v0.4: stage.exited(M-TEST) then run.completed(boundary); no
    stage.entered(M-IMPL)."""
    repo = _repo(tmp_path)
    events = _exit_m_test(_store_at_m_test_exit(repo, version), repo)

    exits = _of_stage(events, "stage.exited", "M-TEST")
    assert len(exits) == 1
    entered_impl = _of_stage(events, "stage.entered", "M-IMPL")
    assert entered_impl == []
    completed = _of(events, "run.completed")
    assert len(completed) == 1
    assert completed[0].payload["terminal_state"] == "boundary"


def test_malformed_version_ends_at_boundary(tmp_path):
    """A malformed version fails closed: M-TEST EXIT completes at the
    boundary exactly like the historical runs."""
    repo = _repo(tmp_path)
    events = _exit_m_test(_store_at_m_test_exit(repo, "bogus"), repo)

    assert len(_of_stage(events, "stage.exited", "M-TEST")) == 1
    assert _of_stage(events, "stage.entered", "M-IMPL") == []
    completed = _of(events, "run.completed")
    assert len(completed) == 1
    assert completed[0].payload["terminal_state"] == "boundary"


# -- M-TEST EXIT: v0.5+ runs enter M-IMPL ------------------------------------

@pytest.mark.parametrize("version", ["v0.5", "v0.10"])
def test_v05_plus_m_test_exit_enters_m_impl(tmp_path, version):
    """v0.5/v0.10: stage.exited(M-TEST) then stage.entered(M-IMPL); no
    run.completed at the M-TEST boundary."""
    repo = _repo(tmp_path)
    events = _exit_m_test(_store_at_m_test_exit(repo, version), repo)

    exits = _of_stage(events, "stage.exited", "M-TEST")
    assert len(exits) == 1
    entered = _of_stage(events, "stage.entered", "M-IMPL")
    assert len(entered) == 1
    assert _of(events, "run.completed") == []


# -- event ordering: M-DESIGN -> M-TEST unaffected ---------------------------

@pytest.mark.parametrize("version", ["v0.1", "v0.5"])
def test_m_design_exit_still_enters_m_test(tmp_path, version):
    """M-DESIGN EXIT always enters M-TEST (no gate on pre-M-TEST edges)."""
    repo = _repo(tmp_path)
    store = Store(paths.tracks_home(repo))
    store.append("RUN", version, "story.requested", {"raw_chars": 5})
    store.append("RUN", version, "stage.entered", {"stage": "M-DESIGN"})
    store.append("RUN", version, "design.committed",
                 {"doc": "architecture.md", "commit_sha": "c"})
    ex = Executor(store, repo, "RUN")
    ex.issue(Command(kind="write_frontmatter", params={"stage": "M-DESIGN"}))
    events = list(store.events("RUN"))

    assert len(_of_stage(events, "stage.exited", "M-DESIGN")) == 1
    assert len(_of_stage(events, "stage.entered", "M-TEST")) == 1
    assert _of_stage(events, "stage.entered", "M-IMPL") == []
    assert _of(events, "run.completed") == []


def test_m_impl_exit_still_ends_at_boundary(tmp_path):
    """M-IMPL EXIT (v0.5) still ends at the boundary: run.completed, no
    successor stage."""
    repo = _repo(tmp_path)
    store = Store(paths.tracks_home(repo))
    store.append("RUN", "v0.5", "story.requested", {"raw_chars": 5})
    store.append("RUN", "v0.5", "stage.entered", {"stage": "M-IMPL"})
    store.append("RUN", "v0.5", "island_2_passed", {})
    ex = Executor(store, repo, "RUN")
    ex.issue(Command(kind="write_frontmatter", params={"stage": "M-IMPL"}))
    events = list(store.events("RUN"))

    assert len(_of_stage(events, "stage.exited", "M-IMPL")) == 1
    completed = _of(events, "run.completed")
    assert len(completed) == 1
    assert completed[0].payload["terminal_state"] == "boundary"
    # no second stage.entered(M-IMPL) beyond the setup entry event
    assert len(_of_stage(events, "stage.entered", "M-IMPL")) == 1


# -- crash/replay cannot emit both run.completed and stage.entered(M-IMPL) ---

def _store_with_pending_write_frontmatter(repo: Path, version: str) -> Store:
    """Store with command.issued(write_frontmatter M-TEST) but no result —
    simulates a crash between issue and the transition events."""
    store = _store_at_m_test_exit(repo, version)
    store.append(
        "RUN", version, "command.issued",
        {"command": {"kind": "write_frontmatter",
                     "params": {"stage": "M-TEST"},
                     "command_id": "CID"}},
        command_id="CID",
    )
    return store


@pytest.mark.parametrize("version,transition", [
    ("v0.1", "run.completed"),
    ("v0.4", "run.completed"),
    ("v0.5", "stage.entered"),
    ("v0.10", "stage.entered"),
])
def test_crash_replay_reconciles_write_frontmatter_once(tmp_path, version,
                                                        transition):
    """A pending write_frontmatter (crash before any transition event) is
    reconciled exactly once: stage.exited(M-TEST) plus ONE transition event.
    Replay can never emit both run.completed and stage.entered(M-IMPL), and
    the chosen transition matches the run's version capability."""
    repo = _repo(tmp_path)
    store = _store_with_pending_write_frontmatter(repo, version)

    ex = Executor(store, repo, "RUN")
    assert ex._recover() == "write_frontmatter"

    events = list(store.events("RUN"))
    assert len(_of_stage(events, "stage.exited", "M-TEST")) == 1
    completed = _of(events, "run.completed")
    entered = _of_stage(events, "stage.entered", "M-IMPL")
    assert len(completed) + len(entered) == 1
    if transition == "run.completed":
        assert len(completed) == 1 and entered == []
        assert completed[0].payload["terminal_state"] == "boundary"
    else:
        assert len(entered) == 1 and completed == []
        assert entered[0].payload["stage"] == "M-IMPL"


@pytest.mark.parametrize("version", ["v0.1", "v0.5"])
def test_reconcile_after_stage_exited_emits_no_transition(tmp_path, version):
    """Crash after stage.exited(M-TEST) persisted: reconciling the same
    write_frontmatter must not emit a second stage.exited nor any transition
    event (run.completed / stage.entered(M-IMPL) stay absent)."""
    repo = _repo(tmp_path)
    store = _store_with_pending_write_frontmatter(repo, version)
    store.append("RUN", version, "stage.exited", {"stage": "M-TEST"},
                 command_id="CID")

    ex = Executor(store, repo, "RUN")
    state = store.state("RUN")
    cmd = Command(kind="write_frontmatter", params={"stage": "M-TEST"},
                  command_id="CID")
    ex._do_write_frontmatter(cmd, state, None, reconcile=True)

    events = list(store.events("RUN"))
    assert len(_of_stage(events, "stage.exited", "M-TEST")) == 1
    assert _of_stage(events, "stage.entered", "M-IMPL") == []
    assert _of(events, "run.completed") == []
