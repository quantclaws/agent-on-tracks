"""OOB b89 anchor-surface collection (S1) — plugin + sidecar.

Implements the planning-time anchor surface sidecar described in
``.tracks/runtime/oob-briefs/b89-anchor-plan-hardening.md`` rev3.1.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import importlib.util
import json
import os
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_TREE_STAMP_SKIP_PREFIXES = (
    ".git/",
    ".opencode/",
    ".test_cache/",
    ".ruff_cache/",
    ".tracks/",
    ".venv/",
    "build/",
    "dist/",
    "logs/",
)


class AnchorSurfaceError(Exception):
    """Fail-closed error for anchor-surface sidecar loading."""


def _git_head(repo: Path) -> str:
    """Return HEAD sha or ``no-head`` on error."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception:
        pass
    return "no-head"


def _git_status_porcelain(repo: Path) -> str:
    """Return ``git status --porcelain -uall`` stdout (empty on error)."""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            return proc.stdout
    except Exception:
        pass
    return ""


def _is_skipped_stamp_path(path: str) -> bool:
    """Whether *path* is excluded from the dirty stamp."""
    if not path:
        return True
    if path.startswith(_TREE_STAMP_SKIP_PREFIXES):
        return True
    if "__pycache__/" in path:
        return True
    return Path(path).name.startswith(".coverage")


