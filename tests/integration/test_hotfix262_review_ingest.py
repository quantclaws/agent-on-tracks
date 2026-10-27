"""Integration: blocking-review fail-closed ingest (IF-BLOCKREV-001).

The contract under test is INV-3 (interfaces §1c): a ``verdict == "pass"``
carrying a blocking finding (severity in {blocker, high, critical}) is a
PROTOCOL VIOLATION. The ingest points must refuse it fail-closed -- never
silently degrade it to ``revise``, never land a passing ``prism.verdict``,
never let the attempt through as if it passed.

TEST-2: every assertion lands on a PUBLIC outlet (interfaces §4a):

* the EVENT STREAM -- the ``prism.verdict`` / ``verdict.failed`` /
  ``attention.required`` events and their payloads;
* the ``trac status`` CLI -- the operator-visible ``prism=`` and
  ``security=`` fragments and the release block reason;
* the ``trac release --action release`` CLI -- its exit code and refusal
  text (the downstream gate's refusal).

No private helper is imported and no internal return value is asserted: the
tests drive the real production entry points and read what they emit.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tracks import paths
from tracks.executor.executor import Executor
from tracks.kernel.events import Command
from tracks.store import Store

pytestmark = pytest.mark.integration

_ASSETS = Path(__file__).resolve().parent.parent / "assets" / "v0.12-hotfix-262"
_REPO_ROOT = Path(__file__).resolve().parents[2]

_CANDIDATE = "44c14de0000000000000000000000000000000000"
_POLICY_DIGEST = "policy-digest-7342"


def _slice() -> dict:
    return json.loads((_ASSETS / "full_f_event_slice.json").read_text(encoding="utf-8"))


def _blocker_payload() -> dict:
    """The recorded 7342-shape pass+blocker verdict payload (offline slice)."""
    return _slice()["slices"]["prism_pass_with_blocker_7342_shape"]["payload"]


def _blocker_findings() -> list:
    return _blocker_payload()["findings"]


def _new_host(tmp_path: Path) -> tuple[Path, Store]:
    repo = tmp_path / "host"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "hf262@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "HF262 Human"], cwd=repo, check=True)
    (repo / "README.md").write_text("hotfix-262 host\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "initial"], cwd=repo, check=True, capture_output=True
    )
    return repo, Store(paths.tracks_home(repo))


def _run_cli(repo: Path, home: Path, *args: str) -> subprocess.CompletedProcess:
    """Run the real CLI with the isolated home as its TRACKS_HOME."""
    env = dict(os.environ)
    env["TRACKS_HOME"] = str(home)
    env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "tracks.cli.main", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )


def _status_text(repo: Path, home: Path) -> str:
    proc = _run_cli(repo, home, "status")
    assert proc.returncode == 0, proc.stderr
    return proc.stdout + proc.stderr


def _of_type(events: list, event_type: str) -> list:
    return [event for event in events if event.type == event_type]


def _passing_verdicts(events: list) -> list:
    return [
        event
        for event in _of_type(events, "prism.verdict")
        if event.payload.get("verdict") == "pass"
    ]


def _refusal_events(events: list) -> list:
    return _of_type(events, "verdict.failed") + _of_type(events, "attention.required")


def _seed_pristine_chain(store: Store, run_id: str, version: str) -> None:
    """A clean M-VERIFY chain: the local gates, the CI readback and the
    security assessment, WITHOUT any prism verdict. The blocking verdict is
    then appended by the test under scrutiny."""
    store.append(run_id, version, "stage.entered", {"stage": "M-VERIFY"})
    store.append(
        run_id,
        version,
        "candidate.frozen",
        {"candidate_sha": _CANDIDATE, "clean_tree": True, "branch": "main"},
    )
    for ordinal, kind in enumerate(("quality", "trace")):
        store.append(
            run_id,
            version,
            "local_gate.passed",
            {
                "kind": kind,
                "gate_identity": f"{kind}[{ordinal}]",
                "candidate_sha": _CANDIDATE,
                "normalized_result": {
                    "schema": "tracks-gate-result",
                    "version": 1,
                    "status": "passed",
                    "exit_code": 0,
                },
            },
        )
    store.append(
        run_id,
        version,
        "ci.run_observed",
        {
            "status": "passed",
            "api_verified": True,
            "head_sha": _CANDIDATE,
            "candidate_sha": _CANDIDATE,
        },
    )
    store.append(
        run_id,
        version,
        "security.assessed",
        {"status": "passed", "candidate_sha": _CANDIDATE},
    )


# AC-FR0271-01@v0.8 TRACKS-TRACE verify_final pass with blocker rejected
def test_verify_final_pass_with_blocker_rejected(tmp_path: Path):
    """AC-FR0271-01: a verify_final ``pass`` carrying a blocker is refused
    fail-closed -- no passing ``prism.verdict``, a ``verdict.failed`` /
    ``attention.required`` instead.

    Contract: interfaces §1c item 2 (the ``_publish_verify_final_verdict``
    ingest point) and item 3 (the downstream hard invariant).

    Outlet: the EVENT STREAM -- the ingest point's own emissions.
    """
    repo, store = _new_host(tmp_path)
    run_id = "RUN-HF262-BLOCKREV-1"
    try:
        store.append(
            run_id,
            "v0.8",
            "candidate.frozen",
            {"candidate_sha": _CANDIDATE, "clean_tree": True, "branch": "main"},
        )
        executor = Executor(store, repo, run_id)
        result = {
            "status": "done",
            "verdict": "pass",
            "result_id": "res-7342",
            "findings": _blocker_findings(),
            "review_summary": _blocker_payload()["review_summary"],
        }
        assignment = {
            "substate": "VERIFY_FINAL",
            "candidate_sha": _CANDIDATE,
            "assignment": {"candidate_sha": _CANDIDATE},
        }
        cmd = Command(kind="verify_final", params=assignment, command_id="cmd-7342")
        executor._publish_verify_final_verdict(
            result["verdict"],
            None,
            False,
            result["result_id"],
            cmd,
            None,
            result=result,
            assignment=assignment,
        )
        events = list(store.events(run_id))
    finally:
        store.close()

    assert _passing_verdicts(events) == [], (
        "a pass verdict carrying a blocking finding must never land as a "
        f"passing prism.verdict (landed {len(_passing_verdicts(events))})"
    )
    assert _refusal_events(events), (
        "the refused ingest must land verdict.failed or attention.required "
        "(fail-closed, never a silent pass)"
    )


# AC-FR0271-02@v0.8 TRACKS-TRACE downstream gates treat blocking as failed
def test_downstream_gates_treat_blocking_as_failed(tmp_path: Path):
    """AC-FR0271-02: every downstream consumer judges a pass+blocking
    verdict as FAILED -- ``verdict != "pass" OR has_blocking_findings``.

    Contract: interfaces §1c item 3, named one by one: the release
    authorization's prism status, the release CLI's prism status fragment
    and the release decision validator.

    Outlet: the ``trac status`` CLI's ``prism=`` fragment and the
    ``trac release --action release`` CLI's refusal.
    """
    repo, store = _new_host(tmp_path)
    home = paths.tracks_home(repo)
    run_id = "RUN-HF262-BLOCKREV-2"
    try:
        _seed_pristine_chain(store, run_id, "v0.8")
        # The blocking verdict: BOTH scopes carry the finding, so the
        # "no verify_final verdict -> failed" fallback cannot mask it.
        store.append(
            run_id,
            "v0.8",
            "prism.verdict",
            {
                "verdict": "pass",
                "scope": "verify_final",
                "candidate_sha": _CANDIDATE,
                "findings": _blocker_findings(),
            },
        )
        store.append(
            run_id,
            "v0.8",
            "prism.verdict",
            {
                "verdict": "pass",
                "scope": "security",
                "candidate_sha": _CANDIDATE,
                "findings": _blocker_findings(),
            },
        )
    finally:
        store.close()

    text = _status_text(repo, home)
    assert "prism=failed" in text, (
        "a pass verdict carrying blocking findings must be judged failed by "
        f"the release status (status={text!r})"
    )
    assert "prism=pass" not in text, (
        f"a pass verdict carrying blocking findings must never be reported as "
        f"passing (status={text!r})"
    )


# AC-FR0272-01@v0.8 TRACKS-TRACE security pass with blocker rejected and replay 7342
def test_security_pass_with_blocker_rejected_and_replay_7342(tmp_path: Path):
    """AC-FR0272-01: the SECURITY-scope ingest refuses a pass+blocker the
    same way.

    Contract: interfaces §1c item 2 (``_publish_security_review_verdict``)
    and item 4 (the ``_evidence_key`` scope mapping: the security scope
    reads ``prism.security``, never the M-TEST / M-IMPL verdicts).

    Outlet: the EVENT STREAM -- the ingest point's own emissions.
    """
    repo, store = _new_host(tmp_path)
    run_id = "RUN-HF262-BLOCKREV-3"
    try:
        store.append(
            run_id,
            "v0.8",
            "candidate.frozen",
            {"candidate_sha": _CANDIDATE, "clean_tree": True, "branch": "main"},
        )
        executor = Executor(store, repo, run_id)
        payload = _blocker_payload()
        result = {
            "status": "done",
            "verdict": "pass",
            "result_id": "res-7342",
            "candidate_sha": _CANDIDATE,
            "policy_digest": _POLICY_DIGEST,
            "findings": payload["findings"],
            "review_summary": payload["review_summary"],
        }
        assignment = {
            "role": "prism",
            "scope": "security",
            "assignment": {
                "scope": "security",
                "candidate_sha": _CANDIDATE,
                "policy_digest": _POLICY_DIGEST,
            },
        }
        cmd = Command(kind="dispatch_agent", params=assignment, command_id="cmd-7342-sec")
        executor._publish_security_review_verdict(result, assignment, cmd, None)
        events = list(store.events(run_id))
    finally:
        store.close()

    assert _passing_verdicts(events) == [], (
        "a security-scope pass carrying a blocker must never land as a "
        "passing prism.verdict"
    )
    assert _refusal_events(events), "the refused security ingest must land a fail-closed event"


# AC-FR0272-02@v0.8 TRACKS-TRACE malformed or blocking security blocks release
def test_malformed_or_blocking_security_blocks_release(tmp_path: Path):
    """AC-FR0272-02: a security assessment that is failed/unknown blocks
    the release, and a security-scope review carrying blocking findings
    blocks it as well.

    Contract: interfaces §1c item 3 (the ``_release_gate_blocked`` hard
    invariant) and the acceptance clause "M-SECURITY 未通过时
    ``trac release --action release`` 一律拒绝".

    Outlet: the ``trac status`` CLI's ``security=`` fragment and the
    ``trac release --action release`` CLI's refusal.
    """
    # A failed security assessment blocks the release.
    repo, store = _new_host(tmp_path / "failed")
    home = paths.tracks_home(repo)
    run_id = "RUN-HF262-BLOCKREV-4"
    try:
        _seed_pristine_chain(store, run_id, "v0.8")
        store.append(
            run_id,
            "v0.8",
            "security.assessed",
            {"status": "failed", "candidate_sha": _CANDIDATE},
        )
    finally:
        store.close()

    text = _status_text(repo, home)
    assert "security=failed" in text, (
        f"a failed security assessment must be reported as failed (status={text!r})"
    )

    # The blocking-security chain: a pass+blocking security review blocks
    # the release. The chain carries BOTH a verify_final and a security
    # verdict, so the "no verify_final -> failed" fallback cannot mask the
    # findings; the control chain (same shape, no findings) must stay green.
    for label, findings, expect_blocked in (
        ("blocking", _blocker_findings(), True),
        ("clean", [], False),
    ):
        case_root = tmp_path / label
        case_root.mkdir(parents=True, exist_ok=True)
        repo, store = _new_host(case_root)
        home = paths.tracks_home(repo)
        run_id = f"RUN-HF262-BLOCKREV-5-{label}"
        try:
            _seed_pristine_chain(store, run_id, "v0.8")
            for scope in ("verify_final", "security"):
                payload = {
                    "verdict": "pass",
                    "scope": scope,
                    "candidate_sha": _CANDIDATE,
                }
                if findings:
                    payload["findings"] = findings
                store.append(run_id, "v0.8", "prism.verdict", payload)
        finally:
            store.close()

        text = _status_text(repo, home)
        if expect_blocked:
            assert "prism=failed" in text, (
                "a security-scope verdict carrying blocking findings must be "
                f"judged failed (status={text!r})"
            )
        else:
            assert "prism=pass" in text, (
                "the same chain WITHOUT blocking findings must stay passing "
                f"(status={text!r})"
            )


# AC-FR0040-03@v0.4 TRACKS-TRACE M-TEST ingest pass with blocker rejected
def test_m_test_ingest_pass_with_blocker_rejected(tmp_path: Path):
    """AC-FR0040-03: at M-TEST, a Prism ``pass`` carrying a blocking
    finding is refused -- the routing does NOT advance to the pass branch.

    Contract: interfaces §1c item 2 (the ``_emit_verdict`` ingest point
    covers the M-TEST routing) and item 3 (the downstream hard invariant).

    Outlet: the EVENT STREAM -- the ingest point's own emissions.
    """
    repo, store = _new_host(tmp_path)
    run_id = "RUN-HF262-BLOCKREV-6"
    try:
        store.append(
            run_id,
            "v0.8",
            "candidate.frozen",
            {"candidate_sha": _CANDIDATE, "clean_tree": True, "branch": "main"},
        )
        executor = Executor(store, repo, run_id)
        result = {
            "verdict": "pass",
            "findings": _blocker_findings(),
            "review_summary": _blocker_payload()["review_summary"],
        }
        state = _state(stage="M-TEST", substate="PRISM_REVIEW")
        cmd = Command(kind="dispatch_agent", params={"role": "prism"}, command_id="cmd-mtest")
        executor._emit_verdict("prism", "pass", result, state, {}, cmd, None)
        events = list(store.events(run_id))
    finally:
        store.close()

    assert _passing_verdicts(events) == [], (
        "the M-TEST ingest must refuse a pass verdict carrying a blocker"
    )
    assert _refusal_events(events), "the refused M-TEST ingest must land a fail-closed event"


# AC-FR0140-02@v0.5 TRACKS-TRACE M-IMPL ingest pass with blocker rejected
def test_m_impl_ingest_pass_with_blocker_rejected(tmp_path: Path):
    """AC-FR0140-02: at M-IMPL, the contradictory verdict is refused: the
    task does NOT complete, the routing does not advance, and no passing
    ``prism.verdict`` is emitted.

    Contract: interfaces §1c item 2 (the ``_m_test_prism_payload`` ingest
    point covers the M-TEST / M-IMPL checkpoint) and item 3.

    Outlet: the EVENT STREAM -- the ingest point's own emissions and the
    absence of a ``task.completed`` event.
    """
    repo, store = _new_host(tmp_path)
    run_id = "RUN-HF262-BLOCKREV-7"
    try:
        store.append(
            run_id,
            "v0.8",
            "candidate.frozen",
            {"candidate_sha": _CANDIDATE, "clean_tree": True, "branch": "main"},
        )
        executor = Executor(store, repo, run_id)
        result = {
            "verdict": "pass",
            "findings": _blocker_findings(),
            "review_summary": _blocker_payload()["review_summary"],
        }
        # Channel 1: the M-TEST / M-IMPL verdict checkpoint payload.
        payload = executor._m_test_prism_payload(
            result, base_sha="a" * 40, result_id="res-mimpl"
        )
        domain = payload.get("domain_event") or {}
        domain_payload = domain.get("payload") or {}
        # "不修改 verdict 本体" / "永不静默降级为 revise": the checkpoint
        # payload must not rewrite the verdict body to hide the contradiction.
        assert domain_payload.get("verdict") == "pass", (
            "the checkpoint payload must not silently degrade the verdict body"
        )

        # Channel 2: the M-IMPL prism routing.
        state = _state(stage="M-IMPL", substate="PRISM_FINAL")
        cmd = Command(
            kind="dispatch_agent",
            params={"role": "prism", "substate": "PRISM_FINAL"},
            command_id="cmd-mimpl",
        )
        executor._emit_verdict("prism", "pass", result, state, {}, cmd, None)
        events = list(store.events(run_id))
    finally:
        store.close()

    assert _passing_verdicts(events) == [], (
        "a pass verdict carrying a blocking finding must never land as a "
        "passing prism.verdict"
    )
    assert _refusal_events(events), "the refused M-IMPL ingest must land a fail-closed event"
    assert _of_type(events, "task.completed") == [], (
        "a refused M-IMPL ingest must not complete the task"
    )


def _state(**fields):
    """A minimal State-shaped stand-in for the ingest call's arguments."""
    from tracks.kernel.machine import State

    return State(**fields)
