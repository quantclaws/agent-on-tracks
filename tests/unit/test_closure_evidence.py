"""Unit contract tests for the closure-evidence mixin (IF-CLOSURE-001 v0.8).

Covers the pure halves of the face: binding-table parsing (kill-manifest
primary + closure-bindings supplement, malformed tables fail closed), the
stale-patch mismatch vocabulary, and the node_not_collected guard that keeps
a vacuous kill (pytest rc=5) from ever crediting an experiment.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tracks.executor.closure_evidence import (
    ClosureBindingsError,
    _apply_patch_factory,
    _load_binding_tables,
    _parse_bindings,
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


def test_run_nodes_requires_declared_adapter(tmp_path, monkeypatch):
    """Language neutrality (NFR-0147): nodes run through the HOST-declared
    adapter, never a guessed toolchain; an undeclared host fails closed."""

    class NoAdapter:
        adapter = None
        unit = None
        integration = None

    import tracks.executor.closure_evidence as ce

    monkeypatch.setattr("tracks.project.load_contract", lambda repo: NoAdapter())
    with pytest.raises(ce.ClosureBindingsError, match="declares no adapter"):
        ce._run_nodes_factory(tmp_path)


def test_run_nodes_error_status_without_result_file(tmp_path, monkeypatch):
    """A failed run writing no result file fails closed per node (error),
    never a vacuous kill."""

    class Section:
        cwd = "."
        run_selected = "pytest {nodes} --junitxml={result}"

    class Declared:
        adapter = SimpleNamespace(
            id="reference-pytest", protocol="tracks-test-result", version=1
        )
        unit = Section()
        integration = Section()

    class Adapter:
        def run_selected(self, template, nodes, result_path, cwd):
            return ("false",)

    import tracks.executor.closure_evidence as ce

    monkeypatch.setattr("tracks.project.load_contract", lambda repo: Declared())
    monkeypatch.setattr("tracks.adapters.base.resolve_adapter", lambda *a: Adapter())
    run_nodes = ce._run_nodes_factory(tmp_path)
    out = run_nodes(tmp_path, ["tests/x.py::t"])
    assert out["tests/x.py::t"].status == "error"
