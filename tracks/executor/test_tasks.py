"""Test-task contract: parse and validate test-plan §8 AC Coverage (D-28/BS-06).

Cohesive pure-logic module extracted from ``validate.py`` for module-size
compliance (C0302). Contains the §8 AC Coverage table parser, the IF-
registry extractor (interfaces.md §5), the design-trace checker (BS-06),
and the M-DESIGN EXIT structured test-task contract validator (D-28).

The parser only accepts the current template forms:
  - ``## 8. AC Coverage`` (level-2 heading, exact)
  - ``| AC id | layer | test | IF |`` (canonical English header)
  - ``## 5. IF Registry`` (level-2 heading, exact)

A level-3 ``###`` subsection inside an already-open §8 section stays inside
(normal Markdown section semantics); it never opens §8 on its own.

Shared utilities (``_acc_scan``, ``_strip_comments``, ``_HEADING``) are
imported from ``validation_shared.py``, breaking what was a circular import
with ``validate.py`` (which imports this module's higher-level contract
functions back).
"""
from __future__ import annotations

import re

from tracks.executor.validation_shared import _HEADING, _acc_scan, _strip_comments

# -- regexes ----------------------------------------------------------------

# FR-0140 IF- identifier pattern (interfaces.md §5 registry).
_IF_ID = re.compile(r"IF-[A-Z]+-\d{3}")

# BS-06 design trace: a test-plan line attributes a layer to an AC when both
# the AC id and a layer token share one (non-comment, non-fenced) line.
_LAYER = re.compile(r"\b(unit|integration|e2e)\b", re.I)

# AC id pattern scoped to a single test-plan §8 coverage-table row.
_AC_ID_IN_ROW = re.compile(r"AC-(?:N?FR)\d{4}-\d{2}")

# Canonical: `## 8. AC Coverage` (level-2 only).
_AC_COVERAGE_HEADING = re.compile(r"^##\s+8\.\s+AC Coverage\b", re.I | re.M)

_LAYER_HEADER = re.compile(r"\blayer\b", re.I)
_TEST_HEADER = re.compile(r"\btest\b", re.I)
_IF_HEADER = re.compile(r"\bif\b", re.I)
_AC_COL = re.compile(r"^AC(?:\s+id)?$", re.I)
_LAYER_COL = re.compile(r"^layer$", re.I)
_TEST_COL = re.compile(r"^test$", re.I)
_IF_COL = re.compile(r"^IF$", re.I)
_LAYER_CELL_SEPARATOR = re.compile(r"\s*(?:\+|,|/|、|&|\band\b)\s*", re.I)
_IF_CELL_SEPARATOR = re.compile(r"[\s,;/+、&]+")

# Canonical: `## 5. IF Registry` (level-2 only).
_IF_REGISTRY_HEADING = re.compile(r"^##\s+5\.\s+IF Registry\b", re.I | re.M)


# -- table helpers ----------------------------------------------------------


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
    m = _HEADING.match(stripped)
    if m:
        level = len(m.group(1))
        if level == 2:
            # A level-2 heading opens/closes the §8 coverage section.
            return bool(_AC_COVERAGE_HEADING.match(stripped)), None, []
        # Level-3 subsections stay inside §8; they never open it.
        if in_coverage:
            return True, None, []
        return in_coverage, columns, []
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


def _is_canonical_header(cells: list[str]) -> bool:
    """True when the header cells match the canonical §8 form."""
    if len(cells) < 4:
        return False
    return bool(_AC_COL.match(cells[0]) and _LAYER_COL.match(cells[1])
                and _TEST_COL.match(cells[2]) and _IF_COL.match(cells[3]))


# -- heading/section tracking (shared by header + empty-cell scanners) -------


def _coverage_heading_state(stripped: str, in_coverage: bool) -> tuple[bool, bool] | None:
    """Map a heading line to (in_coverage, reset_table), or None for non-headings.

    Level-2 ``##`` opens/closes the §8 section. Level-3 ``###`` subsections
    stay inside §8 (normal Markdown section semantics) and reset the table
    state; they never open §8 on their own.
    """
    m = _HEADING.match(stripped)
    if not m:
        return None
    if len(m.group(1)) == 2:
        return bool(_AC_COVERAGE_HEADING.match(stripped)), True
    if in_coverage:
        return True, True
    return in_coverage, False


def _find_coverage_headers(plan_text: str) -> list[list[str]]:
    """Return header cell lists from all §8 AC Coverage tables."""
    headers: list[list[str]] = []
    in_coverage = False
    header_seen = False
    for line in _visible_plan_lines(plan_text):
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        state = _coverage_heading_state(stripped, in_coverage)
        if state is not None:
            in_coverage, reset = state
            if reset:
                header_seen = False
            continue
        if not in_coverage or header_seen or "|" not in stripped:
            continue
        cells = _table_cells(stripped)
        if _table_separator(cells):
            continue
        headers.append(cells)
        header_seen = True
    return headers


