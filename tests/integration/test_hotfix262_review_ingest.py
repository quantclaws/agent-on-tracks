"""Integration: blocking-review fail-closed ingest (IF-BLOCKREV-001).

The contract under test is INV-3 (interfaces §1c): a ``verdict == "pass"``
carrying a blocking finding (severity in {blocker, high, critical}) is a
PROTOCOL VIOLATION. The ingest points must refuse it fail-closed -- never
silently degrade it to ``revise``, never land a passing ``prism.verdict``,
never let the attempt through as if it passed.

Assertions enter through the public ingest entry points:

* the shared predicate ``tracks.executor.review_findings.has_blocking_findings``
  -- the contract's own severity arbiter;
* the production ingest handlers on the ``Executor`` (the public entry the
  runtime drives): ``_publish_verify_final_verdict`` (M-VERIFY verify_final),
  ``_publish_security_review_verdict`` (M-SECURITY security scope),
  ``_emit_verdict`` (the M-TEST / M-IMPL prism routing) and
  ``_m_test_prism_payload`` (the M-TEST / M-IMPL checkpoint payload).

The observation is the EVENT STREAM (the contract's §4a outlet): a rejected
ingest lands ``verdict.failed`` / ``attention.required`` and NO passing
``prism.verdict``; the downstream gates judge the same verdict as failed.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tracks import paths
from tracks.executor.executor import Executor
from tracks.executor.review_findings import has_blocking_findings
from tracks.kernel.events import Command
from tracks.store import Store

pytestmark = pytest.mark.integration

_ASSETS = Path(__file__).resolve().parent.parent / "assets" / "v0.12-hotfix-262"

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


def _executor(repo: Path, store: Store, run_id: str) -> Executor:
    """A production Executor over the isolated store (public ingest entry)."""
    return Executor(store, repo, run_id)


def _of_type(events: list, event_type: str) -> list:
    return [event for event in events if event.type == event_type]


def _blocking(findings) -> bool:
    """The shared severity arbiter, with the stub token as a legal Red.

    The interface stub raises ``NotImplementedError("IF-BLOCKREV-001")`` until
    the implementation lands; that contract token is converted into a plain
    assertion failure so the Red classifies as a behavioural one (never as a
    test-side crash).
    """
    try:
        return has_blocking_findings(findings)
    except NotImplementedError as err:
        raise AssertionError(
            "has_blocking_findings not implemented (IF-BLOCKREV-001): " f"{err}"
        ) from None


def _passing_verdicts(events: list) -> list:
    return [
        event
        for event in _of_type(events, "prism.verdict")
        if event.payload.get("verdict") == "pass"
    ]


def _refusal_events(events: list) -> list:
    return _of_type(events, "verdict.failed") + _of_type(events, "attention.required")


class _Ev:
    """A minimal event envelope for the pure downstream gates."""

    def __init__(self, seq: int, type_: str, payload: dict, command_id: str | None = None):
        self.seq = seq
        self.type = type_
        self.payload = payload
        self.command_id = command_id


# AC-FR0271-01@v0.8 TRACKS-TRACE verify_final pass with blocker rejected
def test_verify_final_pass_with_blocker_rejected(tmp_path: Path):
    """AC-FR0271-01: a verify_final ``pass`` carrying a blocker is refused
    fail-closed -- no passing ``prism.verdict``, a ``verdict.failed`` /
    ``attention.required`` instead.

    Contract: interfaces §1c item 2 (the ``_publish_verify_final_verdict``
    ingest point) and item 3 (the downstream hard invariant).
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
        executor = _executor(repo, store, run_id)
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
def test_downstream_gates_treat_blocking_as_failed():
    """AC-FR0271-02: every downstream consumer judges a pass+blocking
    verdict as FAILED -- ``verdict != "pass" OR has_blocking_findings``.

    Consumers under test (interfaces §1c item 3, named one by one):
    the release preview's evidence-success judge
    (``release_preview._evidence_is_success``), the release authorization's
    prism status (``release_authorization._prism_status``), the release
    CLI's prism status fragment (``release_cmd._prism_final_status``) and
    the shared release-decision validator
    (``release_gate.validate_release_decision``).
    """
    from tracks.cli.release_cmd import _prism_final_status
    from tracks.executor.release_authorization import _prism_status
    from tracks.executor.release_gate import validate_release_decision
    from tracks.executor.release_preview import _evidence_is_success

    findings = _blocker_findings()

    # The shared arbiter: a blocker severity IS blocking.
    assert _blocking(findings) is True, (
        "a finding with severity 'blocker' must be classified as blocking"
    )
    assert _blocking([{"severity": "info"}]) is False, (
        "a non-blocking severity must not be classified as blocking"
    )

    # Downstream consumer 1: the release preview's evidence-success judge.
    assert (
        _evidence_is_success(
            "prism.verdict",
            {
                "verdict": "pass",
                "scope": "verify_final",
                "candidate_sha": _CANDIDATE,
                "findings": findings,
            },
            _CANDIDATE,
        )
        is False
    ), "a pass verdict with blocking findings must not count as release-evidence success"

    events = [
        _Ev(
            1,
            "prism.verdict",
            {
                "verdict": "pass",
                "scope": "verify_final",
                "candidate_sha": _CANDIDATE,
                "findings": findings,
            },
        )
    ]

    # Downstream consumer 2: the release authorization's prism status.
    status, valid = _prism_status(events, _CANDIDATE)
    assert valid is False, (
        "the authorization's prism status must judge a pass+blocking verdict as "
        f"failed (got {status!r})"
    )
    assert status == "failed"

    # Downstream consumer 3: the release CLI's prism status fragment.
    assert _prism_final_status(events) == "failed", (
        "the release CLI's prism status must report failed for a pass+blocking "
        "verdict"
    )

    # Downstream consumer 4: the shared release-decision validator refuses
    # the release action on that status.
    allowed, reason = validate_release_decision(
        "release",
        {"preview_digest": "sha256:abc"},
        {"preview_stale": False, "gate_failed": True, "prism_status": "failed"},
    )
    assert allowed is False, (
        "the release decision must be refused when the prism gate is failed"
    )
    assert reason


