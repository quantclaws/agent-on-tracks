"""Runtime repair: mechanism stops never become Known Issues, and a fixed
registration supersedes the earlier unfixed one everywhere.

Pins the live-run repair for bd0c68eb.../3: a version-gate stop whose reason
is a mechanism failure (remote/credentials unavailable) must be rejected as
not_product_defect, while a real product gate failure keeps the FR-0286 §5
waiver path.
"""

from __future__ import annotations

import pytest

from tests.unit.test_verify_park_coverage import _cmd, _ev, _Host
from tracks import paths
from tracks.cli import main as cli_main
from tracks.cli import release_cmd
from tracks.cli.known_issue_cmd import cmd_known_issue
from tracks.executor.m_impl_ledger import MImplLedgerMixin
from tracks.executor.repair import (
    latest_known_issues,
    list_known_issues_for_preview,
    registered_known_issues,
    waived_associations,
)
from tracks.kernel.machine import State
from tracks.store import Store

_CANDIDATE = "c" * 40
_AC = "AC-FR0348-01"


def _gate_park_events() -> list:
    return [
        _ev(1, "repair.round_started", {"round": 1}),
        _ev(2, "repair.round_started", {"round": 2}),
        _ev(3, "repair.round_started", {"round": 3}),
        _ev(
            4,
            "task.started",
            {"task_id": "T-001", "task": {"ac_refs": [_AC]}},
        ),
        _ev(
            5,
            "full.executed",
            {
                "failed_nodes": [
                    "tests/integration/test_install_package.py::test_version_bump_matches"
                ]
            },
        ),
        _ev(6, "verdict.failed", {"check": "impl_defect", "item_or_ac": _AC}),
    ]


@pytest.mark.parametrize(
    "reason",
    (
        "remote_unavailable",
        "missing_credentials",
        "missing_token",
        "ci_missing_credentials",
    ),
)
def test_mechanism_gate_failure_rejects_known_issue(tmp_path, reason):
    host = _Host(tmp_path, events=_gate_park_events())

    assert host._park_repair_route(_cmd(), _CANDIDATE, "gate", reason) is False
    assert host.store._events[-1].type == "known_issue.pending"
    assert host._park_repair_route(_cmd(), _CANDIDATE, "gate", reason) is False

    events = list(host.store.events("RUN"))
    types = [event.type for event in events]
    assert "known_issue.registered" not in types
    rejected = [event for event in events if event.type == "known_issue.rejected"]
    assert len(rejected) == 1
    assert rejected[0].payload["reason"] == "not_product_defect"
    assert rejected[0].payload["kind"] == "mechanism_failure"
    assert rejected[0].payload["item_or_ac"] == _AC

    associations = waived_associations(events)
    assert _AC not in associations["acs"]
    assert "T-001" not in associations["tasks"]
    assert list_known_issues_for_preview("RUN", events) == []
    assert release_cmd._known_issues_fragment(events) == ""

    # A further drive must not turn the pending/rejected disposition into a
    # registration.
    host._park_repair_route(_cmd(), _CANDIDATE, "gate", reason)
    assert not [
        event for event in host.store.events("RUN") if event.type == "known_issue.registered"
    ]


@pytest.mark.parametrize("reason", ("version_mismatch", "version_decl_mismatch"))
def test_product_gate_failure_still_registers_known_issue(tmp_path, reason):
    host = _Host(tmp_path, events=_gate_park_events())

    assert host._park_repair_route(_cmd(), _CANDIDATE, "gate", reason) is False
    assert host.store._events[-1].type == "known_issue.pending"
    assert host._park_repair_route(_cmd(), _CANDIDATE, "gate", reason) is False

    events = list(host.store.events("RUN"))
    registered = [
        event for event in events if event.type == "known_issue.registered"
    ]
    assert len(registered) == 1
    assert registered[0].payload["fixed"] is False
    assert _AC in waived_associations(events)["acs"]
    assert len(list_known_issues_for_preview("RUN", events)) == 1


def _registration(number: int, *, task_id: str = "T-001", ac: str = _AC, **extra) -> dict:
    payload = {
        "label": "known-issue",
        "issue_number": number,
        "url": f"https://github.com/acme/host/issues/{number}",
        "item_or_ac": ac,
        "candidate_sha": _CANDIDATE,
        "task_id": task_id,
        "ac_refs": [ac],
        "node_refs": [],
        "evidence_refs": ["repair:gate"],
        "fixed": False,
    }
    payload.update(extra)
    return payload


def _active_run_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    store = Store(paths.tracks_home(repo))
    run_id = "RUN"
    store.append(run_id, "v0.12", "stage.entered", {"stage": "M-RELEASE"})
    return repo, store, run_id


def test_latest_known_issues_uses_latest_event_per_number():
    events = [
        _ev(1, "known_issue.registered", _registration(100)),
        _ev(2, "known_issue.registered", _registration(100, fixed=True)),
        _ev(3, "known_issue.registered", _registration(101, ac="AC-FR0348-02")),
        _ev(4, "known_issue.registered", _registration(102, fixed=True)),
    ]

    latest = latest_known_issues(events)
    assert [entry["issue_number"] for entry in latest] == [100, 101, 102]
    assert latest[0]["fixed"] is True
    assert [entry["issue_number"] for entry in registered_known_issues(events)] == [101]


