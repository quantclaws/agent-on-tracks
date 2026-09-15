"""Unit contract tests for the closure-evidence mixin (IF-CLOSURE-001 v0.8).

Covers the pure halves of the face: binding-table parsing (kill-manifest
primary + closure-bindings supplement, malformed tables fail closed), the
stale-patch mismatch vocabulary, and the node_not_collected guard that keeps
a vacuous kill (pytest rc=5) from ever crediting an experiment.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracks.executor.closure_evidence import (
    ClosureBindingsError,
    _apply_patch_factory,
    _load_binding_tables,
    _parse_bindings,
    _run_nodes_factory,
)


def _write_km(repo: Path, rows: list[dict]) -> Path:
    d = repo / "tests" / "counterexamples" / "v0.8"
    d.mkdir(parents=True)
    for row in rows:
        (d / row["patch"]).write_bytes(b"diff --git a/x b/x\n")
    km = d / "kill-manifest.json"
    km.write_text(json.dumps({"bindings": rows}), encoding="utf-8")
    return km


def test_parse_bindings_primary_requires_full_fields(tmp_path):
    km = _write_km(
        tmp_path,
        [
            {
                "ac": "AC-FR0001-01",
                "patch": "p.patch",
                "test": "tests/x.py::t",
                "if_ref": "IF-X-001",
            }
        ],
    )
    table = _parse_bindings(km, label=str(km))
    assert table["AC-FR0001-01"]["test"] == "tests/x.py::t"
    assert table["AC-FR0001-01"]["control"] == ""


def test_parse_bindings_supplement_requires_control(tmp_path):
    supp = tmp_path / "supp.json"
    supp.write_text(
        json.dumps(
            {
                "bindings": [
                    {
                        "ac": "AC-FR0001-02",
                        "patch": "p.patch",
                        "test": "tests/x.py::t2",
                        "if_ref": "IF-X-001",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ClosureBindingsError, match="explicit control"):
        _parse_bindings(supp, label=str(supp), supplement=True)


def test_parse_bindings_rejects_missing_fields(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"bindings": [{"ac": "AC-X-01"}]}), encoding="utf-8")
    with pytest.raises(ClosureBindingsError, match="missing ac/patch/test/if_ref"):
        _parse_bindings(bad, label=str(bad))


def test_load_binding_tables_merges_supplement_over_primary(tmp_path):
    _write_km(
        tmp_path,
        [
            {
                "ac": "AC-FR0001-01",
                "patch": "p.patch",
                "test": "tests/x.py::t",
                "if_ref": "IF-X-001",
            }
        ],
    )
    supp = tmp_path / ".tracks" / "projects" / "v0.8" / "closure-bindings.json"
    supp.parent.mkdir(parents=True)
    supp.write_text(
        json.dumps(
            {
                "bindings": [
                    {
                        "ac": "AC-FR0001-01",
                        "patch": "q.patch",
                        "test": "tests/y.py::t",
                        "if_ref": "IF-X-002",
                        "control": "tests/unit/c.py::c",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    merged = _load_binding_tables(tmp_path, "v0.8")
    assert merged["AC-FR0001-01"]["patch"] == "q.patch"
    assert merged["AC-FR0001-01"]["control"] == "tests/unit/c.py::c"


def test_load_binding_tables_missing_patch_file_fails_closed(tmp_path):
    _write_km(
        tmp_path,
        [
            {
                "ac": "AC-FR0001-01",
                "patch": "gone.patch",
                "test": "tests/x.py::t",
                "if_ref": "IF-X-001",
            }
        ],
    )
    (tmp_path / "tests/counterexamples/v0.8/gone.patch").unlink()
    table = _load_binding_tables(tmp_path, "v0.8")  # parse side is fine
    assert table["AC-FR0001-01"]["patch"] == "gone.patch"
    from tracks.executor.closure_evidence import _patch_paths

    with pytest.raises(ClosureBindingsError, match="not found"):
        _patch_paths(tmp_path, "v0.8", "gone.patch")


def test_apply_patch_reports_mismatch_vocabulary(tmp_path, monkeypatch):
    """A failing git apply must carry the mismatch marker so the experiment
    classifies stale_patch instead of silently running unmutated."""
    patch = tmp_path / "p.patch"
    patch.write_text("diff --git a/x b/x\n")
    apply_patch = _apply_patch_factory(patch, "sha256:deadbeef")
    assert "mismatch" in apply_patch(tmp_path, "sha256:different")


def test_run_nodes_fail_closed_on_uncollected_node(tmp_path, monkeypatch):
    """pytest rc=5 (no tests collected) must raise, never credit a kill."""

    class FakeProc:
        returncode = 5
        stdout = "no tests ran"
        stderr = ""

    def fake_run(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr("subprocess.run", fake_run)
    run_nodes = _run_nodes_factory(tmp_path)
    with pytest.raises(RuntimeError, match="node_not_collected"):
        run_nodes(tmp_path, ["tests/nope.py::ghost"])
