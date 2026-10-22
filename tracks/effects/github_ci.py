"""GitHub required-CI API readback (FR-0270, IF-VERIFY-004).

GET actions/runs filtered by head_sha == candidate; validate the four-tuple
binding and required-check success; fail closed on mismatch/missing/stale.
missing_token stays a classified error (attention.required), never silent.
The public surface remains ``tracks.effects.github`` (re-exported).
"""

from __future__ import annotations

from pathlib import Path

from tracks.effects.github_transport import _api_base, _require_token


def _facade():
    """The re-exporting facade, resolved lazily to keep imports cycle-free."""
    from tracks.effects import github

    return github


def _workflow_matches(run: dict, requested: str) -> bool:
    """Match the configured workflow by path or immutable workflow id
    (``name`` is a display label two files can share; the basename is
    accepted because the host contract commonly supplies ``ci.yml``)."""
    requested = str(requested or "").strip()
    if not requested:
        return False
    workflow_id = run.get("workflow_id")
    if workflow_id is not None and str(workflow_id) == requested:
        return True
    path = str(run.get("path") or "").strip()
    if not path:
        return False
    return path == requested or Path(path).name == Path(requested).name and (
        "/" not in requested or path.endswith("/" + requested.lstrip("/"))
    )


def _workflow_job_page(
    base: str,
    repo_id: str,
    run_id: int,
    page: int,
    checks: dict[str, object],
    expected_total: int | None,
) -> tuple[int, int | None, bool]:
    """Fetch one job page and merge it, rejecting malformed/duplicate jobs."""
    url = f"{base}/repos/{repo_id}/actions/runs/{run_id}/jobs?per_page=100&page={page}"
    data = _facade()._get_any(_facade()._api_request(url, "GET"))
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        return 0, expected_total, False
    total_count = data.get("total_count")
    if total_count is not None and (
        not isinstance(total_count, int) or isinstance(total_count, bool)
    ):
        return 0, expected_total, False
    if expected_total is not None and total_count not in (None, expected_total):
        return 0, expected_total, False
    expected_total = total_count if total_count is not None else expected_total
    jobs = data["jobs"]
    for job in jobs:
        if not isinstance(job, dict) or not job.get("name"):
            return 0, expected_total, False
        name = str(job["name"])
        if name in checks:
            return 0, expected_total, False
        checks[name] = job.get("conclusion")
    return len(jobs), expected_total, True


def _workflow_jobs_complete(
    checks: dict[str, object], page_size: int, expected_total: int | None
) -> bool | None:
    """Return True/False when pagination is decided, else None to continue."""
    if expected_total is None:
        return True if page_size < 100 else None
    if len(checks) > expected_total:
        return False
    if len(checks) == expected_total:
        return True
    return False if page_size == 0 else None


def _workflow_jobs(base: str, repo_id: str, run_id: int) -> tuple[dict, bool]:
    """Read all bounded workflow-job pages and return name -> conclusion.

    A full page is followed until a short page is returned.  Hitting the
    bound fails closed so a partial response cannot be treated as complete.
    """
    checks: dict[str, object] = {}
    expected_total: int | None = None
    for page in range(1, 11):
        page_size, expected_total, valid = _facade()._workflow_job_page(
            base, repo_id, run_id, page, checks, expected_total
        )
        if not valid:
            return {}, False
        complete = _workflow_jobs_complete(checks, page_size, expected_total)
        if complete is True:
            return checks, True
        if complete is False:
            return {}, False
    return {}, False


def _ci_observed_payload(
    repo_id: str,
    workflow: str,
    candidate_sha: str,
    *,
    run: dict | None = None,
    head_sha=None,
    run_id=None,
    conclusion=None,
    status: str = "failed",
    reason: str | None = None,
    checks: dict | None = None,
    api_verified: bool = False,
) -> dict:
    """Build the stable CI observation shape for both run and failure paths."""
    payload = {
        "repo": repo_id,
        "workflow": workflow,
        "workflow_name": run.get("name") if run else None,
        "head_sha": head_sha,
        "run_id": run_id,
        "run_status": run.get("status") if run else None,
        "candidate_sha": candidate_sha,
        "conclusion": conclusion,
        "status": status,
        "api_verified": api_verified,
    }
    if run is not None:
        payload.update(
            {
                "workflow_path": run.get("path"),
                "workflow_id": run.get("workflow_id"),
            }
        )
    if checks is not None:
        payload["checks"] = checks
    if reason is not None:
        payload["reason"] = reason
    return payload


