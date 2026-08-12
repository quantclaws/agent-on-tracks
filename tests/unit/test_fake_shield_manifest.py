"""Focused unit tests for FakeBackend Shield WRITE ``artifact_manifest``.

Root cause: ResultCheckpoint ``_m_test_write_payload`` requires
``result.artifact_manifest.include`` paths to exactly match the tests/ files
whose content identity changed during the Shield WRITE dispatch. FakeBackend
now returns an ``artifact_manifest`` computed from content-identity changes
before/after the dispatch, matching ResultCheckpoint's observed identity
semantics.

Covers: first write manifest, no-op identical rewrite, evidence-driven
revision re-dispatch, sorted/unique repo-relative paths, pre-existing
untouched file exclusion, and failed outcome.
"""
from __future__ import annotations

from tracks.effects.fake import FakeBackend

_TASKS_SINGLE = [
    {"ac_id": "AC-FR0010-01", "layers": ["integration"],
     "if_ids": ["IF-MTEST-001"]},
]

_TASKS_MULTI = [
    {"ac_id": "AC-FR0010-01", "layers": ["integration", "e2e"],
     "if_ids": ["IF-MTEST-001"]},
    {"ac_id": "AC-FR0020-01", "layers": ["integration"],
     "if_ids": ["IF-MTEST-001"]},
]


def _assignment(tasks, evidence=None):
    a = {"test_tasks": tasks}
    if evidence is not None:
        a["evidence"] = evidence
    return a


def _act(repo, tasks, evidence=None):
    return FakeBackend(repo, "v0.4").act(
        "shield", "WRITE", None, None, _assignment(tasks, evidence),
    )


def _manifest_paths(result):
    return [
        e["path"]
        for e in result.get("artifact_manifest", {}).get("include", [])
    ]


# -- first write manifest ----------------------------------------------------

def test_first_write_manifest_includes_all_created_test_files(tmp_path):
    result = _act(tmp_path, _TASKS_SINGLE)

    assert result["status"] == "done"
    assert _manifest_paths(result) == [
        "tests/integration/test_ac_fr0010_01.py"
    ]


def test_first_write_multi_layer_multi_ac_manifest(tmp_path):
    result = _act(tmp_path, _TASKS_MULTI)

    assert result["status"] == "done"
    expected = sorted([
        "tests/integration/test_ac_fr0010_01.py",
        "tests/e2e/test_ac_fr0010_01.py",
        "tests/integration/test_ac_fr0020_01.py",
    ])
    assert _manifest_paths(result) == expected


# -- no-op identical rewrite -------------------------------------------------

def test_noop_identical_rewrite_manifest_is_empty(tmp_path):
    first = _act(tmp_path, _TASKS_SINGLE)
    assert _manifest_paths(first) == [
        "tests/integration/test_ac_fr0010_01.py"
    ]

    second = _act(tmp_path, _TASKS_SINGLE)

    assert second["status"] == "done"
    assert _manifest_paths(second) == [], (
        "identical rewrite must not claim unchanged files; got: "
        + repr(_manifest_paths(second))
    )


# -- evidence-driven revision re-dispatch ------------------------------------

def test_revision_redispatch_manifest_includes_modified_files(tmp_path):
    _act(tmp_path, _TASKS_SINGLE)
    test_file = tmp_path / "tests" / "integration" / "test_ac_fr0010_01.py"
    first_content = test_file.read_text(encoding="utf-8")

    evidence = {
        "check": "no_diff", "reason": "result requires a diff", "attempt": 1,
    }
    second = _act(tmp_path, _TASKS_SINGLE, evidence=evidence)

    assert second["status"] == "done"
    assert _manifest_paths(second) == [
        "tests/integration/test_ac_fr0010_01.py"
    ], ("revision marker modifies the file; it must appear in the manifest")
    second_content = test_file.read_text(encoding="utf-8")
    assert len(second_content) > len(first_content), (
        "revision marker must have appended content"
    )


# -- sorted / unique / repo-relative paths -----------------------------------

def test_manifest_paths_are_sorted_unique_and_repo_relative(tmp_path):
    result = _act(tmp_path, _TASKS_MULTI)

    paths = _manifest_paths(result)
    assert paths == sorted(paths), f"paths must be sorted; got {paths}"
    assert len(paths) == len(set(paths)), (
        f"paths must be unique; got {paths}"
    )
    assert all(p.startswith("tests/") for p in paths), (
        f"paths must be under tests/; got {paths}"
    )
    assert all(not p.startswith("/") for p in paths), (
        f"paths must be repo-relative; got {paths}"
    )


# -- pre-existing untouched file exclusion -----------------------------------

def test_preexisting_untouched_file_not_in_manifest(tmp_path):
    tests_dir = tmp_path / "tests" / "integration"
    tests_dir.mkdir(parents=True)
    pre_existing = tests_dir / "test_preexisting.py"
    pre_existing.write_text(
        "def test_preexisting():\n    pass\n", encoding="utf-8",
    )

    result = _act(tmp_path, _TASKS_SINGLE)

    paths = _manifest_paths(result)
    assert "tests/integration/test_ac_fr0010_01.py" in paths
    assert "tests/integration/test_preexisting.py" not in paths, (
        "pre-existing untouched file must not be claimed"
    )


# -- failed outcome ----------------------------------------------------------

def test_failed_outcome_returns_no_artifact_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", "shield:WRITE=fail")
    result = FakeBackend(tmp_path, "v0.4").act(
        "shield", "WRITE", None, None, _assignment(_TASKS_SINGLE),
    )

    assert result["status"] == "failed"
    assert "artifact_manifest" not in result
    assert not (tmp_path / "tests").exists()


def test_invalid_test_tasks_returns_no_artifact_manifest(tmp_path):
    result = FakeBackend(tmp_path, "v0.4").act(
        "shield", "WRITE", None, None, {},
    )

    assert result["status"] == "failed"
    assert "artifact_manifest" not in result