def _empty_test_cell_issue(stripped: str, cells: list[str], test_idx: int) -> str | None:
    """Return an issue string if a data row has an empty test cell."""
    if test_idx < 0:
        return None
    test_cell = cells[test_idx] if test_idx < len(cells) else ""
    if test_cell.strip():
        return None
    ac_ids = _AC_ID_IN_ROW.findall(stripped)
    ac_ref = ac_ids[0] if ac_ids else "unknown"
    return f"AC {ac_ref} empty test cell"


def _scan_line_for_empty_check(stripped: str, in_coverage: bool,
                               test_idx: int, header_seen: bool
                               ) -> tuple[bool, bool, int, bool]:
    """Decide whether a line is a skip line for empty-cell checking.

    Returns ``(skip, in_coverage, test_idx, header_seen)``.
    """
    if stripped.startswith(">"):
        return True, in_coverage, test_idx, header_seen
    state = _coverage_heading_state(stripped, in_coverage)
    if state is not None:
        in_coverage, reset = state
        if reset:
            return True, in_coverage, -1, False
        return True, in_coverage, test_idx, header_seen
    if not in_coverage or "|" not in stripped:
        return True, in_coverage, test_idx, header_seen
    return False, in_coverage, test_idx, header_seen


def _check_empty_test_cells(plan_text: str, issues: list) -> None:
    """Check that no §8 coverage row has an empty test cell."""
    in_coverage = False
    test_idx = -1
    header_seen = False
    for line in _visible_plan_lines(plan_text):
        stripped = line.strip()
        skip, in_coverage, test_idx, header_seen = _scan_line_for_empty_check(
            stripped, in_coverage, test_idx, header_seen)
        if skip:
            continue
        cells = _table_cells(stripped)
        if _table_separator(cells):
            continue
        if not header_seen:
            test_idx = next((i for i, cell in enumerate(cells)
                             if _TEST_HEADER.search(cell)), -1)
            header_seen = True
            continue
        issue = _empty_test_cell_issue(stripped, cells, test_idx)
        if issue:
            issues.append(issue)


# -- cell parsers ------------------------------------------------------------


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


# -- acceptance-side helpers -------------------------------------------------


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


def parse_test_tasks(acc_path, plan_path) -> list[dict]:
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


# -- visible-line / IF-line checks (BS-06 design trace) ----------------------


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


def required_ac_ids(acc_path, plan_path) -> set[str]:
    """FR-0070 EXIT gate helper: AC IDs with an integration|e2e layer
    attribution in test-plan.md (the ``required`` ACs whose marker binding the
    M-TEST trace gate must verify). Unit-only ACs do not block M-TEST exit."""
    if not acc_path.exists() or not plan_path.exists():
        return set()
    _, acs = _acc_scan(acc_path.read_text(encoding="utf-8"))
    visible = _visible_plan_lines(plan_path.read_text(encoding="utf-8"))
    return {ac_id for ac_id, _ref, _ln, _sec in acs
            if _layer_of(ac_id, visible) in ("integration", "e2e")}


# -- BS-06 design trace ------------------------------------------------------


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


def _extract_if_registry(interfaces_text: str) -> set[str] | None:
    """FR-0140: extract IF- identifiers from interfaces.md §5 IF Registry.
    Returns None when §5 is missing, empty set when §5 exists but has no
    IF- identifiers. HTML comments are stripped first so template guidance
    examples (e.g. ``IF-EXAMPLE-001``) are never counted as registered."""
    m = _IF_REGISTRY_HEADING.search(interfaces_text)
    if m is None:
        return None
    section = interfaces_text[m.start():]
    next_heading = re.search(r"^##\s+", section[1:], re.M)
    if next_heading:
        section = section[:next_heading.start() + 1]
    return set(_IF_ID.findall(_strip_comments(section)))


def check_design_trace_file(path) -> list:
    """BS-06 trace for an on-disk test-plan doc (reads the sibling
    acceptance.md). Used by validate_document (checks=["trace"]) and
    trac validate. FR-0140: also reads sibling interfaces.md for IF- registry."""
    acc_path = path.parent / "acceptance.md"
    if not acc_path.exists():
        return ["line:1 test-plan validate requires acceptance.md in same dir"]
    if_path = path.parent / "interfaces.md"
    if not if_path.exists():
        return ["line:1 test-plan validate requires interfaces.md in same dir"]
    registry = _extract_if_registry(if_path.read_text(encoding="utf-8"))
    if registry is None:
        return ["interfaces.md missing '## 5. IF Registry' section"]
    if not registry:
        return ["interfaces.md §5 IF Registry is empty (no IF- identifiers)"]
    return check_design_trace(
        acc_path.read_text(encoding="utf-8"),
        path.read_text(encoding="utf-8"),
        registry,
    )


# -- D-28 M-DESIGN EXIT contract --------------------------------------------


def _row_declares_shield(layer_cell: str | None) -> bool:
    """True when a §8 coverage row carries an integration/e2e layer token."""
    if not layer_cell or not layer_cell.strip():
        return False
    return any(
        part.strip().lower() in ("integration", "e2e")
        for part in _LAYER_CELL_SEPARATOR.split(layer_cell)
    )


