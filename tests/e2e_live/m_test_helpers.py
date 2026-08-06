"""M-TEST milestone helpers extracted from test_full_journey to keep that
module under the pylint C0302 line limit (NFR-06b: 1050 lines).

Three groups:
  * **Baseline snapshot** -- capture/restore/find at named checkpoints
    (M-REQ-APPROVAL legacy + DESIGN_EXIT_OBSERVED for v0.4 M-TEST resume).
  * **M-TEST phase logic** -- _phase_design_to_m_test, _phase_m_test_to_boundary,
    the bounded Shield/Prism revise loop, design review loop.
  * **P0 assertion helpers** -- boundary adjacency, Shield scope/no-commit,
    criteria-pack anti-self-report triple, test markers, event filtering.

All helpers are deterministic (no live provider required); they operate on
event lists and filesystem state captured by the caller."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.e2e_live.harness import live_root as configured_live_root

# -- event/db helpers (shared with test_full_journey) ----------------------


def _git(repo: Path, *args: str, check: bool = True) -> str:
    process = subprocess.run(["git", *args], cwd=repo, check=check, capture_output=True, text=True)
    return process.stdout.strip()


def _events(repo: Path, run_id: str) -> list[dict]:
    import sqlite3

    database = repo / ".tracks" / "runtime" / "tracks.db"
    blob_dir = repo / ".tracks" / "runtime" / "blobs"
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT seq, type, command_id, payload FROM events WHERE run_id = ? ORDER BY seq",
            (run_id,),
        ).fetchall()
    events: list[dict] = []
    for seq, event_type, command_id, payload in rows:
        data = json.loads(payload)
        if isinstance(data, dict) and set(data.keys()) == {"$ref"}:
            ref_path = blob_dir / data["$ref"]
            if ref_path.exists():
                data = json.loads(ref_path.read_text(encoding="utf-8"))
        events.append(
            {"seq": seq, "type": event_type, "command_id": command_id, "payload": data}
        )
    return events


def _dispatches(events: list[dict], role: str, substate: str, doc: str | None) -> list[dict]:
    return [
        event
        for event in events
        if event["type"] == "command.issued"
        and event["payload"].get("command", {}).get("kind") == "dispatch_agent"
        and event["payload"].get("command", {}).get("params", {}).get("role") == role
        and event["payload"].get("command", {}).get("params", {}).get("substate") == substate
        and event["payload"].get("command", {}).get("params", {}).get("doc") == doc
    ]


# -- P0: strict boundary adjacency -----------------------------------------


def assert_strict_boundary_adjacency(final_events: list[dict]) -> None:
    """P0: assert event-sequence adjacency at both M-DESIGN->M-TEST and
    M-TEST->boundary transitions.

    1. ``stage.exited(M-DESIGN)`` is immediately followed by
       ``stage.entered(M-TEST)`` (next event by seq).
    2. No ``run.completed`` event exists between M-DESIGN exit and M-TEST
       entry (i.e., M-DESIGN does not terminate the run).
    3. ``stage.exited(M-TEST)`` is immediately followed by
       ``run.completed(boundary)`` (next event by seq).

    The executor emits these pairs consecutively in
    ``_do_write_frontmatter`` / ``_do_commit_tests``, so literal next-event
    adjacency is the correct assertion. If the store ever inserts
    bookkeeping events between them, this will fail loudly rather than
    silently weakening."""
    _assert_design_exit_adjacency(final_events)

    # M-TEST -> boundary adjacency.
    m_test_exit = _find_event(final_events, "stage.exited", stage="M-TEST")
    assert m_test_exit, "missing stage.exited(M-TEST)"
    run_completed = _find_event(final_events, "run.completed")
    assert run_completed, "missing run.completed"
    assert run_completed["payload"]["terminal_state"] == "boundary", (
        f"run.completed terminal_state != boundary: {run_completed['payload']}"
    )
    assert run_completed["seq"] == m_test_exit["seq"] + 1, (
        f"stage.exited(M-TEST) at seq={m_test_exit['seq']} is not "
        f"immediately followed by run.completed at seq={run_completed['seq']}; "
        f"boundary adjacency violated"
    )


def _find_event(events: list[dict], event_type: str, **payload_filter) -> dict | None:
    """Find the first event of ``event_type`` whose payload matches all
    ``payload_filter`` key=value pairs. Returns None if not found."""
    for event in events:
        if event["type"] != event_type:
            continue
        if all(event["payload"].get(k) == v for k, v in payload_filter.items()):
            return event
    return None


def _assert_design_exit_adjacency(events: list[dict]) -> None:
    """Require strict ``stage.exited(M-DESIGN)`` -> ``stage.entered(M-TEST)``."""
    design_exit = _find_event(events, "stage.exited", stage="M-DESIGN")
    assert design_exit, "missing stage.exited(M-DESIGN)"
    m_test_entered = _find_event(events, "stage.entered", stage="M-TEST")
    assert m_test_entered, "missing stage.entered(M-TEST)"
    assert m_test_entered["seq"] == design_exit["seq"] + 1, (
        f"stage.exited(M-DESIGN) at seq={design_exit['seq']} is not "
        f"immediately followed by stage.entered(M-TEST) at "
        f"seq={m_test_entered['seq']}; boundary adjacency violated"
    )
    completed_before_exit = [
        event
        for event in events
        if event["type"] == "run.completed" and event["seq"] <= design_exit["seq"]
    ]
    assert not completed_before_exit, (
        f"run.completed before M-DESIGN exit at seq={design_exit['seq']}: "
        f"{[event['seq'] for event in completed_before_exit]}"
    )


# -- P0: Shield assignment contract ----------------------------------------


def assert_shield_assignment_contract(events: list[dict], version: str) -> None:
    """P0: inspect the real ``command.issued(dispatch_agent)`` payload for
    Shield WRITE and assert:
      * role=shield, substate=WRITE, stage=M-TEST
      * assignment.kind == WRITE
      * assignment.docs includes test-plan.md, interfaces.md, acceptance.md
        (the context docs the Shield needs to derive AC layer attribution)
      * assignment.skills includes tracks-discuz
      * assignment.test_tasks is a non-empty list of {ac_id, layers, if_ids}
        dicts, parsed by the Runtime from test-plan §8 (D-28). Every ac_id is
        unique, every layer is integration or e2e, every if_ids list is
        non-empty (IF green-condition ownership).
    """
    shield_dispatches = _dispatches(events, "shield", "WRITE", None)
    assert shield_dispatches, "missing Shield WRITE dispatch"
    dispatch = shield_dispatches[0]
    params = dispatch["payload"]["command"]["params"]
    assert params["role"] == "shield"
    assert params["substate"] == "WRITE"
    assert params["stage"] == "M-TEST"
    assignment = params.get("assignment", {})
    assert assignment.get("kind") == "WRITE", (
        f"Shield assignment.kind != WRITE: {assignment.get('kind')}"
    )
    docs = set(assignment.get("docs", []))
    expected_docs = {"test-plan.md", "interfaces.md", "acceptance.md"}
    assert docs >= expected_docs, (
        f"Shield assignment.docs missing required context docs: "
        f"have {docs}, expected >= {expected_docs}"
    )
    skills = assignment.get("skills", [])
    assert "tracks-discuz" in skills, (
        f"Shield assignment.skills missing tracks-discuz: {skills}"
    )
    test_tasks = assignment.get("test_tasks")
    assert isinstance(test_tasks, list) and test_tasks, (
        f"Shield assignment.test_tasks missing or empty: {test_tasks}"
    )
    seen_acs: set[str] = set()
    for task in test_tasks:
        assert isinstance(task, dict), f"test_tasks entry is not an object: {task}"
        ac_id = task.get("ac_id")
        assert isinstance(ac_id, str) and _TASK_AC_ID.fullmatch(ac_id), (
            f"test_tasks invalid ac_id: {ac_id}"
        )
        assert ac_id not in seen_acs, f"test_tasks duplicate ac_id: {ac_id}"
        seen_acs.add(ac_id)
        layers = task.get("layers", [])
        assert isinstance(layers, list) and layers and all(
            lay in ("integration", "e2e") for lay in layers
        ) and len(set(layers)) == len(layers), (
            f"test_tasks {ac_id} invalid layers: {layers}"
        )
        if_ids = task.get("if_ids", [])
        assert isinstance(if_ids, list) and if_ids and all(
            isinstance(if_id, str) and _TASK_IF_ID.fullmatch(if_id)
            for if_id in if_ids
        ) and len(set(if_ids)) == len(if_ids), (
            f"test_tasks {ac_id} invalid if_ids: {if_ids}"
        )


# -- P0: Shield scope + no commit ------------------------------------------


_ALLOWED_TEST_DIRS = ("integration", "e2e", "assets", "counterexamples")
# Runtime-owned locations are not Shield writes (document locks, runtime DB
# sidecars, provider config) and are skipped by the scope check below.
_RUNTIME_OWNED_TOP_DIRS = frozenset({".tracks", ".opencode", ".venv"})
_TASK_AC_ID = re.compile(r"^AC-(?:N?FR)\d{4}-\d{2}$")
_TASK_IF_ID = re.compile(r"^IF-[A-Z]+-\d{3}$")


def snapshot_git_state(repo: Path) -> dict:
    """Capture git HEAD + status before Shield call so we can assert Shield
    didn't commit/push and only wrote within allowed M-TEST dirs."""
    return {
        "head": _git(repo, "rev-parse", "HEAD"),
        "status_porcelain": _git(repo, "status", "--porcelain"),
        "tracked_files": set(
            _git(repo, "ls-files").splitlines()
        ),
    }


