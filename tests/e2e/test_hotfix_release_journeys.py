"""E2E: hotfix release journeys (v0.8, test-plan §11.2).

The v0.8 hotfix entries (``trac hotfix <issue> --scenario post-release|dev``)
are driven through the real M-DESIGN → M-TEST → M-IMPL → M-VERIFY →
M-RELEASE → M-PUBLISH chain against a real bare remote and the loopback
GitHub stand-in (``ci_echo_standin``). The post-release journey merges main,
syncs the active release branch and publishes a patch tag/release; the dev
journey merges only the active release branch and publishes a pre-release
tag with no public tag/release object.

The harness reuses the integration journey seeding/declaration helpers
(``_seed_hotfix_host`` / ``_walk_hotfix_to_awaiting_release`` /
``_bind_github_origin``): the fake issue corpus and the declared
``[host-contract.operations.*]`` plans are the observable inputs the real
runtime consumes, never mocks of kernel/executor behaviour.
"""

from __future__ import annotations

import re
import subprocess

import pytest

from tests.e2e.helpers import generate_report_md, init_bare_remote
from tests.integration.test_journey_versioning import (
    _bind_github_origin,
    _seed_hotfix_host,
    _walk_hotfix_to_awaiting_release,
)

pytestmark = pytest.mark.e2e


def _published_candidate(events) -> str:
    executed = [
        e
        for e in events
        if e["type"] == "publish.executed" and e["payload"].get("status") == "done"
    ]
    assert executed, "the journey must reach M-PUBLISH"
    candidate = executed[0]["payload"]["candidate_sha"]
    assert all(e["payload"]["candidate_sha"] == candidate for e in executed)
    return candidate


def _remote_ls(bare) -> str:
    return subprocess.run(
        ["git", "ls-remote", str(bare)], capture_output=True, text=True, check=True
    ).stdout


# AC-FR0277-02@v0.8 TRACKS-TRACE post release patch merges main and release branch with patch tag
def test_post_release_hotfix_journey(host_repo, trac, event_log, ci_echo_standin):
    # Seed the released host: approved v0.8 baseline, active release branch
    # and the bug corpus; declare the post-release operation plan.
    _seed_hotfix_host(host_repo)
    bare, _initial = init_bare_remote(host_repo, "e2e_hotfix_bare.git")
    subprocess.run(
        ["git", "push", "-q", "-u", "origin", "releases/v0.8"],
        cwd=host_repo,
        check=True,
        capture_output=True,
    )
    _bind_github_origin(host_repo, bare, ci_echo_standin.repo)

    _walk_hotfix_to_awaiting_release(trac, 42, "post-release")
    assert trac("release", "--action", "release").returncode == 0
    assert trac("run").returncode == 0

    events = event_log()
    candidate = _published_candidate(events)
    targets = {
        e["payload"]["target"]
        for e in events
        if e["type"] == "publish.executed" and e["payload"].get("status") == "done"
    }
    # main + the active release branch advanced to the fixed candidate; a
    # patch tag was published (never the public feature tag).
    assert "main" in targets
    assert "releases/v0.8" in targets
    patch_tags = [
        t for t in targets if re.fullmatch(r"v0\.8\.\d+", t) and t != "v0.8.0"
    ]
    assert patch_tags, f"patch tag must be published, got {targets}"

    ls = _remote_ls(bare)
    assert f"{candidate}\trefs/heads/main" in ls
    assert f"{candidate}\trefs/heads/releases/v0.8" in ls
    assert any(f"{candidate}\trefs/tags/{tag}" in ls for tag in patch_tags)

    # Identity: the trace and the patch release bind the same candidate.
    trace = [e for e in events if e["type"] == "milestone.trace_closed"][-1]["payload"]
    assert trace["candidate_sha"] == candidate
    assert trace["release_tag"] in patch_tags
    assert [e for e in events if e["type"] == "run.completed"]

    # The post-release patch is a public channel.
    report = generate_report_md(trac, host_repo)
    assert candidate in report
    assert "channel=pre-release" not in report


# AC-FR0277-03@v0.8 TRACKS-TRACE dev prerelease only merges release branch with pre channel
def test_dev_hotfix_journey(host_repo, trac, event_log, ci_echo_standin):
    _seed_hotfix_host(host_repo)
    # Dev publishes ONLY the release branch + pre tag: an empty remote (no
    # pre-pushed main) is the Arrange that proves no main merge happened.
    bare, _initial = init_bare_remote(host_repo, "e2e_dev_bare.git", push_main=False)
    subprocess.run(
        ["git", "push", "-q", "-u", "origin", "releases/v0.8"],
        cwd=host_repo,
        check=True,
        capture_output=True,
    )
    _bind_github_origin(host_repo, bare, ci_echo_standin.repo)

    _walk_hotfix_to_awaiting_release(trac, 42, "dev")
    assert trac("release", "--action", "release").returncode == 0
    assert trac("run").returncode == 0

    events = event_log()
    candidate = _published_candidate(events)
    targets = {
        e["payload"]["target"]
        for e in events
        if e["type"] == "publish.executed" and e["payload"].get("status") == "done"
    }
    # Dev publishes ONLY the active release branch + the pre-release tag: no
    # public tag/release object, no main merge.
    assert "releases/v0.8" in targets
    pre_tags = [t for t in targets if t.startswith("v0.8.") and "-pre" in t]
    assert pre_tags, f"pre-release tag must be published, got {targets}"
    assert "main" not in targets
    assert not [t for t in targets if re.fullmatch(r"v0\.8\.\d+", t)]
    assert ci_echo_standin.releases == {}

    ls = _remote_ls(bare)
    assert f"{candidate}\trefs/heads/releases/v0.8" in ls
    assert any(f"{candidate}\trefs/tags/{tag}" in ls for tag in pre_tags)
    assert "refs/heads/main" not in ls

    # The dev channel renders as pre-release in the identity report.
    report = generate_report_md(trac, host_repo)
    assert candidate in report
    assert "channel=pre-release" in report or "pre-release" in report.lower()
