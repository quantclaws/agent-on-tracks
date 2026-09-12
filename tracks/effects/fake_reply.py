"""FakeBackend declared-envelope reply encoding (IF-ENVELOPE-002).

Extracted from ``fake.py`` for module-size compliance (C0302). Encodes the
fake's actual simulated result as the reply the declared assignment demanded;
never invents fields.
"""

from __future__ import annotations

from tracks.effects.envelope_reply import encode_envelope_reply, is_declared_assignment
from tracks.kernel.envelope import (
    DIAGNOSE_KINDS,
    REVIEW_KINDS,
    envelope_kind,
)

# IF-ENVELOPE-002 prerequisite: the reply kinds this fake can encode honestly.
# Writer/authority kinds stay out — their payload schemas are pending (T-024)
# and the fake has no actual writer reply contract to encode; an assignment
# declaring such a kind fails visibly at the Runtime (missing raw_output
# classifies as format_error), never through an invented reply.
_FAKE_SPEAKABLE_KINDS = (*REVIEW_KINDS, *DIAGNOSE_KINDS)

# IF-ENVELOPE-002: provenance a fake declared dispatch attaches to its
# result about the artifact faces. The fake consumes NO materialized
# artifacts (no .opencode/ copies exist for it to read) — the artifact faces
# are genuinely absent for fake dispatches and are recorded as such, never
# invented, and the static four-face parity gate (assignment, backend_fake,
# backend_real, validator) is the enforcement boundary for fake mode.
_FAKE_PARITY_PROVENANCE = {
    "artifact_faces": [],
    "treatment": "absent_not_invented",
    "note": (
        "FakeBackend materializes/consumes no opencode artifacts; the "
        "artifact faces are genuinely absent for fake dispatches (no file "
        "or version evidence invented here). The static four-face parity "
        "gate (assignment, backend_fake, backend_real, validator) is the "
        "enforcement boundary for fake dispatches."
    ),
}



