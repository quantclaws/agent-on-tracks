"""Unit tests for the D-35 (SC-D35 §2.2) structured review payload contract.

Covers extraction/validation of the Prism review JSON (M-TEST/M-IMPL
channel), the machine-side review_ref threading into last_failure, and the
publish-side field threading that previously dropped findings (B23: the
verdict event carried no review fields, leaving revise re-dispatch evidence
empty — live run 01M0AMKV PRISM rounds 1-3, 2026-08-18).
"""

from __future__ import annotations

import json
import subprocess

from tracks.effects.opencode import OpencodeBackend
from tracks.kernel.envelope import REVIEW_SUMMARY_MAX
from tracks.kernel.events import EventEnvelope
from tracks.kernel.machine import State, apply


def _patch_load_contract(monkeypatch, fake):
    """Patch every executor module that resolved ``load_contract`` at import."""
    import importlib

    for name in (
        "executor",
        "doc_face",
        "run_loop",
        "test_collect",
        "test_execute",
        "phase0_face",
        "verify_gates",
    ):
        try:
            module = importlib.import_module(f"tracks.executor.{name}")
        except ModuleNotFoundError:
            continue
        if hasattr(module, "load_contract"):
            monkeypatch.setattr(module, "load_contract", fake)


def _proc(*texts: str) -> subprocess.CompletedProcess:
    lines = "".join(
        json.dumps({"type": "text", "part": {"text": t}}) + "\n" for t in texts
    )
    return subprocess.CompletedProcess(["opencode"], 0, stdout=lines, stderr="")


def _finding(**overrides) -> dict:
    base = {
        "id": "PRISM-V06-R1-01",
        "severity": "blocker",
        "defect_classification": "test_defect",
        "criterion": "1+2",
        "artifact": "tests/integration/test_x.py:40-43",
        "ac_refs": ["AC-FR0245-01"],
        "summary": "断言降级",
    }
    base.update(overrides)
    return base


def _revise_payload(**overrides) -> dict:
    base = {
        "verdict": "revise",
        "defect_classification": "test_defect",
        "review_summary": "三条断言质量缺陷",
        "findings": [_finding()],
        "review_body": "## 完整评审正文\n...",
    }
    base.update(overrides)
    return base


# -- extraction / validation -------------------------------------------


def test_valid_revise_payload_extracts() -> None:
    payload, error = OpencodeBackend._prism_review_payload_from(
        _proc("分析正文", json.dumps(_revise_payload(), ensure_ascii=False))
    )
    assert error is None
    assert payload is not None
    assert payload["verdict"] == "revise"
    assert payload["findings"][0]["id"] == "PRISM-V06-R1-01"


def test_no_json_is_legal_absence() -> None:
    payload, error = OpencodeBackend._prism_review_payload_from(
        _proc("纯散文回复，无 JSON")
    )
    assert payload is None
    assert error is None


def test_bad_verdict_rejected() -> None:
    payload, error = OpencodeBackend._prism_review_payload_from(
        _proc(json.dumps({"verdict": "maybe"}))
    )
    assert payload is None
    assert "verdict" in error


def test_revise_without_summary_derives_from_first_finding() -> None:
    """S3 ruling (round 17, 2026-09-06): a missing aggregated summary no
    longer rejects a substantively valid review -- it derives from
    findings[0].summary deterministically."""
    p = _revise_payload()
    del p["review_summary"]
    payload, error = OpencodeBackend._prism_review_payload_from(_proc(json.dumps(p)))
    assert error is None
    assert payload["review_summary"] == p["findings"][0]["summary"]


def test_revise_summary_cap() -> None:
    p = _revise_payload(review_summary="x" * (REVIEW_SUMMARY_MAX + 1))
    _, error = OpencodeBackend._prism_review_payload_from(_proc(json.dumps(p)))
    assert str(REVIEW_SUMMARY_MAX) in error


