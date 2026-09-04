"""Integration: required CI API readback (FR-0270, IF-VERIFY-004).

b93 §8.1 bootstrap contract: the CLI halves are driven by the shared walker
(``walk_to_m_impl_parked``) — bare ``trac run`` bootstrap is forbidden. The
module-level halves assert the delivered binding faces the readback leans on
(IF-VERIFY-001 candidate freeze + foreign-SHA flagging, the mismatch
fail-closed basis); the ci.run_observed event assertions stay legal Red
against the unwired M-VERIFY producers. Env stand-ins are explicit per §1.4-5.
"""

from __future__ import annotations

import pytest

from tests.e2e.helpers import walk_to_m_impl_parked
from tracks.executor.m_verify import collect_binding_violations, freeze_candidate

pytestmark = pytest.mark.integration

_CANDIDATE = "a" * 40
_FOREIGN = "b" * 40

_ENV_KEYS = ("TRAC_GITHUB_API_BASE", "TRAC_GITHUB_REPO", "GITHUB_TOKEN")


def _set_standins(monkeypatch) -> None:
    monkeypatch.setenv("TRAC_GITHUB_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("TRAC_GITHUB_REPO", "acme/host")
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")


def _clear_env(monkeypatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


# AC-FR0270-01@v0.8 TRACKS-TRACE api readback binds candidate with four-tuple
def test_api_readback_binds_candidate(host_repo, trac, event_log, monkeypatch):
    # Module half: the readback binds the frozen candidate identity — the
    # freeze face must deliver the full HEAD SHA the CI observation carries.
    identity = freeze_candidate(host_repo)
    assert identity.candidate_sha is not None and len(identity.candidate_sha) == 40

    _set_standins(monkeypatch)
    # CLI half: ci.run_observed must carry the full repo/workflow/run_id/
    # head/candidate binding with api_verified. Legal Red: the M-VERIFY CI
    # readback producer is not wired on this baseline.
    walk_to_m_impl_parked(trac)
    events = event_log()
    observed = [e for e in events if e["type"] == "ci.run_observed"]
    assert observed, "ci.run_observed must appear with repo/workflow/run_id/head SHA binding"
    payload = observed[0]["payload"]
    for key in ("repo", "workflow", "run_id", "head_sha", "candidate_sha", "api_verified"):
        assert key in payload, f"ci.run_observed missing {key}"
    assert payload["head_sha"] == payload["candidate_sha"]
    assert payload["api_verified"] is True
    assert payload["conclusion"] == "success"
    status = trac("status")
    assert "ci=bound" in status.stdout
    _clear_env(monkeypatch)


# AC-FR0270-02@v0.8 TRACKS-TRACE mismatch missing stale blocks and not referenced
def test_mismatch_missing_stale_blocks(host_repo, trac, event_log, monkeypatch):
    # Module half (mismatch fail-closed basis): a ci.run_observed bound to a
    # foreign SHA is a binding violation — exactly the evidence that must
    # block and never be referenced by a preview.
    chain = [
        {"kind": "candidate.frozen", "candidate_sha": _CANDIDATE},
        {"kind": "ci.run_observed", "candidate_sha": _FOREIGN},
    ]
    violations = collect_binding_violations(chain, _CANDIDATE)
    assert len(violations) == 1 and "ci.run_observed" in str(violations[0]), (
        f"a foreign-SHA CI observation must be flagged as a binding violation, got {violations!r}"
    )

    _set_standins(monkeypatch)
    # CLI half: failed/mismatched observations must block and must never be
    # referenced by release previews. Legal Red: the M-VERIFY producers are
    # not wired on this baseline.
    walk_to_m_impl_parked(trac)
    events = event_log()
    observed = [e for e in events if e["type"] == "ci.run_observed"]
    failed_shas = [
        o["payload"].get("candidate_sha")
        for o in observed
        if o["payload"].get("status") == "failed"
    ]
    for o in observed:
        if o["payload"].get("status") == "failed":
            assert o["payload"].get("reason") in ("mismatch", "missing", "stale"), (
                f"a failed CI observation must carry its fail reason, got {o['payload']!r}"
            )
    status = trac("status")
    assert (
        "ci=mismatch" in status.stdout
        or "ci=missing" in status.stdout
        or "ci=stale" in status.stdout
        or "blocked" in status.stdout.lower()
    )
    preview_events = [e for e in events if e["type"] == "release.previewed"]
    for prev in preview_events:
        assert prev["payload"].get("candidate_sha") not in failed_shas
    _clear_env(monkeypatch)


# AC-FR0270-03@v0.8 TRACKS-TRACE missing credentials needs_attention with resume
def test_missing_credentials_needs_attention(host_repo, trac, event_log, monkeypatch):
    # Module half (IF-VERIFY-001/004): an unbound ci.run_observed row —
    # carrying no candidate_sha at all — is a binding violation (the
    # "missing" leg of the mismatch/missing/stale fail-closed set).
    violations = collect_binding_violations([{"kind": "ci.run_observed"}], _CANDIDATE)
    assert len(violations) == 1 and "ci.run_observed" in str(violations[0]), (
        f"an unbound CI observation must be flagged as a binding violation, got {violations!r}"
    )

    # No credentials anywhere: the walk runs with the fake backend but the
    # CI readback has neither token nor remote binding.
    _clear_env(monkeypatch)
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")

    # CLI half: without credentials no successful ci.run_observed may appear,
    # and the run must land attention.required with resume guidance.
    # Legal Red: the attention producers for the readback are not wired on
    # this baseline.
    walk_to_m_impl_parked(trac)
    events = event_log()
    successful = [e for e in events if e["type"] == "ci.run_observed" and e["payload"].get("api_verified") is True]
    assert not successful, "without credentials, no successful ci.run_observed should appear"
    attention = [e for e in events if e["type"] == "attention.required"]
    assert attention, "missing credentials must land attention.required"
    assert any(
        a["payload"].get("reason") in ("missing_token", "network_error", "missing_credentials")
        for a in attention
    ), f"attention reasons must identify the credential gap, got {[a['payload'] for a in attention]!r}"
    assert all(a["payload"].get("next") for a in attention), (
        "attention.required must carry next-step guidance"
    )
    assert any(a["payload"].get("area") == "ci_readback" for a in attention)
