"""OpencodeBackend artifact manifest tests."""

import json
import subprocess

import pytest

from tests.integration.test_opencode_backend import fake_opencode as fake_opencode
from tracks.effects.opencode import OpencodeBackend


@pytest.fixture
def target_doc(host_repo):
    doc = host_repo / "story.md"
    doc.write_text("---\ntitle:\n---\n\n# Demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "story.md"], cwd=host_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "story skeleton"], cwd=host_repo, check=True, capture_output=True
    )
    return doc


def backend(host_repo):
    return OpencodeBackend(host_repo, "v0.2")


# -- BOOT-ATTRIBUTION-001: manifest extraction from text NDJSON ----------------


_M_TEST_DOCS = ("test-plan.md", "interfaces.md", "acceptance.md")


def _m_test_assignment_v04():
    return {"kind": "WRITE", "skills": ["tracks-discuz"], "docs": list(_M_TEST_DOCS)}


def _commit_m_test_docs(host_repo, vdir):
    for name in _M_TEST_DOCS:
        path = vdir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nsha:\n---\n\n# doc\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=host_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "m-test docs"], cwd=host_repo, check=True, capture_output=True
    )


def _set_shield_env(monkeypatch, behavior, docs, extra=None):
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", behavior)
    monkeypatch.setenv("FAKE_OPENCODE_DOCS", ",".join(map(str, docs)))
    if extra is not None:
        monkeypatch.setenv("FAKE_OPENCODE_EXTRA", str(extra))


def _write_project_layout(host_repo):
    """Write the Archer-produced ``.tracks/projects/project.toml`` layout
    contract (FR-0120/IF-DEVON-001): the FR-0170 over-reach audit authorizes
    a Shield WRITE's test-asset writes only when ``[layout.shield].writable``
    declares them.  The real M-DESIGN flow produces this file before any
    dispatch; the manifest tests mirror that host state so the fake agent's
    write under ``tests/integration/`` is in scope, not over-reach."""
    toml = host_repo / ".tracks" / "projects" / "project.toml"
    toml.parent.mkdir(parents=True, exist_ok=True)
    toml.write_text(
        "[integration]\n"
        'framework = "pytest"\n'
        'paths = ["tests/integration/"]\n'
        'collect = ".venv/bin/python -m pytest --collect-only -q tests/integration/"\n'
        'run = ".venv/bin/python -m pytest tests/integration/ --tb=short -q"\n'
        'cwd = "."\n\n'
        "[e2e]\n"
        'framework = "pytest"\n'
        'paths = ["tests/e2e/"]\n'
        'collect = ".venv/bin/python -m pytest --collect-only -q tests/e2e/"\n'
        'run = ".venv/bin/python -m pytest tests/e2e/ --tb=short -q"\n'
        'cwd = "."\n\n'
        "[layout]\n\n"
        "[layout.devon]\n"
        'writable = ["tracks/", "tests/unit/"]\n\n'
        "[layout.shield]\n"
        'writable = ["tests/integration/", "tests/e2e/", "tests/e2e_live/", "tests/assets/", "tests/counterexamples/"]\n',
        encoding="utf-8",
    )


def test_shield_write_manifest_extracted_from_final_text_event(
    fake_opencode, host_repo, monkeypatch
):
    """Real opencode JSON output carries the final Agent text in part.text."""
    from tracks import paths

    _write_project_layout(host_repo)
    vdir = paths.version_dir(paths.tracks_home(host_repo), "v0.4")
    vdir.mkdir(parents=True, exist_ok=True)
    _commit_m_test_docs(host_repo, vdir)
    docs = [vdir / n for n in _M_TEST_DOCS]
    test_file = host_repo / "tests" / "integration" / "test_manifest.py"
    _set_shield_env(monkeypatch, "shield_test", docs, test_file)
    payload = {
        "artifact_manifest": {
            "include": [
                {
                    "path": "tests/integration/test_manifest.py",
                    "kind": "integration",
                    "role": "required",
                }
            ]
        },
        "suggested_commit_message": "M-TEST: add manifest integration test",
    }
    monkeypatch.setenv("FAKE_OPENCODE_FINAL_TEXT", f"\n  {json.dumps(payload)}  \n")
    out = OpencodeBackend(host_repo, "v0.4").act(
        "shield", "WRITE", None, None, assignment=_m_test_assignment_v04()
    )
    assert out["status"] == "done"
    assert out["artifact_manifest"]["include"] == [
        {"path": "tests/integration/test_manifest.py", "kind": "integration", "role": "required"}
    ]
    assert out["suggested_commit_message"] == ("M-TEST: add manifest integration test")


