"""D-41 Slice A integration probes (FR-0250-01/-02 selection semantics, RED).

Public-surface pins for the M-TEST selection slice:

- AC-FR0250-01 (v5): M-TEST entry persists ``test.baseline_captured`` BEFORE
  the first Shield WRITE dispatch (full unit+integration+e2e collect of the
  inherited tree, per-node digests + stamped baseline/tree identity); the
  post-WRITE full COLLECT classifies against that persisted snapshot --
  seeded historical unit nodes read r1 while Shield-written nodes read r2,
  which is only possible if the baseline source is the prior-to-Shield
  capture (never a post-WRITE mutable-tree re-guess).
- AC-FR0250-02: ``test.selected`` (scope=r2_delta) precedes execution,
  RED_CHECK executes ONLY the selected R2 nodes through the contract's
  ``run_selected`` command, never the full ``run`` command and never any
  historical (R1) node; feature empty-R2 is fail-closed.
- AC-FR0250-04: a baseline historical node missing from full collect is a
  fail-closed test-asset deletion: asset_deleted evidence plus an actionable
  test_defect verdict are emitted and no valid red/stage exit may occur
  between detection and the Shield repair re-dispatch; a later exit is legal
  only after a repair collect classifies the restored node R1 and R2 executes.
- AC-FR0250-02 (v3 timing): ``red.validated`` precedes ``prism.verdict``.
- AC-FR0250-01 (v5 durability): the normative guarantee is EVENT ORDER
  ``stage.entered(M-TEST) < test.baseline_captured < first Shield WRITE`` --
  not synchronous capture I/O in the stage-entered reducer. Status first
  showing M-TEST therefore proves nothing about capture durability, and the
  snapshot-consuming journeys park via ``_drive_to_persisted_baseline``
  (drive single dispatches until the capture is persisted, failing closed if
  any Shield WRITE dispatch precedes it).

The recorder contract below carries the flat ``[unit]`` section with
``collect``/``run``/``run_selected`` plus the ``[nightly]`` section so the
atomic schema slice can activate against this exact shape (interfaces §1m/§1n).
v6: every ``run``/``run_selected`` template embeds the ``{result}``
placeholder exactly once (Runtime-provided unique JUnit XML path); the
selection recorder consumes it and writes back a minimal VALID JUnit XML
whose testcase identities match its argv nodes exactly, so the audit journey
exercises the machine-readable per-node result channel end-to-end.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import asdict, replace

from tests.e2e.helpers import walk_to_m_test_complete
from tests.integration.helpers import m_test_events, walk_to_m_test
from tracks.executor.executor import Executor
from tracks.executor.taskgraph import TaskNode
from tracks.kernel.events import Command
from tracks.store import Store

_HIST_UNIT_FILE = "tests/unit/test_hist_seed.py"
_HIST_UNIT_BODY = (
    '"""Historical regression seed (inherited tree, no TRACKS-TRACE marker)."""\n'
    "\n"
    "\n"
    "def test_hist_seed_unit():\n"
    '    raise NotImplementedError("historical regression anchor")\n'
)


def _seed_historical_tests(host_repo):
    """Commit an inherited unit-layer test into the host repo BEFORE init so
    the pre-WRITE baseline capture sees it (R1/T-HIST classification input)."""
    path = host_repo / _HIST_UNIT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_HIST_UNIT_BODY, encoding="utf-8")
    for args in (("add", _HIST_UNIT_FILE), ("commit", "-m", "seed historical unit test")):
        subprocess.run(["git", *args], cwd=host_repo, check=True, capture_output=True)


def _hist_unit_node_id():
    return f"{_HIST_UNIT_FILE}::test_hist_seed_unit"


def _file_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _collected_node_ids(host_repo, layer):
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", f"tests/{layer}/"],
        cwd=host_repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return sorted(
        line.strip()
        for line in proc.stdout.splitlines()
        if line.strip().startswith(f"tests/{layer}/") and "::" in line
    )


def _drive_to_m_test(trac):
    """Advance dispatch-by-dispatch until the run enters M-TEST."""
    for _ in range(30):
        status = trac("status")
        assert status.returncode == 0, status.stderr
        if "stage=M-TEST" in status.stdout:
            return
        step = trac("run", "--max-dispatches", "1")
        assert step.returncode == 0, step.stderr
    raise AssertionError(f"run never entered M-TEST: {trac('status').stdout.strip()}")


def _first_m_test_shield_write_seq(evs):
    """seq of this run's first Shield WRITE dispatch inside M-TEST, or None."""
    return next(
        (
            e["seq"]
            for e in evs
            if e["type"] == "command.issued"
            and e["payload"]["command"]["kind"] == "dispatch_agent"
            and e["payload"]["command"]["params"].get("substate") == "WRITE"
            and e["payload"]["command"]["params"].get("stage") == "M-TEST"
        ),
        None,
    )


