"""``trac release`` and IF-RELEASE-003 escape command face.

Extracted from :mod:`tracks.cli.main` for module-size compliance (C0302):
the Human three-way release gate (``cmd_release``), its preview/decision
rendering and fail-closed gates, the release status rendering consumed by
``trac status``, the escape barrier/evidence helpers shared with
``trac return``, and the escape status fragments. ``tracks.cli.main``
re-exports every name below.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tracks import paths
from tracks.executor.release_authorization import (
    assess_release,
    validate_authorization,
)
from tracks.store import Store

from .common import _canonical_stage_order, _err, _err2, writer_lock

# escape.stale_downstream_evidence buckets (IF-RELEASE-003 / FR-0287): only
# buckets actually present in the run's log are staled on a return.
_STALE_EVIDENCE_BUCKETS = (
    "candidate.frozen",
    "evidence.reused",
    "ci.run_observed",
    "security.assessed",
    "release.previewed",
    "release.decided",
)


def _publish_plan_index(store: Store, run_id: str) -> dict:
    """interfaces §1a#9: publish.planned carries the operation descriptor
    (operation_kind/target) keyed by idempotency_key; publish.executed
    (§1a#10) carries only idempotency_key/status/remote_check/candidate_sha
    and joins back to its plan for the operation identity."""
    plans: dict[str, dict] = {}
    for ev in store.events(run_id):
        if ev.type == "publish.planned":
            key = ev.payload.get("idempotency_key") or ev.command_id or ""
            if key:
                plans[key] = {
                    "operation_kind": ev.payload.get("operation_kind")
                    or ev.payload.get("kind"),
                    "target": ev.payload.get("target") or "",
                    "planned_seq": ev.seq,
                }
    return plans


def _already_executed_ops(store: Store, run_id: str) -> list[dict]:
    """AC-FR0287-04: irreversible operations (merge/tag/artifact/release)
    already executed in this run — crossing them requires an explicit
    --confirm. The operation identity joins publish.executed(done) back to
    its publish.planned via idempotency_key (§1a#9/#10); an executed event
    carrying the descriptor inline still reports directly. Mirrors
    executor/escape.report_irreversible_operations's store-backed rule on
    the CLI's own locked store (the escape helper resolves a process-global
    store instead of accepting one)."""
    plans = _publish_plan_index(store, run_id)
    ops: list[dict] = []
    for ev in store.events(run_id):
        if ev.type != "publish.executed" or ev.payload.get("status") != "done":
            continue
        plan = plans.get(ev.payload.get("idempotency_key") or "", {})
        ops.append(
            {
                "operation_kind": ev.payload.get("operation_kind")
                or plan.get("operation_kind")
                or ev.payload.get("kind")
                or ev.type,
                "target": ev.payload.get("target") or plan.get("target") or "",
                "type": ev.type,
            }
        )
    return ops


def _establish_escape_barrier(store: Store, run_id: str, version: str) -> int:
    """IF-RELEASE-003 (A): the Runtime escape barrier — cutover at the run's
    current max seq; late outcomes (seq <= cutover) are quarantined by the
    executor's cutover consumption. Mirrors executor/escape
    .establish_escape_barrier's store-backed semantics on the CLI's own
    locked store."""
    cutover = 0
    for ev in store.events(run_id):
        cutover = max(cutover, ev.seq)
    store.append(
        run_id,
        version,
        "escape.barrier_established",
        {"cutover_seq": cutover, "quiesced_dispatches": []},
    )
    return cutover


def _stale_downstream_evidence(
    store: Store, run_id: str, version: str, target_stage: str
) -> int:
    """IF-RELEASE-003 (A): evidence.staled(reason=human_return) for every
    post-target evidence bucket present in the log (the test freeze lifts
    only when returning to before M-TEST). b93 Q3.3: each staled payload
    carries the bucket's evidence_type and the store seq of the bucket's
    latest event (the authoritative reference back into the log)."""
    latest_seq: dict[str, int] = {}
    existing: set[str] = set()
    for ev in store.events(run_id):
        existing.add(ev.type)
        latest_seq[ev.type] = ev.seq
    count = 0
    for bucket in _STALE_EVIDENCE_BUCKETS:
        if bucket in existing:
            store.append(
                run_id,
                version,
                "evidence.staled",
                {
                    "reason": "human_return",
                    "run_id": run_id,
                    "target_stage": target_stage,
                    "type": bucket,
                    "evidence_type": bucket,
                    "bucket": bucket,
                    "source_seq": latest_seq.get(bucket),
                },
            )
            count += 1
    return count


_RELEASE_ACTIONS = ("release", "delay", "return")
_RELEASE_USAGE = (
    "usage: trac release preview"
    "|trac release --action release|trac release --action delay --reason TEXT"
    "|trac release --action return --to <stage> --reason TEXT"
)


_RELEASE_REJECTED = (
    "error: release rejected — gate failed (m-verify/prism/security) or "
    "preview stale (candidate drift); preview_digest mismatch; run the "
    "release preview subcommand and status"
)
_NO_RELEASE_RUN = "no active release run; enter M-RELEASE via `trac run` first"
_DECISION_STATUS = {"release": "approved", "delay": "delayed", "return": "returned"}


def _parse_release_args(
    args: tuple[str, ...],
) -> tuple[str, str | None, str | None] | None:
    """Parse the §2a grammar. Returns (action, reason, target) or None on a
    usage error: `release preview`, or `--action release|delay|return` with
    an optional --reason and a --to target required only for return."""
    if args == ("preview",):
        return "preview", None, None
    if len(args) < 2 or args[0] != "--action" or args[1] not in _RELEASE_ACTIONS:
        return None
    action, reason, target = args[1], None, None
    idx = 2
    while idx < len(args):
        flag = args[idx]
        if flag not in ("--reason", "--to") or idx + 1 >= len(args):
            return None
        if flag == "--reason":
            reason = args[idx + 1]
        else:
            target = args[idx + 1]
        idx += 2
    if (action == "return") != (target is not None):
        return None
    return action, reason, target


def _latest_release_preview(events: list) -> object | None:
    """The active preview is the latest release.previewed event (§1a#6)."""
    return next((e for e in reversed(events) if e.type == "release.previewed"), None)


def _release_preview_stale_reason(events: list, preview) -> str | None:
    """Closed StaleReason set (§1g): drift/stale markers AFTER the active
    preview make it stale — candidate drift, staled evidence, or a re-frozen
    different candidate. Events before the preview are its basis, not staleness."""
    for e in events:
        if e.seq <= preview.seq:
            continue
        payload = e.payload or {}
        if e.type == "candidate.stale":
            return "candidate_drift"
        if e.type == "evidence.staled":
            return "evidence_staled"
        if e.type == "candidate.frozen":
            fresh = payload.get("candidate_sha")
            if fresh and fresh != (preview.payload or {}).get("candidate_sha"):
                return "candidate_drift"
    return None


def _release_gate_blocked(events: list) -> bool:
    """Fail-closed M-VERIFY/M-SECURITY authorization check (FR-0274-2): the
    release action requires every observed gate green AND evidence that the
    gates ran at all (missing gate evidence never authorizes a release)."""
    security = [e for e in events if e.type == "security.assessed"]
    if security and security[-1].payload.get("status") != "passed":
        return True
    final = [
        e for e in events
        if e.type == "prism.verdict" and e.payload.get("scope") == "verify_final"
    ]
    if final and final[-1].payload.get("verdict") != "pass":
        return True
    gate_evidence = bool(security) or bool(final) or any(
        e.type == "local_gate.passed" for e in events
    )
    return not gate_evidence or any(e.type == "local_gate.failed" for e in events)


def _latest_ci_run(events: list) -> object | None:
    return next((e for e in reversed(events) if e.type == "ci.run_observed"), None)


def _release_block_reason(events: list) -> str:
    """Fail-closed block reason for a started-but-unverified evidence chain
    (interfaces §1d: a non-passing exit renders ``blocked: <reason>``).
    Priority: security verdict > local gate failure > contract refusal >
    parked M-VERIFY attention > missing gate evidence."""
    security = [e for e in events if e.type == "security.assessed"]
    if security and (security[-1].payload or {}).get("status") != "passed":
        return "security_" + str(
            (security[-1].payload or {}).get("status", "unknown")
        )
    if any(e.type == "local_gate.failed" for e in events):
        return "local_gate_failed"
    if any(e.type == "host_contract.invalid" for e in events):
        return "contract_invalid"
    attention = [
        (e.payload or {}).get("reason", "unknown")
        for e in events
        if e.type == "attention.required"
        and (e.payload or {}).get("stage") == "M-VERIFY"
    ]
    if attention:
        return str(attention[-1])
    return "gate_evidence_missing"


def _known_issues_fragment(events: list | None) -> str:
    """(I) known_issues preview fragment (FR-0274): every
    known_issue.registered event contributes its title to the release
    preview; known_issue.rejected (shield-rejected fakes) never surface.
    Renders empty (no output change) when nothing is registered."""
    registered = [
        (e.payload or {}).get("title", "-")
        for e in (events or [])
        if e.type == "known_issue.registered"
    ]
    if not registered:
        return ""
    return " known_issues={" + " | ".join(registered) + "}"


def _attention_fragment(events: list | None) -> str:
    """(H) needs_attention preview fragment (M-REQ-APPROVAL, IF-ISSUE-001):
    every attention.required event surfaces its area and reason so the
    operator sees what the run is waiting on (e.g. issue_creation /
    missing_token — the fail-closed no-fake-fallback path). Renders empty
    (no output change) when nothing requires attention."""
    attention = [
        (e.payload or {}).get("area", "-") + ":" + (e.payload or {}).get("reason", "-")
        for e in (events or [])
        if e.type == "attention.required"
    ]
    if not attention:
        return ""
    return " attention={" + " | ".join(attention) + "}"


def _render_preview_line(
    preview: dict, stale_reason: str | None, events: list | None = None
) -> str:
    """E-01 preview line (§2a): candidate + digest bindings + stale verdict."""
    status = "stale" if stale_reason else "awaiting_release"
    reason = stale_reason or "none"
    evidence = preview.get("evidence_digests") or {}
    if events is not None:
        ci = _latest_ci_run(events)
        if ci is not None:
            p = ci.payload or {}
            ci_frag = (
                f"ci_run={{repo={p.get('repo','-')} workflow={p.get('workflow','-')} "
                f"run_id={p.get('run_id','-')} head={p.get('head_sha','-')}}}"
            )
        else:
            ci_frag = "ci_run={}"
    else:
        ci_frag = "ci_run={}"
    return (
        f"preview: candidate={preview.get('candidate_sha', '-')} "
        f"preview_digest={preview.get('preview_digest', '-')} "
        f"artifact={preview.get('artifact_digest', '-')} {ci_frag} "
        f"evidence_digests={evidence} "
        f"operation_plan={preview.get('operation_plan_digest', '-')} "
        f"status={status} stale_reason={reason}"
        f"{_known_issues_fragment(events)}"
        f"{_attention_fragment(events)}"
    )


def _append_release_rejected(
    store, run_id: str, version: str, action: str, payload: dict, reason: str, detail: str
) -> None:
    store.append(
        run_id,
        version,
        "release.rejected",
        {
            "action": action,
            "candidate_sha": payload.get("candidate_sha"),
            "preview_digest": payload.get("preview_digest"),
            "reason": reason,
            "detail": detail,
        },
    )


def _prism_final_status(events: list) -> str | None:
    """interfaces §2b prism=pass|fail fragment for the verify_final face.

    A landed verify_final verdict projects its own verdict (pass|failed).
    Prism verdicts present WITHOUT a scoped verify_final one mean the
    same-candidate final review has not passed -- the release-chain block
    the Human/repair route keys on -- and render ``prism=failed``
    (test_verify_prism_final: the status must record the prism block).
    ``None`` means no prism verdict has landed at all."""
    verdicts = [e for e in events if e.type == "prism.verdict"]
    if not verdicts:
        return None
    final = [
        e
        for e in verdicts
        if (e.payload or {}).get("scope") == "verify_final"
    ]
    if not final:
        return "failed"
    verdict = (final[-1].payload or {}).get("verdict")
    return "pass" if verdict == "pass" else "failed"


def _full_reuse_status(events: list) -> str | None:
    """interfaces §2b full_reuse=full_f|full_rerun fragment (FR-0268): the
    FULL_F reuse judgment surfaces in status -- full_f when the reused
    evidence is the latest judgment, full_rerun (with the judge's closed
    reason) when the chain executed the battery instead. ``None`` before
    any judgment."""
    reuse = [
        e
        for e in events
        if e.type == "evidence.reused"
        and (e.payload or {}).get("kind") == "full_f"
    ]
    rerun = [e for e in events if e.type == "full.executed"]
    if not reuse and not rerun:
        return None
    latest_is_reuse = bool(reuse) and (
        not rerun or getattr(reuse[-1], "seq", 0) > getattr(rerun[-1], "seq", 0)
    )
    if latest_is_reuse:
        return "full_reuse=full_f"
    reason = str((rerun[-1].payload or {}).get("reason") or "")
    return "full_reuse=full_rerun" + (f" reason={reason}" if reason else "")


def _release_chain_fragments(events: list, lines: list[str]) -> None:
    """(F) evidence-chain fragments (FR-0270/FR-0287 wiring): CI readback
    binding and security verdict surface whenever the chain emitted them; a
    started-but-unverified chain is a non-passing exit and renders the
    fail-closed block reason (interfaces §1d) -- never without chain
    evidence, and never once a preview exists (the release gates own that
    verdict)."""
    ci = _latest_ci_run(events)
    if ci is not None:
        p = ci.payload or {}
        if p.get("api_verified") is True:
            lines.append("ci=bound")
        else:
            lines.append("ci=" + str(p.get("reason") or p.get("status") or "failed"))
    reuse = _full_reuse_status(events)
    if reuse is not None:
        lines.append(reuse)
    security = [e for e in events if e.type == "security.assessed"]
    if security:
        lines.append(
            "security=" + str((security[-1].payload or {}).get("status", "unknown"))
        )
    prism = _prism_final_status(events)
    if prism is not None:
        lines.append("prism=" + prism)
    rounds = [e for e in events if e.type == "repair.round_started"]
    if rounds:
        # §1.0.14 B: an open in-place repair renders its budget position
        # (repair=in_place round=<n>/3) on every non-passing exit.
        last_round = rounds[-1].payload or {}
        lines.append(
            f"repair=in_place round={last_round.get('round', len(rounds))}"
            f"/{last_round.get('budget', 3)}"
        )
    if any(e.type == "candidate.frozen" for e in events) and not [
        e for e in events if e.type == "release.previewed"
    ]:
        lines.append("blocked: " + _release_block_reason(events))


def _release_status_lines(events: list, primary, repo: Path | None = None) -> list[str]:
    """§1.0.1 growth 1 helpers for cmd_status (keeps CCR001 under cap)."""
    lines: list[str] = []
    review_failed = [
        e
        for e in events
        if e.type == "review.failed"
        and (e.payload or {}).get("area") == "failure_evidence"
    ]
    if review_failed:
        # AC-FR0280-03: a lost/mismatched failure evidence chain is a
        # fail-closed block — the operator sees the review outcome and the
        # reason on every subsequent status.
        outcome = (review_failed[-1].payload or {}).get("outcome", "mismatched")
        lines.append(f"blocked: evidence lost or mismatched ({outcome})")
    preview = _latest_release_preview(events)
    if preview is not None:
        payload = preview.payload or {}
        lines.append(
            f"preview_digest={payload.get('preview_digest','')} "
            f"candidate={payload.get('candidate_sha','')}"
        )
        stale = _release_status_stale(repo, primary, events, preview)
        if stale:
            lines.append(f"status=stale stale_reason={stale}")
    decided = [e for e in events if e.type == "release.decided"]
    if decided:
        last = decided[-1].payload or {}
        lines.append(
            f"decision={last.get('action','')} "
            f"preview_digest={last.get('preview_digest','')} "
            f"candidate={last.get('candidate_sha','')}"
        )
    elif _release_status_blocked(repo, primary, events, preview):
        lines.append("blocked: gate failed or preview stale")
        lines.append("rejected: gate failed or preview stale")
    _release_chain_fragments(events, lines)
    lines.extend(_release_attention_lines(events))
    lines.extend(_release_publish_lines(events, primary))
    return lines


def _release_ci_attention_lines(events: list) -> list[str]:
    ci_attention = [
        e
        for e in events
        if e.type == "attention.required"
        and (e.payload or {}).get("area") == "ci_readback"
    ]
    unresolved_attention = []
    ci_successes = [e for e in events if e.type == "ci.run_observed"]
    for attention in ci_attention:
        attention_payload = attention.payload or {}
        attention_candidate = attention_payload.get("candidate_sha")
        resolved = any(
            getattr(success, "seq", 0) > getattr(attention, "seq", 0)
            and (success.payload or {}).get("candidate_sha") == attention_candidate
            and (success.payload or {}).get("head_sha") == attention_candidate
            and (success.payload or {}).get("status") == "passed"
            and (success.payload or {}).get("api_verified") is True
            for success in ci_successes
        )
        if not resolved:
            unresolved_attention.append(attention)
    ci_attention = unresolved_attention
    if not ci_attention:
        return []
    attention = max(ci_attention, key=lambda event: getattr(event, "seq", 0))
    payload = attention.payload or {}
    return [
        (
            "needs_attention="
            + str(payload.get("reason") or "unknown")
            + " next="
            + str(payload.get("next") or "retry CI readback")
        )
    ]


_ATTENTION_RECOVERY_EVENTS = {
    "freeze": ("candidate.frozen",),
    "issue_creation": ("issue.created", "issue.mapped"),
    "issue_close": ("issue.closed",),
    "project_close": ("project.closed",),
    "milestone_seal": ("milestone.sealed",),
    "milestone_refs": ("refs.cleaned",),
}


def _attention_area_resolved(events: list, attention, area: str) -> bool:
    """True when a later recovery event proves the area's attention cleared."""
    recovery = _ATTENTION_RECOVERY_EVENTS.get(area)
    if not recovery:
        return False
    attention_seq = getattr(attention, "seq", 0)
    return any(
        event.type in recovery and getattr(event, "seq", 0) > attention_seq
        for event in events
    )


def _release_attention_lines(events: list) -> list[str]:
    """(H) needs_attention status lines (interfaces §287 / ACC-FR0283-02).

    The ci_readback fragment keeps its existing shape; every other
    attention.required area renders ``needs_attention=<area>:<reason>`` so
    the operator sees issue_creation/freeze/close attention on the status
    line too. An area whose later recovery event landed (issue.created,
    candidate.frozen, issue.closed, ...) no longer renders."""
    lines = _release_ci_attention_lines(events)
    pending: dict[str, object] = {}
    for event in events:
        if event.type != "attention.required":
            continue
        area = str((event.payload or {}).get("area") or "")
        if not area or area == "ci_readback":
            continue
        if _attention_area_resolved(events, event, area):
            continue
        pending[area] = event  # latest unresolved attention per area
    for area, event in sorted(
        pending.items(), key=lambda item: getattr(item[1], "seq", 0)
    ):
        reason = str((event.payload or {}).get("reason") or "unknown")
        lines.append(f"needs_attention={area}:{reason}")
    return lines


def _release_publish_lines(events: list, primary) -> list[str]:
    """(G) publish and terminal fragments for status (interfaces §287 /
    AC-FR0275-01): ``publish=planned|executing|done|reconciled_skip|blocked``
    from the state projection; an explicit publish.blocked event keeps its
    reason fragment."""
    pub_blocked = [e for e in events if e.type == "publish.blocked"]
    lines: list[str] = []
    if pub_blocked:
        block_reason = (pub_blocked[-1].payload or {}).get("reason", "unknown")
        lines.append(f"publish=blocked reason={block_reason}")
    else:
        publish_status = getattr(primary, "publish_status", None)
        if publish_status:
            lines.append(f"publish={publish_status}")
    if primary.status == "completed" and primary.terminal_state == "cancelled":
        lines.append("terminal=cancelled")
    return lines


def _release_status_stale(repo, primary, events, preview):
    if repo is None:
        return _release_preview_stale_reason(events, preview)
    return assess_release(
        repo,
        paths.tracks_home(repo),
        events,
        preview,
        getattr(primary, "version", ""),
    ).stale_reason


def _release_status_blocked(repo, primary, events, preview) -> bool:
    if preview is None:
        return False
    if repo is None:
        return bool(
            _release_gate_blocked(events)
            or _release_preview_stale_reason(events, preview)
        )
    authorization = assess_release(
        repo,
        paths.tracks_home(repo),
        events,
        preview,
        getattr(primary, "version", ""),
    )
    return authorization.preview_stale or authorization.gate_status.get("gate_failed", False)


def _release_report_snippet(events: list) -> str:
    """Snippet for §1.0.1 growth 1 keep-alive (CCR001 helper)."""
    try:
        preview = _latest_release_preview(events)
        if preview is not None:
            payload = preview.payload or {}
            return (
                f"release preview_digest={payload.get('preview_digest','')} "
                f"candidate={payload.get('candidate_sha','')}"
            )
    except Exception:
        return ""
    return ""


def _is_decision_closed(events: list, preview) -> bool:
    decided = [e for e in events if e.type == "release.decided"]
    return bool(decided and decided[-1].seq > preview.seq)


def _validate_return_target(target: str | None) -> int | None:
    if target is None:
        return None
    try:
        from tracks.kernel.machine import _STAGES

        if target not in _STAGES:
            return _err(
                f"invalid --to {target}: must be a canonical stage "
                "(e.g. M-DESIGN, M-TEST, M-IMPL)"
            )
    except Exception:
        pass
    return None


def _unlisted_known_issues(events: list, preview_payload: dict) -> list[str]:
    """AC-FR0286-05 informed consent: registered known issues whose issue
    number does not appear in the preview's known_issues listing. The event
    stream (known_issue.registered) is the durable authority — an unlisted
    unfixed defect blocks the release."""
    listed_numbers = {
        str(entry.get("issue", "")).rsplit("#", 1)[-1]
        for entry in (preview_payload.get("known_issues") or [])
        if isinstance(entry, dict)
    }
    unlisted: list[str] = []
    for e in events:
        if e.type != "known_issue.registered":
            continue
        number = str((e.payload or {}).get("issue_number", ""))
        if number and number not in listed_numbers:
            unlisted.append(number)
    return unlisted


def _release_known_issue_guard(
    store, run_id: str, version: str, action: str, payload: dict, events: list
) -> str | None:
    """AC-FR0286-05 informed consent: an unfixed registered known issue that
    the preview does not list blocks the release — the Human must see every
    open waiver before authorizing. Audited as release.rejected
    (reason=known_issue_not_listed); returns the rejection message or None."""
    if action != "release":
        return None
    unlisted = _unlisted_known_issues(events, payload)
    if not unlisted:
        return None
    detail = "known_issue not listed: " + ", ".join(
        f"acme/host#{n}" for n in unlisted
    )
    _append_release_rejected(
        store, run_id, version, action, payload, "known_issue_not_listed", detail
    )
    return (
        f"error: release rejected — {detail}; "
        "trac run regenerates the preview listing"
    )


def _reject_release_authorization(
    store, run_id: str, version: str, action: str, payload: dict, authorization
) -> int | None:
    allowed, validation_reason = validate_authorization(
        action, authorization, payload
    )
    if allowed:
        return None
    rejection_reason = "preview_stale" if authorization.preview_stale else "gate_failed"
    detail = validation_reason or authorization.detail
    if authorization.preview_stale and action != "release":
        detail = f"preview stale: {authorization.stale_reason}; run trac release preview"
    _append_release_rejected(
        store, run_id, version, action, payload, rejection_reason, detail
    )
    if action == "release":
        return _err(_RELEASE_REJECTED)
    return _err(f"error: {detail}")


@dataclass(frozen=True)
class _ReleaseRequest:
    action: str
    reason: str | None
    target: str | None


@dataclass(frozen=True)
class _ReleaseGate:
    events: list
    preview: object
    version: str
    authorization: object


def _release_request(args: tuple[str, ...]) -> _ReleaseRequest | int:
    """Parse the release invocation; an int result is an already-printed error."""
    parsed = _parse_release_args(args)
    if parsed is None:
        return _err(_RELEASE_USAGE)
    action, reason, target = parsed
    if action == "return":
        err = _validate_return_target(target)
        if err is not None:
            return err
    return _ReleaseRequest(action, reason, target)


def _release_gate(
    repo: Path, home: Path, store: Store, run_id: str
) -> _ReleaseGate | None:
    """Load the active preview and bind the fail-closed authorization to it."""
    events = list(store.events(run_id))
    preview = _latest_release_preview(events)
    if preview is None:
        return None
    version = store.state(run_id).version
    authorization = assess_release(repo, home, events, preview, version)
    return _ReleaseGate(events, preview, version, authorization)


def _decide_release(
    store: Store,
    run_id: str,
    gate: _ReleaseGate,
    request: _ReleaseRequest,
    payload: dict,
) -> int | None:
    """Run the pre-decision rejection gates; None when the decision may land."""
    rejection = _reject_release_authorization(
        store, run_id, gate.version, request.action, payload, gate.authorization
    )
    if rejection is not None:
        return rejection
    rejection = _release_known_issue_guard(
        store, run_id, gate.version, request.action, payload, gate.events
    )
    if rejection is not None:
        return _err(rejection)
    store.append(
        run_id,
        gate.version,
        "release.decided",
        {
            "action": request.action,
            "candidate_sha": payload.get("candidate_sha"),
            "preview_digest": payload.get("preview_digest"),
            "reason": request.reason,
            "target": request.target,
            "actor": "human",
        },
    )
    return None


def cmd_release(repo: Path, *args: str) -> int:
    """Human three-way release gate (§2a): `release preview` renders the E-01
    line; `--action release|delay|return` appends exactly one append-only
    release.decided (writer lock) bound to the active preview. Stale preview
    rejects all three actions; a failed/stale gate additionally rejects only
    `release`. A rejection is audited as release.rejected and produces no
    decision and no stage transfer. The gate is closed after a decision until
    a newer preview is generated (SM-01.11)."""
    request = _release_request(args)
    if isinstance(request, int):
        return request
    home = paths.tracks_home(repo)
    store = Store(home)
    run_id = store.active_run()
    if run_id is None:
        return _err2(_NO_RELEASE_RUN)
    with writer_lock(home):
        gate = _release_gate(repo, home, store, run_id)
        if gate is None:
            return _err2(_NO_RELEASE_RUN)
        if request.action == "preview":
            print(
                _render_preview_line(
                    gate.preview.payload or {}, gate.authorization.stale_reason, gate.events
                )
            )
            return 0
        payload = gate.preview.payload or {}
        if _is_decision_closed(gate.events, gate.preview):
            return _err(
                "error: decision already recorded for this preview; "
                "regenerate the preview via trac run before deciding again"
            )
        rc = _decide_release(store, run_id, gate, request, payload)
        if rc is not None:
            return rc
    print(
        f"decision: {request.action} (candidate={payload.get('candidate_sha')} "
        f"preview_digest={payload.get('preview_digest')}) "
        f"status={_DECISION_STATUS[request.action]}"
    )
    return 0


def _escape_status_scan(events) -> tuple:
    """Scan the event stream for the escape status inputs: (latest
    human.return payload, evidence.staled count, barrier established).
    Quarantine is not event-derived: it is the barrier policy state and
    renders with the barrier itself (b93 Q3.5)."""
    latest_return = None
    staled = 0
    barrier = False
    for ev in events:
        etype = getattr(ev, "type", None)
        if etype == "human.return":
            latest_return = getattr(ev, "payload", None) or {}
        elif etype == "evidence.staled":
            staled += 1
        elif etype == "escape.barrier_established":
            barrier = True
    return latest_return, staled, barrier


def _escape_status_fragments(events) -> list[str]:
    """IF-RELEASE-003 (A) / AC-FR0274+FR-0287: the five escape status
    fragments rendered by `trac status`:

    - human_return=<actor>→<to> (latest human.return)
    - evidence.staled=<n> (staled evidence count, always rendered)
    - barrier=established (escape.barrier_established seen)
    - late_outcome=quarantined (barrier policy state — renders with the
      barrier, even before any late outcome event arrives; b93 Q3.5)
    - frozen_tests=unfrozen (return target ordinal <= M-TEST)
    """
    latest_return, staled, barrier = _escape_status_scan(events)
    fragments: list[str] = []
    if latest_return is not None:
        to = latest_return.get("to") or latest_return.get("to_stage") or "?"
        fragments.append(f"human_return={latest_return.get('actor', '?')}→{to}")
        order = _canonical_stage_order()
        target = latest_return.get("to_stage") or to
        if target in order and order.index(target) <= order.index("M-TEST"):
            fragments.append("frozen_tests=unfrozen")
    fragments.append(f"evidence.staled={staled}")
    if barrier:
        fragments.append("barrier=established")
        fragments.append("late_outcome=quarantined")
    return fragments
