"""Item 6: _maybe_add_scaffold must update dedup state after appending
scaffold paths to prevent duplicate project.toml in the stage list.

When the canonical ``.tracks/projects/project.toml`` is both declared in the
Scaffold 宣言 and returned by ``_project_contract_paths()``, it must appear
in ``stage_paths`` exactly once.
"""

from pathlib import Path

from tracks.executor.result_checkpoint import ResultCheckpointMixin
from tracks.kernel.machine import State


class _FakeExecutor(ResultCheckpointMixin):
    """Minimal host providing the methods _maybe_add_scaffold needs."""

    def __init__(self, repo, scaffold_paths, contract_paths):
        self.repo = repo
        self._scaffold_paths = scaffold_paths
        self._contract_paths = contract_paths
        self.commit_failure_calls = []

    def _artifact_path(self, doc):
        return Path(self.repo) / doc

    def _design_scaffold_paths(self, arch_path):
        return set(self._scaffold_paths), None

    def _project_contract_paths(self):
        return list(self._contract_paths)

    def _emit_commit_failure_evidence(self, state, command_id, reason, evidence):
        self.commit_failure_calls.append((reason, evidence))


def test_maybe_add_scaffold_no_duplicate_project_toml(tmp_path):
    """project.toml appears exactly once when it is in both scaffold_paths
    and _project_contract_paths()."""
    repo = tmp_path / "repo"
    repo.mkdir()

    contract_toml = repo / ".tracks" / "project" / "project.toml"
    contract_toml.parent.mkdir(parents=True)
    contract_toml.write_text("[integration]\nframework = 'pytest'\n")

    fake = _FakeExecutor(
        repo=repo,
        scaffold_paths=[Path(".tracks/projects/project.toml")],
        contract_paths=[Path(".tracks/projects/project.toml")],
    )

    state = State(
        stage="M-DESIGN",
        substate="DRAFT",
        current_attempt=0,
    )

    allowed_paths = ["architecture.md"]
    stage_paths = [fake._artifact_path("architecture.md")]

    result = fake._maybe_add_scaffold(
        state, allowed_paths, stage_paths, type("Cmd", (), {"command_id": "x"})(), attempt=1
    )
    assert result is False, "should not abort"

    toml_count = sum(1 for p in stage_paths if str(p) == ".tracks/projects/project.toml")
    assert toml_count == 1, (
        f"project.toml appeared {toml_count} times in stage_paths; stage_paths={stage_paths}"
    )


def test_maybe_add_scaffold_no_duplicate_when_scaffold_has_extra(tmp_path):
    """Scaffold paths not in contract_paths are added once; contract paths
    already in scaffold are not duplicated."""
    repo = tmp_path / "repo"
    repo.mkdir()

    (repo / "src").mkdir()
    (repo / "src" / "module.py").write_text("")

    contract_toml = repo / ".tracks" / "project" / "project.toml"
    contract_toml.parent.mkdir(parents=True)
    contract_toml.write_text("[integration]\nframework = 'pytest'\n")

    fake = _FakeExecutor(
        repo=repo,
        scaffold_paths=[
            Path(".tracks/projects/project.toml"),
            Path("src/module.py"),
        ],
        contract_paths=[Path(".tracks/projects/project.toml")],
    )

    state = State(stage="M-DESIGN", substate="DRAFT", current_attempt=0)

    allowed_paths = ["architecture.md"]
    stage_paths = [fake._artifact_path("architecture.md")]

    result = fake._maybe_add_scaffold(
        state, allowed_paths, stage_paths, type("Cmd", (), {"command_id": "x"})(), attempt=1
    )
    assert result is False

    toml_count = sum(1 for p in stage_paths if str(p) == ".tracks/projects/project.toml")
    assert toml_count == 1, f"project.toml appeared {toml_count} times; stage_paths={stage_paths}"

    module_count = sum(1 for p in stage_paths if str(p) == "src/module.py")
    assert module_count == 1, f"module.py appeared {module_count} times; stage_paths={stage_paths}"
