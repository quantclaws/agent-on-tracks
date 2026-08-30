"""First reference host adapter declarations (IF-ADAPTER-002).

The reference adapter reproduces the v0.6 pytest collect/run/normalize path
with the same semantics (AC-FR0264-02): it reuses the exact v0.6 machinery for
``--collect-only -q`` node parsing, ``{nodes}``/``{result}`` template
expansion, strict JUnit XML parsing and exact per-node coverage.  Malformed or
inexact results fail closed with ``AdapterResultError`` (AC-FR0264-03).
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path

from tracks.adapters.base import Adapter, AdapterResultError, Layer, TestNode, TestRunResult
from tracks.executor.helpers import parse_collected_nodes
from tracks.executor.test_select import (
    JUnitResultError,
    TestSelectError,
    collect_node_source_digests,
    parse_junit_result,
    resolve_selected_command,
)


class ReferencePytestAdapter(Adapter):
    adapter_id = "reference-pytest"
    protocol = "tracks-test-result"
    protocol_version = 1

    def collect(self, collect_command: str, layer: Layer, cwd: Path) -> list[TestNode]:
        return collect_reference_nodes(collect_command, layer, cwd)

    def run_selected(
        self,
        command_template: str,
        nodes: Sequence[str],
        result_path: Path,
        cwd: Path,
    ) -> tuple[str, ...]:
        return resolve_reference_command(command_template, nodes, result_path, cwd)

    def normalize_result(
        self, result_path: Path, selected: Sequence[str]
    ) -> list[TestRunResult]:
        return normalize_reference_result(result_path, selected)


def collect_reference_nodes(
    collect_command: str, layer: Layer, cwd: Path
) -> list[TestNode]:
    """Run the collect command and map collected node ids to ``TestNode``s.

    Reproduces the v0.6 ``--collect-only -q`` parse (one node id per line with
    a ``::`` separator): each node carries its ``layer`` and the sha256 digest
    of its physical source segment (``collect_node_source_digests``).
    """
    argv = shlex.split(collect_command)
    if not argv:
        raise AdapterResultError("collect command expands to an empty argv")
    proc = subprocess.run(
        argv,
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise AdapterResultError(
            f"collect command failed (exit {proc.returncode}): {collect_command}"
        )
    node_ids = parse_collected_nodes(proc.stdout)
    digests = collect_node_source_digests(cwd, node_ids)
    return [TestNode(node_id=nid, layer=layer, source_digest=digests[nid]) for nid in node_ids]


def resolve_reference_command(
    command_template: str,
    nodes: Sequence[str],
    result_path: Path,
    cwd: Path,
) -> tuple[str, ...]:
    """Expand ``{nodes}``/``{result}`` and resolve argv0 (v0.6 parity).

    The template must embed both ``{nodes}`` and ``{result}``; a missing
    placeholder is a contract defect the Runtime must never guess at
    (AC-FR0264-02/03).
    """
    if "{nodes}" not in command_template or "{result}" not in command_template:
        raise ValueError(
            "run_selected template must embed {nodes} and {result} exactly "
            "(no Runtime-injected flags)"
        )
    if command_template.count("{result}") != 1:
        raise ValueError("run_selected template must embed {result} exactly once")
    try:
        return resolve_selected_command(
            command_template,
            list(nodes),
            str(result_path),
            cwd,
        )
    except TestSelectError as exc:
        raise AdapterResultError(str(exc)) from exc


def normalize_reference_result(
    result_path: Path, selected: Sequence[str]
) -> list[TestRunResult]:
    """Parse the strict JUnit ``{result}`` file into per-node TestRunResult.

    Exact coverage is REQUIRED: a result reporting unselected testcases, or
    missing selected nodes, or carrying malformed/duplicate identities, fails
    closed with ``AdapterResultError`` (AC-FR0264-03).  The v0.6 JUnit parser
    rebuilds nodeids as ``path.py::Test::method`` from the ``classname`` /
    ``name`` attributes; callers select nodes by the exact nodeid they
    collected (e.g. ``t::test_a``), so coverage is matched on the comparable
    suffix and results carry the CALLER's nodeid -- the normalized result stays
    the versionsed ``tracks-test-result`` surface (AC-FR0264-02).
    """

    def _norm(nodeid: str) -> str:
        # t.py::test_a -> t::test_a  (drop the .py from the module segment)
        module, sep, rest = nodeid.rpartition("::")
        if sep and module.endswith(".py"):
            return module[: -len(".py")] + sep + rest
        return nodeid

    try:
        cases = parse_junit_result(result_path)
    except (JUnitResultError, OSError, UnicodeError) as exc:
        raise AdapterResultError(str(exc)) from exc
    by_norm: dict[str, object] = {}
    for case in cases:
        key = _norm(case.nodeid)
        if key in by_norm:
            raise AdapterResultError(f"duplicate node identity in result: {key}")
        by_norm[key] = case
    selected_list = list(selected)
    recorded = set(by_norm)
    wanted = set(selected_list)
    missing = sorted(wanted - recorded)
    if missing:
        raise AdapterResultError(f"selected nodes absent from result: {missing}")
    extra = sorted(recorded - wanted)
    if extra:
        raise AdapterResultError(f"result reports unselected testcases: {extra}")
    results: list[TestRunResult] = []
    for node_id in selected_list:
        case = by_norm[node_id]
        results.append(
            TestRunResult(
                node_id=node_id,
                status=case.status,
                detail=case.detail,
            )
        )
    return results
