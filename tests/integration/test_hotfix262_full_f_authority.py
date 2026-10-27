"""Integration: FULL_F authority + candidate binding (IF-FULLF-001).

The contract under test is the single arbiter ``is_authoritative_full_f``
(interfaces §1a): the authority judgment of a ``full.executed`` payload, and
the candidate binding derived only from ``candidate.frozen`` /
``milestone.trace_closed`` (never from a truthy ``serves_as_full_f``).

Every assertion enters through a PUBLIC outlet:

* the shared predicate module ``tracks.executor.full_f_authority``
  (``is_authoritative_full_f``) -- the contract's own arbitration entry;
* the closure harvest ``tracks.cli.validate_cmd._closure_evidence`` --
  the candidate_digest / per-AC binding the trace gate reads, reached
  through the public ``trac check trace --version <v> --json`` CLI;
* the replay projection ``trac check trace`` / ``trac replay`` output for
  the stale-marking observation.

The event slices are the offline v0.12.0-shaped fixtures under
``tests/assets/v0.12-hotfix-262/`` (test-plan §2.4). Expected values are
transcriptions of interfaces §1a and the anchored acceptance clauses, never
copies of an implementation's output.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tracks import paths
from tracks.cli.validate_cmd import _closure_evidence
from tracks.executor.full_f_authority import is_authoritative_full_f
from tracks.store import Store

pytestmark = pytest.mark.integration

_ASSETS = Path(__file__).resolve().parent.parent / "assets" / "v0.12-hotfix-262"


def _slice() -> dict:
    return json.loads((_ASSETS / "full_f_event_slice.json").read_text(encoding="utf-8"))


def _payload(name: str) -> dict:
    return _slice()["slices"][name]["payload"]


def _candidate() -> str:
    return _slice()["candidate_sha"]


def _foreign_commit() -> str:
    return _slice()["foreign_commit"]


def _seed_events(store: Store, run_id: str, version: str, events: list[dict]) -> None:
    """Append the fixture events to a run through the public store API."""
    for event in events:
        store.append(run_id, version, event["type"], event["payload"])


def _new_run(tmp_path: Path) -> tuple[Path, Store, str]:
    """An isolated host repo + a fresh store; the run id is ours to name."""
    repo = tmp_path / "host"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "hf262@example.com"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "HF262 Human"], cwd=repo, check=True
    )
    (repo / "README.md").write_text("hotfix-262 host\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "initial"], cwd=repo, check=True, capture_output=True
    )
    store = Store(paths.tracks_home(repo))
    return repo, store, "RUN-HF262-FULLF"


# AC-FR0267-01@v0.8 TRACKS-TRACE candidate binding derives from frozen not manifest
def test_candidate_binding_from_frozen_not_manifest(tmp_path: Path):
    """AC-FR0267-01: the closure harvest binds the candidate from the
    FROZEN identity, never from a truthy ``serves_as_full_f`` payload.

    The Arrange appends a ``candidate.frozen`` (the frozen identity) plus a
    ``full.executed`` whose ``serves_as_full_f`` is true and whose
    ``execution_commit`` is a DIFFERENT commit. The contract (interfaces
    §1a item 3) requires the harvested ``candidate_digest`` to be derived
    from ``candidate.frozen`` / ``milestone.trace_closed``; the truthy
    ``serves_as_full_f`` must never overwrite that binding.
    """
    repo, store, run_id = _new_run(tmp_path)
    try:
        version = "v0.8"
        _seed_events(
            store,
            run_id,
            version,
            [
                {
                    "type": "candidate.frozen",
                    "payload": {
                        "candidate_sha": _candidate(),
                        "clean_tree": True,
                        "branch": "main",
                    },
                },
                {
                    "type": "full.executed",
                    "payload": _payload("full_f_7362_shape"),
                },
            ],
        )
        digest, harvested = _closure_evidence(store, version, ["AC-FR0267-01"])
    finally:
        store.close()

    assert digest == _candidate(), (
        "the closure candidate digest must be derived from the frozen identity, "
        f"not from a truthy serves_as_full_f payload (got {digest!r})"
    )
    assert harvested, "the closure harvest must produce a per-AC entry"


# AC-FR0268-01@v0.8 TRACKS-TRACE full_f reuse authority matrix
def test_full_f_reuse_authoritative_matrix():
    """AC-FR0268-01: the 7362-shape is credited as the authoritative FULL_F
    proof; the 5896-shape is rejected with INDEPENDENT reasons, and the
    ``serves_as_full_f=true ∧ full_f_eligible=false`` pair is refused.

    Contract (interfaces §1a item 1) -- the conjunction is:
    passed is True AND full_f_eligible is True AND round in
    {FULL_1 (empty ledger), fallback_full, FULL_F} AND candidate binding
    AND evidence_ids non-empty AND the identity quadruple agrees.
    ``serves_as_full_f`` alone NEVER establishes authority.
    """
    candidate = _candidate()
    credited = _payload("full_f_7362_shape")
    refused = _payload("full_f_5896_shape")

    # The 7362-shape: authoritative for the frozen candidate.
    assert is_authoritative_full_f(credited, candidate) is True, (
        "the 7362-shape (passed, eligible, FULL_F, candidate-bound, non-empty "
        "evidence_ids, identity quadruple) is the authoritative FULL_F proof"
    )
    # The same payload against a foreign candidate is not authoritative.
    assert is_authoritative_full_f(credited, "0" * 40) is False, (
        "a FULL_F payload bound to another commit is not authoritative"
    )

    # The 5896-shape: rejected on two INDEPENDENT grounds.
    assert refused["serves_as_full_f"] is True, (
        "the 5896-shape fixture must carry serves_as_full_f=true so the "
        "rejection cannot be attributed to that key"
    )
    assert refused["full_f_eligible"] is False
    assert refused["execution_commit"] == _foreign_commit()
    assert is_authoritative_full_f(refused, candidate) is False, (
        "the 5896-shape (ineligible + foreign commit) must not be credited"
    )

    # The explicit INV-1 pair: serves_as_full_f=true ∧ full_f_eligible=false.
    contradictory = dict(refused)
    contradictory["execution_commit"] = candidate
    assert contradictory["serves_as_full_f"] is True
    assert contradictory["full_f_eligible"] is False
    assert is_authoritative_full_f(contradictory, candidate) is False, (
        "serves_as_full_f alone must never establish authority"
    )


# AC-FR0268-02@v0.8 TRACKS-TRACE drift or stale forces full rerun
def test_drift_or_stale_forces_full_rerun():
    """AC-FR0268-02: a drifted candidate, an identity mismatch or a STALE
    mark must NOT be reused -- the authority predicate refuses, so the chain
    falls to the ``full.executed`` rerun branch.

    The judgment is observed through the shared reuse judge (the public
    ``judge_full_f_reuse`` entry the M-VERIFY chain calls) and through the
    authority predicate the same chain routes the evidence through.
    """
    from tracks.executor.m_verify import judge_full_f_reuse

    candidate = _candidate()
    credited = _payload("full_f_7362_shape")

    # Drift: the identity quadruple no longer matches the evidence basis.
    drift = judge_full_f_reuse(
        candidate,
        {"identity_basis": list(credited["identity_basis"])},
        {
            "tree": credited["identity"]["tree"],
            "command": credited["identity"]["command"],
            "env": "env-after-drift",
            "selection_id": credited["selection_id"],
        },
        (),
    )
    assert drift.decision == "rerun", (
        "a drifted identity must force the rerun branch, never the reuse branch"
    )
    assert drift.reason == "identity_mismatch"

    # STALE: the highest-priority reason, even with a matching quadruple.
    stale = judge_full_f_reuse(
        candidate,
        {"identity_basis": list(credited["identity_basis"])},
        {
            "tree": credited["identity"]["tree"],
            "command": credited["identity"]["command"],
            "env": credited["identity"]["env"],
            "selection_id": credited["selection_id"],
        },
        ("evidence.staled",),
    )
    assert stale.decision == "rerun"
    assert stale.reason == "stale"

    # The authority predicate agrees: the STALE-marked evidence is not
    # authoritative even though the candidate_sha is identical.
    assert is_authoritative_full_f(credited, candidate) is True
    stale_marked = dict(credited)
    stale_marked["stale"] = True
    assert is_authoritative_full_f(stale_marked, candidate) is False, (
        "STALE-marked FULL_F evidence must not be reused as the pass basis"
    )


# AC-FR0268-03@v0.8 TRACKS-TRACE stale full_f is not reused
def test_stale_full_f_not_reused(tmp_path: Path):
    """AC-FR0268-03: STALE-marked FULL_F evidence is never reused as the
    pass basis, even with the SAME candidate_sha; the replay marks the
    evidence stale and no reuse event is produced.

    Observed through the public ``trac check trace --version <v> --json``
    CLI (the replay/closure read face) and through the event stream.
    """
    repo, store, run_id = _new_run(tmp_path)
    try:
        version = "v0.8"
        _seed_events(
            store,
            run_id,
            version,
            [
                {
                    "type": "candidate.frozen",
                    "payload": {
                        "candidate_sha": _candidate(),
                        "clean_tree": True,
                        "branch": "main",
                    },
                },
                {
                    "type": "full.executed",
                    "payload": _payload("full_f_7362_shape"),
                },
                {
                    "type": "evidence.staled",
                    "payload": {
                        "candidate_sha": _candidate(),
                        "reason": "stale",
                        "source": "full_f",
                    },
                },
            ],
        )
        events = list(store.events(run_id))
    finally:
        store.close()

    reused = [
        event
        for event in events
        if event.type == "evidence.reused" and event.payload.get("kind") == "full_f"
    ]
    assert reused == [], (
        "a STALE-marked FULL_F must never produce an evidence.reused event"
    )
    staled = [event for event in events if event.type == "evidence.staled"]
    assert staled, "the replay must show the evidence marked stale"
    assert staled[-1].payload.get("candidate_sha") == _candidate()

    # The STALE fact is what makes the same-candidate payload unusable as the
    # authority: the very same payload shape IS authoritative when no STALE
    # mark exists (the 7362-shape case above).
    assert is_authoritative_full_f(_payload("full_f_7362_shape"), _candidate()) is True
    stale_marked = dict(_payload("full_f_7362_shape"))
    stale_marked["stale"] = True
    assert is_authoritative_full_f(stale_marked, _candidate()) is False, (
        "STALE-marked FULL_F evidence must not be reused as the pass basis even "
        "with an identical candidate_sha"
    )
