"""Intake evaluation fence (test-plan §6.4 row 5, v0.11 Shield asset).

Drives the Scribe intake evaluation to a terminal state through the
documented outlets only: the accepted ``submit_intake`` command is evaluated
by the supervisor's intake worker under the fake agent backend
(``TRAC_AGENT_BACKEND=fake``, the same L2 contract-sim switch as run
dispatch), and this fence polls the intake read model (interfaces §2b #38)
until the row leaves the evaluating window. The fence never fabricates
report content — the fake backend's reply body is the input under test
(interfaces §1f.2); it only bounds the asynchronous wait.
"""

from __future__ import annotations

import json
import time

from tests._support.v09_web import http_get

# The SM-01 terminal states the fence may settle on (interfaces §1f.1).
_TERMINAL = ("evaluated", "failed", "decided")


def intake_row(base_url: str, cookie: str, project_id: str, intake_id: str) -> dict:
    """One read of the intake read model (§2b #38); asserts the 200 shape."""
    status, body = http_get(
        base_url, f"/api/projects/{project_id}/intakes/{intake_id}", cookies=cookie
    )
    assert status == 200, f"intake read model must resolve: {status} {body[:200]}"
    row = json.loads(body)
    assert row.get("intake_id") == intake_id, f"identity mismatch: {row!r}"
    return row


def wait_intake_terminal(
    base_url: str,
    cookie: str,
    project_id: str,
    intake_id: str,
    *,
    timeout: float = 90.0,
) -> dict:
    """Poll until the intake reaches a terminal SM-01 state; returns the row.

    The evaluation runs inside the real serve supervisor (the accepted
    command is claimed and the evaluator dispatched under the fake backend);
    the fence asserts only the contracted lifecycle outlet — no internals.
    """
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = intake_row(base_url, cookie, project_id, intake_id)
        if last.get("status") in _TERMINAL:
            return last
        time.sleep(0.3)
    raise AssertionError(
        f"intake {intake_id} never reached a terminal state within {timeout}s: {last!r}"
    )


def wait_run_awaiting(
    base_url: str,
    cookie: str,
    run_id: str,
    awaiting_types: tuple[str, ...],
    *,
    timeout: float = 120.0,
) -> dict:
    """Poll run detail until ``awaiting_gate.type`` joins ``awaiting_types``.

    The self-drive loop (interfaces §1j.3) advances the run between gates
    through supervisor ticks; the fence waits on the public run-detail
    projection (§1k.1) instead of any internal scheduler state. A response
    without the ``awaiting_gate`` key at all fails fast — the field group is
    itself the §1k contract, and its absence is precisely the undelivered
    surface (no point burning the poll budget on it).
    """
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        status, body = http_get(base_url, f"/api/runs/{run_id}", cookies=cookie)
        assert status == 200, f"run detail must resolve: {status} {body[:200]}"
        last = json.loads(body)
        if "awaiting_gate" not in last:
            raise AssertionError(
                f"run detail carries no awaiting_gate field group (§1k.1): "
                f"{sorted(last)!r}"
            )
        gate = last.get("awaiting_gate")
        if isinstance(gate, dict) and gate.get("type") in awaiting_types:
            return last
        time.sleep(0.4)
    raise AssertionError(
        f"run {run_id} never reached awaiting {awaiting_types} within {timeout}s: "
        f"awaiting_gate={last.get('awaiting_gate')!r}"
    )
