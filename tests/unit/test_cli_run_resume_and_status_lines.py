"""CLI surface pins: ``trac run --resume``, status attention, publish=.

- AC-FR0283-02 / interfaces §2b: ``trac run --resume`` is the explicit
  resume alias and parses as a no-op (an active run already reconciles on
  the next drive).
- interfaces §287: attention areas beyond ci_readback surface as
  ``needs_attention=<area>:<reason>`` (ci_readback keeps its legacy shape)
  and clear once a later recovery event lands.
- AC-FR0275-01: ``publish=planned|executing|done|reconciled_skip|blocked``
  renders from the state.publish_status projection.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_run_resume_is_a_parsed_noop() -> None:
    from tracks.cli.main import _parse_run_args

    assert _parse_run_args(()) == (None, None)
    assert _parse_run_args(("--resume",)) == (None, None)
    assert _parse_run_args(("--resume", "--max-dispatches", "3")) == (None, 3)
    assert _parse_run_args(
        ("--assignment-overlay", "o.json", "--resume")
    ) == ("o.json", None)
    with pytest.raises(ValueError):
        _parse_run_args(("--resume", "--bogus"))


def _event(seq: int, kind: str, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(seq=seq, type=kind, payload=payload)


def test_status_attention_renders_every_area_and_clears_on_recovery() -> None:
    from tracks.cli.main import _release_attention_lines

    events = [
        _event(1, "attention.required", {"area": "issue_creation", "reason": "missing_token"}),
        _event(2, "attention.required", {"area": "freeze", "reason": "dirty_tree"}),
        _event(3, "candidate.frozen", {"candidate_sha": "c" * 40}),
        _event(
            4,
            "attention.required",
            {"area": "ci_readback", "reason": "network_error", "next": "retry CI"},
        ),
    ]

    lines = _release_attention_lines(events)

    # ci_readback keeps the existing shape, the other areas are generalized.
    assert "needs_attention=network_error next=retry CI" in lines
    assert "needs_attention=issue_creation:missing_token" in lines
    # freeze recovered at seq 3 -> no freeze line.
    assert not any("freeze" in line for line in lines)


def test_publish_status_renders_projection_and_blocked_reason() -> None:
    from tracks.cli.main import _release_publish_lines

    def primary(status: str | None) -> SimpleNamespace:
        return SimpleNamespace(publish_status=status, status="active", terminal_state=None)

    assert _release_publish_lines([], primary("planned")) == ["publish=planned"]
    assert _release_publish_lines([], primary("executing")) == ["publish=executing"]
    assert _release_publish_lines([], primary("done")) == ["publish=done"]
    assert _release_publish_lines([], primary("reconciled_skip")) == [
        "publish=reconciled_skip"
    ]
    assert _release_publish_lines([], primary(None)) == []
    blocked = _release_publish_lines(
        [_event(9, "publish.blocked", {"reason": "agent_forbidden"})], primary("blocked")
    )
    assert blocked == ["publish=blocked reason=agent_forbidden"]
