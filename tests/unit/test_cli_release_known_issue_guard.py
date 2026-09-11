"""CLI release-face guards: informed-consent listing and prism=final face.

Pins the WIP semantics of ``tracks/cli/main.py``:

- AC-FR0286-05: a registered known issue that the release preview does not
  list blocks ``--action release`` (audited ``release.rejected``
  reason=known_issue_not_listed); a listed issue does not block, and
  non-release actions never consume the guard.
- interfaces §2b: the ``prism=`` fragment records the verify_final block —
  a landed scoped verdict projects pass|failed, prism verdicts without a
  scoped verify_final one mean the final review has not passed.
"""

from __future__ import annotations

from types import SimpleNamespace


def _registered(issue_number: int) -> SimpleNamespace:
    return SimpleNamespace(
        type="known_issue.registered",
        payload={"issue_number": issue_number, "fixed": False},
        seq=1,
    )


def test_release_rejected_when_registered_known_issue_unlisted(tmp_path, capsys):
    """AC-FR0286-05: an unfixed registered known issue absent from the
    preview's known_issues listing fails the release closed with an
    append-only release.rejected audit (no release.decided)."""
    from tests.unit.test_release_authorization_direct import _host
    from tracks.cli.main import cmd_release

    repo, store, run_id, _candidate, _digest, _path, _preview = _host(tmp_path)
    store.append(
        run_id,
        "v0.8",
        "known_issue.registered",
        {"issue_number": 100, "fixed": False, "item_or_ac": "AC-FR9001-01"},
    )

    rc = cmd_release(repo, "--action", "release")
    out, err = capsys.readouterr()
    rejected = [e for e in store.events(run_id) if e.type == "release.rejected"]
    decided = [e for e in store.events(run_id) if e.type == "release.decided"]
    store.close()

    assert rc == 1
    assert len(decided) == 0, "the unlisted waiver must not authorize a release"
    assert len(rejected) == 1
    payload = rejected[0].payload
    assert payload["reason"] == "known_issue_not_listed"
    assert "100" in payload["detail"]
    assert "known_issue" in (out + err)


def test_listed_known_issue_does_not_consume_the_guard():
    """AC-FR0286-05: the registered issue listed in the preview is informed
    consent — the guard returns None; delay/return never invoke it."""
    from tracks.cli.main import _release_known_issue_guard, _unlisted_known_issues

    listed = {"known_issues": [{"issue": "acme/host#100", "waiver": "AC-FR9001-01"}]}
    unlisted = {"known_issues": []}
    events = [_registered(100)]

    assert _unlisted_known_issues(events, listed) == []
    assert _unlisted_known_issues(events, unlisted) == ["100"]
    assert (
        _release_known_issue_guard(None, "RUN", "v0.8", "release", listed, events)
        is None
    )
    assert (
        _release_known_issue_guard(None, "RUN", "v0.8", "delay", unlisted, events)
        is None
    )


def test_prism_final_status_renders_the_verify_final_block():
    """interfaces §2b: prism=pass|failed projects the verify_final verdict;
    verdicts that never scoped verify_final render the release-chain block
    (failed), and no prism verdict at all renders nothing."""
    from tracks.cli.main import _prism_final_status

    def verdict(payload: dict) -> SimpleNamespace:
        return SimpleNamespace(type="prism.verdict", payload=payload, seq=1)

    assert _prism_final_status([]) is None
    assert (
        _prism_final_status([verdict({"verdict": "pass", "scope": "verify_final"})])
        == "pass"
    )
    assert (
        _prism_final_status([verdict({"verdict": "revise", "scope": "verify_final"})])
        == "failed"
    )
    assert (
        _prism_final_status([verdict({"verdict": "pass", "scope": "PRISM_PLAN"})])
        == "failed"
    )
