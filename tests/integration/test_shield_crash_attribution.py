"""Item 9: Crash attribution for Shield partial untracked files.

When the executor crashes after the Shield backend writes test files but
before emitting outcome.received, the recovery must use the persisted
pre_dirty baseline (captured at dispatch time) so the crashed-invocation
test files remain attributable. Without persistence, recovery re-captures
pre_dirty (which now includes the crash-written files), making the diff
empty and causing a spurious no_diff failure.
"""

from tests.integration.result_checkpoint_support import (
    _recover_and_artifacts,
    _setup_m_test,
    _ShieldBackend,
)
from tests.m_test_support import make_m_test_dispatch_cmd

_TEST_FILE = "tests/integration/test_ac_fr0010_01.py"
_TEST_CONTENT = (
    "# AC-FR0010-01@v0.4 TRACKS-TRACE integration test\n"
    "def test_ac_fr0010_01():\n"
    "    raise NotImplementedError('IF-MTEST-001')\n"
)


def test_crash_after_shield_write_recovery_attributes_test_files(tmp_path):
    """When the executor crashes after the Shield backend writes test files
    but before outcome.received, _recover() must re-execute the pending
    dispatch and the test files must appear in result_checkpoint.artifacts
    (attributable diff). This requires pre_dirty to be persisted in the
    command.issued payload so recovery uses the original baseline."""
    ex, store, run_id = _setup_m_test(tmp_path)
    repo = ex.repo

    backend = _ShieldBackend(repo, {_TEST_FILE: _TEST_CONTENT})
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

    assert (repo / _TEST_FILE).exists(), "crash simulation must have written the test file"

    state = store.state(run_id)
    assert state.pending, "command must be pending (issued, no outcome.received)"

    ex._execute = original_execute

    artifacts = _recover_and_artifacts(ex, store, run_id, original_execute)
    assert _TEST_FILE in artifacts, (
        f"crash-written test file must be attributed after recovery; artifacts={artifacts}"
    )
