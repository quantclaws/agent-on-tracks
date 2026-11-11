"""Shield-owned seed data: host_repo v0.7 candidate-bound evidence chain.

Test-plan §2.4 test data duty for the IF-CLOSURE-001 integration anchors
(tests/integration/test_trace_closure.py): the anchors' GREEN depends on a
seeded host state that carries the approved AC set plus the baseline /
mutation / FULL evidence chain bound to ONE candidate digest
(interfaces.md §1a event payloads, §1g manifest field set, §1h adapter-audit
extension, §1i closure outlet).

The seed follows the suite's established synthetic-seed precedent
(``tests/hotfix_support.seed_v05_approved_baseline``): the project document
trio plus ``approval.recorded`` come from that existing helper; the runtime
evidence chain is appended through the public ``Store.append`` API with
payloads whose field sets are copied EXACTLY from interfaces.md §1a/§1g/§1h.
Fixture data is the truth source (test-plan §3: no implementation output is
consulted); every digest is derived deterministically from these constants,
so the seeded state is offline-reproducible and stable under xdist.

INV-2 binding: one ``candidate.frozen`` owns the closure candidate digest and
the suite-level ``full.executed`` is the authoritative 7362-shape (§1a item
1) bound to that same digest -- the candidate binding is derived from the
frozen event, never from a mutation manifest.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tests._support.hotfix_support import seed_v05_approved_baseline
from tests._support.release_premises import authoritative_full_f_payload
from tracks import paths
from tracks.store.store import Store

# The seed run carries all chain events under one synthetic run identity;
# the closure join only needs co-present evidence bound to one candidate.
SEED_RUN_ID = "closure-seed-v0.7"

# (ac, if_ref) pairs. The AC ids match the acceptance headings written by
# seed_v05_approved_baseline(version="v0.7") so documents and events agree on
# the bound AC set regardless of which side of the join the reader derives it
# from (§1i wiring: approved-AC -> ... -> same-candidate FULL pass).
CLOSURE_AC_BINDINGS: tuple[tuple[str, str], ...] = (
    ("AC-FR0030-01", "IF-BASELINE-001"),
    ("AC-FR0160-03", "IF-BASELINE-001"),
)

_REFERENCE_ADAPTER = {
    "adapter": "reference-pytest",
    "protocol": "tracks-test-result",
    "protocol_version": 1,
}


def _fixture_digest(*parts: object) -> str:
    """Content-addressed digest over fixed fixture inputs (test-plan §2.4)."""
    canonical = json.dumps(parts, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _candidate_digest(ac_pairs: tuple[tuple[str, str], ...]) -> str:
    return _fixture_digest("v0.7-closure-candidate", ac_pairs)


def _seed_ac_chain(store: Store, ac: str, if_ref: str, cd: str) -> None:
    """Append one AC's selected/red/authenticity/manifest/experiment chain."""
    slug = ac.lower()
    version_ac = f"{ac}@v0.7"
    node = f"opaque-node-{slug}"
    control = f"opaque-control-{slug}"
    selection_id = f"selection-{slug}"
    evidence_id = f"evidence-{slug}"
    # Homogeneous identity premise: the frozen §1i clause of AC-FR0265-03
    # treats a record's ``mutation_evidence`` as bound to the SAME candidate
    # digest as ``candidate_digest``, so this synthetic chain declares one
    # shared digest for both roles (§1g fixes the FIELD SET, not synthetic
    # values; the discriminator for wrong-candidate data stays KILL 2).
    patch_digest = cd

    # Phase 0 baseline repair closes the frozen-baseline side of the chain
    # (§1a row 1 / interfaces §3 sample). The payload carries the EXACT row-1
    # field set: ``bound_node.digest`` is the listed digest slot binding the
    # repaired node to this build's candidate identity, ``evidence_id`` names
    # the baseline authenticity evidence, and ``bound_node.node_id`` is the
    # real collected node bound to the AC.
    store.append(
        SEED_RUN_ID,
        "v0.7",
        "phase0.baseline_repaired",
        {
            "ac": version_ac,
            "bound_node": {"node_id": node, "digest": patch_digest},
            "selection_id": selection_id,
            "evidence_id": evidence_id,
            "command_echo": ["fixture", "phase0", slug],
            "status": "repaired",
        },
    )
    # M-TEST RED_CHECK selection (§1a + §1h adapter audit extension).
    store.append(
        SEED_RUN_ID,
        "v0.7",
        "test.selected",
        {
            "scope": "r2_delta",
            "selection_id": selection_id,
            "nodes": [node],
            "baseline": "baseline-v0.6",
            **_REFERENCE_ADAPTER,
        },
    )
    # RED_CHECK execution result: legal Red paired to the same selection.
    store.append(
        SEED_RUN_ID,
        "v0.7",
        "red.validated",
        {
            "status": "valid",
            "selection_id": selection_id,
            "evidence_id": evidence_id,
            "nodes": [node],
            "outcomes_ref": _fixture_digest("red-outcomes", node),
            **_REFERENCE_ADAPTER,
        },
    )
    # Authenticity gate judgement (§1a row 7 exact field set).
    store.append(
        SEED_RUN_ID,
        "v0.7",
        "authenticity.judged",
        {
            "ac": version_ac,
            "if_refs": [if_ref],
            "category": "new",
            "baseline": "v0.6",
            "nodes": [node],
            "selection_id": selection_id,
            "evidence_id": evidence_id,
            "red": "legal",
            "green": None,
            "counterexample_kill": "none",
            "counterexample_ref": None,
            "unrelated": [],
            "status": "passed",
            "attempt": 1,
            "actor": "runtime",
        },
    )
    # Mutation manifest blob (§1g exact field set; single AC/IF).
    manifest = {
        "protocol_version": 1,
        "ac": version_ac,
        "if_ref": if_ref,
        "candidate_digest": cd,
        "patch_digest": patch_digest,
        "target_nodes": [node],
        "control_nodes": [control],
        "runner_identity": "runtime:mutation-v1",
        "allowed_change_scope": ["tracks/..."],
        "expected_result": {"target": "killed", "controls": "green"},
    }
    manifest_digest = _fixture_digest("manifest", version_ac, if_ref, patch_digest, cd)
    store.append(
        SEED_RUN_ID,
        "v0.7",
        "mutation.manifest",
        {
            # Contracted §1a row-8 trio...
            "manifest_digest": manifest_digest,
            "manifest_blob": manifest,
            "status": "declared",
            # ...plus the top-level binding keys a harvest layer folds into
            # per-AC evidence without unrolling the blob (§1g fields).
            "ac": version_ac,
            "if_ref": if_ref,
            "candidate_digest": cd,
            "patch_digest": patch_digest,
        },
    )
    # Mutation experiment pass bound to the SAME candidate (§1a row 9).
    store.append(
        SEED_RUN_ID,
        "v0.7",
        "mutation.experiment",
        {
            "manifest_digest": manifest_digest,
            "status": "passed",
            "baseline": "verified",
            "apply": "identity_match",
            "target_kill": "verified",
            "controls": "green",
            "rollback": "clean",
            "command_echo": [["fixture", "experiment", slug]],
            "env_fingerprint": _fixture_digest("env-fingerprint"),
            "node_results_ref": None,
            "failure_signatures": [],
            "digests": {"candidate": cd, "patch": patch_digest},
        },
    )


