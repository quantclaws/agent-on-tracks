"""Shared Executor-harness fixtures for the M-TEST slice review-pin suites.

Not pytest-collected: the module name does not match ``test_*`` / ``*_test``
(see ``pyproject.toml`` ``testpaths``). Factors out the
``object.__new__(Executor)`` harness, contract stubs, subprocess shims and
evidence seeding helpers duplicated between
``test_mtest_gate_review_pins.py`` and
``test_mtest_slice_a_final_review_pins.py`` (pylint R0801).

Same seam as tests/unit/test_prism_review_payload.py: events are captured via
a patched ``_emit``, contracts/subprocess are stubbed per test.
"""

from __future__ import annotations

import importlib
import subprocess
from types import SimpleNamespace

from tracks.executor import executor as executor_module
from tracks.executor.executor import Executor
from tracks.store import Store

_RUN_ID = "RUN"


def _patch_load_contract(monkeypatch, fake):
    """Patch every executor module that resolved ``load_contract`` at import."""
    for name in (
        "executor",
        "doc_face",
        "run_loop",
        "test_collect",
        "test_execute",
        "phase0_face",
        "verify_gates",
    ):
        try:
            module = importlib.import_module(f"tracks.executor.{name}")
        except ModuleNotFoundError:
            continue
        if hasattr(module, "load_contract"):
            monkeypatch.setattr(module, "load_contract", fake)


class _State:
    stage = "M-TEST"
    substate = "RED_CHECK"
    current_attempt = 0
    red_validated = False
    hotfix_issue = None
    test_collected = False
    baseline_captured = False


def _cmd(cid):
    return SimpleNamespace(command_id=cid)


def _make_harness(monkeypatch, tmp_path, run_id=_RUN_ID, backend=None):
    ex_repo = tmp_path / "host"
    (ex_repo / "tests" / "integration").mkdir(parents=True, exist_ok=True)
    store = Store(ex_repo / ".tracks")
    emitted = []
    monkeypatch.setattr(
        Executor,
        "_emit",
        lambda self, ev, payload, **kw: emitted.append({"type": ev, "payload": payload}),
    )
    ex = object.__new__(Executor)
    ex.store = store
    ex.repo = ex_repo
    ex.run_id = run_id
    if backend is not None:
        ex.backend = backend
    return ex, store, emitted, ex_repo


def _section(collect="collect-placeholder", run_selected=".venv/bin/python -m pytest {nodes} --junitxml={result}"):
    return SimpleNamespace(
        framework="pytest",
        paths=["tests/"],
        collect=collect,
        run="run-placeholder",
        run_selected=run_selected,
        cwd=".",
    )


def _contract(integration, unit=None, e2e=None):
    return SimpleNamespace(
        unit=unit,
        e2e=e2e,
        integration=integration,
        layout=None,
        lint=None,
        nightly=None,
    )


def _fake_subprocess(monkeypatch, dispatch):
    """dispatch: callable(argv) -> CompletedProcess. git invocations pass
    through to the real runner (tree identity reads inside the handlers)."""
    real_run = subprocess.run

    def _run(argv, cwd=None, capture_output=True, text=True):
        argv = list(argv)
        if argv and argv[0] == "git":
            return real_run(argv, cwd=cwd, capture_output=capture_output, text=text)
        return dispatch(argv)

    monkeypatch.setattr(executor_module.subprocess, "run", _run)


def _proc(argv, rc, stdout="", stderr=""):
    return subprocess.CompletedProcess(list(argv), rc, stdout=stdout, stderr=stderr)


def _install_three_layer_contract(monkeypatch, *, unit_rc, integration_stdout, e2e_rc):
    """Stub load_contract + subprocess.run for the full-layer collect scans.

    rc=4 on the unit collect carries the pytest usage/path-error message (the
    fail-closed Review-1 signal); every other layer keeps plain stderr."""
    _patch_load_contract(
        monkeypatch,
        lambda repo: _contract(
            unit=_section(collect="_tracks_collect_unit"),
            integration=_section(collect="_tracks_collect_integration"),
            e2e=_section(collect="_tracks_collect_e2e"),
        ),
    )

    def _dispatch(argv):
        joined = " ".join(argv)
        if "_tracks_collect_unit" in joined:
            return _proc(argv, unit_rc, stderr="ERROR: usage/path error" if unit_rc == 4 else "")
        if "_tracks_collect_e2e" in joined:
            return _proc(argv, e2e_rc)
        if "_tracks_collect_integration" in joined:
            return _proc(argv, 0, stdout=integration_stdout)
        raise AssertionError(f"unexpected subprocess call: {argv}")

    _fake_subprocess(monkeypatch, _dispatch)


def _seed_baseline(store, entries, run_id=_RUN_ID):
    ref = store.write_audit_blob(entries)
    store.append(
        run_id,
        "v0.4",
        "test.baseline_captured",
        {
            "status": "passed",
            "baseline_id": "b0",
            "baseline_tree": "t0",
            "layers": ["unit", "integration", "e2e"],
            "nodes_count": len(entries),
            "empty_baseline": False,
            "node_digest_blob": f".tracks/runtime/blobs/{ref}",
            "errors": [],
        },
    )


def _seed_collected(store, entries, run_id=_RUN_ID):
    ref = store.write_audit_blob(entries)
    store.append(
        run_id,
        "v0.4",
        "test.collected",
        {
            "status": "passed",
            "collected_count": len(entries),
            "inherited_r1": sum(1 for e in entries if e["class"] == "r1"),
            "delta_r2": sum(1 for e in entries if e["class"] == "r2"),
            "removed": 0,
            "failures": [],
            "per_node_blob": f".tracks/runtime/blobs/{ref}",
            "errors": [],
        },
    )


def _commit_delta(repo, body, message="delta"):
    """Commit a single M-TEST delta test file; return (HEAD, delta path).

    The returned file path lets callers re-write the file afterwards to move
    its dirty-worktree tree stamp under an unchanged HEAD."""
    delta_file = repo / "tests" / "integration" / "test_delta.py"
    delta_file.write_text(f"def test_delta():\n    {body}\n", encoding="utf-8")

    def _git(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    _git("init", "-q")
    _git("-c", "user.email=t@tracks.dev", "-c", "user.name=t", "add", "tests/")
    _git(
        "-c",
        "user.email=t@tracks.dev",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "-m",
        message,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    return head, delta_file


def _of_type(emitted, ev_type):
    return [e for e in emitted if e["type"] == ev_type]