def test_revise_requires_findings() -> None:
    p = _revise_payload(findings=[])
    _, error = OpencodeBackend._prism_review_payload_from(_proc(json.dumps(p)))
    assert "findings" in error


def test_finding_missing_field_rejected() -> None:
    bad = _finding()
    del bad["ac_refs"]
    p = _revise_payload(findings=[bad])
    _, error = OpencodeBackend._prism_review_payload_from(_proc(json.dumps(p)))
    assert "ac_refs" in error


def test_finding_summary_cap() -> None:
    p = _revise_payload(findings=[_finding(summary="y" * (REVIEW_SUMMARY_MAX + 1))])
    _, error = OpencodeBackend._prism_review_payload_from(_proc(json.dumps(p)))
    assert str(REVIEW_SUMMARY_MAX) in error


def test_revise_requires_review_body() -> None:
    p = _revise_payload()
    del p["review_body"]
    _, error = OpencodeBackend._prism_review_payload_from(_proc(json.dumps(p)))
    assert "review_body" in error


def test_pass_payload_needs_no_findings() -> None:
    payload, error = OpencodeBackend._prism_review_payload_from(
        _proc(json.dumps({"verdict": "pass"}))
    )
    assert error is None
    assert payload is not None
    assert payload["verdict"] == "pass"


# -- machine: review_ref into last_failure (AC-03) ----------------------


def _prism_event(payload: dict) -> EventEnvelope:
    return EventEnvelope(
        seq=1,
        ts="2026-08-19T00:00:00+00:00",
        run_id="r",
        version="v0.6",
        type="prism.verdict",
        schema_version=1,
        command_id=None,
        task_id=None,
        payload=payload,
    )


def test_machine_last_failure_carries_review_ref() -> None:
    s = State()
    s.stage = "M-TEST"
    s.substate = "PRISM_REVIEW"
    apply(
        s,
        _prism_event(
            {
                "verdict": "revise",
                "review_summary": "缺陷摘要",
                "findings": [_finding()],
                "review_ref": "deadbeef",
            }
        ),
    )
    assert s.last_failure is not None
    assert s.last_failure["review_ref"] == "deadbeef"
    assert s.last_failure["reason"] == "缺陷摘要"
    assert s.last_failure["evidence"] == [_finding()]


def test_machine_old_event_without_ref_replays_clean() -> None:
    s = State()
    s.stage = "M-TEST"
    s.substate = "PRISM_REVIEW"
    apply(s, _prism_event({"verdict": "revise", "defect_classification": None}))
    assert s.last_failure is not None
    assert "review_ref" not in s.last_failure


# -- executor _emit_verdict threading (M-IMPL path, B23 mirror) ---------


def test_emit_verdict_threads_structured_fields_and_blob(monkeypatch, tmp_path):
    """The M-IMPL legacy emit path must thread review fields + blob ref,
    or Devon/Archer revise re-dispatches carry no findings (D-35 §2.3)."""
    from tracks.executor.executor import Executor
    from tracks.store import Store

    store = Store(tmp_path)
    emitted = []
    monkeypatch.setattr(
        Executor,
        "_emit",
        lambda self, ev, payload, **kw: emitted.append({"type": ev, "payload": payload}),
    )
    ex = object.__new__(Executor)
    ex.store = store

    class _State:
        stage = "M-IMPL"
        review_round = 1

    class _Cmd:
        command_id = "c1"

    body = "## review body\nfull text"
    ex._emit_verdict(
        role="prism",
        verdict="revise",
        result={
            "diff_ref": None,
            "criteria_pack": {"name": "tracks-prism-impl", "version": "0.1"},
            "review_summary": "范围断言不足",
            "findings": [_finding()],
            "review_body": body,
        },
        state=_State(),
        p={},
        cmd=_Cmd(),
        task_id=None,
    )
    prism_events = [e for e in emitted if e["type"] == "prism.verdict"]
    assert prism_events, emitted
    payload = prism_events[0]["payload"]
    assert payload["review_summary"] == "范围断言不足"
    assert payload["findings"] == [_finding()]
    assert payload["review_ref"]
    assert "review_body" not in payload
    from tracks import paths

    blob_path = paths.blobs_dir(store.home) / payload["review_ref"]
    assert blob_path.exists()
    assert body in blob_path.read_text(encoding="utf-8")


