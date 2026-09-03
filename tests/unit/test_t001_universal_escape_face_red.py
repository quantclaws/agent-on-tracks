"""T-001 RED: universal escape face + v0.8 delivery wiring (FR-0274/FR-0287).

Pins the still-undelivered wiring slices of IF-RELEASE-003's CLI/kernel/
executor faces (b93 planning; plan_defect rounds added (E)-(J)):

- machine (C): ``stage.rolled_back`` must route the re-entry substate from
  the target StageDef.initial_substate (M-TEST=DISPATCH, M-IMPL=BASELINE),
  not the fossilized DRAFT literal.
- m_impl (D): the RETURNED route reason mirrors m_test — ``human_return``
  when the state carries a human return, ``diagnose_rollback`` otherwise.
- CLI (A): the canonical stage order single source
  (tuple(machine._STAGES) - M-REQ-APPROVAL + release.RELEASE_STAGES),
  strictly-upstream universal return targets, the dual-track human.return
  payload (actor/from/to/to_stage), the escape barrier event on a successful
  return, the ``--confirm`` gate over already-executed irreversible
  operations, and the ``_escape_status_fragments`` five-fragment rendering.
- executor (B): the rollback closed set extends to the impl-cycle targets
  (M-TEST/M-IMPL, canonical-derived; out-of-set still audit-blob rejected)
  and the dispatch-loop cutover consumption quarantines late outcomes
  (escape.late_outcome, never checkpointed/published).
- executor (E/G/I/J): envelope/failure chain wiring and the publish /
  known-issue / security handler registrations.

All target tests fail on the pre-fix baseline with assertion_failure on the
contract token (missing symbols are guarded with getattr asserts — no
collection/import assembly errors). Preserved-contract guards (M-DESIGN
re-entry, out-of-set rejection, diagnose_rollback mirror) intentionally pass
on the pre-fix baseline. Only unit tests are added (RED discipline,
manifest red_test_paths = tests/unit).
"""

from __future__ import annotations

import types

from tests.unit.helpers import git_repo as _git_repo
from tests.unit.m_test_support import make_dispatch_agent_payload

# -- (C) machine: rolled-back re-entry routes via StageDef.initial_substate --


# AC-FR0274/FR-0287@v0.8 TRACKS-TRACE IF-RELEASE-003 rollback re-entry
def test_stage_rolled_back_routes_via_target_initial_substate():
    """stage.rolled_back must land the target stage's StageDef
    .initial_substate: M-TEST -> DISPATCH, M-IMPL -> BASELINE (never the
    fossilized DRAFT literal)."""
    from tests.unit.helpers import seq
    from tracks.kernel import project

    base = [
        ("story.requested", {"raw_chars": 1}),
        ("stage.entered", {"stage": "M-IMPL"}),
    ]
    to_test = project(
        seq(
            *base,
            (
                "stage.rolled_back",
                {"from_stage": "M-IMPL", "to_stage": "M-TEST", "reason": "human_return"},
            ),
        )
    )
    assert (to_test.stage, to_test.substate) == ("M-TEST", "DISPATCH"), (
        f"assertion failure: a return to M-TEST must re-enter DISPATCH "
        f"(StageDef.initial_substate), got {to_test.substate!r}"
    )
    to_impl = project(
        seq(
            *base,
            (
                "stage.rolled_back",
                {"from_stage": "M-IMPL", "to_stage": "M-IMPL", "reason": "human_return"},
            ),
        )
    )
    assert (to_impl.stage, to_impl.substate) == ("M-IMPL", "BASELINE"), (
        f"assertion failure: a return to M-IMPL must re-enter BASELINE "
        f"(StageDef.initial_substate), got {to_impl.substate!r}"
    )


