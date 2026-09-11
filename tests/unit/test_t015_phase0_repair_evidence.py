"""T-015 RED (REVISE round 9): Phase 0 must ground every claim in real evidence.

Prism (PRISM-V07-R1-02/R1-03, hardened through R2..R5, R6-01/02/03):

- A marker-only baseline gap (planned v0.6 AC whose node IS collected with a
  real identity digest but has no persisted evidence) must be REPAIRED into a
  real collected node carrying audit evidence (a ``phase0.baseline_repaired``
  with ``ac`` version-qualified to the baseline, ``bound_node.{node_id,digest}``,
  ``selection_id``, ``evidence_id``, ``command_echo``, ``status="repaired"``)
  -- or the run parks; it must never silently proceed to a repair-less seal.
- ``phase0.guard_hardened`` must not self-declare ``passed`` with no real
  ``guard.parity`` evidence. When a host carries NO registry/parity evidence a
  conforming fail-closed implementation must park, never fabricate a pass.

Each fixture is executable (a real contract loads; the collect/digest seam is
at the I/O boundary), reachable (approved and planned AC keys are identical
bare ids so ``scan_trace_gaps`` actually selects the gap), and greenable by a
conforming GREEN (the repair/parity evidence it asserts is exactly what GREEN
must emit). Assertions fail on contract tokens, never assembly errors.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.helpers import git_repo
from tracks import paths
from tracks.adapters import base as _adapter_base
from tracks.executor.executor import Executor
from tracks.executor.test_select import (
    make_selection_id,
    resolve_selected_command,
)
from tracks.kernel.events import Command
from tracks.project import load_contract
from tracks.store import Store

# Bare baseline AC: the acceptance scan (`_known_ac_ids` -> AC-FR0250-03) and
# the §8 plan parse both see the SAME bare id, so scan_trace_gaps reaches the
# gap (R5-02/R6-02 reachability). The *public* repair identity is the
# version-qualified form (interfaces §3 shows ac=AC-FR0250-03@v0.6).
_BASELINE_AC = "AC-FR0250-03"
_BASELINE_AC_QUALIFIED = f"{_BASELINE_AC}@v0.6"
_MARKER_NODE = "tests/integration/test_baseline.py::test_marker_closure"
_MARKER_DIGEST = "d" * 64


def _contract() -> str:
    return (
        "[unit]\nframework='pytest'\npaths=['tests/unit/']\n"
        "collect='.venv/bin/python -m pytest --collect-only -q tests/unit/'\n"
        "run='.venv/bin/python -m pytest tests/unit/ --tb=short -q -n 4 "
        "--dist loadscope --junitxml={result}'\n"
        "run_selected='.venv/bin/python -m pytest {nodes} --tb=short -q -n 4 "
        "--dist loadscope --junitxml={result}'\ncwd='.'\n\n"
        "[integration]\nframework='pytest'\npaths=['tests/integration/']\n"
        "collect='.venv/bin/python -m pytest --collect-only -q tests/integration/'\n"
        "run='.venv/bin/python -m pytest tests/integration/ --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\n"
        "run_selected='.venv/bin/python -m pytest {nodes} --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\ncwd='.'\n\n"
        "[e2e]\nframework='pytest'\npaths=['tests/e2e/']\n"
        "collect='.venv/bin/python -m pytest --collect-only -q tests/e2e/'\n"
        "run='.venv/bin/python -m pytest tests/e2e/ --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\n"
        "run_selected='.venv/bin/python -m pytest {nodes} --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\ncwd='.'\n\n"
        "[nightly]\nschedule='0 3 * * *'\nworkflow='.github/workflows/nightly.yml'\n"
        "job='nightly'\nlayers=['unit','integration','e2e']\npurpose='r'\n"
        "[adapter]\nid='reference-pytest'\nprotocol='tracks-test-result'\nversion=1\n"
    )


def _host(tmp_path: Path, *, planned_node: bool = True) -> Path:
    """Host with a contract-valid project.toml, a v0.6 baseline acceptance +
    §8 plan row (bare AC = reachable), and a seeded v0.7 run at M-TEST entry.
    No §4.2 guard registry is seeded: the guard-parity path is pinned through
    its fail-closed branch (missing evidence must not fabricate a pass)."""
    repo = git_repo(tmp_path, gitignore=True)
    contract = paths.project_toml_path(paths.tracks_home(repo))
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text(_contract(), encoding="utf-8")
    base = paths.projects_dir(paths.tracks_home(repo)) / "v0.6"
    base.mkdir(parents=True, exist_ok=True)
    (base / "acceptance.md").write_text(
        f"# v0.6 acceptance\n\n### {_BASELINE_AC}\n\n- baseline trace gap.\n",
        encoding="utf-8",
    )
    target = _MARKER_NODE if planned_node else "-"
    (base / "test-plan.md").write_text(
        "## 8. AC Coverage\n\n| # | AC | Layer | Test | IF |\n|:--|:--|:--|:--|:--|\n"
        f"| 1 | {_BASELINE_AC} | integration | {target} | IF-AUTH-001 |\n",
        encoding="utf-8",
    )
    (repo / "tests" / "integration").mkdir(parents=True, exist_ok=True)
    marker_file = repo / _MARKER_NODE.split("::", 1)[0]
    marker_file.parent.mkdir(parents=True, exist_ok=True)
    marker_file.write_text("def test_marker_closure():\n    assert True\n", encoding="utf-8")
    return repo


def _executor(repo: Path) -> Executor:
    load_contract(Path(repo))  # must not raise (placeholder validity)
    store = Store(paths.tracks_home(repo))
    store.append("RUN", "v0.7", "story.requested", {"raw_chars": 1})
    store.append("RUN", "v0.7", "stage.entered", {"stage": "M-TEST"})
    return Executor(store, repo, "RUN")


def _seed_collection(executor, monkeypatch, node_layer, node_digests) -> None:
    """Adapter/run seam at the I/O boundary: a real collect inventory
    ({node: layer}) + per-node digest map, plus a **recording reference
    adapter** whose ``run_selected`` records the exact argv it is given and
    whose ``normalize_result`` returns valid ``TestRunResult`` objects derived
    from that recorded selection. This makes GREEN's repair selection
    executable (no shell-out) AND makes any ``selection_id`` / ``evidence_id``
    / ``command_echo`` the phase0 handler emits provably bound to a real
    selected execution -- not forgeable canned identity strings."""
    monkeypatch.setattr(
        executor,
        "_collect_all_declared_layers",
        lambda **kw: (dict(node_layer), None),
    )
    monkeypatch.setattr(
        "tracks.executor.executor.collect_node_source_digests",
        lambda repo_path, nodes=None: dict(node_digests),
    )

    recorded = {"argv": None, "nodes": []}

    class _RecordingAdapter:
        adapter_id = "reference-pytest"
        protocol = "tracks-test-result"
        protocol_version = 1

        def collect(self, collect_command, layer, cwd):
            return []

        def run_selected(self, command_template, nodes, result_path, cwd):
            recorded["argv"] = resolve_selected_command(
                command_template, list(nodes), str(result_path), Path(cwd)
            )
            recorded["nodes"] = list(nodes)
            return recorded["argv"]

        def normalize_result(self, result_path, selected):
            return [
                _adapter_base.TestRunResult(node_id=n, status="passed", detail=None)
                for n in recorded["nodes"]
            ]

    monkeypatch.setattr(
        "tracks.adapters.base.resolve_adapter",
        lambda *a, **k: _RecordingAdapter(),
    )

    def _test_result_nodes(result_path):
        from tracks.executor.test_select import TestResultNode

        return [
            TestResultNode(nodeid=n, status="passed", detail=None)
            for n in recorded["nodes"]
        ]

    monkeypatch.setattr(
        "tracks.executor.test_execute.parse_test_result", _test_result_nodes
    )
    return recorded


def test_contract_is_loadable_and_placeholders_valid(tmp_path):
    """R2-01/R3-03: the fixture contract must load (real run/run_selected with
    {nodes}/{result} placeholders) so conforming GREEN wiring runs."""
    repo = _host(tmp_path)
    contract = load_contract(Path(repo))
    assert contract.integration.run_selected.count("{nodes}") == 1
    assert contract.integration.run_selected.count("{result}") == 1


# R1-02 / R5-02 / R6-02 / R7-01 / R7-02: repair evidence must be DERIVED from
# a real selected execution, not fabricated identity strings.
def test_marker_repair_records_full_evidence(tmp_path, monkeypatch):
    """A repairable v0.6 marker gap must be repaired into a phase0.baseline_
    repaired event whose ``ac`` is version-qualified and whose selection/
    evidence identity and command echo are provably bound to the ACTUAL
    adapter-selected execution of the repaired node -- not canned values."""
    repo = _host(tmp_path, planned_node=True)
    executor = _executor(repo)
    recorded = _seed_collection(
        executor, monkeypatch,
        node_layer={_MARKER_NODE: "integration"},
        node_digests={_MARKER_NODE: _MARKER_DIGEST},
    )
    executor._do_phase0_validate(
        Command(kind="phase0_validate", command_id="C-P0"),
        executor.store.state("RUN"),
        None,
        False,
    )
    events = list(executor.store.events("RUN"))
    repaired = [
        ev
        for ev in events
        if ev.type == "phase0.baseline_repaired"
        and ev.payload.get("ac") == _BASELINE_AC_QUALIFIED
    ]
    assert repaired, (
        f"baseline {_BASELINE_AC} must be repaired (ac={_BASELINE_AC_QUALIFIED!r}) "
        f"before any seal; got {[e.type for e in events]}"
    )
    rc = repaired[0].payload
    assert rc.get("status") == "repaired", rc
    node = rc.get("bound_node") or {}
    assert node.get("node_id") == _MARKER_NODE, node
    assert node.get("digest") == _MARKER_DIGEST, node
    # Anti-forgery (R7-01): the repair MUST have actually executed the marker
    # node through the adapter; otherwise there is no selection to cite.
    assert recorded["argv"] is not None, "repair must have run the marker selection"
    assert _MARKER_NODE in recorded["nodes"], recorded
    # The emitted identity must be the deterministic sha256 over the selection
    # that was actually executed (make_selection_id semantics), and the command
    # echo must be the exact argv the adapter ran -- not any non-empty string.
    assert rc.get("selection_id") == make_selection_id(
        nodes=recorded["nodes"],
        scope="r2_delta",
        basis="delta-declaration",
        baseline="v0.6",
        commit="",
        tree_stamp="",
    ), rc
    assert rc.get("evidence_id"), rc
    assert list(rc.get("command_echo") or ()) == list(recorded["argv"]), (
        f"command_echo must equal the adapter-executed argv: {rc.get('command_echo')}"
    )


# R1-03 / R2-04 / R6-03 / R7-03: no registry/guard evidence must FAIL CLOSED
# with the contractual blocked reason, never self-declare pass nor seal.
def test_guard_hardening_failcloses_without_real_parity_evidence(
    tmp_path, monkeypatch
):
    """A host with NO §4.2 registry/guard evidence must park with a contractual
    ``phase0.blocked`` (Phase0BlockReason), not fabricate a passed
    guard_hardened or a seal."""
    repo = _host(tmp_path, planned_node=True)
    executor = _executor(repo)
    _seed_collection(
        executor, monkeypatch,
        node_layer={_MARKER_NODE: "integration"},
        node_digests={_MARKER_NODE: _MARKER_DIGEST},
    )
    executor._do_phase0_validate(
        Command(kind="phase0_validate", command_id="C-P0"),
        executor.store.state("RUN"),
        None,
        False,
    )
    events = list(executor.store.events("RUN"))
    # A self-declared passed guard_hardened with no real parity is illegal.
    guard = next((ev for ev in events if ev.type == "phase0.guard_hardened"), None)
    if guard is not None:
        assert guard.payload.get("status") != "passed", (
            f"guard_hardened must not self-declare passed without parity: {guard.payload}"
        )
    # The ONLY legal terminal without registry/parity evidence is a blocked
    # event carrying the contractual Phase0BlockReason (not an absent terminal,
    # not an unrelated early block).
    blocked = [ev for ev in events if ev.type == "phase0.blocked"]
    assert blocked, (
        "PR1-03/R2-04/R6-03/R7-03: no registry/guard evidence must terminate "
        f"with phase0.blocked; got {[e.type for e in events]}"
    )
    reason = blocked[-1].payload.get("reason")
    assert reason in (
        "guard_registry_invalid",
        "identity_unreachable",
        "baseline_unresolvable",
        "collect_failed",
        "coverage_below_threshold",
    ), f"phase0.blocked reason is not a contractual Phase0BlockReason: {reason!r}"
    assert not any(ev.type == "phase0.sealed" for ev in events), (
        "seal reached with no real registry/guard evidence"
    )


# R1-02 / R2-04: unreachable planned target must block, not fabricate evidence.
def test_seal_requires_real_evidence_not_fabrication(tmp_path, monkeypatch):
    """A plan row that cites a node which is not actually collectable must
    block (identity unreachable) -- a bare early block is the legal no-seal,
    never a fabricated sealed event."""
    repo = _host(tmp_path, planned_node=True)  # plan cites the node...
    executor = _executor(repo)
    # ...but nothing is actually collectable -> identity_unreachable -> blocked.
    _seed_collection(executor, monkeypatch, node_layer={}, node_digests={})
    executor._do_phase0_validate(
        Command(kind="phase0_validate", command_id="C-P0"),
        executor.store.state("RUN"),
        None,
        False,
    )
    kinds = [
        ev.type
        for ev in executor.store.events("RUN")
        if ev.type.startswith("phase0.")
    ]
    assert "phase0.blocked" in kinds, f"must block on unreachable target: {kinds}"
    assert "phase0.sealed" not in kinds
