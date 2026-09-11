"""Integration: three journey versioning (FR-0277, IF-JOURNEY-001/IF-PUBLISH-002).

The v0.8 release chain is driven through its real producers: the shared
walkers park the first journey at M-IMPL/DIAGNOSE, bounded drives land the
M-RELEASE preview, the Human gate decides, and the Runtime executes the
M-PUBLISH operation plan against a real bare remote (``git ls-remote``) and
the loopback GitHub stand-in (release readback). Journey operation plans are
declared through the host contract exactly as the tracks host declares them
(``[host-contract.operations.*]`` + ``[host-contract.version_scheme]``).

The post-release/dev journey anchors drive the real product wiring end to
end: the hotfix M-IMPL planner resolves its anchored registry from the
inherited baseline (IF-HOTFIX-010), the executor translates the hotfix
scenario into the preview journey and resolves the active release branch,
and the shared release-fact resolver renders ``{n}``/``{ulid}`` from the
remote tag census identically at preview, release-gate and publish
re-validation time.
"""

from __future__ import annotations

import re
import subprocess

import pytest

from tests.e2e.helpers import (
    generate_report_md,
    init_bare_remote,
    walk_to_awaiting_release,
)
from tracks.executor.publish import reconcile_operation
from tracks.executor.release_gate import build_operation_plan, dev_precheck

pytestmark = pytest.mark.integration


def _declare_journey_contract(repo, feature_steps):
    """Append a declared ``[host-contract]`` with the feature operation plan.

    Mirrors the host declaration shape of ``.tracks/projects/project.toml``:
    the runtime default contract is the baseline (gates/security/CI stay the
    declared no-ops), and only the feature plan + version_scheme are replaced
    so the journey anchors exercise the real plan resolution.
    """
    from tracks.executor.executor import _DEFAULT_HOST_CONTRACT_TOML

    contract = repo / ".tracks" / "projects" / "project.toml"
    text = contract.read_text(encoding="utf-8")
    assert "[host-contract]" not in text
    declared = _DEFAULT_HOST_CONTRACT_TOML.replace(
        'steps = ["merge:main"]\n\n[host-contract.operations.post_release]',
        f"steps = {feature_steps!r}\n\n[host-contract.operations.post_release]",
        1,
    )
    declared += (
        "\n[host-contract.version_scheme]\n"
        'feature_tag = "v{minor}.0"\n'
        'patch_line = "v{minor}.{n}"\n'
        'prerelease_tag = "v{minor}.{n}-pre"\n'
    )
    contract.write_text(text.rstrip("\n") + "\n\n" + declared, encoding="utf-8")


def _declare_hotfix_contract(repo):
    """Declare the post-release/dev operation plans on the host contract.

    Mirrors the tracks host declaration (``[host-contract.operations.*]`` +
    ``[host-contract.version_scheme]``) for the hotfix journeys: the runtime
    default contract is the baseline (no-op gates/security/CI) and only the
    two journey plans + version scheme are replaced.
    """
    from tracks.executor.executor import _DEFAULT_HOST_CONTRACT_TOML

    contract = repo / ".tracks" / "projects" / "project.toml"
    text = contract.read_text(encoding="utf-8")
    assert "[host-contract]" not in text
    declared = _DEFAULT_HOST_CONTRACT_TOML
    for journey, steps in (
        (
            "post_release",
            [
                "merge:main",
                "merge:releases/v{minor}:when=active_release_branch",
                "tag:{patch_line}",
            ],
        ),
        ("dev", ["merge:releases/v{minor}", "tag:{prerelease_tag}"]),
    ):
        marker = f'[host-contract.operations.{journey}]\nsteps = ["merge:main"]'
        assert marker in declared
        declared = declared.replace(marker, f"[host-contract.operations.{journey}]\nsteps = {steps!r}", 1)
    declared += (
        "\n[host-contract.version_scheme]\n"
        'patch_line = "v{minor}.{n}"\n'
        'prerelease_tag = "v{minor}.{n}-pre.{ulid}"\n'
    )
    contract.write_text(text.rstrip("\n") + "\n\n" + declared, encoding="utf-8")


