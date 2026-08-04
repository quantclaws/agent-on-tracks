"""check_reach pure function tests (FR-0090).

AC-FR0090-01@v0.4 build import graph, AC-FR0090-02@v0.4 islands reported,
AC-FR0090-03@v0.4 test modules excluded, AC-FR0090-04@v0.4 no entrypoints error,
AC-FR0090-05@v0.4 module-level only, AC-NFR0020-02@v0.4 stable order.
"""
from tracks.checks.reach import check_reach


def test_build_import_graph():
    """AC-FR0090-01@v0.4 build module-level import graph from entrypoints."""
    graph = {
        "pkg.main": {"pkg.mod_a", "pkg.mod_b"},
        "pkg.mod_a": set(),
        "pkg.mod_b": {"pkg.mod_a"},
    }
    r = check_reach(
        ["pkg.main"], graph,
        {"pkg.main", "pkg.mod_a", "pkg.mod_b"},
    )
    assert r.status == "pass"
    assert r.islands == ()
    assert "pkg.main" in r.entrypoints


def test_islands_reported():
    """AC-FR0090-02@v0.4 unreachable production modules reported as islands."""
    graph = {
        "pkg.main": {"pkg.mod_a"},
        "pkg.mod_a": set(),
        "pkg.orphan": set(),
    }
    r = check_reach(
        ["pkg.main"], graph,
        {"pkg.main", "pkg.mod_a", "pkg.orphan"},
    )
    assert r.status == "fail"
    assert "pkg.orphan" in r.islands


def test_test_modules_excluded():
    """AC-FR0090-03@v0.4 pure test modules not counted as islands."""
    graph = {
        "pkg.main": {"pkg.mod_a"},
        "pkg.mod_a": set(),
        "tests.test_x": set(),
    }
    r = check_reach(
        ["pkg.main"], graph,
        {"pkg.main", "pkg.mod_a"},
    )
    assert r.status == "pass"
    assert "tests.test_x" not in r.islands


def test_no_entrypoints_error():
    """AC-FR0090-04@v0.4 no entrypoint declaration -> error, non-zero."""
    graph = {"pkg.mod_a": set()}
    r = check_reach([], graph, {"pkg.mod_a"})
    assert r.status == "fail"
    assert any("no entrypoints" in e for e in r.errors)


def test_no_function_level_graph():
    """AC-FR0090-05@v0.4 only module-level import graph, no function-level."""
    graph = {
        "pkg.main": {"pkg.mod_a"},
        "pkg.mod_a": set(),
    }
    r = check_reach(["pkg.main"], graph, {"pkg.main", "pkg.mod_a"})
    assert r.status == "pass"
    assert r.islands == ()


def test_baseline_exemption():
    """AC-FR0100-02@v0.4 baseline-exempted modules not counted as islands."""
    graph = {
        "pkg.main": {"pkg.mod_a"},
        "pkg.mod_a": set(),
        "pkg.legacy": set(),
    }
    baseline = {"reach_exemptions": {"modules": ["pkg.legacy"]}}
    r = check_reach(
        ["pkg.main"], graph,
        {"pkg.main", "pkg.mod_a", "pkg.legacy"},
        baseline=baseline,
    )
    assert "pkg.legacy" not in r.islands


def test_stable_order():
    """AC-NFR0020-02@v0.4 output order stable and reproducible."""
    graph = {
        "pkg.main": {"pkg.mod_a"},
        "pkg.mod_a": set(),
        "pkg.zebra": set(),
        "pkg.alpha": set(),
    }
    r1 = check_reach(["pkg.main"], graph, {"pkg.main", "pkg.mod_a", "pkg.zebra", "pkg.alpha"})
    r2 = check_reach(["pkg.main"], graph, {"pkg.main", "pkg.mod_a", "pkg.zebra", "pkg.alpha"})
    assert r1 == r2
    assert r1.islands == tuple(sorted(r1.islands))


def test_package_import_connects_children():
    """Import of a package connects to modules under it (when package itself
    is not a module in the graph)."""
    graph = {
        "pkg.main": {"pkg.sub"},
        "pkg.sub.mod_a": set(),
    }
    # pkg.sub is NOT in the graph; pkg.sub.mod_a IS.
    # Import of pkg.sub connects to pkg.sub.mod_a (prefix match).
    r = check_reach(
        ["pkg.main"], graph,
        {"pkg.main", "pkg.sub.mod_a"},
    )
    assert r.status == "pass"
    assert "pkg.sub.mod_a" not in r.islands