# AC-FR0274/FR-0287@v0.8 TRACKS-TRACE IF-RELEASE-003 preserved guard
def test_stage_rolled_back_m_design_stays_draft():
    """Preserved guard: M-DESIGN re-entry keeps the DRAFT substate (its
    StageDef.initial_substate) — the fix must not regress the author path."""
    from tests.unit.helpers import seq
    from tracks.kernel import project

    state = project(
        seq(
            ("story.requested", {"raw_chars": 1}),
            ("stage.entered", {"stage": "M-IMPL"}),
            (
                "stage.rolled_back",
                {"from_stage": "M-IMPL", "to_stage": "M-DESIGN", "reason": "human_return"},
            ),
        )
    )
    assert (state.stage, state.substate) == ("M-DESIGN", "DRAFT")


# -- (D) m_impl: RETURNED route reason mirrors m_test -------------------------


# AC-FR0274/FR-0287@v0.8 TRACKS-TRACE IF-RELEASE-003 returned reason mirror
def test_m_impl_returned_reason_mirrors_human_return():
    """A human return out of M-IMPL must carry reason=human_return on the
    rollback_stage command (m_test already mirrors; m_impl hardcodes
    diagnose_rollback)."""
    from tests.unit.helpers import seq
    from tracks.kernel import decide, project

    state = project(
        seq(
            ("story.requested", {"raw_chars": 1}),
            ("stage.entered", {"stage": "M-IMPL"}),
            (
                "human.return",
                {
                    "actor": "alice",
                    "from": "M-IMPL",
                    "to": "M-DESIGN",
                    "to_stage": "M-DESIGN",
                    "reason": "wrong plan",
                },
            ),
        )
    )
    assert state.substate == "RETURNED" and state.returned is True
    cmd = decide(state)
    assert cmd is not None and cmd.kind == "rollback_stage", (
        f"assertion failure: RETURNED must route rollback_stage, got {cmd!r}"
    )
    assert cmd.params["reason"] == "human_return", (
        f"assertion failure: a human return must carry reason=human_return, "
        f"got {cmd.params.get('reason')!r}"
    )


# AC-FR0274/FR-0287@v0.8 TRACKS-TRACE IF-RELEASE-003 preserved guard
def test_m_impl_diagnose_return_keeps_diagnose_rollback():
    """Preserved guard: a Human-approved DIAGNOSE rollback (returned=False)
    keeps reason=diagnose_rollback."""
    from tracks.kernel import machine
    from tracks.kernel.m_impl import _decide_m_impl

    state = machine.State(run_id="RUN", stage="M-IMPL", substate="RETURNED")
    state.return_target = "M-ACC"
    state.returned = False
    cmd = _decide_m_impl(state, "RETURNED")
    assert cmd is not None and cmd.params["reason"] == "diagnose_rollback"


# -- (A) CLI: canonical order + universal upstream targets --------------------


# AC-FR0274/FR-0287@v0.8 TRACKS-TRACE IF-RELEASE-003 canonical order source
def test_canonical_stage_order_single_source():
    """The canonical order single source: tuple(machine._STAGES) minus
    M-REQ-APPROVAL plus release.RELEASE_STAGES, in order."""
    import tracks.cli.main as cli
    from tracks.kernel import machine, release

    order_fn = getattr(cli, "_canonical_stage_order", None)
    assert callable(order_fn), (
        "assertion failure: cli must derive the canonical stage order "
        "(machine._STAGES - M-REQ-APPROVAL + release.RELEASE_STAGES)"
    )
    expected = tuple(
        stage for stage in machine._STAGES if stage != "M-REQ-APPROVAL"
    ) + tuple(release.RELEASE_STAGES)
    assert tuple(order_fn()) == expected, (
        f"assertion failure: canonical order mismatch, expected {expected}"
    )


