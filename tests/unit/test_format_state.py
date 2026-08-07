"""_format_state: shared one-line state format (Fix 3: escalation visibility)."""
from tracks.cli.main import _format_state
from tracks.kernel.machine import State


def _state(**kw) -> State:
    s = State()
    s.stage = kw.pop("stage", "M-STORY")
    s.substate = kw.pop("substate", "DRAFT")
    s.status = kw.pop("status", "active")
    s.awaiting = kw.pop("awaiting", None)
    s.last_failure = kw.pop("last_failure", None)
    s.current_attempt = kw.pop("current_attempt", 0)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_basic_format_no_escalation():
    s = _state(stage="M-SPEC", substate="DRAFT", status="active")
    line = _format_state(s)
    assert line == "stage=M-SPEC substate=DRAFT status=active awaiting=-"


def test_escalation_appends_attempts_and_reason():
    s = _state(
        status="awaiting_human", awaiting="escalation", current_attempt=3,
        last_failure={"check": "template", "reason": "missing section"},
    )
    line = _format_state(s)
    assert "awaiting=escalation" in line
    assert "attempts=3" in line
    assert "reason=[template] missing section" in line


def test_escalation_without_reason_shows_check_only():
    s = _state(
        status="awaiting_human", awaiting="escalation", current_attempt=3,
        last_failure={"check": "over_reach", "reason": None},
    )
    line = _format_state(s)
    assert "attempts=3" in line
    assert "reason=[over_reach]" in line


def test_escalation_multiline_reason_truncated_to_first_line():
    s = _state(
        status="awaiting_human", awaiting="escalation", current_attempt=3,
        last_failure={"check": "agent_error", "reason": "line one\nline two"},
    )
    line = _format_state(s)
    assert "attempts=3" in line
    assert "line one" in line
    assert "line two" not in line


def test_escalation_no_last_failure_still_shows_attempts():
    s = _state(
        status="awaiting_human", awaiting="escalation", current_attempt=3,
        last_failure=None,
    )
    line = _format_state(s)
    assert "awaiting=escalation" in line
    assert "attempts=3" in line
    assert "reason=" not in line


def test_non_escalation_awaiting_has_no_reason():
    s = _state(status="awaiting_human", awaiting="review", current_attempt=1,
               last_failure={"check": "x", "reason": "y"})
    line = _format_state(s)
    assert "reason=" not in line
    assert "attempts=" not in line
