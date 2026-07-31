"""Document format validation (FR-11/FR-19/FR-20, FR-150).

v0.1 checks (D-16): file exists + frontmatter parseable ("schema"), and for
spec.md the FR-count scope gate (> 30 valid FRs -> "scope_overflow", FR-20).

v0.2 adds the ``template`` check (FR-150): a document's required frontmatter
fields and level-2 sections must match its kind template (tracks/templates/).
``check_template`` is pure (reads only the file + its template) and returns a
list of ``line:N`` non-conformance messages (empty = valid); it will back
``trac validate`` and the outcome / exit-gate validation. Pure w.r.t. state.

Spec item grammar (kept in sync with templates/spec.md; the template itself
carries no format prose — this module IS the format contract): every item is
``### FR-XXXX 标题`` / ``### NFR-XXXX 标题`` (uppercase, 4-digit zero-padded,
unique, permanent ID), followed by field lines 状态 / 来源 (+ 交付入口 for valid FRs).
Items marked 有效 ❌ keep their ID but do not count toward FR_LIMIT.
"""
from __future__ import annotations

import re
from pathlib import Path

from tracks import templating
from tracks.frontmatter import split_frontmatter

FR_LIMIT = 30

# spec item grammar: loose head (to find/flag malformed items) + strict form.
_ITEM_HEAD = re.compile(r"^###\s+((?:N?FR)-\d+)\b(.*)$", re.IGNORECASE)
_ITEM_OK = re.compile(r"^### (?:FR|NFR)-\d{4} \S")
_STATUS = re.compile(
    r"^- \*\*状态\*\*：有效 (✅|❌) · 可测 (✅|⚠️[^·]*) · 已决定 (✅|⚠️|❌)(?: .*)?$")

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
        n = _valid_fr_count(body)
        if n > FR_LIMIT:
            return ("scope_overflow", f"{n} FRs > {FR_LIMIT}")
    return None


def _spec_items(text: str) -> list:
    """``(line_no, heading, block_lines)`` per FR/NFR item; fenced code skipped,
    any heading (level <= 3) that is not itself an item closes the open block."""
    items: list = []
    cur = None
    fence = False
    for i, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        if _ITEM_HEAD.match(line):
            cur = (i, line, [])
            items.append(cur)
        elif (m := _HEADING.match(line)) and len(m.group(1)) <= 3:
            cur = None
        elif cur:
            cur[2].append(line)
    return items


def _status_match(block: list):
    return next(filter(None, map(_STATUS.match, block)), None)


def _valid_fr_count(text: str) -> int:
    """FR-20 scope count: FR items not marked 有效 ❌ (NFRs never count)."""
    n = 0
    for _, heading, block in _spec_items(text):
        if not _ITEM_HEAD.match(heading).group(1).upper().startswith("FR-"):
            continue
        m = _status_match(block)
        if m is None or m.group(1) == "✅":  # no status line -> count (fail closed)
            n += 1
    return n


def check_spec_items(text: str) -> list:
    """FR-150 spec item lint — the machine-enforced half of the spec format.

    Checks per item: strict heading form, unique ID, 状态 field line,
    来源 field, and 交付入口 for valid FRs. Returns ``line:N`` messages.
    """
    issues: list = []
    seen: dict = {}
    for line_no, heading, block in _spec_items(text):
        item_id = _ITEM_HEAD.match(heading).group(1).upper()
        if not _ITEM_OK.match(heading):
            issues.append(f"line:{line_no} bad item heading {heading!r}"
                          " (expect '### FR-XXXX 标题', uppercase, 4-digit)")
        if item_id in seen:
            issues.append(f"line:{line_no} duplicate id {item_id}"
                          f" (first at line:{seen[item_id]})")
        seen.setdefault(item_id, line_no)
        status = _status_match(block)
        if status is None:
            issues.append(f"line:{line_no} {item_id} missing status line"
                          " '- **状态**：有效 … · 可测 … · 已决定 …'")
        if not any(ln.startswith("- **来源**：") for ln in block):
            issues.append(f"line:{line_no} {item_id} missing '- **来源**：' field")
        valid = status is None or status.group(1) == "✅"
        if (item_id.startswith("FR-") and valid
                and not any(ln.startswith("- **交付入口**：") for ln in block)):
            issues.append(f"line:{line_no} {item_id} missing '- **交付入口**：' field")
    return issues


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
    template; spec docs additionally pass the item lint (``check_spec_items``).
    Returns ``line:N ...`` non-conformance messages; ``[]`` = valid.
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
    text = path.read_text(encoding="utf-8")
    head, body = split_frontmatter(text)
    issues = [
        f"line:1 missing frontmatter field '{f}'"
        for f in sorted(_fm_fields(tpl_head) - _fm_fields(head))
    ]
    tpl_secs = {s for s in (_norm_heading(ln) for ln in tpl_body.splitlines()) if s}
    doc_secs = {s for s in (_norm_heading(ln) for ln in body.splitlines()) if s}
    issues += [f"line:1 missing section '{s}'" for s in sorted(tpl_secs - doc_secs)]
    if kind == "spec":
        issues += check_spec_items(text)  # true file line numbers (full text scan)
    return issues