def _drive_to_persisted_baseline(trac, run_id, event_log):
    """Park the run where snapshot-consuming journeys need it: M-TEST entered
    AND ``test.baseline_captured`` persisted AND no Shield WRITE dispatched.

    Status first showing ``stage=M-TEST`` does NOT mean the capture is already
    on disk: the normative guarantee is pure event ORDER
    (``stage.entered(M-TEST) < test.baseline_captured < first Shield WRITE``),
    never synchronous I/O in the stage-entered reducer. So after M-TEST entry
    we keep driving ``--max-dispatches 1`` until the stamped capture lands,
    failing closed the moment a Shield WRITE shows up without a preceding
    persisted capture. Returns the persisted baseline event."""
    for _ in range(30):
        evs = event_log(run_id)
        entered_seq = min(
            (
                e["seq"]
                for e in evs
                if e["type"] == "stage.entered" and e["payload"].get("stage") == "M-TEST"
            ),
            default=None,
        )
        baselines = [
            e
            for e in evs
            if e["type"] == "test.baseline_captured" and e["payload"].get("status") == "passed"
        ]
        first_write_seq = _first_m_test_shield_write_seq(evs)
        if entered_seq is not None:
            if not baselines:
                assert first_write_seq is None, (
                    f"Shield WRITE dispatched at seq={first_write_seq} before any persisted "
                    "test.baseline_captured: violates stage.entered(M-TEST) < "
                    "test.baseline_captured < first Shield WRITE"
                )
            else:
                assert baselines[0]["seq"] > entered_seq, (
                    "test.baseline_captured must follow stage.entered(M-TEST)"
                )
                assert first_write_seq is None or first_write_seq > baselines[0]["seq"], (
                    "the first Shield WRITE dispatch must follow the persisted "
                    "test.baseline_captured (event-order guarantee)"
                )
                return baselines[0]
        step = trac("run", "--max-dispatches", "1")
        if step.returncode != 0:
            raise AssertionError(
                "run ended before persisting test.baseline_captured "
                f"(rc={step.returncode}): {(step.stderr or step.stdout).strip()}"
            )
    raise AssertionError(
        f"run never persisted test.baseline_captured: {trac('status').stdout.strip()}"
    )


_RECORDER_BODY = (
    "import json, sys\n"
    "tag, out, rest = sys.argv[1], sys.argv[2], sys.argv[3:]\n"
    "# v6 contract shape: the Runtime-substituted {result} path is the LAST\n"
    "# argument; everything before it is the {nodes} expansion.\n"
    "nodes, result = rest[:-1], rest[-1]\n"
    "with open(out, \"a\", encoding=\"utf-8\") as fh:\n"
    "    fh.write(json.dumps({\"tag\": tag, \"argv\": nodes, \"result\": result}) + \"\\n\")\n"
    "# Write a minimal VALID JUnit XML whose testcase identities match the argv\n"
    "# nodes exactly (machine-readable per-node result for the audit journey).\n"
    "cases = []\n"
    "for n in nodes:\n"
    "    f, _, tail = n.partition(\"::\")\n"
    "    cls = f[:-3].replace(\"/\", \".\") if f.endswith(\".py\") else f.replace(\"/\", \".\")\n"
    "    cases.append('<testcase classname=\"%s\" name=\"%s\">'\n"
    "                 '<failure message=\"AssertionError: recorded selection audit\">'\n"
    "                 'E   AssertionError: recorded selection audit</failure>'\n"
    "                 '</testcase>' % (cls, tail))\n"
    "with open(result, \"w\", encoding=\"utf-8\") as fh:\n"
    "    fh.write('<?xml version=\"1.0\" encoding=\"utf-8\"?>\\n'\n"
    "             '<testsuites><testsuite name=\"recorder\" tests=\"%d\">%s'\n"
    "             '</testsuite></testsuites>\\n' % (len(cases), \"\".join(cases)))\n"
    'print("E   AssertionError: recorded selection audit")\n'
    "raise SystemExit(1)\n"
)


def _install_selection_recorder(host_repo):
    """Point run/run_selected at an argv-recording stub (fixture-owned host
    contract; the real tracks-repo project.toml is untouched). The contract
    shape matches the D-41 atomic schema slice exactly: flat [unit],
    [integration], [e2e] sections each with collect/run/run_selected, plus
    the [nightly] section (interfaces §1m/§1n). v6: every run/run_selected
    template carries {result} exactly once (run_selected also {nodes}); the
    recorder writes a minimal valid JUnit matching its argv nodes."""
    tests_dir = host_repo / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "_rec_select_audit.py").write_text(_RECORDER_BODY, encoding="utf-8")
    toml = host_repo / ".tracks" / "projects" / "project.toml"
    body = (
        "[unit]\n"
        'framework = "pytest"\n'
        'paths = ["tests/unit/"]\n'
        'collect = ".venv/bin/python -m pytest --collect-only -q tests/unit/"\n'
        'run = ".venv/bin/python tests/_rec_select_audit.py full-unit full-unit.jsonl {result}"\n'
        'run_selected = ".venv/bin/python tests/_rec_select_audit.py sel-unit sel-unit.jsonl '
        '{nodes} {result}"\n'
        'cwd = "."\n\n'
        "[integration]\n"
        'framework = "pytest"\n'
        'paths = ["tests/integration/"]\n'
        'collect = ".venv/bin/python -m pytest --collect-only -q tests/integration/"\n'
        'run = ".venv/bin/python tests/_rec_select_audit.py full-integration '
        'full-integration.jsonl {result}"\n'
        'run_selected = ".venv/bin/python tests/_rec_select_audit.py sel-integration '
        'sel-integration.jsonl {nodes} {result}"\n'
        'cwd = "."\n\n'
        "[e2e]\n"
        'framework = "pytest"\n'
        'paths = ["tests/e2e/"]\n'
        'collect = ".venv/bin/python -m pytest --collect-only -q tests/e2e/"\n'
        'run = ".venv/bin/python tests/_rec_select_audit.py full-e2e full-e2e.jsonl {result}"\n'
        'run_selected = ".venv/bin/python tests/_rec_select_audit.py sel-e2e sel-e2e.jsonl '
        '{nodes} {result}"\n'
        'cwd = "."\n\n'
        "[nightly]\n"
        'schedule = "0 3 * * *"\n'
        'workflow = ".github/workflows/nightly.yml"\n'
        'job = "nightly-regression"\n'
        'layers = ["unit", "integration", "e2e"]\n'
        'purpose = "current FULL suite (R1+R2) regression; result fetch future; not a local gate"\n\n'
        "[layout]\n\n"
        "[layout.devon]\n"
        'writable = ["tracks/", "tests/unit/"]\n\n'
        "[layout.shield]\n"
        'writable = ["tests/integration/", "tests/e2e/", "tests/e2e_live/", '
        '"tests/assets/", "tests/counterexamples/"]\n'
    )
    toml.write_text(body, encoding="utf-8")


