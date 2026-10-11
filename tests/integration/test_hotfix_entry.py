"""Hotfix entry presentation (IF-HOTFIXUI-001, IF-HOTFIX-011).

The new read-only ``hotfix-preview`` projection (§2b #44, §1i.1) over the
inherited precheck/anchoring machinery, and the exit discipline: an
unanchored or rejected precheck ends the hotfix entry with guidance and
never leaves a half-built fix branch or run.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

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

pytestmark = pytest.mark.integration


def _seed_hotfix_repo(host_repo) -> None:
    """Approved baseline + issue corpus + active release branch (inherited)."""
    from tests.integration.test_journey_versioning import _seed_hotfix_host

    _seed_hotfix_host(host_repo)


def _seed_hotfix_repo_without_baseline(host_repo) -> None:
    """Unapproved target baseline + issue corpus + active release branch.

    The locked anchoring rule (interfaces.md §1i.1) reads availability from
    the target version's baseline trio existence AND approved status — the
    target v0.9 trio here is unapproved (only v0.7 carries an approval
    event, which also initializes the run-plane store), making the
    version-level anchoring verdict unanchored for this premise.
    """
    from tests._support.hotfix_support import (
        seed_host_issues,
        seed_project_contract,
        seed_release_branch,
    )
    from tests.integration.test_journey_versioning import _declare_hotfix_contract
    from tracks.cli.main import HOST_BYPRODUCTS_GITIGNORE, HOST_BYPRODUCTS_MARKER
    from tracks.paths import tracks_home, version_dir
    from tracks.store import Store

    seed_host_issues(host_repo)
    seed_project_contract(host_repo)
    store = Store(tracks_home(host_repo))
    try:
        store.append(
            run_id="approval-v0.7",
            version="v0.7",
            type="approval.recorded",
            payload={
                "version": "v0.7",
                "actor": "Aaron",
                "digest": "fixture-v0.7",
            },
        )
    finally:
        store.close()
    vdir = version_dir(tracks_home(host_repo), "v0.9")
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "story.md").write_text(
        "# v0.9 story\n\nunapproved baseline story.\n", encoding="utf-8"
    )
    (vdir / "spec.md").write_text(
        "# v0.9 spec\n\n### FR-0030\n\nunapproved baseline spec.\n", encoding="utf-8"
    )
    (vdir / "acceptance.md").write_text(
        "# v0.9 acceptance\n\n### AC-FR0030-01\n\nunapproved baseline AC.\n",
        encoding="utf-8",
    )
    (vdir / "interfaces.md").write_text(
        "# v0.9 interfaces\n\n## 5. IF Registry\n\n"
        "- IF-BASELINE-001: baseline interface contract\n",
        encoding="utf-8",
    )
    _declare_hotfix_contract(host_repo)
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
        ["git", "commit", "-m", "seed unapproved baseline trio"],
        cwd=host_repo,
        check=True,
        capture_output=True,
    )
    seed_release_branch(host_repo, "releases/v0.9")


def _serve_registered(tmp_path: Path, repo: Path, marker: str):
    home = tmp_path / f"serve-home-{marker}"
    proc = start_serve(home, repo, port=0)
    base = parse_base_url(wait_for_port_line(proc))
    wait_for_healthz(base)
    cookie, csrf = login_session(base)
    status, body = http_post(
        base,
        "/api/projects",
        {"repo_path": str(repo)},
        cookies=cookie,
        csrf=csrf,
        idempotency_key=f"hotfixui-reg-{marker}",
    )
    assert status == 201, f"registration must succeed: {status} {body[:200]}"
    project_id = json.loads(body)["project_id"]
    return proc, base, cookie, csrf, project_id


def _preview(base: str, cookie: str, project_id: str, issue: int, scenario: str):
    return http_get(
        base,
        f"/api/projects/{project_id}/hotfix-preview?issue={issue}&scenario={scenario}",
        cookies=cookie,
    )


def _branches(repo: Path) -> set[str]:
    out = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


# AC-FR0341-01@v0.11 TRACKS-TRACE hotfix preview and anchored entry proceeds
def test_hotfix_preview_and_anchor_display(tmp_path: Path, host_repo):
    """AC-FR0341-01: the hotfix entry presents the issue selection and the
    scenario (post-release from the main baseline / dev from the active
    branch), the read-only preview shows the HOTFIX-TRIAGE precheck and the
    AC anchoring result, and an anchored entry proceeds through the existing
    create_run face to a real hotfix run."""
    _seed_hotfix_repo(host_repo)
    proc, base, cookie, csrf, project_id = _serve_registered(
        tmp_path, host_repo, "anchored"
    )
    try:
        status, body = _preview(base, cookie, project_id, 42, "post-release")
        assert status == 200, f"the preview must resolve: {status} {body[:200]}"
        preview = json.loads(body)
        assert preview["scenario"] == "post-release", f"scenario echoes: {preview!r}"
        assert preview["issue"]["number"] == 42, (
            f"the preview names the selected issue: {preview.get('issue')!r}"
        )
        assert preview["issue"].get("title"), (
            f"the preview shows the issue title: {preview.get('issue')!r}"
        )
        precheck = preview["precheck"]
        assert precheck["status"] == "ok", (
            f"the seeded precheck must pass (HOTFIX-TRIAGE display): {precheck!r}"
        )
        anchoring = preview["anchoring"]
        assert anchoring["status"] == "anchored", (
            f"the AC anchoring must resolve for FR-0030@v0.8: {anchoring!r}"
        )
        assert anchoring.get("target_version") == "v0.8", (
            f"the anchored target version is displayed: {anchoring!r}"
        )

        # anchored → the entry proceeds through the existing creation face
        status, body = http_post(
            base,
            f"/api/projects/{project_id}/runs",
            {
                "journey": "hotfix_post",
                "version": "v0.9",
                "story": None,
                "issue": 42,
                "target": None,
                "preempt": False,
            },
            cookies=cookie,
            csrf=csrf,
            idempotency_key="hotfixui-create-1",
        )
        assert status == 202, (
            f"an anchored hotfix entry must proceed: {status} {body[:200]}"
        )
        created = json.loads(body)
        assert created["run_id"] and created["command_id"], body[:200]
    finally:
        stop_serve(proc)


# AC-FR0341-02@v0.11 TRACKS-TRACE anchor failure exits without half branch
def test_anchor_fail_exits_no_half_branch(tmp_path: Path, host_repo):
    """AC-FR0341-02: an unanchored bug and a non-bug issue both end the entry
    with the explicit exit presentation — the preview shows the unanchored /
    rejected state, the creation face refuses fail-closed, and no half-built
    fix branch or run remains; the exit guides to the backlog/new-feature
    entries."""
    _seed_hotfix_repo_without_baseline(host_repo)
    before = _branches(host_repo)
    proc, base, cookie, csrf, project_id = _serve_registered(
        tmp_path, host_repo, "unanchored"
    )
    try:
        # issue #77: the stand-in corpus classifies it not_bug, so the
        # anchoring gate refuses before any version-level anchoring — the
        # display carries the rejected precheck and the unanchored verdict
        status, body = _preview(base, cookie, project_id, 77, "post-release")
        assert status == 200, f"the preview must resolve: {status} {body[:200]}"
        preview = json.loads(body)
        assert preview["precheck"]["status"] == "rejected", (
            f"the not_bug precheck must reject the entry: {preview.get('precheck')!r}"
        )
        assert preview["anchoring"]["status"] == "unanchored", (
            f"the unanchored state must be displayed: {preview.get('anchoring')!r}"
        )
        assert preview["anchoring"].get("details"), (
            f"the unanchored display carries readable details: {preview['anchoring']!r}"
        )

        # non-bug #99: the precheck rejects the entry outright
        status, body = _preview(base, cookie, project_id, 99, "post-release")
        assert status == 200, f"the preview must resolve: {status} {body[:200]}"
        rejected = json.loads(body)
        assert rejected["precheck"]["status"] == "rejected", (
            f"the non-bug precheck must reject: {rejected.get('precheck')!r}"
        )
        assert rejected["precheck"].get("reason"), (
            f"the rejection carries its reason: {rejected['precheck']!r}"
        )

        # creation with the unanchored bug: fail-closed, nothing half-built
        # (the rejection status channel differs by stage — precheck-rejected
        # answers 400, the trio-missing gate 422 — both are the explicit
        # validation_failed rejection)
        status, body = http_post(
            base,
            f"/api/projects/{project_id}/runs",
            {
                "journey": "hotfix_post",
                "version": "v0.9",
                "story": None,
                "issue": 77,
                "target": None,
                "preempt": False,
            },
            cookies=cookie,
            csrf=csrf,
            idempotency_key="hotfixui-create-unanchored-1",
        )
        assert status in (400, 422), (
            f"the unanchored entry must be refused: {status} {body[:200]}"
        )
        refusal = json.loads(body)
        assert refusal["error"]["reason"] == "validation_failed", body[:200]

        # no fix branch and no run: the exit is clean (§1i.2, SM-01.5)
        fix_branches = [
            name for name in _branches(host_repo) - before if name.startswith("fix/")
        ]
        assert not fix_branches, (
            f"a refused hotfix entry must not leave a fix branch: {fix_branches!r}"
        )
        status, body = http_get(
            base, f"/api/projects/{project_id}/overview", cookies=cookie
        )
        runs = json.loads(body).get("runs", []) if status == 200 else []
        assert not [
            row for row in runs if row["version"] == "v0.9" and row.get("stage") == "M-START"
        ], f"no half-built hotfix run may remain: {runs!r}"
    finally:
        stop_serve(proc)