def _untracked_leaves(repo: Path, prefix: str) -> list[str]:
    """Untracked leaf files under ``prefix`` (a porcelain-collapsed ``?? dir/``
    entry), mirroring ``Auditor.untracked_under`` so the scope check judges
    files, not collapsed directories (the run010 harness twin of run001)."""
    out = _git(repo, "ls-files", "--others", "--exclude-standard", "--", prefix)
    return [line.strip().strip('"') for line in out.splitlines() if line.strip()]


def assert_shield_no_commit_scope(
    before: dict, repo: Path, remote_refs_before: str
) -> None:
    """P0: after Shield/collection (before Prism/Runtime commit):
      * HEAD unchanged (Shield no commit)
      * remote refs unchanged (Shield no push)
      * every new/modified path is within tests/integration, tests/e2e,
        tests/assets, tests/counterexamples
      * no product/stub/design-doc write

    Runtime-owned locations (``.tracks/``, ``.opencode/``) are not Shield
    writes (document locks, runtime DB sidecars, provider config) and are
    skipped before the tests-only scope assertion.
    """
    after_head = _git(repo, "rev-parse", "HEAD")
    assert after_head == before["head"], (
        f"Shield committed: HEAD changed {before['head'][:8]} -> {after_head[:8]}"
    )
    remote_refs_after = _git(repo, "ls-remote", "origin")
    assert remote_refs_after == remote_refs_before, (
        "Shield pushed: remote refs changed"
    )
    # Every new/modified path must be within tests/<allowed>/; runtime-owned
    # prefixes (.tracks/, .opencode/) are not Shield writes and are skipped.
    status = _git(repo, "status", "--porcelain")
    lines = [ln for ln in status.splitlines() if ln.strip()]
    if lines:
        for line in lines:
            # porcelain format: XY <path> (2 status chars + 1 space + path).
            # _git returns stdout.strip() which eats the first line's leading
            # space (X=" "), so split on first space(s) to get the path.
            path = line[2:].lstrip().strip('"')
            # Porcelain collapses a fully-untracked directory into a single
            # ``?? dir/`` entry (trailing "/"); expand to its untracked leaf
            # files so the scope check judges actual files, not the collapsed
            # dir (mirrors Auditor.file_level; the run001 product bug's twin).
            # A dir that expands to nothing is kept as-is so the check still
            # applies.
            if path.endswith("/"):
                paths = _untracked_leaves(repo, path) or [path]
            else:
                paths = [path]
            for p in paths:
                top = p.split("/")[0] if "/" in p else p
                if top in _RUNTIME_OWNED_TOP_DIRS:
                    continue
                assert top == "tests", (
                    f"Shield wrote outside tests/: {p}"
                )
                if "/" in p:
                    second = p.split("/")[1]
                    assert second in _ALLOWED_TEST_DIRS, (
                        f"Shield wrote to tests/{second}/ (not in "
                        f"{_ALLOWED_TEST_DIRS}): {p}"
                    )


