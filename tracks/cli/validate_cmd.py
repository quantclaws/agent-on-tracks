"""``trac validate`` / ``trac check`` command face.

Extracted from :mod:`tracks.cli.main` for module-size compliance (C0302):
standalone template and tasks.json validation plus the check subcommands
(deliverables, trace, reach, release-evidence) and the candidate-bound
closure exit (IF-CLOSURE-001). ``tracks.cli.main`` re-exports every name.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

from tracks import paths
from tracks.checks.reach import check_reach_file
from tracks.checks.release_evidence import check_release_evidence_file
from tracks.checks.trace import approved_acs, check_trace_full_file
from tracks.deliverables import check_deliverables
from tracks.executor import version_extensions
from tracks.executor.taskgraph import (
    parse_tasks_json,
    validate_ac_coverage,
    validate_dag,
    validate_issue_numbers,
    validate_scope,
)
from tracks.executor.test_tasks import (
    _extract_if_registry,
    _known_ac_ids,
    parse_hotfix_unit_rows,
)
from tracks.executor.validate import (
    check_design_trace_file,
    check_template,
    check_test_tasks_contract_file,
    check_trace_file,
)
from tracks.project import ContractError, load_contract
from tracks.store import Store

from .common import _err


def _reject_unknown_host_contract_table(path: Path) -> bool:
    """§1e closed-set for [host-contract.*] (FR-0281). Returns True on reject."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    import re as _re

    allowed = {
        "host-contract",
        "host-contract.local_gate",
        "host-contract.version_scheme",
        "host-contract.build",
        "host-contract.smoke",
        "host-contract.security_scan",
        "host-contract.ci",
        "host-contract.tracker",
        "host-contract.operations.feature",
        "host-contract.operations.post_release",
        "host-contract.operations.dev",
    }
    for m in _re.finditer(r"^\s*\[{1,2}\s*([^\]\s]+)\s*\]{1,2}", text, _re.MULTILINE):
        name = m.group(1).strip()
        if name.startswith("host-contract") and name not in allowed:
            print(f"contract error: unknown host-contract table [{name}]", file=sys.stderr)
            return True
    return False


def _validate_non_canonical_file(path: Path) -> list[str]:
    if path.name == "tasks.json":
        return _validate_tasksjson(path)
    issues = check_template(path)
    if path.name == "acceptance.md":
        issues += check_trace_file(path)
    if path.name == "test-plan.md":
        issues += check_design_trace_file(path)
        issues += check_test_tasks_contract_file(path)
    return issues


def cmd_validate(repo: Path, *args) -> int:
    # FR-150 / AC-1501: `trac validate --file <path>` — standalone template
    # check (also reused by the outcome / exit-gate validation). No lock/state:
    # it is a pure read of the given file against its kind template.
    if len(args) != 2 or args[0] != "--file":
        return _err("usage: trac validate --file <path>")
    path = Path(args[1])
    # FRB-F: the canonical host execution contract validates through the
    # real loader (full ContractError fail-closed surface), not a template.
    try:
        canonical = path.resolve() == (
            repo / ".tracks" / "projects" / "project.toml"
        ).resolve()
    except OSError:
        canonical = False
    if canonical:
        if _reject_unknown_host_contract_table(path):
            return 1
        try:
            load_contract(repo)
        except ContractError as exc:
            print(f"contract error: {exc.reason}", file=sys.stderr)
            return 1
        print("valid")
        return 0
    issues = _validate_non_canonical_file(path)
    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        return 1
    print("valid")
    return 0


