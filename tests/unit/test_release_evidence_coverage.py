"""Behavior coverage for the IF-RELEASE-001 checker (release_evidence).

Every assertion targets the documented semantics of the module docstring:
selection by UTF-8 path/run byte order, the fixed reason-code precedence
(missing -> stale -> malformed -> not_real -> audit_incomplete ->
journey_incomplete), strict HEAD equality, and the read-only file wrapper.
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from tracks.checks import release_evidence as re
from tracks.executor.live_evidence import EVIDENCE_ROOT, GATE_NAMES, SCHEMA_VERSION

HEAD = "a" * 40
RUN = "run-001"


# ---------------------------------------------------------------------------
# fixture builders
# ---------------------------------------------------------------------------


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _evidence_path(sha: str = HEAD, run: str = RUN) -> str:
    return f"{EVIDENCE_ROOT}/{sha}/{run}/evidence.json"


def _blob_ref(name: str, sha: str = HEAD, run: str = RUN) -> str:
    return f"{EVIDENCE_ROOT}/{sha}/{run}/blobs/{name}"


def _receipt(
    role: str,
    phase: str,
    command_seq: int,
    outcome_seq: int,
    blobs: dict,
    *,
    sha: str = HEAD,
    run: str = RUN,
):
    in_data = f"{role}-{phase}-input".encode()
    out_data = f"{role}-{phase}-output".encode()
    in_ref = _blob_ref(f"{role}-{phase}-in", sha, run)
    out_ref = _blob_ref(f"{role}-{phase}-out", sha, run)
    blobs[in_ref], blobs[out_ref] = in_data, out_data
    return {
        "role": role,
        "phase": phase,
        "task_id": "T-001",
        "attempt": 1,
        "command_seq": command_seq,
        "outcome_seq": outcome_seq,
        "input_ref": in_ref,
        "input_sha256": _sha(in_data),
        "output_ref": out_ref,
        "output_sha256": _sha(out_data),
        "audit_completeness": "complete",
    }


def _valid_bundle(*, head: str = HEAD, run: str = RUN):
    """A bundle satisfying every §1j/§2d invariant, with its blobs."""
    blobs: dict[str, bytes] = {}
    agent_io = [
        _receipt("devon", "RED", 2, 3, blobs, sha=head, run=run),
        _receipt("devon", "GREEN", 4, 5, blobs, sha=head, run=run),
        _receipt("devon", "REFACTOR", 6, 7, blobs, sha=head, run=run),
        _receipt("prism", "PRISM_RED", 8, 9, blobs, sha=head, run=run),
        _receipt("prism", "PRISM_FINAL", 14, 15, blobs, sha=head, run=run),
    ]
    events_data = b"evidence-events"
    events_ref = _blob_ref("events", head, run)
    blobs[events_ref] = events_data
    trailers = {
        "Tracks-Task": "T-001",
        "Tracks-Attempt": "1",
        "Tracks-R": "c" * 40,
        "Tracks-Issue": "42",
        "Tracks-AC": "AC-FR0030-01",
    }
    gates = [
        {
            "gate": gate,
            "status": "pass",
            "seq": 12 + index,
            "source": "prism" if gate == "PRISM_FINAL" else "runtime",
            "source_event_type": "verdict.passed",
            "command_id": f"cmd-{index}",
        }
        for index, gate in enumerate(GATE_NAMES)
    ]
    bundle = {
        "schema_version": SCHEMA_VERSION,
        "status": "satisfied",
        "candidate_sha": head,
        "run_id": run,
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
            "last_seq": 20,
            "events_ref": events_ref,
            "events_sha256": _sha(events_data),
        },
        "rgr_lineage": {
            "task_id": "T-001",
            "attempt": 1,
            "red_ref": "refs/trac/rgr/run-001/T-001/1/red",
            "r_sha": "c" * 40,
            "g_sha": "d" * 40,
            "red_checkpoint_seq": 10,
            "green_commit_seq": 11,
            "trailers": trailers,
        },
        "gate_observations": gates,
        "boundary": {
            "stage": "M-IMPL",
            "stage_exited_seq": 19,
            "run_completed_seq": 20,
            "terminal_state": "boundary",
        },
    }
    return bundle, blobs


def _git_facts(bundle: dict) -> dict:
    lineage = bundle["rgr_lineage"]
    return {
        "r_ref_sha": lineage["r_sha"],
        "g_parent": "e" * 40,
        "g_trailers": lineage["trailers"],
    }


def _check(bundle, blobs, *, head=HEAD, branch="main", path=None, git_facts=None):
    return re.check_release_evidence(
        current_head=head,
        branch=branch,
        evidence_candidates=[(path or _evidence_path(), json.dumps(bundle).encode())],
        blobs=blobs,
        git_facts=git_facts if git_facts is not None else _git_facts(bundle),
    )


def _assert_reason(report, reason: str) -> None:
    assert report.status == "not_satisfied"
    assert report.reason_code == reason


# ---------------------------------------------------------------------------
# path parsing / selection
# ---------------------------------------------------------------------------


def test_parse_evidence_path_accepts_only_canonical_layout():
    assert re._parse_evidence_path(_evidence_path()) == (HEAD, RUN)
    assert re._parse_evidence_path(f"{EVIDENCE_ROOT}/{HEAD}/evidence.json") is None
    assert re._parse_evidence_path(f"other/root/{HEAD}/{RUN}/evidence.json") is None
    assert re._parse_evidence_path(f"{EVIDENCE_ROOT}/{HEAD}/{RUN}/bundle.json") is None
    assert re._parse_evidence_path("evidence.json") is None


def test_select_current_skips_unparseable_and_marks_other_shas():
    current, has_other = re._select_current(
        HEAD,
        [
            ("not/a/canonical/path.json", b"{}"),
            (_evidence_path(), b"{}"),
            (_evidence_path("f" * 40, "run-000"), b"{}"),
        ],
    )
    assert [item[0] for item in current] == [_evidence_path()]
    assert has_other is True


def test_newest_bundle_picks_highest_run_then_path_byte_order():
    items = [
        (_evidence_path(run="run-002"), "run-002", HEAD, b"b"),
        (_evidence_path(run="run-010"), "run-010", HEAD, b"c"),
        (f"{EVIDENCE_ROOT}/{HEAD}/run-002/evidence.json", "run-002", HEAD, b"a"),
    ]
    picked = re._newest_bundle(items)
    assert picked is not None and picked[1] == "run-010"
    assert re._newest_bundle([]) is None


def test_load_bundle_rejects_bad_utf8_json_and_non_dict():
    assert re._load_bundle(b"\xff\xfe") is None
    assert re._load_bundle(b"{not json") is None
    assert re._load_bundle(b"[1,2]") is None
    assert re._load_bundle(b'{"a":1}') == {"a": 1}


# ---------------------------------------------------------------------------
# public check: reason precedence
# ---------------------------------------------------------------------------


def test_no_candidates_is_missing():
    report = re.check_release_evidence(
        current_head=HEAD,
        branch="main",
        evidence_candidates=[],
        blobs={},
        git_facts={},
    )
    _assert_reason(report, "missing")
    assert report.candidate_sha == HEAD
    assert report.evidence_path is None


def test_only_other_shas_is_stale():
    report = re.check_release_evidence(
        current_head=HEAD,
        branch="main",
        evidence_candidates=[(_evidence_path("f" * 40), b"{}")],
        blobs={},
        git_facts={},
    )
    _assert_reason(report, "stale")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.__setitem__("schema_version", "tracks.release-evidence/v2"),
        lambda b: b.__setitem__("candidate_sha", "f" * 40),
        lambda b: b.__setitem__("run_id", "other-run"),
    ],
)
def test_schema_candidate_and_run_mismatch_are_malformed(mutate):
    bundle, blobs = _valid_bundle()
    mutate(bundle)
    report = _check(bundle, blobs)
    _assert_reason(report, "malformed")
    assert report.evidence_path == _evidence_path()
    assert report.run_id == RUN


def test_malformed_precedes_not_real():
    """A bundle that is both malformed and fake reports the earlier reason."""
    bundle, blobs = _valid_bundle()
    bundle["candidate_sha"] = "f" * 40
    bundle["backend"] = "fake"
    _assert_reason(_check(bundle, blobs), "malformed")


def test_unparseable_json_is_malformed():
    report = re.check_release_evidence(
        current_head=HEAD,
        branch="main",
        evidence_candidates=[(_evidence_path(), b"{not json")],
        blobs={},
        git_facts={},
    )
    _assert_reason(report, "malformed")


def test_not_real_precedes_audit_incomplete():
    bundle, blobs = _valid_bundle()
    bundle["backend"] = "fake"
    blobs.pop(bundle["event_sequence"]["events_ref"])
    _assert_reason(_check(bundle, blobs), "not_real")


def test_audit_incomplete_precedes_journey_incomplete():
    bundle, blobs = _valid_bundle()
    blobs.pop(bundle["event_sequence"]["events_ref"])
    bundle["status"] = "failed"
    report = _check(bundle, blobs)
    _assert_reason(report, "audit_incomplete")
    assert report.backend == "opencode"


def test_journey_incomplete_reports_parsed_identity():
    bundle, blobs = _valid_bundle()
    bundle["status"] = "failed"
    report = _check(bundle, blobs)
    _assert_reason(report, "journey_incomplete")
    assert report.event_bounds == (1, 20)
    assert report.run_id == RUN


def test_satisfied_report_carries_all_observable_fields():
    bundle, blobs = _valid_bundle()
    report = _check(bundle, blobs, branch="release/v1")
    assert report.status == "satisfied"
    assert report.reason_code == "ok"
    assert report.candidate_sha == HEAD
    assert report.run_id == RUN
    assert report.backend == "opencode"
    assert report.evidence_path == _evidence_path()
    assert report.event_bounds == (1, 20)
    assert report.branch == "release/v1"


def test_newest_current_bundle_wins():
    older, blobs = _valid_bundle(run="run-001")
    newer, blobs2 = _valid_bundle(run="run-002")
    blobs.update(blobs2)
    report = re.check_release_evidence(
        current_head=HEAD,
        branch="main",
        evidence_candidates=[
            (_evidence_path(run="run-001"), json.dumps(older).encode()),
            (_evidence_path(run="run-002"), json.dumps(newer).encode()),
        ],
        blobs=blobs,
        git_facts=_git_facts(newer),
    )
    assert report.status == "satisfied"
    assert report.run_id == "run-002"


# ---------------------------------------------------------------------------
# provenance / identity predicates
# ---------------------------------------------------------------------------


def test_provenance_every_fake_field_fails_closed():
    bundle, _ = _valid_bundle()
    assert re._provenance_ok(bundle) is True
    assert re._backend_of({"backend": 7}) is None
    assert re._backend_of({"backend": "opencode"}) == "opencode"
    for field in (
        "fake_backend",
        "trac_fake_simulate",
        "assignment_overlay",
        "assignment_simulation",
    ):
        mutated = copy.deepcopy(bundle)
        mutated["provenance"][field] = True
        assert re._provenance_ok(mutated) is False, field
    assert re._provenance_ok({}) is False
    assert re._provenance_ok({**bundle, "backend": "fake"}) is False
    assert re._provenance_ok({**bundle, "provenance": []}) is False
    assert (
        re._provenance_ok({**bundle, "provenance": {**bundle["provenance"], "backend_class": "Fake"}})
        is False
    )
    assert (
        re._provenance_ok({**bundle, "provenance": {**bundle["provenance"], "event_origin": "manual"}})
        is False
    )


def test_blob_digest_matches_bytes_and_rejects_shapes():
    blobs = {"ref": b"data"}
    assert re._blob_digest("ref", _sha(b"data"), blobs) is True
    assert re._blob_digest("ref", _sha(b"other"), blobs) is False
    assert re._blob_digest("missing", _sha(b"data"), blobs) is False
    assert re._blob_digest(1, _sha(b"data"), blobs) is False
    assert re._blob_digest("ref", 7, blobs) is False


def test_receipt_audit_rejects_each_invariant():
    _, blobs = _valid_bundle()
    good = _receipt("devon", "RED", 1, 2, blobs)
    assert re._receipt_audit_ok(good, blobs) is True
    assert re._receipt_audit_ok(None, blobs) is False
    for field, value in (
        ("audit_completeness", "partial"),
        ("command_seq", "x"),
        ("outcome_seq", None),
        ("command_seq", 2),
    ):
        bad = dict(good)
        bad[field] = value
        assert re._receipt_audit_ok(bad, blobs) is False, field
    bad = dict(good)
    bad["input_sha256"] = "0" * 64
    assert re._receipt_audit_ok(bad, blobs) is False
    bad = dict(good)
    bad["output_ref"] = "missing"
    assert re._receipt_audit_ok(bad, blobs) is False


def test_audit_ok_requires_sequence_and_receipts():
    bundle, blobs = _valid_bundle()
    assert re._audit_ok(bundle, blobs) is True
    assert re._audit_ok({**bundle, "event_sequence": []}, blobs) is False
    assert re._audit_ok({**bundle, "agent_io": {}}, blobs) is False
    assert re._audit_ok({**bundle, "agent_io": [None]}, blobs) is False


def test_event_bounds_shape_rules():
    bundle, _ = _valid_bundle()
    assert re._event_bounds(bundle) == (1, 20)
    assert re._event_bounds({**bundle, "event_sequence": None}) is None
    assert re._event_bounds({**bundle, "event_sequence": {"first_seq": "1", "last_seq": 20}}) is None


def test_required_phases_contract():
    bundle, _ = _valid_bundle()
    assert re._required_phases_ok(bundle) is True
    assert re._required_phases_ok({**bundle, "required_devon_phases": ["RED"]}) is False
    assert (
        re._required_phases_ok({**bundle, "required_devon_phases": ["RED", "GREEN", "REFACTOR", "X"]})
        is False
    )
    assert re._required_phases_ok({**bundle, "agent_io": None}) is False
    missing = [r for r in bundle["agent_io"] if r["phase"] != "GREEN"]
    assert re._required_phases_ok({**bundle, "agent_io": missing}) is False


def test_seq_collection_covers_all_evidence_channels():
    bundle, _ = _valid_bundle()
    seqs = re._collect_seqs(bundle)
    assert {2, 3, 10, 11, 12, 17, 19, 20} <= set(seqs)
    assert re._seqs_from_items([None, {"a": 1}, {"command_seq": "x"}], ("command_seq",)) == []
    empty = {
        **bundle,
        "agent_io": None,
        "rgr_lineage": None,
        "gate_observations": None,
        "boundary": None,
    }
    assert re._collect_seqs(empty) == []


def test_trailers_required_key_set():
    assert re._trailers_ok({"trailers": dict.fromkeys(re._REQUIRED_TRAILERS, "x")}) is True
    assert re._trailers_ok({"trailers": {"Tracks-Task": "x"}}) is False
    assert re._trailers_ok({}) is False


def test_lineage_contract_variants():
    bundle, _ = _valid_bundle()
    git_facts = _git_facts(bundle)
    lineage = bundle["rgr_lineage"]
    assert re._lineage_ok(bundle, git_facts) is True
    assert re._lineage_ok({**bundle, "rgr_lineage": None}, git_facts) is False
    assert re._lineage_ok(bundle, {**git_facts, "r_ref_sha": "f" * 40}) is False
    assert re._lineage_ok(bundle, {**git_facts, "g_parent": None}) is False
    assert re._lineage_ok(bundle, {**git_facts, "g_parent": lineage["r_sha"]}) is False
    assert re._lineage_ok(bundle, {**git_facts, "g_parent": lineage["g_sha"]}) is False
    assert re._lineage_ok(bundle, {**git_facts, "g_trailers": {}}) is False
    assert (
        re._lineage_ok(
            {**bundle, "rgr_lineage": {**lineage, "red_checkpoint_seq": "x"}}, git_facts
        )
        is False
    )
    assert (
        re._lineage_ok(
            {**bundle, "rgr_lineage": {**lineage, "red_checkpoint_seq": 11}}, git_facts
        )
        is False
    )
    assert (
        re._lineage_ok(
            {**bundle, "rgr_lineage": {**lineage, "trailers": {}}}, git_facts
        )
        is False
    )


def test_gates_contract_variants():
    bundle, _ = _valid_bundle()
    gates = copy.deepcopy(bundle["gate_observations"])
    assert re._gates_ok(bundle) is True
    assert re._gates_ok({**bundle, "gate_observations": None}) is False
    assert re._gates_ok({**bundle, "gate_observations": gates[:3]}) is False
    swapped = copy.deepcopy(gates)
    swapped[0]["gate"] = "GREEN_GATE"
    swapped[1]["gate"] = "RED_GATE"
    assert re._gates_ok({**bundle, "gate_observations": swapped}) is False
    non_int = copy.deepcopy(gates)
    non_int[2]["seq"] = "x"
    assert re._gates_ok({**bundle, "gate_observations": non_int}) is False
    non_ascending = copy.deepcopy(gates)
    non_ascending[2]["seq"] = non_ascending[1]["seq"]
    assert re._gates_ok({**bundle, "gate_observations": non_ascending}) is False
    failed = copy.deepcopy(gates)
    failed[3]["status"] = "fail"
    assert re._gates_ok({**bundle, "gate_observations": failed}) is False
    wrong_source = copy.deepcopy(gates)
    wrong_source[3]["source"] = "prism"
    assert re._gates_ok({**bundle, "gate_observations": wrong_source}) is False
    wrong_prism = copy.deepcopy(gates)
    wrong_prism[4]["source"] = "runtime"
    assert re._gates_ok({**bundle, "gate_observations": wrong_prism}) is False


def test_boundary_contract_variants():
    bundle, _ = _valid_bundle()
    boundary = bundle["boundary"]
    assert re._boundary_ok(bundle) is True
    assert re._boundary_ok({**bundle, "boundary": None}) is False
    assert re._boundary_ok({**bundle, "boundary": {**boundary, "stage": "M-TEST"}}) is False
    assert (
        re._boundary_ok({**bundle, "boundary": {**boundary, "terminal_state": "open"}})
        is False
    )
    assert (
        re._boundary_ok({**bundle, "boundary": {**boundary, "stage_exited_seq": "x"}})
        is False
    )
    assert (
        re._boundary_ok({**bundle, "boundary": {**boundary, "stage_exited_seq": 20}})
        is False
    )


def test_journey_contract_short_circuits():
    bundle, _ = _valid_bundle()
    git_facts = _git_facts(bundle)
    assert re._journey_ok(bundle, git_facts, (1, 20)) is True
    assert re._journey_ok({**bundle, "status": "pending"}, git_facts, (1, 20)) is False
    assert re._journey_ok(bundle, git_facts, (10, 20)) is False
    assert re._journey_ok(bundle, git_facts, (1, 5)) is False
    no_trailers = copy.deepcopy(bundle)
    no_trailers["rgr_lineage"]["trailers"] = {}
    assert re._journey_ok(no_trailers, git_facts, (1, 20)) is False


# ---------------------------------------------------------------------------
# real-git file wrapper (read-only path)
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _init_git_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """Repo where HEAD's parent is NOT the R commit (g_parent != r_sha)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "t")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-m", "base")
    _git(repo, "checkout", "-b", "red")
    (repo / "red.txt").write_text("red\n", encoding="utf-8")
    _git(repo, "add", "red.txt")
    _git(repo, "commit", "-m", "red checkpoint")
    red_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "main")
    (repo / "green.txt").write_text("green\n", encoding="utf-8")
    _git(repo, "add", "green.txt")
    _git(
        repo,
        "commit",
        "-m",
        "green commit\n\nTracks-Task: T-001\nTracks-Attempt: 1\n"
        f"Tracks-R: {red_sha}\nTracks-Issue: 42\nTracks-AC: AC-FR0030-01\n",
    )
    return repo, red_sha, _git(repo, "rev-parse", "HEAD")


