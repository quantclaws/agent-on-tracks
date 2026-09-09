"""GitHub Issues effect boundary (FR-0200, design D-04~D-07).

Env (`GITHUB_TOKEN`, `TRAC_GITHUB_REPO`, `TRAC_GITHUB_PROJECT`) is read ONLY
here (D-05); kernel `decide()`/`project()` never see it. The fake channel
(`TRAC_AGENT_BACKEND=fake` / `TRAC_FAKE_SIMULATE` / missing token) uses a
deterministic stand-in that never touches the network.

Split granularity (D-04): one Issue per FR and per NFR — title
`[FR-XXXX] 标题`, body = item text + its AC list + the baseline digest.
Issues are requirement-tracking identities, not execution units (D-07).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

from tracks.frontmatter import split_frontmatter

if TYPE_CHECKING:
    from tracks.executor.hotfix import HostIssue

_ITEM_HEAD = re.compile(r"^### ((?:N?FR)-\d{4})[ \t]*(.*)$")
_ACC_SECTION = re.compile(r"^## ((?:N?FR)-\d{4})\b")
_AC_HEAD = re.compile(r"^### (AC-N?FR\d{4}-\d+)[ \t]*(.*)$")

# HTTP status -> GithubIssuesError classification (shared by _request/_get).
_HTTP_ERROR_CLASSES = {401: "auth", 403: "rate_limit", 404: "not_found", 429: "rate_limit"}


class GithubIssuesError(RuntimeError):
    """Classified GitHub failure: auth | network | rate_limit | not_found."""

    def __init__(self, classification: str, message: str):
        super().__init__(message)
        self.classification = classification


def _item_blocks(spec_body: str) -> list:
    """[(item_id, title, block_text)] for every `### FR/NFR-XXXX` spec item."""
    items, cur = [], None
    for line in spec_body.splitlines():
        m = _ITEM_HEAD.match(line)
        if m:
            cur = [m.group(1), m.group(2).strip(), []]
            items.append(cur)
            continue
        if line.startswith(("# ", "## ", "### ")):
            cur = None
        elif cur is not None and not line.startswith(">"):
            cur[2].append(line)
    return [(iid, title, "\n".join(body).strip()) for iid, title, body in items]


def _ac_index(acc_body: str) -> dict:
    """item_id -> [\"AC-... title\"] from the acceptance sections."""
    index: dict = {}
    section = None
    for line in acc_body.splitlines():
        m = _ACC_SECTION.match(line)
        if m:
            section = index.setdefault(m.group(1), [])
            continue
        if line.startswith("## "):
            section = None
            continue
        m = _AC_HEAD.match(line)
        if m and section is not None:
            section.append(f"{m.group(1)} {m.group(2).strip()}".strip())
    return index


def issue_items(vdir: Path, digest: str) -> list:
    """[(item_id, title, body)] — one entry per FR/NFR (D-04)."""
    _, spec_body = split_frontmatter((vdir / "spec.md").read_text(encoding="utf-8"))
    _, acc_body = split_frontmatter((vdir / "acceptance.md").read_text(encoding="utf-8"))
    acs = _ac_index(acc_body)
    out = []
    for iid, title, block in _item_blocks(spec_body):
        ac_list = "\n".join(f"- {ac}" for ac in acs.get(iid, []))
        body = f"{block}\n\n## Acceptance criteria\n{ac_list}\n\nbaseline digest: {digest}\n"
        out.append((iid, f"[{iid}] {title}".strip(), body))
    return out


class FakeIssueBackend:
    """Deterministic stand-in (D-05): no network; issues recorded in
    `.tracks/runtime/issues.json`. `TRAC_FAKE_SIMULATE` key `github:create`
    injects failures per create call (`fail` token, `|`-sequenced)."""

    def __init__(self, repo: Path, version: str):
        self.repo = repo
        self.version = version
        self.project = os.environ.get("TRAC_GITHUB_PROJECT", "fake-project")
        self._path = repo / ".tracks" / "runtime" / "issues.json"
        self._host_path = repo / ".tracks" / "runtime" / "host-issues.json"
        self._calls = 0

    def _token(self) -> str:
        raw = os.environ.get("TRAC_FAKE_SIMULATE", "")
        seq = None
        for part in raw.replace(",", ";").split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                if k.strip() == "github:create":
                    seq = [t.strip() for t in v.split("|")]
        if not seq:
            return "ok"
        token = seq[min(self._calls, len(seq) - 1)]
        self._calls += 1
        return token

    def create_issue(self, title: str, body: str, labels: list) -> str:
        if self._token() == "fail":
            raise GithubIssuesError("network", "simulated network failure")
        issues = {}
        if self._path.exists():
            issues = json.loads(self._path.read_text(encoding="utf-8"))
        issue_id = f"FAKE-{len(issues) + 1}"
        issues[issue_id] = {"title": title, "body": body, "labels": labels}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(issues, ensure_ascii=False, indent=1), encoding="utf-8")
        return issue_id

    def add_to_project(self, issue_id: str, project: str) -> None:
        pass  # deterministic no-op: association is implied by self.project

    def fetch_issue(self, issue_number: int) -> HostIssue | None:
        """Read channel (IF-HOTFIX-003): the host issue seed at
        ``.tracks/runtime/host-issues.json`` (interfaces.md §3b schema).

        Top-level keys are decimal issue numbers; a missing file or key
        yields None (PRECHECK P-1 maps that to issue_not_found). No
        ``TRAC_FAKE_SIMULATE`` injection here — fetch failures are
        constructed via a missing seed file (ARCH-006 §3.1).
        """
        from tracks.executor.hotfix import HostIssue

        if not self._host_path.exists():
            return None
        try:
            data = json.loads(self._host_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        entry = data.get(str(issue_number))
        if not isinstance(entry, dict):
            return None
        labels = entry.get("labels") or []
        if not isinstance(labels, list):
            labels = []
        return HostIssue(
            number=issue_number,
            title=entry.get("title", ""),
            body=entry.get("body", ""),
            labels=tuple(str(label) for label in labels),
        )


class GithubBackend:
    """Live channel: GitHub REST via GITHUB_TOKEN / TRAC_GITHUB_REPO (owner/name);
    Project association via TRAC_GITHUB_PROJECT."""

    def __init__(self, repo: Path, version: str):
        self.token = os.environ["GITHUB_TOKEN"]
        self.gh_repo = os.environ.get("TRAC_GITHUB_REPO", "")
        self.project = os.environ.get("TRAC_GITHUB_PROJECT", "")
        if not self.gh_repo:
            raise GithubIssuesError("not_found", "TRAC_GITHUB_REPO not set (owner/name)")

    def _urlopen(self, req: urllib.request.Request) -> dict:
        """Perform a JSON request and classify HTTP/URL errors.

        Shared by ``_request`` (POST) and ``_get`` (GET) to avoid duplicating
        the error-mapping logic.  Returns the parsed JSON body on success;
        raises ``GithubIssuesError`` with the appropriate classification.
        """
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            cls = _HTTP_ERROR_CLASSES.get(e.code, "network")
            raise GithubIssuesError(cls, f"HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise GithubIssuesError("network", str(e.reason)) from e

    def _request(self, url: str, payload: dict) -> dict:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
            },
        )
        return self._urlopen(req)

    def _get(self, url: str) -> dict:
        """GET helper (IF-HOTFIX-003): read-only request, same auth/error
        pattern as ``_request`` but with ``method=GET`` and no body."""
        req = urllib.request.Request(
            url,
            method="GET",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
            },
        )
        return self._urlopen(req)

    def create_issue(self, title: str, body: str, labels: list) -> str:
        data = self._request(
            f"https://api.github.com/repos/{self.gh_repo}/issues",
            {"title": title, "body": body, "labels": labels},
        )
        return str(data["number"])

    def add_to_project(self, issue_id: str, project: str) -> None:
        if not project:
            return
        self._request(
            f"https://api.github.com/projects/columns/{project}/cards",
            {"content_id": int(issue_id), "content_type": "Issue"},
        )

    def fetch_issue(self, issue_number: int) -> HostIssue | None:
        """Read channel (IF-HOTFIX-003): ``GET /repos/{repo}/issues/{n}``
        (ARCH-006 §3.1). 404 maps to None (PRECHECK issue_not_found);
        auth/rate-limit/network failures raise :class:`GithubIssuesError`
        (fail-closed; issue_fetch_failed keeps the retry path open)."""
        from tracks.executor.hotfix import HostIssue

        try:
            data = self._get(
                f"https://api.github.com/repos/{self.gh_repo}/issues/{issue_number}"
            )
        except GithubIssuesError as exc:
            if exc.classification == "not_found":
                return None
            raise
        labels = []
        for label in data.get("labels") or []:
            if isinstance(label, dict) and label.get("name"):
                labels.append(str(label["name"]))
        return HostIssue(
            number=issue_number,
            title=data.get("title", ""),
            body=data.get("body", ""),
            labels=tuple(labels),
        )


