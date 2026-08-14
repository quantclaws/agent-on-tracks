"""Focused unit tests for the bounded Devon deployment (v0.5 phase-separated RGR).

Covers: role selection (AGENT_NAME + --agent Devon), prompt materialization,
canonical/deployed equality (agent + skill), Devon dispatch assignment shape
(phase, skill, public keys, r_tree_identity gating), and Devon.md contract
checks (three isolated phases, fail-closed, no whole-cycle, no commit/push).
"""

from pathlib import Path

import pytest

from tests.unit.helpers import (
    ARCHER_DISPATCH,
    ARCHER_DONE,
    BASELINE_CMD,
    BASELINE_FROZEN,
    DEVON_GREEN_DISPATCH,
    DEVON_GREEN_DONE,
    DEVON_RED_DISPATCH,
    DEVON_RED_DONE,
    ENTER_M_IMPL,
    GREEN_COMMIT_CMD,
    GREEN_COMMITTED,
    GREEN_GATE_CMD,
    GREEN_PASS,
    ISLAND1_CMD,
    ISLAND1_PASS,
    M_IMPL_REQUIRED_ASSIGNMENT_KEYS,
    PRISM_PLAN_DISPATCH,
    PRISM_PLAN_DONE,
    PRISM_PLAN_PASS,
    PRISM_RED_DISPATCH,
    PRISM_RED_DONE,
    PRISM_RED_PASS,
    RED_CHECKPOINT_CMD,
    RED_CHECKPOINTED,
    RED_GATE_CMD,
    RED_VALID_PASS,
    SELECT_TASK_CMD,
    TASK_STARTED,
    TASKGRAPH_CMD,
    TASKGRAPH_COMMITTED,
    assert_cleanup_cycle,
    capture_popen_cmd,
    seq,
)
from tracks.effects.opencode import AGENT_NAME, OpencodeBackend
from tracks.kernel import decide, project

_TRACKS_PKG = Path(__file__).resolve().parent.parent.parent / "tracks"
_CANONICAL_AGENTS = _TRACKS_PKG / "agents"
_CANONICAL_SKILLS = _TRACKS_PKG / "skills"
_DEPLOYED = Path(__file__).resolve().parent.parent.parent / ".opencode"


def _to_red():
    return [
        *ENTER_M_IMPL,
        BASELINE_CMD,
        BASELINE_FROZEN,
        ARCHER_DISPATCH,
        ARCHER_DONE,
        TASKGRAPH_CMD,
        TASKGRAPH_COMMITTED,
        ISLAND1_CMD,
        ISLAND1_PASS,
        PRISM_PLAN_DISPATCH,
        PRISM_PLAN_DONE,
        PRISM_PLAN_PASS,
        SELECT_TASK_CMD,
        TASK_STARTED,
    ]


def _to_green():
    return [
        *_to_red(),
        DEVON_RED_DISPATCH,
        DEVON_RED_DONE,
        RED_GATE_CMD,
        RED_VALID_PASS,
        RED_CHECKPOINT_CMD,
        RED_CHECKPOINTED,
        PRISM_RED_DISPATCH,
        PRISM_RED_DONE,
        PRISM_RED_PASS,
    ]


def _to_refactor():
    return [
        *_to_green(),
        DEVON_GREEN_DISPATCH,
        DEVON_GREEN_DONE,
        GREEN_GATE_CMD,
        GREEN_PASS,
        GREEN_COMMIT_CMD,
        GREEN_COMMITTED,
    ]


def _state_of(*items):
    return project(seq(*items))


# -- role selection ----------------------------------------------------------


def test_devon_role_in_agent_name():
    """role=devon maps to agent Name=Devon (FR-0170)."""
    assert AGENT_NAME["devon"] == "Devon"


def test_devon_not_unknown_role(tmp_path):
    """act(role=devon) does not return provider_unavailable/unknown-role."""
    OpencodeBackend(tmp_path, "v0.1")
    assert AGENT_NAME.get("devon") is not None


def test_devon_run_cmd_uses_agent_devon(monkeypatch, tmp_path):
    """opencode run --agent Devon is selected for role=devon."""
    captured = capture_popen_cmd(monkeypatch)
    backend = OpencodeBackend(tmp_path, "v0.1")
    backend._run("Devon", "prompt")
    cmd = captured["cmd"]
    assert "--agent" in cmd
    assert cmd[cmd.index("--agent") + 1] == "Devon"


# -- prompt materialization --------------------------------------------------


def test_devon_canonical_prompt_exists():
    """Canonical Devon.md ships with the package."""
    assert (_CANONICAL_AGENTS / "Devon.md").exists()


def test_devon_materialize_cleanup_cycle(tmp_path):
    """Devon.md materializes byte-identical and is removed on cleanup."""
    backend = OpencodeBackend(tmp_path, "v0.1")
    source = backend._canonical / "Devon.md"
    assert source.exists()
    info = backend._materialize("Devon")
    assert_cleanup_cycle(backend, info, source)


