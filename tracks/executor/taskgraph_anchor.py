"""Task-graph anchor-surface + scope-existence gates (OOB b89 S2).

Extracted from ``taskgraph.py`` for module-size compliance (C0302). Pure
functions; :mod:`tracks.executor.taskgraph` re-exports every name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from tracks.executor.taskgraph_parse import TaskNode
from tracks.executor.taskgraph_validate import _parse_scope_paths, _scope_paths


def _anchor_ast_modules(anchors_map: dict, ref: str) -> list[str]:
    entry = anchors_map.get(ref)
    if not isinstance(entry, dict):
        return []
    if "ast_modules" in entry:
        return [m for m in entry.get("ast_modules", []) if isinstance(m, str)]
    return [m for m in entry.get("modules", []) if isinstance(m, str)]

def _anchor_files_for_refs(anchors_map: dict, refs, repo=None) -> set[str]:
    out: set[str] = set()
    for ref in refs or ():
        if not isinstance(ref, str) or not ref:
            continue
        for mod in _anchor_ast_modules(anchors_map, ref):
            try:
                out.add(_module_to_path(mod, repo))
            except Exception:
                out.add(mod.replace(".", "/") + ".py")
    return out

def _scope_entry_covered(entry: str, anchor_files: set[str]) -> bool:
    base = entry.rstrip("/")
    for path in anchor_files:
        if path in (entry, base):
            return True
        if path.startswith(base + "/"):
            return True
    return False

def scope_anchor_advisories(tasks, surface: dict | None, repo=None) -> list[str]:
    """#133 advisory: scope files with no static anchor-module coverage."""
    if not tasks or not isinstance(surface, dict):
        return []
    anchors_map = surface.get("anchors", {})
    if not isinstance(anchors_map, dict):
        return []
    advisories: list[str] = []
    for task in tasks:
        if getattr(task, "debt", ()):
            continue
        refs = [
            *(getattr(task, "acceptance_refs", ()) or ()),
            *(getattr(task, "deferred_refs", ()) or ()),
            *(getattr(task, "unit_refs", ()) or ()),
        ]
        anchor_files = _anchor_files_for_refs(anchors_map, refs, repo)
        if not anchor_files:
            continue
        scope_files = set(_parse_scope_paths(getattr(task, "scope_boundary", "") or ""))
        diff = sorted(s for s in scope_files if not _scope_entry_covered(s, anchor_files))
        if diff:
            advisories.append(
                f"{task.task_id}: scope files without anchor coverage: "
                + ", ".join(diff)
            )
    return advisories

def _module_to_path(mod: str, repo: Path | str | None = None) -> str:
    """OOB b89 S2 mapping ``tracks.foo.bar`` → ``tracks/foo/bar.py``.

    If ``tracks/foo/bar/__init__.py`` exists (relative to *repo* or
    ``Path.cwd()`` when *repo* is ``None``), the module is a package and
    maps to the directory ``tracks/foo/bar``; otherwise it maps to the file
    ``tracks/foo/bar.py``. Slashes are always ``/``.

    The ``repo`` parameter exists so unit tests can probe both morphologies
    against a temporary repo; callers that guarantee ``cwd`` is the repo root
    may omit it (default ``Path.cwd()``).
    """
    # Normalise module → posix path + ".py"
    base = mod.replace(".", "/") + ".py"
    candidate_dir = mod.replace(".", "/")
    root = Path(repo).resolve() if repo is not None else Path.cwd().resolve()
    init_path = root / candidate_dir / "__init__.py"
    if init_path.is_file():
        return Path(candidate_dir).as_posix()
    return Path(base).as_posix()

@dataclass(frozen=True)
class _ScopeIndex:
    """Scope → owner / per-task scope sets + task lookup (one graph pass)."""

    all_scopes: set[str]
    owner_map: dict[str, str]
    task_scopes: dict[str, set[str]]
    task_by_id: dict[str, TaskNode]