def assert_single_test_commit(
    pre_shield_head: str, repo: Path, test_committed_event: dict
) -> None:
    """P0: after final Runtime exit, exactly one new git commit relative to
    pre-Shield HEAD, and it corresponds to ``test.committed`` commit_sha."""
    post_head = _git(repo, "rev-parse", "HEAD")
    assert post_head != pre_shield_head, (
        "no new commit after M-TEST exit (expected exactly one)"
    )
    commit_sha = test_committed_event["payload"]["commit_sha"]
    assert commit_sha == post_head, (
        f"test.committed commit_sha {commit_sha[:8]} != HEAD {post_head[:8]}"
    )
    # Exactly one new commit (HEAD's parent must be pre_shield_head).
    parent = _git(repo, "rev-parse", "HEAD^")
    assert parent == pre_shield_head, (
        f"more than one new commit: parent of HEAD is {parent[:8]}, "
        f"expected pre-Shield HEAD {pre_shield_head[:8]}"
    )


# -- P0: criteria-pack anti-self-report triple -----------------------------


_CRITERIA_PACK = {"name": "test-asset-criteria", "version": "0.1"}


def assert_criteria_pack_triple(events: list[dict]) -> None:
    """P0: criteria-pack anti-self-report triple (D-29):
      1. Prism M-TEST dispatch assignment explicitly names the criteria pack
      2. Prism outcome/verdict echoes the same identity
      3. No Runtime criteria_pack_mismatch verdict.failed; pass verdict present
    All three assertions are visible and independent."""
    m_test_entered_seq = next(
        (e["seq"] for e in events
         if e["type"] == "stage.entered" and e["payload"]["stage"] == "M-TEST"),
        None,
    )
    assert m_test_entered_seq is not None, "missing stage.entered(M-TEST)"

    # 1. Prism M-TEST dispatch assignment names the criteria pack.
    prism_dispatches = [
        e for e in events
        if e["type"] == "command.issued"
        and e["payload"].get("command", {}).get("kind") == "dispatch_agent"
        and e["payload"]["command"]["params"].get("role") == "prism"
        and e["payload"]["command"]["params"].get("substate") == "PRISM_REVIEW"
        and e["seq"] > m_test_entered_seq
    ]
    assert prism_dispatches, "missing Prism M-TEST PRISM_REVIEW dispatch"
    prism_assignment = prism_dispatches[-1]["payload"]["command"]["params"].get("assignment", {})
    assigned_pack = prism_assignment.get("criteria_pack")
    assert assigned_pack == _CRITERIA_PACK, (
        f"Prism M-TEST dispatch assignment criteria_pack != {_CRITERIA_PACK}: "
        f"got {assigned_pack}"
    )
    # D-29: the criteria pack skill is declared for materialization (not just
    # identity metadata); assignment.skills names test-asset-criteria so the
    # backend materializes it for Prism to consume.
    assigned_skills = prism_assignment.get("skills", [])
    assert "test-asset-criteria" in assigned_skills, (
        f"Prism M-TEST dispatch assignment.skills missing test-asset-criteria: "
        f"got {assigned_skills}"
    )

    # 2. Prism outcome/verdict echoes the same identity.
    m_test_prism_verdicts = [
        e for e in events
        if e["type"] == "prism.verdict" and e["seq"] > m_test_entered_seq
    ]
    assert m_test_prism_verdicts, "missing M-TEST prism.verdict"
    last_verdict = m_test_prism_verdicts[-1]
    assert last_verdict["payload"]["verdict"] == "pass", (
        f"M-TEST Prism verdict != pass: {last_verdict['payload']['verdict']}"
    )
    echoed_pack = last_verdict["payload"].get("criteria_pack")
    assert echoed_pack == _CRITERIA_PACK, (
        f"Prism verdict criteria_pack != {_CRITERIA_PACK}: got {echoed_pack}"
    )

    # 3. No criteria_pack_mismatch verdict.failed; pass verdict present.
    mismatch = [
        e for e in events
        if e["type"] == "verdict.failed"
        and e["payload"].get("check") == "criteria_pack_mismatch"
        and e["seq"] > m_test_entered_seq
    ]
    assert not mismatch, (
        f"criteria_pack_mismatch should not happen: {mismatch}"
    )


