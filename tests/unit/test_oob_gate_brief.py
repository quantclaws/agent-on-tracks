"""Unit coverage for the OOB gate decision brief (awaiting_gate.diagnosis).

The diagnosis is an additive projection field derived only from the run's
event log + state: cause mapping, attempt accounting, convergence judgement,
agent advice (git-backed) and the suggested path. The gate's pre-existing
keys keep their exact meaning; return candidates are suggestions and never
replace the legal ``return_options`` set.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

from tracks import paths
from tracks.kernel.events import EventEnvelope
from tracks.kernel.machine import State
from tracks.server import projections
from tracks.store.store import Store

_CAUSES = {
    "island": "plan",
    "plan_defect": "plan",
    "test_tasks": "plan",
    "taskgraph": "plan",
    "scope": "plan",
    "test_defect": "test",
    "full_suite": "test",
    "red_defect": "test",
    "red_invalid": "test",
    "collection": "test",
    "anchor_static": "test",
    "impl_defect": "implementation",
    "stub_gap": "design",
    "baseline_defect": "design",
    "public_interface": "design",
    "ac_gap": "acceptance",
    "spec_gap": "spec",
    "contract_error": "runtime",
    "collect_defect": "runtime",
    "test_freeze_contamination": "runtime",
    "reply_format_error": "runtime",
    "manifest_malformed": "runtime",
    "evidence_malformed": "runtime",
    "worktree": "runtime",
    "publish_error": "runtime",
    "diagnose_contract_violation": "runtime",
    "lineage": "process",
    "trace": "process",
    "commit": "process",
}
_ACTIONS = {
    "plan": "replan",
    "test": "return_m_test",
    "implementation": "retry",
    "design": "redesign",
    "acceptance": "return_m_test",
    "spec": "return_m_test",
    "runtime": "retry",
    "process": "retry",
    "unknown": "unknown",
}
_RETURN_CANDIDATES = {
    "plan": ["M-DESIGN"],
    "test": ["M-TEST"],
    "implementation": ["M-DESIGN"],
    "design": ["M-DESIGN"],
    "acceptance": ["M-ACC"],
    "spec": ["M-SPEC"],
    "runtime": [],
    "process": ["M-DESIGN"],
    "unknown": [],
}
_DIAGNOSIS_KEYS = frozenset(
    {
        "cause",
        "cause_label",
        "attempts",
        "attempts_max",
        "retry_worthy",
        "retry_reason",
        "agent_advice",
        "suggested_action",
        "return_candidates",
    }
)


def _event(seq: int, type_: str, payload: dict) -> EventEnvelope:
    return EventEnvelope(
        seq=seq,
        ts=f"2026-10-21T00:00:{seq:02d}+00:00",
        run_id="run-1",
        version="v0.9",
        type=type_,
        schema_version=1,
        command_id=None,
        task_id=None,
        payload=payload,
    )


def _entered(seq: int = 0, stage: str = "M-TEST") -> EventEnvelope:
    return _event(seq, "stage.entered", {"stage": stage})


def _failed(seq: int, reason: str, check: str = "test_defect", evidence: str = "") -> EventEnvelope:
    return _event(
        seq,
        "verdict.failed",
        {"check": check, "reason": reason, "evidence": evidence},
    )


def _worth(events: list[EventEnvelope]) -> tuple[bool, str]:
    return projections._retry_worthiness(
        projections._stage_failure_window(events, "M-TEST"), events
    )


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def _git_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "Tracks Tests")
    _git(repo, "commit", "--allow-empty", "-q", "-m", "M-TEST: first checkpoint")
    first = _git(repo, "rev-parse", "HEAD")
    _git(repo, "commit", "--allow-empty", "-q", "-m", "M-TEST: BLOCKED - re-capture baseline")
    second = _git(repo, "rev-parse", "HEAD")
    return repo, first, second


# -- cause mapping -----------------------------------------------------------


def test_cause_mapping_is_closed():
    for check, cause in _CAUSES.items():
        assert projections._diagnosis_cause(check) == cause
    assert projections._diagnosis_cause("not_a_check") == "unknown"
    assert projections._diagnosis_cause(None) == "unknown"
    assert set(_ACTIONS) == set(_RETURN_CANDIDATES)
    assert set(projections._DIAGNOSIS_CAUSE_LABELS) == set(_ACTIONS)


def test_taskgraph_failure_is_a_plan_defect():
    events = [_entered(), _failed(1, "tasks.json invalid", check="taskgraph")]
    diagnosis = projections._gate_diagnosis(
        events,
        State(stage="M-TEST", status="awaiting_human", awaiting="escalation",
            current_attempt=1),
        None,
    )
    assert diagnosis["cause"] == "plan"
    assert diagnosis["suggested_action"] == "replan"
    assert diagnosis["return_candidates"] == ["M-DESIGN"]


# -- attempts ----------------------------------------------------------------


def test_attempts_count_since_last_stage_entry_and_prefer_state_counter():
    events = [
        _entered(0),
        _failed(1, "old failure at T-001"),
        _entered(2),
        _failed(3, "new failure at T-001"),
        _failed(4, "new failure at T-002"),
    ]

    counted = projections._gate_diagnosis(
        events, State(stage="M-TEST", current_attempt=1), None
    )
    carried = projections._gate_diagnosis(
        events, State(stage="M-TEST", current_attempt=5), None
    )

    assert counted["attempts"] == 2
    assert counted["attempts_max"] == 3
    assert carried["attempts"] == 5


# -- convergence -------------------------------------------------------------


def test_first_failure_is_retry_worthy():
    worthy, reason = _worth([_entered(), _failed(1, "single failure at T-001")])
    assert worthy is True
    assert "首次失败" in reason


def test_identical_signatures_without_new_information_are_not_retry_worthy():
    events = [
        _entered(),
        _failed(1, "same failure at T-001"),
        _failed(2, "same failure at T-002"),
        _failed(3, "same failure at T-003"),
    ]
    worthy, reason = _worth(events)
    assert worthy is False
    assert "连续 3 次同一失败签名" in reason
    assert "无新信息" in reason


def test_differing_signatures_are_retry_worthy():
    events = [
        _entered(),
        _failed(1, "first failure: missing anchor"),
        _failed(2, "second failure: broken import"),
    ]
    worthy, reason = _worth(events)
    assert worthy is True
    assert "失败原因已变化" in reason


def test_prism_findings_between_identical_failures_are_new_information():
    events = [
        _entered(),
        _failed(1, "same failure at T-001"),
        _event(2, "prism.verdict", {"verdict": "revise", "findings": ["new defect"]}),
        _failed(3, "same failure at T-002"),
    ]
    worthy, reason = _worth(events)
    assert worthy is True
    assert "上一轮评审发现了新问题" in reason


def test_prism_verdict_without_findings_is_not_new_information():
    events = [
        _entered(),
        _failed(1, "same failure at T-001"),
        _event(2, "prism.verdict", {"verdict": "revise", "findings": []}),
        _failed(3, "same failure at T-002"),
    ]
    worthy, reason = _worth(events)
    assert worthy is False
    assert "无新信息" in reason


def test_prism_verdict_after_the_latest_failure_is_new_information():
    events = [
        _entered(),
        _failed(1, "tasks.json invalid", check="taskgraph"),
        _failed(2, "tasks.json invalid", check="taskgraph"),
        _event(3, "prism.verdict", {"verdict": "revise", "findings": ["blocker one", "blocker two"]}),
    ]
    worthy, reason = _worth(events)
    assert worthy is True
    assert "评审" in reason


def test_prism_verdict_before_the_latest_failure_is_not_new_information():
    events = [
        _entered(),
        _event(1, "prism.verdict", {"verdict": "revise", "findings": ["old finding"]}),
        _failed(2, "tasks.json invalid", check="taskgraph"),
        _failed(3, "tasks.json invalid", check="taskgraph"),
    ]
    worthy, reason = _worth(events)
    assert worthy is False
    assert "无新信息" in reason


def test_reviewer_finding_after_exhausted_attempts_allows_one_more_retry():
    """The human's own example: attempts are spent, but the review found a
    small bug, so one more attempt has something new to fix -> retry is
    recommended (the attempt count alone never decides this)."""
    events = [
        _entered(),
        _failed(1, "same failure at T-001"),
        _failed(2, "same failure at T-001"),
        _failed(3, "same failure at T-001"),
        _event(4, "prism.verdict", {"verdict": "revise", "findings": ["small bug in helper"]}),
        _failed(4, "same failure at T-001"),
    ]
    worthy, reason = _worth(events)
    assert worthy is True
    assert "上一轮评审发现了新问题" in reason


def test_agent_own_commit_between_identical_failures_is_not_new_information():
    """A fresh sha is what "try again" PRODUCES -- it is not new information.

    Treating it as new information would make every repeated attempt look
    like progress, so a non-converging loop would always be told to retry.
    """
    sha = "a" * 40
    events = [
        _entered(),
        _failed(1, "same failure at T-001", evidence="no commit yet"),
        _event(2, "result.checkpointed", {"commit_sha": sha}),
        _failed(3, "same failure at T-002"),
    ]
    worthy, reason = _worth(events)
    assert worthy is False
    assert "无新信息" in reason


def test_checkpoint_sha_already_in_earlier_evidence_is_not_new_information():
    sha = "b" * 40
    events = [
        _entered(),
        _failed(1, "same failure at T-001", evidence=f"commit {sha}"),
        _event(2, "test.written", {"commit_sha": sha}),
        _failed(3, "same failure at T-002"),
    ]
    worthy, reason = _worth(events)
    assert worthy is False
    assert "无新信息" in reason


# -- suggested action / return candidates ------------------------------------


def test_suggested_action_and_return_candidates_follow_cause():
    for check, cause in _CAUSES.items():
        events = [_entered(), _failed(1, "failure", check=check)]
        diagnosis = projections._gate_diagnosis(
            events,
            State(stage="M-TEST", status="awaiting_human", awaiting="escalation",
                current_attempt=1),
            None,
        )
        assert diagnosis["cause"] == cause
        assert diagnosis["suggested_action"] == _ACTIONS[cause]
        assert diagnosis["return_candidates"] == _RETURN_CANDIDATES[cause]


# -- agent advice ------------------------------------------------------------


def test_agent_advice_prefers_review_summary_over_stale_checkpoint_commit(tmp_path: Path):
    repo, first, _second = _git_repo(tmp_path)
    events = [
        _event(1, "result.checkpointed", {"commit_sha": first}),
        _entered(2),
        _failed(3, "fallback failure reason"),
        _event(4, "prism.verdict", {
            "verdict": "revise",
            "findings": ["blocker one", "blocker two"],
            "review_summary": "bind the review-thread red to AC-FR0359-01",
        }),
    ]

    advice = projections._diagnosis_agent_advice(events, str(repo))

    assert advice == "bind the review-thread red to AC-FR0359-01"


def test_agent_advice_falls_back_to_latest_failure_reason(tmp_path: Path):
    repo, _first, _second = _git_repo(tmp_path)
    events = [
        _event(1, "result.checkpointed", {"commit_sha": "not-a-sha"}),
        _failed(2, "first fallback"),
        _failed(3, "latest fallback"),
    ]

    assert projections._diagnosis_agent_advice(events, str(repo)) == "latest fallback"
    assert projections._diagnosis_agent_advice([], str(repo)) == ""


def test_agent_advice_falls_back_to_agent_self_report(tmp_path: Path):
    repo, _first, _second = _git_repo(tmp_path)
    events = [
        _event(1, "outcome.received", {"status": "failed", "self_report": "agent ran out of time"}),
    ]

    assert projections._diagnosis_agent_advice(events, str(repo)) == "agent ran out of time"


# -- run-detail integration --------------------------------------------------

_SERVICE_SCHEMA = """
CREATE TABLE service_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, type TEXT, project_id TEXT, run_id TEXT,
    command_id TEXT, payload TEXT
);
CREATE TABLE projects (
    project_id TEXT PRIMARY KEY, repo_path TEXT UNIQUE, version TEXT,
    registered_by TEXT, registered_at TEXT
);
CREATE TABLE commands (
    command_id TEXT PRIMARY KEY, kind TEXT, params_json TEXT,
    params_digest TEXT, idempotency_key TEXT UNIQUE, actor TEXT,
    actor_class TEXT, surface TEXT, project_id TEXT, run_id TEXT,
    status TEXT, claim_generation INTEGER, result_json TEXT,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE waits (
    run_id TEXT PRIMARY KEY, wait_class TEXT, reason TEXT, retry_at TEXT,
    known_reset INTEGER, backoff_json TEXT, entered_at TEXT
);
CREATE TABLE leases (
    run_id TEXT PRIMARY KEY, worker_id TEXT, generation INTEGER,
    acquired_at TEXT, expires_at TEXT
);
CREATE TABLE schedule (id INTEGER PRIMARY KEY CHECK (id = 1), active_run TEXT, queue_json TEXT);
"""


def _seed_run_detail(tmp_path: Path, repo: Path, run_id: str, events: list[dict]) -> Path:
    store = Store(paths.tracks_home(repo))
    try:
        for event in events:
            store.append(run_id, "v0.9", event["type"], event["payload"])
    finally:
        store.close()
    home = tmp_path / "service"
    home.mkdir()
    conn = sqlite3.connect(home / "service.db")
    try:
        conn.executescript(_SERVICE_SCHEMA)
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            ("proj-1", str(repo), "v0.9", "alice", "2026-10-21T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO commands VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "cmd-1", "create_run", "{}", "digest-1", "idem-1", "alice", "human",
                "http", "proj-1", run_id, "completed", 1, "{}",
                "2026-10-21T00:00:00+00:00", "2026-10-21T00:00:01+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO leases VALUES (?,?,?,?,?)",
            (run_id, "worker-1", 1, "2026-10-21T00:00:00+00:00",
                "2026-10-21T00:01:00+00:00"),
        )
        conn.execute("INSERT INTO schedule VALUES (1, ?, '[]')", (run_id,))
        conn.commit()
    finally:
        conn.close()
    return home


def test_awaiting_gate_keeps_existing_keys_and_adds_exact_diagnosis_shape(tmp_path: Path):
    repo, _first, _second = _git_repo(tmp_path)
    run_id = "run-gate-brief"
    head = _git(repo, "rev-parse", "HEAD")
    home = _seed_run_detail(
        tmp_path,
        repo,
        run_id,
        [
            {"type": "result.checkpointed", "payload": {"commit_sha": head}},
            {"type": "stage.entered", "payload": {"stage": "M-TEST"}},
            {"type": "verdict.failed",
                "payload": {"check": "test_defect", "reason": "same failure at T-001",
                    "evidence": "bundle-1"}},
            {"type": "verdict.failed",
                "payload": {"check": "test_defect", "reason": "same failure at T-002",
                    "evidence": "bundle-2"}},
            {"type": "verdict.failed",
                "payload": {"check": "test_defect", "reason": "same failure at T-003",
                    "evidence": "bundle-3"}},
        ],
    )

    detail = projections.project_run_detail(home, run_id)
    gate = detail["awaiting_gate"]

    assert set(gate) == {
        "type", "reason", "impact", "primary_action", "pending_human_threads",
        "diagnosis",
    }
    assert gate["type"] == "escalation"
    assert gate["impact"] == "在你做出决定前，run 不会继续推进。"
    assert gate["primary_action"] == "retry"
    assert gate["pending_human_threads"] == []
    assert "M-TEST 到达人工门" in gate["reason"]
    assert "same failure at T-003" in gate["reason"]

    diagnosis = gate["diagnosis"]
    assert set(diagnosis) == _DIAGNOSIS_KEYS
    assert diagnosis == {
        "cause": "test",
        "cause_label": "测试资产或测试执行未通过",
        "attempts": 3,
        "attempts_max": 3,
        "retry_worthy": False,
        "retry_reason": "连续 3 次同一失败签名且无新信息，重试不会不同",
        "agent_advice": "same failure at T-003",
        "suggested_action": "return_m_test",
        "return_candidates": ["M-TEST"],
    }
    assert json.dumps(diagnosis, ensure_ascii=False)
