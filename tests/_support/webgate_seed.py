"""Shared seeding for the IF-WEBGATE-001 accept-face integration anchors.

``CommandService.accept`` binds a web human decision to the object it was made
against at acceptance time (interfaces §1b.1, IF-WEBGATE-001): an approval
binds the revision the reviewer actually saw (FR-0308), a release decision
binds the live preview digest (FR-0309), and a controlled retry only accepts
from the legal v0.8 repair/escape position (FR-0311). Each fixture below
arranges the documented storage contract for real — the service-plane
``projects`` row (interfaces §1c table 2), the run's tracks.db through the
real ``Store``, and the material the binding reads — so the frozen
integration anchors exercise the genuine gate instead of an unseeded
``not_found``.

The release fixture follows the proven candidate-bound authorization harness
(contract + frozen candidate + accepted upstream premises + bound preview
blob); nothing here mocks the system under test.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

from tests._support.release_premises import append_verify_release_premises
from tracks.baseline import revision_digest
from tracks.executor.host_contract import load_host_contract
from tracks.executor.release_preview import assemble_preview
from tracks.executor.security import security_policy_digest
from tracks.paths import tracks_home
from tracks.store import Store
from tracks.supervisor import db as sdb
from tracks.supervisor.service import CommandService

_VERSION = "v0.9"
_RELEASE_VERSION = "v0.8"  # the release fixture rides the proven v0.8 harness facts
_ACTOR = "local-user"
_TS = "2026-09-21T00:00:00+00:00"

# Host contract paired with the accepted upstream premises (workflow id and
# required checks match the ``ci.run_observed`` / gate payloads they audit).
_CONTRACT_TOML = """[host-contract]
version = 1
language = "python"
toolchain = "cpython"
install = "python"

[host-contract.version_scheme]
feature_tag = "v{minor}.0"
patch_line = "v{minor}.{n}"
prerelease_tag = "v{minor}.0-rc{n}"

[host-contract.build]
artifact = "dist/package.whl"

[[host-contract.local_gate]]
kind = "quality"
source = "command"
command = "true"
categories = ["quality"]
result_channel = "exit_code"
timeout_seconds = 30

[[host-contract.local_gate]]
kind = "trace"
source = "command"
command = "true"
categories = ["trace"]
result_channel = "exit_code"
timeout_seconds = 30

[[host-contract.security_scan]]
id = "accepted-security-scan"
tool = "fixture-security-tool"
tool_version = "1.0"
install = ""
command = "true"
result_channel = "exit_code"
threshold = "0"
timeout_seconds = 30

[host-contract.ci]
repo_env = "TRAC_GITHUB_REPO"
workflow = "123"
required_checks = ["required-ci"]

[host-contract.operations.feature]
steps = [
  "tag:{feature_tag}"
]
requires = ["local_gates", "ci", "security"]
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _write_trio(repo: Path) -> Path:
    vdir = repo / ".tracks" / "projects" / _VERSION
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "story.md").write_text("story body\n", encoding="utf-8")
    (vdir / "spec.md").write_text("spec body\n", encoding="utf-8")
    (vdir / "acceptance.md").write_text("acceptance body\n", encoding="utf-8")
    return vdir


def _register_project(home: Path, repo: Path, version: str) -> None:
    """Persist the §1c table 2 row the gate resolves the run through."""
    conn = sqlite3.connect(str(home / "service.db"))
    try:
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            ("proj-1", str(repo), version, _ACTOR, _TS),
        )
        conn.commit()
    finally:
        conn.close()


def _service_home(tmp_path: Path, repo: Path, version: str) -> Path:
    home = tmp_path / "service"
    sdb.ServiceDB(home)
    _register_project(home, repo, version)
    return home


def command_service(home: Path) -> CommandService:
    """The composition-root shape: the home-backed store is injected."""
    return CommandService(home, sdb.ServiceDB(home), SimpleNamespace())


def seed_approval_run(tmp_path: Path, run_id: str) -> tuple[Path, Path, str]:
    """A run at the M-REQ-APPROVAL gate whose current revision is the trio.

    Returns ``(home, repo, current_revision)``; the approval anchor submits
    ``current_revision`` and the stale anchor submits a superseded one.
    """
    repo = tmp_path / "repo"
    vdir = _write_trio(repo)
    current = revision_digest(vdir)
    store = Store(tracks_home(repo))
    try:
        store.append(run_id, _VERSION, "stage.entered", {"stage": "M-REQ-APPROVAL"})
        store.append(run_id, _VERSION, "preview.generated", {"digest": current})
    finally:
        store.close()
    return _service_home(tmp_path, repo, _VERSION), repo, current


