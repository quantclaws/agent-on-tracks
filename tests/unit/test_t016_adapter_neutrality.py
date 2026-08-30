"""T-016 (replan x4) RED: executor language-neutrality non-regression.

GREEN 4842683 introduced a language-specific fallback in the executor's Phase 0
repair path -- ``resolve_adapter("reference-pytest", ...)`` in
``_repair_phase0_gap`` -- violating IF-ADAPTER-003 (forbidden zone: kernel/
executor/cli runtime code must carry no ``pytest|junit|java`` token) and
IF-ADAPTER-001 (an undeclared host adapter must fail closed, never fall back
to a built-in reference adapter).  The executor is T-016's final file owner, so
the fix consumes ONLY the host ``[adapter]`` declaration and fails closed on
missing/unknown/malformed.

These pins fail today: the hardcoded fallback keeps the forbidden token in
executor.py and keeps the Phase 0 repair path from failing closed on a missing
declaration.  Each assertion carries the IF-ADAPTER contract token.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit.helpers import git_repo
from tracks import paths
from tracks.adapters.base import UnknownAdapterError
from tracks.executor import executor as _executor_module
from tracks.executor.executor import Executor
from tracks.kernel.events import Command
from tracks.project import load_contract
from tracks.store import Store

_FORBIDDEN_TOKENS = ("pytest", "junit", "java")
_IF_TOKEN = "IF-ADAPTER-003/IF-ADAPTER-001"
_GAP_NODE = "tests/unit/test_x.py::test_x"


def test_executor_source_carries_no_language_token():
    """IF-ADAPTER-003: executor runtime source must carry no pytest|junit|java
    token (word-boundary, case-insensitive; comments/docstrings included).  The
    Phase 0 repair path must consume the host [adapter] declaration, never a
    hardcoded language-specific fallback."""
    source = Path(_executor_module.__file__).read_text(encoding="utf-8")
    found = [tok for tok in _FORBIDDEN_TOKENS if re.search(rf"\b{tok}\b", source, re.I)]
    assert not found, (
        f"{_IF_TOKEN}: forbidden language token(s) in executor.py: {found}"
    )


def _contract() -> str:
    """Loadable project contract WITHOUT an [adapter] declaration (an
    undeclared host contract must fail closed, IF-ADAPTER-001)."""
    return (
        "[unit]\nframework='pytest'\npaths=['tests/unit/']\n"
        "collect='.venv/bin/python -m pytest --collect-only -q tests/unit/'\n"
        "run='.venv/bin/python -m pytest tests/unit/ --tb=short -q -n 4 "
        "--dist loadscope --junitxml={result}'\n"
        "run_selected='.venv/bin/python -m pytest {nodes} --tb=short -q -n 4 "
        "--dist loadscope --junitxml={result}'\ncwd='.'\n\n"
        "[integration]\nframework='pytest'\npaths=['tests/integration/']\n"
        "collect='.venv/bin/python -m pytest --collect-only -q tests/integration/'\n"
        "run='.venv/bin/python -m pytest tests/integration/ --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\n"
        "run_selected='.venv/bin/python -m pytest {nodes} --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\ncwd='.'\n\n"
        "[e2e]\nframework='pytest'\npaths=['tests/e2e/']\n"
        "collect='.venv/bin/python -m pytest --collect-only -q tests/e2e/'\n"
        "run='.venv/bin/python -m pytest tests/e2e/ --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\n"
        "run_selected='.venv/bin/python -m pytest {nodes} --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\ncwd='.'\n\n"
        "[nightly]\nschedule='0 3 * * *'\nworkflow='.github/workflows/nightly.yml'\n"
        "job='nightly'\nlayers=['unit','integration','e2e']\npurpose='r'\n"
    )


def _host(tmp_path) -> Path:
    """Host repo with a loadable contract that declares NO [adapter]."""
    repo = git_repo(tmp_path, gitignore=True)
    contract = paths.project_toml_path(paths.tracks_home(repo))
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text(_contract(), encoding="utf-8")
    return repo


def _executor(repo: Path) -> Executor:
    load_contract(repo)
    store = Store(paths.tracks_home(repo))
    store.append("RUN", "v0.7", "story.requested", {"raw_chars": 1})
    store.append("RUN", "v0.7", "stage.entered", {"stage": "M-TEST"})
    return Executor(store, repo, "RUN")


def _marker_gap():
    return SimpleNamespace(
        ac="AC-FR0001-01",
        reason="marker_only",
        planned_node_id=_GAP_NODE,
    )


def test_phase0_repair_resolves_only_host_declared_adapter(tmp_path, monkeypatch):
    """IF-ADAPTER-001: when the host contract declares no [adapter], the Phase
    0 repair path must fail closed (UnknownAdapterError) BEFORE any adapter
    resolution -- it must never call resolve_adapter with a hardcoded fallback
    (no built-in reference adapter)."""
    repo = _host(tmp_path)
    executor = _executor(repo)
    calls: list = []

    def _record_and_fail(*args, **kwargs):
        calls.append(args)
        raise UnknownAdapterError(f"host declares no adapter: {args}")

    monkeypatch.setattr("tracks.adapters.base.resolve_adapter", _record_and_fail)

    with pytest.raises(UnknownAdapterError):
        executor._repair_phase0_gap(
            Command(kind="phase0_validate", command_id="C-P0"),
            _marker_gap(),
            "v0.6",
            {_GAP_NODE: "d" * 64},
        )
    assert not calls, (
        f"{_IF_TOKEN}: adapter resolution must not be attempted without a host "
        f"[adapter] declaration; hardcoded fallback resolved {calls}"
    )
