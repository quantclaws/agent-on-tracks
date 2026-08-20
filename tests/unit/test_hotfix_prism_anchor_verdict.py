"""Unit tests for the hotfix Prism anchor_verdict threading (IF-HOTFIX-002 /
IF-HOTFIX-009, AC-FR0243-02/03, FR-0243).

The executor must carry the Prism anchor verdict (interfaces.md §1a: default
``"upheld"``, preserved ``"overturned"``) and related routing fields into the
published ``prism.verdict`` event so AC-FR0243-02 review is observable and an
``"overturned"`` anchor routes M-DESIGN -> M-HOTFIX-TRIAGE/SAGE_TRIAGE without
consuming the M-DESIGN redispatch budget (FR-0243-03).

The kernel reducer (``kernel/machine.py``) already routes on
``p.get("anchor_verdict") == "overturned"`` (machine.py L840-846 -> DIAGNOSE,
``_decide_m_design_diagnose`` -> ``rollback_stage`` to ``M-HOTFIX-TRIAGE``),
and the fake backend (``effects/fake.py``) already produces
``result["anchor_verdict"]``. The closure gap is the executor's shared
``_apply_prism_review_fields`` threader (and the M-IMPL ``_emit_verdict``
emission, and the M-DESIGN ``_design_payload`` domain payload, both fed by
it) not yet writing ``anchor_verdict`` into the payload.
"""

from __future__ import annotations

from tracks.executor.executor import Executor
from tracks.kernel.events import EventEnvelope
from tracks.kernel.machine import State, apply, decide
from tracks.store import Store


def _finding(**overrides) -> dict:
    base = {
        "id": "PRISM-V06-R1-01",
        "severity": "blocker",
        "defect_classification": "test_defect",
        "criterion": "1+2",
        "artifact": "tests/integration/test_x.py:40-43",
        "ac_refs": ["AC-FR0243-02"],
        "summary": "anchor verdict threading gap",
    }
    base.update(overrides)
    return base


def _prism_event(payload: dict) -> EventEnvelope:
    return EventEnvelope(
        seq=1,
        ts="2026-08-20T00:00:00+00:00",
        run_id="r",
        version="v0.6",
        type="prism.verdict",
        schema_version=1,
        command_id=None,
        task_id=None,
        payload=payload,
    )


def _executor(tmp_path) -> Executor:
    """A bare Executor wired only with a Store (no repo/backend), for testing
    the pure payload-construction helpers."""
    ex = object.__new__(Executor)
    ex.store = Store(tmp_path)
    return ex


# -- _apply_prism_review_fields: anchor_verdict threading ---------------


# AC-FR0243-02@v0.6 TRACKS-TRACE prism anchor_verdict defaults upheld on pass
def test_apply_prism_review_fields_defaults_anchor_verdict_upheld_on_pass(tmp_path):
    """interfaces §1a: ``anchor_verdict`` 缺省 ``"upheld"``. A pass verdict
    carries no overturn signal, so the published payload must still expose
    ``anchor_verdict="upheld"`` for AC-FR0243-02 observability."""
    ex = _executor(tmp_path)
    payload: dict = {}
    ex._apply_prism_review_fields(payload, "pass", {"verdict": "pass"})
    assert payload.get("anchor_verdict") == "upheld"


# AC-FR0243-02@v0.6 TRACKS-TRACE prism anchor_verdict defaults upheld on revise
def test_apply_prism_review_fields_defaults_anchor_verdict_upheld_on_revise(tmp_path):
    """A revise verdict without an explicit ``anchor_verdict`` (real Prism
    that did not overturn the anchor) still defaults to ``"upheld"``."""
    ex = _executor(tmp_path)
    payload: dict = {}
    result = {
        "verdict": "revise",
        "review_summary": "design gap",
        "findings": [_finding()],
        "review_body": "body",
    }
    ex._apply_prism_review_fields(payload, "revise", result)
    assert payload.get("anchor_verdict") == "upheld"


