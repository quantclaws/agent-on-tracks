"""ResultCheckpoint payload construction (per-stage pipeline payloads).

Extracted from ``result_checkpoint.py`` for module-size compliance (C0302).
The ``ResultPayloadMixin`` is composed into ``ResultCheckpointMixin`` and
relies on the host for ``repo``/``store``/``_emit``/``_doc_path``/
``_doc_paths``/``_design_scaffold_paths``/``_apply_prism_review_fields``.
"""

from __future__ import annotations

from tracks.executor.file_identity import (
    is_regular_file_identity as _is_regular_file_identity,
)
from tracks.executor.helpers import git
from tracks.executor.validate import capture_digests
from tracks.kernel.machine import _STAGES, DESIGN_DOCS
from tracks.project import layout_paths
from tracks.store import new_ulid


class ResultPayloadMixin:
    """ResultCheckpoint payload construction for every stage/substate."""

    def _result_checkpoint_payload(
        self, cmd, state, result, params, substate, role, doc, pre_dirty=None
    ):
        """Build the ResultCheckpoint pipeline payload for a successful agent
        dispatch. Delegates to per-stage helpers."""
        base_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        result_id = cmd.command_id
        if state.stage == "M-DESIGN":
            return self._design_payload(substate, role, result, base_sha, result_id)
        if state.stage == "M-TEST":
            return self._m_test_payload(substate, role, result, base_sha, result_id, pre_dirty)
        return self._requirement_payload(state, substate, role, result, base_sha, result_id)

    def _design_payload(self, substate, role, result, base_sha, result_id):
        """M-DESIGN: multi-doc pipeline (3 design docs + scaffold)."""
        docs = list(DESIGN_DOCS)
        doc_paths = {doc: self._doc_path(doc) for doc in docs}
        digests = capture_digests(doc_paths)
        if substate in ("DRAFT", "RESPOND"):
            return self._design_draft_payload(
                substate, role, docs, doc_paths, digests, base_sha, result_id
            )
        verdict = result.get("verdict")
        # D-35 / PRISM-D35-R1-ADV1: M-DESIGN never REQUIRES the structured
        # fields (AC-FR0240-04) but promises passthrough when present —
        # thread them into the domain payload and persist the body to blobs.
        domain_payload = {"verdict": verdict}
        if verdict != "pass":
            self._apply_prism_review_fields(domain_payload, verdict, result)
        # B48 (issue #60 / PRISM-V06-R2-01): an anchor-overturned revise is
        # carried entirely by the structured channel — the anchor_verdict
        # routing field (interfaces §1a / IF-HOTFIX-009) routes M-DESIGN back
        # to M-HOTFIX-TRIAGE/SAGE_TRIAGE in the kernel without any document
        # diff. requires_diff must not hard-fail it at pipeline validation
        # ("Reviewer no_diff is a real failure") or the verdict never
        # publishes and the rollback never fires — mirror of the M-TEST
        # structured_revise exemption (D-35 SC-D35 §2.3).
        anchor_overturned = (
            verdict != "pass" and result.get("anchor_verdict") == "overturned"
        )
        return {
            "source": "prism",
            "stage": "M-DESIGN",
            "substate": "PRISM_REVIEW",
            "actor_kind": "agent",
            "verdict": verdict,
            "artifacts": docs,
            "allowed_paths": docs,
            "base_sha": base_sha,
            "checks": ["template"],
            "requires_diff": verdict != "pass" and not anchor_overturned,
            "forbid_diff": False,
            "discussion_only": True,
            "commit_label": f"M-DESIGN: prism ({verdict}) checkpoint",
            "result_id": result_id,
            "digests": digests,
            "domain_event": {"type": "prism.verdict", "payload": domain_payload},
        }

    def _design_draft_payload(self, substate, role, docs, doc_paths, digests, base_sha, result_id):
        """M-DESIGN DRAFT/RESPOND: author commits design docs + scaffold."""
        allowed = list(docs)
        scaffold_paths_str = []
        arch_path = doc_paths.get("architecture.md")
        if arch_path and arch_path.exists():
            scaffold_paths, issue = self._design_scaffold_paths(arch_path)
            if issue is None:
                for sp in scaffold_paths:
                    rel = str(sp.relative_to(self.repo))
                    allowed.append(rel)
                    scaffold_paths_str.append(rel)
        return {
            "source": role,
            "stage": "M-DESIGN",
            "substate": substate,
            "actor_kind": "agent",
            "artifacts": docs,
            "allowed_paths": allowed,
            "base_sha": base_sha,
            "checks": ["template"],
            "requires_diff": False,
            "forbid_diff": False,
            "discussion_only": False,
            "commit_label": f"M-DESIGN: {role} commit",
            "result_id": result_id,
            "digests": {
                **digests,
                **capture_digests(
                    {
                        str(p.relative_to(self.repo)): p
                        for p in (self.repo / sp for sp in scaffold_paths_str)
                        if (self.repo / sp).exists()
                    }
                ),
            },
            "domain_event": {"type": "design.committed", "payload": {}},
        }

    def _m_test_payload(self, substate, role, result, base_sha, result_id, pre_dirty):
        """M-TEST: Shield WRITE (tests/) or Prism PRISM_REVIEW."""
        if substate == "WRITE":
            return self._m_test_write_payload(result, base_sha, result_id, pre_dirty)
        return self._m_test_prism_payload(result, base_sha, result_id)

    def _m_test_write_payload(self, result, base_sha, result_id, pre_dirty):
        """M-TEST Shield WRITE: commit test files and support assets.

        ``pre_dirty`` is either a content-identity snapshot (``dict[str,str]``
        persisted in ``command.issued`` params) or a legacy path-set
        (``set[str]`` / ``None``). When a snapshot is available, a path is
        attributed iff its identity changed between dispatch and Agent
        return — catching pre-existing dirty/untracked files that Shield
        *modified* (which the legacy path-set diff missed). Legacy path-sets
        fall back to conservative set-diff semantics.

        Attributes all Shield-created/modified non-ignored regular files
        under the role's writable directories (from ``project.toml
        [layout.shield]``) by content identity, not only ``.py``. Document
        discussion replies (e.g. to ``test-plan.md``) are validated
        separately by the doc-delta check and excluded from the artifact
        manifest comparison."""
        shield_dirs = layout_paths(self.repo, "shield")
        test_files = self._shield_write_test_files(pre_dirty, shield_dirs)
        code_include = self._shield_include_paths(result, shield_dirs)
        test_files, manifest_error = self._shield_manifest_check(test_files, code_include)
        artifacts = list(code_include if manifest_error is None else test_files)
        digests = capture_digests({f: self.repo / f for f in artifacts})
        return {
            "source": "shield",
            "stage": "M-TEST",
            "substate": "WRITE",
            "actor_kind": "agent",
            "artifacts": artifacts,
            "allowed_paths": artifacts,
            "base_sha": base_sha,
            "checks": ["write_scope", "collection", "anchor_static"],
            # D-32: WRITE requires an attributable test-asset diff (test
            # modules and/or support assets), so a done/no-diff Shield
            # output fails validation and can never publish test.written
            # / reach COLLECT (it retries/escalates).
            "requires_diff": True,
            "forbid_diff": False,
            "discussion_only": False,
            "commit_label": result.get("suggested_commit_message"),
            "result_id": result_id,
            "digests": digests,
            "artifact_manifest": result.get("artifact_manifest"),
            "manifest_error": manifest_error,
            "domain_event": {"type": "test.written", "payload": {}},
        }

    def _shield_write_test_files(self, pre_dirty, shield_dirs: list[str]) -> list[str]:
        """Shield-written files, attributed by content identity (or set diff)."""
        if isinstance(pre_dirty, dict):
            post_snapshot = self._dirty_snapshot()
            return sorted(
                f
                for f in post_snapshot
                if _is_regular_file_identity(post_snapshot[f])
                and pre_dirty.get(f) != post_snapshot[f]
                and any(f.startswith(d) for d in shield_dirs)
            )
        post_dirty = self._dirty_files()
        changed = (post_dirty - pre_dirty) if pre_dirty is not None else post_dirty
        return sorted(
            f
            for f in changed
            if any(f.startswith(d) for d in shield_dirs)
            and (self.repo / f).is_file()
            and not (self.repo / f).is_symlink()
        )

    @staticmethod
    def _shield_include_paths(result, shield_dirs: list[str]) -> list[str]:
        """Manifest include paths under the Shield layout dirs.

        Only compare code/data artifacts (under shield_dirs) against observed
        files; document discussion replies are excluded from the manifest
        check (validated by the doc-delta audit instead)."""
        include = (result.get("artifact_manifest") or {}).get("include", [])
        return [
            p
            for p in (e.get("path") for e in include)
            if p and any(p.startswith(d) for d in shield_dirs)
        ]

    def _shield_manifest_check(
        self, test_files: list[str], code_include: list[str]
    ) -> tuple[list[str], str | None]:
        """Reconcile observed files with the manifest include set."""
        manifest_error = None
        if not test_files and code_include:
            # On retry, Shield may not create new files — it verifies
            # existing files from previous attempts. Accept manifest
            # paths if they all exist on disk and are dirty (untracked
            # or modified), even if they weren't changed in this run.
            dirty = self._dirty_files()
            if all(p in dirty and (self.repo / p).is_file() for p in code_include):
                test_files = sorted(code_include)
        if set(code_include) != set(test_files):
            manifest_error = (
                "artifact_manifest include paths do not match observed "
                f"test files: include={sorted(code_include)}, "
                f"observed={test_files}"
            )
        return test_files, manifest_error

    def _m_test_prism_payload(self, result, base_sha, result_id):
        """M-TEST Prism PRISM_REVIEW: checkpoint discussion diff on design docs.
        acceptance.md is excluded - it may carry pre-existing uncommitted
        diffs from stale-approval modifications."""
        prism_docs = ["test-plan.md", "interfaces.md"]
        digests = capture_digests({doc: self._doc_path(doc) for doc in prism_docs})
        verdict = result.get("verdict")
        domain_payload = {"verdict": verdict, "criteria_pack": result.get("criteria_pack")}
        defect_classification = result.get("defect_classification")
        if defect_classification is not None:
            # Omit None so published prism.verdict never carries a null
            # defect_classification (the reducer defaults absent to
            # test_defect; a present null used to leave M-TEST un-routed).
            domain_payload["defect_classification"] = defect_classification
        if verdict != "pass":
            # FR-11: thread reviewer findings into the published event so the
            # kernel can put them into the revise re-dispatch evidence.
            for key in ("review_summary", "findings", "discussion_refs"):
                val = result.get(key)
                if val:
                    domain_payload[key] = val
            # D-35 (SC-D35 §2.3): the full review body is content-addressed
            # into blobs/ and referenced by sha; it never rides inline in the
            # event payload (AC-FR0240-02).
            review_body = result.get("review_body")
            if isinstance(review_body, str) and review_body.strip():
                review_ref = self.store.write_audit_blob(review_body)
                if review_ref:
                    domain_payload["review_ref"] = review_ref
        # D-35 / PRISM-D35-R1-02: a revise carried entirely by the structured
        # channel (validated findings in the result) is complete without a
        # doc diff — requires_diff must not hard-fail it at pipeline
        # validation ("Reviewer no_diff is a real failure"), or the JSON-only
        # path is dead on arrival at M-TEST.
        structured_revise = bool(
            verdict != "pass"
            and result.get("findings")
            and result.get("review_summary")
        )
        return {
            "source": "prism",
            "stage": "M-TEST",
            "substate": "PRISM_REVIEW",
            "actor_kind": "agent",
            "verdict": verdict,
            "artifacts": prism_docs,
            "allowed_paths": prism_docs,
            "base_sha": base_sha,
            "checks": ["template"],
            "requires_diff": verdict != "pass" and not structured_revise,
            "forbid_diff": False,
            "discussion_only": True,
            "commit_label": f"M-TEST: prism ({verdict}) checkpoint",
            "result_id": result_id,
            "digests": digests,
            "domain_event": {"type": "prism.verdict", "payload": domain_payload},
        }

    def _requirement_payload(self, state, substate, role, result, base_sha, result_id):
        """M-STORY/M-SPEC/M-ACC: single-doc pipeline."""
        if substate in ("DRAFT", "RESPOND"):
            return self._requirement_draft_payload(state, substate, role, base_sha, result_id)
        return self._requirement_review_payload(state, substate, role, result, base_sha, result_id)

    def _requirement_draft_payload(self, state, substate, role, base_sha, result_id):
        """Requirement stage DRAFT/RESPOND: author commits single doc."""
        sd = _STAGES[state.stage]
        doc_name = sd.doc
        digests = capture_digests({doc_name: self._doc_path(doc_name)}) if doc_name else {}
        return {
            "source": role,
            "stage": state.stage,
            "substate": substate,
            "actor_kind": "agent",
            "artifacts": [doc_name],
            "allowed_paths": [doc_name],
            "base_sha": base_sha,
            "checks": ["template"],
            "requires_diff": False,
            "forbid_diff": False,
            "discussion_only": False,
            "commit_label": f"{state.stage}: {role} commit",
            "result_id": result_id,
            "digests": digests,
            "domain_event": {
                "type": sd.committed_event,
                "payload": {},
            },
        }

    def _requirement_review_payload(self, state, substate, role, result, base_sha, result_id):
        """Requirement stage reviewer: checkpoint discussion diff, publish verdict."""
        sd = _STAGES[state.stage]
        verdict = result.get("verdict")
        doc_name = sd.doc
        digests = capture_digests({doc_name: self._doc_path(doc_name)}) if doc_name else {}
        return {
            "source": role,
            "stage": state.stage,
            "substate": substate,
            "actor_kind": "agent",
            "verdict": verdict,
            "artifacts": [doc_name] if doc_name else [],
            "allowed_paths": [doc_name] if doc_name else [],
            "base_sha": base_sha,
            "checks": ["template"],
            "requires_diff": verdict != "pass",
            "forbid_diff": False,
            "discussion_only": True,
            "commit_label": f"{state.stage}: {role} ({verdict}) checkpoint",
            "result_id": result_id,
            "digests": digests,
            "domain_event": {
                "type": sd.verdict_event,
                "payload": {"verdict": verdict},
            },
        }

    def submit_human_result(
        self,
        state,
        domain_event_type,
        payload,
        verdict,
        artifacts,
        allowed_paths,
        checks,
        requires_diff,
        commit_label,
        forbid_diff=False,
        discussion_only=False,
    ):
        """v0.5: append result.submitted for a human gate (triage/review).
        The CLI calls this under writer_lock, then calls run_pipeline().

        v0.5 review-A:
        - result_id: stable ULID for audit/reconcile.
        - digests: artifact sha256 at capture, re-verified at checkpoint.
        - forbid_diff: no-comment gate rejects any doc diff.
        - discussion_only: human revise allows body edits (not discussion-only).
        """
        base_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        result_id = new_ulid()
        digests = capture_digests(self._doc_paths(artifacts))
        self._emit(
            "result.submitted",
            {
                "source": "human",
                "actor_kind": "human",
                "stage": state.stage,
                "substate": state.substate,
                "verdict": verdict,
                "artifacts": artifacts,
                "allowed_paths": allowed_paths,
                "base_sha": base_sha,
                "checks": checks,
                "requires_diff": requires_diff,
                "forbid_diff": forbid_diff,
                "discussion_only": discussion_only,
                "commit_label": commit_label,
                "result_id": result_id,
                "digests": digests,
                "domain_event": {
                    "type": domain_event_type,
                    "payload": payload,
                },
            },
        )