def _validate_tasksjson(path: Path) -> list[str]:
    """FR-0180 five structural checks for tasks.json (IF-VALIDATE-001)."""
    if not path.exists():
        return [f"line:1 missing file: {path}"]
    text = path.read_text(encoding="utf-8")
    tasks, err = parse_tasks_json(text)
    if err is not None:
        return [err]
    errors: list[str] = []
    ok, cycle = validate_dag(tasks)
    if not ok:
        errors.append(cycle or "cycle detected")
    ok, overlap_errors = validate_scope(tasks)
    if not ok:
        errors.extend(overlap_errors)
    acc_path = path.parent / "acceptance.md"
    if_path = path.parent / "interfaces.md"
    if not acc_path.exists():
        errors.append("missing acceptance.md: cannot determine required ACs")
        required_acs: list[str] = []
    else:
        required_acs = sorted(_known_ac_ids(acc_path.read_text(encoding="utf-8")))
    if not if_path.exists():
        errors.append("missing interfaces.md: cannot validate IF- registry")
        if_registry: set[str] = set()
    else:
        extracted = _extract_if_registry(if_path.read_text(encoding="utf-8"))
        if extracted is None:
            errors.append("interfaces.md missing '## 5. IF Registry' section")
            if_registry = set()
        else:
            if_registry = extracted
    ok, coverage_errors = validate_ac_coverage(tasks, required_acs, if_registry)
    if not ok:
        errors.extend(coverage_errors)
    ok, issue_errors = validate_issue_numbers(tasks)
    if not ok:
        errors.extend(issue_errors)
    return errors


def _latest_version(home: Path) -> str | None:
    """Return the latest version directory name under .tracks/projects/."""
    projects = paths.projects_dir(home)
    if not projects.exists():
        return None
    versions = sorted(d.name for d in projects.iterdir() if d.is_dir() and d.name.startswith("v"))
    return versions[-1] if versions else None


def _load_baseline(home: Path) -> dict | None:
    """Read .tracks/legacy-baseline.json if it exists (FR-0100)."""
    import json as _json

    bp = home / "legacy-baseline.json"
    if bp.exists():
        return _json.loads(bp.read_text(encoding="utf-8"))
    return None


def _parse_check_trace_args(args: list[str]) -> tuple[bool, str | None] | None:
    """Parse `trace [--json] [--version <ver>]`. Returns (json, version) or None."""
    use_json = False
    version = None
    i = 0
    while i < len(args):
        if args[i] == "--json":
            use_json = True
            i += 1
        elif args[i] == "--version" and i + 1 < len(args):
            version = args[i + 1]
            i += 2
        else:
            return None
    return use_json, version


def _parse_check_reach_args(args: list[str]) -> tuple[bool, list[str]] | None:
    """Parse `reach [--json] [--entry <module>]...`. Returns (json, entries)."""
    use_json = False
    entries: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--json":
            use_json = True
            i += 1
        elif args[i] == "--entry" and i + 1 < len(args):
            entries.append(args[i + 1])
            i += 2
        else:
            return None
    return use_json, entries


def _cmd_check_trace(repo: Path, rest: list[str]) -> int:
    """Handle `trac check trace [--json] [--version <ver>]`."""
    parsed = _parse_check_trace_args(rest)
    if parsed is None:
        return _err("usage: trac check trace [--json] [--version <ver>]")
    use_json, version = parsed
    home = paths.tracks_home(repo)
    if version is None:
        version = _latest_version(home)
    if version is None:
        return _err("no .tracks/projects/v* version directories found")
    # architecture §1.0.9 / FR-0264-02: resolution is keyed by the target
    # version and precedes every classic-path precondition -- only a version
    # whose extension provides the trace capability takes the candidate-bound
    # closure exit (§2c), which needs no version directory; early versions
    # resolve nothing and keep their classic behaviour untouched.
    checker = version_extensions.resolve_capability(version, "trace")
    if checker is not None:
        release_builder = version_extensions.resolve_optional_capability(
            version, "release_trace"
        )
        return _cmd_check_trace_v07(
            repo, home, version, checker, use_json, release_builder
        )
    vdir = paths.version_dir(home, version)
    if not vdir.exists():
        return _err(f"version directory not found: {vdir}")
    tests_dir = repo / "tests"
    baseline = _load_baseline(home)
    hotfix_ctx = _active_hotfix_trace_context(home, vdir)
    report = check_trace_full_file(vdir, tests_dir, baseline, hotfix_ctx)
    if use_json:
        _print_trace_json(report)
    else:
        for e in report.hard_errors:
            print(e)
        for w in report.warnings:
            print(f"warning: {w}")
        if not report.hard_errors:
            print("trace ok")
    return 1 if report.status == "fail" else 0


