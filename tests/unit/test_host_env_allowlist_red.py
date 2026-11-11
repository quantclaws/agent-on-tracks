"""IF-HOSTENV-001 RED: one process-boundary env allowlist (hotfix-262 T-003).

``host_process_env(extra=None, contract_allowlist=())`` is the single env
source for runtime-spawned host subprocesses (interfaces §1e, INV-8):

1. the base allowlist is an allowlist, not a denylist: cross-language
   toolchain variables and the two test-sizing knobs survive; every other
   parent variable (operator shell plumbing included) is dropped;
2. the strip set is closed: ``GITHUB_*`` / ``GH_*`` / ``TRAC_GITHUB_*`` /
   ``TRAC_SERVE_*`` / ``TRAC_AGENT_*`` / ``TRAC_FAKE_*`` / ``GIT_DIR`` /
   ``GIT_WORK_TREE`` / ``GIT_INDEX_FILE`` / ``*_TOKEN`` / ``*_SECRET`` /
   ``*_API_KEY`` never reach the child, even when an explicit overlay or the
   escape hatch names them;
3. the escape hatch admits host-declared non-secret variables through
   ``contract_allowlist`` and ``[host-contract].env_allowlist``;
4. ``extra`` is the explicit runtime overlay (hook root/bin, PYTHONPATH) and
   overrides base values without mutating the parent environment;
5. an existing subprocess site (the host commit hook) exposes the same
   allowlist behavior to its real child process.

Expected values are enumerated from interfaces §1e, never derived from the
implementation under test. Every injected value is a synthetic sentinel;
failure output carries key names, not environment values.

RED discipline: the shipped interface stub raises ``NotImplementedError``;
``call_host_process_env`` converts that into a real ``AssertionError`` so
the RED gate observes ``assertion_failure`` rather than a stub token or an
assembly error. Only unit tests are added/modified; production code is
untouched.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests.unit.host_env_allowlist_support import (
    ESCAPE_HATCH_CONTRACT,
    MINIMAL_CONTRACT,
    STRIP_SAMPLES,
    assert_env_equals,
    call_host_process_env,
    environment_keys,
    load_contract_allowlist,
    synthetic_base,
)
from tracks.executor.m_impl_dispatch import _run_host_commit_hook


# AC-NFR0143-02@v0.8 TRACKS-TRACE IF-HOSTENV-001 base toolchain allowlist
def test_base_allowlist_is_the_only_inherited_environment(monkeypatch):
    expected = synthetic_base(monkeypatch)
    monkeypatch.setenv("OPERATOR_ONLY_VAR", "synthetic::operator")
    monkeypatch.setenv("EDITOR", "synthetic::editor")

    assert_env_equals(call_host_process_env(), expected)


# AC-FR0315-01@v0.9 TRACKS-TRACE IF-HOSTENV-001 strip set closed
def test_strip_set_removes_operator_controls_and_secret_shapes(monkeypatch):
    expected = synthetic_base(monkeypatch)
    for name in STRIP_SAMPLES:
        monkeypatch.setenv(name, "synthetic::secret")

    assert_env_equals(call_host_process_env(), expected)


# AC-FR0315-01@v0.9 TRACKS-TRACE IF-HOSTENV-001 strip wins over overlay/hatch
def test_strip_set_wins_over_extra_and_contract_allowlist(monkeypatch):
    expected = synthetic_base(monkeypatch)
    for name in ("DEPLOY_TOKEN", "CUSTOM_SECRET", "EXTRA_API_KEY", "GITHUB_TOKEN"):
        monkeypatch.setenv(name, "synthetic::secret")

    result = call_host_process_env(
        extra={
            "GITHUB_TOKEN": "synthetic::overlay",
            "EXTRA_API_KEY": "synthetic::overlay",
            "TRACKS_HOOK_ROOT": "/repo",
        },
        contract_allowlist=(
            "DEPLOY_TOKEN",
            "CUSTOM_SECRET",
            "EXTRA_API_KEY",
            "GITHUB_TOKEN",
        ),
    )

    assert_env_equals(result, {**expected, "TRACKS_HOOK_ROOT": "/repo"})


# AC-NFR0143-02@v0.8 TRACKS-TRACE IF-HOSTENV-001 runtime overlay
def test_extra_overlays_explicit_runtime_variables(monkeypatch):
    expected = synthetic_base(monkeypatch)

    result = call_host_process_env(
        extra={
            "TRACKS_HOOK_ROOT": "/repo",
            "TRACKS_HOOK_BIN": "/repo/.venv/bin",
            "PYTHONPATH": "/worktree",
        }
    )

    assert_env_equals(
        result,
        {
            **expected,
            "TRACKS_HOOK_ROOT": "/repo",
            "TRACKS_HOOK_BIN": "/repo/.venv/bin",
            "PYTHONPATH": "/worktree",
        },
    )


# AC-FR0315-01@v0.9 TRACKS-TRACE IF-HOSTENV-001 escape hatch admits host vars
def test_contract_allowlist_admits_declared_host_variables(monkeypatch):
    expected = synthetic_base(monkeypatch)
    monkeypatch.setenv("HOST_TOOLCHAIN_BIN", "/opt/toolchain/bin")
    monkeypatch.setenv("UNDECLARED_HOST_HOME", "/opt/toolchain")

    result = call_host_process_env(
        contract_allowlist=("HOST_TOOLCHAIN_BIN", "DECLARED_BUT_ABSENT")
    )

    assert_env_equals(
        result, {**expected, "HOST_TOOLCHAIN_BIN": "/opt/toolchain/bin"}
    )


# AC-FR0315-01@v0.9 TRACKS-TRACE IF-HOSTENV-001 child env is a copy
def test_result_is_an_independent_mapping(monkeypatch):
    expected = synthetic_base(monkeypatch)

    result = call_host_process_env()
    result["PATH"] = "synthetic::mutated"

    assert os.environ["PATH"] == expected["PATH"]


# AC-FR0315-01@v0.9 TRACKS-TRACE IF-HOSTENV-001 project.toml escape hatch
def test_host_contract_parses_env_allowlist_escape_hatch(tmp_path: Path):
    path = tmp_path / "project.toml"
    path.write_text(ESCAPE_HATCH_CONTRACT, encoding="utf-8")

    assert load_contract_allowlist(path) == ("HOST_TOOLCHAIN_BIN", "BOARD_HOME")


# AC-FR0315-01@v0.9 TRACKS-TRACE IF-HOSTENV-001 escape hatch defaults closed
def test_host_contract_env_allowlist_defaults_to_empty(tmp_path: Path):
    path = tmp_path / "project.toml"
    path.write_text(MINIMAL_CONTRACT, encoding="utf-8")

    assert load_contract_allowlist(path) == ()


# AC-FR0315-01@v0.9 TRACKS-TRACE IF-HOSTENV-001 hook child allowlist
def test_host_commit_hook_child_sees_only_the_allowlist(tmp_path: Path, monkeypatch):
    main = tmp_path / "main"
    main.mkdir()
    gate = tmp_path / "gate"
    gate.mkdir()
    subprocess.run(["git", "-C", str(gate), "init", "-q"], check=True)
    hooks = gate / "host-hooks"
    hooks.mkdir()
    subprocess.run(
        ["git", "-C", str(gate), "config", "core.hooksPath", "host-hooks"],
        check=True,
    )
    observed = tmp_path / "hook-env-keys"
    hook = hooks / "pre-commit"
    hook.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys\n"
        f"pathlib.Path({str(observed)!r}).write_text(\"\\n\".join(os.environ))\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    for name in STRIP_SAMPLES:
        if name not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"}:
            monkeypatch.setenv(name, "synthetic::secret")
    # The site resolves its hook through git before spawning; point the git
    # internals at this fixture repo so resolution works while the child
    # observation still proves they are stripped.
    monkeypatch.setenv("GIT_DIR", str(gate / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(gate))
    monkeypatch.setenv("GIT_INDEX_FILE", str(gate / ".git" / "index"))
    monkeypatch.setenv("TRAC_INFRA_RETRY_LIMIT", "2")
    monkeypatch.setenv("OPERATOR_ONLY_VAR", "synthetic::operator")

    ok, output = _run_host_commit_hook(gate, main, ["src/changed.py"])

    assert ok is True, output
    keys = environment_keys(observed)
    assert set(STRIP_SAMPLES).isdisjoint(keys), sorted(set(STRIP_SAMPLES) & keys)
    assert "TRAC_INFRA_RETRY_LIMIT" not in keys
    assert "OPERATOR_ONLY_VAR" not in keys
    assert "PATH" in keys
    assert {"TRACKS_HOOK_ROOT", "TRACKS_HOOK_BIN"} <= keys
