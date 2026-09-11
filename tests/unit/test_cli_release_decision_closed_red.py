"""T-001 RED round 3: decision closure + rejection audit on the release CLI.

Pins the still-undelivered slices of the ``trac release`` surface
(IF-RELEASE-003, §2a/§1a#8/§1.0.5/SM-01.11):

- Every fail-closed rejection (gate failed / stale preview, for all three
  actions) appends an append-only ``release.rejected`` audit event with the
  §1a#8 closed payload (action, candidate_sha, preview_digest, reason
  ∈ {gate_failed, preview_stale}, detail) — a rejection is observable in the
  event stream, not only on stderr (architecture §1.0.5: 非零退出 +
  release.rejected).
- The three-way gate is CLOSED after a decision until a NEW preview is
  generated: a second decision without a newer ``release.previewed`` is
  rejected with no additional ``release.decided`` (SM-01.11 — only preview
  regeneration reopens AWAITING_RELEASE). Re-decision after regeneration
  stays legal (guard).
- ``--action return --to`` validates its target against the canonical stage
  set — a non-stage token is a fail-closed usage rejection with no decision.
- ``release preview`` renders the E-01 ``ci_run={...}`` binding from the
  observed CI evidence instead of an empty placeholder.

All target tests fail on the pre-fix baseline with assertion_failure on the
contract token (no assembly errors). ``test_decision_reopens_after_preview_
regeneration`` is a preserved-contract guard that intentionally passes on
the pre-fix baseline too — it pins SM-01.11's reopen path so the closure fix
cannot over-block regeneration. Only unit tests are added (RED discipline).
"""

from __future__ import annotations

from pathlib import Path

from tracks.store import Store

_CANDIDATE = "2f6c3a1d" * 8
_PREVIEW = "sha256:" + "7e1b" * 16
_ARTIFACT = "sha256:" + "4a55" * 16
_POLICY = "sha256:" + "d00d" * 16
_CI_REPO = "acme/host"
_CI_WORKFLOW = "ci.yml"
_CI_RUN = 12345


def _preview_payload() -> dict:
    """§1a#6 release.previewed payload (closed field set)."""
    return {
        "candidate_sha": _CANDIDATE,
        "preview_digest": _PREVIEW,
        "artifact_digest": _ARTIFACT,
        "evidence_digests": {"full_f": "sha256:" + "e1" * 32},
        "operation_plan_digest": "sha256:" + "0p" * 32,
        "contract_policy_digest": _POLICY,
        "risks": [],
        "blob_ref": "runtime/blobs/release/RUN/1-preview.json",
    }


