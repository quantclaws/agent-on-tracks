"""Fake Shield behavior (M-TEST WRITE / SHIELD_FIX; FR-0020/FR-0120).

Extracted from ``fake.py`` for module-size compliance (C0302). The
``FakeShieldMixin`` is inherited by ``FakeBackend``; it relies on the host for
``self.token``, ``self.repo``, ``self.version``, and ``self._shield_writes``.
Shield content-identity semantics match ResultCheckpoint's exactly via the
shared ``file_identity`` module (imported lazily to keep the effects boundary
acyclic — importing ``tracks.executor`` at module scope would run
``executor/__init__.py``, which imports ``tracks.effects`` back into this
package during import).

Also hosts the shared ``_ac_slug`` AC-id slug helper (AC-FR0010-01 ->
ac_fr0010_01), used by the Shield test files and, via FakeBackend, by the Fake
M-IMPL task-graph derivation so both name their paths identically.
"""
from __future__ import annotations

from pathlib import Path

from tracks.capabilities import supports_m_impl
from tracks.effects.backend import valid_test_tasks
from tracks.effects.devon_patch import DevonPatchMixin

# FR-0210 exit gate tokens: a failed agent run is not a produced document.
_FAILED_TOKENS = ("over_reach", "timeout", "no_target_diff",
                  "non_zero_exit", "json_truncated", "fail")


def _ac_slug(ac_id: str) -> str:
    """Lowercase hyphen-free module/test slug for an AC id.

    ``AC-FR0010-01`` -> ``ac_fr0010_01``. Shared single source of truth for
    the Shield test filename and the Fake M-IMPL task-graph module names
    (``tracks/impl/{ac_slug}.py`` / ``tests/unit/test_{ac_slug}.py``), so the
    Shield contract and the Devon RGR contract always agree on the path.
    """
    return ac_id.lower().replace("-", "_")


