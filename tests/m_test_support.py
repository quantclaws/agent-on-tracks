"""Shared M-TEST/WRITE ``dispatch_agent`` command factories.

Not pytest-collected: the module name does not match ``test_*`` / ``*_test``
(see ``pyproject.toml`` ``testpaths``). Extracted to remove the four
near-identical ``_shield_cmd`` / ``_non_shield_cmd`` builders duplicated
across the Shield crash-attribution, test-tasks-guard, executor-preflight,
and fake-revision-marker suites (pylint R0801).

- :func:`make_m_test_dispatch_cmd` reproduces the exact ``Command`` payload
  those builders emitted: ``role`` switches Shield vs. Prism, and
  ``evidence`` (when given) is forwarded into ``params`` so FR-11 failure
  evidence drives the FakeBackend revision marker.
- :func:`make_dispatch_agent_payload` builds the ``dispatch_agent`` command
  payload *dict* nested under ``"command"`` in a ``command.issued`` event,
  shared by the M-TEST escalation fixture and the shield-assignment contract
  fixture. Only supplied optional fields are included, so each fixture keeps
  its own field set (escalation: ``attempt``/``review_round``/``command_id``;
  shield-assignment: ``stage``/``assignment``) without seeing foreign keys.
"""
from tracks.kernel.events import Command
from tracks.store import new_ulid

_UNSET = object()


def make_m_test_dispatch_cmd(role="shield", evidence=None):
    """Build a ``dispatch_agent`` Command for the M-TEST/WRITE stage.

    A fresh ``params`` dict (and nested ``assignment``) is allocated per call,
    matching the per-module literals it replaces so no command shares mutable
    state with another.
    """
    params = {
        "role": role,
        "substate": "WRITE",
        "stage": "M-TEST",
        "attempt": 1,
        "review_round": 1,
        "assignment": {
            "kind": "WRITE",
            "skills": ["tracks-discuz"],
            "docs": ["test-plan.md", "interfaces.md", "acceptance.md"],
        },
    }
    if evidence is not None:
        params["evidence"] = evidence
    return Command(kind="dispatch_agent", params=params, command_id=new_ulid())


def make_dispatch_agent_payload(
    *,
    role="shield",
    substate="WRITE",
    stage=_UNSET,
    attempt=_UNSET,
    review_round=_UNSET,
    assignment=_UNSET,
    evidence=_UNSET,
    command_id=_UNSET,
):
    """Build a ``dispatch_agent`` command payload dict as nested under
    ``"command"`` in a ``command.issued`` event.

    Optional fields are included only when supplied, so this reproduces both
    the M-TEST escalation fixture (``attempt``/``review_round``/
    ``command_id``, no ``stage``/``assignment``) and the shield-assignment
    contract fixture (``stage``/``assignment``, no ``attempt``/
    ``review_round``/``command_id``) without either side seeing foreign keys.
    """
    params = {"role": role, "substate": substate}
    if stage is not _UNSET:
        params["stage"] = stage
    if attempt is not _UNSET:
        params["attempt"] = attempt
    if review_round is not _UNSET:
        params["review_round"] = review_round
    if assignment is not _UNSET:
        params["assignment"] = assignment
    if evidence is not _UNSET:
        params["evidence"] = evidence
    payload = {"kind": "dispatch_agent", "params": params}
    if command_id is not _UNSET:
        payload["command_id"] = command_id
    return payload
