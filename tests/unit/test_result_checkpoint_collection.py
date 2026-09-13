"""Focused tests for ResultCheckpoint._check_collection conftest handling.

Contract: collection runs ``pytest --collect-only`` only on test module
targets (``test_*.py`` / ``*_test.py``). ``conftest.py`` and helper files
are support files that pytest auto-loads when collecting the parent
directory; they must NOT be collected as standalone test nodes (pytest
returns rc=5 / no tests).

When no test module targets are present (support-only revision: only
``*.patch`` / ``*.json`` / ``conftest.py`` / helper assets), collection
is validated through the host project test contract's ``collect``
command - never silently skipped. If the contract is missing, its
collection returns non-zero, or no suite is collectible, the check
fails closed with check=collection.
"""

import subprocess
from pathlib import Path

from tracks.executor.result_checkpoint import (
    ResultCheckpointMixin,
    _is_regular_file_identity,
)
from tracks.kernel.machine import _CRITERIA_PACK


class _FakeExecutor(ResultCheckpointMixin):
    """Minimal host providing repo, _artifact_path, _emit, and
    _run_contract_sections for _check_collection tests.

    ``contract_results`` / ``contract_error`` configure the
    _run_contract_sections return for support-only revision tests.
    Default: no contract (fail closed)."""

    def __init__(self, repo):
        self.repo = Path(repo)
        self.failed_events = []
        self.emitted_events = []
        self.contract_results = None
        self.contract_error = "contract error: project contract not found"

    def _artifact_path(self, name):
        return self.repo / name

    def _doc_path(self, name):
        return self.repo / name

    def _emit(self, event_type, payload, command_id=None, task_id=None):
        self.emitted_events.append((event_type, payload, command_id, task_id))
        if event_type == "verdict.failed":
            self.failed_events.append(payload)

    def _run_contract_sections(self, cmd, state, field):
        return self.contract_results, self.contract_error


def _spy_subprocess_run(monkeypatch, captured):
    """Patch subprocess.run in result_checkpoint to record argv while
    delegating to the real implementation."""
    real_run = subprocess.run

    def _spy(*args, **kwargs):
        captured.append(list(args[0]))
        return real_run(*args, **kwargs)

    monkeypatch.setattr("tracks.executor.result_checkpoint.subprocess.run", _spy)
    return captured


def _target_basenames(targets):
    """Extract the basename of the pytest target path (last argv element)
    from each captured subprocess call."""
    return [Path(t[-1]).name for t in targets]


# -- RED 1: conftest + test_sample -> pass, conftest not collected --------


def test_collection_skips_conftest_collects_test_prefix_module(tmp_path, monkeypatch):
    """artifacts=[conftest.py, test_sample.py] with a collectible test
    passes; conftest is never a subprocess target."""
    repo = tmp_path / "repo"
    (repo / "tests" / "e2e").mkdir(parents=True)
    (repo / "tests" / "e2e" / "conftest.py").write_text("", encoding="utf-8")
    (repo / "tests" / "e2e" / "test_sample.py").write_text(
        "def test_sample():\n    assert True\n", encoding="utf-8"
    )

    fake = _FakeExecutor(repo)
    targets = _spy_subprocess_run(monkeypatch, [])

    result = fake._check_collection(
        ["tests/e2e/conftest.py", "tests/e2e/test_sample.py"], attempt=1, command_id="cmd-1"
    )

    assert result is False, "collection must pass when test_sample collects"
    assert fake.failed_events == []
    basenames = _target_basenames(targets)
    assert "conftest.py" not in basenames, (
        f"conftest must never be a collection target; got {basenames}"
    )
    assert "test_sample.py" in basenames, "test_sample.py must be collected"


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
        ["tests/e2e/conftest.py", "tests/e2e/helpers.py"], attempt=1, command_id="cmd-1"
    )

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
        "def test_thing():\n    assert True\n", encoding="utf-8"
    )

    fake = _FakeExecutor(repo)
    targets = _spy_subprocess_run(monkeypatch, [])

    result = fake._check_collection(
        ["tests/unit/conftest.py", "tests/unit/sample_test.py"], attempt=1, command_id="cmd-1"
    )

    assert result is False
    assert fake.failed_events == []
    basenames = _target_basenames(targets)
    assert "sample_test.py" in basenames, "sample_test.py must be collected"
    assert "conftest.py" not in basenames, (
        f"conftest must never be a collection target; got {basenames}"
    )


# -- RED 4: syntax-error test module still fails, precise report --------


def test_collection_syntax_error_test_module_fails_precisely(tmp_path):
    """A test module with a syntax error still fails collection, reporting
    the specific module name."""
    repo = tmp_path / "repo"
    (repo / "tests" / "unit").mkdir(parents=True)
    (repo / "tests" / "unit" / "test_broken.py").write_text(
        "def test_broken(:\n    pass\n", encoding="utf-8"
    )

    fake = _FakeExecutor(repo)

    result = fake._check_collection(["tests/unit/test_broken.py"], attempt=1, command_id="cmd-1")

    assert result is True
    assert len(fake.failed_events) == 1
    assert fake.failed_events[0]["check"] == "collection"
    assert "test_broken" in fake.failed_events[0]["reason"]


