"""Regression: an operator-resolvable attention must never deadlock the run.

Live run bd0c68eb: ``trac run`` held ``.tracks/runtime/lock`` while the run
sat on stale M-VERIFY attentions (freeze / host_contract / local_gate /
ci_binding / run_breaker). ``trac retry`` could not acquire the lock, and a
re-drive after the repair commit kept re-running the old evidence under it.
"""

from __future__ import annotations

import threading
import time

from tests.unit.helpers import git, git_repo
from tracks.cli import run_cmd
from tracks.cli.common import WriterLock
from tracks.cli.gate_cmd import cmd_retry
from tracks.cli.release_cmd import _release_block_reason, _release_status_lines
from tracks.executor.drive import DriveConfig, drive_once
from tracks.executor.executor import Executor
from tracks.server.projections_gate import _blocked
from tracks.store import Store

_RUN = "bd0c68eb37b7539780e90f0b1dd737fd"
_VERSION = "v0.12"
_OLD = "9ac12cee8b5382135dd6594e582dfb1e3189b5e3"
_NEW = "1195aec4cb2bf2871f2f2cdc79c96c9956bfcb8b"


def _seed(store, stages=("M-VERIFY",)):
    store.append(_RUN, _VERSION, "story.requested", {"raw_chars": 1})
    for stage in stages:
        store.append(_RUN, _VERSION, "stage.entered", {"stage": stage})


def _verify_attention(store, area, reason, candidate=None, stage="M-VERIFY"):
    payload = {"area": area, "reason": reason, "stage": stage}
    if candidate is not None:
        payload["candidate_sha"] = candidate
    store.append(_RUN, _VERSION, "attention.required", payload)


def _setup(tmp_path):
    repo = git_repo(tmp_path)
    store = Store(repo / ".tracks")
    return repo, store


def test_retry_clears_active_verify_attention_without_escalation(tmp_path, capsys):
    """The M-VERIFY block is not a formal escalation; retry is still its
    documented recovery verb (live: "run not awaiting escalation")."""
    repo, store = _setup(tmp_path)
    _seed(store)
    _verify_attention(store, "host_contract", "gate_failed")
    assert store.state(_RUN).status == "active"
    assert store.state(_RUN).awaiting is None

    rc = cmd_retry(repo, "--actor", "Maestro")

    events = list(store.events(_RUN))
    store.close()
    assert rc == 0, capsys.readouterr().err
    assert [e.type for e in events].count("human.retry") == 1


def test_driver_wait_releases_the_writer_lock_for_retry(tmp_path, capsys):
    """A driver suspended in a bounded wait yields the runtime lock; the
    operator retry lands and the stale wait is reported as interrupted."""
    repo, store = _setup(tmp_path)
    _seed(store)
    _verify_attention(store, "ci_readback", "missing_token")
    lock = WriterLock(repo / ".tracks")

    executor = Executor.__new__(Executor)
    executor.run_id = _RUN
    executor._wait_yield = lock.suspended  # noqa: SLF001 - the CLI seam

    lock.acquire()
    result: dict[str, bool] = {}

    def _driver_wait():
        executor.store = Store(repo / ".tracks")
        try:
            result["interrupted"] = executor._sleep_wait(2.0)  # noqa: SLF001
        finally:
            executor.store.close()

    worker = threading.Thread(target=_driver_wait)
    worker.start()
    try:
        for _ in range(200):
            if not lock.path.exists():
                break
            time.sleep(0.01)
        assert not lock.path.exists(), "the driver must release the lock while waiting"
        assert cmd_retry(repo, "--actor", "Maestro") == 0
    finally:
        worker.join(timeout=10)
        lock.release()
    assert not worker.is_alive()
    assert result["interrupted"] is True
    assert any(e.type == "human.retry" for e in store.events(_RUN))
    store.close()