def test_devon_rgr_skill_materializes(tmp_path):
    """tracks-devon-rgr skill materializes to .opencode/skills/."""
    backend = OpencodeBackend(tmp_path, "v0.1")
    src = _CANONICAL_SKILLS / "tracks-devon-rgr" / "SKILL.md"
    assert src.exists()
    info = backend._materialize_skill("tracks-devon-rgr")
    assert info is not None
    assert_cleanup_cycle(backend, info, src)


# -- canonical / deployed equality -------------------------------------------


def test_devon_canonical_equals_deployed():
    """Deployed .opencode/agents/Devon.md matches canonical tracks/agents/Devon.md."""
    canonical = (_CANONICAL_AGENTS / "Devon.md").read_bytes()
    deployed = (_DEPLOYED / "agents" / "Devon.md").read_bytes()
    assert canonical == deployed


def test_devon_rgr_skill_canonical_equals_deployed():
    """Deployed skill matches canonical skill."""
    canonical = (_CANONICAL_SKILLS / "tracks-devon-rgr" / "SKILL.md").read_bytes()
    deployed = (_DEPLOYED / "skills" / "tracks-devon-rgr" / "SKILL.md").read_bytes()
    assert canonical == deployed


def test_devon_rgr_skill_frontmatter():
    """Skill has valid frontmatter: name, version, description."""
    from tracks.frontmatter import split_frontmatter

    text = (_CANONICAL_SKILLS / "tracks-devon-rgr" / "SKILL.md").read_text(encoding="utf-8")
    head, _ = split_frontmatter(text)
    fm = {}
    for line in head.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    assert fm.get("name") == "tracks-devon-rgr"
    assert fm.get("version", "").startswith("0.")
    assert fm.get("description")


# -- Devon dispatch assignment shape -----------------------------------------


def test_devon_red_dispatch_phase_and_skill():
    """RED dispatch: phase=red, skill=tracks-devon-rgr (not tracks-discuz)."""
    s = _state_of(*_to_red())
    assert s.substate == "RED"
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "devon"
    assignment = cmd.params["assignment"]
    assert assignment["phase"] == "red"
    assert "tracks-devon-rgr" in assignment["skills"]
    assert "tracks-discuz" not in assignment["skills"]


def test_devon_green_dispatch_phase_and_r_tree():
    """GREEN dispatch: phase=green, r_tree_identity set from checkpoint."""
    s = _state_of(*_to_green())
    assert s.substate == "GREEN"
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assignment = cmd.params["assignment"]
    assert assignment["phase"] == "green"
    assert assignment["r_tree_identity"] == "abc123"


def test_devon_refactor_dispatch_phase_and_r_tree():
    """REFACTOR dispatch: phase=refactor, r_tree_identity preserved."""
    s = _state_of(*_to_refactor())
    assert s.substate == "REFACTOR"
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assignment = cmd.params["assignment"]
    assert assignment["phase"] == "refactor"
    assert assignment["r_tree_identity"] == "abc123"


def test_devon_red_dispatch_r_tree_is_none():
    """RED dispatch: r_tree_identity is None (no checkpoint yet)."""
    s = _state_of(*_to_red())
    cmd = decide(s)
    assignment = cmd.params["assignment"]
    assert assignment["r_tree_identity"] is None


def test_devon_dispatch_has_public_keys():
    """Devon dispatch assignment carries public keys for executor materialization."""
    s = _state_of(*_to_red())
    cmd = decide(s)
    assignment = cmd.params["assignment"]
    for key in ("task_id", "if_ids", "ac_refs", "test_refs", "commands"):
        assert key in assignment, f"missing public key: {key}"
    assert assignment["task_id"] == "T1"
    assert assignment["if_ids"] is None
    assert assignment["ac_refs"] is None
    assert assignment["test_refs"] is None
    assert assignment["commands"] is None


def test_devon_dispatch_has_manifest_placeholder():
    """Devon dispatch carries manifest placeholder for executor materialization."""
    s = _state_of(*_to_red())
    cmd = decide(s)
    assignment = cmd.params["assignment"]
    assert assignment["manifest"] is None
    assert assignment["pre_dirty_snapshot"] is None
    assert assignment["result_identity"] is None


def test_devon_dispatch_still_has_required_base_keys():
    """Devon dispatch still carries all base assignment keys (D-29 parity)."""
    s = _state_of(*_to_red())
    cmd = decide(s)
    assignment = cmd.params["assignment"]
    assert assignment.keys() >= M_IMPL_REQUIRED_ASSIGNMENT_KEYS


# -- Devon.md contract checks ------------------------------------------------


def _devon_md_text():
    return (_CANONICAL_AGENTS / "Devon.md").read_text(encoding="utf-8")


def test_devon_md_describes_three_isolated_phases():
    """Devon.md describes three isolated dispatch modes: red, green, refactor."""
    text = _devon_md_text()
    for phase in ("red", "green", "refactor"):
        assert phase in text.lower()


def test_devon_md_no_whole_rgr_cycle():
    """Devon.md states it never runs a whole RGR cycle in one assignment."""
    text = _devon_md_text()
    assert "phase" in text.lower()
    assert "停止" in text or "stop" in text.lower()


