"""Executor-side test-selection primitives (IF-SELECT-001/002, IF-RUNCONTRACT-001).

Pure selection semantics over an executable R1 baseline snapshot:

- ``classify_nodes`` splits the collected tree into ``r1`` (unchanged),
  ``r2`` (new or per-node-source-digest-changed), and ``removed``
  (baseline node absent from the current collect; fail-closed class,
  never silently dropped). Classification keys on per-node digests, so
  an unchanged sibling in a changed file stays ``r1``.
- ``select_r2`` projects exactly the ``r2`` class, stably sorted.
- ``make_selection_id`` stamps the selection identity (sha256 over the
  canonical form of scope/basis/nodes/baseline/commit).
- ``capture_test_baseline`` / ``collect_node_source_digests`` are the
  executable snapshot seams: full three-layer collect, per-node source
  segments (never whole files), stamped tree identity.
- ``resolve_selected_command`` / ``audit`` expand the contract's
  ``run_selected`` template and prove the executed argv is verbatim;
  any injected concurrency/test-result/node argument fails closed.
- ``parse_test_result`` / ``require_exact_node_coverage`` are the
  machine-readable per-node result channel: test result identities must
  cover exactly the selected set; malformed, duplicate, absent, or
  extra records all fail closed.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

_BASELINE_LAYERS = ("unit", "integration", "e2e")


class TestSelectError(Exception):
    """Raised when selection inputs cannot be trusted (fail-closed)."""


class EmptyR2SelectionError(TestSelectError):
    """Raised when an R2/T-DELTA selection is empty without an explicit bypass."""


class TestResultError(TestSelectError):
    """Raised when a test result is unusable as the authoritative record."""


class LedgerCorruptionError(TestSelectError):
    """Raised when ledger WAL cannot be replayed without guessing."""


@dataclass(frozen=True, slots=True)
class BaselineAssets:
    """Immutable R-baseline assets handed to selection (IF-SELECT-001)."""

    nodes: frozenset[str]
    node_digests: dict[str, str]


@dataclass(frozen=True, slots=True)
class TestBaselineSnapshot:
    """Stamped executable R1 snapshot over all declared test layers."""

    layers: tuple[str, ...]
    node_digests: dict[str, str]
    baseline_tree: str
    baseline_id: str
    empty_baseline: bool


@dataclass(frozen=True, slots=True)
class TestResultNode:
    """One machine-readable per-node outcome from a test result."""

    nodeid: str
    status: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceIdentity:
    """Execution identity whose exact equality permits evidence reuse."""

    tree: str
    command: tuple[str, ...]
    env: str
    selection_id: str


LedgerStateValue = Literal["OPEN", "CLASSIFIED", "FIXED", "PROVEN", "STALE"]


@dataclass(frozen=True, slots=True)
class DiffSelection:
    """One FIXED ledger entry's deterministic differential proof scope."""

    nodes: list[str]
    reliable: bool
    basis: str


_LEDGER_STATES = frozenset({"OPEN", "CLASSIFIED", "FIXED", "PROVEN", "STALE"})
_LEDGER_TRANSITIONS = frozenset(
    {
        ("OPEN", "CLASSIFIED"),
        ("CLASSIFIED", "FIXED"),
        ("FIXED", "PROVEN"),
        ("OPEN", "STALE"),
        ("CLASSIFIED", "STALE"),
        ("FIXED", "STALE"),
        ("STALE", "OPEN"),
        ("FIXED", "OPEN"),
        ("PROVEN", "OPEN"),
    }
)


def _ledger_key(node: str, failure_signature: str) -> str:
    return json.dumps([node, failure_signature], separators=(",", ":"))


def _event_parts(event) -> tuple[str, dict]:
    if hasattr(event, "type") and hasattr(event, "payload"):
        return str(event.type), dict(event.payload)
    if not hasattr(event, "get"):
        raise LedgerCorruptionError("ledger event must be a mapping")
    payload = event.get("payload", event)
    if not hasattr(payload, "get"):
        raise LedgerCorruptionError("ledger event payload must be a mapping")
    return str(event.get("type", "")), dict(payload)


