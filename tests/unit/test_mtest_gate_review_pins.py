"""Slice A code-review RED pins (D-41 follow-up): M-TEST gate fail-closed
semantics around collect rc handling, result staging, selection identity,
the hotfix increment bypass, cross-layer duplicates and audit-blob failures.

Executor-handler journeys driven through the ``object.__new__(Executor)``
harness (same seam as tests/unit/test_prism_review_payload.py): events are
captured via a patched ``_emit``, contracts/subprocess are stubbed per test,
and every pinned defect must surface as an assertion against the emitted
event contract -- never as assembly noise.

Pinned review findings:

- Review-1: collect rc=4 (pytest usage/path error) is a collection FAILURE;
  only rc=5 may mean an empty declared layer. Applies to the baseline capture
  scan and the post-WRITE COLLECT scan alike.
- Review-4: pre-existing JUnit XML at the same command/layer staging path must
  never satisfy RED_CHECK -- a command that writes no fresh result fails
  closed instead of reusing stale records.
- Review-5: selection/tree identity changes when dirty R2 content changes with
  the same node set and HEAD: ``test.selected`` stamps worktree content
  separately from ``commit`` and the selection_id moves with it.
- Review-6: the empty-R2 hotfix bypass requires an explicit PERSISTED
  ``increment.declared(shield=empty, unit_rows nonempty)`` fact -- never
  merely ``hotfix_issue is not None``.
- Review-7: a node collected by MULTIPLE layers fails collect closed; first-
  layer-wins setdefault masking is defect behavior.
- Review-8: audit-blob write failures (baseline digest table, COLLECT
  per-node table, RED_CHECK outcomes) fail closed -- the runtime never emits a
  passed/valid event carrying a null evidence reference.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from types import SimpleNamespace

from tracks.executor import executor as executor_module
from tracks.executor.executor import Executor
from tracks.store import Store

_RUN_ID = "RUN"


# -- harness -----------------------------------------------------------------


class _State:
    stage = "M-TEST"
    substate = "RED_CHECK"
    current_attempt = 0
    red_validated = False
    hotfix_issue = None
    test_collected = False
    baseline_captured = False


def _cmd(cid):
    return SimpleNamespace(command_id=cid)


def _harness(monkeypatch, tmp_path):
    ex_repo = tmp_path / "host"
    (ex_repo / "tests" / "integration").mkdir(parents=True, exist_ok=True)
    store = Store(ex_repo / ".tracks")
    emitted = []
    monkeypatch.setattr(
        Executor,
        "_emit",
        lambda self, ev, payload, **kw: emitted.append({"type": ev, "payload": payload}),
    )
    ex = object.__new__(Executor)
    ex.store = store
    ex.repo = ex_repo
    ex.run_id = _RUN_ID
    return ex, store, emitted, ex_repo


def _section(collect="collect-placeholder", run_selected=".venv/bin/python -m pytest {nodes} --junitxml={result}"):
    return SimpleNamespace(
        framework="pytest",
        paths=["tests/"],
        collect=collect,
        run="run-placeholder",
        run_selected=run_selected,
        cwd=".",
    )


def _contract(integration, unit=None, e2e=None):
    return SimpleNamespace(
        unit=unit,
        e2e=e2e,
        integration=integration,
        layout=None,
        lint=None,
        nightly=None,
    )


def _fake_subprocess(monkeypatch, dispatch):
    """dispatch: callable(argv) -> CompletedProcess. git invocations (tree
    identity reads inside the handlers) pass through to the real runner."""
    real_run = subprocess.run

    def _run(argv, cwd=None, capture_output=True, text=True):
        argv = list(argv)
        if argv and argv[0] == "git":
            return real_run(argv, cwd=cwd, capture_output=capture_output, text=text)
        return dispatch(argv)

    monkeypatch.setattr(executor_module.subprocess, "run", _run)


def _proc(argv, rc, stdout="", stderr=""):
    return subprocess.CompletedProcess(list(argv), rc, stdout=stdout, stderr=stderr)


def _install_three_layer_contract(monkeypatch, *, unit_rc, integration_stdout, e2e_rc):
    """Stub load_contract + subprocess.run for the full-layer collect scans."""
    monkeypatch.setattr(
        executor_module,
        "load_contract",
        lambda repo: _contract(
            unit=_section(collect="_tracks_collect_unit"),
            integration=_section(collect="_tracks_collect_integration"),
            e2e=_section(collect="_tracks_collect_e2e"),
        ),
    )

    def _dispatch(argv):
        joined = " ".join(argv)
        if "_tracks_collect_unit" in joined:
            return _proc(argv, unit_rc, stderr="ERROR: usage/path error" if unit_rc == 4 else "")
        if "_tracks_collect_e2e" in joined:
            return _proc(argv, e2e_rc)
        if "_tracks_collect_integration" in joined:
            return _proc(argv, 0, stdout=integration_stdout)
        raise AssertionError(f"unexpected subprocess call: {argv}")

    _fake_subprocess(monkeypatch, _dispatch)


def _seed_baseline(store, node, digest="baseline-digest-0"):
    ref = store.write_audit_blob(
        [
            {
                "node": node,
                "layer": "integration",
                "digest": digest,
                "node_digest": digest,
            }
        ]
    )
    store.append(
        _RUN_ID,
        "v0.4",
        "test.baseline_captured",
        {
            "status": "passed",
            "baseline_id": "b0",
            "baseline_tree": "t0",
            "layers": ["unit", "integration", "e2e"],
            "nodes_count": 1,
            "empty_baseline": False,
            "node_digest_blob": f".tracks/runtime/blobs/{ref}",
            "errors": [],
        },
    )


def _seed_collected(store, entries):
    ref = store.write_audit_blob(entries)
    store.append(
        _RUN_ID,
        "v0.4",
        "test.collected",
        {
            "status": "passed",
            "collected_count": len(entries),
            "inherited_r1": sum(1 for e in entries if e["class"] == "r1"),
            "delta_r2": sum(1 for e in entries if e["class"] == "r2"),
            "removed": 0,
            "failures": [],
            "per_node_blob": f".tracks/runtime/blobs/{ref}",
            "errors": [],
        },
    )


def _junit_for(nodes, message="AssertionError: recorded red"):
    cases = "".join(
        '<testcase classname="{}" name="{}">'
        "<failure message=\"{}\">E   {}</failure></testcase>".format(
            node.partition("::")[0][: -3].replace("/", "."),
            node.partition("::")[2],
            message,
            message,
        )
        for node in nodes
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<testsuites><testsuite name="review" tests="{len(nodes)}">'
        f"{cases}</testsuite></testsuites>\n"
    )


def _stage_static_junit(monkeypatch, tmp_path, nodes):
    result_path = tmp_path / "staged-result.xml"
    result_path.write_text(_junit_for(nodes), encoding="utf-8")
    monkeypatch.setattr(
        Executor,
        "_result_staging_path",
        lambda self, command_id, section: result_path,
    )
    return result_path


def _recorder_template(repo):
    script = repo / "tests" / "_rec_review.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "import sys\n"
        "nodes, result = sys.argv[1:-1], sys.argv[-1]\n"
        "cases = ''.join(\n"
        "    '<testcase classname=\"%s\" name=\"%s\">'\n"
        "    '<failure message=\"AssertionError: recorded\">'\n"
        "    'E   AssertionError: recorded</failure></testcase>'\n"
        "    % (n.partition('::')[0][:-3].replace('/', '.'), n.partition('::')[2])\n"
        "    for n in nodes)\n"
        "open(result, 'w', encoding='utf-8').write(\n"
        "    '<?xml version=\"1.0\"?><testsuites><testsuite>%s</testsuite></testsuites>'\n"
        "    % cases)\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    return (
        f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} "
        "{nodes} {result}"
    )


def _of_type(emitted, ev_type):
    return [e for e in emitted if e["type"] == ev_type]


# -- Review-1: rc=4 is a collection failure, only rc=5 is an empty layer -----


def test_collect_rc4_usage_error_fails_closed_not_empty_layer(monkeypatch, tmp_path):
    """Review-1: a layer whose collect command exits 4 (pytest usage/file-not-
    found) is a BROKEN declaration, not an empty inventory -- the scan fails
    closed naming the layer instead of silently contributing zero nodes."""
    ex, _store, _emitted, _repo = _harness(monkeypatch, tmp_path)
    _install_three_layer_contract(
        monkeypatch, unit_rc=4, integration_stdout="", e2e_rc=5
    )
    node_layer, error = ex._collect_all_declared_layers()

    assert error is not None, (
        "collect rc=4 (usage/path error) was swallowed as a legal empty layer"
    )
    assert node_layer is None, "a fail-closed collect scan must not yield a node map"
    assert "unit" in error, f"the failing layer must be identified: {error}"


def test_baseline_capture_routes_baseline_defect_on_rc4(monkeypatch, tmp_path):
    """Review-1 handler routing: rc=4 during the pre-WRITE capture scan emits
    test.baseline_captured(failed) + verdict.failed(baseline_defect) -- never
    a passed capture stamped over a broken inventory."""
    ex, _store, emitted, _repo = _harness(monkeypatch, tmp_path)
    _install_three_layer_contract(
        monkeypatch, unit_rc=4, integration_stdout="", e2e_rc=5
    )
    ex._do_capture_baseline(_cmd("cb1"), _State(), None, False)

    passed = [
        e for e in _of_type(emitted, "test.baseline_captured")
        if e["payload"].get("status") == "passed"
    ]
    assert not passed, (
        "a capture over an rc=4 layer must not persist as passed "
        f"(got payloads: {[e['payload'] for e in _of_type(emitted, 'test.baseline_captured')]})"
    )
    defects = [
        e
        for e in _of_type(emitted, "verdict.failed")
        if e["payload"].get("check") == "baseline_defect"
    ]
    assert defects, "rc=4 collect must route fail-closed via baseline_defect"


def test_all_layers_rc5_remain_a_legal_empty_inventory(monkeypatch, tmp_path):
    """Review-1 boundary control: rc=5 everywhere stays a legal first-ever
    empty capture (no false baseline_defect)."""
    ex, _store, emitted, _repo = _harness(monkeypatch, tmp_path)
    _install_three_layer_contract(monkeypatch, unit_rc=5, integration_stdout="", e2e_rc=5)
    ex._do_capture_baseline(_cmd("cb2"), _State(), None, False)

    passed = [
        e for e in _of_type(emitted, "test.baseline_captured")
        if e["payload"].get("status") == "passed"
    ]
    assert passed, "an all-rc=5 inventory is a legal empty baseline capture"
    assert passed[0]["payload"]["empty_baseline"] is True


# -- Review-4: stale result staging -------------------------------------------


def test_preexisting_stale_xml_cannot_satisfy_red_check(monkeypatch, tmp_path):
    """Review-4: a previous attempt's JUnit record left at the same
    command/layer staging path is poison, not evidence. A run_selected
    command that writes NO fresh result must fail closed (contract_error);
    reusing the stale record would validate a RED that never executed."""
    ex, store, emitted, repo = _harness(monkeypatch, tmp_path)
    node = "tests/integration/test_delta.py::test_delta"
    _seed_baseline(store, node)
    _seed_collected(store, [{"node": node, "layer": "integration", "class": "r2"}])

    result_path = tmp_path / "stale-result.xml"
    result_path.write_text(_junit_for([node]), encoding="utf-8")
    monkeypatch.setattr(
        Executor,
        "_result_staging_path",
        lambda self, command_id, section: result_path,
    )
    monkeypatch.setattr(
        executor_module,
        "load_contract",
        lambda repo_: _contract(
            integration=_section(
                run_selected=f"{shlex.quote(sys.executable)} -c 'pass' {{nodes}} {{result}}"
            )
        ),
    )

    ex._do_run_tests(_cmd("c-stale"), _State(), None, False)

    valid_reds = [
        e for e in _of_type(emitted, "red.validated")
        if e["payload"].get("status") == "valid"
    ]
    assert not valid_reds, (
        "stale XML from a previous command was accepted as the per-node "
        "authority: a command that wrote no fresh result validated a RED"
    )
    refusals = [
        e
        for e in _of_type(emitted, "verdict.failed")
        if e["payload"].get("check") == "contract_error"
    ]
    assert refusals, "missing fresh result must fail closed via contract_error"


# -- Review-5: dirty R2 content re-stamps selection identity ------------------


def test_dirty_r2_content_change_re_stamps_selection_identity(monkeypatch, tmp_path):
    """Review-5: same selected node set + same HEAD + different uncommitted R2
    content => different tree_stamp AND different selection_id. A stable
    selection identity over changed content is an evidence-reuse hole."""
    ex, store, emitted, repo = _harness(monkeypatch, tmp_path)
    delta_file = repo / "tests" / "integration" / "test_delta.py"

    def _write(body):
        delta_file.write_text(f"def test_delta():\n    {body}\n", encoding="utf-8")

    def _git(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    _write("assert 'v1'")
    _git("init", "-q")
    _git("-c", "user.email=t@tracks.dev", "-c", "user.name=t", "add", "tests/")
    _git(
        "-c",
        "user.email=t@tracks.dev",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "-m",
        "delta v1",
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    node = str(delta_file.relative_to(repo)) + "::test_delta"
    _seed_baseline(store, node)
    _seed_collected(store, [{"node": node, "layer": "integration", "class": "r2"}])

    monkeypatch.setattr(
        executor_module,
        "load_contract",
        lambda repo_: _contract(integration=_section(run_selected=_recorder_template(repo))),
    )

    ex._do_run_tests(_cmd("sel-1"), _State(), None, False)
    _write("assert 'v2'")  # dirty edit: same nodeid, same HEAD
    _seed_collected(store, [{"node": node, "layer": "integration", "class": "r2"}])
    ex._do_run_tests(_cmd("sel-2"), _State(), None, False)

    selections = [e["payload"] for e in _of_type(emitted, "test.selected")]
    assert len(selections) == 2, f"expected two stamped selections: {selections}"
    first, second = selections
    assert first["commit"] == second["commit"] == head, (
        "both selections share the same HEAD commit"
    )
    stamp_one, stamp_two = first.get("tree_stamp"), second.get("tree_stamp")
    assert stamp_one is not None and stamp_two is not None, (
        "test.selected must stamp the dirty worktree content (tree_stamp) "
        "separately from commit; got payload keys: " + str(sorted(first))
    )
    assert stamp_one != stamp_two, "dirty content change must move the tree stamp"
    assert second["selection_id"] != first["selection_id"], (
        "selection identity must differ when dirty R2 content differs under "
        "an identical node set and HEAD"
    )


# -- Review-6: explicit persisted increment fact gates the empty-R2 bypass ----


def _seed_empty_r2_context(monkeypatch, store):
    monkeypatch.setattr(
        executor_module,
        "load_contract",
        lambda repo_: _contract(integration=_section()),
    )
    node = "tests/integration/test_hist.py::test_hist"
    _seed_baseline(store, node)
    _seed_collected(store, [{"node": node, "layer": "integration", "class": "r1"}])


def test_hotfix_issue_alone_does_not_bypass_empty_r2(monkeypatch, tmp_path):
    """Review-6: ``hotfix_issue is not None`` is NOT the bypass key. Without an
    explicit persisted increment.declared(shield=empty, unit_rows nonempty)
    fact, an empty R2 selection refuses fail-closed even on a hotfix run."""
    ex, store, emitted, _repo = _harness(monkeypatch, tmp_path)
    _seed_empty_r2_context(monkeypatch, store)
    state = _State()
    state.hotfix_issue = 7
    ex._do_run_tests(_cmd("c-bypass"), state, None, False)

    valid_reds = [
        e for e in _of_type(emitted, "red.validated")
        if e["payload"].get("status") == "valid"
    ]
    assert not valid_reds, (
        "a bare hotfix_issue released an empty R2 selection without any "
        "persisted increment.declared fact"
    )
    refusals = [
        e
        for e in _of_type(emitted, "verdict.failed")
        if e["payload"].get("check") == "empty_r2"
    ]
    assert refusals, "undocumented empty-R2 hotfix increment must refuse via empty_r2"


def test_explicit_increment_declaration_admits_empty_r2_bypass(monkeypatch, tmp_path):
    """Review-6 boundary control: WITH the persisted fact
    increment.declared(shield=empty, unit_rows nonempty) the explicit
    unit-only-increment release stays available (FR-0244)."""
    ex, store, emitted, _repo = _harness(monkeypatch, tmp_path)
    _seed_empty_r2_context(monkeypatch, store)
    store.append(
        _RUN_ID,
        "v0.4",
        "increment.declared",
        {
            "shield": "empty",
            "unit_rows": [{"ac_id": "AC-FR0001-01", "test_id": "tests/unit/t.py::t"}],
            "trace_status": "pass",
        },
    )
    state = _State()
    state.hotfix_issue = 7
    ex._do_run_tests(_cmd("c-bypass"), state, None, False)

    valid_reds = [
        e for e in _of_type(emitted, "red.validated")
        if e["payload"].get("status") == "valid"
    ]
    assert valid_reds, (
        "an explicitly declared unit-only increment admits the empty-R2 bypass"
    )
    assert valid_reds[0]["payload"].get("basis") == "unit-only hotfix increment"


def test_increment_declaration_with_zero_unit_rows_does_not_bypass(monkeypatch, tmp_path):
    """Review-6: the persisted fact must declare a NONEMPTY unit increment;
    shield=empty with zero unit rows carries no increment to release."""
    ex, store, emitted, _repo = _harness(monkeypatch, tmp_path)
    _seed_empty_r2_context(monkeypatch, store)
    store.append(
        _RUN_ID,
        "v0.4",
        "increment.declared",
        {"shield": "empty", "unit_rows": [], "trace_status": "pass"},
    )
    state = _State()
    state.hotfix_issue = 7
    ex._do_run_tests(_cmd("c-bypass"), state, None, False)

    valid_reds = [
        e for e in _of_type(emitted, "red.validated")
        if e["payload"].get("status") == "valid"
    ]
    assert not valid_reds, (
        "shield=empty with zero declared unit rows is not an increment; the "
        "empty-R2 selection must still refuse"
    )


# -- Review-7: duplicate nodes across layers fail collect closed --------------


_DUP_NODE = "tests/integration/test_dup.py::test_dup"


def _install_duplicate_collect_contract(monkeypatch, repo):
    physical = repo / "tests" / "integration" / "test_dup.py"
    physical.parent.mkdir(parents=True, exist_ok=True)
    physical.write_text("def test_dup():\n    assert True\n", encoding="utf-8")
    monkeypatch.setattr(
        executor_module,
        "load_contract",
        lambda repo_: _contract(
            unit=_section(collect="_tracks_collect_unit"),
            integration=_section(collect="_tracks_collect_integration"),
            e2e=_section(collect="_tracks_collect_e2e"),
        ),
    )

    def _dispatch(argv):
        joined = " ".join(argv)
        if "_tracks_collect_unit" in joined:
            return _proc(argv, 0, stdout=_DUP_NODE + "\n")
        if "_tracks_collect_integration" in joined:
            return _proc(argv, 0, stdout=_DUP_NODE + "\n")
        if "_tracks_collect_e2e" in joined:
            return _proc(argv, 5)
        raise AssertionError(f"unexpected subprocess call: {argv}")

    _fake_subprocess(monkeypatch, _dispatch)


def test_duplicate_node_across_layers_fails_the_scan_closed(monkeypatch, tmp_path):
    """Review-7: the same nodeid declared by TWO layers is a contract defect --
    the scan fails closed instead of first-layer-wins setdefault masking."""
    ex, _store, _emitted, _repo = _harness(monkeypatch, tmp_path)
    _install_duplicate_collect_contract(monkeypatch, _repo)

    node_layer, error = ex._collect_all_declared_layers()

    assert error is not None, (
        f"a node collected by unit AND integration was silently masked to a "
        f"single layer assignment: {node_layer}"
    )
    assert node_layer is None, "duplicate-node detection must not yield a node map"


def test_duplicate_node_across_layers_fails_collect_event_closed(monkeypatch, tmp_path):
    """Review-7 handler routing: the cross-layer duplicate surfaces as
    test.collected(failed); no passed classification is ever published."""
    ex, store, emitted, repo = _harness(monkeypatch, tmp_path)
    _install_duplicate_collect_contract(monkeypatch, repo)
    _seed_baseline(store, _DUP_NODE)
    ex._do_collect_tests(_cmd("cc-dup"), _State(), None, False)

    passed = [
        e for e in _of_type(emitted, "test.collected")
        if e["payload"].get("status") == "passed"
    ]
    assert not passed, (
        "a cross-layer duplicate node must not publish a passed classification"
    )
    failed = [
        e for e in _of_type(emitted, "test.collected")
        if e["payload"].get("status") == "failed"
    ]
    assert failed, "cross-layer duplicate must fail the COLLECT step closed"


# -- Review-8: audit-blob write failures never emit passed with null refs -----


def test_collect_per_node_blob_write_failure_fails_closed(monkeypatch, tmp_path):
    """Review-8: if the COLLECT per-node table cannot persist, the step fails
    closed -- a passed test.collected with per_node_blob=null has no replayable
    classification and RED_CHECK would have no authoritative input."""
    ex, store, emitted, repo = _harness(monkeypatch, tmp_path)
    node = "tests/integration/test_blob.py::test_blob"
    physical = repo / "tests" / "integration" / "test_blob.py"
    physical.write_text("def test_blob():\n    assert True\n", encoding="utf-8")
    _seed_baseline(store, node)
    monkeypatch.setattr(store, "write_audit_blob", lambda payload: None)
    _install_three_layer_contract(
        monkeypatch, unit_rc=5, integration_stdout=node + "\n", e2e_rc=5
    )

    ex._do_collect_tests(_cmd("cc-blob"), _State(), None, False)

    passed = [
        e for e in _of_type(emitted, "test.collected")
        if e["payload"].get("status") == "passed"
    ]
    assert not passed, (
        "COLLECT published a passed classification while its per_node_blob "
        "reference was null: "
        + str([e["payload"] for e in _of_type(emitted, "test.collected")])
    )


def test_outcomes_blob_write_failure_never_validates_red(monkeypatch, tmp_path):
    """Review-8: RED_CHECK's normalized per-node outcomes table is the evidence
    red.validated binds to. When its blob write fails, the gate fails closed
    via contract_error instead of emitting valid with outcomes_ref=null."""
    ex, store, emitted, _repo = _harness(monkeypatch, tmp_path)
    node = "tests/integration/test_outcome.py::test_outcome"
    _seed_baseline(store, node)
    _seed_collected(store, [{"node": node, "layer": "integration", "class": "r2"}])
    monkeypatch.setattr(store, "write_audit_blob", lambda payload: None)
    _stage_static_junit(monkeypatch, tmp_path, [node])
    monkeypatch.setattr(
        executor_module,
        "load_contract",
        lambda repo_: _contract(
            integration=_section(run_selected=_recorder_template(_repo))
        ),
    )

    ex._do_run_tests(_cmd("c-outcome"), _State(), None, False)

    valid_reds = [
        e for e in _of_type(emitted, "red.validated")
        if e["payload"].get("status") == "valid"
    ]
    assert not valid_reds, (
        "red.validated(valid) was emitted while the outcomes evidence blob "
        "failed to persist (null outcomes_ref)"
    )
    refusals = [
        e
        for e in _of_type(emitted, "verdict.failed")
        if e["payload"].get("check") == "contract_error"
    ]
    assert refusals, "unpersistable outcomes evidence must fail closed"


def test_selection_nodes_blob_write_failure_never_selects_or_executes(monkeypatch, tmp_path):
    """Review-8: ``test.selected.nodes_blob`` is required evidence -- the
    selection's per-node table must persist BEFORE the selection is published.
    When its blob write fails, RED_CHECK fails closed via contract_error and
    never emits test.selected, executes a command, or validates a red."""
    ex, store, emitted, repo = _harness(monkeypatch, tmp_path)
    node = "tests/integration/test_selblob.py::test_selblob"
    _seed_baseline(store, node)
    _seed_collected(store, [{"node": node, "layer": "integration", "class": "r2"}])
    monkeypatch.setattr(store, "write_audit_blob", lambda payload: None)

    def _no_dispatch(argv):
        raise AssertionError(
            f"a run_selected command was executed while the selection nodes "
            f"blob write failed: {argv}"
        )

    _fake_subprocess(monkeypatch, _no_dispatch)
    _stage_static_junit(monkeypatch, tmp_path, [node])
    monkeypatch.setattr(
        executor_module,
        "load_contract",
        lambda repo_: _contract(
            integration=_section(run_selected=_recorder_template(repo))
        ),
    )

    ex._do_run_tests(_cmd("c-selblob"), _State(), None, False)

    assert not _of_type(emitted, "test.selected"), (
        "test.selected was emitted while the selection nodes blob failed to "
        f"persist: {[_of_type(emitted, 'test.selected')]}"
    )
    valid_reds = [
        e for e in _of_type(emitted, "red.validated")
        if e["payload"].get("status") == "valid"
    ]
    assert not valid_reds, (
        "red.validated(valid) was emitted while the selection nodes blob "
        "failed to persist (null nodes_blob)"
    )
    refusals = [
        e
        for e in _of_type(emitted, "verdict.failed")
        if e["payload"].get("check") == "contract_error"
    ]
    assert refusals, "unpersistable selection nodes table must fail closed"


def test_baseline_digest_blob_write_failure_still_routes_baseline_defect(monkeypatch, tmp_path):
    """Review-8 boundary control (already-correct behavior, kept frozen): a
    failed baseline digest-table write routes baseline_defect and never
    persists a passed capture with node_digest_blob=null."""
    ex, store, emitted, repo = _harness(monkeypatch, tmp_path)
    node = "tests/integration/test_cap.py::test_cap"
    physical = repo / "tests" / "integration" / "test_cap.py"
    physical.write_text("def test_cap():\n    assert True\n", encoding="utf-8")
    monkeypatch.setattr(store, "write_audit_blob", lambda payload: None)
    _install_three_layer_contract(
        monkeypatch, unit_rc=5, integration_stdout=node + "\n", e2e_rc=5
    )

    ex._do_capture_baseline(_cmd("cb-blob"), _State(), None, False)

    passed = [
        e for e in _of_type(emitted, "test.baseline_captured")
        if e["payload"].get("status") == "passed"
    ]
    assert not passed, "a capture with an unwritable digest table must not pass"
    defects = [
        e
        for e in _of_type(emitted, "verdict.failed")
        if e["payload"].get("check") == "baseline_defect"
    ]
    assert defects, "digest-table write failure routes via baseline_defect"


# -- Operator 2026-08-24: collect-pipeline blindness screens before author blame


def _seed_empty_baseline(store):
    ref = store.write_audit_blob([])
    store.append(
        _RUN_ID,
        "v0.4",
        "test.baseline_captured",
        {
            "status": "passed",
            "baseline_id": "b0",
            "baseline_tree": "t0",
            "layers": ["unit", "integration", "e2e"],
            "nodes_count": 0,
            "empty_baseline": True,
            "node_digest_blob": f".tracks/runtime/blobs/{ref}",
            "errors": [],
        },
    )


def _seed_zero_collected(store):
    ref = store.write_audit_blob([])
    store.append(
        _RUN_ID,
        "v0.4",
        "test.collected",
        {
            "status": "passed",
            "collected_count": 0,
            "inherited_r1": 0,
            "delta_r2": 0,
            "removed": 0,
            "failures": [],
            "errors": [],
            "per_node_blob": f".tracks/runtime/blobs/{ref}",
        },
    )


def test_empty_r2_total_collect_blindness_routes_collect_defect(monkeypatch, tmp_path):
    """Signature A: baseline and full collect both parsed 0 nodes while the
    declared layer paths contain test modules (pytest 9.1 quiet collect
    emits per-file counts without :: node ids). The gate must escalate as a
    collector defect, NOT charge the author with an empty_r2 round."""
    ex, store, emitted, ex_repo = _harness(monkeypatch, tmp_path)
    monkeypatch.setattr(
        executor_module,
        "load_contract",
        lambda repo_: _contract(integration=_section()),
    )
    (ex_repo / "tests" / "integration" / "test_hist.py").write_text(
        "def test_hist():\n    assert True\n", encoding="utf-8"
    )
    _seed_empty_baseline(store)
    _seed_zero_collected(store)
    ex._do_run_tests(_cmd("c-blind"), _State(), None, False)

    defects = [
        e
        for e in _of_type(emitted, "verdict.failed")
        if e["payload"].get("check") == "collect_defect"
    ]
    assert defects, (
        "zero-node baseline + zero-node collect with on-disk test modules is "
        "collector blindness; expected check=collect_defect"
    )
    assert "attempt" not in defects[0]["payload"], (
        "collect_defect must not carry an attempt charge"
    )
    empty_r2 = [
        e
        for e in _of_type(emitted, "verdict.failed")
        if e["payload"].get("check") == "empty_r2"
    ]
    assert not empty_r2, "author must not be blamed for a collector defect"


def test_empty_r2_new_file_blindness_routes_collect_defect(monkeypatch, tmp_path):
    """Signature B: test modules added by the checkpointed Shield commit have
    zero collected nodes (file prefix never collected) -> collect_defect."""
    ex, store, emitted, ex_repo = _harness(monkeypatch, tmp_path)
    monkeypatch.setattr(
        executor_module,
        "load_contract",
        lambda repo_: _contract(integration=_section()),
    )
    node = "tests/integration/test_hist.py::test_hist"
    _seed_baseline(store, node)
    _seed_collected(store, [{"node": node, "layer": "integration", "class": "r1"}])
    store.append(
        _RUN_ID,
        "v0.4",
        "result.checkpointed",
        {
            "result_id": "R9",
            "base_sha": "aaa",
            "commit_sha": "ccc",
            "created_commit": True,
        },
    )
    store.append(
        _RUN_ID,
        "v0.4",
        "test.written",
        {"commit_sha": "ccc", "result_id": "R9"},
    )

    def _fake_git(repo, *args, check=True):
        if args[:2] == ("diff", "--name-only"):
            return _proc(
                args, 0, stdout="tests/integration/test_new.py\n"
            )
        return _proc(args, 0)

    monkeypatch.setattr(executor_module, "git", _fake_git)
    ex._do_run_tests(_cmd("c-blind2"), _State(), None, False)

    defects = [
        e
        for e in _of_type(emitted, "verdict.failed")
        if e["payload"].get("check") == "collect_defect"
    ]
    assert defects, (
        "a checkpointed test module absent from the collected inventory is "
        "collector blindness; expected check=collect_defect"
    )
    assert "test_new.py" in defects[0]["payload"]["reason"]


def test_empty_r2_healthy_collect_still_blames_author(monkeypatch, tmp_path):
    """Control: a healthy collect (nonzero baseline, the changed module IS in
    the inventory) with an empty R2 remains the author's empty_r2 refusal."""
    ex, store, emitted, _repo = _harness(monkeypatch, tmp_path)
    _seed_empty_r2_context(monkeypatch, store)
    ex._do_run_tests(_cmd("c-healthy"), _State(), None, False)

    refusals = [
        e
        for e in _of_type(emitted, "verdict.failed")
        if e["payload"].get("check") == "empty_r2"
    ]
    assert refusals, "healthy collect + empty R2 must keep the empty_r2 refusal"
    defects = [
        e
        for e in _of_type(emitted, "verdict.failed")
        if e["payload"].get("check") == "collect_defect"
    ]
    assert not defects
