"""Document format validation (FR-11/FR-19/FR-20, FR-150).

v0.1 checks (D-16): file exists + frontmatter parseable ("schema"), and for
spec.md the FR-count scope gate (> 30 valid FRs -> "scope_overflow", FR-20).

v0.2 adds the ``template`` check (FR-150): a document's required frontmatter
fields and level-2 sections must match its kind template (tracks/templates/).
``check_template`` is pure (reads only the file + its template) and returns a
list of ``line:N`` non-conformance messages (empty = valid); it will back
``trac validate`` and the outcome / exit-gate validation. Pure w.r.t. state.
``check_trace`` (FR-0170) is the AC<->FR bidirectional coverage check; it runs
always-on for acceptance.md (not via the checks list, like scope_overflow).

Spec item grammar (kept in sync with templates/spec.md; the template itself
carries no format prose — this module IS the format contract): every item is
``### FR-XXXX 标题`` / ``### NFR-XXXX 标题`` (uppercase, 4-digit zero-padded,
unique ID; obsolete items are deleted, IDs never reused), followed by exactly
one ``- [ ] 已决定`` / ``- [x] 已决定`` checkbox line, a ``- **来源**：`` field,
and (for FRs) a ``- **交付入口**：`` field. Draft validation requires every
checkbox unchecked (Agent cannot self-approve); after Lex + Human review the
Runtime atomically checks every box and final validation requires ``[x]``
(only YES means YES). All present FR items count toward FR_LIMIT. Template
HTML comments are ignored; acceptance level-2 sections vary per FR/NFR so are
not name-checked.
"""
from __future__ import annotations

import re
from pathlib import Path

from tracks import templating
from tracks.discuss.gate import check_ready
from tracks.frontmatter import split_frontmatter

FR_LIMIT = 30

# spec item grammar: loose head (to find/flag malformed items) + strict form.
_ITEM_HEAD = re.compile(r"^###\s+((?:N?FR)-\d+)\b(.*)$", re.IGNORECASE)
_ITEM_OK = re.compile(r"^### (?:FR|NFR)-\d{4} \S")
# Structure accepts either state. Stage-specific checks enforce that Agent
# drafts are all unchecked and Runtime-finalized specs are all checked.
_STATUS = re.compile(r"^- \[( |[xX])\] 已决定\b")
_UNDECIDED_STATUS = re.compile(r"^- \[ \] 已决定\b")
_DECIDED_STATUS = re.compile(r"^- \[[xX]\] 已决定\b")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)

# FR-0170 acceptance grammar: '## FR-XXXX' coverage section + '### AC-FRXXXX-YY'
# item whose embedded ID back-references the spec FR/NFR.
_ACC_SECTION = re.compile(r"^## (N?FR-\d{4})\b")
_ACC_AC = re.compile(r"^### (AC-(N?FR)(\d{4})-\d+)\b")

_KIND_BY_FILE = {
    "story.md": "story",
    "spec.md": "spec",
    "acceptance.md": "acceptance",
    "test-plan.md": "test-plan",
    "prd.md": "prd",
}
_HEADING = re.compile(r"^(#+)\s+(.*\S)\s*$")
_NUM_PREFIX = re.compile(r"^\d+(?:\.\d+)*\.?\s+")


def validate_document(path: Path, doc: str, checks=None) -> tuple[str, str] | None:
    """Return (check, reason) on the first failure, None when valid.

    schema (+ spec scope_overflow) always run (v0.1 D-16 + FR-20); ``checks``
    adds the v0.2 gates: 'template' (FR-150), 'discussion_ready' (FR-100),
    'draft_undecided' (Agent draft: every spec item must be '[ ]'), and
    'final_decided' (Runtime-finalized spec: every item must be '[x]').
    """
    checks = checks or []
    if not path.exists():
        return ("schema", "missing file")
    text = path.read_text(encoding="utf-8")
    head, body = split_frontmatter(text)
    if not head:
        return ("schema", "no frontmatter")
    steps = (
        (_scope_failure, (doc, body)),
        (_trace_failure, (path, doc)),
        (_template_failure, (path, checks)),
        (_decision_failure, (doc, text, checks)),
        (_discussion_failure, (text, checks)),
    )
    for check, args in steps:
        failure = check(*args)
        if failure:
            return failure
    return None


def _scope_failure(doc: str, body: str):
    if doc != "spec.md":
        return None
    n = _valid_fr_count(body)
    return ("scope_overflow", f"{n} FRs > {FR_LIMIT}") if n > FR_LIMIT else None


