"""FR-0090 trac check reach - module-level import-graph island detection.

Pure-function core (check_reach) + file-reading wrapper (check_reach_file).
Uses ast for static import analysis; targeted regex for pyproject [project.scripts]
(architecture.md §3.1). The FR-0120 [layout] contract is loaded through
tracks.project — the single truth the write auditor also consumes (B3, issue
#4): with a contract, reach scans only declared writable roots (whitelist);
without one it keeps the legacy whole-repo blacklist walk.
"""

from __future__ import annotations

import ast
import re
from collections import deque
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Literal

from tracks.project import ContractError, load_contract

_SCRIPTS_SECTION = re.compile(r"^\[project\.scripts\]\s*\n(.*?)(?=^\[|\Z)", re.M | re.S)
_SCRIPT_ENTRY = re.compile(r'^[\w-]+\s*=\s*["\']([^"\']+):')
_SCRIPT_ENTRY_NO_COLON = re.compile(r'^[\w-]+\s*=\s*["\']([^"\']+)["\']')
_PACKAGE_DATA_SECTION = re.compile(
    r"^\[tool\.setuptools\.package-data\]\s*\n(.*?)(?=^\[|\Z)", re.M | re.S
)
_PACKAGE_DATA_ENTRY = re.compile(r"^([\w.\-]+)\s*=\s*\[(.*?)\]", re.M | re.S)
_QUOTED_STRING = re.compile(r'"([^"]*)"')

_EXCLUDE_DIRS = frozenset(
    {
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        "node_modules",
        ".eggs",
        "build",
        "dist",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "_subprocess_coverage",
    }
)


@dataclass(frozen=True)
class ReachReport:
    """FR-0090 reach report (interfaces.md §1f)."""

    status: Literal["pass", "fail"]
    islands: tuple[str, ...]
    entrypoints: tuple[str, ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...] = ()


def _build_adjacency(
    import_graph: dict[str, set[str]],
) -> dict[str, set[str]]:
    """Build adjacency: module -> set of reachable modules via imports."""
    all_modules = set(import_graph.keys())
    adj: dict[str, set[str]] = {}
    for mod, imports in import_graph.items():
        targets: set[str] = set()
        for imp in imports:
            if imp in all_modules and imp != mod:
                targets.add(imp)
            else:
                prefix = imp + "."
                targets.update(
                    other for other in all_modules if other.startswith(prefix) and other != mod
                )
        adj[mod] = targets
    return adj


def _ancestors_in_graph(mod: str, all_modules: set[str]) -> list[str]:
    """Ancestor packages of ``mod`` that are themselves modules in the graph.

    Importing a module implicitly imports every parent package (Python executes
    each ``__init__.py`` on the path), so a reachable module makes its ancestor
    packages reachable too -- otherwise empty ``__init__.py`` files whose only
    submodule is imported would be false-positive islands.
    """
    parts = mod.split(".")
    return [
        ".".join(parts[:i])
        for i in range(len(parts) - 1, 0, -1)
        if ".".join(parts[:i]) in all_modules
    ]


def _bfs_reachable(
    entrypoints: list[str],
    adj: dict[str, set[str]],
    all_modules: set[str],
) -> set[str]:
    """BFS from entrypoints through the adjacency graph.

    Visiting a module also visits its ancestor packages (implicit parent
    imports) so package ``__init__.py`` files are not false-positive islands.
    """
    reachable: set[str] = set()
    queue: deque[str] = deque()
    for ep in entrypoints:
        mod = ep if ep in all_modules else ep.split(":")[0]
        if mod in all_modules:
            queue.append(mod)
    while queue:
        mod = queue.popleft()
        if mod in reachable:
            continue
        reachable.add(mod)
        for anc in _ancestors_in_graph(mod, all_modules):
            if anc not in reachable:
                queue.append(anc)
        queue.extend(n for n in adj.get(mod, set()) if n not in reachable)
    return reachable


def check_reach(
    entrypoints: list[str],
    import_graph: dict[str, set[str]],
    production_modules: set[str],
    baseline: dict | None = None,
) -> ReachReport:
    """FR-0090 module-level import-graph island detection (pure).

    BFS from entrypoints; unreachable production modules are islands.
    No entrypoint declaration -> errors non-empty, status=fail.
    baseline modules not counted as islands. Order stable (NFR-0020).
    """
    baseline_mods: set[str] = set()
    if baseline is not None:
        baseline_mods = set(baseline.get("reach_exemptions", {}).get("modules", []))

    errors: list[str] = []
    if not entrypoints:
        errors.append("no entrypoints declared")

    all_modules = set(import_graph.keys())
    adj = _build_adjacency(import_graph)
    reachable = _bfs_reachable(entrypoints, adj, all_modules)

    islands = sorted(
        mod for mod in production_modules if mod not in reachable and mod not in baseline_mods
    )

    status = "fail" if (islands or errors) else "pass"
    return ReachReport(
        status=status,
        islands=tuple(islands),
        entrypoints=tuple(sorted(entrypoints)),
        errors=tuple(sorted(errors)),
    )


