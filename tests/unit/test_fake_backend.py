"""Scenario-only scaffold behavior for the deterministic Archer backend."""

import stat

import pytest

from tracks import paths
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


def _hotfix_target_repo(tmp_path):
    """Create the target-version baseline (v0.5) that a hotfix run (v0.5-hotfix-42)
    inherits: a minimal approved acceptance whose first AC heading is the anchor
    the fake Sage reports (FR-0240-04, cross-version AC-FRXXXX-YY@v0.5)."""
    vdir = paths.version_dir(paths.tracks_home(tmp_path), "v0.5")
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "acceptance.md").write_text(
        "# v0.5 Acceptance\n\n"
        "## FR-0030 Deterministic output\n\n"
        "### AC-FR0030-01\n\n- x\n\n"
        "## NFR-0020 Stable interface\n\n### AC-NFR0020-01\n\n- y\n",
        encoding="utf-8",
    )
    return vdir


def _hotfix_anchor_assignment(target_version: str = "v0.5", **extra) -> dict:
    return {
        "kind": "SAGE_TRIAGE",
        "target_version": target_version,
        **extra,
    }


def test_sage_triage_anchor_reports_first_ac_title_as_deterministic_anchor(tmp_path):
    _hotfix_target_repo(tmp_path)
    result = FakeBackend(tmp_path, "v0.5-hotfix-42").act(
        "sage",
        "SAGE_TRIAGE",
        None,
        None,
        _hotfix_anchor_assignment(),
    )

    assert result["status"] == "done"
    assert result.get("acs") == ["AC-FR0030-01@v0.5"]
    assert result.get("rationale_refs")  # per-entry grounding blob refs


def test_sage_triage_no_anchor_reports_searched_versions_and_corpus_digests(tmp_path, monkeypatch):
    _hotfix_target_repo(tmp_path)
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", "sage:SAGE_TRIAGE=no_anchor")
    result = FakeBackend(tmp_path, "v0.5-hotfix-42").act(
        "sage",
        "SAGE_TRIAGE",
        None,
        None,
        _hotfix_anchor_assignment(),
    )

    assert result["status"] == "done"
    assert result.get("outcome") == "no_anchor"
    assert "v0.5" in result.get("searched_versions", [])
    assert result.get("corpus_digests")  # deterministic corpus manifest


def test_sage_triage_bad_anchor_references_untruthful_ac(tmp_path, monkeypatch):
    _hotfix_target_repo(tmp_path)
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", "sage:SAGE_TRIAGE=bad_anchor")
    result = FakeBackend(tmp_path, "v0.5-hotfix-42").act(
        "sage",
        "SAGE_TRIAGE",
        None,
        None,
        _hotfix_anchor_assignment(),
    )

    assert result["status"] == "done"
    # the fake cites an AC that does not exist in the target acceptance so the
    # programmatic cross-version validation fails and Sage is red-dispatched
    assert result.get("acs") == ["AC-FR9999-99@v0.5"]


def test_sage_triage_token_sequence_consumes_one_outcome_per_call(tmp_path, monkeypatch):
    _hotfix_target_repo(tmp_path)
    backend = FakeBackend(tmp_path, "v0.5-hotfix-42")
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", "sage:SAGE_TRIAGE=anchor|no_anchor")

    first = backend.act("sage", "SAGE_TRIAGE", None, None, _hotfix_anchor_assignment())
    second = backend.act("sage", "SAGE_TRIAGE", None, None, _hotfix_anchor_assignment())

    assert first.get("acs") == ["AC-FR0030-01@v0.5"]
    assert second.get("outcome") == "no_anchor"


def test_prism_review_default_anchor_verdict_is_upheld(tmp_path):
    result = FakeBackend(tmp_path, "v0.5-hotfix-42").act(
        "prism", "PRISM_REVIEW", None, None, {"anchor_acs": ["AC-FR0030-01@v0.5"]}
    )

    assert result["status"] == "done"
    assert result.get("anchor_verdict") == "upheld"


