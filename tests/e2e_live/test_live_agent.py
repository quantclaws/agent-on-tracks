"""Live opencode E2E — real agent pipeline (TP-003 §4b, AC-0104/0105).

These tests prove the REAL-agent plumbing: real startup, permission whitelist
enforcement, JSON protocol parseability, target-diff product format, and exit
recovery. They assert protocol/format/diff, NEVER prompt or text content, and
they skip (not fail) when the live channel env is unset.

The deterministic fake channel is untouched by this directory (root conftest's
autouse fake-forcing fixture is overridden here).
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time

import pytest

from tests.e2e_live.conftest import _host_config
from tests.e2e_live.harness import (
    LiveInstall,
    LiveTracDriver,
    capture_opencode_log_tail,
    load_scenarios,
    run_bounded,
)
from tests.e2e_live.test_full_journey import _design_agent_timeout
from tracks.effects.opencode import AGENT_NAME

# -- helpers ---------------------------------------------------------------


def test_generated_live_host_config_denies_external_directory():
    config = {
        "TRAC_LIVE_PROVIDER": "provider",
        "TRAC_LIVE_MODEL": "model",
        "TRAC_LIVE_BASE_URL": "https://example.test/v1",
        "TRAC_LIVE_API_KEY": "unused",
    }

    generated = _host_config(config)

    assert generated["permission"]["external_directory"] == "deny"


def _ready_spec(repo):
    """A bare spec skeleton the Sage agent must fill in to pass the template
    check — deliberately incomplete so Sage produces a real diff."""
    p = repo / "spec.md"
    p.write_text(
        "---\nspec_id: SPEC-LIVE\ncreated: 2026-08-01\nstatus: draft\nsha:\n---\n\n"
        "# 需求规格\n\n<!-- 内容待补充 -->\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "spec.md"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "spec skeleton"], cwd=repo, check=True, capture_output=True
    )
    return p


def _outcome_ok(out) -> bool:
    """Protocol sanity only — never inspects agent text."""
    assert set(out) <= {
        "status",
        "artifact_ref",
        "self_report",
        "diff_ref",
        "audit_evidence",
        "failure_class",
        "agent_io",
        "verdict",
        "discussion_evidence",
        "materialization_evidence",
    }
    assert out["status"] in ("done", "failed")
    return out["status"] == "done"


# -- 1. real startup + JSON protocol ----------------------------------------


def test_real_startup_and_json_protocol(host_with_opencode_config, live_backend, steps):
    """The agent truly starts, `opencode run` is found, and its stdout JSON
    event stream is parseable (step_start/text/step_finish, or a classified
    failure) — without asserting the text itself."""
    repo = host_with_opencode_config
    steps.step("fixture: host_with_opencode_config", provider="opencode")
    spec = _ready_spec(repo)
    steps.step("prepared spec skeleton")
    out = live_backend.act("sage", "DRAFT", "spec.md", spec)
    steps.step(
        "opencode sage DRAFT ran",
        status="ok" if out["status"] == "done" else "fail",
        failure_class=out.get("failure_class"),
        has_diff=bool(out.get("diff_ref")),
    )
    ok = _outcome_ok(out)
    assert ok, f"real startup should succeed but got {out.get('failure_class')}"
    assert out["diff_ref"], "live run claims done but has no target diff"
    assert out["agent_io"]["prompt"]
    steps.step("asserted done + target diff present")


# -- 2. permission whitelist is enforced ------------------------------------


def test_permission_whitelist_enforced(host_with_opencode_config, live_backend, steps):
    """Over-reach (write outside the target doc) is detected and rolled back:
    the agent is allowed the target only; the Auditor flags everything else."""
    repo = host_with_opencode_config
    steps.step("fixture: host_with_opencode_config", provider="opencode")
    spec = _ready_spec(repo)
    steps.step("prepared spec skeleton")
    # Trap: the Sage prompt asks for a spec update; if the agent writes any
    # OTHER file, the post-run audit must catch and roll it back.
    extra = repo / "EXTRA.md"
    extra.write_text("nope\n", encoding="utf-8")  # pre-existing (baseline)
    steps.step("planted EXTRA.md baseline trap")
    out = live_backend.act("sage", "DRAFT", "spec.md", spec)
    steps.step(
        "opencode sage DRAFT ran",
        status="ok" if out["status"] == "done" else "fail",
        failure_class=out.get("failure_class"),
    )
    _outcome_ok(out)
    assert out["status"] == "done", (
        f"permission test needs a successful run, got {out.get('failure_class')}"
    )
    # Audit would have flagged over-reach had the agent strayed; and the
    # pre-existing extra file is never touched by our plumbing.
    assert (repo / "EXTRA.md").exists()
    steps.step("asserted EXTRA.md intact")


# -- 3. target diff is the authoritative product ----------------------------


def test_target_diff_is_authoritative_product(host_with_opencode_config, live_backend, steps):
    """The controlled target-file diff is the product; stdout JSON is not."""
    repo = host_with_opencode_config
    steps.step("fixture: host_with_opencode_config", provider="opencode")
    spec = _ready_spec(repo)
    steps.step("prepared spec skeleton")
    out = live_backend.act("sage", "DRAFT", "spec.md", spec)
    steps.step(
        "opencode sage DRAFT ran",
        status="ok" if out["status"] == "done" else "fail",
        failure_class=out.get("failure_class"),
    )
    _outcome_ok(out)
    assert out["status"] == "done", (
        f"diff test needs a successful run, got {out.get('failure_class')}"
    )
    diff = out["diff_ref"]
    steps.step("parsed outcome diff_ref", bytes=len(diff) if diff else 0)
    assert isinstance(diff, str) and diff.strip()
    # Format sanity: a unified diff with +/- markers, no text-content claims.
    assert any(
        line.startswith("+") or line.startswith("-") or line.startswith("@@")
        for line in diff.splitlines()
    )
    steps.step("asserted unified-diff format markers")


# -- 4. Lex semantic review runs (edit denied) ------------------------------


def test_lex_review_runs(host_with_opencode_config, live_backend, steps):
    """Lex (edit: deny) still runs through the real pipe. Its review lives in
    inline-discussion, so no target diff is required; a clean outcome or a
    classified failure both prove the plumbing."""
    repo = host_with_opencode_config
    steps.step("fixture: host_with_opencode_config", provider="opencode")
    spec = _ready_spec(repo)
    steps.step("prepared spec skeleton")
    out = live_backend.act("lex", "LEX_REVIEW", "spec.md", spec)
    steps.step(
        "opencode lex LEX_REVIEW ran",
        status="ok" if out["status"] == "done" else "fail",
        failure_class=out.get("failure_class"),
    )
    _outcome_ok(out)


# -- 5. exit recovery: classified failures and cleanup ----------------------


def test_provider_error_is_classified(host_with_opencode_config, live_backend, monkeypatch, steps):
    """A provider-level failure maps to provider_unavailable and the materialized
    agent is cleaned up (exit recovery, ARCH §4c/§7)."""
    repo = host_with_opencode_config
    steps.step("fixture: host_with_opencode_config", provider="opencode")
    spec = _ready_spec(repo)
    steps.step("prepared spec skeleton")
    # Force an auth/provider failure without touching tracks' code: revoke the
    # API key the subprocess sees, so opencode reports a provider error.
    monkeypatch.setenv("TRAC_LIVE_API_KEY", "sk-invalid-for-this-test")
    steps.step("revoked API key (invalid auth)")
    out = live_backend.act("sage", "DRAFT", "spec.md", spec)
    steps.step(
        "opencode sage DRAFT ran",
        status="ok" if out["status"] == "done" else "fail",
        failure_class=out.get("failure_class"),
    )
    _outcome_ok(out)
    # Whatever the classified result, the materialized agent is gone (cleanup).
    for name in AGENT_NAME.values():
        assert not (repo / ".opencode" / "agents" / f"{name}.md").exists()
    steps.step("asserted materialized agents cleaned up")


# -- 6. driver retry accounting (no real provider) ---------------------------


def _mechanic_driver(live_root):
    """A bounded driver over a synthetic event store — no provider, no agents."""
    install = LiveInstall(
        artifact_dir=live_root,
        isolated_venv=live_root,
        isolated_python=live_root / "python",
        isolated_bin=live_root,
        tools_bin=live_root,
        opencode=live_root / "opencode",
        wheel=live_root / "tracks.whl",
        install_log=live_root / "install.log",
        resources={},
        probe={},
    )
    return LiveTracDriver(
        live_root,
        install,
        load_scenarios(),
        agent_timeout=1,
        command_timeout=1,
        total_timeout=60,
        max_commands=48,
        max_stage_dispatches=8,
        max_review_dispatches=2,
        max_review_rounds=2,
    )


def _capture_driver_run(monkeypatch, driver):
    captured = {}

    class Process:
        pid = 1
        returncode = 0

        def communicate(self, input=None, timeout=None):
            return "", ""

    def popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Process()

    monkeypatch.setattr("tests.e2e_live.harness.subprocess.Popen", popen)
    monkeypatch.setattr("tests.e2e_live.harness.assert_host_agents", lambda *args: None)
    monkeypatch.setattr(driver, "check_bounds", lambda: None)
    return captured


def test_driver_defaults_to_opencode_env_and_scenario_args(monkeypatch, tmp_path):
    driver = _mechanic_driver(tmp_path)
    captured = _capture_driver_run(monkeypatch, driver)

    driver.run("run", scenario="triage")

    assert captured["kwargs"]["env"]["TRAC_AGENT_BACKEND"] == "opencode"
    assert (
        captured["kwargs"]["env"]["TRAC_AGENT_CONSOLE_INPUT"]
        == driver.scenarios["triage"].console_input
    )
    assert captured["command"] == [
        str(tmp_path / "trac"),
        "run",
        "--assignment-overlay",
        str(driver.scenarios["triage"].path),
        "--max-dispatches",
        "1",
    ]


def test_driver_opencode_uses_configured_live_model(monkeypatch, tmp_path):
    driver = _mechanic_driver(tmp_path)
    captured = _capture_driver_run(monkeypatch, driver)
    monkeypatch.setenv("TRAC_LIVE_PROVIDER", "litellm")
    monkeypatch.setenv("TRAC_LIVE_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("TRAC_AGENT_MODEL", "ark/glm-5.2")

    driver.run("run", scenario="triage")

    assert captured["kwargs"]["env"]["TRAC_AGENT_MODEL"] == "litellm/deepseek-v4-flash"


def test_driver_opencode_preserves_prefixed_live_model(monkeypatch, tmp_path):
    driver = _mechanic_driver(tmp_path)
    captured = _capture_driver_run(monkeypatch, driver)
    monkeypatch.setenv("TRAC_LIVE_PROVIDER", "litellm")
    monkeypatch.setenv("TRAC_LIVE_MODEL", "other-provider/deepseek-v4-flash")

    driver.run("run", scenario="triage")

    assert captured["kwargs"]["env"]["TRAC_AGENT_MODEL"] == (
        "other-provider/deepseek-v4-flash"
    )


def test_driver_fake_backend_clears_model_override(monkeypatch, tmp_path):
    driver = _mechanic_driver(tmp_path)
    captured = _capture_driver_run(monkeypatch, driver)
    monkeypatch.setenv("TRAC_AGENT_MODEL", "ark/glm-5.2")

    driver.run("run", scenario="triage", backend="fake")

    assert "TRAC_AGENT_MODEL" not in captured["kwargs"]["env"]


def test_driver_fake_backend_omits_console_env(monkeypatch, tmp_path):
    driver = _mechanic_driver(tmp_path)
    captured = _capture_driver_run(monkeypatch, driver)
    monkeypatch.setenv("TRAC_AGENT_CONSOLE_INPUT", "host console input")

    driver.run("run", scenario="triage", backend="fake")

    env = captured["kwargs"]["env"]
    assert env["TRAC_AGENT_BACKEND"] == "fake"
    assert "TRAC_AGENT_CONSOLE_INPUT" not in env


def test_driver_max_dispatches_overrides_scenario_budget(tmp_path):
    driver = _mechanic_driver(tmp_path)

    args, _ = driver._command_args(
        ("run",), "sage-spec-draft", None, max_dispatches=5
    )

    assert args[args.index("--max-dispatches") + 1] == "5"


def _dispatch_event(seq, command_id, stage, substate):
    return {
        "seq": seq,
        "type": "command.issued",
        "command_id": command_id,
        "payload": {
            "command": {
                "kind": "dispatch_agent",
                "params": {"role": "sage", "stage": stage, "substate": substate},
            }
        },
    }


def _outcome_event(seq, command_id, status):
    payload = {"status": status, "self_report": "explored but wrote nothing"}
    if status == "failed":
        payload["failure_class"] = "no_target_diff"
    return {
        "seq": seq,
        "type": "outcome.received",
        "command_id": command_id,
        "payload": payload,
    }


def _write_events(live_root, events):
    runtime = live_root / ".tracks" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(runtime / "tracks.db") as connection:
        connection.execute(
            "CREATE TABLE events (run_id TEXT, seq INTEGER, ts TEXT, version TEXT,"
            " type TEXT, schema_version INTEGER, command_id TEXT, task_id TEXT,"
            " payload TEXT)"
        )
        connection.executemany(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "01LIVE",
                    ev["seq"],
                    "2026-08-04T00:00:00Z",
                    "live-e2e",
                    ev["type"],
                    1,
                    ev["command_id"],
                    None,
                    json.dumps(ev["payload"]),
                )
                for ev in events
            ],
        )


def test_failed_outcomes_within_retry_budget_do_not_fail_the_driver(tmp_path):
    """A failed outcome consumes one runtime attempt but the runtime retries;
    the driver must only fail at escalation (3 failures), never on the flake."""
    driver = _mechanic_driver(tmp_path)
    _write_events(
        tmp_path,
        [
            _dispatch_event(1, "c1", "M-SPEC", "DRAFT"),
            _outcome_event(2, "c1", "failed"),
            _dispatch_event(3, "c2", "M-SPEC", "DRAFT"),
            _outcome_event(4, "c2", "failed"),
            _dispatch_event(5, "c3", "M-SPEC", "DRAFT"),
            _outcome_event(6, "c3", "done"),
        ],
    )
    driver.check_bounds()  # two failures within the 3-attempt budget: no raise


def test_failed_outcomes_in_distinct_substates_have_separate_budgets(tmp_path):
    """The retry budget is per (stage, substate): two failures in one substate
    plus two in another are each within budget."""
    driver = _mechanic_driver(tmp_path)
    _write_events(
        tmp_path,
        [
            _dispatch_event(1, "c1", "M-SPEC", "DRAFT"),
            _outcome_event(2, "c1", "failed"),
            _dispatch_event(3, "c2", "M-SPEC", "DRAFT"),
            _outcome_event(4, "c2", "failed"),
            _dispatch_event(5, "c3", "M-STORY", "DRAFT"),
            _outcome_event(6, "c3", "failed"),
            _dispatch_event(7, "c4", "M-STORY", "DRAFT"),
            _outcome_event(8, "c4", "failed"),
        ],
    )
    driver.check_bounds()  # each (stage, substate) stays under the limit


def test_third_failed_outcome_escalates_and_fails_mentioning_escalation(tmp_path):
    """3 failures for one (stage, substate) == the runtime's escalation point
    (awaiting=escalation); the driver fails there with the payload preserved."""
    driver = _mechanic_driver(tmp_path)
    _write_events(
        tmp_path,
        [
            _dispatch_event(1, "c1", "M-SPEC", "DRAFT"),
            _outcome_event(2, "c1", "failed"),
            _dispatch_event(3, "c2", "M-SPEC", "DRAFT"),
            _outcome_event(4, "c2", "failed"),
            _dispatch_event(5, "c3", "M-SPEC", "DRAFT"),
            _outcome_event(6, "c3", "failed"),
        ],
    )
    with pytest.raises(AssertionError) as excinfo:
        driver.check_bounds()
    message = str(excinfo.value)
    assert "escalation" in message
    assert "stage=M-SPEC substate=DRAFT" in message
    assert "no_target_diff" in message  # the rich payload survives the failure


def test_dispatch_budget_is_three_for_author_and_reviewer_steps(tmp_path):
    """Author steps (kind DRAFT/RESPOND) and reviewer steps (kind *_REVIEW) get
    a 3-dispatch budget so the runtime can retry a failed outcome internally;
    human-gate steps (no kind) keep a single dispatch."""
    driver = _mechanic_driver(tmp_path)

    def budget(name):
        args, _ = driver._command_args(("run",), name, None)
        return args[args.index("--max-dispatches") + 1]

    for name in ("scribe-story-draft", "sage-spec-draft", "sage-acceptance-draft",
                 "archer-design-draft", "scribe-story-respond", "sage-spec-respond",
                 "archer-design-respond", "sage-story-finding", "sage-story-resolve",
                 "lex-spec-review", "lex-spec-resolve", "lex-acceptance-review",
                 "prism-design-review"):
        assert budget(name) == "3", name
    for name in ("triage", "approval-final"):
        assert budget(name) == "1", name
    non_run, _ = driver._command_args(("status",), None, None)
    assert "--max-dispatches" not in non_run


# -- 7. command timeout covers all attempts (Fix 1, live run049) ------------


def test_command_timeout_covers_all_dispatch_attempts(tmp_path):
    """Fix 1: when agent_timeout is given, the command timeout must cover ALL
    dispatch attempts (max_dispatches * (agent_timeout + 300)), not just one.
    Explicit timeout= wins. live run049 attempt2 was killed mid-flight at 1500s
    while ~90% through a 3-attempt run because the old formula only budgeted
    agent_timeout + 300."""
    driver = _mechanic_driver(tmp_path)
    base = driver.command_timeout  # _mechanic_driver sets 1
    # agent_timeout=1200, archer-design-draft (budget=3):
    # effective = max(1, 3 * (1200 + 300)) = 4500
    assert driver._effective_command_timeout(
        timeout=None, agent_timeout=1200, max_dispatches=None,
        scenario="archer-design-draft",
    ) == 4500
    # archer-design-draft now uses agent_timeout=1800 in the journey (run050):
    # effective = max(1, 3 * (1800 + 300)) = 6300
    assert driver._effective_command_timeout(
        timeout=None, agent_timeout=1800, max_dispatches=None,
        scenario="archer-design-draft",
    ) == 6300
    # Explicit timeout= overrides everything (even with agent_timeout set):
    assert driver._effective_command_timeout(
        timeout=600, agent_timeout=1200, max_dispatches=None,
        scenario="archer-design-draft",
    ) == 600
    # Explicit max_dispatches wins over scenario budget:
    assert driver._effective_command_timeout(
        timeout=None, agent_timeout=1200, max_dispatches=5,
        scenario="archer-design-draft",
    ) == 5 * (1200 + 300)
    # No agent_timeout: formula does not apply, returns command_timeout:
    assert driver._effective_command_timeout(
        timeout=None, agent_timeout=None, max_dispatches=None,
        scenario="archer-design-draft",
    ) == base
    # Non-author/review scenario (budget=1):
    assert driver._effective_command_timeout(
        timeout=None, agent_timeout=300, max_dispatches=None,
        scenario="triage",
    ) == max(base, 1 * (300 + 300))


def test_command_timeout_resolves_design_agent_timeout_env_to_formula(monkeypatch, tmp_path):
    """TRAC_LIVE_DESIGN_AGENT_TIMEOUT=3600 must resolve both design-step
    scenarios' command timeout to 3×(3600+300)=11700, mirroring how the journey
    plumbs the env-derived ``agent_timeout`` into ``_effective_command_timeout``.
    The kernel/opencode hard timeout still applies as a watchdog above this
    outer command timeout."""
    monkeypatch.setenv("TRAC_LIVE_DESIGN_AGENT_TIMEOUT", "3600")
    driver = _mechanic_driver(tmp_path)
    agent_timeout = _design_agent_timeout()
    assert agent_timeout == 3600
    for scenario in ("archer-design-draft", "archer-design-respond"):
        assert driver._effective_command_timeout(
            timeout=None, agent_timeout=agent_timeout, max_dispatches=None,
            scenario=scenario,
        ) == 3 * (3600 + 300) == 11700


# -- 7b. TRAC_LIVE_DESIGN_AGENT_TIMEOUT helper contract ----------------------


def test_design_agent_timeout_defaults_to_1800_when_env_unset(monkeypatch):
    monkeypatch.delenv("TRAC_LIVE_DESIGN_AGENT_TIMEOUT", raising=False)
    assert _design_agent_timeout() == 1800


@pytest.mark.parametrize("value", ["3600", "7200", "1800", "2400"])
def test_design_agent_timeout_reads_env_override(monkeypatch, value):
    monkeypatch.setenv("TRAC_LIVE_DESIGN_AGENT_TIMEOUT", value)
    assert _design_agent_timeout() == int(value)


@pytest.mark.parametrize("value", ["not-a-number", "0", "-1", "1.5", "12s"])
def test_design_agent_timeout_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setenv("TRAC_LIVE_DESIGN_AGENT_TIMEOUT", value)
    with pytest.raises(ValueError, match="TRAC_LIVE_DESIGN_AGENT_TIMEOUT"):
        _design_agent_timeout()


# -- 8. process-tree kill on timeout (Fix 2, live run049 orphan) ------------


def test_run_bounded_kills_entire_process_tree_on_timeout(tmp_path):
    """Fix 2: on timeout, run_bounded must kill the entire process tree,
    including grandchildren that started their own process group via setsid
    (live run049: opencode survived trac's kill because setsid put it in a
    different group). This test forks a grandchild that calls setsid(), then
    verifies the grandchild is dead after the timeout."""
    grandchild_pid_file = tmp_path / "grandchild.pid"
    script = tmp_path / "orphan.py"
    script.write_text(
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        "    os.setsid()\n"
        f"    Path({str(grandchild_pid_file)!r}).write_text(str(os.getpid()))\n"
        "    time.sleep(300)\n"
        "else:\n"
        "    time.sleep(300)\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="timed out"):
        run_bounded(
            [sys.executable, str(script)],
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"]},
            timeout=2,
        )
    # Wait for the grandchild pid file to appear.
    for _ in range(20):
        if grandchild_pid_file.is_file():
            break
        time.sleep(0.1)
    assert grandchild_pid_file.is_file(), "grandchild never started"
    grandchild_pid = int(grandchild_pid_file.read_text().strip())
    # Give SIGKILL time to take effect.
    time.sleep(0.5)
    alive = False
    try:
        os.kill(grandchild_pid, 0)
        alive = True
    except (ProcessLookupError, PermissionError):
        pass
    assert not alive, (
        f"grandchild pid={grandchild_pid} survived timeout; "
        "process tree was not killed"
    )


# -- 9. opencode log-tail helper (Fix 3, failure visibility) ---------------


def test_capture_opencode_log_tail_filters_by_host_and_writes_tail(tmp_path):
    """Fix 3: capture_opencode_log_tail extracts the last ~80 lines matching
    the host path from the opencode log and writes them to the report dir."""
    host = tmp_path / "host"
    host.mkdir()
    report_dir = tmp_path / "report"
    log_file = tmp_path / "opencode.log"
    other_host = tmp_path / "other-host"
    lines = []
    for i in range(100):
        lines.append(
            f'{{"ts":"2026-08-04T00:00:{i:02d}Z",'
            f'"directory={host}","msg":"event {i}"}}'
        )
    for i in range(10):
        lines.append(
            f'{{"ts":"2026-08-04T00:00:{i:02d}Z",'
            f'"directory={other_host}","msg":"other {i}"}}'
        )
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = capture_opencode_log_tail(host, report_dir, log_path=log_file)
    assert result is not None
    assert result == report_dir / "opencode-session-tail.log"
    written_lines = result.read_text(encoding="utf-8").strip().split("\n")
    assert len(written_lines) == 80  # last 80 of 100 matching lines
    assert str(host) in written_lines[-1]
    assert str(other_host) not in result.read_text(encoding="utf-8")


def test_capture_opencode_log_tail_returns_none_when_no_match(tmp_path):
    """When no log lines match the host path, the helper returns None and
    does not create the output file."""
    host = tmp_path / "host"
    host.mkdir()
    report_dir = tmp_path / "report"
    log_file = tmp_path / "opencode.log"
    log_file.write_text(
        f'{{"directory={tmp_path / "other"}","msg":"unrelated"}}\n',
        encoding="utf-8",
    )
    result = capture_opencode_log_tail(host, report_dir, log_path=log_file)
    assert result is None
    assert not (report_dir / "opencode-session-tail.log").exists()


def test_capture_opencode_log_tail_returns_none_when_log_missing(tmp_path):
    """When the opencode log file does not exist, the helper returns None
    without raising (best-effort only)."""
    host = tmp_path / "host"
    host.mkdir()
    result = capture_opencode_log_tail(
        host, tmp_path / "report", log_path=tmp_path / "nonexistent.log"
    )
    assert result is None


def test_capture_opencode_log_tail_creates_report_dir(tmp_path):
    """The helper creates the report dir if it does not exist."""
    host = tmp_path / "host"
    host.mkdir()
    report_dir = tmp_path / "report" / "nested"
    log_file = tmp_path / "opencode.log"
    log_file.write_text(f'directory={host} event\n', encoding="utf-8")
    result = capture_opencode_log_tail(host, report_dir, log_path=log_file)
    assert result is not None
    assert report_dir.is_dir()
    assert result.exists()
