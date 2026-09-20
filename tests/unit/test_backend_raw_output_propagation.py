"""Unit tests: backend raw-output propagation (IF-ENVELOPE-002 prerequisite).

Both agent backends must hand the Runtime the ACTUAL reply text of a declared
dispatch so the collection face (Executor._format_error_shortcircuit) can
classify it:

- Real backend (effects/opencode.py): attaches the agent's exact original
  final reply text (verbatim, never reconstructed from the normalized
  verdict/payload) at its actual reply return paths; malformed and empty
  replies are preserved for Runtime classification; undeclared results stay
  untouched.
- Fake backend (effects/fake.py): encodes its explicitly simulated actual
  result under the assigned declaration — only actual simulated fields, never
  a coerced synthetic pass; schema-required fields the simulation genuinely
  produced (the DIAGNOSE reason/evidence pair and the M-DESIGN review revise
  fields, marked simulated) are carried, and a simulated outcome the payload
  schema cannot represent (an M-TEST/undeclared revise without findings)
  stays incomplete and the Runtime classifies it visibly.

No live subprocess/opencode calls: the real-backend transport tests drive the
actual extraction/return methods with controlled transcript inputs
(subprocess.CompletedProcess objects).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tracks.effects.fake import FakeBackend
from tracks.effects.opencode import OpencodeBackend
from tracks.executor.executor import Executor
from tracks.kernel.envelope import (
    ENVELOPE_VERSION,
    build_assignment_envelope,
    parse_agent_output,
    validate_diagnose_payload,
)
from tracks.kernel.events import Command
from tracks.store import Store

RUN_ID = "run-envelope-unit-0002"
TASK_ID = "T-9"
CMD_ID = "cmd-envelope-0002"
VERSION = "v0.5.0"


def _declared(kind: str = "prism:review", task_id: str = TASK_ID) -> dict:
    """Exactly what the injection face materializes into a declared dispatch."""
    task = {"task_id": task_id}
    return {"task": task, "envelope": build_assignment_envelope(kind, task)}


def _envelope_reply(
    kind: str = "prism:review",
    version: int = ENVELOPE_VERSION,
    payload: dict | None = None,
) -> str:
    envelope = {
        "envelope": {"kind": kind, "version": version},
        "payload": payload if payload is not None else {"verdict": "pass"},
    }
    return (
        "```tracks-envelope\n" + json.dumps(envelope, sort_keys=True) + "\n```\n"
    )


def _transcript(*texts: str, tail: list | None = None) -> subprocess.CompletedProcess:
    """A controlled opencode NDJSON stdout stream ending in the given text
    events (verbatim), with optional extra non-text events appended last."""
    lines = [json.dumps({"type": "text", "part": {"text": text}}, sort_keys=True)
             for text in texts]
    lines.extend(json.dumps(ev, sort_keys=True) for ev in (tail or []))
    return subprocess.CompletedProcess(
        args=["opencode"],
        returncode=0,
        stdout=("\n".join(lines) + "\n") if lines else "",
        stderr="",
    )


def _reply_payload(raw: str) -> dict:
    """Inspect an encoded reply's payload WITHOUT the kernel schema validator —
    an honest-but-incomplete payload (revise without findings) is
    schema-invalid by design; the Runtime classifies it, these tests only
    verify what the fake actually encoded."""
    assert raw.startswith("```tracks-envelope\n") and raw.endswith("\n```\n")
    envelope = json.loads(raw[len("```tracks-envelope\n") : -len("\n```\n")])
    assert set(envelope) == {"envelope", "payload"}
    assert envelope["envelope"]["version"] == ENVELOPE_VERSION
    return envelope["payload"]


@pytest.fixture()
def backend_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Real Store + Executor (fake backend) over a temp repo."""
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")
    store = Store(tmp_path / "store")
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    ex = Executor(store, repo, RUN_ID)
    try:
        yield ex, store, repo
    finally:
        store.close()


def _cmd() -> Command:
    return Command(kind="dispatch_agent", params={}, command_id=CMD_ID)


def _events_after(store: Store, seq: int) -> list:
    return [ev for ev in store.events(RUN_ID) if ev.seq > seq]


