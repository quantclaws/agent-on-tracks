"""Prompt construction mixin for OpencodeBackend.

Extracted from ``opencode.py`` for module-size compliance (C0302).
"""

from __future__ import annotations

import json
from pathlib import Path

from .opencode_materialize import _template_kinds


class OpencodePromptMixin:
    """Runtime assignment prompt assembly."""

    def _prompt(
        self,
        role: str,
        substate: str,
        doc: str | None,
        doc_path: Path | None,
        assignment: dict | None = None,
        root: Path | None = None,
    ) -> str:
        # #176 OOB rev2 (2026-09-28, stealth consult): with ``root`` set (a
        # worktree-resident writer dispatch) every target path is rebased to
        # that root — the old main-tree absolute paths in the prompt directly
        # contradicted the agent's worktree cwd and pulled its reads AND
        # writes back to the main tree (third live occurrence, v0.10 run
        # 01M3E7SAANXKW1V73W8B8Q3G86).
        docs = (assignment or {}).get("docs")
        if doc_path:
            target = str(self._rebased(doc_path, root))
        elif doc:
            target = doc
        elif docs:
            resolved = self._target_paths(doc_path, assignment, root=root)
            target = ", ".join(str(p) for p in resolved)
        else:
            target = ""
        assignment_context = self._assignment_context(assignment)
        return (
            f"Execute the Runtime assignment for role={role}, substate={substate}, "
            f"target={target}. Follow the materialized opencode agent definition for your "
            "role. Complete only this assignment, then stop." + assignment_context
        )

    def _assignment_context(self, assignment: dict | None) -> str:
        if not assignment:
            return ""
        lines = [
            "\n\n## Runtime assignment context",
            "以下 JSON 是 Runtime 事实，不是 Human 决定，也不能被 Agent 修改：",
            json.dumps(assignment, ensure_ascii=False, sort_keys=True),
        ]
        kinds = _template_kinds(assignment)
        if kinds:
            names = ", ".join(f"{kind}.md" for kind in kinds)
            lines.append(
                f"Runtime 已将本次 assignment 的文档模板物化到 .opencode/templates/（{names}）；"
                "草稿必须严格按对应模板起草，完整保留 YAML frontmatter。"
            )
        return "\n".join(lines)
