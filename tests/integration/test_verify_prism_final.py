"""Integration: Prism same-candidate final review (FR-0271, IF-VERIFY-005).

Each node builds the real M-VERIFY boundary in a temporary host, produces a
passed FULL_F result, runs the declared local gate and loopback CI readback,
then reaches Prism through ``Executor.issue``. The fake backend is used only
for the agent effect and is configured for the final verdict.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from tracks import paths
from tracks.discuss.locate import token_for
from tracks.discuss.parser import parse_threads
from tracks.effects.fake import FakeBackend
from tracks.executor.executor import Executor
from tracks.executor.m_verify import freeze_candidate
from tracks.kernel.events import Command
from tracks.store import Store

pytestmark = pytest.mark.integration

_WORKFLOW = "123"
_REPO = "acme/host"
_REQUIRED = ["required-ci"]

_FULL_RUNNER = """\
from pathlib import Path
import sys

layer = sys.argv[sys.argv.index("--layer") + 1]
node = f"tests/{layer}/test_{layer}.py::test_{layer}"
root = Path(__file__).resolve().parent
if "--collect" in sys.argv:
    print(node)
    raise SystemExit(0)
result = Path(sys.argv[sys.argv.index("--result") + 1])
counter = root / ".tracks" / "runtime" / f"prism-full-count-{layer}"
counter.parent.mkdir(parents=True, exist_ok=True)
count = int(counter.read_text() or "0") if counter.exists() else 0
counter.write_text(str(count + 1))
result.write_text(
    '<testsuite tests="1"><testcase classname="tests.%s.test_%s" '
    'name="test_%s" /></testsuite>' % (layer, layer, layer)
)
"""


class _CiStandIn:
    """Loopback subset of the GitHub workflow-runs and jobs API."""

    def __init__(self):
        self.expected_head_sha: str | None = None
        self.requests: list[str] = []
        self._lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):  # pragma: no cover - server noise
                return

            def _reply(self, status: int, body: dict):
                raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler protocol
                parsed = urlparse(self.path)
                with owner._lock:
                    owner.requests.append(self.path)
                    expected = owner.expected_head_sha
                prefix = f"/repos/{_REPO}/actions/"
                if parsed.path in (f"{prefix}workflows/{_WORKFLOW}/runs", f"{prefix}runs"):
                    query = parse_qs(parsed.query)
                    if query.get("head_sha") != [expected]:
                        self._reply(200, {"total_count": 0, "workflow_runs": []})
                    else:
                        self._reply(
                            200,
                            {
                                "total_count": 1,
                                "workflow_runs": [
                                    {
                                        "id": 17,
                                        "name": "Build and verify",
                                        "workflow_id": int(_WORKFLOW),
                                        "path": ".github/workflows/ci.yml",
                                        "head_sha": expected,
                                        "status": "completed",
                                        "conclusion": "success",
                                    }
                                ],
                            },
                        )
                    return
                if parsed.path == f"{prefix}runs/17/jobs":
                    self._reply(
                        200,
                        {
                            "total_count": 1,
                            "jobs": [
                                {
                                    "id": 1,
                                    "run_id": 17,
                                    "name": _REQUIRED[0],
                                    "head_sha": expected,
                                    "status": "completed",
                                    "conclusion": "success",
                                }
                            ],
                        },
                    )
                    return
                self._reply(404, {"message": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def start(self):
        self._thread.start()
        return self

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def ci_standin():
    server = _CiStandIn().start()
    try:
        yield server
    finally:
        server.close()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _write_contract(repo: Path) -> None:
    (repo / "fullf_runner.py").write_text(_FULL_RUNNER, encoding="utf-8")
    for layer in ("unit", "integration", "e2e"):
        layer_dir = repo / "tests" / layer
        layer_dir.mkdir(parents=True, exist_ok=True)
        (layer_dir / f"test_{layer}.py").write_text("# declared FULL node\n", encoding="utf-8")
    python = sys.executable
    lines = [
        "[host-contract]",
        "version = 1",
        'language = "runtime-default"',
        'toolchain = "runtime-default"',
        'install = "true"',
        "",
        "[[host-contract.local_gate]]",
        'kind = "quality"',
        'source = "command"',
        'command = "true"',
        'result_channel = "exit_code"',
        "",
        "[host-contract.ci]",
        'repo_env = "TRAC_CI_REPO"',
        f'workflow = "{_WORKFLOW}"',
        'required_checks = ["required-ci"]',
        "",
    ]
    for layer in ("unit", "integration", "e2e"):
        lines.extend(
            [
                f"[{layer}]",
                'framework = "pytest"',
                f'paths = ["tests/{layer}"]',
                f'collect = "{python} fullf_runner.py --layer {layer} --collect"',
                f'run = "{python} fullf_runner.py --layer {layer} --result {{result}}"',
                f'run_selected = "{python} fullf_runner.py --layer {layer} --nodes {{nodes}} --result {{result}}"',
                'cwd = "."',
                "",
            ]
        )
    lines.extend(
        [
            "[nightly]",
            'schedule = "local"',
            'workflow = "fullf-local"',
            'job = "full"',
            'layers = ["unit", "integration", "e2e"]',
            'purpose = "FULL_F acceptance stand-in"',
            "",
        ]
    )
    (repo / ".tracks" / "projects" / "project.toml").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    subprocess.run(
        [
            "git",
            "add",
            "-f",
            "fullf_runner.py",
            "tests",
            ".gitignore",
            ".tracks/projects/project.toml",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "prism final acceptance contract"],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _seed_prism_boundary(host_repo, trac, *, anchored: bool = False) -> tuple[str, str]:
    assert trac("init").returncode == 0
    assert trac("start", "v0.8", stdin="Prism final boundary").returncode == 0
    if anchored:
        (host_repo / "spec.md").write_text("# Prism final finding\n", encoding="utf-8")
        discussion = trac(
            "discuss",
            "start",
            "--file",
            "spec.md",
            "--anchor-line",
            "1",
            "--speaker",
            "prism",
            "blocking final finding",
        )
        assert discussion.returncode == 0
    _write_contract(host_repo)
    candidate_sha = _git(host_repo, "rev-parse", "HEAD")
    store = Store(paths.tracks_home(host_repo))
    try:
        run_id = store.active_run()
        assert run_id
        version = store.state(run_id).version or "v0.8"
        store.append(run_id, version, "stage.entered", {"stage": "M-IMPL"})
        store.append(
            run_id,
            version,
            "baseline.frozen",
            {"status": "current", "digest": "prism-baseline"},
        )
        seed_executor = Executor(store, host_repo, run_id)
        full = seed_executor._execute_full_round(
            Command(kind="check_island_2", command_id="prism-full-seed"),
            store.state(run_id),
            "FULL_1",
            {},
        )
        assert full["passed"] is True
        assert full["serves_as_full_f"] is True
        assert len(full["evidence_ids"]) == 3
        store.append(run_id, version, "stage.entered", {"stage": "M-VERIFY"})
        identity = freeze_candidate(host_repo)
        assert identity.candidate_sha == candidate_sha
        assert identity.clean_tree is True and identity.branch
        store.append(
            run_id,
            version,
            "candidate.frozen",
            {
                "candidate_sha": identity.candidate_sha,
                "clean_tree": identity.clean_tree,
                "branch": identity.branch,
            },
        )
        return run_id, candidate_sha
    finally:
        store.close()


def _configure_ci(monkeypatch, standin: _CiStandIn, candidate_sha: str) -> None:
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")
    monkeypatch.setenv("TRAC_GITHUB_API_BASE", standin.base_url)
    monkeypatch.setenv("TRAC_CI_REPO", _REPO)
    monkeypatch.setenv("GITHUB_TOKEN", "loopback-token")
    standin.expected_head_sha = candidate_sha


def _set_simulation(monkeypatch, value: str) -> None:
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", value)


class _AnchoredFinalBackend(FakeBackend):
    """Fake agent effect that returns the real discussion anchor it reads."""

    def act(self, role, substate, doc, doc_path, assignment=None, worktree=None):
        result = super().act(role, substate, doc, doc_path, assignment, worktree)
        if role != "prism" or substate != "VERIFY_FINAL":
            return result
        text = (self.repo / "spec.md").read_text(encoding="utf-8")
        threads = parse_threads(text)
        assert len(threads) == 1
        thread = threads[0]
        candidate_sha = (assignment or {}).get("candidate_sha")
        result.update(
            {
                "candidate_sha": candidate_sha,
                "review_summary": "blocking final finding is anchored",
                "review_body": "Blocking final finding is linked to the open discussion thread.",
                "findings": [
                    {
                        "id": "PRISM-V08-FINAL-01",
                        "severity": "blocker",
                        "defect_classification": "behavior",
                        "criterion": "FR-0271-03",
                        "artifact": "spec.md:1",
                        "ac_refs": ["AC-FR0271-03"],
                        "summary": "blocking final finding",
                        "candidate_sha": candidate_sha,
                        "file": "spec.md",
                        "thread_id": thread.thread_id,
                    }
                ],
                "discussion_refs": [
                    {
                        "finding_id": "PRISM-V08-FINAL-01",
                        "file": "spec.md",
                        "thread_id": thread.thread_id,
                        "token": token_for(thread),
                    }
                ],
            }
        )
        return result


# AC-FR0271-01@v0.8 TRACKS-TRACE same candidate consistency pass before M-SECURITY
def test_same_candidate_consistency_pass(host_repo, trac, event_log, monkeypatch, ci_standin):
    run_id, candidate_sha = _seed_prism_boundary(host_repo, trac)
    _configure_ci(monkeypatch, ci_standin, candidate_sha)
    _set_simulation(monkeypatch, "prism:VERIFY_FINAL=pass")
    store = Store(paths.tracks_home(host_repo))
    try:
        Executor(store, host_repo, run_id).issue(
            Command(kind="judge_full_f_reuse", params={"candidate_sha": candidate_sha})
        )
    finally:
        store.close()

    events = event_log(run_id)
    observed = [e for e in events if e["type"] == "ci.run_observed"]
    assert observed and observed[-1]["payload"]["status"] == "passed"
    assert observed[-1]["payload"]["api_verified"] is True
    assert observed[-1]["payload"]["head_sha"] == candidate_sha
    verdicts = [e for e in events if e["type"] == "prism.verdict"]
    assert verdicts, "final Prism dispatch must publish a verdict"
    payload = verdicts[-1]["payload"]
    assert payload["verdict"] == "pass"
    assert payload["scope"] == "verify_final"
    assert payload["candidate_sha"] == candidate_sha
    assert any(
        e["type"] == "stage.entered"
        and e["payload"].get("stage") == "M-SECURITY"
        and e["seq"] > verdicts[-1]["seq"]
        for e in events
    )
    assert any("head_sha=" + candidate_sha in path for path in ci_standin.requests)


# AC-FR0271-02@v0.8 TRACKS-TRACE prism revise blocks and repair rebinds same SHA
def test_prism_fail_blocks_m_impl_gap(host_repo, trac, event_log, monkeypatch, ci_standin):
    run_id, candidate_sha = _seed_prism_boundary(host_repo, trac)
    _configure_ci(monkeypatch, ci_standin, candidate_sha)
    _set_simulation(monkeypatch, "prism:VERIFY_FINAL=revise|pass")
    store = Store(paths.tracks_home(host_repo))
    try:
        executor = Executor(store, host_repo, run_id)
        executor.issue(
            Command(kind="judge_full_f_reuse", params={"candidate_sha": candidate_sha})
        )
        first_events = event_log(run_id)
        first = [e for e in first_events if e["type"] == "prism.verdict"][-1]
        assert first["payload"]["verdict"] == "revise"
        assert first["payload"]["scope"] == "verify_final"
        assert first["payload"]["candidate_sha"] == candidate_sha
        assert not any(
            e["type"] == "stage.entered"
            and e["payload"].get("stage") == "M-SECURITY"
            and e["seq"] > first["seq"]
            for e in first_events
        )

        executor.issue(
            Command(kind="judge_full_f_reuse", params={"candidate_sha": candidate_sha})
        )
    finally:
        store.close()

    events = event_log(run_id)
    verdicts = [e for e in events if e["type"] == "prism.verdict"]
    assert len(verdicts) >= 2
    assert [v["payload"]["verdict"] for v in verdicts[-2:]] == ["revise", "pass"]
    assert all(v["payload"]["scope"] == "verify_final" for v in verdicts[-2:])
    assert all(v["payload"]["candidate_sha"] == candidate_sha for v in verdicts[-2:])
    assert any(
        e["type"] == "stage.entered" and e["payload"].get("stage") == "M-SECURITY"
        for e in events
    )


# AC-FR0271-03@v0.8 TRACKS-TRACE revise requires anchored findings or revise_without_findings
def test_revise_requires_anchored_findings(host_repo, trac, event_log, monkeypatch, ci_standin):
    run_id, candidate_sha = _seed_prism_boundary(host_repo, trac, anchored=True)
    query = trac("discuss", "query", "--file", "spec.md")
    assert query.returncode == 0 and query.stdout.strip()
    _configure_ci(monkeypatch, ci_standin, candidate_sha)
    _set_simulation(monkeypatch, "prism:VERIFY_FINAL=revise")
    store = Store(paths.tracks_home(host_repo))
    try:
        executor = Executor(store, host_repo, run_id)
        executor.backend = _AnchoredFinalBackend(host_repo, "v0.8")
        executor.issue(
            Command(kind="judge_full_f_reuse", params={"candidate_sha": candidate_sha})
        )
    finally:
        store.close()

    events = event_log(run_id)
    verdict = [e for e in events if e["type"] == "prism.verdict"][-1]
    assert verdict["payload"]["verdict"] == "revise"
    assert verdict["payload"]["scope"] == "verify_final"
    assert verdict["payload"]["candidate_sha"] == candidate_sha
    attention = [e for e in events if e["type"] == "attention.required"][-1]
    assert attention["payload"]["reason"] == "verify_final_rejected"


def test_revise_without_findings_blocks(host_repo, trac, event_log, monkeypatch, ci_standin):
    """A revise result without a discussion anchor remains fail-closed."""
    run_id, candidate_sha = _seed_prism_boundary(host_repo, trac)
    _configure_ci(monkeypatch, ci_standin, candidate_sha)
    _set_simulation(monkeypatch, "prism:VERIFY_FINAL=revise")
    store = Store(paths.tracks_home(host_repo))
    try:
        Executor(store, host_repo, run_id).issue(
            Command(kind="judge_full_f_reuse", params={"candidate_sha": candidate_sha})
        )
    finally:
        store.close()

    events = event_log(run_id)
    verdict = [e for e in events if e["type"] == "prism.verdict"][-1]
    attention = [e for e in events if e["type"] == "attention.required"][-1]
    assert verdict["payload"]["verdict"] == "revise"
    assert attention["payload"]["reason"] == "revise_without_findings"