def _assert_single_format_error(store: Store, expected_kind: str) -> None:
    """A declared reply's format error routes: the classified format_error
    event plus the routable verdict.failed (reply_format_error), mirroring
    _assert_format_error_routed in test_declared_envelope_direct.py (commit
    224e51b routed every declared kind; these six call sites are all
    declared assignments and were left asserting the stale single event)."""
    events = list(store.events(RUN_ID))
    kinds = [e.type for e in events]
    assert kinds == ["format_error", "verdict.failed"], kinds
    fe, vf = events
    assert fe.command_id == CMD_ID
    assert fe.task_id == TASK_ID
    assert fe.payload["kind"] == expected_kind
    assert fe.payload["detail"]
    assert vf.command_id == CMD_ID
    assert vf.task_id == TASK_ID
    assert vf.payload["check"] == "reply_format_error"
    assert vf.payload["task_id"] == TASK_ID


# ---------------------------------------------------------------------------
# Fake backend: declared envelope encoding (honest, no synthetic success)
# ---------------------------------------------------------------------------


def test_fake_declared_review_pass_is_valid_and_flows_through(
    backend_env, monkeypatch
):
    ex, store, repo = backend_env
    fake = FakeBackend(repo, VERSION)
    assignment = _declared("prism:review")
    result = fake.act("prism", "PRISM_REVIEW", None, None, assignment=assignment)
    # the fake now hands the Runtime its actual simulated reply text
    assert "raw_output" in result
    parsed = parse_agent_output(result["raw_output"])
    assert parsed["envelope"]["kind"] == "prism:review"
    assert parsed["payload"] == {"verdict": "pass"}
    # Runtime collection: valid declared payload falls through, no format_error
    handled = ex._format_error_shortcircuit(result, _cmd(), TASK_ID, assignment)
    assert handled is False
    assert result["envelope"]["payload"]["verdict"] == "pass"
    assert list(store.events(RUN_ID)) == []


def test_fake_declared_revise_is_encoded_honestly_and_fails_visibly(
    backend_env, monkeypatch
):
    ex, store, repo = backend_env
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", "prism:PRISM_REVIEW=revise")
    fake = FakeBackend(repo, VERSION)
    assignment = _declared("prism:review")
    result = fake.act("prism", "PRISM_REVIEW", None, None, assignment=assignment)
    assert result["verdict"] == "revise"  # actual simulated outcome preserved
    payload = _reply_payload(result["raw_output"])
    # the envelope carries the ACTUAL verdict — never coerced into a pass
    assert payload == {"verdict": "revise"}
    # a revise without simulated findings cannot be represented by the schema:
    # the Runtime classifies it visibly instead of the fake inventing data
    handled = ex._format_error_shortcircuit(result, _cmd(), TASK_ID, assignment)
    assert handled is True
    _assert_single_format_error(store, "schema_violation")


def test_fake_design_review_revise_completes_for_the_respond_loop(
    backend_env, monkeypatch
):
    """flow.md §8.1: the M-DESIGN Prism revise must drive RESPOND, so the
    fake completes it with the schema-required review fields (marked
    simulated; verdict stays revise). The stage signal is the design
    criteria skill the M-DESIGN review assignment carries."""
    ex, store, repo = backend_env
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", "prism:PRISM_REVIEW=revise")
    fake = FakeBackend(repo, VERSION)
    assignment = _declared("prism:review")
    assignment["skills"] = ["tracks-discuz", "tracks-prism-design"]
    assignment["docs"] = ["architecture.md", "interfaces.md", "test-plan.md"]
    result = fake.act("prism", "PRISM_REVIEW", None, None, assignment=assignment)
    assert result["verdict"] == "revise"
    payload = _reply_payload(result["raw_output"])
    assert payload["verdict"] == "revise"
    assert payload["review_summary"]
    assert payload["review_body"]
    assert payload["findings"][0]["severity"] == "blocker"
    parsed = parse_agent_output(result["raw_output"])
    assert parsed["payload"]["findings"][0]["id"] == "FAKE-DESIGN-REVIEW-01"
    # Runtime collection: a schema-valid declared revise falls through.
    handled = ex._format_error_shortcircuit(result, _cmd(), TASK_ID, assignment)
    assert handled is False
    assert list(store.events(RUN_ID)) == []


