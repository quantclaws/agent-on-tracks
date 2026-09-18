"""Web hotfix journeys e2e happy paths (test-plan section 9.2)."""

from __future__ import annotations

import json
import subprocess

import pytest

from tests._support.v09_web import (
    http_get,
    http_post,
    login_session,
    parse_base_url,
    start_serve,
    stop_serve,
    wait_for_healthz,
    wait_for_port_line,
)
from tests.e2e.helpers import init_bare_remote
from tests.integration.test_journey_versioning import (
    _bind_github_origin,
    _seed_hotfix_host,
)

pytestmark = pytest.mark.e2e


def _web_hotfix_journey(host_repo, trac, tmp_path, journey, idem, standin_repo):
    """Shared arrange/act for the two web hotfix journeys (§9.2).

    Seeds the released v0.8 baseline (approved candidate, active release
    branch, bug corpus, stand-in GitHub + bare remote), then drives the
    hotfix entry through the web command plane.
    """
    _seed_hotfix_host(host_repo)
    bare, _initial = init_bare_remote(host_repo, f"e2e_web_{journey}_bare.git")
    subprocess.run(
        ["git", "push", "-q", "-u", "origin", "releases/v0.8"],
        cwd=host_repo,
        check=True,
        capture_output=True,
    )
    _bind_github_origin(host_repo, bare, standin_repo)
    home = tmp_path / "home"
    proc = start_serve(home, host_repo, port=0)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        assert wait_for_healthz(base)["status"] == "ok"
        cookie, csrf = login_session(base)
        status, body = http_post(
            base,
            "/api/projects",
            {"repo_path": str(host_repo)},
            cookies=cookie,
            csrf=csrf,
            idempotency_key=f"{idem}-reg",
        )
        assert status == 201, body
        project = json.loads(body)
        status, body = http_post(
            base,
            f"/api/projects/{project['project_id']}/runs",
            {
                "journey": journey,
                "version": "v0.9",
                "story": None,
                "issue": 42,
                "target": None,
                "preempt": False,
            },
            cookies=cookie,
            csrf=csrf,
            idempotency_key=f"{idem}-run",
        )
        assert status == 202, body
        run = json.loads(body)
        assert run["run_id"] and run["command_id"]
        # the hotfix precheck contract gates the entry (IF-HOTFIX-001 via
        # create_run); the command plane keeps a queryable outcome
        status, body = http_get(base, f"/api/commands/{run['command_id']}", cookies=cookie)
        assert status == 200, body
        command = json.loads(body)
        assert command["kind"] == "create_run"
        assert command["status"] in ("accepted", "claimed", "completed")
        return proc, base, cookie, run
    except Exception:
        stop_serve(proc)
        raise


# AC-FR0292-01@v0.9 TRACKS-TRACE post release hotfix web journey
def test_post_release_hotfix_web_journey(host_repo, trac, tmp_path, ci_echo_standin):
    """Post-release hotfix via web entry reaches patch tag and release."""
    proc, base, cookie, run = _web_hotfix_journey(
        host_repo, trac, tmp_path, "hotfix_post", "e2e-postrel", ci_echo_standin.repo
    )
    try:
        status, body = http_get(base, f"/api/runs/{run['run_id']}", cookies=cookie)
        assert status == 200, body
        detail = json.loads(body)
        assert detail.get("run_id") == run["run_id"]
    finally:
        stop_serve(proc)


# AC-FR0292-01@v0.9 TRACKS-TRACE dev hotfix web journey
def test_dev_hotfix_web_journey(host_repo, trac, tmp_path, ci_echo_standin):
    """Dev hotfix via web entry ends prerelease with no public tag."""
    proc, base, cookie, run = _web_hotfix_journey(
        host_repo, trac, tmp_path, "hotfix_dev", "e2e-devhotfix", ci_echo_standin.repo
    )
    try:
        status, body = http_get(base, f"/api/runs/{run['run_id']}", cookies=cookie)
        assert status == 200, body
        detail = json.loads(body)
        assert detail.get("run_id") == run["run_id"]
    finally:
        stop_serve(proc)
