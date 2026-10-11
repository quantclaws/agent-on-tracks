"""Human gate command worker execution (interfaces §1j.3, IF-GATEUI-001).

The supervisor claims an accepted human gate command (``record_triage`` /
``record_review`` / ``record_stage_approval`` / ``record_release_decision``)
and this module executes it through the SAME human-gate pipeline the CLI
faces use (``trac triage`` / ``trac review`` / ``trac approve`` /
``trac release``), under the run-plane writer lock: the domain event
(``human.triage`` / ``human.review`` / ``human.approval`` /
``release.decided``) lands through the shared ResultCheckpoint pipeline /
shared approval and release cores, and the outcome returns to
``worker_main._execute`` as ``(result, failure)`` — the command completes
on success and fails closed with a classified failure otherwise (§1j.4:
execution success marks the command completed; whether the run crosses the
gate immediately is the kernel's reduction, never the worker's guess).

The actor recorded on every domain event is the command row's actor (the
authenticated submitter of the HTTP face). ``worker_main`` owns the
row-to-repo resolution and params plumbing; this module owns gate
semantics only.

Contract tokens: IF-GATEUI-001 (worker execution face) riding the
IF-DRIVE-001 worker claim/CAS machinery.
"""

from __future__ import annotations

from pathlib import Path

from tracks import paths
from tracks.cli.common import writer_lock
from tracks.cli.gate_cmd import _approve_reviewed_baseline
from tracks.cli.release_cmd import (
    _decide_release,
    _is_decision_closed,
    _release_gate,
    _ReleaseRequest,
)
from tracks.cli.run_cmd import _review_checkpoint, _triage_checkpoint
from tracks.executor.result_checkpoint import run_human_gate_pipeline
from tracks.kernel.machine_verdicts import _REVIEW_PENDING_THREADS
from tracks.store import Store

GATE_KINDS = frozenset(
    {
        "record_triage",
        "record_review",
        "record_stage_approval",
        "record_release_decision",
    }
)

# The service face's review actions (§1b #18) mapped onto the CLI review
# actions `trac review` accepts (§1j.3 与 CLI 等价): `comment` carries the
# reviewer's revise round, `no_comment` closes the review.
_REVIEW_ACTIONS = {"comment": "revise", "no_comment": "no_comment"}


def execute_gate_command(
    repo: Path, run_id: str, kind: str, params: dict, actor: str
) -> tuple[dict | None, dict | None]:
    """Execute one claimed human gate command; returns ``(result, failure)``
    with exactly one non-None (the ``worker_main._execute`` contract)."""
    handler = _HANDLERS.get(kind)
    if handler is None:
        return None, _rejected(f"not a human gate command kind: {kind!r}")
    return handler(repo, run_id, params, actor)


def _rejected(reason: str) -> dict:
    """A permanent gate rejection (§1j.4): the command fails closed."""
    return {"failure_class": "unrecoverable", "reason": reason}


def _known_state(store: Store, run_id: str):
    """The run's state, or the unknown-run failure the worker reports."""
    state = store.state(run_id)
    if state.run_id is None:
        return None, _rejected(f"gate command references unknown run {run_id!r}")
    return state, None


def _run_pipeline(repo, store, run_id, state, checkpoint, label: str):
    """Run the shared §1j.3 pipeline and read it exactly like the CLI tail
    (``run_cmd._pipeline_outcome``): the gate must move, else the pipeline
    failed and its reason is the command failure."""
    rc, result = run_human_gate_pipeline(repo, state, store, run_id, checkpoint)
    if rc or result is None:
        return None, _rejected(f"{label} pipeline rejected (rc={rc})")
    final_state, awaiting_before = result
    if final_state.awaiting == awaiting_before:
        reason = (final_state.last_failure or {}).get("reason", "unknown")
        return None, _rejected(f"{label} pipeline failed: {reason}")
    return {"kind": "gate_recorded", "gate": label}, None


