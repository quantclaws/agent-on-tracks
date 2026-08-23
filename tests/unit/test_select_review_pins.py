"""Slice A code-review RED pins (D-41 follow-up): per-node digest, JUnit
identity normalization, selection identity dirty-content sensitivity.

Each test pins one review finding against tracks/executor/test_select.py:

- Review-2: per-node source digests must support class-method and nested-class
  nodeids and must cover the node's decorators plus tightly attached
  TRACKS-TRACE marker comments; editing one method/decorator/marker moves only
  that node's digest while an unchanged sibling stays byte-identical;
  parametrized cases of one function (top-level or method) share its digest.
- Review-3: JUnit testcase identity normalization must reproduce REAL pytest
  nodeids exactly -- plain functions, class methods, nested classes and
  parametrized ids -- never a naive ``classname.replace(".", "/")`` rewrite
  (which turns ``tests.unit.test_mod.TestCalc`` into the phantom file
  ``tests/unit/test_mod/TestCalc.py``).
- Review-5 (pure seam): selection identity must be sensitive to dirty R2
  content via a tree stamp, so two selections over the same node set with the
  same HEAD but different worktree content do not share a selection_id.

RED discipline: production is untouched; failures land on the pinned contract
tokens (assertion_failure), never on assembly noise.
"""

from __future__ import annotations

import importlib.util

import pytest


def _test_select_module():
    spec = importlib.util.find_spec("tracks.executor.test_select")
    assert spec is not None, (
        "tracks.executor.test_select module missing: D-41 Slice A contract"
    )
    import tracks.executor.test_select as mod

    return mod


def _require(mod, name):
    obj = getattr(mod, name, None)
    assert obj is not None, f"tracks.executor.test_select.{name} missing (D-41 Slice A contract)"
    return obj


def _collect_or_fail(mod, root, nodes, why):
    collect = _require(mod, "collect_node_source_digests")
    try:
        return collect(root, list(nodes))
    except mod.TestSelectError as exc:
        pytest.fail(f"{why}: per-node digest refused a legal pytest nodeid: {exc}")


# -- Review-2: class methods and nested classes ------------------------------

_CLASS_SOURCE_V1 = (
    "class TestCalc:\n"
    "    def test_add(self):\n"
    "        assert 1 + 1 == 2\n"
    "\n"
    "    def test_sub(self):\n"
    "        assert 2 - 1 == 1\n"
)
_CLASS_SOURCE_V2 = _CLASS_SOURCE_V1.replace("assert 1 + 1 == 2", "assert 1 + 1 == 3")


def test_class_method_nodeids_have_isolated_per_node_digests(tmp_path):
    """Review-2: ``file.py::TestClass::test_method`` nodeids digest their own
    method segment; editing one method moves only that node while its sibling
    inside the same class stays byte-identical."""
    mod = _test_select_module()
    physical = tmp_path / "tests" / "unit" / "test_cls.py"
    physical.parent.mkdir(parents=True)
    physical.write_text(_CLASS_SOURCE_V1, encoding="utf-8")
    add = "tests/unit/test_cls.py::TestCalc::test_add"
    sub = "tests/unit/test_cls.py::TestCalc::test_sub"

    before = _collect_or_fail(mod, tmp_path, [add, sub], "class-method nodeids")
    physical.write_text(_CLASS_SOURCE_V2, encoding="utf-8")
    after = _collect_or_fail(mod, tmp_path, [add, sub], "class-method nodeids")

    assert after[add] != before[add], (
        "editing TestCalc::test_add must change that method's per-node digest"
    )
    assert after[sub] == before[sub], (
        "the untouched sibling method TestCalc::test_sub must keep a "
        "byte-identical per-node digest (never a whole-file/class digest)"
    )


