"""Document format validation (FR-11/FR-19/FR-20, FR-150).

v0.1 checks (D-16): file exists + frontmatter parseable ("schema"), and for
spec.md the FR-count scope gate (> 30 rows -> "scope_overflow", FR-20).

v0.2 adds the ``template`` check (FR-150): a document's required frontmatter
fields and level-2 sections must match its kind template (tracks/templates/).
``check_template`` is pure (reads only the file + its template) and returns a
list of ``line:N`` non-conformance messages (empty = valid); it will back
``trac validate`` and the outcome / exit-gate validation. Pure w.r.t. state.
"""
from __future__ import annotations

import re
from pathlib import Path

from tracks import templating
from tracks.frontmatter import split_frontmatter

FR_LIMIT = 30
_FR_ROW = re.compile(r"^\|\s*FR-\d+", re.MULTILINE)

_KIND_BY_FILE = {
    "story.md": "story",
    "spec.md": "spec",
    "acceptance.md": "acceptance",
    "test-plan.md": "test-plan",
    "prd.md": "prd",
}
_HEADING = re.compile(r"^(#+)\s+(.*\S)\s*$")
_NUM_PREFIX = re.compile(r"^\d+(?:\.\d+)*\.?\s+")


def validate_document(path: Path, doc: str) -> tuple[str, str] | None:
    """Return (check, reason) on failure, None when the document is valid."""
    if not path.exists():
        return ("schema", "missing file")
    head, body = split_frontmatter(path.read_text(encoding="utf-8"))
    if not head:
        return ("schema", "no frontmatter")
    if doc == "spec.md":
        n = len(_FR_ROW.findall(body))
        if n > FR_LIMIT:
            return ("scope_overflow", f"{n} FRs > {FR_LIMIT}")
    return None


def _norm_heading(line: str) -> str | None:
    """Normalized level-2 heading text (number prefix stripped), or None."""
    m = _HEADING.match(line)
    if not m or len(m.group(1)) != 2:  # only '##' (level 2)
        return None
    return _NUM_PREFIX.sub("", m.group(2)).strip()


def _fm_fields(head: str) -> set:
    out = set()
    for line in head.splitlines():
        if ":" in line and not line.startswith("---"):
            out.add(line.split(":", 1)[0].strip())
    return out


def check_template(path: Path) -> list:
    """FR-150 'template' check.

    Required frontmatter fields and level-2 sections must match the kind
    template. Returns ``line:N ...`` non-conformance messages; ``[]`` = valid.
    """
    kind = _KIND_BY_FILE.get(path.name)
    if kind is None:
        return [f"line:1 no template mapping for {path.name!r}"]
    try:
        tpl_head, tpl_body = split_frontmatter(templating.load_template(kind))
    except FileNotFoundError:
        return [f"line:1 no template for kind {kind!r}"]
    if not path.exists():
        return ["line:1 missing file"]
    head, body = split_frontmatter(path.read_text(encoding="utf-8"))
    issues = [
        f"line:1 missing frontmatter field '{f}'"
        for f in sorted(_fm_fields(tpl_head) - _fm_fields(head))
    ]
    tpl_secs = {s for s in (_norm_heading(ln) for ln in tpl_body.splitlines()) if s}
    doc_secs = {s for s in (_norm_heading(ln) for ln in body.splitlines()) if s}
    issues += [f"line:1 missing section '{s}'" for s in sorted(tpl_secs - doc_secs)]
    return issues