def _read_jsonl(host_repo, name):
    path = host_repo / name
    if not path.exists():
        return None
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# AC-FR0250-01@v0.6 TRACKS-TRACE classification deterministic full collect
def test_classification_deterministic_full_collect_counts(trac, host_repo, event_log):
    """Pre-WRITE R1 snapshot + post-WRITE full COLLECT classify against it:
    the single test.baseline_captured(passed) precedes the first Shield WRITE
    dispatch and stamps per-node digests of the inherited tree (here: one
    historical UNIT node); the single test.collected(passed) then carries
    inherited_r1/delta_r2/removed counts and a per_node_blob table in which
    only the pre-Shield node reads r1 -- proving the baseline source is this
    run's prior-to-Shield capture, never a post-WRITE mutable-tree re-guess."""
    _seed_historical_tests(host_repo)
    hist_node = _hist_unit_node_id()
    run_id = walk_to_m_test_complete(trac)
    evs = event_log(run_id)

    # v5 R1 snapshot: captured at M-TEST entry, before the first Shield WRITE.
    baselines = [e for e in evs if e["type"] == "test.baseline_captured"]
    assert baselines, "no test.baseline_captured: M-TEST entry skipped the pre-WRITE R1 snapshot"
    assert len(baselines) == 1
    baseline = baselines[0]["payload"]
    assert baseline["status"] == "passed"
    assert baseline.get("baseline_id"), "test.baseline_captured lacks its stamped identity"
    assert baseline.get("baseline_tree"), "test.baseline_captured lacks the tree identity"
    assert sorted(baseline.get("layers") or []) == ["e2e", "integration", "unit"]
    entered_m_test = min(
        e["seq"]
        for e in evs
        if e["type"] == "stage.entered" and e["payload"].get("stage") == "M-TEST"
    )
    first_write = _first_m_test_shield_write_seq(evs)
    assert first_write is not None
    assert entered_m_test < baselines[0]["seq"] < first_write, (
        "normative event order violated: stage.entered(M-TEST) < test.baseline_captured "
        "< first Shield WRITE dispatch (prior-to-Shield source, not a post-WRITE "
        "mutable-tree guess; order -- not reducer-atomic I/O -- is the guarantee)"
    )
    digest_blob = host_repo / baseline["node_digest_blob"]
    stamped = {entry["node"]: entry for entry in json.loads(digest_blob.read_text(encoding="utf-8"))}
    assert set(stamped) == {hist_node}, (
        "the snapshot must cover exactly the inherited (pre-Shield) inventory, not the post-WRITE tree"
    )
    assert stamped[hist_node]["digest"] == _file_digest(host_repo / _HIST_UNIT_FILE)

    collected = [e for e in evs if e["type"] == "test.collected"]
    assert len(collected) == 1
    payload = collected[0]["payload"]
    expected_unit = _collected_node_ids(host_repo, "unit")
    expected_integration = _collected_node_ids(host_repo, "integration")
    expected_e2e = _collected_node_ids(host_repo, "e2e")
    assert hist_node in expected_unit
    expected_total = len(expected_unit) + len(expected_integration) + len(expected_e2e)
    assert payload["status"] == "passed"
    assert payload.get("inherited_r1") == 1, (
        "the seeded historical unit node must classify r1 against the pre-WRITE snapshot"
    )
    assert payload.get("delta_r2") == expected_total - 1
    assert payload.get("removed") == 0
    blob_ref = payload.get("per_node_blob")
    assert blob_ref, "test.collected payload lacks the per_node_blob reference"
    blob = json.loads((host_repo / blob_ref).read_text(encoding="utf-8"))
    classes = {entry["node"]: entry for entry in blob}
    assert set(classes) == {
        *expected_unit,
        *expected_integration,
        *expected_e2e,
    }
    assert classes[hist_node]["class"] == "r1"
    assert classes[hist_node]["layer"] == "unit", "collect classification must include the unit layer"
    for node, entry in classes.items():
        if node != hist_node:
            assert entry["class"] == "r2", node

    # WAL/replay reuse: every selection of this run binds the stamped capture.
    selections = [e for e in evs if e["type"] == "test.selected"]
    assert selections and all(
        s["payload"].get("baseline") == baseline["baseline_id"] for s in selections
    ), "selections must reuse the same stamped baseline capture"


