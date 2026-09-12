"""RED coverage for candidate-bound CLI release authorization."""

from __future__ import annotations

import hashlib
from pathlib import Path

from tests.unit.helpers import git_repo, git_strip
from tracks.executor.host_contract import (
    GATE_RESULT_PROTOCOL,
    GATE_RESULT_VERSION,
    load_host_contract,
    validate_host_contract,
)
from tracks.executor.release_preview import assemble_preview
from tracks.store import Store

_CI = {
    "status": "passed",
    "repo": "acme/host",
    "workflow": "ci.yml",
    "run_id": 17,
    "head_sha": "",
    "candidate_sha": "",
    "conclusion": "success",
    "required_checks": ["quality"],
    "api_verified": True,
}


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _raw_digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _gate(candidate: str, contract_digest: str, *, status: str = "passed") -> dict:
    return {
        "kind": "quality",
        "gate_identity": "quality[0]",
        "candidate_sha": candidate,
        "contract_digest": contract_digest,
        "command_echo": ["quality"],
        "normalized_result": {
            "schema": GATE_RESULT_PROTOCOL,
            "version": GATE_RESULT_VERSION,
            "status": status,
            "exit_code": 0 if status == "passed" else 7,
            "summary": {"status": status},
            "gate_id": "quality",
        },
        "reason": None if status == "passed" else "gate_failed",
    }


