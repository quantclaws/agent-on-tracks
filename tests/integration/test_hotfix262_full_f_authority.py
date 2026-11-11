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


def _authority_credit(repo: Path, home: Path, version: str, ac_id: str) -> str:
    """The authority's credit for *ac_id*, read from the PUBLIC closure face.

    The ``trac check trace --version <v> --json`` CLI reports the
    candidate-bound evidence the authority accepted for the AC -- the
    ``full_pass_evidence`` is the authority's credit.
    """
    report = _trace_record(repo, home, version, ac_id)
    return str(report.get("full_pass_evidence") or "")


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

    _acceptance(
        paths.version_dir(paths.tracks_home(host_repo), "v0.8"), "AC-FR0268-02"
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
    # The drifted judgment must be a RERUN carrying the judgment's reason --
    # and the rerun's execution must be judged on the DRIFTED identity, not
    # on the stale one it replaced.
    drifted_rerun = next(
        (
            e
            for e in reversed(events)
            if e["type"] == "full.executed" and e["seq"] > reused[-1]["seq"]
        ),
        None,
    )
    assert drifted_rerun is not None, (
        "the drifted identity must produce a rerun execution"
    )
    drifted_reason = (
        drifted_rerun["payload"].get("reason")
        or drifted_rerun["payload"].get("judgment_reason")
    )
    assert drifted_reason, (
        "the rerun must record the judgment's reason (drift / stale / "
        f"identity_mismatch) (payload={drifted_rerun['payload']!r})"
    )
    assert drifted_reason in ("drift", "stale", "identity_mismatch"), (
        "the rerun's reason must be one of the AC's closed reasons "
        f"(got {drifted_reason!r})"
    )
    # The SINGLE-ARBITER clause (interfaces §1a item 1): the reuse
    # judgment is the shared authority's, so the reuse must name the
    # source the authority accepted -- an execution the authority refuses
    # can never be the reused source. The observation is the reuse EVENT's
    # own source binding.
    reused_source = next(
        (
            e
            for e in events
            if e["type"] == "full.executed"
            and e["seq"] == reused[-1]["payload"].get("source_event_seq")
        ),
        None,
    )
    assert reused_source is not None, (
        "the reuse must name a real full.executed source (payload="
        f"{reused[-1]['payload']!r})"
    )
    assert reused_source["payload"].get("full_f_eligible") is True, (
        "the reused source must be an ELIGIBLE full execution -- the "
        "authority refuses an ineligible one (interfaces §1a item 1) "
        f"(payload={reused_source['payload']!r})"
    )
    # The reused source must also be CANDIDATE-BOUND: the authority binds
    # the proof to the candidate (interfaces §1a item 1). An execution
    # committed elsewhere is never the authority for this candidate.
    assert reused_source["payload"].get("execution_commit") == candidate_sha, (
        "the reused source must be committed at the candidate -- the "
        "authority binds the proof to it (interfaces §1a item 1) "
        f"(payload={reused_source['payload']!r})"
    )
    # The INELIGIBLE execution (the 5896-shape: eligible=false, truthy
    # serves_as_full_f) must never be the reused source -- the authority
    # refuses it, so the chain must not reuse it. The observation is the
    # reuse event's own source binding.
    ineligible = _payload("full_f_5896_shape")
    assert ineligible["full_f_eligible"] is False
    assert ineligible["serves_as_full_f"] is True
    ineligible_source = next(
        (
            e
            for e in events
            if e["type"] == "full.executed"
            and e["seq"] == reused[-1]["payload"].get("source_event_seq")
            and not e["payload"].get("full_f_eligible")
        ),
        None,
    )
    assert ineligible_source is None, (
        "an INELIGIBLE full execution must never be the reused source -- the "
        "authority refuses it (interfaces §1a item 1) "
        f"(payload={ineligible_source['payload']!r})"
    )
    # The authority's CREDIT for the AC, read from the PUBLIC closure face,
    # must be the rerun's fresh evidence -- never the stale reuse's.
    home = paths.tracks_home(host_repo)
    credit = _authority_credit(host_repo, home, "v0.8", "AC-FR0268-02")
    assert credit, (
        "the closure read face must report the authority's credit for the AC "
        "(the rerun's fresh evidence)"
    )
    # The credit must name the RERUN's evidence, not the stale reuse's:
    # the authority credited the execution it judged, and the drifted
    # judgment judged the rerun.
    drifted_rerun = next(
        (
            e
            for e in reversed(events)
            if e["type"] == "full.executed" and e["seq"] > reused[-1]["seq"]
        ),
        None,
    )
    assert drifted_rerun is not None, (
        "the drifted identity must produce a rerun execution"
    )
    rerun_evidence = [
        eid
        for eid in (drifted_rerun["payload"].get("evidence_ids") or [])
        if isinstance(eid, str)
    ]
    assert rerun_evidence, (
        "the rerun must carry its own evidence ids (the authority credits "
        f"them) (payload={drifted_rerun['payload']!r})"
    )
    assert credit in rerun_evidence, (
        "the authority's credit must be the RERUN's evidence id, never the "
        f"stale reuse's (credit={credit!r}, rerun evidence={rerun_evidence!r})"
    )
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
    # The rerun records the judgment's REASON on the emitted execution (the
    # AC names drift / stale / identity_mismatch); a rerun with no reason
    # leaves the operator without the judgment basis.
    assert reruns[-1]["payload"].get("reason"), (
        "the rerun must record the judgment's reason (drift / stale / "
        f"identity_mismatch) (payload={reruns[-1]['payload']!r})"
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
    # The reuse event names the SOURCE it reused (source_event_seq): the
    # authority clause binds the reuse to the eligible, candidate-bound
    # execution -- never to a truthy serves_as_full_f payload.
    assert reused[-1]["payload"].get("source_event_seq") is not None, (
        "the reuse event must name the source execution it reused "
        f"(payload={reused[-1]['payload']!r})"
    )
    assert reused[-1]["payload"].get("candidate_sha") == candidate_sha, (
        "the reuse must be bound to the frozen candidate "
        f"(payload={reused[-1]['payload']!r})"
    )
    # The named source IS the reused execution, and it carries the double
    # keys the contract requires (both present, the truthy one alone never
    # enough): the reuse must not be bound to an execution that only
    # carries ``serves_as_full_f``.
    source_seq = reused[-1]["payload"]["source_event_seq"]
    source = next(
        (e for e in events if e["type"] == "full.executed" and e["seq"] == source_seq),
        None,
    )
    assert source is not None, (
        f"the reuse must name a real full.executed source (seq={source_seq})"
    )
    assert source["payload"].get("full_f_eligible") is True, (
        "the reused source must be an ELIGIBLE full execution "
        f"(payload={source['payload']!r})"
    )
    # The identity quadruple must AGREE between the reuse judgment and the
    # source execution it reused (the AC's "identity 不一致" clause): a
    # reuse whose basis differs from the source's own identity is a reuse
    # of a DIFFERENT execution than the one that was judged.
    source_identity = source["payload"].get("identity") or {}
    judged_basis = list(reused[-1]["payload"].get("identity_basis") or [])
    assert judged_basis, (
        "the reuse judgment must carry the identity basis it judged "
        f"(payload={reused[-1]['payload']!r})"
    )
    assert len(judged_basis) == 4, (
        "the identity basis is the four-tuple (tree, command, env, "
        f"selection_id) (got {judged_basis!r})"
    )
    assert judged_basis[3] == source_identity.get("selection_id"), (
        "the reuse judgment's selection_id must agree with the reused "
        f"execution's identity (basis={judged_basis!r}, "
        f"identity={source_identity!r})"
    )
    # The reuse judgment's basis must be the SOURCE's own basis, not a
    # basis recomputed elsewhere: the authority is the source execution's
    # identity quadruple (interfaces §1a item 1).
    source_basis = list(source["payload"].get("identity_basis") or [])
    assert source_basis, (
        "the reused execution must carry its own identity basis "
        f"(payload={source['payload']!r})"
    )
    assert judged_basis == source_basis, (
        "the reuse judgment must be made on the reused execution's own "
        f"identity basis (judged={judged_basis!r}, source={source_basis!r})"
    )
    # The reuse judgment is observed through the PUBLIC status face: the
    # chain's latest judgment is the DRIFTED rerun, never a reuse, and it
    # carries the judgment's REASON (the AC names drift / stale /
    # identity_mismatch).
    status = trac("status")
    assert status.returncode == 0, status.stderr
    assert "full_reuse=full_rerun" in status.stdout, (
        "the status must report the drifted judgment as a rerun "
        f"(status={status.stdout!r})"
    )
    assert "full_reuse=full_f" not in status.stdout, (
        "the drifted identity must not be reported as a reuse "
        f"(status={status.stdout!r})"
    )
    assert any(
        reason in status.stdout for reason in ("drift", "stale", "identity_mismatch")
    ), (
        "the rerun must report the judgment's reason (drift / stale / "
        f"identity_mismatch) (status={status.stdout!r})"
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

    _acceptance(
        paths.version_dir(paths.tracks_home(host_repo), "v0.8"), "AC-FR0268-03"
    )
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
    # The AC names the REPLAY face as the observation: `trac replay` must
    # show the evidence marked stale and must not show a reuse.
    replay = trac("replay")
    assert replay.returncode == 0, replay.stderr
    assert "evidence.staled" in replay.stdout, (
        "the replay must show the evidence marked stale "
        f"(replay={replay.stdout[-600:]!r})"
    )
    assert "evidence.reused" not in replay.stdout, (
        "the replay must not show a reuse of the stale evidence "
        f"(replay={replay.stdout[-600:]!r})"
    )
    # The stale evidence is rejected and the battery is re-run instead.
    reruns = [e for e in events if e["type"] == "full.executed"]
    assert len(reruns) >= 2, (
        "a STALE-marked FULL_F must force a real FULL rerun, never a reuse "
        f"(full.executed events: {len(reruns)})"
    )

    # The operator-visible judgment: the STALE-marked evidence is NOT
    # reused -- the status reports the rerun, never a reuse.
    status = trac("status")
    assert status.returncode == 0, status.stderr
    assert "full_reuse=full_f" not in status.stdout, (
        "a STALE-marked FULL_F must not be reported as a reuse "
        f"(status={status.stdout!r})"
    )
    # The authority's CREDIT for the AC, read from the PUBLIC closure face,
    # must be the RERUN's evidence -- never the stale execution's.
    home = paths.tracks_home(host_repo)
    credit = _authority_credit(host_repo, home, "v0.8", "AC-FR0268-03")
    assert credit, (
        "the closure read face must report the authority's credit for the AC "
        "(the rerun's fresh evidence)"
    )
    stale_rerun = next(
        (
            e
            for e in reversed(events)
            if e["type"] == "full.executed" and e["seq"] > seed_seq
        ),
        None,
    )
    assert stale_rerun is not None, (
        "the stale evidence must have been rerun"
    )
    rerun_evidence = [
        eid
        for eid in (stale_rerun["payload"].get("evidence_ids") or [])
        if isinstance(eid, str)
    ]
    assert rerun_evidence, (
        "the rerun must carry its own evidence ids (the authority credits "
        f"them) (payload={stale_rerun['payload']!r})"
    )
    assert credit in rerun_evidence, (
        "the authority's credit must be the RERUN's evidence id, never the "
        f"stale execution's (credit={credit!r}, rerun evidence={rerun_evidence!r})"
    )