# AC-FR0250-02@v0.6 TRACKS-TRACE select r2 only executes delta never history
def test_select_r2_only_executes_delta_never_history(trac, host_repo, event_log):
    """RED_CHECK selects all R2/T-DELTA nodes (Shield int/e2e here; the seeded
    historical unit node is R1), emits test.selected(scope=r2_delta) BEFORE
    executing, runs exactly the run_selected expansion of those nodeids, and
    never invokes the full run command nor any historical node nor injects
    concurrency flags outside the contract string."""
    _seed_historical_tests(host_repo)
    hist_node = _hist_unit_node_id()
    run_id = walk_to_m_test(trac)
    _drive_to_m_test(trac)
    _install_selection_recorder(host_repo)
    final = trac("run")
    assert final.returncode == 0, final.stderr
    evs = event_log(run_id)

    selections = [e for e in evs if e["type"] == "test.selected"]
    assert selections, "no test.selected event: SELECT_R2 never emitted (scope=r2_delta)"
    selection = selections[0]["payload"]
    assert selection.get("scope") == "r2_delta"

    expected_integration = _collected_node_ids(host_repo, "integration")
    expected_e2e = _collected_node_ids(host_repo, "e2e")
    expected_selected = sorted([*expected_integration, *expected_e2e])
    assert sorted(selection.get("nodes") or []) == expected_selected
    assert selection.get("selection_id"), "test.selected lacks its selection identity"
    reds = [e for e in evs if e["type"] == "red.validated"]
    assert reds and reds[0]["payload"]["status"] == "valid"
    assert selections[0]["seq"] < reds[0]["seq"], "execution evidence precedes test.selected"

    full_invocations = [
        entry
        for name in ("full-integration.jsonl", "full-e2e.jsonl", "full-unit.jsonl")
        for entry in (_read_jsonl(host_repo, name) or [])
    ]
    assert not full_invocations, "M-TEST executed the full run command instead of run_selected"

    for layer, tag, logfile in (
        ("integration", "sel-integration", "sel-integration.jsonl"),
        ("e2e", "sel-e2e", "sel-e2e.jsonl"),
    ):
        invocations = _read_jsonl(host_repo, logfile)
        assert invocations, f"run_selected never executed for the {layer} section"
        recorded = [entry for entry in invocations if entry["tag"] == tag]
        assert len(recorded) == 1
        assert recorded[0]["argv"] == sorted(expected_e2e if layer == "e2e" else expected_integration)
        assert not any(
            flag in recorded[0]["argv"]
            for flag in ("-n", "--dist", "-p", "xdist", "--junitxml")
        ), f"Runtime injected concurrency/junit flags into the {layer} selection command"
        # v6 result channel: Runtime substituted exactly one {result} path per
        # command -- unique by run/command, ending in .xml, never a literal
        # placeholder left unsubstituted. (The staging XML itself is consumed
        # evidence: production unlinks it after a normal parse.)
        result_path = recorded[0].get("result")
        assert isinstance(result_path, str) and result_path.endswith(".xml"), (
            f"run_selected did not receive a Runtime-provided {{result}} JUnit path ({layer})"
        )
        assert "{result}" not in result_path and "{nodes}" not in result_path

    # Machine-readable per-node audit channel: production parses each command's
    # JUnit XML then unlinks it (temp staging), persisting the normalized
    # per-node outcomes table on red.validated(outcomes_ref). Assert the exact
    # selected nodes and their pass/fail outcomes from that durable blob --
    # never from the cleaned-up temp XML.
    outcomes_ref = reds[0]["payload"].get("outcomes_ref")
    assert outcomes_ref, "valid red.validated lacks its persisted outcomes_ref"
    outcomes = json.loads((host_repo / outcomes_ref).read_text(encoding="utf-8"))
    by_node = {outcome["node"]: outcome for outcome in outcomes}
    assert sorted(by_node) == expected_selected, (
        "persisted outcomes must cover exactly the selected node set"
    )
    for node in expected_selected:
        assert by_node[node]["status"] == "failed", node
        assert by_node[node]["classification"] == "assertion_failure", node

    # Uniqueness: distinct commands received distinct {result} paths.
    seen_results = [
        entry["result"]
        for name in ("sel-integration.jsonl", "sel-e2e.jsonl")
        for entry in (_read_jsonl(host_repo, name) or [])
    ]
    assert len(set(seen_results)) == len(seen_results), (
        "{result} paths must be unique per run/command"
    )

    # No historical execution: the R1 unit node never enters any execution
    # record (SELECT_R2 stays actual-R2-only even though [unit] is collected).
    assert not (host_repo / "sel-unit.jsonl").exists(), (
        "M-TEST executed the unit selection command; historical/R1 nodes must never execute in M-TEST"
    )
    for name in ("sel-integration.jsonl", "sel-e2e.jsonl"):
        for entry in _read_jsonl(host_repo, name) or []:
            assert hist_node not in entry["argv"], (
                "historical unit node leaked into an M-TEST execution record"
            )