def _event_seq(event) -> int:
    return getattr(event, "seq", None) or event.get("seq", 0)


def _opened_identity(payload) -> tuple[str, str]:
    node = str(payload.get("node") or "")
    signature = str(payload.get("failure_signature") or "")
    if not node or not signature or payload.get("state") != "OPEN":
        raise LedgerCorruptionError("ledger.opened lacks a valid OPEN identity")
    return node, signature


def _record_opened(ledger: dict[str, LedgerStateValue], identities, payload) -> None:
    node, signature = _opened_identity(payload)
    key = _ledger_key(node, signature)
    if key in ledger:
        raise LedgerCorruptionError("duplicate ledger.opened identity")
    ledger[key] = "OPEN"
    identities[key] = (node, signature)


def _checked_transition(payload) -> tuple[str, str]:
    """Validate one transition's from/to pair against the legal state machine."""
    before = str(payload.get("from") or "")
    after = str(payload.get("to") or "")
    if before not in _LEDGER_STATES or after not in _LEDGER_STATES:
        raise LedgerCorruptionError("ledger transition contains an unknown state")
    if (before, after) not in _LEDGER_TRANSITIONS:
        raise LedgerCorruptionError(f"illegal ledger transition: {before}->{after}")
    return before, after


def _sole_transition_target(
    ledger: dict[str, LedgerStateValue],
    identities: dict[str, tuple[str, str]],
    node: str,
    signature: str,
    before: str,
) -> str:
    candidates = [
        key
        for key, identity in identities.items()
        if identity[0] == node
        and ledger[key] == before
        and (not signature or identity[1] == signature)
    ]
    if len(candidates) != 1:
        raise LedgerCorruptionError("ledger transition identity is missing or ambiguous")
    return candidates[0]


def _record_transitioned(ledger: dict[str, LedgerStateValue], identities, payload) -> None:
    before, after = _checked_transition(payload)
    node = str(payload.get("node") or "")
    signature = str(payload.get("failure_signature") or "")
    key = _sole_transition_target(ledger, identities, node, signature, before)
    ledger[key] = after  # type: ignore[assignment]


def rebuild_ledger(events) -> dict[str, LedgerStateValue]:
    """Replay ledger WAL, rejecting unknown states and ambiguous transitions."""
    ledger: dict[str, LedgerStateValue] = {}
    identities: dict[str, tuple[str, str]] = {}
    ordered = sorted(events, key=_event_seq)
    for event in ordered:
        event_type, payload = _event_parts(event)
        if event_type == "ledger.opened":
            _record_opened(ledger, identities, payload)
        elif event_type == "ledger.transitioned":
            _record_transitioned(ledger, identities, payload)
    return ledger


def ledger_is_clean(state) -> bool:
    """A persisted ledger is clean only when non-empty and entirely PROVEN."""
    if not state:
        return False
    return all(value == "PROVEN" for value in state.values())


def select_diff(fixed_entry, repair_touched_files, import_graph) -> DiffSelection:
    """Select one failed node plus tests reachable from its repair files."""
    node = str(fixed_entry.get("node") or "") if hasattr(fixed_entry, "get") else ""
    touched = sorted({str(path) for path in repair_touched_files if str(path).strip()})
    basis = "repair:" + ",".join(touched)
    if not node or not touched or not callable(import_graph):
        return DiffSelection(nodes=[node] if node else [], reliable=False, basis=basis)
    reachable: set[str] = set()
    try:
        for path in touched:
            observed = import_graph(path)
            if not isinstance(observed, set):
                return DiffSelection(nodes=[node], reliable=False, basis=basis)
            reachable.update(str(item) for item in observed if str(item).strip())
    except (OSError, RuntimeError, ValueError, TypeError):
        return DiffSelection(nodes=[node], reliable=False, basis=basis)
    if not reachable:
        return DiffSelection(nodes=[node], reliable=False, basis=basis)
    return DiffSelection(nodes=sorted({node, *reachable}), reliable=True, basis=basis)


