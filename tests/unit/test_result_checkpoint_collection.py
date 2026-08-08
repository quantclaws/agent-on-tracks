"""Focused tests for ResultCheckpoint._check_collection conftest handling.

Contract: collection runs ``pytest --collect-only`` only on test module
targets (``test_*.py`` / ``*_test.py``). ``conftest.py`` and helper files
are support files that pytest auto-loads when collecting the parent
directory; they must NOT be collected as standalone test nodes (pytest
returns rc=5 / no tests). At least one test module target is required;
otherwise fail closed with check=collection.
"""

import subprocess
from pathlib import Path

from tracks.executor.result_checkpoint import ResultCheckpointMixin


class _FakeExecutor(ResultCheckpointMixin):
    """Minimal host providing repo, _artifact_path, and _emit for
    _check_collection tests."""

    def __init__(self, repo):
        self.repo = Path(repo)
        self.failed_events = []

    def _artifact_path(self, name):
        return self.repo / name

    def _emit(self, event_type, payload, command_id=None, task_id=None):
        if event_type == "verdict.failed":
            self.failed_events.append(payload)


def _spy_subprocess_run(monkeypatch, captured):
    """Patch subprocess.run in result_checkpoint to record argv while
    delegating to the real implementation."""
    real_run = subprocess.run

    def _spy(*args, **kwargs):
        captured.append(list(args[0]))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(
        "tracks.executor.result_checkpoint.subprocess.run", _spy)
    return captured


def _target_basenames(targets):
    """Extract the basename of the pytest target path (last argv element)
    from each captured subprocess call."""
    return [Path(t[-1]).name for t in targets]


# -- RED 1: conftest + test_sample -> pass, conftest not collected --------


def test_collection_skips_conftest_collects_test_prefix_module(
        tmp_path, monkeypatch):
    """artifacts=[conftest.py, test_sample.py] with a collectible test
    passes; conftest is never a subprocess target."""
    repo = tmp_path / "repo"
    (repo / "tests" / "e2e").mkdir(parents=True)
    (repo / "tests" / "e2e" / "conftest.py").write_text("", encoding="utf-8")
    (repo / "tests" / "e2e" / "test_sample.py").write_text(
        "def test_sample():\n    assert True\n", encoding="utf-8")

    fake = _FakeExecutor(repo)
    targets = _spy_subprocess_run(monkeypatch, [])

    result = fake._check_collection(
        ["tests/e2e/conftest.py", "tests/e2e/test_sample.py"],
        attempt=1, command_id="cmd-1")

    assert result is False, "collection must pass when test_sample collects"
    assert fake.failed_events == []
    basenames = _target_basenames(targets)
    assert "conftest.py" not in basenames, (
        f"conftest must never be a collection target; got {basenames}")
    assert "test_sample.py" in basenames, (
        "test_sample.py must be collected")


# -- RED 2: only conftest/helper -> fail closed (no test modules) ---------


def test_collection_only_conftest_helper_fails_closed(tmp_path):
    """artifacts with only conftest/helper (no test modules) -> fail closed
    with check=collection (no test modules)."""
    repo = tmp_path / "repo"
    (repo / "tests" / "e2e").mkdir(parents=True)
    (repo / "tests" / "e2e" / "conftest.py").write_text("", encoding="utf-8")
    (repo / "tests" / "e2e" / "helpers.py").write_text("", encoding="utf-8")

    fake = _FakeExecutor(repo)

    result = fake._check_collection(
        ["tests/e2e/conftest.py", "tests/e2e/helpers.py"],
        attempt=1, command_id="cmd-1")

    assert result is True
    assert len(fake.failed_events) == 1
    assert fake.failed_events[0]["check"] == "collection"
    assert "no test module" in fake.failed_events[0]["reason"]


# -- RED 3: sample_test.py (*_test.py) recognised as test module ---------


def test_collection_recognises_suffix_test_module(tmp_path, monkeypatch):
    """sample_test.py (*_test.py pattern) is recognised as a test module
    and collected; conftest alongside it is skipped."""
    repo = tmp_path / "repo"
    (repo / "tests" / "unit").mkdir(parents=True)
    (repo / "tests" / "unit" / "conftest.py").write_text("", encoding="utf-8")
    (repo / "tests" / "unit" / "sample_test.py").write_text(
        "def test_thing():\n    assert True\n", encoding="utf-8")

    fake = _FakeExecutor(repo)
    targets = _spy_subprocess_run(monkeypatch, [])

    result = fake._check_collection(
        ["tests/unit/conftest.py", "tests/unit/sample_test.py"],
        attempt=1, command_id="cmd-1")

    assert result is False
    assert fake.failed_events == []
    basenames = _target_basenames(targets)
    assert "sample_test.py" in basenames, (
        "sample_test.py must be collected")
    assert "conftest.py" not in basenames, (
        f"conftest must never be a collection target; got {basenames}")


# -- RED 4: syntax-error test module still fails, precise report --------


def test_collection_syntax_error_test_module_fails_precisely(tmp_path):
    """A test module with a syntax error still fails collection, reporting
    the specific module name."""
    repo = tmp_path / "repo"
    (repo / "tests" / "unit").mkdir(parents=True)
    (repo / "tests" / "unit" / "test_broken.py").write_text(
        "def test_broken(:\n    pass\n", encoding="utf-8")

    fake = _FakeExecutor(repo)

    result = fake._check_collection(
        ["tests/unit/test_broken.py"],
        attempt=1, command_id="cmd-1")

    assert result is True
    assert len(fake.failed_events) == 1
    assert fake.failed_events[0]["check"] == "collection"
    assert "test_broken" in fake.failed_events[0]["reason"]
