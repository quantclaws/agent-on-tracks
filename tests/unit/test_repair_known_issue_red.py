"""T-029 RED: in-place repair + Known Issue policy (FR-0286, NFR-0143).

Pins the still-unimplemented slices of tracks/executor/repair.py:

- IF-REPAIR-001: closed-set defect classification (behavior/gate/cve/
  contract -> owner + discipline), round_started without any stage.rolled_back
  semantics, frozen-test preservation across a repair round.
- IF-KNOWNISSUE-001: register only Prism-confirmed product-quality defects
  (known-issue label + candidate/evidence linkage); reject mechanism failures
  and security findings (not_product_defect); the preview must list every
  unfixed known issue (informed consent, AC-FR0286-05).

All target tests fail on the pre-fix baseline with assertion_failure on the
contract behaviour (no stub_token, no assembly errors). Only unit tests are
added (RED discipline, manifest red_test_paths = tests/unit).
"""

from __future__ import annotations

from tracks.executor.repair import (
    assert_frozen_tests_untouched,
    classify_defect,
    judge_irreparable,
    list_known_issues_for_preview,
    open_repair_round,
    register_known_issue,
)


def _calls(fn, *args, label: str):
    try:
        return fn(*args)
    except NotImplementedError as err:
        raise AssertionError(
            f"assertion failure: {label} not implemented"
        ) from err


# AC-FR0286-02@v0.8 TRACKS-TRACE IF-REPAIR-001 closed classification
def test_classify_defect_closed_set():
    """IF-REPAIR-001: closed defect_class -> owner + discipline mapping
    (behavior->Devon RED-first, gate->verification-only, cve->Archer advisory,
    contract->controlled revision); unknown finding fails closed."""
    for finding, expected in (
        ({"kind": "behavior"}, "behavior"),
        ({"kind": "gate"}, "gate"),
        ({"kind": "cve"}, "cve"),
        ({"kind": "contract"}, "contract"),
    ):
        result = _calls(classify_defect, finding, {}, label="classify_defect")
        assert isinstance(result, dict), "assertion failure: classification must be dict"
        assert result.get("defect_class") == expected, (
            f"assertion failure: expected defect_class={expected}, got {result!r}"
        )
        assert result.get("owner"), f"assertion failure: missing owner in {result!r}"
        assert result.get("discipline"), f"assertion failure: missing discipline in {result!r}"


# AC-FR0286-02@v0.8 TRACKS-TRACE IF-REPAIR-001 discipline mapping
def test_classify_defect_discipline_owner():
    """IF-REPAIR-001: the discipline/owner pairs are exact per FR-0286 §2
    (behavior->Devon + red_first; gate->verification_only; cve->Archer advisory;
    contract->contract_delta)."""
    mapping = {
        "behavior": ("Devon", "red_first"),
        "gate": (None, "verification_only"),
        "cve": ("Archer", "cve_advisory"),
        "contract": (None, "contract_delta"),
    }
    for kind, (owner, discipline) in mapping.items():
        result = _calls(classify_defect, {"kind": kind}, {}, label="classify_defect")
        if owner is not None:
            assert result.get("owner") == owner, (
                f"assertion failure: {kind} owner must be {owner}, got {result!r}"
            )
        assert result.get("discipline") == discipline, (
            f"assertion failure: {kind} discipline must be {discipline}, got {result!r}"
        )


# AC-FR0286-01@v0.8 TRACKS-TRACE IF-REPAIR-001 round_started never rollback
def test_open_repair_round_never_rolls_back(tmp_path):
    """AC-FR0286-01: open_repair_round emits round_started {round, budget,
    classification} bound to the candidate; it NEVER carries a
    stage.rolled_back / rollback command — in-place repair only."""
    result = _calls(
        open_repair_round,
        str(tmp_path),
        {"defect_class": "behavior", "owner": "Devon", "discipline": "red_first"},
        3,
        label="open_repair_round",
    )
    assert isinstance(result, dict), "assertion failure: round_started must be dict"
    assert result.get("round") == 1, f"assertion failure: first round must be 1, got {result!r}"
    assert result.get("budget") == 3
    assert result.get("classification", {}).get("defect_class") == "behavior"
    serialized = str(result)
    assert "rolled_back" not in serialized, (
        f"assertion failure: in-place repair must never roll back, got {serialized!r}"
    )


