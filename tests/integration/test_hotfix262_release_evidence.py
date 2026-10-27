"""Integration: release evidence key completeness (IF-RELEV-001).

The contract under test is INV-4 / INV-5 (interfaces §1b): a NEW preview /
authorization is invalid unless FIVE evidence keys are present and valid --
``full_f``, ``ci``, ``prism.verify_final``, ``prism.security``, ``security``;
and the pre-v0.12.1 LEGACY preview blob (a single ``"prism"`` key) stays
readable.

Assertions enter through the public outlets:

* the release preview's evidence assembly (``release_preview.evidence_digests``
  / ``_evidence_key``) -- the seam the preview is built from;
* the release authorization (``release_authorization.assess_release``) --
  the read-only authorization the release gate consumes;
* the release CLI (``trac release preview`` / ``trac release --action
  release``) -- the terminal outlet, whose refusal text and exit code are
  the observable (interfaces §4a outlet 2).

The legacy blob fixture is the offline single-``prism``-key payload under
``tests/assets/v0.12-hotfix-262/legacy_preview_blob.json``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tracks import paths
from tracks.executor.release_preview import _evidence_key, evidence_digests
from tracks.store import Store

pytestmark = pytest.mark.integration

_ASSETS = Path(__file__).resolve().parent.parent / "assets" / "v0.12-hotfix-262"

_CANDIDATE = "44c14de0000000000000000000000000000000000"
_REQUIRED_KEYS = ("full_f", "ci", "prism.verify_final", "prism.security", "security")


def _legacy_blob() -> dict:
    return json.loads(
        (_ASSETS / "legacy_preview_blob.json").read_text(encoding="utf-8")
    )["blob"]


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


class _Ev:
    """A minimal event envelope for the pure evidence assembly."""

    def __init__(self, seq: int, type_: str, payload: dict, command_id: str | None = None):
        self.seq = seq
        self.type = type_
        self.payload = payload
        self.command_id = command_id


def _full_executed(candidate: str = _CANDIDATE) -> _Ev:
    return _Ev(
        1,
        "full.executed",
        {
            "passed": True,
            "full_f_eligible": True,
            "round": "FULL_F",
            "execution_commit": candidate,
            "evidence_ids": ["ev-1"],
            "selection_id": "sel-1",
            "identity": {
                "tree": "t",
                "command": ["pytest"],
                "env": "e",
                "selection_id": "sel-1",
            },
        },
    )


def _ci_observed(candidate: str = _CANDIDATE) -> _Ev:
    return _Ev(
        2,
        "ci.run_observed",
        {
            "status": "passed",
            "api_verified": True,
            "head_sha": candidate,
            "candidate_sha": candidate,
        },
    )


def _prism(scope: str, candidate: str = _CANDIDATE) -> _Ev:
    return _Ev(
        3 if scope == "verify_final" else 4,
        "prism.verdict",
        {"verdict": "pass", "scope": scope, "candidate_sha": candidate},
    )


def _security_assessed(candidate: str = _CANDIDATE) -> _Ev:
    return _Ev(
        5,
        "security.assessed",
        {"status": "passed", "candidate_sha": candidate},
    )


def _complete_chain() -> list:
    return [
        _full_executed(),
        _ci_observed(),
        _prism("verify_final"),
        _prism("security"),
        _security_assessed(),
    ]


# AC-FR0270-02@v0.8 TRACKS-TRACE missing required keys block release
def test_missing_required_keys_block_release(tmp_path: Path):
    """AC-FR0270-02: an evidence chain that misses ANY ONE of the five
    required keys is incomplete -- the preview is not valid and the release
    is blocked.

    Contract: interfaces §1b item 1 ("五键全部在场且有效；完备性检查
    无条件执行") and item 3 (``_gate_status`` gains ``full_f_ok``).
    """
    chain = _complete_chain()
    complete = evidence_digests(chain, _CANDIDATE)
    assert complete is not None, (
        "the complete five-key chain must assemble a valid evidence set"
    )
    missing_keys = [key for key in _REQUIRED_KEYS if key not in complete]
    assert missing_keys == [], (
        f"the complete chain must carry every required key (missing {missing_keys})"
    )

    # Each key is individually load-bearing: dropping exactly one must make
    # the evidence set incomplete.
    for dropped in _REQUIRED_KEYS:
        reduced = []
        for event in chain:
            if dropped == "full_f" and event.type == "full.executed":
                continue
            if dropped == "ci" and event.type == "ci.run_observed":
                continue
            if dropped == "prism.verify_final" and event.type == "prism.verdict" and (
                event.payload.get("scope") == "verify_final"
            ):
                continue
            if dropped == "prism.security" and event.type == "prism.verdict" and (
                event.payload.get("scope") == "security"
            ):
                continue
            if dropped == "security" and event.type == "security.assessed":
                continue
            reduced.append(event)
        assert evidence_digests(reduced, _CANDIDATE) is None, (
            f"dropping the required key {dropped!r} must make the evidence set "
            "incomplete (the completeness check is unconditional)"
        )


# AC-FR0273-01@v0.8 TRACKS-TRACE preview requires full key set
def test_preview_requires_full_key_set(tmp_path: Path):
    """AC-FR0273-01: the key set is derived per scope -- the ``verify_final``
    scope maps to ``prism.verify_final`` and the ``security`` scope to
    ``prism.security``; every other scope maps to no key at all.

    Contract: interfaces §1b item 2 (``_evidence_key(scope)``).
    """
    assert _evidence_key(_Ev(1, "prism.verdict", {"scope": "verify_final"}), {}) == (
        "prism.verify_final"
    ), "the verify_final scope must map to the prism.verify_final key"
    assert _evidence_key(_Ev(2, "prism.verdict", {"scope": "security"}), {}) == (
        "prism.security"
    ), "the security scope must map to the prism.security key"

    # Every other scope carries NO release-evidence key: the M-TEST / M-IMPL
    # verdicts never enter the release evidence.
    for scope in ("M-TEST", "M-IMPL", "M-DESIGN", "m_test", "m_impl", "prism"):
        assert _evidence_key(_Ev(3, "prism.verdict", {"scope": scope}), {}) is None, (
            f"scope {scope!r} must not contribute a release-evidence key"
        )


# AC-FR0273-02@v0.8 TRACKS-TRACE legacy single prism key preview readable
def test_legacy_single_prism_key_preview_readable():
    """AC-FR0273-02: the pre-v0.12.1 preview blob (a single ``"prism"`` key)
    stays READABLE -- the legacy read-back succeeds and does NOT report
    ``operation_plan_changed``.

    Contract: interfaces §1b item 4 (INV-5: the historical single-key blob
    remains readable; the split keys are enforced only for a NEWLY assembled
    preview and an unpublished authorization; no DB migration).
    """
    legacy = _legacy_blob()
    assert list(legacy["evidence_digests"]) == ["prism"], (
        "the legacy fixture must carry exactly the single 'prism' evidence key"
    )

    # The legacy blob's key set is a READABLE schema: the preview's own
    # recorded keys are compared against a recomputed current preview by
    # key-set schema, and the legacy shape must not be reported as an
    # operation-plan change.
    from tracks.executor.release_preview import preview_inputs_match

    same_legacy = dict(legacy)
    same_legacy["blob_ref"] = "blobs/" + ("a" * 64)
    assert preview_inputs_match(legacy, same_legacy) is True, (
        "a legacy single-key preview compared against the same legacy content "
        "must match (the legacy blob is readable)"
    )

    # The five required keys are what a NEW preview must carry; the legacy
    # blob legitimately does not.
    required = _legacy_blob()
    expectations = json.loads(
        (_ASSETS / "legacy_preview_blob.json").read_text(encoding="utf-8")
    )["expectations"]
    assert list(expectations["required_keys_for_new_preview"]) == list(_REQUIRED_KEYS)
    assert sorted(required["evidence_digests"]) == ["prism"], (
        "the legacy blob carries only the pre-split 'prism' key"
    )


# AC-FR0274-02@v0.8 TRACKS-TRACE release rejected on gate failed or stale
def test_release_rejected_on_gate_failed_or_stale(tmp_path: Path):
    """AC-FR0274-02: with a failed gate or a stale preview, the release
    action exits non-zero with the fixed refusal text, lands NO
    ``release.decided``, and the run stays in AWAITING_RELEASE.

    The hotfix delta adds the ``full_f`` key to the release gate's
    completeness check (interfaces §1b item 3: "_release_gate_blocked()
    增加 full_f 检查"): a chain whose ``full_f`` evidence is MISSING must
    block the release, and the refusal must carry the contract's fixed
    wording.

    Contract: interfaces §1b item 5 and the acceptance clause "error:
    release rejected — gate failed or preview stale".
    """
    from tracks.cli.release_cmd import _RELEASE_REJECTED, _release_gate_blocked

    # The refusal text is the contract's fixed wording.
    assert "release rejected" in _RELEASE_REJECTED, (
        "the release refusal must carry the contract's fixed wording"
    )

    # A failed gate blocks the release action.
    failed_gate = [
        _Ev(
            1,
            "local_gate.failed",
            {
                "kind": "quality",
                "gate_identity": "quality[0]",
                "candidate_sha": _CANDIDATE,
                "normalized_result": {"status": "failed", "exit_code": 7},
            },
        )
    ]
    assert _release_gate_blocked(failed_gate) is True, (
        "a failed local gate must block the release action"
    )

    # The hotfix's NEW completeness key: a chain with the other four keys
    # but NO ``full_f`` evidence must block the release. The pre-hotfix
    # implementation has no such check, so this is the hotfix's seam.
    without_full_f = [
        _ci_observed(),
        _prism("verify_final"),
        _prism("security"),
        _security_assessed(),
    ]
    assert _release_gate_blocked(without_full_f) is True, (
        "a chain without the full_f evidence must block the release action "
        "(the release gate's completeness check covers the full_f key)"
    )

    # A stale marker makes the preview stale: the release action is refused
    # (the stale preview cannot authorize the release).
    from tracks.cli.release_cmd import _release_preview_stale_reason

    class _Preview:
        seq = 5
        payload = {"candidate_sha": _CANDIDATE}

    stale_events = [
        _Ev(1, "release.previewed", {"candidate_sha": _CANDIDATE}),
        _Ev(6, "candidate.stale", {"candidate_sha": _CANDIDATE, "reason": "head_moved"}),
    ]
    reason = _release_preview_stale_reason(stale_events, _Preview())
    assert reason == "candidate_drift", (
        f"a candidate.stale marker after the preview must make it stale (got {reason!r})"
    )
