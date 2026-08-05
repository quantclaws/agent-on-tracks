"""Deterministic coverage for the v0.4 M-TEST live-journey assertions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

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


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    origin = tmp_path / "origin.git"
    repo.mkdir()
    origin.mkdir()
    _git(origin, "init", "--bare")
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "M-TEST tests")
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
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
