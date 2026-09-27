"""Artifact declaration version bindings (IF-VERSION-001, IF-GUARD-001,
IF-GUARD-002).

Drives the native ``version_decl`` local gate over a real host repo, bare
remote and declared host contract (the documented outlets: the
``local_gate.passed`` / ``local_gate.failed(kind=version,
reason=version_decl_mismatch)`` events naming the offending file/key), and
audits the host declaration + guard-registry digest anchoring.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest
import tomllib

from tests._support.host_contracts import TEST_EXECUTION_CONTRACT
from tests.e2e.helpers import init_bare_remote
from tracks.executor.executor import Executor
from tracks.executor.host_contract import load_host_contract
from tracks.kernel.events import Command
from tracks.store import Store

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CMD_ID = "CMD-VERSION-BINDING"


def _declare_binding_contract(repo: Path, *, bind_to: str = "0.8.0") -> Path:
    """Declare the host contract: feature plan + version_scheme bindings that
    bind ``pyproject.toml project.version`` and ``tracks.__version__`` to the
    journey's release version."""
    from tracks.executor.executor import _DEFAULT_HOST_CONTRACT_TOML

    contract = repo / ".tracks" / "projects" / "project.toml"
    contract.parent.mkdir(parents=True, exist_ok=True)
    text = (
        contract.read_text(encoding="utf-8")
        if contract.exists()
        else TEST_EXECUTION_CONTRACT
    )
    assert "[host-contract]" not in text
    declared = _DEFAULT_HOST_CONTRACT_TOML.replace(
        'steps = ["merge:main"]\n\n[host-contract.operations.post_release]',
        'steps = ["merge:main", "tag:{feature_tag}", "release:{feature_tag}"]\n\n'
        "[host-contract.operations.post_release]",
        1,
    )
    declared = declared.replace(
        "timeout_seconds = 60\n\n[[host-contract.security_scan]]",
        'timeout_seconds = 60\n\n[[host-contract.local_gate]]\nkind = "version"\n'
        'source = "version_decl"\nresult_channel = "exit_code"\ntimeout_seconds = 60\n'
        "\n[[host-contract.security_scan]]",
        1,
    )
    declared += (
        "\n[host-contract.version_scheme]\n"
        'feature_tag = "v{minor}.0"\n'
        'patch_line = "v{minor}.{n}"\n'
        'prerelease_tag = "v{minor}.{n}-pre.{ulid}"\n'
        "bindings = [\n"
        '  { file = "pyproject.toml", key = "project.version",'
        ' expect = "{release_version}" },\n'
        '  { file = "tracks/__init__.py", key = "__version__",'
        ' expect = "{release_version}" },\n'
        "]\n"
    )
    contract.write_text(text.rstrip("\n") + "\n\n" + declared, encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "fixture-host"\nversion = "{bind_to}"\n', encoding="utf-8"
    )
    (repo / "tracks").mkdir(exist_ok=True)
    (repo / "tracks" / "__init__.py").write_text(
        f'__version__ = "{bind_to}"\n', encoding="utf-8"
    )
    for path in (
        ".tracks/projects/project.toml",
        "pyproject.toml",
        "tracks/__init__.py",
    ):
        subprocess.run(
            ["git", "add", "-f", path], cwd=repo, check=True, capture_output=True
        )
    subprocess.run(
        ["git", "commit", "-qm", "declare version bindings and artifact versions"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return contract


def _run_version_gate(repo: Path, run_id: str, contract: Path):
    store = Store(repo / ".tracks")
    try:
        loaded = load_host_contract(contract)
        contract_digest = hashlib.sha256(contract.read_bytes()).hexdigest()
        executor = Executor(store, repo, run_id)
        command = Command(kind="run_local_gates", params={}, command_id=_CMD_ID)
        state = store.state(run_id)
        executor._execute_verify_gates(command, "a" * 40, contract_digest, loaded, state)
        return store
    finally:
        store.close()


def _gate_events(repo: Path, run_id: str) -> list[dict]:
    store = Store(repo / ".tracks")
    try:
        return [
            {"type": e.type, "payload": dict(e.payload or {})}
            for e in store.events(run_id)
            if e.command_id == _CMD_ID
        ]
    finally:
        store.close()


# AC-FR0328-01@v0.10 TRACKS-TRACE version bindings enforced with file/key verdict
def test_version_bindings_enforced(host_repo, trac):
    """AC-FR0328-01: when the declared version files match the journey's
    release version the version gate passes; any file/key mismatch fails the
    gate with ``version_decl_mismatch`` naming the offending file and key."""
    assert trac("init").returncode == 0
    started = trac("start", "v0.8", stdin="构建一个事件溯源运行时")
    assert started.returncode == 0, started.stderr
    init_bare_remote(host_repo, "binding_remote.git")

    contract = _declare_binding_contract(host_repo)
    store = Store(host_repo / ".tracks")
    try:
        run_id = store.active_run()
        assert run_id
        store.append(run_id, "v0.8", "stage.entered", {"stage": "M-VERIFY"})
        candidate = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=host_repo, check=True, capture_output=True, text=True,
        ).stdout.strip()
        store.append(
            run_id,
            "v0.8",
            "candidate.frozen",
            {
                "candidate_sha": candidate,
                "clean_tree": True,
                "branch": "main",
                "frozen_at_seq": 0,
            },
        )
    finally:
        store.close()

    # matching declarations: the gate passes
    _run_version_gate(host_repo, run_id, contract)
    passed = [e for e in _gate_events(host_repo, run_id) if e["type"] == "local_gate.passed"]
    assert any(e["payload"].get("kind") == "version" for e in passed), (
        f"a matching binding set must pass the version gate: {_gate_events(host_repo, run_id)!r}"
    )

    # one binding drifts: the gate fails closed and names the file/key
    (host_repo / "pyproject.toml").write_text(
        '[project]\nname = "fixture-host"\nversion = "0.9.0"\n', encoding="utf-8"
    )
    subprocess.run(
        ["git", "add", "-f", "pyproject.toml"],
        cwd=host_repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-qm", "drift pyproject version"],
        cwd=host_repo, check=True, capture_output=True,
    )
    _run_version_gate(host_repo, run_id, contract)
    failed = [e for e in _gate_events(host_repo, run_id) if e["type"] == "local_gate.failed"]
    version_failed = [e for e in failed if e["payload"].get("kind") == "version"]
    assert version_failed, (
        f"a mismatched binding must fail the version gate: {failed!r}"
    )
    assert any(
        e["payload"].get("reason") == "version_decl_mismatch" for e in version_failed
    ), f"the failure reason must be version_decl_mismatch: {version_failed!r}"
    detail = " ".join(
        str(e["payload"].get("detail") or "") for e in version_failed
    )
    assert "pyproject.toml" in detail and "project.version" in detail, (
        f"the failure must name the offending file and key: {detail!r}"
    )


# AC-FR0328-02@v0.10 TRACKS-TRACE contract bindings declared and registry anchored
def test_contract_bindings_declared_and_registry_anchored():
    """AC-FR0328-02: the host contract declares the file/key/expect bindings
    and the guard-registry config digests are anchored to the on-disk
    pyproject/project.toml bytes (zero drift after the v0.10 re-anchoring)."""
    from tracks.executor.guard_registry import load_guard_registry, validate_guard_registry

    project_toml = (_REPO_ROOT / ".tracks" / "projects" / "project.toml").read_text(
        encoding="utf-8"
    )
    table = tomllib.loads(project_toml)
    scheme = table["host-contract"]["version_scheme"]
    bindings = scheme.get("bindings")
    assert isinstance(bindings, list) and len(bindings) >= 2, (
        "the host contract must declare the version_scheme bindings"
    )
    for binding in bindings:
        assert set(binding) >= {"file", "key", "expect"}, (
            f"each binding carries file/key/expect: {binding!r}"
        )
        assert "{release_version}" in binding["expect"], (
            f"the expect template binds the release version: {binding!r}"
        )

    architecture = _REPO_ROOT / ".tracks" / "projects" / "v0.10" / "architecture.md"
    registry = load_guard_registry(architecture)
    errors = validate_guard_registry(registry, _REPO_ROOT)
    assert errors == (), f"guard registry must be anchored with zero drift: {errors!r}"
