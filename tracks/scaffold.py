"""Pure parsing helpers for architecture scaffold manifests."""

from __future__ import annotations

import re

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_SCAFFOLD_HEADING = re.compile(r"^##\s+(?:\d+(?:\.\d+)*\.?\s+)?Scaffold 宣言\s*$", re.M)
_SCAFFOLD_BULLET = re.compile(r"^\s*-\s+(\S+)\s+—", re.M)


def _scaffold_declared_paths(arch_text: str) -> set[str]:
    """Return repo-relative paths listed in architecture's scaffold section.

    The parser only reads text. HTML comments are stripped so template guidance
    cannot accidentally declare a path.
    """
    heading = _SCAFFOLD_HEADING.search(arch_text)
    if not heading:
        return set()
    rest = arch_text[heading.end() :]
    next_heading = re.search(r"^##\s", rest, re.M)
    if next_heading:
        rest = rest[: next_heading.start()]
    declared = set()
    for raw in _SCAFFOLD_BULLET.findall(_HTML_COMMENT.sub("", rest)):
        path = raw.strip("`'\"")
        if path:
            declared.add(path)
    return declared
