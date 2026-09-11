"""FR-0080 trac check trace - BS->FR->AC->test full-chain orphan detection.

Pure-function core (check_trace_full) + file-reading wrapper (check_trace_full_file).
Reuses executor/validate.py scanning helpers (_spec_items/_acc_scan) per
architecture.md §3.1 / interfaces.md §1d.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tracks.executor.validate import _acc_scan, _spec_items

# BS-XX heading: two-digit, story `### BS-XX` (interfaces.md §1d / FR-0130).
_BS_HEADING = re.compile(r"^###\s+BS-(\d{2})\s", re.M)

# R-1 line-level marker: standalone comment line directly above test function.
# `( # | // ) + optional whitespace + AC-FRXXXX-YY@<version> + TRACKS-TRACE +
# optional one-line description`. The TRACKS-TRACE token is mandatory -- it
# distinguishes a binding marker from a normal AC-ID reference (revision log
# R-1 / interfaces.md §1d). Zero AST, zero third-party deps.
_MARKER_LINE = re.compile(
    r"^\s*(#|//)\s*(AC-(?:N?FR)\d{4}-\d{2})(@\S+)?\s+TRACKS-TRACE\b(.*)$", re.M
)

# Suffix whitelist: top-10 general-purpose languages (SQL excluded).
_TEST_SUFFIXES = frozenset(
    {
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".java",
        ".go",
        ".rs",
        ".cs",
        ".rb",
        ".php",
        ".c",
        ".cc",
        ".cpp",
        ".h",
        ".hpp",
        ".kt",
        ".swift",
    }
)

# tombstone: HTML comment `<!-- tombstone: ID -->` (interfaces.md §3e / FR-0130).
_TOMBSTONE = re.compile(
    r"<!--\s*tombstone:\s*((?:N?FR)-\d{4}|BS-\d{2}|AC-(?:N?FR)\d{4}-\d{2})\s*-->"
)


@dataclass(frozen=True)
class TraceReport:
    """FR-0080 trace report (interfaces.md §1d).

    v0.6 hotfix (IF-HOTFIX-007 / interfaces §2d): ``hotfix_scope`` carries the
    plan-level closure basis ``{run_id, anchor_acs, declared_unit_rows}`` for
    ``trac check trace --json`` output. None on feature-version invocations
    (the field is absent from the JSON — non-hotfix runs have no such field).
    """

    status: Literal["pass", "fail"]
    hard_errors: tuple[str, ...]
    warnings: tuple[str, ...]
    hotfix_scope: dict | None = None


def _scan_bs(story_text: str) -> list[tuple[str, int]]:
    """Return [(BS-XX, line)] from story, skipping fenced code."""
    items: list[tuple[str, int]] = []
    fence = False
    for i, line in enumerate(story_text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if not fence and (m := _BS_HEADING.match(line)):
            items.append((f"BS-{m.group(1)}", i))
    return items


def _scan_tombstones(*texts: str) -> set[str]:
    """Collect tombstone IDs from all texts (HTML comment form)."""
    ids: set[str] = set()
    for text in texts:
        ids.update(m.group(1) for m in _TOMBSTONE.finditer(text))
    return ids


def _scan_fr(spec_text: str) -> list[tuple[str, int]]:
    """Return [(FR-XXXX|NFR-XXXX, line)] from spec."""
    return [
        (re.match(r"^###\s+((?:N?FR)-\d+)", heading, re.I).group(1).upper(), line_no)
        for line_no, heading, _ in _spec_items(spec_text)
    ]


def _detect_duplicates(
    items: list[tuple[str, int]],
    label: str,
) -> list[str]:
    """Detect duplicate IDs in a list of (id, line) tuples."""
    errors: list[str] = []
    seen: dict[str, int] = {}
    for item_id, line in items:
        if item_id in seen:
            errors.append(
                f"duplicate {item_id}: {label} line:{seen[item_id]} and {label} line:{line}"
            )
        else:
            seen[item_id] = line
    return errors


def _check_fr_ac_errors(
    fr_items: list[tuple[str, int]],
    ac_items: list[tuple[str, str, int]],
    fr_ids: set[str],
    fr_to_acs: dict[str, list[str]],
    tombstones: set[str],
) -> list[str]:
    """FR<->AC bidirectional hard error detection."""
    errors: list[str] = []
    for fr_id, line in fr_items:
        if fr_id not in tombstones and fr_id not in fr_to_acs:
            errors.append(f"spec line:{line} {fr_id} has no AC item")
    for ac_id, fr_id, line in ac_items:
        if ac_id in tombstones or fr_id in tombstones:
            continue
        if fr_id not in fr_ids:
            errors.append(f"acceptance line:{line} {ac_id} references non-existent {fr_id}")
    return errors


def _marker_format_error(marker_str: str) -> str | None:
    """Context-free marker grammar layer.

    Single canonical rule, shared by the M-TEST EXIT trace gate
    (_check_one_marker_error) and the Shield WRITE preflight
    (marker_preflight_errors) -- no duplicated parser (2026-08-27 fix:
    short-format markers used to ride through every WRITE/red/Prism round
    and explode only at the EXIT gate, run 01M0S0FQ)."""
    if "@" not in marker_str:
        return f"test marker {marker_str} short format (missing @version)"
    return None


def _preflight_file_errors(
    path: Path, rel: str, current_version: str | None
) -> list[str]:
    """Per-file layer of marker_preflight_errors (CCR split)."""
    content = path.read_text(encoding="utf-8", errors="replace")
    out: list[str] = []
    for m in _MARKER_LINE.finditer(content):
        marker_str = m.group(2) + (m.group(3) or "")
        fmt = _marker_format_error(marker_str)
        if fmt:
            out.append(f"{rel}: {fmt}")
        elif current_version and marker_str.split("@", 1)[1] != current_version:
            # Cross-version marker: its own version's gate validates it
            # (same skip semantics as _check_one_marker_error).
            continue
    return out


def marker_preflight_errors(
    repo: Path, changed_paths: list[str], current_version: str | None
) -> list[str]:
    """Fail-fast marker grammar check for the Shield WRITE validation.

    Reuses _MARKER_LINE and the SAME layered rules as _check_one_marker_error:
    the format layer plus the version-match layer. The acceptance-existence
    layer stays at the M-TEST EXIT trace gate, where the full document
    context (acceptance.md, tombstones) exists. Scans only the round's
    changed test files; returns human-readable error strings."""
    errors: list[str] = []
    for rel in sorted({p for p in changed_paths if isinstance(p, str)}):
        if not rel.startswith("tests/"):
            continue
        path = repo / rel
        if not path.is_file() or path.suffix not in _TEST_SUFFIXES:
            continue
        if _DATA_DIRS & set(Path(rel).parts):
            continue
        errors.extend(_preflight_file_errors(path, rel, current_version))
    return errors


def _check_one_marker_error(
    marker_str: str,
    ac_id: str,
    ac_ids: set[str],
    tombstones: set[str],
    current_version: str | None,
) -> str | None:
    """Return a hard-error string for one marker, or None if it binds cleanly."""
    fmt = _marker_format_error(marker_str)
    if fmt:
        return fmt
    # Skip cross-version markers (historical, validated against their own version)
    if current_version and marker_str.split("@", 1)[1] != current_version:
        return None
    # Skip tombstoned ACs
    if ac_id in tombstones:
        return None
    if ac_id not in ac_ids:
        return f"test marker {marker_str} references non-existent {ac_id}"
    return None


def _check_ac_test_errors(
    ac_items: list[tuple[str, str, int]],
    ac_ids: set[str],
    test_markers: dict[str, list[str]],
    tombstones: set[str],
    current_version: str | None = None,
) -> list[str]:
    """AC<->test bidirectional hard error detection + short-format detection."""
    errors: list[str] = []
    marker_ac_ids_long = {
        ac_id for ac_id, markers in test_markers.items() if any("@" in m for m in markers)
    }
    for ac_id, _, line in ac_items:
        if ac_id not in tombstones and ac_id not in marker_ac_ids_long:
            errors.append(f"acceptance line:{line} {ac_id} has no test marker bound")
    for ac_id, markers in test_markers.items():
        for marker_str in markers:
            err = _check_one_marker_error(
                marker_str,
                ac_id,
                ac_ids,
                tombstones,
                current_version,
            )
            if err:
                errors.append(err)
    return errors


def _check_bs_warnings(
    bs_items: list[tuple[str, int]],
    tombstones: set[str],
) -> list[str]:
    """BS->FR weak-link warnings (do not change exit code)."""
    return [
        f"story line:{line} {bs_id} has no FR承接 (BS->FR weak link)"
        for bs_id, line in bs_items
        if bs_id not in tombstones
    ]


def check_trace_full(
    story_text: str,
    spec_text: str,
    acc_text: str,
    test_markers: dict[str, list[str]],
    current_version: str | None = None,
) -> TraceReport:
    """FR-0080 BS->FR->AC->test full-chain bidirectional orphan detection (pure).

    - FR<->AC hard errors: spec FR with no acceptance AC; acceptance AC pointing
      at a non-existent FR.
    - AC<->test hard errors: AC with no long-format marker; marker pointing at a
      non-existent AC.
    - BS->FR warning: BS with no FR承接 (does not change exit code).
    - Short-format marker (missing @version) -> hard error.
    - Duplicate FR/AC ID -> hard error (both conflicting line:N listed).
    - tombstone ID not counted as orphan.
    Full list reported without short-circuit; order stable and reproducible
    (NFR-0020).
    """
    tombstones = _scan_tombstones(story_text, spec_text, acc_text)
    fr_items = _scan_fr(spec_text)
    _, acs = _acc_scan(acc_text)
    ac_items = [(ac_id, ref_id, ln) for ac_id, ref_id, ln, _ in acs]
    fr_to_acs: dict[str, list[str]] = {}
    for ac_id, fr_id, _ in ac_items:
        fr_to_acs.setdefault(fr_id, []).append(ac_id)

    hard_errors = _detect_duplicates(fr_items, "spec")
    hard_errors += _detect_duplicates([(ac_id, ln) for ac_id, _, ln in ac_items], "acceptance")
    hard_errors += _check_fr_ac_errors(
        fr_items,
        ac_items,
        {fr_id for fr_id, _ in fr_items},
        fr_to_acs,
        tombstones,
    )
    hard_errors += _check_ac_test_errors(
        ac_items,
        {ac_id for ac_id, _, _ in ac_items},
        test_markers,
        tombstones,
        current_version=current_version,
    )
    warnings = _check_bs_warnings(_scan_bs(story_text), tombstones)
    return TraceReport(
        status="fail" if hard_errors else "pass",
        hard_errors=tuple(sorted(hard_errors)),
        warnings=tuple(sorted(warnings)),
    )


_DATA_DIRS = frozenset({"assets", "ground_truth"})


def _scan_test_markers(
    tests_dir: Path,
) -> tuple[dict[str, list[str]], bool]:
    """Scan whitelisted test files in tests_dir for R-1 marker comment lines.

    R-1 convention (revision log R-1): marker = standalone comment line
    ``( # | // ) AC-FRXXXX-YY@<version> [description]`` directly above the
    test function definition.  Detection is a single line-level regex --
    no AST, no third-party deps.  Non-marker positions (string literals,
    docstring bodies, code lines) are not matched because the regex
    requires the line to start with ``#`` or ``//``.

    Skips data directories (``assets`` and ``ground_truth``) by project
    convention and silently ignores unknown suffixes.

    Returns ``(markers, has_files)`` where *markers* maps
    ``{ac_id: [marker_str, ...]}`` and *has_files* is True iff at least
    one whitelisted test file was found outside data dirs.
    """
    markers: dict[str, list[str]] = {}
    has_files = False
    for path in sorted(tests_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix not in _TEST_SUFFIXES:
            continue
        rel_parts = path.relative_to(tests_dir).parts
        if _DATA_DIRS & set(rel_parts):
            continue
        has_files = True
        content = path.read_text(encoding="utf-8")
        for m in _MARKER_LINE.finditer(content):
            ac_id = m.group(2)
            version = m.group(3) or ""
            markers.setdefault(ac_id, []).append(ac_id + version)
    return markers, has_files


def _apply_baseline(report: TraceReport, baseline: dict | None) -> TraceReport:
    """Filter out hard_errors/warnings mentioning baseline-exempted IDs."""
    if baseline is None:
        return report
    exempted = set(baseline.get("trace_exemptions", {}).get("ids", []))
    if not exempted:
        return report
    hard = tuple(e for e in report.hard_errors if not any(eid in e for eid in exempted))
    warns = tuple(w for w in report.warnings if not any(eid in w for eid in exempted))
    return TraceReport(
        status="fail" if hard else "pass",
        hard_errors=hard,
        warnings=warns,
        hotfix_scope=report.hotfix_scope,
    )


def check_trace_full_file(
    version_dir: Path,
    tests_dir: Path,
    baseline: dict | None = None,
    hotfix_ctx: dict | None = None,
) -> TraceReport:
    """FR-0080 file-reading wrapper: reads story/spec/acceptance from
    version_dir, scans whitelisted test files in tests_dir for R-1 marker
    comment lines, calls check_trace_full. baseline is the legacy exemption
    (FR-0100).

    v0.6 hotfix (IF-HOTFIX-007 / interfaces §1f/§2d): when ``hotfix_ctx`` is
    provided the run is a hotfix run AND ``increment.declared`` is in the event
    stream (the caller — executor/CLI — only passes it under those two
    conditions, keeping feature-version trace semantics byte-identical). The
    check then runs the hotfix plan-level closure: the anchor AC set is closed
    by the delta §8 declared unit rows plus existing file markers, and every
    ``@<version>`` anchor reference resolves to the referenced version's
    acceptance.md. ``hotfix_ctx`` shape:
    ``{"projects_dir", "run_id", "anchor_acs", "declared_unit_rows"}``.

    If tests_dir yields zero whitelisted test files, returns a report with
    a warning and zero hard errors (AC-FR0080-12) -- does not falsely
    report all ACs as unbound.
    """
    if hotfix_ctx is not None:
        return check_hotfix_plan_closure(
            projects_dir=Path(hotfix_ctx.get("projects_dir") or version_dir.parent),
            tests_dir=tests_dir,
            run_id=hotfix_ctx.get("run_id", ""),
            anchor_acs=list(hotfix_ctx.get("anchor_acs") or []),
            declared_unit_rows=list(hotfix_ctx.get("declared_unit_rows") or []),
            baseline=baseline,
        )
    story = version_dir / "story.md"
    spec = version_dir / "spec.md"
    acc = version_dir / "acceptance.md"
    story_text = story.read_text(encoding="utf-8") if story.exists() else ""
    spec_text = spec.read_text(encoding="utf-8") if spec.exists() else ""
    acc_text = acc.read_text(encoding="utf-8") if acc.exists() else ""
    test_markers, has_files = _scan_test_markers(tests_dir)
    if not has_files:
        report = TraceReport(
            status="pass",
            hard_errors=(),
            warnings=("no supported test files found",),
        )
        return _apply_baseline(report, baseline)
    version_match = re.match(r"^(v\d+\.\d+)$", version_dir.name)
    current_version = version_match.group(1) if version_match else None
    report = check_trace_full(
        story_text,
        spec_text,
        acc_text,
        test_markers,
        current_version=current_version,
    )
    return _apply_baseline(report, baseline)


# -- v0.6 hotfix plan-level closure (IF-HOTFIX-007, interfaces §1e/§1f/§2d) -----


# Cross-version anchor reference: `AC-FRXXXX-YY@<version>` (interfaces §1e).
_ANCHOR_REF = re.compile(r"^(AC-(?:N?FR)\d{4}-\d{2})@(v\d+\.\d+)$")


def _resolve_anchor_ref(ref: str) -> tuple[str, str] | None:
    """Split ``AC-FRXXXX-YY@<version>`` into (ac_id, version), or None."""
    m = _ANCHOR_REF.match(ref.strip())
    if m is None:
        return None
    return m.group(1), m.group(2)


def _referenced_acs(projects_dir: Path, version: str) -> set[str]:
    """AC ids present in ``<projects_dir>/<version>/acceptance.md`` (empty when
    the version dir or acceptance.md is absent — an unresolvable reference)."""
    acc = projects_dir / version / "acceptance.md"
    if not acc.exists():
        return set()
    _, acs = _acc_scan(acc.read_text(encoding="utf-8"))
    return {ac_id for ac_id, _ref, _ln, _sec in acs}


def approved_acs(projects_dir: Path, version: str) -> list[str]:
    """Approved AC set declared by ``<version>/acceptance.md`` (deterministic).

    The acceptance document is the canonical approval artifact: its AC ids,
    in sorted order, drive one closure record per required AC (interfaces
    §1i -- the CLI consumes this instead of re-scanning documents itself).
    """
    return sorted(_referenced_acs(projects_dir, version))


def _anchor_not_closed(
    ref: str,
    declared_refs: set[str],
    file_marker_refs: set[str],
) -> bool:
    """Plan-level closure basis (interfaces §1f): an anchor AC is closed when
    a delta §8 declared unit row covers it OR an existing test file marker
    binds it. Only called when the run is hotfix and increment.declared is in
    the event stream (the caller gates the enablement)."""
    return ref not in declared_refs and ref not in file_marker_refs


def check_hotfix_plan_closure(
    projects_dir: Path,
    tests_dir: Path,
    run_id: str,
    anchor_acs: list[str],
    declared_unit_rows: list[dict],
    baseline: dict | None = None,
) -> TraceReport:
    """v0.6 hotfix plan-level trace closure (IF-HOTFIX-007, interfaces §1e/§1f).

    Enabled ONLY by the caller when the run is a hotfix run AND
    ``increment.declared`` is in the event stream (interfaces §1f/§2d
    anti-regression condition); feature-version invocations never call this
    function and keep the file-level closure byte-identical.

    Closure basis for the anchor AC set:
      - every ``AC-FRXXXX-YY@<version>`` anchor reference must resolve to the
        referenced version's acceptance.md (missing version dir or missing AC
        heading = hard error, 引用不实);
      - every anchor AC must be covered by a delta §8 declared unit row
        (``declared_unit_rows``, ac ids match) or an existing test file marker.

    The returned report carries ``hotfix_scope = {"run_id", "anchor_acs",
    "declared_unit_rows"}`` for ``trac check trace --json`` (interfaces §2d).
    """
    file_markers, _ = _scan_test_markers(tests_dir)
    file_marker_refs = {marker for markers in file_markers.values() for marker in markers}
    declared_refs = {
        row.get("ac", "")
        for row in declared_unit_rows
        if row.get("ac")
    }
    hard_errors = [
        err
        for ref in anchor_acs
        for err in _anchor_ref_errors(
            ref, projects_dir, declared_refs, file_marker_refs
        )
    ]
    scope = {
        "run_id": run_id,
        "anchor_acs": list(anchor_acs),
        "declared_unit_rows": list(declared_unit_rows),
    }
    report = TraceReport(
        status="fail" if hard_errors else "pass",
        hard_errors=tuple(sorted(hard_errors)),
        warnings=(),
        hotfix_scope=scope,
    )
    return _apply_baseline(report, baseline)


def _anchor_ref_errors(
    ref: str,
    projects_dir: Path,
    declared_refs: set[str],
    file_marker_refs: set[str],
) -> list[str]:
    """Hard errors for one ``AC-FRXXXX-YY@<version>`` anchor reference:
    malformed syntax, unresolvable in the referenced version's acceptance.md,
    or not closed by a declared unit row / existing test marker."""
    parsed = _resolve_anchor_ref(ref)
    if parsed is None:
        return [f"test marker {ref} malformed (expected AC-FRXXXX-YY@<version>)"]
    ac_id, version = parsed
    errors: list[str] = []
    if ac_id not in _referenced_acs(projects_dir, version):
        errors.append(
            f"test marker {ref} references non-existent AC in {version}/acceptance.md"
        )
    if _anchor_not_closed(ref, declared_refs, file_marker_refs):
        errors.append(f"test marker {ref} not closed by declared unit row or test marker")
    return errors


def _ac_ref_id(ac_ref: str) -> str:
    """Strip an optional ``@<version>`` suffix from an AC reference."""
    return ac_ref.split("@", 1)[0].strip()


# -- v0.7 candidate-bound closure (IF-CLOSURE-001, interfaces §1i / FR-0265) -----


# Closed set of hard-error tokens (interfaces §1i: node_missing | skip_xfail |
# identity_drift | control_failure | baseline_missing | mutation_missing |
# full_pass_missing | foreign_candidate).
CLOSURE_HARD_ERRORS = frozenset(
    {
        "node_missing",
        "skip_xfail",
        "identity_drift",
        "control_failure",
        "baseline_missing",
        "mutation_missing",
        "full_pass_missing",
        "foreign_candidate",
    }
)


@dataclass(frozen=True)
class ClosureRecord:
    """Per-AC candidate-bound trace record (interfaces §1i)."""

    ac: str
    outlet: str
    nodes: tuple[str, ...]
    baseline_evidence: str | None
    mutation_evidence: str | None
    full_pass_evidence: str | None
    candidate_digest: str
    status: Literal["pass", "fail"]


@dataclass(frozen=True)
class ClosureReport:
    """Candidate-bound trace closure report (interfaces §2c)."""

    status: Literal["pass", "fail"]
    closure: Literal["candidate-bound"]
    hard_errors: tuple[str, ...]
    records: tuple[ClosureRecord, ...]


def _closure_errors(candidate_digest: str, evidence: dict) -> list[str]:
    """Closed-set hard errors for one AC's candidate-bound evidence chain.

    Checks, in fixed order, every blocking condition (interfaces §1i / FR-0265):
    collected node missing, selected node skipped/xfailed (not truly executed),
    selection/evidence identity drift (node_count vs status_count mismatch),
    mutation control node not green, missing baseline/mutation/FULL evidence,
    and a baseline or mutation candidate digest foreign to this candidate.
    """
    errors: list[str] = []
    nodes = tuple(evidence.get("nodes") or ())
    node_statuses = tuple(evidence.get("node_statuses") or ())
    control_statuses = tuple(evidence.get("control_statuses") or ())

    if not nodes:
        errors.append("node_missing")
    if node_statuses and len(node_statuses) != len(nodes):
        errors.append("identity_drift")
    if any(s in ("skipped", "xfail") for s in node_statuses):
        errors.append("skip_xfail")
    if any(c != "pass" for c in control_statuses):
        errors.append("control_failure")
    if evidence.get("baseline_evidence") is None:
        errors.append("baseline_missing")
    if evidence.get("mutation_evidence") is None:
        errors.append("mutation_missing")
    if evidence.get("full_pass_evidence") is None:
        errors.append("full_pass_missing")
    if evidence.get("baseline_candidate") != candidate_digest or (
        evidence.get("mutation_candidate") != candidate_digest
    ):
        errors.append("foreign_candidate")
    return errors


def check_closure_candidate(
    acs: list[str],
    candidate_digest: str,
    per_ac_evidence: dict,
) -> ClosureReport:
    """Candidate-bound executable trace checker (IF-CLOSURE-001 / FR-0265).

    Pure join over every approved AC connecting acceptance -> outlet ->
    collected node -> baseline evidence -> mutation evidence -> same-candidate
    FULL pass. Top-level ``closure`` is always ``"candidate-bound"`` (the
    schema is v0.7-only); top-level ``status=pass`` only when every required AC
    record passes. Aggregates all hard errors without short-circuiting.

    The same function backs ``trac check trace --version v0.7`` and the
    ISLAND_GATE_2 exit gate (interfaces §1i / architecture §1.0.7).
    """
    records: list[ClosureRecord] = []
    hard_errors: list[str] = []
    for ac in acs:
        evidence = per_ac_evidence.get(ac) or {}
        errors = _closure_errors(candidate_digest, evidence)
        record = ClosureRecord(
            ac=ac,
            outlet=str(evidence.get("outlet", "")),
            nodes=tuple(evidence.get("nodes") or ()),
            baseline_evidence=evidence.get("baseline_evidence"),
            mutation_evidence=evidence.get("mutation_evidence"),
            full_pass_evidence=evidence.get("full_pass_evidence"),
            candidate_digest=candidate_digest,
            status="pass" if not errors else "fail",
        )
        records.append(record)
        hard_errors.extend(errors)
    return ClosureReport(
        status="pass" if all(r.status == "pass" for r in records) else "fail",
        closure="candidate-bound",
        hard_errors=tuple(dict.fromkeys(hard_errors)),
        records=tuple(records),
    )


# -- v0.8 release trace closed-loop export (IF-MILESTONE-001, interfaces §1i) --


def _canonical_json(value) -> str:
    """Repo canonical JSON (sort_keys + compact separators, phase0_seal convention)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def build_release_trace(
    *,
    candidate_sha: str,
    artifact_digest: str,
    evidence_digests: list,
    preview_digest: str,
    human_approval_event_seq: int,
    operation_digests: list,
    release_tag: str,
) -> dict:
    """Compose the interfaces §1i closed release trace with its trace_digest.

    ``trace_digest = "sha256:" + sha256(canonical_json(remaining fields))``
    makes the close-comment tokens (candidate SHA / preview_digest /
    release_tag) externally cross-checkable against the tag/release
    (NFR-0143-02). The keyword-only signature enforces the §1i closed field
    set: unknown or missing fields fail closed (TypeError) instead of
    producing a digest over a partial or extended payload.
    """
    remaining = {
        "candidate_sha": candidate_sha,
        "artifact_digest": artifact_digest,
        "evidence_digests": evidence_digests,
        "preview_digest": preview_digest,
        "human_approval_event_seq": human_approval_event_seq,
        "operation_digests": operation_digests,
        "release_tag": release_tag,
    }
    digest = "sha256:" + hashlib.sha256(
        _canonical_json(remaining).encode("utf-8")
    ).hexdigest()
    return {**remaining, "trace_digest": digest}


