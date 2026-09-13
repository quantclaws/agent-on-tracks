"""D1: declared artifact glob resolution and real build/smoke execution.

- M-RELEASE preview resolves a declared ``[host-contract.build].artifact``
  glob (``dist/*.whl``) to exactly one file, binds its byte sha256 into
  ``artifact_digest`` and records ``artifact: {name,size,digest}``; zero or
  multiple matches block the preview with audited attention instead of
  landing a preview without an artifact.
- M-VERIFY executes the declared install -> build -> smoke chain for real
  (commands from the contract only), landing ``local_gate`` results plus an
  ``artifact.built`` event carrying the byte-verified identity; failures fail
  closed.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.integration.test_release_preview_direct import (
    _events,
    _issue_preview,
    _prepare,
    _previews,
)
from tests.unit.helpers import git_repo
from tracks.executor.executor import Executor
from tracks.kernel.events import Command
from tracks.store import Store

pytestmark = pytest.mark.integration


# -- M-RELEASE preview artifact resolution -----------------------------------


def _glob_setup(host_repo, trac, tmp_path):
    run_id, candidate, contract, _remote, _digest, _cd = _prepare(
        host_repo, trac, tmp_path
    )
    contract.write_text(
        contract.read_text(encoding="utf-8").replace(
            'artifact = "dist/package.whl"', 'artifact = "dist/*.whl"'
        ),
        encoding="utf-8",
    )
    return run_id, candidate


def _artifact_attention(events) -> list:
    return [
        e
        for e in events
        if e.type == "attention.required" and (e.payload or {}).get("area") == "artifact"
    ]


# AC-FR0273-01/AC-FR0269-01@v0.8: a glob artifact resolves to its bytes.
def test_preview_artifact_glob_binds_single_file_bytes(host_repo, trac, tmp_path):
    run_id, candidate = _glob_setup(host_repo, trac, tmp_path)
    wheel = host_repo / "dist" / "package.whl"
    _issue_preview(host_repo, run_id, candidate)

    previews = _previews(_events(host_repo, run_id))
    assert len(previews) == 1
    payload = previews[0].payload
    digest = "sha256:" + hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert payload["artifact_digest"] == digest
    assert payload["artifact"] == {
        "name": wheel.name,
        "size": wheel.stat().st_size,
        "digest": digest,
    }


# AC-FR0269-02@v0.8: zero/multiple artifact matches block the preview.
def test_preview_artifact_missing_or_ambiguous_blocks(host_repo, trac, tmp_path):
    run_id, candidate = _glob_setup(host_repo, trac, tmp_path)
    wheel = host_repo / "dist" / "package.whl"
    wheel.unlink()
    _issue_preview(host_repo, run_id, candidate)

    events = _events(host_repo, run_id)
    assert not _previews(events), "a missing artifact must never preview"
    attention = _artifact_attention(events)
    assert attention and attention[-1].payload["reason"] == "artifact_missing"

    wheel.write_bytes(b"accepted preview artifact\n")
    (host_repo / "dist" / "other.whl").write_bytes(b"other")
    _issue_preview(host_repo, run_id, candidate)

    events = _events(host_repo, run_id)
    assert not _previews(events), "an ambiguous artifact must never preview"
    attention = _artifact_attention(events)
    assert attention and attention[-1].payload["reason"] == "artifact_ambiguous"


# -- M-VERIFY install -> build -> smoke execution -----------------------------

_STEPS_SCRIPT = """\
import pathlib
import sys

mode = sys.argv[1]
if mode == "install":
    pathlib.Path("install-ran.txt").write_text("1", encoding="utf-8")
elif mode == "fail-install":
    raise SystemExit(5)
elif mode == "build":
    dist = pathlib.Path("dist")
    dist.mkdir(exist_ok=True)
    (dist / "demo-1.0-py3-none-any.whl").write_bytes(b"wheel-bytes")
elif mode == "fail-build":
    raise SystemExit(3)
elif mode == "smoke":
    target = pathlib.Path(sys.argv[2])
    if not target.is_file() or target.stat().st_size == 0:
        raise SystemExit(4)
elif mode == "fail-smoke":
    raise SystemExit(6)
