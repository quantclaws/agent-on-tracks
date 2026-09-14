"""Materialize/cleanup + prepared-parity mixin for OpencodeBackend (ARCH §4c).

Extracted from ``opencode.py`` for module-size compliance (C0302).
"""

from __future__ import annotations

from pathlib import Path

from tracks import templating
from tracks.effects.dispatch_parity import (
    PreparedContext,
    agent_face,
    bind_artifact,
    check_prepared_parity,
    is_declared,
    parity_evidence,
    parity_failure_result,
    readback_mismatches,
    skill_face,
    template_face,
)
from tracks.effects.envelope_reply import is_declared_assignment

from .opencode_core import OpencodeError


def _skill_names(assignment: dict | None) -> list[str]:
    """Skills this dispatch materializes: an explicit ``skills`` list (batch B:
    the M-DESIGN author assignment carries several) wins over the legacy single
    ``skill`` string; falsy entries are skipped, [] means no skill."""
    assignment = assignment or {}
    if assignment.get("skills") is not None:
        return [name for name in assignment["skills"] if name]
    skill = assignment.get("skill")
    return [skill] if skill else []

def _template_kinds(assignment: dict | None) -> list[str]:
    """Document template kinds this dispatch materializes: an explicit
    ``templates`` list (M-DESIGN trio) wins over the single ``template_kind``
    (single-doc stages); None entries are skipped, None means no template."""
    assignment = assignment or {}
    if assignment.get("templates") is not None:
        return [kind for kind in assignment["templates"] if kind]
    kind = assignment.get("template_kind")
    return [kind] if kind else []


