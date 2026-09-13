"""Final review blocker RED pins (kernel): M-TEST contract_error rollback
routing and single-attempt accounting for the REMOVED collect/verdict pair.

Pure reducer/decide journeys over projected event sequences (same seam as
test_machine_m_test.py). Each pinned defect must fail on the behavior
contract token, never on fixture assembly:

- FRB-K1: after ``red.validated(invalid)``, the executor's fail-closed
  companion ``verdict.failed(check=contract_error, target_stage=M-DESIGN)``
  must decide ``rollback_stage(to_stage=M-DESIGN)`` -- never a Shield
  re-dispatch and never an attempt consumption. An unusable tree is an
  upstream/design defect discovered before any agent wrote; burning the
  shared <=3 semantic budget on it (today it lands in the pipeline-failure
  else-branch: WRITE + consume) grinds every RED_CHECK infra failure into
  wasted Shield rounds.
- FRB-K2: the REMOVED pair -- ``test.collected(failed,
  error_class=asset_deleted, companion=test_defect)`` plus its companion
  ``verdict.failed(check=test_defect)`` emitted by ONE collect command
  (same command_id; the collect half carries the FORWARD companion payload
  marker naming its verdict check, the verdict carries NO reverse key) --
  is ONE Shield rewrite round and must consume EXACTLY ONE attempt total
  and leave NO latent pairing state on the projected State afterwards.
"""

from tests.unit.helpers import ev, seq
from tests.unit.m_test_machine_support import (
    COLLECT_CMD,
    ENTER_M_TEST,
    TO_COLLECT_POPULATED,
    TO_RED_CHECK_POPULATED,
)
from tracks.kernel import decide, project

RUN_CMD = (
    "command.issued",
    {"command": {"kind": "run_tests", "params": {"stage": "M-TEST"}, "command_id": "C3"}},
)
INVALID_RED = (
    "red.validated",
    {
        "status": "invalid",
        "findings": [{"test_id": "*", "classification": "collection_error"}],
        "log_ref": ".tracks/runtime/blobs/red-log",
    },
)


# -- FRB-K1: contract_error(target_stage=M-DESIGN) rolls back, never Shield ---


CONTRACT_ERROR_TO_DESIGN = (
    "verdict.failed",
    {
        "check": "contract_error",
        "target_stage": "M-DESIGN",
        "artifact_disposition": "rollback",
        "reason": "contract error: [integration] layer collection failed",
        "evidence": [{"test_id": "*", "classification": "collection_error"}],
        "log_ref": ".tracks/runtime/blobs/contract-error",
        "attempt": 1,
    },
)


def test_contract_error_target_design_decides_rollback_never_shield():
    """FRB-K1: invalid red + verdict.failed(contract_error, target_stage=
    M-DESIGN) must leave decide() producing rollback_stage(M-DESIGN) with the
    attempt budget untouched."""
    s = project(
        seq(
            *ENTER_M_TEST,
            *TO_RED_CHECK_POPULATED,
            RUN_CMD,
            INVALID_RED,
            CONTRACT_ERROR_TO_DESIGN,
        )
    )
    assert s.current_attempt == 0, (
        f"a contract_error rollback must consume NO attempt; got "
        f"current_attempt={s.current_attempt}"
    )
    cmd = decide(s)
    assert cmd is not None and cmd.kind == "rollback_stage", (
        f"contract_error(target_stage=M-DESIGN) must decide rollback_stage, got "
        f"{cmd!r} (a Shield dispatch would rewrite assets for an upstream defect)"
    )
    assert cmd.params["to_stage"] == "M-DESIGN"


# -- FRB-K2: REMOVED collect/companion-verdict pair = exactly one attempt -----


_NODE_GONE = "tests/integration/test_gone.py::test_gone"

COLLECT_FAILED_REMOVED = {
    "status": "failed",
    "collected_count": 2,
    "removed": 1,
    "failures": [{"node": _NODE_GONE, "error_class": "asset_deleted"}],
    "errors": [f"test asset deleted since baseline snapshot: {_NODE_GONE}"],
    # ACTUAL executor pairing marker (executor._do_collect_tests): the
    # collect half points FORWARD at its companion verdict's check token.
    "companion": "test_defect",
}
COMPANION_TEST_DEFECT = {
    "check": "test_defect",
    "target_stage": "M-TEST",
    "artifact_disposition": "rewrite",
    # Companion half of the failed collect above, emitted by the SAME issued
    # command -- with NO reverse ``companion`` key (actual executor payload:
    # pairing is declared by the collect side alone).
    "reason": (
        f"REMOVED test assets deleted since the pre-WRITE baseline snapshot: {_NODE_GONE}"
    ),
    "evidence": [{"node": _NODE_GONE, "error_class": "asset_deleted"}],
    "log_ref": ".tracks/runtime/blobs/removed-pair",
    "attempt": 1,
}


def test_removed_asset_deleted_pair_consumes_exactly_one_attempt():
    """FRB-K2: the paired test.collected(failed, asset_deleted,
    companion=test_defect) + companion verdict.failed(test_defect) is one
    Shield rewrite round -- exactly one attempt total, no latent pairing
    state left on State, followed by a single Shield re-dispatch."""
    prefix = seq(*ENTER_M_TEST, *TO_COLLECT_POPULATED, COLLECT_CMD)
    # Actual executor protocol: same command_id ("C2") on both halves plus
    # the collect-side forward ``companion: test_defect`` marker; the verdict
    # carries no reverse companion key.
    collected_ev = ev(len(prefix) + 1, "test.collected", COLLECT_FAILED_REMOVED, command_id="C2")
    verdict_ev = ev(len(prefix) + 2, "verdict.failed", COMPANION_TEST_DEFECT, command_id="C2")
    s = project([*prefix, collected_ev, verdict_ev])
    assert s.current_attempt == 1, (
        f"the REMOVED pair is ONE rewrite round; consumed attempts = "
        f"{s.current_attempt} (double consumption halves the shared budget)"
    )
    assert s.substate == "WRITE"
    assert not getattr(s, "m_test_pair_charge_pending", False), (
        f"no latent pairing state may remain after the pair projects; stale "
        f"m_test_pair_charge_pending={getattr(s, 'm_test_pair_charge_pending', None)!r} "
        f"would re-charge a later companion verdict for an already-settled round"
    )
    cmd = decide(s)
    assert cmd is not None and cmd.kind == "dispatch_agent", f"unexpected command: {cmd!r}"
    assert cmd.params["role"] == "shield", (
        f"the single rewrite round must go to Shield, got {cmd.params.get('role')!r}"
    )
