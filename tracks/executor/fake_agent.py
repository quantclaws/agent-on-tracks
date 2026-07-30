"""Deterministic FakeAgent (NFR-01) — first-class executor stand-in, not a mock.

Behavior injection: TRAC_FAKE_SIMULATE, read ONLY at the cli/executor boundary
(interfaces §5 note); project()/decide() never see it. Format: semicolon- or
comma-separated `key=token` entries, key = "role:substate" (or "validator:doc").
A token may be a `|`-separated sequence consumed one per call (last one
sticks), e.g. `sage:SAGE_REVIEW=comment|pass`. Defaults: document work "ok",
reviews "pass". Token "hang" blocks (recovery/lock tests, AC-27a).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from tracks.frontmatter import split_frontmatter

STORY_SECTIONS = ("## 目标", "## 范围", "## 验收标准")

SPEC_TEMPLATE = """# {version} — 功能规格（FakeAgent 草案）

## 能力边界

由 story.md 派生的确定性规格草案。

## 功能需求

| ID | 需求 | Story 来源 |
| :--- | :--- | :--- |
| FR-01 | 覆盖 story 目标章节 | story §目标 |
"""


def _simulate_map() -> dict:
    raw = os.environ.get("TRAC_FAKE_SIMULATE", "")
    out: dict = {}
    for part in raw.replace(",", ";").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class FakeAgent:
    def __init__(self, repo: Path, version: str):
        self.repo = repo
        self.version = version
        self._calls: dict = {}

    def token(self, key_left: str, key_right: str, default: str) -> str:
        key = f"{key_left}:{key_right}"
        seq = _simulate_map().get(key, default).split("|")
        n = self._calls.get(key, 0)
        self._calls[key] = n + 1
        return seq[min(n, len(seq) - 1)].strip()

    def act(self, role: str, substate: str, doc: str | None, doc_path: Path | None) -> dict:
        if substate == "TRIAGE":
            return {"status": "done", "artifact_ref": None,
                    "self_report": "explored raw requirement"}
        if substate in ("DRAFT", "RESPOND"):
            token = self.token(role, substate, "ok")
            if token == "hang":
                time.sleep(600)  # blocked agent: lock-contention path (AC-27a)
            if doc == "story.md":
                self._write_story(doc_path)
            else:
                self._write_spec(doc_path, token)
            return {"status": "done", "artifact_ref": str(doc_path),
                    "self_report": f"wrote {doc} ({token})"}
        verdict = self.token(role, substate, "pass")
        return {"status": "done", "artifact_ref": None,
                "self_report": f"review: {verdict}", "verdict": verdict}

    def _write_story(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        head, body = split_frontmatter(text)
        first = next((line for line in body.splitlines() if line.strip()), "untitled")
        title = first.lstrip("# ").strip()[:60] or "untitled"
        lines = head.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("title:") and not line.split(":", 1)[1].strip():
                lines[i] = f"title: {title}"
        head = "\n".join(lines) + "\n" if lines else ""
        for sec in STORY_SECTIONS:
            if sec not in body:
                body += f"\n{sec}\n\n（FakeAgent 确定性填充）\n"
        path.write_text(head + body, encoding="utf-8")

    def _write_spec(self, path: Path, token: str = "ok") -> None:
        if token == "scope_overflow":
            rows = "\n".join(
                f"| FR-{i:02d} | 需求 {i} | story §目标 |" for i in range(1, 32)
            )
            body = (
                f"# {self.version} — 功能规格（FakeAgent 过量草案）\n\n"
                "## 功能需求\n\n| ID | 需求 | Story 来源 |\n| :--- | :--- | :--- |\n"
                + rows + "\n"
            )
            path.write_text(
                "---\nspec_id: SPEC-001\nstory_ref: S-001\nstatus: draft\nsha:\n---\n\n"
                + body,
                encoding="utf-8",
            )
            return
        if path.exists():
            return
        path.write_text(
            "---\n"
            "spec_id: SPEC-001\n"
            "story_ref: S-001\n"
            "status: draft\n"
            "sha:\n"
            "---\n\n" + SPEC_TEMPLATE.format(version=self.version),
            encoding="utf-8",
        )
