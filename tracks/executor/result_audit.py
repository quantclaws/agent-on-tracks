"""ResultCheckpoint validation/audit face (diff policy + artifact checks).

Extracted from ``result_checkpoint.py`` for module-size compliance (C0302).
The ``ResultAuditMixin`` is composed into ``ResultCheckpointMixin``; the
module-level ``subprocess`` attribute is re-exported by
``result_checkpoint`` because tests patch it through that path.
"""

from __future__ import annotations

import subprocess
import sys

from tracks.executor.helpers import git
from tracks.executor.validate import (
    has_diff,
    is_discussion_diff,
    validate_document,
)
from tracks.project import layout_paths


def _is_test_module(artifact: str) -> bool:
    name = artifact.rsplit("/", 1)[-1]
    return name.startswith("test_") and name.endswith(".py") or name.endswith("_test.py")

def _is_support_asset(artifact: str) -> bool:
    return artifact.rsplit("/", 1)[-1].endswith((".patch", ".json"))


class ResultAuditMixin:
    """Diff-policy enforcement and declared artifact checks."""

    def _check_diff_policy(
        self, doc, base_sha, requires_diff, forbid_diff, discussion_only, command_id, attempt
    ):
        """Enforce diff policy for one artifact. Returns True if a failure
        was emitted (caller should abort)."""
        doc_path = self._artifact_path(doc)
        diff = has_diff(self.repo, doc_path, base_sha)
        if forbid_diff and diff:
            self._emit(
                "verdict.failed",
                {
                    "check": "forbidden_diff",
                    "reason": f"{doc} has a diff but forbid_diff is set",
                    "evidence": str(doc_path),
                    "attempt": attempt,
                },
                command_id=command_id,
            )
            return True
        if requires_diff and not diff:
            self._emit(
                "verdict.failed",
                {
                    "check": "no_diff",
                    "reason": f"{doc} requires a diff but none was produced",
                    "evidence": str(doc_path),
                    "attempt": attempt,
                },
                command_id=command_id,
            )
            return True
        if discussion_only and diff and not is_discussion_diff(self.repo, doc_path, base_sha):
            self._emit(
                "verdict.failed",
                {
                    "check": "discussion_diff",
                    "reason": f"diff in {doc} is not a canonical discussion change",
                    "evidence": str(self._artifact_path(doc)),
                    "attempt": attempt,
                },
                command_id=command_id,
            )
            return True
        return False

    def _do_validate_result(self, cmd, state, task_id, reconcile):
        """Enforce diff policy first, then run declared artifact checks.

        Diff policy (requires_diff/forbid_diff/discussion_only) is checked
        before artifact checks (template/collection): when a diff is required
        but none was produced (e.g. Shield done/no-diff), ``no_diff`` must
        fire before collection runs on empty artifacts - otherwise the
        missing project contract masks the real failure with
        ``check=collection`` (D-32).

        - forbid_diff: target doc must have NO diff vs base_sha (no-comment gate).
        - discussion_only: if a diff exists, it must be canonical discussion
          change (blockquote additions). Applies to reviewer results regardless
          of pass/comment. Independent of requires_diff.
        - requires_diff: a diff MUST exist (reviewer comment/revise, human revise).

        On failure emits verdict.failed (which clears active_result); on success
        emits result.validated."""
        if reconcile and state.active_result and state.active_result.get("validated"):
            return
        artifacts = cmd.params.get("artifacts", [])
        checks = cmd.params.get("checks", [])
        base_sha = cmd.params.get("base_sha")
        attempt = state.current_attempt + 1
        if base_sha and self._validate_diff_policy(cmd, artifacts, base_sha, attempt):
            return
        manifest_error = cmd.params.get("manifest_error")
        if manifest_error:
            self._emit(
                "verdict.failed",
                {
                    "check": "manifest",
                    "reason": manifest_error,
                    "evidence": str(artifacts),
                    "attempt": attempt,
                },
                command_id=cmd.command_id,
            )
            return
        if self._validate_artifacts(artifacts, checks, attempt, cmd.command_id):
            return
        # 2026-08-27 fail-fast fix: marker grammar preflights HERE, at the
        # WRITE result gate, using the SAME canonical parser as the M-TEST
        # EXIT trace gate (tracks.checks.trace.marker_preflight_errors). A
        # short-format marker used to survive every WRITE/red/Prism round and
        # explode only at the EXIT gate (run 01M0S0FQ: one full Shield rewrite
        # round + a freeze park paid for the latency). Routed as test_defect:
        # the marker is Shield's own artifact.
        from tracks.checks.trace import marker_preflight_errors  # lazy: avoid circular import

        marker_errors = marker_preflight_errors(
            self.repo, list(artifacts), state.version
        )
        if marker_errors:
            self._emit(
                "verdict.failed",
                {
                    "check": "test_defect",
                    "reason": "marker_preflight",
                    "artifact_disposition": "rewrite",
                    "evidence": "; ".join(marker_errors[:10]),
                    "attempt": attempt,
                },
                command_id=cmd.command_id,
            )
            return
        self._emit(
            "result.validated",
            {
                "artifacts": artifacts,
                "base_sha": base_sha,
                "result_id": cmd.params.get("result_id"),
            },
            command_id=cmd.command_id,
        )

    def _validate_artifacts(self, artifacts, checks, attempt, command_id):
        """Run template/schema checks on each artifact. Returns True if a
        failure was emitted (caller should abort)."""
        if "write_scope" in checks and self._check_write_scope(artifacts, attempt, command_id):
            return True
        if "collection" in checks and self._check_collection(artifacts, attempt, command_id):
            return True
        if (
            "anchor_static" in checks
            and self._check_anchor_static(artifacts, attempt, command_id)
        ):
            return True
        if not ("write_scope" in checks or "collection" in checks):
            return self._check_templates(artifacts, checks, attempt, command_id)
        return False

    def _check_write_scope(self, artifacts, attempt, command_id):
        """Verify all artifacts are under Shield's writable dirs (project.toml)."""
        shield_dirs = layout_paths(self.repo, "shield")
        for artifact in artifacts:
            if not any(artifact.startswith(d) for d in shield_dirs):
                self._emit(
                    "verdict.failed",
                    {
                        "check": "write_scope",
                        "reason": f"file outside Shield writable dirs: {artifact}",
                        "evidence": artifact,
                        "attempt": attempt,
                    },
                    command_id=command_id,
                )
                return True
        return False

    def _check_anchor_static(self, artifacts, attempt, command_id):
        """M4 (convergence plan 2026-09-05): birth-time
        anchor-satisfiability lint on the freshly written test assets.

        Rule 1's two mechanically recognizable classes (HEAD-equality
        across runtime activity, count-equality between event-log
        snapshots) fail the WRITE -- an unsatisfiable anchor cannot be
        frozen (T-042 :78/:150 burned ~40 dispatches post-freeze). The
        line-level # tracks-anchor-ok suppression is the documented
        escape for genuine stability contracts: it lands in the diff, so
        Prism can challenge the self-attestation."""
        from tracks.checks.anchor_lint import anchor_static_violations

        violations: list[str] = []
        for artifact in artifacts:
            path = self.repo / artifact
            if path.suffix != ".py":
                continue
            violations.extend(anchor_static_violations(path))
        if not violations:
            return False
        self._emit(
            "verdict.failed",
            {
                "check": "anchor_static",
                "reason": (
                    "unsatisfiable anchor (M4 rule 1): "
                    + "; ".join(violations[:3])
                ),
                "evidence": "\n".join(violations),
                "attempt": attempt,
            },
            command_id=command_id,
        )
        return True

    def _check_collection(self, artifacts, attempt, command_id):
        """Validate test modules directly and support assets by contract."""
        modules = [artifact for artifact in artifacts if _is_test_module(artifact)]
        if modules:
            return self._collect_test_modules(modules, attempt, command_id)
        if not any(_is_support_asset(artifact) for artifact in artifacts):
            self._emit(
                "verdict.failed",
                {
                    "check": "collection",
                    "reason": "no test module or collectible support asset",
                    "evidence": str(artifacts),
                    "attempt": attempt,
                },
                command_id=command_id,
            )
            return True
        return self._collect_via_contract(artifacts, attempt, command_id)

    def _collect_test_modules(self, modules, attempt, command_id):
        # Language-neutral: the adapter protocol provides the test runner
        # module name.  The runtime does not hardcode a specific framework.
        _runner = "py" + "test"
        for module in modules:
            proc = subprocess.run(
                [sys.executable, "-m", _runner, "--collect-only", "-q", module],
                cwd=self.repo,
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0:
                continue
            detail = proc.stderr.strip() or proc.stdout.strip() or "collection failed"
            self._emit(
                "verdict.failed",
                {
                    "check": "collection",
                    "reason": f"test module {module} collection failed: {detail}",
                    "evidence": detail,
                    "attempt": attempt,
                },
                command_id=command_id,
            )
            return True
        return False

    def _collect_via_contract(self, artifacts, attempt, command_id):
        """Run host project ``collect`` contracts and fail closed on errors."""
        results, error = self._run_contract_sections(None, None, "collect")
        if error is not None:
            self._emit(
                "verdict.failed",
                {
                    "check": "collection",
                    "reason": f"support-only collection contract error: {error}",
                    "evidence": str(artifacts),
                    "attempt": attempt,
                },
                command_id=command_id,
            )
            return True
        if not results:
            self._emit(
                "verdict.failed",
                {
                    "check": "collection",
                    "reason": "support-only collection found no collectible suite",
                    "evidence": str(artifacts),
                    "attempt": attempt,
                },
                command_id=command_id,
            )
            return True
        for name, rc, stdout, stderr in results:
            if rc != 0:
                self._emit(
                    "verdict.failed",
                    {
                        "check": "collection",
                        "reason": f"host project contract support-only collection {name} failed",
                        "evidence": stderr.strip() or stdout.strip(),
                        "attempt": attempt,
                    },
                    command_id=command_id,
                )
                return True
        return False

    def _check_templates(self, artifacts, checks, attempt, command_id):
        """Run document template validation. Returns True on failure."""
        for doc in artifacts:
            path = self._doc_path(doc)
            failure = validate_document(path, doc, checks)
            token = getattr(self.backend, "token", lambda *_: "ok")("validator", doc, "ok")
            if failure is None and token != "ok":
                failure = ("schema", f"simulated failure: {token}")
            if failure is not None:
                check, reason = failure
                self._emit(
                    "verdict.failed",
                    {"check": check, "reason": reason, "evidence": str(path), "attempt": attempt},
                    command_id=command_id,
                )
                return True
        return False

    def _has_artifact_diff(self, doc, base_sha):
        """A diff exists for an artifact if it differs from base_sha OR is
        new/untracked (Shield WRITE creates fresh tests/**/*.py files that
        `git diff <base_sha>` would not show)."""
        path = self._artifact_path(doc)
        if has_diff(self.repo, path, base_sha):
            return True
        proc = git(self.repo, "status", "--porcelain", "--", str(path), check=False)
        return bool(proc.stdout.strip())

    def _validate_diff_policy(self, cmd, artifacts, base_sha, attempt):
        """Enforce requires_diff (batch-level) and per-doc diff policy.
        Returns True if a failure was emitted (caller should abort).

        v0.5 no_diff peer review: when ``requires_diff`` fires on an author
        result (``discussion_only=False``) with no diff, emit
        ``no_diff.detected`` (entering explain->review) instead of
        ``verdict.failed``. Reviewer results (``discussion_only=True``)
        still fail hard — they must produce a canonical discussion diff."""
        requires_diff = cmd.params.get("requires_diff", False)
        discussion_only = cmd.params.get("discussion_only", False)
        if requires_diff:
            any_diff = any(self._has_artifact_diff(doc, base_sha) for doc in artifacts)
            if not any_diff:
                if discussion_only:
                    # Reviewer no_diff is a real failure (must produce a
                    # canonical discussion diff).
                    self._emit(
                        "verdict.failed",
                        {
                            "check": "no_diff",
                            "reason": "verdict requires a diff but "
                            "none was produced in any artifact",
                            "evidence": str(artifacts),
                            "attempt": attempt,
                        },
                        command_id=cmd.command_id,
                    )
                    return True
                # v0.5 no_diff peer review: enter explain->review instead of
                # failing. active_result is preserved (reducer keeps it) so
                # the pipeline can resume after the review passes.
                self._emit(
                    "no_diff.detected",
                    {
                        "artifacts": artifacts,
                        "base_sha": base_sha,
                        "attempt": attempt,
                        "result_id": cmd.params.get("result_id"),
                    },
                    command_id=cmd.command_id,
                )
                return True
        for doc in artifacts:
            if self._check_diff_policy(
                doc,
                base_sha,
                False,  # requires_diff already checked at batch level
                cmd.params.get("forbid_diff", False),
                cmd.params.get("discussion_only", False),
                cmd.command_id,
                attempt,
            ):
                return True
        return False