def _select_workflow_run(data: object, workflow: str) -> tuple[dict | None, str | None]:
    """Select one workflow run, returning a closed response error when absent."""
    if not isinstance(data, dict):
        return None, "malformed"
    runs = data.get("workflow_runs")
    if not isinstance(runs, list):
        return None, "malformed"
    matching = [
        run for run in runs if isinstance(run, dict) and _workflow_matches(run, workflow)
    ]
    if matching:
        return matching[0], None
    return None, "workflow_mismatch" if runs else "missing"


def _workflow_run_error(run: dict) -> tuple[str | None, object, object]:
    """Validate provider run identity/status and return (reason, head, id)."""
    run_id = run.get("id")
    head_sha = run.get("head_sha")
    if not isinstance(run_id, int) or isinstance(run_id, bool):
        return "malformed", head_sha, run_id
    if not isinstance(head_sha, str) or not head_sha:
        return "missing", head_sha, run_id
    if run.get("status") is not None and run.get("status") != "completed":
        return "run_incomplete", head_sha, run_id
    return None, head_sha, run_id


def readback_ci_run(repo_id: str, workflow: str, candidate_sha: str) -> dict:
    """GET /repos/{repo}/actions/runs filtered by head_sha; returns the
    normalized `ci.run_observed` payload (IF-VERIFY-004). Missing credentials
    raise a classified GithubIssuesError(missing_token) — never a silent pass."""
    _require_token()
    base = _api_base()
    url = f"{base}/repos/{repo_id}/actions/runs?head_sha={candidate_sha}&per_page=100"
    data = _facade()._get_any(_facade()._api_request(url, "GET"))
    run, selection_error = _select_workflow_run(data, workflow)
    if run is None:
        return _ci_observed_payload(
            repo_id, workflow, candidate_sha, reason=selection_error or "malformed"
        )
    reason, head_sha, run_id = _workflow_run_error(run)
    if reason is not None:
        return _ci_observed_payload(
            repo_id, workflow, candidate_sha, run=run, head_sha=head_sha,
            run_id=run_id, conclusion=run.get("conclusion"), reason=reason
        )
    return _readback_run_outcome(
        base, repo_id, workflow, candidate_sha, run, head_sha, run_id
    )


def _readback_run_outcome(
    base: str,
    repo_id: str,
    workflow: str,
    candidate_sha: str,
    run: dict,
    head_sha: object,
    run_id: object,
) -> dict:
    """Normalized payload for a completed run, including its job checks."""
    checks, jobs_complete = _facade()._workflow_jobs(base, repo_id, run_id)
    status = "passed" if run.get("conclusion") == "success" and jobs_complete else "failed"
    reason = None if jobs_complete else "jobs_incomplete"
    return _ci_observed_payload(
        repo_id,
        workflow,
        candidate_sha,
        run=run,
        head_sha=head_sha,
        run_id=run_id,
        conclusion=run.get("conclusion"),
        status=status,
        reason=reason,
        checks=checks,
        api_verified=jobs_complete,
    )


def judge_ci_binding(observed: dict | None, candidate_sha: str, required_checks: list) -> dict:
    """Pure fail-closed binding verdict over an observed CI run: the closed
    `ci.run_observed` payload, status {passed, failed}, reason in {mismatch,
    missing, stale, failed_conclusion, check_failed} when failed."""
    if observed is None:
        return {
            "head_sha": candidate_sha,
            "candidate_sha": candidate_sha,
            "status": "failed",
            "reason": "missing",
            "api_verified": False,
        }
    out = dict(observed)
    out["candidate_sha"] = candidate_sha
    if observed.get("stale"):
        out["status"] = "failed"
        out["reason"] = "stale"
        out["api_verified"] = False
        return out
    if not observed.get("head_sha"):
        out["status"] = "failed"
        out["reason"] = "missing"
        out["api_verified"] = False
        return out
    if observed.get("reason") == "jobs_incomplete":
        out["status"] = "failed"
        out["reason"] = "check_failed"
        out["api_verified"] = False
        return out
    if observed.get("reason") in {"malformed", "run_incomplete"}:
        out["status"] = "failed"
        out["api_verified"] = False
        return out
    head = observed.get("head_sha")
    if head != candidate_sha:
        out["status"] = "failed"
        out["reason"] = "mismatch"
        out["api_verified"] = False
        return out
    if observed.get("conclusion") != "success":
        out["status"] = "failed"
        out["reason"] = "failed_conclusion"
        out["api_verified"] = False
        return out
    checks = observed.get("checks") or {}
    for requirement in required_checks:
        if checks.get(requirement) != "success":
            out["status"] = "failed"
            out["reason"] = "check_failed"
            out["api_verified"] = False
            return out
    out["status"] = "passed"
    out["reason"] = "bound"
    out["api_verified"] = True
    return out
