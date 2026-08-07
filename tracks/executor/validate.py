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

v0.3 adds the design trio kinds (architecture / interfaces / test-plan,
flow.md §8): the ``template`` check works generically from their templates,
and ``check_design_trace`` (BS-06) is the design ``trace`` check — every AC
of acceptance.md must carry a test-layer attribution (unit/integration/e2e)
in test-plan.md. Unlike the acceptance trace it is checks-list gated. Design
docs reserve blockquotes for inline-discussion threads, so the design-kinds
``template`` check also rejects leftover template-guidance blockquotes
(live run044): the marker set is derived from the design templates' guidance
comments (single source). Design docs additionally reject fabricated ``trac``
invocations (live run045): every ``trac <token>`` in the doc text (code
fences included) must name a subcommand that actually exists
(``TRAC_SUBCOMMANDS``, kept in parity with tracks/cli/main.py USAGE).

Spec item grammar (kept in sync with templates/spec.md; the template itself
carries no format prose — this module IS the format contract): every item is
``### FR-XXXX 标题`` / ``### NFR-XXXX 标题`` (uppercase, 4-digit zero-padded,
unique ID; obsolete items are deleted, IDs never reused), with a
``- **来源**：`` field and (for FRs) a ``- **交付入口**：`` field. Decisions are
recorded in inline discussions; only unresolved discussions block review exit.
All present FR items count toward FR_LIMIT. Template HTML comments are ignored;
acceptance level-2 sections vary per FR/NFR so are not name-checked.
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
    # v0.3 design trio (flow.md §8 / BS-03)
    "architecture.md": "architecture",
    "interfaces.md": "interfaces",
}
_HEADING = re.compile(r"^(#+)\s+(.*\S)\s*$")
_NUM_PREFIX = re.compile(r"^\d+(?:\.\d+)*\.?\s+")
_FENCE = re.compile(r"^\s*(```|~~~)")

# run044 guidance-blockquote guard: conditional-section guidance lives in the
# design templates' HTML comments as '**Marker**:' lines (When needed /
# Lifecycle); the same bold prefix as a blockquote in a delivered doc is
# leftover template guidance that the discuss parser would misread as an open
# inline-discussion thread.
_DESIGN_KINDS = ("architecture", "interfaces", "test-plan")
_BOLD_MARKER = r"\*\*([A-Za-z][A-Za-z0-9 ]*?)(?::\*\*|\*\*\s*:)"
_COMMENT_MARKER = re.compile(r"^\s*" + _BOLD_MARKER, re.M)
_BQ_GUIDANCE = re.compile(r"^\s*>+\s*" + _BOLD_MARKER)

# BS-06 design trace: a test-plan line attributes a layer to an AC when both
# the AC id and a layer token share one (non-comment, non-fenced) line.
_LAYER = re.compile(r"\b(unit|integration|e2e)\b", re.I)

# FR-0130 BS-XX heading grammar (story.md): two-digit, unique ID.
_BS_ITEM_HEAD = re.compile(r"^###\s+(BS-\d+)\b(.*)$", re.IGNORECASE)
_BS_ITEM_OK = re.compile(r"^### BS-\d{2} \S")

# FR-0140 IF- identifier pattern (interfaces.md §5 registry).
_IF_ID = re.compile(r"IF-[A-Z]+-\d{3}")

# FR-0130 cross-version qualified reference: AC-FRXXXX-YY@vX.Y
_VERSION_QUALIFIED = re.compile(r"AC-(?:N?FR)\d{4}-\d{2}@(v\d+\.\d+)")

# run045 contract realism: canonical `trac` subcommand set — keep in sync with
# the USAGE constant in tracks/cli/main.py (tests/unit/test_validate.py pins
# the parity). A design doc invoking `trac <token>` with any other token
# fabricates tooling (run045 finding: `trac agent archer ci-scan` never
# existed); to-be-created tooling must be marked as a foundation task instead.
TRAC_SUBCOMMANDS = frozenset({
    "approve", "check", "discuss", "init", "replay", "report", "retry",
    "return", "review", "run", "start", "status", "triage", "validate",
})
_TRAC_CALL = re.compile(r"\btrac\s+([A-Za-z][A-Za-z0-9_-]*)")


