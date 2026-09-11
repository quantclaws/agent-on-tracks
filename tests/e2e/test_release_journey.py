"""E2E: feature release happy path (v0.8, test-plan §11.1).

b93 §8.1 bootstrap contract: bare ``trac run`` bootstrap is forbidden; the
journey is driven through the shared walkers over a real bare remote and the
loopback GitHub stand-in (CI readback + release objects). The real CLI,
kernel, executor and M-MILESTONE producers run end to end — the agent
channel is the deterministic fake, the remote/API are documented stand-ins.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.e2e.helpers import (
    generate_report_md,
    init_bare_remote,
    walk_to_awaiting_release,
)
from tests.integration.test_journey_versioning import (
    _bind_github_origin,
    _declare_journey_contract,
)

pytestmark = pytest.mark.e2e


# AC-FR0267-01@v0.8 TRACKS-TRACE feature release journey candidate frozen
# AC-FR0268-01@v0.8 TRACKS-TRACE feature release reuses full_f when eligible
# AC-FR0270-01@v0.8 TRACKS-TRACE feature release binds CI
# AC-FR0273-01@v0.8 TRACKS-TRACE feature release preview binds all
# AC-FR0274-01@v0.8 TRACKS-TRACE feature release human gate
# AC-FR0275-01@v0.8 TRACKS-TRACE feature release publish done
# AC-FR0276-01@v0.8 TRACKS-TRACE feature release milestone sealed and trace closed
# AC-FR0277-01@v0.8 TRACKS-TRACE feature public release via main
# AC-NFR0143-02@v0.8 TRACKS-TRACE feature release trace exported
def test_feature_release_journey(host_repo, trac, event_log, ci_echo_standin):
    # Seed: real bare remote bound as the GitHub origin + the declared
    # feature operation plan (merge main / public tag / release object).
    bare, _initial = init_bare_remote(host_repo, "e2e_bare.git")
    _bind_github_origin(host_repo, bare, ci_echo_standin.repo)
    run_id = walk_to_awaiting_release(
        trac,
        pre_seed_hook=lambda repo: _declare_journey_contract(
            repo, ["merge:main", "tag:{feature_tag}", "release:{feature_tag}"]
        ),
    )
    assert run_id

    # The independent release CLI exposes the preview bound to the candidate
    # before the Human decision.
    preview_cli = trac("release", "preview")
    assert "preview_digest=" in preview_cli.stdout
    assert "candidate=" in preview_cli.stdout

    assert trac("release", "--action", "release").returncode == 0
    assert trac("run").returncode == 0

    events = event_log(run_id)
    frozen = [e for e in events if e["type"] == "candidate.frozen"]
    assert frozen and frozen[0]["payload"]["clean_tree"] is True
    candidate = frozen[0]["payload"]["candidate_sha"]
    assert len(candidate) == 40

    # FULL_F reuse on the M-IMPL evidence: no local re-run, identity bound.
    reused = [
        e
        for e in events
        if e["type"] == "evidence.reused" and e["payload"].get("kind") == "full_f"
    ]
    assert reused, "the eligible FULL_F must be reused"
    assert reused[0]["payload"]["candidate_sha"] == candidate

    # CI readback binds the four-tuple: head SHA == candidate, api verified.
    ci = [e for e in events if e["type"] == "ci.run_observed"][-1]["payload"]
    assert ci["api_verified"] is True
    assert ci["head_sha"] == ci["candidate_sha"] == candidate

    # Security assessment and the same-candidate Prism verdict precede the
    # preview; the preview digest binds the candidate.
    assert [e for e in events if e["type"] == "security.assessed"]
    assert any(
        e["payload"].get("verdict") == "pass"
        for e in events
        if e["type"] == "prism.verdict"
    )
    previews = [e for e in events if e["type"] == "release.previewed"]
    assert previews and previews[-1]["payload"]["preview_digest"].startswith("sha256:")
    assert previews[-1]["payload"]["candidate_sha"] == candidate
    decided = [e for e in events if e["type"] == "release.decided"]
    assert decided and decided[-1]["payload"]["action"] == "release"
    preview_digest = decided[-1]["payload"]["preview_digest"]

    # M-PUBLISH: merge main + public tag + release object, all done against
    # the same candidate with sha256 idempotency keys.
    executed = [
        e
        for e in events
        if e["type"] == "publish.executed" and e["payload"].get("status") == "done"
    ]
    targets = {e["payload"]["target"] for e in executed}
    assert {"main", "v0.8.0"} <= targets, targets
    assert all(e["payload"]["candidate_sha"] == candidate for e in executed)
    assert all(
        e["payload"]["idempotency_key"].startswith("sha256:") for e in executed
    )

    # M-MILESTONE seals the trace on the same candidate and cleans refs.
    trace = [e for e in events if e["type"] == "milestone.trace_closed"][-1]["payload"]
    assert trace["candidate_sha"] == candidate
    assert trace["release_tag"] == "v0.8.0"
    assert trace["preview_digest"] == preview_digest
    assert [e for e in events if e["type"] == "milestone.sealed"]
    assert [e for e in events if e["type"] == "refs.cleaned"]
    completed = [e for e in events if e["type"] == "run.completed"][-1]["payload"]
    assert completed["terminal_state"] == "released"
    assert completed["release_tag"] == "v0.8.0"

    # Status projects the released identity; every candidate_sha-bearing
    # event binds the one frozen candidate (NFR-0143).
    status = trac("status")
    assert "terminal=released" in status.stdout
    assert "full_reuse=full_f" in status.stdout
    assert "ci=bound" in status.stdout
    for event in events:
        if "candidate_sha" in event["payload"]:
            assert event["payload"]["candidate_sha"] == candidate, event["type"]

    # Remote mutual verification: main and the public tag point at the
    # candidate; the stand-in release object targets the same commit.
    ls = subprocess.run(
        ["git", "ls-remote", str(bare)], capture_output=True, text=True, check=True
    ).stdout
    assert f"{candidate}\trefs/heads/main" in ls
    assert f"{candidate}\trefs/tags/v0.8.0" in ls
    release = ci_echo_standin.releases.get("v0.8.0")
    assert release is not None and release["target_commitish"] == candidate
    assert release["prerelease"] is False

    # Report exports the same trace identity.
    report = generate_report_md(trac, host_repo)
    assert "Release trace" in report
    assert trace["trace_digest"] in report
    assert candidate in report
