"""T-002 RED: HTTP command-guard schema mirror for the v0.11 kind set.

Devon-owned unit RED for the PLANNING round 1 scope addition (DIAGNOSE
plan_defect seq 10722 承接): ``tracks/server/guard.py`` must stay the same
contract source as the service command closed set — every new endpoint
funnels through ``_mutate -> guard.validate_command_payload``
(api_command.py:282), so a kind the service accepts but the guard cannot
schema is a KeyError crash on the HTTP face, not a validation outcome:

- the five v0.11 kinds (submit_intake / decide_intake / dispose_backlog /
  record_triage / record_review) carry ``_PARAM_TYPES`` entries with their
  optional keys (content? / issue? / origin_entry? / confirm? / to?) and
  ``_ENUM_PARAMS`` mirrors of the closed enums (§1b #14-18) — a legal
  payload passes, an unknown enum value answers ``validation_failed``;
- ``submit_clarification`` accepts ``anchor_line`` alongside
  ``thread_token`` (§1n.3): the XOR is validated service-side, the guard
  tolerates either key (both null-tolerant, the int line form included —
  a JSON body carries the line number as an integer).

RED discipline: every failing node guards the absent schema into a real
``AssertionError`` (KeyError/TypeError/GuardRejection guards — no stub
tokens, no assembly errors, no ``pytest.fail``). The guard module itself
is imported through the real package; nothing mocks the system under test.

AC: FR-0338/FR-0339/FR-0340/FR-0342 — TRACKS-TRACE IF-INTAKE-001 /
IF-BACKLOG-001 / IF-MSTART-001 / IF-GATEUI-001 (the HTTP admission face
of every kind this task's storage/acceptance halves accept).
"""

from __future__ import annotations

from tracks.server import guard

_ACTOR_OK = "human"

# Legal payloads per §1b #14-18 (minimal required keys + the optional keys
# each kind documents; None stands for "key present, value null").
_LEGAL_PAYLOADS = {
    "submit_intake": {
        "project_id": "proj-1",
        "source": "text",
        "content": "统一入口的一段输入",
        "issue": None,
        "origin_entry": None,
    },
    "decide_intake": {
        "intake_id": "01INTAKEINTAKEINTAKEINTAKEINTAK00",
        "action": "develop",
        "confirm": None,
    },
    "dispose_backlog": {
        "project_id": "proj-1",
        "entry_id": "01ENTRYENTRYENTRYENTRYENTRY0000",
        "disposition": "promoted",
        "to": "v0.3",
    },
    "record_triage": {"run_id": "run-tri", "decision": "go"},
    "record_review": {"run_id": "run-rev", "action": "comment"},
}

# One illegal enum value per kind (§1b params columns) — each must answer
# validation_failed from the guard, exactly like the closed sets the
# service-side preflights enforce.
_ILLEGAL_ENUM_PAYLOADS = {
    "submit_intake": {"project_id": "proj-1", "source": "gossip"},
    "decide_intake": {"intake_id": "01INTAKEINTAKEINTAKEINTAKEINTAK00", "action": "ship"},
    "dispose_backlog": {
        "project_id": "proj-1",
        "entry_id": "01ENTRYENTRYENTRYENTRYENTRY0000",
        "disposition": "lost",
    },
    "record_triage": {"run_id": "run-tri", "decision": "maybe"},
    "record_review": {"run_id": "run-rev", "action": "ship"},
}


def _validate(kind: str, params: dict) -> guard.GuardRejection | None:
    """Run one guard validation; mechanical schema absence (a kind the
    registry cannot schema yet) becomes a real AssertionError, never an
    unclassified KeyError/TypeError from inside the guard."""
    try:
        guard.validate_command_payload(kind, params)
    except guard.GuardRejection as rejection:
        return rejection
    except (KeyError, TypeError) as exc:
        raise AssertionError(
            f"guard schema must mirror the service closed set (PLANNING round 1,"
            f" tracks/server/guard.py): validating {kind!r} crashed with"
            f" {type(exc).__name__}: {exc} — the HTTP face funnels every new"
            " endpoint through _mutate -> validate_command_payload"
            " (api_command.py:282), so a missing schema entry is a crash, not"
            " a validation outcome"
        ) from None
    return None


# AC-FR0338-01@v0.11 TRACKS-TRACE IF-INTAKE-001 guard mirrors the new kinds
# AC-FR0340-02@v0.11 TRACKS-TRACE IF-BACKLOG-001 dispose_backlog passes guard
# AC-FR0342-01@v0.11 TRACKS-TRACE IF-GATEUI-001 gate kinds pass guard
def test_guard_schemas_five_new_kinds_legal_payloads():
    for kind, payload in _LEGAL_PAYLOADS.items():
        rejection = _validate(kind, dict(payload))
        assert rejection is None, (
            f"a legal {kind} payload must pass the guard schema (§1b #14-18);"
            f" rejected with {rejection.reason}: {rejection.detail}"
        )
    # the human partition holds for the five new kinds on the guard face too
    for kind in _LEGAL_PAYLOADS:
        guard.check_actor_class(kind, _ACTOR_OK)  # must not raise


# AC-FR0338-01@v0.11 TRACKS-TRACE IF-INTAKE-001 closed enums on the guard face
def test_guard_enums_reject_unknown_values_for_new_kinds():
    for kind, payload in _ILLEGAL_ENUM_PAYLOADS.items():
        rejection = _validate(kind, dict(payload))
        assert rejection is not None and rejection.reason == "validation_failed", (
            f"an unknown enum value for {kind} must answer validation_failed from"
            f" the guard (the closed enums mirror §1b); got"
            f" {'acceptance (no rejection)' if rejection is None else rejection.reason}"
        )


# AC-FR0344-01@v0.11 TRACKS-TRACE IF-DOCSWRITE-001 anchor_line passes the guard
def test_submit_clarification_anchor_line_and_thread_token_pass_guard():
    payloads = (
        # the inline-comment form: a JSON body carries the line as an integer
        {"run_id": "run-rev", "doc": "story", "body": "inline comment", "anchor_line": 2},
        # the planning-letter (str, null) tolerance for the same key
        {"run_id": "run-rev", "doc": "story", "body": "inline comment", "anchor_line": "2"},
        # the existing reply form keeps passing untouched
        {"run_id": "run-rev", "doc": "story", "body": "a reply", "thread_token": "T-9"},
        # the XOR itself is service-side (§1n.3): neither key present must not
        # be a guard rejection — the service answers the 422
        {"run_id": "run-rev", "doc": "story", "body": "x"},
    )
    for payload in payloads:
        rejection = _validate("submit_clarification", dict(payload))
        assert rejection is None, (
            "submit_clarification guard schema must tolerate anchor_line and"
            " thread_token as optional keys (XOR validated in the service,"
            f" PLANNING round 1 item a); rejected {payload!r} with"
            f" {rejection.reason}: {rejection.detail}"
        )
