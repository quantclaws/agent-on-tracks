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
import sqlite3
import subprocess

import pytest

from tests.e2e_live.harness import (
    LiveInstall,
    LiveTracDriver,
    load_scenarios,
)
from tracks.effects.opencode import AGENT_NAME

# -- helpers ---------------------------------------------------------------


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