# -- RED 5: support-only revision passes when contract collects ----------


def test_collection_support_only_revision_passes_when_contract_collects(tmp_path):
    """A support-only revision (no test module targets, only .patch/.json
    assets) passes when the host project contract's collect command
    succeeds — an existing M-TEST suite is present and collectible."""
    repo = tmp_path / "repo"
    repo.mkdir()
    fake = _FakeExecutor(repo)
    fake.contract_results = [("integration", 0, "3 tests collected", "")]
    fake.contract_error = None

    result = fake._check_collection(
        ["tests/counterexamples/fix.patch", "tests/assets/data.json"], attempt=1, command_id="cmd-1"
    )

    assert result is False, "support-only revision must pass when contract collection succeeds"
    assert fake.failed_events == []


# -- RED 6: support-only revision fails when contract collection fails ----


def test_collection_support_only_revision_fails_when_contract_collection_fails(tmp_path):
    """A support-only revision fails closed when the contract's collect
    command returns non-zero (no existing suite / collection error)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    fake = _FakeExecutor(repo)
    fake.contract_results = [("integration", 1, "", "no tests collected")]
    fake.contract_error = None

    result = fake._check_collection(
        ["tests/counterexamples/fix.patch"], attempt=1, command_id="cmd-1"
    )

    assert result is True
    assert len(fake.failed_events) == 1
    assert fake.failed_events[0]["check"] == "collection"
    assert "support-only" in fake.failed_events[0]["reason"]


def test_collection_support_only_reason_identifies_host_project_contract(tmp_path):
    """The fail-closed reason for a support-only revision whose contract
    collection returned non-zero must identify the host project contract,
    not a generic message (test_support_asset_attribution RED 3)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    fake = _FakeExecutor(repo)
    fake.contract_results = [("integration", 1, "", "no tests collected")]
    fake.contract_error = None

    result = fake._check_collection(
        ["tests/counterexamples/fix.patch"], attempt=1, command_id="cmd-1"
    )

    assert result is True
    assert len(fake.failed_events) == 1
    reason = fake.failed_events[0]["reason"]
    assert "host project contract" in reason, f"reason={reason}"
    assert "integration" in reason, f"reason={reason}"
    assert fake.failed_events[0]["evidence"] == "no tests collected"


# -- RED 7: support-only revision fails closed with no contract ----------


def test_collection_support_only_revision_fails_closed_no_contract(tmp_path):
    """A support-only revision with no project contract fails closed
    (cannot validate collection through the contract)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    fake = _FakeExecutor(repo)
    # Default: contract_error set, contract_results None

    result = fake._check_collection(["tests/assets/data.json"], attempt=1, command_id="cmd-1")

    assert result is True
    assert len(fake.failed_events) == 1
    assert fake.failed_events[0]["check"] == "collection"
    assert "contract error" in fake.failed_events[0]["reason"]


# -- FOLLOW-UP: support-only revision fails closed on empty contract results


def test_collection_support_only_revision_fails_closed_empty_results(tmp_path):
    """A support-only revision fails closed when the contract returns an
    empty results list with no error - no collectible suite is verified,
    so the check must not silently pass (Prism advisory: fail closed
    with check=collection)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    fake = _FakeExecutor(repo)
    fake.contract_results = []
    fake.contract_error = None

    result = fake._check_collection(["tests/assets/data.json"], attempt=1, command_id="cmd-1")

    assert result is True
    assert len(fake.failed_events) == 1
    assert fake.failed_events[0]["check"] == "collection"
    assert "no collectible suite" in fake.failed_events[0]["reason"]


# -- RED 8: _is_regular_file_identity helper ----------------------------


def test_is_regular_file_identity_distinguishes_regular_from_non_regular():
    """The identity classifier must accept sha256 hashes and reject
    symlinks, missing, and unreadable entries."""
    assert _is_regular_file_identity("a" * 64) is True, "sha256 hash is a regular file"
    assert _is_regular_file_identity("symlink:/etc/passwd") is False, (
        "symlink is not a regular file"
    )
    assert _is_regular_file_identity("missing") is False
    assert _is_regular_file_identity("unreadable") is False


# -- Prism M-TEST payload serialization (null defect_classification) ---------