# AC-FR0250-04@v0.6 TRACKS-TRACE removed baseline node blocks M-TEST exit
def test_removed_baseline_node_fail_closed_blocks_mtest_exit(trac, host_repo, event_log):
    """A baseline historical node missing from full collect is fail-closed
    REMOVED (AC-FR0250-04): the collect carries error_class=asset_deleted
    evidence plus an actionable test_defect verdict, and between that
    detection and the subsequent Shield repair WRITE re-dispatch NOTHING may
    go through -- no valid red, no stage.exited(M-TEST), no run.completed
    (silent de-registration or continued gating is the defect behavior).

    The repair WRITE may legitimately restore the deleted asset (a worktree-
    published rewrite brings back the committed file), so an eventual exit IS
    legal -- but only via the full repair chain: a later full collect passes
    again classifying the restored node R1 against the UNCHANGED pre-WRITE
    snapshot, then SELECT_R2 executes the R2 delta to a valid red before the
    stage exits. A silent pass -- exiting without the defect evidence or
    without that restored-R1 + R2-execution chain -- still fails here."""
    _seed_historical_tests(host_repo)
    hist_node = _hist_unit_node_id()
    run_id = walk_to_m_test(trac)
    # Park where the snapshot is DURABLE (not merely "status says M-TEST"):
    # stage.entered(M-TEST) < test.baseline_captured persisted, no Shield WRITE.
    parked_baseline = _drive_to_persisted_baseline(trac, run_id, event_log)
    assert _HIST_UNIT_FILE in json.dumps(
        (host_repo / parked_baseline["payload"]["node_digest_blob"]).read_text(encoding="utf-8")
    ), "parked snapshot must cover the inherited inventory that is about to lose a node"
    (host_repo / _HIST_UNIT_FILE).unlink()  # inherited asset disappears post-snapshot
    final = trac("run")
    evs = event_log(run_id)

    # 1) Detection: the full collect fails closed naming every deleted asset...
    detect = next(
        (
            e
            for e in evs
            if e["type"] == "test.collected"
            and e["payload"].get("status") == "failed"
            and any(
                f.get("error_class") == "asset_deleted"
                for f in e["payload"].get("failures") or []
            )
        ),
        None,
    )
    assert detect is not None, (
        "a baseline node missing from full collect must fail closed as "
        "asset_deleted, not be silently de-registered"
    )
    assert any(f.get("node") == hist_node for f in detect["payload"]["failures"]), (
        "the asset_deleted evidence must name exactly the deleted baseline node"
    )
    # ...plus the actionable DIAGNOSE verdict that routes the repair rewrite.
    assert [
        e
        for e in evs
        if e["type"] == "verdict.failed"
        and e["payload"].get("check") == "test_defect"
        and e["seq"] > detect["seq"]
    ], "REMOVED detection must emit an actionable test_defect verdict"

    # 2) The verdict routes a Shield repair WRITE dispatch after detection.
    repair_seq = next(
        (
            e["seq"]
            for e in evs
            if e["seq"] > detect["seq"]
            and e["type"] == "command.issued"
            and e["payload"]["command"]["kind"] == "dispatch_agent"
            and e["payload"]["command"]["params"].get("substate") == "WRITE"
            and e["payload"]["command"]["params"].get("stage") == "M-TEST"
        ),
        None,
    )
    assert repair_seq is not None, "the test_defect verdict must re-dispatch Shield WRITE"

    # 3) Fail-closed window: between detection and the repair dispatch there
    # is no valid red, no M-TEST exit, and no run completion.
    window = [e for e in evs if detect["seq"] < e["seq"] < repair_seq]
    assert not [
        e
        for e in window
        if e["type"] == "red.validated" and e["payload"].get("status") == "valid"
    ], "no valid red may be produced while the REMOVED node is open (pre-repair)"
    assert not [
        e for e in window if e["type"] == "stage.exited" and e["payload"].get("stage") == "M-TEST"
    ], "M-TEST must not exit while the REMOVED node is open (pre-repair)"
    assert not [e for e in window if e["type"] == "run.completed"], (
        "the run must not complete while the REMOVED node is open (pre-repair)"
    )

    # 4) Eventual exit is legal ONLY via repair -> restored-R1 -> R2 executes:
    # the repair restores the committed asset, the next full collect passes
    # again classifying it R1 against the unchanged snapshot, SELECT_R2 runs
    # the delta (never history), and a valid red precedes any stage exit --
    # anything less is the silent pass this journey refuses.
    restored = [
        e
        for e in evs
        if e["type"] == "test.collected"
        and e["payload"].get("status") == "passed"
        and e["seq"] > repair_seq
    ]
    assert restored, (
        "after the repair WRITE full collect must pass again (restored asset), "
        "not keep failing closed forever"
    )
    r1_again = False
    for e in restored:
        per_node = json.loads(
            (host_repo / e["payload"]["per_node_blob"]).read_text(encoding="utf-8")
        )
        entry = next((x for x in per_node if x.get("node") == hist_node), None)
        if entry is not None and entry.get("class") == "r1":
            r1_again = True
            break
    assert r1_again, (
        "a post-repair collect must classify the restored node R1 against the "
        "unchanged pre-WRITE snapshot (never re-baseline the loss away)"
    )
    selection = next(
        (
            e
            for e in evs
            if e["type"] == "test.selected"
            and e["seq"] > repair_seq
            and e["payload"].get("scope") == "r2_delta"
            and hist_node not in (e["payload"].get("nodes") or [])
        ),
        None,
    )
    assert selection is not None, (
        "post-repair RED_CHECK must execute the R2 delta, never the restored history"
    )
    assert any(
        e["type"] == "red.validated"
        and e["payload"].get("status") == "valid"
        and e["seq"] > selection["seq"]
        for e in evs
    ), "the post-repair R2 execution must validate as a legal Red before any exit"
    exited = [
        e for e in evs if e["type"] == "stage.exited" and e["payload"].get("stage") == "M-TEST"
    ]
    completed = [e for e in evs if e["type"] == "run.completed"]
    assert exited and exited[-1]["seq"] > selection["seq"], (
        "no silent pass: M-TEST may exit only after the restored-R1 collect and "
        f"R2 execution chain (final rc={final.returncode})"
    )
    assert completed and completed[-1]["payload"].get("terminal_state") == "boundary", (
        f"the repaired run completes at the boundary (rc={final.returncode})"
    )


