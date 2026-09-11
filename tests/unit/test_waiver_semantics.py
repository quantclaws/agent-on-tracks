"""FR-0286 §5 waiver semantics through the machine projection and the FULL gate.

Layer 1: ``known_issue.registered`` lifts the M-IMPL escalation park, closes
the failed task as ``waived`` (an independent terminal state, never
task.completed / PROVEN / FIXED) and lets the task graph reach EXIT once every
task is terminal. ``known_issue.rejected`` (mechanism/security exclusions)
must keep the run parked.

Layer 2: nodes bound to the waived AC are still executed and recorded by the
FULL round but no longer block ``passed``/``full_f_eligible``; the ledger
never opens identities for them.
"""

from __future__ import annotations

import subprocess

from tests.unit.helpers import seq
from tracks.executor.test_select import rebuild_ledger
from tracks.kernel import decide, project
from tracks.kernel.events import Command

_WAIVED_MARKER = "# AC-FR9001-01@v0.8 TRACKS-TRACE waived fixture\n"
_WAIVED_FAILURE = _WAIVED_MARKER + "\n\ndef test_waived_failure():\n    assert False\n"
_WAIVED_NODE = "tests/unit/test_waived.py::test_waived_failure"


def _park_and_register(items, **registration):
    """M-IMPL taskgraph + park + one known-issue registration event list."""
    payload = {
        "label": "known-issue",
        "issue_number": 100,
        "url": "https://github.com/acme/host/issues/100",
        "item_or_ac": "AC-FR9001-01",
        "candidate_sha": "c" * 40,
        "evidence_refs": ["repair:behavior"],
        "fixed": False,
    }
    payload.update(registration)
    return seq(*items, ("known_issue.registered", payload))


def test_registered_known_issue_lifts_park_and_waives_task():
    s = project(
        _park_and_register(
            [
                ("stage.entered", {"stage": "M-IMPL"}),
                (
                    "taskgraph.committed",
                    {"task_count": 2, "tasks": [{"task_id": "T-001"}, {"task_id": "T-002"}]},
                ),
                ("task.started", {"task_id": "T-001"}),
                ("ledger.opened", {
                    "node": _WAIVED_NODE,
                    "failure_signature": "sig-1",
                    "state": "OPEN",
                    "task_id": "T-001",
                }),
                ("run.breaker_tripped", {"condition": "attempt budget exhausted"}),
            ],
            task_id="T-001",
            ac_refs=["AC-FR9001-01"],
            node_refs=[_WAIVED_NODE],
        )
    )
    assert s.status == "active" and s.awaiting is None
    assert s.substate == "TASK_DISPATCH", "T-002 remains selectable"
    assert s.waived_task_ids == ["T-001"]
    assert s.ledger_open == 0
    assert any(
        entry.get("state") == "WAIVED" for entry in s.ledger_entries.values()
    ), "waived ledger entries keep an independent terminal state"
    cmd = decide(s)
    assert isinstance(cmd, Command) and cmd.kind == "select_task"


def test_all_tasks_waived_reaches_island_and_exit():
    s = project(
        seq(
            ("stage.entered", {"stage": "M-IMPL"}),
            (
                "taskgraph.committed",
                {"task_count": 1, "tasks": [{"task_id": "T-001"}]},
            ),
            ("full.executed", {"round": "FULL_F"}),
            ("run.breaker_tripped", {"condition": "attempt budget exhausted"}),
            (
                "known_issue.registered",
                {
                    "label": "known-issue",
                    "issue_number": 100,
                    "candidate_sha": "c" * 40,
                    "task_id": "T-001",
                    "ac_refs": ["AC-FR9001-01"],
                    "node_refs": [_WAIVED_NODE],
                    "fixed": False,
                },
            ),
        )
    )
    assert s.substate == "ISLAND_GATE_2"
    passed = project(
        seq(
            ("stage.entered", {"stage": "M-IMPL"}),
            (
                "taskgraph.committed",
                {"task_count": 1, "tasks": [{"task_id": "T-001"}]},
            ),
            ("full.executed", {"round": "FULL_F"}),
            ("run.breaker_tripped", {"condition": "attempt budget exhausted"}),
            (
                "known_issue.registered",
                {
                    "label": "known-issue",
                    "issue_number": 100,
                    "candidate_sha": "c" * 40,
                    "task_id": "T-001",
                    "ac_refs": ["AC-FR9001-01"],
                    "node_refs": [_WAIVED_NODE],
                    "fixed": False,
                },
            ),
            ("verdict.passed", {"check": "island_2"}),
        )
    )
    cmd = decide(passed)
    assert isinstance(cmd, Command) and cmd.kind == "write_frontmatter", (
        "waived tasks must not block the M-IMPL EXIT"
    )


