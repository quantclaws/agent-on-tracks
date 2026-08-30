"""T-015 RED: engine commands must come from the host project contract.

plan_defect main cause (dfbf379 rename leftover): the four m_impl_runtime
command builders (``_unit_commands``, ``_unit_commands_from_manifest``,
``_do_anchor_red``, ``_do_verify_task``) hardcode a ``framework_runner``
module that exists nowhere in the tree. interfaces §1h keeps
kernel/executor/cli runtime code language-neutral by construction -- the
only framework literals live in the host project.toml, and every engine
argv must be the contract ``run``/``run_selected`` expansion (the
anchor_probe.py / test_select.py pattern via ``load_contract``).

Every failing assertion below lands on the contract token, never on an
assembly error.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.unit.helpers import git_repo
from tracks import paths
from tracks.executor.executor import Executor
from tracks.executor.m_impl_runtime import MImplRuntimeMixin
from tracks.executor.test_select import audit
from tracks.kernel.events import Command
from tracks.project import load_contract
from tracks.store import Store

_REPO_ROOT = Path(__file__).resolve().parents[2]

_UNIT_RUN = (
    ".venv/bin/python -m pytest tests/unit/ --tb=short -q -n 8 "
    "--dist loadscope --junitxml={result}"
)
_UNIT_RUN_SELECTED = (
    ".venv/bin/python -m pytest {nodes} --tb=short -q -n 8 "
    "--dist loadscope --junitxml={result}"
)
_INTEGRATION_RUN_SELECTED = (
    ".venv/bin/python -m pytest {nodes} --tb=short -q -n 8 "
    "--dist loadscope --junitxml={result}"
)

_ANCHOR_REFS = [
    "tests/integration/test_anchor.py::test_one",
    "tests/integration/test_anchor.py::test_two",
]

_FORBIDDEN_ZONES = ("tracks/kernel", "tracks/executor", "tracks/cli")
_PHANTOM_TOKEN = "framework_runner"


def _engine_repo(tmp_path: Path) -> Path:
    """Minimal host repo whose project.toml is the only framework-literal source."""
    repo = git_repo(tmp_path, gitignore=True)
    contract = paths.project_toml_path(paths.tracks_home(repo))
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text(
        "[unit]\nframework='pytest'\npaths=['tests/unit/']\n"
        "collect='.venv/bin/python -m pytest --collect-only -q tests/unit/'\n"
        f"run='{_UNIT_RUN}'\n"
        f"run_selected='{_UNIT_RUN_SELECTED}'\ncwd='.'\n\n"
        "[integration]\nframework='pytest'\npaths=['tests/integration/']\n"
        "collect='.venv/bin/python -m pytest --collect-only -q tests/integration/'\n"
        "run='.venv/bin/python -m pytest tests/integration/ --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\n"
        f"run_selected='{_INTEGRATION_RUN_SELECTED}'\ncwd='.'\n\n"
        "[e2e]\nframework='pytest'\npaths=['tests/e2e/']\n"
        "collect='.venv/bin/python -m pytest --collect-only -q tests/e2e/'\n"
        "run='.venv/bin/python -m pytest tests/e2e/ --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\n"
        f"run_selected='{_INTEGRATION_RUN_SELECTED}'\ncwd='.'\n\n"
        "[nightly]\nschedule='0 3 * * *'\nworkflow='.github/workflows/nightly.yml'\n"
        "job='nightly-regression'\nlayers=['unit','integration','e2e']\n"
        "purpose='regression'\n\n"
        "[layout.devon]\nwritable=['tracks/', 'tests/unit/']\n",
        encoding="utf-8",
    )
    (repo / "tests" / "integration").mkdir(parents=True)
    (repo / "tests" / "e2e").mkdir(parents=True)
    return repo


class _ContractHost(MImplRuntimeMixin):
    """Bare mixin host: the command builders only need ``repo``."""

    def __init__(self, repo: Path):
        self.repo = repo


class _ManifestHost(_ContractHost):
    """Host whose ``_current_manifest`` is fixed for the fallback test."""

    def __init__(self, repo: Path, manifest: dict | None):
        super().__init__(repo)
        self._manifest = manifest

    def _current_manifest(self) -> dict | None:
        return self._manifest


# AC-NFR0140-01@v0.7 TRACKS-TRACE unit commands derive from the contract run
def test_unit_commands_derive_from_contract_unit_run(tmp_path):
    repo = _engine_repo(tmp_path)
    commands = _ContractHost(repo)._unit_commands()
    assert commands == [load_contract(Path(repo)).unit.run], (
        "engine unit commands must be the host contract [unit].run "
        "(load_contract pattern), not a hardcoded runner module"
    )


# AC-NFR0140-01@v0.7 TRACKS-TRACE manifest fallback is contract-derived
def test_unit_commands_manifest_fallback_derives_from_contract(tmp_path):
    repo = _engine_repo(tmp_path)
    host = _ManifestHost(repo, {"task_id": "T-1"})  # no unit_commands key
    assert host._unit_commands_from_manifest() == [load_contract(Path(repo)).unit.run], (
        "the no-manifest fallback must derive from the host contract, "
        "not a hardcoded runner module"
    )


def test_unit_commands_manifest_passthrough_is_preserved(tmp_path):
    repo = _engine_repo(tmp_path)
    declared = ["host-tool run --all"]
    host = _ManifestHost(repo, {"unit_commands": declared})
    assert host._unit_commands_from_manifest() == declared


# AC-NFR0140-01@v0.7 TRACKS-TRACE runtime sources stay language-neutral
def test_runtime_sources_carry_no_phantom_framework_runner_token():
    offenders: list[str] = []
    for zone in _FORBIDDEN_ZONES:
        for path in sorted((_REPO_ROOT / zone).rglob("*.py")):
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if _PHANTOM_TOKEN in line:
                    offenders.append(f"{zone}/{path.relative_to(_REPO_ROOT / zone)}:{lineno}")
    assert offenders == [], (
        "phantom runner literal outside the host contract "
        "(interfaces §1h language-neutral forbidden zone): " + ", ".join(offenders)
    )


def _task_state(store: Store):
    state = store.state("RUN")
    state.current_task_id = "T-1"
    state.current_task_metadata = {"task_id": "T-1", "test_refs": list(_ANCHOR_REFS)}
    return state


def _fake_run(captured: list, returncode: int, stdout: str):
    """Intercept only the engine's test command; git helpers keep the real run
    (the real callable is bound before the monkeypatch replaces the module attr)."""

    real_run = subprocess.run

    def _run(argv, *args, **kwargs):
        if list(argv)[:1] == ["git"]:
            return real_run(argv, *args, **kwargs)
        captured.append(list(argv))
        return subprocess.CompletedProcess(list(argv), returncode, stdout=stdout, stderr="")

    return _run


def _assert_contract_expansion(captured: list, repo: Path) -> None:
    assert captured, "the engine must execute the task's declared test refs itself"
    argv = captured[0]
    # The contract template spells ``--junitxml={result}`` — one argv token
    # (``--junitxml=<path>``), never a bare flag with a separate value.
    junit = [a for a in argv if a.startswith("--junitxml=")]
    assert junit, (
        f"engine argv is not a contract run_selected expansion: {argv}"
    )
    result_path = junit[0].split("=", 1)[1]
    template = load_contract(Path(repo)).integration.run_selected
    assert audit(template, list(_ANCHOR_REFS), result_path, argv, Path(repo)), (
        "engine argv must equal the contract [integration].run_selected "
        "expansion for the declared refs"
    )


# AC-NFR0140-01@v0.7 TRACKS-TRACE anchor-red argv is contract-derived
def test_do_anchor_red_executes_contract_run_selected_expansion(tmp_path, monkeypatch):
    import tracks.executor.m_impl_runtime as mir

    repo = _engine_repo(tmp_path)
    store = Store(paths.tracks_home(repo))
    executor = Executor(store, repo, "RUN")
    captured: list = []
    monkeypatch.setattr(mir.subprocess, "run", _fake_run(captured, 1, "1 failed\n"))
    executor._do_anchor_red(
        Command(kind="run_task_gates", params={}, command_id="C-AR"),
        _task_state(store),
        "T-1",
        False,
    )
    _assert_contract_expansion(captured, repo)


# AC-NFR0140-01@v0.7 TRACKS-TRACE verification argv is contract-derived
def test_do_verify_task_executes_contract_run_selected_expansion(tmp_path, monkeypatch):
    import tracks.executor.m_impl_runtime as mir

    repo = _engine_repo(tmp_path)
    store = Store(paths.tracks_home(repo))
    executor = Executor(store, repo, "RUN")
    captured: list = []
    monkeypatch.setattr(mir.subprocess, "run", _fake_run(captured, 0, "1 passed\n"))
    executor._do_verify_task(
        Command(kind="run_task_gates", params={}, command_id="C-VT"),
        _task_state(store),
        "T-1",
        False,
    )
    _assert_contract_expansion(captured, repo)
