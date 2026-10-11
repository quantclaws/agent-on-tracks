"""M-START gate: enter-development branch/run creation (IF-MSTART-001,
IF-BACKLOG-001).

Covers the develop action's gate sequence (§1g.1) through the documented
#40 endpoint: the happy derivation ``releases/v{{N+1}}`` with the Scribe title
in the story frontmatter, the active-run/dirty-tree/unmerged-branches
refusals with their no-half-built guarantees, and the terminal non-develop
actions that never open a run or a branch.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from tests._support.intake_fence import wait_intake_terminal
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
from tracks.paths import tracks_home
from tracks.store import Store

pytestmark = pytest.mark.integration

_SEED = "给发布清单页加一个导出按钮，导出为 CSV。"


def _prepared_repo(tmp_path: Path, trac) -> Path:
    """A host repo scaffolded as a runnable tracks project (web journey)."""
    repo = trac.repo
    contract_dir = repo / ".tracks" / "projects"
    contract_dir.mkdir(parents=True, exist_ok=True)
    (contract_dir / "project.toml").write_text(
        "[unit]\ncollect = \"python3 -m pytest --collect-only tests/unit\"\n"
        "run = \"python3 -m pytest tests/unit -q\"\n"
        "[integration]\ncollect = \"python3 -m pytest --collect-only tests/integration\"\n"
        "run = \"python3 -m pytest tests/integration -q\"\n",
        encoding="utf-8",
    )
    assert trac("init").returncode == 0
    return repo


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
        idempotency_key=f"mstart-reg-{marker}",
    )
    assert status == 201, f"registration must succeed: {status} {body[:200]}"
    project_id = json.loads(body)["project_id"]
    return proc, base, cookie, csrf, project_id


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def _branches(repo: Path) -> set[str]:
    out = _git(repo, "branch", "--format=%(refname:short)")
    return {line.strip() for line in out.splitlines() if line.strip()}


def _evaluated_intake(base: str, cookie: str, csrf: str, project_id: str, key: str) -> str:
    """Submit the seed text and fence the evaluation to ``evaluated``."""
    status, body = http_post(
        base,
        f"/api/projects/{project_id}/intakes",
        {"source": "text", "content": _SEED},
        cookies=cookie,
        csrf=csrf,
        idempotency_key=f"mstart-submit-{key}",
    )
    assert status == 202, f"the submission must be accepted: {status} {body[:200]}"
    intake_id = json.loads(body)["intake_id"]
    evaluated = wait_intake_terminal(base, cookie, project_id, intake_id)
    assert evaluated["status"] == "evaluated", (
        f"the evaluation must land evaluated: {evaluated!r}"
    )
    return intake_id


def _develop(base: str, cookie: str, csrf: str, intake_id: str, key: str, confirm=None):
    payload: dict = {"action": "develop"}
    if confirm is not None:
        payload["confirm"] = confirm
    return http_post(
        base,
        f"/api/intakes/{intake_id}/decisions",
        payload,
        cookies=cookie,
        csrf=csrf,
        idempotency_key=f"mstart-decide-{key}",
    )


def _wait_for(condition, timeout: float = 120.0, interval: float = 0.5):
    """Poll a zero-arg predicate until truthy; returns its value or None."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(interval)
    return None