def validate_document(path: Path, doc: str, checks=None) -> tuple[str, str] | None:
    """Return (check, reason) on the first failure, None when valid.

    schema (+ spec scope_overflow) always run (v0.1 D-16 + FR-20); ``checks``
    adds the v0.2 gate 'discussion_ready' (FR-100), which blocks only when
    inline discussions remain unresolved.
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
        (_design_trace_failure, (path, doc, checks)),
        (_template_failure, (path, checks)),
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


def _design_trace_failure(path: Path, doc: str, checks: list):
    if doc != "test-plan.md" or "trace" not in checks:
        return None
    issues = check_design_trace_file(path)
    return ("trace", "; ".join(issues)) if issues else None


def _template_failure(path: Path, checks: list):
    if "template" not in checks:
        return None
    issues = check_template(path)
    return ("template", "; ".join(issues)) if issues else None


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


def _check_if_line(
    ac_id: str, line_no: int, ln: str, if_registry: set[str],
) -> list[str]:
    """Check IF- attribution on a single test-plan line."""
    if not _LAYER.search(ln):
        return []
    lower = ln.lower()
    if "integration" not in lower and "e2e" not in lower:
        return []
    if_ids = _IF_ID.findall(ln)
    if not if_ids:
        return [f"line:{line_no} {ac_id} integration/e2e AC missing IF- attribution"]
    return [
        f"line:{line_no} {if_id} not defined in interfaces.md §5"
        for if_id in if_ids if if_id not in if_registry
    ]


def _check_if_attribution(
    ac_id: str,
    line_no: int,
    ac_lines: list[str],
    if_registry: set[str],
) -> list[str]:
    """FR-0140: validate IF- attribution for integration/e2e ACs."""
    issues: list[str] = []
    for ln in ac_lines:
        issues += _check_if_line(ac_id, line_no, ln, if_registry)
    return issues


def _visible_plan_lines(plan_text: str) -> list[str]:
    """Strip HTML comments and fenced code from plan text."""
    visible: list[str] = []
    fence = False
    for line in _strip_comments(plan_text).splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if not fence:
            visible.append(line)
    return visible


def _check_ac_layer_and_if(
    ac_id: str,
    line_no: int,
    visible: list[str],
    if_registry: set[str] | None,
) -> list[str]:
    """Check layer attribution and IF- attribution for one AC."""
    ac_lines = [ln for ln in visible if ac_id in ln]
    if not any(_LAYER.search(ln) for ln in ac_lines):
        return [
            f"line:{line_no} {ac_id} has no layer attribution "
            "(unit/integration/e2e) in test-plan.md"
        ]
    if if_registry is not None:
        return _check_if_attribution(ac_id, line_no, ac_lines, if_registry)
    return []


def _layer_of(ac_id: str, visible: list[str]) -> str | None:
    """Return the test-layer attribution (unit/integration/e2e) of ``ac_id``
    from the visible test-plan lines, or None when unattributed."""
    for ln in visible:
        if ac_id in ln:
            m = _LAYER.search(ln)
            if m:
                return m.group(1).lower()
    return None


def required_ac_ids(acc_path: Path, plan_path: Path) -> set[str]:
    """FR-0070 EXIT gate helper: AC IDs with an integration|e2e layer
    attribution in test-plan.md (the ``required`` ACs whose marker binding the
    M-TEST trace gate must verify). Unit-only ACs do not block M-TEST exit."""
    if not acc_path.exists() or not plan_path.exists():
        return set()
    _, acs = _acc_scan(acc_path.read_text(encoding="utf-8"))
    visible = _visible_plan_lines(plan_path.read_text(encoding="utf-8"))
    return {ac_id for ac_id, _ref, _ln, _sec in acs
            if _layer_of(ac_id, visible) in ("integration", "e2e")}


# AC id pattern scoped to a single test-plan §8 coverage-table row.
_AC_ID_IN_ROW = re.compile(r"AC-(?:N?FR)\d{4}-\d{2}")
_AC_COVERAGE_HEADING = re.compile(r"^##\s+(?:8\.\s*)?AC Coverage\b", re.I)
_LAYER_HEADER = re.compile(r"layer|层", re.I)
_IF_HEADER = re.compile(r"\bif\b|归属", re.I)
_LAYER_CELL_SEPARATOR = re.compile(r"\s*(?:\+|,|/|、|&|\band\b)\s*", re.I)
_IF_CELL_SEPARATOR = re.compile(r"[\s,;/+、&]+")


def _table_cells(line: str) -> list[str]:
    if "|" not in line:
        return []
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _table_separator(cells: list[str]) -> bool:
    return not cells or all(set(cell) <= {"-", ":", " "} for cell in cells)


def _coverage_columns(cells: list[str]) -> tuple[int, int]:
    layer_idx = next((i for i, cell in enumerate(cells)
                      if _LAYER_HEADER.search(cell)), None)
    if_idx = next((i for i, cell in enumerate(cells)
                   if _IF_HEADER.search(cell)), None)
    return (layer_idx if layer_idx is not None else -1,
            if_idx if if_idx is not None else -1)


def _cell_at(cells: list[str], index: int) -> str | None:
    return cells[index] if 0 <= index < len(cells) else None


def _coverage_row_items(
    line: str, cells: list[str], columns: tuple[int, int],
) -> list[tuple[str, str | None, str | None]]:
    layer_idx, if_idx = columns
    return [
        (ac_id, _cell_at(cells, layer_idx), _cell_at(cells, if_idx))
        for ac_id in _AC_ID_IN_ROW.findall(line)
    ]


def _coverage_line(
    line: str, in_coverage: bool, columns: tuple[int, int] | None,
) -> tuple[bool, tuple[int, int] | None, list[tuple[str, str | None, str | None]]]:
    stripped = line.strip()
    if stripped.startswith(">"):
        return in_coverage, columns, []
    if stripped.startswith("## "):
        return bool(_AC_COVERAGE_HEADING.match(stripped)), None, []
    if not in_coverage or "|" not in line:
        return in_coverage, columns, []
    cells = _table_cells(line)
    if _table_separator(cells):
        return in_coverage, columns, []
    if columns is None:
        columns = _coverage_columns(cells)
        if columns != (-1, -1):
            return in_coverage, columns, []
    return in_coverage, columns, _coverage_row_items(line, cells, columns)


def _coverage_rows(plan_text: str) -> list[tuple[str, str | None, str | None]]:
    """Read AC rows from the test-plan §8 table.

    The column positions come from the table header instead of from the row's
    prose. This keeps a malformed layer/IF cell attached to its AC so the
    assignment can fail closed rather than silently dropping that AC.
    """
    rows: list[tuple[str, str | None, str | None]] = []
    in_coverage = False
    columns: tuple[int, int] | None = None
    for line in _visible_plan_lines(plan_text):
        in_coverage, columns, line_rows = _coverage_line(
            line, in_coverage, columns,
        )
        rows.extend(line_rows)
    return rows


def _parse_layer_cell(raw: str | None) -> tuple[set[str], bool]:
    if raw is None or not raw.strip():
        return set(), False
    parts = [part.strip().lower() for part in _LAYER_CELL_SEPARATOR.split(raw)]
    if not parts or any(part not in ("unit", "integration", "e2e") for part in parts):
        return set(), False
    return set(parts), True


def _parse_if_cell(raw: str | None) -> tuple[set[str], bool]:
    if raw is None or not raw.strip():
        return set(), False
    if_ids = set(_IF_ID.findall(raw))
    residue = _IF_CELL_SEPARATOR.sub("", _IF_ID.sub("", raw))
    if not if_ids or residue:
        return set(), False
    return if_ids, True


def _parse_ac_row(
    rows: list[tuple[str | None, str | None]],
) -> tuple[list[str], list[str]] | None:
    """Parse one AC's coverage rows, preserving malformed required rows."""
    candidate_layers: set[str] = set()
    required_rows: list[tuple[str | None, str | None]] = []
    malformed_layer = False
    for layer_cell, if_cell in rows:
        parsed_layers, layer_ok = _parse_layer_cell(layer_cell)
        if not layer_ok:
            malformed_layer = True
        required = parsed_layers & {"integration", "e2e"}
        if required:
            candidate_layers.update(required)
            required_rows.append((layer_cell, if_cell))
    if not candidate_layers and not malformed_layer:
        return None  # valid unit-only coverage is not a Shield task
    if malformed_layer:
        return [], []
    if_ids: set[str] = set()
    malformed_if = False
    for _layer_cell, if_cell in required_rows:
        parsed_if_ids, if_ok = _parse_if_cell(if_cell)
        if not if_ok:
            malformed_if = True
        if_ids.update(parsed_if_ids)
    return (sorted(candidate_layers),
            [] if malformed_if else sorted(if_ids))


