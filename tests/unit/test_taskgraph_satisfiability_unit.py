"""OB-2 unit tests: _module_to_path, validate_anchor_satisfiability, validate_scope_existence.

Helper ``make_task`` mirrors ``TaskNode`` construction used elsewhere.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tracks.executor.taskgraph import (
    TaskNode,
    _module_to_path,
    validate_anchor_satisfiability,
    validate_scope_existence,
)


def make_task(
    task_id: str,
    scope_boundary: str,
    depends_on: list[str] | None = None,
    acceptance_refs: list[str] | None = None,
    schema: int = 2,
    issue_number: int = 1,
) -> TaskNode:
    return TaskNode(
        task_id=task_id,
        issue_number=issue_number,
        description=f"task {task_id}",
        ac_refs=("AC-FR0001-01",),
        fr_refs=("FR-0001",),
        if_ids=("IF-IMPL-001",),
        test_refs=tuple(acceptance_refs or []),
        scope_boundary=scope_boundary,
        depends_on=tuple(depends_on or []),
        batch="1",
        parallel=False,
        budget=2,
        unit_refs=(),
        acceptance_refs=tuple(acceptance_refs or []),
        schema=schema,
    )


# ---------------------------------------------------------------------------
# _module_to_path dual morphology
# ---------------------------------------------------------------------------


def test_module_to_path_file(tmp_path: Path):
    # no __init__.py → file
    (tmp_path / "tracks" / "foo").mkdir(parents=True)
    (tmp_path / "tracks" / "foo" / "bar.py").write_text("", encoding="utf-8")
    assert _module_to_path("tracks.foo.bar", repo=tmp_path) == "tracks/foo/bar.py"


def test_module_to_path_package(tmp_path: Path):
    # __init__.py present → directory
    pkg = tmp_path / "tracks" / "foo" / "bar"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    assert _module_to_path("tracks.foo.bar", repo=tmp_path) == "tracks/foo/bar"


def test_module_to_path_unifies_slash(tmp_path: Path):
    # always forward slash even on win-like input
    assert _module_to_path("tracks.foo.bar").count("/") >= 2
    assert "\\" not in _module_to_path("tracks.foo.bar")


# ---------------------------------------------------------------------------
# validate_anchor_satisfiability
# ---------------------------------------------------------------------------


def test_bad_sidecar_fail_closed():
    """Missing anchor entry → violation (fail-closed)."""
    t1 = make_task("T-001", "tracks/foo.py", acceptance_refs=["tests/integration/test_a.py::test_x"])
    # surface lacks the anchor
    surface = {"schema": 1, "anchors": {}, "anchor_set_digest": "d", "recorded_tree": "r"}
    ok, violations, _adv = validate_anchor_satisfiability([t1], surface)
    assert not ok
    assert any("missing from anchor-surface sidecar" in v for v in violations)


def test_over_attribution_safe():
    """Unowned module → advisory, not violation (over-attribution safe)."""
    t1 = make_task("T-001", "tracks/foo.py", acceptance_refs=["a"])
    surface = {
        "schema": 1,
        "anchors": {"a": {"modules": ["tracks.unowned.mod"], "outcome": "pass"}},
        "anchor_set_digest": "d",
        "recorded_tree": "r",
    }
    ok, violations, advisories = validate_anchor_satisfiability([t1], surface)
    # no violation, but advisory for unowned
    assert ok
    assert violations == []
    assert any("imports unowned tracks module" in adv for adv in advisories)


def test_multi_anchor_union():
    """Same task multiple anchors → violations are union."""
    t1 = make_task("T-001", "tracks/foo.py", acceptance_refs=["a1", "a2"])
    t2 = make_task("T-002", "tracks/bar.py")
    t3 = make_task("T-003", "tracks/baz.py")
    surface = {
        "schema": 1,
        "anchors": {
            "a1": {"modules": ["tracks.bar"], "outcome": "pass"},
            "a2": {"modules": ["tracks.baz"], "outcome": "pass"},
        },
        "anchor_set_digest": "d",
        "recorded_tree": "r",
    }
    # T-001 depends on nothing, needs both bar and baz -> two violations
    ok, violations, _ = validate_anchor_satisfiability([t1, t2, t3], surface)
    assert not ok
    assert len([v for v in violations if "without depends_on edge" in v]) == 2


def test_schema1_skip():
    t1 = make_task("T-001", "tracks/foo.py", acceptance_refs=["a"], schema=1)
    surface = {"schema": 1, "anchors": {"a": {"modules": ["tracks.foo"], "outcome": "pass"}}, "anchor_set_digest": "d", "recorded_tree": "r"}
    ok, violations, advisories = validate_anchor_satisfiability([t1], surface)
    assert ok and violations == [] and advisories == []


def test_satisfiability_happy_with_depends():
    """Direct depends_on satisfies violation."""
    owner = make_task("T-002", "tracks/executor/release_gate.py")
    facade = make_task("T-001", "tracks/cli/main.py", depends_on=["T-002"], acceptance_refs=["a1"])
    surface = {"schema": 1, "anchors": {"a1": {"modules": ["tracks.executor.release_gate"], "outcome": "pass"}}, "anchor_set_digest": "d", "recorded_tree": "r"}
    ok, violations, _ = validate_anchor_satisfiability([facade, owner], surface)
    assert ok and violations == []


def test_satisfiability_transitive_closure():
    """Transitive closure covers violation."""
    c = make_task("T-003", "tracks/c.py")
    b = make_task("T-002", "tracks/b.py", depends_on=["T-003"])
    a = make_task("T-001", "tracks/a.py", depends_on=["T-002"], acceptance_refs=["a1"])
    surface = {"schema": 1, "anchors": {"a1": {"modules": ["tracks.c"], "outcome": "fail"}}, "anchor_set_digest": "d", "recorded_tree": "r"}
    ok, violations, _ = validate_anchor_satisfiability([a, b, c], surface)
    assert ok, violations


def test_satisfiability_missing_edge():
    """Without transitive edge, violation reported with owner."""
    owner = make_task("T-026", "tracks/executor/release_gate.py")
    t1 = make_task("T-001", "tracks/cli/main.py", acceptance_refs=["a1"])
    surface = {"schema": 1, "anchors": {"a1": {"modules": ["tracks.executor.release_gate"], "outcome": "fail"}}, "anchor_set_digest": "d", "recorded_tree": "r"}
    ok, violations, _ = validate_anchor_satisfiability([t1, owner], surface)
    assert not ok
    assert any("T-001 anchor a1 exercises tracks/executor/release_gate.py owned by T-026 without depends_on edge" in v for v in violations)


def test_satisfiability_own_scope_no_violation():
    t = make_task("T-001", "tracks/foo.py", acceptance_refs=["a1"])
    surface = {"schema": 1, "anchors": {"a1": {"modules": ["tracks.foo"], "outcome": "pass"}}, "anchor_set_digest": "d", "recorded_tree": "r"}
    ok, violations, _ = validate_anchor_satisfiability([t], surface)
    assert ok


def test_ast_vs_dynamic_split():
    """Same surface: AST-declared missing edge → violation; dynamic-only → advisory."""
    t1 = make_task("T-001", "tracks/foo.py", acceptance_refs=["a1"])
    t2 = make_task("T-002", "tracks/bar.py")
    t3 = make_task("T-003", "tracks/baz.py")
    surface = {
        "schema": 1,
        "anchors": {
            "a1": {
                "modules": sorted(
                    {"tracks.bar", "tracks.baz", "tracks.unowned_mod"}
                ),
                "ast_modules": ["tracks.bar"],
                "dynamic_modules": ["tracks.baz", "tracks.unowned_mod"],
                "outcome": "pass",
            }
        },
        "anchor_set_digest": "d",
        "recorded_tree": "r",
    }
    ok, violations, advisories = validate_anchor_satisfiability([t1, t2, t3], surface)
    # ast bar missing edge → hard violation
    assert not ok
    missing_edges = [v for v in violations if "without depends_on edge" in v]
    assert any(
        "T-001 anchor a1 exercises tracks/bar.py owned by T-002 without depends_on edge" in v
        for v in missing_edges
    )
    # dynamic-only baz must NOT become a violation
    assert not any("tracks/baz.py" in v for v in missing_edges)
    # dynamic-only owned-missing → advisory with explicit (advisory) wording
    assert any(
        "anchor a1 dynamically loads owned tracks module tracks/baz.py (advisory)" in adv
        for adv in advisories
    )
    # dynamic-only unowned → advisory, distinct wording
    assert any(
        "anchor a1 dynamically loads unowned tracks module tracks/unowned_mod.py (advisory)"
        in adv
        for adv in advisories
    )


def test_ast_unowned_imports_still_advisory():
    """AST-declared unowned module keeps the original advisory wording."""
    t1 = make_task("T-001", "tracks/foo.py", acceptance_refs=["a1"])
    surface = {
        "schema": 1,
        "anchors": {
            "a1": {
                "modules": ["tracks.stray", "tracks.stray"],
                "ast_modules": ["tracks.stray"],
                "dynamic_modules": [],
                "outcome": "pass",
            }
        },
        "anchor_set_digest": "d",
        "recorded_tree": "r",
    }
    ok, violations, advisories = validate_anchor_satisfiability([t1], surface)
    assert ok
    assert any("anchor a1 imports unowned tracks module tracks/stray.py" in adv for adv in advisories)


def test_legacy_sidecar_without_split_fields_hard_gates():
    """Legacy entry (no ast_modules/dynamic_modules) → ast = modules (old behaviour)."""
    owner = make_task("T-026", "tracks/executor/release_gate.py")
    t1 = make_task("T-001", "tracks/cli/main.py", acceptance_refs=["a1"])
    surface = {"schema": 1, "anchors": {"a1": {"modules": ["tracks.executor.release_gate"], "outcome": "fail"}}, "anchor_set_digest": "d", "recorded_tree": "r"}
    ok, violations, _ = validate_anchor_satisfiability([t1, owner], surface)
    assert not ok
    assert any("owned by T-026 without depends_on edge" in v for v in violations)


# ---------------------------------------------------------------------------
# validate_scope_existence — 10 named cases (rev3)
# ---------------------------------------------------------------------------


def _git_init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=False)
    subprocess.run(["git", "config", "user.email", "a@b.com"], cwd=str(repo), check=False)
    subprocess.run(["git", "config", "user.name", "a"], cwd=str(repo), check=False)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init", "-q"], cwd=str(repo), check=False)


def test_synthetic_scope_rejected(tmp_path: Path):
    repo = tmp_path / "repo1"
    repo.mkdir()
    _git_init_repo(repo)
    t = make_task("T-001", "tracks/new_feature/foo.py")
    ok, errs = validate_scope_existence([t], repo, {})
    assert not ok
    assert any("neither exists in repo nor is declared" in e for e in errs)


def test_declared_new_scope_passes(tmp_path: Path):
    repo = tmp_path / "repo2"
    repo.mkdir()
    _git_init_repo(repo)
    t = make_task("T-001", "tracks/new_feature/foo.py")
    design = {"architecture.md": "  tracks/new_feature/foo.py  "}
    ok, errs = validate_scope_existence([t], repo, design)
    assert ok and errs == []


def test_existing_scope_passes(tmp_path: Path):
    repo = tmp_path / "repo3"
    repo.mkdir()
    _git_init_repo(repo)
    (repo / "tracks" / "existing").mkdir(parents=True)
    (repo / "tracks" / "existing" / "foo.py").write_text("", encoding="utf-8")
    t = make_task("T-001", "tracks/existing/foo.py")
    ok, errs = validate_scope_existence([t], repo, {})
    assert ok


def test_synthetic_scope_substring_slip_rejected(tmp_path: Path):
    repo = tmp_path / "repo4"
    repo.mkdir()
    _git_init_repo(repo)
    # scope is tracks/a.py, design only contains tracks/a.py.bak (substring trap)
    t = make_task("T-001", "tracks/a.py")
    design = {"arch.md": "tracks/a.py.bak\nmy_tracks/a.py\ntracks/aa.py"}
    ok, errs = validate_scope_existence([t], repo, design)
    assert not ok, "naive substring must not pass"


def test_declared_with_punctuation_passes(tmp_path: Path):
    repo = tmp_path / "repo5"
    repo.mkdir()
    _git_init_repo(repo)
    t = make_task("T-001", "tracks/foo.py")
    design = {"arch.md": "`tracks/foo.py`, tracks/foo.py\n"}
    ok, errs = validate_scope_existence([t], repo, design)
    assert ok, errs


def test_file_parent_exists_not_sufficient(tmp_path: Path):
    repo = tmp_path / "repo6"
    repo.mkdir()
    _git_init_repo(repo)
    (repo / "tracks" / "executor").mkdir(parents=True)
    # parent exists but file does not
    t = make_task("T-001", "tracks/executor/_verify2.py")
    ok, errs = validate_scope_existence([t], repo, {})
    assert not ok
    assert any("T-001" in e for e in errs)


def test_directory_scope_declared_prefix_passes(tmp_path: Path):
    repo = tmp_path / "repo7"
    repo.mkdir()
    _git_init_repo(repo)
    # directory scope, repo lacks dir but design declares file under it
    t = make_task("T-001", "tracks/newfeat/")
    design = {"arch.md": "tracks/newfeat/foo.py"}
    ok, errs = validate_scope_existence([t], repo, design)
    assert ok, errs


def test_multi_file_scope_comma_newline_split(tmp_path: Path):
    repo = tmp_path / "repo8"
    repo.mkdir()
    _git_init_repo(repo)
    (repo / "tracks").mkdir(parents=True, exist_ok=True)
    (repo / "tracks" / "a.py").write_text("", encoding="utf-8")
    # b.py and c.py missing, but declare b in design, c missing → one error
    t = make_task("T-001", "tracks/a.py, tracks/b.py\ntracks/c.py, tracks/a.py")
    design = {"arch.md": "tracks/b.py"}
    ok, errs = validate_scope_existence([t], repo, design)
    assert not ok
    assert any("tracks/c.py" in e for e in errs)
    # only c should be reported, not a or b
    assert not any("tracks/a.py" in e for e in errs if "a.py" in e and "neither" in e)
    assert not any("tracks/b.py" in e for e in errs if "neither" in e and "b.py" in e)


def test_scope_trailing_slash_normalized(tmp_path: Path):
    repo = tmp_path / "repo9"
    repo.mkdir()
    _git_init_repo(repo)
    (repo / "tracks" / "executor").mkdir(parents=True)
    # tracks/executor with trailing slash → same as without
    t1 = make_task("T-001", "tracks/executor/")
    ok1, _ = validate_scope_existence([t1], repo, {})
    t2 = make_task("T-001", "tracks/executor")
    ok2, _ = validate_scope_existence([t2], repo, {})
    assert ok1 == ok2


def test_scope_backslash_normalized(tmp_path: Path):
    repo = tmp_path / "repo10"
    repo.mkdir()
    _git_init_repo(repo)
    (repo / "tracks" / "executor").mkdir(parents=True)
    (repo / "tracks" / "executor" / "a.py").write_text("", encoding="utf-8")
    # backslash form should normalize to forward slash and find file
    t = make_task("T-001", r"tracks\executor\a.py")
    ok, errs = validate_scope_existence([t], repo, {})
    assert ok, errs