def test_fake_synthesized_findings_carry_the_simulated_isolation_marker(
    backend_env, monkeypatch
):
    """The synthesized M-DESIGN/VERIFY_FINAL findings are marked ``simulated``
    end to end (result, encoded reply, verdict-facing payload) so anchoring/
    counting consumers can never consume them as real blocker evidence. The
    marker rides only in the fake's own fields — the declared reply schema
    does not require it, and the kernel validator tolerates the extra key."""
    ex, store, repo = backend_env
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", "prism:PRISM_REVIEW=revise")
    fake = FakeBackend(repo, VERSION)
    assignment = _declared("prism:review")
    assignment["skills"] = ["tracks-discuz", "tracks-prism-design"]
    assignment["docs"] = ["architecture.md", "interfaces.md", "test-plan.md"]
    result = fake.act("prism", "PRISM_REVIEW", None, None, assignment=assignment)
    finding = result["findings"][0]
    assert finding["id"] == "FAKE-DESIGN-REVIEW-01"
    assert finding["simulated"] is True
    payload = _reply_payload(result["raw_output"])
    assert payload["findings"][0]["simulated"] is True
    assert parse_agent_output(result["raw_output"])["payload"]["findings"][0][
        "simulated"
    ] is True
    # The verdict-facing helper threads the finding dicts verbatim, marker
    # included (the prism.verdict payload is never a false real blocker).
    verdict_payload: dict = {}
    ex._apply_prism_review_fields(verdict_payload, "revise", result)
    assert verdict_payload["findings"][0]["simulated"] is True


def test_fake_verify_final_revise_is_marked_simulated(backend_env, monkeypatch):
    ex, store, repo = backend_env
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", "prism:VERIFY_FINAL=revise")
    fake = FakeBackend(repo, VERSION)
    assignment = _declared("prism:final")
    assignment["candidate_sha"] = "a" * 40
    result = fake.act("prism", "VERIFY_FINAL", None, None, assignment=assignment)
    assert result["verdict"] == "revise"
    finding = result["findings"][0]
    assert finding["id"] == "FAKE-VERIFY-FINAL-01"
    assert finding["severity"] == "blocker"
    assert finding["simulated"] is True
    # No discussion_refs is synthesized: the anchor judge can never bind the
    # simulated finding to a real thread (see the executor anchor judge).
    assert "discussion_refs" not in result
    payload = _reply_payload(result["raw_output"])
    assert payload["findings"][0]["simulated"] is True


def test_fake_m_test_review_revise_keeps_the_honesty_rule(backend_env, monkeypatch):
    """The other reviewer card (``tracks-prism-test``) is the M-TEST review:
    its bare revise keeps the 32b81c2 honest-incomplete classification, never
    a synthesized design finding."""
    ex, store, repo = backend_env
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", "prism:PRISM_REVIEW=revise")
    fake = FakeBackend(repo, VERSION)
    assignment = _declared("prism:review")
    assignment["skills"] = ["tracks-discuz", "tracks-prism-test"]
    assignment["red_evidence"] = {"status": "valid"}
    result = fake.act("prism", "PRISM_REVIEW", None, None, assignment=assignment)
    assert _reply_payload(result["raw_output"]) == {"verdict": "revise"}
    handled = ex._format_error_shortcircuit(result, _cmd(), TASK_ID, assignment)
    assert handled is True
    _assert_single_format_error(store, "schema_violation")


def test_fake_declared_diagnose_label_is_a_visible_failure(backend_env, monkeypatch):
    ex, store, repo = backend_env
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", "diagnose:classification=test_defect")
    fake = FakeBackend(repo, VERSION)
    assignment = _declared("prism:diagnose")
    result = fake.act("prism", "DIAGNOSE", None, None, assignment=assignment)
    assert result["verdict"] == "test_defect"
    # the declared prism:diagnose schema (kernel/envelope
    # validate_diagnose_payload) requires classification + non-empty reason +
    # non-empty evidence; the simulated label carries explicitly simulated
    # reason/evidence so the DIAGNOSE verdict lands as a real declared reply
    # instead of stranding the loop as a schema_violation format_error
    # (package 5 / 792f70e). The simulation marker keeps the fake provenance
    # visible — never a real forensic package.
    payload = _reply_payload(result["raw_output"])
    assert payload["classification"] == "test_defect"
    assert payload["reason"]
    assert payload["evidence"]
    assert validate_diagnose_payload(payload) is None
    parsed = parse_agent_output(result["raw_output"])
    assert parsed["envelope"]["kind"] == "prism:diagnose"
    # Runtime collection: a schema-valid declared payload falls through, no
    # format_error
    handled = ex._format_error_shortcircuit(result, _cmd(), TASK_ID, assignment)
    assert handled is False
    assert result["envelope"]["payload"]["classification"] == "test_defect"
    assert list(store.events(RUN_ID)) == []


def test_fake_declared_kind_mismatch_is_not_encoded(backend_env):
    ex, store, repo = backend_env
    fake = FakeBackend(repo, VERSION)
    # declaration pins prism:plan but the dispatch speaks a PRISM_REVIEW reply
    assignment = _declared("prism:plan")
    result = fake.act("prism", "PRISM_REVIEW", None, None, assignment=assignment)
    assert "raw_output" not in result  # cannot be honestly encoded -> absent
    handled = ex._format_error_shortcircuit(result, _cmd(), TASK_ID, assignment)
    assert handled is True
    _assert_single_format_error(store, "malformed_json")