def test_m_test_prism_payload_omits_none_defect_classification(tmp_path):
    """A Prism revise result with no defect_classification must NOT serialize
    defect_classification=None into the prism.verdict domain payload. The
    reducer defaults a *missing* key to test_defect; a *present null* used to
    leave M-TEST un-routed (run halt)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test-plan.md").write_text("plan", encoding="utf-8")
    (repo / "interfaces.md").write_text("if", encoding="utf-8")
    fake = _FakeExecutor(repo)

    payload = fake._m_test_prism_payload(
        result={"verdict": "revise", "criteria_pack": dict(_CRITERIA_PACK)},
        base_sha="b",
        result_id="C3",
    )

    domain_payload = payload["domain_event"]["payload"]
    assert domain_payload["verdict"] == "revise"
    assert domain_payload["criteria_pack"] == dict(_CRITERIA_PACK)
    assert "defect_classification" not in domain_payload, (
        "None defect_classification must not be serialized"
    )


def test_m_test_prism_payload_keeps_valid_defect_classification(tmp_path):
    """A Prism revise result WITH a valid defect_classification is preserved
    verbatim in the domain payload (rollback routing must be unchanged)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test-plan.md").write_text("plan", encoding="utf-8")
    (repo / "interfaces.md").write_text("if", encoding="utf-8")
    fake = _FakeExecutor(repo)

    payload = fake._m_test_prism_payload(
        result={
            "verdict": "revise",
            "criteria_pack": {},
            "defect_classification": "test_plan_defect",
        },
        base_sha="b",
        result_id="C3",
    )

    assert payload["domain_event"]["payload"]["defect_classification"] == "test_plan_defect"


def test_publish_prism_verdict_omits_none_defect_classification(tmp_path):
    """_publish_prism_verdict must not write defect_classification=None into
    the published prism.verdict payload for M-TEST when the checkpoint did
    not carry one."""
    from types import SimpleNamespace

    repo = tmp_path / "repo"
    repo.mkdir()
    fake = _FakeExecutor(repo)
    cmd = SimpleNamespace(
        command_id="C3",
        params={
            "domain_event": {
                "type": "prism.verdict",
                "payload": {"verdict": "revise", "criteria_pack": {"name": "x"}},
            }
        },
    )
    state = SimpleNamespace(stage="M-TEST")

    fake._publish_prism_verdict(
        "revise",
        commit_sha="c",
        created_commit=True,
        result_id="C3",
        state=state,
        cmd=cmd,
        task_id=None,
    )

    published = [p for t, p, _, _ in fake.emitted_events if t == "prism.verdict"]
    assert len(published) == 1
    assert published[0]["verdict"] == "revise"
    assert "defect_classification" not in published[0], (
        "None defect_classification must not be published"
    )


def test_publish_prism_verdict_keeps_valid_defect_classification(tmp_path):
    """A valid defect_classification in the checkpoint's domain payload is
    published verbatim (rollback routing preserved)."""
    from types import SimpleNamespace

    repo = tmp_path / "repo"
    repo.mkdir()
    fake = _FakeExecutor(repo)
    cmd = SimpleNamespace(
        command_id="C3",
        params={
            "domain_event": {
                "type": "prism.verdict",
                "payload": {
                    "verdict": "revise",
                    "criteria_pack": {"name": "x"},
                    "defect_classification": "acceptance_defect",
                },
            }
        },
    )
    state = SimpleNamespace(stage="M-TEST")

    fake._publish_prism_verdict(
        "revise",
        commit_sha="c",
        created_commit=True,
        result_id="C3",
        state=state,
        cmd=cmd,
        task_id=None,
    )

    published = [p for t, p, _, _ in fake.emitted_events if t == "prism.verdict"]
    assert published[0]["defect_classification"] == "acceptance_defect"


class _DesignPublishHost(ResultCheckpointMixin):
    """Minimal host capturing the M-DESIGN publish side effects."""

    def __init__(self, repo):
        self.repo = Path(repo)
        self.committed = []
        self.issued_commands = []

    def _emit_committed(self, doc, sha, command_id, result_id=None):
        self.committed.append((doc, sha, result_id))

    def issue(self, cmd, command_id=None):
        self.issued_commands.append(cmd)


def test_design_publish_materializes_host_contract(tmp_path):
    """IF-HOSTCONTRACT-002 / AC-FR0281-01: Archer's M-DESIGN completion
    (the design publish) issues the production materialize_host_contract
    command so the versioned host contract is recorded at design time."""
    from types import SimpleNamespace

    host = _DesignPublishHost(tmp_path)
    cmd = SimpleNamespace(
        command_id="C-DESIGN",
        params={
            "artifacts": ["architecture.md", "interfaces.md", "test-plan.md"],
            "result_id": "R1",
        },
    )

    host._publish_design_committed("SHA-DESIGN", None, cmd)

    assert [doc for doc, _, _ in host.committed] == [
        "architecture.md",
        "interfaces.md",
        "test-plan.md",
    ]
    materialize = [
        c for c in host.issued_commands if c.kind == "materialize_host_contract"
    ]
    assert materialize, "the design publish must materialize the host contract"
    assert materialize[0].params["contract_path"].endswith(
        ".tracks/projects/project.toml"
    )
