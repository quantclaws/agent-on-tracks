"""Behavior coverage for the FakeBackend M-DESIGN manufacturer
(``effects.fake_design``): story/spec/acceptance projections, deterministic
trio revisions, reach-entry derivation and the test-only scaffold validation
contract.
"""

from __future__ import annotations

import re
from pathlib import Path

from tracks.effects.fake_design import FakeDesignMixin, _story_title


class _Host(FakeDesignMixin):
    def __init__(self, tmp_path: Path):
        self.repo = tmp_path / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.version = "v0.5"
        self._design_revisions = 0
        self._vdir_path = tmp_path / "vdir"
        self._vdir_path.mkdir(parents=True, exist_ok=True)
        self.reach_supported = False
        self.reach_lines: list[str] | None = None
        self.templates = {
            "interfaces": "# interfaces\n\n## 1. Section\n\ntext\n",
            "architecture": "# architecture\n\n## 1. Section\n\ntext\n",
            "test-plan": "# test plan\n\n## 1. Section\n\ntext\n",
        }

    def _design_vdir(self) -> Path:
        return self._vdir_path

    def _design_doc(self, kind: str) -> str:
        return self.templates.get(kind, f"# {kind}\n")

    def _reach_entries_supported(self) -> bool:
        return self.reach_supported

    def _reach_entry_lines(self) -> list[str]:
        if self.reach_lines is not None:
            return self.reach_lines
        return super()._reach_entry_lines()

    def _write_project_contract(self) -> None:
        return None


def test_story_title_request_section_break_and_fallback():
    body = "## 1. 原始输入\n\n> the request text\n\n## 2. Next\n"
    assert _story_title(body) == "the request text"

    broken = "## 1. 原始输入\n\nplain line\n## 2. Next\n"
    assert _story_title(broken) == "1. 原始输入"

    assert _story_title("# Heading\nbody\n") == "Heading"
    assert _story_title("") == "untitled"
    assert _story_title("   \n") == "untitled"


