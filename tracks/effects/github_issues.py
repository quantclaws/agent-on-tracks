"""GitHub issue spec parsing, verified creation, and authoritative map.

Split granularity (D-04): one Issue per FR and per NFR — title
`[FR-XXXX] 标题`, body = item text + its AC list + the baseline digest.
Issues are requirement-tracking identities, not execution units (D-07).

This module owns the spec/item parsing, the create-then-verify flow
(FR-0270, IF-VERIFY-004), and the authoritative issue map; FAKE artifacts can
never claim api_verified. The public surface remains ``tracks.effects.github``
(re-exported).
"""

from __future__ import annotations

import json
import re
import urllib.error
from pathlib import Path

from tracks.effects.github_transport import (
    _HTTP_ERROR_CLASSES,
    FakeIssueBackend,
    GithubIssuesError,
    _api_base,
    _require_token,
)
from tracks.frontmatter import split_frontmatter

_ITEM_HEAD = re.compile(r"^### ((?:N?FR)-\d{4})[ \t]*(.*)$")
_ACC_SECTION = re.compile(r"^## ((?:N?FR)-\d{4})\b")
_AC_HEAD = re.compile(r"^### (AC-N?FR\d{4}-\d+)[ \t]*(.*)$")


def _facade():
    """The re-exporting facade, resolved lazily to keep imports cycle-free."""
    from tracks.effects import github

    return github


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


