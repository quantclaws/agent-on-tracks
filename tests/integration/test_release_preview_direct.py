"""Direct M-RELEASE preview acceptance at the Store/Executor boundary.

The temporary host, contract, candidate, bare origin, and upstream verification
events are real Arrange facts.  The result under test is produced by the real
``generate_preview`` command; preview, blob, Human decision, and tag publish
results are never seeded.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import tomllib

from tracks import paths
from tracks.effects.publish import read_remote_state
from tracks.executor.executor import Executor
from tracks.executor.host_contract import load_host_contract, validate_host_contract
from tracks.executor.m_verify import freeze_candidate
from tracks.executor.release_gate import build_operation_plan
from tracks.kernel.events import Command
from tracks.store import Store

pytestmark = pytest.mark.integration


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _write_contract(repo: Path, operation_steps: list[str]) -> Path:
    contract = repo / ".tracks" / "projects" / "project.toml"
    contract.parent.mkdir(parents=True, exist_ok=True)
    rendered = ",\n  ".join(json.dumps(step) for step in operation_steps)
    contract.write_text(
        "\n".join(
            [
                "[host-contract]",
                "version = 1",
                'language = "python"',
                'toolchain = "cpython"',
                'install = "python"',
                "",
                "[host-contract.version_scheme]",
                'feature_tag = "v{minor}.0"',
                'patch_line = "v{minor}.{n}"',
                'prerelease_tag = "v{minor}.0-rc{n}"',
                "",
                "[host-contract.build]",
                'artifact = "dist/package.whl"',
                "",
                "[[host-contract.local_gate]]",
                'kind = "quality"',
                'source = "command"',
                'command = "true"',
                'categories = ["quality"]',
                'result_channel = "exit_code"',
                "timeout_seconds = 30",
                "",
                "[[host-contract.local_gate]]",
                'kind = "trace"',
                'source = "command"',
                'command = "true"',
                'categories = ["trace"]',
                'result_channel = "exit_code"',
                "timeout_seconds = 30",
                "",
                "[host-contract.ci]",
                'repo_env = "TRAC_GITHUB_REPO"',
                'workflow = "123"',
                'required_checks = ["required-ci"]',
                "",
                "[host-contract.operations.feature]",
                f"steps = [\n  {rendered}\n]",
                "requires = [\"local_gates\", \"ci\", \"security\"]",
                "",
            ]
        ),
        encoding="utf-8",
    )
    loaded = load_host_contract(contract)
    assert loaded.contract_version == 1
    assert validate_host_contract(loaded, repo) == ()
    return contract


def _append_upstream_premises(
    store: Store,
    run_id: str,
    version: str,
    candidate: str,
    branch: str,
    contract_digest: str,
    artifact_digest: str,
) -> None:
    """Record already accepted upstream evidence; none is the target result."""
    store.append(run_id, version, "stage.entered", {"stage": "M-VERIFY"})
    store.append(
        run_id,
        version,
        "candidate.frozen",
        {
            "candidate_sha": candidate,
            "clean_tree": True,
            "branch": branch,
            "frozen_at_seq": len(list(store.events(run_id))) + 1,
        },
    )
    for ordinal, kind in enumerate(("quality", "trace")):
        normalized = {
            "schema": "tracks-gate-result",
            "version": 1,
            "status": "passed",
            "exit_code": 0,
            "summary": {"source": "accepted upstream premise"},
            "gate_id": kind,
        }
        store.append(
            run_id,
            version,
            "local_gate.passed",
            {
                "kind": kind,
                "gate_identity": f"{kind}[{ordinal}]",
                "candidate_sha": candidate,
                "contract_digest": contract_digest,
                "command_echo": ["true"],
                "normalized_result": dict(normalized),
                "status": "passed",
                "exit_code": 0,
                "summary": dict(normalized["summary"]),
            },
        )
    store.append(
        run_id,
        version,
        "artifact.built",
        {
            "candidate_sha": candidate,
            "artifact": "dist/package.whl",
            "artifact_digest": artifact_digest,
            "status": "passed",
        },
    )
    store.append(
        run_id,
        version,
        "ci.run_observed",
        {
            "status": "passed",
            "repo": "local/preview-host",
            "workflow": "123",
            "run_id": 17,
            "head_sha": candidate,
            "candidate_sha": candidate,
            "conclusion": "success",
            "required_checks": ["required-ci"],
            "api_verified": True,
        },
    )
    store.append(
        run_id,
        version,
        "prism.verdict",
        {
            "verdict": "pass",
            "scope": "verify_final",
            "candidate_sha": candidate,
            "evidence_digests": {"artifact": artifact_digest},
        },
    )
    store.append(
        run_id,
        version,
        "security.assessed",
        {
            "status": "passed",
            "policy_digest": contract_digest,
            "candidate_sha": candidate,
            "scans": [{"id": "accepted-upstream", "status": "passed"}],
            "prism_scope": "security",
        },
    )
    store.append(run_id, version, "stage.exited", {"stage": "M-VERIFY"})
    store.append(run_id, version, "stage.entered", {"stage": "M-SECURITY"})
    store.append(run_id, version, "stage.exited", {"stage": "M-SECURITY"})
    store.append(run_id, version, "stage.entered", {"stage": "M-RELEASE"})


def _prepare(host_repo: Path, trac, tmp_path: Path, operation_steps: list[str] | None = None):
    assert trac("init").returncode == 0
    assert trac("start", "v0.8", stdin="M-RELEASE preview acceptance").returncode == 0
    steps = operation_steps or ["tag:{feature_tag}"]
    contract = _write_contract(host_repo, steps)
    artifact = host_repo / "dist" / "package.whl"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"accepted preview artifact\n")
    subprocess.run(
        ["git", "add", "-f", ".tracks/projects/project.toml", "dist/package.whl"],
        cwd=host_repo,
        check=True,
        capture_output=True,
    )
    _git(host_repo, "commit", "-qm", "materialize preview contract and artifact")
    candidate = _git(host_repo, "rev-parse", "HEAD")
    identity = freeze_candidate(host_repo)
    assert identity.candidate_sha == candidate
    assert identity.clean_tree is True and identity.branch

    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, capture_output=True)
    _git(host_repo, "remote", "add", "origin", str(remote))
    _git(host_repo, "push", "-q", "origin", "HEAD:refs/heads/main")

    artifact_digest = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
    contract_digest = hashlib.sha256(contract.read_bytes()).hexdigest()
    store = Store(paths.tracks_home(host_repo))
    try:
        run_id = store.active_run()
        assert run_id
        version = store.state(run_id).version or "v0.8"
        _append_upstream_premises(
            store,
            run_id,
            version,
            candidate,
            identity.branch,
            contract_digest,
            artifact_digest,
        )
    finally:
        store.close()
    return run_id, candidate, contract, remote, artifact_digest, contract_digest


def _events(repo: Path, run_id: str) -> list:
    store = Store(paths.tracks_home(repo))
    try:
        return list(store.events(run_id))
    finally:
        store.close()


def _issue_preview(repo: Path, run_id: str, candidate: str) -> None:
    store = Store(paths.tracks_home(repo))
    try:
        Executor(store, repo, run_id).issue(
            Command(kind="generate_preview", params={"candidate_sha": candidate})
        )
    finally:
        store.close()


def _reconcile_preview(repo: Path, run_id: str) -> None:
    store = Store(paths.tracks_home(repo))
    try:
        issued = [
            event
            for event in store.events(run_id)
            if event.type == "command.issued"
            and (event.payload or {}).get("command", {}).get("kind") == "generate_preview"
        ][-1]
        raw = issued.payload["command"]
        Executor(store, repo, run_id)._execute(
            Command(
                kind=raw["kind"],
                params=raw.get("params", {}),
                command_id=raw.get("command_id"),
            ),
            store.state(run_id),
            None,
            reconcile=True,
        )
    finally:
        store.close()


def _previews(events: list) -> list:
    return [event for event in events if event.type == "release.previewed"]


def _expected_plan(contract: Path) -> dict:
    raw = tomllib.loads(contract.read_text(encoding="utf-8"))
    return build_operation_plan(
        raw["host-contract"],
        "feature",
        {
            "version": "v0.8",
            "major": "0",
            "minor": "0.8",
            "n": "0",
            "ulid": "01HLOCALPREVIEW",
            "artifact": "dist/package.whl",
            "feature_tag": "v0.8.0",
        },
    )


# AC-FR0273-01: M-RELEASE aggregates a real candidate and contract-backed
# evidence into a complete, content-addressed preview.
def test_real_preview_producer_binds_contract_evidence_and_blob(host_repo, trac, tmp_path):
    run_id, candidate, contract, _remote, artifact_digest, contract_digest = _prepare(
        host_repo, trac, tmp_path
    )
    _issue_preview(host_repo, run_id, candidate)
    events = _events(host_repo, run_id)
    previews = _previews(events)
    assert len(previews) == 1
    payload = previews[0].payload
    assert payload["candidate_sha"] == candidate
    assert payload["artifact_digest"] == artifact_digest
    assert payload["contract_policy_digest"] == "sha256:" + contract_digest
    assert payload["evidence_digests"]
    assert payload["operation_plan"] == _expected_plan(contract)
    expected_plan_digest = "sha256:" + hashlib.sha256(
        json.dumps(payload["operation_plan"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert payload["operation_plan_digest"] == expected_plan_digest
    assert isinstance(payload.get("blob_ref"), str) and payload["blob_ref"]
    blob_hash = Path(payload["blob_ref"]).name
    store = Store(paths.tracks_home(host_repo))
    try:
        blob = store.load_payload({"$ref": blob_hash})
    finally:
        store.close()
    assert blob == {key: value for key, value in payload.items() if key != "blob_ref"}


# AC-FR0273-01/IF-RELEASE-002: the same command and inputs reconcile without
# appending a second preview or changing its content address.
def test_same_preview_command_is_idempotent(host_repo, trac, tmp_path):
    run_id, candidate, _contract, _remote, _artifact_digest, _contract_digest = _prepare(
        host_repo, trac, tmp_path
    )
    _issue_preview(host_repo, run_id, candidate)
    first = _previews(_events(host_repo, run_id))[0].payload
    _reconcile_preview(host_repo, run_id)
    previews = _previews(_events(host_repo, run_id))
    assert len(previews) == 1
    assert previews[0].payload["preview_digest"] == first["preview_digest"]
    assert previews[0].payload.get("blob_ref") == first.get("blob_ref")


@pytest.mark.parametrize("mutation", ["evidence", "contract"])
# AC-FR0273-02: changed evidence content or a new clean candidate produces a
# new preview while retaining the old append-only preview.
def test_changed_evidence_or_contract_requires_new_preview(
    host_repo, trac, tmp_path, mutation
):
    run_id, candidate, contract, remote, artifact_digest, contract_digest = _prepare(
        host_repo, trac, tmp_path
    )
    _issue_preview(host_repo, run_id, candidate)
    old = _previews(_events(host_repo, run_id))[0].payload
    if mutation == "evidence":
        store = Store(paths.tracks_home(host_repo))
        try:
            store.append(
                run_id,
                store.state(run_id).version or "v0.8",
                "local_gate.passed",
                {
                    "kind": "trace",
                    "gate_identity": "trace[1]",
                    "candidate_sha": candidate,
                    "contract_digest": contract_digest,
                    "command_echo": ["true", "changed-input"],
                    "normalized_result": {
                        "schema": "tracks-gate-result",
                        "version": 1,
                        "status": "passed",
                        "exit_code": 0,
                        "summary": {"source": "changed upstream evidence"},
                        "gate_id": "trace",
                    },
                },
            )
        finally:
            store.close()
        _issue_preview(host_repo, run_id, candidate)
    else:
        contract.write_text(
            contract.read_text(encoding="utf-8").replace('feature_tag = "v{minor}.0"', 'feature_tag = "v{minor}.1"'),
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "-f", str(contract.relative_to(host_repo))], cwd=host_repo, check=True)
        _git(host_repo, "commit", "-qm", "change release operation contract")
        fresh_candidate = _git(host_repo, "rev-parse", "HEAD")
        identity = freeze_candidate(host_repo)
        assert identity.candidate_sha == fresh_candidate and identity.clean_tree
        _git(host_repo, "push", "-q", "origin", "HEAD:refs/heads/main")
        fresh_contract_digest = hashlib.sha256(contract.read_bytes()).hexdigest()
        store = Store(paths.tracks_home(host_repo))
        try:
            _append_upstream_premises(
                store,
                run_id,
                store.state(run_id).version or "v0.8",
                fresh_candidate,
                identity.branch,
                fresh_contract_digest,
                artifact_digest,
            )
        finally:
            store.close()
        _issue_preview(host_repo, run_id, fresh_candidate)
    previews = _previews(_events(host_repo, run_id))
    assert len(previews) == 2
    assert previews[0].payload["preview_digest"] == old["preview_digest"]
    assert previews[1].payload["preview_digest"] != old["preview_digest"]
    if mutation == "contract":
        assert previews[1].payload["candidate_sha"] != candidate
        assert read_remote_state(str(remote), "branch", "main")["object_id"] == previews[1].payload[
            "candidate_sha"
        ]


# AC-FR0274-01/AC-FR0275-01: the real Human CLI decision authorizes the
# already accepted single-tag Runtime consumer on a local bare origin.
def test_cli_release_approval_reaches_real_tag_consumer(host_repo, trac, tmp_path, monkeypatch):
    run_id, candidate, _contract, remote, _artifact_digest, _contract_digest = _prepare(
        host_repo, trac, tmp_path
    )
    _issue_preview(host_repo, run_id, candidate)
    preview = _previews(_events(host_repo, run_id))[0].payload
    decision = trac("release", "--action", "release")
    assert decision.returncode == 0, decision.stderr
    decided = [event for event in _events(host_repo, run_id) if event.type == "release.decided"]
    assert decided and decided[-1].payload["actor"] == "human"
    assert decided[-1].payload["candidate_sha"] == candidate
    assert decided[-1].payload["preview_digest"] == preview["preview_digest"]

    monkeypatch.chdir(host_repo)
    store = Store(paths.tracks_home(host_repo))
    try:
        Executor(store, host_repo, run_id).issue(
            Command(
                kind="execute_publish",
                params={"preview_digest": preview["preview_digest"]},
            )
        )
    finally:
        store.close()
    events = _events(host_repo, run_id)
    executed = [event for event in events if event.type == "publish.executed"]
    assert executed and executed[-1].payload["status"] == "done"
    assert executed[-1].payload["candidate_sha"] == candidate
    remote_state = read_remote_state(str(remote), "tag", "v0.8.0", expected_object=candidate)
    assert remote_state["exists"] is True
    assert remote_state["object_id"] == candidate