# AC-FR0286-04@v0.8 TRACKS-TRACE IF-REPAIR-001 budget-exhaustion signal
def test_open_repair_round_reports_budget_exhaustion():
    """FR-0286 §4 / AC-FR0286-04: the repair budget is finite (default 3,
    status shows round=<n>/3). Once the used rounds reach the budget, a
    further open_repair_round MUST surface budget exhaustion (so the caller
    routes to irreparable) instead of emitting an unbounded fresh round."""
    first = _calls(
        open_repair_round,
        "RUN-budget",
        {"defect_class": "behavior", "owner": "Devon", "discipline": "red_first"},
        1,
        label="open_repair_round",
    )
    assert first.get("round") == 1, f"assertion failure: first round must be 1, got {first!r}"
    second = _calls(
        open_repair_round,
        "RUN-budget",
        {"defect_class": "behavior", "owner": "Devon", "discipline": "red_first"},
        1,
        label="open_repair_round",
    )
    assert second.get("rounds_exhausted") is True or second.get("exhausted") is True, (
        "assertion failure: with budget 1 a second open_repair_round must surface "
        f"budget exhaustion, got {second!r}"
    )


# AC-FR0286-04@v0.8 TRACKS-TRACE IF-REPAIR-001 irreparable judgment
def test_judge_irreparable_budget_and_attribution():
    """AC-FR0286-04: irreparable only when budget exhausted AND Prism confirms
    attribution unchanged; with budget remaining it is never irreparable."""
    exhausted = _calls(
        judge_irreparable,
        3,
        3,
        {"attribution_unchanged": True},
        label="judge_irreparable",
    )
    assert exhausted is True, (
        "assertion failure: budget exhausted + attribution unchanged must be irreparable"
    )
    remaining = _calls(
        judge_irreparable,
        1,
        3,
        {"attribution_unchanged": True},
        label="judge_irreparable",
    )
    assert remaining is False, (
        "assertion failure: budget not exhausted must not be irreparable"
    )
    changed = _calls(
        judge_irreparable,
        3,
        3,
        {"attribution_unchanged": False},
        label="judge_irreparable",
    )
    assert changed is False, (
        "assertion failure: Prism attribution changed must keep repair path open"
    )


# AC-FR0286-04@v0.8 TRACKS-TRACE IF-REPAIR-002 C-class alternate triggers
def test_judge_irreparable_no_fix_or_contract_revision():
    """AC-FR0286-04: the two alternate C-class triggers are irreparable on
    their own — a fix that exceeds controlled contract revision, or a
    dependency with no available fix — regardless of remaining budget."""
    no_fix = _calls(
        judge_irreparable,
        1,
        3,
        {"attribution_unchanged": True, "no_fix_available": True},
        label="judge_irreparable",
    )
    assert no_fix is True, (
        "assertion failure: a dependency with no available fix must be judged "
        "irreparable even with repair budget remaining"
    )
    exceeds = _calls(
        judge_irreparable,
        1,
        3,
        {"attribution_unchanged": True, "exceeds_contract_revision": True},
        label="judge_irreparable",
    )
    assert exceeds is True, (
        "assertion failure: a fix exceeding controlled contract revision must "
        "be judged irreparable even with repair budget remaining"
    )


# AC-FR0286-02@v0.8 TRACKS-TRACE IF-REPAIR-001 frozen missing file blocks
def test_assert_frozen_tests_untouched_missing_file_blocks(tmp_path):
    """FR-0286 §10: a frozen test registered in before_digests that is MISSING
    from the repo is also a frozen-test breach — it must fail closed as a
    contract assertion, not leak an unrelated FileNotFoundError."""
    breach = AssertionError  # the contracted fail-closed shape
    try:
        assert_frozen_tests_untouched(
            tmp_path,
            {"tests/integration/test_vanished_frozen.py": "deadbeef"},
        )
    except breach:
        return  # contract breach surfaced fail-closed
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: assert_frozen_tests_untouched not implemented"
        ) from err
    except Exception as err:  # noqa: BLE001
        raise AssertionError(
            "assertion failure: a missing frozen test must fail closed as a "
            f"frozen-test contract breach (AssertionError), got {type(err).__name__}"
        ) from err
    raise AssertionError(
        "assertion failure: a missing frozen test must fail closed, but no "
        "breach was raised"
    )


# AC-FR0286-05@v0.8 TRACKS-TRACE IF-KNOWNISSUE-001 register product defect
def test_register_known_issue_product_defect():
    """AC-FR0286-05: a Prism-confirmed product-quality defect registers with
    the known-issue label and links candidate + evidence refs."""
    result = _calls(
        register_known_issue,
        "repo",
        {"kind": "behavior", "item_or_ac": "AC-FR0286-05"},
        "c" * 40,
        label="register_known_issue",
    )
    assert isinstance(result, dict), "assertion failure: registration must be dict"
    assert result.get("label") == "known-issue", (
        f"assertion failure: label must be known-issue, got {result!r}"
    )
    assert result.get("candidate_sha") == "c" * 40
    assert result.get("issue_number") or result.get("url"), (
        f"assertion failure: registration must carry issue number/url, got {result!r}"
    )


