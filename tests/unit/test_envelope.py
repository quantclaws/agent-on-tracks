"""Unit: kernel.envelope unified reply envelope (IF-ENVELOPE-001/002).

Operator OOB 2026-09-06 (user-authorized): the module half of the envelope
contract — single deterministic parse path, per-kind payload schemas (the
ones DECLARED on dispatch assignments), schema digest, and staged parity.
The integration contract files (tests/integration/test_envelope_*.py) stay
T-024's registered deliverables; these unit pins protect the injection face
that ships now.
"""

import json

import pytest

from tracks.kernel.envelope import (
    ENVELOPE_PROTOCOL,
    ENVELOPE_VERSION,
    REVIEW_SUMMARY_MAX,
    EnvelopeFormatError,
    build_assignment_envelope,
    check_envelope_parity,
    envelope_declared_kind,
    envelope_kind,
    envelope_schema_digest,
    parse_agent_output,
    validate_envelope,
)


def _finding(idx=1, **over):
    finding = {
        "id": f"F-{idx}",
        "severity": "P1",
        "defect_classification": "impl_defect",
        "criterion": "AC-FR0270-02",
        "artifact": "tracks/executor/executor.py",
        "ac_refs": ["AC-FR0270-02"],
        "summary": "fake channel fakes api_verified before the credential check",
    }
    finding.update(over)
    return finding


def _revise_payload(**over):
    payload = {
        "verdict": "revise",
        "review_summary": "P1: fake channel fakes api_verified",
        "review_body": "The readback binds api_verified=True before any "
        "credential check, violating IF-VERIFY-004.",
        "findings": [_finding()],
    }
    payload.update(over)
    return payload


def _envelope_text(payload, kind="prism:final", version=ENVELOPE_VERSION):
    header = {"kind": kind, "version": version}
    return (
        "一些前置散文（prose is tolerated outside the single block）\n"
        f"```{ENVELOPE_PROTOCOL}\n"
        + json.dumps({"envelope": header, "payload": payload}, ensure_ascii=False)
        + "\n```\n"
    )


# -- parse: the single deterministic path --------------------------------------


def test_parse_accepts_single_envelope_block_with_prose_around_it():
    parsed = parse_agent_output(_envelope_text(_revise_payload()))
    assert parsed["envelope"]["kind"] == "prism:final"
    assert parsed["envelope"]["version"] == ENVELOPE_VERSION
    assert parsed["payload"]["verdict"] == "revise"


def test_parse_pass_payload_needs_no_revise_fields():
    parsed = parse_agent_output(_envelope_text({"verdict": "pass"}, kind="prism:review"))
    assert parsed["payload"] == {"verdict": "pass"}


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("no fence at all, just prose", "no_envelope_block"),
        (
            _envelope_text(_revise_payload()) + _envelope_text(_revise_payload()),
            "multiple_envelope_blocks",
        ),
        (
            f"```{ENVELOPE_PROTOCOL}\n" + json.dumps(_revise_payload()),
            "malformed_json",
        ),
        (
            f"```{ENVELOPE_PROTOCOL}\nnot json at all\n```",
            "malformed_json",
        ),
        (
            f"```{ENVELOPE_PROTOCOL}\n"
            + json.dumps({"payload": _revise_payload()})
            + "\n```",
            "missing_kind",
        ),
        (
            f"```{ENVELOPE_PROTOCOL}\n"
            + json.dumps({"envelope": {"kind": "prism:final"}, "payload": {}})
            + "\n```",
            "missing_version",
        ),
        (
            _envelope_text(_revise_payload(), version=1),
            "unknown_version",
        ),
        (
            _envelope_text(_revise_payload(), kind="maestro:orchestrate"),
            "unknown_kind",
        ),
    ],
)
def test_parse_rejects_deviations_with_taxonomy_kind(text, kind):
    with pytest.raises(EnvelopeFormatError) as exc:
        parse_agent_output(text)
    assert exc.value.kind == kind