def test_rejected_known_issue_keeps_the_park():
    events = seq(
        ("stage.entered", {"stage": "M-IMPL"}),
        ("task.started", {"task_id": "T-001"}),
        ("run.breaker_tripped", {"condition": "attempt budget exhausted"}),
        (
            "known_issue.rejected",
            {
                "label": "known-issue",
                "issue_number": None,
                "candidate_sha": "c" * 40,
                "task_id": "T-001",
                "reason": "not_product_defect",
            },
        ),
    )
    s = project(events)
    assert s.status == "awaiting_human"
    assert s.awaiting == "escalation", "mechanism/security rejections are not waivable"
    assert s.waived_task_ids == []


def test_registered_known_issue_outside_m_impl_is_inert():
    s = project(
        seq(
            ("stage.entered", {"stage": "M-VERIFY"}),
            ("run.breaker_tripped", {"condition": "verify stop"}),
            (
                "known_issue.registered",
                {"candidate_sha": "c" * 40, "task_id": "T-001", "fixed": False},
            ),
        )
    )
    assert s.stage == "M-VERIFY"
    assert s.status == "awaiting_human" and s.awaiting == "escalation"


# -- layer 2: FULL exclusion of waived AC nodes --------------------------------


def _full_host_with_waived_failure(tmp_path):
    from tests.unit.test_fullf_evidence_oob import _full_host

    repo, store, executor = _full_host(tmp_path)
    (repo / "tests" / "unit" / "test_waived.py").write_text(
        _WAIVED_FAILURE, encoding="utf-8"
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "waived fixture"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return repo, store, executor


def _run_full(executor, store, command_id="C-FULL"):
    return executor._execute_full_round(
        Command("check_island_2", command_id=command_id),
        store.state("RUN"),
        "FULL_F",
        {},
    )


def _register_waiver(store):
    store.append(
        "RUN",
        "v0.8",
        "known_issue.registered",
        {
            "label": "known-issue",
            "issue_number": 100,
            "url": "https://github.com/acme/host/issues/100",
            "item_or_ac": "AC-FR9001-01",
            "candidate_sha": "c" * 40,
            "task_id": "T-001",
            "ac_refs": ["AC-FR9001-01"],
            "node_refs": [_WAIVED_NODE],
            "evidence_refs": ["repair:behavior"],
            "fixed": False,
        },
    )


def test_full_round_waives_bound_nodes_but_records_execution(tmp_path):
    repo, store, executor = _full_host_with_waived_failure(tmp_path)
    first = _run_full(executor, store, "C-1")
    assert first["passed"] is False and _WAIVED_NODE in first["failed_nodes"]

    _register_waiver(store)
    second = _run_full(executor, store, "C-2")

    assert second["passed"] is True, "waived nodes must not block required-green"
    assert second["failed_nodes"] == []
    assert second["waived_nodes"] == [_WAIVED_NODE]
    assert second["full_f_eligible"] is True
    outcomes = executor._read_runtime_blob(second["outcomes_ref"])
    recorded = {item["node"]: item["status"] for item in outcomes}
    assert recorded[_WAIVED_NODE] == "failed", (
        "the execution record is never deleted -- only the blocking set changes"
    )

    executor._record_full_failures(
        Command("check_island_2", command_id="C-LEDGER"), store.state("RUN"), first, {}
    )
    assert rebuild_ledger(
        [
            {"seq": ev.seq, "type": ev.type, "payload": dict(ev.payload)}
            for ev in store.events("RUN")
        ]
    ) == {}, "a waived failure opens no ledger identity"
    store.close()


def test_full_f_producer_accepts_waived_only_failures(tmp_path):
    repo, store, executor = _full_host_with_waived_failure(tmp_path)
    _run_full(executor, store, "C-1")
    _register_waiver(store)
    _run_full(executor, store, "C-2")
    candidate = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    executor._emit_full_f_judgment(
        Command("judge_full_f_reuse", command_id="C-REUSE"), candidate
    )

    assert any(
        event.type == "evidence.reused"
        and event.payload.get("kind") == "full_f"
        and event.payload.get("candidate_sha") == candidate
        for event in store.events("RUN")
    ), "the M-VERIFY reuse judgment must accept the waiver-adjusted FULL_F evidence"
    store.close()


# AC-FR0275-04: a blocked publish face parks the M-RELEASE decider (a
# zero-effect preflight failure must never re-issue execute_publish in a
# tight loop); a fresh decision only follows a new preview.
def test_release_decider_parks_on_blocked_publish():
    from tracks.kernel.release import decide_release_stage
    from tracks.kernel.machine import State

    s = State()
    s.stage = "M-RELEASE"
    s.substate = "AWAITING_RELEASE"
    s.release_decision = "release"
    s.publish_status = None
    cmd = decide_release_stage("M-RELEASE", "AWAITING_RELEASE", s)
    assert cmd is not None and cmd.kind == "execute_publish"
    for terminal in ("blocked", "done", "reconciled_skip"):
        s.publish_status = terminal
        assert decide_release_stage("M-RELEASE", "AWAITING_RELEASE", s) is None, terminal