def _write_file_bundle(repo: Path, red_sha: str, head: str, run: str = RUN):
    bundle, blobs = _valid_bundle(head=head, run=run)
    bundle["rgr_lineage"]["red_ref"] = "red"
    bundle["rgr_lineage"]["r_sha"] = red_sha
    bundle["rgr_lineage"]["g_sha"] = head
    bundle["rgr_lineage"]["trailers"]["Tracks-R"] = red_sha
    run_dir = repo / EVIDENCE_ROOT / head / run
    (run_dir / "blobs").mkdir(parents=True)
    for ref, data in blobs.items():
        (repo / ref).write_bytes(data)
    payload = copy.deepcopy(bundle)
    (run_dir / "evidence.json").write_text(json.dumps(payload), encoding="utf-8")
    return bundle


def test_check_file_satisfied_uses_real_git_facts(tmp_path: Path):
    repo, red_sha, head = _init_git_repo(tmp_path)
    _write_file_bundle(repo, red_sha, head)
    report = re.check_release_evidence_file(str(repo))
    assert report.status == "satisfied"
    assert report.candidate_sha == head
    assert report.run_id == RUN
    assert report.branch == "main"


def test_check_file_missing_and_stale(tmp_path: Path):
    repo, _red, _head = _init_git_repo(tmp_path)
    missing = re.check_release_evidence_file(str(repo))
    _assert_reason(missing, "missing")
    assert missing.branch == "main"
    _write_file_bundle(repo, _red, "f" * 40, run="old-run")
    stale = re.check_release_evidence_file(str(repo))
    _assert_reason(stale, "stale")


