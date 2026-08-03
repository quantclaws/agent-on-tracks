"""Generic harness contracts for explicit scenario assignment inputs."""

import json

import pytest

from tracks.effects import select_backend
from tracks.effects.opencode import AGENT_NAME, OpencodeBackend


def _start(trac):
    assert trac("init").returncode == 0
    started = trac("start", "v0.1", stdin="a deterministic requirement\n")
    assert started.returncode == 0
    return started


def _dispatches(event_log, run_id):
    return [
        event for event in event_log(run_id)
        if event["type"] == "command.issued"
        and event["payload"]["command"]["kind"] == "dispatch_agent"
    ]


def test_normal_run_has_no_scenario_context_or_dispatch_limit(trac, event_log):
    started = _start(trac)
    run_id = started.stdout.split("run ", 1)[1].split(" started", 1)[0]

    first = trac("run")
    assert first.returncode == 0
    first_dispatches = _dispatches(event_log, run_id)
    assert len(first_dispatches) == 1
    assert "scenario_context" not in first_dispatches[0]["payload"]["command"]["params"][
        "assignment"
    ]

    assert trac("triage", "go").returncode == 0
    second = trac("run")
    assert second.returncode == 0
    assert len(_dispatches(event_log, run_id)) > len(first_dispatches) + 1


def test_overlay_is_nested_without_overriding_base_assignment(
    host_repo, trac, event_log
):
    started = _start(trac)
    run_id = started.stdout.split("run ", 1)[1].split(" started", 1)[0]
    scenario = {
        "kind": "scenario-kind",
        "template_kind": "scenario-template",
        "skill": "scenario-skill",
        "finding": "scenario-only-finding",
    }
    path = host_repo / "scenario.json"
    path.write_text(json.dumps(scenario), encoding="utf-8")

    result = trac(
        "run",
        "--assignment-overlay",
        str(path),
        "--max-dispatches",
        "1",
    )
    assert result.returncode == 0
    assignment = _dispatches(event_log, run_id)[0]["payload"]["command"]["params"][
        "assignment"
    ]
    assert set(assignment) == {
        "kind", "template_kind", "skill", "skill_version", "scenario_context"
    }
    assert assignment["kind"] == "TRIAGE"
    assert assignment["template_kind"] == "story"
    assert assignment["skill"] == "tracks-discuz"
    assert assignment["skill_version"] == "0.2"
    assert assignment["scenario_context"] == scenario


def test_max_dispatches_stops_after_closed_dispatch_and_next_run_continues(
    trac, event_log
):
    started = _start(trac)
    run_id = started.stdout.split("run ", 1)[1].split(" started", 1)[0]

    assert trac("run", "--max-dispatches", "1").returncode == 0
    assert len(_dispatches(event_log, run_id)) == 1
    assert trac("triage", "go").returncode == 0

    assert trac("run", "--max-dispatches", "1").returncode == 0
    assert len(_dispatches(event_log, run_id)) == 2
    assert "substate=SAGE_REVIEW" in trac("status").stdout

    continued = trac("run", "--max-dispatches", "1")
    assert continued.returncode == 0
    assert len(_dispatches(event_log, run_id)) == 3
    assert "awaiting=review" in continued.stdout
    assert not [event for event in event_log(run_id) if event["type"] == "run.interrupted"]


@pytest.mark.parametrize("value", ["0", "-1", "not-an-integer"])
def test_invalid_dispatch_limit_fails_closed(trac, event_log, value):
    started = _start(trac)
    run_id = started.stdout.split("run ", 1)[1].split(" started", 1)[0]
    before = event_log(run_id)

    result = trac("run", "--max-dispatches", value)

    assert result.returncode != 0
    assert "positive integer" in result.stderr
    assert event_log(run_id) == before


