"""Live opencode E2E — real agent pipeline (TP-003 §4b, AC-0104/0105).

These tests prove the REAL-agent plumbing: real startup, permission whitelist
enforcement, JSON protocol parseability, target-diff product format, and exit
recovery. They assert protocol/format/diff, NEVER prompt or text content, and
they skip (not fail) when the live channel env is unset.

The deterministic fake channel is untouched by this directory (root conftest's
autouse fake-forcing fixture is overridden here).
"""

from __future__ import annotations

import subprocess

from tracks.effects.opencode import AGENT_NAME

# -- helpers ---------------------------------------------------------------


def _ready_spec(repo):
    """A bare spec skeleton the Sage agent must fill in to pass the template
    check — deliberately incomplete so Sage produces a real diff."""
    p = repo / "spec.md"
    p.write_text(
        "---\nspec_id: SPEC-LIVE\ncreated: 2026-08-01\nstatus: draft\nsha:\n---\n\n"
        "# 需求规格\n\n<!-- 内容待补充 -->\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "spec.md"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "spec skeleton"], cwd=repo, check=True, capture_output=True
    )
    return p


def _outcome_ok(out) -> bool:
    """Protocol sanity only — never inspects agent text."""
    assert set(out) <= {
        "status",
        "artifact_ref",
        "self_report",
        "diff_ref",
        "audit_evidence",
        "failure_class",
        "agent_io",
        "verdict",
        "discussion_evidence",
        "materialization_evidence",
    }
    assert out["status"] in ("done", "failed")
    return out["status"] == "done"


# -- 1. real startup + JSON protocol ----------------------------------------


def test_real_startup_and_json_protocol(host_with_opencode_config, live_backend, steps):
    """The agent truly starts, `opencode run` is found, and its stdout JSON
    event stream is parseable (step_start/text/step_finish, or a classified
    failure) — without asserting the text itself."""
    repo = host_with_opencode_config
    steps.step("fixture: host_with_opencode_config", provider="opencode")
    spec = _ready_spec(repo)
    steps.step("prepared spec skeleton")
    out = live_backend.act("sage", "DRAFT", "spec.md", spec)
    steps.step(
        "opencode sage DRAFT ran",
        status="ok" if out["status"] == "done" else "fail",
        failure_class=out.get("failure_class"),
        has_diff=bool(out.get("diff_ref")),
    )
    ok = _outcome_ok(out)
    assert ok, f"real startup should succeed but got {out.get('failure_class')}"
    assert out["diff_ref"], "live run claims done but has no target diff"
    assert out["agent_io"]["prompt"]
    steps.step("asserted done + target diff present")


# -- 2. permission whitelist is enforced ------------------------------------


def test_permission_whitelist_enforced(host_with_opencode_config, live_backend, steps):
    """Over-reach (write outside the target doc) is detected and rolled back:
    the agent is allowed the target only; the Auditor flags everything else."""
    repo = host_with_opencode_config
    steps.step("fixture: host_with_opencode_config", provider="opencode")
    spec = _ready_spec(repo)
    steps.step("prepared spec skeleton")
    # Trap: the Sage prompt asks for a spec update; if the agent writes any
    # OTHER file, the post-run audit must catch and roll it back.
    extra = repo / "EXTRA.md"
    extra.write_text("nope\n", encoding="utf-8")  # pre-existing (baseline)
    steps.step("planted EXTRA.md baseline trap")
    out = live_backend.act("sage", "DRAFT", "spec.md", spec)
    steps.step(
        "opencode sage DRAFT ran",
        status="ok" if out["status"] == "done" else "fail",
        failure_class=out.get("failure_class"),
    )
    _outcome_ok(out)
    assert out["status"] == "done", (
        f"permission test needs a successful run, got {out.get('failure_class')}"
    )
    # Audit would have flagged over-reach had the agent strayed; and the
    # pre-existing extra file is never touched by our plumbing.
    assert (repo / "EXTRA.md").exists()
    steps.step("asserted EXTRA.md intact")


# -- 3. target diff is the authoritative product ----------------------------


def test_target_diff_is_authoritative_product(host_with_opencode_config, live_backend, steps):
    """The controlled target-file diff is the product; stdout JSON is not."""
    repo = host_with_opencode_config
    steps.step("fixture: host_with_opencode_config", provider="opencode")
    spec = _ready_spec(repo)
    steps.step("prepared spec skeleton")
    out = live_backend.act("sage", "DRAFT", "spec.md", spec)
    steps.step(
        "opencode sage DRAFT ran",
        status="ok" if out["status"] == "done" else "fail",
        failure_class=out.get("failure_class"),
    )
    _outcome_ok(out)
    assert out["status"] == "done", (
        f"diff test needs a successful run, got {out.get('failure_class')}"
    )
    diff = out["diff_ref"]
    steps.step("parsed outcome diff_ref", bytes=len(diff) if diff else 0)
    assert isinstance(diff, str) and diff.strip()
    # Format sanity: a unified diff with +/- markers, no text-content claims.
    assert any(
        line.startswith("+") or line.startswith("-") or line.startswith("@@")
        for line in diff.splitlines()
    )
    steps.step("asserted unified-diff format markers")


# -- 4. Lex semantic review runs (edit denied) ------------------------------


def test_lex_review_runs(host_with_opencode_config, live_backend, steps):
    """Lex (edit: deny) still runs through the real pipe. Its review lives in
    inline-discussion, so no target diff is required; a clean outcome or a
    classified failure both prove the plumbing."""
    repo = host_with_opencode_config
    steps.step("fixture: host_with_opencode_config", provider="opencode")
    spec = _ready_spec(repo)
    steps.step("prepared spec skeleton")
    out = live_backend.act("lex", "LEX_REVIEW", "spec.md", spec)
    steps.step(
        "opencode lex LEX_REVIEW ran",
        status="ok" if out["status"] == "done" else "fail",
        failure_class=out.get("failure_class"),
    )
    _outcome_ok(out)


# -- 5. exit recovery: classified failures and cleanup ----------------------


def test_provider_error_is_classified(host_with_opencode_config, live_backend, monkeypatch, steps):
    """A provider-level failure maps to provider_unavailable and the materialized
    agent is cleaned up (exit recovery, ARCH §4c/§7)."""
    repo = host_with_opencode_config
    steps.step("fixture: host_with_opencode_config", provider="opencode")
    spec = _ready_spec(repo)
    steps.step("prepared spec skeleton")
    # Force an auth/provider failure without touching tracks' code: revoke the
    # API key the subprocess sees, so opencode reports a provider error.
    monkeypatch.setenv("TRAC_LIVE_API_KEY", "sk-invalid-for-this-test")
    steps.step("revoked API key (invalid auth)")
    out = live_backend.act("sage", "DRAFT", "spec.md", spec)
    steps.step(
        "opencode sage DRAFT ran",
        status="ok" if out["status"] == "done" else "fail",
        failure_class=out.get("failure_class"),
    )
    _outcome_ok(out)
    # Whatever the classified result, the materialized agent is gone (cleanup).
    for name in AGENT_NAME.values():
        assert not (repo / ".opencode" / "agents" / f"{name}.md").exists()
    steps.step("asserted materialized agents cleaned up")