# -- P0: test markers ------------------------------------------------------


def assert_test_markers(tracks_tests_dir: Path, version: str) -> None:
    """P0: every test file under tests/integration/ and tests/e2e/ carries
    at least one R-1 long-format marker (``AC-FRXXXX-YY@<version>
    TRACKS-TRACE``). Shield must not write tests outside these dirs."""
    marker_re = re.compile(
        r"^\s*(#|//)\s*AC-(?:N?FR)\d{4}-\d{2}@(\S+)\s+TRACKS-TRACE\b",
        re.MULTILINE,
    )
    found = False
    for subdir in ("integration", "e2e"):
        layer_dir = tracks_tests_dir / subdir
        if not layer_dir.is_dir():
            continue
        for test_file in layer_dir.glob("test_*.py"):
            content = test_file.read_text(encoding="utf-8")
            matches = marker_re.findall(content)
            assert matches, f"test file {test_file} has no R-1 TRACKS-TRACE marker"
            for _line, ver in matches:
                assert ver == version, (
                    f"marker version {ver} != expected {version} in {test_file}"
                )
            found = True
    assert found, "no test files with markers found under tests/integration|e2e/"


# -- P0: M-TEST boundary full assertion ------------------------------------


def assert_m_test_boundary(final_events: list[dict]) -> None:
    """P0 assertions for the M-TEST -> M-IMPL boundary:
    - strict boundary adjacency (stage.exited(M-TEST) + run.completed)
    - Runtime legit Red: red.validated(valid)
    - trace gate: verdict.passed(trace) present
    - controlled exit: one test.committed, stage.exited(M-TEST),
      run.completed(boundary)
    - no human gate in M-TEST (BS-05)
    """
    assert_strict_boundary_adjacency(final_events)
    test_committed = [e for e in final_events if e["type"] == "test.committed"]
    assert len(test_committed) == 1, (
        f"expected exactly one test.committed, got {len(test_committed)}"
    )
    assert test_committed[0]["payload"]["commit_sha"], "test.committed missing commit_sha"
    # Runtime legit Red.
    red_validated = [
        e
        for e in final_events
        if e["type"] == "red.validated" and e["payload"].get("status") == "valid"
    ]
    assert red_validated, "missing red.validated(valid)"
    # Trace gate: verdict.passed(trace) present.
    trace_passed = [
        e
        for e in final_events
        if e["type"] == "verdict.passed" and e["payload"].get("check") == "trace"
    ]
    assert trace_passed, "missing verdict.passed(trace) at M-TEST EXIT"
    # Criteria-pack anti-self-report triple.
    assert_criteria_pack_triple(final_events)
    # No human gate in M-TEST (BS-05).
    m_test_entered = next(
        e["seq"]
        for e in final_events
        if e["type"] == "stage.entered" and e["payload"]["stage"] == "M-TEST"
    )
    human_in_m_test = [
        e
        for e in final_events
        if e["type"].startswith("human.") and e["seq"] > m_test_entered
    ]
    assert not human_in_m_test, (
        f"M-TEST must carry no human gate (BS-05): {human_in_m_test}"
    )


