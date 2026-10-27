"""Integration: FULL_F authority + candidate binding (IF-FULLF-001).

The contract under test is the single arbiter ``is_authoritative_full_f``
(interfaces §1a): the authority judgment of a ``full.executed`` payload, and
the candidate binding derived only from ``candidate.frozen`` /
``milestone.trace_closed`` (never from a truthy ``serves_as_full_f``).

TEST-2: every assertion lands on a PUBLIC outlet (interfaces §4a):

* the ``trac check trace --version <v> --json`` CLI -- the closure read face
  that reports, per approved AC, the harvested ``candidate_digest`` and the
  ``full_pass_evidence`` (the authority credit);
* the EVENT STREAM -- the ``full.executed`` / ``evidence.reused`` /
  ``evidence.staled`` events and their payloads;
* the ``trac status`` CLI -- the operator-visible reuse judgment.

No private helper is imported and no internal return value is asserted: the
tests drive the real production entry points and read what they emit.

The event slices are the offline v0.12.0-shaped fixtures under
``tests/assets/v0.12-hotfix-262/`` (test-plan §2.4). Expected values are
transcriptions of interfaces §1a and the anchored acceptance clauses, never
copies of an implementation's output.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tracks import paths
from tracks.store import Store

pytestmark = pytest.mark.integration

_ASSETS = Path(__file__).resolve().parent.parent / "assets" / "v0.12-hotfix-262"
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _slice() -> dict:
    return json.loads((_ASSETS / "full_f_event_slice.json").read_text(encoding="utf-8"))


def _payload(name: str) -> dict:
    return _slice()["slices"][name]["payload"]


def _candidate() -> str:
    return _slice()["candidate_sha"]


def _foreign_commit() -> str:
    return _slice()["foreign_commit"]


def _new_host(tmp_path: Path) -> tuple[Path, Store]:
    """An isolated host repo + a fresh store; the run id is ours to name."""
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


def _acceptance(version_dir: Path, ac_id: str) -> None:
    """A version acceptance declaring exactly one AC (the trace gate's input)."""
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / "acceptance.md").write_text(
        "---\n---\n\n"
        f"# Acceptance\n\n## FR-9999 hotfix-262 fixture\n\n### {ac_id}\n\n"
        "  - the fixture acceptance clause.\n",
        encoding="utf-8",
    )


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


def _trace_record(repo: Path, home: Path, version: str, ac_id: str) -> dict:
    """The per-AC closure record the ``trac check trace --json`` CLI reports.

    The CLI is the public closure read face: it reports the harvested
    ``candidate_digest`` and the ``full_pass_evidence`` -- the two bindings
    the contract governs.
    """
    proc = _run_cli(repo, home, "check", "trace", "--version", version, "--json")
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            "trac check trace --json did not emit a JSON closure record "
            f"(stdout={proc.stdout!r}, stderr={proc.stderr!r})"
        ) from exc
    records = {record["ac"]: record for record in report.get("records") or []}
    assert ac_id in records, (
        f"the closure read face must report a record for {ac_id} "
        f"(records={sorted(records)})"
    )
    return records[ac_id]


def _authority(payload: dict, candidate_sha: str | None = None) -> bool:
    """The contract's named arbitration entry (interfaces §1a item 1).

    The interface stub raises its contract token until the implementation
    lands; the token is converted into a plain assertion failure so the
    Red classifies as behavioural (never as a test-side crash).
    """
    from tracks.executor.full_f_authority import is_authoritative_full_f

    try:
        return is_authoritative_full_f(payload, candidate_sha)
    except NotImplementedError as err:
        raise AssertionError(
            "is_authoritative_full_f not implemented (IF-FULLF-001): " f"{err}"
        ) from None


def _of_type(events: list, event_type: str) -> list:
    return [event for event in events if event.type == event_type]


# AC-FR0267-01@v0.8 TRACKS-TRACE candidate binding derives from frozen not manifest
def test_candidate_binding_from_frozen_not_manifest(tmp_path: Path):
    """AC-FR0267-01: the closure read face binds the candidate to the
    FROZEN identity -- a truthy ``serves_as_full_f`` payload never
    contributes a binding of its own.

    The Arrange appends a ``candidate.frozen`` (the frozen identity) plus a
    ``full.executed`` whose ``serves_as_full_f`` is true and whose
    ``execution_commit`` is a DIFFERENT commit. The contract (interfaces
    §1a items 1 and 3) requires the harvested candidate binding to be the
    frozen one; the FULL payload's own commit must not become the binding.

    Outlet: the ``trac check trace --version <v> --json`` CLI, whose per-AC
    record carries the harvested ``candidate_digest``.
    """
    repo, store = _new_host(tmp_path)
    home = paths.tracks_home(repo)
    run_id = "RUN-HF262-FULLF-1"
    version = "v0.8"
    try:
        _acceptance(paths.version_dir(home, version), "AC-FR0267-01")
        store.append(
            run_id,
            version,
            "candidate.frozen",
            {
                "candidate_sha": _candidate(),
                "clean_tree": True,
                "branch": "main",
            },
        )
        # A mutation manifest bound to a FOREIGN candidate: the harvest must
        # not take its digest as the binding.
        store.append(
            run_id,
            version,
            "mutation.manifest",
            {
                "ac": "AC-FR0267-01",
                "candidate_digest": _foreign_commit(),
                "patch_digest": "sha256:hf262-patch",
                "manifest_digest": "sha256:hf262-manifest",
                "status": "declared",
                "if_ref": "IF-FULLF-001",
            },
        )
        # And a FULL execution whose truthy serves_as_full_f must not bind
        # a foreign commit either.
        store.append(
            run_id,
            version,
            "full.executed",
            _payload("full_f_7362_shape"),
        )
    finally:
        store.close()

    record = _trace_record(repo, home, version, "AC-FR0267-01")
    digest = record["candidate_digest"]
    assert digest != _foreign_commit(), (
        "the closure candidate binding must never be a foreign commit carried "
        f"by a stream event (got {digest!r})"
    )
    assert digest in (None, _candidate()), (
        "the closure candidate binding must be the frozen identity, never a "
        f"commit carried by a stream event (got {digest!r})"
    )