def _print_trace_json(report) -> None:
    payload = {
        "status": report.status,
        "hard_errors": list(report.hard_errors),
        "warnings": list(report.warnings),
    }
    if report.status == "pass" and report.hotfix_scope is not None:
        payload["hotfix_scope"] = report.hotfix_scope
    print(json.dumps(payload, ensure_ascii=False))


# -- v0.7 candidate-bound closure exit (IF-CLOSURE-001, interfaces §1i) ----------


def _ac_base_ref(ac) -> str | None:
    """Bare AC id from a possibly cross-version ``AC-FRXXXX-YY@vX.Y`` reference."""
    if not isinstance(ac, str) or not ac.strip():
        return None
    return ac.split("@", 1)[0].strip()


def _merge_baseline_repair(entry: dict, payload: dict) -> None:
    """Fold one ``phase0.baseline_repaired`` payload into an AC evidence entry.

    Interfaces §1a row-1 fields: ``evidence_id`` names the frozen-baseline
    authenticity evidence, ``bound_node.node_id`` is the real collected node
    the gap was repaired with and ``bound_node.digest`` is the candidate
    identity the frozen baseline was repaired under -- the baseline-side
    digest channel the pure join compares against (interfaces §1i).
    """
    entry["baseline_evidence"] = payload.get("evidence_id") or entry.get("baseline_evidence")
    bound_node = payload.get("bound_node") or {}
    node_id = bound_node.get("node_id")
    if isinstance(node_id, str) and node_id:
        nodes = list(entry.get("nodes") or [])
        nodes.append(node_id)
        entry["nodes"] = list(dict.fromkeys(nodes))
    digest = bound_node.get("digest")
    if isinstance(digest, str) and digest:
        entry["baseline_candidate"] = digest


def _merge_mutation_manifest(entry: dict, payload: dict) -> None:
    """Fold one ``mutation.manifest`` payload (§1g field set) into an entry."""
    digest = payload.get("candidate_digest")
    if isinstance(digest, str) and digest:
        entry["mutation_candidate"] = digest
    patch = payload.get("patch_digest")
    if isinstance(patch, str) and patch:
        entry["mutation_evidence"] = patch


def _apply_full_execution(per_ac: dict, acs: list[str], payload: dict) -> None:
    """Credit a suite-level FULL execution to every required AC equally.

    The FULL gate executes the whole suite, so its outcome (real emitted
    ``full.executed`` payload keys: ``serves_as_full_f`` + ``evidence_ids``)
    becomes each required AC's full-pass evidence for this candidate.
    """
    if not payload.get("serves_as_full_f"):
        return
    evidences = sorted(e for e in payload.get("evidence_ids") or [] if isinstance(e, str))
    evidence_id = evidences[0] if evidences else payload.get("outcomes_ref")
    for ac in acs:
        per_ac.setdefault(ac, {})["full_pass_evidence"] = evidence_id


def _merge_stream_event(
    candidate_digest: str | None, per_ac: dict, acs: list[str], event
) -> str | None:
    """Fold one stream event into the harvest state; returns the current digest."""
    payload = event.payload if isinstance(event.payload, dict) else {}
    if event.type == "mutation.manifest":
        base = _ac_base_ref(payload.get("ac")) or ""
        _merge_mutation_manifest(per_ac.setdefault(base, {}), payload)
        digest = payload.get("candidate_digest")
        if isinstance(digest, str) and digest:
            return digest
    elif event.type == "phase0.baseline_repaired":
        base = _ac_base_ref(payload.get("ac")) or ""
        _merge_baseline_repair(per_ac.setdefault(base, {}), payload)
    elif event.type == "full.executed":
        _apply_full_execution(per_ac, acs, payload)
    return candidate_digest


