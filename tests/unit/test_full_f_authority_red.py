"""IF-FULLF-001 RED: single FULL_F authority predicate (hotfix-262 T-001).

``is_authoritative_full_f`` must be the only arbiter of whether a
``full.executed`` payload is an authoritative FULL_F proof (INV-1):

  passed ∧ full_f_eligible ∧ round ∈ {FULL_1, fallback_full, FULL_F}
  ∧ candidate binding ∧ non-empty evidence_ids ∧ identity quadruple.

``serves_as_full_f`` alone never confers authority: the historical
7362 shape has ``serves_as_full_f=false, full_f_eligible=true`` and must be
credited, while the 5896 shape has ``serves_as_full_f=true,
full_f_eligible=false`` and must be rejected.

Expected values are enumerated from INV-1 / interfaces §1a, not derived
from the predicate implementation.
"""

from __future__ import annotations

import pytest

from tests.unit.full_f_authority_support import (
    CANDIDATE_SHA,
    FOREIGN_SHA,
    IDENTITY,
    authoritative_payload,
    authority,
)


# AC-FR0267-01@v0.8 TRACKS-TRACE IF-FULLF-001 7362-shape credited
def test_authoritative_full_f_credits_7362_shape_without_serves_flag():
    """7362 shape: serves_as_full_f=false, eligible=true -> authoritative."""
    payload = authoritative_payload(serves_as_full_f=False, full_f_eligible=True)

    assert authority(payload, CANDIDATE_SHA) is True


# AC-FR0268-01@v0.8 TRACKS-TRACE IF-FULLF-001 no candidate -> no binding check
def test_authoritative_full_f_accepts_without_candidate_sha():
    assert authority(authoritative_payload(), None) is True


# AC-FR0268-01@v0.8 TRACKS-TRACE IF-FULLF-001 5896-shape reason 1: ineligible
def test_authoritative_full_f_rejects_5896_shape_ineligible():
    """5896 shape reason 1: serves=true but full_f_eligible=false."""
    payload = authoritative_payload(serves_as_full_f=True, full_f_eligible=False)

    assert authority(payload, CANDIDATE_SHA) is False


# AC-FR0268-01@v0.8 TRACKS-TRACE IF-FULLF-001 5896-shape reason 2: foreign commit
def test_authoritative_full_f_rejects_5896_shape_foreign_commit():
    """5896 shape reason 2: the execution commit is not the candidate."""
    payload = authoritative_payload(serves_as_full_f=True, execution_commit=FOREIGN_SHA)

    assert authority(payload, CANDIDATE_SHA) is False


# AC-FR0268-01@v0.8 TRACKS-TRACE IF-FULLF-001 passed is conjunctive
@pytest.mark.parametrize("passed", [False, None, "true", 1])
def test_authoritative_full_f_rejects_non_true_passed(passed):
    payload = authoritative_payload(passed=passed)

    assert authority(payload, CANDIDATE_SHA) is False


# AC-FR0268-01@v0.8 TRACKS-TRACE IF-FULLF-001 eligible is conjunctive
@pytest.mark.parametrize("eligible", [False, None, "true", 1])
def test_authoritative_full_f_rejects_non_true_eligible(eligible):
    payload = authoritative_payload(full_f_eligible=eligible)

    assert authority(payload, CANDIDATE_SHA) is False


# AC-FR0268-01@v0.8 TRACKS-TRACE IF-FULLF-001 round closed set
@pytest.mark.parametrize(
    "round_name",
    ["FULL_1", "fallback_full", "FULL_F"],
)
def test_authoritative_full_f_accepts_authoritative_rounds(round_name):
    payload = authoritative_payload(round=round_name)

    assert authority(payload, CANDIDATE_SHA) is True


# AC-FR0268-01@v0.8 TRACKS-TRACE IF-FULLF-001 round closed set
@pytest.mark.parametrize(
    "round_name",
    ["FULL_2", "SELECT_DIFF", "local_gates", "", None],
)
def test_authoritative_full_f_rejects_non_authoritative_rounds(round_name):
    payload = authoritative_payload(round=round_name)

    assert authority(payload, CANDIDATE_SHA) is False


# AC-FR0268-01@v0.8 TRACKS-TRACE IF-FULLF-001 evidence_ids non-empty
@pytest.mark.parametrize("evidence_ids", [[], (), None, [""]])
def test_authoritative_full_f_requires_non_empty_evidence_ids(evidence_ids):
    payload = authoritative_payload(evidence_ids=evidence_ids)

    assert authority(payload, CANDIDATE_SHA) is False


# AC-FR0268-01@v0.8 TRACKS-TRACE IF-FULLF-001 identity quadruple
_IDENTITY_BASIS = [
    IDENTITY["tree"],
    IDENTITY["command"],
    IDENTITY["env"],
    IDENTITY["selection_id"],
]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"identity": None}, True),
        ({"identity_basis": None}, True),
        ({"selection_id": ""}, False),
        ({"identity_basis": []}, False),
        ({"identity_basis": _IDENTITY_BASIS[:3]}, False),
        (
            {"identity_basis": [*_IDENTITY_BASIS[:3], "another-selection"]},
            False,
        ),
        (
            {"identity": {**IDENTITY, "selection_id": "another-selection"}},
            False,
        ),
        ({"identity": {**IDENTITY, "tree": "another-tree"}}, False),
        ({"identity": None, "identity_basis": None}, False),
    ],
)
def test_authoritative_full_f_identity_quadruple_must_be_consistent(overrides, expected):
    payload = authoritative_payload(**overrides)

    assert authority(payload, CANDIDATE_SHA) is expected


# AC-FR0268-03@v0.8 TRACKS-TRACE IF-FULLF-001 non-mapping payloads fail closed
@pytest.mark.parametrize("payload", [None, [], "full.executed", 42])
def test_authoritative_full_f_rejects_non_mapping_payload(payload):
    assert authority(payload, CANDIDATE_SHA) is False


# AC-FR0268-02@v0.8 TRACKS-TRACE IF-FULLF-001 selection identity must match
def test_authoritative_full_f_selection_id_is_required():
    payload = authoritative_payload(
        selection_id="",
        identity={**IDENTITY, "selection_id": ""},
        identity_basis=[IDENTITY["tree"], IDENTITY["command"], IDENTITY["env"], ""],
    )

    assert authority(payload, CANDIDATE_SHA) is False
