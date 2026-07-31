"""Deterministic FakeBackend (NFR-01) — first-class executor stand-in, not a mock.

Moved from tracks/executor/fake_agent.py (v0.1 FakeAgent) into the effects
boundary (ARCH-003 §4); behavior is unchanged. Implements ``AgentBackend``.

Behavior injection: TRAC_FAKE_SIMULATE, read ONLY at the cli/executor boundary
(interfaces §5 note); project()/decide() never see it. Format: semicolon- or
comma-separated `key=token` entries, key = "role:substate" (or "validator:doc").
A token may be a `|`-separated sequence consumed one per call (last one
sticks), e.g. `sage:SAGE_REVIEW=comment|pass`. Defaults: document work "ok",
reviews "pass". Token "hang" blocks (recovery/lock tests, AC-27a).
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from tracks.frontmatter import split_frontmatter

# spec items the fake acceptance draft must cover (FR-0170 trace)
_SPEC_ITEM = re.compile(r"^### (N?FR-\d{4})[ \t]*(.*)$", re.M)

SPEC_TEMPLATE = """# {version} — 功能规格（FakeAgent 草案）

## 功能需求

### FR-0010 覆盖 story 目标

- [x] 已决定 — FakeAgent 确定性草案
- **来源**：story §目标
- **交付入口**：无独立入口，依附 story

由 story.md 派生的确定性规格草案。

## 非功能需求

### NFR-0010 确定性输出

- [x] 已决定
- **来源**：story §目标

FakeAgent 输出确定、可复现。
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


def _story_title(body: str) -> str:
    """Coherent title: the §1 原始输入 request, else the first heading."""
    in_raw = False
    for line in body.splitlines():
        if line.strip() == "## 1. 原始输入":
            in_raw = True
            continue
        if in_raw:
            if line.startswith("#"):  # reached the next section
                break
            if line.startswith(">") and line[1:].strip():
                return line[1:].strip()[:60]
    first = next((ln for ln in body.splitlines() if ln.strip()), "untitled")
    return (first.lstrip("# ").strip() or "untitled")[:60]


class FakeBackend:
    """Deterministic AgentBackend (v0.1 FakeAgent, relocated to effects/)."""

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
            elif doc == "acceptance.md":
                self._write_acceptance(doc_path, token)
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
        title = _story_title(body)
        lines = head.splitlines()
        for i, line in enumerate(lines):
            if not line.startswith("title:"):
                continue
            val = line.split(":", 1)[1].strip()
            if not val or val.startswith("{"):  # empty or unfilled placeholder
                lines[i] = f"title: {title}"
        head = "\n".join(lines) + "\n" if lines else ""
        # The M-START skeleton (templating.render_story_skeleton) already carries
        # every required section, so FakeAgent only fills the title (FR-150 gate).
        path.write_text(head + body, encoding="utf-8")

    def _write_spec(self, path: Path, token: str = "ok") -> None:
        if token == "scope_overflow":
            items = "\n".join(
                f"### FR-{i:04d} 需求 {i}\n\n- [x] 已决定\n- **来源**：story §目标\n"
                "- **交付入口**：无独立入口\n" for i in range(1, 32)
            )
            body = (
                f"# {self.version} — 功能规格（FakeAgent 过量草案）\n\n"
                "## 功能需求\n\n" + items
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
            "created: 1970-01-01\n"
            "story_ref: S-001\n"
            "status: draft\n"
            "sha:\n"
            "---\n\n" + SPEC_TEMPLATE.format(version=self.version),
            encoding="utf-8",
        )

    def _write_acceptance(self, path: Path, token: str = "ok") -> None:
        """Deterministic acceptance draft: full AC coverage derived from the
        sibling spec.md (FR-0170). Token "trace_orphan" leaves the last spec
        item uncovered so the trace gate fails and re-dispatches."""
        spec = path.parent / "spec.md"
        items = _SPEC_ITEM.findall(spec.read_text(encoding="utf-8")) if spec.exists() else []
        if token == "trace_orphan":
            items = items[:-1]
        sections = "\n".join(
            f"## {iid} {title}\n\n"
            f"### AC-{iid.replace('-', '')}-01 外部可观察\n\n"
            f"- [ ] 已确认\n  - 条件：{title or iid} 可在系统外断言\n"
            for iid, title in items
        )
        path.write_text(
            "---\nacc_id: ACC-001\ncreated: 1970-01-01\nstatus: draft\nsha:\n---\n\n"
            f"# {self.version} — 验收标准（FakeAgent 草案）\n\n" + sections,
            encoding="utf-8",
        )
