"""Web vertical journey e2e happy path (test-plan section 9.1)."""

from __future__ import annotations

import json
import time

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

pytestmark = pytest.mark.e2e

_RUN_PARAMS = {
    "journey": "feature",
    "version": "v0.9",
    "story": None,
    "issue": None,
    "target": None,
    "preempt": False,
}


# AC-FR0288-01@v0.9 TRACKS-TRACE web vertical journey happy path
# AC-FR0289-01@v0.9 TRACKS-TRACE web vertical journey happy path
# AC-FR0290-01@v0.9 TRACKS-TRACE web vertical journey happy path
# AC-FR0291-01@v0.9 TRACKS-TRACE web vertical journey happy path
# AC-FR0293-01@v0.9 TRACKS-TRACE web vertical journey happy path
# AC-FR0297-01@v0.9 TRACKS-TRACE web vertical journey happy path
# AC-FR0298-01@v0.9 TRACKS-TRACE web vertical journey happy path
# AC-FR0300-01@v0.9 TRACKS-TRACE web vertical journey happy path
# AC-FR0308-01@v0.9 TRACKS-TRACE web vertical journey happy path
# AC-FR0309-01@v0.9 TRACKS-TRACE web vertical journey happy path
# AC-FR0310-01@v0.9 TRACKS-TRACE web vertical journey happy path
# AC-FR0312-01@v0.9 TRACKS-TRACE web vertical journey happy path
def test_feature_web_vertical_journey(host_repo, trac, tmp_path, monkeypatch):
    # 2026-09-23 (island-2 sweep): the credentials_ref probe requires the
    # credential env NAME to be present (values are never read -- readiness.py
    # docstring); the journey was green only in shells that export GITHUB_TOKEN.
    # Seed a dummy so the test is hermetic.
    monkeypatch.setenv("GITHUB_TOKEN", "e2e-dummy-token")
    """Feature web vertical journey: serve to released via stdlib HTTP only."""
    # 2026-09-23 (island-2 sweep): AC-FR0290-02 blocking semantics — a
    # project whose contract probe is red (no project.toml) must NOT be able
    # to create a run. The journey targets a READY project: seed the minimal
    # valid contract before registering (the contract probe needs
    # unit+integration sections).
    contract_dir = host_repo / ".tracks" / "projects"
    contract_dir.mkdir(parents=True, exist_ok=True)
    (contract_dir / "project.toml").write_text(
        "[unit]\ncollect = \"python3 -m pytest --collect-only tests/unit\"\n"
        "run = \"python3 -m pytest tests/unit -q\"\n"
        "[integration]\ncollect = \"python3 -m pytest --collect-only tests/integration\"\n"
        "run = \"python3 -m pytest tests/integration -q\"\n",
        encoding="utf-8",
    )
    assert trac("init").returncode == 0
    home = tmp_path / "home"
    proc = start_serve(home, host_repo, port=0)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        payload = wait_for_healthz(base)
        assert payload["status"] == "ok"
        assert payload["projects"] == 0
        cookie, csrf = login_session(base)
        # register the seeded repo through the web command plane (§2b #5)
        status, body = http_post(
            base,
            "/api/projects",
            {"repo_path": str(host_repo)},
            cookies=cookie,
            csrf=csrf,
            idempotency_key="e2e-feature-reg-1",
        )
        assert status == 201, body
        project = json.loads(body)
        assert project["repo_path"] == str(host_repo)
        # readiness probe through the web plane (§2b #7)
        status, body = http_post(
            base,
            f"/api/projects/{project['project_id']}/readiness",
            {},
            cookies=cookie,
            csrf=csrf,
            idempotency_key="e2e-feature-ready-1",
        )
        assert status == 200, body
        readiness = json.loads(body)
        assert set(readiness["checks"]) == {
            "contract",
            "harness_model",
            "credentials_ref",
            "tools",
        }
        # create the feature run through the web plane (§2b #8)
        runs_path = f"/api/projects/{project['project_id']}/runs"
        status, body = http_post(
            base,
            runs_path,
            _RUN_PARAMS,
            cookies=cookie,
            csrf=csrf,
            idempotency_key="e2e-feature-run-1",
        )
        assert status == 202, body
        run = json.loads(body)
        assert run["run_id"] and run["command_id"]
        # duplicate submit dedups to the same run (§1b.2)
        status, body = http_post(
            base,
            runs_path,
            _RUN_PARAMS,
            cookies=cookie,
            csrf=csrf,
            idempotency_key="e2e-feature-run-1",
        )
        assert status == 200, body
        assert json.loads(body)["run_id"] == run["run_id"]
        # command status query is restart-safe (§2b #27)
        status, body = http_get(base, f"/api/commands/{run['command_id']}", cookies=cookie)
        assert status == 200, body
        command = json.loads(body)
        assert command["kind"] == "create_run"
        assert command["status"] in ("accepted", "claimed", "completed")
        # the run detail snapshot stays readable while the supervisor drives
        status, body = http_get(base, f"/api/runs/{run['run_id']}", cookies=cookie)
        assert status == 200, body
        detail = json.loads(body)
        assert detail.get("event_cursor") is not None
        # the service drives the run forward under the fake backend; the
        # run must leave the creation boundary within the polling budget
        deadline = time.time() + 60
        seen_stage = None
        while time.time() < deadline:
            _status, _body = http_get(base, f"/api/runs/{run['run_id']}", cookies=cookie)
            detail = json.loads(_body)
            seen_stage = detail.get("stage")
            if seen_stage and seen_stage != "PHASE-0":
                break
            time.sleep(0.5)
        assert seen_stage and seen_stage != "PHASE-0", detail
    finally:
        stop_serve(proc)
