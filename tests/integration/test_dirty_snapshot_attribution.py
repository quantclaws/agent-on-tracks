"""Dirty-snapshot content-identity attribution for M-TEST Shield WRITE.

The legacy ``pre_dirty`` path-set (``post_dirty - pre_dirty``) mis-attributes
when Shield *modifies* a pre-existing dirty/untracked test file: the path is
in both sets so the diff is empty -> false ``no_diff``. The correct contract
persists a **content-identity snapshot** at dispatch time and compares
identities per path after the Agent returns.

RED cases:
1. pre-existing untracked test modified by Shield -> attributed (not no_diff).
2. pre-existing unchanged Human dirty test -> NOT attributed.
3. new untracked test -> attributed (happy path guard).
4. crash recovery uses the persisted snapshot (file already Agent-modified
   at recovery time still attributable).
5. snapshot lives in command.issued params and is JSON-serializable.
"""

import json

from tests.integration.result_checkpoint_support import (
    _recover_and_artifacts,
    _setup_m_test,
    _ShieldBackend,
)
from tests.m_test_support import make_m_test_dispatch_cmd

_PRE_EXISTING = "tests/integration/test_pre_existing.py"
_PRE_EXISTING_ORIG = "# pre-existing untracked\ndef test_pre_existing():\n    pass\n"
_PRE_EXISTING_MODIFIED = (
    "# AC-FR0010-01@v0.4 TRACKS-TRACE integration test\n"
    "def test_pre_existing():\n"
    "    raise NotImplementedError('IF-MTEST-001')\n"
)

_HUMAN_DIRTY = "tests/integration/test_human_dirty.py"
_HUMAN_DIRTY_CONTENT = (
    "# Human-authored dirty test (unchanged by Shield)\ndef test_human_dirty():\n    assert True\n"
)

_NEW_TEST = "tests/integration/test_new.py"
_NEW_TEST_CONTENT = (
    "# AC-FR0010-01@v0.4 TRACKS-TRACE integration test\n"
    "def test_new():\n"
    "    raise NotImplementedError('IF-MTEST-001')\n"
)