def test_resolve_appends_fixed_and_supersedes_everywhere(tmp_path, capsys):
    repo, store, run_id = _active_run_repo(tmp_path)
    store.append(
        run_id,
        "v0.12",
        "known_issue.registered",
        _registration(100, title="false version-gate waiver", reason="remote_unavailable"),
    )
    before = len(list(store.events(run_id)))

    assert cmd_known_issue(repo, "resolve", "--issue", "100", "--reason", "install gate passes") == 0
    out = capsys.readouterr().out
    assert "known issue #100 resolved: fixed=true" in out

    events = list(store.events(run_id))
    assert len(events) == before + 1
    resolved = events[-1]
    assert resolved.type == "known_issue.registered"
    assert resolved.payload["fixed"] is True
    assert resolved.payload["issue_number"] == 100
    assert resolved.payload["url"] == "https://github.com/acme/host/issues/100"
    assert resolved.payload["label"] == "known-issue"
    assert resolved.payload["item_or_ac"] == _AC
    assert resolved.payload["candidate_sha"] == _CANDIDATE
    assert resolved.payload["task_id"] == "T-001"
    assert resolved.payload["ac_refs"] == [_AC]
    assert resolved.payload["node_refs"] == []
    assert resolved.payload["evidence_refs"] == ["repair:gate"]
    assert resolved.payload["reason"] == "install gate passes"
    assert resolved.payload["resolved_by"]

    assert list_known_issues_for_preview(run_id, events) == []
    associations = waived_associations(events)
    assert associations == {"acs": set(), "nodes": set(), "tasks": set()}
    assert MImplLedgerMixin._effective_completed_task_ids(events, State(run_id=run_id)) == set()
    assert release_cmd._known_issues_fragment(events) == ""
    assert release_cmd._unlisted_known_issues(
        events, {"known_issues": [{"issue": "acme/host#100"}]}
    ) == []
    assert (
        release_cmd._release_known_issue_guard(
            None, run_id, "v0.12", "release", {"known_issues": []}, events
        )
        is None
    )

    # Idempotent: the latest registration is already fixed -- no new event.
    assert cmd_known_issue(repo, "resolve", "--issue", "100", "--reason", "again") == 0
    assert "known issue #100 already fixed" in capsys.readouterr().out
    assert len(list(store.events(run_id))) == before + 1
    store.close()


def test_fixed_100_does_not_affect_unrelated_unfixed_102(tmp_path):
    repo, store, run_id = _active_run_repo(tmp_path)
    store.append(
        run_id,
        "v0.12",
        "known_issue.registered",
        _registration(100, title="false waiver"),
    )
    store.append(
        run_id,
        "v0.12",
        "known_issue.registered",
        _registration(
            102,
            task_id="T-003",
            ac="AC-FR0348-02",
            title="real product waiver",
        ),
    )
    assert cmd_known_issue(repo, "resolve", "--issue", "100", "--reason", "false alarm") == 0

    events = list(store.events(run_id))
    preview = list_known_issues_for_preview(run_id, events)
    assert [entry["issue"] for entry in preview] == ["acme/host#102"]
    assert preview[0]["waiver"] == "AC-FR0348-02"
    fragment = release_cmd._known_issues_fragment(events)
    assert fragment == " known_issues={real product waiver}"
    associations = waived_associations(events)
    assert associations["acs"] == {"AC-FR0348-02"}
    assert associations["tasks"] == {"T-003"}
    assert MImplLedgerMixin._effective_completed_task_ids(events, State(run_id=run_id)) == {
        "T-003"
    }
    assert release_cmd._unlisted_known_issues(events, {"known_issues": []}) == ["102"]
    guard = release_cmd._release_known_issue_guard(
        store, run_id, "v0.12", "release", {"known_issues": []}, events
    )
    assert guard is not None and "102" in guard
    assert release_cmd._unlisted_known_issues(
        events, {"known_issues": [{"issue": "acme/host#102"}]}
    ) == []
    store.close()


def test_resolve_unknown_issue_fails_without_writing(tmp_path, capsys):
    repo, store, run_id = _active_run_repo(tmp_path)
    store.append(
        run_id,
        "v0.12",
        "known_issue.registered",
        _registration(100),
    )
    before = len(list(store.events(run_id)))

    assert cmd_known_issue(repo, "resolve", "--issue", "999", "--reason", "nope") != 0
    assert "unknown known issue" in capsys.readouterr().err
    assert len(list(store.events(run_id))) == before
    store.close()


def test_cli_usage_and_subcommand_registry_advertise_known_issue(capsys):
    from tracks.executor.validate import TRAC_SUBCOMMANDS

    assert "known-issue" in TRAC_SUBCOMMANDS
    assert "known-issue resolve" in cli_main.USAGE
    assert cli_main.main(["--help"]) == 0
    assert "known-issue resolve --issue N" in capsys.readouterr().out
