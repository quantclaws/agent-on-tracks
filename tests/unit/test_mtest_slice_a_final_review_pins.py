"""Final Slice A code-review RED pins (D-41 follow-up, second batch): M-TEST
error-status JUnit classification, undecodable collected source, REMOVED
asset routing, result-staging hygiene, and crash/reconcile selection staleness.

Executor-handler journeys driven through the ``object.__new__(Executor)``
harness (same seam as tests/unit/test_mtest_gate_review_pins.py): events are
captured via a patched ``_emit``, contracts/subprocess are stubbed per test,
and every pinned defect must surface as an assertion against the emitted
event contract -- never as assembly noise.

The harness/contract/seed helpers live in
``tests/unit/m_test_executor_support.py`` (shared with
tests/unit/test_mtest_gate_review_pins.py); this suite additionally injects a
``backend`` into the harness (classification token) and keeps its JUnit
writer/dispatch stubs local.

Pinned final review findings:

- FA-1: a JUnit case with ``status=error`` carrying an ordinary fixture/
  setup/import ValueError is ALWAYS illegit (collection_error -> verdict
  test_defect), never the behavioral-Red default ``assertion_failure``;
  infrastructure keywords outrank an AssertionError mention in the same
  record. Failed-status behavioral records stay legit (control).
- FA-2: latin-1/PEP 263 or invalid-UTF-8 collected source routes the
  pre-WRITE baseline capture through ``baseline_defect`` -- the handler
  must not crash on UnicodeDecodeError nor strand the command pending.
- FA-3: REMOVED nodes (``asset_deleted``) emit actionable
  ``verdict.failed(check=test_defect)`` evidence naming the deleted asset,
  so upstream routes a Shield rewrite instead of blindly re-dispatching.
- FA-4: the per-run ``{result}`` staging XML is unlinked after a normal
  parse success AND after every handled failure; the empty per-run staging
  directory is removed when possible.
- FA-5: crash/reconcile replay of a command whose ``test.selected`` is
  already persisted refuses stale identity fail-closed WITHOUT executing
  when the recomputed selection_id/tree_stamp differs from the persisted
  record; an exact match may continue (control).
"""

from __future__ import annotations

import shlex
import sys
from types import SimpleNamespace

import pytest

from tests.unit.m_test_executor_support import (
    _RUN_ID,
    _cmd,
    _commit_delta,
    _contract,
    _fake_subprocess,
    _install_three_layer_contract,
    _make_harness,
    _of_type,
    _patch_load_contract,
    _proc,
    _section,
    _seed_baseline,
    _seed_collected,
    _State,
)
from tracks.executor import executor as executor_module
from tracks.executor.executor import Executor
from tracks.executor.test_select import make_selection_id

# -- harness -----------------------------------------------------------------


def _harness(monkeypatch, tmp_path, run_id=_RUN_ID):
    ex, store, emitted, repo = _make_harness(monkeypatch, tmp_path, run_id=run_id)
    ex.backend = SimpleNamespace(token=lambda *a, **k: "test_defect")
    return ex, store, emitted, repo


_WRITER_BODY = """
import sys
kind, sentinel = sys.argv[1], sys.argv[2]
nodes, result = sys.argv[3:-1], sys.argv[-1]
open(sentinel, 'a').write('run\\n')
if kind == 'malformed':
    open(result, 'w', encoding='utf-8').write('<testsuites><oops>')
    raise SystemExit(1)
cases = ''
for n in nodes:
    cls = n.partition('::')[0][:-3].replace('/', '.')
    name = n.partition('::')[2]
    if kind == 'failure_assert':
        cases += (
            '<testcase classname="%s" name="%s">'
            '<failure message="AssertionError: recorded red">'
            'E   AssertionError: recorded red</failure></testcase>' % (cls, name)
        )
    elif kind == 'error_valueerror':
        cases += (
            '<testcase classname="%s" name="%s">'
            '<error message="ValueError: fixture db init blew up">'
            'E   ValueError: fixture db init blew up</error></testcase>' % (cls, name)
        )
    elif kind == 'error_infra_assert':
        cases += (
            '<testcase classname="%s" name="%s">'
            '<error message="ERROR collecting %s">'
            'Traceback while collecting\\nE   AssertionError during collection'
            '</error></testcase>' % (cls, name, n)
        )
head = '<?xml version="1.0" encoding="utf-8"?>\\n<testsuites><testsuite name="s">'
open(result, 'w', encoding='utf-8').write(head + cases + '</testsuite></testsuites>')
raise SystemExit(1)
"""