def test_fake_undeclared_result_untouched(backend_env):
    ex, store, repo = backend_env
    fake = FakeBackend(repo, VERSION)
    for assignment in (None, {"task": {"task_id": TASK_ID}}, {"envelope": None}):
        result = fake.act(
            "prism", "PRISM_REVIEW", None, None, assignment=assignment
        )
        assert "raw_output" not in result
        handled = ex._format_error_shortcircuit(
            result, _cmd(), TASK_ID, assignment
        )
        assert handled is False
    assert list(store.events(RUN_ID)) == []


def test_fake_envelope_version_is_a_reviewed_independent_declaration():
    # The backend declares the contract version it actually implements. The
    # value equals today's kernel authority, but the declaration is an
    # independent reviewed literal in the backend class (NOT an alias of
    # ENVELOPE_VERSION): a stale implementation keeps its old literal and the
    # pre-dispatch parity gate sees the mismatch against the Runtime authority.
    assert FakeBackend.envelope_version == ENVELOPE_VERSION


def test_fake_envelope_version_participates_in_parity_gate(
    backend_env, monkeypatch
):
    ex, store, repo = backend_env
    assignment = _declared("prism:review")
    # consistent: backend declares v2 == Runtime authority v2. The static
    # gate of a declared dispatch never emits the success event — the single
    # dispatch.parity success lands only after the complete gate passed
    # (_emit_dispatch_parity_success).
    assert ex._dispatch_parity_ok(_cmd(), TASK_ID, assignment) is True
    assert not any(
        ev.type in ("dispatch.parity", "dispatch.rejected")
        for ev in store.events(RUN_ID)
    )
    # the post-collection success path emits the full audit event once the
    # declared dispatch passed the complete gate
    fake = FakeBackend(repo, VERSION)
    result = fake.act("prism", "PRISM_REVIEW", None, None, assignment=assignment)
    ex._emit_dispatch_parity_success(result, _cmd(), TASK_ID, assignment)
    parity = [ev for ev in store.events(RUN_ID) if ev.type == "dispatch.parity"]
    assert len(parity) == 1
    assert set(parity[0].payload["referenced"]) == {
        "assignment",
        "backend_fake",
        "backend_real",
        "validator",
    }
    # stale backend declaration: fail closed BEFORE backend act
    seq = max(ev.seq for ev in store.events(RUN_ID))
    monkeypatch.setattr(FakeBackend, "envelope_version", 3)
    assert ex._dispatch_parity_ok(_cmd(), TASK_ID, assignment) is False
    rejected = [ev for ev in _events_after(store, seq) if ev.type == "dispatch.rejected"]
    assert len(rejected) == 1
    assert rejected[0].payload["reason"] == "version_parity_mismatch"
    # a rejected dispatch never produces a dispatch.parity success record
    assert not [
        ev for ev in _events_after(store, seq) if ev.type == "dispatch.parity"
    ]


# ---------------------------------------------------------------------------
# Real backend: verbatim raw_output propagation from controlled transcripts
# ---------------------------------------------------------------------------


def test_real_attaches_exact_original_final_reply_text(backend_env):
    _, _, repo = backend_env
    be = OpencodeBackend(repo, VERSION)
    original = "final word:\n\n" + _envelope_reply() + "trailing prose"
    proc = _transcript("earlier turn text", original)
    assignment = _declared("prism:review")
    result = be._attach_raw_output({}, proc, assignment)
    # byte-identical to the actual final reply — never re-serialized, never
    # reconstructed from the normalized payload
    assert result["raw_output"] == original
    assert "earlier turn text" not in result["raw_output"]


def test_real_preserves_malformed_envelope_reply_for_runtime_classification(
    backend_env,
):
    ex, store, repo = backend_env
    be = OpencodeBackend(repo, VERSION)
    malformed = "```tracks-envelope\n" + json.dumps(
        {
            "envelope": {"kind": "prism:review", "version": ENVELOPE_VERSION},
            "payload": {"verdict": "pass"},
        },
        sort_keys=True,
    )  # no closing fence, no newline
    proc = _transcript(malformed)
    assignment = _declared("prism:review")
    result = be._attach_raw_output({}, proc, assignment)
    assert result["raw_output"] == malformed  # preserved exactly
    handled = ex._format_error_shortcircuit(result, _cmd(), TASK_ID, assignment)
    assert handled is True
    _assert_single_format_error(store, "malformed_json")


