"""Unit tests: #172 dual delivery — criteria pack to the writer + the
result_file injection wiring (#174's missing pipe).

Two faces under test:

1. kernel builders (``m_impl_decide``): Archer PLANNING and Devon
   RED/GREEN/REFACTOR dispatch assignments carry the SAME criteria pack
   identity the reviewer echoes (``tracks-prism-impl``) plus a pre-emission
   ``criteria_checklist``; the skills list materializes the skill file so
   the generator reads the identical rubric text (single source, no drift).
2. executor injection face (``run_loop._enrich_envelope_params``): every
   declared dispatch carries a ``result_file`` block whose path is ABSOLUTE
   under the main repo's runtime inbox and named by the per-dispatch
   command id (fresh by construction); ``TRAC_RESULT_FILE=0`` reverts to
   fence-only delivery.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tracks.executor.executor import Executor
from tracks.kernel.contracts import (
    ARCHER_PLANNING_CRITERIA_CHECKLIST,
    DEVON_RGR_CRITERIA_CHECKLIST,
    result_file_contract,
)
from tracks.kernel.m_impl import _m_impl_archer_dispatch, _m_impl_devon_dispatch
from tracks.kernel.m_impl_state import _M_IMPL_CRITERIA_PACK
from tracks.kernel.machine import State
from tracks.store import Store

RUN_ID = "run-dual-delivery-0001"
TASK_ID = "T-9"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILL_FRONTMATTER = (
    _REPO_ROOT / "tracks" / "skills" / "tracks-prism-impl" / "SKILL.md"
).read_text(encoding="utf-8")


def _skill_version() -> str:
    match = re.search(r"^version:\s*(\S+)", _SKILL_FRONTMATTER, re.MULTILINE)
    assert match, "tracks-prism-impl SKILL.md carries a version"
    return match.group(1)


@pytest.fixture()
def exec_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")
    store = Store(tmp_path / "store")
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    ex = Executor(store, repo, RUN_ID)
    try:
        yield ex, repo
    finally:
        store.close()


# ---------------------------------------------------------------------------
# kernel: generator dispatches carry the reviewer's rubric (#172)
# ---------------------------------------------------------------------------


def test_criteria_pack_identity_tracks_the_skill_frontmatter():
    """Single-source rule: the identity the runtime assigns (and the verdict
    echo checks) names the version the skill file actually ships. The 0.1
    identity rode along while the skill shipped 0.2 — dual delivery makes
    the identity load-bearing for writers, so it must not drift again."""
    assert {
        "name": "tracks-prism-impl",
        "version": _skill_version(),
    } == _M_IMPL_CRITERIA_PACK


def test_archer_planning_dual_delivers_criteria():
    cmd = _m_impl_archer_dispatch(State(stage="M-IMPL", substate="PLANNING"))
    assignment = cmd.params["assignment"]
    assert assignment["skills"] == [
        "tracks-discuz",
        "tracks-archer-planning",
        "tracks-prism-impl",
    ]
    checklist = assignment["criteria_checklist"]
    assert checklist["_doc"]
    items = checklist["self_check_before_emission"]
    assert len(items) >= 4
    # The checklist names the criteria, the skill carries the full wording.
    assert any("覆盖闭包" in item for item in items)
    assert any("可满足性" in item for item in items)
    assert "tracks-prism-impl" in checklist["_doc"]


@pytest.mark.parametrize("sub", ["RED", "GREEN", "REFACTOR"])
def test_devon_rgr_dual_delivers_criteria(sub):
    cmd = _m_impl_devon_dispatch(
        State(stage="M-IMPL", substate=sub, current_task_id=TASK_ID), sub
    )
    assignment = cmd.params["assignment"]
    assert assignment["skills"] == ["tracks-devon-rgr", "tracks-prism-impl"]
    checklist = assignment["criteria_checklist"]
    items = checklist["self_check_before_emission"]
    assert any("IMPL-3" in item for item in items)
    assert any("IMPL-1" in item for item in items)
    # The evidence contract stays (B30) alongside the new checklist.
    assert assignment["evidence_contract"]


def test_prism_dispatch_keeps_its_own_pack_not_the_checklist():
    """The reviewer card keeps its shape: criteria_pack identity, no writer
    checklist (the reviewer does not self-check against its own rubric)."""
    from tracks.kernel.m_impl import _m_impl_prism_dispatch

    cmd = _m_impl_prism_dispatch(State(stage="M-IMPL", substate="PRISM_PLAN"), "PRISM_PLAN")
    assignment = cmd.params["assignment"]
    assert assignment["criteria_pack"] == dict(_M_IMPL_CRITERIA_PACK)
    assert "criteria_checklist" not in assignment
    assert "tracks-prism-impl" in assignment["skills"]


def test_checklists_are_plain_data():
    """Kernel contracts stay JSON-serializable plain data (card bytes)."""
    import json

    for checklist in (ARCHER_PLANNING_CRITERIA_CHECKLIST, DEVON_RGR_CRITERIA_CHECKLIST):
        blob = json.dumps(checklist, ensure_ascii=False)
        assert len(blob) < 2000, "checklist must stay lean (M5 card diet)"


# ---------------------------------------------------------------------------
# executor: result_file injection on every declared dispatch (#172/#174)
# ---------------------------------------------------------------------------


def _declared_params() -> dict:
    return {
        "role": "prism",
        "substate": "PRISM_FINAL",
        "assignment": {"task": {"task_id": TASK_ID}},
    }


def test_declared_dispatch_carries_result_file(exec_env):
    ex, repo = exec_env
    params = _declared_params()
    ex._enrich_envelope_params(params, "cmd-abc123")
    block = params["assignment"]["result_file"]
    path = Path(block["result_path"])
    assert path.is_absolute()
    # Absolute + anchored at the MAIN repo inbox: writer dispatches run in a
    # worktree that dies at replay cleanup — a repo-relative path would be
    # lost with it, so the agent must write through to the main repo.
    assert path.parent == (repo / ".tracks" / "runtime" / "inbox").resolve()
    assert path.name == "cmd-abc123.json"
    assert path.parent.is_dir()
    assert block["verify_command"].endswith("--kind prism:final")
    assert str(path) in block["how"]


def test_result_file_path_is_unique_per_dispatch(exec_env):
    """Fresh/anti-stale by construction: the command id names the file, so a
    reused (poisoned) session can only ever write its own dispatch's path."""
    ex, _repo = exec_env
    first, second = _declared_params(), _declared_params()
    ex._enrich_envelope_params(first, "cmd-one")
    ex._enrich_envelope_params(second, "cmd-two")
    assert first["assignment"]["result_file"]["result_path"] != (
        second["assignment"]["result_file"]["result_path"]
    )


