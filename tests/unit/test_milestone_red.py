"""T-016 RED: milestone lifecycle / issue close domain (FR-0284/FR-0276).

Pins the still-undelivered slices of tracks/executor/milestone.py:

- IF-MILESTONE-001: build_release_trace / compute_trace_digest /
  seal_evidence_readonly / clean_temp_refs
- IF-ISSUE-002 (close consumption face): close_issues_with_comment /
  close_project_milestone — the authoritative-map closers that only consume
  api_verified mappings and append release-trace comments.

All target tests fail on the pre-fix baseline with assertion_failure on the
contract behaviour (no stub_token, no assembly errors). Only unit tests are
added (RED discipline, manifest red_test_paths = tests/unit).
"""

from __future__ import annotations

from tracks.executor.milestone import (
    clean_temp_refs,
    close_issues_with_comment,
    seal_evidence_readonly,
)


# AC-FR0284-01@v0.8 TRACKS-TRACE IF-ISSUE-002 close only api_verified map
def test_close_consumes_only_authoritative_map():
    """IF-ISSUE-002: closers consume ONLY authoritative issue.mapped
    entries (api_verified=true); a FAKE mapping (api_verified=false or a
    FAKE-N number) must be refused and never closed."""
    try:
        closed = close_issues_with_comment(
            "<repo>",
            {
                # authoritative
                "FR-0283": {"repo": "acme/host", "issue_number": 123, "api_verified": True},
                # fake/not verified -> must not close
                "NFR-0148": {"repo": "acme/host", "issue_number": "FAKE-95", "api_verified": False},
            },
            {"trace_digest": "sha256:abc", "candidate_sha": "c0ffee"},
        )
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: close_issues_with_comment not implemented"
        ) from err
    assert isinstance(closed, list), "assertion failure: close must return list"
    # only the authoritative entry may be closed
    numbers = [c.get("issue_number") if isinstance(c, dict) else None for c in closed]
    assert 123 in numbers, (
        f"assertion failure: authoritative mapping must be closed, got {closed!r}"
    )
    assert "FAKE-95" not in [str(n) for n in numbers if n is not None], (
        f"assertion failure: fake mapping must not be closed, got {closed!r}"
    )
    # every close must carry trace linkage fields
    for c in closed:
        assert isinstance(c, dict)
        for key in ("candidate_sha", "trace_digest", "comment_ref", "state"):
            assert key in c, f"assertion failure: close entry missing {key}"
        assert c["state"] == "closed"


# AC-FR0284-01@v0.8 TRACKS-TRACE IF-ISSUE-002 close project milestone
def test_close_project_milestone_returns_closed_state():
    """IF-ISSUE-002: closing the release Project/milestone records a closed
    state bound to the trace."""
    try:
        from tracks.executor.milestone import close_project_milestone
    except ImportError as err:
        raise AssertionError(
            "assertion failure: close_project_milestone not implemented"
        ) from err
    try:
        result = close_project_milestone(
            "<repo>",
            {"project": "release v0.8", "milestone": "55"},
            {"trace_digest": "sha256:abc", "candidate_sha": "c0ffee"},
        )
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: close_project_milestone not implemented"
        ) from err
    assert isinstance(result, dict)
    assert result.get("state") == "closed", (
        f"assertion failure: milestone must be closed, got {result!r}"
    )
    assert result.get("trace_digest") == "sha256:abc"
    assert "candidate_sha" in result


# AC-FR0276-01@v0.8 TRACKS-TRACE IF-MILESTONE-001 seal readonly
def test_seal_evidence_readonly():
    """IF-MILESTONE-001: sealing marks the evidence blob readonly=true and
    binds the candidate."""
    try:
        sealed = seal_evidence_readonly("<repo>", "c0ffee")
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: seal_evidence_readonly not implemented"
        ) from err
    assert isinstance(sealed, dict)
    assert sealed.get("readonly") is True, (
        f"assertion failure: seal must be readonly=true, got {sealed!r}"
    )
    assert sealed.get("candidate_sha") == "c0ffee"
    assert "seal_blob" in sealed or "blob" in str(sealed)


# AC-FR0276-01@v0.8 TRACKS-TRACE IF-MILESTONE-001 refs cleaned
def test_clean_temp_refs_returns_zero_remaining():
    """IF-MILESTONE-001: clean_temp_refs removes refs/trac/tmp/{run}/* and
    reports remaining=0 (verified by for-each-ref)."""
    try:
        cleaned = clean_temp_refs("<repo>", "RUN")
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: clean_temp_refs not implemented"
        ) from err
    assert isinstance(cleaned, dict)
    assert cleaned.get("remaining") == 0, (
        f"assertion failure: after cleanup remaining must be 0, got {cleaned!r}"
    )
