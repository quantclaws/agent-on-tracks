"""FakeBackend dispatch face (token simulation, legacy/author routing).

Extracted from ``fake.py`` for module-size compliance (C0302). The
``FakeActMixin`` is inherited by ``FakeBackend``; it relies on the host for
``repo``/``version``/``_calls`` and on the role mixins for the per-stage work.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from tracks.effects.fake_shield import _FAILED_TOKENS

_PRISM_REVIEW_SUBSTATES = (
    "PRISM_REVIEW",
    "PRISM_PLAN",
    "PRISM_RED",
    "PRISM_FINAL",
    "DIAGNOSE",
)

_ANCHOR_VERDICT = {"anchor_overturned": "overturned"}


def _simulate_map() -> dict:
    raw = os.environ.get("TRAC_FAKE_SIMULATE", "")
    out: dict = {}
    for part in raw.replace(",", ";").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out



class FakeActMixin:
    """TRAC_FAKE_SIMULATE token routing and the legacy act() dispatch table."""

    def token(self, key_left: str, key_right: str, default: str) -> str:
        key = f"{key_left}:{key_right}"
        seq = _simulate_map().get(key, default).split("|")
        n = self._calls.get(key, 0)
        self._calls[key] = n + 1
        return seq[min(n, len(seq) - 1)].strip()

    def act(
        self,
        role: str,
        substate: str,
        doc: str | None,
        doc_path: Path | None,
        assignment: dict | None = None,
        worktree: Path | None = None,
    ) -> dict:
        self._worktree_root = Path(worktree) if worktree is not None else None
        no_diff = self._act_no_diff(role, substate)
        if no_diff is not None:
            return no_diff
        if role == "sage" and substate == "SAGE_TRIAGE":
            return self._act_sage_triage(assignment)
        if role == "devon":
            data = assignment if isinstance(assignment, dict) else {}
            return self._act_m_impl_devon(substate, data)
        return self._attach_declared_raw_output(
            self._act_legacy(role, substate, doc, doc_path, assignment),
            role,
            substate,
            assignment,
        )

    def _repo_root(self) -> Path:
        """B1 (issue #2): the root writer fakes write to — the isolated
        worktree when the dispatch carries one, else the main tree."""
        return self._worktree_root or self.repo

    def _act_legacy(
        self,
        role: str,
        substate: str,
        doc: str | None,
        doc_path: Path | None,
        assignment: dict | None,
    ) -> dict:
        if substate == "TRIAGE":
            return {
                "status": "done",
                "artifact_ref": None,
                "self_report": "explored raw requirement",
            }
        if role == "shield":
            return self._act_shield(substate, assignment)
        if substate in ("DRAFT", "RESPOND"):
            return self._act_draft(role, substate, doc, doc_path, assignment)
        if self._is_m_impl_planning(role, substate):
            return self._act_m_impl_planning(assignment)
        verdict = self.token(role, substate, "pass")
        result = {
            "status": "done",
            "artifact_ref": None,
            "self_report": f"review: {verdict}",
            "verdict": verdict,
        }
        self._apply_diagnose_simulation(role, substate, result)
        # v0.5 ResultCheckpoint: non-pass verdicts must produce a diff
        # (discussion annotation) so the pipeline can checkpoint it.
        if verdict in ("revise", "comment"):
            self._maybe_annotate_review(doc_path, assignment, role, verdict, result)
        # D-29: Prism echoes the assigned criteria-pack identity.
        if role == "prism" and substate in _PRISM_REVIEW_SUBSTATES:
            assigned_pack = (assignment or {}).get("criteria_pack")
            if assigned_pack:
                result["criteria_pack"] = dict(assigned_pack)
            # FR-0243: the review payload carries the anchor verdict with a
            # default of upheld; only the anchor_overturned token flips it.
            result["anchor_verdict"] = _ANCHOR_VERDICT.get(verdict, "upheld")
        # defect_classification injection (for testing rollback routing)
        if role == "prism" and substate in _PRISM_REVIEW_SUBSTATES and verdict != "pass":
            dc = _simulate_map().get("prism:defect_classification")
            if dc:
                result["defect_classification"] = dc
        self._ack_injected_evidence(result, assignment)
        return result

    @staticmethod
    def _ack_injected_evidence(result: dict, assignment: dict | None) -> None:
        """Simulate the outcome's ``evidence_ack`` (IF-FAILURE-001 §1k).

        The agent consumed the failure ids the Runtime injected into its
        assignment (``failure_evidence``); the fake echoes exactly those ids
        — never an invented acknowledgement."""
        if not isinstance(result, dict) or result.get("evidence_ack"):
            return
        injected = [
            item
            for item in (assignment or {}).get("failure_evidence") or []
            if isinstance(item, str) and item
        ]
        if injected:
            result["evidence_ack"] = injected

    def _act_no_diff(self, role: str, substate: str) -> dict | None:
        """v0.5 no_diff peer review: canned outcomes for explain/review."""
        if substate == "NO_DIFF_EXPLAIN":
            return {
                "status": "done",
                "artifact_ref": None,
                "self_report": "work was already committed in a prior commit; no new diff produced",
            }
        if substate == "NO_DIFF_REVIEW":
            verdict = self.token(role, "NO_DIFF_REVIEW", "pass")
            return {
                "status": "done",
                "artifact_ref": None,
                "verdict": verdict,
                "self_report": f"no_diff review: {verdict}",
            }
        return None

    def _maybe_annotate_review(self, doc_path, assignment, role, verdict, result):
        """Annotate the review doc in-place so non-pass verdicts produce a
        diff for the ResultCheckpoint pipeline."""
        annotate_path = doc_path
        if annotate_path is None and assignment and assignment.get("docs"):
            annotate_path = self._design_vdir() / assignment["docs"][0]
        if annotate_path and annotate_path.exists():
            self._annotate_review(annotate_path, role, verdict)
            result["diff_ref"] = "annotation"

    @staticmethod
    def _annotate_review(doc_path: Path, role: str, verdict: str) -> None:
        """Append a canonical discussion annotation to the doc so the
        checkpoint pipeline has a diff to stage for non-pass verdicts."""
        text = doc_path.read_text(encoding="utf-8")
        name = role.capitalize()
        annotation = f"\n\n> **{name}:** review annotation ({verdict})\n"
        doc_path.write_text(text + annotation, encoding="utf-8")

    @staticmethod
    def _resolve_open_threads(doc_path: Path) -> None:
        """Resolve all open discussion threads in the doc.

        Simulates the author addressing reviewer comments during RESPOND:
        changes ``> **Name:**`` (open) to ``> **Name [resolved]:**`` so the
        EXIT ``discussion_ready`` gate passes."""
        text = doc_path.read_text(encoding="utf-8")
        changed = re.sub(r"^(>\s*\*\*@?[^*\[\]]+?)\s*:\*\*", r"\1 [resolved]:**", text, flags=re.M)
        if changed != text:
            doc_path.write_text(changed, encoding="utf-8")

    def _write_stage_doc(self, doc: str | None, doc_path: Path | None, token: str) -> None:
        if doc == "story.md":
            self._write_story(doc_path)
        elif doc == "acceptance.md":
            self._write_acceptance(doc_path, token)
        else:
            self._write_spec(doc_path, token)

    def _act_draft(
        self,
        role: str,
        substate: str,
        doc: str | None,
        doc_path: Path | None,
        assignment: dict | None = None,
    ) -> dict:
        token = self.token(role, substate, "ok")
        if token in _FAILED_TOKENS:
            fclass = "agent_failed" if token == "fail" else token
            return {
                "status": "failed",
                "artifact_ref": None,
                "failure_class": fclass,
                "audit_evidence": f"simulated {fclass}",
                "self_report": f"agent exit gate failed: {fclass}",
            }
        if token == "hang":
            time.sleep(600)  # blocked agent: lock-contention path (AC-27a)
        if role == "archer":
            delta = self._act_hotfix_design(assignment, token)
            if delta is not None:
                return delta
            scaffold, failure = self._validated_fake_scaffold(assignment)
            if failure is not None:
                return failure
            return self._act_design(substate, token, scaffold)
        self._write_stage_doc(doc, doc_path, token)
        if substate == "RESPOND" and doc_path and doc_path.exists():
            self._resolve_open_threads(doc_path)
        return {
            "status": "done",
            "artifact_ref": str(doc_path),
            "self_report": f"wrote {doc} ({token})",
        }

    @staticmethod
    def _apply_diagnose_simulation(role: str, substate: str, result: dict) -> None:
        """Mirror the real DIAGNOSE classification payload in fake mode.

        The declared ``prism:diagnose`` schema requires classification +
        non-empty reason + non-empty evidence; a simulated classification
        without them would be classified as a schema_violation format_error
        at collection (the DIAGNOSE verdict would never land). The simulated
        reason/evidence carry an explicit "simulated" marker — honest about
        being fake-mode output, never masquerading as a real forensic
        package."""
        if role != "prism" or substate != "DIAGNOSE":
            return
        classification = _simulate_map().get("diagnose:classification")
        if classification in {
            "test_defect",
            "impl_defect",
            "red_defect",
            "plan_defect",
            "stub_gap",
            "ac_gap",
            "spec_gap",
        }:
            result["verdict"] = classification
            result["self_report"] = f"diagnose: {classification}"
            result["reason"] = f"simulated diagnosis: {classification}"
            result["evidence"] = {
                "source": "fake_simulation",
                "detail": (
                    "FakeBackend simulated classification (no real forensic "
                    f"package); requested classify={classification}"
                ),
            }
