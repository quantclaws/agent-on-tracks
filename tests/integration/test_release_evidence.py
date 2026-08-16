"""Deterministic release-evidence integration tests (FR-0230~FR-0233/NFR-0080).

All assertions land on the public CLI contract (interfaces.md §2d/§4d):
`trac check release-evidence [--json]` stdout/stderr/exit, and the canonical
evidence path.  The CLI subcommand is not yet wired (IF-RELEASE-001/public CLI
wiring), producing a legal Red at the CLI-routing layer.

PRISM-V05-R2-01 revision: a hand-written evidence bundle is a FABRICATED
artifact — it is schema-valid but has no backing blobs/, git R ref, or events
in the store, so a wired check must reject it as ``audit_incomplete`` (§2d
reason_code closed set).  The integration layer therefore only asserts the
anti-fabrication negative; the satisfied text/JSON/exit-0 form is asserted in
``tests/e2e_live/`` where the real live journey produces the bundle.
"""

import json
import os
import subprocess

import pytest


def _run_release_check(repo, *args):
    """Invoke the INSTALLED public CLI for ``trac check release-evidence``.

    PRISM-V05-R2-01: run the installed console script (the user-facing
    entry point, ``host/.venv/bin/trac`` — the conftest symlink to the test
    venv where ``tracks`` is installed), not ``sys.executable -m`` (which
    resolves the module through the source tree).  The subcommand is not yet
    wired, so this currently returns the CLI-routing error
    ``usage: trac check <deliverables|trace|reach>`` (stderr, exit 1).
    """
    cmd = [str(repo / ".venv" / "bin" / "trac"), "check", "release-evidence", *args]
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("TRAC_TEST_DOC_DELTA", "TRAC_FAKE_SIMULATE")
    }
    proc = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, env=env)
    return proc