def _trace_failure(path: Path, doc: str):
    if doc != "acceptance.md":
        return None
    issues = check_trace_file(path)
    return ("trace", "; ".join(issues)) if issues else None


def _template_failure(path: Path, checks: list):
    if "template" not in checks:
        return None
    issues = check_template(path)
    return ("template", "; ".join(issues)) if issues else None


def _decision_failure(doc: str, text: str, checks: list):
    if doc != "spec.md":
        return None
    for check, decided in (("draft_undecided", False), ("final_decided", True)):
        if check in checks:
            issues = check_spec_decisions(text, decided=decided)
            if issues:
                return (check, "; ".join(issues))
    return None


def _discussion_failure(text: str, checks: list):
    if "discussion_ready" not in checks:
        return None
    ready, blockers = check_ready(text)
    if ready:
        return None
    return ("discussion_ready", "unresolved threads: " + ", ".join(blockers))


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


def _status_matches(block: list) -> list:
    return list(filter(None, map(_STATUS.match, block)))


def _valid_fr_count(text: str) -> int:
    """FR-20 scope count: all FR items (obsolete ones are deleted, not marked;
    NFRs never count)."""
    return sum(
        1 for _, heading, _ in _spec_items(text)
        if _ITEM_HEAD.match(heading).group(1).upper().startswith("FR-")
    )


