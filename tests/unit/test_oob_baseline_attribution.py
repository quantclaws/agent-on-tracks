"""OOB-attributed baseline retirement for post-WRITE COLLECT.

A baseline node absent from the current collect is normally a fail-closed
``asset_deleted``. When an accepted operator OOB commit itself renamed or
removed that node -- and the worktree still holds exactly that OOB version of
the file -- the deletion is explained by the runtime's own declared change and
must be retired instead of re-dispatched as a Shield test defect.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.unit.m_test_executor_support import (
    _RUN_ID,
    _cmd,
    _contract,
    _fake_subprocess,
    _make_harness,
    _of_type,
    _patch_load_contract,
    _proc,
    _section,
    _seed_baseline,
    _State,
)
from tracks.executor.test_collect import _partition_removed_nodes

_OOB_SHA = "a" * 40
_OOB_FILE = "tests/unit/test_breaker.py"
_OLD_NODE = (
    f"{_OOB_FILE}::test_self_rollback_without_operator_repair_not_counted"
)
_NEW_NODE = f"{_OOB_FILE}::test_self_rollback_with_repair_counts_as_real_rollback"
_OTHER_FILE = "tests/unit/test_other.py"
_OTHER_NODE = f"{_OTHER_FILE}::test_gone"

_NEW_SOURCE = "def test_self_rollback_with_repair_counts_as_real_rollback():\n    pass\n"
_OLD_SOURCE = (
    "def test_self_rollback_without_operator_repair_not_counted():\n    pass\n"
)


def _partition(
    removed,
    *,
    baseline_nodes,
    oob_files,
    oob_source,
    worktree_blob="worktree-blob",
    oob_blob="worktree-blob",
):
    return _partition_removed_nodes(
        list(removed),
        baseline_nodes=frozenset(baseline_nodes),
        oob_files=set(oob_files),
        oob_shas=[_OOB_SHA],
        oob_blob=lambda sha, file: oob_blob,
        worktree_blob=lambda file: worktree_blob,
        oob_source=lambda sha, file: oob_source,
    )


def test_oob_version_retires_renamed_node():
    retired, unexplained = _partition(
        [_OLD_NODE],
        baseline_nodes=[_OLD_NODE],
        oob_files={_OOB_FILE},
        oob_source=_NEW_SOURCE,
    )

    assert retired == [_OLD_NODE]
    assert unexplained == []


def test_non_oob_file_stays_unexplained():
    retired, unexplained = _partition(
        [_OLD_NODE],
        baseline_nodes=[_OLD_NODE],
        oob_files=set(),
        oob_source=_NEW_SOURCE,
    )

    assert retired == []
    assert unexplained == [_OLD_NODE]


def test_agent_modified_after_oob_stays_unexplained():
    retired, unexplained = _partition(
        [_OLD_NODE],
        baseline_nodes=[_OLD_NODE],
        oob_files={_OOB_FILE},
        oob_source=_NEW_SOURCE,
        worktree_blob="agent-blob",
        oob_blob="oob-blob",
    )

    assert retired == []
    assert unexplained == [_OLD_NODE]


def test_oob_version_still_defining_node_stays_unexplained():
    retired, unexplained = _partition(
        [_OLD_NODE],
        baseline_nodes=[_OLD_NODE],
        oob_files={_OOB_FILE},
        oob_source=_OLD_SOURCE,
    )

    assert retired == []
    assert unexplained == [_OLD_NODE]


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "-c", "user.email=t@tracks.dev", "-c", "user.name=t", "add", "tests/")
    _git(
        repo,
        "-c",
        "user.email=t@tracks.dev",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "-m",
        message,
    )
    return _git(repo, "rev-parse", "HEAD")


def _seed_oob(store, sha: str, files: list[str]) -> None:
    store.append(
        _RUN_ID,
        "v0.4",
        "oob.accepted",
        {"sha": sha, "reason": "operator hotfix", "files": files},
    )


def _install_collect(monkeypatch, unit_stdout: str) -> None:
    _patch_load_contract(
        monkeypatch,
        lambda repo: _contract(
            unit=_section(collect="_tracks_collect_unit"),
            integration=_section(collect="_tracks_collect_integration"),
            e2e=_section(collect="_tracks_collect_e2e"),
        ),
    )

    def _dispatch(argv):
        joined = " ".join(argv)
        if "_tracks_collect_unit" in joined:
            return _proc(argv, 0, stdout=unit_stdout)
        if "_tracks_collect_integration" in joined:
            return _proc(argv, 0)
        if "_tracks_collect_e2e" in joined:
            return _proc(argv, 0)
        raise AssertionError(f"unexpected subprocess call: {argv}")

    _fake_subprocess(monkeypatch, _dispatch)


def _oob_repo(tmp_path):
    repo = tmp_path / "host"
    test_file = repo / _OOB_FILE
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(_OLD_SOURCE, encoding="utf-8")
    _git(repo, "init", "-q")
    _commit(repo, "baseline")
    test_file.write_text(_NEW_SOURCE, encoding="utf-8")
    return repo, test_file, _commit(repo, "oob rename")


def _baseline_entries():
    return [
        {
            "node": _OLD_NODE,
            "layer": "unit",
            "digest": "old-file-digest",
            "node_digest": "old-node-digest",
        }
    ]


def test_collect_passes_and_records_oob_retired(monkeypatch, tmp_path):
    ex, store, emitted, repo = _make_harness(monkeypatch, tmp_path)
    repo, test_file, oob_sha = _oob_repo(tmp_path)
    assert test_file.read_text(encoding="utf-8") == _NEW_SOURCE
    _seed_baseline(store, _baseline_entries())
    _seed_oob(store, oob_sha, [_OOB_FILE])
    _install_collect(monkeypatch, _NEW_NODE + "\n")

    ex._do_collect_tests(_cmd("cc-oob"), _State(), None, False)

    passed = [
        e for e in _of_type(emitted, "test.collected")
        if e["payload"].get("status") == "passed"
    ]
    assert len(passed) == 1, [e["payload"] for e in emitted]
    assert passed[0]["payload"]["oob_retired"] == [_OLD_NODE]
    assert passed[0]["payload"]["delta_r2"] == 1
    assert not _of_type(emitted, "verdict.failed")


def test_mixed_removed_fails_closed_naming_only_unexplained(monkeypatch, tmp_path):
    ex, store, emitted, repo = _make_harness(monkeypatch, tmp_path)
    repo, _test_file, oob_sha = _oob_repo(tmp_path)
    other = repo / _OTHER_FILE
    other.write_text("def test_gone():\n    pass\n", encoding="utf-8")
    _seed_baseline(
        store,
        _baseline_entries()
        + [
            {
                "node": _OTHER_NODE,
                "layer": "unit",
                "digest": "other-file-digest",
                "node_digest": "other-node-digest",
            }
        ],
    )
    _seed_oob(store, oob_sha, [_OOB_FILE])
    _install_collect(monkeypatch, _NEW_NODE + "\n")

    ex._do_collect_tests(_cmd("cc-mixed"), _State(), None, False)

    failed = [
        e for e in _of_type(emitted, "test.collected")
        if e["payload"].get("status") == "failed"
    ]
    assert len(failed) == 1
    payload = failed[0]["payload"]
    assert payload["failures"] == [
        {"node": _OTHER_NODE, "error_class": "asset_deleted"}
    ]
    assert payload["errors"] == [
        f"test asset deleted since baseline snapshot: {_OTHER_NODE}"
    ]
    assert payload["oob_retired"] == [_OLD_NODE]
    defects = [
        e for e in _of_type(emitted, "verdict.failed")
        if e["payload"].get("check") == "test_defect"
    ]
    assert len(defects) == 1
    assert _OTHER_NODE in defects[0]["payload"]["reason"]
    assert _OLD_NODE in defects[0]["payload"]["reason"]
    assert "OOB-retired" in defects[0]["payload"]["reason"]
    assert not any(
        e["payload"].get("status") == "passed"
        for e in _of_type(emitted, "test.collected")
    )