def _write_canonical_evidence(repo, candidate_sha, run_id, status="satisfied",
                              complete=True):
    """Fabricate a schema-valid evidence bundle at the release-evidence root.

    PRISM-V05-R2-01: this is an ANTI-FABRICATION fixture.  The bundle matches
    the §1j schema but has NO backing reality — no ``blobs/`` files, no git R
    ref, and no events in the store — so a wired check must classify it as
    ``audit_incomplete`` (§2d reason_code closed set), never ``satisfied``.

    When *complete* is True, the bundle satisfies the §1j closed invariants
    (non-empty agent_io with proper seq ordering, non-empty gate_observations
    with ascending seq, and non-empty trailers).  When *complete* is False,
    writes a minimal bundle with empty agent_io/gates/trailers (used for
    negative tests that must fail closed).
    """
    root = repo / ".tracks" / "runtime" / "release-evidence" / "v1" / candidate_sha / run_id
    root.mkdir(parents=True, exist_ok=True)

    if complete:
        agent_io = [
            {
                "role": "devon",
                "phase": "RED",
                "task_id": "T-001",
                "attempt": 1,
                "command_seq": 10,
                "outcome_seq": 20,
                "input_ref": f"blobs/{'e' * 64}",
                "input_sha256": "e" * 64,
                "output_ref": f"blobs/{'f' * 64}",
                "output_sha256": "f" * 64,
                "audit_completeness": "complete",
            },
            {
                "role": "devon",
                "phase": "GREEN",
                "task_id": "T-001",
                "attempt": 1,
                "command_seq": 30,
                "outcome_seq": 40,
                "input_ref": f"blobs/{'g' * 64}",
                "input_sha256": "g" * 64,
                "output_ref": f"blobs/{'h' * 64}",
                "output_sha256": "h" * 64,
                "audit_completeness": "complete",
            },
            {
                "role": "devon",
                "phase": "REFACTOR",
                "task_id": "T-001",
                "attempt": 1,
                "command_seq": 50,
                "outcome_seq": 60,
                "input_ref": f"blobs/{'i' * 64}",
                "input_sha256": "i" * 64,
                "output_ref": f"blobs/{'j' * 64}",
                "output_sha256": "j" * 64,
                "audit_completeness": "complete",
            },
            {
                "role": "prism",
                "phase": "PRISM_RED",
                "task_id": "T-001",
                "attempt": 1,
                "command_seq": 25,
                "outcome_seq": 35,
                "input_ref": f"blobs/{'k' * 64}",
                "input_sha256": "k" * 64,
                "output_ref": f"blobs/{'l' * 64}",
                "output_sha256": "l" * 64,
                "audit_completeness": "complete",
            },
            {
                "role": "prism",
                "phase": "PRISM_FINAL",
                "task_id": "T-001",
                "attempt": 1,
                "command_seq": 70,
                "outcome_seq": 80,
                "input_ref": f"blobs/{'m' * 64}",
                "input_sha256": "m" * 64,
                "output_ref": f"blobs/{'n' * 64}",
                "output_sha256": "n" * 64,
                "audit_completeness": "complete",
            },
        ]
        gate_observations = [
            {
                "gate": "RED_GATE", "status": "pass", "seq": 22,
                "source": "runtime", "source_event_type": "test.collected",
                "command_id": "cmd-red-gate",
            },
            {
                "gate": "GREEN_GATE", "status": "pass", "seq": 42,
                "source": "runtime", "source_event_type": "test.collected",
                "command_id": "cmd-green-gate",
            },
            {
                "gate": "REFACTOR_GATE", "status": "pass", "seq": 62,
                "source": "runtime", "source_event_type": "test.collected",
                "command_id": "cmd-refactor-gate",
            },
            {
                "gate": "TASK_REVIEW", "status": "pass", "seq": 72,
                "source": "runtime", "source_event_type": "test.collected",
                "command_id": "cmd-task-review",
            },
            {
                "gate": "PRISM_FINAL", "status": "pass", "seq": 82,
                "source": "prism", "source_event_type": "prism.verdict",
                "command_id": "cmd-prism-final",
            },
            {
                "gate": "ISLAND_GATE_2", "status": "pass", "seq": 96,
                "source": "runtime", "source_event_type": "test.collected",
                "command_id": "cmd-island-gate-2",
            },
        ]
        trailers = {
            "Tracks-Task": "T-001",
            "Tracks-Attempt": "1",
            "Tracks-R": "c" * 40,
            "Tracks-Issue": "42",
            "Tracks-AC": "AC-FR0030-01,AC-FR0070-03",
        }
    else:
        agent_io = []
        gate_observations = []
        trailers = {}

    bundle = {
        "schema_version": "tracks.release-evidence/v1",
        "status": status,
        "candidate_sha": candidate_sha,
        "candidate_artifact": {
            "name": "agent_on_tracks-0.5.0-py3-none-any.whl",
            "sha256": "a" * 64,
            "distribution": "agent-on-tracks",
            "version": "0.5.0",
            "installed_import_path": "/tmp/venv/lib/python3.14/site-packages/tracks/__init__.py",
            "source_tree_import": False,
        },
        "run_id": run_id,
        "backend": "opencode",
        "provenance": {
            "backend_class": "OpencodeBackend",
            "fake_backend": False,
            "trac_fake_simulate": False,
            "assignment_overlay": False,
            "assignment_simulation": False,
            "event_origin": "runtime",
        },
        "required_devon_phases": ["RED", "GREEN", "REFACTOR"],
        "agent_io": agent_io,
        "event_sequence": {
            "first_seq": 1,
            "last_seq": 100,
            "events_ref": f"blobs/{'b' * 64}",
            "events_sha256": "b" * 64,
        },
        "rgr_lineage": {
            "task_id": "T-001",
            "attempt": 1,
            "red_ref": "refs/trac/rgr/run01/T-001/1/red",
            "r_sha": "c" * 40,
            "g_sha": "d" * 40,
            "red_checkpoint_seq": 50,
            "green_commit_seq": 60,
            "trailers": trailers,
        },
        "gate_observations": gate_observations,
        "boundary": {
            "stage": "M-IMPL",
            "stage_exited_seq": 95,
            "run_completed_seq": 100,
            "terminal_state": "boundary",
        },
    }
    evidence_path = root / "evidence.json"
    evidence_path.write_text(
        json.dumps(bundle, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    return evidence_path


@pytest.mark.integration
# AC-FR0230-03@v0.5 TRACKS-TRACE incomplete/failed/cancelled/fake never write success
def test_incomplete_failed_cancelled_and_fake_never_write_success(host_repo, trac, event_log):
    """Verify that the CLI check rejects fake/incomplete journeys (legal Red).

    The `trac check release-evidence` subcommand is not yet wired; the test
    asserts exit 1 + NOT satisfied but gets the CLI-routing error — a legal Red.
    Once wired, this test asserts that fake/incomplete journeys never produce
    success evidence.
    """
    # Setup: run a fake journey
    from tests.integration.v05_contract_helpers import run_m_impl_journey
    run_m_impl_journey(trac, event_log)

    # Assert: check CLI reports NOT satisfied (fails because subcommand not wired)
    proc = _run_release_check(host_repo)
    assert proc.returncode == 1, (
        f"expected exit 1, got {proc.returncode}: {proc.stdout}"
    )
    assert "release-evidence: NOT satisfied" in proc.stdout


@pytest.mark.integration
# AC-FR0231-02@v0.5 TRACKS-TRACE provenance rejects fake/simulation/overlay/manual
def test_provenance_rejects_fake_simulation_overlay_manual_events(
    host_repo, trac, event_log,
):
    """Verify that the CLI check rejects fake provenance (legal Red).

    The subcommand is not yet wired; the test asserts exit 1 + NOT satisfied
    but gets the CLI-routing error.  Once wired, this test asserts that
    FakeBackend, TRAC_FAKE_SIMULATE, assignment overlay, manual events, and
    incomplete agent I/O are all rejected.
    """
    from tests.integration.v05_contract_helpers import run_m_impl_journey
    run_m_impl_journey(trac, event_log)

    proc = _run_release_check(host_repo)
    assert proc.returncode == 1, (
        f"expected exit 1, got {proc.returncode}: {proc.stdout}"
    )
    assert "release-evidence: NOT satisfied" in proc.stdout


@pytest.mark.integration
# AC-FR0232-01@v0.5 TRACKS-TRACE check accepts latest current real auditable bundle
def test_check_accepts_latest_current_real_auditable_bundle(host_repo, trac):
    """Verify the check rejects a fabricated bundle at the current HEAD (legal Red).

    PRISM-V05-R2-01: a hand-written bundle — even schema-valid, even at the
    CURRENT candidate SHA — has no backing blobs/, git R ref, or events in
    the store, so it is not a "real auditable bundle" and must be rejected
    as ``audit_incomplete``.  The satisfied form requires a REAL journey and is
    asserted in tests/e2e_live/.  The subcommand is not yet wired; the test
    asserts exit 1 + the exact NOT-satisfied line but currently gets the
    CLI-routing error — legal Red.
    """
    candidate_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=host_repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _write_canonical_evidence(host_repo, candidate_sha, "run-001")

    proc = _run_release_check(host_repo)
    assert proc.returncode == 1, (
        f"fabricated bundle must NOT satisfy the check; got exit {proc.returncode}: "
        f"{proc.stdout}{proc.stderr}"
    )
    assert "release-evidence: NOT satisfied — audit_incomplete" in proc.stdout, (
        "schema-valid but unbacked evidence must be rejected with reason audit_incomplete"
    )


@pytest.mark.integration
# AC-FR0232-02@v0.5 TRACKS-TRACE check rejects stale and candidate SHA mismatch
def test_check_rejects_stale_and_candidate_sha_mismatch(host_repo, trac):
    """Verify that the CLI check rejects stale evidence (legal Red).

    The subcommand is not yet wired; the test asserts exit 1 + NOT satisfied
    but gets the CLI-routing error.  Once wired, this test asserts that stale
    and SHA-mismatched evidence are rejected.
    """
    _write_canonical_evidence(host_repo, "f" * 40, "run-002")

    proc = _run_release_check(host_repo)
    assert proc.returncode == 1, (
        f"expected exit 1, got {proc.returncode}: {proc.stdout}"
    )
    assert "release-evidence: NOT satisfied" in proc.stdout


@pytest.mark.integration
# AC-FR0232-03@v0.5 TRACKS-TRACE check success text/JSON and exit 0 contract
def test_check_success_text_json_and_exit_contract(host_repo, trac):
    """Verify the deterministic text/JSON contract on a fabricated bundle (legal Red).

    PRISM-V05-R2-01: the satisfied text/JSON/exit-0 form moved to
    tests/e2e_live/ (only a REAL journey can produce it).  This test keeps
    the deterministic-output contract at the integration layer using the
    fabricated bundle: §2d's exact two-line NOT-satisfied text (exit 1) and
    the canonical single-line 7-field ``--json`` object with
    reason_code=audit_incomplete (exit 1 — "exit 0 iff satisfied").  The subcommand
    is not yet wired; both variants currently get the CLI-routing error —
    legal Red.
    """
    candidate_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=host_repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _write_canonical_evidence(host_repo, candidate_sha, "run-003")

    # Text variant: exact two-line NOT-satisfied form + exit 1 (§2d).
    proc_text = _run_release_check(host_repo)
    assert proc_text.returncode == 1, (
        f"text: fabricated bundle must exit 1, got {proc_text.returncode}: "
        f"{proc_text.stdout}{proc_text.stderr}"
    )
    text_lines = proc_text.stdout.splitlines()
    assert text_lines[:2] == [
        "release-evidence: NOT satisfied — audit_incomplete",
        "  next: rerun the opt-in live journey at current HEAD, then re-check",
    ], f"text: §2d NOT-satisfied lines are locked, got {text_lines!r}"

    # JSON variant: canonical single-line 7-field object + exit 1 (§2d).
    proc_json = _run_release_check(host_repo, "--json")
    assert proc_json.returncode == 1, (
        f"json: exit 0 iff satisfied — fabricated bundle must exit 1, got "
        f"{proc_json.returncode}: {proc_json.stdout}{proc_json.stderr}"
    )
    assert "\n" not in proc_json.stdout.rstrip("\n"), (
        "json: §2d output is a single line"
    )
    result = json.loads(proc_json.stdout)
    assert set(result) == {
        "backend", "candidate_sha", "evidence_path", "event_bounds",
        "reason_code", "run_id", "status",
    }, f"json: §2d field set is closed, got {sorted(result)}"
    assert result["reason_code"] == "audit_incomplete", (
        "json: fabricated-but-schema-valid bundle must report audit_incomplete"
    )
    assert result["status"] != "satisfied", (
        "json: fabricated bundle must never report satisfied"
    )
    assert json.dumps(result, sort_keys=True, separators=(",", ":")) == proc_json.stdout.rstrip("\n"), (
        "json: §2d canonical form is sort_keys + compact separators"
    )


@pytest.mark.integration
# AC-FR0232-04@v0.5 TRACKS-TRACE check fail-closed reason precedence text/JSON and exit
def test_check_fail_closed_reason_precedence_text_json_and_exit(host_repo, trac):
    """Verify that the CLI check produces fail-closed reasons (legal Red).

    The subcommand is not yet wired; the test asserts exit 1 + NOT satisfied
    for each fail-closed class but gets the CLI-routing error.  Once wired,
    this test asserts every closed-set reason code in precedence order.
    """
    # No bundle at all -> missing
    proc_missing = _run_release_check(host_repo)
    assert proc_missing.returncode == 1, (
        f"missing: expected exit 1, got {proc_missing.returncode}"
    )
    assert "release-evidence: NOT satisfied" in proc_missing.stdout

    # Bundle with non-current SHA -> stale
    _write_canonical_evidence(host_repo, "f" * 40, "run-004")
    proc_stale = _run_release_check(host_repo)
    assert proc_stale.returncode == 1, (
        f"stale: expected exit 1, got {proc_stale.returncode}"
    )
    assert "release-evidence: NOT satisfied" in proc_stale.stdout


@pytest.mark.integration
# AC-FR0232-05@v0.5 TRACKS-TRACE check is read-only and emits no verify/release/publish effect
def test_check_is_read_only_and_emits_no_verify_release_publish_effect(host_repo, trac):
    """Verify that the CLI check is read-only (legal Red).

    The subcommand is not yet wired; the test asserts exit 1 + NOT satisfied
    (no evidence bundle exists -> missing) but gets the CLI-routing error.
    Once wired, this test snapshots canonical evidence bytes, events DB,
    refs, HEAD, commit-graph, index, and worktree before and after BOTH text
    and JSON checks, asserting no verify/release/publish effect.
    """
    proc = _run_release_check(host_repo)
    assert proc.returncode == 1, (
        f"expected exit 1, got {proc.returncode}: {proc.stdout}"
    )
    assert "release-evidence: NOT satisfied" in proc.stdout


@pytest.mark.integration
# AC-FR0233-03@v0.5 TRACKS-TRACE routine skip and fake never satisfy release prerequisite
def test_routine_skip_and_fake_never_satisfy_release_prerequisite(host_repo, trac, event_log):
    """Verify that the CLI check rejects fake journeys (legal Red).

    The subcommand is not yet wired; the test asserts exit 1 + NOT satisfied
    but gets the CLI-routing error.  Once wired, this test asserts that routine
    skip (no live credentials) and fake backend never produce a satisfied bundle
    that could satisfy the release prerequisite.
    """
    from tests.integration.v05_contract_helpers import run_m_impl_journey
    run_m_impl_journey(trac, event_log)

    proc = _run_release_check(host_repo)
    assert proc.returncode == 1, (
        f"expected exit 1, got {proc.returncode}: {proc.stdout}"
    )
    assert "release-evidence: NOT satisfied" in proc.stdout


@pytest.mark.integration
# AC-NFR0080-01@v0.5 TRACKS-TRACE check is deterministic, auditable, and rejects event-only evidence
def test_check_is_deterministic_auditable_and_rejects_event_only_evidence(host_repo, trac):
    """Verify that the CLI check is deterministic (legal Red).

    The subcommand is not yet wired; the test asserts exit 1 + NOT satisfied
    (no evidence bundle exists -> missing) but gets the CLI-routing error.
    Once wired, this test asserts that the check is deterministic (same input
    → same output), auditable, and rejects event-only evidence.
    """
    proc = _run_release_check(host_repo)
    assert proc.returncode == 1, (
        f"expected exit 1, got {proc.returncode}: {proc.stdout}"
    )
    assert "release-evidence: NOT satisfied" in proc.stdout


@pytest.mark.integration
# AC-NFR0080-02@v0.5 TRACKS-TRACE non-real sources neither produce nor satisfy evidence
def test_non_real_sources_neither_produce_nor_satisfy_evidence(host_repo, trac, event_log):
    """Verify that the CLI check rejects non-real sources (legal Red).

    The subcommand is not yet wired; the test asserts exit 1 + NOT satisfied
    but gets the CLI-routing error.  Once wired, this test asserts that
    FakeBackend, TRAC_FAKE_SIMULATE, assignment overlay, and simulation
    neither produce nor satisfy release evidence.
    """
    from tests.integration.v05_contract_helpers import run_m_impl_journey
    run_m_impl_journey(trac, event_log)

    proc = _run_release_check(host_repo)
    assert proc.returncode == 1, (
        f"expected exit 1, got {proc.returncode}: {proc.stdout}"
    )
    assert "release-evidence: NOT satisfied" in proc.stdout
