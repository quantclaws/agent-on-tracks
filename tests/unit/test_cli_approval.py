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