def select_issue_backend(repo: Path, version: str):
    """Boundary selection (D-05): explicit fake channel -> stand-in.

    A live agent channel without GITHUB_TOKEN fails closed with the
    classified ``missing_token`` error (AC-FR0270-03, readback precedent) —
    the stale silent Fake fallback on a missing token is removed: a fake
    stand-in may only be selected deliberately, never by missing secrets.
    """
    if (
        os.environ.get("TRAC_FAKE_SIMULATE")
        or os.environ.get("TRAC_AGENT_BACKEND", "").strip().lower() == "fake"
    ):
        return FakeIssueBackend(repo, version)
    if not os.environ.get("GITHUB_TOKEN"):
        raise GithubIssuesError("missing_token", "GITHUB_TOKEN not set")
    return GithubBackend(repo, version)


# -- IF-VERIFY-004 required-CI API readback (FR-0270) --------------------------
#
# GET actions/runs, filter by head_sha == candidate, validate the four-tuple
# binding and required-check success; fail closed on mismatch/missing/stale and
# never silently pass without credentials (missing_token stays a classified
# error so the caller can surface attention.required).


def _api_base() -> str:
    """Explicit stand-in override or the real GitHub REST base."""
    return os.environ.get("TRAC_GITHUB_API_BASE", "https://api.github.com").rstrip("/")


