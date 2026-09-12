"""Behavior coverage for ``trac validate`` / ``trac check``.

Targets the canonical host-contract closed set, non-canonical template /
tasks.json validation, trace (classic + v0.7 candidate-bound closure), reach,
release-evidence and the check dispatcher (FR-150, IF-CLOSURE-001,
IF-RELEASE-001).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

from tracks.cli import validate_cmd
from tracks.cli.validate_cmd import (
    _ac_base_ref,
    _active_hotfix_trace_context,
    _apply_full_execution,
    _closure_evidence,
    _cmd_check_reach,
    _cmd_check_release_evidence,
    _cmd_check_trace,
    _cmd_check_trace_v07,
    _emit_trace_v07_json,
    _emit_trace_v07_text,
    _latest_version,
    _load_baseline,
    _merge_baseline_repair,
    _merge_mutation_manifest,
    _merge_stream_event,
    _parse_check_reach_args,
    _parse_check_trace_args,
    _reject_unknown_host_contract_table,
    _trace_v07_report,
    _validate_non_canonical_file,
    _validate_tasksjson,
    _version_events,
    cmd_check,
    cmd_validate,
)
from tracks.project import ContractError

FIXTURES = Path(__file__).parents[1] / "assets" / "taskgraph_fixtures"


def _copy_tasks(tmp_path: Path, fixture: str) -> Path:
    shutil.copy(FIXTURES / fixture, tmp_path / "tasks.json")
    shutil.copy(FIXTURES / "acceptance.md", tmp_path / "acceptance.md")
    shutil.copy(FIXTURES / "interfaces.md", tmp_path / "interfaces.md")
    return tmp_path / "tasks.json"


def _ev(type, payload=None):
    return SimpleNamespace(type=type, payload=payload or {})


# ---------------------------------------------------------------------------
# host-contract closed set
# ---------------------------------------------------------------------------


def test_reject_unknown_host_contract_table(tmp_path, capsys):
    good = tmp_path / "good.toml"
    good.write_text("[host-contract.ci]\nx = 1\n", encoding="utf-8")
    assert _reject_unknown_host_contract_table(good) is False

    bad = tmp_path / "bad.toml"
    bad.write_text("[[host-contract.bogus]]\ny = 2\n", encoding="utf-8")
    assert _reject_unknown_host_contract_table(bad) is True
    assert "unknown host-contract table [host-contract.bogus]" in capsys.readouterr().err

    # Unreadable path fails open (the loader owns the real fail-closed surface).
    assert _reject_unknown_host_contract_table(tmp_path) is False


def test_cmd_validate_canonical_rejects_unknown_table(tmp_path, capsys):
    canonical = tmp_path / ".tracks" / "projects" / "project.toml"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("[host-contract.nope]\n", encoding="utf-8")
    assert cmd_validate(tmp_path, "--file", str(canonical)) == 1
    assert "unknown host-contract table" in capsys.readouterr().err


def test_cmd_validate_canonical_contract_error(tmp_path, capsys, monkeypatch):
    canonical = tmp_path / ".tracks" / "projects" / "project.toml"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("[host-contract]\n", encoding="utf-8")

    def _bad_contract(repo):
        raise ContractError("broken [host-contract]")

    monkeypatch.setattr(validate_cmd, "load_contract", _bad_contract)
    assert cmd_validate(tmp_path, "--file", str(canonical)) == 1
    assert "contract error: broken" in capsys.readouterr().err


def test_cmd_validate_canonical_ok(tmp_path, capsys, monkeypatch):
    canonical = tmp_path / ".tracks" / "projects" / "project.toml"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("[host-contract]\n", encoding="utf-8")
    monkeypatch.setattr(validate_cmd, "load_contract", lambda repo: SimpleNamespace())
    assert cmd_validate(tmp_path, "--file", str(canonical)) == 0
    assert "valid" in capsys.readouterr().out


def test_cmd_validate_resolve_oserror_falls_through(tmp_path, capsys, monkeypatch):
    target = tmp_path / "weird.toml"
    target.write_text("", encoding="utf-8")
    original = Path.resolve

    def _resolve(self, *args, **kwargs):
        if self.name == "weird.toml":
            raise OSError("cannot resolve")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", _resolve)
    monkeypatch.setattr(validate_cmd, "check_template", lambda path: [])
    assert cmd_validate(tmp_path, "--file", str(target)) == 0
    assert "valid" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _validate_non_canonical_file / tasks.json
# ---------------------------------------------------------------------------


def test_validate_non_canonical_tasksjson_route(tmp_path):
    path = _copy_tasks(tmp_path, "valid.json")
    issues = _validate_non_canonical_file(path)
    assert issues == []


def test_validate_tasksjson_parse_and_structural_errors(tmp_path):
    bad = tmp_path / "tasks.json"
    bad.write_text("{ not json", encoding="utf-8")
    issues = _validate_tasksjson(bad)
    assert issues and "invalid JSON" in issues[0]

    assert any("cycle:" in i for i in _validate_tasksjson(_copy_tasks(tmp_path, "dag_cycle.json")))
    assert any(
        "scope overlap" in i
        for i in _validate_tasksjson(_copy_tasks(tmp_path, "scope_overlap.json"))
    )
    assert any(
        "not covered by any task" in i
        for i in _validate_tasksjson(_copy_tasks(tmp_path, "ac_coverage_gap.json"))
    )
    assert any(
        "not in registry" in i
        for i in _validate_tasksjson(_copy_tasks(tmp_path, "if_invalid.json"))
    )
    assert any(
        "not a positive integer" in i
        for i in _validate_tasksjson(_copy_tasks(tmp_path, "issue_invalid.json"))
    )


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def test_latest_version_and_load_baseline(tmp_path):
    home = tmp_path / ".tracks"
    assert _latest_version(home) is None
    projects = home / "projects"
    projects.mkdir(parents=True)
    assert _latest_version(home) is None
    (projects / "v0.1").mkdir()
    (projects / "v0.9").mkdir()
    (projects / "notes").mkdir()
    assert _latest_version(home) == "v0.9"

    assert _load_baseline(home) is None
    (home / "legacy-baseline.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert _load_baseline(home) == {"a": 1}


def test_parse_check_args():
    assert _parse_check_trace_args([]) == (False, None)
    assert _parse_check_trace_args(["--json", "--version", "v0.1"]) == (True, "v0.1")
    assert _parse_check_trace_args(["--bogus"]) is None
    assert _parse_check_trace_args(["--version"]) is None

    assert _parse_check_reach_args([]) == (False, [])
    assert _parse_check_reach_args(["--json", "--entry", "a", "--entry", "b"]) == (True, ["a", "b"])
    assert _parse_check_reach_args(["--bogus"]) is None
    assert _parse_check_reach_args(["--entry"]) is None


# ---------------------------------------------------------------------------
# check trace
# ---------------------------------------------------------------------------


def test_cmd_check_trace_parse_error_and_no_versions(tmp_path, capsys, monkeypatch):
    assert _cmd_check_trace(tmp_path, ["--bogus"]) == 1
    assert "usage: trac check trace" in capsys.readouterr().err

    monkeypatch.setattr(
        validate_cmd.version_extensions, "resolve_capability", lambda version, cap: None
    )
    assert _cmd_check_trace(tmp_path, []) == 1
    assert "no .tracks/projects/v*" in capsys.readouterr().err


def test_cmd_check_trace_v07_dispatch(tmp_path, capsys, monkeypatch):
    home = tmp_path / ".tracks"
    (home / "projects" / "v0.8").mkdir(parents=True)
    seen = {}

    def _fake_v07(repo, home_arg, version, checker, use_json, release_builder):
        seen.update(version=version, checker=checker, use_json=use_json, release=release_builder)
        return 0

    monkeypatch.setattr(
        validate_cmd.version_extensions, "resolve_capability", lambda version, cap: "CHECKER"
    )
    monkeypatch.setattr(
        validate_cmd.version_extensions,
        "resolve_optional_capability",
        lambda version, cap: "RELEASE_BUILDER",
    )
    monkeypatch.setattr(validate_cmd, "_cmd_check_trace_v07", _fake_v07)
    assert _cmd_check_trace(tmp_path, ["--version", "v0.8", "--json"]) == 0
    assert seen == {
        "version": "v0.8",
        "checker": "CHECKER",
        "use_json": True,
        "release": "RELEASE_BUILDER",
    }


def test_cmd_check_trace_missing_vdir(tmp_path, capsys, monkeypatch):
    (tmp_path / ".tracks" / "projects").mkdir(parents=True)
    monkeypatch.setattr(
        validate_cmd.version_extensions, "resolve_capability", lambda version, cap: None
    )
    assert _cmd_check_trace(tmp_path, ["--version", "v9.9"]) == 1
    assert "version directory not found" in capsys.readouterr().err


def test_cmd_check_trace_classic_json_and_text(tmp_path, capsys, monkeypatch):
    home = tmp_path / ".tracks"
    vdir = home / "projects" / "v0.1"
    vdir.mkdir(parents=True)
    monkeypatch.setattr(
        validate_cmd.version_extensions, "resolve_capability", lambda version, cap: None
    )
    monkeypatch.setattr(validate_cmd, "_active_hotfix_trace_context", lambda home, vdir: None)

    report = SimpleNamespace(
        status="footnote",
        hard_errors=["hard-1"],
        warnings=["warn-1"],
        hotfix_scope={"scope": "x"},
    )
    monkeypatch.setattr(validate_cmd, "check_trace_full_file", lambda *a: report)
    assert _cmd_check_trace(tmp_path, ["--version", "v0.1"]) == 0
    out = capsys.readouterr().out
    assert "hard-1" in out and "warning: warn-1" in out

    report.status = "pass"
    assert _cmd_check_trace(tmp_path, ["--version", "v0.1", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "pass"
    assert payload["hotfix_scope"] == {"scope": "x"}

    report.status = "fail"
    assert _cmd_check_trace(tmp_path, ["--version", "v0.1", "--json"]) == 1

    report.status = "pass"
    report.hard_errors = []
    assert _cmd_check_trace(tmp_path, ["--version", "v0.1"]) == 0
    assert "trace ok" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# closure harvest helpers
# ---------------------------------------------------------------------------


def test_ac_base_ref_variants():
    assert _ac_base_ref("AC-FR0030-01@v0.8") == "AC-FR0030-01"
    assert _ac_base_ref("  AC-1  ") == "AC-1"
    assert _ac_base_ref("") is None
    assert _ac_base_ref(None) is None
    assert _ac_base_ref(5) is None


def test_merge_baseline_repair_and_mutation_manifest():
    entry = {"nodes": ["n0"], "baseline_evidence": "old"}
    _merge_baseline_repair(
        entry,
        {
            "evidence_id": "ev-1",
            "bound_node": {"node_id": "n1", "digest": "digest-1"},
        },
    )
    assert entry["baseline_evidence"] == "ev-1"
    assert entry["nodes"] == ["n0", "n1"]
    assert entry["baseline_candidate"] == "digest-1"

    _merge_baseline_repair(entry, {"bound_node": {"node_id": "n1"}})
    assert entry["nodes"] == ["n0", "n1"]  # deduped
    assert entry["baseline_evidence"] == "ev-1"  # preserved on absent evidence

    _merge_mutation_manifest(entry, {"candidate_digest": "cand", "patch_digest": "patch"})
    assert entry["mutation_candidate"] == "cand"
    assert entry["mutation_evidence"] == "patch"
    _merge_mutation_manifest(entry, {})


def test_apply_full_execution_variants():
    per_ac: dict = {}
    _apply_full_execution(per_ac, ["AC-1"], {})
    assert per_ac == {}
    _apply_full_execution(
        per_ac, ["AC-1", "AC-2"], {"serves_as_full_f": True, "evidence_ids": ["e1", "e2"]}
    )
    assert per_ac["AC-1"]["full_pass_evidence"] == "e1"
    assert per_ac["AC-2"]["full_pass_evidence"] == "e1"
    per_ac = {}
    _apply_full_execution(
        per_ac, ["AC-1"], {"serves_as_full_f": True, "outcomes_ref": "ref"}
    )
    assert per_ac["AC-1"]["full_pass_evidence"] == "ref"


def test_merge_stream_event_all_branches():
    per_ac: dict = {}
    digest = _merge_stream_event(
        None,
        per_ac,
        ["AC-1"],
        _ev("mutation.manifest", {"ac": "AC-1@v0.8", "candidate_digest": "d1"}),
    )
    assert digest == "d1"
    assert "AC-1" in per_ac

    digest = _merge_stream_event(
        digest, per_ac, ["AC-1"], _ev("phase0.baseline_repaired", {"ac": "AC-1", "evidence_id": "e"})
    )
    assert digest == "d1"
    assert per_ac["AC-1"]["baseline_evidence"] == "e"

    _merge_stream_event(
        digest,
        per_ac,
        ["AC-1"],
        _ev("full.executed", {"serves_as_full_f": True, "evidence_ids": ["full-1"]}),
    )
    assert per_ac["AC-1"]["full_pass_evidence"] == "full-1"

    assert _merge_stream_event(digest, per_ac, ["AC-1"], _ev("other")) == "d1"


class _FakeStore:
    def __init__(self, runs=(), events=None):
        self._runs = runs
        self._events = events or {}

    def runs_for_version(self, version):
        return list(self._runs)

    def events(self, run_id):
        return list(self._events.get(run_id, []))

    def close(self):
        pass


def test_closure_evidence_and_version_events():
    store = _FakeStore(
        runs=["R1", "R2"],
        events={
            "R1": [_ev("mutation.manifest", {"ac": "AC-1", "candidate_digest": "d1"})],
            "R2": [_ev("full.executed", {"serves_as_full_f": True, "evidence_ids": ["f"]})],
        },
    )
    digest, per_ac = _closure_evidence(store, "v0.1", ["AC-1"])
    assert digest == "d1"
    assert per_ac["AC-1"]["full_pass_evidence"] == "f"
    assert [e.type for e in _version_events(store, "v0.1")] == [
        "mutation.manifest",
        "full.executed",
    ]


# ---------------------------------------------------------------------------
# v0.7 emit path / report assembly
# ---------------------------------------------------------------------------


def test_cmd_check_trace_v07_json_and_text(capsys, monkeypatch):
    report = SimpleNamespace(status="pass", hard_errors=[], closure={"ok": True}, records=[])
    monkeypatch.setattr(validate_cmd, "_trace_v07_report", lambda *a: (report, None))
    assert _cmd_check_trace_v07(Path("."), Path("."), "v0.8", lambda *a: report, True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["closure"] == {"ok": True}
    assert payload["records"] == []

    inconsistent = {"status": "inconsistent", "reason": "pending"}
    monkeypatch.setattr(validate_cmd, "_trace_v07_report", lambda *a: (report, inconsistent))
    assert _cmd_check_trace_v07(Path("."), Path("."), "v0.8", lambda *a: report, False) == 1
    out = capsys.readouterr().out
    assert "release: inconsistent" in out
    assert "release trace error: pending" in out

    report.status = "fail"
    monkeypatch.setattr(validate_cmd, "_trace_v07_report", lambda *a: (report, inconsistent))
    assert _cmd_check_trace_v07(Path("."), Path("."), "v0.8", lambda *a: report, True) == 1


def test_trace_v07_report_assembles_inputs(tmp_path, monkeypatch):
    home = tmp_path / ".tracks"
    report = SimpleNamespace(status="pass")
    monkeypatch.setattr(validate_cmd, "approved_acs", lambda projects, version: ["AC-1", "AC-2"])
    store = _FakeStore(
        runs=["R1"],
        events={"R1": [_ev("mutation.manifest", {"ac": "AC-1", "candidate_digest": "d1"})]},
    )
    monkeypatch.setattr(validate_cmd, "Store", lambda home_arg: store)
    calls = {}

    def _checker(acs, digest, harvested):
        calls.update(acs=acs, digest=digest, harvested=harvested)
        return report

    result_report, release_segment = _trace_v07_report(home, "v0.8", _checker, None)
    assert result_report is report
    assert release_segment is None
    assert calls["acs"] == ["AC-1", "AC-2"]
    assert calls["digest"] == "d1"
    assert calls["harvested"]["AC-1"]["mutation_candidate"] == "d1"
    assert calls["harvested"]["AC-2"] == {}

    def builder(events):
        return {"status": "closed"}

    result_report, release_segment = _trace_v07_report(home, "v0.8", _checker, builder)
    assert result_report is report
    assert release_segment == {"status": "closed"}


def test_emit_trace_v07_json_includes_release(capsys):
    report = SimpleNamespace(status="pass", hard_errors=[], closure={}, records=[])
    _emit_trace_v07_json(report, {"status": "closed"})
    assert json.loads(capsys.readouterr().out)["release"] == {"status": "closed"}
    _emit_trace_v07_text(report, None, False)
    assert "trace ok" in capsys.readouterr().out
    report.hard_errors = ["hard-x"]
    _emit_trace_v07_text(report, None, False)
    assert "hard-x" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# active hotfix trace context
# ---------------------------------------------------------------------------


def test_active_hotfix_trace_context_increment_and_fallback(tmp_path):
    from tracks.store import Store

    home = tmp_path / ".tracks"
    vdir = home / "projects" / "v0.1"
    vdir.mkdir(parents=True)
    store = Store(home)
    store.append("RUN", "v0.1", "hotfix.requested", {"issue": 5, "scenario": "s"})
    store.append("RUN", "v0.1", "stage.entered", {"stage": "M-TEST"})
    store.append(
        "RUN",
        "v0.1",
        "increment.declared",
        {"trace_status": "pass", "unit_rows": ["u1"]},
    )
    ctx = _active_hotfix_trace_context(home, vdir)
    assert ctx["run_id"] == "RUN"
    assert ctx["declared_unit_rows"] == ["u1"]
    assert ctx["projects_dir"].endswith("projects")
    store.close()


def test_active_hotfix_trace_context_parses_test_plan_fallback(tmp_path):
    from tracks.store import Store

    home = tmp_path / ".tracks"
    vdir = home / "projects" / "v0.2"
    vdir.mkdir(parents=True)
    (vdir / "test-plan.md").write_text("## unit\n- u", encoding="utf-8")
    store = Store(home)
    store.append("RUN2", "v0.2", "hotfix.requested", {"issue": 6, "scenario": "s"})
    store.append("RUN2", "v0.2", "stage.entered", {"stage": "M-TEST"})
    store.append("RUN2", "v0.2", "increment.declared", {"trace_status": "pass"})
    ctx = _active_hotfix_trace_context(home, vdir)
    assert isinstance(ctx["declared_unit_rows"], list)
    store.close()


def test_active_hotfix_trace_context_skips_mismatched_run_version(tmp_path):
    from tracks.store import Store

    home = tmp_path / ".tracks"
    vdir = home / "projects" / "v0.4"
    vdir.mkdir(parents=True)
    store = Store(home)
    store.append("RUN4", "v0.9", "hotfix.requested", {"issue": 8, "scenario": "s"})
    store.append("RUN4", "v0.9", "stage.entered", {"stage": "M-TEST"})
    assert _active_hotfix_trace_context(home, vdir) is None
    store.close()


def test_active_hotfix_trace_context_none_without_hotfix(tmp_path):
    from tracks.store import Store

    home = tmp_path / ".tracks"
    vdir = home / "projects" / "v0.3"
    vdir.mkdir(parents=True)
    store = Store(home)
    store.append("RUN3", "v0.3", "story.requested", {"raw_chars": 1})
    store.append("RUN3", "v0.3", "stage.entered", {"stage": "M-TEST"})
    assert _active_hotfix_trace_context(home, vdir) is None
    store.close()


# ---------------------------------------------------------------------------
# check reach / release-evidence / dispatcher
# ---------------------------------------------------------------------------


def test_cmd_check_reach_parsing_and_output(tmp_path, capsys, monkeypatch):
    assert _cmd_check_reach(tmp_path, ["--bogus"]) == 1
    assert "usage: trac check reach" in capsys.readouterr().err

    report = SimpleNamespace(
        status="fail",
        islands=["island.a"],
        entrypoints=["main"],
        errors=["reach err"],
        warnings=["reach warn"],
    )
    monkeypatch.setattr(validate_cmd, "check_reach_file", lambda repo, baseline, entries: report)
    assert _cmd_check_reach(tmp_path, []) == 1
    out = capsys.readouterr()
    assert "reach err" in out.err
    assert "warning: reach warn" in out.out
    assert "island module: island.a" in out.out

    report.islands = []
    report.errors = []
    report.status = "pass"
    assert _cmd_check_reach(tmp_path, ["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "pass"
    assert payload["entrypoints"] == ["main"]

    assert _cmd_check_reach(tmp_path, []) == 0
    assert "reach ok" in capsys.readouterr().out


def test_cmd_check_release_evidence(tmp_path, capsys, monkeypatch):
    assert _cmd_check_release_evidence(tmp_path, ["--bogus"]) == 2
    assert "usage: trac check release-evidence" in capsys.readouterr().err

    report = SimpleNamespace(
        backend="opencode",
        candidate_sha="abc",
        evidence_path="/e",
        event_bounds=(1, 2),
        reason_code="missing",
        run_id="RUN",
        status="not_satisfied",
        branch="main",
    )
    monkeypatch.setattr(validate_cmd, "check_release_evidence_file", lambda repo: report)
    assert _cmd_check_release_evidence(tmp_path, []) == 1
    out = capsys.readouterr().out
    assert "NOT satisfied — missing" in out
    assert "live journey" in out
    assert _cmd_check_release_evidence(tmp_path, ["--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["event_bounds"] == [1, 2]

    report.status = "satisfied"
    assert _cmd_check_release_evidence(tmp_path, []) == 0
    assert "release-evidence: satisfied" in capsys.readouterr().out
    assert _cmd_check_release_evidence(tmp_path, ["--json"]) == 0


def test_cmd_check_dispatch(tmp_path, capsys, monkeypatch):
    assert cmd_check(tmp_path) == 1
    assert "usage: trac check" in capsys.readouterr().err

    monkeypatch.setattr(validate_cmd, "check_deliverables", lambda: [])
    assert cmd_check(tmp_path, "deliverables") == 0
    assert "deliverables ok" in capsys.readouterr().out

    monkeypatch.setattr(validate_cmd, "check_deliverables", lambda: ["deliverable gap"])
    assert cmd_check(tmp_path, "deliverables") == 1
    assert "deliverable gap" in capsys.readouterr().err

    assert cmd_check(tmp_path, "deliverables", "extra") == 1
    assert "usage: trac check deliverables" in capsys.readouterr().err

    monkeypatch.setattr(validate_cmd, "_cmd_check_trace", lambda repo, rest: 11)
    monkeypatch.setattr(validate_cmd, "_cmd_check_reach", lambda repo, rest: 22)
    monkeypatch.setattr(validate_cmd, "_cmd_check_release_evidence", lambda repo, rest: 33)
    assert cmd_check(tmp_path, "trace") == 11
    assert cmd_check(tmp_path, "reach") == 22
    assert cmd_check(tmp_path, "release-evidence") == 33
    assert cmd_check(tmp_path, "bogus") == 1
    assert "usage: trac check" in capsys.readouterr().err
