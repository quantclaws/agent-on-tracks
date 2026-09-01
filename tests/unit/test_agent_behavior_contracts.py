"""Runtime-observable agent behavior contracts (b91 Q5 aggregate).

Judged by behavior, never by prompt prose: one parametrized matrix pins the
kernel ``decide()`` dispatch surface (role/substate/stage/skills) for every
agent-touching stage and asserts the parser-stub gate (M3: no assignment may
declare ``tracks-envelope:v2`` while ``envelope.parse_agent_output`` raises
``IF-ENVELOPE-001``), one sweep materializes + cleans up every DELIVERABLE,
and the FakeBackend bootstrap outcomes for each writer role are consumed
through the same runtime validators the executor uses.

Per-stage dispatch details are already covered by test_machine_design,
test_machine_m_impl, test_machine_m_test and test_devon_rgr; this module is
the cross-role aggregation so a host-neutral rewrite (b91) cannot drift one
stage's routing or re-introduce an envelope v2 declaration unnoticed.
"""

import json

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
    seq,
)
from tracks.deliverables import DELIVERABLES
from tracks.effects.backend import valid_test_tasks
from tracks.effects.fake import FakeBackend
from tracks.executor.m_impl_runtime import _m_impl_red_classification_error
from tracks.kernel import decide, project
from tracks.kernel.envelope import parse_agent_output

# -- dispatch matrix: role/substate/stage/skills + envelope declaration gate --


def _m_design_draft():
    return project(
        seq(("story.requested", {"raw_chars": 5}), ("stage.entered", {"stage": "M-DESIGN"}))
    )


def _m_impl(*items):
    return project(seq(*ENTER_M_IMPL, *items))


