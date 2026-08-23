"""Final review blocker RED pins (runtime): classifier honesty for failed
ValueError records, absent-layer capture semantics, baseline/tree identity,
the ``trac validate --file project.toml`` surface, missing-executable event
routing, and the nightly workflow deliverable.

Executor-handler journeys use the ``object.__new__(Executor)`` harness (same
seam as test_mtest_slice_a_final_review_pins.py): events captured via a
patched ``_emit``, contracts stubbed per test, git/subprocess pass through to
real runners unless a deterministic collect dispatch is installed. Pinned
final review findings:

- FRB-A: a JUnit ``status=failed`` record carrying an ordinary
  ``ValueError: body blew up`` (no AssertionError/E-line, no stub token, no
  symbol keyword) is NOT a legit behavioral Red; end-to-end RED_CHECK must
  publish red.validated(invalid), never valid.
- FRB-D: a VALID contract may declare unit/integration/e2e paths absent from
  disk; the pre-WRITE capture must pass EMPTY over them. Separately, a
  malformed collect command over an EXISTING declared path returning rc4
  stays baseline_defect -- rc4 is never blessed wholesale.
- FRB-E: dirty pre-WRITE source/test content stamps a baseline_tree distinct
  from HEAD/clean; after git mv + mutating the renamed file (porcelain stays
  rename/modification) the selection tree stamp must move with the renamed
  file's new content.
- FRB-F: ``trac validate --file <canonical project.toml>`` returns 0 on a
  valid contract and nonzero with the ContractError reason on malformed
  contracts (missing [nightly], missing run_selected). No new CLI syntax.
- FRB-G: a nonexistent executable in a contract collect/run_selected command
  routes through the event channel (test.collected failed /
  test.baseline_captured failed + baseline_defect / contract_error verdict),
  never as a raw FileNotFoundError out of the handler.
- FRB-H: ``.github/workflows/nightly.yml`` exists and declares a scheduled
  job running the full pytest regression (root-contract concept), without
  parsing the concurrently-owned root project.toml.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from tracks.cli.main import cmd_validate
from tracks.executor import executor as executor_module
from tracks.executor.executor import Executor
from tracks.executor.helpers import _LEGIT_RED, classify_red_detail
from tracks.store import Store

_RUN_ID = "RUN"


# -- harness ------------------------------------------------------------------


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


def _harness(monkeypatch, tmp_path, *, make_integration_dir=True):
    repo = tmp_path / "host"
    if make_integration_dir:
        (repo / "tests" / "integration").mkdir(parents=True, exist_ok=True)
    store = Store(repo / ".tracks")
    emitted = []
    monkeypatch.setattr(
        Executor,
        "_emit",
        lambda self, ev_, payload, **kw: emitted.append({"type": ev_, "payload": payload}),
    )
    ex = object.__new__(Executor)
    ex.store = store
    ex.repo = repo
    ex.run_id = _RUN_ID
    return ex, store, emitted, repo


def _section(collect="collect-placeholder", paths=("tests/integration/",), run_selected=None):
    return SimpleNamespace(
        framework="pytest",
        paths=list(paths),
        collect=collect,
        run="run-placeholder",
        run_selected=run_selected or ".venv/bin/python -m pytest {nodes} --junitxml={result}",
        cwd=".",
    )


def _contract(integration=None, unit=None, e2e=None):
    return SimpleNamespace(unit=unit, integration=integration, e2e=e2e, layout=None, lint=None)


def _fake_subprocess(monkeypatch, dispatch):
    """Stub non-git subprocess runs through ``dispatch``; real git passes."""
    real_run = subprocess.run

    def _run(argv, cwd=None, capture_output=True, text=True, **kwargs):
        argv = list(argv)
        if argv and argv[0] == "git":
            return real_run(argv, cwd=cwd, capture_output=capture_output, text=text, **kwargs)
        return dispatch(argv)

    monkeypatch.setattr(executor_module.subprocess, "run", _run)


def _proc(argv, rc, stdout="", stderr=""):
    return subprocess.CompletedProcess(list(argv), rc, stdout=stdout, stderr=stderr)


def _of_type(emitted, ev_type):
    return [e for e in emitted if e["type"] == ev_type]


def _passed(emitted):
    return [e for e in _of_type(emitted, "test.baseline_captured") if e["payload"].get("status") == "passed"]


def _baseline_defects(emitted):
    return [e for e in _of_type(emitted, "verdict.failed") if e["payload"].get("check") == "baseline_defect"]


def _seed_r2_selection(store, node):
    entries = [{"node": node, "layer": "integration", "digest": "d0", "node_digest": "d0"}]
    ref = store.write_audit_blob(entries)
    store.append(
        _RUN_ID,
        "v0.4",
        "test.baseline_captured",
        {
            "status": "passed",
            "baseline_id": "b0",
            "baseline_tree": "t0",
            "layers": ["unit", "integration", "e2e"],
            "nodes_count": len(entries),
            "empty_baseline": False,
            "node_digest_blob": f".tracks/runtime/blobs/{ref}",
            "errors": [],
        },
    )
    ref2 = store.write_audit_blob([{"node": node, "layer": "integration", "class": "r2"}])
    store.append(
        _RUN_ID,
        "v0.4",
        "test.collected",
        {
            "status": "passed",
            "collected_count": 1,
            "inherited_r1": 0,
            "delta_r2": 1,
            "removed": 0,
            "failures": [],
            "per_node_blob": f".tracks/runtime/blobs/{ref2}",
            "errors": [],
        },
    )


# -- FRB-A: failed-status ValueError is not a legit Red ------------------------


def test_failed_status_valueerror_is_not_a_legit_red_classification():
    """FRB-A: classify_red_detail on a FAILED JUnit record whose detail is an
    ordinary ValueError (no assertion/stub/symbol signal) must not default to
    the behavioral-Red class."""
    klass = classify_red_detail("ValueError: body blew up", status="failed")
    assert klass != "assertion_failure", (
        f"a failed ValueError record classified as legit assertion_failure ({klass!r})"
    )
    assert klass not in _LEGIT_RED, (
        f"a failed ValueError record ({klass!r}) must stay outside the legit "
        f"Red set {sorted(_LEGIT_RED)}"
    )


_NODE_VE = "tests/integration/test_ve.py::test_ve"


def _valueerror_failure_dispatch(monkeypatch, tmp_path, executed):
    """run_selected template over a stubbed writer script (result path last):
    argv shape is [python, writer, *nodes, result]."""
    writer = tmp_path / "writers" / "valueerror_failure.py"
    writer.parent.mkdir(parents=True, exist_ok=True)
    writer.write_text("# stubbed junit failure writer\n", encoding="utf-8")
    template = f"{sys.executable} {writer} {{nodes}} {{result}}"

    def _dispatch(argv):
        executed.append(list(argv))
        nodeids = argv[2:-1]
        cases = ""
        for n in nodeids:
            cls = n.partition("::")[0][:-3].replace("/", ".")
            name = n.partition("::")[2]
            cases += (
                f'<testcase classname="{cls}" name="{name}">'
                '<failure message="ValueError: body blew up">'
                "ValueError: body blew up</failure></testcase>"
            )
        xml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<testsuites><testsuite name="s">' + cases + "</testsuite></testsuites>"
        )
        with open(argv[-1], "w", encoding="utf-8") as handle:
            handle.write(xml)
        return _proc(argv, 1)

    _fake_subprocess(monkeypatch, _dispatch)
    return template


def test_red_check_failed_valueerror_record_is_invalid_not_valid(monkeypatch, tmp_path):
    """FRB-A end-to-end: RED_CHECK over a selected node whose JUnit record is
    status=failed with 'ValueError: body blew up' must publish
    red.validated(INVALID); validating it as legit would freeze a broken run."""
    ex, store, emitted, _repo = _harness(monkeypatch, tmp_path)
    _seed_r2_selection(store, _NODE_VE)
    executed: list = []
    template = _valueerror_failure_dispatch(monkeypatch, tmp_path, executed)
    monkeypatch.setattr(
        executor_module, "load_contract", lambda r: _contract(integration=_section(run_selected=template))
    )

    ex._do_run_tests(_cmd("c-ve"), _State(), None, False)

    assert executed, "precondition: the selected node must have been executed"
    valid = [e for e in _of_type(emitted, "red.validated") if e["payload"].get("status") == "valid"]
    assert not valid, (
        "a failed ValueError record was validated as a LEGIT red "
        f"(assertion_failure default): {[e['payload'] for e in _of_type(emitted, 'red.validated')]}"
    )
    invalid = [e for e in _of_type(emitted, "red.validated") if e["payload"].get("status") == "invalid"]
    assert invalid, (
        f"the failed ValueError record must invalidate RED_CHECK; emitted: "
        f"{[e['type'] for e in emitted]}"
    )
    for finding in invalid[0]["payload"].get("findings", []):
        assert finding.get("classification") not in _LEGIT_RED, (
            f"invalid-red finding still carries a legit class: {finding}"
        )


# -- FRB-D: absent declared layer paths capture empty; existing rc4 stays defect


_ABSENT_LAYERS = {"unit": "tests/unit/", "integration": "tests/integration/", "e2e": "tests/e2e/"}


def _install_absent_layer_contract(monkeypatch):
    monkeypatch.setattr(
        executor_module,
        "load_contract",
        lambda r: _contract(**{
            layer: _section(collect=f"_tracks_collect_{layer}", paths=(path,))
            for layer, path in _ABSENT_LAYERS.items()
        }),
    )

    def _dispatch(argv):
        joined = " ".join(argv)
        for layer, path in _ABSENT_LAYERS.items():
            if f"_tracks_collect_{layer}" in joined:
                # Faithful pytest emulation for a missing directory: rc4.
                return _proc(argv, 4, stderr=f"ERROR: file or directory not found: {path}")
        raise AssertionError(f"unexpected subprocess call: {argv}")

    _fake_subprocess(monkeypatch, _dispatch)


def test_capture_over_absent_declared_layer_paths_passes_empty(monkeypatch, tmp_path):
    """FRB-D: a valid contract whose declared unit/integration/e2e paths are
    all absent from disk must yield a PASSED EMPTY pre-WRITE capture -- an
    absent declared layer contributes zero nodes, it is not a collection
    defect that rolls back M-DESIGN."""
    ex, _store, emitted, _repo = _harness(monkeypatch, tmp_path, make_integration_dir=False)
    _install_absent_layer_contract(monkeypatch)

    ex._do_capture_baseline(_cmd("cap-absent"), _State(), None, False)

    passed = _passed(emitted)
    assert passed, (
        "capture over absent declared layer paths must PASS empty; emitted: "
        f"{[(e['type'], e['payload'].get('status')) for e in emitted]}"
    )
    payload = passed[0]["payload"]
    assert payload.get("empty_baseline") is True and payload.get("nodes_count") == 0, (
        f"an absent-layer capture must be empty, got: {payload}"
    )
    assert not _baseline_defects(emitted), (
        f"absent declared layers are not a baseline_defect: {_baseline_defects(emitted)}"
    )


def test_existing_declared_path_collect_rc4_malformed_stays_baseline_defect(
    monkeypatch, tmp_path
):
    """FRB-D boundary control: rc4 from a collect command over an EXISTING
    declared path (malformed usage) stays baseline_defect -- only truly
    absent declared layer paths may pass empty; never bless all rc4."""
    ex, _store, emitted, repo = _harness(monkeypatch, tmp_path)
    (repo / "tests" / "integration" / "test_real.py").write_text(
        "def test_real():\n    assert True\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        executor_module,
        "load_contract",
        lambda r: _contract(
            unit=_section(collect="_tracks_collect_unit", paths=("tests/unit/",)),
            integration=_section(
                collect="_tracks_collect_integration", paths=("tests/integration/",)
            ),
            e2e=_section(collect="_tracks_collect_e2e", paths=("tests/e2e/",)),
        ),
    )

    def _dispatch(argv):
        joined = " ".join(argv)
        if "_tracks_collect_integration" in joined:
            # Existing path + broken usage: pytest rc4 usage error shape.
            return _proc(argv, 4, stderr="usage: pytest [options]... error: unrecognized arguments")
        for layer, path in (("unit", "tests/unit/"), ("e2e", "tests/e2e/")):
            if f"_tracks_collect_{layer}" in joined:
                return _proc(argv, 4, stderr=f"ERROR: file or directory not found: {path}")
        raise AssertionError(f"unexpected subprocess call: {argv}")

    _fake_subprocess(monkeypatch, _dispatch)

    ex._do_capture_baseline(_cmd("cap-rc4"), _State(), None, False)

    assert not _passed(emitted), (
        f"a malformed collect over an EXISTING declared path (rc4 usage error) "
        f"must not capture: {[_e['payload'] for _e in _passed(emitted)]}"
    )
    assert _baseline_defects(emitted), (
        "malformed rc4 over an existing declared path must stay baseline_defect; "
        f"emitted: {[(e['type'], e['payload'].get('check')) for e in emitted]}"
    )


def test_capture_section_cwd_absent_is_baseline_defect_not_empty_layer(
    monkeypatch, tmp_path
):
    """FRB-D boundary: a section cwd that ITSELF does not exist makes every
    declared path absent as a side effect. That is malformed infrastructure
    (the declared layer's working tree is gone), NOT a legal fresh empty
    layer -- capture must emit baseline_defect instead of passing empty."""
    ex, _store, emitted, _repo = _harness(monkeypatch, tmp_path, make_integration_dir=False)
    vanished = _section(collect="_tracks_collect_integration", paths=("tests/integration/",))
    vanished.cwd = "packages/vanished"
    assert not ((tmp_path / "host") / "packages" / "vanished").exists(), (
        "precondition: the section cwd must not exist on disk"
    )
    monkeypatch.setattr(
        executor_module,
        "load_contract",
        lambda r: _contract(
            unit=_section(collect="_tracks_collect_unit", paths=("tests/unit/",)),
            integration=vanished,
            e2e=_section(collect="_tracks_collect_e2e", paths=("tests/e2e/",)),
        ),
    )

    ex._do_capture_baseline(_cmd("cap-missing-cwd"), _State(), None, False)

    assert not _passed(emitted), (
        f"a section whose cwd does not exist is malformed infrastructure, not a "
        f"legal empty layer; capture must not pass empty: "
        f"{[_e['payload'] for _e in _passed(emitted)]}"
    )
    assert _baseline_defects(emitted), (
        f"a nonexistent section cwd must route fail-closed via baseline_defect; "
        f"emitted: {[(e['type'], e['payload'].get('check')) for e in emitted]}"
    )


# -- FRB-E: baseline identity moves with dirty content and renamed files ------


def _init_git_repo(repo):
    def _git(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)

    _git("init", "-q", "-b", "main")
    _git("config", "user.email", "t@tracks.dev")
    _git("config", "user.name", "t")


def _commit_file(repo, relpath, body):
    path = repo / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    subprocess.run(["git", "add", relpath], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@tracks.dev", "-c", "user.name=t", "commit", "-q", "-m", "seed"],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _head(repo):
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


_DIRTY_NODE = "tests/integration/test_dirty.py::test_dirty"


def _install_dirty_capture_contract(monkeypatch):
    monkeypatch.setattr(
        executor_module,
        "load_contract",
        lambda r: _contract(
            unit=_section(collect="_tracks_collect_unit"),
            integration=_section(collect="_tracks_collect_integration"),
            e2e=_section(collect="_tracks_collect_e2e"),
        ),
    )

    def _dispatch(argv):
        joined = " ".join(argv)
        if "_tracks_collect_unit" in joined or "_tracks_collect_e2e" in joined:
            return _proc(argv, 5)
        if "_tracks_collect_integration" in joined:
            return _proc(argv, 0, stdout=_DIRTY_NODE + "\n")
        raise AssertionError(f"unexpected subprocess call: {argv}")

    _fake_subprocess(monkeypatch, _dispatch)


def test_dirty_prewrite_content_stamps_baseline_tree_off_head(monkeypatch, tmp_path):
    """FRB-E part 1: dirty source/test CONTENT at pre-WRITE time must stamp a
    baseline_tree distinct from HEAD and from the clean-tree capture;
    otherwise two different uncommitted trees share one snapshot identity."""
    ex, _store, emitted, repo = _harness(monkeypatch, tmp_path)
    _init_git_repo(repo)
    _commit_file(repo, "tests/integration/test_dirty.py", "def test_dirty():\n    assert 'v1'\n")
    _install_dirty_capture_contract(monkeypatch)
    head = _head(repo)

    ex._do_capture_baseline(_cmd("cap-clean"), _State(), None, False)
    clean = _passed(emitted)
    assert clean, "precondition: the clean-tree capture must pass"
    clean_tree = clean[0]["payload"]["baseline_tree"]

    (repo / "tests" / "integration" / "test_dirty.py").write_text(
        "def test_dirty():\n    assert 'v2-dirty'\n", encoding="utf-8"
    )
    ex._do_capture_baseline(_cmd("cap-dirty"), _State(), None, False)
    dirty = _passed(emitted)
    assert len(dirty) > len(clean), "precondition: the dirty capture must also pass"
    dirty_tree = dirty[-1]["payload"]["baseline_tree"]

    assert dirty_tree != head, (
        f"dirty pre-WRITE content must stamp a baseline_tree different from HEAD; "
        f"both are {dirty_tree!r}"
    )
    assert dirty_tree != clean_tree, (
        f"dirty and clean captures share one baseline_tree ({dirty_tree!r}); the "
        "snapshot identity ignores uncommitted source/test content"
    )


def test_renamed_mutated_file_moves_selection_tree_stamp(tmp_path):
    """FRB-E part 2: after git mv + mutating the renamed file (porcelain keeps
    showing the rename/modification pair), the selection tree stamp must
    change -- the renamed path's new content must enter the stamp instead of
    the whole entry reading as one constant pseudo-path digest."""
    repo = tmp_path / "host"
    (repo / "tests" / "integration").mkdir(parents=True)
    _init_git_repo(repo)
    _commit_file(repo, "tests/integration/test_orig.py", "def test_orig():\n    assert 'v1'\n")
    subprocess.run(
        ["git", "mv", "tests/integration/test_orig.py", "tests/integration/test_ren.py"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    porcelain_mid = subprocess.run(
        ["git", "status", "--porcelain", "-uall"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout
    assert "->" in porcelain_mid and "R" in porcelain_mid[:2], (
        f"precondition: staged rename expected in porcelain:\n{porcelain_mid}"
    )
    ex = object.__new__(Executor)
    ex.repo = repo

    stamp_before = ex._dirty_tree_stamp()

    (repo / "tests" / "integration" / "test_ren.py").write_text(
        "def test_orig():\n    assert 'v2-renamed'\n", encoding="utf-8"
    )
    porcelain_after = subprocess.run(
        ["git", "status", "--porcelain", "-uall"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout
    assert "->" in porcelain_after, (
        f"precondition: the mutated renamed file must keep rename/modification "
        f"porcelain:\n{porcelain_after}"
    )

    stamp_after = ex._dirty_tree_stamp()

    assert stamp_after != stamp_before, (
        "mutating a git-mv-renamed file left the selection tree stamp unchanged "
        f"({stamp_before!r}): the renamed path's content never enters the stamp"
    )


# -- FRB-F: trac validate --file <canonical project.toml> surface --------------


def _contract_toml(*, nightly=True, run_selected=True):
    lines = []
    for layer, path in (
        ("unit", "tests/unit/"),
        ("integration", "tests/integration/"),
        ("e2e", "tests/e2e/"),
    ):
        lines += [
            f"[{layer}]",
            "framework='pytest'",
            f"paths=['{path}']",
            f"collect='pytest --collect-only -q {path}'",
            f"run='pytest {path} --junitxml={{result}}'",
        ]
        if run_selected:
            lines.append("run_selected='pytest {nodes} --junitxml={result}'")
        lines += ["cwd='.'", ""]
    if nightly:
        lines += [
            "[nightly]",
            "schedule='0 3 * * *'",
            "workflow='.github/workflows/nightly.yml'",
            "job='nightly-regression'",
            "layers=['unit', 'integration', 'e2e']",
            "purpose='scheduled FULL-suite regression'",
        ]
    return "\n".join(lines) + "\n"


def _write_canonical_contract(repo: Path, text: str) -> Path:
    toml_path = repo / ".tracks" / "projects" / "project.toml"
    toml_path.parent.mkdir(parents=True, exist_ok=True)
    toml_path.write_text(text, encoding="utf-8")
    return toml_path


def test_validate_project_toml_accepts_valid_contract(tmp_path, capsys):
    """FRB-F: `trac validate --file .tracks/projects/project.toml` accepts a
    valid canonical contract (exit 0). No new CLI syntax."""
    repo = tmp_path / "host"
    repo.mkdir()
    toml_path = _write_canonical_contract(repo, _contract_toml())

    rc = cmd_validate(repo, "--file", str(toml_path))
    err = capsys.readouterr().err

    assert rc == 0, f"a valid canonical project.toml must validate (rc={rc}): {err.strip()!r}"


def test_validate_project_toml_reports_missing_nightly_as_contract_error(tmp_path, capsys):
    """FRB-F: a contract without [nightly] exits nonzero naming the ContractError
    reason (not a template-mapping complaint)."""
    repo = tmp_path / "host"
    repo.mkdir()
    toml_path = _write_canonical_contract(repo, _contract_toml(nightly=False))

    rc = cmd_validate(repo, "--file", str(toml_path))
    err = capsys.readouterr().err

    assert rc != 0, "a contract missing [nightly] must fail validation"
    assert "nightly" in err.lower(), (
        f"nonzero exit must carry the ContractError reason about nightly, got stderr: {err.strip()!r}"
    )


def test_validate_project_toml_reports_missing_run_selected_as_contract_error(tmp_path, capsys):
    """FRB-F: a layer section without run_selected exits nonzero naming the
    ContractError reason."""
    repo = tmp_path / "host"
    repo.mkdir()
    toml_path = _write_canonical_contract(repo, _contract_toml(run_selected=False))

    rc = cmd_validate(repo, "--file", str(toml_path))
    err = capsys.readouterr().err

    assert rc != 0, "a contract without run_selected must fail validation"
    assert "run_selected" in err, (
        f"nonzero exit must name run_selected as the reason, got stderr: {err.strip()!r}"
    )


# -- FRB-G: nonexistent executables route event failures, never raw raises -----


_MISSING_EXE = "tracks-no-such-binary-xyz"


def _missing_executable_sections():
    return {
        "unit": _section(collect=f"{_MISSING_EXE} --collect-only tests/unit/"),
        "integration": _section(collect=f"{_MISSING_EXE} --collect-only tests/integration/"),
        "e2e": _section(collect=f"{_MISSING_EXE} --collect-only tests/e2e/"),
    }


def test_capture_missing_collect_executable_routes_event_failure(monkeypatch, tmp_path):
    """FRB-G: a collect command whose executable does not exist must route
    test.baseline_captured(failed) + baseline_defect -- never raise
    FileNotFoundError out of the handler."""
    ex, _store, emitted, _repo = _harness(monkeypatch, tmp_path)
    sections = _missing_executable_sections()
    monkeypatch.setattr(executor_module, "load_contract", lambda r: _contract(**sections))

    crashed = None
    try:
        ex._do_capture_baseline(_cmd("cap-missing-exe"), _State(), None, False)
    except Exception as exc:  # the pinned defect is precisely such a crash
        crashed = exc

    assert crashed is None, (
        f"capture crashed on a missing collect executable instead of routing the "
        f"event failure: {crashed!r}"
    )
    failed = [e for e in _of_type(emitted, "test.baseline_captured") if e["payload"].get("status") == "failed"]
    assert failed, (
        f"a missing collect executable must emit test.baseline_captured(failed); "
        f"emitted: {[(e['type']) for e in emitted]}"
    )
    assert _baseline_defects(emitted), "the failed capture must route baseline_defect"


def test_collect_missing_executable_routes_test_collected_failure(monkeypatch, tmp_path):
    """FRB-G: same routing discipline for COLLECT: a missing collect executable
    emits test.collected(failed) instead of raising."""
    ex, _store, emitted, _repo = _harness(monkeypatch, tmp_path)
    sections = _missing_executable_sections()
    monkeypatch.setattr(executor_module, "load_contract", lambda r: _contract(**sections))

    crashed = None
    try:
        ex._do_collect_tests(_cmd("col-missing-exe"), _State(), None, False)
    except Exception as exc:
        crashed = exc

    assert crashed is None, (
        f"collect crashed on a missing executable instead of emitting the "
        f"failure event: {crashed!r}"
    )
    failed = [e for e in _of_type(emitted, "test.collected") if e["payload"].get("status") == "failed"]
    assert failed, (
        f"a missing collect executable must emit test.collected(failed); "
        f"emitted: {[e['type'] for e in emitted]}"
    )


def test_red_check_missing_run_selected_executable_emits_contract_error(monkeypatch, tmp_path):
    """FRB-G: RED_CHECK with a nonexistent run_selected executable emits the
    contract_error channel (red.validated invalid + verdict.failed
    contract_error) rather than letting the raw exception escape."""
    ex, store, emitted, _repo = _harness(monkeypatch, tmp_path)
    _seed_r2_selection(store, _NODE_VE)
    monkeypatch.setattr(
        executor_module,
        "load_contract",
        lambda r: _contract(
            integration=_section(run_selected=f"{_MISSING_EXE} {{nodes}} --junitxml={{result}}")
        ),
    )

    crashed = None
    try:
        ex._do_run_tests(_cmd("run-missing-exe"), _State(), None, False)
    except Exception as exc:
        crashed = exc

    assert crashed is None, (
        f"RED_CHECK raised on a missing run_selected executable instead of "
        f"emitting contract_error: {crashed!r}"
    )
    refusals = [e for e in _of_type(emitted, "verdict.failed") if e["payload"].get("check") == "contract_error"]
    assert refusals, (
        f"a missing run_selected executable must emit verdict.failed(contract_error); "
        f"emitted: {[e['type'] for e in emitted]}"
    )
    assert not [e for e in _of_type(emitted, "red.validated") if e["payload"].get("status") == "valid"], (
        "a RED_CHECK that could not execute must never validate red"
    )


# -- FRB-H: nightly workflow deliverable ---------------------------------------

_ROOT = Path(__file__).resolve().parents[2]
NIGHTLY = _ROOT / ".github" / "workflows" / "nightly.yml"


def _job_blocks(text: str) -> dict[str, str]:
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    in_jobs = False
    for line in text.splitlines():
        if line == "jobs:":
            in_jobs = True
            current = None
            continue
        if not in_jobs:
            continue
        job = re.match(r"^  ([A-Za-z0-9][A-Za-z0-9_-]*):\s*$", line)
        if job:
            current = job.group(1)
            blocks[current] = []
        elif line and not line[0].isspace():
            current = None
        elif current is not None:
            blocks[current].append(line)
    return {name: "\n".join(lines) for name, lines in blocks.items()}


def _top_level_block(text: str, key: str) -> str:
    grab = False
    out: list[str] = []
    for line in text.splitlines():
        if line.rstrip() == f"{key}:":
            grab = True
            continue
        if grab:
            if line and not line[0].isspace():
                break
            out.append(line)
    return "\n".join(out)


def test_nightly_workflow_deliverable_declares_scheduled_regression_job():
    """FRB-H: the workflow contract's deliverable exists at
    .github/workflows/nightly.yml and declares a scheduled job running the
    full pytest regression (the root contract's [nightly] concept), without
    requiring the concurrently-owned root project.toml content."""
    assert NIGHTLY.exists(), (
        f"missing workflow-contract deliverable: {NIGHTLY}. The root contract's "
        "[nightly] scheduled FULL-suite regression concept requires this "
        "workflow file to exist."
    )
    text = NIGHTLY.read_text(encoding="utf-8")
    on_block = _top_level_block(text, "on")
    assert "schedule:" in on_block, (
        f"nightly.yml must trigger on a schedule; on-block:\n{on_block}"
    )
    assert "cron" in on_block, "the schedule must declare a cron expression"
    jobs = _job_blocks(text)
    assert jobs, "nightly.yml must declare at least one job under jobs:"
    regression = "\n".join(jobs.values())
    assert "pytest" in regression, (
        f"the scheduled nightly job must run the full pytest regression suite; "
        f"jobs: {sorted(jobs)}"
    )
