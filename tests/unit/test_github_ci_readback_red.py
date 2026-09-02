"""T-002 RED: required-CI API readback behavior body (FR-0270, IF-VERIFY-004).

Pins the still-undelivered slices of tracks/effects/github.py (CI readback
half of IF-VERIFY-004; the github module is the effects owner per
architecture growth axis 11 / FR-0270 owner=effects/github.py):

- AC-FR0270-01: API readback binds repo+workflow+run_id+head SHA==candidate
  with api_verified=True on success (stand-in base honored)
- AC-FR0270-02: mismatch/missing/stale fail closed (status=failed, reason in
  the closed set), never a passing observation
- AC-FR0270-03: missing credentials -> attention (missing_token), never a
  silent pass or local fallback

All target tests fail on the pre-fix baseline with assertion_failure (the
contract functions do not exist yet; no stub_token, no assembly errors).
Only unit tests are added (RED discipline, manifest red_test_paths = tests/unit).

IF-VERIFY-004: readback_ci_run, judge_ci_binding
"""

from __future__ import annotations

import urllib.request

import pytest

from tracks.effects.github import GithubIssuesError


# AC-FR0270-03@v0.8 TRACKS-TRACE IF-VERIFY-004 missing token -> attention
def test_ci_readback_missing_token_needs_attention(monkeypatch):
    """AC-FR0270-03: without GITHUB_TOKEN the readback must raise a
    classified error (missing_token) — never a silent pass or local fallback."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("TRAC_GITHUB_API_BASE", raising=False)
    try:
        from tracks.effects.github import readback_ci_run
    except ImportError as err:
        raise AssertionError(
            "assertion failure: readback_ci_run not implemented in github effects"
        ) from err
    try:
        readback_ci_run("acme/host", "ci.yml", "a" * 40)
    except GithubIssuesError as exc:
        assert exc.classification == "missing_token", (
            f"assertion failure: expected missing_token classification, "
            f"got {exc.classification!r}"
        )
        return
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: readback_ci_run missing-token path not implemented"
        ) from err
    raise AssertionError(
        "assertion failure: missing token must raise a classified error, not pass"
    )


# AC-FR0270-01@v0.8 TRACKS-TRACE IF-VERIFY-004 stand-in base honored
def test_ci_readback_uses_stand_in_api_base(monkeypatch):
    """AC-FR0270-01: the readback GET must honor TRAC_GITHUB_API_BASE
    (explicit stand-in channel) instead of the real api.github.com."""
    captured: dict = {}

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"workflow_runs": [{"id": 123, "head_sha": "a", "conclusion": "success"}]}'

    def _fake_urlopen(req, timeout=30):
        captured["url"] = req.full_url if hasattr(req, "full_url") else str(req)
        captured["auth"] = req.headers.get("Authorization")
        return _FakeResp()

    monkeypatch.setenv("TRAC_GITHUB_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    try:
        from tracks.effects.github import readback_ci_run
    except ImportError as err:
        raise AssertionError(
            "assertion failure: readback_ci_run not implemented in github effects"
        ) from err
    try:
        data = readback_ci_run("acme/host", "ci.yml", "a" * 40)
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: readback_ci_run stand-in path not implemented"
        ) from err
    assert isinstance(data, dict), "assertion failure: readback must return dict"
    assert captured["url"].startswith("http://127.0.0.1:9"), (
        f"assertion failure: stand-in API base must be honored, got {captured.get('url')!r}"
    )
    assert "actions/runs" in captured["url"], (
        f"assertion failure: must query actions runs, got {captured.get('url')!r}"
    )
    assert captured["auth"] == "Bearer token", (
        f"assertion failure: token auth must be sent, got {captured.get('auth')!r}"
    )


# AC-FR0270-01@v0.8 TRACKS-TRACE IF-VERIFY-004 binding verdict passed
def test_ci_binding_verdict_passed():
    """AC-FR0270-01: head SHA == candidate SHA + conclusion success + required
    checks green -> status=passed with api_verified=True and the closed
    payload fields bound."""
    try:
        from tracks.effects.github import judge_ci_binding
    except ImportError as err:
        raise AssertionError(
            "assertion failure: judge_ci_binding not implemented in github effects"
        ) from err
    observed = {
        "repo": "acme/host",
        "workflow": "ci.yml",
        "run_id": 123,
        "head_sha": "a" * 40,
        "conclusion": "success",
        "checks": {"lint": "success", "test": "success"},
    }
    try:
        verdict = judge_ci_binding(observed, "a" * 40, ["lint", "test"])
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: judge_ci_binding passed path not implemented"
        ) from err
    assert isinstance(verdict, dict)
    assert verdict.get("status") == "passed", (
        f"assertion failure: bound run must be passed, got {verdict!r}"
    )
    assert verdict.get("api_verified") is True
    for key in ("repo", "workflow", "run_id", "head_sha", "candidate_sha", "conclusion"):
        assert key in verdict, f"assertion failure: verdict missing {key}"


# AC-FR0270-02@v0.8 TRACKS-TRACE IF-VERIFY-004 fail-closed verdicts
def test_ci_binding_verdict_fail_closed():
    """AC-FR0270-02: mismatch / missing / stale / failed conclusion all yield
    status=failed with a closed-set reason and api_verified reflected."""
    try:
        from tracks.effects.github import judge_ci_binding
    except ImportError as err:
        raise AssertionError(
            "assertion failure: judge_ci_binding not implemented in github effects"
        ) from err

    # head mismatch
    try:
        v1 = judge_ci_binding(
            {"head_sha": "b" * 40, "conclusion": "success", "checks": {}},
            "a" * 40,
            [],
        )
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: judge_ci_binding mismatch path not implemented"
        ) from err
    assert v1.get("status") == "failed", (
        f"assertion failure: head mismatch must fail closed, got {v1!r}"
    )
    assert v1.get("reason") == "mismatch"

    # missing run
    try:
        v2 = judge_ci_binding(None, "a" * 40, [])
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: judge_ci_binding missing path not implemented"
        ) from err
    assert v2.get("status") == "failed", (
        f"assertion failure: missing run must fail closed, got {v2!r}"
    )
    assert v2.get("reason") == "missing"
    assert v2.get("api_verified") is not True

    # stale marker
    try:
        v3 = judge_ci_binding(
            {"head_sha": "a" * 40, "conclusion": "success", "stale": True, "checks": {}},
            "a" * 40,
            [],
        )
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: judge_ci_binding stale path not implemented"
        ) from err
    assert v3.get("status") == "failed", (
        f"assertion failure: stale run must fail closed, got {v3!r}"
    )
    assert v3.get("reason") == "stale"

    # failed conclusion
    try:
        v4 = judge_ci_binding(
            {"head_sha": "a" * 40, "conclusion": "failure", "checks": {}},
            "a" * 40,
            [],
        )
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: judge_ci_binding failed-conclusion path not implemented"
        ) from err
    assert v4.get("status") == "failed", (
        f"assertion failure: failed conclusion must not pass, got {v4!r}"
    )

    # required check not green
    try:
        v5 = judge_ci_binding(
            {
                "head_sha": "a" * 40,
                "conclusion": "success",
                "checks": {"lint": "success", "test": "failure"},
            },
            "a" * 40,
            ["lint", "test"],
        )
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: judge_ci_binding check-green path not implemented"
        ) from err
    assert v5.get("status") == "failed", (
        f"assertion failure: non-green required check must fail closed, got {v5!r}"
    )


# guard: pytest collection anchor (imported fixture-free module scope)
_ = pytest