def _bind_github_origin(host_repo, bare, repo_id):
    """Point ``origin`` at the GitHub URL while git rewrites it to ``bare``.

    The release/artifact effects resolve ``origin`` to an ``owner/repo`` id
    (never a guessed URL) and call the GitHub API; ``url.<bare>.insteadOf``
    keeps every git-side readback/push on the real local bare remote.
    """
    url = f"https://github.com/{repo_id}.git"
    subprocess.run(
        ["git", "remote", "set-url", "origin", url], cwd=host_repo, check=True
    )
    subprocess.run(
        ["git", "config", "--add", f"url.{bare}.insteadOf", url],
        cwd=host_repo,
        check=True,
    )


# AC-FR0277-01@v0.8 TRACKS-TRACE feature public release merges main and publishes tag/release
def test_feature_public_release(host_repo, trac, event_log, ci_echo_standin):
    plan = build_operation_plan(
        {
            "operations": {
                "feature": {
                    "steps": [
                        "merge:main",
                        "tag:{feature_tag}",
                        "release:{feature_tag}",
                    ]
                }
            }
        },
        "feature",
        {"feature_tag": "v0.8.0"},
    )
    assert plan["steps"] == ["merge:main", "tag:v0.8.0", "release:v0.8.0"]

    bare, _initial = init_bare_remote(host_repo, "journey_bare.git")
    _bind_github_origin(host_repo, bare, ci_echo_standin.repo)
    run_id = walk_to_awaiting_release(
        trac,
        pre_seed_hook=lambda repo: _declare_journey_contract(
            repo, ["merge:main", "tag:{feature_tag}", "release:{feature_tag}"]
        ),
    )
    assert run_id

    assert trac("release", "--action", "release").returncode == 0
    assert trac("run").returncode == 0

    events = event_log(run_id)
    candidate = next(
        e["payload"]["candidate_sha"] for e in events if e["type"] == "candidate.frozen"
    )
    # The declared feature plan merges main and publishes the public tag +
    # release through the M-PUBLISH write-ahead/execute pair.
    planned = [e for e in events if e["type"] == "publish.planned"]
    kinds = {p["payload"]["operation_kind"] for p in planned}
    assert {"merge", "tag", "release"} <= kinds, f"feature plan kinds: {kinds}"
    executed = [
        e for e in events
        if e["type"] == "publish.executed" and e["payload"].get("status") == "done"
    ]
    executed_kinds = {e["payload"]["target"] for e in executed}
    assert {"main", "v0.8.0"} <= executed_kinds, executed_kinds
    assert all(e["payload"]["candidate_sha"] == candidate for e in executed)

    # Remote mutual verification: main advanced to the candidate and the
    # public tag points at it.
    ls = subprocess.run(
        ["git", "ls-remote", str(bare)], capture_output=True, text=True, check=True
    ).stdout
    assert f"{candidate}\trefs/heads/main" in ls
    assert f"{candidate}\trefs/tags/v0.8.0" in ls

    # GitHub release exists on the stand-in (created + read back by tag).
    release = ci_echo_standin.releases.get("v0.8.0")
    assert release is not None and release["target_commitish"] == candidate
    assert release["prerelease"] is False

    # Milestone tail: the trace closes the same candidate identity and the
    # public release_tag.
    trace_events = [e for e in events if e["type"] == "milestone.trace_closed"]
    assert trace_events
    trace = trace_events[-1]["payload"]
    assert trace["candidate_sha"] == candidate
    assert trace["release_tag"] == "v0.8.0"
    operation_digests = {row["kind"]: row for row in trace["operation_digests"]}
    assert {"merge", "tag", "release"} <= set(operation_digests)
    assert all(row["status"] == "done" for row in operation_digests.values())
    assert [e for e in events if e["type"] == "milestone.sealed"]
    assert [e for e in events if e["type"] == "refs.cleaned"]

    status = trac("status")
    assert "terminal=released" in status.stdout

    # ``trac report`` exports the same trace identity (byte-verifiable).
    report = generate_report_md(trac, host_repo)
    assert "Release trace" in report
    assert trace["trace_digest"] in report
    assert candidate in report


_POST_RELEASE_CONTRACT = {
    "operations": {
        "post_release": {
            "steps": [
                "merge:main",
                "merge:releases/v{minor}:when=active_release_branch",
                "tag:v{minor}.{n}",
            ]
        }
    }
}