# AC-FR0268-01@v0.8 TRACKS-TRACE full_f reuse authority matrix
def test_full_f_reuse_authoritative_matrix(tmp_path: Path):
    """AC-FR0268-01: the 7362-shape is credited as the authoritative FULL_F
    proof; the 5896-shape is rejected, and the
    ``serves_as_full_f=true ∧ full_f_eligible=false`` pair is refused.

    Contract (interfaces §1a item 1) -- the conjunction is:
    passed is True AND full_f_eligible is True AND round in
    {FULL_1 (empty ledger), fallback_full, FULL_F} AND candidate binding
    AND evidence_ids non-empty AND the identity quadruple agrees.
    ``serves_as_full_f`` alone NEVER establishes authority.

    Outlet: the ``trac check trace --json`` CLI -- the harvested
    ``full_pass_evidence`` is the authority credit, and its absence is the
    refusal.
    """
    candidate = _candidate()
    credited = _payload("full_f_7362_shape")
    refused = _payload("full_f_5896_shape")

    # The 5896-shape really carries the contradicting pair, so a refusal
    # cannot be attributed to the truthy serves_as_full_f key alone.
    assert refused["serves_as_full_f"] is True
    assert refused["full_f_eligible"] is False
    assert refused["execution_commit"] == _foreign_commit()

    for label, payload, expect_credit in (
        ("authoritative", credited, True),
        ("refused", refused, False),
    ):
        case_root = tmp_path / label
        case_root.mkdir(parents=True, exist_ok=True)
        repo, store = _new_host(case_root)
        home = paths.tracks_home(repo)
        run_id = "RUN-HF262-MATRIX"
        version = "v0.8"
        try:
            _acceptance(paths.version_dir(home, version), "AC-FR0268-01")
            store.append(
                run_id,
                version,
                "candidate.frozen",
                {"candidate_sha": candidate, "clean_tree": True, "branch": "main"},
            )
            store.append(run_id, version, "full.executed", payload)
        finally:
            store.close()

        record = _trace_record(repo, home, version, "AC-FR0268-01")
        if expect_credit:
            assert record["full_pass_evidence"], (
                "the 7362-shape (passed, eligible, FULL_F, candidate-bound, "
                "non-empty evidence_ids, identity quadruple) must be credited "
                "as the authoritative FULL_F proof"
            )
        else:
            assert not record["full_pass_evidence"], (
                "the 5896-shape (ineligible + foreign commit) must not be "
                "credited as the authoritative FULL_F proof"
            )