def test_trac_result_file_zero_reverts_to_fence_only(exec_env, monkeypatch):
    ex, _repo = exec_env
    monkeypatch.setenv("TRAC_RESULT_FILE", "0")
    params = _declared_params()
    ex._enrich_envelope_params(params, "cmd-off")
    assignment = params["assignment"]
    assert assignment["envelope"]["kind"] == "prism:final"
    assert "result_file" not in assignment


def test_envelope_declare_off_disables_result_file_too(exec_env, monkeypatch):
    """The result channel rides the declaration gate: an undeclared dispatch
    never carries a result_file pointer."""
    ex, _repo = exec_env
    monkeypatch.setenv("TRAC_ENVELOPE_DECLARE", "0")
    params = _declared_params()
    ex._enrich_envelope_params(params, "cmd-undeclared")
    assert "envelope" not in params["assignment"]
    assert "result_file" not in params["assignment"]


def test_undeclared_role_substate_gets_no_result_file(exec_env):
    ex, _repo = exec_env
    params = {"role": "archer", "substate": "UNKNOWN", "assignment": {}}
    ex._enrich_envelope_params(params, "cmd-unknown")
    assert "result_file" not in params["assignment"]


# ---------------------------------------------------------------------------
# kernel: result_file_contract composition
# ---------------------------------------------------------------------------


def test_result_file_contract_uses_caller_path_verbatim():
    block = result_file_contract("/tmp/anywhere/inbox/x.json", "devon:green")
    assert block["result_path"] == "/tmp/anywhere/inbox/x.json"
    assert block["verify_command"] == (
        "python -m tracks.cli.main validate-reply"
        " --file /tmp/anywhere/inbox/x.json --kind devon:green"
    )
    assert "json.dump" in block["how"]