def _shield_task_issues(
    ac_id: str, task: dict | None, if_registry: set[str] | None,
) -> list[str]:
    """Per-AC issue strings for an invalid Shield task (layers/IF/registry)."""
    if task is None:
        return []  # valid unit-only coverage is not a Shield task
    out: list[str] = []
    if not task["layers"]:
        out.append(f"AC {ac_id} invalid layer (must be unit/integration/e2e)")
        return out
    if not task["if_ids"]:
        out.append(f"AC {ac_id} missing IF- attribution for integration/e2e")
        return out
    if if_registry is not None:
        out += [f"{if_id} not registered"
                for if_id in task["if_ids"] if if_id not in if_registry]
    return out


def _check_ac_task(ac_id: str, rows: list, if_registry: set[str] | None,
                   issues: list) -> bool:
    """Validate one AC's coverage rows; return True when it yields a valid
    Shield test task (non-empty integration/e2e layers AND non-empty IF- ids)."""
    if not _rows_for_ac(ac_id, rows):
        issues.append(f"missing row for AC {ac_id}")
        return False
    task = _test_task_for_ac(ac_id, rows)
    if task is None:
        return False  # valid unit-only coverage is not a Shield task
    issues += _shield_task_issues(ac_id, task, if_registry)
    return bool(task["layers"] and task["if_ids"]
                and (if_registry is None
                     or all(i in if_registry for i in task["if_ids"])))


def _coverage_section_issues(plan_text: str, issues: list) -> None:
    """Append issues for missing §8 section, non-canonical headers, duplicate
    AC rows, and empty test cells."""
    if not _AC_COVERAGE_HEADING.search(plan_text):
        issues.append("test-plan.md missing '## 8. AC Coverage' section")
    for header_cells in _find_coverage_headers(plan_text):
        if not _is_canonical_header(header_cells):
            issues.append(f"non-canonical §8 header: {' | '.join(header_cells)}")
    rows = _coverage_rows(plan_text)
    ac_counts: dict[str, int] = {}
    for ac_id, _layer, _if in rows:
        ac_counts[ac_id] = ac_counts.get(ac_id, 0) + 1
    for ac_id in sorted(ac_counts):
        if ac_counts[ac_id] > 1:
            issues.append(f"duplicate AC row: {ac_id}")
    _check_empty_test_cells(plan_text, issues)


def check_test_tasks(acc_text: str, plan_text: str,
                     if_registry: set[str] | None = None) -> list:
    """D-28 M-DESIGN EXIT structured test-task contract check (fail-closed
    M-DESIGN->M-TEST). Validates test-plan §8 AC Coverage against acceptance.md:

    - §8 must be present.
    - Every acceptance AC must have a coverage row; unknown AC rows are flagged.
    - Every row's layer must be one of {unit, integration, e2e}.
    - An integration/e2e AC must carry at least one IF- id; when ``if_registry``
      is provided each IF- must be registered there.
    - When any integration/e2e layer is declared, at least one AC must yield a
      valid Shield test task (non-empty layers AND non-empty IF- ids).

    Returns ``line``-free issue strings ([] = valid). No I/O.
    """
    issues: list = []
    _coverage_section_issues(plan_text, issues)
    known = _known_ac_ids(acc_text)
    rows = _coverage_rows(plan_text)
    row_ac_ids = {ac for ac, _layer, _if in rows}
    for ac_id in sorted(row_ac_ids - known):
        issues.append(f"unknown AC {ac_id}")
    valid_shield = False
    for ac_id in sorted(known):
        if _check_ac_task(ac_id, rows, if_registry, issues):
            valid_shield = True
    if not valid_shield:
        if any(_row_declares_shield(layer) for _ac, layer, _if in rows):
            issues.append("no valid Shield test task "
                          "(integration/e2e declared but none is attributable)")
        else:
            issues.append("no valid Shield test task "
                          "(no integration/e2e layer declared)")
    return issues


def check_test_tasks_contract_file(plan_path) -> list:
    """D-28 file-level contract check for an on-disk test-plan doc (reads the
    sibling acceptance.md + interfaces.md IF- registry). Used by
    validate_document (checks=["test_tasks"]) and trac validate."""
    acc_path = plan_path.parent / "acceptance.md"
    if not acc_path.exists():
        return ["line:1 test-plan test_tasks validate requires acceptance.md"]
    if_path = plan_path.parent / "interfaces.md"
    if not if_path.exists():
        return ["line:1 test-plan test_tasks validate requires interfaces.md"]
    registry = _extract_if_registry(if_path.read_text(encoding="utf-8"))
    if registry is None:
        return ["interfaces.md missing '## 5. IF Registry' section"]
    if not registry:
        return ["interfaces.md §5 IF Registry is empty (no IF- identifiers)"]
    return check_test_tasks(
        acc_path.read_text(encoding="utf-8"),
        plan_path.read_text(encoding="utf-8"),
        registry,
    )