def _known_ac_ids(acc_text: str) -> set[str]:
    _, acs = _acc_scan(acc_text)
    return {ac_id for ac_id, _ref, _line_no, _section in acs}


def _rows_for_ac(
    ac_id: str, rows: list[tuple[str, str | None, str | None]],
) -> list[tuple[str | None, str | None]]:
    return [
        (layer, if_cell)
        for row_ac, layer, if_cell in rows
        if row_ac == ac_id
    ]


def _empty_test_task(ac_id: str) -> dict:
    return {"ac_id": ac_id, "layers": [], "if_ids": []}


def _test_task_for_ac(
    ac_id: str, rows: list[tuple[str, str | None, str | None]],
) -> dict | None:
    ac_rows = _rows_for_ac(ac_id, rows)
    if not ac_rows:
        return _empty_test_task(ac_id)
    parsed = _parse_ac_row(ac_rows)
    if parsed is None:
        return None
    layers, if_ids = parsed
    return {"ac_id": ac_id, "layers": layers, "if_ids": if_ids}


def _test_tasks(
    ac_ids: set[str], rows: list[tuple[str, str | None, str | None]],
) -> list[dict]:
    tasks: list[dict] = []
    for ac_id in sorted(ac_ids):
        task = _test_task_for_ac(ac_id, rows)
        if task is not None:
            tasks.append(task)
    return tasks