def _setup(tmp_path) -> tuple[Store, Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = Store(repo / ".tracks")
    return store, repo, "RUN"


def _seed_gates_ok(store: Store, run_id: str) -> None:
    store.append(
        run_id, "v0.8", "local_gate.passed",
        {
            "kind": "quality",
            "candidate_sha": _CANDIDATE,
            "contract_digest": _POLICY,
            "command_echo": [],
            "normalized_result": {},
            "reason": None,
        },
    )
    store.append(
        run_id, "v0.8", "prism.verdict",
        {"verdict": "pass", "scope": "verify_final", "candidate_sha": _CANDIDATE},
    )
    store.append(
        run_id, "v0.8", "security.assessed",
        {
            "status": "passed",
            "policy_digest": _POLICY,
            "candidate_sha": _CANDIDATE,
            "scans": [],
            "prism_scope": "security",
        },
    )


def _seed_awaiting_release(store: Store, run_id: str, *, ci: bool = False) -> None:
    store.append(run_id, "v0.8", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.8", "stage.entered", {"stage": "M-VERIFY"})
    _seed_gates_ok(store, run_id)
    if ci:
        store.append(
            run_id, "v0.8", "ci.run_observed",
            {
                "status": "passed",
                "repo": _CI_REPO,
                "workflow": _CI_WORKFLOW,
                "run_id": _CI_RUN,
                "head_sha": _CANDIDATE,
                "candidate_sha": _CANDIDATE,
                "conclusion": "success",
                "required_checks": ["lint", "test"],
                "api_verified": True,
            },
        )
    store.append(run_id, "v0.8", "stage.entered", {"stage": "M-SECURITY"})
    store.append(run_id, "v0.8", "stage.entered", {"stage": "M-RELEASE"})
    store.append(run_id, "v0.8", "release.previewed", _preview_payload())


def _seed_gate_failed(store: Store, run_id: str) -> None:
    store.append(run_id, "v0.8", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.8", "stage.entered", {"stage": "M-SECURITY"})
    store.append(
        run_id, "v0.8", "security.assessed",
        {
            "status": "failed",
            "policy_digest": _POLICY,
            "candidate_sha": _CANDIDATE,
            "scans": [],
            "prism_scope": "security",
        },
    )
    store.append(run_id, "v0.8", "stage.entered", {"stage": "M-RELEASE"})
    store.append(run_id, "v0.8", "release.previewed", _preview_payload())


def _types(store: Store, run_id: str, kind: str) -> list:
    return [e for e in store.events(run_id) if e.type == kind]


# -- release.rejected audit event (§1a#8, architecture §1.0.5) ------------------


def test_gate_failed_rejection_appends_release_rejected(tmp_path, capsys):
    """AC-FR0274-02: a gate-failed `--action release` rejection is audited as
    an append-only release.rejected event with reason=gate_failed, bound to
    the attempted action + candidate + preview."""
    from tests.unit.test_release_authorization_direct import _append_preview, _gate, _host
    from tracks.cli.main import cmd_release

    repo, store, run_id, candidate, digest, _path, _preview = _host(tmp_path)
    store.append(run_id, "v0.8", "local_gate.failed", _gate(candidate, digest, status="failed"))
    _append_preview(store, repo, run_id, candidate, digest)
    preview = _types(store, run_id, "release.previewed")[-1].payload

    rc = cmd_release(repo, "--action", "release")
    capsys.readouterr()
    rejected = _types(store, run_id, "release.rejected")
    decided = _types(store, run_id, "release.decided")
    store.close()

    assert rc == 1
    assert len(decided) == 0
    assert len(rejected) == 1, (
        "a fail-closed rejection must be observable in the event stream "
        "(architecture §1.0.5: 非零退出 + release.rejected), not only on stderr"
    )
    payload = rejected[0].payload
    assert payload["action"] == "release"
    assert payload["reason"] == "gate_failed"
    assert payload["candidate_sha"] == candidate
    assert payload["preview_digest"] == preview["preview_digest"]
    assert isinstance(payload.get("detail"), str) and payload["detail"]


def test_stale_release_rejection_appends_release_rejected(tmp_path, capsys):
    """A stale-preview `--action release` rejection appends release.rejected
    with reason=preview_stale."""
    from tracks.cli.main import cmd_release

    store, repo, run_id = _setup(tmp_path)
    _seed_awaiting_release(store, run_id)
    store.append(
        run_id, "v0.8", "candidate.stale",
        {"candidate_sha": _CANDIDATE, "reason": "candidate_drift", "detail": "HEAD moved"},
    )

    rc = cmd_release(repo, "--action", "release")
    capsys.readouterr()
    rejected = _types(store, run_id, "release.rejected")
    store.close()

    assert rc == 1
    assert len(rejected) == 1
    payload = rejected[0].payload
    assert payload["action"] == "release"
    assert payload["reason"] == "preview_stale"
    assert payload["preview_digest"] == _PREVIEW


def test_stale_delay_and_return_rejections_append_release_rejected(tmp_path, capsys):
    """AC-FR0274-04: stale-preview delay and return rejections are audited as
    release.rejected (reason=preview_stale, action=delay|return) — the stale
    check is uniform across the three-way gate."""
    from tracks.cli.main import cmd_release

    store, repo, run_id = _setup(tmp_path)
    _seed_awaiting_release(store, run_id)
    store.append(
        run_id, "v0.8", "candidate.stale",
        {"candidate_sha": _CANDIDATE, "reason": "candidate_drift", "detail": "HEAD moved"},
    )

    rc_delay = cmd_release(repo, "--action", "delay", "--reason", "later")
    capsys.readouterr()
    rc_return = cmd_release(repo, "--action", "return", "--to", "M-DESIGN", "--reason", "back")
    capsys.readouterr()
    rejected = _types(store, run_id, "release.rejected")
    decided = _types(store, run_id, "release.decided")
    store.close()

    assert rc_delay != 0 and rc_return != 0
    assert len(decided) == 0
    assert len(rejected) == 2, "both stale rejections must be audited"
    actions = {r.payload["action"] for r in rejected}
    assert actions == {"delay", "return"}
    assert all(r.payload["reason"] == "preview_stale" for r in rejected)


# -- decision closure until preview regeneration (SM-01.11) ---------------------


def test_second_decision_without_new_preview_rejected(tmp_path, capsys):
    """The three-way gate is closed after a decision: without a NEWER
    release.previewed, a further decision is rejected (rc != 0, no extra
    release.decided) — only regeneration reopens AWAITING_RELEASE."""
    from tracks.cli.main import cmd_release

    store, repo, run_id = _setup(tmp_path)
    _seed_awaiting_release(store, run_id)
    store.append(
        run_id, "v0.8", "release.decided",
        {
            "action": "release",
            "candidate_sha": _CANDIDATE,
            "preview_digest": _PREVIEW,
            "reason": None,
            "target": None,
            "actor": "human",
        },
    )

    rc = cmd_release(repo, "--action", "delay", "--reason", "second thoughts")
    out, err = capsys.readouterr()
    decided = _types(store, run_id, "release.decided")
    store.close()

    assert rc != 0, (
        "a decision already binds this preview; the gate stays closed until a "
        "new preview is generated (SM-01.11)"
    )
    assert len(decided) == 1, "no additional release.decided may be appended"
    assert "preview" in err.lower(), "the rejection must point at preview regeneration"


def test_decision_reopens_after_preview_regeneration(tmp_path, capsys):
    """Preserved-contract guard (SM-01.11): after a delay decision, a NEW
    release.previewed reopens the gate — `--action release` then succeeds and
    binds the regenerated preview digest."""
    from tests.unit.test_release_authorization_direct import _append_preview, _gate, _host
    from tracks.cli.main import cmd_release

    repo, store, run_id, candidate, digest, _path, old_preview = _host(tmp_path)
    rc_delay = cmd_release(repo, "--action", "delay", "--reason", "wait")
    assert rc_delay == 0
    capsys.readouterr()

    gate = _gate(candidate, digest)
    gate["normalized_result"]["summary"] = {"result": "fresh evidence after delay"}
    store.append(run_id, "v0.8", "local_gate.passed", gate)
    _append_preview(store, repo, run_id, candidate, digest)
    fresh = _types(store, run_id, "release.previewed")[-1].payload
    assert fresh["preview_digest"] != old_preview["preview_digest"]

    rc = cmd_release(repo, "--action", "release")
    out, err = capsys.readouterr()
    decided = _types(store, run_id, "release.decided")
    store.close()

    assert rc == 0, f"stderr={err!r}"
    assert len(decided) == 2
    assert decided[-1].payload["preview_digest"] == fresh["preview_digest"], (
        "the reopened decision must bind the regenerated preview"
    )


# -- return target closed set ----------------------------------------------------


def test_release_return_rejects_non_stage_target(tmp_path, capsys):
    """§2a/§2d: `--action return --to <stage>` targets the canonical stage
    set; a non-stage token is a fail-closed rejection with no decision."""
    from tracks.cli.main import cmd_release

    store, repo, run_id = _setup(tmp_path)
    _seed_awaiting_release(store, run_id)

    rc = cmd_release(repo, "--action", "return", "--to", "bogus-stage", "--reason", "x")
    out, err = capsys.readouterr()
    decided = _types(store, run_id, "release.decided")
    store.close()

    assert rc != 0, "a non-stage --to target must not produce a decision"
    assert len(decided) == 0
    assert "usage" in err.lower() or "invalid" in err.lower()


# -- E-01 preview ci_run binding --------------------------------------------------


def test_release_preview_renders_ci_run_binding(tmp_path, capsys):
    """E-01: the preview line renders the observed CI binding
    (ci_run={repo… workflow… run_id… head…}) instead of an empty placeholder
    when a ci.run_observed event backs the preview (§4a#4)."""
    from tracks.cli.main import cmd_release

    store, repo, run_id = _setup(tmp_path)
    _seed_awaiting_release(store, run_id, ci=True)

    rc = cmd_release(repo, "preview")
    out, err = capsys.readouterr()
    store.close()

    assert rc == 0, f"stderr={err!r}"
    assert "ci_run={}" not in out, "ci_run must not render as an empty placeholder"
    assert f"repo={_CI_REPO}" in out
    assert f"workflow={_CI_WORKFLOW}" in out
    assert f"run_id={_CI_RUN}" in out
    assert f"head={_CANDIDATE}" in out
