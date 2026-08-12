"""Unit tests for RGR git operations (IF-IMPL-004).

classify_red is tested with the same closed-set taxonomy; create_red_ref /
create_green_commit / verify_lineage are tested against a throwaway git repo.
"""
from __future__ import annotations

import time

import pytest

from tests.unit.helpers import git as _git
from tests.unit.helpers import init_repo as _init_repo
from tracks.executor.rgr import (
    classify_red,
    create_green_commit,
    create_red_ref,
    verify_lineage,
)

_RED_DIFF = (
    "diff --git a/tests/unit/test_task.py b/tests/unit/test_task.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/tests/unit/test_task.py\n"
    "@@ -0,0 +1 @@\n"
    "+def test_task(): assert False\n"
)
_GREEN_DIFF = (
    "diff --git a/app.py b/app.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/app.py\n"
    "@@ -0,0 +1 @@\n"
    "+def app(): return True\n"
)
_COMBINED_AC_REFS = ["AC-FR0030-01", "FR-0030", "NFR-0001"]


def test_classify_red_stub_token():
    assert classify_red("t1", 1, "",
                        'raise NotImplementedError("IF-IMPL-004")') == "stub_token_failure"


def test_classify_red_assertion():
    assert classify_red("t1", 1, "E   assert False\n", "") == "assertion_failure"


def test_classify_red_symbol_missing():
    assert classify_red("t1", 1, "", "AttributeError: no attr") == "symbol_missing"


def test_classify_red_collection_error():
    assert classify_red("t1", 1, "", "ImportError: No module") == "collection_error"


def test_classify_red_unexpected_pass():
    assert classify_red("t1", 0, "1 passed", "") == "unexpected_pass"


def test_create_red_ref(tmp_path):
    repo, base = _init_repo(tmp_path)
    ref = create_red_ref(str(repo), "run-1", "T-001", 1, _RED_DIFF, base)
    assert ref.ref == "refs/trac/rgr/run-1/T-001/1/red"
    assert ref.created
    sha = _git(repo, "rev-parse", ref.ref).stdout.strip()
    assert sha == ref.sha


def test_create_red_ref_compare_and_set(tmp_path):
    repo, base = _init_repo(tmp_path)
    ref1 = create_red_ref(str(repo), "run-1", "T-001", 1, _RED_DIFF, base)
    ref2 = create_red_ref(str(repo), "run-1", "T-001", 1, _RED_DIFF, base)
    assert ref1.sha == ref2.sha
    assert ref1.created
    assert not ref2.created


def test_create_green_commit(tmp_path):
    repo, base = _init_repo(tmp_path)
    ref = create_red_ref(str(repo), "run-1", "T-001", 1, _RED_DIFF, base)
    green = create_green_commit(
        str(repo), "run-1", "T-001", 1, _GREEN_DIFF, base, ref.sha,
        issue_number=1, ac_refs=["AC-FR0030-01"],
    )
    message = _git(repo, "log", "--format=%B", "-1", green.sha).stdout
    assert "Tracks-Task: T-001" in message
    assert "Tracks-Attempt: 1" in message
    assert f"Tracks-R: {ref.sha}" in message
    assert "Tracks-Issue: 1" in message
    assert "Tracks-AC: AC-FR0030-01" in message
    parent = _git(repo, "rev-parse", f"{green.sha}^").stdout.strip()
    assert parent == base


def test_verify_lineage_ok(tmp_path):
    repo, base = _init_repo(tmp_path)
    ref = create_red_ref(str(repo), "run-1", "T-001", 1, _RED_DIFF, base)
    green = create_green_commit(
        str(repo), "run-1", "T-001", 1, _GREEN_DIFF, base, ref.sha,
        issue_number=1, ac_refs=["AC-FR0030-01"],
    )
    events = [
        {"seq": 1, "type": "red.checkpointed", "payload": {"task_id": "T-001", "attempt": 1}},
        {"seq": 2, "type": "green.committed", "payload": {"task_id": "T-001", "attempt": 1}},
    ]
    proof = verify_lineage(
        str(repo), "run-1", "T-001", 1, green.sha, events,
    )
    assert proof.r_ref_exists
    assert proof.g_trailers_valid
    assert proof.event_order_valid
    assert proof.r_before_g


def test_verify_lineage_missing_ref(tmp_path):
    repo, base = _init_repo(tmp_path)
    green = create_green_commit(
        str(repo), "run-1", "T-001", 1, _GREEN_DIFF, base, "0" * 40,
        issue_number=1, ac_refs=["AC-FR0030-01"],
    )
    proof = verify_lineage(
        str(repo), "run-1", "T-001", 1, green.sha, [],
    )
    assert not proof.r_ref_exists
    assert not proof.r_before_g