def _workflow_matches(run: dict, requested: str) -> bool:
    """Match the configured workflow by path or immutable workflow id.

    GitHub's ``name`` is a display label and is not a workflow-file identity;
    two files can share it.  The host contract commonly supplies ``ci.yml``,
    so the basename is accepted when the API returns the full workflow path.
    """
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
    url = (
        f"{base}/repos/{repo_id}/actions/runs/{run_id}/jobs"
        f"?per_page=100&page={page}"
    )
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
        },
    )
    data = _get_any(req)
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
        page_size, expected_total, valid = _workflow_job_page(
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
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise GithubIssuesError("missing_token", "GITHUB_TOKEN not set")
    base = _api_base()
    url = (
        f"{base}/repos/{repo_id}/actions/runs"
        f"?head_sha={candidate_sha}&per_page=100"
    )
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
    )
    data = _get_any(req)
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
    checks, jobs_complete = ({}, False)
    checks, jobs_complete = _workflow_jobs(base, repo_id, run_id)
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


def _get_any(req) -> dict:
    """urlopen wrapper (module-level; mirrors _urlopen but without backend
    state). Returns the decoded JSON body."""
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (test stand-in base)
        return json.loads(resp.read().decode("utf-8"))


def judge_ci_binding(observed: dict | None, candidate_sha: str, required_checks: list) -> dict:
    """Pure fail-closed binding verdict over an observed CI run.

    Returns the closed `ci.run_observed` payload with status in
    {passed, failed} and reason in {mismatch, missing, stale, failed_conclusion,
    check_failed} when failed.
    """
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