def _writer_template(tmp_path, kind):
    """run_selected template over a stubbed writer script (outside the host
    repo so it never enters a tree stamp); the script emits ``kind`` JUnit."""
    script = tmp_path / "writers" / f"{kind}.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(_WRITER_BODY, encoding="utf-8")
    sentinel = tmp_path / "sentinel.log"
    return (
        f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} "
        f"{kind} {shlex.quote(str(sentinel))} {{nodes}} {{result}}"
    )


def _junit_dispatch(monkeypatch, kind, executed):
    """Stub non-git subprocess runs: record the argv as EXECUTED and write
    the ``kind`` JUnit record to the Runtime-provided {result} path."""

    def _dispatch(argv):
        executed.append(list(argv))
        result_path = argv[-1]
        # full argv: [python, script, kind, sentinel, *nodes, result]
        nodeids = argv[4:-1]
        cases = ""
        for n in nodeids:
            cls = n.partition("::")[0][:-3].replace("/", ".")
            name = n.partition("::")[2]
            if kind == "failure_assert":
                cases += (
                    f'<testcase classname="{cls}" name="{name}">'
                    '<failure message="AssertionError: recorded red">'
                    "E   AssertionError: recorded red</failure></testcase>"
                )
            elif kind == "malformed":
                _write_result(result_path, "<testsuites><oops>")
                return _proc(argv, 1)
            elif kind == "error_valueerror":
                cases += (
                    f'<testcase classname="{cls}" name="{name}">'
                    '<error message="ValueError: fixture db init blew up">'
                    "E   ValueError: fixture db init blew up</error></testcase>"
                )
            elif kind == "error_infra_assert":
                cases += (
                    f'<testcase classname="{cls}" name="{name}">'
                    f'<error message="ERROR collecting {n}">'
                    "Traceback while collecting\nE   AssertionError during collection"
                    "</error></testcase>"
                )
        xml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<testsuites><testsuite name="s">' + cases + "</testsuite></testsuites>"
        )
        _write_result(result_path, xml)
        return _proc(argv, 1)

    _fake_subprocess(monkeypatch, _dispatch)


def _write_result(result_path, content):
    with open(result_path, "w", encoding="utf-8") as handle:
        handle.write(content)


# -- FA-1: status=error records are always illegit ----------------------------


_NODE_ERR = "tests/integration/test_err.py::test_err"


def _seed_r2_selection(store, node, run_id=_RUN_ID):
    _seed_baseline(
        store,
        [{"node": node, "layer": "integration", "digest": "d0", "node_digest": "d0"}],
        run_id=run_id,
    )
    _seed_collected(store, [{"node": node, "layer": "integration", "class": "r2"}], run_id=run_id)