def _red_prefix():
    return [
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


def _green_prefix():
    return [
        *_red_prefix(),
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


def _m_test_write():
    # D-41 v3 timing: the pre-WRITE R1 snapshot gates the first Shield WRITE.
    return project(
        seq(
            ("story.requested", {"raw_chars": 5}),
            ("stage.entered", {"stage": "M-TEST"}),
            (
                "command.issued",
                {
                    "command": {
                        "kind": "capture_baseline",
                        "params": {"stage": "M-TEST"},
                        "command_id": "C0",
                    }
                },
            ),
            (
                "test.baseline_captured",
                {
                    "status": "passed",
                    "baseline_id": "b0",
                    "baseline_tree": "t0",
                    "layers": ["unit", "integration", "e2e"],
                    "nodes_count": 0,
                    "empty_baseline": True,
                    "node_digest_blob": None,
                    "errors": [],
                },
            ),
        )
    )


def _m_test_prism_review():
    return project(
        seq(
            ("story.requested", {"raw_chars": 5}),
            ("stage.entered", {"stage": "M-TEST"}),
            (
                "command.issued",
                {
                    "command": {
                        "kind": "capture_baseline",
                        "params": {"stage": "M-TEST"},
                        "command_id": "C0",
                    }
                },
            ),
            (
                "test.baseline_captured",
                {
                    "status": "passed",
                    "baseline_id": "b0",
                    "baseline_tree": "t0",
                    "layers": ["unit", "integration", "e2e"],
                    "nodes_count": 0,
                    "empty_baseline": True,
                    "node_digest_blob": None,
                    "errors": [],
                },
            ),
            (
                "command.issued",
                {
                    "command": {
                        "kind": "dispatch_agent",
                        "params": {"role": "shield", "substate": "WRITE"},
                        "command_id": "C1",
                    }
                },
            ),
            ("outcome.received", {"role": "shield", "status": "done"}),
            (
                "command.issued",
                {
                    "command": {
                        "kind": "collect_tests",
                        "params": {"stage": "M-TEST"},
                        "command_id": "C2",
                    }
                },
            ),
            ("test.collected", {"status": "passed", "collected_count": 1, "errors": []}),
            (
                "command.issued",
                {
                    "command": {
                        "kind": "run_tests",
                        "params": {"stage": "M-TEST"},
                        "command_id": "C4",
                    }
                },
            ),
            ("red.validated", {"status": "valid", "findings": []}),
        )
    )


DISPATCH_CASES = (
    (
        "m_design_draft",
        _m_design_draft,
        "archer",
        "DRAFT",
        "M-DESIGN",
        ["tracks-discuz", "tracks-archer-design", "tracks-quality-guards"],
    ),
    (
        "m_impl_planning",
        lambda: _m_impl(BASELINE_FROZEN),
        "archer",
        "PLANNING",
        "M-IMPL",
        ["tracks-discuz", "tracks-archer-planning"],
    ),
    ("m_impl_red", lambda: _m_impl(*_red_prefix()), "devon", "RED", "M-IMPL", ["tracks-devon-rgr"]),
    (
        "m_impl_green",
        lambda: _m_impl(*_green_prefix()),
        "devon",
        "GREEN",
        "M-IMPL",
        ["tracks-devon-rgr"],
    ),
    (
        "m_impl_refactor",
        lambda: _m_impl(
            *_green_prefix(),
            DEVON_GREEN_DISPATCH,
            DEVON_GREEN_DONE,
            GREEN_GATE_CMD,
            GREEN_PASS,
            GREEN_COMMIT_CMD,
            GREEN_COMMITTED,
        ),
        "devon",
        "REFACTOR",
        "M-IMPL",
        ["tracks-devon-rgr"],
    ),
    (
        "m_impl_prism_plan",
        lambda: _m_impl(
            BASELINE_CMD,
            BASELINE_FROZEN,
            ARCHER_DISPATCH,
            ARCHER_DONE,
            TASKGRAPH_CMD,
            TASKGRAPH_COMMITTED,
            ISLAND1_CMD,
            ISLAND1_PASS,
        ),
        "prism",
        "PRISM_PLAN",
        "M-IMPL",
        ["tracks-discuz", "tracks-prism-impl"],
    ),
    (
        "m_impl_shield_fix",
        lambda: _m_impl(
            *_green_prefix(),
            DEVON_GREEN_DISPATCH,
            DEVON_GREEN_DONE,
            GREEN_GATE_CMD,
            ("verdict.failed", {"check": "unknown_attribution", "reason": "unclear", "attempt": 1}),
            ("verdict.failed", {"check": "test_defect", "attempt": 1}),
        ),
        "shield",
        "WRITE",
        "M-IMPL",
        ["tracks-discuz", "tracks-shield"],
    ),
    (
        "m_test_write",
        _m_test_write,
        "shield",
        "WRITE",
        "M-TEST",
        ["tracks-discuz", "tracks-shield"],
    ),
    (
        "m_test_prism_review",
        _m_test_prism_review,
        "prism",
        "PRISM_REVIEW",
        "M-TEST",
        ["tracks-discuz", "tracks-prism-test"],
    ),
)


@pytest.mark.parametrize(
    "name,state,role,substate,stage,skills", DISPATCH_CASES, ids=[c[0] for c in DISPATCH_CASES]
)
def test_stage_dispatch_contract(name, state, role, substate, stage, skills):
    """decide() emits the agent dispatch with the locked role/substate/stage
    and the exact skill set for the stage (host-neutral routing)."""
    built = state()
    cmd = decide(built)
    assert cmd.kind == "dispatch_agent", f"{name}: {cmd.kind}"
    params = cmd.params
    assert params["role"] == role, f"{name}: role {params['role']}"
    assert params["substate"] == substate, f"{name}: substate {params['substate']}"
    assert params["stage"] == stage, f"{name}: stage {params['stage']}"
    assignment = params["assignment"]
    assert list(assignment["skills"]) == skills, f"{name}: skills {assignment['skills']}"


@pytest.mark.parametrize(
    "name,state,role,substate,stage,skills", DISPATCH_CASES, ids=[c[0] for c in DISPATCH_CASES]
)
def test_no_assignment_declares_envelope_v2(name, state, role, substate, stage, skills):
    """M3 gate: the envelope parser is still a stub, so runtime-built
    assignments must not declare ``tracks-envelope:v2`` — every dispatch in
    the matrix rides the bootstrap bare-JSON contract."""
    cmd = decide(state())
    assert "tracks-envelope:v2" not in json.dumps(cmd.params["assignment"]), name


def test_envelope_parser_is_still_a_stub():
    """IF-ENVELOPE-001: b91 ships the conditional prompt contract without the
    parser; flipping this stub is a separate OOB (must flip the assignment
    declaration in the same change, see test_no_assignment_declares_envelope_v2)."""
    with pytest.raises(NotImplementedError, match="IF-ENVELOPE-001"):
        parse_agent_output("```tracks-envelope\n{}\n```")


# -- materialization: every deliverable copies byte-identical and cleans up ----


@pytest.mark.parametrize("asset", DELIVERABLES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_deliverable_materializes_byte_identical_and_cleans_up(tmp_path, asset):
    from tracks.effects.opencode import OpencodeBackend

    backend = OpencodeBackend(tmp_path, "v1.0")
    if asset.parent.name == "agents":
        info = backend._materialize(asset.stem)
    else:
        info = backend._materialize_skill(asset.parent.name)
    assert info is not None, f"materializer returned None for {asset}"
    assert_cleanup_cycle(backend, info, asset)


# -- FakeBackend bootstrap outcomes through the runtime validators ------------


def _devon_assignment(phase: str) -> dict:
    value = {
        "task_id": "T-100",
        "phase": phase,
        "if_ids": ["IF-IMPL-001"],
        "ac_refs": ["AC-FR0001-01"],
        "test_refs": ["tests/unit/test_widget.py::test_widget"],
        "commands": [".venv/bin/python -m pytest -n 4 tests/unit/test_widget.py"],
        "manifest": {
            "allowed_paths": ["tracks/impl/widget.py", "tests/unit/test_widget.py"],
            "forbidden_paths": ["tests/integration/**"],
        },
        "pre_dirty_snapshot": {},
        "result_identity": "result-100",
    }
    if phase in ("green", "refactor"):
        value["r_tree_identity"] = "r-tree-100"
    return value


@pytest.mark.parametrize("phase", ["red", "green", "refactor"])
def test_fake_devon_outcome_is_legal_evidence(tmp_path, phase):
    """The fake Devon evidence rides the same legal path the executor
    validates: RED classifications pass the runtime classifier; GREEN/REFACTOR
    carry the shape rules from assignment.evidence_contract."""
    outcome = FakeBackend(tmp_path, "v0.5").act(
        "devon", phase.upper(), None, None, _devon_assignment(phase)
    )
    assert outcome["status"] == "done", outcome.get("failure_class")
    assert outcome["phase"] == phase
    assert outcome["manifest_compliance"] is True
    assert isinstance(outcome["commands"], list) and outcome["commands"]
    if phase == "red":
        error = _m_impl_red_classification_error(outcome)
        assert error is None, f"runtime rejected the fake RED evidence: {error}"
    else:
        assert outcome["r_identity"], "green/refactor evidence must carry r_identity"


def test_fake_shield_write_manifest_matches_assignment_contract(tmp_path):
    """Shield WRITE: the D-28 test_tasks contract gates the dispatch, and the
    returned artifact_manifest covers exactly the files written under the
    assignment-declared test layout."""
    tasks = [
        {"ac_id": "AC-FR0001-01", "layers": ["integration"], "if_ids": ["IF-IMPL-001"]},
        {"ac_id": "AC-FR0002-01", "layers": ["e2e"], "if_ids": ["IF-IMPL-002"]},
    ]
    assert valid_test_tasks(tasks), "fixture test_tasks must satisfy the D-28 validator"
    repo = tmp_path
    (repo / "tests" / "integration").mkdir(parents=True)
    (repo / "tests" / "e2e").mkdir(parents=True)
    outcome = FakeBackend(repo, "v0.5").act("shield", "WRITE", None, None, {"test_tasks": tasks})
    assert outcome["status"] == "done", outcome.get("failure_class")
    manifest = outcome["artifact_manifest"]
    includes = (
        manifest["artifact_manifest"]["include"]
        if "artifact_manifest" in manifest
        else manifest["include"]
    )
    written = {entry["path"] for entry in includes}
    assert written, "done Shield WRITE must declare its writes"
    for path in written:
        assert not path.startswith("/"), f"manifest path must be repo-relative: {path}"
        assert (repo / path).is_file(), f"manifest path not on disk: {path}"
        assert path.startswith("tests/"), f"write escaped the declared test layout: {path}"