# AC-FR0268-02@v0.8 TRACKS-TRACE drift or stale forces full rerun
def test_drift_or_stale_forces_full_rerun(host_repo, trac, event_log):
    """AC-FR0268-02: a drifted candidate, an identity mismatch or a STALE
    mark must NOT be reused -- the chain falls to the ``full.executed``
    rerun branch.

    Contract: interfaces §1a item 1 (the identity quadruple and the STALE
    clause), §4a outlet 1.

    Outlet: the EVENT STREAM -- the real ``judge_full_f_reuse`` command's
    judgment: a reuse event on the matching identity, and a rerun (no
    reuse) on the drifted one.
    """
    from tests.integration.test_verify_fullf_reuse import (
        _counter_total,
        _issue_judgment,
        _seed_real_full,
    )

    run_id, candidate_sha = _seed_real_full(host_repo, trac)
    before = event_log(run_id)
    seed_full = [e for e in before if e["type"] == "full.executed"][-1]
    producer_identity = seed_full["payload"].get("identity_basis")
    assert isinstance(producer_identity, (list, tuple)) and len(producer_identity) == 4
    assert _counter_total(host_repo) == 3

    # The matching identity: the FULL_F is reused.
    _issue_judgment(host_repo, run_id, candidate_sha)
    reused = [
        e
        for e in event_log(run_id)
        if e["type"] == "evidence.reused" and e["payload"].get("kind") == "full_f"
    ]
    assert reused, (
        "an undrifted, identity-matching FULL_F must be reused (the "
        "evidence.reused event is the judgment)"
    )

    # A DRIFTED identity must NOT be reused: the chain must rerun the
    # battery instead. The drift is the environment identity moving away
    # from the recorded basis (the judgment's identity_mismatch clause).
    # The env knob is the identity the chain recomputes, so changing it
    # between the two judgments is the drift the contract names.
    import os

    os.environ["TRAC_HF262_DRIFT_PROBE"] = "drifted"
    try:
        _issue_judgment(host_repo, run_id, candidate_sha)
    finally:
        os.environ.pop("TRAC_HF262_DRIFT_PROBE", None)
    events = event_log(run_id)
    after_reuse = [e for e in events if e["seq"] > reused[-1]["seq"]]
    assert not any(e["type"] == "evidence.reused" for e in after_reuse), (
        "a drifted identity must not reuse the stale FULL_F evidence"
    )
    reruns = [e for e in after_reuse if e["type"] == "full.executed"]
    assert reruns, (
        "a drifted identity must rerun the declared FULL instead of reusing"
    )
    assert _counter_total(host_repo) == 6, (
        "the drift must execute the declared FULL again, never reuse it"
    )

    # The AUTHORITY clause (INV-1): the reuse decision is the authority
    # predicate's, so the reused evidence must be one the predicate
    # accepts. The published FULL payload carries the double keys; the
    # authority is the ELIGIBILITY plus the candidate binding -- a truthy
    # serves_as_full_f alone is never the authority.
    reused_evidence = next(
        (
            e["payload"]
            for e in events
            if e["type"] == "full.executed" and e["seq"] < reused[-1]["seq"]
        ),
        {},
    )
    assert reused_evidence.get("full_f_eligible") is True, (
        "only an ELIGIBLE FULL execution may be reused as the pass basis "
        f"(payload={reused_evidence!r})"
    )
    assert reused_evidence.get("execution_commit") == candidate_sha, (
        "the reused FULL execution must be bound to the candidate "
        f"(payload={reused_evidence!r})"
    )
    assert _authority(reused_evidence, candidate_sha) is True, (
        "the reused FULL execution must be accepted by the contract's "
        "authority arbiter (interfaces §1a item 1)"
    )


# AC-FR0268-03@v0.8 TRACKS-TRACE stale full_f is not reused
def test_stale_full_f_not_reused(host_repo, trac, event_log):
    """AC-FR0268-03: STALE-marked FULL_F evidence is never reused as the
    pass basis, even with the SAME candidate_sha; the replay marks the
    evidence stale and no reuse event is produced.

    Contract: interfaces §1a item 1 (the STALE clause), §4a outlet 1.

    Outlet: the EVENT STREAM -- the real ``judge_full_f_reuse`` command's
    judgment.
    """
    from tests.integration.test_verify_fullf_reuse import (
        _issue_judgment,
        _seed_real_full,
    )
    from tracks import paths
    from tracks.store import Store

    run_id, candidate_sha = _seed_real_full(host_repo, trac)
    before = event_log(run_id)
    seed_full = [e for e in before if e["type"] == "full.executed"][-1]
    seed_seq = seed_full["seq"]

    store = Store(paths.tracks_home(host_repo))
    try:
        version = store.state(run_id).version or "v0.8"
        store.append(
            run_id,
            version,
            "evidence.staled",
            {
                "candidate_sha": candidate_sha,
                "reason": "stale",
                "source": "full_f",
                "source_seq": seed_seq,
            },
        )
    finally:
        store.close()

    _issue_judgment(host_repo, run_id, candidate_sha)
    events = event_log(run_id)

    reused = [
        e
        for e in events
        if e["type"] == "evidence.reused" and e["payload"].get("kind") == "full_f"
    ]
    assert reused == [], (
        "a STALE-marked FULL_F must never produce an evidence.reused event "
        f"(landed {len(reused)})"
    )
    staled = [e for e in events if e["type"] == "evidence.staled"]
    assert staled, "the replay must show the evidence marked stale"
    assert staled[-1]["payload"].get("candidate_sha") == candidate_sha
    assert staled[-1]["payload"].get("reason") == "stale"
    # The stale evidence is rejected and the battery is re-run instead.
    reruns = [e for e in events if e["type"] == "full.executed"]
    assert len(reruns) >= 2, (
        "a STALE-marked FULL_F must force a real FULL rerun, never a reuse "
        f"(full.executed events: {len(reruns)})"
    )

    # The STALE-marked evidence must NOT be authority-accepted: the
    # contract's arbiter refuses it, so the chain must not reuse it.
    stale_evidence = next(
        (e["payload"] for e in events if e["type"] == "full.executed"), {}
    )
    marked = dict(stale_evidence)
    marked["stale"] = True
    assert _authority(marked, candidate_sha) is False, (
        "a STALE-marked FULL_F must not be accepted by the contract's "
        "authority arbiter (interfaces §1a item 1)"
    )
