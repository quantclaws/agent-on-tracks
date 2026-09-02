"""Shield-owned seed data: escape-gate downstream evidence + irreversible facts.

test-plan §8.1 A3/A4 seeding contract (b93 B2): the batch-1 machine registers
stages only through M-IMPL (M-VERIFY..M-MILESTONE have no producers), so the
return anchors cannot build post-target evidence or executed publish facts by
walking. This module appends them through the public ``Store.append`` API on
the ACTIVE (parked) run, with payload field sets copied exactly from
interfaces.md §1a (precedent: ``tests/integration/v07_closure_seed.py``). All
digests derive from fixture constants — offline reproducible, xdist-stable.

The seeding therefore never consults implementation output (test-plan §3):
A3 must observe that a conforming ``cmd_return`` stales exactly these buckets,
A4 that a conforming confirmation gate reports exactly this executed merge.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tracks import paths
from tracks.store.store import Store

# Six post-M-TEST release-evidence buckets (test-plan §8.1 A3 minimal set).
DOWNSTREAM_BUCKETS = (
    "candidate.frozen",
    "evidence.reused",
    "ci.run_observed",
    "security.assessed",
    "release.previewed",
    "release.decided",
)


def _digest(*parts: object) -> str:
    """Content-addressed utf8 digest over fixed fixture inputs."""
    canonical = json.dumps(parts, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _candidate_sha() -> str:
    return "sha256:" + _digest("escape-seed", "candidate")


def _preview_digest() -> str:
    return "sha256:" + _digest("escape-seed", "preview")


def seed_escape_downstream_evidence(host_repo: Path) -> dict[str, int]:
    """Append the six post-target evidence buckets to the active run.

    Returns ``{event_type: seq}`` so the anchor can assert that each staled
    entry references the actually-existing seed (b93 Q3.3: only existing
    buckets are staled).
    """
    home = paths.tracks_home(host_repo)
    store = Store(home)
    try:
        run_id = store.active_run()
        assert run_id, "escape seeds require an active (parked) run"
        version = store.state(run_id).version
        sha = _candidate_sha()
        preview = _preview_digest()
        seqs: dict[str, int] = {}

        def seed(event_type: str, payload: dict) -> None:
            ev = store.append(run_id, version, event_type, payload)
            seqs[event_type] = ev.seq

        # §1a#1 candidate.frozen: exact listed field set.
        seed(
            "candidate.frozen",
            {
                "candidate_sha": sha,
                "clean_tree": True,
                "branch": "releases/v0.5",
                "frozen_at_seq": 1,
            },
        )
        # §1a#2 evidence.reused (kind=full_f) — v0.8 extension fields.
        seed(
            "evidence.reused",
            {
                "kind": "full_f",
                "evidence_id": "seed-full-f-evidence",
                "identity_basis": {"candidate_sha": sha, "identity_quadruple": "seed"},
                "candidate_sha": sha,
            },
        )
        # §1a#4 ci.run_observed: exact listed field set.
        seed(
            "ci.run_observed",
            {
                "status": "passed",
                "repo": "seed/escape",
                "workflow": "ci.yml",
                "run_id": 1,
                "head_sha": sha,
                "candidate_sha": sha,
                "conclusion": "success",
                "required_checks": ["escape-seed-check"],
                "api_verified": True,
            },
        )
        # §1a#5 security.assessed: exact listed field set.
        seed(
            "security.assessed",
            {
                "status": "passed",
                "policy_digest": sha,
                "candidate_sha": sha,
                "scans": [],
                "prism_scope": "security",
            },
        )
        # §1a#6 release.previewed: exact listed field set.
        seed(
            "release.previewed",
            {
                "candidate_sha": sha,
                "preview_digest": preview,
                "artifact_digest": sha,
                "evidence_digests": {},
                "operation_plan_digest": sha,
                "contract_policy_digest": sha,
                "risks": [],
                "blob_ref": "seed-preview-blob",
            },
        )
        # §1a#7 release.decided: exact listed field set.
        seed(
            "release.decided",
            {
                "action": "release",
                "candidate_sha": sha,
                "preview_digest": preview,
                "reason": None,
                "target": None,
                "actor": "human",
            },
        )
    finally:
        store.close()
    return seqs


def seed_escape_publish_done(host_repo: Path) -> dict[str, int]:
    """Append one executed irreversible operation (publish.planned + executed
    done, §1b#8 sequence) to the active run for the A4 confirmation gate.

    Returns ``{event_type: seq}``.
    """
    home = paths.tracks_home(host_repo)
    store = Store(home)
    try:
        run_id = store.active_run()
        assert run_id, "escape seeds require an active (parked) run"
        version = store.state(run_id).version
        sha = _candidate_sha()
        preview = _preview_digest()
        key = "sha256:" + _digest("escape-seed", "merge", "releases/v0.5")
        seqs: dict[str, int] = {}

        # §1a#9 publish.planned — operation_kind/ target/ idempotency_key etc.
        planned = store.append(
            run_id,
            version,
            "publish.planned",
            {
                "operation_kind": "merge",
                "target": "releases/v0.5",
                "preview_digest": preview,
                "candidate_sha": sha,
                "idempotency_key": key,
                "when": None,
            },
        )
        seqs["publish.planned"] = planned.seq
        # §1a#10 publish.executed(done) — the operation IS an external fact.
        executed = store.append(
            run_id,
            version,
            "publish.executed",
            {
                "idempotency_key": key,
                "status": "done",
                "remote_check": {"exists": True, "matches": True},
                "candidate_sha": sha,
            },
        )
        seqs["publish.executed"] = executed.seq
    finally:
        store.close()
    return seqs