def test_check_file_without_git_is_read_only_and_missing(tmp_path: Path):
    report = re.check_release_evidence_file(str(tmp_path))
    _assert_reason(report, "missing")
    assert report.candidate_sha is None
    assert report.branch == "(detached)"
    assert not list(tmp_path.iterdir())


def test_git_returns_none_on_nonzero_and_oserror(tmp_path: Path, monkeypatch):
    assert re._git(tmp_path, "rev-parse", "HEAD") is None

    def boom(*_args, **_kwargs):
        raise OSError("no git")

    monkeypatch.setattr(re.subprocess, "run", boom)
    assert re._git(tmp_path, "rev-parse", "HEAD") is None


def test_git_facts_paths(tmp_path: Path, monkeypatch):
    repo, red_sha, head = _init_git_repo(tmp_path)
    _write_file_bundle(repo, red_sha, head)
    candidates = re._enumerate_evidence(repo)
    facts = re._git_facts(repo, head, candidates)
    assert facts["r_ref_sha"] == red_sha
    assert facts["g_parent"] == _git(repo, "rev-parse", f"{head}^")
    assert facts["g_trailers"]["Tracks-Task"] == "T-001"
    defaults = {"r_ref_sha": None, "g_parent": None, "g_trailers": {}}
    assert re._git_facts(repo, None, []) == defaults
    assert re._git_facts(repo, "f" * 40, candidates) == defaults
    assert re._bundle_lineage(None) is None
    assert re._bundle_lineage({}) is None
    assert re._bundle_lineage({"rgr_lineage": {"x": 1}}) == {"x": 1}