def parse_test_tasks(acc_path: Path, plan_path: Path) -> list[dict]:
    """D-28: Runtime-side parse of test-plan §8 AC Coverage rows into the
    structured ``test_tasks`` list injected into the Shield WRITE assignment.

    Each row maps a required AC to its non-unit test layer(s) and the IF-
    green-condition identifiers that own it. Unit-only ACs are dropped (they
    do not block M-TEST exit and Shield writes only integration/e2e tests).

    Returns a deterministic list of ``{"ac_id", "layers", "if_ids"}`` dicts,
    sorted by ac_id, with layers and if_ids de-duplicated and sorted. An AC
    row missing a non-unit layer or carrying no IF- attribution is still
    included (fail-closed: the Shield sees the gap and the trace/Prism gates
    surface it) - this parser only structures the input, it does not gate.
    """
    if not acc_path.exists() or not plan_path.exists():
        return []
    known = _known_ac_ids(acc_path.read_text(encoding="utf-8"))
    rows = _coverage_rows(plan_path.read_text(encoding="utf-8"))
    return _test_tasks(known, rows)


def check_design_trace(
    acc_text: str, plan_text: str, if_registry: set[str] | None = None,
) -> list:
    """BS-06 design trace: every AC id in acceptance.md must appear in
    test-plan.md with a test-layer attribution (unit/integration/e2e) on the
    same line. Returns the complete orphan list with ``line:N`` messages (the
    acceptance line of the AC; no short-circuit); [] = pass. HTML comments and
    fenced code are ignored on the plan side. No I/O.

    FR-0140 extension: when ``if_registry`` is provided, also validates that
    every integration/e2e AC has an IF- attribution and that each IF- identifier
    is registered in interfaces.md §5."""
    _, acs = _acc_scan(acc_text)
    visible = _visible_plan_lines(plan_text)
    issues: list = []
    for ac_id, _ref, line_no, _section in acs:
        issues += _check_ac_layer_and_if(ac_id, line_no, visible, if_registry)
    return issues


def _extract_if_registry(interfaces_text: str) -> set[str]:
    """FR-0140: extract IF- identifiers from interfaces.md §5 table."""
    return set(_IF_ID.findall(interfaces_text))


def check_design_trace_file(path: Path) -> list:
    """BS-06 trace for an on-disk test-plan doc (reads the sibling
    acceptance.md). Used by validate_document (checks=["trace"]) and
    trac validate. FR-0140: also reads sibling interfaces.md for IF- registry."""
    acc_path = path.parent / "acceptance.md"
    if not acc_path.exists():
        return ["line:1 test-plan validate requires acceptance.md in same dir"]
    if_path = path.parent / "interfaces.md"
    if_registry: set[str] | None = None
    if if_path.exists():
        registry = _extract_if_registry(if_path.read_text(encoding="utf-8"))
        if_registry = registry if registry else None
    return check_design_trace(
        acc_path.read_text(encoding="utf-8"),
        path.read_text(encoding="utf-8"),
        if_registry,
    )


