"""Real backend subprocess -> raw reply -> Runtime collection boundary."""
import json

import pytest

from tests.integration.test_opencode_backend import (
    DESIGN_DOCS,
    backend,
    commit_trio,
    design_assignment,
)
from tests.integration.test_opencode_backend import design_vdir as design_vdir
from tests.integration.test_opencode_backend import fake_opencode as fake_opencode
from tracks import paths
from tracks.executor.executor import Executor
from tracks.kernel.envelope import build_assignment_envelope
from tracks.kernel.events import Command
from tracks.store import Store

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("case", ["valid", "empty", "provider"])
def test_backend_raw_reply_reaches_collection_without_reclassification(
    fake_opencode, design_vdir, host_repo, trac, monkeypatch, case
):
    assert trac("init").returncode == 0
    assert trac("start", "v0.8", stdin="backend collection identity").returncode == 0
    commit_trio(host_repo, [design_vdir / doc for doc in DESIGN_DOCS])
    assignment = design_assignment("PRISM_REVIEW")
    assignment["envelope"] = build_assignment_envelope("prism:review")
    raw = "```tracks-envelope\n" + json.dumps({
        "envelope": {"kind": "prism:review", "version": 2},
        "payload": {"verdict": "pass"},
    }) + "\n```"
    if case == "empty":
        raw = ""
    monkeypatch.setenv("FAKE_OPENCODE_FINAL_TEXT", raw)
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "provider_error" if case == "provider" else "no_edit")
    result = backend(host_repo).act("prism", "PRISM_REVIEW", None, None, assignment)
    if case != "provider":
        assert result["raw_output"] == raw
    else:
        assert result["failure_class"] == "provider_unavailable"
    store = Store(paths.tracks_home(host_repo))
    try:
        run = store.active_run()
        executor = Executor(store, host_repo, run)
        before = list(store.events(run))[-1].seq
        handled = executor._format_error_shortcircuit(
            result, Command(kind="dispatch_agent", params={}, command_id="BACKEND-COLLECT"),
            None, assignment,
        )
        assert handled is (case == "empty")
        errors = [e for e in store.events(run) if e.seq > before and e.type == "format_error"]
        assert len(errors) == (1 if case == "empty" else 0)
        if case == "valid":
            assert result["status"] == "done"
            assert result["envelope"]["payload"]["verdict"] == "pass"
    finally:
        store.close()