# -- PRISM-D35-R1 fixes: requires_diff exemption, merge guard, publish ----


def test_m_test_prism_payload_requires_diff_off_for_structured_revise(
    monkeypatch, tmp_path
):
    """PRISM-D35-R1-02: a JSON-carried revise must not be hard-failed by the
    reviewer no-diff policy — requires_diff yields to structured findings."""
    from tracks.executor.executor import Executor
    from tracks.store import Store

    store = Store(tmp_path)
    monkeypatch.setattr(
        Executor, "_doc_path", lambda self, doc: tmp_path / doc, raising=True
    )
    ex = object.__new__(Executor)
    ex.store = store
    result = {
        "verdict": "revise",
        "review_summary": "缺陷",
        "findings": [_finding()],
        "review_body": "正文",
    }
    payload = ex._m_test_prism_payload(result, base_sha="x", result_id="r1")
    assert payload["requires_diff"] is False
    assert payload["domain_event"]["payload"]["review_ref"]

    result_threaded_only = {"verdict": "revise"}  # doc-anchored, no JSON
    payload2 = ex._m_test_prism_payload(result_threaded_only, "x", "r2")
    assert payload2["requires_diff"] is True


def test_merge_pass_does_not_override_open_thread_revise(tmp_path):
    """PRISM-D35-R1-ADV4: JSON pass while the reviewer's own threads are open
    keeps the derived revise (threads must be closed first)."""
    from tracks.effects.opencode import OpencodeBackend

    backend = OpencodeBackend(repo=tmp_path, version="v0.6")
    payload = json.dumps({"verdict": "pass"})
    result = {"status": "done", "verdict": "revise"}
    merged = backend._merge_review_payload(
        result,
        role="prism",
        substate="PRISM_REVIEW",
        assignment={"stage": "M-TEST"},
        proc=_proc(payload),
        prompt="p",
        console_input=None,
    )
    assert merged["verdict"] == "revise"

    result_ready = {"status": "done", "verdict": "pass"}
    merged2 = backend._merge_review_payload(
        result_ready,
        role="prism",
        substate="PRISM_REVIEW",
        assignment={"stage": "M-TEST"},
        proc=_proc(payload),
        prompt="p",
        console_input=None,
    )
    assert merged2["verdict"] == "pass"


def test_merge_derived_revise_without_json_fails(tmp_path):
    from tracks.effects.opencode import OpencodeBackend

    backend = OpencodeBackend(repo=tmp_path, version="v0.6")
    merged = backend._merge_review_payload(
        {"status": "done", "verdict": "revise"},
        role="prism",
        substate="PRISM_REVIEW",
        assignment={"stage": "M-TEST"},
        proc=_proc("纯散文"),
        prompt="p",
        console_input=None,
    )
    assert merged["status"] == "failed"
    assert merged["failure_class"] == "manifest_malformed"


# -- PRISM-D35-R2 fixes: dc threading, M-DESIGN passthrough coverage -----