def test_nested_class_nodeid_digests_own_segment(tmp_path):
    """Review-2: nested-class nodeids (::TestOuter::TestInner::test_deep) are
    first-class classification inputs, not unlocatable failures."""
    mod = _test_select_module()
    physical = tmp_path / "tests" / "unit" / "test_nested.py"
    physical.parent.mkdir(parents=True)

    def _source(body):
        return (
            "class TestOuter:\n"
            "    class TestInner:\n"
            f"        def test_deep(self):\n"
            f"            {body}\n"
        )

    physical.write_text(_source("assert True"), encoding="utf-8")
    deep = "tests/unit/test_nested.py::TestOuter::TestInner::test_deep"

    before = _collect_or_fail(mod, tmp_path, [deep], "nested-class nodeid")
    physical.write_text(_source("assert 1 == 2"), encoding="utf-8")
    after = _collect_or_fail(mod, tmp_path, [deep], "nested-class nodeid")

    assert after[deep] != before[deep], (
        "a nested-class method edit must move that node's per-node digest"
    )


def test_same_named_methods_in_distinct_classes_stay_isolated(tmp_path):
    """Review-2: two classes may declare methods with the same name; each
    class-method nodeid digests ITS OWN segment -- never the first def the
    AST walk happens to find."""
    mod = _test_select_module()
    physical = tmp_path / "tests" / "unit" / "test_twin.py"
    physical.parent.mkdir(parents=True)

    def _source(b_body):
        return (
            "class TestA:\n"
            "    def test_x(self):\n"
            "        assert 'A'\n"
            "\n"
            "class TestB:\n"
            "    def test_x(self):\n"
            f"        {b_body}\n"
        )

    physical.write_text(_source("assert 'B'"), encoding="utf-8")
    node_a = "tests/unit/test_twin.py::TestA::test_x"
    node_b = "tests/unit/test_twin.py::TestB::test_x"

    before = _collect_or_fail(mod, tmp_path, [node_a, node_b], "same-named methods")
    assert before[node_a] != before[node_b], (
        "distinct classes' same-named methods must not share a digest just "
        "because their def names collide"
    )
    physical.write_text(_source("assert 'B2'"), encoding="utf-8")
    after = _collect_or_fail(mod, tmp_path, [node_a, node_b], "same-named methods")

    assert after[node_b] != before[node_b], (
        "editing TestB::test_x must move TestB::test_x's own digest"
    )
    assert after[node_a] == before[node_a], (
        "TestA::test_x must stay byte-identical when only TestB::test_x changed"
    )


# -- Review-2: decorators and tightly attached TRACKS-TRACE markers ----------


def _decorator_source(marker_line):
    return (
        "def test_plain_sibling():\n"
        "    assert True\n"
        "\n"
        "\n"
        f"{marker_line}\n"
        "def test_flagged():\n"
        "    assert True\n"
    )


def test_decorator_lines_are_part_of_the_node_segment(tmp_path):
    """Review-2: a node's segment starts at its first decorator, so changing
    ONLY the decorator line changes that node's digest while an undecorated
    sibling stays stable."""
    mod = _test_select_module()
    physical = tmp_path / "tests" / "unit" / "test_deco.py"
    physical.parent.mkdir(parents=True)
    sibling = "tests/unit/test_deco.py::test_plain_sibling"
    flagged = "tests/unit/test_deco.py::test_flagged"

    physical.write_text(_decorator_source("@pytest.mark.smoke"), encoding="utf-8")
    before = _collect_or_fail(mod, tmp_path, [sibling, flagged], "decorated node")
    physical.write_text(_decorator_source("@pytest.mark.slow"), encoding="utf-8")
    after = _collect_or_fail(mod, tmp_path, [sibling, flagged], "decorated node")

    assert after[flagged] != before[flagged], (
        "the decorator line belongs to the node's source segment: swapping "
        "@pytest.mark.smoke for @pytest.mark.slow must move its digest"
    )
    assert after[sibling] == before[sibling], (
        "an undecorated sibling must keep a byte-identical digest across the "
        "decorator-only edit"
    )


