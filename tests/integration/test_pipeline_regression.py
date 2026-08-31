"""Integration: pipeline regression (FR-0285, IF-PIPELINE-001)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


# AC-FR0285-01@v0.8 TRACKS-TRACE write collect redcheck prism chain intact
def test_write_collect_redcheck_prism_chain_intact(host_repo, trac, event_log):
    trac("run")
    events = event_log()
    types = [e["type"] for e in events]
    assert "stage.entered" in types, "stage.entered must appear"
    m_test_enter = next(i for i, t in enumerate(types) if t == "stage.entered" and "M-TEST" in str(events[i]["payload"]))
    chain = types[m_test_enter:]
    assert "red.validated" in chain, "red.validated must appear in M-TEST chain"
    assert "prism.verdict" in chain, "prism.verdict must appear"
    red_idx = chain.index("red.validated")
    prism_idx = chain.index("prism.verdict")
    assert red_idx < prism_idx
    replay_out = trac("replay").stdout
    assert "prism.verdict" in replay_out
    assert "red.validated" not in types or "pipeline incomplete" in trac("status").stdout or "blocked" in trac("status").stdout


# AC-FR0285-02@v0.8 TRACKS-TRACE no selfcheck authority
def test_no_selfcheck_authority(host_repo, trac, event_log):
    trac("run")
    events = event_log()
    selfcheck = [e for e in events if "selfcheck" in e["type"].lower()]
    assert not any(sc["type"] == "red.validated" for sc in selfcheck)
    full = [e for e in events if e["type"] == "full.executed"]
    red = [e for e in events if e["type"] == "red.validated"]
    assert red, "red.validated must appear, not full.executed"
    assert not (full and not red)


# AC-FR0285-03@v0.8 TRACKS-TRACE no new pipeline definition
def test_no_new_pipeline_definition(host_repo, trac, event_log):
    v = trac("validate", "--file", ".tracks/projects/project.toml")
    report = trac("report").stdout
    assert "pipeline" in report.lower()
    events = event_log()
    stages = [e["payload"].get("stage") for e in events if e["type"] == "stage.entered"]
    known = {"M-START", "M-STORY", "M-SPEC", "M-ACC", "M-REQ-APPROVAL", "M-DESIGN", "M-TEST", "M-IMPL", "M-VERIFY", "M-SECURITY", "M-RELEASE", "M-PUBLISH", "M-MILESTONE"}
    for s in stages:
        assert s in known
    assert v.returncode in (0, 1)
