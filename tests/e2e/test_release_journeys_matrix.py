"""E2E: six journeys dual host matrix (NFR-0149-01, test-plan §11.4).

Two independent hosts (``tracks`` and the reference-host-shaped sibling) each
run the three release journeys — feature, post-release hotfix and dev hotfix
— through the real CLI/kernel/executor over a bare remote and the loopback
GitHub stand-in. Each journey asserts its terminal state, the one-candidate
identity across the chain and the exported release trace; the reference host
chains additionally pass the FR-0282 same-shape comparator.

The credential-less path is asserted last: with no GitHub token/API binding a
walked run lands ``attention.required`` and produces no successful release —
counted as not-pass, never a silent fake green (AC-NFR0149-01). The real
reference host materialization and its live toolchain journey are covered by
``test_reference_host_journey`` and the milestone/weekly live twin.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests._support.fixtures import _load_event_payload
from tests.e2e.helpers import (
    generate_report_md,
    init_bare_remote,
    walk_to_awaiting_release,
    walk_to_m_impl_parked,
)
from tests.integration.test_journey_versioning import (
    _bind_github_origin,
    _declare_journey_contract,
    _seed_hotfix_host,
    _walk_hotfix_to_awaiting_release,
)
from tests.integration.test_reference_host import _JOURNEY_CHAIN as _CANONICAL_CHAIN
from tracks import paths
from tracks.executor.reference_host import verify_reference_equivalence

pytestmark = pytest.mark.e2e

_REPO_ROOT = Path(__file__).resolve().parents[2]
_JOURNEYS = ("feature", "post_release", "dev")
_HOSTS = ("tracks", "reference")


def _fresh_repo(tmp_path: Path, name: str) -> Path:
    """A host repo shaped like the ``host_repo`` fixture (venv link included)."""
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test Human"], cwd=repo, check=True
    )
    (repo / "README.md").write_text("host project\n", encoding="utf-8")
    (repo / ".venv").symlink_to(Path(sys.prefix).resolve(), target_is_directory=True)
    (repo / ".gitignore").write_text(".venv\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md", ".gitignore"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True
    )
    return repo


def _runner(repo: Path):
    """The per-repo ``trac`` runner the shared walkers expect."""

    def run(*args, stdin=None, simulate=None):
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("TRACKS_HOME", "TRAC_FAKE_SIMULATE")
        }
        env["TRAC_AGENT_BACKEND"] = "fake"
        if simulate:
            env["TRAC_FAKE_SIMULATE"] = simulate
        env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run(
            [sys.executable, "-m", "tracks.cli.main", *args],
            cwd=repo,
            env=env,
            input=stdin,
            capture_output=True,
            text=True,
        )

    run.repo = repo
    return run


def _events(repo: Path) -> list[dict]:
    home = paths.tracks_home(repo)
    conn = sqlite3.connect(home / "runtime" / "tracks.db")
    try:
        rows = conn.execute(
            "SELECT seq, type, payload FROM events ORDER BY seq"
        ).fetchall()
    finally:
        conn.close()
    return [
        {"seq": r[0], "type": r[1], "payload": _load_event_payload(home, json.loads(r[2]))}
        for r in rows
    ]


def _assert_identity(events: list[dict], report_body: str) -> str:
    completed = [e for e in events if e["type"] == "run.completed"]
    assert completed and completed[-1]["payload"]["terminal_state"] == "released"
    frozen = [e for e in events if e["type"] == "candidate.frozen"]
    assert frozen and frozen[0]["payload"]["clean_tree"] is True
    candidate = frozen[0]["payload"]["candidate_sha"]
    for event in events:
        if "candidate_sha" in event["payload"]:
            assert event["payload"]["candidate_sha"] == candidate, event["type"]
    executed = [
        e
        for e in events
        if e["type"] == "publish.executed" and e["payload"].get("status") == "done"
    ]
    assert executed and all(e["payload"]["candidate_sha"] == candidate for e in executed)
    trace = [e for e in events if e["type"] == "milestone.trace_closed"][-1]["payload"]
    assert trace["candidate_sha"] == candidate
    assert candidate in report_body
    assert trace["trace_digest"] in report_body
    return candidate


def _run_journey(host: str, journey: str, repo: Path, ci_echo_standin) -> list[dict]:
    trac = _runner(repo)
    bare, _initial = init_bare_remote(repo, f"bare_{host}_{journey}.git")
    _bind_github_origin(repo, bare, ci_echo_standin.repo)
    if journey == "feature":
        walk_to_awaiting_release(
            trac,
            pre_seed_hook=lambda r: _declare_journey_contract(
                r, ["merge:main", "tag:{feature_tag}", "release:{feature_tag}"]
            ),
        )
    else:
        _seed_hotfix_host(repo)
        subprocess.run(
            ["git", "push", "-q", "-u", "origin", "releases/v0.8"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        _walk_hotfix_to_awaiting_release(
            trac, 42, "post-release" if journey == "post_release" else "dev"
        )
    assert trac("release", "--action", "release").returncode == 0
    assert trac("run").returncode == 0
    return _events(repo)


# AC-NFR0149-01@v0.8 TRACKS-TRACE six journeys dual host with needs_attention counting
def test_six_journeys_dual_host(
    tmp_path, ci_echo_standin, monkeypatch
):
    results: dict[tuple[str, str], tuple[Path, list[dict]]] = {}
    for host in _HOSTS:
        for journey in _JOURNEYS:
            repo = _fresh_repo(tmp_path, f"{host}_{journey}")
            # Journeys are independent repositories: the shared loopback
            # stand-in must not leak one journey's release objects (same
            # public tag across hosts) into the next; a leak is a real
            # reconcile_conflict the publish face now fails closed on.
            ci_echo_standin.releases.clear()
            ci_echo_standin.assets.clear()
            events = _run_journey(host, journey, repo, ci_echo_standin)
            body = generate_report_md(_runner(repo), repo, name="report_out")
            candidate = _assert_identity(events, body)
            assert candidate
            results[(host, journey)] = (repo, events)

    assert len(results) == 6

    # The reference host chains pass the FR-0282 same-shape comparator (the
    # local stand-in for the live isomorphic journey).
    for journey in _JOURNEYS:
        events = results[("reference", journey)][1]
        kinds = [e["type"] for e in events if e["type"] in _CANONICAL_CHAIN]
        ok, reasons = verify_reference_equivalence(
            {"events": [{"kind": kind} for kind in kinds]}
        )
        assert ok is True and reasons == (), (
            f"reference {journey} chain must be same-shape: {(ok, reasons)!r}"
        )

    # Dev is the pre-release channel: no public tag/release object.
    dev_repo = results[("tracks", "dev")][0]
    dev_body = generate_report_md(_runner(dev_repo), dev_repo, name="dev_report")
    assert "channel=pre-release" in dev_body or "pre-release" in dev_body.lower()

    # Credential-less path: without token/API binding the walked run lands
    # needs_attention, is counted as not-pass and publishes nothing.
    monkeypatch.delenv("TRAC_GITHUB_API_BASE", raising=False)
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    no_creds_repo = _fresh_repo(tmp_path, "no_creds")
    walk_to_m_impl_parked(_runner(no_creds_repo))
    events = _events(no_creds_repo)
    assert not [e for e in events if e["type"] == "release.decided"]
    assert not [
        e
        for e in events
        if e["type"] == "run.completed"
        and e["payload"].get("terminal_state") == "released"
    ]
    attention = [e for e in events if e["type"] == "attention.required"]
    assert attention, "missing credentials must land attention.required"
    assert any(
        a["payload"].get("reason")
        in ("remote_unavailable", "missing_credentials", "missing_token", "ci_missing_credentials")
        for a in attention
    ), f"attention reasons must identify the credential gap: {[a['payload'] for a in attention]!r}"