def supersede_trio(repo: Path) -> str:
    """Move the material to a new revision; returns the new digest.

    Models the FR-0308/FR-0294 construction: the reviewer signed revision N,
    then the document was edited to revision N+1.
    """
    vdir = repo / ".tracks" / "projects" / _VERSION
    with (vdir / "spec.md").open("a", encoding="utf-8") as handle:
        handle.write("superseded clause\n")
    return revision_digest(vdir)


def seed_release_run(tmp_path: Path, run_id: str, *, stale: bool = False) -> tuple[Path, Path, str]:
    """A run at M-RELEASE with a candidate-bound fresh (or staled) preview.

    Returns ``(home, repo, preview_digest)``; the release anchor submits the
    bound digest, the stale anchor the same digest after an
    ``evidence.staled`` marker.
    """
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("readme\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    contract = repo / ".tracks" / "projects" / "project.toml"
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text(_CONTRACT_TOML, encoding="utf-8")
    artifact = repo / "dist" / "package.whl"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"accepted preview artifact\n")
    _git(repo, "add", "-f", ".tracks/projects/project.toml", "dist/package.whl")
    _git(repo, "commit", "-m", "materialize preview contract and artifact")
    candidate = _git(repo, "rev-parse", "HEAD")

    contract_digest = hashlib.sha256(contract.read_bytes()).hexdigest()
    artifact_digest = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
    store = Store(tracks_home(repo))
    try:
        store.append(run_id, _RELEASE_VERSION, "story.requested", {"raw_chars": 1})
        store.append(run_id, _RELEASE_VERSION, "stage.entered", {"stage": "M-VERIFY"})
        store.append(
            run_id,
            _RELEASE_VERSION,
            "candidate.frozen",
            {
                "candidate_sha": candidate,
                "clean_tree": True,
                "branch": "main",
                "frozen_at_seq": 3,
            },
        )
        policy_digest = security_policy_digest(load_host_contract(contract))
        append_verify_release_premises(
            store, run_id, _RELEASE_VERSION, candidate, "main",
            contract_digest, artifact_digest, policy_digest,
        )
        facts = {"version": _RELEASE_VERSION, "major": "0", "minor": "0.8"}
        preview = assemble_preview(
            repo,
            load_host_contract(contract),
            candidate,
            "sha256:" + contract_digest,
            facts,
            list(store.events(run_id)),
            journey="feature",
        )
        blob = store.write_audit_blob(preview)
        stored = dict(preview)
        stored["blob_ref"] = f".tracks/runtime/blobs/{blob}"
        store.append(run_id, _RELEASE_VERSION, "release.previewed", stored)
        if stale:
            store.append(
                run_id, _RELEASE_VERSION, "evidence.staled", {"candidate_sha": candidate}
            )
    finally:
        store.close()
    return _service_home(tmp_path, repo, _RELEASE_VERSION), repo, preview["preview_digest"]


def seed_retry_run(tmp_path: Path, run_id: str, *, legal: bool = True) -> tuple[Path, Path]:
    """A run at the legal retry position (escalation) or an illegal one.

    Returns ``(home, repo)``. ``legal=False`` models a failed verification
    that never reached the escalation gate, so the controlled retry is
    rejected with the concrete reason (FR-0311).
    """
    repo = tmp_path / "repo"
    store = Store(tracks_home(repo))
    try:
        store.append(run_id, _VERSION, "stage.entered", {"stage": "M-IMPL"})
        if legal:
            store.append(
                run_id,
                _VERSION,
                "run.breaker_tripped",
                {"condition": "attempts exhausted", "report": {"failures": 3}},
            )
        else:
            store.append(
                run_id,
                _VERSION,
                "verdict.failed",
                {"check": "red_classifier", "ac_refs": ["AC-FR0311-02"], "attempt": 1},
            )
    finally:
        store.close()
    return _service_home(tmp_path, repo, _VERSION), repo


def accepted_commands(home: Path) -> list[dict]:
    """The accepted command rows (SM-01.5: a rejected request leaves none)."""
    conn = sqlite3.connect(f"file:{home / 'service.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM commands WHERE status = 'accepted'").fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def rejected_reasons(home: Path) -> list[str]:
    """The audited ``command.rejected`` reasons (§1a #8)."""
    conn = sqlite3.connect(f"file:{home / 'service.db'}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT payload FROM service_events WHERE type = 'command.rejected' ORDER BY seq"
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(row[0]).get("reason") for row in rows]
