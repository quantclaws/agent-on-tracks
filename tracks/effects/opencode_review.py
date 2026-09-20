"""Structured review/DIAGNOSE payload mixin for OpencodeBackend (D-35).

Extracted from ``opencode.py`` for module-size compliance (C0302).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tracks.discuss.gate import check_ready
from tracks.effects.devon_evidence import _result_file_payload
from tracks.kernel.envelope import (
    REVIEW_FINDING_FIELDS,
    REVIEW_SUMMARY_MAX,
    EnvelopeFormatError,
    parse_agent_output,
    validate_review_finding,
    validate_review_payload,
)
from tracks.kernel.m_impl import DIAGNOSE_CLASSIFICATIONS

from .opencode_audit import _discussion_snapshot, _docset_text


class OpencodeReviewMixin:
    """Review/DIAGNOSE payload extraction, validation and threading."""

    # #89 单一真相源在 kernel（assignment 注入 + 校验共用）；类属性保留
    # 为既有引用点的稳定别名。
    _DIAGNOSE_CLASSIFICATIONS = DIAGNOSE_CLASSIFICATIONS

    # D-35 (SC-D35 §2.2) structured review payload contract. Single-source
    # delegation: the field sets and the 140-char caps are owned by
    # kernel.envelope (the schema injected into dispatch assignments), so
    # the mechanical checks and the declared schema cannot drift apart.
    _REVIEW_FINDING_FIELDS = REVIEW_FINDING_FIELDS
    _REVIEW_SUMMARY_MAX = REVIEW_SUMMARY_MAX
    _REVIEW_SUBSTATES = (
        "PRISM_REVIEW",
        "PRISM_PLAN",
        "PRISM_RED",
        "PRISM_FINAL",
        "VERIFY_FINAL",
    )
    _REVIEW_KEYS = (
        "review_summary",
        "findings",
        "review_body",
        "defect_classification",
        "discussion_refs",
        "review_ref",
        "candidate_sha",
    )

    @classmethod
    def _review_dispatch(cls, role: str, substate: str, assignment: dict | None) -> bool:
        """A Prism review dispatch whose stage is known (D-35: the executor
        threads ``stage`` into the assignment context)."""
        return (
            role == "prism"
            and substate in cls._REVIEW_SUBSTATES
            and (assignment or {}).get("stage") is not None
        )

    @classmethod
    def _structured_channel(cls, role: str, substate: str, assignment: dict | None) -> bool:
        """M-TEST/M-IMPL review: the structured payload is REQUIRED on revise.
        M-DESIGN keeps the doc-anchored channel (AC-FR0240-04)."""
        return (
            cls._review_dispatch(role, substate, assignment)
            and (assignment or {}).get("stage") in ("M-TEST", "M-IMPL", "M-VERIFY")
        )

    def _review_channel_failure(
        self,
        role: str,
        substate: str,
        assignment: dict | None,
        proc: subprocess.CompletedProcess,
        prompt: str,
        console_input: str | None,
    ) -> dict | None:
        """Early D-35 gate: a malformed structured payload fails the dispatch
        before the audits run (strict channel only; M-DESIGN never rejects)."""
        if not self._structured_channel(role, substate, assignment):
            return None
        payload, error = self._prism_review_payload_from(proc)
        if substate == "VERIFY_FINAL" and payload is None and error is None:
            error = "VERIFY_FINAL requires a valid pass/revise review payload"
        if error is None:
            return None
        return self._review_payload_malformed_result(error, proc, prompt, console_input)

    def _has_structured_findings(
        self,
        role: str,
        substate: str,
        assignment: dict | None,
        proc: subprocess.CompletedProcess,
    ) -> bool:
        """Whether the structured payload carries validated findings — the
        discussion gate exempts such a revise from the doc-thread rule."""
        if not self._structured_channel(role, substate, assignment):
            return False
        payload, error = self._prism_review_payload_from(proc)
        return error is None and bool(payload and payload.get("findings"))

    def _merge_review_payload(
        self,
        result: dict,
        role: str,
        substate: str,
        assignment: dict | None,
        proc: subprocess.CompletedProcess,
        prompt: str,
        console_input: str | None,
    ) -> dict:
        """Merge the structured review payload into a done result.

        Strict channel (M-TEST/M-IMPL): the payload's verdict is Prism's own
        judgment and overrides the discussion-derived one; a revise with no
        payload is a contract violation (AC-FR0240-01). M-DESIGN: payload
        fields are threaded through when present but never required and never
        rejected (AC-FR0240-04)."""
        if not self._review_dispatch(role, substate, assignment):
            return result
        if result.get("status") != "done":
            return result
        payload, _ = self._prism_review_payload_from(proc)
        if payload is None:
            if (
                self._structured_channel(role, substate, assignment)
                and result.get("verdict") == "revise"
            ):
                return self._review_payload_malformed_result(
                    "revise verdict on the structured channel must end with the "
                    "review JSON payload (verdict/review_summary/findings/"
                    "review_body; SC-D35 §2.2)",
                    proc,
                    prompt,
                    console_input,
                )
            return result
        for key in self._REVIEW_KEYS:
            if key in payload:
                result[key] = payload[key]
        if not self._structured_channel(role, substate, assignment):
            return result
        # PRISM-D35-R1-ADV4 (R2-04 wording): a JSON "pass" must not override
        # a derived revise while the docset discussion is not ready —
        # check_ready covers ALL open threads on the docset (any initiator),
        # so a leftover thread holds the revise until someone closes it
        # (bounded by the attempt budget; re-dispatch targets the author,
        # not the reviewer, so no self-deadlock).
        if payload["verdict"] == "pass" and result.get("verdict") == "revise":
            return result
        result["verdict"] = payload["verdict"]
        return result

    @classmethod
    def _validate_review_finding(cls, finding: object, idx: int) -> str | None:
        # Canonical semantics live in kernel.envelope (the injected schema).
        return validate_review_finding(finding, idx)

    @classmethod
    def _validate_review_payload(cls, payload: dict) -> str | None:
        # Canonical semantics live in kernel.envelope (the injected schema).
        return validate_review_payload(payload)

    @classmethod
    def _envelope_reply_payload(
        cls, text: object
    ) -> tuple[dict | None, str | None, bool]:
        """IF-ENVELOPE-001: when the final reply carries a tracks-envelope
        block, the payload IS the structured reply (single deterministic
        path, payload schema validated against the kernel registry). Returns
        ``(payload, error, found)``; ``found=False`` keeps the legacy
        bare-JSON channels — a missing block is a compliance miss, not
        ambiguity, and the Runtime's declared-dispatch gate owns the
        hard-fail policy for structural breakage."""
        if not isinstance(text, str) or "tracks-envelope" not in text:
            return None, None, False
        try:
            parsed = parse_agent_output(text)
        except EnvelopeFormatError as exc:
            if exc.kind == "no_envelope_block":
                return None, None, False
            return None, f"envelope format error ({exc.kind}): {exc.detail}", True
        payload = parsed.get("payload")
        return (payload if isinstance(payload, dict) else None), None, True

    @classmethod
    def _review_payload_from_reply(cls, text: object) -> tuple[dict | None, str | None]:
        """Structured reply payload selection (single deterministic path).

        Envelope block present: the block's payload IS the reply, validated
        against the kernel registry. No block: legacy shape-aware bare-JSON
        scan (declared-dispatch hard enforcement stays with the Runtime
        gate). Returns ``(payload, None)`` when found, ``(None, error)`` on
        a contract violation, ``(None, None)`` when no payload was attempted
        (legal absence — callers decide whether that is allowed)."""
        payload, env_error, env_found = cls._envelope_reply_payload(text)
        if env_found:
            if env_error is not None:
                return None, env_error
            if not isinstance(payload, dict) or "verdict" not in payload:
                return None, (
                    "envelope payload for a review kind must carry "
                    "verdict/review_summary/findings/review_body (SC-D35 §2.2)"
                )
            return payload, None
        if not isinstance(text, str):
            return None, None
        # Shape-aware first (the review JSON carries "verdict"); the blind
        # last-wins pick stays as fallback so the existing legal-absence
        # semantics ("verdict" not in payload) is preserved.
        payload = cls._first_json_object(text.strip(), ("verdict",)) or (
            cls._first_json_object(text.strip())
        )
        if not isinstance(payload, dict) or "verdict" not in payload:
            return None, None
        return payload, None

    @classmethod
    def _prism_review_payload_from(
        cls,
        proc: subprocess.CompletedProcess,
    ) -> tuple[dict | None, str | None]:
        """Extract + validate the structured review JSON from the reviewer's
        final reply (SC-D35 §2.2, same final-text bare-JSON convention as
        DIAGNOSE). Returns ``(payload, None)`` when valid, ``(None, error)``
        on a contract violation, and ``(None, None)`` when no payload was
        attempted (legal absence — callers decide whether that is allowed)."""
        event = cls._final_text_event(proc)
        part = event.get("part") if isinstance(event, dict) else None
        text = part.get("text") if isinstance(part, dict) else None
        payload, error = cls._review_payload_from_reply(text)
        if error is not None or payload is None:
            return None, error
        if payload.get("verdict") not in ("pass", "revise"):
            return None, (
                f"review payload verdict must be 'pass'|'revise', "
                f"got {payload.get('verdict')!r}"
            )
        error = cls._validate_review_payload(payload)
        if error is not None:
            return None, error
        return payload, None

    def _review_payload_malformed_result(
        self,
        error: str,
        proc: subprocess.CompletedProcess,
        prompt: str,
        console_input: str | None,
    ) -> dict:
        return {
            "status": "failed",
            "artifact_ref": None,
            "self_report": f"review payload malformed: {error}",
            "audit_evidence": f"review_payload_malformed: {error}",
            "failure_class": "manifest_malformed",
            "agent_io": self._capture_io(proc, prompt, console_input),
        }

    def _diagnose_payload_from_reply(self, text: object) -> dict | None:
        """DIAGNOSE payload selection — envelope block first (single
        deterministic path), legacy shape-aware bare-JSON scan otherwise."""
        payload, env_error, env_found = self._envelope_reply_payload(text)
        if env_found:
            if env_error is None and isinstance(payload, dict):
                return payload
            return None
        if not isinstance(text, str):
            return None
        # Shape-aware first (the diagnostic carries "classification"); blind
        # last-wins stays as fallback for the no-payload path.
        payload = self._first_json_object(text.strip(), ("classification",)) or (
            self._first_json_object(text.strip())
        )
        return payload if isinstance(payload, dict) else None

    def _diagnose_classification_from(self, proc, result_path: str | None = None) -> dict | None:
        """Extract the skill-contract DIAGNOSE JSON ({"classification",
        "reason", "evidence"}) — result FILE first, reply text fallback. The
        full payload flows onward so the fixer dispatch receives the
        diagnostic's actual analysis, not just the classification label.

        #174 consumer wiring (2026-09-20, run 01M2QTJB T-003): with the
        file channel the final reply is a one-line pointer; the text-only
        extraction then saw "no {classification,reason,evidence} JSON" and
        burned the attempt as a contract violation while the delivered file
        held a valid, substantive prism:diagnose verdict (same gap class
        the Devon evidence face had). File acceptance mirrors the
        collection face (validate_envelope, kind-agnostic)."""
        payload = _result_file_payload(result_path)
        if not isinstance(payload, dict):
            event = self._final_text_event(proc)
            part = event.get("part") if isinstance(event, dict) else None
            text = part.get("text") if isinstance(part, dict) else None
            payload = self._diagnose_payload_from_reply(text)
        if not isinstance(payload, dict):
            return None
        if payload.get("classification") not in self._DIAGNOSE_CLASSIFICATIONS:
            return None
        return payload

    @staticmethod
    def _enrich_discussion(
        result: dict,
        doc_paths: list[Path],
        substate: str,
        reviewer_assignment: bool,
    ) -> dict:
        if reviewer_assignment or substate == "TRIAGE":
            text = _docset_text(doc_paths)
            result["discussion_evidence"] = _discussion_snapshot(text)
            if reviewer_assignment:
                ready, _ = check_ready(text)
                result["verdict"] = "pass" if ready else "revise"
        return result

    @staticmethod
    def _enrich_failure_discussion(
        result: dict,
        doc_paths: list[Path],
        reviewer_assignment: bool,
    ) -> dict:
        if reviewer_assignment and doc_paths:
            text = _docset_text(doc_paths)
            if text:
                result["discussion_evidence"] = _discussion_snapshot(text)
        return result
