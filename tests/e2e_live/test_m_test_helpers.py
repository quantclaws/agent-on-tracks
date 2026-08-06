"""Deterministic coverage for the v0.4 M-TEST live-journey assertions."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.e2e_live import m_test_helpers as _helpers
from tests.e2e_live import test_full_journey as _journey
from tests.e2e_live.harness import LiveInstall, prepare_host_venv
from tests.e2e_live.m_test_helpers import (
    _git,
    _setup_baseline_remote,
    assert_criteria_pack_triple,
    assert_shield_assignment_contract,
    assert_shield_no_commit_scope,
    assert_single_test_commit,
    assert_strict_boundary_adjacency,
    assert_test_markers,
    snapshot_git_state,
)
from tests.e2e_live.test_full_journey import _run_m_test_review_loop


def _event(seq: int, event_type: str, payload: dict) -> dict:
    return {"seq": seq, "type": event_type, "payload": payload}


def _boundary_events(*, design_gap: bool = False, m_test_gap: bool = False) -> list[dict]:
    events = [_event(1, "stage.exited", {"stage": "M-DESIGN"})]
    if design_gap:
        events.append(_event(2, "audit.written", {}))
    events.append(_event(len(events) + 1, "stage.entered", {"stage": "M-TEST"}))
    events.append(_event(len(events) + 1, "stage.exited", {"stage": "M-TEST"}))
    if m_test_gap:
        events.append(_event(len(events) + 1, "audit.written", {}))
    events.append(
        _event(
            len(events) + 1,
            "run.completed",
            {"terminal_state": "boundary"},
        )
    )
    return events


def test_strict_boundary_adjacency_accepts_only_adjacent_transitions():
    assert_strict_boundary_adjacency(_boundary_events())

    with pytest.raises(AssertionError):
        assert_strict_boundary_adjacency(_boundary_events(design_gap=True))
    with pytest.raises(AssertionError):
        assert_strict_boundary_adjacency(_boundary_events(m_test_gap=True))


_M_TEST_DOCS = ["test-plan.md", "interfaces.md", "acceptance.md"]
_VALID_TEST_TASKS = [
    {"ac_id": "AC-FR0010-01", "layers": ["integration"], "if_ids": ["IF-HTTP-001"]}
]
_MISSING = object()


def _shield_events(test_tasks: object = _VALID_TEST_TASKS) -> list[dict]:
    assignment = {
        "kind": "WRITE",
        "docs": list(_M_TEST_DOCS),
        "skills": ["tracks-discuz"],
    }
    if test_tasks is not _MISSING:
        assignment["test_tasks"] = test_tasks
    return [
        _event(
            1,
            "command.issued",
            {
                "command": {
                    "kind": "dispatch_agent",
                    "params": {
                        "role": "shield",
                        "substate": "WRITE",
                        "stage": "M-TEST",
                        "assignment": assignment,
                    },
                }
            },
        )
    ]


def test_shield_assignment_contract_validates_test_tasks():
    assert_shield_assignment_contract(_shield_events(), "v0.4")

    invalid_tasks = (
        _MISSING,
        [],
        [{"ac_id": "AC-FR0010-01", "layers": ["unit"], "if_ids": ["IF-HTTP-001"]}],
        [{"ac_id": "AC-FR0010-01", "layers": ["integration"], "if_ids": ["BAD"]}],
    )
    for test_tasks in invalid_tasks:
        with pytest.raises(AssertionError):
            assert_shield_assignment_contract(_shield_events(test_tasks), "v0.4")


_CRITERIA_PACK = {"name": "test-asset-criteria", "version": "0.1"}


def _criteria_events(
    *, assigned_pack: dict | None = None, echoed_pack: dict | None = None,
    runtime_mismatch: bool = False,
) -> list[dict]:
    assigned = _CRITERIA_PACK if assigned_pack is None else assigned_pack
    echoed = _CRITERIA_PACK if echoed_pack is None else echoed_pack
    events = [
        _event(1, "stage.entered", {"stage": "M-TEST"}),
        _event(
            2,
            "command.issued",
            {
                "command": {
                    "kind": "dispatch_agent",
                    "params": {
                        "role": "prism",
                        "substate": "PRISM_REVIEW",
                        "assignment": {"criteria_pack": assigned},
                    },
                }
            },
        ),
        _event(
            3,
            "prism.verdict",
            {"verdict": "pass", "criteria_pack": echoed},
        ),
    ]
    if runtime_mismatch:
        events.append(
            _event(
                4,
                "verdict.failed",
                {"check": "criteria_pack_mismatch"},
            )
        )
    return events


def test_criteria_pack_triple_accepts_matching_runtime_evidence():
    assert_criteria_pack_triple(_criteria_events())


@pytest.mark.parametrize(
    "events",
    [
        _criteria_events(assigned_pack={"name": "other", "version": "0.1"}),
        _criteria_events(echoed_pack={"name": "other", "version": "0.1"}),
        _criteria_events(runtime_mismatch=True),
    ],
    ids=("assignment-mismatch", "verdict-mismatch", "runtime-mismatch"),
)
def test_criteria_pack_triple_rejects_any_mismatch(events):
    with pytest.raises(AssertionError):
        assert_criteria_pack_triple(events)


def test_test_marker_helper_checks_version_and_trace_token(tmp_path):
    tests_dir = tmp_path / "tests"
    test_file = tests_dir / "integration" / "test_marker.py"

    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "# AC-FR0010-01@v0.4 TRACKS-TRACE integration\n"
        "def test_marker():\n"
        "    pass\n",
        encoding="utf-8",
    )
    assert_test_markers(tests_dir, "v0.4")

    test_file.write_text(
        "# AC-FR0010-01@v0.3 TRACKS-TRACE integration\n"
        "def test_marker():\n"
        "    pass\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError):
        assert_test_markers(tests_dir, "v0.4")

    test_file.write_text(
        "# AC-FR0010-01@v0.4 integration\n"
        "def test_marker():\n"
        "    pass\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError):
        assert_test_markers(tests_dir, "v0.4")


class _ReviewDriver:
    def __init__(self, *outputs: str):
        self.outputs = list(outputs)
        self.scenarios: list[str] = []

    def __call__(self, *args, **kwargs):
        assert args == ("run",)
        self.scenarios.append(kwargs["scenario"])
        assert self.outputs, "review loop made an unexpected extra call"
        return SimpleNamespace(stdout=self.outputs.pop(0))


def test_m_test_review_loop_accepts_first_prism_pass():
    driver = _ReviewDriver("run RUN: stage=M-TEST substate=RED_CHECK status=completed awaiting=-")

    result = _run_m_test_review_loop(driver)

    assert "status=completed" in result.stdout
    assert driver.scenarios == ["prism-test-review"]


def test_m_test_review_loop_responds_to_shield_revise():
    driver = _ReviewDriver(
        "run RUN: stage=M-TEST substate=WRITE status=active awaiting=-",
        "run RUN: stage=M-TEST substate=PRISM_REVIEW status=active awaiting=-",
        "run RUN: stage=M-TEST substate=RED_CHECK status=completed awaiting=-",
    )

    _run_m_test_review_loop(driver)

    assert driver.scenarios == [
        "prism-test-review",
        "shield-test-respond",
        "prism-test-review",
    ]


def test_m_test_review_loop_raises_after_two_revises():
    driver = _ReviewDriver(
        "run RUN: stage=M-TEST substate=WRITE status=active awaiting=-",
        "run RUN: stage=M-TEST substate=PRISM_REVIEW status=active awaiting=-",
        "run RUN: stage=M-TEST substate=WRITE status=active awaiting=-",
        "run RUN: stage=M-TEST substate=PRISM_REVIEW status=active awaiting=-",
    )

    with pytest.raises(AssertionError, match="within 2 rounds"):
        _run_m_test_review_loop(driver)
    assert driver.scenarios == [
        "prism-test-review",
        "shield-test-respond",
        "prism-test-review",
        "shield-test-respond",
    ]


class _DesignTailDriver:
    def __init__(self, substate="DISPATCH"):
        self.calls = []
        self.substate = substate

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return SimpleNamespace(
            stdout=(
                f"run RUN: stage=M-TEST substate={self.substate} "
                "status=active awaiting=-"
            )
        )


def _patch_design_exit_resume(monkeypatch, tmp_path, req_baseline):
    calls = {
        "find_req": [],
        "restore": [],
        "remote": [],
        "sanity_req": [],
        "phase_design": [],
        "capture": [],
        "sanity_design": [],
        "m_test": [],
    }
    monkeypatch.setattr(_journey, "_find_design_exit_baseline", lambda _: None)

    def find_req(version):
        calls["find_req"].append(version)
        return req_baseline

    monkeypatch.setattr(_journey, "_find_baseline_for_sha", find_req)
    monkeypatch.setattr(
        _journey,
        "_restore_baseline",
        lambda baseline, root: calls["restore"].append((baseline, root)),
    )
    monkeypatch.setattr(
        _journey,
        "_setup_baseline_remote",
        lambda root: calls["remote"].append(root) or ("origin", ""),
    )

    def sanity_req(driver, root, baseline):
        calls["sanity_req"].append((driver, root, baseline))
        return "REQ-RUN"

    monkeypatch.setattr(_journey, "_sanity_check_resumed_host", sanity_req)

    def phase_design(driver, root, run_id, version, expected_substate="WRITE"):
        calls["phase_design"].append(expected_substate)
        driver("run", scenario="archer-design-draft")

    monkeypatch.setattr(_journey, "_phase_design_to_m_test", phase_design)
    monkeypatch.setattr(
        _journey,
        "_events",
        lambda *_: [
            {"seq": 10, "type": "stage.exited", "payload": {"stage": "M-DESIGN"}},
            {"seq": 11, "type": "stage.entered", "payload": {"stage": "M-TEST"}},
        ],
    )

    def capture(root, version, run_id, checkpoint_substate="WRITE"):
        calls["capture"].append((root, version, run_id, checkpoint_substate))
        return tmp_path / "design-exit"

    monkeypatch.setattr(_journey, "_capture_design_exit_baseline", capture)

    def sanity_design(driver, root, baseline):
        calls["sanity_design"].append((driver, root, baseline))
        return "DESIGN-RUN"

    monkeypatch.setattr(_journey, "_sanity_check_design_exit_host", sanity_design)
    monkeypatch.setattr(
        _journey,
        "_phase_m_test_to_boundary",
        lambda *args, **kwargs: calls["m_test"].append((args, kwargs)),
    )
    return calls


def test_fake_design_baseline_restores_req_and_uses_two_dispatch_budget(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("TRAC_LIVE_FAKE_DESIGN_BASELINE", "1")
    monkeypatch.delenv("TRAC_LIVE_BUILD_BASELINE", raising=False)
    req_baseline = tmp_path / "req-baseline"
    calls = _patch_design_exit_resume(monkeypatch, tmp_path, req_baseline)
    driver = _DesignTailDriver()

    _journey.test_m_test_from_design_exit_baseline(
        tmp_path, None, None, driver, monkeypatch, None
    )

    assert calls["find_req"] == ["live-e2e-code-stats"]
    assert calls["restore"] == [(req_baseline, tmp_path)]
    assert calls["remote"] == [tmp_path]
    assert calls["sanity_req"] == [(driver, tmp_path, req_baseline)]
    assert calls["phase_design"] == ["DISPATCH"]
    assert driver.calls == [
        (
            ("run",),
            {
                "scenario": "archer-design-draft",
                "backend": "fake",
                "max_dispatches": 2,
            },
        )
    ]
    assert calls["capture"] == [
        (tmp_path, "live-e2e-code-stats", "REQ-RUN", "DISPATCH")
    ]
    assert calls["sanity_design"] == [
        (driver, tmp_path, tmp_path / "design-exit")
    ]
    assert calls["m_test"][0][0][0] is driver


def test_design_exit_capture_records_dispatch_substate(monkeypatch, tmp_path):
    root = tmp_path / "host"
    root.mkdir()
    monkeypatch.delenv("TRAC_LIVE_SKIP_BASELINE", raising=False)
    monkeypatch.setattr(_helpers, "_baselines_root", lambda: tmp_path / "baselines")
    monkeypatch.setattr(_helpers, "_tracks_short_sha", lambda: "abc1234")

    baseline = _helpers._capture_design_exit_baseline(
        root, "v1", "RUN", checkpoint_substate="DISPATCH"
    )

    assert baseline is not None
    manifest = _helpers._read_manifest(baseline)
    assert manifest["checkpoint"] == "DESIGN_EXIT_OBSERVED"
    assert manifest["checkpoint_substate"] == "DISPATCH"


def _design_exit_baseline(tmp_path, checkpoint_substate="DISPATCH"):
    baseline = tmp_path / f"baseline-{checkpoint_substate or 'legacy'}"
    baseline.mkdir()
    manifest = {
        "run_id": "RUN",
        "checkpoint": "DESIGN_EXIT_OBSERVED",
    }
    if checkpoint_substate is not None:
        manifest["checkpoint_substate"] = checkpoint_substate
    (baseline / ".tracks-baseline-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return baseline


def _patch_design_exit_events(monkeypatch, *, adjacent=True):
    events = [
        {"seq": 1, "type": "stage.exited", "payload": {"stage": "M-DESIGN"}},
    ]
    if not adjacent:
        events.append({"seq": 2, "type": "audit.written", "payload": {}})
    events.append(
        {
            "seq": len(events) + 1,
            "type": "stage.entered",
            "payload": {"stage": "M-TEST"},
        }
    )
    monkeypatch.setattr(_helpers, "_events", lambda *_: events)


def _status_driver(substate):
    def status(*_):
        return SimpleNamespace(
            stdout=f"run RUN: stage=M-TEST substate={substate} status=active awaiting=-"
        )

    return status


def test_design_exit_sanity_accepts_dispatch_manifest(monkeypatch, tmp_path):
    baseline = _design_exit_baseline(tmp_path, "DISPATCH")
    _patch_design_exit_events(monkeypatch)
    status = _status_driver("DISPATCH")

    assert _helpers._sanity_check_design_exit_host(status, tmp_path, baseline) == "RUN"


def test_design_exit_sanity_rejects_wrong_declared_substate(monkeypatch, tmp_path):
    baseline = _design_exit_baseline(tmp_path, "DISPATCH")
    _patch_design_exit_events(monkeypatch)
    status = _status_driver("WRITE")

    with pytest.raises(AssertionError, match="DISPATCH"):
        _helpers._sanity_check_design_exit_host(status, tmp_path, baseline)


def test_design_exit_sanity_accepts_legacy_write_manifest(monkeypatch, tmp_path):
    baseline = _design_exit_baseline(tmp_path, None)
    _patch_design_exit_events(monkeypatch)
    status = _status_driver("WRITE")

    assert _helpers._sanity_check_design_exit_host(status, tmp_path, baseline) == "RUN"


def test_design_exit_sanity_rejects_non_adjacent_design_transition(monkeypatch, tmp_path):
    baseline = _design_exit_baseline(tmp_path, "DISPATCH")
    _patch_design_exit_events(monkeypatch, adjacent=False)
    status = _status_driver("DISPATCH")

    with pytest.raises(AssertionError, match="not immediately followed"):
        _helpers._sanity_check_design_exit_host(status, tmp_path, baseline)


def test_fake_design_baseline_skips_without_req_baseline(monkeypatch, tmp_path):
    monkeypatch.setenv("TRAC_LIVE_FAKE_DESIGN_BASELINE", "1")
    monkeypatch.setenv("TRAC_LIVE_BUILD_BASELINE", "1")
    calls = _patch_design_exit_resume(monkeypatch, tmp_path, None)
    driver = _DesignTailDriver()

    with pytest.raises(pytest.skip.Exception, match="no M-REQ-APPROVED baseline"):
        _journey.test_m_test_from_design_exit_baseline(
            tmp_path, None, None, driver, monkeypatch, None
        )

    assert calls["restore"] == []
    assert driver.calls == []
    assert calls["m_test"] == []


def test_design_exit_without_fake_flag_preserves_existing_skip(monkeypatch, tmp_path):
    monkeypatch.delenv("TRAC_LIVE_FAKE_DESIGN_BASELINE", raising=False)
    monkeypatch.delenv("TRAC_LIVE_BUILD_BASELINE", raising=False)
    monkeypatch.setattr(_journey, "_find_design_exit_baseline", lambda _: None)
    monkeypatch.setattr(
        _journey,
        "_find_baseline_for_sha",
        lambda *_: pytest.fail("new fake branch must be opt-in"),
    )

    with pytest.raises(pytest.skip.Exception, match="no DESIGN_EXIT_OBSERVED baseline"):
        _journey.test_m_test_from_design_exit_baseline(
            tmp_path, None, None, _DesignTailDriver(), monkeypatch, None
        )


def _git_repo(tmp_path, *, with_tests: bool = True):
    repo = tmp_path / "repo"
    origin = tmp_path / "origin.git"
    repo.mkdir()
    origin.mkdir()
    _git(origin, "init", "--bare")
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "M-TEST tests")
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    if with_tests:
        for layer in ("integration", "e2e"):
            layer_dir = repo / "tests" / layer
            layer_dir.mkdir(parents=True)
            (layer_dir / ".gitkeep").write_text("", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "--set-upstream", "origin", "main")
    return repo


def test_scope_and_single_test_commit_contracts(tmp_path):
    repo = _git_repo(tmp_path)
    before = snapshot_git_state(repo)
    remote_refs = _git(repo, "ls-remote", "origin")
    (repo / "tests" / "integration" / "test_ok.py").write_text(
        "def test_ok():\n    pass\n", encoding="utf-8"
    )
    (repo / "tests" / "e2e" / "test_ok.py").write_text(
        "def test_ok():\n    pass\n", encoding="utf-8"
    )

    assert_shield_no_commit_scope(before, repo, remote_refs)
    _git(repo, "add", "tests")
    _git(repo, "commit", "-m", "freeze tests")
    commit_sha = _git(repo, "rev-parse", "HEAD")
    assert_single_test_commit(
        before["head"], repo, {"payload": {"commit_sha": commit_sha}}
    )


def test_scope_contract_rejects_outside_path(tmp_path):
    repo = _git_repo(tmp_path)
    before = snapshot_git_state(repo)
    remote_refs = _git(repo, "ls-remote", "origin")
    (repo / "README.md").write_text("outside\n", encoding="utf-8")

    with pytest.raises(AssertionError, match="outside tests"):
        assert_shield_no_commit_scope(before, repo, remote_refs)


def test_single_test_commit_contract_rejects_extra_commit(tmp_path):
    repo = _git_repo(tmp_path)
    before = snapshot_git_state(repo)
    (repo / "tests" / "integration" / "test_first.py").write_text(
        "def test_first():\n    pass\n", encoding="utf-8"
    )
    _git(repo, "add", "tests")
    _git(repo, "commit", "-m", "first test commit")
    (repo / "tests" / "e2e" / "test_second.py").write_text(
        "def test_second():\n    pass\n", encoding="utf-8"
    )
    _git(repo, "add", "tests")
    _git(repo, "commit", "-m", "extra test commit")

    with pytest.raises(AssertionError, match="more than one new commit"):
        assert_single_test_commit(
            before["head"],
            repo,
            {"payload": {"commit_sha": _git(repo, "rev-parse", "HEAD")}},
        )


def test_baseline_remote_preserves_origin_when_disabled(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    monkeypatch.delenv("TRAC_LIVE_LOCAL_REMOTE", raising=False)
    expected = (
        _git(repo, "remote", "get-url", "origin"),
        _git(repo, "ls-remote", "origin"),
    )

    assert _setup_baseline_remote(repo) == expected
    assert _git(repo, "remote", "get-url", "origin") == expected[0]


def test_baseline_remote_uses_local_bare_origin_when_enabled(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    original_url = _git(repo, "remote", "get-url", "origin")
    monkeypatch.setenv("TRAC_LIVE_LOCAL_REMOTE", "1")

    remote_url, remote_refs = _setup_baseline_remote(repo)

    local_remote = tmp_path / "repo-artifacts" / "baseline-local-remote.git"
    assert remote_url == str(local_remote)
    assert remote_url != original_url
    assert local_remote.is_dir()
    assert (local_remote / "HEAD").is_file()
    assert remote_refs == ""
    assert _git(repo, "ls-remote", "origin") == ""


def test_baseline_remote_setup_is_idempotent(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    monkeypatch.setenv("TRAC_LIVE_LOCAL_REMOTE", "1")

    first = _setup_baseline_remote(repo)
    second = _setup_baseline_remote(repo)

    assert second == first
    assert _git(repo, "remote", "get-url", "origin") == first[0]
    assert _git(repo, "ls-remote", "origin") == first[1]


# -- runtime DB snapshot consistency (WAL/sidecar handling) ----------------
# SQLite WAL: committed txns may live in ``tracks.db-wal`` until a checkpoint.
# Raw-copying the trio is unsafe; the snapshot excludes all three and rebuilds
# a clean ``tracks.db`` in the target via the backup API.


def _seed_runtime_db(
    host: Path, events: list[dict], *, keep_wal: bool = False
) -> sqlite3.Connection | None:
    """Write events into ``host/.tracks/runtime/tracks.db`` in WAL mode.

    With *keep_wal*, an extra row (seq=9999) is written while a reader holds
    a read lock so the WAL/SHM trio stays on disk; the reader is returned
    and the caller MUST close it after the snapshot runs. Otherwise returns
    None."""
    runtime = host / ".tracks" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    blobs = runtime / "blobs"
    blobs.mkdir(exist_ok=True)
    db = runtime / "tracks.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE events ("
        "run_id TEXT, seq INTEGER, ts TEXT, version TEXT, type TEXT,"
        " schema_version INTEGER, command_id TEXT, task_id TEXT, payload TEXT)"
    )
    conn.executemany(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                ev.get("run_id", "R1"),
                ev["seq"],
                ev.get("ts", "2026-08-05T00:00:00Z"),
                ev.get("version", "v1"),
                ev["type"],
                1,
                ev.get("command_id"),
                None,
                json.dumps(ev["payload"]),
            )
            for ev in events
        ],
    )
    conn.commit()
    conn.close()
    if not keep_wal:
        return None
    # Force the WAL to retain un-checkpointed pages: a second connection
    # holding a read lock prevents the writer's commit from truncating the
    # WAL, so ``tracks.db-wal`` is present on disk when the snapshot runs.
    reader = sqlite3.connect(db)
    reader.execute("BEGIN").fetchone()
    reader.execute("SELECT * FROM events").fetchall()
    writer = sqlite3.connect(db)
    writer.execute(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("R1", 9999, "2026-08-05T00:00:01Z", "v1", "noop", 1, None, None, "{}"),
    )
    writer.commit()
    writer.close()
    wal_path = runtime / "tracks.db-wal"
    assert wal_path.is_file(), "test seed did not produce a WAL sidecar"
    return reader


def _runtime_files(host: Path) -> set[str]:
    runtime = host / ".tracks" / "runtime"
    return {p.name for p in runtime.iterdir()} if runtime.is_dir() else set()


def test_wal_backed_source_capture_restores_all_committed_events(tmp_path):
    """A snapshot taken while the source DB has an un-checkpointed WAL must
    copy every committed event into the target via the backup API."""
    src = tmp_path / "host"
    src.mkdir()
    reader = _seed_runtime_db(
        src,
        [
            {"seq": 1, "type": "run.started", "payload": {"v": "v0.5"}},
            {"seq": 2, "type": "stage.entered", "payload": {"stage": "M-REQ"}},
        ],
        keep_wal=True,
    )
    assert reader is not None
    # Sanity: the source has a real WAL+SHM trio (reader holds it open).
    assert "tracks.db-wal" in _runtime_files(src)
    assert "tracks.db-shm" in _runtime_files(src)

    target = tmp_path / "snapshot"
    target.mkdir()
    try:
        _helpers._copy_tree(src, target)
    finally:
        reader.close()

    target_files = _runtime_files(target)
    assert "tracks.db" in target_files
    assert "tracks.db-wal" not in target_files, (
        "snapshot leaked WAL sidecar into target"
    )
    assert "tracks.db-shm" not in target_files, (
        "snapshot leaked SHM sidecar into target"
    )
    # All committed events survive - including the WAL-resident one (seq=9999).
    conn = sqlite3.connect(target / ".tracks" / "runtime" / "tracks.db")
    seqs = [r[0] for r in conn.execute("SELECT seq FROM events ORDER BY seq")]
    conn.close()
    assert seqs == [1, 2, 9999], f"snapshot lost WAL-resident commits: {seqs}"


def test_snapshot_excludes_db_trio_even_when_sidecars_present(tmp_path):
    """The snapshot must exclude the db trio and rebuild only ``tracks.db``."""
    src = tmp_path / "host"
    src.mkdir()
    reader = _seed_runtime_db(
        src, [{"seq": 1, "type": "run.started", "payload": {}}], keep_wal=True
    )
    assert reader is not None
    # All three files are present (real WAL+SHM, not bogus bytes).
    runtime = src / ".tracks" / "runtime"
    captured = {p.name for p in runtime.iterdir()}
    assert {"tracks.db", "tracks.db-wal", "tracks.db-shm"} <= captured

    target = tmp_path / "snapshot"
    target.mkdir()
    try:
        _helpers._copy_tree(src, target)
    finally:
        reader.close()

    target_files = _runtime_files(target)
    assert target_files == {"tracks.db", "blobs"}, (
        f"target runtime must contain only tracks.db + blobs, got {target_files}"
    )


def test_restore_removes_destination_stale_sidecars(tmp_path):
    """Stale ``-wal``/``-shm`` in the destination are wiped before restore."""
    live_root = tmp_path / "live"
    live_root.mkdir()
    runtime = live_root / ".tracks" / "runtime"
    runtime.mkdir(parents=True)
    # Stale sidecars from a previous run.
    (runtime / "tracks.db").write_bytes(b"stale main")
    (runtime / "tracks.db-wal").write_bytes(b"stale wal")
    (runtime / "tracks.db-shm").write_bytes(b"stale shm")

    baseline = tmp_path / "baseline"
    baseline.mkdir()
    _seed_runtime_db(
        baseline,
        [{"seq": 1, "type": "run.started", "payload": {"ok": True}}],
    )

    _helpers._restore_baseline(baseline, live_root)

    restored = _runtime_files(live_root)
    assert "tracks.db" in restored
    assert "tracks.db-wal" not in restored
    assert "tracks.db-shm" not in restored
    conn = sqlite3.connect(live_root / ".tracks" / "runtime" / "tracks.db")
    rows = conn.execute("SELECT seq, type FROM events ORDER BY seq").fetchall()
    conn.close()
    assert rows == [(1, "run.started")]


def test_restore_into_host_with_stale_sidecars_does_not_corrupt(tmp_path):
    """A restored DB opens cleanly even when the destination had stale
    sidecars before restore."""
    live_root = tmp_path / "live"
    live_root.mkdir()
    runtime = live_root / ".tracks" / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "tracks.db").write_bytes(b"junk")
    (runtime / "tracks.db-wal").write_bytes(b"junk-wal")
    (runtime / "tracks.db-shm").write_bytes(b"junk-shm")

    baseline = tmp_path / "baseline"
    baseline.mkdir()
    _seed_runtime_db(
        baseline,
        [{"seq": i, "type": f"e{i}", "payload": {}} for i in range(1, 6)],
    )

    _helpers._restore_baseline(baseline, live_root)

    conn = sqlite3.connect(live_root / ".tracks" / "runtime" / "tracks.db")
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()
    assert integrity == "ok"
    assert count == 5


def test_restore_wipes_host_with_symlinked_venv_and_normal_entries(tmp_path):
    """Symlinked entries (file or dir targets) are unlinked, not rmtree'd;
    real dirs still go through rmtree. Host .venv is a symlink to the runner
    venv, which ``rmtree`` refuses."""
    live_root = tmp_path / "live"
    live_root.mkdir()
    runner_venv = tmp_path / "runner-venv"
    runner_venv.mkdir()
    (runner_venv / "pyvenv.cfg").write_text("home=/usr/bin\n", encoding="utf-8")
    linked_file_target = tmp_path / "linked-file-target"
    linked_file_target.write_text("elsewhere\n", encoding="utf-8")
    (live_root / ".venv").symlink_to(runner_venv)
    (live_root / "linked-file").symlink_to(linked_file_target)
    (live_root / "work").mkdir()
    (live_root / "work" / "f.txt").write_text("x\n", encoding="utf-8")
    (live_root / "plain.txt").write_text("y\n", encoding="utf-8")
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    _seed_runtime_db(baseline, [{"seq": 1, "type": "run.started", "payload": {}}])
    _helpers._restore_baseline(baseline, live_root)
    assert not (live_root / ".venv").is_symlink()
    assert not (live_root / "linked-file").is_symlink()
    assert (runner_venv / "pyvenv.cfg").read_text(encoding="utf-8") == "home=/usr/bin\n"
    assert linked_file_target.read_text(encoding="utf-8") == "elsewhere\n"
    assert not (live_root / "work").exists()
    assert not (live_root / "plain.txt").exists()
    conn = sqlite3.connect(live_root / ".tracks" / "runtime" / "tracks.db")
    rows = conn.execute("SELECT seq, type FROM events ORDER BY seq").fetchall()
    conn.close()
    assert rows == [(1, "run.started")]


# -- LiveTracDriver.events() transient-error retries -----------------------
# ``events()`` may race the Runtime writer's shutdown and see a transient
# ``sqlite3.DatabaseError``/``OperationalError``; it must retry a bounded
# number of times and only fail with diagnostics on persistent corruption.


def _mechanic_driver_for_events(tmp_path):
    """A ``LiveTracDriver`` wired to ``tmp_path`` with no provider/agents,
    for exercising ``events()`` in isolation."""
    from tests.e2e_live.test_live_agent import _mechanic_driver

    return _mechanic_driver(tmp_path)


def _seed_simple_events(host: Path) -> None:
    runtime = host / ".tracks" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(runtime / "tracks.db")
    conn.execute(
        "CREATE TABLE events (seq INTEGER, type TEXT, command_id TEXT, payload TEXT)"
    )
    conn.execute(
        "INSERT INTO events VALUES (?, ?, ?, ?)",
        (1, "run.started", None, json.dumps({"v": "live"})),
    )
    conn.commit()
    conn.close()


def test_events_transient_database_error_retries_then_succeeds(monkeypatch, tmp_path):
    """A transient ``DatabaseError`` (the run050 shutdown race) is retried up
    to ``EVENT_READ_RETRIES`` times; once the read succeeds, events return."""
    driver = _mechanic_driver_for_events(tmp_path)
    _seed_simple_events(tmp_path)

    real_connect = sqlite3.connect
    calls = {"n": 0}

    def flaky_connect(database, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise sqlite3.DatabaseError("database disk image is malformed")
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr("tests.e2e_live.harness.sqlite3.connect", flaky_connect)
    # Shorten the retry delay so the test is fast.
    monkeypatch.setattr(driver, "EVENT_READ_RETRY_DELAY", 0.001)

    events = driver.events()

    assert len(events) == 1
    assert events[0]["type"] == "run.started"
    assert calls["n"] == 3, f"expected 2 flaky + 1 success, got {calls['n']}"


def test_events_transient_locked_error_retries_then_succeeds(monkeypatch, tmp_path):
    """``sqlite3.OperationalError("database is locked")`` from a racing writer
    is also retried and succeeds once the writer releases."""
    driver = _mechanic_driver_for_events(tmp_path)
    _seed_simple_events(tmp_path)

    real_connect = sqlite3.connect
    calls = {"n": 0}

    def flaky_connect(database, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr("tests.e2e_live.harness.sqlite3.connect", flaky_connect)
    monkeypatch.setattr(driver, "EVENT_READ_RETRY_DELAY", 0.001)

    events = driver.events()

    assert len(events) == 1
    assert calls["n"] == 2


def test_events_persistent_database_error_fails_with_diagnostics(monkeypatch, tmp_path):
    """A persistent ``DatabaseError`` fails after the bounded retries with
    diagnostics, including a ``PRAGMA integrity_check`` result."""
    driver = _mechanic_driver_for_events(tmp_path)
    _seed_simple_events(tmp_path)

    def always_fails(database, *args, **kwargs):
        raise sqlite3.DatabaseError("database disk image is malformed")

    # The integrity_check path opens its own connection; let it succeed so we
    # can assert the diagnostic surfaces the check result rather than masking.
    real_connect = sqlite3.connect
    integrity_calls = {"n": 0}

    def connect_for_read_or_integrity(database, *args, **kwargs):
        # Distinguish the events() read (first attempts) from the final
        # integrity_check connection by counting: events() opens 1 connection
        # per attempt; the integrity_check is the very last open.
        integrity_calls["n"] += 1
        if integrity_calls["n"] <= driver.EVENT_READ_RETRIES:
            raise sqlite3.DatabaseError("database disk image is malformed")
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr("tests.e2e_live.harness.sqlite3.connect", connect_for_read_or_integrity)
    monkeypatch.setattr(driver, "EVENT_READ_RETRY_DELAY", 0.001)

    with pytest.raises(AssertionError) as excinfo:
        driver.events()

    message = str(excinfo.value)
    assert "failed after 5 retries" in message
    assert "database disk image is malformed" in message
    assert "integrity_check=" in message


def test_events_returns_empty_when_db_absent(tmp_path):
    """No runtime DB on disk (e.g. before ``trac init``) returns an empty
    list without raising or retrying."""
    driver = _mechanic_driver_for_events(tmp_path)
    assert driver.events() == []


# -- prepare_host_venv offline-first symlink provisioning -------------------
# The harness provisions the host ``.venv`` without pip/network by symlinking
# to the current virtualenv, mirroring ``tests/conftest.py``'s ``host_repo``.


def _mechanic_install(live_root: Path) -> LiveInstall:
    """Synthetic ``LiveInstall`` mirroring ``test_live_agent._mechanic_driver``
    so the R0801 duplicate-code gate stays green across both modules."""
    from tests.e2e_live.test_live_agent import _mechanic_driver

    return _mechanic_driver(live_root).install


def test_prepare_host_venv_symlinks_current_prefix_and_logs_method(tmp_path):
    host = tmp_path / "host"
    host.mkdir()
    install = _mechanic_install(tmp_path)
    install.install_log.write_text("", encoding="utf-8")

    result = prepare_host_venv(host, install)

    assert result == host / ".venv"
    assert result.is_symlink()
    assert result.resolve() == Path(sys.prefix).resolve()
    log_text = install.install_log.read_text(encoding="utf-8")
    assert f"host_venv=symlink:{Path(sys.prefix).resolve()}" in log_text


def test_prepare_host_venv_is_idempotent_for_symlink(tmp_path):
    host = tmp_path / "host"
    host.mkdir()
    install = _mechanic_install(tmp_path)
    install.install_log.write_text("", encoding="utf-8")

    first = prepare_host_venv(host, install)
    log_after_first = install.install_log.read_text(encoding="utf-8")
    second = prepare_host_venv(host, install)
    log_after_second = install.install_log.read_text(encoding="utf-8")

    assert second == first
    assert log_after_second == log_after_first, (
        "idempotent re-entry must not append a second provisioning line"
    )


def test_prepare_host_venv_is_idempotent_for_real_directory(tmp_path):
    host = tmp_path / "host"
    host.mkdir()
    (host / ".venv").mkdir()
    install = _mechanic_install(tmp_path)
    install.install_log.write_text("", encoding="utf-8")

    result = prepare_host_venv(host, install)

    assert result == host / ".venv"
    assert not result.is_symlink()
    assert install.install_log.read_text(encoding="utf-8") == "", (
        "pre-existing real .venv dir must not trigger provisioning log"
    )


def test_prepare_host_venv_uses_no_network(tmp_path, monkeypatch):
    host = tmp_path / "host"
    host.mkdir()
    install = _mechanic_install(tmp_path)
    install.install_log.write_text("", encoding="utf-8")

    def fail_subprocess(*args, **kwargs):
        raise AssertionError(
            f"prepare_host_venv must not spawn subprocesses; got {args!r}"
        )

    monkeypatch.setattr("tests.e2e_live.harness.subprocess.run", fail_subprocess)
    monkeypatch.setattr("tests.e2e_live.harness.subprocess.Popen", fail_subprocess)

    result = prepare_host_venv(host, install)

    assert result.is_symlink()


def test_prepare_host_venv_fails_clearly_when_pytest_unimportable(tmp_path, monkeypatch):
    host = tmp_path / "host"
    host.mkdir()
    install = _mechanic_install(tmp_path)
    install.install_log.write_text("", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "pytest", None)

    with pytest.raises(AssertionError, match="no importable pytest"):
        prepare_host_venv(host, install)
    assert not (host / ".venv").exists()
