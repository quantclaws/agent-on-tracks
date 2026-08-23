"""Deterministic, in-memory patch construction for Fake Devon."""

from __future__ import annotations

import hashlib
import json
import re
from difflib import unified_diff


def append_text(existing: str | None, addition: str) -> str:
    """Append one deterministic text block without touching the filesystem."""
    if existing is None or not existing:
        return addition
    separator = "\n" if existing.endswith("\n") else "\n\n"
    return existing + separator + addition


def unified_patch(path: str, existing: str | None, updated: str) -> str:
    """Return a stable git-apply-able patch for one regular text file."""
    old_lines = [] if existing is None else existing.splitlines(keepends=True)
    new_lines = updated.splitlines(keepends=True)
    source = "/dev/null" if existing is None else f"a/{path}"
    header = f"diff --git a/{path} b/{path}\n"
    if existing is None:
        header += "new file mode 100644\n"
    body = "".join(
        unified_diff(
            old_lines,
            new_lines,
            fromfile=source,
            tofile=f"b/{path}",
            lineterm="\n",
        )
    )
    return header + body


class DevonPatchMixin:
    """Pure path, patch, and evidence helpers shared by Fake Devon phases."""

    def _devon_existing_text(self, relative: str) -> tuple[str | None, str | None]:
        """Read an existing regular file, without following a symlink."""
        path = self.repo / relative
        if not path.exists():
            return None, None
        if path.is_symlink() or not path.is_file():
            return None, f"target path is not a regular file: {relative}"
        try:
            return path.read_text(encoding="utf-8"), None
        except (OSError, UnicodeError) as exc:
            return None, f"cannot read target path {relative}: {exc}"

    def _devon_test_path(self, assignment: dict) -> tuple[str | None, str | None]:
        manifest = assignment["manifest"]
        allowed = manifest["allowed_paths"]
        forbidden = manifest["forbidden_paths"]
        candidates = []
        for raw in allowed:
            path = raw.strip().replace("\\", "/").rstrip("/")
            if path.startswith("tests/unit/") and "*" not in path:
                candidates.append(path)
        for raw in assignment["test_refs"]:
            path = raw.split("::", 1)[0].strip().replace("\\", "/")
            if path.startswith("tests/unit/"):
                candidates.append(path)
        candidates.append(f"tests/unit/test_{self._devon_slug(assignment)}.py")
        selected = self._devon_select_path(candidates, allowed, forbidden, "tests/unit/")
        if selected is None:
            return None, "manifest has no allowed tests/unit path"
        return selected, None

    def _devon_production_path(self, assignment: dict) -> tuple[str | None, str | None]:
        manifest = assignment["manifest"]
        allowed = manifest["allowed_paths"]
        forbidden = manifest["forbidden_paths"]
        slug = self._devon_slug(assignment)
        candidates = []
        for raw in allowed:
            path = raw.strip().replace("\\", "/").rstrip("/")
            if path.startswith("tests/") or path == "tests":
                continue
            if "*" not in path:
                basename = path.rsplit("/", 1)[-1]
                candidate = f"{path}/{slug}.py" if "." not in basename else path
                candidates.append(candidate)
            elif path.endswith("/**"):
                candidates.append(f"{path[:-3].rstrip('/')}/{slug}.py")
        selected = self._devon_select_path(candidates, allowed, forbidden, "")
        if selected is None:
            return None, "manifest has no allowed production path"
        return selected, None

    def _devon_production_reference(self, assignment: dict) -> str:
        production, _ = self._devon_production_path(assignment)
        return production or f"tracks/impl/{self._devon_slug(assignment)}.py"

    @staticmethod
    def _devon_select_path(
        candidates: list[str], allowed: object, forbidden: object, prefix: str
    ) -> str | None:
        seen: set[str] = set()
        for candidate in candidates:
            path = candidate.rstrip("/")
            if (
                not path
                or path in seen
                or path.startswith("/")
                or ".." in path.split("/")
                or (prefix and not path.startswith(prefix))
                or path.startswith("tests/")
                and not prefix
            ):
                continue
            seen.add(path)
            if any(
                DevonPatchMixin._devon_manifest_matches(path, rule) for rule in allowed
            ) and not any(
                DevonPatchMixin._devon_manifest_matches(path, rule) for rule in forbidden
            ):
                return path
        return None

    @staticmethod
    def _devon_manifest_matches(path: str, rule: str) -> bool:
        rule = rule.strip().replace("\\", "/").rstrip("/")
        if rule.endswith("/**"):
            root = rule[:-3].rstrip("/")
            return path == root or path.startswith(root + "/")
        return path == rule or path.startswith(rule + "/")

    @staticmethod
    def _devon_slug(assignment: dict) -> str:
        raw = str(assignment["task_id"]).lower()
        slug = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
        return slug or "task"

    @staticmethod
    def _devon_implementation_text(existing: str | None, if_id: str) -> str:
        body = f'"""Deterministic implementation for {if_id}."""\n'
        body += f'IMPLEMENTED_IF = "{if_id}"\n'
        return append_text(existing, body) if existing else body

    @staticmethod
    def _devon_stub_test(if_id: str) -> str:
        return f'def test_devon_red_stub():\n    raise NotImplementedError("{if_id}")\n'

    @staticmethod
    def _devon_assertion_test(production_path: str, if_id: str, implementation: str) -> str:
        path_literal = json.dumps(production_path)
        return (
            "import runpy\n"
            "from pathlib import Path\n\n"
            "\n"
            "def test_devon_behavioral_contract():\n"
            f"    implementation = Path({path_literal})\n"
            "    namespace = (\n"
            "        runpy.run_path(str(implementation))\n"
            "        if implementation.is_file() else {}\n"
            "    )\n"
            f'    assert namespace.get("IMPLEMENTED_IF") == {if_id!r}\n'
        )

    @staticmethod
    def _devon_test_target(assignment: dict, target: str) -> str:
        if target.startswith("tests/unit/"):
            return target
        return assignment["test_refs"][0].strip()

    @staticmethod
    def _devon_commands(assignment: dict, target: str, outcome: str, summary: str) -> list[dict]:
        test_target = DevonPatchMixin._devon_test_target(assignment, target)
        command = f".venv/bin/python -m pytest -n 4 {test_target}"
        return [{"cmd": command, "result": outcome, "output_summary": summary}]

    @staticmethod
    def _devon_digest(value: object) -> str:
        raw = json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _devon_evidence(
        self,
        assignment: dict,
        phase: str,
        changed_paths: list[str],
        commands: list[dict],
        results: list[dict],
        patch_marker: str,
        implemented_if_ids: list[str],
        no_change_reason: str | None = None,
    ) -> dict:
        pre_identity, post_identity = self._devon_evidence_identities(
            assignment,
            phase,
            patch_marker,
        )
        evidence = {
            "phase": phase,
            "changed_paths": changed_paths,
            "commands": commands,
            "results": results,
            "manifest_compliance": True,
            "pre_identity": pre_identity,
            "post_identity": post_identity,
            "implemented_if_ids": implemented_if_ids,
            "result_identity": assignment["result_identity"],
        }
        audit_evidence = {
            "phase": phase,
            "changed_paths": list(changed_paths),
            "commands": self._devon_command_summaries(commands),
            "results": self._devon_result_summaries(results),
            "manifest_compliance": True,
            "pre_identity": pre_identity,
            "post_identity": post_identity,
            "implemented_if_ids": list(implemented_if_ids),
            "result_identity": assignment["result_identity"],
        }
        if phase in ("green", "refactor"):
            r_identity = assignment["r_tree_identity"]
            audit_evidence["r_identity"] = r_identity
        if no_change_reason is not None:
            audit_evidence["no_change_reason"] = no_change_reason
        return {
            **evidence,
            "audit_evidence": audit_evidence,
            **(
                {"r_identity": assignment["r_tree_identity"]}
                if phase in ("green", "refactor")
                else {}
            ),
            **({"no_change_reason": no_change_reason} if no_change_reason is not None else {}),
        }

    def _devon_evidence_identities(
        self,
        assignment: dict,
        phase: str,
        patch_marker: str,
    ) -> tuple[str, str]:
        """Pre/post content identities for the Devon evidence envelope."""
        post_material = {
            "assignment_identity": self._devon_digest(assignment),
            "phase": phase,
            "patch_or_no_change": patch_marker,
        }
        pre = self._devon_digest(assignment["pre_dirty_snapshot"])
        post = self._devon_digest(post_material)
        return pre, post

    @staticmethod
    def _devon_command_summaries(commands: list[dict]) -> list[dict]:
        return [
            {
                "cmd": command.get("cmd"),
                "result": command.get("result"),
                "output_summary": command.get("output_summary"),
            }
            for command in commands
        ]

    @staticmethod
    def _devon_result_summaries(results: list[dict]) -> list[dict]:
        return [
            {
                "status": result.get("status"),
                "classification": result.get("classification"),
                "output_summary": result.get("output_summary"),
            }
            for result in results
        ]