def test_emit_verdict_threads_defect_classification(monkeypatch, tmp_path):
    """PRISM-D35-R2-01: the M-IMPL emit path must carry defect_classification
    or every PRISM_PLAN/FINAL revise falls to the default route."""
    from tracks.executor.executor import Executor
    from tracks.store import Store

    store = Store(tmp_path)
    emitted = []
    monkeypatch.setattr(
        Executor,
        "_emit",
        lambda self, ev, payload, **kw: emitted.append({"type": ev, "payload": payload}),
    )
    ex = object.__new__(Executor)
    ex.store = store

    class _State:
        stage = "M-IMPL"
        review_round = 1

    class _Cmd:
        command_id = "c1"

    ex._emit_verdict(
        role="prism",
        verdict="revise",
        result={
            "diff_ref": None,
            "criteria_pack": None,
            "defect_classification": "red_defect",
            "review_summary": "范围断言不足",
            "findings": [_finding()],
            "review_body": "正文",
        },
        state=_State(),
        p={},
        cmd=_Cmd(),
        task_id=None,
    )
    prism_events = [e for e in emitted if e["type"] == "prism.verdict"]
    payload = prism_events[0]["payload"]
    assert payload["defect_classification"] == "red_defect"


def test_design_payload_passthrough_threads_fields(monkeypatch, tmp_path):
    """PRISM-D35-R2-02: M-DESIGN passthrough (AC-FR0240-04) end to end —
    payload builder threads optional fields + blob ref; requires_diff and
    the doc-anchored channel stay unchanged."""
    from tracks.executor.executor import Executor
    from tracks.store import Store

    store = Store(tmp_path)
    monkeypatch.setattr(Executor, "_doc_path", lambda self, doc: tmp_path / doc)
    ex = object.__new__(Executor)
    ex.store = store
    result = {
        "verdict": "revise",
        "review_summary": "设计缺口",
        "findings": [_finding()],
        "review_body": "正文",
    }
    payload = ex._design_payload("PRISM_REVIEW", "prism", result, "x", "r1")
    domain = payload["domain_event"]["payload"]
    assert domain["review_summary"] == "设计缺口"
    assert domain["findings"] == [_finding()]
    assert domain["review_ref"]
    assert "review_body" not in domain
    assert payload["requires_diff"] is True  # doc-anchored channel unchanged


# -- B21/#23: RED_CHECK evidence relay ------------------------------------