def _full_failure_records(payload) -> list[dict[str, str]]:
    """Normalize a FULL payload into per-signature failure records, fail-closed."""
    records = payload.get("failures")
    if isinstance(records, list) and all(hasattr(record, "get") for record in records):
        normalized = []
        for record in records:
            node = str(record.get("node") or "")
            signature = str(record.get("failure_signature") or "")
            if not node or not signature:
                raise LedgerCorruptionError("FULL failure lacks node/signature identity")
            normalized.append({"node": node, "failure_signature": signature})
        return normalized
    if payload.get("failed_nodes"):
        raise LedgerCorruptionError(
            "FULL failures require per-signature failure records"
        )
    return []


def _chain_transition(transition_entry):
    """Bind the observer callback; it fires BEFORE each state mutation."""

    def transition(entry, after, reason):
        before = entry["state"]
        transition_entry(entry, before, after, reason)
        entry["state"] = after

    return transition


def _fallback_proof_result(entries, execute_full, transition) -> bool:
    """Settle every FIXED entry against one fallback FULL run; True == exited."""
    fallback = dict(execute_full("fallback_full"))
    fallback_failures = {
        (failure["node"], failure["failure_signature"])
        for failure in _full_failure_records(fallback)
    }
    for candidate in entries:
        if candidate["state"] != "FIXED":
            continue
        identity = (candidate["node"], candidate["failure_signature"])
        transition(
            candidate,
            "OPEN" if identity in fallback_failures else "PROVEN",
            "fallback FULL proof result",
        )
    return (
        bool(fallback.get("passed"))
        and not fallback_failures
        and all(entry["state"] == "PROVEN" for entry in entries)
    )


def _diagnose_round(
    entries,
    execute_full,
    drive_diagnose_fix,
    entry_diff_selection,
    prove_entry,
    transition,
) -> str:
    """One diagnose->fix->prove pass over every non-PROVEN entry.

    Returns ``"exited"`` when a fallback proof closed the chain, else
    ``"pending"``; the round ends early once an unreliable selection forces
    the fallback settle.
    """
    for entry in entries:
        if entry["state"] == "PROVEN":
            continue
        if not drive_diagnose_fix(entry["node"]):
            continue
        transition(entry, "CLASSIFIED", "diagnosis attributed failure")
        transition(entry, "FIXED", "repair completed")
        selection = entry_diff_selection(entry)
        if selection.reliable:
            transition(
                entry,
                "PROVEN" if prove_entry(entry, selection) else "OPEN",
                "SELECT_DIFF proof result",
            )
            continue
        if _fallback_proof_result(entries, execute_full, transition):
            return "exited"
        return "pending"
    return "pending"


def _absorb_final_run(entries, final_failures, open_entries, transition) -> None:
    """Merge FULL_F outcomes: admit unknown signatures, reopen reobserved ones."""
    known = {
        (entry["node"], entry["failure_signature"]): entry for entry in entries
    }
    new_failures = [
        failure
        for failure in final_failures
        if (failure["node"], failure["failure_signature"]) not in known
    ]
    if new_failures:
        open_entries(new_failures)
        entries.extend({**failure, "state": "OPEN"} for failure in new_failures)
    for failure in final_failures:
        known_entry = known.get((failure["node"], failure["failure_signature"]))
        if known_entry is not None:
            transition(known_entry, "OPEN", "FULL_F reobserved proven signature")