def test_real_preserves_empty_reply_for_runtime_classification(backend_env):
    ex, store, repo = backend_env
    be = OpencodeBackend(repo, VERSION)
    proc = _transcript("")
    assignment = _declared("prism:review")
    result = be._attach_raw_output({}, proc, assignment)
    assert result["raw_output"] == ""  # empty reply preserved, not dropped
    handled = ex._format_error_shortcircuit(result, _cmd(), TASK_ID, assignment)
    assert handled is True
    _assert_single_format_error(store, "no_envelope_block")


def test_real_missing_text_event_leaves_raw_output_absent(backend_env):
    ex, store, repo = backend_env
    be = OpencodeBackend(repo, VERSION)
    proc = _transcript(tail=[{"type": "step_finish", "part": {"reason": "stop"}}])
    assignment = _declared("prism:review")
    result = be._attach_raw_output({}, proc, assignment)
    assert "raw_output" not in result  # no actual reply text exists
    handled = ex._format_error_shortcircuit(result, _cmd(), TASK_ID, assignment)
    assert handled is True
    _assert_single_format_error(store, "malformed_json")


def test_real_undeclared_result_untouched(backend_env):
    _, _, repo = backend_env
    be = OpencodeBackend(repo, VERSION)
    proc = _transcript(_envelope_reply())
    for assignment in (None, {"task": {"task_id": TASK_ID}}):
        result = be._attach_raw_output({}, proc, assignment)
        assert "raw_output" not in result
    # declared from the backend's point of view, but the result already carries
    # a raw_output: never clobbered
    result = be._attach_raw_output(
        {"raw_output": "already set"}, proc, _declared("prism:review")
    )
    assert result["raw_output"] == "already set"


def test_real_envelope_version_is_a_reviewed_independent_declaration():
    assert OpencodeBackend.envelope_version == ENVELOPE_VERSION


# ---------------------------------------------------------------------------
# Fake backend: writer/authority kinds (FR-0278-01)
# ---------------------------------------------------------------------------


def _devon_assignment(phase: str) -> dict:
    assignment = {
        "task_id": TASK_ID,
        "phase": phase,
        "if_ids": ["IF-IMPL-001"],
        "ac_refs": ["AC-FR0001-01"],
        "test_refs": ["tests/unit/test_widget.py::test_widget"],
        "commands": [".venv/bin/python -m pytest -n4 tests/unit/test_widget.py"],
        "manifest": {
            "allowed_paths": ["tracks/impl/widget.py", "tests/unit/test_widget.py"],
            "forbidden_paths": ["tests/integration/**"],
        },
        "pre_dirty_snapshot": {},
        "result_identity": "result-100",
    }
    if phase in ("green", "refactor"):
        assignment["r_tree_identity"] = "r-tree-100"
    assignment["envelope"] = build_assignment_envelope(f"devon:{phase}")
    return assignment


@pytest.mark.parametrize("phase", ["red", "green", "refactor"])
def test_fake_declared_devon_phase_encodes_schema_complete_evidence(
    backend_env, phase
):
    ex, store, repo = backend_env
    fake = FakeBackend(repo, VERSION)
    assignment = _devon_assignment(phase)
    result = fake.act("devon", phase.upper(), None, None, assignment=assignment)
    assert result["status"] == "done"
    parsed = parse_agent_output(result["raw_output"])
    assert parsed["envelope"]["kind"] == f"devon:{phase}"
    payload = parsed["payload"]
    assert payload["phase"] == phase
    assert payload["simulated"] is True
    assert payload["manifest_compliance"] is True
    assert payload["commands"] and payload["commands"][0]["cmd"]
    # Runtime collection: a schema-valid declared reply never format_errors.
    handled = ex._format_error_shortcircuit(result, _cmd(), TASK_ID, assignment)
    assert handled is False
    assert list(store.events(RUN_ID)) == []


def test_fake_declared_shield_write_encodes_manifest_contract(backend_env):
    ex, store, repo = backend_env
    fake = FakeBackend(repo, VERSION)
    assignment = _declared("shield:write")
    assignment["test_tasks"] = [
        {"ac_id": "AC-FR0001-01", "layers": ["integration"], "if_ids": ["IF-TEST-001"]}
    ]
    result = fake.act("shield", "WRITE", None, None, assignment=assignment)
    assert result["status"] == "done"
    parsed = parse_agent_output(result["raw_output"])
    assert parsed["envelope"]["kind"] == "shield:write"
    payload = parsed["payload"]
    include = payload["artifact_manifest"]["include"]
    assert include and payload["suggested_commit_message"]
    assert payload["simulated"] is True
    for entry in include:
        for field in ("path", "kind", "role"):
            assert entry[field]
    handled = ex._format_error_shortcircuit(result, _cmd(), TASK_ID, assignment)
    assert handled is False
    assert list(store.events(RUN_ID)) == []