_NO_DELTA_STUB_BODY = (
    "import sys\n"
    "mode = sys.argv[1]\n"
    'if mode == "collect":\n'
    "    raise SystemExit(0)\n"
    'print("E   AssertionError: RED_CHECK must refuse an empty feature selection '
    'before executing anything")\n'
    "raise SystemExit(1)\n"
)


def _install_no_delta_contract(host_repo):
    """Tests-only fixture/fake simulation seam: repoint the contract's
    integration/e2e layers at a zero-node stub so the Shield WRITE delta
    (real files land in tests/integration/ + tests/e2e/, satisfying the
    WRITE diff policy) falls OUTSIDE every declared collect scope. The
    post-WRITE full COLLECT can therefore only classify R1 nodes against
    the pre-WRITE snapshot -- exactly the ``SELECT_R2 == []`` state a
    feature M-TEST must refuse (fixture-owned host contract; the real
    tracks-repo project.toml is untouched)."""
    tests_dir = host_repo / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "_rec_no_delta_stub.py").write_text(_NO_DELTA_STUB_BODY, encoding="utf-8")
    toml = host_repo / ".tracks" / "projects" / "project.toml"
    body = (
        "[unit]\n"
        'framework = "pytest"\n'
        'paths = ["tests/unit/"]\n'
        'collect = ".venv/bin/python -m pytest --collect-only -q tests/unit/"\n'
        'run = ".venv/bin/python -m pytest tests/unit/ -q --junitxml={result}"\n'
        'run_selected = ".venv/bin/python -m pytest {nodes} -q --junitxml={result}"\n'
        'cwd = "."\n\n'
        "[integration]\n"
        'framework = "pytest"\n'
        'paths = ["tests/integration/"]\n'
        'collect = ".venv/bin/python tests/_rec_no_delta_stub.py collect"\n'
        'run = ".venv/bin/python tests/_rec_no_delta_stub.py run-integration {result}"\n'
        'run_selected = ".venv/bin/python tests/_rec_no_delta_stub.py sel-integration '
        '{nodes} {result}"\n'
        'cwd = "."\n\n'
        "[e2e]\n"
        'framework = "pytest"\n'
        'paths = ["tests/e2e/"]\n'
        'collect = ".venv/bin/python tests/_rec_no_delta_stub.py collect"\n'
        'run = ".venv/bin/python tests/_rec_no_delta_stub.py run-e2e {result}"\n'
        'run_selected = ".venv/bin/python tests/_rec_no_delta_stub.py sel-e2e {nodes} {result}"\n'
        'cwd = "."\n\n'
        "[nightly]\n"
        'schedule = "0 3 * * *"\n'
        'workflow = ".github/workflows/nightly.yml"\n'
        'job = "nightly-regression"\n'
        'layers = ["unit", "integration", "e2e"]\n'
        'purpose = "current FULL suite (R1+R2) regression; result fetch future; not a local gate"\n\n'
        "[layout]\n\n"
        "[layout.devon]\n"
        'writable = ["tracks/", "tests/unit/"]\n\n'
        "[layout.shield]\n"
        'writable = ["tests/integration/", "tests/e2e/", "tests/e2e_live/", '
        '"tests/assets/", "tests/counterexamples/"]\n'
    )
    toml.write_text(body, encoding="utf-8")


