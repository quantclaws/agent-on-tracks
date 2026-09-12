"""``trac`` Human gate command face: approve/return/recover/retry/abandon.

Extracted from :mod:`tracks.cli.main` for module-size compliance (C0302):
the FR-0180 approval gate, the SM-01.13 rollback approval, the
IF-RELEASE-003 return/escape gate, the escalation retry, the B32
M-IMPL recover gate, and the SM-01.21 abandon termination exit.
``tracks.cli.main`` re-exports every name below.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from tracks import paths
from tracks.baseline import baseline_summary, revision_digest
from tracks.store import Store

from .common import (
    _canonical_stage_order,
    _err,
    _format_state,
    _human_actor,
    _resolve_actor,
    active_run_context,
)
from .hotfix_cmd import _approve_hotfix_gap, _hotfix_gap_exit
from .release_cmd import (
    _already_executed_ops,
    _establish_escape_barrier,
    _stale_downstream_evidence,
)


def _retry_gate_error(state, clear_evidence: bool) -> str | None:
    if clear_evidence:
        if state.awaiting != "escalation" and not (
            state.status == "active" and state.awaiting is None
        ):
            return (
                "retry --clear-evidence requires escalation or a clean "
                f"active state (status={state.status} "
                f"awaiting={state.awaiting or 'nothing'})"
            )
    elif state.awaiting != "escalation" and not (
        state.stage == "M-IMPL"
        and state.awaiting == "rollback"
        and (state.last_failure or {}).get("check") == "lineage"
    ):
        return f"run not awaiting escalation (awaiting={state.awaiting or 'nothing'})"
    return None


def cmd_retry(repo: Path, *args: str) -> int:
    usage = "usage: trac retry [--actor NAME] [--clear-evidence]"
    actor = None
    clear_evidence = False
    args = list(args)
    i = 0
    while i < len(args):
        if args[i] == "--actor" and i + 1 < len(args):
            actor = args[i + 1]
            i += 2
        elif args[i] == "--clear-evidence":
            clear_evidence = True
            i += 1
        else:
            return _err(usage)
    with active_run_context(repo) as opened:
        if opened is None:
            return _err("no active run")
        home, store, run_id = opened
        state = store.state(run_id)
        gate_err = _retry_gate_error(state, clear_evidence)
        if gate_err is not None:
            return _err(gate_err)
        store.append(
            run_id,
            state.version,
            "human.retry",
            {"actor": actor or _human_actor(repo), "clear_evidence": clear_evidence},
        )
        state = store.state(run_id)
    print("human.retry event appended; escalation gate cleared; attempt budget reset")
    print(f"run {run_id}: {_format_state(state)}")
    return 0


def _approval_gate(store: Store, run_id: str):
    """FR-0180: approve/return are only legal at a Human gate.

    Two gates accept ``approve``: M-REQ-APPROVAL (awaiting=approval) and
    DIAGNOSE ac_gap/spec_gap rollback at awaiting=rollback: SM-01.13 for
    M-TEST and FR-0150 four-way routing for M-IMPL. The kernel reducer
    (_on_human_approval) already accepts both stages; the CLI gate lagged,
    stranding run 01KZTHE7 at M-IMPL awaiting=rollback (2026-08-17)."""
    state = store.state(run_id)
    if state.awaiting == "rollback" and state.stage in ("M-TEST", "M-IMPL"):
        return state, None  # SM-01.13: Human approves the rollback
    if state.stage != "M-REQ-APPROVAL" or state.awaiting != "approval":
        return None, (
            f"run not awaiting approval (stage={state.stage} "
            f"awaiting={state.awaiting or 'nothing'})"
        )
    return state, None


# B57 re-fix (#73): rollback targets the Human may choose at approval time.
# Exactly the stages the kernel presets for gap-typed rollbacks from
# M-TEST/M-IMPL (ac_gap -> M-ACC, spec_gap -> M-SPEC, stub_gap -> M-DESIGN);
# a lineage park presets none -- `--to` is then REQUIRED. Forward re-entry
# without discarding the stage (stay in M-IMPL) is NOT a rollback: it goes
# through `trac recover --to M-IMPL` (B32 #32).
_APPROVE_ROLLBACK_TARGETS = ("M-ACC", "M-SPEC", "M-DESIGN")


def _approve_rollback_target(state, to_stage: str | None) -> tuple[str | None, str | None]:
    """Resolve the rollback target for an SM-01.13 approval.

    Returns (target, err). ``--to`` is validated against the closed set and
    is only an M-IMPL concern (M-TEST rollbacks always carry a gap-typed
    preset). A lineage park presets nothing: the Human MUST choose; a preset
    gap-typed target stays authoritative unless explicitly overridden.
    """
    if to_stage is not None:
        if state.stage != "M-IMPL":
            return None, "--to is only valid for M-IMPL rollback approvals"
        if to_stage not in _APPROVE_ROLLBACK_TARGETS:
            return None, (
                "invalid --to target: choose one of " + ", ".join(_APPROVE_ROLLBACK_TARGETS)
            )
    target = to_stage or state.return_target
    if not target:
        return None, (
            "rollback target not set: pass --to {"
            + "|".join(_APPROVE_ROLLBACK_TARGETS)
            + "} (to re-enter M-IMPL without redoing the design, approve "
            "--to M-DESIGN and then `trac recover --to M-IMPL` -- recover "
            "only unlocks after the rollback has executed)"
        )
    return target, None


def _parse_approve_args(args: list[str]) -> tuple[str | None, str | None, str | None]:
    """Parse `trac approve [--actor NAME] [--to STAGE]`.

    Returns (actor, to_stage, err).
    """
    actor = None
    to_stage = None
    while args:
        flag = args.pop(0)
        if flag == "--actor" and args:
            actor = args.pop(0)
        elif flag == "--to" and args:
            to_stage = args.pop(0)
        else:
            return None, None, "usage: trac approve [--actor NAME] [--to STAGE]"
    return actor, to_stage, None


def cmd_approve(repo: Path, *args) -> int:
    actor, to_stage, err = _parse_approve_args(list(args))
    if err:
        return _err(err)
    with active_run_context(repo) as opened:
        if opened is None:
            return _err("no active run")
        home, store, run_id = opened
        state, err = _approval_gate(store, run_id)
        if err:
            return _err(err)
        # FR-0248 (IF-HOTFIX-009): hotfix ac_gap/spec_gap Human decision ->
        # human.approval evidence -> backlog.recorded -> run.completed terminal.
        if _hotfix_gap_exit(state):
            return _approve_hotfix_gap(repo, store, run_id, state, actor)
        # SM-01.13: M-TEST / M-IMPL rollback approval needs no digest check
        if state.awaiting == "rollback" and state.stage in ("M-TEST", "M-IMPL"):
            target, terr = _approve_rollback_target(state, to_stage)
            if terr:
                return _err(terr)
            actor = _resolve_actor(repo, actor)
            store.append(
                run_id,
                state.version,
                "human.approval",
                {
                    "actor": actor,
                    "digest": None,
                    "to_stage": target,
                    "ts": datetime.now(timezone.utc).isoformat(),
                },
            )
            print(f"approved rollback to {target}")
            return 0
        vdir = paths.version_dir(home, state.version)
        digest = revision_digest(vdir)
        previews = [e for e in store.events(run_id) if e.type == "preview.generated"]
        if not previews or previews[-1].payload["digest"] != digest:
            # C-02: the trio changed under the reviewed preview — reject THIS
            # approve (not the run) and regenerate the preview for re-review.
            store.append(
                run_id,
                state.version,
                "preview.generated",
                {"digest": digest, "summary": baseline_summary(vdir)},
            )
            return _err(
                "baseline changed since preview: approve rejected, "
                "preview regenerated — review and approve again"
            )
        actor = _resolve_actor(repo, actor)
        store.append(
            run_id,
            state.version,
            "human.approval",
            {"actor": actor, "digest": digest, "ts": datetime.now(timezone.utc).isoformat()},
        )
    print(f"approved {digest}")
    return 0


# trac return target sets (item: state-specific M-DESIGN). The syntax union
# includes M-DESIGN, but each gate enforces its own closed target set:
#   - M-REQ-APPROVAL (awaiting=approval): only upstream requirement stages;
#     forward M-DESIGN is rejected (no event, SM-05.7a / C-03).
#   - any author/review stage (M-STORY|M-SPEC|M-ACC|M-DESIGN|M-TEST) at
#     awaiting=escalation: current-or-earlier author stage (per-stage closed
#     set, see _escalation_return_targets). M-TEST itself is not a re-author
#     target (existing semantics); M-REQ-APPROVAL is never a return target.
_RETURN_STAGES_APPROVAL = ("M-STORY", "M-SPEC", "M-ACC")
_RETURN_STAGES_ESCALATION = ("M-STORY", "M-SPEC", "M-ACC", "M-DESIGN")
# Author-stage chain in dependency order. M-TEST escalation reuses the full
# chain (M-TEST itself is never a target); M-REQ-APPROVAL is intentionally
# absent - it is never a return target.
_RETURN_AUTHOR_STAGES = ("M-STORY", "M-SPEC", "M-ACC", "M-DESIGN")


def _universal_return_targets(stage: str) -> tuple[str, ...]:
    """IF-RELEASE-003 / FR-0287: universal return targets — the canonical
    stages with a strictly smaller ordinal than ``stage`` (no self, no
    downstream, M-REQ-APPROVAL never a target). Unknown stages -> ()."""
    order = _canonical_stage_order()
    if stage not in order:
        return ()
    return order[: order.index(stage)]


def _escalation_return_targets(stage: str) -> tuple[str, ...]:
    """Closed per-stage set of `--to` targets for an escalation return.

    The Human may return to the current or an earlier author stage:
    M-STORY -> {M-STORY}; M-SPEC -> {M-STORY,M-SPEC}; M-ACC -> ...+M-ACC;
    M-DESIGN -> ...+M-DESIGN; M-TEST -> all four author stages (M-TEST itself
    is not a re-author target, existing semantics). Forward targets and
    M-REQ-APPROVAL are never allowed. Returns () for stages that cannot
    escalate-return (M-REQ-APPROVAL, M-START, unknown)."""
    if stage in _RETURN_AUTHOR_STAGES:
        return _RETURN_AUTHOR_STAGES[: _RETURN_AUTHOR_STAGES.index(stage) + 1]
    if stage == "M-TEST":
        return _RETURN_STAGES_ESCALATION
    return ()


def _return_gate(store: Store, run_id: str):
    """trac return gate. Returns (state, allowed_targets, err).

    Two gates accept `return`:
      - M-REQ-APPROVAL (awaiting=approval): upstream requirement stages only.
      - an author/review stage (M-STORY|M-SPEC|M-ACC|M-DESIGN|M-TEST) at
        awaiting=escalation: current-or-earlier author stage (per-stage set).
    M-TEST (awaiting=rollback) is handled by `approve`, not `return`."""
    state = store.state(run_id)
    if state.stage == "M-REQ-APPROVAL" and state.awaiting == "approval":
        return state, _RETURN_STAGES_APPROVAL, None
    # IF-RELEASE-003 / FR-0287 universal sources (interfaces §2d): M-IMPL at
    # NEEDS_ATTENTION or DIAGNOSE-escalation is a legal return source whose
    # targets are the canonical strictly-upstream stages — this branch MUST
    # precede the author-escalation branch, whose closed author set returns
    # no targets for M-IMPL and would otherwise reject the legal source.
    if state.stage == "M-IMPL" and (
        state.substate == "NEEDS_ATTENTION" or state.awaiting == "escalation"
    ):
        return state, _universal_return_targets("M-IMPL"), None
    if state.awaiting == "escalation":
        allowed = _escalation_return_targets(state.stage)
        if allowed:
            return state, allowed, None
        # Non-author stages (the re-enterable release stages) fall through
        # to the universal canonical-upstream set instead of dead-ending.
        if state.stage in _canonical_stage_order():
            universal = _universal_return_targets(state.stage)
            if universal:
                return state, universal, None
        return state, (), (f"escalation at stage {state.stage} has no return targets")
    return (
        state,
        (),
        (
            f"run not awaiting approval or escalation (stage={state.stage} "
            f"awaiting={state.awaiting or 'nothing'})"
        ),
    )


_RETURN_USAGE = (
    "usage: trac return --to <stage> --reason TEXT [--confirm] "
    "(stage: an upstream canonical stage; --confirm crosses irreversible ops)"
)


def _parse_return_args(args: list) -> tuple[dict, str | None]:
    """Parse the `trac return` grammar: --to/--reason values plus the
    optional --confirm flag. Returns (opts, None) or ({}, usage_error)."""
    opts: dict = {}
    while args:
        flag = args.pop(0)
        if flag == "--confirm":
            opts["--confirm"] = True
            continue
        if flag not in ("--to", "--reason") or not args or flag in opts:
            return {}, _RETURN_USAGE
        opts[flag] = args.pop(0)
    if "--to" not in opts or "--reason" not in opts:
        return {}, _RETURN_USAGE
    return opts, None


def cmd_return(repo: Path, *args) -> int:
    opts, usage_err = _parse_return_args(list(args))
    if usage_err:
        return _err(usage_err)
    with active_run_context(repo) as opened:
        if opened is None:
            return _err("no active run")
        home, store, run_id = opened
        state, allowed, err = _return_gate(store, run_id)
        if err:
            return _err(err)
        if opts["--to"] not in allowed:
            # SM-05.7a/C-03 + FR-0287: closed upstream set, explicitly
            # validated — no event on reject.
            return _err(
                f"invalid --to {opts['--to']}: must be one of " + "|".join(allowed)
            )
        # AC-FR0287-04: crossing already-executed irreversible operations
        # requires an explicit confirmation — no events, pointer unmoved.
        executed = _already_executed_ops(store, run_id)
        if executed and "--confirm" not in opts:
            print("already_executed operations this return would cross:")
            for op in executed:
                print(f"  - {op['operation_kind']} {op['target']} ({op['type']})")
            return _err("irreversible operations present: re-run with --confirm")
        # interfaces §1a: the human.return payload actor is pinned to the
        # literal "Human" (E-01 renders human_return=Human→<to>) — not an
        # environment-sourced operator name.
        actor = "Human"
        _establish_escape_barrier(store, run_id, state.version)
        _stale_downstream_evidence(store, run_id, state.version, opts["--to"])
        store.append(
            run_id,
            state.version,
            "human.return",
            {
                "actor": actor,
                "from": state.stage,
                "to": opts["--to"],
                "to_stage": opts["--to"],  # the kernel hard-read key
                "reason": opts["--reason"],
            },
        )
    print(f"returned to {opts['--to']}")
    return 0


_ABANDON_USAGE = "usage: trac abandon --reason TEXT"


def cmd_abandon(repo: Path, *args: str) -> int:
    """Lightweight termination exit (§2d, SM-01.21): Human-only `trac abandon
    --reason` completes the run with terminal_state=cancelled. Evidence is
    kept, issues/branches untouched — zero external side effects; a cancelled
    run then rejects `trac run` (see cmd_run)."""
    opts: dict[str, str] = {}
    args = list(args)
    while args:
        flag = args.pop(0)
        if flag not in ("--reason",) or not args or flag in opts:
            return _err(_ABANDON_USAGE)
        opts[flag] = args.pop(0)
    if set(opts) != {"--reason"}:
        return _err(_ABANDON_USAGE)
    with active_run_context(repo) as opened:
        if opened is None:
            return _err("no active run")
        home, store, run_id = opened
        state = store.state(run_id)
        store.append(
            run_id,
            state.version,
            "run.completed",
            {"terminal_state": "cancelled", "reason": opts["--reason"]},
        )
    print(f"run {run_id}: terminal=cancelled reason={opts['--reason']}")
    return 0


def _recover_gate(store: Store, run_id: str):
    # B32 (#32): accept ONLY the post-rollback quiescent state (M-DESIGN/DRAFT,
    # last rollback from M-IMPL, no new work since); fail-closed otherwise.
    state = store.state(run_id)
    if state.stage != "M-DESIGN" or state.substate != "DRAFT":
        return state, (
            f"recover requires M-DESIGN/DRAFT after an M-IMPL rollback "
            f"(stage={state.stage} substate={state.substate or 'nothing'})")
    recents = list(store.events(run_id))
    rolled_back = [e for e in recents if e.type == "stage.rolled_back"]
    if not rolled_back:
        return state, "recover requires a prior stage.rolled_back event"
    last = rolled_back[-1]
    if last.payload.get("from_stage") != "M-IMPL" or last.payload.get("to_stage") != "M-DESIGN":
        return state, (
            "recover only after a stage.rolled_back from M-IMPL to M-DESIGN "
            f"(last rollback was {last.payload.get('from_stage')} -> "
            f"{last.payload.get('to_stage')})")
    for e in recents[recents.index(last) + 1 :]:
        if e.type in ("stage.entered", "stage.exited"):
            return state, "recover rejected: another stage entered/exited after the rollback"
        cmd = e.payload.get("command", {}) if e.type == "command.issued" else {}
        if cmd.get("kind") == "dispatch_agent":
            return state, "recover rejected: new dispatch_agent work issued after the rollback"
    return state, None


def cmd_recover(repo: Path, *args) -> int:
    # B32 (#32): human.recover gate -> append or fail-closed.
    opts = {}
    args = list(args)
    usage = "usage: trac recover --reason TEXT"
    while args:
        flag = args.pop(0)
        if flag not in ("--reason",) or not args or flag in opts:
            return _err(usage)
        opts[flag] = args.pop(0)
    if set(opts) != {"--reason"}:
        return _err(usage)
    with active_run_context(repo) as opened:
        if opened is None:
            return _err("no active run")
        home, store, run_id = opened
        state, err = _recover_gate(store, run_id)
        if err:
            return _err(err)
        store.append(
            run_id,
            state.version,
            "human.recover",
            {"reason": opts["--reason"], "to_stage": "M-IMPL"},
        )
    print("recover requested: -> M-IMPL")
    return 0
