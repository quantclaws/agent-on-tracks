"""``attention.required`` lifecycle shared by the run plane and the CLI.

An attention is a durable operator-facing block, never a hidden wait. The
single resolution rule lives here so the executor's re-drive gate, the
server projections and ``trac status`` cannot disagree about whether an
attention is still current:

* a later candidate freeze supersedes attentions tied to the old candidate
  (a repair commit is a new candidate);
* an area-specific recovery event (same candidate where the attention is
  candidate-bound) resolves it;
* an operator reset (``human.retry`` / ``human.recover``) resolves every
  older attention explicitly;
* a successful same-command outcome or a later same-stage exit resolves it;
* a successful same-candidate CI readback resolves ``ci_readback``.

A failing gate/CI event never resolves anything.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# Attention areas whose condition is bound to one frozen candidate (or to
# the verify chain that produced it). A newer candidate freeze makes every
# older one stale -- the repair commit's re-walk owns the fresh evidence.
CANDIDATE_SCOPED_AREAS = frozenset(
    {
        "freeze",
        "host_contract",
        "local_gate",
        "gate_worktree",
        "version_gate",
        "ci_readback",
        "pipeline",
        "artifact",
        "release_evidence",
        "release_facts",
        "security",
    }
)

# attention.required area -> events that prove the blocked condition cleared.
# A later same-stage exit, a successful same-command outcome, or an operator
# reset also resolves generically (see attention_resolved).
RECOVERY_EVENTS = {
    "freeze": ("candidate.frozen",),
    "issue_creation": ("issue.created", "issues.created", "issue.mapped"),
    "issue_close": ("issue.closed",),
    "project_close": ("project.closed",),
    "milestone_seal": ("milestone.sealed",),
    "milestone_refs": ("refs.cleaned",),
    "publish": ("milestone.trace_closed", "publish.executed"),
    "sync_product": ("sync_product.verified",),
    "artifact": ("release.previewed",),
    "release_evidence": ("release.previewed",),
    "release_facts": ("release.previewed",),
    "version_gate": ("local_gate.passed",),
    "host_contract": ("local_gate.passed", "host_contract.materialized"),
    "local_gate": ("local_gate.passed",),
    "gate_worktree": ("local_gate.passed",),
    "pipeline": ("red.validated", "prism.verdict"),
    "run_breaker": ("human.retry", "human.recover"),
}

# Reasons whose next move belongs to the operator (configure a token, clean
# the tree, create the milestone, ...). Retry stays available afterwards;
# these only stop the UI presenting retry as the immediate primary action.
NON_RETRYABLE_ATTENTION_REASONS = frozenset(
    {
        "missing_token",
        "missing_credentials",
        "dirty_tree",
        "milestone_not_found",
        "remote_unavailable",
        "tag_already_exists",
        "version_mismatch",
        "evidence_incomplete_for_candidate",
        "revise_without_findings",
        "refs_remaining",
        "candidate_missing",
        "gate_failed",
        "malformed_result",
    }
)

# Attentions a plain re-drive may genuinely clear because the external fact
# may have changed without a commit (tree cleaned, CI registered, remote
# configured). Everything else needs a repair commit or an operator reset.
AUTO_RETRY_ATTENTION_AREAS = frozenset(
    {"freeze", "ci_readback", "version_gate"}
)

# Attention areas whose documented recovery verb is ``trac retry`` even when
# the run never became a formal escalation (M-VERIFY freeze/gate/CI blocks).
RETRYABLE_ATTENTION_AREAS = frozenset(
    {
        "freeze",
        "host_contract",
        "local_gate",
        "gate_worktree",
        "version_gate",
        "ci_readback",
        "run_breaker",
    }
)

_RESET_EVENTS = frozenset({"human.retry", "human.recover"})


def _seq(event: Any) -> int:
    return int(getattr(event, "seq", 0) or 0)


def _payload(event: Any) -> dict:
    payload = getattr(event, "payload", {}) or {}
    return payload if isinstance(payload, dict) else {}


def _is_success_outcome(payload: dict) -> bool:
    return str(payload.get("status") or "") in (
        "done",
        "completed",
        "passed",
        "success",
    )


def _ci_success_bound(payload: dict, candidate: object) -> bool:
    if payload.get("status") != "passed" or payload.get("api_verified") is not True:
        return False
    if not candidate:
        return True
    return (
        payload.get("candidate_sha") == candidate
        and payload.get("head_sha") == candidate
    )


def _ci_attention_resolved(
    events: list, attention_seq: int, attention_payload: dict
) -> bool:
    candidate = attention_payload.get("candidate_sha")
    return any(
        event.type == "ci.run_observed"
        and _seq(event) > attention_seq
        and _ci_success_bound(_payload(event), candidate)
        for event in events
    )


def _candidate_superseded(events: list, attention: Any) -> bool:
    """True when a newer candidate freeze makes this attention stale.

    Candidate-bound attentions compare the frozen sha. An attention without
    a candidate (e.g. ``host_contract`` gate failure) is stale once ANY new
    candidate freezes after it: the failure belonged to the previous verify
    window, and the new candidate's re-walk owns the fresh verdict.
    """
    payload = _payload(attention)
    area = str(payload.get("area") or "")
    if area not in CANDIDATE_SCOPED_AREAS:
        return False
    candidate = payload.get("candidate_sha")
    attention_seq = _seq(attention)
    for event in events:
        if _seq(event) <= attention_seq or event.type != "candidate.frozen":
            continue
        fresh = _payload(event).get("candidate_sha")
        if not fresh:
            continue
        if not candidate or fresh != candidate:
            return True
    return False


def _same_stage_exit(event: Any, stage: str) -> bool:
    if event.type != "stage.exited":
        return False
    return not stage or str(_payload(event).get("stage") or "") == stage


def _same_command_success(event: Any, attention: Any) -> bool:
    if not getattr(event, "command_id", None) or getattr(
        event, "command_id", None
    ) != getattr(attention, "command_id", None):
        return False
    payload = _payload(event)
    if event.type == "outcome.received":
        return _is_success_outcome(payload)
    if event.type == "command.completed":
        return str(payload.get("status") or "completed") in (
            "completed",
            "done",
            "succeeded",
        )
    return False


def _recovery_matches(
    event: Any, recovery: tuple, attention_payload: dict
) -> bool:
    if event.type not in recovery:
        return False
    payload = _payload(event)
    if (
        str(attention_payload.get("area") or "") == "version_gate"
        and str(payload.get("kind") or "") != "version"
    ):
        return False
    attention_candidate = attention_payload.get("candidate_sha")
    event_candidate = payload.get("candidate_sha")
    return not (
        attention_candidate
        and event_candidate
        and attention_candidate != event_candidate
    )


def _generic_resolution(
    event: Any, attention: Any, stage: str, recovery: tuple, payload: dict
) -> bool:
    return (
        event.type in _RESET_EVENTS
        or _same_stage_exit(event, stage)
        or _same_command_success(event, attention)
        or _recovery_matches(event, recovery, payload)
    )


def attention_resolved(events: list, attention: Any) -> bool:
    """True when the attention no longer blocks the current run.

    Resolution sources are candidate supersession, an operator reset, an
    area-specific recovery event, a successful same-command outcome, a
    successful same-candidate CI readback, or any later exit of the
    attention's stage. A failing gate/CI event never resolves anything.
    """
    if _candidate_superseded(events, attention):
        return True
    payload = _payload(attention)
    attention_seq = _seq(attention)
    stage = str(payload.get("stage") or "")
    area = str(payload.get("area") or "")
    if area == "ci_readback" and _ci_attention_resolved(
        events, attention_seq, payload
    ):
        return True
    recovery = RECOVERY_EVENTS.get(area, ())
    return any(
        _seq(event) > attention_seq
        and _generic_resolution(event, attention, stage, recovery, payload)
        for event in events
    )


def unresolved_attentions(events: Iterable) -> list:
    """Latest unresolved attention per area, ordered by sequence."""
    events = list(events)
    pending: dict[str, Any] = {}
    for event in events:
        if event.type != "attention.required":
            continue
        if attention_resolved(events, event):
            continue
        area = str(_payload(event).get("area") or "run")
        pending[area] = event
    return sorted(pending.values(), key=_seq)


def latest_unresolved_attention(events: Iterable) -> Any | None:
    pending = unresolved_attentions(events)
    return pending[-1] if pending else None