# AC-FR0274/FR-0287@v0.8 TRACKS-TRACE IF-RELEASE-003 universal targets
def test_universal_return_targets_are_strictly_upstream():
    """Universal return targets = canonical stages with a smaller ordinal:
    M-IMPL may return to M-TEST/M-DESIGN, M-VERIFY may return into the impl
    cycle, no self/downstream targets, M-REQ-APPROVAL never a target."""
    import tracks.cli.main as cli

    targets_fn = getattr(cli, "_universal_return_targets", None)
    assert callable(targets_fn), (
        "assertion failure: cli must expose strictly-upstream universal "
        "return targets derived from the canonical stage order"
    )
    impl_targets = set(targets_fn("M-IMPL"))
    assert {"M-STORY", "M-SPEC", "M-ACC", "M-DESIGN", "M-TEST"} <= impl_targets, (
        f"assertion failure: M-IMPL must return into the author+test chain, "
        f"got {sorted(impl_targets)}"
    )
    assert "M-IMPL" not in impl_targets and "M-REQ-APPROVAL" not in impl_targets
    verify_targets = set(targets_fn("M-VERIFY"))
    assert {"M-IMPL", "M-TEST", "M-DESIGN"} <= verify_targets, (
        f"assertion failure: a release stage must return into the impl cycle, "
        f"got {sorted(verify_targets)}"
    )
    assert "M-VERIFY" not in verify_targets and "M-PUBLISH" not in verify_targets


def _escalation_events():
    """Events that bring M-TEST to awaiting=escalation (shared fixture)."""
    items = [
        ("story.requested", {"raw_chars": 1}),
        ("stage.entered", {"stage": "M-TEST"}),
    ]
    for attempt in range(1, 4):
        items.append(
            (
                "command.issued",
                {
                    "command": make_dispatch_agent_payload(
                        attempt=attempt,
                        review_round=1,
                        command_id=f"shield-{attempt}",
                    )
                },
            )
        )
        items.append(
            (
                "outcome.received",
                {
                    "role": "shield",
                    "status": "done",
                    "artifact_ref": "tests",
                    "self_report": "wrote",
                },
            )
        )
        items.append(
            (
                "verdict.failed",
                {
                    "check": "no_diff_justified",
                    "reason": "reviewer rejected no-diff explanation",
                    "attempt": attempt,
                },
            )
        )
    return items


def _seeded_store(repo, events):
    from tracks import paths
    from tracks.store import Store

    store = Store(paths.tracks_home(repo))
    for event_type, payload in events:
        store.append("RUN", "v0.8", event_type, payload)
    return store


# -- (A) CLI: dual-track human.return payload + escape barrier ----------------


# AC-FR0274/FR-0287@v0.8 TRACKS-TRACE IF-RELEASE-003 dual-track payload
def test_cmd_return_payload_and_barrier_event(tmp_path, monkeypatch):
    """A successful `trac return` emits the dual-track human.return payload
    (actor/from/to/to_stage — to_stage is the kernel-hard-read key) AND the
    escape.barrier_established event (the CLI is the barrier wiring site)."""
    import tracks.cli.main as cli
    import tracks.executor.escape as escape

    repo = _git_repo(tmp_path)
    store = _seeded_store(repo, _escalation_events())
    monkeypatch.setattr(escape, "_open_store", lambda: store)
    rc = cli.cmd_return(repo, "--to", "M-DESIGN", "--reason", "bad plan")
    events = list(store.events("RUN"))
    returns = [e for e in events if e.type == "human.return"]
    assert rc == 0 and returns, (
        f"assertion failure: escalation return must succeed with a "
        f"human.return event, rc={rc}, events={[e.type for e in returns]}"
    )
    payload = returns[-1].payload
    for key in ("actor", "from", "to", "to_stage"):
        assert key in payload, (
            f"assertion failure: human.return payload must carry the "
            f"dual-track key {key!r}, got {sorted(payload)}"
        )
    assert payload["to_stage"] == "M-DESIGN"
    assert any(e.type == "escape.barrier_established" for e in events), (
        "assertion failure: a successful return must establish the escape "
        "barrier (escape.barrier_established event)"
    )