def _release_trace_digest_matches(payload: dict) -> bool:
    """True when trace_digest re-derives from the §1i closed field set."""
    fields = {key: value for key, value in payload.items() if key != "trace_digest"}
    expected = "sha256:" + hashlib.sha256(
        _canonical_json(fields).encode("utf-8")
    ).hexdigest()
    return payload.get("trace_digest") == expected


def _event_type_payload(event):
    if isinstance(event, dict):
        return event.get("type"), event.get("payload") or {}
    return getattr(event, "type", None), getattr(event, "payload", None) or {}


def build_release_trace_segment(events) -> dict:
    """IF-TRACE-003 release segment for ``trac check trace --version v0.8``.

    Pure projection of the landed ``milestone.trace_closed`` record: the
    segment is ``closed`` only when the §1i trace_digest re-derives from its
    own closed field set, ``inconsistent`` on any drift, and ``pending`` when
    no trace closure exists yet. The verdict is never inferred from agent
    text and never recomputes a pass from missing evidence.
    """
    closed = None
    for event in events or ():
        etype, _payload = _event_type_payload(event)
        if etype == "milestone.trace_closed":
            closed = event
    if closed is None:
        return {"status": "pending", "reason": "milestone.trace_closed missing"}
    _etype, payload = _event_type_payload(closed)
    payload = dict(payload)
    if not _release_trace_digest_matches(payload):
        return {
            "status": "inconsistent",
            "reason": "trace_digest mismatch",
            "trace_digest": payload.get("trace_digest", ""),
        }
    return {"status": "closed", **payload}
