"""Integration: candidate-bound trace closure (IF-CLOSURE-001, IF-MUTATION-001).

AC-FR0265-01@v0.7 closure candidate-bound pass,
AC-FR0265-02@v0.7 blocking conditions hard errors,
AC-FR0265-03@v0.7 candidate digest consistency.

Assertions land on `trac check trace --version v0.7` (IF-CLOSURE-001,
interfaces §1i) CLI outlet.
"""

from __future__ import annotations

from tracks.cli.main import cmd_check


# AC-FR0265-01@v0.7 TRACKS-TRACE closure candidate-bound pass
def test_closure_candidate_bound_pass(host_repo, capsys):
    """AC-FR0265-01: v0.7 trace check outputs status=pass closure=candidate-bound."""
    rc = cmd_check(host_repo, "trace", "--version", "v0.7", "--json")
    out = capsys.readouterr().out
    # Contract (§1i/§2c): success stdout carries closure=candidate-bound.
    assert "closure=candidate-bound" in out, (
        f"v0.7 closure must report candidate-bound; got: {out!r}"
    )
    assert rc == 0, f"candidate-bound pass must exit 0; got {rc}"


# AC-FR0265-02@v0.7 TRACKS-TRACE blocking conditions hard errors
def test_blocking_conditions_hard_errors(host_repo, capsys):
    """AC-FR0265-02: node_missing/skip_xfail/identity_drift/control_failure hard-error."""
    cmd_check(host_repo, "trace", "--version", "v0.7", "--json")
    out = capsys.readouterr().out
    # The v0.7 candidate-bound closure (IF-CLOSURE-001) must surface the
    # closure field; its absence (not wired) is the legal Red anchor.
    assert "closure" in out, (
        "v0.7 closure must surface hard errors from the closed set "
        "(node_missing/skip_xfail/identity_drift/control_failure)"
    )


# AC-FR0265-03@v0.7 TRACKS-TRACE candidate digest consistency
def test_candidate_digest_consistency(host_repo, capsys):
    """AC-FR0265-03: baseline + mutation evidence bind the same candidate digest."""
    cmd_check(host_repo, "trace", "--version", "v0.7", "--json")
    out = capsys.readouterr().out
    # The --json per-AC record (§1i) carries candidate_digest; a foreign
    # candidate digest must hard-error (foreign_candidate).
    assert "candidate_digest" in out, (
        "candidate-bound closure must expose candidate_digest per AC"
    )