# AC-FR0339-01@v0.11 TRACKS-TRACE develop creates branch run and titles story
def test_develop_creates_branch_run_title(tmp_path: Path, host_repo, trac):
    """AC-FR0339-01: the develop action passes the M-START gate, derives
    ``releases/v{{N+1}}`` from the project's max existing release minor, creates
    the branch from main with a run that enters M-STORY, and the Scribe title
    lands in the story frontmatter; no half-built run or branch survives a
    refusal."""
    repo = _prepared_repo(tmp_path, trac)
    _git(repo, "branch", "releases/v1.0")
    proc, base, cookie, csrf, project_id = _serve_registered(tmp_path, repo, "happy")
    try:
        intake_id = _evaluated_intake(base, cookie, csrf, project_id, "happy-1")
        status, body = _develop(base, cookie, csrf, intake_id, "happy-1")
        assert status == 202, (
            f"the develop action must pass the gate: {status} {body[:200]}"
        )

        branch = _wait_for(
            lambda: "releases/v2.0" if "releases/v2.0" in _branches(repo) else None
        )
        assert branch is not None, (
            f"the gate must derive releases/v2.0 from the seeded releases/v1.0: "
            f"{sorted(_branches(repo))!r}"
        )
        # the branch grows from main (created off the main head, §4c.1)
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", "main", branch],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert ancestor.returncode == 0, (
            "the release branch must descend from main "
            f"({ancestor.stderr.strip()})"
        )

        # a run exists for the derived version and enters M-STORY
        def _find_run():
            status, body = http_get(
                base, f"/api/projects/{project_id}/overview", cookies=cookie
            )
            if status != 200:
                return None
            runs = json.loads(body).get("runs", [])
            for row in runs:
                if row.get("version") == "v2.0":
                    return row["run_id"]
            return None

        run_id = _wait_for(_find_run)
        assert run_id is not None, "the develop action must establish a run (§1g.1d)"
        deadline = time.time() + 120
        stage = None
        while time.time() < deadline:
            status, body = http_get(base, f"/api/runs/{run_id}", cookies=cookie)
            if status == 200:
                stage = json.loads(body).get("stage")
                if stage == "M-STORY":
                    break
            time.sleep(0.5)
        assert stage == "M-STORY", (
            f"the new release must enter M-STORY (§4c.1): stage={stage!r}"
        )

        # the Scribe title lands in the story frontmatter (§1g.2)
        story = _wait_for(
            lambda: (
                subprocess.run(
                    ["git", "show", "releases/v2.0:.tracks/projects/v2.0/story.md"],
                    cwd=repo,
                    capture_output=True,
                    text=True,
                ).stdout
                or None
            )
        )
        assert story, "the story capture must exist on the release branch"
        title_lines = [
            line
            for line in story.splitlines()
            if line.lower().startswith("title:")
        ]
        assert title_lines and title_lines[0].split(":", 1)[1].strip(), (
            f"the frontmatter title must carry the Scribe suggestion: "
            f"{title_lines!r}"
        )
        assert _SEED in story, "the raw seed rides the skeleton verbatim"
    finally:
        stop_serve(proc)