@pytest.mark.parametrize(
    "payload",
    [
        {"verdict": "revise"},  # no summary/body/findings
        _revise_payload(review_summary="x" * (REVIEW_SUMMARY_MAX + 1)),
        _revise_payload(review_summary="   "),
        _revise_payload(review_body=""),
        _revise_payload(findings=[]),
        _revise_payload(findings=["not-an-object"]),
        _revise_payload(findings=[{k: v for k, v in _finding().items() if k != "severity"}]),
        _revise_payload(findings=[_finding(summary="x" * (REVIEW_SUMMARY_MAX + 1))]),
    ],
)
def test_parse_rejects_review_schema_violations(payload):
    with pytest.raises(EnvelopeFormatError) as exc:
        parse_agent_output(_envelope_text(payload))
    assert exc.value.kind == "schema_violation"


def test_validate_envelope_expected_kind_mismatch_is_schema_violation():
    envelope = parse_agent_output(_envelope_text(_revise_payload(), kind="prism:final"))
    with pytest.raises(EnvelopeFormatError) as exc:
        validate_envelope(envelope, "prism:diagnose")
    assert exc.value.kind == "schema_violation"
    assert validate_envelope(envelope, "prism:final") == envelope


# -- kind closed set + declared rollout gate -----------------------------------


def test_envelope_kind_maps_prism_review_family_and_diagnose():
    assert envelope_kind("prism", "PRISM_REVIEW") == "prism:review"
    assert envelope_kind("prism", "prism_plan") == "prism:plan"
    assert envelope_kind("prism", "DIAGNOSE") == "prism:diagnose"
    assert envelope_kind("devon", "GREEN") == "devon:green"
    assert envelope_kind("shield", "WRITE") == "shield:write"
    assert envelope_kind("archer", "RULING") == "archer:ruling"
    assert envelope_kind("maestro", "WHATEVER") is None
    assert envelope_kind("prism", "NO_SUCH_SUBSTATE") is None


def test_declared_kind_only_for_kinds_with_registered_schema():
    assert envelope_declared_kind("prism", "PRISM_FINAL") == "prism:final"
    assert envelope_declared_kind("prism", "DIAGNOSE") == "prism:diagnose"
    # Writer/authority kinds stay undeclared until T-024 registers schemas.
    assert envelope_declared_kind("devon", "GREEN") is None
    assert envelope_declared_kind("archer", "PLANNING") is None


# -- schema digest (parity evidence) -------------------------------------------


def test_schema_digest_is_stable_and_kind_distinct():
    assert envelope_schema_digest("prism:final") == envelope_schema_digest("prism:review")
    assert envelope_schema_digest("prism:final") != envelope_schema_digest("prism:diagnose")
    with pytest.raises(EnvelopeFormatError):
        envelope_schema_digest("nope:nope")


# -- parity (staged: undeclared faces skip, declared faces must match) ---------


def test_parity_all_undeclared_is_consistent():
    verdict = check_envelope_parity({"assignment": None, "backend": None})
    assert verdict == {"consistent": True, "mismatches": []}


def test_parity_accepts_v2_int_and_token():
    verdict = check_envelope_parity(
        {"assignment": 2, "backend": f"{ENVELOPE_PROTOCOL}:v2", "validator": ENVELOPE_VERSION}
    )
    assert verdict["consistent"]


def test_parity_rejects_mismatched_declared_face():
    verdict = check_envelope_parity({"assignment": 2, "backend": 1, "validator": 2})
    assert not verdict["consistent"]
    assert verdict["mismatches"] == [{"face": "backend", "version": 1}]


# -- assignment injection face --------------------------------------------------


def test_build_assignment_envelope_carries_inline_schema_and_digest():
    declaration = build_assignment_envelope("prism:final", {"task_id": "T-042"})
    assert declaration["protocol"] == ENVELOPE_PROTOCOL
    assert declaration["version"] == ENVELOPE_VERSION
    assert declaration["token"] == "tracks-envelope:v2"
    assert declaration["kind"] == "prism:final"
    assert declaration["payload_schema"]["review_summary"]["max_chars"] == REVIEW_SUMMARY_MAX
    assert declaration["payload_schema"]["findings"]["items"]["id"] == {"required": True}
    assert declaration["schema_digest"] == envelope_schema_digest("prism:final")
    assert declaration["task_id"] == "T-042"


def test_build_assignment_envelope_unknown_kind_fails_closed():
    with pytest.raises(EnvelopeFormatError) as exc:
        build_assignment_envelope("nope:nope")
    assert exc.value.kind == "unknown_kind"