_DEV_CONTRACT = {
    "operations": {
        "dev": {
            "steps": ["merge:releases/v{minor}", "tag:v{minor}.{n}-pre.{ulid}"]
        }
    }
}


def _seed_hotfix_host(host_repo, version="v0.8"):
    """Approved baseline + active ``releases/{version}`` branch + bug corpus.

    The post-release/dev journeys require a locatable approved target version
    (spec/acceptance on disk + ``approval.recorded``) and the dev precheck an
    active ``releases/*`` branch; the fake issue channel serves bug #42.
    The target is ``v0.8``: hotfix runs inherit their target's release
    capability (FR-0277), so only a v0.8+ target re-routes at the M-IMPL
    boundary into the M-VERIFY release chain (sub-threshold hotfixes keep
    the v0.6 boundary completion).
    """
    from tests._support.hotfix_support import (
        seed_host_issues,
        seed_release_branch,
        seed_v05_approved_baseline,
    )
    from tracks.cli.main import HOST_BYPRODUCTS_GITIGNORE, HOST_BYPRODUCTS_MARKER

    seed_v05_approved_baseline(host_repo, version=version)
    seed_host_issues(host_repo)
    _declare_hotfix_contract(host_repo)
    # The initialized-host scaffold: the runtime test-command byproduct block
    # (`trac init` / `trac hotfix` managed) must be committed on the scenario
    # base branch or the B59 freeze flags `tests/**/__pycache__` as residue.
    gitignore = host_repo / ".gitignore"
    text = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if HOST_BYPRODUCTS_MARKER not in text:
        sep = "" if not text or text.endswith("\n") else "\n"
        gitignore.write_text(text + sep + HOST_BYPRODUCTS_GITIGNORE, encoding="utf-8")
    subprocess.run(
        ["git", "add", ".tracks/projects", ".gitignore"],
        cwd=host_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", f"seed {version} approved baseline"],
        cwd=host_repo,
        check=True,
        capture_output=True,
    )
    # Branch AFTER the seed commit: the dev entry branches fix/{issue} from
    # the active release branch, which must carry the approved baseline.
    seed_release_branch(host_repo, f"releases/{version}")


def _walk_hotfix_to_awaiting_release(trac, issue, scenario, *, max_drives=12):
    """Entry + bounded M-RELEASE drives for a v0.8 hotfix release journey.

    Mirrors ``walk_to_awaiting_release`` for ``trac hotfix`` entries (the
    shared walker is feature-``trac start`` specific): the M-IMPL failure
    injection parks the first release-chain stop and the bounded drives walk
    M-IMPL EXIT -> M-VERIFY -> M-RELEASE.
    """
    from tests.e2e.helpers import M_IMPL_PARK_SIMULATE

    entry = trac("hotfix", str(issue), "--scenario", scenario)
    assert entry.returncode == 0, entry.stderr
    for _ in range(max_drives):
        drive = trac("run", simulate=M_IMPL_PARK_SIMULATE)
        assert drive.returncode == 0, drive.stderr
        status = trac("status")
        if "AWAITING_RELEASE" in status.stdout:
            return
    status = trac("status")
    raise AssertionError(
        f"hotfix {scenario} journey must reach M-RELEASE/AWAITING_RELEASE; "
        f"blocked at: {status.stdout.strip()}"
    )


