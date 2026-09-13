"""D-32/D-28 live-replay guard integration tests.

- The executor refuses to call the (production or fake) backend when the
  enriched ``assignment.test_tasks`` violates the M-DESIGN test-task contract:
  it emits a ``stub_gap`` failed outcome instead, and the M-TEST reducer rolls
  back to M-DESIGN without consuming attempts or awaiting a Human.
- Shield WRITE requires an attributable ``tests/**/*.py`` diff: a done/no-diff
  output fails validation/retry and never publishes test.written / COLLECT.
- Valid tasks + real diff keep the happy path unchanged.
"""

from tests._support.m_test_support import make_m_test_dispatch_cmd
from tests.integration.result_checkpoint_support import (
    _init_workspace,
    _setup_m_test,
    _ShieldBackend,
    _StubBackend,
)
from tracks.executor import Executor
from tracks.kernel import decide


def _invalid_contract_workspace(tmp_path):
    """.tracks M-TEST workspace whose §8 coverage lacks the IF green condition."""
    repo, home, store, run_id, vdir = _init_workspace(tmp_path, "v0.4")
    (vdir / "acceptance.md").write_text(
        "## FR-0010 F\n\n### AC-FR0010-01\n\n  - c\n", encoding="utf-8"
    )
    (vdir / "test-plan.md").write_text(
        "## 8. AC Coverage\n\n"
        "| AC id | layer | test | IF |\n|---|---|---|---|\n"
        "| AC-FR0010-01（d） | integration | test_a | |\n",
        encoding="utf-8",
    )
    store.append(run_id, "v0.4", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.4", "stage.entered", {"stage": "M-TEST"})
    return Executor(store, repo, run_id), store, run_id


def test_production_backend_not_called_on_invalid_test_tasks(tmp_path):
    """Item 3: an invalid M-DESIGN test-task contract must NOT reach the backend;
    the executor emits a stub_gap failed Outcome, the reducer routes to
    DIAGNOSE/stub_gap, and decide() emits rollback_stage(M-DESIGN)."""
    ex, store, run_id = _invalid_contract_workspace(tmp_path)

    class BombBackend:
        def act(self, role, substate, doc, doc_path, assignment=None, worktree=None):
            raise AssertionError("backend must not be called on invalid test_tasks")

    ex.backend = BombBackend()
    ex.issue(make_m_test_dispatch_cmd())

    assert [e.type for e in store.events(run_id) if e.type == "outcome.received"]
    out = [e for e in store.events(run_id) if e.type == "outcome.received"][0]
    assert out.payload["status"] == "failed"
    assert out.payload["failure_class"] == "stub_gap"

    state = store.state(run_id)
    assert state.substate == "DIAGNOSE"
    assert state.diagnose_classification == "stub_gap"
    assert state.current_attempt == 0  # no free retries, no escalation
    assert state.status == "active" and state.awaiting is None
    cmd = decide(state)
    assert cmd.kind == "rollback_stage"
    assert cmd.params["to_stage"] == "M-DESIGN"
    assert cmd.params["reason"] == "stub_gap"


def test_stub_gap_failed_outcome_does_not_consume_shield_budget(tmp_path):
    """Three invalid-contract dispatches do NOT escalate to Human (item 4)."""
    ex, store, run_id = _invalid_contract_workspace(tmp_path)
    ex.backend = _StubBackend({"status": "done", "artifact_ref": "tests", "self_report": "wrote"})
    for _ in range(3):
        ex.issue(make_m_test_dispatch_cmd())
    state = store.state(run_id)
    assert state.current_attempt == 0
    assert state.status == "active" and not state.awaiting
    stub_outs = [
        e
        for e in store.events(run_id)
        if e.type == "outcome.received" and e.payload.get("failure_class") == "stub_gap"
    ]
    assert len(stub_outs) == 3, "stub_gap should re-fire on each re-entry, not escalate"


def test_shield_no_diff_fails_and_never_publishes(tmp_path, monkeypatch):
    """Item 5: done/no-diff Shield output enters the v0.5 no_diff peer review
    (explain -> review). A reviewer revise (the default for a stub backend
    that returns no verdict) consumes an attempt and re-dispatches Shield.
    test.written / COLLECT are never published.

    ``TRAC_ENVELOPE_DECLARE=0`` is the explicit legacy opt-out: the stub
    reply has no declared manifest (a state the declared shield:write
    contract cannot represent — it requires a non-empty manifest), so this
    test pins the LEGACY no_diff channel semantics."""
    monkeypatch.setenv("TRAC_ENVELOPE_DECLARE", "0")
    ex, store, run_id = _setup_m_test(tmp_path)
    ex.backend = _StubBackend(
        {"status": "done", "artifact_ref": "tests", "self_report": "wrote"}
    )  # no test files written

    ex.issue(make_m_test_dispatch_cmd())
    ex.run_pipeline()

    state = store.state(run_id)
    assert not [e for e in store.events(run_id) if e.type == "test.written"]
    # v0.5: no_diff.detected enters the explain->review flow instead of
    # emitting verdict.failed(check=no_diff) directly.
    detected = [e for e in store.events(run_id) if e.type == "no_diff.detected"]
    assert detected
    evaluated = [e for e in store.events(run_id) if e.type == "result.validated"]
    assert not evaluated  # validation did not pass -> no publish
    # The stub backend returns no verdict -> reviewer rejects -> revise ->
    # routes through the M-TEST verdict.failed handler (no_diff_justified),
    # re-dispatching Shield in WRITE.
    assert state.substate == "WRITE"  # re-dispatch pending

    # Second + third no-diff cycles escalate (the <=3 budget, unchanged).
    for _ in range(2):
        ex.backend = _StubBackend(
            {"status": "done", "artifact_ref": "tests", "self_report": "wrote"}
        )
        cmd = decide(store.state(run_id))
        assert cmd is not None and cmd.kind == "dispatch_agent"
        ex.issue(cmd)
        ex.run_pipeline()

    state = store.state(run_id)
    assert state.current_attempt == 3 or state.awaiting == "escalation"
    assert not [e for e in store.events(run_id) if e.type == "test.written"]


def test_valid_tasks_with_diff_unchanged_happy_path(tmp_path):
    """Item 9: valid tasks + an attributable tests diff keeps the WRITE
    pipeline publishing test.written -> COLLECT."""
    ex, store, run_id = _setup_m_test(tmp_path)
    ex.backend = _ShieldBackend(
        ex.repo,
        {
            "tests/integration/test_a.py": (
                "# AC-FR0010-01@v0.4 TRACKS-TRACE integration\n"
                'def test_a():\n    raise NotImplementedError("IF-MTEST-001")\n'
            ),
        },
    )

    ex.issue(make_m_test_dispatch_cmd())
    ex.run_pipeline()

    written = [e for e in store.events(run_id) if e.type == "test.written"]
    assert written and written[0].payload["commit_sha"]
    state = store.state(run_id)
    assert state.active_result is None
    assert state.substate == "COLLECT"