def seed_v07_closure_chain(host_repo: Path) -> Path:
    """Seed *host_repo* with the v0.7 candidate-bound closure evidence chain.

    Documents + approval first (shared helper, precedent-consistent), then
    the per-AC M-TEST/M-IMPL chain and one same-candidate FULL pass — all
    through the public Store API. Idempotent per fresh tmp host_repo; every
    digest stems from fixture constants (offline deterministic).
    """
    seed_v05_approved_baseline(host_repo, version="v0.7")
    cd = _candidate_digest(CLOSURE_AC_BINDINGS)
    evidence_ids = [f"evidence-{ac.lower()}" for ac, _ in CLOSURE_AC_BINDINGS]
    home = paths.tracks_home(host_repo)
    store = Store(home)
    try:
        # INV-2: the closure candidate digest is derived ONLY from the frozen
        # candidate (or the milestone trace close); every per-AC chain below
        # binds that same digest.
        store.append(
            SEED_RUN_ID,
            "v0.7",
            "candidate.frozen",
            {"candidate_sha": cd, "clean_tree": True, "branch": "main"},
        )
        for ac, if_ref in CLOSURE_AC_BINDINGS:
            _seed_ac_chain(store, ac, if_ref, cd)
        # One full-suite pass covering the chained ACs (§1a + §1h extension):
        # the same candidate closes baseline/mutation/FULL into one identity.
        # The payload is the AUTHORITATIVE 7362-shape transcribed from
        # interfaces §1a item 1 (``serves_as_full_f`` deliberately false,
        # ``full_f_eligible`` true, identity quadruple present) -- the single
        # authority predicate credits nothing less.
        full_payload = authoritative_full_f_payload(cd)
        full_payload.update(
            {
                "suite": ["unit", "integration", "e2e"],
                "failed_nodes": [],
                "command_echo": {},
                "outcomes_ref": _fixture_digest("full-outcomes", cd),
                "gate": "ISLAND_GATE_2",
                **_REFERENCE_ADAPTER,
            }
        )
        full_payload["evidence_ids"] = evidence_ids
        store.append(
            SEED_RUN_ID,
            "v0.7",
            "full.executed",
            full_payload,
        )
    finally:
        store.close()
    return home