# -- baseline snapshot ------------------------------------------------------


def _tracks_short_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _baselines_root() -> Path:
    return configured_live_root() / "baselines"


def _baseline_dir(version: str, sha: str, checkpoint: str = "M-REQ-APPROVAL") -> Path:
    """Directory name for a baseline. M-REQ-APPROVAL keeps the legacy
    ``baseline-{sha}-{version}`` form; later checkpoints get a suffix so
    both coexist under the same root without collision."""
    if checkpoint == "M-REQ-APPROVAL":
        return _baselines_root() / f"baseline-{sha}-{version}"
    slug = checkpoint.lower().replace("_", "-")
    return _baselines_root() / f"baseline-{slug}-{sha}-{version}"


_SNAPSHOT_EXCLUDE_NAMES = {"node_modules"}

# Snapshot metadata written into the baseline dir by ``_capture_baseline``.
# It describes the snapshot itself, not host state, so it must never be
# restored into the live host (otherwise ``git status`` flags it as an
# untracked top-level file and trips the Shield scope assertion that every
# new path lives under ``tests/``). Capture still writes it into the baseline
# dir; only the restore copy skips it.
_BASELINE_MANIFEST_NAME = ".tracks-baseline-manifest.json"

# The runtime event store uses SQLite WAL mode: at any moment the live DB may
# have sidecar files ``tracks.db-wal`` and ``tracks.db-shm`` carrying
# un-checkpointed committed transactions. Raw-copying these sidecars with the
# main DB risks an inconsistent snapshot (the WAL/SHM are only valid in the
# presence of the original page file plus an active writer). Instead the
# snapshot excludes all three and rebuilds a clean ``tracks.db`` in the
# target via the Python ``sqlite3`` backup API, which walks the live source
# under a read connection and produces a single self-contained file with no
# sidecars and all committed transactions folded in.
_RUNTIME_DB_NAME = "tracks.db"
_RUNTIME_DB_SIDECARS = (_RUNTIME_DB_NAME + "-wal", _RUNTIME_DB_NAME + "-shm")
_SNAPSHOT_EXCLUDE_DB_NAMES = {_RUNTIME_DB_NAME, *_RUNTIME_DB_SIDECARS}


def _snapshot_ignore(directory: str, names: list[str]) -> set[str]:
    """``shutil.copytree`` ignore callback: skip the runtime DB trio
    (``tracks.db`` + ``-wal`` + ``-shm``) only inside ``.tracks/runtime/``.

    The runtime DB is rebuilt in the target via ``_snapshot_runtime_db`` so
    the raw files must be excluded from the tree copy. The guard is
    positional so it applies only at the runtime dir, never elsewhere."""
    dir_path = Path(directory)
    if dir_path.name == "runtime" and dir_path.parent.name == ".tracks":
        return {name for name in names if name in _SNAPSHOT_EXCLUDE_DB_NAMES}
    return set()


def _copy_tree(
    src: Path, dst: Path, *, ignore_names: set[str] | None = None
) -> None:
    """Copy src into dst, excluding opencode's node_modules (recreated on
    demand), the baseline manifest itself, and the runtime SQLite DB trio
    (``tracks.db`` + ``-wal`` + ``-shm``). The runtime DB is rebuilt in the
    target via ``_snapshot_runtime_db`` so the snapshot is internally
    consistent and sidecar-free.

    ``ignore_names`` skips matching top-level entries in ``src`` (e.g. the
    baseline manifest marker when restoring into the live host); it does not
    recurse into subdirectories."""
    ignored = ignore_names or set()
    for entry in src.iterdir():
        if entry.name in _SNAPSHOT_EXCLUDE_NAMES and entry.is_dir():
            continue
        if entry.is_dir() and entry.name == "node_modules":
            continue
        if entry.name in ignored:
            continue
        dest = dst / entry.name
        if entry.is_dir():
            shutil.copytree(entry, dest, dirs_exist_ok=True, ignore=_snapshot_ignore)
        else:
            shutil.copy2(entry, dest)
    _snapshot_runtime_db(src, dst)


