"""Markdown + YAML-frontmatter helpers shared by executor and FakeAgent."""

from __future__ import annotations

import hashlib
from pathlib import Path


def split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter block incl. delimiters, body); no frontmatter -> (\"\", text)."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[: end + 5], text[end + 5 :]
    return "", text


def doc_body_sha(path: Path) -> str:
    # sha over the body only, so sealing the sha into frontmatter does not
    # invalidate it (FR-17/FR-23).
    _, body = split_frontmatter(path.read_text(encoding="utf-8"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def set_frontmatter_field(path: Path, field: str, value: str) -> None:
    text = path.read_text(encoding="utf-8")
    head, body = split_frontmatter(text)
    if not head:
        raise ValueError(f"{path} has no frontmatter")
    lines = head.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f"{field}:"):
            lines[i] = f"{field}: {value}"
            break
    else:
        lines.insert(-1, f"{field}: {value}")
    path.write_text("\n".join(lines) + "\n" + body, encoding="utf-8")