def _closure_evidence(store: Store, version: str, acs: list[str]) -> tuple[str | None, dict]:
    """Harvest per-AC candidate-bound evidence from *version*'s event stream.

    Only contracted payloads feed the join: baseline repair (§3), mutation
    manifest (§1g) and same-suite FULL executions. Absent elements stay
    absent -- the pure join degrades those records to fail; evidence is
    never invented, padded or suppressed.
    """
    candidate_digest: str | None = None
    per_ac: dict[str, dict] = {}
    for run_id in store.runs_for_version(version):
        for event in store.events(run_id):
            candidate_digest = _merge_stream_event(candidate_digest, per_ac, acs, event)
    return candidate_digest, per_ac


def _closure_json(report) -> dict:
    """§1i JSON shape: classic envelope plus closure schema (records ordered)."""
    return {
        "status": report.status,
        "hard_errors": list(report.hard_errors),
        "warnings": [],
        "closure": report.closure,
        "records": [asdict(record) for record in report.records],
    }


def _version_events(store: Store, version: str) -> list:
    """All events of every run bound to *version* in seq order (audit order)."""
    events: list = []
    for run_id in store.runs_for_version(version):
        events.extend(store.events(run_id))
    return events


def _cmd_check_trace_v07(
    repo: Path, home: Path, version: str, checker, use_json: bool, release_builder=None
) -> int:
    """Candidate-bound closure exit slice (IF-CLOSURE-001 / interfaces §1i).

    Approved ACs come from the version's acceptance.md (canonical approval
    artifact): one record per approved AC, degraded to fail when evidence is
    missing -- records are never suppressed. ``checker`` is the same pure
    join ISLAND_GATE_2 uses; this branch only assembles inputs and reports.
    ``release_builder`` (IF-TRACE-003, v0.8 release-capable versions only)
    appends the release segment after the inherited closure: closed only on a
    self-consistent ``milestone.trace_closed`` record; a pending/inconsistent
    segment adds its reason instead of a silent pass.
    """
    del repo  # evidence lives in .tracks events; kept for CLI signature symmetry
    acs = approved_acs(paths.projects_dir(home), version)
    release: dict | None = None
    store = Store(home)
    try:
        candidate_digest, harvested = _closure_evidence(store, version, acs)
        if release_builder is not None:
            release = release_builder(_version_events(store, version))
    finally:
        store.close()
    report = checker(acs, candidate_digest, {ac: harvested.get(ac, {}) for ac in acs})
    release_failed = release is not None and release.get("status") == "inconsistent"
    if use_json:
        payload = _closure_json(report)
        if release is not None:
            payload["release"] = release
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for error in report.hard_errors:
            print(error)
        if release is not None:
            print(f"release: {release.get('status')}")
            if release_failed:
                print(f"release trace error: {release.get('reason', '')}")
        if not report.hard_errors and not release_failed:
            print("trace ok")
    return 1 if report.status == "fail" or release_failed else 0


def _active_hotfix_trace_context(home: Path, vdir: Path) -> dict | None:
    """Return release-evidence trace context for the active hotfix, if any."""
    store = Store(home)
    candidates = (store.active_run(), *store.runs_for_version(vdir.name))
    for run_id in candidates:
        if run_id is None or _run_version(store, run_id) != vdir.name:
            continue
        state = store.state(run_id)
        increment = next(
            (
                ev.payload
                for ev in reversed(list(store.events(run_id)))
                if ev.type == "increment.declared" and ev.payload.get("trace_status") == "pass"
            ),
            None,
        )
        if state.hotfix_issue is not None and increment is not None:
            break
    else:
        return None
    unit_rows = increment.get("unit_rows")
    if not isinstance(unit_rows, list):
        plan_path = vdir / "test-plan.md"
        unit_rows = parse_hotfix_unit_rows(
            plan_path.read_text(encoding="utf-8", errors="replace") if plan_path.exists() else ""
        )
    return {
        "projects_dir": str(paths.projects_dir(home)),
        "run_id": run_id,
        "anchor_acs": list(state.hotfix_anchor_acs or []),
        "declared_unit_rows": unit_rows,
    }