def _host(tmp_path: Path):
    repo = git_repo(tmp_path, gitignore=True)
    project = repo / ".tracks" / "projects"
    project.mkdir(parents=True, exist_ok=True)
    contract_path = project / "project.toml"
    contract_path.write_text(
        "[host-contract]\n"
        "version = 1\n"
        "language = 'python'\n"
        "toolchain = 'cpython'\n"
        "install = ''\n"
        "\n[host-contract.version_scheme]\n"
        "feature_tag = 'v{minor}.0'\n"
        "patch_line = 'v{minor}.{n}'\n"
        "prerelease_tag = 'v{minor}.{n}-pre.{ulid}'\n"
        "\n[[host-contract.local_gate]]\n"
        "kind = 'quality'\n"
        "source = 'command'\n"
        "command = 'quality'\n"
        "result_channel = 'exit_code'\n"
        "\n[host-contract.ci]\n"
        "repo_env = 'CI_REPO'\n"
        "workflow = 'ci.yml'\n"
        "required_checks = ['quality']\n"
        "\n[host-contract.operations.feature]\n"
        "steps = ['tag:{feature_tag}']\n",
        encoding="utf-8",
    )
    git_strip(repo, "add", "-f", ".tracks/projects/project.toml")
    git_strip(repo, "commit", "-m", "authorization contract")
    candidate = git_strip(repo, "rev-parse", "HEAD")
    home = repo / ".tracks"
    store = Store(home)
    run_id = "RUN"
    store.append(run_id, "v0.8", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.8", "stage.entered", {"stage": "M-RELEASE"})
    contract_digest = _raw_digest(contract_path.read_bytes())
    store.append(
        run_id,
        "v0.8",
        "candidate.frozen",
        {
            "candidate_sha": candidate,
            "clean_tree": True,
            "branch": "main",
            "frozen_at_seq": 3,
        },
    )
    store.append(run_id, "v0.8", "local_gate.passed", _gate(candidate, contract_digest))
    ci = dict(_CI)
    ci.update({"head_sha": candidate, "candidate_sha": candidate})
    store.append(run_id, "v0.8", "ci.run_observed", ci)
    store.append(
        run_id,
        "v0.8",
        "prism.verdict",
        {
            "verdict": "pass",
            "scope": "verify_final",
            "candidate_sha": candidate,
        },
    )
    store.append(
        run_id, "v0.8", "prism.verdict",
        {"scope": "security", "verdict": "pass", "candidate_sha": candidate,
         "policy_digest": contract_digest}, command_id="security-review",
    )
    store.append(
        run_id,
        "v0.8",
        "security.assessed",
        {
            "status": "passed",
            "policy_digest": contract_digest,
            "candidate_sha": candidate,
            "scans": [{"id": "security", "status": "passed", "exit_code": 0}],
            "prism_scope": "security",
            "review_command_id": "security-review",
        },
    )
    contract = load_host_contract(contract_path)
    assert validate_host_contract(contract, repo) == ()
    preview_policy_digest = _digest(contract_path.read_bytes())
    facts = {"version": "v0.8", "major": "0", "minor": "0.8"}
    preview = assemble_preview(
        repo,
        contract,
        candidate,
        preview_policy_digest,
        facts,
        list(store.events(run_id)),
        journey="feature",
    )
    assert preview is not None
    blob = store.write_audit_blob(preview)
    assert blob is not None
    preview = dict(preview)
    preview["blob_ref"] = f".tracks/runtime/blobs/{blob}"
    store.append(run_id, "v0.8", "release.previewed", preview)
    return repo, store, run_id, candidate, contract_digest, contract_path, preview


def _append_preview(store, repo, run_id, candidate, contract_digest):
    contract = load_host_contract(repo / ".tracks" / "projects" / "project.toml")
    preview_policy_digest = (
        contract_digest
        if contract_digest.startswith("sha256:")
        else "sha256:" + contract_digest
    )
    preview = assemble_preview(
        repo,
        contract,
        candidate,
        preview_policy_digest,
        {"version": "v0.8", "major": "0", "minor": "0.8"},
        list(store.events(run_id)),
        journey="feature",
    )
    assert preview is not None
    blob = store.write_audit_blob(preview)
    assert blob is not None
    preview = dict(preview)
    preview["blob_ref"] = f".tracks/runtime/blobs/{blob}"
    store.append(run_id, "v0.8", "release.previewed", preview)


def _new_candidate(repo: Path) -> str:
    marker = repo / "candidate-change.txt"
    marker.write_text("new candidate\n", encoding="utf-8")
    git_strip(repo, "add", "candidate-change.txt")
    git_strip(repo, "commit", "-m", "new candidate")
    return git_strip(repo, "rev-parse", "HEAD")


def _events(store, run_id, kind):
    return [event for event in store.events(run_id) if event.type == kind]


def test_current_candidate_full_authorization_can_release(tmp_path, capsys):
    from tracks.cli.main import cmd_release

    repo, store, run_id, _candidate, _digest, _path, _preview = _host(tmp_path)
    rc = cmd_release(repo, "--action", "release")
    capsys.readouterr()

    assert rc == 0
    assert len(_events(store, run_id, "release.decided")) == 1


def test_new_candidate_without_own_ci_cannot_reuse_old_ci(tmp_path, capsys):
    from tracks.cli.main import cmd_release

    repo, store, run_id, _candidate, digest, _path, _preview = _host(tmp_path)
    new_candidate = _new_candidate(repo)
    store.append(
        run_id,
        "v0.8",
        "candidate.frozen",
        {"candidate_sha": new_candidate, "clean_tree": True, "branch": "main"},
    )
    _append_preview(store, repo, run_id, new_candidate, digest)

    rc = cmd_release(repo, "--action", "release")
    capsys.readouterr()

    assert rc != 0
    assert not _events(store, run_id, "release.decided")


def test_old_foreign_failure_does_not_poison_current_candidate(tmp_path, capsys):
    from tracks.cli.main import cmd_release

    repo, store, run_id, candidate, digest, _path, _preview = _host(tmp_path)
    store.append(
        run_id,
        "v0.8",
        "local_gate.failed",
        _gate("c" * 40, digest, status="failed"),
    )

    rc = cmd_release(repo, "--action", "release")
    capsys.readouterr()

    assert rc == 0
    assert _events(store, run_id, "release.decided")[-1].payload["candidate_sha"] == candidate


def test_latest_ci_failure_blocks_release(tmp_path, capsys):
    from tracks.cli.main import cmd_release

    repo, store, run_id, candidate, digest, _path, _preview = _host(tmp_path)
    failed = dict(_CI)
    failed.update(
        {
            "status": "failed",
            "conclusion": "failure",
            "head_sha": candidate,
            "candidate_sha": candidate,
            "api_verified": True,
        }
    )
    store.append(run_id, "v0.8", "ci.run_observed", failed)

    rc = cmd_release(repo, "--action", "release")
    capsys.readouterr()

    assert rc != 0
    assert not _events(store, run_id, "release.decided")


def test_later_security_scope_does_not_replace_verify_final(tmp_path, capsys):
    from tracks.cli.main import cmd_release

    repo, store, run_id, candidate, digest, _path, _preview = _host(tmp_path)
    store.append(
        run_id,
        "v0.8",
        "prism.verdict",
        {
            "verdict": "pass",
            "scope": "security",
            "candidate_sha": candidate,
            "policy_digest": digest,
        },
        command_id="later-security-review",
    )
    assessment = dict(_events(store, run_id, "security.assessed")[-1].payload)
    assessment["review_command_id"] = "later-security-review"
    store.append(run_id, "v0.8", "security.assessed", assessment)
    _append_preview(store, repo, run_id, candidate, digest)

    rc = cmd_release(repo, "--action", "release")
    capsys.readouterr()

    assert rc == 0
    assert len(_events(store, run_id, "release.decided")) == 1


def test_all_actions_reject_stale_preview(tmp_path, capsys):
    from tracks.cli.main import cmd_release

    repo, store, run_id, candidate, _digest, _path, _preview = _host(tmp_path)
    store.append(
        run_id,
        "v0.8",
        "evidence.staled",
        {"candidate_sha": candidate, "reason": "human_return"},
    )

    assert cmd_release(repo, "--action", "release") != 0
    capsys.readouterr()
    assert cmd_release(repo, "--action", "delay", "--reason", "later") != 0
    _out, err = capsys.readouterr()
    assert "run trac release preview" in err
    assert (
        cmd_release(repo, "--action", "return", "--to", "M-DESIGN", "--reason", "back")
        != 0
    )
    _out, err = capsys.readouterr()
    assert "run trac release preview" in err

    assert not _events(store, run_id, "release.decided")
    assert len(_events(store, run_id, "release.rejected")) == 3
