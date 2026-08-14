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

from tracks.frontmatter import split_frontmatter

_ITEM_HEAD = re.compile(r"^### ((?:N?FR)-\d{4})[ \t]*(.*)$")
_ACC_SECTION = re.compile(r"^## ((?:N?FR)-\d{4})\b")
_AC_HEAD = re.compile(r"^### (AC-N?FR\d{4}-\d+)[ \t]*(.*)$")


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


class GithubBackend:
    """Live channel: GitHub REST via GITHUB_TOKEN / TRAC_GITHUB_REPO (owner/name);
    Project association via TRAC_GITHUB_PROJECT."""

    def __init__(self, repo: Path, version: str):
        self.token = os.environ["GITHUB_TOKEN"]
        self.gh_repo = os.environ.get("TRAC_GITHUB_REPO", "")
        self.project = os.environ.get("TRAC_GITHUB_PROJECT", "")
        if not self.gh_repo:
            raise GithubIssuesError("not_found", "TRAC_GITHUB_REPO not set (owner/name)")

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
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            cls = {401: "auth", 403: "rate_limit", 404: "not_found", 429: "rate_limit"}.get(
                e.code, "network"
            )
            raise GithubIssuesError(cls, f"HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise GithubIssuesError("network", str(e.reason)) from e

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


def select_issue_backend(repo: Path, version: str):
    """Boundary selection (D-05): fake channel / missing token -> stand-in."""
    if (
        os.environ.get("TRAC_FAKE_SIMULATE")
        or os.environ.get("TRAC_AGENT_BACKEND", "").strip().lower() == "fake"
        or not os.environ.get("GITHUB_TOKEN")
    ):
        return FakeIssueBackend(repo, version)
    return GithubBackend(repo, version)