def _write_pre_existing(repo, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_pre_existing_untracked_test_modified_by_shield_is_attributed(tmp_path):
    """RED 1: a pre-existing untracked tests/**/*.py that Shield *modifies*
    must appear in result_checkpoint.artifacts/allowed_paths and must NOT
    trigger a no_diff failure."""
    ex, store, run_id = _setup_m_test(tmp_path)
    repo = ex.repo
    _write_pre_existing(repo, {_PRE_EXISTING: _PRE_EXISTING_ORIG})
    ex.backend = _ShieldBackend(repo, {_PRE_EXISTING: _PRE_EXISTING_MODIFIED})

    ex.issue(make_m_test_dispatch_cmd())

    outcomes = [e for e in store.events(run_id) if e.type == "outcome.received"]
    assert outcomes, "outcome.received must be emitted"
    rc = outcomes[-1].payload.get("result_checkpoint", {})
    artifacts = rc.get("artifacts", [])
    allowed = rc.get("allowed_paths", [])
    assert _PRE_EXISTING in artifacts, (
        f"pre-existing modified test must be attributed; artifacts={artifacts}"
    )
    assert _PRE_EXISTING in allowed
    no_diff = [
        e
        for e in store.events(run_id)
        if e.type == "verdict.failed" and e.payload.get("check") == "no_diff"
    ]
    assert not no_diff, "pre-existing modified test must not trigger no_diff"


def test_pre_existing_unchanged_human_dirty_test_not_attributed(tmp_path):
    """RED 2: a pre-existing Human dirty test file that Shield does NOT touch
    must NOT enter artifacts (no false attribution)."""
    ex, store, run_id = _setup_m_test(tmp_path)
    repo = ex.repo
    _write_pre_existing(repo, {_HUMAN_DIRTY: _HUMAN_DIRTY_CONTENT})
    ex.backend = _ShieldBackend(repo, {_NEW_TEST: _NEW_TEST_CONTENT})

    ex.issue(make_m_test_dispatch_cmd())

    outcomes = [e for e in store.events(run_id) if e.type == "outcome.received"]
    rc = outcomes[-1].payload.get("result_checkpoint", {})
    artifacts = rc.get("artifacts", [])
    assert _HUMAN_DIRTY not in artifacts, (
        f"unchanged Human dirty test must not be attributed; artifacts={artifacts}"
    )
    assert _NEW_TEST in artifacts


def test_new_untracked_test_is_attributed(tmp_path):
    """RED 3: a brand-new untracked tests/**/*.py written by Shield must be
    attributed (happy-path guard against the snapshot change regressing it)."""
    ex, store, run_id = _setup_m_test(tmp_path)
    ex.backend = _ShieldBackend(ex.repo, {_NEW_TEST: _NEW_TEST_CONTENT})

    ex.issue(make_m_test_dispatch_cmd())

    outcomes = [e for e in store.events(run_id) if e.type == "outcome.received"]
    rc = outcomes[-1].payload.get("result_checkpoint", {})
    artifacts = rc.get("artifacts", [])
    assert _NEW_TEST in artifacts, f"new test must be attributed; artifacts={artifacts}"


def test_recovery_uses_persisted_snapshot_for_pre_existing_modified(tmp_path):
    """RED 4: crash after Shield modifies a pre-existing test file, before
    outcome.received. Recovery must use the persisted pre_dirty_snapshot
    (original content identity) so the already-modified file is still
    attributable — even though at recovery time the file is in the
    Agent-modified state."""
    ex, store, run_id = _setup_m_test(tmp_path)
    repo = ex.repo
    _write_pre_existing(repo, {_PRE_EXISTING: _PRE_EXISTING_ORIG})
    backend = _ShieldBackend(repo, {_PRE_EXISTING: _PRE_EXISTING_MODIFIED})
    ex.backend = backend

    original_execute = ex._execute

    def _crash_after_backend_write(executor_self, cmd, state, task_id, reconcile=False):
        if cmd.kind == "dispatch_agent" and not reconcile:
            p = cmd.params
            backend.act(
                p["role"], p["substate"], p.get("doc"), None, assignment=p.get("assignment")
            )
            return
        original_execute(cmd, state, task_id, reconcile)

    ex._execute = lambda cmd, state, task_id=None, reconcile=False: _crash_after_backend_write(
        ex, cmd, state, task_id, reconcile
    )

    ex.issue(make_m_test_dispatch_cmd())

    assert (repo / _PRE_EXISTING).exists()
    state = store.state(run_id)
    assert state.pending, "command must be pending (crashed before outcome)"

    artifacts = _recover_and_artifacts(ex, store, run_id, original_execute)
    assert _PRE_EXISTING in artifacts, (
        f"recovery must attribute pre-existing modified test via snapshot; artifacts={artifacts}"
    )


def test_pre_dirty_snapshot_in_command_issued_and_json_serializable(tmp_path):
    """RED 5: command.issued params for M-TEST Shield WRITE must carry a
    ``pre_dirty_snapshot`` dict that round-trips through JSON."""
    ex, store, run_id = _setup_m_test(tmp_path)
    repo = ex.repo
    _write_pre_existing(repo, {_PRE_EXISTING: _PRE_EXISTING_ORIG})
    ex.backend = _ShieldBackend(repo, {_NEW_TEST: _NEW_TEST_CONTENT})

    ex.issue(make_m_test_dispatch_cmd())

    issued = [e for e in store.events(run_id) if e.type == "command.issued"]
    assert issued, "command.issued must be logged"
    params = issued[-1].payload["command"]["params"]
    assert "pre_dirty_snapshot" in params, (
        "pre_dirty_snapshot must be persisted in command.issued params"
    )
    snap = params["pre_dirty_snapshot"]
    assert isinstance(snap, dict) and snap, "snapshot must be a non-empty dict"
    assert _PRE_EXISTING in snap, "pre-existing dirty path must be in snapshot"
    # JSON round-trip (store already serializes, but assert explicitly).
    round_tripped = json.loads(json.dumps(snap))
    assert round_tripped == snap, "snapshot must be JSON-serializable"
    assert all(isinstance(v, str) for v in snap.values()), (
        "every snapshot identity must be a string"
    )