def _snapshot_runtime_db(src_host: Path, dst_host: Path) -> None:
    """Copy the live runtime ``tracks.db`` into the target host using the
    Python ``sqlite3`` backup API, so the WAL/SHM sidecars are not raw-copied
    and the target DB contains every committed event in a single file.

    Idempotent and silent: skips when the source runtime dir or DB is absent
    (e.g. capture before ``trac init``) and never emits sidecars into the
    target. The backup API reads the live source under a read-only URI
    connection while the source remains readable by any active writer."""
    src_runtime = src_host / ".tracks" / "runtime"
    src_db = src_runtime / _RUNTIME_DB_NAME
    if not src_db.is_file():
        return
    dst_runtime = dst_host / ".tracks" / "runtime"
    dst_runtime.mkdir(parents=True, exist_ok=True)
    dst_db = dst_runtime / _RUNTIME_DB_NAME
    # Defensive: never inherit a stale sidecar in the target (restore path
    # may have pre-existing sidecars from a previous run).
    for sidecar in _RUNTIME_DB_SIDECARS:
        with suppress(FileNotFoundError):
            (dst_runtime / sidecar).unlink()
    source = sqlite3.connect(f"file:{src_db.as_posix()}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(str(dst_db))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    # The backup API never creates sidecars in the destination, but assert
    # the invariant explicitly so a future regression fails loudly here
    # instead of producing a corrupt snapshot.
    for sidecar in _RUNTIME_DB_SIDECARS:
        assert not (dst_runtime / sidecar).exists(), (
            f"snapshot leaked sidecar into target runtime: {sidecar}"
        )


def _restore_baseline(baseline_dir: Path, live_root: Path) -> None:
    """Restore a baseline snapshot into the test's live host directory.

    Path-stability: the runtime resolves ``.tracks/`` from the runtime cwd
    (``paths.tracks_home``), and ``artifact_ref`` fields in the DB carry
    absolute paths from the original host but are used only as evidence
    strings (``last_failure`` re-dispatch context), never for file access.
    So restoring to a different path is safe - the runtime re-derives every
    path from the new cwd.

    Stale sidecar removal: the destination ``tracks.db-wal``/``-shm`` from a
    prior run are removed (via the full wipe of ``live_root`` below, plus a
    defensive pass inside ``_snapshot_runtime_db``) before the baseline DB
    is rebuilt via the backup API, so a stale WAL/SHM pair can never attach
    to the freshly-written main file."""
    for entry in list(live_root.iterdir()):
        if entry.is_symlink():
            entry.unlink()
        elif entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    # The baseline manifest is snapshot metadata, not host state; restore
    # must not drop it into the live host or Shield's scope assertion trips
    # on the untracked top-level marker.
    _copy_tree(
        baseline_dir, live_root, ignore_names={_BASELINE_MANIFEST_NAME}
    )


def _setup_baseline_remote(live_root: Path) -> tuple[str, str]:
    """Capture the restored origin URL and refs, optionally using a local bare remote.

    ``TRAC_LIVE_LOCAL_REMOTE=1`` is an explicit offline-test switch. The bare
    repository lives beside the host in the run's artifact directory, so it
    cannot appear in the restored worktree or Shield's scope check. All git
    failures propagate; this helper never falls back from a configured local
    remote or from the restored remote when the switch is disabled.
    """
    if os.environ.get("TRAC_LIVE_LOCAL_REMOTE", "").strip() == "1":
        resolved_root = live_root.resolve()
        artifact_dir = resolved_root.parent / f"{resolved_root.name}-artifacts"
        local_remote = artifact_dir / "baseline-local-remote.git"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        if not (local_remote / "HEAD").is_file():
            local_remote.mkdir(parents=True, exist_ok=True)
            _git(local_remote, "init", "--bare")
        _git(resolved_root, "remote", "set-url", "origin", str(local_remote))

    remote_url = _git(live_root, "remote", "get-url", "origin")
    remote_refs = _git(live_root, "ls-remote", "origin")
    return remote_url, remote_refs


def _read_manifest(path: Path) -> dict | None:
    manifest_path = path / ".tracks-baseline-manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _capture_baseline(
    live_root: Path,
    version: str,
    run_id: str,
    checkpoint: str = "M-REQ-APPROVAL",
    checkpoint_substate: str | None = None,
) -> Path | None:
    """Capture a baseline snapshot of the live host at a named checkpoint.
    The snapshot is a copy of the host directory (.tracks/ runtime DB +
    project docs, .opencode/ provider config, the git checkout) minus
    opencode's node_modules (recreated on demand). The isolated venv + wheel
    live outside the host in runNNN-artifacts/ and are NOT snapshotted: the
    resumed test rebuilds them from the current working tree so code
    iteration takes effect.

    The ``checkpoint`` field in the manifest identifies which state the
    snapshot was taken at (default ``M-REQ-APPROVAL`` for backward
    compatibility with the existing resume test). Design-exit snapshots also
    record ``checkpoint_substate`` when supplied."""
    if os.environ.get("TRAC_LIVE_SKIP_BASELINE", "").strip() == "1":
        print("LIVE_E2E_BASELINE=skipped (TRAC_LIVE_SKIP_BASELINE=1)", flush=True)
        return None
    sha = _tracks_short_sha()
    target = _baseline_dir(version, sha, checkpoint)
    if target.exists():
        print(f"LIVE_E2E_BASELINE=exists {target}", flush=True)
        return target
    target.mkdir(parents=True, exist_ok=True)
    _copy_tree(live_root, target)
    manifest = {
        "tracks_sha": sha,
        "version": version,
        "run_id": run_id,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_host": str(live_root.resolve()),
        "checkpoint": checkpoint,
    }
    if checkpoint_substate is not None:
        manifest["checkpoint_substate"] = checkpoint_substate
    (target / ".tracks-baseline-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    substate = (
        f" checkpoint_substate={checkpoint_substate}"
        if checkpoint_substate is not None
        else ""
    )
    print(
        f"LIVE_E2E_BASELINE=captured {target} checkpoint={checkpoint}{substate}",
        flush=True,
    )
    return target