def _dirty_tree_stamp(repo: Path) -> str:
    """Dirty-aware worktree stamp (mirrors ``executor._dirty_tree_stamp``)."""
    repo = Path(repo).resolve()
    head = _git_head(repo)
    status = _git_status_porcelain(repo)
    dirty: dict[str, str] = {}
    for line in status.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        if _is_skipped_stamp_path(path):
            continue
        try:
            digest = hashlib.sha256((repo / path).read_bytes()).hexdigest()
        except OSError:
            digest = "unreadable"
        dirty[path] = digest
    if not dirty:
        return head
    canonical = json.dumps({"head": head, "dirty": dirty}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _anchor_set_digest(anchors: Sequence[str]) -> str:
    """SHA256 of newline-joined sorted anchors."""
    joined = "\n".join(sorted(anchors))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _add_import_names(node: ast.Import, out: set[str]) -> None:
    """Collect ``tracks.*`` names from an ``Import`` node."""
    for alias in node.names:
        name = alias.name
        if name == "tracks" or name.startswith("tracks."):
            out.add(name)


def _add_importfrom_names(node: ast.ImportFrom, out: set[str]) -> None:
    """Collect ``tracks.*`` names from an ``ImportFrom`` node."""
    if node.level != 0:
        return
    mod = node.module
    if mod is None:
        return
    if mod == "tracks":
        for alias in node.names:
            if alias.name == "*":
                out.add("tracks")
            else:
                out.add(f"tracks.{alias.name}")
        return
    if mod.startswith("tracks."):
        out.add(mod)


def static_imports_of(test_file_text: str) -> set[str]:
    """Return ``tracks.*`` modules statically imported in *test_file_text*.

    Captures ``import tracks.x`` and ``from tracks.x import ...`` even inside
    functions (AST walk). Used to complement the dynamic ``sys.modules`` snapshot
    for early-failing tests (OB-1).
    """
    try:
        tree = ast.parse(test_file_text)
    except SyntaxError:
        return set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            _add_import_names(node, modules)
        elif isinstance(node, ast.ImportFrom):
            _add_importfrom_names(node, modules)
    return modules


def _find_tracks_dir(cwd: Path) -> tuple[Path, Path]:
    """Return (repo, tracks_dir) enclosing *cwd*."""
    repo = cwd.resolve()
    tracks_dir = (repo / "tracks").resolve()
    if tracks_dir.is_dir():
        return repo, tracks_dir
    for parent in repo.parents:
        cand = (parent / "tracks").resolve()
        if cand.is_dir():
            return parent, cand
    return repo, tracks_dir


def _is_module_in_tracks(name: str, tracks_dir: Path) -> bool:
    """Whether ``find_spec(name).origin`` lies inside *tracks_dir*."""
    try:
        spec = importlib.util.find_spec(name)
    except Exception:
        return False
    if spec is None or spec.origin is None:
        return False
    origin = spec.origin
    if origin in ("built-in", "frozen"):
        return False
    try:
        origin_path = Path(origin).resolve()
    except Exception:
        return False
    try:
        origin_path.relative_to(tracks_dir)
    except ValueError:
        return False
    return True


def pytest_sessionfinish(session, exitstatus) -> None:  # type: ignore[no-untyped-def]
    """Pytest hook: dump the **dynamic** ``tracks.*`` snapshot to ``TRACKS_SURFACE_OUT``.

    Writes ``{"modules": [...], "outcome": "pass|fail"}`` where ``modules`` is
    this interpreter's ``sys.modules`` filtered to repo-origin ``tracks.*``
    modules (``find_spec(...).origin`` inside repo ``tracks/``). The collector
    treats this as ``dynamic_modules`` and unions it with the AST scan of the
    anchor's test file.
    """
    out = os.environ.get("TRACKS_SURFACE_OUT")
    if not out:
        return
    repo, tracks_dir = _find_tracks_dir(Path.cwd())
    modules: list[str] = []
    for name in list(sys.modules.keys()):
        if not name.startswith("tracks."):
            continue
        if _is_module_in_tracks(name, tracks_dir):
            modules.append(name)
    modules = sorted(set(modules))
    outcome = "pass" if exitstatus == 0 else "fail"
    data = {"modules": modules, "outcome": outcome}
    try:
        out_p = Path(out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    except Exception:
        return


def _is_pytest_contract(contract) -> bool:
    """Whether *contract* declares pytest (mirrors ``anchor_probe``)."""
    fw = getattr(contract, "framework", None)
    if isinstance(fw, str) and fw == "pytest":
        return True
    integ = getattr(contract, "integration", None)
    if integ is not None:
        fw2 = getattr(integ, "framework", None)
        if fw2 == "pytest":
            return True
    return False


def _validate_sidecar_dict(data: dict, path: Path) -> None:
    """Validate sidecar dict shape; raise ``AnchorSurfaceError`` on error."""
    if not isinstance(data, dict):
        raise AnchorSurfaceError("anchor-surface top level must be object")
    if data.get("schema") != 1:
        raise AnchorSurfaceError(f"anchor-surface schema must be 1, got {data.get('schema')!r}")
    anchors = data.get("anchors")
    if not isinstance(anchors, dict):
        raise AnchorSurfaceError("anchor-surface anchors must be object")
    for key, entry in anchors.items():
        _validate_anchor_entry(key, entry)
    digest = data.get("anchor_set_digest")
    if not isinstance(digest, str) or not digest:
        raise AnchorSurfaceError("anchor_set_digest must be non-empty string")
    recorded = data.get("recorded_tree")
    if not isinstance(recorded, str) or not recorded:
        raise AnchorSurfaceError("recorded_tree must be non-empty string")


def _validate_anchor_entry(key: str, entry: dict) -> None:
    """Validate one anchor entry.

    ``modules`` is required (kept for backward compatibility); the split fields
    ``ast_modules``/``dynamic_modules`` are optional — a legacy sidecar without
    them stays valid, and a new sidecar carries all three.
    """
    if not isinstance(key, str) or not key:
        raise AnchorSurfaceError(f"anchor key must be non-empty string: {key!r}")
    if not isinstance(entry, dict):
        raise AnchorSurfaceError(f"anchor entry must be object: {key}")
    mods = entry.get("modules")
    outcome = entry.get("outcome")
    if not isinstance(mods, list) or not all(isinstance(m, str) for m in mods):
        raise AnchorSurfaceError(f"anchor {key} modules must be list of strings")
    for field in ("ast_modules", "dynamic_modules"):
        if field in entry:
            val = entry[field]
            if not isinstance(val, list) or not all(isinstance(m, str) for m in val):
                raise AnchorSurfaceError(f"anchor {key} {field} must be list of strings")
    if outcome not in ("pass", "fail", "error"):
        raise AnchorSurfaceError(f"anchor {key} outcome must be pass|fail|error, got {outcome!r}")


def load_anchor_surface(path: Path) -> dict:
    """Load and validate an anchor-surface sidecar (fail-closed)."""
    p = Path(path)
    if not p.exists():
        raise AnchorSurfaceError(f"anchor-surface file missing: {path}")
    try:
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as exc:
        raise AnchorSurfaceError(f"anchor-surface json malformed: {exc}") from exc
    _validate_sidecar_dict(data, p)
    return data


def _python_bin_for_repo(repo: Path) -> str:
    """Return ``.venv/bin/python`` if present, else ``sys.executable``."""
    venv_python = repo / ".venv" / "bin" / "python"
    if venv_python.is_file():
        return str(venv_python)
    return sys.executable


def _make_skipped_sidecar(digest: str, recorded_tree: str, contract) -> dict:
    """Sidecar for non-pytest contracts (skipped marker)."""
    fw = getattr(contract, "framework", None)
    integ = getattr(contract, "integration", None)
    fw2 = getattr(integ, "framework", None) if integ is not None else None
    reason = (
        f"anchor surface skipped: non-pytest contract "
        f"(framework={fw!r}, integration.framework={fw2!r})"
    )
    return {
        "schema": 1,
        "anchors": {},
        "anchor_set_digest": digest,
        "recorded_tree": recorded_tree,
        "skipped": True,
        "skipped_reason": reason,
    }


def _read_plugin_output(tmp_path: Path, timeout: int) -> tuple[list[str], str, str]:
    """Read plugin JSON; return (modules, outcome, error_msg)."""
    if not tmp_path.exists():
        return [], "error", "surface file missing"
    try:
        raw = tmp_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        mods = list(data.get("modules", []))
        outcome = data.get("outcome", "fail")
        if outcome not in ("pass", "fail"):
            outcome = "fail"
        return mods, outcome, ""
    except Exception as exc:
        return [], "error", f"surface json malformed: {exc}"


def _split_surface(anchor: str, repo: Path, dynamic: list[str]) -> tuple[list[str], list[str]]:
    """Split surface into ``(ast_modules, dynamic_modules)`` for *anchor*.

    ``ast_modules`` is the AST scan of the anchor's test file (full text,
    ``import``/``ImportFrom`` including function bodies) restricted to
    ``tracks.*`` modules mappable via :func:`_module_to_path`. It is the set
    the test file *explicitly declares*; hard-gate violations are computed
    from it. ``dynamic_modules`` is the subprocess ``sys.modules`` snapshot
    filtered to repo-origin ``tracks.*`` modules.

    Returns both sorted, deduped.
    """
    file_part = anchor.partition("::")[0]
    test_file = repo / file_part
    ast_mods: set[str] = set()
    if test_file.is_file():
        try:
            text = test_file.read_text(encoding="utf-8")
            ast_mods = static_imports_of(text)
        except Exception:
            ast_mods = set()
    # Restrict to modules mappable via taskgraph._module_to_path (lazy import to
    # avoid a module-scope cycle; taskgraph does not import anchor_surface).
    try:
        from tracks.executor.taskgraph import _module_to_path

        mapped: set[str] = set()
        for m in ast_mods:
            try:
                _module_to_path(m)
                mapped.add(m)
            except Exception:
                continue
        ast_mods = mapped
    except Exception:
        pass
    return sorted(ast_mods), sorted(set(dynamic))


def _run_one_anchor(  # noqa: CCR001
    anchor: str,
    repo: Path,
    python_bin: str,
    plugin_root_str: str,
    timeout: int,
) -> tuple[str, dict]:
    """Collect surface for a single anchor (isolated subprocess)."""
    fd, tmp_name = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    tmp_path = Path(tmp_name)
    with contextlib.suppress(Exception):
        tmp_path.unlink(missing_ok=True)
    env = os.environ.copy()
    env["TRACKS_SURFACE_OUT"] = str(tmp_path)
    repo_str = str(repo)
    if plugin_root_str != repo_str:
        env["PYTHONPATH"] = repo_str + os.pathsep + plugin_root_str
    else:
        env["PYTHONPATH"] = repo_str
    argv = [
        python_bin,
        "-m",
        "pytest",
        anchor,
        "-p",
        "tracks.executor.anchor_surface",
        "--no-header",
        "-q",
    ]
    modules: list[str] = []
    outcome = "error"
    error_msg = ""
    try:
        proc = subprocess.run(
            argv,
            cwd=str(repo),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        mods, outc, err = _read_plugin_output(tmp_path, timeout)
        if err and not tmp_path.exists():
            error_msg = f"surface file missing (rc={proc.returncode}) stdout={proc.stdout[:250]!r}"
        elif err:
            error_msg = err
        else:
            modules = mods
            outcome = outc
            if proc.returncode == 0 and outcome == "fail" and not mods:
                # keep fail; plugin already reports fail
                pass
        if err and outcome != "error":
            outcome = "error"
    except subprocess.TimeoutExpired as exc:
        error_msg = f"timeout after {timeout}s: {exc}"
        outcome = "error"
    except OSError as exc:
        error_msg = f"infra failure: {exc}"
        outcome = "error"
    # split surface: AST (explicit test-file imports) vs dynamic (subprocess snapshot)
    if outcome in ("pass", "fail"):
        ast_mods, dyn_mods = _split_surface(anchor, repo, modules)
    else:
        ast_mods, dyn_mods = [], []
        modules = []
    with contextlib.suppress(Exception):
        tmp_path.unlink(missing_ok=True)
    merged = sorted(set(ast_mods) | set(dyn_mods))
    result: dict = {
        "modules": merged,
        "ast_modules": ast_mods,
        "dynamic_modules": dyn_mods,
        "outcome": outcome,
    }
    if error_msg:
        result["error"] = error_msg
    return anchor, result


def collect_anchor_surface(  # noqa: CCR001
    repo: Path,
    anchors: Sequence[str],
    contract,
    jobs: int = 4,
    timeout: int = 120,
) -> dict:
    """Collect the anchor surface sidecar (OB-1, pure + subprocess).

    Each anchor runs in an isolated interpreter with ``PYTHONPATH`` locked to
    *repo*; infra failures are recorded as ``outcome:error``.
    """
    repo = Path(repo).resolve()
    anchors = list(anchors)
    digest = _anchor_set_digest(anchors)
    recorded_tree = _dirty_tree_stamp(repo)
    if not _is_pytest_contract(contract):
        return _make_skipped_sidecar(digest, recorded_tree, contract)
    integ = getattr(contract, "integration", None)
    run_cmd = getattr(integ, "run", None) if integ is not None else None
    if isinstance(run_cmd, str) and run_cmd.strip():
        with contextlib.suppress(ValueError):
            shlex.split(run_cmd)
        # unparsable is treated as skipped (mirror anchor_probe)
        try:
            shlex.split(run_cmd)
        except ValueError as exc:
            return {
                "schema": 1,
                "anchors": {},
                "anchor_set_digest": digest,
                "recorded_tree": recorded_tree,
                "skipped": True,
                "skipped_reason": f"anchor surface skipped: contract run unparsable ({exc})",
            }
    if not anchors:
        return {
            "schema": 1,
            "anchors": {},
            "anchor_set_digest": digest,
            "recorded_tree": recorded_tree,
        }
    python_bin = _python_bin_for_repo(repo)
    plugin_root = Path(__file__).resolve().parents[2].resolve()
    plugin_root_str = str(plugin_root)
    results: dict[str, dict] = {}
    if jobs is None or jobs < 1:
        jobs = 1
    if jobs == 1 or len(anchors) == 1:
        for a in anchors:
            k, v = _run_one_anchor(a, repo, python_bin, plugin_root_str, timeout)
            results[k] = v
        return {
            "schema": 1,
            "anchors": results,
            "anchor_set_digest": digest,
            "recorded_tree": recorded_tree,
        }
    max_workers = min(jobs, len(anchors))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_anchor = {
            pool.submit(_run_one_anchor, a, repo, python_bin, plugin_root_str, timeout): a
            for a in anchors
        }
        for fut in as_completed(future_to_anchor):
            try:
                k, v = fut.result()
            except Exception as exc:
                anc = future_to_anchor[fut]
                k, v = anc, {"modules": [], "outcome": "error", "error": str(exc)}
            results[k] = v
    return {
        "schema": 1,
        "anchors": results,
        "anchor_set_digest": digest,
        "recorded_tree": recorded_tree,
    }


def _load_anchors_from_tasks(version_dir: Path) -> list[str]:  # noqa: CCR001
    """Load anchor node-ids from ``tasks.json`` in *version_dir*."""
    tasks_path = version_dir / "tasks.json"
    raw = tasks_path.read_text(encoding="utf-8")
    data = json.loads(raw)
    tasks_data = data.get("tasks", [])
    anchors: set[str] = set()
    for t in tasks_data:
        acc = t.get("acceptance_refs")
        if acc is not None:
            for x in acc:
                if isinstance(x, str) and x.startswith("tests/"):
                    anchors.add(x)
        else:
            for x in t.get("test_refs", []):
                if isinstance(x, str) and x.startswith("tests/"):
                    anchors.add(x)
    return sorted(anchors)


def main(argv: Sequence[str] | None = None) -> int:  # noqa: CCR001
    """CLI entry point for ``python -m tracks.executor.anchor_surface``."""
    parser = argparse.ArgumentParser(prog="tracks.executor.anchor_surface")
    parser.add_argument("--projects-dir", default=".tracks/projects", help="projects directory")
    sub = parser.add_subparsers(dest="cmd", required=True)
    coll = sub.add_parser("collect", help="collect anchor surface sidecar")
    coll.add_argument("--version", required=True, help="version e.g. v0.8")
    coll.add_argument("--out", dest="out", default=None, help="output path")
    coll.add_argument("--jobs", type=int, default=4, help="parallel jobs")
    args = parser.parse_args(argv)
    if args.cmd == "collect":
        repo = Path.cwd().resolve()
        projects_dir = Path(args.projects_dir)
        if not projects_dir.is_absolute():
            projects_dir = repo / projects_dir
        version_dir = projects_dir / args.version
        tasks_path = version_dir / "tasks.json"
        if not tasks_path.exists():
            print(f"tasks.json not found: {tasks_path}", file=sys.stderr)
            return 2
        try:
            anchors = _load_anchors_from_tasks(version_dir)
        except Exception as exc:
            print(f"failed to load anchors: {exc}", file=sys.stderr)
            return 2
        try:
            from tracks.project import load_contract

            contract = load_contract(repo)
        except Exception as exc:
            print(f"no contract: {exc}", file=sys.stderr)
            return 2
        sidecar = collect_anchor_surface(repo, anchors, contract, jobs=args.jobs)
        out_path = Path(args.out) if args.out else version_dir / "anchor-surface.json"
        if not out_path.is_absolute():
            out_path = repo / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(sidecar, indent=2, sort_keys=True), encoding="utf-8")
        try:
            h = hashlib.sha256(json.dumps(sidecar, sort_keys=True).encode("utf-8")).hexdigest()
            blobs_dir = repo / ".tracks" / "runtime" / "blobs"
            blobs_dir.mkdir(parents=True, exist_ok=True)
            (blobs_dir / h).write_text(json.dumps(sidecar, sort_keys=True), encoding="utf-8")
        except Exception:
            pass
        print(f"anchor-surface written to {out_path} ({len(anchors)} anchors)")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