# AC-FR0243-03@v0.6 TRACKS-TRACE prism anchor_verdict overturned preserved
def test_apply_prism_review_fields_preserves_anchor_verdict_overturned(tmp_path):
    """interfaces §1a / IF-HOTFIX-009: when Prism overturns the hotfix anchor
    (fake ``prism:PRISM_REVIEW=anchor_overturned`` token or real Prism), the
    ``"overturned"`` value is preserved into the payload verbatim."""
    ex = _executor(tmp_path)
    payload: dict = {}
    result = {
        "verdict": "revise",
        "anchor_verdict": "overturned",
        "review_summary": "anchor set not established",
        "findings": [_finding()],
        "review_body": "body",
    }
    ex._apply_prism_review_fields(payload, "revise", result)
    assert payload.get("anchor_verdict") == "overturned"


# -- _emit_verdict: M-IMPL prism.verdict carries anchor_verdict --------


def _emit_verdict_recorder(monkeypatch, tmp_path, *, stage="M-IMPL"):
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
        pass

    state = _State()
    state.stage = stage
    state.review_round = 1

    class _Cmd:
        command_id = "c1"

    return ex, emitted, state, _Cmd()


# AC-FR0243-02@v0.6 TRACKS-TRACE emit_verdict carries default upheld anchor_verdict
def test_emit_verdict_prism_carries_default_upheld_anchor_verdict(monkeypatch, tmp_path):
    """The prism.verdict event emitted via ``_emit_verdict`` must carry
    ``anchor_verdict``, defaulting to ``"upheld"`` when the reviewer did not
    overturn the anchor."""
    ex, emitted, state, cmd = _emit_verdict_recorder(monkeypatch, tmp_path)
    ex._emit_verdict(
        role="prism",
        verdict="pass",
        result={"verdict": "pass"},
        state=state,
        p={},
        cmd=cmd,
        task_id=None,
    )
    prism_events = [e for e in emitted if e["type"] == "prism.verdict"]
    assert prism_events, emitted
    assert prism_events[0]["payload"].get("anchor_verdict") == "upheld"


# AC-FR0243-03@v0.6 TRACKS-TRACE emit_verdict preserves overturned anchor_verdict
def test_emit_verdict_prism_preserves_overturned_anchor_verdict(monkeypatch, tmp_path):
    """The prism.verdict event emitted via ``_emit_verdict`` preserves
    ``anchor_verdict="overturned"`` so the kernel can route back to
    M-HOTFIX-TRIAGE/SAGE_TRIAGE."""
    ex, emitted, state, cmd = _emit_verdict_recorder(monkeypatch, tmp_path)
    ex._emit_verdict(
        role="prism",
        verdict="revise",
        result={
            "verdict": "revise",
            "anchor_verdict": "overturned",
            "review_summary": "anchor overturned",
            "findings": [_finding()],
            "review_body": "body",
        },
        state=state,
        p={},
        cmd=cmd,
        task_id=None,
    )
    prism_events = [e for e in emitted if e["type"] == "prism.verdict"]
    assert prism_events, emitted
    assert prism_events[0]["payload"].get("anchor_verdict") == "overturned"


# -- M-DESIGN passthrough: anchor_verdict into domain payload ----------


# AC-FR0243-02@v0.6 TRACKS-TRACE design payload threads anchor_verdict
def test_design_payload_threads_anchor_verdict_into_domain_payload(
    monkeypatch, tmp_path
):
    """AC-FR0243-02: the M-DESIGN delta Prism review domain payload (threaded
    via ``_apply_prism_review_fields``) must carry ``anchor_verdict`` so the
    Prism review is observable and the kernel can route an overturn."""
    monkeypatch.setattr(Executor, "_doc_path", lambda self, doc: tmp_path / doc)
    ex = _executor(tmp_path)
    result = {
        "verdict": "revise",
        "anchor_verdict": "overturned",
        "review_summary": "anchor set overturned",
        "findings": [_finding()],
        "review_body": "body",
    }
    payload = ex._design_payload("PRISM_REVIEW", "prism", result, "x", "r1")
    domain = payload["domain_event"]["payload"]
    assert domain.get("anchor_verdict") == "overturned"