def check_spec_items(text: str) -> list:
    """FR-150 spec item lint — the machine-enforced half of the spec format.

    Checks per item: strict heading form, unique ID, a '- **来源**：' field,
    and (for FRs) a '- **交付入口**：' field. Decisions live in discussions;
    discussion_ready is checked separately. Returns ``line:N`` messages.
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
        if not any(ln.startswith("- **来源**：") for ln in block):
            issues.append(f"line:{line_no} {item_id} missing '- **来源**：' field")
        if (item_id.startswith("FR-")
                and not any(ln.startswith("- **交付入口**：") for ln in block)):
            issues.append(f"line:{line_no} {item_id} missing '- **交付入口**：' field")
    return issues


def _story_bs_items(text: str) -> list:
    """``(line_no, heading, block_lines)`` per BS-XX item in story.md; fenced
    code skipped, any heading (level <= 3) that is not itself an item closes
    the open block."""
    items: list = []
    cur = None
    fence = False
    for i, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        if _BS_ITEM_HEAD.match(line):
            cur = (i, line, [])
            items.append(cur)
        elif (m := _HEADING.match(line)) and len(m.group(1)) <= 3:
            cur = None
        elif cur:
            cur[2].append(line)
    return items


def check_story_items(text: str) -> list:
    """FR-0130 story item lint - BS-XX grammar enforcement.

    Checks per item: strict heading form (``### BS-XX 标题``, two-digit,
    uppercase), unique ID. ID immutability/tombstone rules are enforced by the
    trace tool (FR-0080); this validates the heading grammar. Returns
    ``line:N`` messages.
    """
    issues: list = []
    seen: dict = {}
    for line_no, heading, _block in _story_bs_items(text):
        item_id = _BS_ITEM_HEAD.match(heading).group(1).upper()
        if not _BS_ITEM_OK.match(heading):
            issues.append(
                f"line:{line_no} bad item heading {heading!r}"
                " (expect '### BS-XX 标题', uppercase, 2-digit)"
            )
        if item_id in seen:
            issues.append(
                f"line:{line_no} duplicate id {item_id}"
                f" (first at line:{seen[item_id]})"
            )
        seen.setdefault(item_id, line_no)
    return issues


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


def _design_guidance_markers() -> frozenset:
    """Guidance marker set, derived from the design trio templates (single
    source): '**Marker**:' lines inside their HTML guidance comments are the
    converted conditional-section guidance (When needed / Lifecycle)."""
    markers: set = set()
    for kind in _DESIGN_KINDS:
        try:
            tpl_text = templating.load_template(kind)
        except FileNotFoundError:
            continue
        for comment in _HTML_COMMENT.findall(tpl_text):
            markers.update(_COMMENT_MARKER.findall(comment))
    return frozenset(markers)


def _guidance_blockquote_issues(text: str) -> list:
    """Leftover template-guidance blockquotes (run044): delivered design docs
    reserve blockquotes for inline-discussion threads, so any blockquote whose
    bold prefix matches a guidance marker fails the template check. HTML
    comment and fenced-code lines are ignored. Returns ``line:N`` messages."""
    markers = _design_guidance_markers()
    if not markers:
        return []
    hidden: set = set()
    for m in _HTML_COMMENT.finditer(text):
        first = text.count("\n", 0, m.start()) + 1
        hidden.update(range(first, first + m.group(0).count("\n") + 1))
    issues: list = []
    fence = False
    for line_no, line in enumerate(text.splitlines(), start=1):
        if line_no in hidden:
            continue
        if _FENCE.match(line):
            fence = not fence
            continue
        if fence:
            continue
        if (m := _BQ_GUIDANCE.match(line)) and m.group(1) in markers:
            issues.append(f"line:{line_no} template guidance blockquote left "
                          f"in doc ('{m.group(1)}')")
    return issues


def _trac_command_issues(text: str) -> list:
    """run045 fabricated-command guard: every ``trac <token>`` invocation in
    the doc text (code fences included — fabricated commands hide there) must
    name a subcommand from ``TRAC_SUBCOMMANDS``. HTML comments are ignored.
    Returns ``line:N`` messages (true file line numbers)."""
    hidden: set = set()
    for m in _HTML_COMMENT.finditer(text):
        first = text.count("\n", 0, m.start()) + 1
        hidden.update(range(first, first + m.group(0).count("\n") + 1))
    issues: list = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if line_no in hidden:
            continue
        for token in _TRAC_CALL.findall(line):
            if token not in TRAC_SUBCOMMANDS:
                issues.append(
                    f"line:{line_no} unknown trac subcommand {token!r} "
                    "(no such command; mark to-be-created tooling as a "
                    "foundation task)")
    return issues


def check_template(path: Path) -> list:
    """FR-150 'template' check.

    Required frontmatter fields and level-2 sections must match the kind
    template (HTML comments ignored); spec docs additionally pass the item lint
    (``check_spec_items``). Acceptance level-2 sections vary per FR/NFR, so only
    frontmatter is name-checked there. Design trio docs additionally reject
    leftover template-guidance blockquotes (run044) and fabricated ``trac``
    subcommand invocations (run045). Returns ``line:N ...`` messages;
    [] = valid.
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
    if kind == "story":  # FR-0130: BS-XX grammar enforcement
        issues += check_story_items(text)
    if kind in _DESIGN_KINDS:  # run044: blockquote = discussion thread only
        issues += _guidance_blockquote_issues(text)
        # run045: no fabricated `trac` calls (full text -> true file line numbers)
        issues += _trac_command_issues(text)
    return issues