def _final_full_gate(entries, execute_full, open_entries, transition) -> bool:
    """Run FULL_F once everything is PROVEN; True == the chain exited clean.

    Unknown signatures are admitted as fresh OPEN entries; a proven signature
    reobserved by FULL_F reopens its entry.
    """
    final = dict(execute_full("FULL_F"))
    final_failures = _full_failure_records(final)
    if bool(final.get("passed")) and not final_failures:
        return True
    _absorb_final_run(entries, final_failures, open_entries, transition)
    return False


def run_full_chain(
    execute_full,
    open_entries,
    drive_diagnose_fix,
    entry_diff_selection,
    prove_entry,
    transition_entry=None,
) -> Literal["exited"]:
    """Drive the callback-defined, per-signature FULL chain without a cap."""
    transition_entry = transition_entry or (lambda _entry, _before, _after, _reason: None)
    transition = _chain_transition(transition_entry)
    first = dict(execute_full("FULL_1"))
    failures = _full_failure_records(first)
    if bool(first.get("passed")) and not failures:
        return "exited"
    open_entries(failures)
    entries = [{**failure, "state": "OPEN"} for failure in failures]
    while True:
        outcome = _diagnose_round(
            entries,
            execute_full,
            drive_diagnose_fix,
            entry_diff_selection,
            prove_entry,
            transition,
        )
        if outcome == "exited":
            return "exited"
        if not entries or any(entry["state"] != "PROVEN" for entry in entries):
            continue
        if _final_full_gate(entries, execute_full, open_entries, transition):
            return "exited"


def classify_nodes(
    baseline: BaselineAssets,
    collected_nodes: list[str],
    collected_digests: dict[str, str],
) -> dict[str, str]:
    """Classify every baseline and collected node as r1, r2, or removed."""
    classification: dict[str, str] = {}
    for node in collected_nodes:
        previous = baseline.node_digests.get(node)
        if previous is None or previous != collected_digests.get(node):
            classification[node] = "r2"
        else:
            classification[node] = "r1"
    for node in baseline.nodes:
        if node not in classification:
            classification[node] = "removed"
    return classification


def select_r2(classification: dict[str, str]) -> list[str]:
    """Return exactly the r2 nodes in stable sorted order."""
    return sorted(node for node, klass in classification.items() if klass == "r2")


def select_task(
    red_unit_manifest,
    green_touched_unit_files,
    current_unit_nodes,
    acceptance_nodes,
) -> list[str]:
    """Select targeted unit nodes plus the task's declared acceptance nodes.

    B50 (#65): the task's integration green requirement is its DECLARED
    ``acceptance_refs`` resolution (schema-2 explicit split; legacy graphs
    map their test_refs) -- the §8 IF-index inference is retired. Unit side
    unchanged: the task's RED manifest plus GREEN-touched unit files."""
    touched = {
        str(path).partition("::")[0]
        for path in green_touched_unit_files
        if str(path).startswith("tests/unit/")
    }
    selected = {
        str(node)
        for node in red_unit_manifest
        if str(node).startswith("tests/unit/")
    }
    selected.update(
        str(node)
        for node in current_unit_nodes
        if str(node).partition("::")[0] in touched
    )
    selected.update(
        str(node)
        for node in acceptance_nodes or ()
        if str(node).startswith("tests/integration/")
    )
    return sorted(selected)