# AC-FR0287-04@v0.8 TRACKS-TRACE IF-RELEASE-003 confirm gate
def test_cmd_return_confirm_gate_over_executed_ops(tmp_path, monkeypatch):
    """Crossing already-executed irreversible operations requires --confirm:
    without it the return refuses (zero human.return events); with it the
    return proceeds."""
    import tracks.cli.main as cli
    import tracks.executor.escape as escape

    repo = _git_repo(tmp_path)
    store = _seeded_store(repo, _escalation_events())
    store.append(
        "RUN",
        "v0.8",
        "publish.executed",
        {"status": "done", "operation_kind": "tag", "target": "v0.8.0"},
    )
    monkeypatch.setattr(escape, "_open_store", lambda: store)
    rc_plain = cli.cmd_return(repo, "--to", "M-DESIGN", "--reason", "r")
    assert rc_plain != 0, (
        "assertion failure: a return across executed irreversible ops must "
        "refuse without --confirm"
    )
    assert not [e for e in store.events("RUN") if e.type == "human.return"], (
        "assertion failure: the unconfirmed refusal must emit zero "
        "human.return events (already_executed report only)"
    )
    rc_confirm = cli.cmd_return(
        repo, "--to", "M-DESIGN", "--reason", "r", "--confirm"
    )
    assert rc_confirm == 0, (
        "assertion failure: --confirm must admit the return across the "
        "reported irreversible operations"
    )
    assert [e for e in store.events("RUN") if e.type == "human.return"], (
        "assertion failure: the confirmed return must emit human.return"
    )


# AC-FR0274/FR-0287@v0.8 TRACKS-TRACE IF-RELEASE-003 status fragments
def test_escape_status_fragments_render_five_faces():
    """`trac status` escape fragments render the five contract faces:
    human_return=<actor>→<to> / evidence.staled=<n> / barrier=established /
    late_outcome=quarantined / frozen_tests=unfrozen (target ≤ M-TEST)."""
    import tracks.cli.main as cli

    render = getattr(cli, "_escape_status_fragments", None)
    assert callable(render), (
        "assertion failure: cli must render the five escape status fragments"
    )

    def envelope(event_type, payload):
        return types.SimpleNamespace(type=event_type, payload=payload)

    events = [
        envelope(
            "human.return",
            {"actor": "alice", "from": "M-VERIFY", "to": "M-DESIGN",
             "to_stage": "M-DESIGN"},
        ),
        envelope("escape.barrier_established", {"cutover_seq": 9}),
        envelope("escape.late_outcome", {"status": "quarantined"}),
        envelope("evidence.staled", {"reason": "human_return"}),
        envelope("evidence.staled", {"reason": "human_return"}),
    ]
    rendered = " ".join(render(events))
    assert "human_return=alice→M-DESIGN" in rendered, (
        f"assertion failure: fragments must render human_return=<actor>→<to>, "
        f"got {rendered!r}"
    )
    assert "barrier=established" in rendered
    assert "late_outcome=quarantined" in rendered
    assert "evidence.staled=2" in rendered
    assert "frozen_tests=unfrozen" in rendered, (
        "assertion failure: a target ≤ M-TEST must report the unfrozen "
        "test freeze fragment"
    )
    late_events = [
        envelope(
            "human.return",
            {"actor": "bob", "from": "M-PUBLISH", "to": "M-PUBLISH",
             "to_stage": "M-PUBLISH"},
        ),
    ]
    rendered_late = " ".join(render(late_events))
    assert "frozen_tests=unfrozen" not in rendered_late, (
        "assertion failure: a target after M-TEST must not report the "
        "frozen_tests fragment"
    )


# -- (B) executor: rollback closed set + escape cutover -----------------------


def _executor_on(repo, events):
    from tracks.executor.executor import Executor

    store = _seeded_store(repo, events)
    executor = Executor(store, repo, "RUN")
    return store, executor