def test_fake_declared_shield_noop_reports_no_target_diff(backend_env):
    """A declared WRITE reply cannot represent done-with-nothing-written: the
    fake reports the simulated exit-gate failure instead of a schema-invalid
    done reply. The undeclared channel keeps the legacy empty-manifest done
    result untouched."""
    ex, store, repo = backend_env
    fake = FakeBackend(repo, VERSION)
    tasks = [
        {"ac_id": "AC-FR0001-01", "layers": ["integration"], "if_ids": ["IF-TEST-001"]}
    ]
    first = fake.act(
        "shield", "WRITE", None, None, assignment=_declared("shield:write") | {"test_tasks": tasks}
    )
    assert first["status"] == "done"
    # Re-dispatch rewrites byte-identical files: no attributable diff.
    assignment = _declared("shield:write")
    assignment["test_tasks"] = tasks
    second = fake.act("shield", "WRITE", None, None, assignment=assignment)
    assert second["status"] == "failed"
    assert second["failure_class"] == "no_target_diff"
    handled = ex._format_error_shortcircuit(second, _cmd(), TASK_ID, assignment)
    assert handled is False  # failure classification preserved, no format_error
    assert list(store.events(RUN_ID)) == []

    # Undeclared: the legacy empty-manifest done result is unchanged.
    legacy = fake.act(
        "shield", "WRITE", None, None, assignment={"test_tasks": tasks}
    )
    assert legacy["status"] == "done"
    assert legacy.get("artifact_manifest", {}).get("include") == []


def _planning_docs(repo: Path, version: str = VERSION) -> None:
    from tracks import paths

    vdir = paths.version_dir(paths.tracks_home(repo), version)
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "acceptance.md").write_text(
        "# 验收\n\n## FR-0001 测试需求\n\n### AC-FR0001-01\n\n  - 可在系统外断言\n",
        encoding="utf-8",
    )
    (vdir / "interfaces.md").write_text(
        "# 接口\n\n## 5. IF Registry\n\n### IF-TEST-001 测试合同\n\n- **合同**：x。\n",
        encoding="utf-8",
    )


def test_fake_declared_archer_planning_encodes_task_graph(backend_env):
    ex, store, repo = backend_env
    _planning_docs(repo)
    fake = FakeBackend(repo, VERSION)
    assignment = _declared("archer:planning")
    result = fake.act("archer", "PLANNING", None, None, assignment=assignment)
    assert result["status"] == "done"
    parsed = parse_agent_output(result["raw_output"])
    assert parsed["envelope"]["kind"] == "archer:planning"
    tasks = parsed["payload"]["tasks"]
    assert tasks and all(task["task_id"] for task in tasks)
    assert parsed["payload"]["simulated"] is True
    handled = ex._format_error_shortcircuit(result, _cmd(), TASK_ID, assignment)
    assert handled is False
    assert list(store.events(RUN_ID)) == []


def test_fake_declared_archer_ruling_encodes_paired_delta(backend_env):
    ex, store, repo = backend_env
    fake = FakeBackend(repo, VERSION)
    assignment = _declared("archer:ruling")
    result = fake.act("archer", "RULING", None, None, assignment=assignment)
    assert result["status"] == "done"
    parsed = parse_agent_output(result["raw_output"])
    assert parsed["envelope"]["kind"] == "archer:ruling"
    payload = parsed["payload"]
    assert payload["devon_side"] and payload["shield_side"]
    assert payload["ordering"] == "devon_then_shield"
    assert payload["simulated"] is True
    handled = ex._format_error_shortcircuit(result, _cmd(), TASK_ID, assignment)
    assert handled is False
    assert list(store.events(RUN_ID)) == []


# ---------------------------------------------------------------------------
# Real backend: declared writer replies feed the legacy extraction faces
# ---------------------------------------------------------------------------