# AC-FR0277-02@v0.8 TRACKS-TRACE post release patch merges main and release branch with patch tag
def test_post_release_patch(host_repo, trac, event_log, ci_echo_standin):
    # Plan face (real resolution): main is unconditional, the active-release
    # sync-merge step is kept only when the branch exists (IF-JOURNEY-001 §1m).
    plan_without = build_operation_plan(
        _POST_RELEASE_CONTRACT, "post-release", {"minor": "0.8", "n": "1"}
    )
    assert "merge:main" in plan_without["steps"]
    assert not any("merge:releases/" in step for step in plan_without["steps"])
    plan_with = build_operation_plan(
        _POST_RELEASE_CONTRACT,
        "post-release",
        {"minor": "0.8", "n": "1"},
        active_release_branch="releases/v0.8",
    )
    assert "merge:main" in plan_with["steps"]
    assert "merge:releases/v0.8" in plan_with["steps"]
    assert "tag:v0.8.1" in plan_with["steps"]

    _seed_hotfix_host(host_repo)
    bare, _initial = init_bare_remote(host_repo, "journey_bare2.git")
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
    candidate = next(
        e["payload"]["candidate_sha"] for e in events if e["type"] == "candidate.frozen"
    )
    executed = [
        e
        for e in events
        if e["type"] == "publish.executed" and e["payload"].get("status") == "done"
    ]
    assert executed and all(e["payload"]["candidate_sha"] == candidate for e in executed)
    targets = {e["payload"]["target"] for e in executed}
    # main + the active release branch advanced to the fixed candidate and a
    # patch tag was published (never the public feature tag).
    assert "main" in targets
    assert "releases/v0.8" in targets
    patch_tags = [
        t for t in targets if re.fullmatch(r"v0\.8\.\d+", t) and t != "v0.8.0"
    ]
    assert patch_tags, f"patch tag must be published, got {targets}"

    ls = subprocess.run(
        ["git", "ls-remote", str(bare)], capture_output=True, text=True, check=True
    ).stdout
    assert f"{candidate}\trefs/heads/main" in ls
    assert f"{candidate}\trefs/heads/releases/v0.8" in ls
    assert any(f"{candidate}\trefs/tags/{tag}" in ls for tag in patch_tags)

    # Every release-chain identity fact binds the same candidate SHA.
    trace = [e for e in events if e["type"] == "milestone.trace_closed"][-1]["payload"]
    assert trace["candidate_sha"] == candidate
    assert trace["release_tag"] in patch_tags


# AC-FR0277-03@v0.8 TRACKS-TRACE dev prerelease only merges release branch with pre channel
def test_dev_prerelease_only(host_repo, trac, event_log, ci_echo_standin):
    # Precheck face (real): dev fails closed without an active release branch
    # and passes with one (IF-JOURNEY-001).
    ok, reason = dev_precheck(_DEV_CONTRACT, {"minor": "0.8"}, active_release_branch=None)
    assert ok is False
    assert "no active release branch" in reason
    ok, _reason = dev_precheck(
        _DEV_CONTRACT, {"minor": "0.8"}, active_release_branch="releases/v0.8"
    )
    assert ok is True
    plan = build_operation_plan(
        _DEV_CONTRACT,
        "dev",
        {"minor": "0.8", "n": "1", "ulid": "RUN"},
        active_release_branch="releases/v0.8",
    )
    assert "merge:releases/v0.8" in plan["steps"]
    assert any("-pre" in step for step in plan["steps"])
    assert not any(step == "tag:v0.8.1" for step in plan["steps"])

    _seed_hotfix_host(host_repo)
    # Dev publishes ONLY the release branch + pre tag: an empty remote (no
    # pre-pushed main) is the Arrange that proves no main merge happened.
    bare, _initial = init_bare_remote(host_repo, "journey_bare3.git", push_main=False)
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
    candidate = next(
        e["payload"]["candidate_sha"] for e in events if e["type"] == "candidate.frozen"
    )
    executed = [
        e
        for e in events
        if e["type"] == "publish.executed" and e["payload"].get("status") == "done"
    ]
    assert executed and all(e["payload"]["candidate_sha"] == candidate for e in executed)
    targets = {e["payload"]["target"] for e in executed}
    # Dev publishes ONLY the active release branch + the pre-release tag: no
    # public tag/release object, no main merge.
    assert "releases/v0.8" in targets
    pre_tags = [t for t in targets if t.startswith("v0.8.") and "-pre" in t]
    assert pre_tags, f"pre-release tag must be published, got {targets}"
    assert "main" not in targets
    assert not [t for t in targets if re.fullmatch(r"v0\.8\.\d+", t)]
    assert ci_echo_standin.releases == {}

    ls = subprocess.run(
        ["git", "ls-remote", str(bare)], capture_output=True, text=True, check=True
    ).stdout
    assert f"{candidate}\trefs/heads/releases/v0.8" in ls
    assert any(f"{candidate}\trefs/tags/{tag}" in ls for tag in pre_tags)
    assert "refs/heads/main" not in ls
    report = generate_report_md(trac, host_repo)
    assert "channel=pre-release" in report or "pre-release" in report.lower()