# AC-FR0243-03@v0.6 TRACKS-TRACE B48 anchor_overturned structured verdict needs no doc diff
def test_design_payload_requires_diff_off_for_anchor_overturned(monkeypatch, tmp_path):
    """B48 (issue #60 / PRISM-V06-R2-01): an anchor-overturned revise is
    carried entirely by the structured channel (anchor_verdict routing
    field, interfaces §1a / IF-HOTFIX-009) — it routes M-DESIGN back to
    M-HOTFIX-TRIAGE/SAGE_TRIAGE in the kernel without any document diff,
    so ``requires_diff`` must yield or the pipeline rejects the verdict
    with ``no_diff`` before ``prism.verdict`` ever publishes (mirror of
    the M-TEST structured_revise exemption, D-35 SC-D35 §2.3). A plain
    revise (doc-anchored channel) still requires a diff."""
    monkeypatch.setattr(Executor, "_doc_path", lambda self, doc: tmp_path / doc)
    ex = _executor(tmp_path)
    overturned = {
        "verdict": "revise",
        "anchor_verdict": "overturned",
        "review_summary": "anchor set overturned",
        "findings": [_finding()],
        "review_body": "body",
    }
    payload = ex._design_payload("PRISM_REVIEW", "prism", overturned, "x", "r1")
    assert payload["requires_diff"] is False

    # Fake token path (simulate="prism:PRISM_REVIEW=anchor_overturned"):
    # the token verdict itself is "anchor_overturned" and the fake maps it
    # to anchor_verdict="overturned" — same structured-channel exemption.
    token_result = {
        "verdict": "anchor_overturned",
        "anchor_verdict": "overturned",
        "review_summary": "anchor set overturned",
        "findings": [_finding()],
        "review_body": "body",
    }
    payload_token = ex._design_payload("PRISM_REVIEW", "prism", token_result, "x", "r1")
    assert payload_token["requires_diff"] is False

    plain = {
        "verdict": "revise",
        "review_summary": "design gap",
        "findings": [_finding()],
        "review_body": "body",
    }
    payload2 = ex._design_payload("PRISM_REVIEW", "prism", plain, "x", "r1")
    assert payload2["requires_diff"] is True  # doc-anchored channel unchanged


# -- consequence: anchor_overturned routes to M-HOTFIX-TRIAGE ----------


# AC-FR0243-03@v0.6 TRACKS-TRACE anchor overturned routes to hotfix triage without m_design budget
def test_anchor_overturned_enables_hotfix_triage_rollback_without_m_design_redispatch(
    tmp_path,
):
    """AC-FR0243-03 / IF-HOTFIX-009: once the executor threads
    ``anchor_verdict="overturned"`` into prism.verdict, the kernel routes
    M-DESIGN -> M-HOTFIX-TRIAGE (rollback) for Sage re-anchoring WITHOUT
    re-dispatching Archer (the M-DESIGN redispatch budget is preserved)."""
    ex = _executor(tmp_path)

    # Executor builds the prism.verdict payload via the shared threader.
    payload: dict = {"verdict": "revise"}
    result = {
        "verdict": "revise",
        "anchor_verdict": "overturned",
        "review_summary": "anchor overturned",
        "findings": [_finding()],
        "review_body": "body",
    }
    ex._apply_prism_review_fields(payload, "revise", result)

    # AC-FR0243-02: the published payload carries anchor_verdict (observable).
    assert payload.get("anchor_verdict") == "overturned"

    # AC-FR0243-03 consequence: kernel routes to M-HOTFIX-TRIAGE rollback.
    s = State()
    s.stage = "M-DESIGN"
    s.substate = "PRISM_REVIEW"
    s.review_round = 1
    apply(s, _prism_event(payload))
    assert s.substate == "DIAGNOSE"
    assert s.diagnose_classification == "anchor_overturned"
    # Not routed to RESPOND (Archer re-dispatch) -> M-DESIGN budget preserved.
    assert s.substate != "RESPOND"
    cmd = decide(s)
    assert cmd is not None
    assert cmd.kind == "rollback_stage"
    assert cmd.params["to_stage"] == "M-HOTFIX-TRIAGE"
    assert cmd.params["reason"] == "anchor_overturned"