def _acc_scan(acc_text: str) -> tuple[dict, list]:
    """Acceptance-side scan: ``({section_id: line_no}, [(ac_id, ref_id, line_no,
    section_id)])``. Fenced code and discussion blocks ('>' lines) skipped;
    a non-section level-2 heading closes the open section."""
    sections: dict = {}
    acs: list = []
    fence = False
    current = None
    for i, line in enumerate(acc_text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence or line.lstrip().startswith(">"):
            continue
        if m := _ACC_SECTION.match(line):
            current = m.group(1).upper()
            sections.setdefault(current, i)
        elif m := _ACC_AC.match(line):
            ref = f"{m.group(2)}-{m.group(3)}".upper()
            acs.append((m.group(1), ref, i, current))
        elif (h := _HEADING.match(line)) and len(h.group(1)) == 2:
            current = None
    return sections, acs


def check_trace(spec_text: str, acc_text: str) -> list:
    """FR-0170 AC<->FR bidirectional coverage (both hard errors). Returns the
    complete orphan list with ``line:N`` messages (no short-circuit); [] = pass.

    Forward: every spec ``### FR-XXXX``/``### NFR-XXXX`` needs an acceptance
    ``## FR-XXXX`` section containing >=1 ``### AC-FRXXXX-YY`` back-referencing
    it (orphan -> item ID + spec line:N). Reverse: every acceptance AC must
    back-reference an existing spec item (orphan -> AC ID + acceptance line:N).
    Discussion blocks ('>' lines) and fenced code are ignored. No I/O."""
    spec_ids: dict = {}
    for line_no, heading, _ in _spec_items(spec_text):
        spec_ids.setdefault(_ITEM_HEAD.match(heading).group(1).upper(), line_no)
    sections, acs = _acc_scan(acc_text)
    covered = {section for _, ref, _, section in acs if section == ref}
    issues = [
        f"line:{line_no} {item_id} has no '## {item_id}' section in acceptance"
        if item_id not in sections else
        f"line:{line_no} {item_id} acceptance section has no AC item for it"
        for item_id, line_no in spec_ids.items()
        if item_id not in sections or item_id not in covered
    ]
    issues += [
        f"line:{line_no} {ac_id} refers to missing {ref} in spec"
        for ac_id, ref, line_no, _ in acs if ref not in spec_ids
    ]
    return issues


def check_trace_file(path: Path) -> list:
    """FR-0170 trace for an on-disk acceptance doc (reads the sibling spec.md).
    Used by validate_document (always-on for acceptance.md) and trac validate."""
    spec_path = path.parent / "spec.md"
    if not spec_path.exists():
        return ["line:1 acceptance validate requires spec.md in same dir"]
    return check_trace(spec_path.read_text(encoding="utf-8"),
                       path.read_text(encoding="utf-8"))


def check_spec_items(text: str) -> list:
    """FR-150 spec item lint — the machine-enforced half of the spec format.

    Checks per item: strict heading form, unique ID, a '- [ ] 已决定' /
    '- [x] 已决定' checkbox line, a '- **来源**：' field, and (for FRs) a
    '- **交付入口**：' field. Returns ``line:N`` messages.
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
        statuses = _status_matches(block)
        if not statuses:
            issues.append(f"line:{line_no} {item_id} missing 已决定 checkbox"
                          " (require exactly one '- [ ] 已决定' / '- [x] 已决定' line)")
        elif len(statuses) > 1:
            issues.append(f"line:{line_no} {item_id} has duplicate 已决定 checkboxes"
                          " (require exactly one)")
        if not any(ln.startswith("- **来源**：") for ln in block):
            issues.append(f"line:{line_no} {item_id} missing '- **来源**：' field")
        if (item_id.startswith("FR-")
                and not any(ln.startswith("- **交付入口**：") for ln in block)):
            issues.append(f"line:{line_no} {item_id} missing '- **交付入口**：' field")
    return issues


def check_spec_decisions(text: str, *, decided: bool) -> list:
    """Stage-specific spec decision state; [] = all items match.

    ``decided=False`` is the Sage draft contract: every item is ``[ ]`` and an
    Agent-produced ``[x]`` is rejected as self-approval. ``decided=True`` is
    the final contract after Runtime conversion: every item must be ``[x]``.
    Structural presence/uniqueness remains ``check_spec_items``'s concern.
    """
    expected = _DECIDED_STATUS if decided else _UNDECIDED_STATUS
    want = "- [x] 已决定" if decided else "- [ ] 已决定"
    state = "decided" if decided else "undecided draft"
    issues = []
    for line_no, heading, block in _spec_items(text):
        item_id = _ITEM_HEAD.match(heading).group(1).upper()
        if not any(expected.match(line) for line in block):
            issues.append(f"line:{line_no} {item_id} must be {state} ('{want}')")
    return issues


def _rewrite_spec_decisions(text: str, source: re.Pattern, replacement: str) -> tuple[str, int]:
    """Rewrite decision lines only inside real FR/NFR item blocks."""
    lines = text.splitlines(keepends=True)
    fence = False
    in_item = False
    converted = 0
    for i, line in enumerate(lines):
        raw = line.rstrip("\r\n")
        if raw.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        if _ITEM_HEAD.match(raw):
            in_item = True
            continue
        if (heading := _HEADING.match(raw)) and len(heading.group(1)) <= 3:
            in_item = False
            continue
        if in_item and source.match(raw):
            marker = source.match(raw).group(0)
            lines[i] = line.replace(marker, replacement, 1)
            converted += 1
    return "".join(lines), converted


def finalize_spec_decisions(text: str) -> tuple[str, int]:
    """Runtime: convert item ``[ ]`` to ``[x]`` after Lex + Human approval.

    Fenced code, discussion examples, and unrelated checkboxes remain unchanged.
    Returns ``(new_text, converted_count)`` and is idempotent for reconcile.
    """
    return _rewrite_spec_decisions(text, _UNDECIDED_STATUS, "- [x] 已决定")


def _strip_comments(text: str) -> str:
    """Drop HTML comment blocks (template guidance / commented-out conditional
    sections must not be read as required content)."""
    return _HTML_COMMENT.sub("", text)


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
    template (HTML comments ignored); spec docs additionally pass the item lint
    (``check_spec_items``). Acceptance level-2 sections vary per FR/NFR, so only
    frontmatter is name-checked there. Returns ``line:N ...`` messages; [] = valid.
    """
    kind = _KIND_BY_FILE.get(path.name)
    if kind is None:
        return [f"line:1 no template mapping for {path.name!r}"]
    try:
        tpl_text = templating.load_template(kind)
    except FileNotFoundError:
        return [f"line:1 no template for kind {kind!r}"]
    if not path.exists():
        return ["line:1 missing file"]
    text = path.read_text(encoding="utf-8")
    tpl_head, tpl_body = split_frontmatter(_strip_comments(tpl_text))
    head, body = split_frontmatter(_strip_comments(text))
    issues = [
        f"line:1 missing frontmatter field '{f}'"
        for f in sorted(_fm_fields(tpl_head) - _fm_fields(head))
    ]
    if kind != "acceptance":  # acceptance sections vary per FR/NFR (FR-150)
        tpl_secs = {s for s in (_norm_heading(ln) for ln in tpl_body.splitlines()) if s}
        doc_secs = {s for s in (_norm_heading(ln) for ln in body.splitlines()) if s}
        issues += [f"line:1 missing section '{s}'" for s in sorted(tpl_secs - doc_secs)]
    if kind == "spec":
        issues += check_spec_items(text)  # true file line numbers (full text scan)
    return issues