def test_design_doc_drops_blockquotes_and_fences(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(
        "tracks.effects.fake_design.templating.load_template",
        lambda kind: (
            "# t\n> quote\n```\n> fenced quote\n```\n- {path} — entry\nplain\n"
        ),
    )
    assert FakeDesignMixin._design_doc(host, "architecture") == (
        "# t\n```\n> fenced quote\n```\nplain\n"
    )


def test_write_design_template_broken_trims_last_section(tmp_path):
    host = _Host(tmp_path)
    vdir = host._write_design("template_broken")
    arch = (vdir / "architecture.md").read_text(encoding="utf-8")
    assert "## 1. Section" not in arch
    assert arch.startswith("# architecture")
    assert (vdir / "interfaces.md").exists()
    assert (vdir / "test-plan.md").exists()


def test_revise_design_trace_orphan_rewrites(tmp_path):
    host = _Host(tmp_path)
    host._write_design("ok")
    vdir = host._revise_design("trace_orphan")
    assert vdir == host._vdir_path
    assert host._design_revisions == 0


def test_revise_design_rebuilds_missing_doc(tmp_path):
    host = _Host(tmp_path)
    host._write_design("ok")
    (host._vdir_path / "test-plan.md").unlink()
    vdir = host._revise_design("ok")
    assert vdir == host._vdir_path
    assert (vdir / "test-plan.md").exists()


def test_reach_entry_lines_missing_acceptance(tmp_path):
    host = _Host(tmp_path)
    assert host._reach_entry_lines() == []


def test_write_reach_entries_empty_and_written(tmp_path):
    host = _Host(tmp_path)
    host.reach_lines = []
    host._write_reach_entries(host._vdir_path)
    assert not (host.repo / ".tracks" / "reach-entries.txt").exists()

    host.reach_lines = ["tracks.impl.ac_fr0001_01"]
    host._write_reach_entries(host._vdir_path)
    assert (host.repo / ".tracks" / "reach-entries.txt").read_text(
        encoding="utf-8"
    ) == "tracks.impl.ac_fr0001_01\n"


def test_ac_coverage_trace_orphan_drops_last(tmp_path):
    host = _Host(tmp_path)
    (host._vdir_path / "acceptance.md").write_text(
        "### AC-FR0001-01 one\n\n### AC-FR0001-02 two\n", encoding="utf-8"
    )
    full = host._ac_coverage("ok")
    orphan = host._ac_coverage("trace_orphan")
    assert "AC-FR0001-01" in full and "AC-FR0001-02" in full
    assert "AC-FR0001-01" in orphan
    assert "AC-FR0001-02" not in orphan


def test_island_closure_lines_requires_acceptance_and_if_registry(tmp_path):
    host = _Host(tmp_path)
    assert host._island_closure_lines("# interfaces\n") == []

    (host._vdir_path / "acceptance.md").write_text(
        "### AC-FR0001-01 one\n", encoding="utf-8"
    )
    assert host._island_closure_lines("# interfaces\n") == []

    closure = host._island_closure_lines("## 5. IF Registry\n\n- IF-MTEST-001\n")
    assert len(closure) == 1
    assert "IF-MTEST-001" in closure[0]


def _scaffold_entry(path: str, content: str = "x", **overrides) -> dict:
    entry = {
        "path": path,
        "content": content,
        "mode": "executable",
        "description": "stub",
    }
    entry.update(overrides)
    return entry


def test_validated_fake_scaffold_rejects_bad_shapes(tmp_path):
    host = _Host(tmp_path)
    assert host._validated_fake_scaffold(None) == ([], None)
    assert host._validated_fake_scaffold({"scenario_context": {}}) == ([], None)
    _, error = host._validated_fake_scaffold(
        {"scenario_context": {"fake_scaffold": "nope"}}
    )
    assert error is not None and error["failure_class"] == "invalid_fake_scaffold"

    cases = [
        ("not-an-object", "entry 0 must be an object"),
        (_scaffold_entry(""), "entry 0 path must be a non-empty string"),
        (_scaffold_entry("a.py", content=1), "entry 0 content must be a string"),
        (_scaffold_entry("a.py", mode="rw"), "entry 0 mode must be 'executable'"),
        (
            _scaffold_entry("a.py", description="two\nlines"),
            "entry 0 description must be a single line",
        ),
        (
            _scaffold_entry(".tracks/state.json"),
            "entry 0 path is not allowed: '.tracks/state.json'",
        ),
        (
            _scaffold_entry("a b.py"),
            "entry 0 path escapes repository: 'a b.py'",
        ),
    ]
    for raw, expected in cases:
        entries, error = host._validated_fake_scaffold(
            {"scenario_context": {"fake_scaffold": [raw]}}
        )
        assert entries == []
        assert error is not None and error["audit_evidence"] == expected


def test_validated_fake_scaffold_duplicate_and_directory(tmp_path):
    host = _Host(tmp_path)
    duplicate = {
        "scenario_context": {
            "fake_scaffold": [_scaffold_entry("a.py"), _scaffold_entry("a.py")]
        }
    }
    _, error = host._validated_fake_scaffold(duplicate)
    assert error is not None and error["audit_evidence"] == "duplicate path: a.py"

    (host.repo / "pkg").mkdir()
    _, error = host._validated_fake_scaffold(
        {"scenario_context": {"fake_scaffold": [_scaffold_entry("pkg")]}}
    )
    assert error is not None
    assert "is not a regular file" in error["audit_evidence"]


def test_validated_fake_scaffold_symlink_component(tmp_path):
    host = _Host(tmp_path)
    (host.repo / "real").mkdir()
    (host.repo / "link").symlink_to(host.repo / "real")
    assert host._has_symlink_component(Path("link")) is True
    assert host._has_symlink_component(Path("real")) is False


def test_validated_fake_scaffold_accepts_clean_entry(tmp_path):
    host = _Host(tmp_path)
    entries, error = host._validated_fake_scaffold(
        {"scenario_context": {"fake_scaffold": [_scaffold_entry("bin/tool")]}}
    )
    assert error is None
    assert entries == [
        {
            "path": "bin/tool",
            "content": "x",
            "mode": "executable",
            "description": "stub",
        }
    ]


def test_append_section_lines_missing_heading_returns_text():
    text = "# t\n\nno heading here\n"
    pattern = re.compile(r"^## nope$", re.M)
    assert FakeDesignMixin._append_section_lines(text, pattern, ["x"]) == text


def test_ensure_design_scaffold_empty_and_reach_supported(tmp_path):
    host = _Host(tmp_path)
    host._ensure_design_scaffold(host._vdir_path, [])
    assert not (host._vdir_path / "architecture.md").exists()

    (host._vdir_path / "architecture.md").write_text(
        "# arch\n\n## Scaffold 宣言\n\nexisting\n", encoding="utf-8"
    )
    (host._vdir_path / "interfaces.md").write_text(
        "# ifaces\n\n## CLI 接口合同\n\n- x\n", encoding="utf-8"
    )
    host.reach_supported = True
    host.reach_lines = ["tracks.impl.x"]
    host._ensure_design_scaffold(
        host._vdir_path, [_scaffold_entry("bin/tool")]
    )
    assert (host._vdir_path / "architecture.md").read_text(encoding="utf-8").count(
        "Scaffold 宣言"
    ) == 1
    assert (host.repo / ".tracks" / "reach-entries.txt").exists()
    assert (host.repo / "bin" / "tool").read_text(encoding="utf-8") == "x"


def test_write_spec_scope_overflow_and_existing_guard(tmp_path):
    host = _Host(tmp_path)
    path = tmp_path / "spec.md"
    host._write_spec(path, "scope_overflow")
    text = path.read_text(encoding="utf-8")
    assert text.count("### FR-") == 31
    path.write_text("sentinel", encoding="utf-8")
    host._write_spec(path, "ok")
    assert path.read_text(encoding="utf-8") == "sentinel"


def test_write_acceptance_trace_orphan(tmp_path):
    host = _Host(tmp_path)
    spec = tmp_path / "spec.md"
    spec.write_text(
        "### FR-0001 one\n\n### FR-0002 two\n", encoding="utf-8"
    )
    acceptance = tmp_path / "acceptance.md"
    host._write_acceptance(acceptance, "trace_orphan")
    text = acceptance.read_text(encoding="utf-8")
    assert "AC-FR0001-01" in text
    assert "AC-FR0002-01" not in text