def _execute_triage(repo: Path, run_id: str, params: dict, actor: str):
    decision = str(params.get("decision") or "")
    home = paths.tracks_home(repo)
    store = Store(home)
    try:
        with writer_lock(home):
            state, failure = _known_state(store, run_id)
            if failure is not None:
                return None, failure
            if state.awaiting != "triage":
                return None, _rejected(
                    "run not awaiting triage"
                    f" (awaiting={state.awaiting or 'nothing'})"
                )
            checkpoint = _triage_checkpoint(repo, state, actor, decision)
            return _run_pipeline(repo, store, run_id, state, checkpoint, "triage")
    finally:
        store.close()


def _execute_review(repo: Path, run_id: str, params: dict, actor: str):
    cli_action = _REVIEW_ACTIONS.get(str(params.get("action") or ""))
    if cli_action is None:
        return None, _rejected(
            f"unknown review action {params.get('action')!r};"
            " closed set is comment|no_comment"
        )
    home = paths.tracks_home(repo)
    store = Store(home)
    try:
        with writer_lock(home):
            state, failure = _known_state(store, run_id)
            if failure is not None:
                return None, failure
            if state.awaiting not in ("review", _REVIEW_PENDING_THREADS):
                return None, _rejected(
                    "run not awaiting review"
                    f" (awaiting={state.awaiting or 'nothing'})"
                )
            checkpoint = _review_checkpoint(repo, state, actor, cli_action)
            return _run_pipeline(repo, store, run_id, state, checkpoint, "review")
    finally:
        store.close()


def _execute_approval(repo: Path, run_id: str, params: dict, actor: str):
    decision = str(params.get("decision") or "approve")
    if decision != "approve":
        return None, _rejected(
            f"unsupported approval decision {decision!r} on the worker face"
        )
    home = paths.tracks_home(repo)
    store = Store(home)
    try:
        with writer_lock(home):
            state, failure = _known_state(store, run_id)
            if failure is not None:
                return None, failure
            if state.awaiting == "rollback":
                # SM-01.13 rollback approvals carry a --to target choice the
                # service params cannot express; they stay on the CLI face.
                return None, _rejected(
                    "rollback approvals need the CLI --to face; the worker"
                    " executes M-REQ-APPROVAL approvals only"
                )
            if state.stage != "M-REQ-APPROVAL" or state.awaiting != "approval":
                return None, _rejected(
                    f"run not awaiting approval (stage={state.stage}"
                    f" awaiting={state.awaiting or 'nothing'})"
                )
            approved, detail = _approve_reviewed_baseline(
                repo, home, store, run_id, state, actor
            )
            if not approved:
                return None, _rejected(detail)
            return {"kind": "gate_recorded", "gate": "approval", "digest": detail}, None
    finally:
        store.close()


def _execute_release(repo: Path, run_id: str, params: dict, actor: str):
    home = paths.tracks_home(repo)
    store = Store(home)
    try:
        with writer_lock(home):
            state, failure = _known_state(store, run_id)
            if failure is not None:
                return None, failure
            gate = _release_gate(repo, home, store, run_id)
            if gate is None:
                return None, _rejected(
                    "no release preview is bound to this run"
                )
            if _is_decision_closed(gate.events, gate.preview):
                return None, _rejected(
                    "decision already recorded for this preview; regenerate"
                    " the preview before deciding again"
                )
            request = _ReleaseRequest(
                action=str(params.get("action") or ""),
                reason=params.get("reason"),
                target=params.get("target"),
            )
            payload = gate.preview.payload or {}
            rc = _decide_release(store, run_id, gate, request, payload, actor=actor)
            if rc is not None:
                return None, _rejected(f"release decision rejected (rc={rc})")
            return {
                "kind": "gate_recorded",
                "gate": "release",
                "action": request.action,
            }, None
    finally:
        store.close()


_HANDLERS = {
    "record_triage": _execute_triage,
    "record_review": _execute_review,
    "record_stage_approval": _execute_approval,
    "record_release_decision": _execute_release,
}