def test_enumerate_evidence_reads_canonical_files(tmp_path: Path):
    repo = tmp_path / "repo"
    run_dir = repo / EVIDENCE_ROOT / HEAD / RUN
    run_dir.mkdir(parents=True)
    (run_dir / "evidence.json").write_bytes(b"{}")
    (run_dir / "ignored.txt").write_text("x", encoding="utf-8")
    assert re._enumerate_evidence(repo) == [(_evidence_path(), b"{}")]
    assert re._enumerate_evidence(tmp_path / "nope") == []


def test_enumerate_evidence_unreadable_file_is_empty_bytes(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    run_dir = repo / EVIDENCE_ROOT / HEAD / RUN
    run_dir.mkdir(parents=True)
    (run_dir / "evidence.json").write_bytes(b"ignored")
    original = Path.read_bytes

    def broken(self):
        raise OSError("denied")

    monkeypatch.setattr(Path, "read_bytes", broken)
    try:
        assert re._enumerate_evidence(repo) == [(_evidence_path(), b"")]
    finally:
        monkeypatch.setattr(Path, "read_bytes", original)


def test_enumerate_blobs_skips_directories_and_unreadable(tmp_path: Path):
    repo = tmp_path / "repo"
    blob_dir = repo / EVIDENCE_ROOT / HEAD / RUN / "blobs"
    blob_dir.mkdir(parents=True)
    (blob_dir / "a").write_bytes(b"a")
    (blob_dir / "nested").mkdir()
    blobs = re._enumerate_blobs(repo)
    assert blobs == {f"{EVIDENCE_ROOT}/{HEAD}/{RUN}/blobs/a": b"a"}


def test_run_dirs_sorted_and_files_ignored(tmp_path: Path):
    root = tmp_path / "root"
    (root / "sha-b" / "run-2").mkdir(parents=True)
    (root / "sha-a" / "run-1").mkdir(parents=True)
    (root / "sha-a" / "file.txt").write_text("x", encoding="utf-8")
    (root / "loose.txt").write_text("x", encoding="utf-8")
    names = [p.parent.name + "/" + p.name for p in re._run_dirs(root)]
    assert names == ["sha-a/run-1", "sha-b/run-2"]
    assert re._run_dirs(tmp_path / "missing") == []


def test_parse_trailers_shapes():
    assert re._parse_trailers(None) == {}
    assert re._parse_trailers("") == {}
    assert re._parse_trailers("subject only") == {}
    message = "subject\nTracks-Task: T-001\nBad line\nTracks-Task: T-002\n"
    assert re._parse_trailers(message) == {"Tracks-Task": "T-002"}


def test_file_wrapper_skips_unparseable_sibling_evidence(tmp_path: Path):
    repo, red_sha, head = _init_git_repo(tmp_path)
    _write_file_bundle(repo, red_sha, head)
    junk_dir = repo / EVIDENCE_ROOT / "not-a-sha" / "run-x"
    junk_dir.mkdir(parents=True)
    (junk_dir / "evidence.json").write_bytes(b"{}")
    assert re.check_release_evidence_file(str(repo)).status == "satisfied"
