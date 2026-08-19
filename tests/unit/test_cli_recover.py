"""B32 (#32): trac recover CLI gate tests.

Tests the fail-closed gates in _recover_gate and the happy path that
appends human.recover.  Modeled after test_cli_return_escalation.py's
fixture pattern (store.append + cmd_* call + capsys assertion).
"""

from tracks.cli.main import cmd_recover
from tracks.store import Store


def _seed_post_rollback(store, run_id, version="v0.5"):
    """Minimal event log that leaves a run at M-DESIGN/DRAFT after a
    stub_gap rollback from M-IMPL (the B32 recovery scenario)."""
    store.append(run_id, version, "story.requested", {"raw_chars": 1})
    store.append(run_id, version, "stage.entered", {"stage": "M-IMPL"})
    store.append(run_id, version, "stage.rolled_back", {
        "from_stage": "M-IMPL", "to_stage": "M-DESIGN", "reason": "stub_gap",
    })


def _recovers(store, run_id):
    return [e for e in store.events(run_id) if e.type == "human.recover"]


def _setup(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    store = Store(repo / ".tracks")
    return store, repo, "RUN"


# -- Happy path ----------------------------------------------------------------


def test_recover_happy_path(tmp_path, capsys):
    """Post-rollback M-DESIGN/DRAFT -> cmd_recover -> human.recover appended."""
    store, repo, run_id = _setup(tmp_path)
    _seed_post_rollback(store, run_id)

    rc = cmd_recover(repo, "--reason", "mis-typed stub_gap; task graph must re-decompose")
    out, err = capsys.readouterr()
    events = list(store.events(run_id))
    store.close()

    assert rc == 0, f"stderr={err!r}"
    assert err == ""
    assert "recover requested: -> M-IMPL" in out

    recovers = [e for e in events if e.type == "human.recover"]
    assert len(recovers) == 1
    assert recovers[0].payload["reason"] == "mis-typed stub_gap; task graph must re-decompose"
    assert recovers[0].payload["to_stage"] == "M-IMPL"


# -- Fail-closed gates ---------------------------------------------------------


def test_recover_no_active_run(tmp_path, capsys):
    """No active run -> error, no event."""
    store, repo, run_id = _setup(tmp_path)
    store.close()

    rc = cmd_recover(repo, "--reason", "x")
    out, err = capsys.readouterr()
    assert rc == 1
    assert "no active run" in err


def test_recover_wrong_stage(tmp_path, capsys):
    """Stage is not M-DESIGN -> rejected."""
    store, repo, run_id = _setup(tmp_path)
    store.append(run_id, "v0.5", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.5", "stage.entered", {"stage": "M-STORY"})

    rc = cmd_recover(repo, "--reason", "x")
    out, err = capsys.readouterr()
    assert rc == 1
    assert "M-DESIGN" in err
    recovers = _recovers(store, run_id)
    store.close()

    assert recovers == []


def test_recover_wrong_substate(tmp_path, capsys):
    """Substate is not DRAFT -> rejected (e.g. after human.recover itself)."""
    store, repo, run_id = _setup(tmp_path)
    _seed_post_rollback(store, run_id)
    # Enter a non-DRAFT state: manually append a human.recover (which sets
    # RECOVER_PENDING) to simulate a partially-applied recovery attempt.
    store.append(run_id, "v0.5", "human.recover", {"reason": "x", "to_stage": "M-IMPL"})

    rc = cmd_recover(repo, "--reason", "x")
    out, err = capsys.readouterr()
    assert rc == 1
    recovers = _recovers(store, run_id)
    store.close()
    assert "M-DESIGN" in err

    # Only one human.recover (the one we seeded), no new one.
    assert len(recovers) == 1


def test_recover_last_rollback_not_from_m_impl(tmp_path, capsys):
    """Last rolled_back did not come from M-IMPL -> rejected."""
    store, repo, run_id = _setup(tmp_path)
    store.append(run_id, "v0.5", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.5", "stage.entered", {"stage": "M-STORY"})
    store.append(run_id, "v0.5", "stage.rolled_back", {
        "from_stage": "M-STORY", "to_stage": "M-STORY", "reason": "human_return",
    })

    rc = cmd_recover(repo, "--reason", "x")
    out, err = capsys.readouterr()
    assert rc == 1
    store.close()
    assert "M-IMPL" in err


def test_recover_new_dispatch_after_rollback(tmp_path, capsys):
    """dispatch_agent after the rolled_back -> rejected (quiescence not met)."""
    store, repo, run_id = _setup(tmp_path)
    _seed_post_rollback(store, run_id)
    store.append(run_id, "v0.5", "command.issued", {
        "command": {"kind": "dispatch_agent", "params": {"role": "archer", "substate": "DRAFT"}, "command_id": "C1"},
    })

    rc = cmd_recover(repo, "--reason", "x")
    out, err = capsys.readouterr()
    assert rc == 1
    store.close()
    assert "dispatch_agent" in err


def test_recover_new_stage_entered_after_rollback(tmp_path, capsys):
    """stage.entered after the rolled_back -> rejected (run already moved on)."""
    store, repo, run_id = _setup(tmp_path)
    _seed_post_rollback(store, run_id)
    store.append(run_id, "v0.5", "stage.entered", {"stage": "M-DESIGN"})

    rc = cmd_recover(repo, "--reason", "x")
    out, err = capsys.readouterr()
    assert rc == 1
    store.close()
    assert "stage entered/exited after the rollback" in err


def test_recover_no_rolled_back_at_all(tmp_path, capsys):
    """No stage.rolled_back event exists -> rejected."""
    store, repo, run_id = _setup(tmp_path)
    store.append(run_id, "v0.5", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.5", "stage.entered", {"stage": "M-DESIGN"})

    rc = cmd_recover(repo, "--reason", "x")
    out, err = capsys.readouterr()
    assert rc == 1
    store.close()
    assert "stage.rolled_back" in err


# -- Argument validation -------------------------------------------------------


def test_recover_missing_reason(tmp_path, capsys):
    """Missing --reason -> usage error, no event appended."""
    store, repo, run_id = _setup(tmp_path)
    _seed_post_rollback(store, run_id)

    rc = cmd_recover(repo)
    out, err = capsys.readouterr()
    assert rc == 1
    recovers = _recovers(store, run_id)
    store.close()
    assert "usage: trac recover" in err
    assert recovers == []