def test_cmd_run_returns_immediately_on_operator_attention(tmp_path, capsys):
    """A current unresolved verify block releases the lock instead of
    re-running the unchanged chain (the operator retry then succeeds)."""
    repo, store = _setup(tmp_path)
    _seed(store)
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    store.append(
        _RUN,
        _VERSION,
        "candidate.frozen",
        {"candidate_sha": head, "clean_tree": True},
    )
    _verify_attention(store, "host_contract", "gate_failed")

    assert run_cmd.cmd_run(repo, "--no-watch") == 0
    err = capsys.readouterr().err
    assert "operator attention host_contract:gate_failed" in err
    assert not [
        e
        for e in store.events(_RUN)
        if e.type == "command.issued"
    ], "the blocked re-drive must not run the chain under the lock"
    assert not (repo / ".tracks" / "runtime" / "lock").exists()

    assert cmd_retry(repo, "--actor", "Maestro") == 0
    store.close()


def test_repair_candidate_supersedes_stale_verify_attentions(tmp_path):
    """A repair commit's fresh freeze clears the previous candidate's
    freeze/host_contract/local_gate/ci_binding blocks; human.retry clears the
    breaker block. ``blocked`` and status re-evaluate instead of pinning."""
    repo, store = _setup(tmp_path)
    _seed(store)
    store.append(
        _RUN, _VERSION, "candidate.frozen", {"candidate_sha": _OLD, "clean_tree": True}
    )
    _verify_attention(store, "freeze", "dirty_tree")
    store.append(
        _RUN,
        _VERSION,
        "local_gate.failed",
        {"candidate_sha": _OLD, "gate_identity": "quality[0]", "kind": "quality"},
    )
    _verify_attention(store, "host_contract", "gate_failed")
    _verify_attention(store, "ci_readback", "ci_binding_blocked", candidate=_OLD)
    _verify_attention(store, "run_breaker", "rollbacks>=2")
    store.append(_RUN, _VERSION, "human.retry", {"actor": "Maestro"})
    store.append(
        _RUN, _VERSION, "candidate.frozen", {"candidate_sha": _NEW, "clean_tree": True}
    )
    events = list(store.events(_RUN))
    state = store.state(_RUN)
    store.close()

    assert _blocked(events, state) is None
    lines = _release_status_lines(
        events, state
    )
    assert not any(line.startswith("needs_attention=") for line in lines)
    assert _release_block_reason(events) != "local_gate_failed"


def test_supervisor_drive_reports_attention_as_human_boundary(tmp_path):
    """drive_once maps an unresolved attention to await_human, not to the
    "halted without progress" runtime failure (supervisor autonomy)."""
    repo, store = _setup(tmp_path)
    _seed(store)
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    store.append(
        _RUN,
        _VERSION,
        "candidate.frozen",
        {"candidate_sha": head, "clean_tree": True},
    )
    _verify_attention(store, "host_contract", "gate_failed")
    store.close()

    result = drive_once(repo, _RUN, config=DriveConfig())

    assert result.kind == "await_human"
    assert "host_contract:gate_failed" in result.detail


def test_same_candidate_gate_pass_supersedes_its_failure(tmp_path):
    """A re-run gate that passes clears its own earlier failure (no stale
    ``blocked: local_gate_failed`` after the operator re-drives)."""
    repo, store = _setup(tmp_path)
    _seed(store)
    store.append(
        _RUN,
        _VERSION,
        "local_gate.failed",
        {"candidate_sha": _NEW, "gate_identity": "quality[0]", "kind": "quality"},
    )
    store.append(
        _RUN,
        _VERSION,
        "local_gate.passed",
        {"candidate_sha": _NEW, "gate_identity": "quality[0]", "kind": "quality"},
    )
    events = list(store.events(_RUN))
    store.close()

    assert _release_block_reason(events) != "local_gate_failed"