def test_create_red_ref_idempotent_across_second(tmp_path):
    """R commit SHA is deterministic (date derived from base commit, not
    wall-clock).  Calling twice across a second boundary produces the same
    SHA; second call returns created=False."""
    repo, base = _init_repo(tmp_path)
    ref1 = create_red_ref(str(repo), "run-1", "T-001", 1, _RED_DIFF, base)
    time.sleep(1.1)
    ref2 = create_red_ref(str(repo), "run-1", "T-001", 1, _RED_DIFF, base)
    assert ref1.sha == ref2.sha
    assert ref1.created
    assert not ref2.created
    r_date = _git(repo, "log", "-1", "--format=%cI", ref1.sha).stdout.strip()
    base_date = _git(repo, "log", "-1", "--format=%cI", base).stdout.strip()
    assert r_date == base_date


def test_create_red_ref_mismatch_raises(tmp_path):
    """Same ref cannot be replaced by a different diff (immutable mismatch)."""
    repo, base = _init_repo(tmp_path)
    create_red_ref(str(repo), "run-1", "T-001", 1, _RED_DIFF, base)
    diff_b = (
        "diff --git a/newfile.txt b/newfile.txt\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/newfile.txt\n"
        "@@ -0,0 +1 @@\n"
        "+content\n"
    )
    with pytest.raises(RuntimeError, match="immutable ref"):
        create_red_ref(str(repo), "run-1", "T-001", 1, diff_b, base)


def test_empty_rgr_diffs_fail_closed(tmp_path):
    repo, base = _init_repo(tmp_path)
    with pytest.raises(ValueError, match="captured test diff"):
        create_red_ref(str(repo), "run-1", "T-001", 1, "", base)
    with pytest.raises(ValueError, match="captured implementation diff"):
        create_green_commit(
            str(repo), "run-1", "T-001", 1, "", base, "r",
            issue_number=1, ac_refs=["AC-FR0030-01"],
        )


def test_create_green_commit_records_combined_ac_refs_only(tmp_path):
    """G commit records exactly five trailers; AC/FR/NFR provenance is the
    single combined Tracks-AC trailer (interfaces.md §3e, FR-0220). No
    Tracks-FR / Tracks-NFR trailers exist."""
    repo, base = _init_repo(tmp_path)
    ref = create_red_ref(str(repo), "run-1", "T-001", 1, _RED_DIFF, base)
    green = create_green_commit(
        str(repo), "run-1", "T-001", 1, _GREEN_DIFF, base, ref.sha,
        issue_number=1, ac_refs=_COMBINED_AC_REFS,
    )
    assert green.parent == base
    assert green.trailers == {
        "Tracks-Task": "T-001",
        "Tracks-Attempt": "1",
        "Tracks-R": ref.sha,
        "Tracks-Issue": "1",
        "Tracks-AC": "AC-FR0030-01,FR-0030,NFR-0001",
    }
    message = _git(repo, "log", "--format=%B", "-1", green.sha).stdout
    assert "Tracks-Task: T-001" in message
    assert "Tracks-Attempt: 1" in message
    assert f"Tracks-R: {ref.sha}" in message
    assert "Tracks-Issue: 1" in message
    assert "Tracks-AC: AC-FR0030-01,FR-0030,NFR-0001" in message
    assert "Tracks-FR:" not in message
    assert "Tracks-NFR:" not in message
    parent = _git(repo, "rev-parse", f"{green.sha}^").stdout.strip()
    assert parent == base


def test_create_green_commit_is_deterministic_with_combined_ac_refs(tmp_path):
    repo, base = _init_repo(tmp_path)
    ref = create_red_ref(str(repo), "run-1", "T-001", 1, _RED_DIFF, base)
    kwargs = {
        "repo": str(repo), "run_id": "run-1", "task_id": "T-001", "attempt": 1,
        "impl_diff": _GREEN_DIFF, "base_sha": base, "r_sha": ref.sha,
        "issue_number": 1, "ac_refs": _COMBINED_AC_REFS,
    }
    first = create_green_commit(**kwargs)
    second = create_green_commit(**kwargs)
    assert first.sha == second.sha
    assert first.parent == base


def _lineage_events(task_id="T-001", attempt=1):
    return [
        {"seq": 1, "type": "red.checkpointed",
         "payload": {"task_id": task_id, "attempt": attempt}},
        {"seq": 2, "type": "green.committed",
         "payload": {"task_id": task_id, "attempt": attempt}},
    ]


