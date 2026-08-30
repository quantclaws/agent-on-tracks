"""T-016 (replan x3) RED: explicit package-data Python assets are not islands.

interfaces section 4 table 8 and the Scaffold declaration freeze the wheel
package-data Python assets (``tracks/assets/demo_host/demo_calc.py`` and the
demo ``tests/**``) as data -- never imported as product modules.  The current
``check_reach`` treats every ``.py`` under the writable root as production, so
it wrongly reports the four package-data Python assets as ISLAND_GATE_2 reach
islands (false positive).

This pins the fix: ``check_reach_file`` must data-drive the host
``pyproject.toml [tool.setuptools.package-data]`` explicit allowlist, match the
declared patterns, and exclude matched Python data assets from the production/
island sets -- no hardcoded ``demo_host`` path, no fabricated product import or
reach entry, no broad exemption.  The same fixture proves an ordinary
unreferenced module is STILL an island, so the exclusion is provably narrow
and data-driven.  Each assertion carries the reach/package-data contract token.
"""

from __future__ import annotations

from pathlib import Path

from tracks.checks.reach import check_reach_file

_DEMO_ASSETS = (
    "pkg.assets.demo_host.demo_calc",
    "pkg.assets.demo_host.tests.unit.test_demo_calc",
    "pkg.assets.demo_host.tests.integration.test_demo_contract",
    "pkg.assets.demo_host.tests.e2e.test_demo_journey",
)
_ORDINARY_ORPHAN = "pkg.orphan"

# The host pyproject.toml real allowlist shape (replan x3 red_defect re-pin):
# the demo-host wheel package-data declares demo_calc.py AND the three demo
# tests/** dirs as data -- the fixture must mirror ALL four patterns or the
# assertions are unsatisfiable by any correct implementation.
_DEMO_HOST_PATTERNS = (
    "assets/demo_host/demo_calc.py",
    "assets/demo_host/tests/unit/*",
    "assets/demo_host/tests/integration/*",
    "assets/demo_host/tests/e2e/*",
)


def _package_data_repo(tmp_path, *, asset_patterns: list[str]) -> Path:
    """Host repo: pyproject package-data allowlist (all *asset_patterns*) +
    [project.scripts] entry, one entrypoint module, and the package-data assets
    written by the caller."""
    repo = tmp_path / "host"
    repo.mkdir()
    body = "\n".join(f'  "{pat}",' for pat in asset_patterns)
    (repo / "pyproject.toml").write_text(
        "[project.scripts]\ntrac = \"pkg.main:main\"\n\n"
        "[tool.setuptools.package-data]\npkg = [\n"
        f"{body}\n"
        "]\n",
        encoding="utf-8",
    )
    (repo / "pkg").mkdir()
    (repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pkg" / "main.py").write_text("X = 1\n", encoding="utf-8")
    return repo


def _write_demo_host_assets(repo: Path) -> None:
    """Write the demo-host wheel package-data Python assets (the four that the
    current reach reports as false-positive islands)."""
    demo = repo / "pkg" / "assets" / "demo_host"
    demo.mkdir(parents=True)
    (demo / "demo_calc.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8"
    )
    for rel, body in (
        ("tests/unit/test_demo_calc.py", "def test_add():\n    assert True\n"),
        ("tests/integration/test_demo_contract.py", "def test_contract():\n    assert True\n"),
        ("tests/e2e/test_demo_journey.py", "def test_journey():\n    assert True\n"),
    ):
        target = demo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")


def _write_ordinary_orphan(repo: Path) -> None:
    """Ordinary unreferenced production module: must remain an island."""
    (repo / "pkg" / "orphan.py").write_text("X = 1\n", encoding="utf-8")


def test_explicit_package_data_python_assets_are_not_production_islands(tmp_path):
    """interfaces section 4 table 8 / IF-FAILCLOSED-001: the demo-host wheel
    package-data Python assets (demo_calc.py + demo tests/**) are declared data,
    never product modules -- check_reach must exclude them from islands.  The
    same fixture must keep the ordinary unreferenced module an island, proving
    the exclusion is narrow and data-driven, not a broad exemption."""
    repo = _package_data_repo(tmp_path, asset_patterns=list(_DEMO_HOST_PATTERNS))
    _write_demo_host_assets(repo)
    _write_ordinary_orphan(repo)

    report = check_reach_file(repo)
    islands = set(report.islands)
    for mod in _DEMO_ASSETS:
        assert mod not in islands, (
            "IF-FAILCLOSED-001/reach package-data: explicitly allowlisted "
            f"Python data asset must not be a production island, got {mod} in "
            f"islands {sorted(islands)}"
        )
    assert _ORDINARY_ORPHAN in islands, (
        "IF-FAILCLOSED-001/reach package-data: an ordinary unreferenced module "
        f"must remain an island; missing from {sorted(islands)}"
    )


def test_package_data_exclusion_is_data_driven_not_hardcoded(tmp_path):
    """The exclusion must come from the host pyproject [tool.setuptools.
    package-data] allowlist, not a hardcoded demo_host special case: a Python
    data asset at a DIFFERENT declared path is excluded too, while the orphan
    stays an island."""
    repo = _package_data_repo(tmp_path, asset_patterns=["assets/templates/seed.py"])
    (repo / "pkg" / "assets" / "templates").mkdir(parents=True)
    (repo / "pkg" / "assets" / "templates" / "seed.py").write_text(
        "SEED = 1\n", encoding="utf-8"
    )
    _write_ordinary_orphan(repo)

    report = check_reach_file(repo)
    islands = set(report.islands)
    assert "pkg.assets.templates.seed" not in islands, (
        "IF-FAILCLOSED-001/reach package-data: allowlisted Python data asset at "
        f"a non-demo_host path must be excluded from islands, got "
        f"{sorted(islands)}"
    )
    assert _ORDINARY_ORPHAN in islands, (
        "IF-FAILCLOSED-001/reach package-data: ordinary unreferenced module "
        f"must remain an island; missing from {sorted(islands)}"
    )