def test_error_status_valueerror_is_never_a_legit_red(monkeypatch, tmp_path):
    """FA-1: an ``<error>`` record carrying an ordinary fixture/setup
    ValueError is infrastructure failure, not behavioral Red. It must route
    illegit (collection_error findings -> verdict.failed(test_defect));
    defaulting it to legit ``assertion_failure`` validates a broken run."""
    ex, store, emitted, _repo = _harness(monkeypatch, tmp_path)
    _seed_r2_selection(store, _NODE_ERR)
    executed: list = []
    _junit_dispatch(monkeypatch, "error_valueerror", executed)
    _patch_load_contract(monkeypatch,
        lambda repo_: _contract(integration=_section(run_selected=_writer_template(tmp_path, "error_valueerror"))),
    )

    ex._do_run_tests(_cmd("c-err"), _State(), None, False)

    assert executed, "the selected node must have been executed and recorded"
    valid_reds = [
        e for e in _of_type(emitted, "red.validated")
        if e["payload"].get("status") == "valid"
    ]
    assert not valid_reds, (
        "an error-status ValueError record was validated as a LEGIT red "
        f"(assertion_failure default): {[e['payload'] for e in _of_type(emitted, 'red.validated')]}"
    )
    invalid = [
        e for e in _of_type(emitted, "red.validated")
        if e["payload"].get("status") == "invalid"
    ]
    assert invalid, "the error-status record must invalidate the red gate"
    classifications = [f["classification"] for f in invalid[0]["payload"].get("findings", [])]
    assert classifications == ["collection_error"], (
        f"expected exact illegit class collection_error, got {classifications}"
    )
    defects = [
        e
        for e in _of_type(emitted, "verdict.failed")
        if e["payload"].get("check") == "test_defect"
    ]
    assert defects, "error-status ValueError must route verdict.failed(test_defect)"


def test_infra_keywords_outrank_assertion_error_in_error_records(monkeypatch, tmp_path):
    """FA-1: within an error-status record mentioning BOTH infrastructure
    failure shapes and AssertionError, the infrastructure keyword wins --
    collection_error, never the legit assertion_failure default."""
    ex, store, emitted, _repo = _harness(monkeypatch, tmp_path)
    _seed_r2_selection(store, _NODE_ERR)
    executed: list = []
    _junit_dispatch(monkeypatch, "error_infra_assert", executed)
    _patch_load_contract(monkeypatch,
        lambda repo_: _contract(integration=_section(run_selected=_writer_template(tmp_path, "error_infra_assert"))),
    )

    ex._do_run_tests(_cmd("c-infra"), _State(), None, False)

    valid_reds = [
        e for e in _of_type(emitted, "red.validated")
        if e["payload"].get("status") == "valid"
    ]
    assert not valid_reds, (
        "an ERROR-collecting record with an AssertionError mention was "
        f"validated as a legit red: {[e['payload'] for e in _of_type(emitted, 'red.validated')]}"
    )
    invalid = [
        e for e in _of_type(emitted, "red.validated")
        if e["payload"].get("status") == "invalid"
    ]
    assert invalid, "the mixed infra/assertion error record must invalidate the gate"
    classifications = [f["classification"] for f in invalid[0]["payload"].get("findings", [])]
    assert classifications == ["collection_error"], (
        f"infrastructure keywords must outrank AssertionError, got {classifications}"
    )


def test_failed_status_assertion_record_stays_legit(monkeypatch, tmp_path):
    """FA-1 boundary control (kept frozen): a FAILED-status record with a
    plain AssertionError remains the legit behavioral Red default."""
    ex, store, emitted, _repo = _harness(monkeypatch, tmp_path)
    _seed_r2_selection(store, _NODE_ERR)
    executed: list = []
    _junit_dispatch(monkeypatch, "failure_assert", executed)
    _patch_load_contract(monkeypatch,
        lambda repo_: _contract(integration=_section(run_selected=_writer_template(tmp_path, "failure_assert"))),
    )

    ex._do_run_tests(_cmd("c-legit"), _State(), None, False)

    valid_reds = [
        e for e in _of_type(emitted, "red.validated")
        if e["payload"].get("status") == "valid"
    ]
    assert valid_reds, "a failed-status AssertionError record must remain a legit red"
    failures = _of_type(emitted, "verdict.failed")
    assert not failures, "no verdict may accompany a fully legit red"


# -- FA-2: undecodable collected source routes baseline_defect ----------------