def test_real_devon_envelope_reply_is_unwrapped_into_evidence(backend_env):
    from tracks.effects.devon_evidence import extract_devon_evidence

    payload = {
        "phase": "red",
        "changed_paths": ["tests/unit/test_widget.py"],
        "commands": [
            {"cmd": "pytest", "result": "fail", "output_summary": "assertion_failure"}
        ],
        "manifest_compliance": True,
        "pre_identity": "pre",
        "post_identity": "post",
        "implemented_if_ids": ["IF-IMPL-001"],
    }
    text = _envelope_reply("devon:red", payload=payload)
    event = {"type": "text", "part": {"text": text}}
    evidence = extract_devon_evidence(
        None, lambda _proc: event, lambda _text: None
    )
    assert evidence["phase"] == "red"
    assert evidence["changed_paths"] == ["tests/unit/test_widget.py"]


def test_real_shield_envelope_reply_is_unwrapped_into_manifest(backend_env):
    _, _, repo = backend_env
    be = OpencodeBackend(repo, VERSION)
    payload = {
        "artifact_manifest": {
            "include": [
                {
                    "path": "tests/integration/test_widget.py",
                    "kind": "integration_test",
                    "role": "test",
                }
            ]
        },
        "suggested_commit_message": "M-TEST: add tests",
    }
    text = _envelope_reply("shield:write", payload=payload)
    text_event = {"type": "text", "part": {"text": text}}
    unwrapped, error = be._manifest_payload(text_event)
    assert error is None
    assert unwrapped["suggested_commit_message"] == "M-TEST: add tests"
    manifest, commit, error = be._extract_manifest(_transcript(text))
    assert error is None
    assert commit == "M-TEST: add tests"
    assert manifest["include"] == [
        {
            "path": "tests/integration/test_widget.py",
            "kind": "integration_test",
            "role": "test",
        }
    ]


# ---------------------------------------------------------------------------
# #174 wiring gap fix: the result FILE payload feeds the Devon evidence face
# ---------------------------------------------------------------------------

_GREEN = {
    "phase": "green",
    "changed_paths": ["tracks/supervisor/readiness.py"],
    "commands": [{"cmd": "pytest", "result": "pass", "output_summary": "29 passed"}],
    "results": [],
    "manifest_compliance": True,
    "pre_identity": "pre",
    "post_identity": "post",
    "r_identity": "r-1",
    "implemented_if_ids": ["IF-PROJ-001"],
}


def _write_result_file(path, payload, kind="devon:green"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"envelope": {"kind": kind, "version": 2}, "payload": payload}),
        encoding="utf-8",
    )


def test_devon_evidence_prefers_the_result_file_payload(tmp_path):
    """Live defect (run 01M2QTJB T-002, 2026-09-20): Devon wrote a complete
    devon:green envelope to the inbox file and replied with a one-line
    pointer; the extractor read only the reply text -> every evidence field
    "missing" -> evidence_malformed twice -> wrongful S3 contract-simplification
    RULING. The file payload is now the FIRST source."""
    from tracks.effects.devon_evidence import extract_devon_evidence

    path = tmp_path / "inbox" / "cmd-x.json"
    _write_result_file(path, _GREEN)
    event = {"type": "text", "part": {"text": "result: " + str(path)}}
    evidence = extract_devon_evidence(
        None, lambda _proc: event, lambda _text: None, result_path=str(path)
    )
    assert evidence["phase"] == "green"
    assert evidence["changed_paths"] == ["tracks/supervisor/readiness.py"]
    assert evidence["manifest_compliance"] is True
    assert evidence["r_identity"] == "r-1"


def test_devon_evidence_file_wins_over_stale_text_payload(tmp_path):
    """Both sources present: the machine-serialized file is authoritative."""
    from tracks.effects.devon_evidence import extract_devon_evidence

    path = tmp_path / "r.json"
    _write_result_file(path, _GREEN)
    stale = {
        "phase": "red",
        "changed_paths": ["tests/unit/test_widget.py"],
        "commands": [{"cmd": "pytest", "result": "fail", "output_summary": "x"}],
        "results": [{"classification": "assertion_failure"}],
        "manifest_compliance": True,
        "pre_identity": "pre",
        "post_identity": "post",
        "implemented_if_ids": ["IF-IMPL-001"],
    }
    text = "```tracks-envelope\n" + json.dumps(
        {"envelope": {"kind": "devon:red", "version": 2}, "payload": stale}
    ) + "\n```\n"
    event = {"type": "text", "part": {"text": text}}
    evidence = extract_devon_evidence(
        None, lambda _proc: event, lambda _text: None, result_path=str(path)
    )
    assert evidence["phase"] == "green"
    assert evidence["changed_paths"] == ["tracks/supervisor/readiness.py"]


