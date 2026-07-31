"""Requirement baseline digest + preview summary (FR-0190, design D-01).

`revision_digest` is pure content addressing: sha256 over the three doc body
shas joined with fixed labels in fixed order (story -> spec -> acc). Bodies are
frontmatter-stripped (`doc_body_sha`) so sealing a sha never self-invalidates,
and the labelled join removes concatenation-boundary collisions. No timestamps
participate, so staleness is replayable from content alone (D-02/D-03).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from tracks.frontmatter import doc_body_sha, split_frontmatter

_TRIO = (("story", "story.md"), ("spec", "spec.md"), ("acc", "acceptance.md"))
_SPEC_ITEM = re.compile(r"^### (?:FR|NFR)-\d{4}\b", re.M)
_AC_ITEM = re.compile(r"^### AC-N?FR\d{4}-\d+\b", re.M)


def revision_digest(vdir: Path) -> str:
    joined = "\n".join(f"{label}:{doc_body_sha(vdir / doc)}" for label, doc in _TRIO)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _title(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return "untitled"


def baseline_summary(vdir: Path) -> str:
    parts = []
    for _, doc in _TRIO:
        _, body = split_frontmatter((vdir / doc).read_text(encoding="utf-8"))
        entry = f"{doc}: {_title(body)}"
        if doc == "spec.md":
            entry += f" [{len(_SPEC_ITEM.findall(body))} FR/NFR]"
        elif doc == "acceptance.md":
            entry += f" [{len(_AC_ITEM.findall(body))} AC]"
        parts.append(entry)
    return "; ".join(parts)