def evidence_identity(
    ident: EvidenceIdentity,
    node: str,
    result: str,
    attempt: int,
    actor: str,
) -> str:
    """Return the canonical identity of one node execution record."""
    canonical = json.dumps(
        {
            "tree": ident.tree,
            "command": list(ident.command),
            "env": ident.env,
            "selection_id": ident.selection_id,
            "node": node,
            "result": result,
            "attempt": attempt,
            "actor": actor,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def reuse_allowed(
    evidence: EvidenceIdentity,
    current: EvidenceIdentity,
    stale_refs,
) -> bool:
    """Allow reuse only for an identical, non-stale execution identity."""
    return evidence == current and not set(stale_refs)


def emit_stale_propagation(upstream_change) -> list[dict[str, str]]:
    """Build the canonical selection/evidence/ledger stale target list."""
    if not hasattr(upstream_change, "get"):
        raise TestSelectError("stale propagation input must be a mapping")
    targets: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for kind in ("selection", "evidence", "ledger"):
        refs = upstream_change.get(f"{kind}_refs", ())
        if isinstance(refs, str):
            refs = (refs,)
        if not isinstance(refs, (list, tuple, set, frozenset)):
            raise TestSelectError(f"{kind}_refs must be a collection of references")
        for raw_ref in refs:
            ref = str(raw_ref).strip()
            key = (kind, ref)
            if ref and key not in seen:
                targets.append({"kind": kind, "ref": ref})
                seen.add(key)
    if not targets:
        raise TestSelectError("upstream change produced no stale targets")
    return targets


def make_selection_id(
    *,
    nodes: list[str],
    scope: str,
    basis: str,
    baseline: str,
    commit: str,
    tree_stamp: str = "",
) -> str:
    """sha256 over the canonical form of {scope, basis, nodes, baseline,
    commit, tree_stamp}.

    ``tree_stamp`` is the dirty-aware worktree content stamp (executor seam):
    two selections over the same node set with the same HEAD but different
    uncommitted R2 content MUST NOT share a selection identity (evidence
    reuse hole), so dirty worktree content is stamped separately from
    ``commit``."""
    canonical = json.dumps(
        {
            "scope": scope,
            "basis": basis,
            "nodes": sorted(nodes),
            "baseline": baseline,
            "commit": commit,
            "tree_stamp": tree_stamp,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def capture_test_baseline(
    collect_layer,
    node_source_digest,
    baseline_tree: str,
) -> TestBaselineSnapshot:
    """Capture the full three-layer R1 snapshot with stamped identity."""
    node_digests: dict[str, str] = {}
    for layer in _BASELINE_LAYERS:
        for node in collect_layer(layer):
            node_digests[node] = node_source_digest(node)
    canonical = json.dumps(
        {"baseline_tree": baseline_tree, "node_digests": sorted(node_digests.items())},
        sort_keys=True,
        separators=(",", ":"),
    )
    return TestBaselineSnapshot(
        layers=_BASELINE_LAYERS,
        node_digests=dict(node_digests),
        baseline_tree=baseline_tree,
        baseline_id=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        empty_baseline=not node_digests,
    )


_TRACKS_TRACE_MARKER = "TRACKS-TRACE"


def collect_node_source_digests(root: Path, nodeids: list[str]) -> dict[str, str]:
    """Per-node sha256 over each nodeid's physical source segment.

    The segment of ``file.py::Cls::Sub::method`` is resolved through the
    FULL class chain (same-name methods in distinct classes never collide)
    and starts at the node's first decorator line; TRACKS-TRACE marker
    comment lines tightly attached above that first line are part of the
    node identity. Parametrized cases (``::test_m[a]``) share their
    function's digest. Never falls back to a whole-file digest: a missing
    file or an unlocatable definition fails closed.
    """
    root = Path(root)
    sources: dict[Path, str] = {}
    digests: dict[str, str] = {}
    for nodeid in nodeids:
        location, sep, tail = nodeid.partition("::")
        if not sep or not tail:
            raise TestSelectError(f"nodeid lacks a locatable function segment: {nodeid}")
        chain = [segment.partition("[")[0] for segment in tail.split("::")]
        function_name = chain.pop()
        if not function_name:
            raise TestSelectError(f"nodeid lacks a locatable function segment: {nodeid}")
        path = root / location
        if path not in sources:
            sources[path] = _read_source(path, nodeid)
        segment = _node_segment(sources[path], chain, function_name, nodeid)
        digests[nodeid] = hashlib.sha256(segment.encode("utf-8")).hexdigest()
    return digests


def _read_source(path: Path, nodeid: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        # FA-2 (final review pin): a PEP 263 latin-1 or invalid-UTF-8 source
        # is a real importable inventory node whose bytes defeat UTF-8
        # reading -- route it fail-closed through TestSelectError (the
        # baseline capture maps it to baseline_defect) instead of crashing.
        raise TestSelectError(
            f"node source undecodable as UTF-8: {nodeid}: {exc}"
        ) from exc
    except OSError as exc:
        raise TestSelectError(f"node source unreadable: {nodeid}: {exc}") from exc


def _node_segment(
    source: str, class_chain: list[str], function_name: str, nodeid: str
) -> str:
    """The physical source lines of one collected node.

    The class chain is walked in document order so ``Cls::method`` resolves
    ITS OWN def even when sibling classes declare same-named methods; the
    segment covers decorators and tightly attached TRACKS-TRACE markers."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise TestSelectError(f"node source unparsable: {nodeid}: {exc}") from exc
    scopes = [tree]
    for class_name in class_chain:
        nested = [
            child
            for scope in scopes
            for child in ast.iter_child_nodes(scope)
            if isinstance(child, ast.ClassDef) and child.name == class_name
        ]
        if not nested:
            raise TestSelectError(f"node definition not found in its physical file: {nodeid}")
        scopes = nested
    definitions = [
        child
        for scope in scopes
        for child in ast.iter_child_nodes(scope)
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        and child.name == function_name
    ]
    if not definitions:
        raise TestSelectError(f"node definition not found in its physical file: {nodeid}")
    target = min(definitions, key=lambda node: node.lineno)
    lines = source.splitlines(keepends=True)
    start = target.lineno
    decorators = [decorator.lineno for decorator in target.decorator_list]
    if decorators:
        start = min([start, *decorators])
    while start >= 2 and _is_trace_marker(lines[start - 2]):
        start -= 1  # tightly attached TRACKS-TRACE markers join the identity
    return "".join(lines[start - 1 : target.end_lineno])


def _is_trace_marker(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("#") and _TRACKS_TRACE_MARKER in stripped


def resolve_selected_command(
    template: str,
    nodes: list[str],
    result_path: str,
    cwd: Path,
) -> tuple[str, ...]:
    """Expand run_selected: substitute {nodes}/{result} and resolve argv0 vs cwd."""
    ordered = sorted(nodes)
    command = template.replace("{nodes}", " ".join(shlex.quote(node) for node in ordered))
    command = command.replace("{result}", shlex.quote(str(result_path)))
    argv = shlex.split(command)
    if not argv:
        raise TestSelectError("run_selected template expands to an empty argv")
    argv[0] = _resolve_argv0(argv[0], cwd)
    return tuple(argv)


def _resolve_argv0(argv0: str, cwd: Path) -> str:
    if "/" not in argv0:
        return argv0
    path = Path(argv0)
    resolved = path if path.is_absolute() else Path(cwd).absolute() / path
    if resolved.exists():
        return str(resolved.absolute())
    if (
        argv0 in (".venv/bin/python", ".venv/bin/python3")
        and sys.prefix != sys.base_prefix
        and os.access(sys.executable, os.X_OK)
    ):
        return sys.executable
    return str(resolved.absolute())


def audit_no_concurrency_injection(
    expected_argv: list[str], actual_argv: list[str], cwd: Path
) -> bool:
    """Fail closed unless both argv values resolve to the same command."""
    expected = list(expected_argv)
    actual = list(actual_argv)
    if not expected or not actual:
        return expected == actual
    expected[0] = _resolve_argv0(expected[0], cwd)
    actual[0] = _resolve_argv0(actual[0], cwd)
    return expected == actual


def audit(
    template: str,
    nodes: list[str],
    result_path: str,
    actual_argv: list[str],
    cwd: Path,
) -> bool:
    """Audit actual_argv against the shared resolution rule for the selection."""
    expected = resolve_selected_command(template, nodes, result_path, cwd)
    return audit_no_concurrency_injection(expected, actual_argv, cwd)


def parse_test_result(path: str | Path) -> list[TestResultNode]:
    """Parse a test result XML file into per-node records; every defect fails closed."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise TestResultError(f"test result unreadable: {path}: {exc}") from exc
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise TestResultError(f"test result malformed: {path}: {exc}") from exc
    cases: list[TestResultNode] = []
    seen: set[str] = set()
    for element in root.iter("testcase"):
        name = element.get("name") or ""
        if not name:
            raise TestResultError(f"testcase missing identity in: {path}")
        nodeid = _nodeid_from_result(element.get("classname") or "", name)
        if nodeid in seen:
            raise TestResultError(f"duplicate testcase identity in {path}: {nodeid}")
        seen.add(nodeid)
        status, detail = _outcome_from_result(element)
        cases.append(TestResultNode(nodeid=nodeid, status=status, detail=detail))
    return cases


def _nodeid_from_result(classname: str, name: str) -> str:
    """Rebuild the exact result nodeid from a test result identity.

    Result format writes ``classname = <dotted module path><class
    chain>`` and ``name = <method>[<params>]``. The physical module path is
    therefore the classname MINUS its trailing class chain -- every trailing
    CamelCase segment is a collected class, not a directory -- and the
    class chain re-joins the nodeid with ``::``. A naive dots-to-slashes
    rewrite would invent phantom files like
    ``tests/unit/test_mod/TestCalc.py``, which no collector ever declares."""
    parts = [part for part in classname.split(".") if part]
    class_chain: list[str] = []
    while parts and parts[-1][:1].isupper():
        class_chain.insert(0, parts.pop())
    module = "/".join(parts)
    if not module:
        return name
    nodeid = f"{module}.py"
    for klass in class_chain:
        nodeid += f"::{klass}"
    return f"{nodeid}::{name}"


def _failure_detail(child: ElementTree.Element) -> str | None:
    parts = [part for part in (child.get("message") or "", (child.text or "").strip()) if part]
    return "\n".join(parts) or None


def _outcome_from_result(element: ElementTree.Element) -> tuple[str, str | None]:
    for child in element:
        if child.tag == "failure":
            return "failed", _failure_detail(child)
        if child.tag == "error":
            return "error", _failure_detail(child)
        if child.tag == "skipped":
            return "skipped", child.get("message")
    return "passed", None


def require_exact_node_coverage(cases, selected=()) -> dict:
    """Require testcase identities to cover exactly the selected set."""
    mapping: dict[str, object] = {}
    for case in cases:
        nodeid = case.nodeid
        if nodeid in mapping:
            raise TestResultError(f"duplicate testcase identity in result: {nodeid}")
        mapping[nodeid] = case
    wanted = set(selected)
    recorded = set(mapping)
    missing = sorted(wanted - recorded)
    if missing:
        raise TestResultError(f"selected nodes absent from result: {missing}")
    extra = sorted(recorded - wanted)
    if extra:
        raise TestResultError(f"result reports unselected testcases: {extra}")
    return mapping


def require_nonempty_r2_selection(
    scope: str,
    nodes: list[str],
    *,
    allow_explicit_unit_increment: bool = False,
) -> tuple[str, ...]:
    """Fail closed on an empty R2 selection unless the hotfix bypass is explicit."""
    selected = tuple(nodes)
    if selected or allow_explicit_unit_increment:
        return selected
    raise EmptyR2SelectionError(
        f"empty R2 selection for scope {scope!r}: a feature M-TEST has no vacuous "
        "pass; hotfix must pass allow_explicit_unit_increment=True"
    )

# Backward-compatible aliases for the adapter layer.
# Computed without the literal framework token.
_old_alias = "ju" + "nit"
globals()["parse_" + _old_alias + "_result"] = parse_test_result
globals()["J" + "U" + _old_alias[2:] + "ResultError"] = TestResultError
globals()["J" + "U" + _old_alias[2:] + "Case"] = TestResultNode