def _seed_bad_source_file(repo, bad_kind):
    physical = repo / "tests" / "integration" / "test_bad.py"
    if bad_kind == "pep263_latin1":
        payload = (
            "# -*- coding: latin-1 -*-\n"
            '"""latin-1 module (PEP 263): imports cleanly, unreadable as UTF-8."""\n'
            "\n"
            "\n"
            "def test_bad():\n"
            "    mark = 'caf\xe9'\n"
        )
        physical.write_bytes(payload.encode("latin-1"))
    else:
        physical.write_bytes(b'def test_bad():\n    blob = "\xff\xfe"\n')
    return "tests/integration/test_bad.py::test_bad"


@pytest.mark.parametrize("bad_kind", ["pep263_latin1", "invalid_utf8"])
def test_baseline_capture_routes_baseline_defect_on_undecodable_source(
    monkeypatch, tmp_path, bad_kind
):
    """FA-2: latin-1/PEP 263 and invalid-UTF-8 collected sources are real
    Python-importable inventories whose bytes defeat utf-8 reading. The
    capture must emit baseline_defect fail-closed -- never crash the handler
    on UnicodeDecodeError nor leave the command pending."""
    ex, _store, emitted, repo = _harness(monkeypatch, tmp_path)
    node = _seed_bad_source_file(repo, bad_kind)
    _install_three_layer_contract(
        monkeypatch, unit_rc=5, integration_stdout=node + "\n", e2e_rc=5
    )

    crashed = None
    try:
        ex._do_capture_baseline(_cmd("cap-bad"), _State(), None, False)
    except Exception as exc:  # the pinned defect is precisely such a crash
        crashed = exc

    assert crashed is None, (
        f"baseline capture crashed on undecodable collected source ({bad_kind}) "
        f"instead of routing baseline_defect: {crashed!r}"
    )
    passed = [
        e for e in _of_type(emitted, "test.baseline_captured")
        if e["payload"].get("status") == "passed"
    ]
    assert not passed, "a capture over undecodable source must not persist as passed"
    defects = [
        e
        for e in _of_type(emitted, "verdict.failed")
        if e["payload"].get("check") == "baseline_defect"
    ]
    assert defects, (
        f"undecodable source ({bad_kind}) must route verdict.failed(baseline_defect); "
        f"emitted: {[e['type'] for e in emitted]}"
    )


# -- FA-3: REMOVED asset_deleted carries actionable test_defect evidence ------


_NODE_GONE = "tests/integration/test_gone.py::test_gone"
_NODE_KEPT = "tests/integration/test_kept.py::test_kept"


def test_removed_asset_deleted_emits_actionable_test_defect_verdict(
    monkeypatch, tmp_path
):
    """FA-3: a baseline node absent from full collect is test-asset deletion.
    Besides test.collected(failed, error_class=asset_deleted) the step must
    emit verdict.failed(check=test_defect) evidence naming the deleted node
    BEFORE any Shield rewrite -- without it the router has nothing actionable
    and blindly re-dispatches the same command."""
    ex, store, emitted, repo = _harness(monkeypatch, tmp_path)
    kept_physical = repo / "tests" / "integration" / "test_kept.py"
    kept_physical.write_text("def test_kept():\n    assert True\n", encoding="utf-8")
    _seed_baseline(
        store,
        [
            {"node": _NODE_GONE, "layer": "integration", "digest": "g0", "node_digest": "g0"},
            {"node": _NODE_KEPT, "layer": "integration", "digest": "k0", "node_digest": "k0"},
        ],
    )
    _install_three_layer_contract(
        monkeypatch, unit_rc=5, integration_stdout=_NODE_KEPT + "\n", e2e_rc=5
    )

    ex._do_collect_tests(_cmd("cc-gone"), _State(), None, False)

    failed = [
        e for e in _of_type(emitted, "test.collected")
        if e["payload"].get("status") == "failed"
    ]
    assert failed and any(
        f.get("error_class") == "asset_deleted" and f.get("node") == _NODE_GONE
        for f in failed[0]["payload"].get("failures") or []
    ), f"asset_deleted detection itself regressed: {[e['payload'] for e in failed]}"
    defects = [
        e
        for e in _of_type(emitted, "verdict.failed")
        if e["payload"].get("check") == "test_defect"
    ]
    assert defects, (
        "REMOVED asset_deleted routed NO actionable verdict.failed(test_defect); "
        f"emitted event types: {[e['type'] for e in emitted]}"
    )
    assert _NODE_GONE in str(defects[0]["payload"].get("evidence")), (
        "the test_defect verdict must carry evidence naming the deleted asset: "
        + str(defects[0]["payload"])
    )


