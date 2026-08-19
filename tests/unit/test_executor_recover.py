"""B32 (#32): executor validation for recover_stage.

Mirrors test_rollback_boundary.py's fixture pattern: seed a Store with the
stranded post-rollback event log, call ``_do_recover_stage`` directly, and
assert the emitted ``stage.recovered`` event (or the fail-closed rejection
with no event).
"""

from pathlib import Path

from tests.unit.helpers import git_repo as _repo
from tracks import paths
from tracks.executor.executor import Executor
from tracks.kernel.events import Command
from tracks.store import Store

# Minimal chain from M-IMPL through a stub_gap rollback to M-DESIGN/DRAFT,
# plus a human.recover that parks decide() in RECOVER_PENDING.
_POST_ROLLBACK_EVENTS = [
    ("story.requested", {"raw_chars": 1}),
    ("stage.entered", {"stage": "M-IMPL"}),
    ("stage.rolled_back", {"from_stage": "M-IMPL", "to_stage": "M-DESIGN", "reason": "stub_gap"}),
    ("human.recover", {"reason": "mis-typed stub_gap", "to_stage": "M-IMPL"}),
]


def _store_with(repo: Path, events, version: str = "v0.5") -> Store:
    store = Store(paths.tracks_home(repo))
    for t, p in events:
        store.append("RUN", version, t, p)
    return store


def _post_rollback_store(repo: Path) -> Store:
    """Store projected to RECOVER_PENDING (valid recover_stage precondition)."""
    return _store_with(repo, _POST_ROLLBACK_EVENTS)


def _recover_cmd(to_stage="M-IMPL", kind="recover_stage"):
    return Command(
        kind=kind,
        params={"to_stage": to_stage, "reason": "mis-typed stub_gap"},
        command_id="recover-cid",
    )


def _recovered_events(store, run_id="RUN"):
    return [e for e in store.events(run_id) if e.type == "stage.recovered"]


def test_recover_stage_valid_emits_stage_recovered(tmp_path):
    """Valid recover_stage (RECOVER_PENDING, to_stage=M-IMPL) emits a single
    stage.recovered event carrying the target stage."""
    repo = _repo(tmp_path)
    store = _post_rollback_store(repo)
    state = store.state("RUN")
    assert state.substate == "RECOVER_PENDING"

    ex = Executor(store, repo, "RUN")
    ex._do_recover_stage(_recover_cmd(), state, None, reconcile=False)

    events = _recovered_events(store)
    store.close()
    assert len(events) == 1
    assert events[0].payload["stage"] == "M-IMPL"
    assert events[0].payload["from_stage"] == "M-DESIGN"
    assert events[0].payload["reason"] == "mis-typed stub_gap"
    assert events[0].payload["stage"] == "M-IMPL"


def test_recover_stage_idempotent_on_reconcile(tmp_path):
    """When stage.recovered already exists for the command_id, reconcile=True
    must NOT emit a duplicate."""
    repo = _repo(tmp_path)
    store = _post_rollback_store(repo)
    store.append(
        "RUN",
        "v0.5",
        "stage.recovered",
        {"stage": "M-IMPL", "from_stage": "M-DESIGN", "reason": "mis-typed stub_gap"},
        command_id="recover-cid",
    )
    state = store.state("RUN")
    ex = Executor(store, repo, "RUN")
    ex._do_recover_stage(_recover_cmd(), state, None, reconcile=True)
    events = _recovered_events(store)
    store.close()
    assert len(events) == 1


def test_recover_stage_rejected_when_not_recover_pending(tmp_path):
    """recover_stage in a non-RECOVER_PENDING substate is fail-closed: no
    stage.recovered event is emitted (an audit record is written instead)."""
    repo = _repo(tmp_path)
    store = Store(paths.tracks_home(repo))
    store.append("RUN", "v0.5", "story.requested", {"raw_chars": 1})
    store.append("RUN", "v0.5", "stage.entered", {"stage": "M-DESIGN"})
    state = store.state("RUN")
    assert state.substate == "DRAFT"  # not RECOVER_PENDING

    ex = Executor(store, repo, "RUN")
    ex._do_recover_stage(_recover_cmd(), state, None, reconcile=False)

    events = _recovered_events(store)
    store.close()
    assert events == []


def test_recover_stage_rejected_for_non_m_impl_target(tmp_path):
    """recover_stage with a target outside the v0.6 M-IMPL whitelist is
    fail-closed: no stage.recovered event is written."""
    repo = _repo(tmp_path)
    store = _post_rollback_store(repo)
    state = store.state("RUN")
    assert state.substate == "RECOVER_PENDING"

    ex = Executor(store, repo, "RUN")
    ex._do_recover_stage(_recover_cmd(to_stage="M-SPEC"), state, None, reconcile=False)

    events = _recovered_events(store)
    store.close()
    assert events == []
