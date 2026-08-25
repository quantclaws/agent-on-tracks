"""IF-SELECT-001/002 + IF-RUNCONTRACT-001 pure-function contracts (D-41 Slice A RED).

Pins the executor/test_select.py selection semantics exactly as specified in
interfaces.md §1j/§1m (R6, v5; v6 result channel): deterministic per-node digest
classification (R2 = new ∨ digest-changed; unchanged sibling in the same changed
file stays r1; REMOVED is a fail-closed class, never silently dropped), the
executable R1 snapshot capture seam (test.baseline_captured), SELECT_R2 selection
identity, the empty-R2 fail-closed gate with the hotfix explicit-increment bypass,
the concurrency/junit-injection audit comparison over explicitly resolved argv,
and the machine-readable per-node result seam ({result} JUnit XML parsing +
exact-set coverage, all failure modes fail-closed).

D-41 Slice A RED: the module does not exist yet, so every test fails on a
guarded assertion naming the missing contract symbol (uniform
assertion_failure, no assembly/import noise).
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys

import pytest

_DIGEST_PAIR_FILE = "tests/unit/test_digest_pair.py"


def _digest_pair_source(second_body: str) -> str:
    """One physical test file with two sibling functions; only the second
    function's body is parameterized so an edit touches a single node's
    source segment."""
    return (
        "def test_first_sibling():\n"
        "    assert 1 + 1 == 2\n"
        "\n"
        "\n"
        "def test_second_sibling():\n"
        f"    {second_body}\n"
        "\n"
        "\n"
        "def test_shared():\n"
        '    """Parametrized by the fixture id space, not distinct defs."""\n'
        "    assert True\n"
    )


def _test_select_module():
    spec = importlib.util.find_spec("tracks.executor.test_select")
    assert spec is not None, (
        "tracks.executor.test_select module missing: IF-SELECT-001/002 and "
        "IF-RUNCONTRACT-001 selection semantics are not implemented"
    )
    import tracks.executor.test_select as mod

    return mod


def _require(mod, name):
    obj = getattr(mod, name, None)
    assert obj is not None, f"tracks.executor.test_select.{name} missing (D-41 Slice A contract)"
    return obj


def _make_baseline(mod, nodes_to_digest):
    BaselineAssets = _require(mod, "BaselineAssets")
    return BaselineAssets(
        nodes=frozenset(nodes_to_digest),
        node_digests=dict(nodes_to_digest),
    )


# IF-SELECT-001: R2/T-DELTA is exactly new nodes plus digest-changed nodes.
def test_classify_new_and_digest_changed_nodes_are_r2():
    mod = _test_select_module()
    classify_nodes = _require(mod, "classify_nodes")
    baseline = _make_baseline(
        mod,
        {
            "tests/integration/test_a.py::test_hist": "d0",
            "tests/integration/test_a.py::test_touched": "d1",
        },
    )
    classification = classify_nodes(
        baseline,
        [
            "tests/integration/test_a.py::test_hist",
            "tests/integration/test_a.py::test_touched",
            "tests/integration/test_new.py::test_fresh",
        ],
        {
            "tests/integration/test_a.py::test_hist": "d0",
            "tests/integration/test_a.py::test_touched": "d1changed",
            "tests/integration/test_new.py::test_fresh": "d2",
        },
    )
    assert classification["tests/integration/test_a.py::test_touched"] == "r2"
    assert classification["tests/integration/test_new.py::test_fresh"] == "r2"


# IF-SELECT-001: unchanged sibling in the same changed file remains r1
# (per-node digest; whole-file digest classification is the ce_wholefile_digest
# defect and must misclassify nothing here).
def test_unchanged_sibling_in_changed_file_stays_r1():
    mod = _test_select_module()
    classify_nodes = _require(mod, "classify_nodes")
    sibling = "tests/integration/test_pair.py::test_sibling"
    changed = "tests/integration/test_pair.py::test_changed"
    baseline = _make_baseline(mod, {sibling: "s1", changed: "c1"})
    classification = classify_nodes(
        baseline,
        [sibling, changed],
        {sibling: "s1", changed: "c2"},
    )
    assert classification[sibling] == "r1"
    assert classification[changed] == "r2"


# IF-SELECT-001: baseline node missing from current collect is the REMOVED
# fail-closed class, never a silent deregistration.
def test_removed_baseline_node_classified_removed():
    mod = _test_select_module()
    classify_nodes = _require(mod, "classify_nodes")
    gone = "tests/integration/test_old.py::test_gone"
    kept = "tests/integration/test_keep.py::test_kept"
    baseline = _make_baseline(mod, {gone: "g1", kept: "k1"})
    classification = classify_nodes(baseline, [kept], {kept: "k1"})
    assert classification[gone] == "removed"
    assert classification[kept] == "r1"


# IF-SELECT-001: classification is deterministic -- same inputs in any
# iteration order produce the identical mapping.
def test_classify_deterministic_same_input_any_order():
    mod = _test_select_module()
    classify_nodes = _require(mod, "classify_nodes")
    sibling = "tests/integration/test_pair.py::test_sibling"
    changed = "tests/integration/test_pair.py::test_changed"
    fresh = "tests/integration/test_pair.py::test_fresh"
    baseline = _make_baseline(mod, {sibling: "s1", changed: "c1"})
    digests = {sibling: "s1", changed: "c2", fresh: "f1"}
    first = classify_nodes(baseline, [sibling, changed, fresh], digests)
    second = classify_nodes(baseline, [fresh, sibling, changed], dict(reversed(list(digests.items()))))
    assert first == second


# IF-SELECT-002: SELECT_R2 returns exactly the r2 nodes, stably sorted;
# r1 and removed nodes are never selected.
def test_select_r2_returns_sorted_r2_only():
    mod = _test_select_module()
    select_r2 = _require(mod, "select_r2")
    classification = {
        "tests/integration/test_b.py::test_two": "r2",
        "tests/integration/test_a.py::test_one": "r1",
        "tests/integration/test_c.py::test_three": "r2",
        "tests/integration/test_old.py::test_gone": "removed",
    }
    assert select_r2(classification) == [
        "tests/integration/test_b.py::test_two",
        "tests/integration/test_c.py::test_three",
    ]


# IF-SELECT-002 + B50 (#65): SELECT_TASK is exactly targeted unit nodes plus
# the task's DECLARED acceptance nodes (the gate resolves acceptance_refs
# fail-closed upstream in _acceptance_anchors -- see test_m_impl_runtime.py).
# Here select_task enforces the layer boundary: integration acceptance nodes
# are admitted, e2e never, unrelated unit nodes never.
def test_select_task_composes_targeted_unit_and_declared_acceptance_only():
    mod = _test_select_module()
    select_task = _require(mod, "select_task")
    selected = select_task(
        red_unit_manifest=["tests/unit/test_a.py::test_red"],
        green_touched_unit_files=["tests/unit/test_b.py"],
        current_unit_nodes=[
            "tests/unit/test_a.py::test_red",
            "tests/unit/test_b.py::test_green_one",
            "tests/unit/test_b.py::test_green_two",
            "tests/unit/test_other.py::test_unrelated",
        ],
        acceptance_nodes=[
            "tests/integration/test_one.py::test_if_one",
            "tests/e2e/test_one.py::test_must_not_leak",
        ],
    )
    assert selected == [
        "tests/integration/test_one.py::test_if_one",
        "tests/unit/test_a.py::test_red",
        "tests/unit/test_b.py::test_green_one",
        "tests/unit/test_b.py::test_green_two",
    ]


# IF-SELECT-002/NFR-0130: selection identity is sha256 over the canonical
# form of {scope, basis, nodes, baseline, commit}; deterministic, sensitive to
# every field, and invariant to node iteration order (canonical json).
def test_make_selection_id_canonical_and_field_sensitive():
    mod = _test_select_module()
    make_selection_id = _require(mod, "make_selection_id")
    base = {"scope": "r2_delta", "basis": "delta-declaration", "baseline": "b-commit", "commit": "w-commit"}
    id_ab = make_selection_id(nodes=["tests/t.py::test_a", "tests/t.py::test_b"], **base)
    id_ba = make_selection_id(nodes=["tests/t.py::test_b", "tests/t.py::test_a"], **base)
    assert id_ab == id_ba
    assert id_ab == make_selection_id(nodes=["tests/t.py::test_a", "tests/t.py::test_b"], **base)
    changed_scope = make_selection_id(
        nodes=["tests/t.py::test_a", "tests/t.py::test_b"],
        scope="task_if",
        basis="delta-declaration",
        baseline="b-commit",
        commit="w-commit",
    )
    changed_basis = make_selection_id(
        nodes=["tests/t.py::test_a", "tests/t.py::test_b"],
        scope="r2_delta",
        basis="other",
        baseline="b-commit",
        commit="w-commit",
    )
    changed_commit = make_selection_id(
        nodes=["tests/t.py::test_a", "tests/t.py::test_b"],
        scope="r2_delta",
        basis="delta-declaration",
        baseline="b-commit",
        commit="other",
    )
    assert len({id_ab, changed_scope, changed_basis, changed_commit}) == 4


# IF-EVIDENCE-001: evidence identity is deterministic over the complete
# execution tuple, and reuse is allowed only when every tuple field is equal
# and neither identity is explicitly stale.
def test_evidence_identity_and_reuse_are_strict_and_deterministic():
    mod = _test_select_module()
    EvidenceIdentity = _require(mod, "EvidenceIdentity")
    evidence_identity = _require(mod, "evidence_identity")
    reuse_allowed = _require(mod, "reuse_allowed")
    ident = EvidenceIdentity(
        tree="tree-1",
        command=("pytest", "tests/unit/test_a.py::test_a"),
        env="env-1",
        selection_id="selection-1",
    )
    evidence_id = evidence_identity(
        ident, "tests/unit/test_a.py::test_a", "passed", 1, "runtime"
    )
    assert evidence_id == evidence_identity(
        ident, "tests/unit/test_a.py::test_a", "passed", 1, "runtime"
    )
    assert reuse_allowed(ident, ident, set())
    assert not reuse_allowed(ident, ident, {ident.selection_id})
    assert not reuse_allowed(ident, ident, {"related-evidence-id"})
    changed = EvidenceIdentity(
        tree="tree-2",
        command=ident.command,
        env=ident.env,
        selection_id=ident.selection_id,
    )
    assert not reuse_allowed(ident, changed, set())


def test_stale_propagation_targets_are_canonical_and_deduplicated():
    mod = _test_select_module()
    emit_stale_propagation = _require(mod, "emit_stale_propagation")
    assert emit_stale_propagation(
        {
            "selection_refs": ["selection-a", "selection-a"],
            "evidence_refs": {"evidence-b"},
            "ledger_refs": (),
        }
    ) == [
        {"kind": "selection", "ref": "selection-a"},
        {"kind": "evidence", "ref": "evidence-b"},
    ]


def test_stale_propagation_fails_closed_without_targets():
    mod = _test_select_module()
    emit_stale_propagation = _require(mod, "emit_stale_propagation")
    with pytest.raises(mod.TestSelectError, match="no stale targets"):
        emit_stale_propagation({})


def test_ledger_rebuild_closed_transitions_and_clean_predicate():
    mod = _test_select_module()
    rebuild_ledger = _require(mod, "rebuild_ledger")
    ledger_is_clean = _require(mod, "ledger_is_clean")
    events = [
        {
            "seq": 1,
            "type": "ledger.opened",
            "payload": {
                "node": "tests/unit/test_a.py::test_a",
                "failure_signature": "sig-a",
                "state": "OPEN",
            },
        },
        *[
            {
                "seq": index,
                "type": "ledger.transitioned",
                "payload": {
                    "node": "tests/unit/test_a.py::test_a",
                    "failure_signature": "sig-a",
                    "from": before,
                    "to": after,
                },
            }
            for index, (before, after) in enumerate(
                [
                    ("OPEN", "CLASSIFIED"),
                    ("CLASSIFIED", "FIXED"),
                    ("FIXED", "PROVEN"),
                    ("PROVEN", "OPEN"),
                    ("OPEN", "STALE"),
                    ("STALE", "OPEN"),
                    ("OPEN", "CLASSIFIED"),
                    ("CLASSIFIED", "FIXED"),
                    ("FIXED", "OPEN"),
                    ("OPEN", "CLASSIFIED"),
                    ("CLASSIFIED", "FIXED"),
                    ("FIXED", "PROVEN"),
                ],
                start=2,
            )
        ],
    ]
    rebuilt = rebuild_ledger(events)
    assert list(rebuilt.values()) == ["PROVEN"]
    assert ledger_is_clean(rebuilt)
    assert not ledger_is_clean({})
    assert not ledger_is_clean({"entry": "STALE"})


def test_ledger_rebuild_rejects_illegal_and_ambiguous_transitions():
    mod = _test_select_module()
    rebuild_ledger = _require(mod, "rebuild_ledger")
    opened = {
        "type": "ledger.opened",
        "payload": {"node": "tests/t.py::test_x", "failure_signature": "a", "state": "OPEN"},
    }
    with pytest.raises(mod.LedgerCorruptionError, match="illegal"):
        rebuild_ledger(
            [
                opened,
                {
                    "type": "ledger.transitioned",
                    "payload": {"node": "tests/t.py::test_x", "from": "OPEN", "to": "PROVEN"},
                },
            ]
        )
    with pytest.raises(mod.LedgerCorruptionError, match="ambiguous"):
        rebuild_ledger(
            [
                opened,
                {
                    "type": "ledger.opened",
                    "payload": {
                        "node": "tests/t.py::test_x",
                        "failure_signature": "b",
                        "state": "OPEN",
                    },
                },
                {
                    "type": "ledger.transitioned",
                    "payload": {
                        "node": "tests/t.py::test_x",
                        "from": "OPEN",
                        "to": "CLASSIFIED",
                    },
                },
            ]
        )


def test_select_diff_is_per_entry_and_falls_back_when_graph_is_unreliable():
    mod = _test_select_module()
    select_diff = _require(mod, "select_diff")
    entry = {"node": "tests/unit/test_a.py::test_a", "state": "FIXED"}
    selected = select_diff(
        entry,
        ["tracks/a.py"],
        lambda path: {"tests/integration/test_a.py::test_if_a"} if path == "tracks/a.py" else set(),
    )
    assert selected.reliable
    assert selected.nodes == [
        "tests/integration/test_a.py::test_if_a",
        "tests/unit/test_a.py::test_a",
    ]
    fallback = select_diff(entry, ["tracks/a.py"], lambda _path: set())
    assert not fallback.reliable
    assert fallback.nodes == [entry["node"]]


def test_run_full_chain_loops_fullf_without_a_loop_cap():
    mod = _test_select_module()
    run_full_chain = _require(mod, "run_full_chain")
    rounds = []
    def failed(*nodes):
        return {
            "passed": False,
            "failed_nodes": list(nodes),
            "failures": [
                {"node": node, "failure_signature": f"signature-{node}"}
                for node in nodes
            ],
        }

    full_results = iter(
        [
            failed("node-a"),
            failed("node-a", "node-b"),
            {"passed": True, "failed_nodes": []},
        ]
    )

    def execute_full(round_name):
        rounds.append(round_name)
        return next(full_results)

    opened = []
    transitions = []
    result = run_full_chain(
        execute_full,
        lambda nodes: opened.extend(nodes),
        lambda _node: True,
        lambda entry: mod.DiffSelection([entry["node"]], True, "entry"),
        lambda _entry, _selection: True,
        lambda entry, before, after, _reason: transitions.append(
            (entry["node"], entry["failure_signature"], before, after)
        ),
    )
    assert result == "exited"
    assert rounds == ["FULL_1", "FULL_F", "FULL_F"]
    assert [entry["node"] for entry in opened] == ["node-a", "node-b"]
    assert ("node-a", opened[0]["failure_signature"], "PROVEN", "OPEN") in transitions


# IF-RUNCONTRACT-001 (v5 API; v6 signature): resolve_selected_command explicitly
# receives the selected nodes AND the Runtime-provided result path, substitutes
# {nodes}/{result}, and resolves argv0 against cwd; the audit compares expected
# vs actual argv under ONE shared resolution rule -- verbatim execution passes,
# any appended -n/--dist/worker flag or injected --junitxml or swapped node
# fails closed.
_TEMPLATE = (
    ".venv/bin/python -m pytest {nodes} --tb=short -q -n 8 --dist loadscope "
    "--junitxml={result}"
)
_NODES = [
    "tests/integration/test_a.py::test_x",
    "tests/integration/test_a.py::test_y",
]
_RESULT = "/tmp/tracks-staging/run42/sel-result.xml"


def _audit_passes(fn, *args):
    try:
        result = fn(*args)
    except Exception:  # noqa: BLE001 - fail-closed may raise or return falsy
        return False
    return bool(result)


def test_resolve_selected_command_substitutes_nodes_and_resolves_argv0(tmp_path):
    mod = _test_select_module()
    resolve = _require(mod, "resolve_selected_command")
    argv = resolve(_TEMPLATE, sorted(_NODES), _RESULT, tmp_path)
    expected = [
        sys.executable,
        "-m",
        "pytest",
        "tests/integration/test_a.py::test_x",
        "tests/integration/test_a.py::test_y",
        "--tb=short",
        "-q",
        "-n",
        "8",
        "--dist",
        "loadscope",
        f"--junitxml={_RESULT}",
    ]
    assert list(argv) == expected, (
        "resolved argv must be the verbatim contract expansion: argv0 resolved "
        "against cwd, {nodes} substituted with the selected nodes in canonical "
        "order, {result} substituted with the Runtime-provided path, every other "
        "token preserved"
    )
    # Selection nodes are canonical sorted: reversing the input order must
    # resolve identically (order never leaks into the resolved argv).
    assert list(resolve(_TEMPLATE, list(reversed(_NODES)), _RESULT, tmp_path)) == expected


def test_audit_accepts_verbatim_and_fails_on_injection(tmp_path):
    mod = _test_select_module()
    resolve = _require(mod, "resolve_selected_command")
    audit_pair = _require(mod, "audit_no_concurrency_injection")
    audit = _require(mod, "audit")
    expected = list(resolve(_TEMPLATE, sorted(_NODES), _RESULT, tmp_path))

    # Verbatim execution of the resolved selection command passes.
    assert _audit_passes(audit_pair, expected, list(expected), tmp_path)
    assert _audit_passes(audit, _TEMPLATE, sorted(_NODES), _RESULT, list(expected), tmp_path)

    # The pair API owns argv0 normalization on BOTH sides: a command echo may
    # retain the contract-relative executable while the expected side is
    # already resolved against cwd. These are the same command identity.
    relative_actual = [".venv/bin/python", *expected[1:]]
    assert _audit_passes(audit_pair, expected, relative_actual, tmp_path)

    # Any Runtime-injected argument fails closed on both audit forms --
    # including an injected --junitxml (v6: the result flag may only be
    # embedded by Archer via the {result} placeholder).
    for injected in (
        ["-n", "2"],
        ["--dist", "loadfile"],
        ["-p", "xdist"],
        ["--junitxml=/tmp/injected.xml"],
        ["tests/integration/test_intruder.py::test_z"],
    ):
        assert not _audit_passes(audit_pair, expected, [*expected, *injected], tmp_path), injected
        assert not _audit_passes(
            audit,
            _TEMPLATE,
            sorted(_NODES),
            _RESULT,
            [*expected, *injected],
            tmp_path,
        ), injected

    # Actual argv whose {nodes} expansion differs from the selected nodes fails.
    # Selection nodes are canonically sorted, so reversing the input resolves
    # identically (order alone can never be a mismatch); a genuine mismatch
    # replaces one selected node with a different nodeid.
    replaced = [
        "tests/integration/test_a.py::test_x",
        "tests/integration/test_swapped.py::test_replaced",
    ]
    other = list(resolve(_TEMPLATE, replaced, _RESULT, tmp_path))
    assert other != expected
    assert not _audit_passes(audit_pair, expected, other, tmp_path)
    assert not _audit_passes(audit, _TEMPLATE, sorted(_NODES), _RESULT, other, tmp_path)


# IF-RUNCONTRACT-001 (v6): the machine-readable per-node result channel -- a
# pure JUnit XML parser plus exact-set coverage gate. Test execution results
# are ONLY authoritative as JUnit XML whose testcase identities cover exactly
# the selected set (full run => exactly the collected FULL set); malformed or
# missing files, duplicate identities, absent selected nodes and extra nodes
# all fail closed (JUnitResultError). stdout/stderr are logs, never authority.
_JUNIT_VALID = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<testsuites><testsuite name="sel" tests="3">\n'
    '<testcase classname="tests.integration.test_a" name="test_x" time="0.01"/>\n'
    '<testcase classname="tests.integration.test_a" name="test_y" time="0.01">\n'
    "  <failure message=\"AssertionError: stub token\">IF-STUB-1 raised</failure>\n"
    "</testcase>\n"
    '<testcase classname="tests.integration.test_b" name="test_z" time="0.01">\n'
    "  <skipped/></testcase>\n"
    "</testsuite></testsuites>\n"
)


def test_parse_junit_result_reads_per_node_records(tmp_path):
    mod = _test_select_module()
    JUnitCase = _require(mod, "JUnitCase")
    parse = _require(mod, "parse_junit_result")
    path = tmp_path / "result.xml"
    path.write_text(_JUNIT_VALID, encoding="utf-8")
    cases = parse(path)
    assert len(cases) == 3
    by_id = {case.nodeid: case for case in cases}
    assert isinstance(cases[0], JUnitCase)
    assert by_id["tests/integration/test_a.py::test_x"].status == "passed"
    assert by_id["tests/integration/test_a.py::test_y"].status == "failed"
    assert "IF-STUB-1" in (by_id["tests/integration/test_a.py::test_y"].detail or ""), (
        "failure detail is the legal-red classification input"
    )
    assert by_id["tests/integration/test_b.py::test_z"].status == "skipped"


def test_require_exact_node_coverage_exact_selected_set_passes():
    mod = _test_select_module()
    require_coverage = _require(mod, "require_exact_node_coverage")
    selected = [
        "tests/integration/test_a.py::test_x",
        "tests/integration/test_a.py::test_y",
        "tests/integration/test_b.py::test_z",
    ]

    class _FakeCase:
        def __init__(self, nodeid):
            self.nodeid = nodeid

    mapping = require_coverage([_FakeCase(n) for n in selected], selected)
    assert sorted(mapping) == sorted(selected), (
        "exact coverage returns the node -> record mapping"
    )
    # Full-run semantics are the same gate over the collected FULL set.
    assert sorted(require_coverage([], [])) == []


@pytest.mark.parametrize(
    ("xml", "why"),
    [
        ('<testsuite><testcase name="broken"', "malformed"),
        ("", "empty-file"),
        (
            '<testsuites><testsuite name="s">'
            '<testcase classname="a.t" name="test_one"/>'
            '<testcase classname="a.t" name="test_one"/>'
            "</testsuite></testsuites>",
            "duplicate-identity",
        ),
        (
            '<testsuites><testsuite name="s"><testcase name=""/></testsuite></testsuites>',
            "missing-identity",
        ),
    ],
)
def test_parse_junit_result_malformed_missing_duplicate_fail_closed(tmp_path, xml, why):
    mod = _test_select_module()
    parse = _require(mod, "parse_junit_result")
    error_type = _require(mod, "JUnitResultError")
    path = tmp_path / f"{why}.xml"
    path.write_text(xml, encoding="utf-8")
    with pytest.raises(error_type):
        parse(path)

    # A missing file is equally fail-closed (never treated as zero failures).
    with pytest.raises(error_type):
        parse(tmp_path / "does-not-exist.xml")


def test_require_exact_node_coverage_absent_or_extra_nodes_fail_closed():
    mod = _test_select_module()
    require_coverage = _require(mod, "require_exact_node_coverage")

    def _fails_closed(cases, selected):
        try:
            require_coverage(cases, selected)
        except Exception:  # noqa: BLE001 - fail-closed may raise any error type
            return True
        return False

    class _Case:
        def __init__(self, nodeid):
            self.nodeid = nodeid

    sel = ["tests/t.py::test_one", "tests/t.py::test_two"]
    # Absent selected node: one selected testcase missing from the result.
    assert _fails_closed([_Case(sel[0])], sel), "absent selected node must fail closed"
    # Extra node: result reports a testcase nobody selected.
    assert _fails_closed(
        [_Case(sel[0]), _Case(sel[1]), _Case("tests/t.py::test_extra")], sel
    ), "extra testcase must fail closed"
    # Duplicate identity inside the result (even when the set would otherwise match).
    assert _fails_closed([_Case(sel[0]), _Case(sel[0])], [sel[0]]), (
        "duplicate testcase identity must fail closed"
    )
    # Empty records against a non-empty selection can never count as a pass.
    assert _fails_closed([], sel), "missing result must never be read as zero failures"


# IF-SELECT-001 (v5): executable R1 snapshot capture -- full three-layer collect,
# per-node digests, stamped baseline/tree identity; deterministic; first-ever
# project may legitimately capture an empty snapshot.
def test_capture_test_baseline_stamps_identity_over_all_layers():
    mod = _test_select_module()
    capture = _require(mod, "capture_test_baseline")
    snapshot_type = _require(mod, "TestBaselineSnapshot")
    layers = ("unit", "integration", "e2e")
    nodes_by_layer = {
        "unit": ["tests/unit/test_hist.py::test_hist_seed"],
        "integration": ["tests/integration/test_a.py::test_one"],
        "e2e": [],
    }
    digests = {
        node: hashlib.sha256(node.encode()).hexdigest()
        for ids in nodes_by_layer.values()
        for node in ids
    }
    snapshot = capture(
        lambda layer: nodes_by_layer[layer],
        lambda node: digests[node],
        "tree-sha-1",
    )
    assert isinstance(snapshot, snapshot_type)
    assert snapshot.baseline_tree == "tree-sha-1"
    assert tuple(snapshot.layers) == layers
    assert set(snapshot.node_digests) == {*nodes_by_layer["unit"], *nodes_by_layer["integration"]}
    assert snapshot.empty_baseline is False
    assert snapshot.node_digests["tests/unit/test_hist.py::test_hist_seed"] == (
        digests["tests/unit/test_hist.py::test_hist_seed"]
    )

    # Deterministic: same inputs -> same stamped identity.
    again = capture(lambda layer: nodes_by_layer[layer], lambda n: digests[n], "tree-sha-1")
    assert snapshot.baseline_id == again.baseline_id

    # Identity sensitivity: any digest or tree change re-stamps.
    changed = capture(lambda layer: nodes_by_layer[layer], lambda n: digests[n], "tree-sha-2")
    assert changed.baseline_id != snapshot.baseline_id


def test_capture_test_baseline_allows_legitimate_first_empty_snapshot():
    mod = _test_select_module()
    capture = _require(mod, "capture_test_baseline")
    snapshot = capture(lambda layer: [], lambda node: node, "tree-empty")
    assert snapshot.empty_baseline is True
    assert snapshot.node_digests == {}
    assert snapshot.baseline_id


# IF-SELECT-001 (v6): the REAL per-node source digest API behind
# capture_test_baseline's node_digest seam -- digests are computed from each
# nodeid's physical function source segment, never from whole files.
def test_collect_node_source_digests_sibling_isolation_in_one_file(tmp_path):
    mod = _test_select_module()
    collect_digests = _require(mod, "collect_node_source_digests")
    physical = tmp_path / "tests" / "unit" / "test_digest_pair.py"
    physical.parent.mkdir(parents=True)
    node_a = f"{_DIGEST_PAIR_FILE}::test_first_sibling"
    node_b = f"{_DIGEST_PAIR_FILE}::test_second_sibling"
    physical.write_text(_digest_pair_source("assert 2 + 2 == 4"), encoding="utf-8")

    before = collect_digests(tmp_path, [node_a, node_b])
    assert set(before) == {node_a, node_b}

    # Edit ONLY the second function body: its source digest must change while
    # the unchanged sibling's digest is byte-identical (a whole-file digest
    # would move both -- the ce_wholefile_digest defect class).
    physical.write_text(_digest_pair_source("assert 2 + 2 == 5"), encoding="utf-8")
    after = collect_digests(tmp_path, [node_a, node_b])
    assert after[node_b] != before[node_b], (
        "the edited function's per-node source digest must change"
    )
    assert after[node_a] == before[node_a], (
        "the unchanged sibling's per-node source digest must be identical "
        "(per-node digests, never a whole-file digest)"
    )


def test_collect_node_source_digests_parametrized_nodes_share_function_digest(tmp_path):
    mod = _test_select_module()
    collect_digests = _require(mod, "collect_node_source_digests")
    physical = tmp_path / "tests" / "unit" / "test_digest_pair.py"
    physical.parent.mkdir(parents=True)
    physical.write_text(_digest_pair_source("assert 2 + 2 == 4"), encoding="utf-8")
    param_0 = f"{_DIGEST_PAIR_FILE}::test_shared[alpha]"
    param_1 = f"{_DIGEST_PAIR_FILE}::test_shared[beta]"

    first = collect_digests(tmp_path, [param_0, param_1])
    assert first[param_0] == first[param_1], (
        "parametrized nodeids sharing one function must share its source digest"
    )
    # Deterministic: same inputs in any order re-collect identical digests.
    again = collect_digests(tmp_path, [param_1, param_0])
    assert again == first


# IF-SELECT-001 (v6 §3a boundary): a nodeid whose physical file is missing or
# whose function def cannot be located fails closed -- a whole-file digest
# fallback is exactly the ce_wholefile_digest defect class and must never
# paper over an unlocatable node.
def test_collect_node_source_digests_unlocatable_node_fails_closed(tmp_path):
    mod = _test_select_module()
    collect_digests = _require(mod, "collect_node_source_digests")
    physical = tmp_path / "tests" / "unit" / "test_digest_pair.py"
    physical.parent.mkdir(parents=True)
    physical.write_text(_digest_pair_source("assert 2 + 2 == 4"), encoding="utf-8")
    phantoms = (
        f"{_DIGEST_PAIR_FILE}::test_never_defined",
        "tests/unit/test_absent.py::test_gone_with_the_file",
    )

    def _fails_closed(node):
        try:
            collected = collect_digests(tmp_path, [node])
        except Exception:  # noqa: BLE001 - fail-closed may raise any error type
            return True
        return not collected

    for phantom in phantoms:
        assert _fails_closed(phantom), (
            f"{phantom} resolved without failing closed: an unlocatable node "
            "definition must fail closed, never fall back to a whole-file digest"
        )


# IF-SELECT-002 (v5): feature empty-R2 selection is fail-closed at the gate;
# the hotfix explicit unit-only increment bypass returns the empty selection.
#
# Boundary note: this pure-helper pin stays because the only public
# empty-Shield-increment journey (AC-FR0244-04,
# tests/integration/test_hotfix_mtest.py::test_empty_shield_increment_release_with_unit_closure)
# still parks at the IF-HOTFIX-010 resolver seam and cannot drive this M-TEST
# gate yet; the feature side of the gate is covered publicly by
# tests/integration/test_selection_semantics.py::test_empty_r2_feature_mtest_fail_closed.
def test_require_nonempty_r2_selection_feature_fail_closed_hotfix_bypass():
    mod = _test_select_module()
    require_sel = _require(mod, "require_nonempty_r2_selection")

    def _passes(*args, **kwargs):
        try:
            return bool(require_sel(*args, **kwargs) is not None)
        except Exception:  # noqa: BLE001 - fail-closed may raise
            return False

    assert _passes("r2_delta", ["tests/t.py::test_r2"], allow_explicit_unit_increment=False)
    assert _passes("r2_delta", [], allow_explicit_unit_increment=True), (
        "hotfix explicit unit-only increment bypass must admit the empty R2 selection"
    )
    assert not _passes("r2_delta", [], allow_explicit_unit_increment=False), (
        "feature M-TEST with an empty R2 selection must fail closed (no vacuous pass)"
    )
