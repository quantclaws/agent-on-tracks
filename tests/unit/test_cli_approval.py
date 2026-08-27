"""CLI approval-gate guards (FR-0180, C-02/C-03): trac approve / trac return
reject illegal input WITHOUT writing events — SM-05.3a / SM-05.7a.
"""

from tests.e2e.helpers import walk_to_await_human
from tracks.baseline import revision_digest


def _events_of(event_log, *types_):
    return [e for e in event_log() if e["type"] in types_]


def test_gate_required_before_approve_or_return(trac, event_log):
    # approve/return are only legal at AWAIT_HUMAN (FR-0180)
    assert trac("init").returncode == 0
    assert trac("start", "v0.1", stdin="需求").returncode == 0
    r = trac("approve")
    assert r.returncode != 0 and "not awaiting approval" in r.stderr
    r = trac("return", "--to", "M-SPEC", "--reason", "早退")
    assert r.returncode != 0 and "not awaiting approval" in r.stderr
    assert not _events_of(event_log, "human.approval", "human.return")


def test_return_invalid_to_stage_rejected(trac, event_log):
    # SM-05.7a (C-03): closed target set — forward/self/unknown all rejected,
    # no event lands, the gate still holds.
    walk_to_await_human(trac)
    for bad in ("M-REQ-APPROVAL", "M-DESIGN", "M-START", "bogus"):
        r = trac("return", "--to", bad, "--reason", "试探")
        assert r.returncode != 0, bad
        assert f"invalid --to {bad}" in r.stderr
    r = trac("return", "--to", "M-SPEC")  # missing --reason
    assert r.returncode != 0 and "usage" in r.stderr
    assert not _events_of(event_log, "human.return", "stage.rolled_back")
    r = trac("status")
    assert r.returncode == 0 and "awaiting=approval" in r.stdout


def test_approve_digest_mismatch_rejected(host_repo, trac, event_log):
    # SM-05.3a (C-02): the trio changed under the reviewed preview — reject
    # THIS approve (no human.approval event, run not failed) and regenerate
    # the preview; the fresh preview can then be approved.
    walk_to_await_human(trac)
    vdir = host_repo / ".tracks" / "projects" / "v0.1"
    spec = vdir / "spec.md"
    spec.write_text(spec.read_text(encoding="utf-8") + "\n偷改一行\n", encoding="utf-8")
    r = trac("approve", "--actor", "Aaron")
    assert r.returncode != 0
    assert "preview regenerated" in r.stderr
    previews = _events_of(event_log, "preview.generated")
    assert len(previews) == 2
    assert previews[1]["payload"]["digest"] == revision_digest(vdir)
    assert not _events_of(event_log, "human.approval")
    r = trac("status")
    assert r.returncode == 0 and "awaiting=approval" in r.stdout
    # the regenerated preview is approvable
    r = trac("approve", "--actor", "Aaron")
    assert r.returncode == 0, r.stderr
    approvals = _events_of(event_log, "human.approval")
    assert len(approvals) == 1
    assert approvals[0]["payload"]["digest"] == previews[1]["payload"]["digest"]


# -- B57 re-fix (#73): rollback target resolution at approval time ------------


def _rollback_state(stage, return_target):
    import types

    return types.SimpleNamespace(stage=stage, return_target=return_target)


def test_approve_rollback_target_requires_choice_when_no_preset():
    """A lineage park presets no return target: the Human MUST choose; the
    guidance names the valid set and the real two-step M-IMPL re-entry path
    (recover only unlocks after the rollback has executed)."""
    from tracks.cli.main import _approve_rollback_target

    target, err = _approve_rollback_target(_rollback_state("M-IMPL", None), None)
    assert target is None
    assert err and "--to" in err and "M-ACC|M-SPEC|M-DESIGN" in err
    assert "trac recover --to M-IMPL" in err


def test_approve_rollback_target_closed_set():
    """--to is validated against exactly the kernel-preset rollback stages."""
    from tracks.cli.main import _approve_rollback_target

    for bad in ("M-IMPL", "M-STORY", "M-TEST", "M-REQ-APPROVAL", "m-design", ""):
        target, err = _approve_rollback_target(_rollback_state("M-IMPL", None), bad)
        assert target is None
        assert err and "invalid --to target" in err, bad
    assert _approve_rollback_target(_rollback_state("M-IMPL", None), "M-DESIGN") == (
        "M-DESIGN",
        None,
    )


def test_approve_rollback_target_preset_and_override():
    """A gap-typed preset (ac_gap->M-ACC) stays authoritative without --to;
    an explicit --to overrides it (the Human outranks the preset)."""
    from tracks.cli.main import _approve_rollback_target

    assert _approve_rollback_target(_rollback_state("M-IMPL", "M-ACC"), None) == ("M-ACC", None)
    assert _approve_rollback_target(_rollback_state("M-IMPL", "M-ACC"), "M-SPEC") == (
        "M-SPEC",
        None,
    )


def test_approve_rollback_target_mtest_rejects_to():
    """M-TEST rollbacks always carry gap-typed presets: --to is an M-IMPL
    concern only (B57 re-fix scope)."""
    from tracks.cli.main import _approve_rollback_target

    target, err = _approve_rollback_target(_rollback_state("M-TEST", "M-ACC"), "M-DESIGN")
    assert target is None
    assert err and "only valid for M-IMPL" in err
    assert _approve_rollback_target(_rollback_state("M-TEST", "M-ACC"), None) == ("M-ACC", None)


def test_approve_parse_args_actor_and_to():
    from tracks.cli.main import _parse_approve_args

    assert _parse_approve_args([]) == (None, None, None)
    assert _parse_approve_args(["--actor", "Aaron"]) == ("Aaron", None, None)
    assert _parse_approve_args(["--to", "M-DESIGN"]) == (None, "M-DESIGN", None)
    assert _parse_approve_args(["--actor", "A", "--to", "M-ACC"]) == ("A", "M-ACC", None)
    assert _parse_approve_args(["--to", "M-DESIGN", "--actor", "A"]) == ("A", "M-DESIGN", None)
    for bad in (["--actor"], ["--to"], ["bogus"], ["--actor", "A", "extra"]):
        actor, to_stage, err = _parse_approve_args(bad)
        assert err and err.startswith("usage:"), bad