def test_prism_review_anchor_overturned_token_sets_anchor_verdict(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", "prism:PRISM_REVIEW=anchor_overturned")
    result = FakeBackend(tmp_path, "v0.5-hotfix-42").act(
        "prism", "PRISM_REVIEW", None, None, {"anchor_acs": ["AC-FR0030-01@v0.5"]}
    )

    assert result["status"] == "done"
    assert result.get("anchor_verdict") == "overturned"


def _devon_hotfix_assignment(phase: str = "red", issue: int = 42) -> dict:
    value = {
        "task_id": "T-006",
        "phase": phase,
        "if_ids": ["IF-HOTFIX-009"],
        "ac_refs": ["AC-FR0243-02"],
        "test_refs": ["tests/unit/test_fake_backend.py"],
        "commands": [".venv/bin/python -m pytest -n 4 tests/unit/test_fake_backend.py"],
        "manifest": {
            "allowed_paths": ["tracks/effects/fake.py", "tests/unit/test_fake_backend.py"],
            "forbidden_paths": [".tracks/projects/**", "tests/integration/**"],
        },
        "pre_dirty_snapshot": {},
        "result_identity": "result-1",
        "hotfix_issue": issue,
    }
    if phase in ("green", "refactor"):
        value["r_tree_identity"] = "r-tree-1"
    return value


def test_devon_hotfix_outcome_carries_tracks_issue_trailer(tmp_path):
    result = FakeBackend(tmp_path, "v0.5-hotfix-42").act(
        "devon", "RED", None, None, _devon_hotfix_assignment("red", issue=42)
    )

    assert result["status"] == "done"
    audit = result.get("audit_evidence") or {}
    assert "Tracks-Issue" in audit
    assert "42" in str(audit.get("trailers", {})) or "Tracks-Issue" in str(audit)


def test_archer_hotfix_design_writes_delta_trio_with_anchored_acs(tmp_path):
    vdir = _hotfix_target_repo(tmp_path)
    # the hotfix run's own project dir already exists (created at entry by the
    # Runtime); the delta trio must land there, distinct from the baseline dir
    hvdir = paths.version_dir(paths.tracks_home(tmp_path), "v0.5-hotfix-42")
    hvdir.mkdir(parents=True, exist_ok=True)
    result = FakeBackend(tmp_path, "v0.5-hotfix-42").act(
        "archer",
        "DRAFT",
        None,
        None,
        {
            "anchor_acs": ["AC-FR0030-01@v0.5", "AC-NFR0020-01@v0.5"],
            "target_version": "v0.5",
            "baseline_doc_paths": [str(vdir / name) for name in
                                   ("story.md", "spec.md", "acceptance.md",
                                    "architecture.md", "interfaces.md", "test-plan.md")],
        },
    )

    assert result["status"] == "done"
    arch = (hvdir / "architecture.md").read_text(encoding="utf-8")
    interfaces = (hvdir / "interfaces.md").read_text(encoding="utf-8")
    test_plan = (hvdir / "test-plan.md").read_text(encoding="utf-8")
    # delta trio carries the anchored cross-version AC references (FR-0243-02)
    assert "AC-FR0030-01@v0.5" in arch + interfaces + test_plan
    assert "AC-NFR0020-01@v0.5" in arch + interfaces + test_plan
    # FR-0241-02: hotfix run inherits, never creates, requirement-stage artifacts
    assert not (hvdir / "story.md").exists()
    assert not (hvdir / "spec.md").exists()
    assert not (hvdir / "acceptance.md").exists()


def test_shield_hotfix_write_uses_cross_version_ac_marker(tmp_path):
    FakeBackend(tmp_path, "v0.5-hotfix-42").act(
        "shield",
        "WRITE",
        None,
        None,
        {
            "test_tasks": [
                {"ac_id": "AC-FR0030-01", "layers": ["integration"],
                 "if_ids": ["IF-HOTFIX-007"]},
                {"ac_id": "AC-NFR0020-01", "layers": ["e2e"],
                 "if_ids": ["IF-HOTFIX-007"]},
            ],
        },
    )

    integration = (tmp_path / "tests" / "integration" / "test_ac_fr0030_01.py").read_text(
        encoding="utf-8"
    )
    # hotfix WRITE regression case: cross-version AC marker bound to the target
    # baseline version, not the hotfix run's own -hotfix- identity (FR-0244-03)
    assert "AC-FR0030-01@v0.5 TRACKS-TRACE" in integration
    assert "@v0.5-hotfix-42" not in integration
