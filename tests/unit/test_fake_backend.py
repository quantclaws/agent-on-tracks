"""Scenario-only scaffold behavior for the deterministic Archer backend."""

import stat

import pytest

from tracks.effects.fake import FakeBackend
from tracks.scaffold import _scaffold_declared_paths

_STUB_CONTENT = '#!/usr/bin/env python3\nraise NotImplementedError("IF-MTEST-001 code-stats CLI")\n'
_STUB_DESCRIPTION = "public CLI stub for IF-MTEST-001 (kind: stub)"


def _assignment(entry: dict | None = None) -> dict:
    return {
        "scenario_context": {
            "fake_scaffold": [
                entry
                or {
                    "path": "code-stats",
                    "description": _STUB_DESCRIPTION,
                    "content": _STUB_CONTENT,
                    "mode": "executable",
                }
            ],
        },
    }


def _design_repo(tmp_path):
    vdir = tmp_path / ".tracks" / "projects" / "v0.4"
    vdir.mkdir(parents=True)
    (vdir / "acceptance.md").write_text(
        "---\nstatus: draft\nsha:\n---\n\n### AC-FR0010-01 x\n",
        encoding="utf-8",
    )
    return vdir


def test_valid_fake_scaffold_is_declared_materialized_and_rebuilt(tmp_path):
    vdir = _design_repo(tmp_path)
    backend = FakeBackend(tmp_path, "v0.4")

    result = backend.act("archer", "DRAFT", None, None, _assignment())

    assert result["status"] == "done"
    architecture = (vdir / "architecture.md").read_text(encoding="utf-8")
    interfaces = (vdir / "interfaces.md").read_text(encoding="utf-8")
    assert architecture.count("## 2. Scaffold 宣言") == 1
    assert f"- code-stats — {_STUB_DESCRIPTION}" in architecture
    assert _scaffold_declared_paths(architecture) == {"code-stats"}
    assert "`./code-stats` -> IF-MTEST-001" in interfaces
    stub = tmp_path / "code-stats"
    assert stub.read_text(encoding="utf-8") == _STUB_CONTENT
    assert stub.stat().st_mode & stat.S_IXUSR

    stub.unlink()
    result = backend.act("archer", "RESPOND", None, None, _assignment())

    assert result["status"] == "done"
    assert stub.read_text(encoding="utf-8") == _STUB_CONTENT
    assert stub.stat().st_mode & stat.S_IXUSR
    architecture = (vdir / "architecture.md").read_text(encoding="utf-8")
    interfaces = (vdir / "interfaces.md").read_text(encoding="utf-8")
    assert architecture.count(f"- code-stats — {_STUB_DESCRIPTION}") == 1
    assert interfaces.count("`./code-stats` -> IF-MTEST-001") == 1


@pytest.mark.parametrize(
    "entry",
    [
        {
            "path": "../outside",
            "description": "escape",
            "content": _STUB_CONTENT,
            "mode": "executable",
        },
        {
            "path": "code-stats",
            "description": "bad content",
            "content": None,
            "mode": "executable",
        },
        {
            "path": "code-stats",
            "description": "bad mode",
            "content": _STUB_CONTENT,
            "mode": "0644",
        },
    ],
)
def test_invalid_fake_scaffold_fails_before_writing(tmp_path, entry):
    vdir = _design_repo(tmp_path)
    result = FakeBackend(tmp_path, "v0.4").act("archer", "DRAFT", None, None, _assignment(entry))

    assert result["status"] == "failed"
    assert result["failure_class"] == "invalid_fake_scaffold"
    assert not (vdir / "architecture.md").exists()
    assert not (tmp_path / "code-stats").exists()
    assert not (tmp_path.parent / "outside").exists()


def test_no_scenario_metadata_keeps_generic_fake_design(tmp_path):
    vdir = _design_repo(tmp_path)
    result = FakeBackend(tmp_path, "v0.4").act(
        "archer", "DRAFT", None, None, {"scenario_context": {"finding": "none"}}
    )

    assert result["status"] == "done"
    architecture = (vdir / "architecture.md").read_text(encoding="utf-8")
    assert not (tmp_path / "code-stats").exists()
    assert _scaffold_declared_paths(architecture) <= {"{path}"}
