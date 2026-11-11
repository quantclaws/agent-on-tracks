"""IF-RELEV-001 RED: release evidence key completeness (hotfix-262 T-004).

The v0.12.1 release preview/authorization contract (interfaces §1b, INV-4/5):

1. a new preview is valid only when the five required successful evidence
   keys are all present: ``full_f``, ``ci``, ``prism.verify_final``,
   ``prism.security`` and ``security``; the completeness check is
   unconditional (a stale barrier never skips it);
2. prism verdict keys are split by scope -- verify_final and security only;
   every other prism scope stays out of the release evidence;
3. FULL_F credit comes from the single authority predicate, never from
   ``serves_as_full_f`` alone -- a bound ``evidence.reused(kind="full_f")``
   is an acceptable proof too;
4. ``_gate_status`` surfaces ``full_f_ok``; ``_release_gate_blocked`` adds the
   full_f check;
5. legacy v0.12.0 preview blobs with the single ``"prism"`` key remain
   readable: ``preview_inputs_match`` compares against the key-set schema the
   blob recorded, without a DB migration.

Expected values are enumerated from interfaces §1b / architecture §1.1, never
derived from the implementation under test.
"""

from __future__ import annotations

import copy
import hashlib

import pytest

from tests.unit.full_f_authority_support import CANDIDATE_SHA, authoritative_payload
from tests.unit.hotfix262_release_support import (
    RELEASE_FACTS,
)
from tests.unit.hotfix262_release_support import (
    ci_payload as _ci_payload,
)
from tests.unit.hotfix262_release_support import (
    ev as _ev,
)
from tests.unit.hotfix262_release_support import (
    prism_payload as _prism_payload,
)
from tests.unit.hotfix262_release_support import (
    reused_full_payload as _reused_payload,
)
from tests.unit.hotfix262_release_support import (
    security_payload as _security_payload,
)
from tests.unit.test_release_preview_direct import _host
from tracks.executor.host_contract import load_host_contract
from tracks.executor.release_authorization import _gate_status
from tracks.executor.release_preview import (
    _evidence_is_success,
    _evidence_key,
    assemble_preview,
    evidence_digests,
    preview_inputs_match,
)

CANDIDATE = CANDIDATE_SHA
REQUIRED_KEYS = frozenset(
    {"full_f", "ci", "prism.verify_final", "prism.security", "security"}
)
RELEASE_TEST_FACTS = dict(RELEASE_FACTS)


def _complete_events(
    *,
    candidate: str = CANDIDATE,
    skip: str | None = None,
    full_payload: dict | None = None,
    verify_final_findings: list | None = None,
    security_findings: list | None = None,
) -> list:
    payloads = {
        "full_f": ("full.executed", full_payload or authoritative_payload()),
        "ci": ("ci.run_observed", _ci_payload(candidate)),
        "prism.verify_final": (
            "prism.verdict",
            _prism_payload("verify_final", candidate, verify_final_findings),
        ),
        "prism.security": (
            "prism.verdict",
            _prism_payload("security", candidate, security_findings),
        ),
        "security": ("security.assessed", _security_payload(candidate)),
    }
    return [
        _ev(seq, payloads[key][0], copy.deepcopy(payloads[key][1]))
        for seq, key in enumerate(sorted(payloads), start=1)
        if key != skip
    ]


def _required_event(event, key: str) -> bool:
    if key == "full_f":
        return event.type == "full.executed"
    if key == "ci":
        return event.type == "ci.run_observed"
    if key == "security":
        return event.type == "security.assessed"
    return event.type == "prism.verdict" and event.payload.get("scope") == key.split(".", 1)[1]


# ---------------------------------------------------------------------------
# split prism keys / required key completeness
# ---------------------------------------------------------------------------


# AC-FR0273-01@v0.8 TRACKS-TRACE IF-RELEV-001 prism key split by scope
@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ("verify_final", "prism.verify_final"),
        ("security", "prism.security"),
        ("PRISM_PLAN", None),
        ("PRISM_RED", None),
        ("M-TEST", None),
        ("M-IMPL", None),
        ("", None),
    ],
)
def test_prism_evidence_key_is_split_by_scope(scope, expected):
    event = _ev(1, "prism.verdict", {"scope": scope, "verdict": "pass"})

    assert _evidence_key(event, event.payload) == expected


# AC-FR0270-02@v0.8 TRACKS-TRACE IF-RELEV-001 five required keys
def test_evidence_digests_requires_all_five_release_keys():
    out = evidence_digests(_complete_events(), CANDIDATE)

    assert out is not None
    assert set(out) >= REQUIRED_KEYS
    assert "prism" not in out