# AC-FR0286-06@v0.8 TRACKS-TRACE IF-KNOWNISSUE-001 exclusions rejected
def test_register_known_issue_rejects_mechanism_and_security():
    """AC-FR0286-06: mechanism failures and security findings are excluded —
    registration is rejected with reason=not_product_defect."""
    for excluded in ("mechanism_failure", "security_finding"):
        result = _calls(
            register_known_issue,
            "repo",
            {"kind": excluded, "item_or_ac": "AC-FR0286-06"},
            "c" * 40,
            label="register_known_issue",
        )
        assert isinstance(result, dict)
        assert result.get("reason") == "not_product_defect", (
            f"assertion failure: excluded {excluded} must reject with "
            f"not_product_defect, got {result!r}"
        )
        assert not result.get("issue_number"), (
            f"assertion failure: excluded {excluded} must not register an issue"
        )


# AC-FR0286-05@v0.8 TRACKS-TRACE IF-KNOWNISSUE-001 preview lists all unfixed
def test_list_known_issues_for_preview():
    """AC-FR0286-05: every unfixed known issue MUST appear in the preview —
    an unlisted known issue blocks release (informed consent)."""
    listed = _calls(
        list_known_issues_for_preview,
        "RUN-known",
        label="list_known_issues_for_preview",
    )
    assert isinstance(listed, list), "assertion failure: preview list must be a list"
    # each entry carries issue + waiver/AC linkage for the preview rendering
    for entry in listed:
        assert isinstance(entry, dict)
        assert entry.get("issue"), f"assertion failure: preview entry missing issue, got {entry!r}"
        assert entry.get("waiver"), (
            f"assertion failure: preview entry missing waiver/AC binding, got {entry!r}"
        )


# §1a row 28@v0.8 TRACKS-TRACE NFR-0143-01 closed repair.round_started payload
def test_open_repair_round_carries_candidate_and_trigger_seq():
    """§1a row 28 closed payload: repair.round_started carries round, budget,
    classification AND candidate_sha + trigger_event_seq — NFR-0143-01 binds
    every repair-family event to the frozen candidate and the trigger event
    seq makes the in-place disposition replayable (never a rollback,
    AC-FR0286-01)."""
    try:
        result = open_repair_round(
            "RUN-trigger",
            {
                "defect_class": "behavior",
                "owner": "Devon",
                "discipline": "red_first",
            },
            3,
            candidate_sha="d" * 40,
            trigger_event_seq=41,
        )
    except TypeError as err:
        raise AssertionError(
            "assertion failure: open_repair_round does not expose the §1a#28 "
            f"closed payload surface candidate_sha/trigger_event_seq: {err}"
        ) from err
    assert result.get("candidate_sha") == "d" * 40, (
        f"assertion failure: repair.round_started must bind the frozen "
        f"candidate_sha (§1a#28/NFR-0143-01), got {result!r}"
    )
    assert result.get("trigger_event_seq") == 41, (
        f"assertion failure: repair.round_started must carry trigger_event_seq "
        f"(§1a#28 disposition replayability), got {result!r}"
    )
    assert result.get("round") == 1, (
        f"assertion failure: first round must stay 1, got {result!r}"
    )
    assert result.get("budget") == 3, (
        f"assertion failure: budget must be forwarded, got {result!r}"
    )
    assert result.get("event") == "repair.round_started", (
        f"assertion failure: round opener must name its event, got {result!r}"
    )


# §1a row 29@v0.8 TRACKS-TRACE IF-KNOWNISSUE-001 rejected pair payload
def test_register_known_issue_rejected_carries_pair_payload():
    """§1a row 29: BOTH faces of the known-issue pair carry the closed
    payload — issue_number nullable, url nullable, item_or_ac, candidate_sha,
    evidence_refs, label=known-issue; the rejected face additionally carries
    reason=not_product_defect (AC-FR0286-06 zero-waiver exclusions)."""
    for excluded in ("mechanism_failure", "security_finding"):
        result = _calls(
            register_known_issue,
            "repo",
            {"kind": excluded, "item_or_ac": "AC-FR0286-06"},
            "c" * 40,
            label="register_known_issue",
        )
        assert "issue_number" in result and result["issue_number"] is None, (
            f"assertion failure: rejected {excluded} must carry the nullable "
            f"issue_number member of the §1a#29 pair payload, got {result!r}"
        )
        assert "url" in result and result["url"] is None, (
            f"assertion failure: rejected {excluded} must carry the nullable "
            f"url member of the §1a#29 pair payload, got {result!r}"
        )
        assert result.get("label") == "known-issue", (
            f"assertion failure: rejected {excluded} must carry the pair label "
            f"(§1a#29), got {result!r}"
        )
        assert isinstance(result.get("evidence_refs"), list), (
            f"assertion failure: rejected {excluded} must carry evidence_refs "
            f"(§1a#29), got {result!r}"
        )
        assert result.get("reason") == "not_product_defect", (
            f"assertion failure: rejected {excluded} must reject with "
            f"not_product_defect, got {result!r}"
        )
        assert result.get("candidate_sha") == "c" * 40, (
            f"assertion failure: rejected {excluded} must stay candidate-bound, "
            f"got {result!r}"
        )
