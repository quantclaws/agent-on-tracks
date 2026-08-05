"""FR-0090 trac check reach - module-level import-graph island detection.

Pure-function core (check_reach) + file-reading wrapper (check_reach_file).
Uses ast for static import analysis; targeted regex for pyproject [project.scripts]
(no tomli/tomllib dependency, architecture.md §3.1).
"""
from __future__ import annotations

import ast
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_SCRIPTS_SECTION = re.compile(
    r"^\[project\.scripts\]\s*\n(.*?)(?=^\[|\Z)", re.M | re.S
)
_SCRIPT_ENTRY = re.compile(r'^[\w-]+\s*=\s*["\']([^"\']+):')
_SCRIPT_ENTRY_NO_COLON = re.compile(r'^[\w-]+\s*=\s*["\']([^"\']+)["\']')

_EXCLUDE_DIRS = frozenset({
    "__pycache__", ".git", ".venv", "venv", "node_modules",
    ".eggs", "build", "dist", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "_subprocess_coverage",
})


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
                    other for other in all_modules
                    if other.startswith(prefix) and other != mod
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
    return [".".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)
            if ".".join(parts[:i]) in all_modules]


def _bfs_reachable(
    entrypoints: list[str], adj: dict[str, set[str]], all_modules: set[str],
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
        queue.extend(
            n for n in adj.get(mod, set()) if n not in reachable
        )
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
        mod for mod in production_modules
        if mod not in reachable and mod not in baseline_mods
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


def _find_py_files(repo: Path) -> list[Path]:
    """Find all .py files in repo, excluding non-production directories."""
    return [
        path for path in sorted(repo.rglob("*.py"))
        if not any(part in _EXCLUDE_DIRS for part in path.relative_to(repo).parts)
    ]


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


def _build_graph(
    py_files: list[Path], repo: Path,
) -> tuple[dict[str, set[str]], set[str]]:
    """Build import graph and production module set.

    The package context for relative-import resolution is the module itself
    when it is an ``__init__.py`` (the package), otherwise its parent.
    """
    import_graph: dict[str, set[str]] = {}
    production: set[str] = set()
    for py_file in py_files:
        mod_name = _module_name(py_file, repo)
        is_init = py_file.name == "__init__.py"
        package = (mod_name if is_init
                   else mod_name.rsplit(".", 1)[0] if "." in mod_name else "")
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            imports = _extract_imports(tree, package)
        except SyntaxError:
            imports = set()
        import_graph[mod_name] = imports
        if not _is_test_module(mod_name):
            production.add(mod_name)
    return import_graph, production


def check_reach_file(
    repo: Path,
    baseline: dict | None = None,
    extra_entries: list[str] | None = None,
) -> ReachReport:
    """FR-0090 file-reading wrapper: parses pyproject.toml [project.scripts],
    scans package dirs for __main__.py, reads .tracks/reach-entries.txt whitelist,
    ast-parses .py files to build import graph, calls check_reach.
    """
    extra_entries = extra_entries or []
    py_files = _find_py_files(repo)
    if not py_files:
        return ReachReport(
            status="pass",
            islands=(),
            entrypoints=(),
            errors=(),
            warnings=("no Python files found; reach check not applicable",),
        )
    entrypoints = _discover_entrypoints(repo, py_files, extra_entries)
    import_graph, production = _build_graph(py_files, repo)
    return check_reach(entrypoints, import_graph, production, baseline)