# AC-FR0272-01@v0.8 TRACKS-TRACE security pass with blocker rejected and replay 7342
def test_security_pass_with_blocker_rejected_and_replay_7342(tmp_path: Path):
    """AC-FR0272-01: the SECURITY-scope ingest refuses a pass+blocker the
    same way.

    Contract: interfaces §1c item 2 (``_publish_security_review_verdict``)
    and item 4 (the ``_evidence_key`` scope mapping: the security scope
    reads ``prism.security``, never the M-TEST / M-IMPL verdicts).
    """
    repo, store = _new_host(tmp_path)
    run_id = "RUN-HF262-BLOCKREV-2"
    try:
        store.append(
            run_id,
            "v0.8",
            "candidate.frozen",
            {"candidate_sha": _CANDIDATE, "clean_tree": True, "branch": "main"},
        )
        executor = _executor(repo, store, run_id)
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
def test_malformed_or_blocking_security_blocks_release():
    """AC-FR0272-02: a security assessment that is failed/unknown blocks
    the release, and a security-scope review carrying blocking findings
    blocks it as well.

    Contract: interfaces §1c item 3 (the ``_release_gate_blocked`` hard
    invariant) and the acceptance clause "M-SECURITY 未通过时
    ``trac release --action release`` 一律拒绝".
    """
    from tracks.cli.release_cmd import _prism_final_status, _release_gate_blocked

    # A failed security assessment blocks the release.
    failed_security = [
        _Ev(
            1,
            "security.assessed",
            {"status": "failed", "candidate_sha": _CANDIDATE},
        )
    ]
    assert _release_gate_blocked(failed_security) is True, (
        "a failed security assessment must block the release action"
    )

    # An unknown (malformed) security assessment blocks the release too.
    unknown_security = [
        _Ev(
            1,
            "security.assessed",
            {"status": "unknown", "candidate_sha": _CANDIDATE},
        )
    ]
    assert _release_gate_blocked(unknown_security) is True, (
        "an unknown security assessment must block the release action"
    )

    # A pass+blocking security review blocks the release as well: the
    # downstream invariant is "verdict != pass OR has_blocking_findings".
    #
    # The chain below carries BOTH a verify_final and a security verdict, so
    # the "no verify_final verdict -> failed" fallback cannot mask the
    # blocking findings: the only thing that can turn this chain into
    # "failed" is the findings themselves. The control chain (the same
    # shape, no findings) must stay green -- the pair is what makes the
    # assertion discriminating.
    blocking_security = [
        _Ev(
            1,
            "prism.verdict",
            {
                "verdict": "pass",
                "scope": "verify_final",
                "candidate_sha": _CANDIDATE,
                "findings": _blocker_findings(),
            },
        ),
        _Ev(
            2,
            "prism.verdict",
            {
                "verdict": "pass",
                "scope": "security",
                "candidate_sha": _CANDIDATE,
                "findings": _blocker_findings(),
            },
        ),
        _Ev(
            3,
            "security.assessed",
            {
                "status": "passed",
                "candidate_sha": _CANDIDATE,
                "review_command_id": "cmd-7342-sec",
            },
        ),
    ]
    security_status = _prism_final_status(blocking_security)
    assert security_status == "failed", (
        "a pass verdict carrying blocking findings must be judged failed by "
        f"the release status (got {security_status!r})"
    )
    assert _release_gate_blocked(blocking_security) is True, (
        "a security assessment whose review carries blocking findings must "
        "block the release action"
    )

    # The control: the same chain WITHOUT blocking findings stays green --
    # the refusal above is caused by the findings, not by the chain shape.
    clean_security = [
        _Ev(
            1,
            "prism.verdict",
            {
                "verdict": "pass",
                "scope": "verify_final",
                "candidate_sha": _CANDIDATE,
            },
        ),
        _Ev(
            2,
            "prism.verdict",
            {
                "verdict": "pass",
                "scope": "security",
                "candidate_sha": _CANDIDATE,
            },
        ),
        _Ev(
            3,
            "security.assessed",
            {
                "status": "passed",
                "candidate_sha": _CANDIDATE,
                "review_command_id": "cmd-7342-sec",
            },
        ),
    ]
    assert _prism_final_status(clean_security) == "pass", (
        "the same chain without blocking findings must stay passing"
    )
    assert _release_gate_blocked(clean_security) is False, (
        "the same chain without blocking findings must not block the release"
    )


