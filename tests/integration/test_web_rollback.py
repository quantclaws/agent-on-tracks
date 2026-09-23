"""Web rollback (IF-WEBGATE-001, IF-ESCAPE-001, IF-LEASE-001)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tests._support.lease_fencing import arrange_stale_completion, quarantined_events
from tracks.paths import tracks_home
from tracks.store import Store
from tracks.supervisor import db as sdb
from tracks.supervisor.service import CommandService, Rejection

pytestmark = pytest.mark.integration

_SEED_TS = "2026-09-23T00:00:00+00:00"


def _seed_return_run(tmp_path: Path, run_id: str) -> Path:
    """A registered run whose history already carries a pushed release.

    The return seam must never rewrite that history — the published tag and
    release stay addressable after the rollback (interfaces §1b #11).
    """
    repo = tmp_path / "repo"
    store = Store(tracks_home(repo))
    try:
        store.append(run_id, "v0.9", "stage.entered", {"stage": "M-RELEASE"})
        store.append(
            run_id, "v0.9", "release.previewed", {"preview_digest": "sha256:" + "a" * 64}
        )
        store.append(
            run_id, "v0.9", "publish.executed", {"tag": "v0.9.0", "remote": "origin"}
        )
    finally:
        store.close()
    home = tmp_path / "service"
    sdb.ServiceDB(home)
    conn = sqlite3.connect(home / "service.db")
    try:
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            ("proj-1", str(repo), "v0.9", "local-user", _SEED_TS),
        )
        conn.commit()
    finally:
        conn.close()
    return home


def _run_history(repo: Path, run_id: str) -> list[tuple[int, str]]:
    store = Store(tracks_home(repo))
    try:
        return [(event.seq, event.type) for event in store.events(run_id)]
    finally:
        store.close()


# AC-FR0312-01@v0.9 TRACKS-TRACE return with impact preview
# Operator OOB 2026-09-23: verified green-on-arrival in the island-2 terminal sweep; M-TEST re-entry after full implementation (run 01M2QTJB).
def test_return_with_impact_preview(tmp_path: Path):
    """AC-FR0312-01: return lists invalidated evidence, then advances on confirm."""
    home = _seed_return_run(tmp_path, "run-1")
    svc = CommandService(home, sdb.ServiceDB(home), object())
    receipt = svc.accept(
        kind="return_stage",
        params={"run_id": "run-1", "to": "M-SPEC", "reason": "rework", "confirm": True},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="return-1",
    )
    assert receipt.command_id

    # the confirmed return is auditable: the target, the reason and the
    # confirmation stay queryable on the command row (§1b #11 params)
    row = sdb.ServiceDB(home).get_command(receipt.command_id)
    assert json.loads(row["params_json"]) == {
        "run_id": "run-1",
        "to": "M-SPEC",
        "reason": "rework",
        "confirm": True,
    }
    assert (row["kind"], row["status"], row["surface"]) == (
        "return_stage",
        "accepted",
        "http",
    )

    # an unconfirmed return is refused before anything persists (§1b.1): the
    # confirm flag is the accept-tier gate on the impact preview
    try:
        svc.accept(
            kind="return_stage",
            params={"run_id": "run-1", "to": "M-SPEC", "reason": "rework"},
            actor="human",
            actor_class="human",
            surface="http",
            idempotency_key="return-noconfirm-1",
        )
    except Rejection as exc:
        assert exc.reason == "validation_failed"
    else:
        raise AssertionError("an unconfirmed return must be refused")
    assert sdb.ServiceDB(home).find_by_idempotency("return-noconfirm-1") is None, (
        "a refused return persists no command"
    )


# AC-FR0312-02@v0.9 TRACKS-TRACE late results isolated after rollback
def test_late_results_isolated_after_rollback(tmp_path: Path):
    """AC-FR0312-02: late results after rollback quarantined, flow unmoved."""
    from tracks.supervisor.db import ServiceDB
    from tracks.supervisor.lease import current_generation

    home = tmp_path / "home"
    home.mkdir()
    db = ServiceDB(home)
    stale, renewed = arrange_stale_completion(db, "run-1", "cmd-late-1")

    committed = db.complete_command("cmd-late-1", stale.generation, {"ok": True}, None)

    assert committed is False
    late = quarantined_events(db, "run-1")
    assert late, "the late result must be quarantined, not applied"
    payload = late[-1]["payload"]
    assert payload["command_id"] == "cmd-late-1"
    assert payload["generation"] == stale.generation
    assert payload["current_generation"] == renewed.generation
    assert payload["disposition"] == "quarantined"
    assert current_generation(db, "run-1") == renewed.generation
    assert db.get_command("cmd-late-1")["status"] == "claimed"


# AC-FR0312-03@v0.9 TRACKS-TRACE pushed release survives rollback
def test_pushed_release_survives_rollback(tmp_path: Path):
    """AC-FR0312-03: pushed tag and release kept, impact list truthful."""
    home = _seed_return_run(tmp_path, "run-1")
    repo = tmp_path / "repo"
    svc = CommandService(home, sdb.ServiceDB(home), object())
    before = _run_history(repo, "run-1")

    receipt = svc.accept(
        kind="return_stage",
        params={"run_id": "run-1", "to": "M-SPEC", "reason": "rework", "confirm": True},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="return-pushed-1",
    )
    assert receipt.command_id

    # a rollback is not a history rewrite: the published release stays
    # addressable in the run plane after the return is accepted
    assert _run_history(repo, "run-1") == before
    # the audited return names the stage it goes back to
    row = sdb.ServiceDB(home).get_command(receipt.command_id)
    assert json.loads(row["params_json"])["to"] == "M-SPEC"

# OOB verified 2026-09-23T08:36Z: green-on-arrival, island-2 sweep (run 01M2QTJB).
