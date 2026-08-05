"""Ground-truth parity tests for reach tool (FR-0090).

Compares tracks.checks.reach.check_reach_file output against the
independent reference implementation tests/ground_truth/reach_reference.py
on fixture corpora under tests/assets/reach_fixtures/.

AC-FR0090-01@v0.4 build import graph + ground truth,
AC-FR0090-02@v0.4 islands reported + ground truth,
AC-FR0090-04@v0.4 no entrypoints error + ground truth.
"""
import sys
from pathlib import Path

from tracks.checks.reach import check_reach_file

GROUND_TRUTH_DIR = Path(__file__).resolve().parent.parent / "ground_truth"
sys.path.insert(0, str(GROUND_TRUTH_DIR))
from reach_reference import (  # noqa: E402
    build_import_graph,
    compute_reach,
    find_main_modules,
    parse_scripts_toml,
)

FIXTURES = Path(__file__).resolve().parent.parent / "assets" / "reach_fixtures"


def _read_fixture(name: str) -> tuple[dict[str, str], set[str]]:
    """Read all .py files from a reach fixture as {module_name: content}
    plus the set of module names that are packages (``__init__.py``)."""
    d = FIXTURES / name
    py_files = {}
    packages: set[str] = set()
    for py in sorted(d.rglob("*.py")):
        rel = py.relative_to(d)
        parts = list(rel.parts)
        is_init = parts[-1] == "__init__.py"
        if is_init:
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1][:-3]
        mod_name = ".".join(parts)
        py_files[mod_name] = py.read_text(encoding="utf-8")
        if is_init:
            packages.add(mod_name)
    return py_files, packages


def _compare(name: str) -> None:
    """Compare check_reach_file vs compute_reach on a fixture."""
    d = FIXTURES / name
    py_files, packages = _read_fixture(name)

    # Implementation output
    impl = check_reach_file(d)

    # Oracle: build expected output using ground truth helpers
    import_graph, all_modules = build_import_graph(py_files, packages)

    entrypoints: list[str] = []
    pyproject = d / "pyproject.toml"
    if pyproject.exists():
        entrypoints.extend(parse_scripts_toml(pyproject.read_text(encoding="utf-8")))
    entrypoints.extend(find_main_modules(py_files))

    production_modules = {m for m in all_modules if not m.startswith("tests.")}
    oracle = compute_reach(entrypoints, import_graph, production_modules)

    assert impl.status == oracle["status"], (
        f"{name}: status mismatch impl={impl.status} oracle={oracle['status']}"
    )
    assert tuple(sorted(impl.islands)) == tuple(sorted(oracle["islands"])), (
        f"{name}: islands mismatch\n"
        f"  impl={impl.islands}\n  oracle={oracle['islands']}"
    )
    assert tuple(sorted(impl.entrypoints)) == tuple(sorted(oracle["entrypoints"])), (
        f"{name}: entrypoints mismatch\n"
        f"  impl={impl.entrypoints}\n  oracle={oracle['entrypoints']}"
    )


# AC-FR0090-01@v0.4 TRACKS-TRACE clean fixture parity
def test_clean_parity():
    """AC-FR0090-01@v0.4 clean fixture: all modules reachable."""
    _compare("clean")


# AC-FR0090-02@v0.4 TRACKS-TRACE island fixture parity
def test_island_parity():
    """AC-FR0090-02@v0.4 island fixture: orphan module detected."""
    _compare("island")


# AC-FR0090-04@v0.4 TRACKS-TRACE no entries fixture parity
def test_no_entries_parity():
    """AC-FR0090-04@v0.4 no-entries fixture: error on no entrypoints."""
    _compare("no_entries")


# AC-FR0090-01@v0.4 TRACKS-TRACE package edge fixture parity
def test_package_edge_parity():
    """AC-FR0090-01@v0.4 package-edge fixture: ``from pkg import name``
    connects the submodule; relative imports (``from .sub import X``) resolve;
    package ``__init__.py`` files are reachable as implicit parent packages."""
    _compare("package_edge")
