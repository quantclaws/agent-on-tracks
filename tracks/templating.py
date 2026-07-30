"""Document templates (ARCH-003 §5a / SPEC-003 FR-140).

Reads canonical templates from ``tracks/templates/`` by kind, replacing the
hardcoded ``STORY_TEMPLATE``. M-START renders a *skeleton* (template + the
verbatim raw requirement in §1 原始输入, other placeholders preserved); the
skeleton is NOT validated at creation — validation happens at agent outcome and
at the review exit gate (FR-150).
"""
from __future__ import annotations

import re
from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

KNOWN_KINDS = ("story", "spec", "acceptance", "test-plan", "prd")

_RAW_PLACEHOLDER = re.compile(
    r"(## 1\. 原始输入\n\n)> \{用户原始输入，逐字记录，不修改或转述\}")


def template_path(kind: str) -> Path:
    return TEMPLATE_DIR / f"{kind}.md"


def load_template(kind: str) -> str:
    """Return the raw template text for ``kind``; raise if absent."""
    path = template_path(kind)
    if not path.exists():
        raise FileNotFoundError(f"no template for kind {kind!r}: {path}")
    return path.read_text(encoding="utf-8")


def render_story_skeleton(raw: str, created: str, story_id: str = "S-001") -> str:
    """Render the story template as an M-START skeleton.

    Fills ``story_id`` / ``created`` and the §1 原始输入 blockquote with the
    verbatim raw requirement; every other placeholder is left for Scribe.
    """
    text = load_template("story")
    text = text.replace("S-NNN", story_id)
    text = text.replace("{YYYY-MM-DD}", created)
    return _RAW_PLACEHOLDER.sub(lambda m: m.group(1) + _blockquote(raw), text)


def _blockquote(raw: str) -> str:
    """Render multi-line raw input as a markdown blockquote (> per line)."""
    lines = raw.splitlines() or [""]
    return "\n".join(f"> {line}" if line.strip() else ">" for line in lines)