# AC-FR0040-03@v0.4 TRACKS-TRACE M-TEST ingest pass with blocker rejected
def test_m_test_ingest_pass_with_blocker_rejected(tmp_path: Path):
    """AC-FR0040-03: at M-TEST, a Prism ``pass`` carrying a blocking
    finding is refused -- the routing does NOT advance to the pass branch.

    Contract: interfaces §1c item 2 (the ``_emit_verdict`` ingest point
    covers the M-TEST routing) and item 3 (the downstream hard invariant).
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
        executor = _executor(repo, store, run_id)
        result = {
            "verdict": "pass",
            "findings": _blocker_findings(),
            "review_summary": _blocker_payload()["review_summary"],
        }
        state = SimpleNamespace(stage="M-TEST", substate="PRISM_REVIEW")
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

    Contract: interfaces §1c item 2 -- the ``_m_test_prism_payload`` ingest
    point (the M-TEST / M-IMPL verdict checkpoint) and ``_emit_verdict``;
    "永不静默降级为 ``revise``" (the verdict body is never modified) and
    "永不发射 passing ``prism.verdict``".
    """
    repo, store = _new_host(tmp_path)
    run_id = "RUN-HF262-BLOCKREV-4"
    try:
        store.append(
            run_id,
            "v0.8",
            "candidate.frozen",
            {"candidate_sha": _CANDIDATE, "clean_tree": True, "branch": "main"},
        )
        executor = _executor(repo, store, run_id)
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
        state = SimpleNamespace(stage="M-IMPL", substate="PRISM_FINAL")
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