def test_devon_evidence_schema_invalid_file_falls_back_to_text(tmp_path):
    """Prism #174b R1: acceptance mirrors the collection face — a parseable
    but SCHEMA-INVALID file (green without r_identity) never feeds evidence;
    the valid fenced text path takes over (no split verdicts)."""
    from tracks.effects.devon_evidence import extract_devon_evidence

    invalid = {k: v for k, v in _GREEN.items() if k != "r_identity"}
    path = tmp_path / "invalid.json"
    _write_result_file(path, invalid)
    valid_text = {
        "phase": "red",
        "changed_paths": ["tests/unit/test_widget.py"],
        "commands": [{"cmd": "pytest", "result": "fail", "output_summary": "x"}],
        "results": [{"classification": "assertion_failure"}],
        "manifest_compliance": True,
        "pre_identity": "pre",
        "post_identity": "post",
        "implemented_if_ids": ["IF-IMPL-001"],
    }
    text = "```tracks-envelope\n" + json.dumps(
        {"envelope": {"kind": "devon:red", "version": 2}, "payload": valid_text}
    ) + "\n```\n"
    event = {"type": "text", "part": {"text": text}}
    evidence = extract_devon_evidence(
        None, lambda _proc: event, lambda _text: None, result_path=str(path)
    )
    assert evidence["phase"] == "red"


def test_devon_evidence_bad_file_falls_back_to_text(tmp_path):
    """Unparsable/missing file: the text paths keep working (fail-closed to
    the legacy extraction; the collection face reports the file failure)."""
    from tracks.effects.devon_evidence import extract_devon_evidence

    bad = tmp_path / "not-json"
    bad.write_text("{ nope", encoding="utf-8")
    valid_text = {
        "phase": "red",
        "changed_paths": ["tests/unit/test_widget.py"],
        "commands": [{"cmd": "pytest", "result": "fail", "output_summary": "x"}],
        "results": [{"classification": "assertion_failure"}],
        "manifest_compliance": True,
        "pre_identity": "pre",
        "post_identity": "post",
        "implemented_if_ids": ["IF-IMPL-001"],
    }
    text = "```tracks-envelope\n" + json.dumps(
        {"envelope": {"kind": "devon:red", "version": 2}, "payload": valid_text}
    ) + "\n```\n"
    event = {"type": "text", "part": {"text": text}}
    evidence = extract_devon_evidence(
        None, lambda _proc: event, lambda _text: None, result_path=str(bad)
    )
    assert evidence["phase"] == "red"
    evidence2 = extract_devon_evidence(
        None, lambda _proc: event, lambda _text: None, result_path=str(tmp_path / "absent.json")
    )
    assert evidence2["phase"] == "red"


def test_devon_evidence_file_without_text_event(tmp_path):
    """File delivered but no final text event at all: the file still carries
    the evidence (the pointer reply is optional prose)."""
    from tracks.effects.devon_evidence import extract_devon_evidence

    path = tmp_path / "r.json"
    _write_result_file(path, _GREEN)
    evidence = extract_devon_evidence(
        None, lambda _proc: None, lambda _text: None, result_path=str(path)
    )
    assert evidence["phase"] == "green"


def test_declared_result_path_helper_shapes():
    from tracks.effects.envelope_reply import declared_result_path

    assert declared_result_path(None) is None
    assert declared_result_path({}) is None
    assert declared_result_path({"result_file": "junk"}) is None
    assert declared_result_path({"result_file": {"result_path": ""}}) is None
    assert declared_result_path({"result_file": {"result_path": "/x/y.json"}}) == "/x/y.json"


def test_success_result_hydrates_evidence_from_declared_result_file(backend_env, tmp_path):
    """Prism #174b R1 (major): the WIRING — act -> _audited_result ->
    _success_result threads assignment.result_file.result_path into the
    extractor. A declared assignment with a ready result file and a
    pointer-only reply produces an outcome carrying the file's evidence
    (the exact live shape that burned twice as evidence_malformed)."""
    _, _, repo = backend_env
    be = OpencodeBackend(repo, VERSION)
    path = tmp_path / "inbox" / "cmd-live.json"
    _write_result_file(path, _GREEN)
    pointer_line = json.dumps(
        {"type": "text", "part": {"text": "result: " + str(path)}}
    )
    proc = SimpleNamespace(stdout=pointer_line + "\n", stderr="", returncode=0)
    result = be._success_result(
        "Devon",
        "GREEN",
        [],
        None,
        proc,
        "",
        None,
        author_assignment=False,
        reviewer_assignment=False,
        result_path=str(path),
    )
    assert result["phase"] == "green"
    assert result["changed_paths"] == ["tracks/supervisor/readiness.py"]
    assert result["r_identity"] == "r-1"