def test_attached_tracks_trace_marker_is_part_of_the_node_segment(tmp_path):
    """Review-2: a TRACKS-TRACE marker comment tightly attached above the
    def/decorator participates in the node identity; a detached comment
    between siblings belongs to no node and must move nobody."""
    mod = _test_select_module()
    physical = tmp_path / "tests" / "unit" / "test_trace_marker.py"
    physical.parent.mkdir(parents=True)
    sibling = "tests/unit/test_trace_marker.py::test_detached_sibling"
    bound = "tests/unit/test_trace_marker.py::test_bound"

    def _source(marker_description, detached_note):
        parts = [
            "# AC-FR0100-01@v0.6 TRACKS-TRACE ",
            marker_description,
            "\n",
            "def test_bound():\n",
            "    assert True\n",
            "\n",
            "\n",
        ]
        if detached_note:
            parts += [detached_note, "\n"]
        parts += ["def test_detached_sibling():\n", "    assert True\n"]
        return "".join(parts)

    physical.write_text(_source("alpha binding", None), encoding="utf-8")
    before = _collect_or_fail(mod, tmp_path, [bound, sibling], "marked node")
    # Marker description changes -> the binding moved.
    physical.write_text(_source("beta binding", None), encoding="utf-8")
    marked = _collect_or_fail(mod, tmp_path, [bound, sibling], "marked node")
    # A detached note between the siblings belongs to neither node.
    physical.write_text(
        _source("beta binding", "# free floating maintenance note"), encoding="utf-8"
    )
    detached = _collect_or_fail(mod, tmp_path, [bound, sibling], "detached note")

    assert marked[bound] != before[bound], (
        "the tightly attached '# ... TRACKS-TRACE <description>' line is part "
        "of the node identity: rewriting the description must move its digest"
    )
    assert detached[bound] == marked[bound], (
        "a blank-line-detached comment between siblings must not enter any "
        "node segment (tightly attached markers only)"
    )
    assert detached[sibling] == marked[sibling] == before[sibling], (
        "the sibling without an attached marker must stay byte-identical "
        "across both edits"
    )


# -- Review-2: parametrized cases share one function digest ------------------


def test_parametrized_cases_share_the_class_method_digest(tmp_path):
    """Review-2: ``Cls::test_m[a]``/``Cls::test_m[b]``/``Cls::test_m`` all
    resolve to the same physical def and therefore share one digest."""
    mod = _test_select_module()
    physical = tmp_path / "tests" / "unit" / "test_cls_param.py"
    physical.parent.mkdir(parents=True)
    physical.write_text(_CLASS_SOURCE_V1, encoding="utf-8")
    base = "tests/unit/test_cls_param.py::TestCalc::test_add"
    param_a = base + "[alpha]"
    param_b = base + "[beta]"

    digests = _collect_or_fail(
        mod, tmp_path, [param_a, param_b, base], "parametrized class-method nodeids"
    )

    assert digests[param_a] == digests[param_b] == digests[base], (
        "parametrized cases of one method share that method's source digest"
    )


# -- Review-3: JUnit identity normalization vs real pytest XML ---------------

_REAL_PYTEST_JUNIT = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<testsuites><testsuite name="pytest" tests="4">\n'
    '<testcase classname="tests.unit.test_mod" name="test_func" time="0.001"/>\n'
    '<testcase classname="tests.unit.test_mod.TestCalc" name="test_method" time="0.001"/>\n'
    '<testcase classname="tests.unit.test_mod.TestOuter.TestInner" name="test_deep"'
    ' time="0.001"/>\n'
    '<testcase classname="tests.unit.test_mod" name="test_func[param-0]" time="0.001"/>\n'
    "</testsuite></testsuites>\n"
)

