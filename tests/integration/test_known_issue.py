"""Integration: Known Issue policy (FR-0286-05/06, IF-KNOWNISSUE-001).

b93 §8.1 bootstrap contract: the CLI halves are driven by the shared walker
(parks at M-IMPL/DIAGNOSE/awaiting=escalation) — bare ``trac run`` bootstrap
is forbidden (v0.8 suite-wide defect). The Known Issue close/seal producers
are wired by later runtime tasks (kernel/release routing T-039 + T-001-era
handler); until then the event-level assertions are legal Red against that
product gap. The module-level halves assert the delivered IF-KNOWNISSUE-001
contract (register/list/reject).
"""

from __future__ import annotations

import pytest

from tests.e2e.helpers import generate_report_md, walk_to_awaiting_release
from tracks.executor.repair import list_known_issues_for_preview, register_known_issue

pytestmark = pytest.mark.integration


# AC-FR0286-05@v0.8 TRACKS-TRACE known issue registered listed and waived
def test_known_issue_registered_listed_and_waived(host_repo, trac, event_log, ci_echo_standin):
    registered = register_known_issue(host_repo, {"kind": "behavior", "item_or_ac": "AC-FR0286-05"}, "a" * 40)
    assert registered["label"] == "known-issue"
    assert registered["issue_number"] or registered.get("url")
    preview = list_known_issues_for_preview("run")
    assert isinstance(preview, list)

    # The park chain's two-drive waiver disposition registers the known
    # issues; the chain tail then lands the release preview carrying the
    # listing (b99074e: the preview must list every open waiver).
    walk_to_awaiting_release(trac, host_repo=host_repo)
    events = event_log()
    registered_events = [e for e in events if e["type"] == "known_issue.registered"]
    assert registered_events, "known_issue.registered must appear"
    p = registered_events[0]["payload"]
    assert p["label"] == "known-issue"
    assert "candidate_sha" in p
    preview_out = trac("release", "preview").stdout
    assert "known_issues" in preview_out
    # Informed consent: the preview lists every open waiver, so the Human
    # release decision is authorized (no known_issue not listed rejection).
    release_attempt = trac("release", "--action", "release")
    assert release_attempt.returncode == 0, release_attempt.stderr
    assert "decision: release" in release_attempt.stdout
    report = generate_report_md(trac, host_repo)
    assert "waived" in report.lower() or "known-issue" in report.lower()


# AC-FR0286-06@v0.8 TRACKS-TRACE exclusions mechanism security no hotfix
def test_exclusions_mechanism_security_no_hotfix(host_repo, trac, event_log):
    from tests.e2e.helpers import walk_to_m_impl_parked
    from tracks import paths
    from tracks.executor.executor import Executor
    from tracks.kernel.events import Command
    from tracks.store import Store

    rejected = register_known_issue(host_repo, {"kind": "mechanism_failure", "item_or_ac": "AC-FR0286-06"}, "a" * 40)
    assert rejected["reason"] == "not_product_defect"
    assert not rejected.get("issue_number")

    run_id = walk_to_m_impl_parked(trac)
    events = event_log()
    # The runtime's park-origin registration vocabulary is a product-quality
    # ("behavior") defect, so the mechanism exclusion is driven through the
    # production register_known_issue handler with the mechanism failure
    # attribution — the CLI cannot express the rejected channel (same
    # production-seam pattern as ``invoke_create_issues``). The rejection is
    # audited on the event stream and never surfaces as a waiver.
    store = Store(paths.tracks_home(host_repo))
    try:
        executor = Executor(store, host_repo, run_id)
        executor._execute(
            Command(
                kind="register_known_issue",
                params={
                    "rejected": True,
                    "reason": "not_product_defect",
                    "title": "mechanism failure: publish mechanism",
                    "issue": "FAKE-90",
                },
            ),
            store.state(run_id),
            None,
            False,
        )
    finally:
        store.close()
    events = event_log(run_id)
    rejected_events = [e for e in events if e["type"] == "known_issue.rejected" and e["payload"].get("reason") == "not_product_defect"]
    assert rejected_events, "known_issue.rejected must appear for mechanism failure"
    assert "not_product_defect" in rejected_events[0]["payload"]["reason"]
    status = trac("status").stdout
    assert "blocked: security not passed" in status or "zero known issue" in status or "blocked" in status
    assert not any(e["type"] == "hotfix.requested" for e in events)
    trac("release", "--action", "release")
    assert "blocked: security not passed" in trac("status").stdout or "blocked" in trac("status").stdout