# AC-FR0270-02@v0.8 TRACKS-TRACE IF-RELEV-001 missing key invalidates
@pytest.mark.parametrize("missing", sorted(REQUIRED_KEYS))
def test_missing_required_evidence_key_invalidates_preview_digests(missing):
    out = evidence_digests(_complete_events(skip=missing), CANDIDATE)

    assert out is None, f"missing {missing} must fail the preview closed"


# AC-FR0270-02@v0.8 TRACKS-TRACE IF-RELEV-001 7362-shape credited
def test_full_f_credit_uses_authority_not_serves_flag():
    payload = authoritative_payload(
        serves_as_full_f=False, full_f_eligible=True, round="FULL_F"
    )

    out = evidence_digests(_complete_events(full_payload=payload), CANDIDATE)

    assert out is not None
    assert "full_f" in out


# AC-FR0270-02@v0.8 TRACKS-TRACE IF-RELEV-001 non-authoritative round
def test_non_authoritative_full_round_does_not_satisfy_completeness():
    payload = authoritative_payload(round="PARTIAL")

    out = evidence_digests(_complete_events(full_payload=payload), CANDIDATE)

    assert out is None


# AC-FR0270-02@v0.8 TRACKS-TRACE IF-RELEV-001 bound reuse is a full_f proof
def test_bound_evidence_reused_full_f_satisfies_required_key():
    events = _complete_events(skip="full_f")
    events.append(_ev(6, "evidence.reused", _reused_payload(CANDIDATE)))

    out = evidence_digests(events, CANDIDATE)

    assert out is not None
    assert "full_f" in out


# AC-FR0270-02@v0.8 TRACKS-TRACE IF-RELEV-001 reuse must be full_f
def test_non_full_f_reuse_does_not_satisfy_the_full_f_key():
    events = _complete_events(skip="full_f")
    reuse = _reused_payload(CANDIDATE)
    reuse["kind"] = "ci"
    events.append(_ev(6, "evidence.reused", reuse))

    assert evidence_digests(events, CANDIDATE) is None


# AC-FR0271-02@v0.8 TRACKS-TRACE IF-RELEV-001 pass+blocker is not success
@pytest.mark.parametrize("scope", ["verify_final", "security"])
def test_blocking_findings_on_prism_verdict_invalidate_the_split_key(scope):
    findings = [{"severity": "blocker"}]
    events = _complete_events(
        verify_final_findings=findings if scope == "verify_final" else None,
        security_findings=findings if scope == "security" else None,
    )

    assert evidence_digests(events, CANDIDATE) is None


# AC-FR0271-02@v0.8 TRACKS-TRACE IF-RELEV-001 success predicate matrix
@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, True),
        ({"round": "PARTIAL"}, False),
        ({"round": "FULL_2"}, False),
        ({"full_f_eligible": False}, False),
        ({"passed": False}, False),
    ],
)
def test_full_evidence_success_uses_the_authority_predicate(overrides, expected):
    payload = authoritative_payload(**overrides)

    assert _evidence_is_success("full.executed", payload, CANDIDATE) is expected


# AC-FR0271-02@v0.8 TRACKS-TRACE IF-RELEV-001 pass+blocker is not success
def test_prism_success_rejects_pass_with_blocking_findings():
    payload = _prism_payload("verify_final", CANDIDATE, findings=[{"severity": "high"}])

    assert _evidence_is_success("prism.verdict", payload) is False


# ---------------------------------------------------------------------------
# preview assembly / gate status
# ---------------------------------------------------------------------------


# AC-FR0273-01@v0.8 TRACKS-TRACE IF-RELEV-001 preview invalid without key
@pytest.mark.parametrize("missing", sorted(REQUIRED_KEYS))
def test_assemble_preview_is_invalid_without_each_required_key(tmp_path, missing):
    _executor, store, contract_path, candidate, _expected = _host(tmp_path)
    events = [
        event
        for event in store.events("RUN")
        if not _required_event(event, missing)
    ]
    contract = load_host_contract(contract_path)
    digest = "sha256:" + hashlib.sha256(contract_path.read_bytes()).hexdigest()

    preview = assemble_preview(
        store.home.parent,
        contract,
        candidate,
        digest,
        dict(RELEASE_TEST_FACTS),
        events,
        journey="feature",
    )

    assert preview is None