def readback_issue(repo_id: str, issue_number: int) -> dict:
    """GET /repos/{repo}/issues/{n} verified readback (AC-FR0270-01):
    classified fail-closed on HTTP/transport failures (never a silent
    pass); the mapping carries the GLOBAL identity (``node_id`` /
    numeric ``global_id``, FR-0283-01/04)."""
    _require_token()
    url = f"{_api_base()}/repos/{repo_id}/issues/{issue_number}"
    try:
        data = _facade()._get_any(_facade()._api_request(url, "GET"))
    except urllib.error.HTTPError as e:
        cls = _HTTP_ERROR_CLASSES.get(e.code, "network")
        raise GithubIssuesError(cls, f"HTTP {e.code}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise GithubIssuesError("network", str(e.reason)) from e
    return {
        "issue_number": data.get("number", issue_number),
        "node_id": data.get("node_id"),
        "global_id": data.get("id"),
        "title": data.get("title", ""),
        "state": data.get("state", ""),
        "url": data.get("html_url", ""),
        "api_verified": True,
    }


def list_issues(repo_id: str, *, per_page: int = 100, max_pages: int = 10) -> list[dict]:
    """GET /repos/{repo}/issues normalized listing (FR-0283-04): up to
    ``max_pages`` pages of ``state=all`` issues (PRs excluded); any failure,
    malformed page, or page-bound overrun raises classified — never guess."""
    _require_token()
    base = _api_base()
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        req = _facade()._api_request(
            f"{base}/repos/{repo_id}/issues"
            f"?state=all&per_page={per_page}&page={page}",
            "GET",
        )
        try:
            data = _facade()._get_any(req)
        except urllib.error.HTTPError as e:
            cls = _HTTP_ERROR_CLASSES.get(e.code, "network")
            raise GithubIssuesError(cls, f"HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise GithubIssuesError("network", str(e.reason)) from e
        if not isinstance(data, list):
            raise GithubIssuesError("network", "issues listing is not a list")
        for item in data:
            if not isinstance(item, dict) or "pull_request" in item:
                continue
            out.append(
                {
                    "issue_number": item.get("number"),
                    "node_id": item.get("node_id"),
                    "global_id": item.get("id"),
                    "title": item.get("title", ""),
                    "state": item.get("state", ""),
                    "url": item.get("html_url", ""),
                    "body": item.get("body", ""),
                }
            )
        if len(data) < per_page:
            return out
    raise GithubIssuesError(
        "network", f"issues listing exceeded {max_pages} pages"
    )


def find_remote_issue(repo_id: str, title: str, baseline_digest: str) -> dict | None:
    """Crash-recovery dedup search (FR-0283-04): the remote issue matching
    ``(repo, title, baseline_digest)`` — exact title AND the create body's
    digest marker. None when nothing matches; a failing search raises
    (fail-closed) so absence is proven, never guessed."""
    wanted = str(title or "")
    digest = str(baseline_digest or "")
    for issue in _facade().list_issues(repo_id):
        if str(issue.get("title") or "") != wanted:
            continue
        if digest and digest not in str(issue.get("body") or ""):
            continue
        return issue
    return None


def _mapping_key_matches(mapping: object, repo_id: str, title: str, baseline_digest: str) -> bool:
    """True when a stored mapping binds exactly (repo, title, baseline)."""
    if not isinstance(mapping, dict) or not mapping.get("api_verified"):
        return False
    return (
        str(mapping.get("repo") or "") == str(repo_id or "")
        and str(mapping.get("baseline_digest") or "") == str(baseline_digest or "")
        and str(mapping.get("title") or "") == str(title or "")
    )


def find_authoritative_mapping(
    repo: Path,
    repo_id: str,
    title: str,
    baseline_digest: str,
) -> dict | None:
    """The authoritative-map entry for ``(repo, title, baseline_digest)``
    (only ``api_verified=true`` entries match, FR-0283-02/04); else None."""
    path = repo / ".tracks" / "runtime" / "issue-map.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    for item_id, mapping in data.items():
        if _mapping_key_matches(mapping, repo_id, title, baseline_digest):
            return {**mapping, "item_id": item_id}
    return None


def _reuse_mapping(entry: dict, *, recovered: bool) -> dict:
    """Normalize a reused identity into the shared mapping vocabulary."""
    return {
        "issue_number": entry.get("issue_number"),
        "node_id": entry.get("node_id"),
        "global_id": entry.get("global_id"),
        "title": entry.get("title", ""),
        "state": entry.get("state", ""),
        "url": entry.get("url", ""),
        "api_verified": True,
        "reused": True,
        "recovered": recovered,
    }


def create_issue_verified(
    backend,
    title: str,
    body: str,
    labels: list,
    *,
    repo_dir: Path | None = None,
    repo_id: str | None = None,
    baseline_digest: str | None = None,
) -> dict:
    """Create an issue and verify it by an immediate API readback. Live:
    create -> GET -> ``api_verified=true`` (AC-FR0270-01) with the global
    ``node_id``/``url`` identity (FR-0283-01). Fake stand-in: can never
    verify (``api_verified=false``, AC-FR0270-02); a FAKE artifact on the
    real channel is returned unverified so ``reject_fake_artifact`` lands
    ``fake_rejected``. Crash-idempotent dedup (FR-0283-04) with the
    full ``(repo_dir, repo_id, baseline_digest)`` context: (1) reuse an
    authoritative ``api_verified=true`` map entry without remote calls;
    (2) else reuse a remote issue with the same title + digest through its
    API readback; (3) a search that cannot prove absence fails closed.
    """
    if isinstance(backend, FakeIssueBackend):
        issue_id = backend.create_issue(title, body, labels)
        return {
            "issue_number": issue_id,
            "title": title,
            "api_verified": False,
        }
    dedup = bool(repo_dir and repo_id and baseline_digest)
    if dedup:
        existing = _facade().find_authoritative_mapping(
            repo_dir, repo_id, title, baseline_digest
        )
        if existing is not None:
            return _reuse_mapping(existing, recovered=False)
        remote = _facade().find_remote_issue(repo_id, title, baseline_digest)
        if remote is not None:
            readback = _facade().readback_issue(repo_id, int(remote["issue_number"]))
            return _reuse_mapping(
                {
                    **remote,
                    "title": readback.get("title", remote.get("title", "")),
                    "state": readback.get("state", remote.get("state", "")),
                    "node_id": readback.get("node_id", remote.get("node_id")),
                    "global_id": readback.get("global_id", remote.get("global_id")),
                },
                recovered=True,
            )
    issue_id = backend.create_issue(title, body, labels)
    if str(issue_id).startswith(("FAKE-", "fake")):
        return {"issue_number": issue_id, "title": title, "api_verified": False}
    readback = _facade().readback_issue(backend.gh_repo, int(str(issue_id)))
    return {
        "issue_number": issue_id,
        "node_id": readback.get("node_id"),
        "global_id": readback.get("global_id"),
        "title": readback.get("title", ""),
        "state": readback.get("state", ""),
        "url": readback.get("url", ""),
        "api_verified": bool(readback.get("api_verified")),
        "reused": False,
        "recovered": False,
    }


def persist_issue_mapping(repo: Path, item_id: str, mapping: dict) -> dict:
    """Merge into ``.tracks/runtime/issue-map.json`` (AC-FR0270-01); a
    crash-retry re-persist dedups by item id — last verified write wins."""
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
    """Real-channel fake-artifact rejection (AC-FR0270-02): a FAKE-prefixed
    id lands the closed ``fake_rejected`` verdict, never the map."""
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