# AC-FR0250-02@v0.6 TRACKS-TRACE feature empty r2 fail closed public journey
def test_empty_r2_feature_mtest_fail_closed(trac, host_repo, event_log):
    """Public M-TEST journey: a feature run whose post-WRITE full COLLECT
    yields ZERO R2 nodes against the pre-WRITE snapshot is fail-closed --
    verdict.failed(check=empty_r2 | contract_error), no prism.verdict, and
    no stage.exited(M-TEST) (never a vacuous pass; FR-0244 regression).

    The Shield-no-test-delta state is produced by a tests-only simulation
    seam (``_install_no_delta_contract``): the pre-WRITE baseline is really
    captured at M-TEST entry, Shield really writes its integration/e2e
    delta (WRITE diff policy satisfied), but the delta lands outside every
    declared collect scope, so classification against the stamped snapshot
    admits no R2 node.

    Hotfix bypass boundary: the explicit unit-only increment bypass keeps
    its pure-helper regression
    (tests/unit/test_select_pure_functions.py::test_require_nonempty_r2_selection_feature_fail_closed_hotfix_bypass)
    plus the existing AC-FR0244-04 hotfix journey
    (tests/integration/test_hotfix_mtest.py::test_empty_shield_increment_release_with_unit_closure);
    that journey cannot yet drive past the IF-HOTFIX-010 resolver seam into
    M-TEST, so a public bypass-through-this-gate regression stays deferred
    until it lands -- documented boundary, not an omission."""
    _seed_historical_tests(host_repo)
    run_id = walk_to_m_test(trac)
    # Park where the snapshot is DURABLE (not merely "status says M-TEST"):
    # the no-delta contract swap below must not race an uncaptured baseline.
    _drive_to_persisted_baseline(trac, run_id, event_log)
    _install_no_delta_contract(host_repo)
    final = trac("run")
    evs = event_log(run_id)

    # The empty selection is computed against THIS run's pre-WRITE stamped
    # capture: it exists, passed, and sits in the normative event order
    # stage.entered(M-TEST) < test.baseline_captured < first Shield WRITE.
    baselines = [e for e in evs if e["type"] == "test.baseline_captured"]
    assert baselines, "no test.baseline_captured: the empty-R2 judgment has no pre-WRITE source"
    assert baselines[0]["payload"]["status"] == "passed"
    entered_m_test = min(
        e["seq"]
        for e in evs
        if e["type"] == "stage.entered" and e["payload"].get("stage") == "M-TEST"
    )
    first_write = _first_m_test_shield_write_seq(evs)
    assert first_write is not None
    assert entered_m_test < baselines[0]["seq"] < first_write, (
        "baseline capture must sit between M-TEST entry and this run's first "
        "Shield WRITE dispatch (Shield no-delta means: nothing new relative to "
        "THAT snapshot)"
    )

    # Feature gate: fail closed on the empty R2 selection.
    refusals = [
        e
        for e in evs
        if e["type"] == "verdict.failed"
        and e["payload"].get("check") in ("empty_r2", "contract_error")
    ]
    assert refusals, (
        "feature M-TEST with an empty R2 selection passed vacuously: no "
        "verdict.failed(check=empty_r2|contract_error) was emitted "
        f"(final rc={final.returncode})"
    )

    # The refusal routes upstream of peer review and exit: Prism never sees
    # the run and M-TEST never exits. (The M-DESIGN prism.verdict earlier in
    # the same run is upstream design review, not the M-TEST PRISM_REVIEW.)
    assert not [e for e in m_test_events(evs) if e["type"] == "prism.verdict"], (
        "an empty-R2 feature run must be refused before PRISM_REVIEW"
    )
    exited = [
        e for e in evs if e["type"] == "stage.exited" and e["payload"].get("stage") == "M-TEST"
    ]
    completed = [e for e in evs if e["type"] == "run.completed"]
    assert not exited and not completed, (
        "an empty-R2 feature run must never exit M-TEST; run instead ended with: "
        + (completed[0]["payload"].get("terminal_state", "?") if completed else "stage.exited")
    )
    valid_reds = [
        e for e in evs if e["type"] == "red.validated" and e["payload"].get("status") == "valid"
    ]
    assert not valid_reds, "an empty selection must not validate as a legal Red"


# AC-FR0250-02@v0.6 TRACKS-TRACE m-test v3 timing red before prism
def test_red_validated_precedes_prism_verdict(trac, event_log):
    """v3 M-TEST substate timing: WRITE -> COLLECT -> RED_CHECK(SELECT_R2) ->
    PRISM_REVIEW -> EXIT, so red.validated lands before prism.verdict and
    Prism consumes current-tree runtime evidence. (Both events are read from
    the M-TEST slice: the upstream M-DESIGN prism.verdict precedes the stage
    and says nothing about M-TEST ordering.)"""
    run_id = walk_to_m_test_complete(trac)
    evs = m_test_events(event_log(run_id))
    types = [(e["type"], e["seq"]) for e in evs]
    red = next(seq for t, seq in types if t == "red.validated")
    prism = next(seq for t, seq in types if t == "prism.verdict")
    assert red < prism, (
        f"red.validated(seq={red}) must precede prism.verdict(seq={prism}) "
        "(v3 M-TEST timing: RED_CHECK before PRISM_REVIEW)"
    )


