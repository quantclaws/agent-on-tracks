"""Behavior coverage for the ``trac`` Human gate command face.

Targets retry gating, approve (normal preview digest + SM-01.13 rollback +
FR-0248 hotfix gap), return (closed target sets, irreversible-op confirm),
abandon, and recover (B32 quiescent post-rollback gate) — gate_cmd error
windows and event side effects.
"""

from __future__ import annotations

from types import SimpleNamespace

from tracks.cli import gate_cmd
from tracks.cli.gate_cmd import (
    _approval_gate,
    _approve_rollback_target,
    _escalation_return_targets,
    _parse_approve_args,
    _parse_return_args,
    _recover_gate,
    _retry_gate_error,
    _return_gate,
    _universal_return_targets,
    cmd_abandon,
    cmd_approve,
    cmd_recover,
    cmd_retry,
    cmd_return,
)
from tracks.store import Store


def _setup(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo, Store(repo / ".tracks")


def _seed(store, run_id, version="v0.1", stage="M-STORY"):
    store.append(run_id, version, "story.requested", {"raw_chars": 1})
    store.append(run_id, version, "stage.entered", {"stage": stage})


def _seed_rollback(store, run_id, stage, check):
    _seed(store, run_id, stage=stage)
    store.append(run_id, "v0.1", "verdict.failed", {"check": check, "attempt": 1})


# ---------------------------------------------------------------------------
# _retry_gate_error / cmd_retry
# ---------------------------------------------------------------------------


def test_retry_gate_error_clear_evidence_windows():
    clean = SimpleNamespace(awaiting=None, status="active")
    assert _retry_gate_error(clean, clear_evidence=True) is None
    escalation = SimpleNamespace(awaiting="escalation", status="awaiting_human")
    assert _retry_gate_error(escalation, clear_evidence=True) is None
    blocked = SimpleNamespace(awaiting="rollback", status="awaiting_human")
    msg = _retry_gate_error(blocked, clear_evidence=True)
    assert "retry --clear-evidence requires escalation" in msg
    assert "awaiting=rollback" in msg
    ordinary = _retry_gate_error(
        SimpleNamespace(awaiting=None, status="active", stage="M-STORY"), clear_evidence=False
    )
    assert ordinary == "run not awaiting escalation (awaiting=nothing)"
    lineage = SimpleNamespace(
        awaiting="rollback", status="awaiting_human", stage="M-IMPL", last_failure={"check": "lineage"}
    )
    assert _retry_gate_error(lineage, clear_evidence=False) is None


def test_cmd_retry_clear_evidence_success_on_clean_active(tmp_path, capsys):
    repo, store = _setup(tmp_path)
    _seed(store, "RUN")
    rc = cmd_retry(repo, "--clear-evidence", "--actor", "T")
    out = capsys.readouterr().out
    retries = [e for e in store.events("RUN") if e.type == "human.retry"]
    store.close()
    assert rc == 0
    assert "human.retry event appended" in out
    assert retries[0].payload == {"actor": "T", "clear_evidence": True}


def test_cmd_retry_rejected_by_gate(tmp_path, capsys):
    repo, store = _setup(tmp_path)
    _seed(store, "RUN", stage="M-TEST")
    store.append("RUN", "v0.1", "verdict.failed", {"check": "ac_gap", "attempt": 1})
    assert cmd_retry(repo) == 1
    assert "run not awaiting escalation" in capsys.readouterr().err
    assert not [e for e in store.events("RUN") if e.type == "human.retry"]
    store.close()


def test_cmd_retry_parse_error_and_no_active_run(tmp_path, capsys):
    repo, store = _setup(tmp_path)
    assert cmd_retry(repo, "--bogus") == 1
    assert "usage: trac retry" in capsys.readouterr().err
    assert cmd_retry(repo, "--actor") == 1
    assert "usage: trac retry" in capsys.readouterr().err
    assert cmd_retry(repo, "--clear-evidence") == 1
    assert "no active run" in capsys.readouterr().err
    store.close()


# ---------------------------------------------------------------------------
# approve helpers
# ---------------------------------------------------------------------------


def test_approval_gate_variants():
    rollback = SimpleNamespace(awaiting="rollback", stage="M-TEST")
    state, err = _approval_gate(SimpleNamespace(state=lambda rid: rollback), "R")
    assert state is rollback and err is None

    wrong = SimpleNamespace(awaiting=None, stage="M-STORY")
    state, err = _approval_gate(SimpleNamespace(state=lambda rid: wrong), "R")
    assert state is None
    assert "run not awaiting approval (stage=M-STORY awaiting=nothing)" in err

    right = SimpleNamespace(awaiting="approval", stage="M-REQ-APPROVAL")
    state, err = _approval_gate(SimpleNamespace(state=lambda rid: right), "R")
    assert state is right and err is None


def test_approve_rollback_target_resolution():
    m_test = SimpleNamespace(stage="M-TEST", return_target="M-ACC")
    assert _approve_rollback_target(m_test, None) == ("M-ACC", None)
    assert _approve_rollback_target(m_test, "M-SPEC")[1] == "--to is only valid for M-IMPL rollback approvals"

    m_impl = SimpleNamespace(stage="M-IMPL", return_target=None)
    assert _approve_rollback_target(m_impl, None)[1].startswith("rollback target not set")
    assert _approve_rollback_target(m_impl, "M-DOWN")[1].startswith("invalid --to target")
    assert _approve_rollback_target(m_impl, "M-SPEC") == ("M-SPEC", None)
    preset = SimpleNamespace(stage="M-IMPL", return_target="M-DESIGN")
    assert _approve_rollback_target(preset, "M-ACC") == ("M-ACC", None)


def test_parse_approve_args():
    assert _parse_approve_args(["--actor", "A", "--to", "M-SPEC"]) == ("A", "M-SPEC", None)
    assert _parse_approve_args([]) == (None, None, None)
    assert _parse_approve_args(["--bogus"])[2].startswith("usage:")
    assert _parse_approve_args(["--to"])[2].startswith("usage:")


# ---------------------------------------------------------------------------
# cmd_approve
# ---------------------------------------------------------------------------


def test_cmd_approve_parse_error_and_no_run(tmp_path, capsys):
    repo, store = _setup(tmp_path)
    assert cmd_approve(repo, "--bogus") == 1
    assert "usage: trac approve" in capsys.readouterr().err
    assert cmd_approve(repo) == 1
    assert "no active run" in capsys.readouterr().err
    store.close()


def test_cmd_approve_gate_error(tmp_path, capsys):
    repo, store = _setup(tmp_path)
    _seed(store, "RUN")
    assert cmd_approve(repo) == 1
    assert "run not awaiting approval" in capsys.readouterr().err
    store.close()


def test_cmd_approve_hotfix_gap_exit(tmp_path, capsys):
    repo, store = _setup(tmp_path)
    store.append("RUN", "v0.1", "hotfix.requested", {"issue": 7, "scenario": "b"})
    store.append("RUN", "v0.1", "stage.entered", {"stage": "M-TEST"})
    store.append("RUN", "v0.1", "verdict.failed", {"check": "ac_gap", "attempt": 1})
    rc = cmd_approve(repo, "--actor", "T")
    out = capsys.readouterr().out
    types = [e.type for e in store.events("RUN")]
    store.close()
    assert rc == 0
    assert "approved ac_gap -> backlog/new feature for issue 7" in out
    assert types[-2:] == ["backlog.recorded", "run.completed"]


def test_cmd_approve_m_test_rollback_uses_preset_target(tmp_path, capsys):
    repo, store = _setup(tmp_path)
    _seed_rollback(store, "RUN", "M-TEST", "ac_gap")
    assert store.state("RUN").awaiting == "rollback"
    rc = cmd_approve(repo, "--actor", "T")
    out = capsys.readouterr().out
    approvals = [e for e in store.events("RUN") if e.type == "human.approval"]
    store.close()
    assert rc == 0
    assert "approved rollback to M-ACC" in out
    assert approvals[-1].payload["to_stage"] == "M-ACC"
    assert approvals[-1].payload["actor"] == "T"


def test_cmd_approve_m_impl_rollback_requires_target_and_validates(tmp_path, capsys):
    repo, store = _setup(tmp_path)
    _seed_rollback(store, "RUN", "M-IMPL", "lineage")
    state = store.state("RUN")
    assert state.awaiting == "rollback"
    assert cmd_approve(repo) == 1
    assert "rollback target not set" in capsys.readouterr().err
    assert cmd_approve(repo, "--to", "M-NOPE") == 1
    assert "invalid --to target" in capsys.readouterr().err
    rc = cmd_approve(repo, "--to", "M-DESIGN", "--actor", "T")
    out = capsys.readouterr().out
    store.close()
    assert rc == 0
    assert "approved rollback to M-DESIGN" in out


def test_cmd_approve_preview_digest_mismatch(tmp_path, capsys, monkeypatch):
    repo, store = _setup(tmp_path)
    _seed(store, "RUN", stage="M-REQ-APPROVAL")
    store.append("RUN", "v0.1", "preview.generated", {"digest": "OLD", "summary": "s"})
    monkeypatch.setattr(gate_cmd, "revision_digest", lambda vdir: "NEW")
    monkeypatch.setattr(gate_cmd, "baseline_summary", lambda vdir: "summary")
    rc = cmd_approve(repo)
    err = capsys.readouterr().err
    previews = [e for e in store.events("RUN") if e.type == "preview.generated"]
    store.close()
    assert rc == 1
    assert "baseline changed since preview" in err
    assert previews[-1].payload["digest"] == "NEW"


def test_cmd_approve_success_appends_digest(tmp_path, capsys, monkeypatch):
    repo, store = _setup(tmp_path)
    _seed(store, "RUN", stage="M-REQ-APPROVAL")
    store.append("RUN", "v0.1", "preview.generated", {"digest": "DIGEST", "summary": "s"})
    monkeypatch.setattr(gate_cmd, "revision_digest", lambda vdir: "DIGEST")
    rc = cmd_approve(repo, "--actor", "T")
    out = capsys.readouterr().out
    approvals = [e for e in store.events("RUN") if e.type == "human.approval"]
    store.close()
    assert rc == 0
    assert "approved DIGEST" in out
    assert approvals[-1].payload["digest"] == "DIGEST"
    assert approvals[-1].payload["actor"] == "T"


# ---------------------------------------------------------------------------
# return helpers / cmd_return
# ---------------------------------------------------------------------------


def test_universal_and_escalation_return_targets():
    assert _universal_return_targets("M-IMPL")[0] == "M-STORY"
    assert "M-IMPL" not in _universal_return_targets("M-IMPL")
    assert _universal_return_targets("NOPE") == ()
    assert _escalation_return_targets("M-DESIGN") == ("M-STORY", "M-SPEC", "M-ACC", "M-DESIGN")
    assert _escalation_return_targets("M-TEST") == ("M-STORY", "M-SPEC", "M-ACC", "M-DESIGN")
    assert _escalation_return_targets("M-REQ-APPROVAL") == ()


def test_return_gate_branches():
    approval = SimpleNamespace(stage="M-REQ-APPROVAL", awaiting="approval", substate="PREVIEW")
    state, allowed, err = _return_gate(SimpleNamespace(state=lambda rid: approval), "R")
    assert (state, err) == (approval, None)
    assert allowed == ("M-STORY", "M-SPEC", "M-ACC")

    needs_attention = SimpleNamespace(stage="M-IMPL", substate="NEEDS_ATTENTION", awaiting=None)
    _, allowed, err = _return_gate(SimpleNamespace(state=lambda rid: needs_attention), "R")
    assert err is None and allowed == _universal_return_targets("M-IMPL")

    author = SimpleNamespace(stage="M-SPEC", substate="DRAFT", awaiting="escalation")
    _, allowed, err = _return_gate(SimpleNamespace(state=lambda rid: author), "R")
    assert (allowed, err) == (("M-STORY", "M-SPEC"), None)

    release = SimpleNamespace(stage="M-PUBLISH", substate="X", awaiting="escalation")
    _, allowed, err = _return_gate(SimpleNamespace(state=lambda rid: release), "R")
    assert err is None and allowed == _universal_return_targets("M-PUBLISH")

    dead_end = SimpleNamespace(stage="M-NOPE", substate="X", awaiting="escalation")
    _, allowed, err = _return_gate(SimpleNamespace(state=lambda rid: dead_end), "R")
    assert allowed == () and "has no return targets" in err

    idle = SimpleNamespace(stage="M-STORY", substate="DRAFT", awaiting=None)
    _, allowed, err = _return_gate(SimpleNamespace(state=lambda rid: idle), "R")
    assert allowed == () and "run not awaiting approval or escalation" in err


def test_parse_return_args():
    opts, err = _parse_return_args(["--to", "M-STORY", "--reason", "r", "--confirm"])
    assert err is None
    assert opts == {"--to": "M-STORY", "--reason": "r", "--confirm": True}
    assert _parse_return_args(["--to", "M-STORY"])[1] == gate_cmd._RETURN_USAGE
    assert _parse_return_args(["--to"])[1] == gate_cmd._RETURN_USAGE
    assert _parse_return_args(["--to", "A", "--to", "B", "--reason", "r"])[1] == gate_cmd._RETURN_USAGE
    assert _parse_return_args([])[1] == gate_cmd._RETURN_USAGE


def test_cmd_return_usage_no_run_and_gate_errors(tmp_path, capsys):
    repo, store = _setup(tmp_path)
    assert cmd_return(repo, "--to", "M-STORY") == 1
    assert "usage: trac return" in capsys.readouterr().err
    assert cmd_return(repo, "--to", "M-STORY", "--reason", "r") == 1
    assert "no active run" in capsys.readouterr().err
    _seed(store, "RUN")
    assert cmd_return(repo, "--to", "M-STORY", "--reason", "r") == 1
    assert "run not awaiting approval or escalation" in capsys.readouterr().err
    store.close()


def test_cmd_return_invalid_target_lists_allowed(tmp_path, capsys):
    repo, store = _setup(tmp_path)
    _seed(store, "RUN", stage="M-REQ-APPROVAL")
    store.append("RUN", "v0.1", "preview.generated", {"digest": "D"})
    assert cmd_return(repo, "--to", "M-DESIGN", "--reason", "r") == 1
    assert "invalid --to M-DESIGN: must be one of M-STORY|M-SPEC|M-ACC" in capsys.readouterr().err
    store.close()


def test_cmd_return_ok_and_irreversible_confirm(tmp_path, capsys, monkeypatch):
    repo, store = _setup(tmp_path)
    _seed(store, "RUN", stage="M-REQ-APPROVAL")
    store.append("RUN", "v0.1", "preview.generated", {"digest": "D"})
    monkeypatch.setattr(gate_cmd, "_establish_escape_barrier", lambda *a: None)
    monkeypatch.setattr(gate_cmd, "_stale_downstream_evidence", lambda *a: None)
    monkeypatch.setattr(
        gate_cmd,
        "_already_executed_ops",
        lambda store, run_id: [{"operation_kind": "publish", "target": "pypi", "type": "publish"}],
    )
    assert cmd_return(repo, "--to", "M-STORY", "--reason", "r") == 1
    out = capsys.readouterr().out
    assert "already_executed operations this return would cross:" in out
    assert "publish pypi" in out
    rc = cmd_return(repo, "--to", "M-STORY", "--reason", "r", "--confirm")
    out = capsys.readouterr().out
    returns = [e for e in store.events("RUN") if e.type == "human.return"]
    store.close()
    assert rc == 0
    assert "returned to M-STORY" in out
    assert returns[-1].payload == {
        "actor": "Human",
        "from": "M-REQ-APPROVAL",
        "to": "M-STORY",
        "to_stage": "M-STORY",
        "reason": "r",
    }


# ---------------------------------------------------------------------------
# cmd_abandon
# ---------------------------------------------------------------------------


def test_cmd_abandon_parse_errors_and_success(tmp_path, capsys):
    repo, store = _setup(tmp_path)
    assert cmd_abandon(repo) == 1
    assert "usage: trac abandon --reason" in capsys.readouterr().err
    assert cmd_abandon(repo, "--reason") == 1
    assert "usage: trac abandon --reason" in capsys.readouterr().err
    assert cmd_abandon(repo, "--bogus", "x") == 1
    assert "usage: trac abandon --reason" in capsys.readouterr().err
    assert cmd_abandon(repo, "--reason", "a", "--reason", "b") == 1
    assert "usage: trac abandon --reason" in capsys.readouterr().err
    assert cmd_abandon(repo, "--reason", "stop") == 1
    assert "no active run" in capsys.readouterr().err
    _seed(store, "RUN")
    rc = cmd_abandon(repo, "--reason", "stop")
    out = capsys.readouterr().out
    completed = [e for e in store.events("RUN") if e.type == "run.completed"]
    store.close()
    assert rc == 0
    assert "terminal=cancelled reason=stop" in out
    assert completed[-1].payload == {"terminal_state": "cancelled", "reason": "stop"}


# ---------------------------------------------------------------------------
# recover
# ---------------------------------------------------------------------------


class _FakeStore:
    def __init__(self, state, events):
        self._state = state
        self._events = events

    def state(self, run_id):
        return self._state

    def events(self, run_id):
        return self._events


def _ev(type, payload=None):
    return SimpleNamespace(type=type, payload=payload or {})


def test_recover_gate_rejections():
    wrong = SimpleNamespace(stage="M-TEST", substate="DISPATCH")
    _, err = _recover_gate(_FakeStore(wrong, []), "R")
    assert "recover requires M-DESIGN/DRAFT" in err

    quiescent = SimpleNamespace(stage="M-DESIGN", substate="DRAFT")
    _, err = _recover_gate(_FakeStore(quiescent, []), "R")
    assert err == "recover requires a prior stage.rolled_back event"

    wrong_dir = [_ev("stage.rolled_back", {"from_stage": "M-TEST", "to_stage": "M-DESIGN"})]
    _, err = _recover_gate(_FakeStore(quiescent, wrong_dir), "R")
    assert "recover only after a stage.rolled_back from M-IMPL to M-DESIGN" in err

    rollback = _ev("stage.rolled_back", {"from_stage": "M-IMPL", "to_stage": "M-DESIGN"})
    reentered = [rollback, _ev("stage.entered", {"stage": "M-DESIGN"})]
    _, err = _recover_gate(_FakeStore(quiescent, reentered), "R")
    assert "another stage entered/exited after the rollback" in err

    dispatched = [
        rollback,
        _ev("command.issued", {"command": {"kind": "dispatch_agent", "params": {}}}),
    ]
    _, err = _recover_gate(_FakeStore(quiescent, dispatched), "R")
    assert "new dispatch_agent work issued after the rollback" in err

    tolerated = [rollback, _ev("command.issued", {"command": {"kind": "collect_tests"}})]
    state, err = _recover_gate(_FakeStore(quiescent, tolerated), "R")
    assert (state, err) == (quiescent, None)


def test_cmd_recover_parse_and_gate_errors(tmp_path, capsys):
    repo, store = _setup(tmp_path)
    assert cmd_recover(repo) == 1
    assert "usage: trac recover --reason" in capsys.readouterr().err
    assert cmd_recover(repo, "--reason") == 1
    assert "usage: trac recover --reason" in capsys.readouterr().err
    assert cmd_recover(repo, "--reason", "r") == 1
    assert "no active run" in capsys.readouterr().err
    _seed(store, "RUN", stage="M-TEST")
    assert cmd_recover(repo, "--reason", "r") == 1
    assert "recover requires M-DESIGN/DRAFT" in capsys.readouterr().err
    store.close()


def test_cmd_recover_success(tmp_path, capsys):
    repo, store = _setup(tmp_path)
    _seed(store, "RUN", stage="M-IMPL")
    store.append(
        "RUN", "v0.1", "stage.rolled_back", {"from_stage": "M-IMPL", "to_stage": "M-DESIGN"}
    )
    state = store.state("RUN")
    assert (state.stage, state.substate) == ("M-DESIGN", "DRAFT")
    rc = cmd_recover(repo, "--reason", "retry design")
    out = capsys.readouterr().out
    recovers = [e for e in store.events("RUN") if e.type == "human.recover"]
    store.close()
    assert rc == 0
    assert "recover requested: -> M-IMPL" in out
    assert recovers[-1].payload == {"reason": "retry design", "to_stage": "M-IMPL"}