def _scope_index(tasks: list[TaskNode]) -> _ScopeIndex:
    all_scopes: set[str] = set()
    owner_map: dict[str, str] = {}
    task_scopes: dict[str, set[str]] = {}
    for t in tasks:
        paths, _errs = _scope_paths(t.scope_boundary, t.task_id)
        normed: set[str] = set()
        for p in paths:
            # _scope_paths already normalises (\→/, rstrip "/")
            normed.add(p)
            all_scopes.add(p)
            owner_map[p] = t.task_id
        task_scopes[t.task_id] = normed
    task_by_id: dict[str, TaskNode] = {t.task_id: t for t in tasks}
    return _ScopeIndex(all_scopes, owner_map, task_scopes, task_by_id)


def _closure_scopes(index: _ScopeIndex, task_id: str) -> set[str]:
    """Union of scopes owned by transitive ``depends_on`` of *task_id*."""
    stack: list[str] = (
        list(index.task_by_id[task_id].depends_on) if task_id in index.task_by_id else []
    )
    visited: set[str] = set()
    scopes: set[str] = set()
    while stack:
        cur = stack.pop()
        if cur == "-" or cur in visited:
            continue
        visited.add(cur)
        if cur in index.task_scopes:
            scopes.update(index.task_scopes[cur])
        if cur in index.task_by_id:
            stack.extend(index.task_by_id[cur].depends_on)
    return scopes


def _anchors_map(surface) -> dict:
    if not isinstance(surface, dict):
        return {}
    anchors_map = surface.get("anchors", {})
    return anchors_map if isinstance(anchors_map, dict) else {}


def _split_anchor_modules(entry) -> tuple[list[str], list[str]]:
    """AST-declared vs dynamic modules of one sidecar entry (legacy → ast)."""
    if not isinstance(entry, dict):
        return [], []
    if "ast_modules" in entry and "dynamic_modules" in entry:
        return (
            [m for m in entry.get("ast_modules", []) if isinstance(m, str)],
            [m for m in entry.get("dynamic_modules", []) if isinstance(m, str)],
        )
    return [m for m in entry.get("modules", []) if isinstance(m, str)], []


def _map_paths(modnames: list[str]) -> set[str]:
    """Map module names to repo-relative paths (deterministic fallback)."""
    out: set[str] = set()
    for m in modnames:
        try:
            out.add(_module_to_path(m))
        except Exception:
            out.add(m.replace(".", "/") + ".py")
    return out


def _anchor_edge_violations(
    index: _ScopeIndex,
    task: TaskNode,
    anchor: str,
    needs: set[str],
    own: set[str],
    dep_scopes: set[str],
    violations: list[str],
) -> None:
    """Missing depends_on edge for AST-declared, owned anchor modules."""
    for path in sorted(needs - own - dep_scopes):
        owner = index.owner_map.get(path, "unknown")
        violations.append(
            f"{task.task_id} anchor {anchor} exercises {path} "
            f"owned by {owner} without depends_on edge"
        )


def _anchor_findings(
    index: _ScopeIndex,
    task: TaskNode,
    anchor: str,
    entry,
    violations: list[str],
    advisories: list[str],
) -> None:
    """Hard-gate/advisory findings for one acceptance anchor."""
    if entry is None:
        violations.append(f"anchor {anchor} missing from anchor-surface sidecar")
        return
    ast_mods, dyn_mods = _split_anchor_modules(entry)
    ast_mapped = _map_paths(ast_mods)
    dyn_mapped = _map_paths(dyn_mods)
    # dynamic-only = modules loaded by shared infra / fixtures, not explicitly
    # declared by the test file → advisories only
    dyn_only = dyn_mapped - ast_mapped
    for mp in sorted(ast_mapped - index.all_scopes):
        advisories.append(f"anchor {anchor} imports unowned tracks module {mp}")
    own = index.task_scopes.get(task.task_id, set())
    dep_scopes = _closure_scopes(index, task.task_id)
    needs = ast_mapped & index.all_scopes
    _anchor_edge_violations(index, task, anchor, needs, own, dep_scopes, violations)
    # dynamic-only → advisories (owned missing edge and unowned)
    for mp in sorted(dyn_only - index.all_scopes):
        advisories.append(
            f"anchor {anchor} dynamically loads unowned tracks module {mp} (advisory)"
        )
    for mp in sorted((dyn_only & index.all_scopes) - own - dep_scopes):
        advisories.append(
            f"anchor {anchor} dynamically loads owned tracks module {mp} (advisory)"
        )