def test_shield_write_manifest_extracted_from_markdown_wrapped_text(
    fake_opencode, host_repo, monkeypatch
):
    """Agent prose (Markdown) before the JSON manifest is tolerated."""
    from tracks import paths

    _write_project_layout(host_repo)
    vdir = paths.version_dir(paths.tracks_home(host_repo), "v0.4")
    vdir.mkdir(parents=True, exist_ok=True)
    _commit_m_test_docs(host_repo, vdir)
    docs = [vdir / n for n in _M_TEST_DOCS]
    test_file = host_repo / "tests" / "integration" / "test_manifest_md.py"
    _set_shield_env(monkeypatch, "shield_test", docs, test_file)
    payload = {
        "artifact_manifest": {
            "include": [
                {
                    "path": "tests/integration/test_manifest_md.py",
                    "kind": "integration",
                    "role": "required",
                }
            ]
        },
        "suggested_commit_message": "M-TEST: add manifest markdown test",
    }
    prose = (
        "All 14 previously-referenced test files are already committed. "
        "My assignment's changes are:\n"
        "- **Modified**: `test_rgr_contract.py`, `test_interfaces.py`\n\n"
        "Here is the outcome manifest:\n\n"
    )
    monkeypatch.setenv("FAKE_OPENCODE_FINAL_TEXT", prose + json.dumps(payload))
    out = OpencodeBackend(host_repo, "v0.4").act(
        "shield", "WRITE", None, None, assignment=_m_test_assignment_v04()
    )
    assert out["status"] == "done"
    assert out["artifact_manifest"]["include"] == [
        {"path": "tests/integration/test_manifest_md.py", "kind": "integration", "role": "required"}
    ]
    assert out["suggested_commit_message"] == ("M-TEST: add manifest markdown test")


def test_shield_write_missing_required_field_fails(fake_opencode, host_repo, monkeypatch):
    from tracks import paths

    vdir = paths.version_dir(paths.tracks_home(host_repo), "v0.4")
    vdir.mkdir(parents=True, exist_ok=True)
    _commit_m_test_docs(host_repo, vdir)
    docs = [vdir / n for n in _M_TEST_DOCS]
    test_file = host_repo / "tests" / "integration" / "test_missing.py"
    _set_shield_env(monkeypatch, "shield_test", docs, test_file)
    monkeypatch.setenv(
        "FAKE_OPENCODE_FINAL_TEXT",
        json.dumps(
            {
                "artifact_manifest": {
                    "include": [
                        {
                            "path": "tests/integration/test_missing.py",
                            "kind": "integration",
                        }
                    ]
                },
                "suggested_commit_message": "M-TEST: add missing test",
            }
        ),
    )
    out = OpencodeBackend(host_repo, "v0.4").act(
        "shield", "WRITE", None, None, assignment=_m_test_assignment_v04()
    )
    assert out["status"] == "failed"
    assert out["failure_class"] == "manifest_malformed"


def test_shield_write_non_json_final_text_fails(fake_opencode, host_repo, monkeypatch):
    from tracks import paths

    vdir = paths.version_dir(paths.tracks_home(host_repo), "v0.4")
    vdir.mkdir(parents=True, exist_ok=True)
    _commit_m_test_docs(host_repo, vdir)
    docs = [vdir / n for n in _M_TEST_DOCS]
    test_file = host_repo / "tests" / "integration" / "test_non_json.py"
    _set_shield_env(monkeypatch, "shield_test", docs, test_file)
    monkeypatch.setenv("FAKE_OPENCODE_FINAL_TEXT", "manifest unavailable")
    out = OpencodeBackend(host_repo, "v0.4").act(
        "shield", "WRITE", None, None, assignment=_m_test_assignment_v04()
    )
    assert out["status"] == "failed"
    assert out["failure_class"] == "manifest_malformed"


def test_shield_write_quota_error_classified_as_provider_unavailable(
    fake_opencode, host_repo, monkeypatch
):
    """When the LLM stream is interrupted by a quota error, opencode exits 0
    with valid JSON events on stdout but a truncated final text.  The manifest
    is malformed, but the root cause is provider quota — classify as
    provider_unavailable so the runtime retries instead of wasting dispatch
    attempts."""
    from tracks import paths

    vdir = paths.version_dir(paths.tracks_home(host_repo), "v0.4")
    vdir.mkdir(parents=True, exist_ok=True)
    _commit_m_test_docs(host_repo, vdir)
    docs = [vdir / n for n in _M_TEST_DOCS]
    test_file = host_repo / "tests" / "integration" / "test_quota.py"
    _set_shield_env(monkeypatch, "quota_error", docs, test_file)
    monkeypatch.setenv("FAKE_OPENCODE_FINAL_TEXT", "manifest truncated")
    out = OpencodeBackend(host_repo, "v0.4").act(
        "shield", "WRITE", None, None, assignment=_m_test_assignment_v04()
    )
    assert out["status"] == "failed"
    assert out["failure_class"] == "provider_unavailable"


def test_other_role_does_not_require_manifest(fake_opencode, target_doc, host_repo, monkeypatch):
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_target")
    monkeypatch.setenv("FAKE_OPENCODE_TARGET", str(target_doc))
    monkeypatch.setenv("FAKE_OPENCODE_FINAL_TEXT", "not JSON")
    out = backend(host_repo).act("scribe", "DRAFT", "story.md", target_doc)
    assert out["status"] == "done"
    assert out.get("failure_class") is None