# AC-FR0270-02@v0.8 TRACKS-TRACE IF-RELEV-001 gate status full_f_ok
def test_gate_status_surfaces_full_f_ok_and_passes_the_complete_set(tmp_path):
    _repo, store, run_id, candidate, digest, contract_path, _preview = _host_appendix(
        tmp_path
    )
    contract = load_host_contract(contract_path)

    status = _gate_status(list(store.events(run_id)), candidate, contract, digest)

    assert status.get("full_f_ok") is True
    assert status["gate_failed"] is False


# AC-FR0270-02@v0.8 TRACKS-TRACE IF-RELEV-001 gate status fails without full_f
def test_gate_status_fails_without_a_full_f_key(tmp_path):
    _repo, store, run_id, candidate, digest, contract_path, _preview = _host_appendix(
        tmp_path
    )
    events = [
        event
        for event in store.events(run_id)
        if not _required_event(event, "full_f")
    ]
    contract = load_host_contract(contract_path)

    status = _gate_status(events, candidate, contract, digest)

    assert status.get("full_f_ok") is False
    assert status["gate_failed"] is True


# AC-FR0270-02@v0.8 TRACKS-TRACE IF-RELEV-001 bound reuse satisfies the gate
def test_gate_status_accepts_bound_evidence_reused_full_f(tmp_path):
    _repo, store, run_id, candidate, digest, contract_path, _preview = _host_appendix(
        tmp_path
    )
    events = [
        event
        for event in store.events(run_id)
        if not _required_event(event, "full_f")
    ]
    events.append(_ev(99, "evidence.reused", _reused_payload(candidate)))
    contract = load_host_contract(contract_path)

    status = _gate_status(events, candidate, contract, digest)

    assert status.get("full_f_ok") is True
    assert status["gate_failed"] is False


def _host_appendix(tmp_path):
    """The authorization host fixture (full evidence incl. local gate)."""
    from tests.unit.test_release_authorization_direct import _host as auth_host

    return auth_host(tmp_path)


# ---------------------------------------------------------------------------
# legacy preview blob read-back
# ---------------------------------------------------------------------------


def _blob(evidence: dict, **overrides) -> dict:
    blob = {
        "candidate_sha": "a" * 40,
        "artifact_digest": "",
        "evidence_digests": evidence,
        "operation_plan_digest": "sha256:" + "1" * 64,
        "contract_policy_digest": "sha256:" + "2" * 64,
        "operation_plan": {"journey": "feature", "steps": ["tag:v0.8.0"]},
        "risks": [],
        "preview_digest": "sha256:" + "3" * 64,
    }
    blob.update(overrides)
    return blob


_SPLIT_EVIDENCE = {
    "full_f": "sha256:" + "f" * 64,
    "ci": "sha256:" + "c" * 64,
    "prism.verify_final": "sha256:" + "v" * 64,
    "prism.security": "sha256:" + "s" * 64,
    "security": "sha256:" + "e" * 64,
}


# AC-FR0273-02@v0.8 TRACKS-TRACE IF-RELEV-001 legacy single prism is readable
def test_legacy_single_prism_blob_still_matches_the_split_assembly():
    old = _blob({"prism": "sha256:" + "s" * 64})
    old["blob_ref"] = ".tracks/runtime/blobs/legacy"
    current = _blob(dict(_SPLIT_EVIDENCE))

    assert preview_inputs_match(old, current) is True


# AC-FR0273-02@v0.8 TRACKS-TRACE IF-RELEV-001 legacy blob rejects drift
def test_legacy_blob_still_rejects_a_changed_prism_digest():
    old = _blob({"prism": "sha256:" + "s" * 64})
    current = _blob({**_SPLIT_EVIDENCE, "prism.security": "sha256:" + "x" * 64})

    assert preview_inputs_match(old, current) is False


# AC-FR0273-02@v0.8 TRACKS-TRACE IF-RELEV-001 legacy blob rejects plan drift
def test_legacy_blob_still_rejects_changed_non_evidence_inputs():
    old = _blob({"prism": "sha256:" + "s" * 64})
    current = _blob(
        dict(_SPLIT_EVIDENCE), operation_plan_digest="sha256:" + "9" * 64
    )

    assert preview_inputs_match(old, current) is False


# AC-FR0273-02@v0.8 TRACKS-TRACE IF-RELEV-001 split blob stays exact
def test_split_schema_blob_requires_an_exact_evidence_match():
    old = _blob(dict(_SPLIT_EVIDENCE))
    matching = _blob(dict(_SPLIT_EVIDENCE))
    drifted = _blob({**_SPLIT_EVIDENCE, "ci": "sha256:" + "d" * 64})

    assert preview_inputs_match(old, matching) is True
    assert preview_inputs_match(old, drifted) is False
