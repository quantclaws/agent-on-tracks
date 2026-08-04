"""Independent reference implementation for reach tool (FR-0090).

Ground truth (test-plan §3.4): recomputes the module-level import-graph island
list independently of tracks.checks.reach, using only the standard library.
Shield's tests compare tracks.checks.reach.check_reach output against this
script's output on the same fixture data.

Isolation rules (test-plan §3.2):
- Must NOT import tracks.* (CI static check blocks merge on violation).
- Only standard library (ast/pathlib/re) + test data files.
- Read fixture files directly from tests/assets/reach_fixtures/.
"""
from __future__ import annotations

import ast
import re
from collections import deque
from pathlib import Path


def _module_name(path: Path, root: Path) -> str:
    """Dotted module name of a .py file relative to root."""
    rel = path.relative_to(root)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]  # strip .py
    return ".".join(parts)


def _extract_imports(tree: ast.AST) -> set[str]:
    """Extract imported module names from an AST.

    Returns full dotted module paths (e.g. "tracks.kernel.machine"), not just
    top-level packages. Relative imports (level > 0) are skipped (cannot
    resolve without package context; reach is module-level, not function-level).
    """
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def build_import_graph(
    py_files: dict[str, str],
) -> tuple[dict[str, set[str]], set[str]]:
    """Build module-level import graph from {module_name: file_content}.

    Returns (import_graph, all_modules) where:
    - import_graph: {module_name: {top_level_imported_package, ...}}
    - all_modules: set of all module names discovered
    """
    import_graph: dict[str, set[str]] = {}
    all_modules: set[str] = set()
    for mod_name, content in py_files.items():
        all_modules.add(mod_name)
        try:
            tree = ast.parse(content)
            imports = _extract_imports(tree)
        except SyntaxError:
            imports = set()
        import_graph[mod_name] = imports
    return import_graph, all_modules


def parse_scripts_toml(toml_text: str) -> list[str]:
    """Parse [project.scripts] section from pyproject.toml.

    Returns a list of entry module names (e.g. ["tracks.cli.main"]).
    Uses a targeted regex parser (no tomli dependency).
    """
    # Find the [project.scripts] section
    m = re.search(
        r"^\[project\.scripts\]\s*\n(.*?)(?=^\[|\Z)",
        toml_text, re.M | re.S)
    if not m:
        return []
    entries: list[str] = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Format: name = "module:func" or name = 'module:func'
        # Capture the module part (before the colon).
        m2 = re.match(r'^[\w-]+\s*=\s*["\']([^"\']+):', line)
        if m2:
            entries.append(m2.group(1))
        else:
            # No colon: entry might be just "module" (no function)
            m3 = re.match(r'^[\w-]+\s*=\s*["\']([^"\']+)["\']', line)
            if m3:
                entries.append(m3.group(1))
    return entries


def find_main_modules(py_files: dict[str, str]) -> list[str]:
    """Find modules that have a __main__.py (entrypoint candidates)."""
    return [name for name in py_files if name.endswith(".__main__")]


def compute_reach(
    entrypoints: list[str],
    import_graph: dict[str, set[str]],
    production_modules: set[str],
    baseline: set[str] | None = None,
) -> dict:
    """Recompute the island list independently.

    Returns {"status": "pass"|"fail", "islands": [...], "entrypoints": [...],
    "errors": [...]}.

    BFS from entrypoints through the import graph; unreachable production
    modules are islands. Test modules (tests/...) are excluded.
    """
    if baseline is None:
        baseline = set()

    errors: list[str] = []
    if not entrypoints:
        errors.append("no entrypoints declared")

    # Build adjacency: module -> set of reachable modules via imports.
    # An import "tracks.kernel.machine" connects to that exact module (or to
    # all modules under it if it's a package with no matching module).
    all_modules = set(import_graph.keys())
    adj: dict[str, set[str]] = {}
    for mod, imports in import_graph.items():
        targets: set[str] = set()
        for imp in imports:
            if imp in all_modules and imp != mod:
                targets.add(imp)
            else:
                # The import might be a package; connect to modules under it
                prefix = imp + "."
                for other in all_modules:
                    if other.startswith(prefix) and other != mod:
                        targets.add(other)
        adj[mod] = targets

    # BFS from entrypoints
    reachable: set[str] = set()
    queue: deque[str] = deque()
    for ep in entrypoints:
        if ep in all_modules:
            queue.append(ep)
        else:
            # entrypoint might be a function reference like "tracks.cli.main:main"
            mod_part = ep.split(":")[0]
            if mod_part in all_modules:
                queue.append(mod_part)
    while queue:
        mod = queue.popleft()
        if mod in reachable:
            continue
        reachable.add(mod)
        for neighbor in adj.get(mod, set()):
            if neighbor not in reachable:
                queue.append(neighbor)

    # Islands: production modules not reachable, not in baseline
    islands = sorted(
        mod for mod in production_modules
        if mod not in reachable
        and mod not in baseline
    )

    status = "fail" if (islands or errors) else "pass"
    return {
        "status": status,
        "islands": islands,
        "entrypoints": sorted(entrypoints),
        "errors": sorted(errors),
    }
