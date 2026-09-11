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

import hashlib
import re
from pathlib import Path

from tracks import paths
from tracks.capabilities import supports_m_impl
from tracks.effects.backend import valid_test_tasks
from tracks.effects.devon_patch import DevonPatchMixin

# FR-0210 exit gate tokens: a failed agent run is not a produced document.
_FAILED_TOKENS = (
    "over_reach",
    "timeout",
    "no_target_diff",
    "non_zero_exit",
    "json_truncated",
    "fail",
)


def _ac_slug(ac_id: str) -> str:
    """Lowercase hyphen-free module/test slug for an AC id.

    ``AC-FR0010-01`` -> ``ac_fr0010_01``. Shared single source of truth for
    the Shield test filename and the Fake M-IMPL task-graph module names
    (``tracks/impl/{ac_slug}.py`` / ``tests/unit/test_{ac_slug}.py``), so the
    Shield contract and the Devon RGR contract always agree on the path.
    """
    return ac_id.lower().replace("-", "_")


def _shield_marker_base_version(version: str) -> str:
    """Cross-version AC marker version for a Shield WRITE (FR-0244-03).

    A hotfix run's version identity is ``{target}-hotfix-{issue}`` (e.g.
    ``v0.5-hotfix-42``); its regression tests bind to the *target* baseline
    version (``v0.5``), never the hotfix run identity. Feature versions return
    themselves unchanged.
    """
    if "-hotfix-" in version:
        return version.split("-hotfix-", 1)[0]
    return version


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
            return {
                "status": "failed",
                "artifact_ref": None,
                "failure_class": "agent_failed" if token == "fail" else token,
                "audit_evidence": f"simulated {token}",
                "self_report": f"shield exit gate failed: {token}",
            }
        tasks = assignment.get("test_tasks") if isinstance(assignment, dict) else None
        if not self._valid_test_tasks(tasks):
            return {
                "status": "failed",
                "artifact_ref": None,
                "failure_class": "invalid_test_tasks",
                "audit_evidence": (
                    "assignment.test_tasks must be a non-empty list of {ac_id, layers, if_ids}"
                ),
                "self_report": "Shield assignment.test_tasks is missing or malformed",
            }
        pre_snapshot = self._snapshot_tests_identity()
        required = [(task["ac_id"], layer) for task in tasks for layer in task["layers"]]
        if_id_by_ac = {
            task["ac_id"]: (task["if_ids"][0] if task["if_ids"] else None) for task in tasks
        }
        tests_dir = self._repo_root() / "tests"
        for subdir in ("integration", "e2e", "assets", "counterexamples"):
            (tests_dir / subdir).mkdir(parents=True, exist_ok=True)
        for ac_id, layer in required:
            self._write_test_file(tests_dir, ac_id, layer, if_id_by_ac.get(ac_id), token)
        # D-32: WRITE checkpoints require an attributable tests/**/*.py diff.
        # A re-dispatch (e.g. after Prism revise) that rewrites identical bytes
        # would be a done/no-diff no-op and fail the pipeline. The re-write
        # appends a deterministic marker attributed to the review/failure
        # evidence that drove the re-dispatch (FR-11 evidence, not an arbitrary
        # counter) - a faithful stand-in for "Shield revises the tests".
        self._shield_writes += 1
        self._append_revision_marker(tests_dir, assignment)
        manifest = self._shield_artifact_manifest(pre_snapshot)
        return {
            "status": "done",
            "artifact_ref": str(tests_dir),
            "self_report": f"wrote {len(required)} test files ({token})",
            "artifact_manifest": manifest,
        }

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
        tests_dir = self._repo_root() / "tests"
        snapshot: dict[str, str] = {}
        if not tests_dir.exists():
            return snapshot
        for path in sorted(tests_dir.rglob("*")):
            if path.is_symlink() or path.is_file():
                rel = str(path.relative_to(self._repo_root()))
                snapshot[rel] = self._path_identity(path)
        return snapshot

    def _shield_artifact_manifest(self, pre_snapshot: dict[str, str]) -> dict:
        """Build ``artifact_manifest`` from content-identity changes.

        Returns include entries for every regular non-symlink ``tests/`` file
        whose identity changed (created or content-modified) during this
        dispatch, sorted deterministically. Matches ResultCheckpoint's
        observed identity semantics.
        """
        post_snapshot = self._snapshot_tests_identity()
        changed = sorted(
            path
            for path, post_id in post_snapshot.items()
            if self._is_regular_file_identity(post_id) and pre_snapshot.get(path) != post_id
        )
        return {"include": [{"path": path} for path in changed]}

    def _append_revision_marker(self, tests_dir: Path, assignment: dict | None) -> None:
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
        # The re-dispatch evidence may be a single mapping (FR-11 record)
        # or a list of failure records (e.g. an injected RED evidence list);
        # the marker takes the FIRST record's reason/check deterministically,
        # never crashing on the shape.
        if isinstance(evidence, list):
            evidence = next(
                (item for item in evidence if isinstance(item, dict)), {}
            )
        reason = evidence.get("reason") or evidence.get("check") or "revision"
        reason = " ".join(str(reason).split()).strip() or "revision"
        marker = f"# shield revision: {reason}\n"
        for path in existing:
            path.write_text(path.read_text(encoding="utf-8") + marker, encoding="utf-8")

    @staticmethod
    def _valid_test_tasks(tasks: object) -> bool:
        # D-28: single shared validator (effects boundary) so the executor's
        # pre-dispatch gate and the fake's own guard agree exactly.
        return valid_test_tasks(tasks)

    def _write_test_file(
        self, tests_dir: Path, ac_id: str, layer: str, if_id: str | None, token: str
    ) -> None:
        subdir = tests_dir / layer
        slug = _ac_slug(ac_id)
        fname = f"test_{slug}.py"
        # R-1 marker: standalone comment line directly above the test def,
        # mandatory TRACKS-TRACE token (FR-0080/FR-0130, revision log R-1).
        if token == "short_marker":
            marker = f"# {ac_id} TRACKS-TRACE short format (no @version)"
        else:
            base = _shield_marker_base_version(self.version)
            marker = f"# {ac_id}@{base} TRACKS-TRACE {layer} test"
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

    def _default_test_body(self, slug: str, if_id: str | None, marker: str) -> str:
        """Default Shield test body.

        v0.5+ (the M-IMPL flow): a deterministic behavioral contract over the
        Fake Archer production path ``tracks/impl/{slug}.py`` (see
        ``_behavioral_test_body``). Older flow versions (v0.1/v0.4) end at the
        M-TEST boundary — no implementation phase ever turns the test green —
        so they keep the legacy legit M-TEST stub token (stub_token_failure).
        """
        if supports_m_impl(self.version):
            return self._behavioral_test_body(slug, if_id, marker)
        return f'{marker}\ndef test_{slug}():\n    raise NotImplementedError("IF-MTEST-001")\n'

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
            return f'{marker}\ndef test_{slug}():\n    raise NotImplementedError("IF-MTEST-001")\n'
        implementation = DevonPatchMixin._devon_implementation_text(None, if_id)
        body = DevonPatchMixin._devon_assertion_test(
            f"tracks/impl/{slug}.py",
            if_id,
            implementation,
        )
        return marker + "\n" + body

    # -- hotfix fake behavior (IF-HOTFIX-009) -------------------------------

    def _act_sage_triage(self, assignment: dict | None) -> dict:
        """Deterministic SAGE_TRIAGE outcome (FR-0240-04).

        ``sage:SAGE_TRIAGE`` tokens (``|``-sequence semantics unchanged):
        the default ``anchor`` resolves the target-version acceptance's first
        AC heading as the cross-version anchor set (``acs`` + per-entry
        ``rationale_refs``); ``no_anchor`` reports the searched versions and
        the deterministic corpus digests; ``bad_anchor`` cites an AC that does
        not exist in the target acceptance so programmatic validation fails and
        Sage is red-dispatched (AC-FR0240-05).
        """
        data = assignment if isinstance(assignment, dict) else {}
        target_version = str(data.get("target_version") or self._hotfix_target_version())
        token = self.token("sage", "SAGE_TRIAGE", "anchor")
        if token == "no_anchor":
            return {
                "status": "done",
                "outcome": "no_anchor",
                "searched_versions": self._sage_searched_versions(target_version),
                "corpus_digests": self._sage_corpus_digests(target_version),
            }
        if token == "bad_anchor":
            return {
                "status": "done",
                "acs": [f"AC-FR9999-99@{target_version}"],
            }
        return self._sage_anchor_outcome(target_version)

    def _hotfix_target_version(self) -> str:
        """The target baseline version of this run: a hotfix run inherits its
        target release's version (``v0.5-hotfix-42`` -> ``v0.5``); any other
        version is its own identity."""
        return _shield_marker_base_version(str(self.version))

    def _target_acceptance(self, target_version: str) -> Path:
        return paths.version_dir(paths.tracks_home(self.repo), target_version) / "acceptance.md"

    @staticmethod
    def _first_anchor_ac(acc_text: str) -> str:
        """First ``### AC-…`` heading in the acceptance, in document order."""
        match = re.search(r"^### (AC-(?:N?FR)\d{4}-\d+)\b", acc_text, re.M)
        return match.group(1) if match else ""

    def _sage_searched_versions(self, target_version: str) -> list[str]:
        """Deterministic corpus search trace: the target version plus the run's
        own identity when it is a hotfix (target + hotfix run both searched)."""
        versions = [target_version]
        own = str(self.version)
        if own != target_version and own not in versions:
            versions.append(own)
        return versions

    def _sage_corpus_digests(self, target_version: str) -> list[dict]:
        """Deterministic corpus manifest: sha256 digest per searched version
        acceptance (missing corpus is recorded as absent, never guessed)."""
        digests = []
        for version in self._sage_searched_versions(target_version):
            acceptance = self._target_acceptance(version)
            if acceptance.is_file():
                digest = hashlib.sha256(acceptance.read_bytes()).hexdigest()
            else:
                digest = "absent"
            digests.append({"version": version, "digest": digest})
        return digests

    def _sage_anchor_outcome(self, target_version: str) -> dict:
        """Anchored outcome: first target acceptance AC as the deterministic
        anchor set, with one grounding ref per entry (AC-FR0240-04)."""
        acceptance = self._target_acceptance(target_version)
        first = self._first_anchor_ac(
            acceptance.read_text(encoding="utf-8") if acceptance.is_file() else ""
        )
        anchored = [f"{first}@{target_version}"]
        rationale_refs = [
            {
                "ac": ref,
                "version": target_version,
                "ref": f"acceptance.md:{first}",
            }
            for ref in anchored
        ]
        return {"status": "done", "acs": anchored, "rationale_refs": rationale_refs}

    def _act_hotfix_design(self, assignment: dict | None, token: str = "ok") -> dict | None:
        """Archer M-DESIGN hotfix dispatch (FR-0243): produce the delta trio
        (architecture/interfaces/test-plan) inside the hotfix run's own project
        directory, carrying the anchored cross-version AC set. Returns ``None``
        for ordinary (non-hotfix) design dispatches.

        Requirement-stage artifacts (story/spec/acceptance) are always
        inherited from the target baseline, never created (FR-0241-02).
        """
        if not isinstance(assignment, dict):
            return None
        anchor_acs = assignment.get("anchor_acs")
        target_version = assignment.get("target_version")
        if not isinstance(anchor_acs, (list, tuple)) or not anchor_acs:
            return None
        if not isinstance(target_version, str) or not target_version.strip():
            return None
        anchored = [
            f"{ac}@{target_version}" if "@" not in ac else ac for ac in anchor_acs
        ]
        vdir = self._design_vdir()
        vdir.mkdir(parents=True, exist_ok=True)
        self._write_hotfix_delta(vdir, anchored, target_version, unit_only=token == "unit_only")
        self._write_reach_entries(vdir)
        return {
            "status": "done",
            "artifact_ref": str(vdir.relative_to(self.repo)),
            "self_report": f"wrote hotfix delta trio to {vdir.relative_to(self.repo)}",
            "anchored_acs": anchored,
        }

    def _write_hotfix_delta(
        self, vdir: Path, anchored: list[str], target_version: str, *, unit_only: bool = False
    ) -> None:
        """Write the delta trio with complete frontmatter and one anchored-AC
        section each; test-plan carries the §8 regression rows."""
        arch = self._append_hotfix_anchor_section(
            self._design_doc("architecture"), anchored, "架构增量"
        )
        closure = "\n".join(
            f"- {ac}: owner=Devon; surface=hotfix implementation; "
            f"composition=delta; wiring=Runtime; test=unit; evidence={ac}; "
            "if_ids=IF-HOTFIX-009"
            for ac in anchored
        )
        arch = arch.rstrip("\n") + "\n\n" + closure + "\n"
        (vdir / "architecture.md").write_text(arch, encoding="utf-8")
        interfaces = self._append_hotfix_anchor_section(
            self._design_doc("interfaces"), anchored, "接口增量"
        )
        (vdir / "interfaces.md").write_text(interfaces, encoding="utf-8")
        plan = self._drop_coverage_section(self._design_doc("test-plan"))
        plan = self._append_hotfix_anchor_section(plan, anchored, "回归增量")
        plan = plan.rstrip("\n") + "\n" + self._hotfix_delta_coverage(
            anchored, unit_only=unit_only
        )
        (vdir / "test-plan.md").write_text(plan, encoding="utf-8")

    def _baseline_ac_ids(self, target_version: str) -> list[str]:
        """IF-HOTFIX-010: read all AC ids from the target baseline
        acceptance.md so the delta test-plan's §8 coverage can attribute a
        unit layer to inherited (non-anchored) ACs too — the file-level
        design-trace validator reads the baseline acceptance (via the
        resolver) and checks every AC has a layer token in the delta plan."""
        from tracks.executor.test_tasks import _known_ac_ids  # noqa: PLC0415

        acc = self._design_vdir().parent / target_version / "acceptance.md"
        if not acc.is_file():
            return []
        return sorted(_known_ac_ids(acc.read_text(encoding="utf-8")))

    @staticmethod
    def _append_hotfix_anchor_section(text: str, anchored: list[str], title: str) -> str:
        """Append one delta section listing the anchored cross-version AC refs."""
        lines = ["", "## Anchor 承接（hotfix delta）", ""]
        for ac in anchored:
            lines.append(f"- **{ac}** — {title}，目标版本既有验收条目")
        lines.append("")
        return text.rstrip("\n") + "\n" + "\n".join(lines) + "\n"

    def _hotfix_delta_coverage(self, anchored: list[str], *, unit_only: bool = False) -> str:
        """§8 regression rows (hotfix delta test-plan): each anchored AC with
        an integration-layer test declaration (cross-version hotfix ACs need
        integration coverage — FR-0244-02/03 — and the file-level test-tasks
        validator requires at least one integration/e2e Shield task).
        Inherited baseline coverage remains in the source release artifacts."""
        layer = "unit" if unit_only else "integration"
        rows = [
            f"| {ac} | {layer} | tests/{layer}/test_{_ac_slug(ac.split('@', 1)[0])}.py | "
            "IF-HOTFIX-009 |"
            for ac in anchored
        ]
        return (
            "\n## 8. AC Coverage\n\n"
            "| AC id | layer | test | IF |\n|---|---|---|---|\n" + "\n".join(rows) + "\n"
        )

    def _devon_attach_hotfix_trailers(self, result: dict, assignment: dict) -> None:
        """Attach the Tracks-Issue trailer evidence to a Devon hotfix outcome
        (FR-0246-03 audit chain): the hotfix run's issue number is the demand-
        tracking identity and must survive into the auditor's evidence."""
        if not isinstance(assignment, dict):
            return
        issue = assignment.get("hotfix_issue")
        if issue is None:
            return
        audit = result.get("audit_evidence")
        if not isinstance(audit, dict):
            audit = {}
            result["audit_evidence"] = audit
        trailers = {"Tracks-Issue": str(issue)}
        audit["trailers"] = trailers
        audit["Tracks-Issue"] = str(issue)