# -- IF-VERIFY-004 issue creation verification & authoritative map (FR-0270) ---
#
# Create-then-verify issue flow plus the authoritative issue map consumed by
# the milestone closers (issue_number + api_verified vocabulary). FAKE
# artifacts are rejected in the real channel and can never claim
# api_verified=true, so unverified mappings can never close work.


def readback_issue(repo_id: str, issue_number: int) -> dict:
    """GET /repos/{repo}/issues/{n} verified readback (AC-FR0270-01).

    Honors ``TRAC_GITHUB_API_BASE`` (explicit stand-in channel) like
    ``readback_ci_run``; missing credentials raise the classified
    ``missing_token`` error — never a silent pass. HTTP failures are
    classified (auth | rate_limit | not_found | network) and fail closed.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise GithubIssuesError("missing_token", "GITHUB_TOKEN not set")
    url = f"{_api_base()}/repos/{repo_id}/issues/{issue_number}"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        data = _get_any(req)
    except urllib.error.HTTPError as e:
        cls = _HTTP_ERROR_CLASSES.get(e.code, "network")
        raise GithubIssuesError(cls, f"HTTP {e.code}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise GithubIssuesError("network", str(e.reason)) from e
    return {
        "issue_number": data.get("number", issue_number),
        "title": data.get("title", ""),
        "state": data.get("state", ""),
        "api_verified": True,
    }


def create_issue_verified(backend, title: str, body: str, labels: list) -> dict:
    """Create an issue and verify it by an immediate API readback.

    Live channel: create -> GET the created issue -> mapping with
    ``api_verified=true`` (AC-FR0270-01). Fake stand-in channel: the
    deterministic stand-in can never verify (``api_verified=false``,
    AC-FR0270-02) — fake mappings must stay unclosable.
    """
    issue_id = backend.create_issue(title, body, labels)
    if isinstance(backend, FakeIssueBackend):
        return {"issue_number": issue_id, "api_verified": False}
    readback = readback_issue(backend.gh_repo, int(str(issue_id)))
    return {
        "issue_number": issue_id,
        "title": readback.get("title", ""),
        "state": readback.get("state", ""),
        "api_verified": bool(readback.get("api_verified")),
    }


def persist_issue_mapping(repo: Path, item_id: str, mapping: dict) -> dict:
    """Merge ``{item_id: mapping}`` into the authoritative issue map at
    ``.tracks/runtime/issue-map.json`` (AC-FR0270-01).

    Entries use the closer vocabulary (``issue_number`` + ``api_verified``,
    see milestone.close_issues_with_comment). A crash-retry re-persist dedups
    by item id — one entry per item, the last verified write wins.
    """
    path = repo / ".tracks" / "runtime" / "issue-map.json"
    data: dict = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            data = {}
    data[str(item_id)] = dict(mapping)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def reject_fake_artifact(issue_id) -> dict:
    """Real-channel fake-artifact rejection (AC-FR0270-02).

    A FAKE-prefixed id can never be API-verified and is rejected with the
    closed ``fake_rejected`` verdict so callers surface it as such instead of
    letting an unverified stand-in mapping enter the authoritative map.
    """
    sid = str(issue_id)
    if sid.startswith(("FAKE-", "fake")):
        return {
            "issue_id": sid,
            "status": "rejected",
            "reason": "fake_rejected",
            "api_verified": False,
        }
    return {
        "issue_id": sid,
        "status": "ok",
        "reason": "not_fake",
        "api_verified": False,
    }
