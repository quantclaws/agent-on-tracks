"""Deterministic coverage for the v0.4 M-TEST live-journey assertions."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tests.e2e_live import m_test_helpers as _helpers
from tests.e2e_live import test_full_journey as _journey
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
        tmp_path, None, None, driver, monkeypatch
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
            tmp_path, None, None, driver, monkeypatch
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
            tmp_path, None, None, _DesignTailDriver(), monkeypatch
        )


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