def _verify_lineage_exact(repo, run_id, task_id, attempt, g_sha, events,
                         issue_number, ac_refs):
    # Intended exactness contract: verify_lineage must compare exact
    # Tracks-Task / Tracks-Attempt from the positional args, exact Tracks-R
    # from the immutable red ref, and exact Tracks-Issue / Tracks-AC from the
    # expected issue_number + combined ac_refs.
    return verify_lineage(
        repo, run_id, task_id, attempt, g_sha, events,
        issue_number=issue_number, ac_refs=ac_refs,
    )


def test_verify_lineage_accepts_expected_issue_and_ac_refs(tmp_path):
    repo, base = _init_repo(tmp_path)
    ref = create_red_ref(str(repo), "run-1", "T-001", 1, _RED_DIFF, base)
    green = create_green_commit(
        str(repo), "run-1", "T-001", 1, _GREEN_DIFF, base, ref.sha,
        issue_number=1, ac_refs=_COMBINED_AC_REFS,
    )
    proof = _verify_lineage_exact(
        str(repo), "run-1", "T-001", 1, green.sha, _lineage_events(),
        issue_number=1, ac_refs=_COMBINED_AC_REFS,
    )
    assert proof.r_ref_exists
    assert proof.g_trailers_valid
    assert proof.event_order_valid
    assert proof.r_before_g


def test_verify_lineage_rejects_wrong_expected_task(tmp_path):
    repo, base = _init_repo(tmp_path)
    ref = create_red_ref(str(repo), "run-1", "T-001", 1, _RED_DIFF, base)
    green = create_green_commit(
        str(repo), "run-1", "T-999", 1, _GREEN_DIFF, base, ref.sha,
        issue_number=1, ac_refs=_COMBINED_AC_REFS,
    )
    proof = _verify_lineage_exact(
        str(repo), "run-1", "T-001", 1, green.sha, _lineage_events(),
        issue_number=1, ac_refs=_COMBINED_AC_REFS,
    )
    assert proof.r_ref_exists
    assert not proof.g_trailers_valid
    assert not proof.r_before_g


def test_verify_lineage_rejects_wrong_expected_r(tmp_path):
    repo, base = _init_repo(tmp_path)
    create_red_ref(str(repo), "run-1", "T-001", 1, _RED_DIFF, base)
    green = create_green_commit(
        str(repo), "run-1", "T-001", 1, _GREEN_DIFF, base, "0" * 40,
        issue_number=1, ac_refs=_COMBINED_AC_REFS,
    )
    proof = _verify_lineage_exact(
        str(repo), "run-1", "T-001", 1, green.sha, _lineage_events(),
        issue_number=1, ac_refs=_COMBINED_AC_REFS,
    )
    assert proof.r_ref_exists
    assert not proof.g_trailers_valid
    assert not proof.r_before_g


def test_verify_lineage_rejects_wrong_expected_fr(tmp_path):
    repo, base = _init_repo(tmp_path)
    ref = create_red_ref(str(repo), "run-1", "T-001", 1, _RED_DIFF, base)
    green = create_green_commit(
        str(repo), "run-1", "T-001", 1, _GREEN_DIFF, base, ref.sha,
        issue_number=1, ac_refs=_COMBINED_AC_REFS,
    )
    proof = _verify_lineage_exact(
        str(repo), "run-1", "T-001", 1, green.sha, _lineage_events(),
        issue_number=1, ac_refs=["AC-FR0030-01", "FR-9999", "NFR-0001"],
    )
    assert proof.r_ref_exists
    assert not proof.g_trailers_valid
    assert not proof.r_before_g


def test_verify_lineage_rejects_wrong_expected_nfr(tmp_path):
    repo, base = _init_repo(tmp_path)
    ref = create_red_ref(str(repo), "run-1", "T-001", 1, _RED_DIFF, base)
    green = create_green_commit(
        str(repo), "run-1", "T-001", 1, _GREEN_DIFF, base, ref.sha,
        issue_number=1, ac_refs=_COMBINED_AC_REFS,
    )
    proof = _verify_lineage_exact(
        str(repo), "run-1", "T-001", 1, green.sha, _lineage_events(),
        issue_number=1, ac_refs=["AC-FR0030-01", "FR-0030", "NFR-9999"],
    )
    assert proof.r_ref_exists
    assert not proof.g_trailers_valid
    assert not proof.r_before_g