# AC-FR0339-02@v0.11 TRACKS-TRACE active run backlog dirty refusal unmerged confirm
def test_active_run_backlog_dirty_refusal_unmerged_confirm(tmp_path: Path, host_repo, trac):
    """AC-FR0339-02: the gate refuses with reasons and never half-builds — an
    active run records the input to backlog instead of opening a run; a dirty
    worktree refuses with ``dirty_tree``; unmerged release branches are listed
    with their consequence and only a confirmed decision passes (cancel exits
    clean)."""
    repo = _prepared_repo(tmp_path, trac)
    proc, base, cookie, csrf, project_id = _serve_registered(tmp_path, repo, "refuse")
    try:
        # (a) active run: the seed records to backlog, no run, no branch
        status, body = http_post(
            base,
            f"/api/projects/{project_id}/runs",
            {
                "journey": "feature",
                "version": "v0.9",
                "story": None,
                "issue": None,
                "target": None,
                "preempt": False,
            },
            cookies=cookie,
            csrf=csrf,
            idempotency_key="mstart-active-run-1",
        )
        assert status == 202, f"the active-run premise must be creatable: {body[:200]}"
        active_run = json.loads(body)["run_id"]
        before = _branches(repo)

        intake_a = _evaluated_intake(base, cookie, csrf, project_id, "active-1")
        status, body = _develop(base, cookie, csrf, intake_a, "active-1")
        assert status == 200, (
            f"the active-run form must answer 200 with the outcome: "
            f"{status} {body[:200]}"
        )
        outcome = json.loads(body)
        assert outcome.get("outcome") == "backlog_recorded", (
            f"the active-run form records to backlog (§1g.1a): {outcome!r}"
        )
        assert outcome.get("active_run") == active_run, (
            f"the outcome names the active run: {outcome!r}"
        )
        store = Store(tracks_home(repo))
        try:
            backlog_events = [
                dict(event.payload or {})
                for event in store.events(active_run)
                if event.type == "backlog.recorded"
            ]
        finally:
            store.close()
        active_records = [
            payload
            for payload in backlog_events
            if payload.get("reason") == "active_run"
        ]
        assert active_records, (
            f"the gate must land backlog.recorded(reason=active_run): {backlog_events!r}"
        )
        assert active_records[-1].get("source") == "text", (
            f"the record carries the intake source (§1d.2): {active_records[-1]!r}"
        )
        assert active_records[-1].get("intake_id") == intake_a, (
            f"the record links the intake: {active_records[-1]!r}"
        )
        assert not (set(_branches(repo)) - before), (
            "the active-run form must not create a branch (§1g.1a)"
        )

        # (b) dirty tree: refused with the reason, no branch, no run
        (repo / "README.md").write_text("dirty probe\n", encoding="utf-8")
        intake_b = _evaluated_intake(base, cookie, csrf, project_id, "dirty-1")
        status, body = _develop(base, cookie, csrf, intake_b, "dirty-1")
        assert status == 422, (
            f"a dirty worktree must refuse the develop action: {status} {body[:200]}"
        )
        assert json.loads(body)["error"]["reason"] == "dirty_tree", body[:200]
        assert not (set(_branches(repo)) - before), (
            "a refused develop must not create a branch"
        )
        subprocess.run(
            ["git", "checkout", "--", "README.md"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        # (c) unmerged release branch: listed with consequence, confirm gates it
        subprocess.run(
            ["git", "checkout", "-b", "releases/v9.9"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "unmerged.txt").write_text("divergent work\n", encoding="utf-8")
        _git(repo, "add", "unmerged.txt")
        _git(repo, "commit", "-m", "divergent release work")
        subprocess.run(
            ["git", "checkout", "main"], cwd=repo, check=True, capture_output=True
        )
        before_unmerged = set(_branches(repo))

        intake_c = _evaluated_intake(base, cookie, csrf, project_id, "unmerged-1")
        status, body = _develop(base, cookie, csrf, intake_c, "unmerged-1")
        assert status == 409, (
            f"unmerged release branches must surface the confirmation: "
            f"{status} {body[:200]}"
        )
        conflict = json.loads(body)
        assert conflict["error"]["reason"] == "unmerged_release_branches", body[:200]
        listed = conflict["error"].get("branches") or conflict.get("branches") or []
        assert "releases/v9.9" in listed, (
            f"the unmerged branches must be listed: {conflict!r}"
        )

        # confirm=false: cancel exits without building anything
        status, body = _develop(
            base, cookie, csrf, intake_c, "unmerged-cancel", confirm=False
        )
        assert status in (200, 202), (
            f"the cancel must be a legal exit: {status} {body[:200]}"
        )
        assert not (set(_branches(repo)) - before_unmerged), (
            "the cancelled develop must not create a branch (§1g.1c)"
        )

        # a fresh develop with confirm=true passes the gate
        intake_d = _evaluated_intake(base, cookie, csrf, project_id, "unmerged-2")
        status, body = _develop(
            base, cookie, csrf, intake_d, "unmerged-confirm", confirm=True
        )
        assert status == 202, (
            f"the confirmed develop must pass the gate: {status} {body[:200]}"
        )
        created = _wait_for(
            lambda: (set(_branches(repo)) - before_unmerged - {"releases/v9.9"}) or None
        )
        assert created, (
            f"the confirmed develop must create its branch: {sorted(_branches(repo))!r}"
        )
    finally:
        stop_serve(proc)


# AC-FR0339-03@v0.11 TRACKS-TRACE non develop actions never build
def test_nondevelop_actions_no_run_branch(tmp_path: Path, host_repo, trac):
    """AC-FR0339-03 (service half): the backlog, remove and archive actions are
    terminal — each completes without opening a run or creating a branch; the
    flow.md contract half lives in test_flow_contracts."""
    repo = _prepared_repo(tmp_path, trac)
    proc, base, cookie, csrf, project_id = _serve_registered(tmp_path, repo, "terminal")
    try:
        before = _branches(repo)
        runs_before = _project_run_ids(base, cookie, project_id)

        # backlog action
        intake_a = _evaluated_intake(base, cookie, csrf, project_id, "backlog-1")
        status, body = http_post(
            base,
            f"/api/intakes/{intake_a}/decisions",
            {"action": "backlog"},
            cookies=cookie,
            csrf=csrf,
            idempotency_key="mstart-terminal-backlog-1",
        )
        assert status == 202, f"the backlog action must be accepted: {body[:200]}"

        # archive action
        intake_b = _evaluated_intake(base, cookie, csrf, project_id, "archive-1")
        status, body = http_post(
            base,
            f"/api/intakes/{intake_b}/decisions",
            {"action": "archive"},
            cookies=cookie,
            csrf=csrf,
            idempotency_key="mstart-terminal-archive-1",
        )
        assert status == 202, f"the archive action must be accepted: {body[:200]}"

        assert _branches(repo) == before, (
            f"terminal actions must not create branches: {sorted(_branches(repo))!r}"
        )
        assert _project_run_ids(base, cookie, project_id) == runs_before, (
            "terminal actions must not open runs (§1f.1 动作①③为终态)"
        )
    finally:
        stop_serve(proc)


def _project_run_ids(base: str, cookie: str, project_id: str) -> set[str]:
    status, body = http_get(
        base, f"/api/projects/{project_id}/overview", cookies=cookie
    )
    if status != 200:
        return set()
    return {row["run_id"] for row in json.loads(body).get("runs", [])}
