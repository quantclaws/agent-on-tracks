"""Task-graph anchor-surface + scope-existence gates (OOB b89 S2).

Extracted from ``taskgraph.py`` for module-size compliance (C0302). Pure
functions; :mod:`tracks.executor.taskgraph` re-exports every name.
"""

from __future__ import annotations

import re
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

def validate_anchor_satisfiability(  # noqa: CCR001
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

    # Build scope → owner and per-task scope sets (reuse _scope_paths)
    all_scopes: set[str] = set()
    owner_map: dict[str, str] = {}
    task_scopes: dict[str, set[str]] = {}
    for t in tasks:
        paths, errs = _scope_paths(t.scope_boundary, t.task_id)
        # format errors are reported elsewhere; skip them here
        if errs:
            # still honour parsed paths (if any) but do not add illegal ones
            pass
        normed: set[str] = set()
        for p in paths:
            # _scope_paths already normalises (\→/, rstrip "/")
            n = p
            normed.add(n)
            all_scopes.add(n)
            owner_map[n] = t.task_id
        task_scopes[t.task_id] = normed

    task_by_id: dict[str, TaskNode] = {t.task_id: t for t in tasks}

    def _closure_scopes(task_id: str) -> set[str]:
        """Union of scopes owned by transitive ``depends_on`` of *task_id*."""
        stack: list[str] = list(task_by_id[task_id].depends_on) if task_id in task_by_id else []
        visited: set[str] = set()
        scopes: set[str] = set()
        while stack:
            cur = stack.pop()
            if cur == "-" or cur in visited:
                continue
            visited.add(cur)
            if cur in task_scopes:
                scopes.update(task_scopes[cur])
            if cur in task_by_id:
                stack.extend(task_by_id[cur].depends_on)
        return scopes

    anchors_map = {}
    if isinstance(surface, dict):
        anchors_map = surface.get("anchors", {})
        if not isinstance(anchors_map, dict):
            anchors_map = {}

    def _map_paths(modnames: list[str]) -> set[str]:
        """Map module names to repo-relative paths (deterministic fallback)."""
        out: set[str] = set()
        for m in modnames:
            try:
                out.add(_module_to_path(m))
            except Exception:
                out.add(m.replace(".", "/") + ".py")
        return out

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
        own = task_scopes.get(t.task_id, set())
        dep_scopes = _closure_scopes(t.task_id)
        for a in refs:
            if not isinstance(a, str) or not a:
                continue
            entry = anchors_map.get(a)
            if entry is None:
                violations.append(f"anchor {a} missing from anchor-surface sidecar")
                continue
            mods = entry.get("modules", []) if isinstance(entry, dict) else []
            if isinstance(entry, dict) and "ast_modules" in entry and "dynamic_modules" in entry:
                # split sidecar (b89 rev4): hard gate on AST-declared imports only
                ast_mods = [m for m in entry.get("ast_modules", []) if isinstance(m, str)]
                dyn_mods = [m for m in entry.get("dynamic_modules", []) if isinstance(m, str)]
            else:
                # legacy sidecar: no split fields → ast = modules (preserve old hard gate)
                ast_mods = [m for m in mods if isinstance(m, str)]
                dyn_mods = []

            ast_mapped = _map_paths(ast_mods)
            dyn_mapped = _map_paths(dyn_mods)
            # dynamic-only = modules loaded by shared infra / fixtures, not
            # explicitly declared by the test file → advisories only
            dyn_only = dyn_mapped - ast_mapped
            # advisories: unowned tracks modules (AST-declared)
            for mp in sorted(ast_mapped - all_scopes):
                advisories.append(f"anchor {a} imports unowned tracks module {mp}")
            needs = ast_mapped & all_scopes
            missing = needs - own - dep_scopes
            for path in sorted(missing):
                owner = owner_map.get(path, "unknown")
                msg = (
                    f"{t.task_id} anchor {a} exercises {path} "
                    f"owned by {owner} without depends_on edge"
                )
                violations.append(msg)
            # dynamic-only → advisories (owned missing edge and unowned)
            for mp in sorted(dyn_only - all_scopes):
                advisories.append(
                    f"anchor {a} dynamically loads unowned tracks module {mp} (advisory)"
                )
            dyn_missing = (dyn_only & all_scopes) - own - dep_scopes
            for mp in sorted(dyn_missing):
                advisories.append(
                    f"anchor {a} dynamically loads owned tracks module {mp} (advisory)"
                )

    # Also surface any anchored modules that are not tied to a specific task's
    # needs but are globally unowned? The per-anchor advisory already covers
    # all anchors referenced by tasks; anchors in the sidecar that no task
    # references are ignored (they are not part of needs/advisory contract).

    ok = not violations
    return (ok, violations, advisories)

def validate_scope_existence(  # noqa: CCR001
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
    first_schema = getattr(tasks[0], "schema", 1)
    if first_schema == 1:
        return (True, [])
    # also any-task legacy? Notes says first; we honour first-only to avoid
    # diverging from Notes. Additionally, if any task is schema 1, treat as skip
    # for safety (brief says any → skip). Keep both:
    if any(getattr(t, "schema", 1) == 1 for t in tasks):
        return (True, [])

    # Repo root
    repo_root = Path(repo).resolve() if repo is not None else Path.cwd().resolve()

    # Design text normalisation
    if design_docs_text is not None:
        design_text = design_docs_text
    elif isinstance(design_doc_texts, dict):
        parts: list[str] = []
        for v in design_doc_texts.values():
            if isinstance(v, str):
                parts.append(v)
            else:
                parts.append(str(v))
        design_text = "\n".join(parts)
    elif isinstance(design_doc_texts, str):
        design_text = design_doc_texts
    elif design_doc_texts is None and design_docs_text is None:
        design_text = ""
    else:
        design_text = str(design_doc_texts)

    # Token extraction (rev3 Notes, exact — token-precise, no naive substring)
    # Use word-boundary style guard so ``my_tracks/a.py`` does not yield ``tracks/a.py``
    # and ``tracks/a.py`` is not considered declared by ``tracks/a.py.bak``.
    declared: set[str] = set(
        re.findall(
            r"(?<![A-Za-z0-9_])tracks/(?:[A-Za-z0-9_.\-]+/)*[A-Za-z0-9_.\-]+\.[A-Za-z0-9]+\b",
            design_text,
        )
    )
    declared_dirs = set(
        re.findall(r"(?<![A-Za-z0-9_])tracks/(?:[A-Za-z0-9_.\-]+/)+", design_text)
    )

    errors: list[str] = []
    for t in tasks:
        paths, fmt_errs = _scope_paths(t.scope_boundary, t.task_id)
        if fmt_errs:
            continue
        for p in paths:
            normalized = p.replace("\\", "/").rstrip("/")
            if (repo_root / normalized).exists():
                continue
            is_dir_scope = "." not in normalized.rsplit("/", 1)[-1]
            if not is_dir_scope:
                if normalized in declared:
                    continue
            else:
                if normalized in declared or normalized + "/" in declared_dirs:
                    continue
                if any(d.startswith(normalized + "/") for d in declared):
                    continue
            errors.append(
                f"{t.task_id} scope {p} neither exists in repo nor is declared "
                f"in frozen design docs"
            )
    return (not errors, errors)