# AC-FR0251-02@v0.6 TRACKS-TRACE upstream task IF change stales and reselects
def test_select_task_stale_reselects_on_upstream_change(host_repo):
    """A changed task IF set stales prior selection/evidence before rerun."""
    project = host_repo / ".tracks" / "projects"
    vdir = project / "v0.5"
    vdir.mkdir(parents=True)
    (project / "project.toml").write_text(
        "[unit]\nframework='pytest'\npaths=['tests/unit/']\n"
        "collect='.venv/bin/python -m pytest --collect-only -q tests/unit/'\n"
        "run='.venv/bin/python -m pytest -q tests/unit/ --junitxml={result}'\n"
        "run_selected='.venv/bin/python -m pytest -q {nodes} --junitxml={result}'\n"
        "cwd='.'\n\n"
        "[integration]\nframework='pytest'\npaths=['tests/integration/']\n"
        "collect='.venv/bin/python -m pytest --collect-only -q tests/integration/'\n"
        "run='.venv/bin/python -m pytest -q tests/integration/ --junitxml={result}'\n"
        "run_selected='.venv/bin/python -m pytest -q {nodes} --junitxml={result}'\n"
        "cwd='.'\n\n"
        "[e2e]\nframework='pytest'\npaths=['tests/e2e/']\n"
        "collect='.venv/bin/python -m pytest --collect-only -q tests/e2e/'\n"
        "run='.venv/bin/python -m pytest -q tests/e2e/ --junitxml={result}'\n"
        "run_selected='.venv/bin/python -m pytest -q {nodes} --junitxml={result}'\n"
        "cwd='.'\n\n"
        "[nightly]\nschedule='0 3 * * *'\nworkflow='.github/workflows/nightly.yml'\n"
        "job='nightly-regression'\nlayers=['unit','integration','e2e']\n"
        "purpose='scheduled FULL regression'\n",
        encoding="utf-8",
    )
    (vdir / "test-plan.md").write_text(
        "# Test plan\n\n## 8. AC Coverage\n\n"
        "| AC id | layer | test | IF |\n|---|---|---|---|\n"
        "| AC-FR0251-01 | integration | tests/integration/test_if_a.py | IF-A |\n"
        "| AC-FR0251-02 | integration | tests/integration/test_if_b.py | IF-B |\n",
        encoding="utf-8",
    )
    for layer in ("unit", "integration", "e2e"):
        (host_repo / "tests" / layer).mkdir(parents=True)
    (host_repo / "tests" / "unit" / "test_target.py").write_text(
        "def test_target():\n    assert True\n", encoding="utf-8"
    )
    (host_repo / "tests" / "integration" / "test_if_a.py").write_text(
        "def test_if_a():\n    assert True\n", encoding="utf-8"
    )
    (host_repo / "tests" / "integration" / "test_if_b.py").write_text(
        "def test_if_b():\n    assert True\n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "add", ".tracks/projects", "tests"],
        cwd=host_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "seed task selection"],
        cwd=host_repo,
        check=True,
        capture_output=True,
    )
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=host_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    store = Store(host_repo / ".tracks")
    store.append("RUN", "v0.5", "story.requested", {"raw_chars": 1})
    executor = Executor(store, host_repo, "RUN")
    task = TaskNode(
        task_id="T-STALE",
        issue_number=47,
        description="selection stale integration",
        ac_refs=("AC-FR0251-02",),
        fr_refs=("FR-0251",),
        if_ids=("IF-A",),
        test_refs=("tests/unit/test_target.py::test_target",),
        scope_boundary="tracks/selection.py",
        depends_on=(),
        batch="1",
        parallel=False,
        budget=3,
    )
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {
            "task_id": task.task_id,
            "task": asdict(task),
            "manifest": {
                "task_id": task.task_id,
                "allowed_paths": ["tests/unit/test_target.py"],
                "forbidden_paths": [".tracks/projects/**"],
            },
        },
    )
    store.append(
        "RUN",
        "v0.5",
        "red.checkpointed",
        {"task_id": task.task_id, "attempt": 1, "r_sha": baseline},
    )
    state = store.state("RUN")
    first = executor._execute_task_selection(
        Command("run_task_gates", command_id="C-FIRST"), state, str(host_repo), task
    )
    store.append(
        "RUN",
        "v0.5",
        "green.committed",
        {
            "task_id": task.task_id,
            "evidence_ids": first["evidence_ids"],
            "identity_basis": first["identity_basis"],
        },
    )

    changed_task = replace(task, if_ids=("IF-B",))
    second = executor._execute_task_selection(
        Command("run_task_gates", command_id="C-SECOND"),
        state,
        str(host_repo),
        changed_task,
    )
    events = list(store.events("RUN"))
    selections = [ev for ev in events if ev.type == "test.selected"]
    stale = next(ev for ev in events if ev.type == "evidence.staled")
    assert selections[0].seq < stale.seq < selections[1].seq
    assert selections[0].payload["selection_id"] == first["selection_id"]
    assert selections[1].payload["selection_id"] == second["selection_id"]
    assert first["selection_id"] != second["selection_id"]
    assert selections[1].payload["task_ifs"] == ["IF-B"]
    assert stale.payload["reason"] == "task_if_changed,selected_nodes_changed"
    targets = {(item["kind"], item["ref"]) for item in stale.payload["targets"]}
    assert ("selection", first["selection_id"]) in targets
    assert {("evidence", ref) for ref in first["evidence_ids"]} <= targets
    assert set(first["evidence_ids"]).isdisjoint(second["evidence_ids"])
    assert not [ev for ev in events if ev.type == "evidence.reused"]