_EXPECTED_NODEIDS = {
    ("tests.unit.test_mod", "test_func"): "tests/unit/test_mod.py::test_func",
    ("tests.unit.test_mod.TestCalc", "test_method"): (
        "tests/unit/test_mod.py::TestCalc::test_method"
    ),
    ("tests.unit.test_mod.TestOuter.TestInner", "test_deep"): (
        "tests/unit/test_mod.py::TestOuter::TestInner::test_deep"
    ),
    ("tests.unit.test_mod", "test_func[param-0]"): (
        "tests/unit/test_mod.py::test_func[param-0]"
    ),
}


def test_junit_identity_normalization_matches_real_pytest_nodeids(tmp_path):
    """Review-3: normalization must rebuild the exact collected nodeid --
    module path from the classname MINUS the trailing class chain, then the
    ::-joined class chain plus name. The naive dots->slashes rewrite produces
    ``tests/unit/test_mod/TestCalc.py::test_method``, which matches nothing
    pytest ever collects."""
    mod = _test_select_module()
    parse = _require(mod, "parse_junit_result")
    path = tmp_path / "real.xml"
    path.write_text(_REAL_PYTEST_JUNIT, encoding="utf-8")

    cases = parse(path)
    by_id = {case.nodeid: case for case in cases}
    expected_ids = list(_EXPECTED_NODEIDS.values())
    for expected in expected_ids:
        assert by_id.get(expected) is not None, (
            f"junit identity normalization must yield {expected!r}; got "
            f"{sorted(by_id)} -- a naive dots->slashes rewrite cannot match "
            "real pytest class-method/nested nodeids"
        )
    require_coverage = _require(mod, "require_exact_node_coverage")
    try:
        mapping = require_coverage(cases, sorted(expected_ids))
    except mod.JUnitResultError as exc:
        pytest.fail(f"exact coverage failed on real pytest junit identities: {exc}")
    assert sorted(mapping) == sorted(expected_ids)


def test_junit_normalization_never_invents_phantom_files(tmp_path):
    """Review-3 strictness pin: every normalized identity must correspond to a
    real collection target of the declaring module -- no testcase may map to a
    path whose directory chain swallowed a class segment."""
    mod = _test_select_module()
    parse = _require(mod, "parse_junit_result")
    path = tmp_path / "real.xml"
    path.write_text(_REAL_PYTEST_JUNIT, encoding="utf-8")

    phantom_fragments = ("/TestCalc.py::", "/TestOuter.py::", "/TestInner.py::")
    offenders = [
        case.nodeid
        for case in parse(path)
        if any(fragment in case.nodeid for fragment in phantom_fragments)
    ]
    assert not offenders, (
        f"class segments leaked into physical paths: {offenders} -- naive "
        "dots->slashes normalization invents files no collector ever declares"
    )


# -- Review-5 (pure seam): dirty-content sensitivity of selection identity ---


def test_make_selection_id_is_sensitive_to_tree_stamp():
    """Review-5: two selections over the SAME nodes/baseline/HEAD but different
    dirty worktree content must not share a selection identity; the payload
    stamps the worktree content separately from commit via ``tree_stamp``."""
    mod = _test_select_module()
    make_selection_id = _require(mod, "make_selection_id")
    kwargs = {
        "nodes": ["tests/integration/test_delta.py::test_delta"],
        "scope": "r2_delta",
        "basis": "delta-declaration",
        "baseline": "b-identical",
        "commit": "c-same-head",
    }
    try:
        stamped_one = make_selection_id(**kwargs, tree_stamp="tree-content-v1")
        stamped_two = make_selection_id(**kwargs, tree_stamp="tree-content-v2")
    except TypeError as exc:
        pytest.fail(
            "make_selection_id must accept a tree_stamp input: selection "
            f"identity is blind to dirty R2 content changes otherwise ({exc})"
        )
    assert stamped_one != stamped_two, (
        "selection identity must differ when the dirty R2 content differs "
        "under an identical node set and HEAD (evidence reuse hole)"
    )
