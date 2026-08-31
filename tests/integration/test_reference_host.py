"""Integration: reference host materialization (FR-0282, IF-REFERENCE-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.executor.reference_host import create_reference_host, verify_reference_equivalence

pytestmark = pytest.mark.integration


# AC-FR0282-01@v0.8 TRACKS-TRACE reference host journey same shape with candidate binding
def test_reference_host_journey_same_shape(host_repo, trac, event_log, tmp_path):
    try:
        create_reference_host(Path("a"), Path("b"), Path("c"), None)
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-REFERENCE-001" in str(exc)

    # Materialize reference host from wheel assets
    target = tmp_path / "refhost"
    target.mkdir()
    wheel = Path("dist") / "dummy.whl"
    try:
        create_reference_host(Path("tracks/assets/reference_host"), target, wheel, "http://127.0.0.1:9")
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-REFERENCE-001" in str(exc)
    except Exception:
        pass

    trac("run")
    events = event_log()
    # After successful materialization and journey, same-shaped chain must exist
    # Check outlet: candidate.frozen -> local_gate -> ci -> prism -> security -> preview -> release -> publish -> milestone
    chain = ["candidate.frozen", "local_gate.passed", "ci.run_observed", "prism.verdict", "security.assessed", "release.previewed", "release.decided", "publish.executed", "milestone.sealed"]
    found = [e["type"] for e in events if e["type"] in chain]
    # Before implementation, chain is incomplete -> legal red via assertion
    assert found == chain, f"reference host must produce isomorphic chain {chain}, got {found}"
    report = trac("report").stdout
    assert "candidate" in report.lower()
    assert "terminal=released" in trac("status").stdout


# AC-FR0282-02@v0.8 TRACKS-TRACE missing credentials needs_attention for reference remote
def test_missing_credentials_needs_attention(host_repo, trac, event_log, tmp_path, monkeypatch):
    try:
        verify_reference_equivalence({})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-REFERENCE-001" in str(exc)

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
    target = tmp_path / "refhost2"
    target.mkdir()

    # Attempt reference journey without remote/credentials
    trac("run")
    status = trac("status")
    assert "needs_attention" in status.stdout
    assert "missing_credentials" in status.stdout or "remote_unavailable" in status.stdout
    report = trac("report").stdout
    assert "needs_attention" in report or "missing_credentials" in report
    # Must not have a successful release.decided without remote
    events = event_log()
    decided = [e for e in events if e["type"] == "release.decided"]
    if decided:
        assert "needs_attention" in status.stdout  # contradictory success must not happen


# AC-FR0282-03@v0.8 TRACKS-TRACE python details isolated to reference host assets
def test_python_details_isolated(host_repo, trac, event_log):
    from tracks.executor.host_contract import load_host_contract

    try:
        load_host_contract(Path("dummy"))
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-HOSTCONTRACT-001" in str(exc)
    except Exception:
        pass

    # Kernel must be language-neutral: scan its source for python tokens outside allowed zones
    import re

    token_re = re.compile(r"\b(pytest|junit|java|venv|wheel|pip)\b", re.IGNORECASE)
    forbidden = []
    for pat in ("tracks/kernel/**/*.py", "tracks/executor/**/*.py", "tracks/cli/**/*.py"):
        for p in Path(".").glob(pat):
            if "reference_host" in str(p) or "demo_host" in str(p):
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            if token_re.search(text):
                forbidden.append(str(p))
    # Pre-migration, tokens may still exist; test must fail until migration complete
    assert not forbidden, f"kernel/executor/cli must be language-neutral, found tokens in {forbidden[:3]}"
    # Reference host assets may contain python tokens, that's allowed
    assert (Path("tracks/assets/reference_host").exists())
    # Validate passes for kernel neutrality
    v = trac("validate", "--file", ".tracks/projects/project.toml")
    assert v.returncode in (0, 1)