class FakeReplyMixin:
    """raw_output envelope encoding + schema-completing synthesize helpers."""

    def _attach_declared_raw_output(
        self, result: dict, role: str, substate: str, assignment: dict | None
    ) -> dict:
        """Encode the fake's actual simulated result as the reply the declared
        assignment demanded (raw_output), so the Runtime's collection face
        classifies the exact reply text.

        Only explicitly simulated fields are encoded, under the kind the
        assignment actually declares. A simulated outcome the payload schema
        cannot represent (a revise without simulated findings, a diagnosis
        label without a simulated reason/evidence) stays incomplete ON
        PURPOSE: the Runtime's kernel validator classifies it as a
        schema_violation format_error — visible, never silently coerced into
        a synthetic pass, never invented here. A declaration the fake cannot
        honestly speak (kind mismatch, writer/authority kind) is left
        un-encoded: the missing raw_output classify as format_error at the
        Runtime. Undeclared dispatches keep their legacy result dicts
        untouched (no raw_output key). The two deliberate exceptions are the
        synthesized VERIFY_FINAL / M-DESIGN review revise fields
        (``_ensure_verify_final_revise_fields`` /
        ``_ensure_design_review_revise_fields``): those stages need the
        schema-required review content to classify the revise (the M-VERIFY
        anchor judge and the M-DESIGN RESPOND loop), so the fake completes
        the minimal deterministic fields instead of leaving the reply
        incomplete — and marks every synthesized finding ``simulated`` so no
        consumer mistakes it for real review evidence."""
        if not is_declared_assignment(assignment):
            return result
        kind = assignment["envelope"].get("kind")
        if kind not in _FAKE_SPEAKABLE_KINDS or kind != envelope_kind(role, substate):
            return result
        # Honest treatment of the artifact faces on a fake declared dispatch:
        # they are genuinely absent (no materialized copies exist), recorded
        # as provenance — never fabricated into evidence.
        result.setdefault("parity", dict(_FAKE_PARITY_PROVENANCE))
        if kind == "prism:final" and str(substate or "").upper() == "VERIFY_FINAL":
            self._ensure_verify_final_revise_fields(result, assignment)
        if kind == "prism:review" and self._is_design_prism_review(assignment):
            self._ensure_design_review_revise_fields(result, assignment)
        payload = self._declared_reply_payload(kind, result)
        result["raw_output"] = encode_envelope_reply(
            kind, payload, self.envelope_version
        )
        return result

    def finalize_act(
        self,
        result: dict,
        role: str,
        substate: str,
        assignment: dict | None = None,
    ) -> dict:
        """IF-ENVELOPE-002 finalization seam: encode the declared reply from
        the FINAL result.

        ``act()`` may return before result-enriching subclasses (the accepted
        ``_AnchoredFinalBackend`` pattern) finish updating the outcome; the
        Runtime calls this hook after ``act()`` and before the collection face
        consumes ``raw_output``, so the encoded reply always reflects the
        bytes the fake finally stands behind."""
        return self._attach_declared_raw_output(result, role, substate, assignment)

    @staticmethod
    def _ensure_verify_final_revise_fields(
        result: dict, assignment: dict | None
    ) -> None:
        """Complete a simulated VERIFY_FINAL revise into a schema-valid review.

        M-VERIFY judges an anchoring on a WELL-FORMED review: a revise whose
        findings are not tied to an open discussion thread is
        ``revise_without_findings`` (an attention-required block), never a
        format error. The simulated bare revise therefore carries the minimum
        deterministic review content the declared schema requires and NO
        ``discussion_refs``: the Runtime's anchor judge classifies it. Fields
        the simulation (or a result-enriching subclass) already produced are
        never overwritten. M-TEST review kinds keep the 32b81c2 honesty rule —
        a simulated revise the schema cannot represent stays incomplete and is
        classified visibly at the Runtime; the M-DESIGN review completes its
        simulated revise instead (see ``_ensure_design_review_revise_fields``)
        because the v0.3 flow.md §8.1 RESPOND loop must really run."""
        if result.get("verdict") != "revise":
            return
        summary = result.get("review_summary")
        if not isinstance(summary, str) or not summary.strip():
            result["review_summary"] = "simulated final review requested changes"
        body = result.get("review_body")
        if not isinstance(body, str) or not body.strip():
            result["review_body"] = (
                "FakeBackend simulated a final-review revise without anchored "
                "discussion findings."
            )
        findings = result.get("findings")
        if isinstance(findings, list) and findings:
            return
        candidate_sha = result.get("candidate_sha")
        if not isinstance(candidate_sha, str) or not candidate_sha:
            candidate_sha = (assignment or {}).get("candidate_sha") or "unfrozen"
        result["findings"] = [
            {
                "id": "FAKE-VERIFY-FINAL-01",
                "severity": "blocker",
                "defect_classification": result.get("defect_classification")
                or "behavior",
                "criterion": "simulated",
                "artifact": f"candidate:{candidate_sha}",
                "ac_refs": [],
                "summary": "simulated final-review finding (no discussion anchor)",
                # Isolation marker: consumers must never treat a synthesized
                # finding as a real anchored blocker (the executor anchor
                # judge excludes simulated findings; see
                # Executor._verify_final_discussion_anchor).
                "simulated": True,
            }
        ]

    @staticmethod
    def _is_design_prism_review(assignment: dict | None) -> bool:
        """True for the M-DESIGN PRISM_REVIEW dispatch only.

        The M-DESIGN review assignment is the sole reviewer card whose
        ``skills`` carry the design criteria pack ``tracks-prism-design``
        (machine_decide ``_decide_review`` multi-doc branch); the M-TEST
        review carries ``tracks-prism-test``. This is the stage signal the
        ``act()`` face has — the assignment itself never carries ``stage``."""
        skills = (assignment or {}).get("skills")
        return isinstance(skills, list) and "tracks-prism-design" in skills

    @staticmethod
    def _ensure_design_review_revise_fields(
        result: dict, assignment: dict | None
    ) -> None:
        """Complete a simulated M-DESIGN revise into a schema-valid review.

        flow.md §8.1: a Prism revise in M-DESIGN drives the RESPOND loop
        (Archer revises the trio, then a NEW review round). The declared
        ``prism:review`` schema requires review_summary + review_body +
        findings on a revise; a bare simulated revise would be classified as
        a schema_violation format_error at collection and strand the run at
        active/PRISM_REVIEW forever (the B62 #80 stall class, fixed for
        DIAGNOSE in 792f70e). The simulated revise therefore carries the
        minimum deterministic review content the schema requires, marked
        simulated so the fake provenance stays visible — never a synthesized
        pass (the verdict stays ``revise``) and never a real design finding.
        Fields already produced (or enriched by a subclass) are kept."""
        if result.get("verdict") != "revise":
            return
        summary = result.get("review_summary")
        if not isinstance(summary, str) or not summary.strip():
            result["review_summary"] = "simulated design review requested changes"
        body = result.get("review_body")
        if not isinstance(body, str) or not body.strip():
            result["review_body"] = (
                "FakeBackend simulated a design-review revise; the RESPOND "
                "loop re-drafts the design trio."
            )
        findings = result.get("findings")
        if isinstance(findings, list) and findings:
            return
        docs = (assignment or {}).get("docs")
        artifact = docs[0] if isinstance(docs, list) and docs else "design-trio"
        result["findings"] = [
            {
                "id": "FAKE-DESIGN-REVIEW-01",
                "severity": "blocker",
                "defect_classification": result.get("defect_classification")
                or "design_gap",
                "criterion": "simulated",
                "artifact": artifact,
                "ac_refs": [],
                "summary": "simulated design-review finding (drives RESPOND)",
                # Isolation marker: never consumed as a real design blocker
                # (the verdict payload/audit carries it as simulated prose).
                "simulated": True,
            }
        ]

    def _declared_reply_payload(self, kind: str, result: dict) -> dict:
        """The fake's actual simulated fields under the assigned kind — never
        invented, never coerced: the verdict/classification come straight
        from the simulated outcome, and fields the simulation did not
        explicitly produce stay absent for the Runtime to classify."""
        fields = (
            "review_summary", "review_body", "findings", "defect_classification",
            "discussion_refs", "review_ref", "candidate_sha", "reason", "evidence",
        )
        payload = {field: result[field] for field in fields if field in result}
        if kind == "prism:diagnose":
            payload["classification"] = result.get("classification", result.get("verdict"))
        else:
            payload["verdict"] = result.get("verdict")
        return payload