def test_devon_md_fail_closed_contract():
    """Devon.md lists required assignment keys for fail-closed."""
    text = _devon_md_text()
    assert "fail closed" in text.lower()
    for key in (
        "task_id",
        "phase",
        "if_ids",
        "ac_refs",
        "test_refs",
        "commands",
        "manifest",
        "pre_dirty_snapshot",
        "result_identity",
        "r_tree_identity",
    ):
        assert key in text


def test_devon_md_red_forbids_product_code():
    """RED phase contract: product code forbidden."""
    text = _devon_md_text()
    assert "产品代码" in text or "product code" in text.lower()
    assert "禁止" in text or "forbidden" in text.lower()


def test_devon_md_green_r_tests_immutable():
    """GREEN phase contract: R tests and frozen tests immutable."""
    text = _devon_md_text()
    assert "不可变" in text or "immutable" in text.lower()


def test_devon_md_refactor_no_change_allowed():
    """REFACTOR phase contract: may return no_change + reason."""
    text = _devon_md_text()
    assert "no_change" in text


def test_devon_md_no_commit_push_issues():
    """Devon.md forbids commit/push/Issues/task state in all phases."""
    text = _devon_md_text()
    assert "不 commit/push" in text or "no commit" in text.lower()
    assert "Issues" in text
    assert "task state" in text.lower()


def test_devon_md_virtualenv_and_n4():
    """Devon.md requires virtualenv and pytest -n 4."""
    text = _devon_md_text()
    assert ".venv" in text or "虚拟环境" in text
    assert "-n 4" in text


def test_devon_md_output_schema():
    """Devon.md defines structured output schema with required fields."""
    text = _devon_md_text()
    for field in ("phase", "changed_paths", "manifest_compliance", "no_change_reason"):
        assert field in text


def test_devon_md_frozen_test_isolation():
    """Devon.md preserves frozen test isolation rules."""
    text = _devon_md_text()
    for path in ("tests/integration", "tests/e2e", "tests/counterexamples", "tests/ground_truth"):
        assert path in text


def test_devon_md_version_bumped():
    """Devon.md version bumped to reflect phase-separated contract."""
    from tracks.frontmatter import split_frontmatter

    head, _ = split_frontmatter(_devon_md_text())
    for line in head.splitlines():
        if line.startswith("version:"):
            assert line.split(":", 1)[1].strip().startswith("0.")
            return
    pytest.fail("no version in frontmatter")


# -- kernel purity (Devon dispatch is pure) ----------------------------------


def test_devon_dispatch_pure_no_io():
    """decide() at RED/GREEN/REFACTOR performs no I/O (NFR-0030)."""
    s = _state_of(*_to_red())
    cmd = decide(s)
    assert cmd is not None
    assert cmd.kind == "dispatch_agent"
    # Rebuilding from same events yields identical dispatch
    s2 = _state_of(*_to_red())
    cmd2 = decide(s2)
    assert cmd2.params["role"] == cmd.params["role"]
    assert cmd2.params["assignment"]["phase"] == cmd.params["assignment"]["phase"]


# -- T-03 contract 6: Runtime classification is the routing authority ---------


def test_runtime_impl_defect_routes_green_to_devon_not_agent_override():
    """Contract 6 (Human routing contract): a Runtime-observed GREEN failure
    classified `impl_defect` leads the kernel to GREEN/Devon. An Agent-provided
    owner/route field on the outcome cannot override the Runtime mapping —
    routing follows the Runtime verdict event, not the Agent's self-report."""
    items = [
        *_to_green(),
        DEVON_GREEN_DISPATCH,
        # Devon's outcome attempts to claim an owner/route that would reroute
        # the failure away from the Runtime mapping. The kernel ignores it.
        (
            "outcome.received",
            {"role": "devon", "status": "done", "owner": "shield", "route": "refactor_no_change"},
        ),
        GREEN_GATE_CMD,
        # Runtime-observed GREEN failure, classified impl_defect.
        ("verdict.failed", {"check": "impl_defect", "reason": "runtime gate", "attempt": 1}),
        # Runtime classification is authoritative -> Prism DIAGNOSE confirms,
        # then the kernel routes to GREEN/Devon.
        (
            "command.issued",
            {
                "command": {
                    "kind": "dispatch_agent",
                    "params": {"role": "prism", "substate": "DIAGNOSE"},
                    "command_id": "C14",
                }
            },
        ),
        ("outcome.received", {"role": "prism", "status": "done"}),
        ("verdict.failed", {"check": "impl_defect", "attempt": 1}),
    ]
    s = _state_of(*items)
    assert s.substate == "GREEN", (
        "Runtime impl_defect must route the kernel to GREEN, never to the agent-claimed route"
    )
    assert s.last_failure and s.last_failure["check"] == "impl_defect"
    assert s.current_attempt == 1
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "devon"
    assert cmd.params["substate"] == "GREEN"
    assert cmd.params["assignment"]["phase"] == "green"
    assert cmd.params["assignment"]["r_tree_identity"] == "abc123"