def validate_anchor_satisfiability(
    tasks: list[TaskNode],
    surface: dict,
) -> tuple[bool, list[str], list[str]]:
    """OOB b89 S2 anchor satisfiability hard gate (pure; ast/dynamic split).

    Per anchor entry, modules are split into:

    * ``ast_modules`` — modules the test file *explicitly declares* via AST
      scan (top-level and function-body ``import``/``ImportFrom``). These are
      the dependencies Archer is expected to have derived from docs/code, so
      **hard-gate violations** come from them only.
    * ``dynamic_modules`` — the subprocess ``sys.modules`` snapshot (origin
      inside repo ``tracks/``). This includes conftest/fixture framework
      loading (shared infra), so dynamic-only modules (``dynamic − ast``)
      produce **advisories only**, never violations.

    Backward compatibility: a legacy entry without ``ast_modules`` /
    ``dynamic_modules`` is treated as ``ast_modules = modules`` (preserving
    the pre-split hard-gate behaviour); a new entry read by an old lint
    simply uses ``modules`` and ignores the extra fields.

    Rules:

    * ``ast`` mapped via :func:`_module_to_path` ∩ all task scopes, minus own
      scope minus transitive ``depends_on`` closure → violation
      ``{T} anchor {a} exercises {path} owned by {owner} without depends_on edge``.
    * ``ast`` mapped − all scopes (unowned) → advisory
      ``anchor {a} imports unowned tracks module {mp}``.
    * dynamic-only mapped − all scopes → advisory
      ``anchor {a} dynamically loads unowned tracks module {mp} (advisory)``.
    * dynamic-only mapped ∩ scopes, missing edge → advisory
      ``anchor {a} dynamically loads owned tracks module {mp} (advisory)``.
    * Missing anchor entry → fail-closed violation
      ``anchor {a} missing from anchor-surface sidecar``.
    * Any ``task.schema == 1`` (legacy) → skip whole check
      ``(True, [], [])``.

    Returns ``(ok, violations, advisories)``; ``ok`` is ``True`` iff
    ``violations`` is empty.
    """
    if not tasks:
        return (True, [], [])
    # legacy skip — any task with schema 1 (per OB-2 brief)
    if any(getattr(t, "schema", 1) == 1 for t in tasks):
        return (True, [], [])

    index = _scope_index(tasks)
    anchors_map = _anchors_map(surface)
    violations: list[str] = []
    advisories: list[str] = []

    for t in tasks:
        # Per S2, only tasks that declare acceptance anchors participate.
        refs = getattr(t, "acceptance_refs", None)
        if refs is None:
            refs = getattr(t, "test_refs", ()) or ()
        # empty refs → nothing to check for this task
        if not refs:
            continue
        for a in refs:
            if not isinstance(a, str) or not a:
                continue
            _anchor_findings(index, t, a, anchors_map.get(a), violations, advisories)

    # Anchors in the sidecar that no task references are ignored (they are not
    # part of the needs/advisory contract); the per-anchor advisory already
    # covers all anchors referenced by tasks.
    ok = not violations
    return (ok, violations, advisories)

def _design_doc_text(design_doc_texts, design_docs_text) -> str:
    """Normalise the accepted design-doc input shapes to one text."""
    if design_docs_text is not None:
        return design_docs_text
    if isinstance(design_doc_texts, dict):
        parts: list[str] = []
        for v in design_doc_texts.values():
            if isinstance(v, str):
                parts.append(v)
            else:
                parts.append(str(v))
        return "\n".join(parts)
    if isinstance(design_doc_texts, str):
        return design_doc_texts
    if design_doc_texts is None and design_docs_text is None:
        return ""
    return str(design_doc_texts)