def _module_name(path: Path, root: Path) -> str:
    """Dotted module name of a .py file relative to root."""
    rel = path.relative_to(root)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]
    return ".".join(parts)


def _resolve_relative(package: str, level: int, module: str | None) -> str | None:
    """Resolve a relative import to an absolute base module name.

    `package` is the importing module's package (the module itself when it is
    an ``__init__.py``). ``level`` >= 1; ``module`` is the ``from`` part (may
    be None for ``from . import X``). Returns the absolute base, or None when
    the resolution climbs above the top level.
    """
    parts = package.split(".") if package else []
    up = level - 1  # level 1 = current package, 2 = parent, ...
    if up > len(parts):
        return None
    base = ".".join(parts[: len(parts) - up])
    if module:
        return f"{base}.{module}" if base else module
    return base


def _importfrom_targets(node: ast.ImportFrom, package: str) -> set[str]:
    """Resolve an ImportFrom node to its target module names (absolute + the
    ``module.name`` candidates for the imported names)."""
    if node.level == 0:
        if not node.module:
            return set()
        base = node.module
    else:
        base = _resolve_relative(package, node.level, node.module) or ""
    if not base:
        return set()
    targets = {base}
    targets.update(f"{base}.{alias.name}" for alias in node.names)
    return targets


def _extract_imports(tree: ast.AST, package: str = "") -> set[str]:
    """Extract imported module names from an AST.

    For ``from pkg import name`` (absolute) both ``pkg`` and ``pkg.name`` are
    emitted, so an imported name that is itself a submodule connects to it
    (FR-0090 package-edge: ``from tracks import paths`` -> ``tracks.paths``).
    Relative imports (level > 0) are resolved against ``package``.
    """
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.update(_importfrom_targets(node, package))
    return modules


def _parse_scripts_toml(toml_text: str) -> list[str]:
    """Parse [project.scripts] section from pyproject.toml (targeted regex)."""
    m = _SCRIPTS_SECTION.search(toml_text)
    if not m:
        return []
    entries: list[str] = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m2 = _SCRIPT_ENTRY.match(line)
        if m2:
            entries.append(m2.group(1))
        else:
            m3 = _SCRIPT_ENTRY_NO_COLON.match(line)
            if m3:
                entries.append(m3.group(1))
    return entries


def _layout_scan_roots(repo: Path) -> tuple[list[Path] | None, tuple[str, ...]]:
    """B3 (issue #4): scan roots = union of every role's writable dirs from
    the project.toml [layout] contract (FR-0120) — the same truth the write
    auditor consumes, resolving the two-sources-of-truth split.

    Returns ``(None, ())`` when no contract or no [layout] section exists, so
    the caller falls back to the legacy blacklist walk (backward compat).
    Declared-but-missing roots are skipped with a warning; a contract whose
    roots are all missing still yields whitelist mode with an empty list —
    it never silently re-opens the whole-repo scan.
    """
    try:
        contract = load_contract(repo)
    except ContractError:
        return None, ()
    if contract.layout is None:
        return None, ()
    roots: list[Path] = []
    warnings: list[str] = []
    for field in fields(contract.layout):
        agent = getattr(contract.layout, field.name)
        if agent is None:
            continue
        for entry in agent.writable:
            root = repo / entry
            if root.is_dir():
                roots.append(root)
            else:
                warnings.append(f"layout root not found on disk, skipped: {entry}")
    return roots, tuple(warnings)


def _find_py_files(repo: Path, roots: list[Path] | None = None) -> list[Path]:
    """Find .py files to scan. ``roots is None`` -> legacy blacklist walk of
    the whole repo (no [layout] contract). Otherwise scan only under the
    declared writable roots (B3 whitelist); ``_EXCLUDE_DIRS`` hygiene still
    applies inside the roots and overlapping roots are deduplicated."""
    if roots is None:
        candidates = sorted(repo.rglob("*.py"))
    else:
        candidates = sorted(p for root in roots for p in root.rglob("*.py"))
    files: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if not any(part in _EXCLUDE_DIRS for part in path.relative_to(repo).parts):
            files.append(path)
    return files


def _is_test_module(mod_name: str) -> bool:
    """Check if a module is a test module (under tests/)."""
    return mod_name.startswith("tests.") or mod_name == "tests"