def test_run_tests_verdict_carries_findings_and_log_ref(monkeypatch, tmp_path):
    """B21: invalid Red verdicts must carry per-node findings + a blobs ref
    to the full logs, or the DIAGNOSE fixer re-derives everything. D-41 (v6):
    RED_CHECK selects R2 from the persisted COLLECT classification and reads
    per-node outcomes from the {result} JUnit records."""
    import subprocess
    from pathlib import Path
    from types import SimpleNamespace

    from tracks.executor.executor import Executor
    from tracks.store import Store

    # The Runtime home is the host's .tracks dir; runtime blob refs are
    # repo-relative (".tracks/runtime/blobs/<sha>").
    ex_repo = tmp_path / "host"
    ex_repo.mkdir()
    store = Store(ex_repo / ".tracks")
    emitted = []
    monkeypatch.setattr(
        Executor,
        "_emit",
        lambda self, ev, payload, **kw: emitted.append({"type": ev, "payload": payload}),
    )
    run_id = "RUN"
    # Persisted pre-WRITE snapshot + COLLECT classification (two R2 nodes).
    baseline_ref = store.write_audit_blob(
        [
            {
                "node": "tests/integration/test_a.py::test_x",
                "layer": "integration",
                "digest": "d0",
                "node_digest": "d0",
            }
        ]
    )
    store.append(
        run_id,
        "v0.4",
        "test.baseline_captured",
        {
            "status": "passed",
            "baseline_id": "b0",
            "baseline_tree": "t0",
            "layers": ["unit", "integration", "e2e"],
            "nodes_count": 1,
            "empty_baseline": False,
            "node_digest_blob": f".tracks/runtime/blobs/{baseline_ref}",
            "errors": [],
        },
    )
    per_node_ref = store.write_audit_blob(
        [
            {
                "node": "tests/integration/test_a.py::test_x",
                "layer": "integration",
                "class": "r2",
            },
            {
                "node": "tests/integration/test_a.py::test_collection_broken",
                "layer": "integration",
                "class": "r2",
            },
        ]
    )
    store.append(
        run_id,
        "v0.4",
        "test.collected",
        {
            "status": "passed",
            "collected_count": 2,
            "inherited_r1": 0,
            "delta_r2": 2,
            "removed": 0,
            "failures": [],
            "per_node_blob": f".tracks/runtime/blobs/{per_node_ref}",
            "errors": [],
        },
    )
    # A {result} JUnit record whose identities exactly cover the selection:
    # one legit assertion failure + one illegit ImportError.
    junit = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<testsuites><testsuite name="sel" tests="2">\n'
        '<testcase classname="tests.integration.test_a" name="test_x">'
        "<failure message=\"AssertionError: assert 1 == 2\">E   assert 1 == 2</failure>"
        "</testcase>\n"
        '<testcase classname="tests.integration.test_a" name="test_collection_broken">'
        "<error message=\"ImportError\">ImportError: No module named 'x'</error>"
        "</testcase>\n"
        "</testsuite></testsuites>\n"
    )
    # D-41 v6: the Runtime stages a unique writable {result} path per
    # command/layer and unlinks any stale record before dispatch. The mocked
    # pytest process therefore writes its JUnit XML to the Runtime-provided
    # path carried in argv -- never pre-seeds it.
    junit_path = tmp_path / "selection-result.xml"
    monkeypatch.setattr(
        Executor,
        "_result_staging_path",
        lambda self, command_id, section: junit_path,
    )
    from tracks.executor import executor as executor_module

    def _fake_pytest_run(argv, **kwargs):
        if argv and argv[0] == "git":
            return subprocess.CompletedProcess(argv, 0, "", "")
        result_arg = next(arg for arg in argv if arg.endswith(".xml"))
        Path(result_arg.split("=", 1)[-1]).write_text(junit, encoding="utf-8")
        return subprocess.CompletedProcess(argv, 1, "", "")

    monkeypatch.setattr(executor_module.subprocess, "run", _fake_pytest_run)

    def _load_contract(repo):
        return SimpleNamespace(
            unit=None,
            e2e=None,
            integration=SimpleNamespace(
                run_selected=".venv/bin/python -m pytest {nodes} --junitxml={result}",
                cwd=".",
            ),
            layout=None,
            lint=None,
            nightly=None,
        )

    _patch_load_contract(monkeypatch, _load_contract)
    monkeypatch.setattr(
        Executor, "_diagnose_classification", lambda self: "test_defect", raising=True
    )
    ex = object.__new__(Executor)
    ex.store = store
    ex.repo = ex_repo
    ex.run_id = run_id

    class _State:
        stage = "M-TEST"
        current_attempt = 0
        red_validated = False
        hotfix_issue = None

    class _Cmd:
        command_id = "c1"

    ex._do_run_tests(_Cmd(), _State(), None, False)
    verdicts = [e for e in emitted if e["type"] == "verdict.failed"]
    assert verdicts, emitted
    payload = verdicts[0]["payload"]
    assert payload["evidence"] and payload["evidence"][0]["test_id"].startswith("tests/")
    assert payload["log_ref"]
    import os

    blob = paths_blobs(store) / os.path.basename(payload["log_ref"])
    assert payload["log_ref"].startswith(".tracks/runtime/blobs/")
    assert blob.exists()
    assert "assert 1 == 2" in blob.read_text(encoding="utf-8")
    reds = [e for e in emitted if e["type"] == "red.validated"]
    assert reds[0]["payload"]["log_ref"] == payload["log_ref"]
    # D-41: the invalid verdict is bound to the stamped selection identity.
    selections = [e for e in emitted if e["type"] == "test.selected"]
    assert selections and selections[0]["payload"]["scope"] == "r2_delta"
    assert reds[0]["payload"]["selection_id"] == selections[0]["payload"]["selection_id"]


def paths_blobs(store):
    from tracks import paths

    return paths.blobs_dir(store.home)


