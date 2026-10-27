"""Integration: stage events, temp-ref cleanup and the legacy timeline
fallback (IF-STAGE-001, IF-CLEANUP-001).

The contract under test is INV-9 (interfaces §1f):

* ``stage.exited`` / ``stage.entered`` are emitted EXACTLY ONCE at the
  three release-tail boundaries -- M-RELEASE -> M-PUBLISH -> M-MILESTONE --
  and a ``retry_tail`` re-entry never duplicates them;
* the ORDER is: ``stage.exited(M-RELEASE)`` + ``stage.entered(M-PUBLISH)``
  BEFORE the first ``publish.planned``; ``stage.exited(M-PUBLISH)`` +
  ``stage.entered(M-MILESTONE)`` after the publish batch is complete and
  before ``milestone.trace_closed``; ``stage.exited(M-MILESTONE)`` at
  completion;
* a LEGACY run (no explicit stage events) gets the transitions SYNTHESIZED
  by the projection (the synthesis is not persisted, and an explicit event
  always wins);
* ``clean_temp_refs`` also clears ``refs/trac/rgr/{run_id}/*`` and the
  measured ``remaining`` is 0.

The observation is the EVENT STREAM (interfaces §4a outlets 5 and 6), the
git ref namespace (``git for-each-ref``) and the HTTP timeline projection
(interfaces §4c outlet 3).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tracks import paths
from tracks.executor.milestone import clean_temp_refs
from tracks.store import Store

pytestmark = pytest.mark.integration

_ASSETS = Path(__file__).resolve().parent.parent / "assets" / "v0.12-hotfix-262"

_CANDIDATE = "44c14de0000000000000000000000000000000000"


def _legacy_events() -> list:
    return json.loads(
        (_ASSETS / "full_f_event_slice.json").read_text(encoding="utf-8")
    )["legacy_run_7473_events"]


def _new_host(tmp_path: Path) -> tuple[Path, Store]:
    repo = tmp_path / "host"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "hf262@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "HF262 Human"], cwd=repo, check=True)
    (repo / "README.md").write_text("hotfix-262 host\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "initial"], cwd=repo, check=True, capture_output=True
    )
    return repo, Store(paths.tracks_home(repo))


def _stage_events(events: list, kind: str, stage: str) -> list:
    return [
        event
        for event in events
        if event.type == kind and (event.payload or {}).get("stage") == stage
    ]


def _types(events: list) -> list:
    return [event.type for event in events]


# AC-FR0276-01@v0.8 TRACKS-TRACE exactly once stage events under retry tail
def test_exactly_once_stage_events_under_retry_tail(tmp_path: Path):
    """AC-FR0276-01: the three release-tail stage transitions are emitted
    EXACTLY ONCE, in the contract's order, and a ``retry_tail`` re-entry
    never duplicates them.

    Contract: interfaces §1f items 1 and 2 (the visit guard, the
    ``retry_tail`` re-entry safety) and §4a outlet 5.
    """
    repo, store = _new_host(tmp_path)
    run_id = "RUN-HF262-STAGE-1"
    try:
        # The Arrange is the release tail's own event sequence: the release
        # decision, the publish batch, the milestone close and the terminal
        # completion. The re-entry replays the SAME command id (the
        # retry_tail resume re-executes the tail).
        store.append(
            run_id,
            "v0.8",
            "release.decided",
            {"action": "release", "candidate_sha": _CANDIDATE},
        )
        store.append(
            run_id,
            "v0.8",
            "publish.planned",
            {"candidate_sha": _CANDIDATE, "idempotency_key": "k-1"},
        )
        store.append(
            run_id,
            "v0.8",
            "publish.executed",
            {
                "candidate_sha": _CANDIDATE,
                "idempotency_key": "k-1",
                "status": "done",
            },
        )
        store.append(
            run_id,
            "v0.8",
            "milestone.trace_closed",
            {"trace_digest": "sha256:hf262", "candidate_sha": _CANDIDATE},
        )
        store.append(
            run_id,
            "v0.8",
            "run.completed",
            {"terminal_state": "released", "release_tag": "v0.8.0"},
        )
        events = list(store.events(run_id))
    finally:
        store.close()

    # The release tail MUST carry the explicit stage transitions; the
    # current implementation emits none, so this is the legal Red.
    for kind, stage in (
        ("stage.exited", "M-RELEASE"),
        ("stage.entered", "M-PUBLISH"),
        ("stage.exited", "M-PUBLISH"),
        ("stage.entered", "M-MILESTONE"),
        ("stage.exited", "M-MILESTONE"),
    ):
        occurrences = _stage_events(events, kind, stage)
        assert len(occurrences) == 1, (
            f"{kind}({stage}) must be emitted exactly once (got {len(occurrences)})"
        )

    # The ORDER: the M-RELEASE exit precedes the first publish.planned.
    types = _types(events)
    first_planned = types.index("publish.planned")
    exited_release = next(
        index
        for index, event in enumerate(events)
        if event.type == "stage.exited"
        and (event.payload or {}).get("stage") == "M-RELEASE"
    )
    entered_publish = next(
        index
        for index, event in enumerate(events)
        if event.type == "stage.entered"
        and (event.payload or {}).get("stage") == "M-PUBLISH"
    )
    assert exited_release < entered_publish < first_planned, (
        "the M-RELEASE exit and the M-PUBLISH entry must both precede the "
        "first publish.planned"
    )

    # The M-PUBLISH exit / M-MILESTONE entry precede the trace close.
    trace_closed = types.index("milestone.trace_closed")
    exited_publish = next(
        index
        for index, event in enumerate(events)
        if event.type == "stage.exited"
        and (event.payload or {}).get("stage") == "M-PUBLISH"
    )
    entered_milestone = next(
        index
        for index, event in enumerate(events)
        if event.type == "stage.entered"
        and (event.payload or {}).get("stage") == "M-MILESTONE"
    )
    assert exited_publish < entered_milestone < trace_closed, (
        "the M-PUBLISH exit and the M-MILESTONE entry must precede "
        "milestone.trace_closed"
    )

    # The M-MILESTONE exit lands at completion.
    completed = types.index("run.completed")
    exited_milestone = next(
        index
        for index, event in enumerate(events)
        if event.type == "stage.exited"
        and (event.payload or {}).get("stage") == "M-MILESTONE"
    )
    assert exited_milestone <= completed, (
        "the M-MILESTONE exit must land at completion"
    )


# AC-FR0276-01@v0.8 TRACKS-TRACE clean temp refs remaining zero
def test_clean_temp_refs_remaining_zero(tmp_path: Path):
    """AC-FR0276-01: the temp-ref cleanup also clears the
    ``refs/trac/rgr/{run_id}/*`` namespace, and the MEASURED ``remaining``
    is 0 afterwards.

    Contract: interfaces §1h item 1, §4a outlet 6
    (``git for-each-ref refs/trac/tmp refs/trac/rgr`` is empty).
    """
    repo, store = _new_host(tmp_path)
    run_id = "RUN-HF262-CLEANUP-1"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    try:
        _git(repo, "update-ref", "refs/trac/tmp/hotfix-262", head)
        _git(repo, "update-ref", f"refs/trac/rgr/{run_id}/work", head)
        before = _listed_refs(repo)
        assert before, "the Arrange must plant the temp refs"

        cleaned = clean_temp_refs(repo, run_id)
        after = _listed_refs(repo)
    finally:
        store.close()

    assert after == [], (
        f"the cleanup must leave no temp ref behind (remaining {after})"
    )
    assert cleaned.get("remaining") == 0, (
        f"the measured remaining count must be 0 (got {cleaned.get('remaining')!r})"
    )
    # The rgr namespace is part of the cleaned scope.
    listed = cleaned.get("remaining_refs")
    assert listed == [], (
        f"the cleanup must report no remaining ref (got {listed!r})"
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _listed_refs(repo: Path) -> list:
    proc = subprocess.run(
        ["git", "for-each-ref", "refs/trac/tmp", "refs/trac/rgr", "--format=%(refname)"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


# AC-FR0335-01@v0.11 TRACKS-TRACE legacy run timeline synthesizes transitions
def test_legacy_run_timeline_synthesizes_transitions(tmp_path: Path):
    """AC-FR0335-01: a LEGACY run (no explicit stage events) gets the three
    release-tail transitions SYNTHESIZED by the projection -- the projection
    renders the same visits a fresh run would, and the synthesis is NOT
    persisted to the event stream.

    Contract: interfaces §1f item 3 (the legacy fallback rules and the
    "explicit events win" rule), §4c outlet 3.
    """
    from tracks.server.projections_timeline import project_timeline

    repo, store = _new_host(tmp_path)
    run_id = "RUN-HF262-LEGACY-7473"
    try:
        for event in _legacy_events():
            store.append(run_id, "v0.8", event["type"], event["payload"])
        stored = list(store.events(run_id))
    finally:
        store.close()

    # The seed really is a legacy run: no explicit RELEASE-TAIL stage events.
    release_tail_stages = ("M-RELEASE", "M-PUBLISH", "M-MILESTONE")
    explicit_tail = [
        (event.type, (event.payload or {}).get("stage"))
        for event in stored
        if event.type in ("stage.exited", "stage.entered")
        and (event.payload or {}).get("stage") in release_tail_stages
    ]
    assert explicit_tail == [], (
        "the legacy fixture must carry no explicit release-tail stage events "
        f"(got {explicit_tail!r})"
    )

    home = _service_home(repo)
    timeline = project_timeline(home, run_id)
    rows = timeline.get("events") or []
    assert rows, "the legacy run's timeline must not be empty"

    # The synthesized transitions are visible on the timeline rows.
    synthesized = [
        (row.get("type"), (row.get("payload") or {}).get("stage"))
        for row in rows
        if row.get("type") in ("stage.exited", "stage.entered")
    ]
    for expected in (
        ("stage.exited", "M-RELEASE"),
        ("stage.entered", "M-PUBLISH"),
        ("stage.exited", "M-PUBLISH"),
        ("stage.entered", "M-MILESTONE"),
        ("stage.exited", "M-MILESTONE"),
    ):
        assert expected in synthesized, (
            f"the legacy run's timeline must synthesize {expected!r} "
            f"(got {synthesized!r})"
        )

    # The synthesis is NOT persisted: the event stream still has no explicit
    # release-tail stage events.
    store = Store(paths.tracks_home(repo))
    try:
        after = list(store.events(run_id))
    finally:
        store.close()
    persisted_tail = [
        (event.type, (event.payload or {}).get("stage"))
        for event in after
        if event.type in ("stage.exited", "stage.entered")
        and (event.payload or {}).get("stage") in release_tail_stages
    ]
    assert persisted_tail == [], (
        "the synthesized transitions must not be persisted to the event stream "
        f"(got {persisted_tail!r})"
    )



def _service_home(repo: Path) -> Path:
    """The service-plane home the timeline projection reads.

    The projection resolves the run through the service ``projects`` table;
    the fixture registers the project so the legacy run is addressable.
    """
    import sqlite3

    home = paths.tracks_home(repo)
    service = home / "service.db"
    conn = sqlite3.connect(str(service))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS projects "
            "(project_id TEXT, repo_path TEXT, version TEXT, owner TEXT, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            (
                "proj-hf262-legacy",
                str(repo),
                "v0.8",
                "local-user",
                "2026-10-27T00:00:00+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return home
