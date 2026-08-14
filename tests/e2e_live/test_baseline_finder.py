"""Deterministic tests for the baseline finder + manifest helpers in
``tests.e2e_live.m_test_helpers``.

These cover the contract documented in ``_find_baseline_for_sha``:

* exact SHA match is preferred;
* on SHA mismatch the finder falls back to the newest candidate with a
  readable manifest, so the caller's ``TRAC_LIVE_FORCE_BASELINE=1`` decision
  block is reachable (the bug being fixed returned ``None`` on mismatch);
* candidates whose manifest is missing or unreadable are skipped in both
  the exact-match pass and the fallback pass.

The helpers are exercised in isolation with ``tmp_path`` baselines and
monkeypatched ``_baselines_root`` / ``_tracks_short_sha``; no live channel
is required.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from tests.e2e_live import m_test_helpers as _helpers
from tests.e2e_live.harness import DEFAULT_LIVE_ROOT, live_root
from tests.e2e_live.m_test_helpers import (
    _assert_issue_events,
    _baseline_dir,
    _find_baseline_for_sha,
    _find_design_exit_baseline,
    _read_manifest,
)

_BASELINE_MODULE = "tests.e2e_live.m_test_helpers"


def _write_manifest(
    baseline: Path,
    *,
    tracks_sha: str,
    version: str,
    run_id: str = "R1",
    checkpoint: str | None = None,
) -> None:
    manifest = {
        "tracks_sha": tracks_sha,
        "version": version,
        "run_id": run_id,
        "captured_at": "2026-08-04T00:00:00+00:00",
        "source_host": str(baseline),
    }
    if checkpoint is not None:
        manifest["checkpoint"] = checkpoint
    (baseline / ".tracks-baseline-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _make_baseline(
    root: Path,
    name: str,
    *,
    tracks_sha: str | None,
    version: str,
    checkpoint: str | None = None,
) -> Path:
    path = root / name
    path.mkdir(parents=True)
    if tracks_sha is not None:
        _write_manifest(path, tracks_sha=tracks_sha, version=version, checkpoint=checkpoint)
    return path


def _set_mtime(path: Path, ns: int) -> None:
    os.utime(path, ns=(ns, ns))


def _patch_finder_env(monkeypatch, root: Path, sha: str) -> None:
    monkeypatch.delenv("TRAC_LIVE_BASELINE_DIR", raising=False)
    monkeypatch.delenv("TRAC_LIVE_FORCE_BASELINE", raising=False)
    monkeypatch.setattr(f"{_BASELINE_MODULE}._baselines_root", lambda: root)
    monkeypatch.setattr(f"{_BASELINE_MODULE}._tracks_short_sha", lambda: sha)


def test_live_root_defaults_when_env_is_unset(monkeypatch):
    monkeypatch.delenv("TRAC_LIVE_ROOT", raising=False)

    assert live_root() == DEFAULT_LIVE_ROOT


def test_live_root_override_aligns_baseline_root(monkeypatch, tmp_path):
    configured = tmp_path / "live-hosts"
    monkeypatch.setenv("TRAC_LIVE_ROOT", str(configured))

    assert live_root() == configured
    assert _helpers._baselines_root() == configured / "baselines"


def test_read_manifest_returns_none_for_missing_or_corrupt(tmp_path):
    missing = tmp_path / "no-manifest"
    missing.mkdir()
    assert _read_manifest(missing) is None

    corrupt = tmp_path / "corrupt"
    corrupt.mkdir()
    (corrupt / ".tracks-baseline-manifest.json").write_text("{not json", encoding="utf-8")
    assert _read_manifest(corrupt) is None


def test_read_manifest_parses_valid_json(tmp_path):
    ok = tmp_path / "ok"
    ok.mkdir()
    _write_manifest(ok, tracks_sha="abc1234", version="v1")
    manifest = _read_manifest(ok)
    assert manifest is not None
    assert manifest["tracks_sha"] == "abc1234"
    assert manifest["version"] == "v1"


def test_find_baseline_prefers_exact_sha_match_over_newer_mismatch(monkeypatch, tmp_path):
    version = "live-e2e-code-stats"
    _patch_finder_env(monkeypatch, tmp_path, sha="match1")
    older_match = _make_baseline(
        tmp_path,
        "baseline-match1-live-e2e-code-stats",
        tracks_sha="match1",
        version=version,
    )
    newer_mismatch = _make_baseline(
        tmp_path,
        "baseline-other-live-e2e-code-stats",
        tracks_sha="other",
        version=version,
    )
    _set_mtime(older_match, 1_000_000)
    _set_mtime(newer_mismatch, 2_000_000)
    assert _find_baseline_for_sha(version) == older_match


def test_find_baseline_falls_back_to_newest_readable_manifest_on_mismatch(monkeypatch, tmp_path):
    version = "live-e2e-code-stats"
    _patch_finder_env(monkeypatch, tmp_path, sha="headsha")
    older_mismatch = _make_baseline(
        tmp_path,
        "baseline-old1-live-e2e-code-stats",
        tracks_sha="old1",
        version=version,
    )
    newer_mismatch = _make_baseline(
        tmp_path,
        "baseline-old2-live-e2e-code-stats",
        tracks_sha="old2",
        version=version,
    )
    _set_mtime(older_mismatch, 1_000_000)
    _set_mtime(newer_mismatch, 2_000_000)
    # Before the fix this returned None; the FORCE check was unreachable.
    assert _find_baseline_for_sha(version) == newer_mismatch


def test_find_baseline_skips_unreadable_manifests_in_both_passes(monkeypatch, tmp_path):
    version = "live-e2e-code-stats"
    _patch_finder_env(monkeypatch, tmp_path, sha="headsha")
    # Newest but corrupt: must be skipped by the fallback pass too.
    corrupt = _make_baseline(
        tmp_path,
        "baseline-corrupt-live-e2e-code-stats",
        tracks_sha=None,
        version=version,
    )
    (corrupt / ".tracks-baseline-manifest.json").write_text("{bad json", encoding="utf-8")
    readable_mismatch = _make_baseline(
        tmp_path,
        "baseline-readable-live-e2e-code-stats",
        tracks_sha="other",
        version=version,
    )
    _set_mtime(corrupt, 2_000_000)
    _set_mtime(readable_mismatch, 1_000_000)
    assert _find_baseline_for_sha(version) == readable_mismatch


def test_find_baseline_returns_none_when_no_readable_manifest(monkeypatch, tmp_path):
    version = "live-e2e-code-stats"
    _patch_finder_env(monkeypatch, tmp_path, sha="headsha")
    empty = _make_baseline(
        tmp_path,
        "baseline-empty-live-e2e-code-stats",
        tracks_sha=None,
        version=version,
    )
    corrupt = _make_baseline(
        tmp_path,
        "baseline-bad-live-e2e-code-stats",
        tracks_sha=None,
        version=version,
    )
    (corrupt / ".tracks-baseline-manifest.json").write_text("{bad", encoding="utf-8")
    _set_mtime(empty, 1_000_000)
    _set_mtime(corrupt, 2_000_000)
    assert _find_baseline_for_sha(version) is None


def test_find_baseline_returns_none_when_root_missing(monkeypatch, tmp_path):
    version = "live-e2e-code-stats"
    _patch_finder_env(monkeypatch, tmp_path, sha="headsha")
    missing_root = tmp_path / "does-not-exist"
    monkeypatch.setattr(f"{_BASELINE_MODULE}._baselines_root", lambda: missing_root)
    assert _find_baseline_for_sha(version) is None


def test_find_baseline_explicit_dir_takes_precedence_and_bypasses_sha(monkeypatch, tmp_path):
    version = "live-e2e-code-stats"
    _patch_finder_env(monkeypatch, tmp_path, sha="headsha")
    explicit = tmp_path / "explicit-baseline"
    explicit.mkdir()
    _write_manifest(explicit, tracks_sha="other", version=version)
    monkeypatch.setenv("TRAC_LIVE_BASELINE_DIR", str(explicit))
    assert _find_baseline_for_sha(version) == explicit


def test_find_baseline_explicit_dir_without_manifest_skips(monkeypatch, tmp_path):
    version = "live-e2e-code-stats"
    _patch_finder_env(monkeypatch, tmp_path, sha="headsha")
    explicit = tmp_path / "explicit-baseline"
    explicit.mkdir()
    monkeypatch.setenv("TRAC_LIVE_BASELINE_DIR", str(explicit))
    with pytest.raises(pytest.skip.Exception) as exc:
        _find_baseline_for_sha(version)
    assert "no manifest" in str(exc.value)


def test_baseline_force_decision_matrix(monkeypatch, tmp_path):
    """Covers the three matrix rows the finder enables, replicating the
    FORCE-decision block from ``test_journey_from_req_approved_baseline``:

    * exact SHA match -> proceed regardless of FORCE
    * SHA mismatch + FORCE=1 -> proceed
    * SHA mismatch + no FORCE -> skip with "baseline SHA ... != HEAD"
    * no readable baseline -> skip with "no M-REQ-APPROVED baseline"
    """
    version = "live-e2e-code-stats"
    head_sha = "head1234"
    _patch_finder_env(monkeypatch, tmp_path, sha=head_sha)

    def decide(force: bool) -> str:
        monkeypatch.delenv("TRAC_LIVE_FORCE_BASELINE", raising=False)
        if force:
            monkeypatch.setenv("TRAC_LIVE_FORCE_BASELINE", "1")
        baseline = _find_baseline_for_sha(version)
        if baseline is None:
            return "skip-none"
        manifest = _read_manifest(baseline)
        if (
            manifest
            and manifest.get("tracks_sha") != _helpers._tracks_short_sha()
            and os.environ.get("TRAC_LIVE_FORCE_BASELINE", "").strip() != "1"
        ):
            return "skip-mismatch"
        return "proceed"

    # Row 3: exact match -> proceed regardless of FORCE.
    exact = _make_baseline(
        tmp_path,
        f"baseline-{head_sha}-{version}",
        tracks_sha=head_sha,
        version=version,
    )
    _set_mtime(exact, 1_000_000)
    assert decide(force=False) == "proceed"
    assert decide(force=True) == "proceed"

    # Replace with a mismatch: rows 1 and 2.
    shutil.rmtree(exact)
    mismatch = _make_baseline(
        tmp_path,
        f"baseline-other-{version}",
        tracks_sha="other1234",
        version=version,
    )
    _set_mtime(mismatch, 1_000_000)
    assert decide(force=False) == "skip-mismatch"
    assert decide(force=True) == "proceed"

    # No readable baseline at all: skip-none (FORCE cannot help).
    for child in list(tmp_path.iterdir()):
        shutil.rmtree(child)
    bad = _make_baseline(
        tmp_path,
        f"baseline-bad-{version}",
        tracks_sha=None,
        version=version,
    )
    (bad / ".tracks-baseline-manifest.json").write_text("{bad", encoding="utf-8")
    assert decide(force=False) == "skip-none"
    assert decide(force=True) == "skip-none"


# -- _assert_issue_events helper tests -------------------------------------


def _issue_event(issue_id) -> dict:
    """Build a minimal issue.created event dict for the helper."""
    return {"type": "issue.created", "payload": {"issue_id": issue_id}}


def test_assert_issue_events_empty_fails_both_modes():
    # An empty event list has no issue.created events.
    with pytest.raises(AssertionError, match="missing issue.created event"):
        _assert_issue_events([], expect_real_issues=True)
    with pytest.raises(AssertionError, match="missing issue.created event"):
        _assert_issue_events([], expect_real_issues=False)
    # A non-empty list with no issue.created events also fails (the helper
    # filters by type before asserting presence).
    other = [{"type": "run.completed", "payload": {}}]
    with pytest.raises(AssertionError, match="missing issue.created event"):
        _assert_issue_events(other, expect_real_issues=True)
    with pytest.raises(AssertionError, match="missing issue.created event"):
        _assert_issue_events(other, expect_real_issues=False)


def test_assert_issue_events_real_only_matrix():
    """expect_real_issues=True: all-digits pass; FAKE- and mixed fail."""
    # all-digits ids pass.
    _assert_issue_events([_issue_event("123"), _issue_event("456")], expect_real_issues=True)
    # FAKE- id fails (the baseline-resume bug being scoped out).
    with pytest.raises(AssertionError):
        _assert_issue_events([_issue_event("FAKE-1")], expect_real_issues=True)
    # mixed digits + FAKE fails.
    with pytest.raises(AssertionError):
        _assert_issue_events(
            [_issue_event("123"), _issue_event("FAKE-2")],
            expect_real_issues=True,
        )


def test_assert_issue_events_fake_accepted_matrix():
    """expect_real_issues=False: all-digits and FAKE- both pass (alone or
    mixed); a non-digit/non-FAKE id still fails."""
    # all-digits pass.
    _assert_issue_events([_issue_event("123"), _issue_event("456")], expect_real_issues=False)
    # FAKE- ids pass (the kernel's fake channel format, FAKE-<n>).
    _assert_issue_events(
        [_issue_event("FAKE-1"), _issue_event("FAKE-2")],
        expect_real_issues=False,
    )
    # mixed digits + FAKE pass.
    _assert_issue_events([_issue_event("123"), _issue_event("FAKE-2")], expect_real_issues=False)
    # a non-digit, non-FAKE id still fails under the lenient mode.
    with pytest.raises(AssertionError):
        _assert_issue_events([_issue_event("abc")], expect_real_issues=False)


def test_assert_issue_events_payload_must_carry_issue_id():
    """The always-true contract: every issue.created payload has an issue_id."""
    missing = [{"type": "issue.created", "payload": {}}]
    with pytest.raises(AssertionError, match="missing issue_id"):
        _assert_issue_events(missing, expect_real_issues=True)
    with pytest.raises(AssertionError, match="missing issue_id"):
        _assert_issue_events(missing, expect_real_issues=False)


# -- checkpoint-aware baseline finder tests (v0.4 M-TEST milestone) --------


def test_baseline_dir_legacy_checkpoint_uses_unprefixed_name(monkeypatch, tmp_path):
    """M-REQ-APPROVAL (default) keeps ``baseline-{sha}-{version}`` so
    existing snapshots are backward-compatible."""
    monkeypatch.setattr(f"{_BASELINE_MODULE}._baselines_root", lambda: tmp_path)
    assert _baseline_dir("v1", "abc1234") == tmp_path / "baseline-abc1234-v1"


def test_baseline_dir_design_exit_uses_prefixed_name(monkeypatch, tmp_path):
    """The design-exit checkpoint uses a ``baseline-design-exit-observed-{sha}-
    {version}`` directory name so it never collides with the legacy
    M-REQ-APPROVED baseline."""
    monkeypatch.setattr(f"{_BASELINE_MODULE}._baselines_root", lambda: tmp_path)
    assert _baseline_dir("v1", "abc1234", "DESIGN_EXIT_OBSERVED") == (
        tmp_path / "baseline-design-exit-observed-abc1234-v1"
    )


def test_find_design_exit_baseline_matches_prefixed_dir(monkeypatch, tmp_path):
    """The design-exit checkpoint uses a ``baseline-design-exit-observed-*``
    directory prefix and a ``checkpoint=DESIGN_EXIT_OBSERVED`` manifest
    field so it never collides with the legacy M-REQ-APPROVED baseline."""
    version = "live-e2e-code-stats"
    _patch_finder_env(monkeypatch, tmp_path, sha="match1")
    design_exit = _make_baseline(
        tmp_path,
        "baseline-design-exit-observed-match1-live-e2e-code-stats",
        tracks_sha="match1",
        version=version,
        checkpoint="DESIGN_EXIT_OBSERVED",
    )
    _set_mtime(design_exit, 1_000_000)
    assert _find_design_exit_baseline(version) == design_exit


def test_find_design_exit_baseline_ignores_req_approved_baseline(monkeypatch, tmp_path):
    """An M-REQ-APPROVED baseline (no checkpoint field, default
    M-REQ-APPROVAL) must not be returned when looking for a design-exit
    baseline."""
    version = "live-e2e-code-stats"
    _patch_finder_env(monkeypatch, tmp_path, sha="match1")
    req_approved = _make_baseline(
        tmp_path,
        "baseline-match1-live-e2e-code-stats",
        tracks_sha="match1",
        version=version,
    )
    _set_mtime(req_approved, 1_000_000)
    assert _find_design_exit_baseline(version) is None


def test_find_req_approved_baseline_ignores_design_exit_baseline(monkeypatch, tmp_path):
    """Conversely, a design-exit baseline must not be returned when looking
    for an M-REQ-APPROVED baseline."""
    version = "live-e2e-code-stats"
    _patch_finder_env(monkeypatch, tmp_path, sha="match1")
    design_exit = _make_baseline(
        tmp_path,
        "baseline-design-exit-observed-match1-live-e2e-code-stats",
        tracks_sha="match1",
        version=version,
        checkpoint="DESIGN_EXIT_OBSERVED",
    )
    _set_mtime(design_exit, 1_000_000)
    assert _find_baseline_for_sha(version) is None


def test_find_design_exit_baseline_prefers_exact_sha(monkeypatch, tmp_path):
    """Exact SHA match is preferred over newer mismatch, same as the legacy
    finder."""
    version = "live-e2e-code-stats"
    _patch_finder_env(monkeypatch, tmp_path, sha="match1")
    older_match = _make_baseline(
        tmp_path,
        "baseline-design-exit-observed-match1-live-e2e-code-stats",
        tracks_sha="match1",
        version=version,
        checkpoint="DESIGN_EXIT_OBSERVED",
    )
    newer_mismatch = _make_baseline(
        tmp_path,
        "baseline-design-exit-observed-other-live-e2e-code-stats",
        tracks_sha="other",
        version=version,
        checkpoint="DESIGN_EXIT_OBSERVED",
    )
    _set_mtime(older_match, 1_000_000)
    _set_mtime(newer_mismatch, 2_000_000)
    assert _find_design_exit_baseline(version) == older_match


def test_find_design_exit_baseline_falls_back_on_mismatch(monkeypatch, tmp_path):
    """SHA mismatch falls back to newest readable design-exit baseline."""
    version = "live-e2e-code-stats"
    _patch_finder_env(monkeypatch, tmp_path, sha="headsha")
    older = _make_baseline(
        tmp_path,
        "baseline-design-exit-observed-old1-live-e2e-code-stats",
        tracks_sha="old1",
        version=version,
        checkpoint="DESIGN_EXIT_OBSERVED",
    )
    newer = _make_baseline(
        tmp_path,
        "baseline-design-exit-observed-old2-live-e2e-code-stats",
        tracks_sha="old2",
        version=version,
        checkpoint="DESIGN_EXIT_OBSERVED",
    )
    _set_mtime(older, 1_000_000)
    _set_mtime(newer, 2_000_000)
    assert _find_design_exit_baseline(version) == newer


def test_find_design_exit_baseline_skips_wrong_checkpoint_manifest(monkeypatch, tmp_path):
    """A baseline with the right directory prefix but wrong manifest
    checkpoint is skipped."""
    version = "live-e2e-code-stats"
    _patch_finder_env(monkeypatch, tmp_path, sha="headsha")
    wrong = _make_baseline(
        tmp_path,
        "baseline-design-exit-observed-headsha-live-e2e-code-stats",
        tracks_sha="headsha",
        version=version,
        checkpoint="M-REQ-APPROVAL",
    )
    _set_mtime(wrong, 1_000_000)
    assert _find_design_exit_baseline(version) is None


def test_find_design_exit_baseline_returns_none_when_root_missing(monkeypatch, tmp_path):
    version = "live-e2e-code-stats"
    _patch_finder_env(monkeypatch, tmp_path, sha="headsha")
    missing_root = tmp_path / "does-not-exist"
    monkeypatch.setattr(f"{_BASELINE_MODULE}._baselines_root", lambda: missing_root)
    assert _find_design_exit_baseline(version) is None


def test_find_design_exit_baseline_explicit_dir(monkeypatch, tmp_path):
    """TRAC_LIVE_BASELINE_DIR takes precedence for design-exit too."""
    version = "live-e2e-code-stats"
    _patch_finder_env(monkeypatch, tmp_path, sha="headsha")
    explicit = tmp_path / "explicit-design-exit"
    explicit.mkdir()
    _write_manifest(
        explicit,
        tracks_sha="other",
        version=version,
        checkpoint="DESIGN_EXIT_OBSERVED",
    )
    monkeypatch.setenv("TRAC_LIVE_BASELINE_DIR", str(explicit))
    assert _find_design_exit_baseline(version) == explicit


def test_find_design_exit_baseline_coexists_with_req_approved(monkeypatch, tmp_path):
    """Both checkpoint types can coexist under the same root without
    interfering: each finder returns only its own checkpoint type."""
    version = "live-e2e-code-stats"
    _patch_finder_env(monkeypatch, tmp_path, sha="match1")
    req_approved = _make_baseline(
        tmp_path,
        "baseline-match1-live-e2e-code-stats",
        tracks_sha="match1",
        version=version,
    )
    design_exit = _make_baseline(
        tmp_path,
        "baseline-design-exit-observed-match1-live-e2e-code-stats",
        tracks_sha="match1",
        version=version,
        checkpoint="DESIGN_EXIT_OBSERVED",
    )
    _set_mtime(req_approved, 1_000_000)
    _set_mtime(design_exit, 2_000_000)
    assert _find_baseline_for_sha(version) == req_approved
    assert _find_design_exit_baseline(version) == design_exit


def test_restore_drops_baseline_manifest_marker(tmp_path):
    """Restore drops ``.tracks-baseline-manifest.json`` (snapshot metadata,
    not host state) while keeping all other host content. Leaving the marker
    would make ``git status`` report an untracked top-level file and trip the
    Shield scope assertion that every new path lives under ``tests/``."""
    live_root = tmp_path / "live"
    live_root.mkdir()
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    (baseline / "README.md").write_text("host doc\n", encoding="utf-8")
    (baseline / "tests" / "integration").mkdir(parents=True)
    (baseline / "tests" / "integration" / "test_a.py").write_text(
        "# AC-FR0001-01@v0.5 TRACKS-TRACE\n", encoding="utf-8"
    )
    manifest_name = _helpers._BASELINE_MANIFEST_NAME
    (baseline / manifest_name).write_text(
        json.dumps({"tracks_sha": "abc1234", "checkpoint": "M-REQ-APPROVAL"}),
        encoding="utf-8",
    )

    _helpers._restore_baseline(baseline, live_root)

    assert not (live_root / manifest_name).exists(), (
        "restore leaked baseline manifest marker into live host"
    )
    assert (live_root / "README.md").read_text(encoding="utf-8") == "host doc\n"
    assert (live_root / "tests" / "integration" / "test_a.py").read_text(
        encoding="utf-8"
    ) == "# AC-FR0001-01@v0.5 TRACKS-TRACE\n"