# -- FA-4: result staging XML lifecycle ---------------------------------------


def _real_staging_paths(ex, cid):
    import shutil

    result_path = Executor._result_staging_path(ex, cid, "integration")
    shutil.rmtree(result_path.parent, ignore_errors=True)  # drop prior-session residue
    result_path.parent.mkdir(parents=True, exist_ok=True)
    return result_path, result_path.parent


def test_result_xml_unlinked_and_dir_removed_after_parse_success(monkeypatch, tmp_path):
    """FA-4: after a normal parse success the per-command staging XML is
    consumed evidence, not residue -- it must be unlinked and the empty
    per-run staging directory removed."""
    run_id = "RUN-CLEANUP-OK"
    ex, store, emitted, repo = _harness(monkeypatch, tmp_path, run_id=run_id)
    node = "tests/integration/test_clean.py::test_clean"
    _seed_r2_selection(store, node, run_id=run_id)
    executed: list = []
    _junit_dispatch(monkeypatch, "failure_assert", executed)
    _patch_load_contract(monkeypatch,
        lambda repo_: _contract(integration=_section(run_selected=_writer_template(tmp_path, "failure_assert"))),
    )
    result_path, run_dir = _real_staging_paths(ex, "c-clean")

    ex._do_run_tests(_cmd("c-clean"), _State(), None, False)

    valid_reds = [
        e for e in _of_type(emitted, "red.validated")
        if e["payload"].get("status") == "valid"
    ]
    assert valid_reds, "precondition regression: the legit red did not validate"
    assert not result_path.exists(), (
        f"staging XML survived a normal parse success at {result_path}: staging "
        "residue leaks per-attempt records across attempts"
    )
    assert not run_dir.exists(), (
        f"empty per-run staging directory survived at {run_dir}; it must be "
        "removed when possible after its staged results are consumed"
    )


def test_result_xml_unlinked_after_handled_failure(monkeypatch, tmp_path):
    """FA-4: even a HANDLED failure (malformed result -> contract_error)
    leaves no staging XML behind -- handled means cleaned up, not abandoned."""
    run_id = "RUN-CLEANUP-BAD"
    ex, store, emitted, repo = _harness(monkeypatch, tmp_path, run_id=run_id)
    node = "tests/integration/test_badxml.py::test_badxml"
    _seed_r2_selection(store, node, run_id=run_id)
    executed: list = []
    _junit_dispatch(monkeypatch, "malformed", executed)
    _patch_load_contract(monkeypatch,
        lambda repo_: _contract(integration=_section(run_selected=_writer_template(tmp_path, "malformed"))),
    )
    result_path, run_dir = _real_staging_paths(ex, "c-bad")

    ex._do_run_tests(_cmd("c-bad"), _State(), None, False)

    refusals = [
        e
        for e in _of_type(emitted, "verdict.failed")
        if e["payload"].get("check") == "contract_error"
    ]
    assert refusals, "precondition regression: malformed result did not fail closed"
    assert not result_path.exists(), (
        f"staging XML survived a handled parse failure at {result_path}"
    )
    assert not run_dir.exists(), (
        f"empty per-run staging directory survived a handled failure at {run_dir}"
    )


# -- FA-5: reconcile refuses stale persisted selection identity ---------------