def _find_baseline_for_sha(
    version: str, checkpoint: str = "M-REQ-APPROVAL"
) -> Path | None:
    """Locate a baseline for the current HEAD at the named checkpoint.

    Lookup order: ``TRAC_LIVE_BASELINE_DIR`` (explicit path) else the newest
    ``baselines/baseline-*-{version}`` with a readable manifest. An exact SHA
    match is preferred; on SHA mismatch the caller decides (via
    ``TRAC_LIVE_FORCE_BASELINE=1``) whether to use it. Candidates whose
    manifest is missing, unreadable, or marked with a different checkpoint
    are skipped in both passes."""
    explicit = os.environ.get("TRAC_LIVE_BASELINE_DIR", "").strip()
    if explicit:
        path = Path(explicit)
        manifest = _read_manifest(path)
        if manifest is None:
            pytest.skip(f"TRAC_LIVE_BASELINE_DIR has no manifest: {path}")
        return path
    sha = _tracks_short_sha()
    root = _baselines_root()
    if not root.is_dir():
        return None
    if checkpoint == "M-REQ-APPROVAL":
        glob_pat = f"baseline-*-{version}"
    else:
        slug = checkpoint.lower().replace("_", "-")
        glob_pat = f"baseline-{slug}-*-{version}"
    candidates = sorted(
        (p for p in root.glob(glob_pat) if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    # First pass: prefer an exact SHA match with a matching checkpoint.
    for path in candidates:
        manifest = _read_manifest(path)
        if (
            manifest
            and manifest.get("tracks_sha") == sha
            and manifest.get("checkpoint", "M-REQ-APPROVAL") == checkpoint
        ):
            return path
    # Fallback: newest candidate with a readable manifest matching the
    # checkpoint; the caller handles SHA mismatch via TRAC_LIVE_FORCE_BASELINE.
    for path in candidates:
        manifest = _read_manifest(path)
        if manifest and manifest.get("checkpoint", "M-REQ-APPROVAL") == checkpoint:
            return path
    return None


# -- design-exit checkpoint (v0.4 M-TEST milestone) ------------------------
#
# Checkpoint truth: the snapshot is captured after the Prism design review
# passed and the machine transitioned M-DESIGN EXIT -> M-TEST. The normal live
# gate stops at ``M-TEST/WRITE`` with Shield's dispatch pending; the accelerated
# two-dispatch fake stops earlier at ``M-TEST/DISPATCH``. Both are ready for
# the real Shield dispatch when the resume test continues.

DESIGN_EXIT_OBSERVED_CHECKPOINT = "DESIGN_EXIT_OBSERVED"
_DESIGN_EXIT_SUBSTATES = frozenset(("DISPATCH", "WRITE"))


def _capture_design_exit_baseline(
    live_root: Path,
    version: str,
    run_id: str,
    checkpoint_substate: str = "WRITE",
) -> Path | None:
    """Capture a design-exit baseline at the declared M-TEST substate.

    ``WRITE`` is the existing live checkpoint; accelerated design capture is
    ``DISPATCH`` because the two-dispatch cap stops before Shield is issued.
    """
    if checkpoint_substate not in _DESIGN_EXIT_SUBSTATES:
        raise ValueError(
            "design-exit checkpoint_substate must be DISPATCH or WRITE, "
            f"got {checkpoint_substate!r}"
        )
    return _capture_baseline(
        live_root,
        version,
        run_id,
        checkpoint=DESIGN_EXIT_OBSERVED_CHECKPOINT,
        checkpoint_substate=checkpoint_substate,
    )


def _find_design_exit_baseline(version: str) -> Path | None:
    return _find_baseline_for_sha(version, checkpoint=DESIGN_EXIT_OBSERVED_CHECKPOINT)


def _sanity_check_resumed_host(live_trac, live_root: Path, baseline: Path) -> str:
    """Assert the restored host is at the M-REQ-APPROVED checkpoint:
    stage=M-REQ-APPROVAL, substate=APPROVED, active (not escalation), and no
    failed outcomes in the DB events. Returns the run_id from the manifest."""
    manifest = _read_manifest(baseline)
    assert manifest, f"baseline missing manifest: {baseline}"
    run_id = manifest["run_id"]

    status = live_trac("status")
    assert "stage=M-REQ-APPROVAL" in status.stdout, (
        f"baseline is not at M-REQ-APPROVAL: {status.stdout}"
    )
    assert "substate=APPROVED" in status.stdout, (
        f"baseline is not in APPROVED substate: {status.stdout}"
    )
    assert "status=active" in status.stdout, (
        f"baseline is not active (escalation?): {status.stdout}"
    )
    assert "awaiting=-" in status.stdout, (
        f"baseline is awaiting (escalation?): {status.stdout}"
    )

    events = _events(live_root, run_id)
    failed = [
        e
        for e in events
        if e["type"] == "outcome.received" and e["payload"].get("status") != "done"
    ]
    assert not failed, (
        f"baseline has non-done outcomes (escalation evidence): "
        f"{[(e['seq'], e['payload'].get('status')) for e in failed]}"
    )
    return run_id


def _sanity_check_design_exit_host(
    live_trac, live_root: Path, baseline: Path
) -> str:
    """Assert a restored design-exit host is active at its manifest substate.

    New manifests declare ``DISPATCH`` or ``WRITE``. A missing declaration is
    the legacy/live ``WRITE`` baseline. The transition into M-TEST must remain
    strictly adjacent to the M-DESIGN exit in the event log.
    """
    manifest = _read_manifest(baseline)
    assert manifest, f"baseline missing manifest: {baseline}"
    run_id = manifest["run_id"]
    assert manifest.get("checkpoint") == DESIGN_EXIT_OBSERVED_CHECKPOINT, (
        f"baseline checkpoint mismatch: expected "
        f"{DESIGN_EXIT_OBSERVED_CHECKPOINT}, got "
        f"{manifest.get('checkpoint')}"
    )
    checkpoint_substate = manifest.get("checkpoint_substate", "WRITE")
    assert checkpoint_substate in _DESIGN_EXIT_SUBSTATES, (
        "design-exit manifest checkpoint_substate must be DISPATCH or WRITE, "
        f"got {checkpoint_substate!r}"
    )

    status = live_trac("status")
    assert "stage=M-TEST" in status.stdout, (
        f"design-exit baseline is not at M-TEST: {status.stdout}"
    )
    assert f"substate={checkpoint_substate}" in status.stdout.split(), (
        f"design-exit baseline is not in manifest-declared "
        f"{checkpoint_substate} substate: {status.stdout}"
    )
    assert "status=active" in status.stdout, (
        f"design-exit baseline is not active (escalation?): {status.stdout}"
    )

    events = _events(live_root, run_id)
    _assert_design_exit_adjacency(events)
    failed = [
        e
        for e in events
        if e["type"] == "outcome.received" and e["payload"].get("status") != "done"
    ]
    assert not failed, (
        f"design-exit baseline has non-done outcomes (escalation evidence): "
        f"{[(e['seq'], e['payload'].get('status')) for e in failed]}"
    )
    return run_id


# -- issue events ----------------------------------------------------------


def _assert_issue_events(final_events: list[dict], expect_real_issues: bool) -> None:
    """issue.created events present; ids digits-only (expect_real_issues) else
    digits/FAKE- (baseline resume fake channel, tracks/effects/github.py)."""
    evs = [e for e in final_events if e["type"] == "issue.created"]
    assert evs, "missing issue.created event"
    assert all("issue_id" in e["payload"] for e in evs), "missing issue_id"
    ids = [str(e["payload"]["issue_id"]) for e in evs]
    if expect_real_issues:
        assert all(i.isdigit() for i in ids)
    else:
        assert all(i.isdigit() or i.startswith("FAKE-") for i in ids), (
            "issue_id is neither all digits nor a FAKE- fake id"
        )
