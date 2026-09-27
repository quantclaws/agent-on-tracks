"""Workbench shell contracts (IF-WORKBENCH-001, IF-WEBAUTH-001, IF-CMDSVC-001,
IF-QUERY-001, IF-WEBUI-001, IF-SECRECY-001).

Covers the v0.10 shell/static-face deltas through the documented outlets
(interfaces §4a): rendered page HTML over a real serve subprocess, the
repository build-chain facts, the read-projection parity surface, the
mutation guard, and the UI discipline static scan of the Shield ui assets.
"""

from __future__ import annotations

import json
import re
import sqlite3
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

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Build-chain artifacts whose presence would violate the native-ES-modules
# contract (AC-FR0316-02 / AC-NFR0156-01, interfaces §1l.2).
_BUILD_CHAIN_MARKERS = (
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "node_modules",
    "vite.config.js",
    "vite.config.ts",
    "vite.config.mjs",
    "webpack.config.js",
    "webpack.config.ts",
    "rollup.config.js",
    "rollup.config.ts",
    "tsconfig.json",
    ".npmrc",
)


def _start_ui_serve(tmp_path: Path):
    """Real serve subprocess over an isolated home + host repo."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("shell host\n", encoding="utf-8")
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "shell@example.com"),
        ("config", "user.name", "Shell Human"),
        ("add", "README.md"),
        ("commit", "-m", "initial"),
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    proc = start_serve(home, repo, port=0)
    return proc, home, repo


def _seed_overview_facts(home: Path, repo: Path, run_id: str, version: str) -> None:
    """The observable service + event facts the projections read."""
    from tracks.paths import tracks_home
    from tracks.store import Store

    conn = sqlite3.connect(str(home / "service.db"))
    try:
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            ("proj-shell-1", str(repo), version, "local-user", "2026-09-27T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()
    store = Store(tracks_home(repo))
    try:
        store.append(run_id, version, "story.requested", {"raw_chars": 1})
        store.append(run_id, version, "stage.entered", {"stage": "M-IMPL"})
        store.append(run_id, version, "taskgraph.committed", {"task_count": 2})
    finally:
        store.close()


# AC-FR0316-02@v0.10 TRACKS-TRACE no build chain, native ES module bootstrap
# AC-NFR0156-01@v0.10 TRACKS-TRACE no build chain, native ES module bootstrap
def test_no_build_chain_native_esm(tmp_path: Path):
    """AC-FR0316-02 / AC-NFR0156-01: the repository carries no npm/Vite build
    chain and the workbench shell bootstraps native ES modules from the same
    origin — no bundler artifact, no second toolchain gate."""
    for marker in _BUILD_CHAIN_MARKERS:
        found = [
            path
            for path in _REPO_ROOT.glob(marker)
            if ".venv" not in path.parts and ".git" not in path.parts
        ]
        assert not found, f"build-chain artifact {marker} must not exist: {found}"

    proc, home, repo = _start_ui_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, _csrf = login_session(base)
        status, body = http_get(base, "/", cookies=cookie)
        assert status == 200, body[:200]
        html = body.decode("utf-8")
        assert 'data-route=' in html, "the workbench shell must carry its deep-link route"
        assert '<script type="module"' in html, (
            "the shell must bootstrap as a native ES module (no bundler)"
        )
        assert "/static/app/shell.js" in html, (
            "the module entry must be the same-origin /static asset (§1l.2)"
        )
        external = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
        assert not external, f"the shell must not reference an external asset: {external}"
    finally:
        stop_serve(proc)


# AC-NFR0153-01@v0.10 TRACKS-TRACE projections match seeded facts, no mock corpus
def test_no_mock_data_parity_with_projections(tmp_path: Path):
    """AC-NFR0153-01: every audited data face serves the event-sourced
    projection values (no fabricated corpus): the overview/run-detail/todo
    queries return exactly the seeded facts, and the shell fetches its data
    through the module+API surface instead of embedding rows."""
    proc, home, repo = _start_ui_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, _csrf = login_session(base)
        run_id = "run-shell-overview-1"
        _seed_overview_facts(home, repo, run_id, "v1.0")

        status, body = http_get(base, "/api/projects/proj-shell-1/overview", cookies=cookie)
        assert status == 200, body[:200]
        overview = json.loads(body)
        runs = overview["runs"]
        seeded = [row for row in runs if row["run_id"] == run_id]
        assert len(seeded) == 1, f"the seeded run must be projected once: {runs!r}"
        assert seeded[0]["version"] == "v1.0"
        assert seeded[0]["stage"] == "M-IMPL"
        assert overview["projects"] and any(
            project["project_id"] == "proj-shell-1" for project in overview["projects"]
        ), f"the seeded project must be projected: {overview['projects']!r}"

        status, body = http_get(base, f"/api/runs/{run_id}", cookies=cookie)
        assert status == 200, body[:200]
        detail = json.loads(body)
        assert detail["run_id"] == run_id
        assert detail["stage"] == "M-IMPL"
        assert detail.get("event_cursor") is not None

        status, body = http_get(base, "/", cookies=cookie)
        assert status == 200, body[:200]
        html = body.decode("utf-8")
        assert f'data-run-id="{run_id}"' not in html, (
            "run rows must come from the live projection, never the baked-in HTML"
        )
        assert "/static/app/shell.js" in html, (
            "the shell must load the data surface through the module entry, "
            "not inline fabricated rows (§1l.6)"
        )
    finally:
        stop_serve(proc)


# AC-NFR0154-02@v0.10 TRACKS-TRACE mutations reject missing CSRF with no effect
def test_mutation_endpoints_reject_missing_csrf(tmp_path: Path):
    """AC-NFR0154-02: every mutation face (inherited command plane and the new
    auth-name endpoint) refuses a missing/mismatching X-Trac-CSRF credential
    with 403 and no execution effect; the refusal is audited."""
    proc, home, repo = _start_ui_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, csrf = login_session(base)
        run_id = "run-shell-csrf-1"
        _seed_overview_facts(home, repo, run_id, "v1.0")

        # inherited command plane (regression guard): missing CSRF is refused
        status, body = http_post(
            base,
            f"/api/runs/{run_id}/docs/spec/edits",
            {"base_revision": "rev-0", "content": "csrf probe\n"},
            cookies=cookie,
            idempotency_key="shell-csrf-1",
        )
        assert status == 403, body[:200]
        assert json.loads(body)["error"]["reason"] == "unauthenticated"

        # mismatched CSRF is refused with the same closed semantics
        status, body = http_post(
            base,
            f"/api/runs/{run_id}/docs/spec/edits",
            {"base_revision": "rev-0", "content": "csrf probe\n"},
            cookies=cookie,
            csrf="wrong-token",
            idempotency_key="shell-csrf-2",
        )
        assert status == 403, body[:200]

        # the write contract also requires the Idempotency-Key
        status, body = http_post(
            base,
            f"/api/runs/{run_id}/docs/spec/edits",
            {"base_revision": "rev-0", "content": "csrf probe\n"},
            cookies=cookie,
            csrf=csrf,
        )
        assert status == 400, body[:200]
        assert json.loads(body)["error"]["reason"] == "validation_failed"

        # the new name-binding endpoint enforces the same credential
        status, body = http_post(
            base, "/api/auth/name", {"name": "Shell Human"}, cookies=cookie
        )
        assert status == 403, (
            "POST /api/auth/name without X-Trac-CSRF must be refused (§2b #29)"
        )

        # no execution effect: the refused edit persisted no command row
        conn = sqlite3.connect(f"file:{home / 'service.db'}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT COUNT(*) FROM commands WHERE idempotency_key IN (?, ?)",
                ("shell-csrf-1", "shell-csrf-2"),
            ).fetchone()
        finally:
            conn.close()
        assert rows[0] == 0, "a CSRF-refused mutation must persist no command"
    finally:
        stop_serve(proc)


def _ui_test_files() -> list[Path]:
    return sorted(path for path in (_REPO_ROOT / "tests" / "e2e").glob("test_*.py"))


# AC-NFR0155-01@v0.10 TRACKS-TRACE ui assets bind data-testid, no API substitution
def test_ui_tests_bind_data_testid():
    """AC-NFR0155-01 (static discipline half): every ui-marked e2e module
    locates controls through ``data-testid`` only, never through CSS class or
    DOM hierarchy, and none of the key UI operations is replaced by an API
    request (§1t.2, §1.3 #12/#13)."""
    ui_files = [
        path
        for path in _ui_test_files()
        if "pytest.mark.ui" in path.read_text(encoding="utf-8")
    ]
    assert ui_files, "the v0.10 ui layer must exist under tests/e2e"
    forbidden = (
        'locator("css=',
        "locator('css=",
        'locator("xpath=',
        "locator('xpath=",
        "locator('.css-",
        "page.query_selector(",
        "fetch(",
        "requests.post(",
        "requests.get(",
        "urlopen(",
    )
    for path in ui_files:
        text = path.read_text(encoding="utf-8")
        assert "TRACKS-TRACE" in text, f"{path.name}: ui tests must carry trace markers"
        assert "get_by_test_id(" in text, (
            f"{path.name}: ui tests must locate controls via data-testid"
        )
        for pattern in forbidden:
            assert pattern not in text, (
                f"{path.name}: forbidden ui selector/substitution {pattern!r}"
            )


# AC-NFR0155-02@v0.10 TRACKS-TRACE browser infra declared as separate budget
def test_ui_e2e_infra_declared():
    """AC-NFR0155-02: the Playwright/Chromium layer is declared as an
    independent infrastructure budget — dev dependency + marker + addopts
    exclusion in the host contract files, the ui-e2e required check in CI,
    and the budget statement in the v0.10 plan."""
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "playwright==" in pyproject, "playwright must be a declared dev dependency"
    assert '"ui:' in pyproject or "ui:" in pyproject, "the ui marker must be declared"
    assert "not performance and not ui" in pyproject, (
        "the default suite must exclude the ui marker via addopts"
    )
    ci = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "ui-e2e:" in ci, "the ui-e2e CI job must exist"
    assert "playwright install" in ci and "-m 'ui'" in ci.replace('"', "'"), (
        "the ui-e2e job installs Chromium and selects the ui marker"
    )
    project_toml = (
        _REPO_ROOT / ".tracks" / "projects" / "project.toml"
    ).read_text(encoding="utf-8")
    assert '"ui-e2e"' in project_toml, (
        "ui-e2e must be a required check in the host contract"
    )
    plan = (
        _REPO_ROOT / ".tracks" / "projects" / "v0.10" / "test-plan.md"
    ).read_text(encoding="utf-8")
    assert "独立基础设施预算" in plan, (
        "the browser download budget must be declared in the test plan"
    )