def _seed_persisted_selection(ex, store, node, commit):
    """Persist exactly the test.selected record the executor would stamp for
    the CURRENT tree (simulating a crash right after selection publication)."""
    tree_stamp = ex._dirty_tree_stamp()
    selection_id = make_selection_id(
        nodes=[node],
        scope=executor_module._R2_SCOPE,
        basis=executor_module._R2_BASIS,
        baseline="b0",
        commit=commit,
        tree_stamp=tree_stamp,
    )
    store.append(
        _RUN_ID,
        "v0.4",
        "test.selected",
        {
            "scope": executor_module._R2_SCOPE,
            "basis": executor_module._R2_BASIS,
            "nodes_count": 1,
            "nodes": [node],
            "nodes_blob": ".tracks/runtime/blobs/seeded",
            "baseline": "b0",
            "commit": commit,
            "tree_stamp": tree_stamp,
            "selection_id": selection_id,
            "task_id": None,
            "task_ifs": None,
        },
        command_id="c-replay",
    )
    return selection_id


def _replay_harness(monkeypatch, tmp_path):
    ex, store, emitted, repo = _harness(monkeypatch, tmp_path)
    node = "tests/integration/test_delta.py::test_delta"
    commit, _ = _commit_delta(repo, "assert 'v1'")
    _seed_baseline(
        store,
        [{"node": node, "layer": "integration", "digest": "d0", "node_digest": "d0"}],
    )
    _seed_collected(store, [{"node": node, "layer": "integration", "class": "r2"}])
    _seed_persisted_selection(ex, store, node, commit)
    executed: list = []
    _junit_dispatch(monkeypatch, "failure_assert", executed)
    _patch_load_contract(monkeypatch,
        lambda repo_: _contract(integration=_section(run_selected=_writer_template(tmp_path, "failure_assert"))),
    )
    return ex, emitted, repo, executed


def test_replay_with_changed_worktree_refuses_stale_selection_without_executing(
    monkeypatch, tmp_path
):
    """FA-5: a crash/reconcile replay of command c-replay whose persisted
    test.selected stamps a DIFFERENT tree (dirty R2 content changed after the
    crash) must fail closed (stale/contract_error) WITHOUT executing any
    node -- executing against a moved tree under the old selection identity
    is an evidence-integrity hole."""
    ex, emitted, repo, executed = _replay_harness(monkeypatch, tmp_path)
    delta = repo / "tests" / "integration" / "test_delta.py"
    delta.write_text("def test_delta():\n    assert 'v2-changed'\n", encoding="utf-8")
    state = _State()
    state.red_validated = False

    ex._do_run_tests(_cmd("c-replay"), state, None, True)

    assert not executed, (
        f"replayed RED_CHECK EXECUTED R2 nodes against a changed worktree "
        f"under the stale persisted selection: {executed}"
    )
    stale = [
        e
        for e in _of_type(emitted, "verdict.failed")
        if e["payload"].get("check") in ("stale", "contract_error")
    ]
    assert stale, (
        "stale persisted selection identity must refuse via a stale/"
        f"contract_error verdict; emitted: {[e['type'] for e in emitted]}"
    )
    assert not _of_type(emitted, "red.validated"), (
        "a stale-selection replay must never publish red.validated"
    )
    assert not _of_type(emitted, "test.selected"), (
        "the replay must reuse-or-refuse the persisted selection, never re-stamp it"
    )


def test_replay_with_exact_identity_match_may_continue(monkeypatch, tmp_path):
    """FA-5 boundary control: an unchanged worktree recomputes the SAME
    selection_id/tree_stamp as the persisted record, so the replay continues
    normally (executes once, validates)."""
    ex, emitted, _repo, executed = _replay_harness(monkeypatch, tmp_path)
    state = _State()
    state.red_validated = False

    ex._do_run_tests(_cmd("c-replay"), state, None, True)

    assert len(executed) == 1, (
        f"an exact-match replay refused to continue: executed={executed}, "
        f"emitted={[e['type'] for e in emitted]}"
    )
    valid_reds = [
        e for e in _of_type(emitted, "red.validated")
        if e["payload"].get("status") == "valid"
    ]
    assert valid_reds, "an exact-match replay must complete the red validation"
    stale = [
        e
        for e in _of_type(emitted, "verdict.failed")
        if e["payload"].get("check") in ("stale", "contract_error")
    ]
    assert not stale, "an exact-match replay must not be refused as stale"
