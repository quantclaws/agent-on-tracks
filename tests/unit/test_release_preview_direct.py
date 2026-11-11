"""RED coverage for the M-RELEASE preview producer."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tests.unit.helpers import git_repo, git_strip
from tests.unit.hotfix262_release_support import (
    MINIMAL_HOST_CONTRACT_TOML,
    RELEASE_FACTS,
    quality_gate_payload,
)
from tests.unit.hotfix262_release_support import (
    full_payload as _full_evidence,
)
from tests.unit.hotfix262_release_support import (
    release_evidence as _release_evidence,
)
from tracks import paths
from tracks.executor.executor import Executor
from tracks.executor.release_authorization import assess_release
from tracks.executor.release_gate import build_operation_plan
from tracks.kernel.events import Command
from tracks.store import Store


def _canonical_digest(value: object) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _evidence_digest(payload: dict) -> str:
    material = {
        key: value for key, value in payload.items() if key != "contract_digest"
    }
    return _canonical_digest(material)


def _gate_payload(candidate: str, contract_digest: str) -> dict:
    return quality_gate_payload(
        candidate, contract_digest, summary={"result": "baseline"}
    )


def _expected_evidence_digests(
    gate_payload: dict, evidence_events: list[tuple[str, dict]]
) -> dict:
    expected = {"quality[0]": _evidence_digest(gate_payload)}
    for event_type, event_payload in evidence_events:
        key = {
            "full.executed": "full_f",
            "ci.run_observed": "ci",
            "security.assessed": "security",
        }.get(event_type)
        if event_type == "prism.verdict":
            key = "prism." + event_payload["scope"]
        assert key is not None
        expected[key] = _evidence_digest(event_payload)
    return expected


def _write_contract(repo: Path) -> Path:
    projects = repo / ".tracks" / "projects"
    projects.mkdir(parents=True, exist_ok=True)
    contract_path = projects / "project.toml"
    contract_path.write_text(MINIMAL_HOST_CONTRACT_TOML, encoding="utf-8")
    git_strip(repo, "add", "-f", ".tracks/projects/project.toml")
    git_strip(repo, "commit", "-m", "preview contract")
    return contract_path


def _host(tmp_path: Path) -> tuple[Executor, Store, Path, str, dict]:
    repo = git_repo(tmp_path, gitignore=True)
    contract_path = _write_contract(repo)
    candidate = git_strip(repo, "rev-parse", "HEAD")
    contract_digest = "sha256:" + hashlib.sha256(contract_path.read_bytes()).hexdigest()
    gate_payload = _gate_payload(candidate, contract_digest)
    evidence_events = _release_evidence(candidate)
    store = Store(repo / ".tracks")
    store.append(
        "RUN",
        "v0.8",
        "candidate.frozen",
        {
            "candidate_sha": candidate,
            "clean_tree": True,
            "branch": "main",
            "frozen_at_seq": 1,
        },
    )
    store.append("RUN", "v0.8", "local_gate.passed", gate_payload)
    for event_type, event_payload in evidence_events:
        store.append("RUN", "v0.8", event_type, event_payload)
    executor = Executor(store, repo, "RUN")
    facts = dict(RELEASE_FACTS)
    contract = {"operations": {"feature": {"steps": ["tag:{feature_tag}"]}}}
    plan = build_operation_plan(contract, "feature", facts)
    return executor, store, contract_path, candidate, {
        "plan": plan,
        "operation_plan_digest": _canonical_digest(plan),
        "contract_policy_digest": contract_digest,
        "evidence_digests": _expected_evidence_digests(gate_payload, evidence_events),
    }


def _emit_preview(executor: Executor, candidate: str) -> None:
    executor._release_preview(
        Command("generate_preview", command_id="CMD-PREVIEW"), candidate
    )


def _previews(store: Store) -> list:
    return [event for event in store.events("RUN") if event.type == "release.previewed"]


def test_release_preview_is_resolved_and_content_addressed(tmp_path):
    executor, store, _contract_path, candidate, expected = _host(tmp_path)

    _emit_preview(executor, candidate)

    previews = _previews(store)
    assert len(previews) == 1
    payload = previews[0].payload
    assert payload["candidate_sha"] == candidate  # tracks-anchor-ok: candidate comes from the _host capture; this test drives no HEAD-moving activity
    assert payload["operation_plan"] == expected["plan"]
    assert payload["operation_plan_digest"] == expected["operation_plan_digest"]
    assert payload["contract_policy_digest"] == expected["contract_policy_digest"]
    assert payload["evidence_digests"] == expected["evidence_digests"]
    blob_ref = payload["blob_ref"]
    blob_path = paths.blobs_dir(store.home) / blob_ref.rsplit("/", 1)[-1]
    raw = blob_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == blob_path.name
    assert json.loads(raw) == {
        key: value for key, value in payload.items() if key != "blob_ref"
    }


def test_contract_change_does_not_reuse_old_preview(tmp_path):
    # This unit mutates the same checkout only to isolate preview invalidation;
    # a publishable integration flow would commit the change as a new candidate.
    executor, store, contract_path, candidate, _expected = _host(tmp_path)
    _emit_preview(executor, candidate)
    contract_path.write_text(
        contract_path.read_text(encoding="utf-8").replace(
            "tag:{feature_tag}", "tag:v9.0"
        ),
        encoding="utf-8",
    )

    _emit_preview(executor, candidate)

    assert len(_previews(store)) == 2
    assert _previews(store)[-1].payload["preview_digest"] != _previews(store)[-2].payload[
        "preview_digest"
    ]


def test_evidence_change_does_not_reuse_old_preview(tmp_path):
    executor, store, _contract_path, candidate, _expected = _host(tmp_path)
    _emit_preview(executor, candidate)
    changed = quality_gate_payload(
        candidate,
        "sha256:" + hashlib.sha256(_contract_path.read_bytes()).hexdigest(),
        summary={"result": "changed"},
    )
    changed["command_echo"] = ["quality-v2"]
    store.append("RUN", "v0.8", "local_gate.passed", changed)

    _emit_preview(executor, candidate)

    assert len(_previews(store)) == 2
    assert _previews(store)[-1].payload["evidence_digests"] != _previews(store)[-2].payload[
        "evidence_digests"
    ]


def test_plan_change_does_not_reuse_old_preview(tmp_path):
    executor, store, contract_path, candidate, _expected = _host(tmp_path)
    _emit_preview(executor, candidate)
    contract_path.write_text(
        contract_path.read_text(encoding="utf-8").replace(
            "steps = ['tag:{feature_tag}']",
            "steps = ['tag:{feature_tag}', 'tag:v9.9']",
        ),
        encoding="utf-8",
    )

    _emit_preview(executor, candidate)

    assert len(_previews(store)) == 2
    assert _previews(store)[-1].payload["operation_plan"] != _previews(store)[-2].payload[
        "operation_plan"
    ]


def test_declared_missing_artifact_does_not_emit_preview(tmp_path):
    executor, store, contract_path, candidate, _expected = _host(tmp_path)
    contract_path.write_text(
        contract_path.read_text(encoding="utf-8")
        + "\n[host-contract.build]\nartifact = 'dist/missing.whl'\n",
        encoding="utf-8",
    )

    _emit_preview(executor, candidate)

    assert _previews(store) == []


def test_runtime_default_contract_preview_reads_authorized(tmp_path):
    """An undeclared host's preview is built from the runtime-materialized
    default contract; read-time authorization must resolve that same source
    (not read the preview back as stale)."""
    repo = git_repo(tmp_path, gitignore=True)
    (repo / ".tracks" / "projects").mkdir(parents=True, exist_ok=True)
    candidate = git_strip(repo, "rev-parse", "HEAD")
    store = Store(repo / ".tracks")
    store.append(
        "RUN",
        "v0.8",
        "candidate.frozen",
        {
            "candidate_sha": candidate,
            "clean_tree": True,
            "branch": "main",
            "frozen_at_seq": 1,
        },
    )
    for event_type, event_payload in _release_evidence(candidate):
        store.append("RUN", "v0.8", event_type, event_payload)
    executor = Executor(store, repo, "RUN")
    _emit_preview(executor, candidate)

    previews = _previews(store)
    assert len(previews) == 1
    authorization = assess_release(
        repo,
        paths.tracks_home(repo),
        list(store.events("RUN")),
        previews[0],
        "v0.8",
    )

    assert authorization.stale_reason is None
    assert authorization.preview_stale is False
    assert (
        repo / ".tracks" / "runtime" / "materialized-host-contract.toml"
    ).is_file()
    store.close()


def test_latest_failed_gate_revokes_previous_pass_evidence(tmp_path):
    executor, store, contract_path, candidate, _expected = _host(tmp_path)
    _emit_preview(executor, candidate)
    contract_digest = "sha256:" + hashlib.sha256(contract_path.read_bytes()).hexdigest()
    failed_gate = quality_gate_payload(
        candidate, contract_digest, status="failed", summary={"result": "failed"}
    )
    store.append("RUN", "v0.8", "local_gate.failed", failed_gate)

    _emit_preview(executor, candidate)

    assert len(_previews(store)) == 2
    assert "quality[0]" not in _previews(store)[-1].payload["evidence_digests"]


def test_latest_noneligible_full_evidence_revokes_previous_pass(tmp_path):
    executor, store, _contract_path, candidate, _expected = _host(tmp_path)
    store.append("RUN", "v0.8", "full.executed", _full_evidence(candidate, eligible=True))
    _emit_preview(executor, candidate)
    store.append("RUN", "v0.8", "full.executed", _full_evidence(candidate, eligible=False))

    _emit_preview(executor, candidate)

    assert len(_previews(store)) == 1, (
        "missing full_f must invalidate the new preview, not land a partial set"
    )
    attention = [e for e in store.events("RUN") if e.type == "attention.required"]
    assert attention and attention[-1].payload["reason"] == (
        "evidence_incomplete_for_candidate"
    )


def test_full_failure_without_execution_commit_revokes_old_success(tmp_path):
    executor, store, _contract_path, candidate, _expected = _host(tmp_path)
    store.append("RUN", "v0.8", "full.executed", _full_evidence(candidate, eligible=True))
    _emit_preview(executor, candidate)
    failed = _full_evidence(candidate, eligible=False)
    failed.pop("execution_commit")
    failed["judgment_reason"] = "gate_failed"
    store.append("RUN", "v0.8", "full.executed", failed)

    _emit_preview(executor, candidate)

    assert len(_previews(store)) == 1, (
        "missing full_f must invalidate the new preview, not land a partial set"
    )
    attention = [e for e in store.events("RUN") if e.type == "attention.required"]
    assert attention and attention[-1].payload["reason"] == (
        "evidence_incomplete_for_candidate"
    )


def test_full_success_with_mismatched_execution_commit_is_rejected(tmp_path):
    executor, store, _contract_path, candidate, _expected = _host(tmp_path)
    store.append("RUN", "v0.8", "full.executed", _full_evidence(candidate, eligible=True))
    _emit_preview(executor, candidate)
    mismatched = _full_evidence(candidate, eligible=True)
    mismatched["execution_commit"] = "b" * 40
    store.append("RUN", "v0.8", "full.executed", mismatched)

    _emit_preview(executor, candidate)

    assert len(_previews(store)) == 1, (
        "missing full_f must invalidate the new preview, not land a partial set"
    )
    attention = [e for e in store.events("RUN") if e.type == "attention.required"]
    assert attention and attention[-1].payload["reason"] == (
        "evidence_incomplete_for_candidate"
    )


def test_stale_barrier_allows_recovery_after_fresh_evidence(tmp_path):
    executor, store, contract_path, candidate, _expected = _host(tmp_path)
    _emit_preview(executor, candidate)
    store.append(
        "RUN",
        "v0.8",
        "evidence.staled",
        {"candidate_sha": candidate, "reason": "human_return"},
    )
    contract_digest = "sha256:" + hashlib.sha256(contract_path.read_bytes()).hexdigest()
    fresh_gate = quality_gate_payload(
        candidate, contract_digest, summary={"result": "fresh"}
    )
    fresh_gate["command_echo"] = ["quality-retry"]
    store.append("RUN", "v0.8", "local_gate.passed", fresh_gate)
    for event_type, event_payload in _release_evidence(candidate):
        store.append("RUN", "v0.8", event_type, event_payload)

    _emit_preview(executor, candidate)

    assert len(_previews(store)) == 2
    assert "quality[0]" in _previews(store)[-1].payload["evidence_digests"]


def test_explicit_unknown_journey_fails_closed(tmp_path):
    executor, store, _contract_path, candidate, _expected = _host(tmp_path)
    executor._release_preview(
        Command(
            "generate_preview",
            params={"journey": "not-declared"},
            command_id="CMD-PREVIEW",
        ),
        candidate,
    )

    assert _previews(store) == []