def _discover_entrypoints(repo: Path, py_files: list[Path], extra: list[str]) -> list[str]:
    """Discover entrypoints from pyproject, __main__.py, and whitelist."""
    entrypoints: list[str] = list(extra)
    pyproject = repo / "pyproject.toml"
    if pyproject.exists():
        entrypoints.extend(_parse_scripts_toml(pyproject.read_text(encoding="utf-8")))
    for py_file in py_files:
        if py_file.name == "__main__.py":
            entrypoints.append(_module_name(py_file, repo))
    entries_file = repo / ".tracks" / "reach-entries.txt"
    if entries_file.exists():
        for line in entries_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                entrypoints.append(line)
    # Deduplicate preserving order.
    seen: set[str] = set()
    return [ep for ep in entrypoints if not (ep in seen or seen.add(ep))]


def _package_data_patterns(repo: Path) -> dict[str, list[str]]:
    """Data-driven wheel package-data allowlist: package -> path patterns from
    the host ``pyproject.toml [tool.setuptools.package-data]`` section.

    The scaffolds freeze demo-host Python files and their tests as wheel data
    (never product modules); reach must treat ONLY assets the host itself
    allowlists as data -- no hardcoded asset paths, no broad exemption.
    """
    pyproject = repo / "pyproject.toml"
    if not pyproject.exists():
        return {}
    m = _PACKAGE_DATA_SECTION.search(pyproject.read_text(encoding="utf-8"))
    if not m:
        return {}
    patterns: dict[str, list[str]] = {}
    for entry in _PACKAGE_DATA_ENTRY.finditer(m.group(1)):
        pats = _QUOTED_STRING.findall(entry.group(2))
        if pats:
            patterns[entry.group(1)] = pats
    return patterns


def _package_data_py_files(repo: Path, patterns: dict[str, list[str]]) -> set[Path]:
    """Resolve the package-data allowlist to concrete .py files, by globbing
    every declared pattern under its package directory (patterns are relative
    to the package dir, setuptools package-data semantics)."""
    matched: set[Path] = set()
    for package, pats in patterns.items():
        pkg_dir = repo / package
        if not pkg_dir.is_dir():
            continue
        for pat in pats:
            try:
                hits = pkg_dir.glob(pat)
            except (ValueError, OSError):
                continue
            matched.update(path for path in hits if path.is_file() and path.suffix == ".py")
    return matched


def _build_graph(
    py_files: list[Path],
    repo: Path,
    package_data_files: set[Path] | None = None,
) -> tuple[dict[str, set[str]], set[str]]:
    """Build import graph and production module set.

    The package context for relative-import resolution is the module itself
    when it is an ``__init__.py`` (the package), otherwise its parent.

    ``package_data_files`` are the Python assets the host declares in
    ``pyproject.toml [tool.setuptools.package-data]`` (wheel data, never
    product modules): they stay in the import graph but are EXCLUDED from
    the production set, so they can never surface as reach islands
    (IF-FAILCLOSED-001 / interfaces section 4 table 8).
    """
    package_data_files = package_data_files or set()
    import_graph: dict[str, set[str]] = {}
    production: set[str] = set()
    for py_file in py_files:
        mod_name = _module_name(py_file, repo)
        is_init = py_file.name == "__init__.py"
        package = mod_name if is_init else mod_name.rsplit(".", 1)[0] if "." in mod_name else ""
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            imports = _extract_imports(tree, package)
        except SyntaxError:
            imports = set()
        import_graph[mod_name] = imports
        if not _is_test_module(mod_name) and py_file not in package_data_files:
            production.add(mod_name)
    return import_graph, production


def check_reach_file(
    repo: Path,
    baseline: dict | None = None,
    extra_entries: list[str] | None = None,
) -> ReachReport:
    """FR-0090 file-reading wrapper: parses pyproject.toml [project.scripts],
    scans package dirs for __main__.py, reads .tracks/reach-entries.txt whitelist,
    ast-parses .py files to build import graph, calls check_reach. Scan scope:
    FR-0120 [layout] whitelist when a contract exists, legacy blacklist otherwise.
    """
    extra_entries = extra_entries or []
    roots, layout_warnings = _layout_scan_roots(repo)
    py_files = _find_py_files(repo, roots)
    if not py_files:
        return ReachReport(
            status="pass",
            islands=(),
            entrypoints=(),
            errors=(),
            warnings=(
                "no Python files found; reach check not applicable",
                *layout_warnings,
            ),
        )
    import_graph, production = _build_graph(
        py_files,
        repo,
        _package_data_py_files(repo, _package_data_patterns(repo)),
    )
    if not production:
        return ReachReport(
            status="pass",
            islands=(),
            entrypoints=(),
            errors=(),
            warnings=(
                "no production modules found; reach check not applicable",
                *layout_warnings,
            ),
        )
    entrypoints = _discover_entrypoints(repo, py_files, extra_entries)
    report = check_reach(entrypoints, import_graph, production, baseline)
    if layout_warnings:
        report = replace(report, warnings=report.warnings + layout_warnings)
    return report
