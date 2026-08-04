"""Deterministic tests for the baseline finder + manifest helpers in
``tests.e2e_live.test_full_journey``.

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

from tests.e2e_live import test_full_journey as _journey
from tests.e2e_live.test_full_journey import (
    _find_baseline_for_sha,
    _read_manifest,
)

_BASELINE_MODULE = "tests.e2e_live.test_full_journey"


def _write_manifest(
    baseline: Path, *, tracks_sha: str, version: str, run_id: str = "R1"
) -> None:
    manifest = {
        "tracks_sha": tracks_sha,
        "version": version,
        "run_id": run_id,
        "captured_at": "2026-08-04T00:00:00+00:00",
        "source_host": str(baseline),
    }
    (baseline / ".tracks-baseline-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def _make_baseline(
    root: Path, name: str, *, tracks_sha: str | None, version: str
) -> Path:
    path = root / name
    path.mkdir(parents=True)
    if tracks_sha is not None:
        _write_manifest(path, tracks_sha=tracks_sha, version=version)
    return path


def _set_mtime(path: Path, ns: int) -> None:
    os.utime(path, ns=(ns, ns))


def _patch_finder_env(monkeypatch, root: Path, sha: str) -> None:
    monkeypatch.delenv("TRAC_LIVE_BASELINE_DIR", raising=False)
    monkeypatch.delenv("TRAC_LIVE_FORCE_BASELINE", raising=False)
    monkeypatch.setattr(f"{_BASELINE_MODULE}._baselines_root", lambda: root)
    monkeypatch.setattr(f"{_BASELINE_MODULE}._tracks_short_sha", lambda: sha)


def test_read_manifest_returns_none_for_missing_or_corrupt(tmp_path):
    missing = tmp_path / "no-manifest"
    missing.mkdir()
    assert _read_manifest(missing) is None

    corrupt = tmp_path / "corrupt"
    corrupt.mkdir()
    (corrupt / ".tracks-baseline-manifest.json").write_text(
        "{not json", encoding="utf-8"
    )
    assert _read_manifest(corrupt) is None


def test_read_manifest_parses_valid_json(tmp_path):
    ok = tmp_path / "ok"
    ok.mkdir()
    _write_manifest(ok, tracks_sha="abc1234", version="v1")
    manifest = _read_manifest(ok)
    assert manifest is not None
    assert manifest["tracks_sha"] == "abc1234"
    assert manifest["version"] == "v1"


def test_find_baseline_prefers_exact_sha_match_over_newer_mismatch(
    monkeypatch, tmp_path
):
    version = "live-e2e-code-stats"
    _patch_finder_env(monkeypatch, tmp_path, sha="match1")
    older_match = _make_baseline(
        tmp_path, "baseline-match1-live-e2e-code-stats",
        tracks_sha="match1", version=version,
    )
    newer_mismatch = _make_baseline(
        tmp_path, "baseline-other-live-e2e-code-stats",
        tracks_sha="other", version=version,
    )
    _set_mtime(older_match, 1_000_000)
    _set_mtime(newer_mismatch, 2_000_000)
    assert _find_baseline_for_sha(version) == older_match


def test_find_baseline_falls_back_to_newest_readable_manifest_on_mismatch(
    monkeypatch, tmp_path
):
    version = "live-e2e-code-stats"
    _patch_finder_env(monkeypatch, tmp_path, sha="headsha")
    older_mismatch = _make_baseline(
        tmp_path, "baseline-old1-live-e2e-code-stats",
        tracks_sha="old1", version=version,
    )
    newer_mismatch = _make_baseline(
        tmp_path, "baseline-old2-live-e2e-code-stats",
        tracks_sha="old2", version=version,
    )
    _set_mtime(older_mismatch, 1_000_000)
    _set_mtime(newer_mismatch, 2_000_000)
    # Before the fix this returned None; the FORCE check was unreachable.
    assert _find_baseline_for_sha(version) == newer_mismatch


def test_find_baseline_skips_unreadable_manifests_in_both_passes(
    monkeypatch, tmp_path
):
    version = "live-e2e-code-stats"
    _patch_finder_env(monkeypatch, tmp_path, sha="headsha")
    # Newest but corrupt: must be skipped by the fallback pass too.
    corrupt = _make_baseline(
        tmp_path, "baseline-corrupt-live-e2e-code-stats",
        tracks_sha=None, version=version,
    )
    (corrupt / ".tracks-baseline-manifest.json").write_text(
        "{bad json", encoding="utf-8"
    )
    readable_mismatch = _make_baseline(
        tmp_path, "baseline-readable-live-e2e-code-stats",
        tracks_sha="other", version=version,
    )
    _set_mtime(corrupt, 2_000_000)
    _set_mtime(readable_mismatch, 1_000_000)
    assert _find_baseline_for_sha(version) == readable_mismatch


def test_find_baseline_returns_none_when_no_readable_manifest(
    monkeypatch, tmp_path
):
    version = "live-e2e-code-stats"
    _patch_finder_env(monkeypatch, tmp_path, sha="headsha")
    empty = _make_baseline(
        tmp_path, "baseline-empty-live-e2e-code-stats",
        tracks_sha=None, version=version,
    )
    corrupt = _make_baseline(
        tmp_path, "baseline-bad-live-e2e-code-stats",
        tracks_sha=None, version=version,
    )
    (corrupt / ".tracks-baseline-manifest.json").write_text(
        "{bad", encoding="utf-8"
    )
    _set_mtime(empty, 1_000_000)
    _set_mtime(corrupt, 2_000_000)
    assert _find_baseline_for_sha(version) is None


def test_find_baseline_returns_none_when_root_missing(monkeypatch, tmp_path):
    version = "live-e2e-code-stats"
    _patch_finder_env(monkeypatch, tmp_path, sha="headsha")
    missing_root = tmp_path / "does-not-exist"
    monkeypatch.setattr(
        f"{_BASELINE_MODULE}._baselines_root", lambda: missing_root
    )
    assert _find_baseline_for_sha(version) is None


def test_find_baseline_explicit_dir_takes_precedence_and_bypasses_sha(
    monkeypatch, tmp_path
):
    version = "live-e2e-code-stats"
    _patch_finder_env(monkeypatch, tmp_path, sha="headsha")
    explicit = tmp_path / "explicit-baseline"
    explicit.mkdir()
    _write_manifest(explicit, tracks_sha="other", version=version)
    monkeypatch.setenv("TRAC_LIVE_BASELINE_DIR", str(explicit))
    assert _find_baseline_for_sha(version) == explicit


def test_find_baseline_explicit_dir_without_manifest_skips(
    monkeypatch, tmp_path
):
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
            and manifest.get("tracks_sha") != _journey._tracks_short_sha()
            and os.environ.get("TRAC_LIVE_FORCE_BASELINE", "").strip() != "1"
        ):
            return "skip-mismatch"
        return "proceed"

    # Row 3: exact match -> proceed regardless of FORCE.
    exact = _make_baseline(
        tmp_path, f"baseline-{head_sha}-{version}",
        tracks_sha=head_sha, version=version,
    )
    _set_mtime(exact, 1_000_000)
    assert decide(force=False) == "proceed"
    assert decide(force=True) == "proceed"

    # Replace with a mismatch: rows 1 and 2.
    shutil.rmtree(exact)
    mismatch = _make_baseline(
        tmp_path, f"baseline-other-{version}",
        tracks_sha="other1234", version=version,
    )
    _set_mtime(mismatch, 1_000_000)
    assert decide(force=False) == "skip-mismatch"
    assert decide(force=True) == "proceed"

    # No readable baseline at all: skip-none (FORCE cannot help).
    for child in list(tmp_path.iterdir()):
        shutil.rmtree(child)
    bad = _make_baseline(
        tmp_path, f"baseline-bad-{version}",
        tracks_sha=None, version=version,
    )
    (bad / ".tracks-baseline-manifest.json").write_text(
        "{bad", encoding="utf-8"
    )
    assert decide(force=False) == "skip-none"
    assert decide(force=True) == "skip-none"