def _seed_gate_evidence_missing(repo, store, *, pending=False):
    """Live bd0c68eb shape: a frozen repair candidate with only the OLD
    candidate's gate evidence, optionally leaving the long FULL judgment
    link in the WAL (a killed driver's pending command)."""
    _seed(store)
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    store.append(
        _RUN,
        _VERSION,
        "local_gate.passed",
        {"candidate_sha": _OLD, "gate_identity": "quality[0]", "kind": "quality"},
    )
    store.append(
        _RUN,
        _VERSION,
        "candidate.frozen",
        {"candidate_sha": head, "clean_tree": True},
    )
    if pending:
        store.append(
            _RUN,
            _VERSION,
            "command.issued",
            {
                "command": {
                    "kind": "judge_full_f_reuse",
                    "params": {"candidate_sha": head},
                    "command_id": "C-FULL-PENDING",
                }
            },
            command_id="C-FULL-PENDING",
        )
    return head


def test_stale_full_link_parks_with_attention_and_releases_the_lock(tmp_path, capsys):
    """A pending long verify link + missing gate evidence must not be
    replayed under the writer lock: return an operator attention immediately
    (live bd0c68eb held the lock for 90+ minutes in the FULL rerun)."""
    repo, store = _setup(tmp_path)
    _seed_gate_evidence_missing(repo, store, pending=True)
    assert store.state(_RUN).pending["kind"] == "judge_full_f_reuse"
    before = [e.type for e in store.events(_RUN)].count("command.issued")

    started = time.monotonic()
    rc = run_cmd.cmd_run(repo, "--resume", "--no-watch")
    elapsed = time.monotonic() - started

    err = capsys.readouterr().err
    events = list(store.events(_RUN))
    assert rc == 0
    assert elapsed < 10, f"re-drive must return promptly, took {elapsed:.1f}s"
    assert "stale verify link judge_full_f_reuse" in err
    assert "no gate evidence" in err
    assert not (repo / ".tracks" / "runtime" / "lock").exists()
    assert [e.type for e in events].count("command.issued") == before
    attention = [e for e in events if e.type == "attention.required"][-1]
    assert attention.payload["area"] == "local_gate"
    assert attention.payload["reason"] == "gate_evidence_missing"
    assert attention.payload["candidate_sha"] == store.state(_RUN).candidate_sha

    assert cmd_retry(repo, "--actor", "Maestro") == 0
    store.close()


def test_retry_accepts_stale_gate_evidence_without_an_attention(tmp_path, capsys):
    """``blocked: gate_evidence_missing`` is a status projection, not an
    attention event; retry is still its documented recovery verb."""
    repo, store = _setup(tmp_path)
    _seed_gate_evidence_missing(repo, store)
    events = list(store.events(_RUN))
    store.close()
    assert _release_block_reason(events) == "gate_evidence_missing"

    assert cmd_retry(repo, "--actor", "Maestro") == 0, capsys.readouterr().err

    store = Store(repo / ".tracks")
    assert [e.type for e in store.events(_RUN)].count("human.retry") == 1
    store.close()


def test_redrive_reruns_missing_gates_for_the_frozen_candidate(tmp_path, capsys):
    """With no stale WAL link, the re-drive re-runs the missing local gates
    and records candidate-bound evidence (never the long FULL head)."""
    repo, store = _setup(tmp_path)
    head = _seed_gate_evidence_missing(repo, store)

    started = time.monotonic()
    rc = run_cmd.cmd_run(repo, "--resume", "--no-watch")
    elapsed = time.monotonic() - started

    events = list(store.events(_RUN))
    assert rc == 0, capsys.readouterr().err
    assert elapsed < 30, f"gate recovery must be prompt, took {elapsed:.1f}s"
    assert not (repo / ".tracks" / "runtime" / "lock").exists()
    passed = [
        e
        for e in events
        if e.type == "local_gate.passed"
        and e.payload.get("candidate_sha") == head
    ]
    assert passed, "the missing local gates must record evidence for the current candidate"
    issued = [
        e.payload["command"]["kind"]
        for e in events
        if e.type == "command.issued"
    ]
    assert "judge_full_f_reuse" not in issued
    assert "run_local_gates" in issued
    store.close()
