"""Document templates (ARCH-003 §5a / SPEC-003 FR-140).

Reads canonical templates from ``tracks/templates/`` by kind, replacing the
hardcoded ``STORY_TEMPLATE``. M-START renders a *skeleton* (template + the
verbatim raw requirement in §1 原始输入, other placeholders preserved); the
skeleton is NOT validated at creation — validation happens at agent outcome and
at the review exit gate (FR-150).

v0.11 IF-INTAKE-001 (interfaces §1f.4/§1g.2): the unified intake entry
classifies its input before adoption — ``classify_story_input`` is the pure
machine authority (a complete story is adopted verbatim, a raw seed renders
into the skeleton); ``render_story_capture`` carries the Scribe-suggested
title into the story frontmatter for the develop path.
"""

from __future__ import annotations

import re
from pathlib import Path

from tracks.frontmatter import split_frontmatter

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

KNOWN_KINDS = ("story", "spec", "acceptance", "test-plan", "prd")

_RAW_PLACEHOLDER = re.compile(r"(## 1\. 原始输入\n\n)> \{用户原始输入，逐字记录，不修改或转述\}")

# The story template's fixed section structure (§1f.4: "解析出 story
# frontmatter（story_id/title 等）与固定章节结构即 complete"). Both halves are
# required — frontmatter alone is still a raw seed.
_STORY_SECTIONS = (
    "## 1. 原始输入",
    "## 2. 用户意图",
    "## 3. 核心操作路径",
    "## 4. 行为种子",
    "## 5. 范围、约束与例外",
    "## 6. 开放产品决定",
    "## 7. 必要性与风险",
)
_TITLE_PLACEHOLDER = "{一句话标题}"


def template_path(kind: str) -> Path:
    return TEMPLATE_DIR / f"{kind}.md"


def load_template(kind: str) -> str:
    """Return the raw template text for ``kind``; raise if absent."""
    path = template_path(kind)
    if not path.exists():
        raise FileNotFoundError(f"no template for kind {kind!r}: {path}")
    return path.read_text(encoding="utf-8")


def classify_story_input(content: str) -> str:
    """The §1f.4 machine classification: ``"complete"`` or ``"raw"``.

    A complete story parses into story frontmatter (a ``story_id`` and a
    ``title`` key with non-placeholder values) AND carries the fixed section
    structure; everything else is a raw seed. Pure and total: no filesystem,
    no clock — the same input always classifies the same way, and the intake
    report's ``input_form`` stays a mere suggestion next to it.
    """
    text = str(content or "")
    head, _body = split_frontmatter(text)
    if not head:
        return "raw"
    if not _frontmatter_value(head, "story_id") or not _frontmatter_value(head, "title"):
        return "raw"
    if not all(section in text for section in _STORY_SECTIONS):
        return "raw"
    return "complete"


def _frontmatter_value(head: str, key: str) -> str | None:
    """The stripped ``key:`` value of a frontmatter block; None when the
    key is absent, empty or still the template placeholder."""
    for line in head.splitlines():
        match = re.match(rf"^({re.escape(key)}):\s*(.*)$", line)
        if match is None:
            continue
        value = match.group(2).strip().strip("\"'")
        if not value or value == _TITLE_PLACEHOLDER or value.startswith("{"):
            return None
        return value
    return None


def render_story_skeleton(
    raw: str, created: str, story_id: str = "S-001", *, title: str | None = None
) -> str:
    """Render the story template as an M-START skeleton.

    Fills ``story_id`` / ``created`` and the §1 原始输入 blockquote with the
    verbatim raw requirement; every other placeholder is left for Scribe.
    ``title`` (v0.11 §1g.2, optional) prefills the frontmatter title and the
    H1 heading with the Scribe-suggested title of the develop path; without
    it the placeholder stays for Scribe, exactly as before.
    """
    text = load_template("story")
    text = text.replace("S-NNN", story_id)
    text = text.replace("{YYYY-MM-DD}", created)
    if title:
        text = text.replace(_TITLE_PLACEHOLDER, title)
    return _RAW_PLACEHOLDER.sub(lambda m: m.group(1) + _blockquote(raw), text)


def render_story_capture(
    content: str, created: str, story_id: str = "S-001", *, title: str | None = None
) -> str:
    """The §1g.2 story capture render: raw seeds enter the skeleton verbatim,
    complete stories are adopted as-is (never wrapped into §1, never
    rewritten or reordered)."""
    if classify_story_input(content) == "complete":
        return content
    return render_story_skeleton(content, created, story_id, title=title)


def _blockquote(raw: str) -> str:
    """Render multi-line raw input as a markdown blockquote (> per line)."""
    lines = raw.splitlines() or [""]
    return "\n".join(f"> {line}" if line.strip() else ">" for line in lines)
