"""Behavior coverage for tag-publish authority validation (``publish_runtime``):

preview blob verification, contract resolution fallbacks, candidate/approval
checks, operation-record validation and the remote/API effect mapping.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from tracks import paths
from tracks.executor import publish_runtime as pr


def _ev(seq: int, etype: str, payload=None):
    return SimpleNamespace(seq=seq, type=etype, payload=payload or {})


def _authority(**overrides) -> dict:
    record = {
        "idempotency_key": "key-1",
        "operation_kind": "tag",
        "target": "v1.0",
    }
    record.update(overrides.pop("record", {}))
    base = {
        "candidate_sha": "c" * 40,
        "preview_digest": "digest",
        "journey": "feature",
        "record": record,
        "records": [record],
        "remote_url": "https://github.com/acme/host",
    }
    base.update(overrides)
    return base


# -- preview blob -----------------------------------------------------------


def test_preview_blob_matches_paths(tmp_path):
    home = tmp_path / ".tracks"
    assert pr._preview_blob_matches(home, {}) is False
    assert pr._preview_blob_matches(home, {"blob_ref": "no-ref"}) is False

    preview = {"verdict": "pass"}
    raw = json.dumps(preview, sort_keys=True).encode("utf-8")
    import hashlib

    name = hashlib.sha256(raw).hexdigest()
    blobs = paths.blobs_dir(home)
    blobs.mkdir(parents=True)
    (blobs / name).write_bytes(raw)
    assert pr._preview_blob_matches(
        home, {**preview, "blob_ref": f".tracks/runtime/blobs/{name}"}
    ) is True

    (blobs / name).write_text("{bad", encoding="utf-8")
    assert pr._preview_blob_matches(
        home, {**preview, "blob_ref": f".tracks/runtime/blobs/{name}"}
    ) is False

    invalid = b"{bad"
    invalid_name = hashlib.sha256(invalid).hexdigest()
    (blobs / invalid_name).write_bytes(invalid)
    assert pr._preview_blob_matches(
        home, {"blob_ref": f".tracks/runtime/blobs/{invalid_name}"}
    ) is False


# -- contract resolution ----------------------------------------------------


def _write_contract(repo: Path, text: str) -> None:
    path = repo.joinpath(*pr.CANONICAL_CONTRACT_RELPATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_load_contract_invalid_declared_falls_back(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_contract(repo, "not = [valid")
    assert pr._load_contract(repo) is None


def test_load_contract_declared_but_invalid_fails_closed(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_contract(repo, "[host-contract]\nversion = 1\n")
    monkeypatch.setattr(
        pr,
        "load_host_contract",
        lambda path: (_ for _ in ()).throw(ValueError("bad contract")),
    )
    assert pr._load_contract(repo) is None


def test_load_contract_materialized_default(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_contract(repo, "# no host-contract table\n")
    monkeypatch.setattr(pr.paths, "tracks_home", lambda repo: tmp_path / "home")
    runtime = tmp_path / "home" / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "materialized-host-contract.toml").write_text(
        "[host-contract]\nversion = 1\nlanguage = 'python'\n", encoding="utf-8"
    )
    raw, table = pr._load_contract(repo)
    assert isinstance(raw, bytes)
    assert table["language"] == "python"


# -- latest preview / candidate / approval ----------------------------------


def test_latest_preview_branches():
    assert pr._latest_preview([], None) == (None, "missing_preview")
    first = _ev(1, "release.previewed", {"preview_digest": "d1"})
    second = _ev(2, "release.previewed", {"preview_digest": "d2"})
    assert pr._latest_preview([first, second], "d1") == (None, "preview_stale")
    assert pr._latest_preview([first, second], "missing") == (None, "missing_preview")
    assert pr._latest_preview([first], "d1") == (first, None)

    missing_digest = _ev(1, "release.previewed", {})
    assert pr._latest_preview([missing_digest], None) == (None, "missing_preview")


def test_candidate_sha_missing_drift_and_clean(monkeypatch, tmp_path):
    preview = {"candidate_sha": "c" * 40}
    assert pr._candidate_sha(tmp_path, [], preview) == (None, "missing_candidate")

    frozen = _ev(1, "candidate.frozen", {"candidate_sha": "c" * 40})
    monkeypatch.setattr(
        pr, "git", lambda *a, **k: SimpleNamespace(returncode=1, stdout="")
    )
    assert pr._candidate_sha(tmp_path, [frozen], preview) == (None, "candidate_drift")

    calls = {"n": 0}

    def fake_git(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            return SimpleNamespace(returncode=0, stdout="c" * 40 + "\n")
        return SimpleNamespace(returncode=0, stdout=" M dirty.py\n")

    monkeypatch.setattr(pr, "git", fake_git)
    assert pr._candidate_sha(tmp_path, [frozen], preview) == (None, "candidate_dirty")


def test_approval_error_branches():
    candidate = "c" * 40
    preview_event = _ev(1, "release.previewed", {"preview_digest": "d"})
    assert pr._approval_error([], preview_event, "d", candidate) == "not_approved"

    wrong_actor = _ev(
        2,
        "release.decided",
        {
            "candidate_sha": candidate,
            "preview_digest": "d",
            "action": "release",
            "actor": "agent",
        },
    )
    assert pr._approval_error(
        [preview_event, wrong_actor], preview_event, "d", candidate
    ) == "not_approved"

    approved = _ev(
        2,
        "release.decided",
        {
            "candidate_sha": candidate,
            "preview_digest": "d",
            "action": "release",
            "actor": "human",
        },
    )
    stale = _ev(3, "candidate.stale", {"candidate_sha": candidate})
    assert pr._approval_error(
        [preview_event, approved, stale], preview_event, "d", candidate
    ) == "preview_stale"
    assert pr._approval_error(
        [preview_event, approved], preview_event, "d", candidate
    ) is None


# -- preview error validation ----------------------------------------------


def _preview(plan=None, **overrides) -> dict:
    from tracks.executor.release_gate import compute_preview_digest

    plan = {"journey": "feature", "steps": []} if plan is None else plan
    base = {
        "artifact_digest": "",
        "evidence_digests": {},
        "operation_plan_digest": pr._digest(plan),
        "contract_policy_digest": "sha256:" + "0" * 64,
        "operation_plan": plan,
    }
    base.update(overrides)
    base["preview_digest"] = compute_preview_digest(
        "c" * 40,
        base["artifact_digest"],
        base["evidence_digests"],
        base["operation_plan_digest"],
        base["contract_policy_digest"],
    )
    return base


def test_preview_error_digest_and_plan_shape(tmp_path):
    preview = _preview()
    _, error = pr._preview_error(
        tmp_path, preview, "bogus", "c" * 40, {}
    )
    assert error == "preview_digest_mismatch"

    malformed = _preview(plan={"journey": "feature"})
    malformed["operation_plan"] = "not-a-dict"
    _, error = pr._preview_error(
        tmp_path, malformed, malformed["preview_digest"], "c" * 40, {}
    )
    assert error == "malformed"

    changed = _preview()
    changed["operation_plan_digest"] = "sha256:other"
    from tracks.executor.release_gate import compute_preview_digest

    changed["preview_digest"] = compute_preview_digest(
        "c" * 40,
        changed["artifact_digest"],
        changed["evidence_digests"],
        changed["operation_plan_digest"],
        changed["contract_policy_digest"],
    )
    _, error = pr._preview_error(
        tmp_path, changed, changed["preview_digest"], "c" * 40, {}
    )
    assert error == "operation_plan_changed"


def test_preview_error_contract_branches(tmp_path, monkeypatch):
    preview = _preview()
    monkeypatch.setattr(pr, "_load_contract", lambda repo: None)
    _, error = pr._preview_error(
        tmp_path, preview, preview["preview_digest"], "c" * 40, {}
    )
    assert error == "missing_contract"

    monkeypatch.setattr(pr, "_load_contract", lambda repo: (b"raw", {}))
    _, error = pr._preview_error(
        tmp_path, preview, preview["preview_digest"], "c" * 40, {}
    )
    assert error == "contract_changed"

    preview = _preview(contract_policy_digest=pr._digest_bytes(b"raw"))
    monkeypatch.setattr(
        pr, "_revalidated_plan", lambda *a: (None, "release_facts_unresolved")
    )
    _, error = pr._preview_error(
        tmp_path, preview, preview["preview_digest"], "c" * 40, {}
    )
    assert error == "release_facts_unresolved"

    monkeypatch.setattr(pr, "_revalidated_plan", lambda *a: ({"other": 1}, None))
    _, error = pr._preview_error(
        tmp_path, preview, preview["preview_digest"], "c" * 40, {}
    )
    assert error == "operation_plan_changed"


def test_revalidated_plan_facts_unresolved(monkeypatch, tmp_path):
    monkeypatch.setattr(
        pr, "complete_version_facts", lambda *a, **k: (None, "missing_facts")
    )
    plan, error = pr._revalidated_plan(
        tmp_path, {"journey": "feature"}, {}, {"feature_tag": "v1.0"}
    )
    assert plan is None
    assert error == "missing_facts"


# -- ref/artifact validation ------------------------------------------------


def test_valid_refs_and_repo_id(tmp_path):
    assert pr._valid_ref(tmp_path, None) is False
    assert pr._valid_ref(tmp_path, "") is False
    assert pr._valid_tag_ref(tmp_path, None) is False
    assert pr._valid_tag_ref(tmp_path, "") is False
    assert pr._valid_branch_ref(tmp_path, None) is False
    assert pr._valid_branch_ref(tmp_path, "") is False
    assert pr._repo_id_from_remote(None) is None
    assert pr._repo_id_from_remote("git@github.com:acme/host.git") is None
    assert pr._repo_id_from_remote("https://github.com/acme/host.git") == "acme/host"


def test_resolve_artifact_shapes(tmp_path, monkeypatch):
    assert pr._resolve_artifact(tmp_path, None) is None
    assert pr._resolve_artifact(tmp_path, "   ") is None
    assert pr._resolve_artifact(tmp_path, "missing.bin") is None

    target = tmp_path / "pkg.bin"
    target.write_bytes(b"data")
    identity = pr._resolve_artifact(tmp_path, "pkg.bin")
    assert identity is not None
    assert identity["size"] == 4
    assert identity["name"] == "pkg.bin"

    monkeypatch.setattr(
        Path, "read_bytes", lambda self: (_ for _ in ()).throw(OSError("gone"))
    )
    assert pr._resolve_artifact(tmp_path, "pkg.bin") is None
    monkeypatch.undo()

    target.unlink()
    target.mkdir()
    assert pr._resolve_artifact(tmp_path, "pkg.bin") is None


# -- authority resolution ---------------------------------------------------


def test_resolve_publish_authority_error_and_remote(tmp_path, monkeypatch):
    preview_event = _ev(1, "release.previewed", {"preview_digest": "d"})
    monkeypatch.setattr(pr, "_latest_preview", lambda events, digest: (preview_event, None))
    monkeypatch.setattr(pr, "_preview_blob_matches", lambda home, preview: True)
    monkeypatch.setattr(pr, "_candidate_sha", lambda repo, events, preview: ("c" * 40, None))
    monkeypatch.setattr(pr, "_approval_error", lambda *a: None)
    monkeypatch.setattr(pr, "_preview_error", lambda *a: (None, "preview_digest_mismatch"))
    assert pr.resolve_publish_authority(
        tmp_path, [preview_event], "d", {}, tmp_path
    ) == (None, "preview_digest_mismatch")

    monkeypatch.setattr(pr, "_preview_error", lambda *a: ({"journey": "f"}, None))
    monkeypatch.setattr(pr, "_operation_records", lambda *a: ([], None))
    monkeypatch.setattr(
        pr, "git", lambda *a, **k: SimpleNamespace(returncode=1, stdout="")
    )
    assert pr.resolve_publish_authority(
        tmp_path, [preview_event], "d", {}, tmp_path
    ) == (None, "missing_remote")


def test_operation_records_and_validation(tmp_path):
    assert pr._operation_records(
        tmp_path, {"journey": "feature", "steps": []}, "d"
    ) == (None, "unknown_operation")
    assert pr._operation_records(tmp_path, "not-a-plan", "d") == (
        None,
        "unknown_operation",
    )
    assert pr._operation_records(
        tmp_path, {"journey": "feature", "steps": ["webhook:main"]}, "d"
    ) == (None, "unknown_operation")
    assert pr._operation_records(
        tmp_path, {"journey": "feature", "steps": [{"kind": "tag"}]}, "d"
    ) == (None, "malformed")


def test_validate_operation_record_unknown_and_merge(tmp_path, monkeypatch):
    assert pr._validate_operation_record(
        tmp_path, {"operation_kind": "webhook", "target": "x"}, set(), None
    ) == (None, "unknown_operation")

    monkeypatch.setattr(pr, "_valid_branch_ref", lambda repo, target: False)
    assert pr._validate_operation_record(
        tmp_path, {"operation_kind": "merge", "target": "main"}, set(), None
    ) == (None, "malformed")

    monkeypatch.setattr(pr, "_valid_tag_ref", lambda repo, target: True)
    assert pr._validate_operation_record(
        tmp_path, {"operation_kind": "release", "target": "v2"}, {"v1"}, None
    ) == (None, "malformed")

    monkeypatch.setattr(pr, "_resolve_artifact", lambda repo, target: None)
    assert pr._validate_operation_record(
        tmp_path, {"operation_kind": "artifact", "target": "pkg"}, set(), "v1"
    ) == (None, "malformed")


def test_artifact_release_target_selection():
    records = [
        {"operation_kind": "release", "target": "v1"},
        {"operation_kind": "tag", "target": "v1"},
    ]
    assert pr._artifact_release_target(records) == "v1"
    assert pr._artifact_release_target(
        [
            {"operation_kind": "release", "target": "v1"},
            {"operation_kind": "release", "target": "v2"},
        ]
    ) is None
    assert pr._artifact_release_target(
        [{"operation_kind": "tag", "target": "v1"}]
    ) == "v1"


# -- effect mapping ---------------------------------------------------------


def test_remote_verdict_and_push_confirmation():
    assert pr._remote_verdict({}, "sha") == "invalid"
    assert pr._remote_verdict(
        {"exists": True, "matches": False, "object_id": "x"}, "sha"
    ) == "conflict"
    assert pr._remote_verdict(
        {"exists": True, "matches": True, "object_id": "sha"}, "sha"
    ) == "skip"
    assert pr._remote_verdict({"exists": False}, "sha") == "pending"

    assert pr._push_confirmation("not-a-dict", "sha") == (None, {})
    status, confirmed = pr._push_confirmation(
        {
            "status": "done",
            "object_id": "sha",
            "remote_check": {"exists": True, "matches": True, "object_id": "sha"},
        },
        "sha",
    )
    assert status == "done"
    assert confirmed["matches"] is True


def test_handle_remote_error_and_invalid():
    fails: list = []
    events: list = []
    assert pr._handle_remote(
        _authority(),
        "CMD",
        {"error": "read failed"},
        lambda *a: None,
        lambda *a: events.append(a),
        lambda reason, remote: fails.append(reason),
        allow_ff=False,
    ) is False
    assert fails == ["remote_read_failed"]

    assert pr._handle_remote(
        _authority(),
        "CMD",
        {"exists": None},
        lambda *a: None,
        lambda *a: events.append(a),
        lambda reason, remote: fails.append(reason),
        allow_ff=False,
    ) is False
    assert fails[-1] == "remote_read_failed"


def test_handle_api_effect_shapes():
    fails: list = []
    events: list = []
    assert pr._handle_api_effect(
        _authority(), "CMD", "nope", lambda *a: events.append(a),
        lambda reason, remote: fails.append(reason),
    ) is False
    assert fails[-1] == "operation_failed"

    assert pr._handle_api_effect(
        _authority(),
        "CMD",
        {"status": "failed", "reason": "boom"},
        lambda *a: events.append(a),
        lambda reason, remote: fails.append(reason),
    ) is False
    assert fails[-1] == "boom"


def test_execute_record_unknown_operation_and_planned_payload():
    fails: list = []
    authority = _authority(record={"operation_kind": "webhook", "target": "main"})
    assert pr._execute_record(
        authority,
        "CMD",
        [],
        {},
        lambda *a, **k: None,
        lambda *a, **k: fails.append(a),
        None,
    ) is False
    assert fails[-1][0] == "unknown_operation"

    planned, error = pr._planned_payload([], authority, "CMD", None)
    assert error is None
    assert planned["candidate_sha"] == "c" * 40


def test_execute_tag_operation_wires_effects(monkeypatch):
    captured: dict = {}

    def fake_execute(authority, command_id, events, effects, emit, fail, when):
        captured["effects"] = effects
        return True

    monkeypatch.setattr(pr, "_execute_record", fake_execute)
    assert pr.execute_tag_operation(
        _authority(),
        "CMD",
        [],
        "read",
        "push",
        lambda *a: None,
        lambda *a: None,
        None,
    ) is True
    assert captured["effects"]["read_remote"] == "read"
    assert captured["effects"]["push_tag"] == "push"


def test_execute_publish_operations_stops_on_failure(monkeypatch):
    calls: list = []

    def fake_execute(single, command_id, events, effects, emit, fail, when):
        calls.append(single["record"]["target"])
        return single["record"]["target"] == "v1"

    monkeypatch.setattr(pr, "_execute_record", fake_execute)
    authority = _authority(
        records=[
            {"idempotency_key": "k1", "operation_kind": "tag", "target": "v1"},
            {"idempotency_key": "k2", "operation_kind": "tag", "target": "v2"},
        ]
    )
    pr.execute_publish_operations(
        authority, "CMD", lambda: [], {}, lambda *a: None, lambda *a: None, None
    )
    assert calls == ["v1", "v2"]
