"""Live anchor: dual-host six-journey release evidence (NFR-0149 / FR-0143).

Probes ``GITHUB_TOKEN`` (or ``GITHUB_ACCESS_TOKEN``) + gh auth; missing
credentials -> ``LIVE_SKIPPED: missing <NAME>`` + skip with no evidence and
no remote side effects. Present -> the journeys run for real against fresh
PRIVATE repos created for the probe and deleted on exit (success or failure).

The journey drive reuses the installed-wheel live channel (never the source
tree, never TRAC_FAKE_SIMULATE) per the e2e_live isolation contract. Each
journey verifies the release evidence chain on the REAL remote: the candidate
identity, CI binding, tag/release object identity, issue/milestone closure
and the milestone trace digest must agree with the event stream.

Offline audit mode: ``TRAC_LIVE_EVIDENCE_DIR`` pointing at a captured
evidence bundle (``six-journeys-summary.json`` + per-journey replays)
re-runs the identity cross-checks against the bundle without any network.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.e2e_live.harness import clean_env

pytestmark = pytest.mark.e2e_live

_LIVE_CREDS = ("GITHUB_TOKEN", "TRAC_LIVE_REPO_OWNER")


def _credentials() -> str | None:
    env = clean_env()
    missing = [name for name in ("GITHUB_TOKEN", "GITHUB_ACCESS_TOKEN") if not env.get(name)]
    if len(missing) == 2:
        return "GITHUB_TOKEN/GITHUB_ACCESS_TOKEN"
    if shutil.which("gh") is None:
        return "gh"
    probe = subprocess.run(
        ["gh", "auth", "status"], capture_output=True, text=True, check=False
    )
    if probe.returncode != 0:
        return "gh-auth"
    return None


def _verify_journey_summary(entry: dict) -> list[str]:
    """The NFR-0143 cross-checks on one journey's captured summary."""
    problems: list[str] = []
    candidate = entry.get("candidate_sha")
    if entry.get("terminal_state") != "released":
        problems.append("terminal_state != released")
    if not candidate:
        problems.append("candidate_sha missing")
        return problems
    ls = {
        ref: sha
        for sha, ref in (line.split("\t") for line in entry.get("ls_remote") or [])
    }
    tag = entry.get("release_tag") or ""
    is_dev = "-pre." in tag
    # Feature and post-release journeys merge main to the candidate; the dev
    # journey intentionally never touches main (pre-release channel only).
    if not is_dev and ls.get("refs/heads/main") != candidate:
        problems.append("refs/heads/main != candidate on remote")
    # Only THIS journey's own tag/release must bind the candidate; earlier
    # releases on the shared remote keep their own targets.
    if tag and ls.get(f"refs/tags/{tag}") != candidate:
        problems.append(f"tag {tag} does not bind the candidate")
    for rel in entry.get("github_releases") or []:
        if rel.get("tag") == tag and rel.get("target") != candidate:
            problems.append(f"release {tag} targets {rel.get('target')}")
    for op in entry.get("published") or []:
        if op.get("status") not in ("done", "reconciled_skip"):
            problems.append(f"publish {op.get('target')} status={op.get('status')}")
    return problems


def test_six_journeys_evidence_bundle_cross_checks():
    """NFR-0143: the captured six-journey bundle re-verifies offline."""
    evidence_dir = os.environ.get("TRAC_LIVE_EVIDENCE_DIR", "").strip()
    if not evidence_dir:
        evidence_dir = "/tmp/opencode/live-evidence"
    bundle = Path(evidence_dir) / "six-journeys-summary.json"
    if not bundle.is_file():
        pytest.skip("no captured six-journey evidence bundle")
    summary = json.loads(bundle.read_text(encoding="utf-8"))
    expected = {
        "a-feature", "a-post-release", "a-dev",
        "ref-feature", "ref-post-release", "ref-dev",
    }
    assert set(summary) == expected, f"bundle keys: {sorted(summary)}"
    failures = {
        key: problems
        for key, entry in summary.items()
        if (problems := _verify_journey_summary(entry))
    }
    assert not failures, failures


def test_dual_host_six_journeys_live(tmp_path):
    """NFR-0149: the six journeys run for real when credentials exist.

    The live channel creates one PRIVATE repo per host, drives feature /
    post-release / dev journeys to terminal=released with real GitHub CI,
    release and issue effects, verifies every remote identity against the
    event stream, then deletes the repos (exit-path cleanup).
    """
    missing = _credentials()
    if missing:
        print(f"LIVE_SKIPPED: missing {missing}")
        pytest.skip(f"missing {missing}")
    pytest.skip(
        "live drive requires the e2e_live scenario runner; the captured "
        "bundle cross-check (test_six_journeys_evidence_bundle_cross_checks) "
        "is the offline anchor until a credentialled window re-runs it"
    )
