"""Behavior coverage for the IF-LIVE-001 evidence producer (live_evidence).

Binds independently observable journey facts and asserts the fail-closed
contracts: fake/simulated/overlay provenance rejected, identity shape rules,
strictly increasing event sequence, per-role dispatch receipt binding,
journey-ordered gates, the M-IMPL boundary adjacency, and atomic
content-addressed persistence.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tracks.executor import live_evidence as le
from tracks.executor.live_evidence import (
    EVIDENCE_ROOT,
    SCHEMA_VERSION,
    CandidateArtifactEvidence,
    LiveEvidenceBundle,
)

CAND = "a" * 40
RUN = "run-001"
TRAILERS = {
    "Tracks-Task": "T-001",
    "Tracks-Attempt": "2",
    "Tracks-R": "c" * 40,
    "Tracks-Issue": "42",
    "Tracks-AC": "AC-FR0030-01",
}


def _artifact(**overrides) -> CandidateArtifactEvidence:
    values = {
        "name": "agent_on_tracks-0.5.0-py3-none-any.whl",
        "sha256": "b" * 64,
        "distribution": "agent-on-tracks",
        "version": "0.5.0",
        "installed_import_path": "/venv/site-packages/tracks/__init__.py",
        "source_tree_import": False,
    }
    values.update(overrides)
    return CandidateArtifactEvidence(**values)


def _dispatch(seq: int, role: str, substate: str, *, attempt: int | None = None) -> dict:
    params: dict = {"role": role, "substate": substate}
    if attempt is not None:
        params["assignment"] = {"attempt": attempt}
    return {
        "seq": seq,
        "type": "command.issued",
        "payload": {"command": {"kind": "dispatch_agent", "params": params}},
        "command_id": f"cmd-{seq}",
    }


def _outcome(
    seq: int,
    role: str,
    phase: str,
    *,
    status: str | None = "done",
    completeness: str | None = "complete",
    task_id: str = "T-001",
    **extra,
) -> dict:
    payload = {"role": role, "phase": phase, **extra}
    if status is not None:
        payload["status"] = status
    if completeness is not None:
        payload["audit_completeness"] = completeness
    return {
        "seq": seq,
        "type": "outcome.received",
        "payload": payload,
        "task_id": task_id,
    }


def _gate(seq: int, check: str, **extra) -> dict:
    return {
        "seq": seq,
        "type": "verdict.passed",
        "payload": {"check": check, **extra},
        "command_id": f"gate-{seq}",
    }


def _happy_journey() -> list[dict]:
    return [
        {"seq": 1, "type": "task.started", "payload": {"task_id": "T-001", "attempt": 2}},
        _dispatch(2, "devon", "RED"),
        _outcome(3, "devon", "RED"),
        _dispatch(4, "devon", "GREEN"),
        _outcome(5, "devon", "GREEN"),
        _dispatch(6, "devon", "REFACTOR"),
        _outcome(7, "devon", "REFACTOR"),
        _dispatch(8, "prism", "PRISM_RED"),
        _outcome(9, "prism", "PRISM_RED"),
        _dispatch(10, "prism", "PRISM_FINAL"),
        _outcome(11, "prism", "PRISM_FINAL"),
        {
            "seq": 12,
            "type": "red.checkpointed",
            "payload": {"ref": "refs/trac/rgr/RUN/T-001/2/red", "r_sha": "c" * 40},
        },
        {
            "seq": 13,
            "type": "green.committed",
            "payload": {"g_sha": "d" * 40, "trailers": dict(TRAILERS)},
        },
        _gate(14, "red_valid"),
        _gate(15, "green_gate"),
        _gate(16, "refactor_gate"),
        _gate(17, "task_review"),
        _gate(18, "prism_final"),
        _gate(19, "island_gate_2"),
        {"seq": 20, "type": "stage.exited", "payload": {"stage": "M-IMPL"}},
        {"seq": 21, "type": "run.completed", "payload": {"terminal_state": "boundary"}},
    ]


def _bind(journey: list[dict] | None = None, **overrides) -> LiveEvidenceBundle:
    kwargs = {
        "repo": ".",
        "run_id": RUN,
        "candidate_sha": CAND,
        "candidate_artifact": _artifact(),
        "backend_name": "opencode",
        "trac_fake_simulate": False,
        "assignment_overlay": False,
        "assignment_simulation": False,
        "events": journey if journey is not None else _happy_journey(),
    }
    kwargs.update(overrides)
    return le.bind_live_evidence(**kwargs)


# ---------------------------------------------------------------------------
# canonical helpers
# ---------------------------------------------------------------------------


def test_canonical_bytes_and_hashes():
    value = {"b": 1, "a": "x"}
    raw = le._canonical_bytes(value)
    assert raw == b'{"a":"x","b":1}\n'
    assert le._sha256_hex(b"x") == hashlib.sha256(b"x").hexdigest()
    assert le._canonical_sha256(value) == le._sha256_hex(raw)
    assert (
        le._blob_ref(CAND, RUN, "f" * 64)
        == f"{EVIDENCE_ROOT}/{CAND}/{RUN}/blobs/{'f' * 64}"
    )


@pytest.mark.parametrize(
    ("value", "length", "expected"),
    [
        ("a" * 40, 40, True),
        ("A" * 40, 40, False),
        ("a" * 39, 40, False),
        ("g" * 40, 40, False),
        ("", 40, False),
    ],
)
def test_is_hex(value, length, expected):
    assert le._is_hex(value, length) is expected


def test_event_dict_accepts_dicts_and_envelope_like_objects():
    plain = {"seq": 1, "type": "x", "payload": {"a": 1}}
    assert le._event_dict(plain) == plain
    assert le._event_dict(plain) is not plain
    env = SimpleNamespace(
        seq=2, type="y", payload={"b": 2}, command_id="c", task_id="T"
    )
    assert le._event_dict(env) == {
        "seq": 2,
        "type": "y",
        "payload": {"b": 2},
        "command_id": "c",
        "task_id": "T",
    }
    bare = SimpleNamespace(seq=3, type="z")
    assert le._event_dict(bare)["payload"] == {}
    assert le._payload_of({"payload": "not-a-dict"}) == {}
    assert le._payload_of({}) == {}


# ---------------------------------------------------------------------------
# provenance / identity validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("backend", "fake", "overlay", "simulation", "message"),
    [
        ("fake", False, False, False, "backend is not opencode"),
        ("opencode", True, False, False, "TRAC_FAKE_SIMULATE"),
        ("opencode", False, True, False, "assignment overlay"),
        ("opencode", False, False, True, "assignment simulation"),
    ],
)
def test_provenance_failures(backend, fake, overlay, simulation, message):
    with pytest.raises(ValueError, match=message):
        _bind(
            backend_name=backend,
            trac_fake_simulate=fake,
            assignment_overlay=overlay,
            assignment_simulation=simulation,
        )


def test_identity_rejects_empty_run_and_bad_candidate_sha():
    with pytest.raises(ValueError, match="run_id is empty"):
        _bind(run_id="")
    with pytest.raises(ValueError, match="candidate_sha"):
        _bind(candidate_sha="A" * 40)
    with pytest.raises(ValueError, match="candidate_sha"):
        _bind(candidate_sha="a" * 39)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"distribution": "something-else"}, "distribution is not agent-on-tracks"),
        ({"source_tree_import": True}, "imported from the source tree"),
        ({"sha256": "g" * 64}, "artifact sha256 is not 64 lowercase hex"),
        ({"name": ""}, "identity fields are incomplete"),
        ({"version": ""}, "identity fields are incomplete"),
        ({"installed_import_path": ""}, "identity fields are incomplete"),
    ],
)
def test_identity_rejects_artifact_violations(overrides, message):
    with pytest.raises(ValueError, match=message):
        _bind(candidate_artifact=_artifact(**overrides))


# ---------------------------------------------------------------------------
# event normalization and fake-provenance scan
# ---------------------------------------------------------------------------


def test_normalize_events_requires_nonempty_and_strict_sequence():
    with pytest.raises(ValueError, match="no events"):
        le._normalize_events([])
    with pytest.raises(ValueError, match="missing seq/type"):
        le._normalize_events([{"seq": "x", "type": "a"}])
    with pytest.raises(ValueError, match="missing seq/type"):
        le._normalize_events([{"seq": 1, "type": 7}])
    with pytest.raises(ValueError, match="not strictly increasing"):
        le._normalize_events(
            [{"seq": 2, "type": "a"}, {"seq": 2, "type": "b"}]
        )
    assert [item["seq"] for item in le._normalize_events(
        [{"seq": 1, "type": "a"}, {"seq": 2, "type": "b"}]
    )] == [1, 2]


@pytest.mark.parametrize(
    "key",
    ["fake_backend", "simulated", "trac_fake_simulate", "assignment_overlay", "assignment_simulation"],
)
def test_event_payload_fake_provenance_fails_closed(key):
    journey = _happy_journey()
    journey[2]["payload"][key] = True
    with pytest.raises(ValueError, match=key):
        _bind(journey)
    # Falsy values are legitimate runtime facts.
    journey[2]["payload"][key] = False
    le._scan_event_provenance(le._normalize_events(journey))


# ---------------------------------------------------------------------------
# receipt binding
# ---------------------------------------------------------------------------


def test_dispatch_detection_and_nearest_command():
    assert le._is_dispatch_command(_dispatch(1, "devon", "RED")) is True
    assert le._is_dispatch_command({"type": "outcome.received", "payload": {}}) is False
    assert le._is_dispatch_command({"type": "command.issued", "payload": {"command": 1}}) is False
    assert le._is_dispatch_command(
        {"type": "command.issued", "payload": {"command": {"kind": "other"}}}
    ) is False
    journey = [_dispatch(1, "devon", "RED"), _dispatch(3, "devon", "GREEN")]
    nearest = le._nearest_dispatch(journey, 4)
    assert nearest is not None and nearest["seq"] == 3
    assert le._nearest_dispatch(journey, 1) is None


def test_attempt_resolution_order():
    outcome = _outcome(3, "devon", "RED", attempt=4)
    command = _dispatch(2, "devon", "RED", attempt=2)
    assert le._attempt_of(outcome, command) == 4
    outcome_no_attempt = _outcome(3, "devon", "RED")
    assert le._attempt_of(outcome_no_attempt, command) == 2
    plain_command = _dispatch(2, "devon", "RED")
    assert le._attempt_of(outcome_no_attempt, plain_command) == 1
    zero = _outcome(3, "devon", "RED", attempt=0)
    assert le._attempt_of(zero, plain_command) == 1


def test_outcome_items_missing_phase_fails_closed():
    with pytest.raises(ValueError, match="missing Devon dispatch receipts for GREEN,REFACTOR"):
        le._outcome_items_for_role(
            [_outcome(1, "devon", "RED")], "devon", ("RED", "GREEN", "REFACTOR"), "Devon"
        )


def test_outcome_events_filter_by_role_and_phase():
    journey = [
        _outcome(1, "devon", "RED"),
        _outcome(2, "devon", "GREEN"),
        _outcome(3, "prism", "PRISM_RED"),
    ]
    items = le._outcome_events(journey, "devon", ("RED",))
    assert [item["seq"] for item in items] == [1]


def test_validate_outcome_rejects_non_done_status_and_partial_audit():
    le._validate_outcome(_outcome(1, "devon", "RED", status=None, completeness=None))
    with pytest.raises(ValueError, match="status is 'failed'"):
        le._validate_outcome(_outcome(1, "devon", "RED", status="failed"))
    with pytest.raises(ValueError, match="'partial'"):
        le._validate_outcome(_outcome(1, "devon", "RED", completeness="partial"))


def test_bind_agent_io_without_dispatch_command_fails_closed():
    journey = [
        _outcome(1, "devon", "RED"),
        _outcome(2, "devon", "GREEN"),
        _outcome(3, "devon", "REFACTOR"),
        _outcome(4, "prism", "PRISM_RED"),
        _outcome(5, "prism", "PRISM_FINAL"),
    ]
    with pytest.raises(ValueError, match="no dispatch command before seq 1"):
        le._bind_agent_io(journey, CAND, RUN)


def test_receipt_fields_are_content_addressed():
    journey = _happy_journey()
    receipts = le._bind_agent_io(journey, CAND, RUN)
    assert [r.phase for r in receipts] == [
        "RED",
        "GREEN",
        "REFACTOR",
        "PRISM_RED",
        "PRISM_FINAL",
    ]
    first = receipts[0]
    assert first.role == "devon"
    assert first.task_id == "T-001"
    assert first.command_seq == 2 and first.outcome_seq == 3
    assert first.audit_completeness == "complete"
    assert first.input_sha256 == le._canonical_sha256(journey[1])
    assert first.input_ref.endswith(first.input_sha256)
    assert first.output_ref.endswith(first.output_sha256)


# ---------------------------------------------------------------------------
# gate journey
# ---------------------------------------------------------------------------


def test_gate_tokens_and_first_event_wins():
    journey = [
        _gate(1, "red"),  # alias token
        _gate(2, "red_valid"),  # first RED_GATE stays
        _gate(3, "island_2"),
    ]
    gates = le._gate_events(journey)
    assert gates["RED_GATE"]["seq"] == 1
    assert gates["ISLAND_GATE_2"]["seq"] == 3


@pytest.mark.parametrize("missing_check", ["island_gate_2", "task_review"])
def test_validate_gate_journey_missing_gate(missing_check):
    journey = [
        event
        for event in _happy_journey()
        if not (
            event["type"] == "verdict.passed"
            and event["payload"].get("check") == missing_check
        )
    ]
    with pytest.raises(ValueError, match="missing gates"):
        le._validate_gate_journey(journey)


def test_validate_gate_journey_order():
    journey = _happy_journey()
    for event in journey:
        if (
            event["type"] == "verdict.passed"
            and event["payload"]["check"] == "green_gate"
        ):
            event["seq"] = 5
    with pytest.raises(ValueError, match="not in journey order"):
        le._validate_gate_journey(journey)


def test_gate_observations_command_id_fallback():
    journey = _happy_journey()
    for event in journey:
        if event["type"] == "verdict.passed" and event["payload"]["check"] == "red_valid":
            event.pop("command_id")
            event["payload"]["command_id"] = "payload-command"
    gates = le._validate_gate_journey(journey)
    observations = le._gate_observations(gates)
    red = observations[0]
    assert red.command_id == "payload-command"
    assert red.source == "runtime" and red.status == "pass"
    assert observations[4].source == "prism"
    assert observations[5].gate == "ISLAND_GATE_2"


def test_gate_observation_without_any_command_id_is_empty_string():
    journey = _happy_journey()
    for event in journey:
        if event["type"] == "verdict.passed" and event["payload"]["check"] == "red_valid":
            event.pop("command_id")
    gates = le._validate_gate_journey(journey)
    assert le._gate_observations(gates)[0].command_id == ""


# ---------------------------------------------------------------------------
# boundary
# ---------------------------------------------------------------------------


def test_boundary_requires_exit_completed_and_island_ordering():
    journey = _happy_journey()
    le._validated_boundary(journey, island_seq=19)
    cases = [
        (
            [e for e in journey if e["type"] != "stage.exited"],
            "missing stage.exited",
        ),
        (
            [
                {**e, "payload": {"stage": "M-TEST"}}
                if e["type"] == "stage.exited"
                else e
                for e in journey
            ],
            "missing stage.exited",
        ),
        (
            [e for e in journey if e["type"] != "run.completed"],
            "missing run.completed",
        ),
        (
            [
                {**e, "payload": {"terminal_state": "released"}}
                if e["type"] == "run.completed"
                else e
                for e in journey
            ],
            "missing run.completed",
        ),
        (
            [
                {**e, "seq": 22} if e["type"] == "run.completed" else e
                for e in journey
            ],
            "does not immediately follow",
        ),
    ]
    for events, message in cases:
        with pytest.raises(ValueError, match=message):
            le._validated_boundary(events, island_seq=19)
    with pytest.raises(ValueError, match="boundary precedes ISLAND_GATE_2"):
        le._validated_boundary(journey, island_seq=21)


# ---------------------------------------------------------------------------
# lineage
# ---------------------------------------------------------------------------


def test_rgr_lineage_uses_explicit_checkpoints():
    journey = _happy_journey()
    gates = le._validate_gate_journey(journey)
    agent_io = le._bind_agent_io(journey, CAND, RUN)
    lineage = le._rgr_lineage(journey, gates, agent_io)
    assert lineage.red_checkpoint_seq == 12
    assert lineage.green_commit_seq == 13
    assert lineage.task_id == "T-001"
    assert lineage.attempt == 2
    assert lineage.red_ref == "refs/trac/rgr/RUN/T-001/2/red"
    assert lineage.g_sha == "d" * 40
    assert lineage.trailers == TRAILERS


def test_rgr_lineage_falls_back_to_gates_and_receipts():
    journey = [
        e
        for e in _happy_journey()
        if e["type"] not in ("red.checkpointed", "green.committed", "task.started")
    ]
    gates = le._validate_gate_journey(journey)
    agent_io = le._bind_agent_io(journey, CAND, RUN)
    lineage = le._rgr_lineage(journey, gates, agent_io)
    assert lineage.red_checkpoint_seq == gates["RED_GATE"]["seq"]
    assert lineage.green_commit_seq == gates["GREEN_GATE"]["seq"]
    assert lineage.task_id == agent_io[0].task_id
    assert lineage.trailers == {}


def test_rgr_lineage_attempt_from_command_assignment():
    journey = _happy_journey()
    journey = [
        {**e, "payload": {"task_id": "T-001"}} if e["type"] == "task.started" else e
        for e in journey
    ]
    dispatch = next(e for e in journey if e.get("command_id") == "cmd-2")
    dispatch["payload"]["command"]["params"]["assignment"] = {"attempt": 5}
    gates = le._validate_gate_journey(journey)
    agent_io = le._bind_agent_io(journey, CAND, RUN)
    assert le._rgr_lineage(journey, gates, agent_io).attempt == 5


def test_rgr_lineage_red_not_before_green_fails_closed():
    journey = _happy_journey()
    for event in journey:
        if event["type"] == "red.checkpointed":
            event["seq"] = 14
    gates = le._validate_gate_journey(journey)
    agent_io = le._bind_agent_io(journey, CAND, RUN)
    with pytest.raises(ValueError, match="red checkpoint not before green commit"):
        le._rgr_lineage(journey, gates, agent_io)


def test_rgr_lineage_non_dict_trailers_become_empty():
    journey = _happy_journey()
    for event in journey:
        if event["type"] == "green.committed":
            event["payload"]["trailers"] = ["not", "a", "dict"]
    gates = le._validate_gate_journey(journey)
    agent_io = le._bind_agent_io(journey, CAND, RUN)
    assert le._rgr_lineage(journey, gates, agent_io).trailers == {}


# ---------------------------------------------------------------------------
# bind happy path
# ---------------------------------------------------------------------------


def test_bind_live_evidence_happy_path():
    artifact = _artifact()
    bundle = _bind(candidate_artifact=artifact)
    assert bundle.schema_version == SCHEMA_VERSION
    assert bundle.status == "satisfied"
    assert bundle.candidate_sha == CAND
    assert bundle.candidate_artifact is artifact
    assert bundle.run_id == RUN
    assert bundle.backend == "opencode"
    assert bundle.provenance.backend_class == "OpencodeBackend"
    assert bundle.provenance.event_origin == "runtime"
    assert bundle.provenance.fake_backend is False
    assert bundle.required_devon_phases == ("RED", "GREEN", "REFACTOR")
    assert [r.phase for r in bundle.agent_io] == [
        "RED",
        "GREEN",
        "REFACTOR",
        "PRISM_RED",
        "PRISM_FINAL",
    ]
    assert bundle.event_sequence.first_seq == 1
    assert bundle.event_sequence.last_seq == 21
    assert bundle.event_sequence.events_ref == le._blob_ref(
        CAND, RUN, le._canonical_sha256(_happy_journey())
    )
    assert bundle.rgr_lineage.trailers == TRAILERS
    assert [g.gate for g in bundle.gate_observations] == list(le.GATE_NAMES)
    assert bundle.boundary.stage == "M-IMPL"
    assert bundle.boundary.stage_exited_seq == 20
    assert bundle.boundary.run_completed_seq == 21
    assert bundle.boundary.terminal_state == "boundary"


def test_bind_live_evidence_accepts_envelope_like_events():
    dict_journey = _happy_journey()
    envelope_journey = [SimpleNamespace(**event) for event in dict_journey]
    bundle = _bind(journey=envelope_journey)
    assert bundle.event_sequence.last_seq == 21
    assert [r.phase for r in bundle.agent_io][-1] == "PRISM_FINAL"


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------


def test_resolve_ref_rejects_escapes():
    root = Path("/tmp/root")
    assert le._resolve_ref(root, "a/b") == root / "a" / "b"
    for bad in ("", "a\x00b", "/abs/path", "../escape", "a/../../b"):
        with pytest.raises(ValueError, match="IF-LIVE-001"):
            le._resolve_ref(root, bad)


def test_persist_bytes_is_atomic_and_idempotent(tmp_path: Path):
    le._persist_bytes(tmp_path, "nested/ref", b"data")
    assert (tmp_path / "nested" / "ref").read_bytes() == b"data"
    assert not list(tmp_path.rglob("*.tmp"))
    le._persist_bytes(tmp_path, "nested/ref", b"data")
    with pytest.raises(ValueError, match="different bytes"):
        le._persist_bytes(tmp_path, "nested/ref", b"other")


def test_write_live_evidence_persists_bundle_and_blobs(tmp_path: Path):
    bundle = _bind()
    blobs = {"custom/blob": b"payload"}
    ref = le.write_live_evidence(str(tmp_path), bundle, blobs)
    assert ref == f"{EVIDENCE_ROOT}/{CAND}/{RUN}/evidence.json"
    evidence = tmp_path / ref
    assert evidence.is_file()
    raw = evidence.read_bytes()
    assert raw.endswith(b"\n")
    assert json.loads(raw)["run_id"] == RUN
    assert json.loads(raw)["candidate_sha"] == CAND
    assert (tmp_path / "custom" / "blob").read_bytes() == b"payload"
    # identical rewrite is a no-op
    le.write_live_evidence(str(tmp_path), bundle, blobs)
    # same ref, different bytes fails closed
    with pytest.raises(ValueError, match="different bytes"):
        le.write_live_evidence(str(tmp_path), bundle, {"custom/blob": b"changed"})


def test_write_live_evidence_requires_bundle(tmp_path: Path):
    with pytest.raises(ValueError, match="requires a LiveEvidenceBundle"):
        le.write_live_evidence(str(tmp_path), object(), {})