class FakeShieldMixin:
    """Deterministic Shield: writes collectable, legit-Red test files."""

    def _act_shield(self, substate: str, assignment: dict | None) -> dict:
        """Shield writes collectable, legit-Red test files for every required
        (integration|e2e) AC in the host project's test-plan, with long-format
        markers. Tokens: ``fail``/``over_reach`` simulate a failed outcome;
        ``illegit_red`` writes an ImportError-raising test (illegit Red);
        ``short_marker`` writes a short-format marker (trace gate fails).

        D-28: the assignment's ``test_tasks`` (Runtime-parsed from test-plan
        §8) is the only input. Missing or malformed task data is a failed
        outcome; this backend never re-derives it from the design documents.

        Returns ``artifact_manifest`` with repo-relative paths of every
        regular non-symlink tests/ file whose content identity changed during
        this dispatch, matching ResultCheckpoint's observed identity semantics."""
        token = self.token("shield", substate, "ok")
        if token in _FAILED_TOKENS:
            return {"status": "failed", "artifact_ref": None,
                    "failure_class": "agent_failed" if token == "fail" else token,
                    "audit_evidence": f"simulated {token}",
                    "self_report": f"shield exit gate failed: {token}"}
        tasks = assignment.get("test_tasks") if isinstance(assignment, dict) else None
        if not self._valid_test_tasks(tasks):
            return {
                "status": "failed",
                "artifact_ref": None,
                "failure_class": "invalid_test_tasks",
                "audit_evidence": (
                    "assignment.test_tasks must be a non-empty list of "
                    "{ac_id, layers, if_ids}"
                ),
                "self_report": "Shield assignment.test_tasks is missing or malformed",
            }
        pre_snapshot = self._snapshot_tests_identity()
        required = [(task["ac_id"], layer)
                    for task in tasks for layer in task["layers"]]
        if_id_by_ac = {
            task["ac_id"]: (task["if_ids"][0] if task["if_ids"] else None)
            for task in tasks
        }
        tests_dir = self.repo / "tests"
        for subdir in ("integration", "e2e", "assets", "counterexamples"):
            (tests_dir / subdir).mkdir(parents=True, exist_ok=True)
        for ac_id, layer in required:
            self._write_test_file(tests_dir, ac_id, layer,
                                  if_id_by_ac.get(ac_id), token)
        # D-32: WRITE checkpoints require an attributable tests/**/*.py diff.
        # A re-dispatch (e.g. after Prism revise) that rewrites identical bytes
        # would be a done/no-diff no-op and fail the pipeline. The re-write
        # appends a deterministic marker attributed to the review/failure
        # evidence that drove the re-dispatch (FR-11 evidence, not an arbitrary
        # counter) - a faithful stand-in for "Shield revises the tests".
        self._shield_writes += 1
        self._append_revision_marker(tests_dir, assignment)
        manifest = self._shield_artifact_manifest(pre_snapshot)
        return {"status": "done", "artifact_ref": str(tests_dir),
                "self_report": f"wrote {len(required)} test files ({token})",
                "artifact_manifest": manifest}

    @staticmethod
    def _path_identity(path: Path) -> str:
        """Content identity matching ResultCheckpoint's _path_identity:
        sha256(content) for regular files, ``symlink:{target}`` for symlinks,
        ``missing`` for deleted, ``unreadable`` for non-regular/permission-
        denied files."""
        from tracks.executor.file_identity import path_identity  # noqa: PLC0415
        return path_identity(path)

    @staticmethod
    def _is_regular_file_identity(identity: str) -> bool:
        """True iff identity represents a readable regular file (sha256),
        matching ResultCheckpoint's ``_is_regular_file_identity``."""
        from tracks.executor.file_identity import (  # noqa: PLC0415
            is_regular_file_identity,
        )
        return is_regular_file_identity(identity)

    def _snapshot_tests_identity(self) -> dict[str, str]:
        """Content-identity snapshot of all files under ``tests/``.

        Returns ``{repo_relative_path: identity}`` for every file or symlink.
        Mirrors ResultCheckpoint's identity semantics so the artifact_manifest
        exactly matches the files ResultCheckpoint observes as changed.
        """
        tests_dir = self.repo / "tests"
        snapshot: dict[str, str] = {}
        if not tests_dir.exists():
            return snapshot
        for path in sorted(tests_dir.rglob("*")):
            if path.is_symlink() or path.is_file():
                rel = str(path.relative_to(self.repo))
                snapshot[rel] = self._path_identity(path)
        return snapshot

    def _shield_artifact_manifest(self,
                                  pre_snapshot: dict[str, str]) -> dict:
        """Build ``artifact_manifest`` from content-identity changes.

        Returns include entries for every regular non-symlink ``tests/`` file
        whose identity changed (created or content-modified) during this
        dispatch, sorted deterministically. Matches ResultCheckpoint's
        observed identity semantics.
        """
        post_snapshot = self._snapshot_tests_identity()
        changed = sorted(
            path for path, post_id in post_snapshot.items()
            if self._is_regular_file_identity(post_id)
            and pre_snapshot.get(path) != post_id
        )
        return {"include": [{"path": path} for path in changed]}

    def _append_revision_marker(self, tests_dir: Path,
                                assignment: dict | None) -> None:
        """Append a deterministic revision marker to existing test files when
        a re-dispatch carries evidence (FR-11). No-op without evidence.

        The evidence reason is sanitized to a single comment-safe deterministic
        line (whitespace collapsed, trimmed): a raw multi-line reason must not
        inject newlines into the appended marker (SyntaxError/Syntax injection)."""
        evidence = (assignment or {}).get("evidence")
        if not evidence or not tests_dir.exists():
            return
        existing = sorted(tests_dir.rglob("test_*.py"))
        if not existing:
            return
        reason = evidence.get("reason") or evidence.get("check") or "revision"
        reason = " ".join(str(reason).split()).strip() or "revision"
        marker = f"# shield revision: {reason}\n"
        for path in existing:
            path.write_text(path.read_text(encoding="utf-8") + marker,
                            encoding="utf-8")

    @staticmethod
    def _valid_test_tasks(tasks: object) -> bool:
        # D-28: single shared validator (effects boundary) so the executor's
        # pre-dispatch gate and the fake's own guard agree exactly.
        return valid_test_tasks(tasks)

    def _write_test_file(self, tests_dir: Path, ac_id: str, layer: str,
                         if_id: str | None, token: str) -> None:
        subdir = tests_dir / layer
        slug = _ac_slug(ac_id)
        fname = f"test_{slug}.py"
        # R-1 marker: standalone comment line directly above the test def,
        # mandatory TRACKS-TRACE token (FR-0080/FR-0130, revision log R-1).
        if token == "short_marker":
            marker = f"# {ac_id} TRACKS-TRACE short format (no @version)"
        else:
            marker = f"# {ac_id}@{self.version} TRACKS-TRACE {layer} test"
        if token == "illegit_red":
            # Import inside the test body so collection passes but the test
            # fails at runtime with ImportError (illegit Red -> DIAGNOSE).
            body = f"{marker}\ndef test_{slug}():\n"
            body += "    import nonexistent_module  # illegit Red\n"
        elif token == "mixed_red":
            # R1-01 per-section classification: integration layer fails with
            # a legit assertion_failure; e2e layer fails with an illegit
            # ImportError. Overall verdict must be invalid (DIAGNOSE).
            body = f"{marker}\ndef test_{slug}():\n"
            if layer == "integration":
                body += "    assert False  # legit Red (assertion_failure)\n"
            else:
                body += "    import nonexistent_module  # illegit Red\n"
        elif token == "pass_red":
            # Test passes instead of failing (unexpected pass -> DIAGNOSE).
            body = f"{marker}\ndef test_{slug}():\n"
            body += "    pass\n"
        else:
            body = self._default_test_body(slug, if_id, marker)
        (subdir / fname).write_text(body, encoding="utf-8")

    def _default_test_body(self, slug: str, if_id: str | None,
                           marker: str) -> str:
        """Default Shield test body.

        v0.5+ (the M-IMPL flow): a deterministic behavioral contract over the
        Fake Archer production path ``tracks/impl/{slug}.py`` (see
        ``_behavioral_test_body``). Older flow versions (v0.1/v0.4) end at the
        M-TEST boundary — no implementation phase ever turns the test green —
        so they keep the legacy legit M-TEST stub token (stub_token_failure).
        """
        if supports_m_impl(self.version):
            return self._behavioral_test_body(slug, if_id, marker)
        return (
            f"{marker}\ndef test_{slug}():\n"
            '    raise NotImplementedError("IF-MTEST-001")\n'
        )

    @staticmethod
    def _behavioral_test_body(slug: str, if_id: str | None, marker: str) -> str:
        """A deterministic behavioral contract over the Fake Archer production
        path ``tracks/impl/{slug}.py``.

        Reuses the same assertion generator as Fake Devon's RED tests
        (``_devon_assertion_test``), so the Shield contract and the Devon RGR
        contract agree on exactly what turns the test green: the implementation
        file exists and carries ``IMPLEMENTED_IF == <if_id>``. Before
        implementation the file does not exist -> namespace is empty and the
        assertion fails as a legitimate Red (assertion_failure) with collection
        succeeding. Never edits product code.

        With no applicable IF id (should not happen for a valid Shield
        contract), fall back to the legacy M-TEST legit stub token.
        """
        if not if_id:
            return (
                f"{marker}\ndef test_{slug}():\n"
                '    raise NotImplementedError("IF-MTEST-001")\n'
            )
        implementation = DevonPatchMixin._devon_implementation_text(None, if_id)
        body = DevonPatchMixin._devon_assertion_test(
            f"tracks/impl/{slug}.py", if_id, implementation,
        )
        return marker + "\n" + body