"""

_CANDIDATE = "a" * 40


def _contract_toml(
    repo: Path, *, install: str, build: str, smoke: list[str]
) -> str:
    python = sys.executable
    default_install = f"{python} steps.py install"
    default_build = f"{python} steps.py build"
    default_smoke = [f"{python} steps.py smoke {{artifact}}"]
    return "\n".join(
        [
            "[host-contract]",
            "version = 1",
            'language = "python"',
            'toolchain = "cpython"',
            f"install = {json.dumps(install if install else default_install)}",
            "",
            "[host-contract.version_scheme]",
            'feature_tag = "v{minor}.0"',
            "",
            "[host-contract.build]",
            f"command = {json.dumps(build if build else default_build)}",
            'artifact = "dist/*.whl"',
            'result_channel = "exit_code"',
            "timeout_seconds = 60",
            "",
            "[host-contract.smoke]",
            f"steps = {json.dumps(smoke if smoke else default_smoke)}",
            'result_channel = "exit_code"',
            "timeout_seconds = 60",
            "",
            "[[host-contract.local_gate]]",
            'kind = "quality"',
            'source = "command"',
            f"command = {json.dumps(f'{python} steps.py gate')}",
            'result_channel = "exit_code"',
            "timeout_seconds = 60",
            "",
        ]
    )


def _build_host(tmp_path, *, install="", build="", smoke=None):
    repo = git_repo(tmp_path, gitignore=True)
    (repo / "steps.py").write_text(_STEPS_SCRIPT, encoding="utf-8")
    contract_path = repo / ".tracks" / "projects" / "project.toml"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(
        _contract_toml(repo, install=install, build=build, smoke=list(smoke or [])),
        encoding="utf-8",
    )
    store = Store(repo / ".tracks")
    executor = Executor(store, repo, "RUN")
    return repo, store, executor


def _run_gates(executor, *, resume=False):
    return executor._run_contract_gates(
        Command("run_local_gates", params={"candidate_sha": _CANDIDATE}, command_id="C-BUILD"),
        _CANDIDATE,
        SimpleNamespace(version="v0.8", hotfix_scenario=None),
        resume=resume,
    )


def _gate_events(store, identity):
    return [
        e
        for e in store.events("RUN")
        if e.type in ("local_gate.passed", "local_gate.failed")
        and (e.payload or {}).get("gate_identity") == identity
    ]


# AC-FR0269-01@v0.8: install/build/smoke run for real in contract order.
def test_verify_runs_declared_install_build_smoke(tmp_path):
    repo, store, executor = _build_host(tmp_path)

    contract, digest = _run_gates(executor)

    assert contract is not None and digest
    assert (repo / "install-ran.txt").is_file(), "install must really run"
    built = [e for e in store.events("RUN") if e.type == "artifact.built"]
    assert len(built) == 1
    payload = built[0].payload
    raw = (repo / "dist" / "demo-1.0-py3-none-any.whl").read_bytes()
    assert payload["candidate_sha"] == _CANDIDATE
    assert payload["artifact"] == "demo-1.0-py3-none-any.whl"
    assert payload["size"] == len(raw)
    assert payload["artifact_digest"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    assert payload["status"] == "passed"

    build_events = _gate_events(store, "build[0]")
    assert [e.payload["summary"]["phase"] for e in build_events] == ["install", "build"]
    assert all(e.type == "local_gate.passed" for e in build_events)
    smoke = _gate_events(store, "smoke[0]")
    assert smoke and smoke[-1].type == "local_gate.passed"

    # Resume: complete evidence is reused, no duplicate phase execution.
    before = len(list(store.events("RUN")))
    contract2, digest2 = _run_gates(executor, resume=True)
    assert (contract2, digest2) == (contract, digest)
    assert len(list(store.events("RUN"))) == before


# AC-FR0269-02@v0.8: a failing install stops before build/smoke/artifact.
def test_verify_install_failure_fails_closed(tmp_path):
    _repo, store, executor = _build_host(
        tmp_path, install=f"{sys.executable} steps.py fail-install"
    )

    contract, _digest = _run_gates(executor)

    assert contract is None
    build_events = _gate_events(store, "build[0]")
    assert len(build_events) == 1
    assert build_events[0].type == "local_gate.failed"
    assert build_events[0].payload["summary"]["phase"] == "install"
    assert not [e for e in store.events("RUN") if e.type == "artifact.built"]


# AC-FR0269-02@v0.8: a failing build stops before smoke/artifact.
def test_verify_build_failure_fails_closed(tmp_path):
    _repo, store, executor = _build_host(
        tmp_path, build=f"{sys.executable} steps.py fail-build"
    )

    contract, _digest = _run_gates(executor)

    assert contract is None
    build_events = _gate_events(store, "build[0]")
    assert build_events[-1].type == "local_gate.failed"
    assert build_events[-1].payload["summary"]["phase"] == "build"
    assert not [e for e in store.events("RUN") if e.type == "artifact.built"]
    assert any(
        e.type == "attention.required"
        and (e.payload or {}).get("area") == "host_contract"
        for e in store.events("RUN")
    )


# AC-FR0269-02@v0.8: a failing smoke step fails closed after the artifact.
def test_verify_smoke_failure_fails_closed(tmp_path):
    _repo, store, executor = _build_host(
        tmp_path, smoke=[f"{sys.executable} steps.py fail-smoke"]
    )

    contract, _digest = _run_gates(executor)

    assert contract is None
    smoke = _gate_events(store, "smoke[0]")
    assert smoke and smoke[-1].type == "local_gate.failed"
    assert len([e for e in store.events("RUN") if e.type == "artifact.built"]) == 1