class OpencodeMaterializeMixin:
    """Canonical prompt/skill/template materialization + IF-ENVELOPE-002 parity."""

    def _cleanup_materialized(self, materialized: dict | None) -> None:
        if materialized is not None:
            self._cleanup(materialized)

    def _prepare_parity(
        self, cleanup_infos: list, assignment: dict | None, prompt: str, name: str | None = None
    ) -> PreparedContext:
        """(E) IF-ENVELOPE-002 preparation/check seam, run after ALL
        materialization writes and immediately before the agent spawn.

        Builds the artifact manifest bound to the actual written bytes
        (path + sha256 + frontmatter token), read-backs every prepared path
        from disk (a raced overwrite between preparation and execution is a
        dispatch failure on its own evidence), and asks the kernel parity
        gate to compare every selected face — the four static faces plus
        every selected artifact face (agent definition + every named
        skill/template, ALL of them, not just the first) — against the
        Runtime validator. A selection whose materialization produced no
        entry (absent canonical file) is a genuinely absent REQUIRED face —
        no file/version evidence is invented for it. A rejection returns a
        failed result carrying the manifest and mismatches for audit
        (``parity_rejected``); the caller returns it without spawning and
        the existing cleanup finally still runs. Undeclared dispatches skip
        the gate entirely (today's semantics).
        """
        if not is_declared_assignment(assignment):
            return PreparedContext(ok=True)
        entries = [
            info["manifest"]
            for info in cleanup_infos
            if info is not None and info.get("manifest")
        ]
        expected = ([agent_face(name)] if name else []) + [
            skill_face(skill_name)
            for skill_name in _skill_names(assignment)
        ] + [template_face(kind) for kind in _template_kinds(assignment)]
        verdict = check_prepared_parity(
            assignment,
            entries,
            expected,
            prompt=prompt,
            readback=readback_mismatches(entries),
        )
        if verdict["consistent"]:
            return PreparedContext(ok=True, evidence=parity_evidence(verdict))
        return PreparedContext(
            ok=False,
            failure=parity_failure_result(assignment, verdict["mismatches"], evidence=verdict),
        )

    def _materialize(self, name: str, root: Path | None = None) -> dict:
        """Copy canonical prompt to opencode discovery path; back up any existing
        file so Human's agent is never silently clobbered (restored on cleanup).

        ``root`` is the dispatch's effective root (the isolated worktree for
        writer dispatches): opencode discovers .opencode/agents relative to
        its cwd, so a worktree session without its own copy would run with
        NO agent definition at all -- the envelope reply contract lives in
        the materialized file (live 01M19FJ T-042: worktree dispatches
        replied bare JSON, no_envelope_block, three attempts in a row)."""
        src = self._canonical / f"{name}.md"
        if not src.exists():
            raise OpencodeError("opencode_missing", f"canonical prompt not found: {src}")
        dest_dir = (root or self.repo) / ".opencode" / "agents"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{name}.md"
        info = {
            "dest": dest,
            "backup": None,
            "existed": dest.exists(),
            "face": agent_face(name),
        }
        if dest.exists():
            info["backup"] = dest.read_bytes()
        data = src.read_bytes()
        dest.write_bytes(data)
        # IF-ENVELOPE-002 prepare binding: hash+token from the bytes actually
        # written (never re-scanned from the canonical source).
        info["manifest"] = bind_artifact(info["face"], dest, data)
        return info

    def _materialize_skills(
        self, assignment: dict | None, cleanup_infos: list, root: Path | None = None
    ) -> None:
        """Materialize every skill of the assignment (batch B: ``skills`` list,
        backward compatible with the single ``skill`` string); each materialized
        skill registers for cleanup the moment it is written (ARCH §4c)."""
        for skill_name in _skill_names(assignment):
            cleanup_infos.append(self._materialize_skill(skill_name, root=root))

    def _materialize_skill(self, skill_name: str, root: Path | None = None) -> dict | None:
        """Materialize one skill to opencode's discovery path for progressive
        disclosure."""
        src = self._canonical.parent / "skills" / skill_name / "SKILL.md"
        if not src.exists():
            return None
        dest_dir = (root or self.repo) / ".opencode" / "skills" / skill_name
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "SKILL.md"
        info = {
            "dest": dest,
            "backup": None,
            "existed": dest.exists(),
            "face": skill_face(skill_name),
        }
        if dest.exists():
            info["backup"] = dest.read_bytes()
        data = src.read_bytes()
        dest.write_bytes(data)
        info["manifest"] = bind_artifact(info["face"], dest, data)
        return info

    def _materialize_templates(
        self, assignment: dict | None, cleanup_infos: list, root: Path | None = None
    ) -> None:
        """Materialize the assignment's canonical document templates into the
        host repo (live run043: a host repo has no tracks/templates/, so the
        templates travel with the dispatch exactly like the agent definition
        and skill; backup/restore on cleanup, ARCH §4c). Each file registers
        for cleanup the moment it is written, so a missing canonical kind
        mid-way still cleans up its predecessors."""
        declared = is_declared(assignment)
        for kind in _template_kinds(assignment):
            info = self._materialize_template(kind, declared=declared, root=root)
            if info is not None:
                cleanup_infos.append(info)

    def _materialize_template(
        self, kind: str, declared: bool = False, root: Path | None = None
    ) -> dict | None:
        """Copy canonical tracks/templates/{kind}.md to .opencode/templates/;
        a requested kind without a canonical template is a dispatch failure
        (parity with a missing canonical prompt), never a silent skip.

        On a DECLARED dispatch a missing canonical template is a genuinely
        absent REQUIRED parity face: materialize nothing and let the
        Runtime-owned gate reject it as ``missing`` (no invented file/version
        evidence); undeclared dispatches keep today's infra classification."""
        src = templating.template_path(kind)
        if not src.exists():
            if declared:
                return None
            raise OpencodeError("opencode_missing", f"canonical template not found: {src}")
        dest_dir = (root or self.repo) / ".opencode" / "templates"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{kind}.md"
        info = {
            "dest": dest,
            "backup": None,
            "existed": dest.exists(),
            "face": template_face(kind),
        }
        if dest.exists():
            info["backup"] = dest.read_bytes()
        data = src.read_bytes()
        dest.write_bytes(data)
        info["manifest"] = bind_artifact(info["face"], dest, data)
        return info

    def _cleanup(self, info: dict) -> None:
        dest = info["dest"]
        try:
            if info["existed"] and info["backup"] is not None:
                dest.write_bytes(info["backup"])  # restore Human's agent
            elif not info["existed"] and dest.exists():
                dest.unlink()  # remove what we created
        except OSError:
            pass  # terminal cleanup is best-effort