# AC-FR0274/FR-0287@v0.8 TRACKS-TRACE IF-RELEASE-003 closed-set extension
def test_rollback_closed_set_includes_impl_cycle(tmp_path):
    """The executor rollback closed set is canonical-derived and includes
    the impl-cycle targets M-TEST/M-IMPL; out-of-set stays audit-rejected."""
    from tracks.kernel.events import Command

    repo = _git_repo(tmp_path)
    store, executor = _executor_on(
        repo,
        [
            ("story.requested", {"raw_chars": 1}),
            ("stage.entered", {"stage": "M-VERIFY"}),
        ],
    )
    state = store.state("RUN")
    for target in ("M-TEST", "M-IMPL"):
        executor._do_rollback_stage(
            Command(
                kind="rollback_stage",
                params={"to_stage": target, "reason": "human_return"},
                command_id=f"C-{target}",
            ),
            state,
            None,
            False,
        )
    rolled = [
        e.payload.get("to_stage")
        for e in store.events("RUN")
        if e.type == "stage.rolled_back"
    ]
    assert rolled == ["M-TEST", "M-IMPL"], (
        f"assertion failure: the closed set must admit the impl-cycle "
        f"targets M-TEST/M-IMPL, rolled back {rolled!r}"
    )
    executor._do_rollback_stage(
        Command(
            kind="rollback_stage",
            params={"to_stage": "M-NONSENSE", "reason": "human_return"},
            command_id="C-bogus",
        ),
        state,
        None,
        False,
    )
    targets = [
        e.payload.get("to_stage")
        for e in store.events("RUN")
        if e.type == "stage.rolled_back"
    ]
    assert "M-NONSENSE" not in targets, (
        "assertion failure: out-of-set rollback targets must stay "
        "audit-blob rejected with no event"
    )


# AC-FR0287-02@v0.8 TRACKS-TRACE IF-RELEASE-003 cutover consumption
def test_late_outcomes_quarantined_never_checkpointed(tmp_path):
    """The dispatch loop's cutover consumption: with an established escape
    barrier, an outcome whose seq ≤ cutover_seq is quarantined
    (escape.late_outcome event) and never checkpointed/published; a later
    outcome passes through untouched."""
    repo = _git_repo(tmp_path)
    store, executor = _executor_on(
        repo,
        [
            ("story.requested", {"raw_chars": 1}),
            ("stage.entered", {"stage": "M-IMPL"}),
            ("escape.barrier_established", {"cutover_seq": 5}),
        ],
    )
    consume = getattr(executor, "_consume_escape_cutover", None)
    assert callable(consume), (
        "assertion failure: the executor must consume the escape cutover "
        "(quarantine late outcomes, allow post-barrier ones)"
    )
    late = consume({"dispatch_id": "D1", "seq": 3})
    assert late is True, "assertion failure: seq<=cutover outcome is late"
    quarantined = [
        e for e in store.events("RUN") if e.type == "escape.late_outcome"
    ]
    assert quarantined and quarantined[-1].payload.get("status") == "quarantined", (
        "assertion failure: the late outcome must land "
        "escape.late_outcome(status=quarantined)"
    )
    allowed = consume({"dispatch_id": "D2", "seq": 9})
    assert allowed is False, (
        "assertion failure: a post-barrier outcome must pass through"
    )


# -- (E/G/I/J) executor: envelope/failure chain + domain handlers -------------


# AC-FR0274@v0.8 TRACKS-TRACE IF-RELEASE-003 envelope/failure wiring
def test_envelope_failure_chain_wired_into_executor():
    """The dispatch loop wiring consumes the kernel/failure-review faces
    (check_envelope_parity, parse_agent_output, record_failure,
    select_failure, review_failure_chain)."""
    import tracks.executor.executor as executor_module

    for name in (
        "check_envelope_parity",
        "parse_agent_output",
        "record_failure",
        "select_failure",
        "review_failure_chain",
    ):
        assert getattr(executor_module, name, None) is not None, (
            f"assertion failure: executor must wire {name} into the "
            f"dispatch loop (architecture §1.1 Envelope/failure chain)"
        )


# AC-FR0274@v0.8 TRACKS-TRACE IF-RELEASE-003 domain handler registration
def test_release_domain_handlers_registered():
    """The executor registers the release-domain handlers: execute_publish
    (T-021), register_known_issue (T-029), assess_security (T-034) — the
    `_do_<kind>` registry consumed by decide_release_stage routing (T-039)."""
    from tracks.executor.executor import Executor

    for handler in ("_do_execute_publish", "_do_register_known_issue",
                    "_do_assess_security"):
        assert getattr(Executor, handler, None) is not None, (
            f"assertion failure: Executor must register {handler} "
            f"(must-not-drop wiring, plan_defect rounds G/I/J)"
        )