@pytest.mark.parametrize("kind", ["missing", "invalid", "non-object"])
def test_invalid_overlay_fails_closed(host_repo, trac, event_log, kind):
    started = _start(trac)
    run_id = started.stdout.split("run ", 1)[1].split(" started", 1)[0]
    path = host_repo / f"{kind}.json"
    if kind == "invalid":
        path.write_text("{not json", encoding="utf-8")
    elif kind == "non-object":
        path.write_text("[1, 2, 3]", encoding="utf-8")
    else:
        path = host_repo / "does-not-exist.json"
    before = event_log(run_id)

    result = trac("run", "--assignment-overlay", str(path))

    assert result.returncode != 0
    assert "assignment overlay" in result.stderr
    assert event_log(run_id) == before


def test_prompt_is_generic_and_exposes_scenario_json(tmp_path):
    backend = OpencodeBackend(tmp_path, "v0.1")
    assignment = {
        "kind": "TRIAGE",
        "template_kind": "story",
        "skill": "tracks-discuz",
        "skill_version": "0.2",
        "scenario_context": {"finding": "scenario-only"},
    }

    prompt = backend._prompt("scribe", "TRIAGE", "story.md", None, assignment)

    assert "scenario_context" in prompt
    assert "scenario-only" in prompt
    for forbidden in (
        "Runtime live console contract",
        "tracks-live-console/v1",
        "LiveE2E-Human",
        "STORY-OUTPUT-PLACEMENT",
        "SPEC-BLANK-LINE-SEMANTICS",
    ):
        assert forbidden not in prompt


def test_role_map_covers_every_tracks_role():
    # Every Runtime role (IF-001 §5) maps to a shipped opencode agent Name —
    # including the v0.3 M-DESIGN pair (Archer drafts, Prism reviews).
    assert AGENT_NAME == {
        "scribe": "Scribe",
        "sage": "Sage",
        "lex": "Lex",
        "archer": "Archer",
        "prism": "Prism",
    }


@pytest.mark.parametrize("role,name", sorted(AGENT_NAME.items()))
def test_materialize_cleanup_cycle_for_every_agent(tmp_path, role, name):
    """Each agent definition materializes byte-identical and is removed on
    cleanup (ARCH §4c) — identical contract for Archer/Prism."""
    backend = OpencodeBackend(tmp_path, "v0.1")
    source = backend._canonical / f"{name}.md"
    assert source.exists()  # canonical prompt ships with the package
    info = backend._materialize(name)
    try:
        assert info["dest"].read_bytes() == source.read_bytes()
    finally:
        backend._cleanup(info)
    assert not info["dest"].exists()


def test_target_paths_single_doc_passthrough(tmp_path):
    backend = OpencodeBackend(tmp_path, "v0.1")
    doc = tmp_path / "story.md"
    assert backend._target_paths(doc, None) == [doc]
    assert backend._target_paths(None, None) == []
    assert backend._target_paths(None, {"kind": "DRAFT"}) == []


def test_target_paths_doc_set_derived_from_assignment(tmp_path, monkeypatch):
    """A multi-doc assignment (M-DESIGN DRAFT) resolves its whole doc set under
    .tracks/projects/{version}/ — derived from the assignment, not the role."""
    monkeypatch.delenv("TRACKS_HOME", raising=False)
    backend = OpencodeBackend(tmp_path, "v0.1")
    assignment = {"kind": "DRAFT",
                  "docs": ["architecture.md", "interfaces.md", "test-plan.md"]}
    vdir = tmp_path / ".tracks" / "projects" / "v0.1"
    assert backend._target_paths(None, assignment) == [
        vdir / "architecture.md",
        vdir / "interfaces.md",
        vdir / "test-plan.md",
    ]


def test_prompt_names_the_doc_set_of_a_multi_doc_assignment(tmp_path):
    backend = OpencodeBackend(tmp_path, "v0.1")
    assignment = {"kind": "DRAFT",
                  "docs": ["architecture.md", "interfaces.md", "test-plan.md"]}
    prompt = backend._prompt("archer", "DRAFT", None, None, assignment)
    assert "architecture.md, interfaces.md, test-plan.md" in prompt


def test_agent_timeout_uses_generic_environment_name(monkeypatch, tmp_path):
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "opencode")
    monkeypatch.setenv("TRAC_AGENT_TIMEOUT", "17")
    backend = select_backend(tmp_path, "v0.1")
    assert isinstance(backend, OpencodeBackend)
    assert backend.timeout == 17