def test_criteria_pack_mismatch_skips_failed_outcomes():
    """Live T-002: a failed (manifest_malformed) prism result legitimately
    lacks the criteria_pack echo — the mismatch check must not re-classify
    it and double-burn the attempt."""
    from tracks.executor.executor import Executor

    ex = object.__new__(Executor)

    class _State:
        stage = "M-IMPL"

    result = {"status": "failed", "failure_class": "manifest_malformed"}
    assignment = {"criteria_pack": {"name": "tracks-prism-impl", "version": "0.1"}}
    assert not ex._criteria_pack_mismatch(
        "prism", "PRISM_FINAL", _State(), None, assignment, result, None
    )
    done = {"status": "done", "verdict": "revise", "criteria_pack": None}
    assert ex._criteria_pack_mismatch(
        "prism", "PRISM_FINAL", _State(), "revise", assignment, done, None
    )


# -- B17/B20 narrow + Devon evidence contract (live T-003 GREEN) ---------


def test_abnormal_step_finish_detected_and_infra_classified(tmp_path):
    """step_finish reason=unknown (provider interruption, exit 0) must
    classify as infra — never reach DIAGNOSE as impl_defect."""
    from tracks.effects.opencode import OpencodeBackend

    lines = "".join(
        json.dumps(ev) + "\n"
        for ev in (
            {"type": "step_start", "part": {"id": "a"}},
            {"type": "text", "part": {"text": "工作中..."}},
            {"type": "step_finish", "part": {"id": "a", "reason": "unknown"}},
        )
    )
    proc = subprocess.CompletedProcess(["opencode"], 0, stdout=lines, stderr="")
    assert OpencodeBackend._abnormal_step_finish(proc)

    backend = OpencodeBackend(repo=tmp_path, version="v0.6")
    result = backend._abnormal_step_result(proc, "p", None)
    assert result["status"] == "failed"
    assert result["failure_class"] == "abnormal_step_finish"

    normal = "".join(
        json.dumps(ev) + "\n"
        for ev in (
            {"type": "step_finish", "part": {"reason": "stop"}},
        )
    )
    ok_proc = subprocess.CompletedProcess(["opencode"], 0, stdout=normal, stderr="")
    assert not OpencodeBackend._abnormal_step_finish(ok_proc)


def test_devon_dispatch_carries_evidence_contract():
    from tracks.kernel.m_impl import _m_impl_devon_dispatch
    from tracks.kernel.machine import State

    s = State()
    s.stage = "M-IMPL"
    s.substate = "GREEN"
    s.current_task_id = "T-009"
    cmd = _m_impl_devon_dispatch(s, "GREEN")
    contract = cmd.params["assignment"].get("evidence_contract")
    assert contract and contract["example"]["phase"] == "green"
    assert isinstance(contract["example"]["commands"], list)
    assert contract["shape_rules"]


# -- contract parity: injected examples must pass the real validators -----


def test_devon_evidence_contract_example_passes_validator():
    """Parity (live T-004 lesson): every example shown to agents must pass
    the consuming validator — a hand-written shape drift burned a dispatch."""
    from tracks.executor.m_impl_runtime import MImplRuntimeMixin
    from tracks.kernel.contracts import DEVON_EVIDENCE_CONTRACT

    example = dict(DEVON_EVIDENCE_CONTRACT["example"])
    for key in ("pre_identity", "post_identity", "r_identity"):
        example[key] = "sha256:abc"
    error = MImplRuntimeMixin._devon_evidence_fields_error("green", example)
    assert error is None, error


def test_write_manifest_contract_example_passes_item_validation():
    from tracks.effects.opencode import OpencodeBackend
    from tracks.kernel.contracts import WRITE_MANIFEST_CONTRACT

    include = WRITE_MANIFEST_CONTRACT["example"]["artifact_manifest"]["include"]
    for index, item in enumerate(include):
        error = OpencodeBackend._manifest_item_error(index, item)
        assert error is None, error