def _declared_tokens(design_text: str) -> tuple[set[str], set[str]]:
    # Token extraction (rev3 Notes, exact — token-precise, no naive substring)
    # Use word-boundary style guard so ``my_tracks/a.py`` does not yield
    # ``tracks/a.py`` and ``tracks/a.py`` is not declared by ``tracks/a.py.bak``.
    declared: set[str] = set(
        re.findall(
            r"(?<![A-Za-z0-9_])tracks/(?:[A-Za-z0-9_.\-]+/)*[A-Za-z0-9_.\-]+\.[A-Za-z0-9]+\b",
            design_text,
        )
    )
    declared_dirs = set(
        re.findall(r"(?<![A-Za-z0-9_])tracks/(?:[A-Za-z0-9_.\-]+/)+", design_text)
    )
    return declared, declared_dirs


def _scope_declared(normalized: str, declared: set[str], declared_dirs: set[str]) -> bool:
    """Design-side membership: exact file token, dir token or dir prefix."""
    is_dir_scope = "." not in normalized.rsplit("/", 1)[-1]
    if not is_dir_scope:
        return normalized in declared
    if normalized in declared or normalized + "/" in declared_dirs:
        return True
    return any(d.startswith(normalized + "/") for d in declared)


def _scope_existence_errors(
    tasks: list[TaskNode],
    repo_root: Path,
    declared: set[str],
    declared_dirs: set[str],
) -> list[str]:
    errors: list[str] = []
    for t in tasks:
        paths, fmt_errs = _scope_paths(t.scope_boundary, t.task_id)
        if fmt_errs:
            continue
        for p in paths:
            normalized = p.replace("\\", "/").rstrip("/")
            if (repo_root / normalized).exists():
                continue
            if _scope_declared(normalized, declared, declared_dirs):
                continue
            errors.append(
                f"{t.task_id} scope {p} neither exists in repo nor is declared "
                f"in frozen design docs"
            )
    return errors


def validate_scope_existence(
    tasks: list[TaskNode],
    repo: Path | str | None = None,
    design_doc_texts: dict[str, str] | str | None = None,
    design_docs_text: str | None = None,
) -> tuple[bool, list[str]]:
    """OOB b89 S2 scope existence gate (pure, rev3 delta Notes).

    Checks each scope path (parsed via :func:`_scope_paths`) satisfies either:

    * **repo side**: ``(repo / normalized).exists()`` (exact path, not parent),
    * **design side**: token-exact membership or directory-prefix closure in the
      frozen design docs (``architecture.md`` + ``test-plan.md``).

    Token extraction uses ``tracks/...\\.[ext]`` path-token regex (exact
    equality, **no naive substring**); directory scopes also accept
    ``dir/`` literal or any file token with ``dir/`` prefix (see Notes).

    Legacy ``schema==1`` graphs skip (``(True, [])``). Format errors from
    ``_scope_paths`` are not re-reported here.

    ``design_doc_texts`` may be a ``dict`` (``{"architecture.md": text, ...}``
    or ``{"architecture": text}``) or a single concatenated ``str``; both are
    accepted for test compatibility (rev3 Notes uses ``str``).
    """
    # schema-1 skip (per Notes: tasks[0].schema == 1)
    if not tasks:
        return (True, [])
    if getattr(tasks[0], "schema", 1) == 1:
        return (True, [])
    # also any-task legacy? Notes says first; we honour first-only to avoid
    # diverging from Notes. Additionally, if any task is schema 1, treat as skip
    # for safety (brief says any → skip). Keep both:
    if any(getattr(t, "schema", 1) == 1 for t in tasks):
        return (True, [])

    repo_root = Path(repo).resolve() if repo is not None else Path.cwd().resolve()
    design_text = _design_doc_text(design_doc_texts, design_docs_text)
    declared, declared_dirs = _declared_tokens(design_text)
    errors = _scope_existence_errors(tasks, repo_root, declared, declared_dirs)
    return (not errors, errors)