def _run_version(store: Store, run_id: str) -> str | None:
    return next((event.version for event in store.events(run_id)), None)


def _cmd_check_reach(repo: Path, rest: list[str]) -> int:
    """Handle `trac check reach [--json] [--entry <module>]...`."""
    parsed = _parse_check_reach_args(rest)
    if parsed is None:
        return _err("usage: trac check reach [--json] [--entry <module>]...")
    use_json, entries = parsed
    home = paths.tracks_home(repo)
    baseline = _load_baseline(home)
    report = check_reach_file(repo, baseline, entries)
    if use_json:
        print(
            json.dumps(
                {
                    "status": report.status,
                    "islands": list(report.islands),
                    "entrypoints": list(report.entrypoints),
                    "errors": list(report.errors),
                    "warnings": list(report.warnings),
                },
                ensure_ascii=False,
            )
        )
    else:
        for e in report.errors:
            print(e, file=sys.stderr)
        for w in report.warnings:
            print(f"warning: {w}")
        for island in report.islands:
            print(f"island module: {island}")
        if not report.islands and not report.errors:
            print("reach ok")
    return 1 if report.status == "fail" else 0


def _cmd_check_release_evidence(repo: Path, rest: list[str]) -> int:
    """Handle `trac check release-evidence [--json]` (IF-RELEASE-001, §2d)."""
    if rest not in ([], ["--json"]):
        print("usage: trac check release-evidence [--json]", file=sys.stderr)
        return 2
    use_json = bool(rest)
    report = check_release_evidence_file(str(repo))
    if use_json:
        print(
            json.dumps(
                {
                    "backend": report.backend,
                    "candidate_sha": report.candidate_sha,
                    "evidence_path": report.evidence_path,
                    "event_bounds": (
                        list(report.event_bounds) if report.event_bounds is not None else None
                    ),
                    "reason_code": report.reason_code,
                    "run_id": report.run_id,
                    "status": report.status,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0 if report.status == "satisfied" else 1
    if report.status == "satisfied":
        print(
            f"release-evidence: satisfied (backend={report.backend}, run {report.run_id}, "
            f"candidate HEAD {report.candidate_sha}, branch {report.branch})"
        )
        return 0
    print(f"release-evidence: NOT satisfied — {report.reason_code}")
    print("  next: rerun the opt-in live journey at current HEAD, then re-check")
    return 1


def cmd_check(repo: Path, *args) -> int:
    # FR-040/FR-130 / AC-1303: `trac check deliverables` - pre-commit/CI gate
    # FR-0080: `trac check trace [--json] [--version <ver>]`
    # FR-0090: `trac check reach [--json] [--entry <module>]...`
    # FR-0232: `trac check release-evidence [--json]`
    args = list(args)
    if not args:
        return _err("usage: trac check <deliverables|trace|reach|release-evidence>")
    sub, rest = args[0], args[1:]
    if sub == "deliverables":
        if rest:
            return _err("usage: trac check deliverables")
        issues = check_deliverables()
        if issues:
            for issue in issues:
                print(issue, file=sys.stderr)
            return 1
        print("deliverables ok")
        return 0
    if sub == "trace":
        return _cmd_check_trace(repo, rest)
    if sub == "reach":
        return _cmd_check_reach(repo, rest)
    if sub == "release-evidence":
        return _cmd_check_release_evidence(repo, rest)
    return _err("usage: trac check <deliverables|trace|reach|release-evidence>")