# AC-FR0277-04@v0.8 TRACKS-TRACE identity and idempotent journeys
def test_identity_and_idempotent_journeys(host_repo, trac, event_log, ci_echo_standin):
    from tests.e2e.helpers import replay_execute_publish

    # Module face (real reconcile verdicts; the old stub expected
    # NotImplementedError).
    assert reconcile_operation({"idempotency_key": "k"}, {"exists": False}) == "pending"
    assert (
        reconcile_operation(
            {"idempotency_key": "k"}, {"exists": True, "matches": True}
        )
        == "skip"
    )

    bare, _initial = init_bare_remote(host_repo, "journey_bare4.git")
    _bind_github_origin(host_repo, bare, ci_echo_standin.repo)
    run_id = walk_to_awaiting_release(
        trac,
        pre_seed_hook=lambda repo: _declare_journey_contract(
            repo, ["merge:main", "tag:{feature_tag}", "release:{feature_tag}"]
        ),
    )
    assert trac("release", "--action", "release").returncode == 0
    assert trac("run").returncode == 0

    events = event_log(run_id)
    candidate = next(
        e["payload"]["candidate_sha"] for e in events if e["type"] == "candidate.frozen"
    )
    # One shared candidate identity: every candidate_sha-bearing event binds it.
    for ev in events:
        if "candidate_sha" in ev["payload"]:
            assert ev["payload"]["candidate_sha"] == candidate, (
                f"{ev['type']} binds a foreign candidate"
            )
    done = [
        e
        for e in events
        if e["type"] == "publish.executed" and e["payload"].get("status") == "done"
    ]
    assert done, "the first publish must complete before the idempotent replay"

    # Idempotent resume (the runtime D-13 recovery replays the SAME issued
    # execute_publish with reconcile=True): per-operation reconciled_skip,
    # same keys, no duplicate done and no remote duplication.
    replay_execute_publish(host_repo, run_id)
    events2 = event_log(run_id)
    skipped = [
        e
        for e in events2
        if e["type"] == "publish.executed"
        and e["payload"].get("status") == "reconciled_skip"
    ]
    assert skipped, "reconciled_skip required for the idempotent journey resume"
    done_keys = {e["payload"]["idempotency_key"] for e in done}
    skipped_keys = {e["payload"]["idempotency_key"] for e in skipped}
    assert skipped_keys == done_keys
    for skip in skipped:
        assert skip["payload"]["idempotency_key"].startswith("sha256:")
        assert skip["payload"]["candidate_sha"] == candidate
    done_after = [
        e
        for e in events2
        if e["type"] == "publish.executed" and e["payload"].get("status") == "done"
    ]
    assert len(done_after) == len(done)
    assert "reconciled_skip" in trac("replay").stdout


# AC-FR0277-04@v0.8 TRACKS-TRACE dev precheck fails without release branch
def test_dev_precheck_fails_without_release_branch(host_repo, trac, event_log):
    from tests._support.hotfix_support import seed_host_issues

    seed_host_issues(host_repo)
    result = trac("hotfix", "42", "--scenario", "dev")
    # Fail closed: non-zero exit, the no-active-release-branch reason on the
    # triage audit, the rejection terminal state, and no fix branch created.
    assert result.returncode == 1, result.stderr
    assert "no_active_release_branch" in result.stdout
    assert "no_active_release_branch" in result.stderr
    status = trac("status")
    assert "terminal=rejected" in status.stdout
    assert "stage=M-HOTFIX-TRIAGE" in status.stdout
    replay = trac("replay")
    assert "no_active_release_branch" in replay.stdout
    assert '"status": "rejected"' in replay.stdout
    events = event_log()
    rejected = [e for e in events if e["type"] == "run.completed"]
    assert rejected and rejected[-1]["payload"]["terminal_state"] == "rejected"
    assert rejected[-1]["payload"]["reason"] == "no_active_release_branch"
    assert not [e for e in events if e["type"] == "branch.created"]
    branches = subprocess.run(
        ["git", "branch", "--list"], cwd=host_repo, capture_output=True, text=True, check=True
    ).stdout
    assert "fix/" not in branches
