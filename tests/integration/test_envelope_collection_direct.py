"""Declared reply format and transport-failure identity acceptance."""
import pytest

from tracks import paths
from tracks.executor.executor import Executor
from tracks.kernel.envelope import build_assignment_envelope
from tracks.kernel.events import Command
from tracks.store import Store

pytestmark = pytest.mark.integration

@pytest.mark.parametrize("raw", ["", '{"verdict": "pass"}'])
def test_declared_missing_envelope_is_format_error_without_semantic_attempt(
    host_repo, trac, raw
):
    assert trac("init").returncode == 0
    assert trac("start", "v0.8", stdin="closed envelope contract").returncode == 0
    store = Store(paths.tracks_home(host_repo))
    try:
        run = store.active_run()
        executor = Executor(store, host_repo, run)
        before = list(store.events(run))
        attempts = store.state(run).current_attempt
        command = Command(kind="dispatch_agent", params={}, command_id="FORMAT-PROBE")
        assignment = {"envelope": build_assignment_envelope("prism:final")}
        handled = executor._format_error_shortcircuit(
            {"raw_output": raw}, command, None, assignment
        )
        assert handled is True
        new = [e for e in store.events(run) if e.seq > before[-1].seq]
        errors = [e for e in new if e.type == "format_error"]
        assert len(errors) == 1
        assert errors[0].command_id == command.command_id
        # 2026-09-20 semantics: the format verdict consumes the attempt
        # (FR-11 evidence re-dispatch), bounded by the task budget.
        assert store.state(run).current_attempt == attempts + 1
        assert not any(e.type in {"semantic_attempt_failed", "publish.executed",
                                  "release.decided", "verdict.passed"} for e in new)
    finally:
        store.close()


@pytest.mark.parametrize("failure_class", ["provider_unavailable", "filesystem"])
def test_transport_failure_without_reply_preserves_failure_pipeline(
    host_repo, trac, failure_class
):
    assert trac("init").returncode == 0
    assert trac("start", "v0.8", stdin="transport failure identity").returncode == 0
    store = Store(paths.tracks_home(host_repo))
    try:
        run = store.active_run()
        executor = Executor(store, host_repo, run)
        before = list(store.events(run))
        result = {"status": "failed", "failure_class": failure_class}
        handled = executor._format_error_shortcircuit(
            result, Command(kind="dispatch_agent", params={}, command_id="TRANSPORT"),
            None, {"envelope": build_assignment_envelope("prism:final")},
        )
        assert handled is False
        assert list(store.events(run)) == before
        assert result == {"status": "failed", "failure_class": failure_class}
    finally:
        store.close()
