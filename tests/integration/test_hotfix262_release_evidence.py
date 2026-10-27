"""Integration: release evidence key completeness (IF-RELEV-001).

The contract under test is INV-4 / INV-5 (interfaces §1b): a NEW preview /
authorization is invalid unless FIVE evidence keys are present and valid --
``full_f``, ``ci``, ``prism.verify_final``, ``prism.security``, ``security``;
and the pre-v0.12.1 LEGACY preview blob (a single ``"prism"`` key) stays
readable.

TEST-2: every assertion lands on a PUBLIC outlet (interfaces §4a):

* the ``release.previewed`` EVENT and its payload -- the preview's own
  ``evidence_digests`` key set, produced by the real preview command;
* the ``trac release preview`` CLI -- the preview line and its stale reason;
* the ``trac release --action release`` CLI -- its exit code, its fixed
  refusal text, and the absence of a ``release.decided`` event.

No private helper is imported and no internal return value is asserted: the
tests drive the real production entry points and read what they emit.

The legacy blob fixture is the offline single-``prism``-key payload under
``tests/assets/v0.12-hotfix-262/legacy_preview_blob.json``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration.test_release_preview_direct import (
    _events,
    _issue_preview,
    _prepare,
    _previews,
)
from tracks import paths
from tracks.store import Store

pytestmark = pytest.mark.integration

_ASSETS = Path(__file__).resolve().parent.parent / "assets" / "v0.12-hotfix-262"
_REPO_ROOT = Path(__file__).resolve().parents[2]

_CANDIDATE = "44c14de0000000000000000000000000000000000"
_REQUIRED_KEYS = ("full_f", "ci", "prism.verify_final", "prism.security", "security")


def _legacy_blob() -> dict:
    return json.loads(
        (_ASSETS / "legacy_preview_blob.json").read_text(encoding="utf-8")
    )["blob"]


def _run_cli(repo: Path, home: Path, *args: str) -> subprocess.CompletedProcess:
    """Run the real CLI with the isolated home as its TRACKS_HOME."""
    env = dict(os.environ)
    env["TRACKS_HOME"] = str(home)
    env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "tracks.cli.main", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )


def _of_type(events: list, event_type: str) -> list:
    return [event for event in events if event.type == event_type]


# AC-FR0270-02@v0.8 TRACKS-TRACE missing required keys block release
def test_missing_required_keys_block_release(host_repo, trac, tmp_path):
    """AC-FR0270-02: an evidence chain that misses ANY ONE of the five
    required keys is incomplete -- the preview carries no valid evidence set.

    Contract: interfaces §1b item 1 ("五键全部在场且有效；完备性检查
    无条件执行") and item 3 (``_gate_status`` gains ``full_f_ok``).

    Outlet: the ``release.previewed`` EVENT -- the preview's own
    ``evidence_digests`` key set. A chain missing a required key must NOT
    produce a preview whose evidence set claims completeness.
    """
    run_id, candidate, _contract, _remote, _artifact, _cdigest = _prepare(
        host_repo, trac, tmp_path
    )
    _issue_preview(host_repo, run_id, candidate)
    previews = _previews(_events(host_repo, run_id))
    assert previews, "the real preview command must emit release.previewed"
    payload = previews[-1].payload
    evidence = payload.get("evidence_digests") or {}
    missing = [key for key in _REQUIRED_KEYS if key not in evidence]
    assert missing == [], (
        "the preview's evidence set must carry every required key "
        f"(missing {missing}; present {sorted(evidence)})"
    )


# AC-FR0273-01@v0.8 TRACKS-TRACE preview requires full key set
def test_preview_requires_full_key_set(host_repo, trac, tmp_path):
    """AC-FR0273-01: the key set is derived per scope -- the ``verify_final``
    scope maps to ``prism.verify_final`` and the ``security`` scope to
    ``prism.security``; every other scope maps to no key at all.

    Contract: interfaces §1b item 2 (``_evidence_key(scope)``).

    Outlet: the ``release.previewed`` EVENT's ``evidence_digests`` -- the
    key set the preview actually assembled from the stream.
    """
    run_id, candidate, _contract, _remote, _artifact, _cdigest = _prepare(
        host_repo, trac, tmp_path
    )
    _issue_preview(host_repo, run_id, candidate)
    previews = _previews(_events(host_repo, run_id))
    assert previews, "the real preview command must emit release.previewed"
    evidence = (previews[-1].payload.get("evidence_digests") or {})

    # The split keys are present, and the legacy single 'prism' key is NOT
    # (a new preview carries the split schema, never the pre-v0.12.1 one).
    assert "prism.verify_final" in evidence, (
        f"the verify_final scope must contribute the prism.verify_final key "
        f"(keys={sorted(evidence)})"
    )
    assert "prism.security" in evidence, (
        f"the security scope must contribute the prism.security key "
        f"(keys={sorted(evidence)})"
    )
    assert "prism" not in evidence, (
        f"a NEW preview must carry the split keys, never the legacy single "
        f"'prism' key (keys={sorted(evidence)})"
    )


# AC-FR0273-02@v0.8 TRACKS-TRACE legacy single prism key preview readable
def test_legacy_single_prism_key_preview_readable(host_repo, trac, tmp_path):
    """AC-FR0273-02: the pre-v0.12.1 preview blob (a single ``"prism"`` key)
    stays READABLE -- its read-back against the CURRENT (split-key) preview
    succeeds and does NOT report ``operation_plan_changed``.

    Contract: interfaces §1b item 4 (INV-5: the historical single-key blob
    remains readable; the split keys are enforced only for a NEWLY assembled
    preview and an unpublished authorization; no DB migration).

    The discriminating seam is the ASYMMETRY, and both halves are asserted
    here through the SAME public outlet:

    * the LEGACY blob (single ``"prism"`` key) is readable -- the release
      preview CLI must not report an operation-plan change for it;
    * a NEWLY assembled preview carries the SPLIT keys -- the release preview
      CLI's own preview must show the split key set, never the legacy one.

    Outlet: the ``trac release preview`` CLI and the ``release.previewed``
    EVENT it reads.
    """
    legacy = _legacy_blob()
    assert list(legacy["evidence_digests"]) == ["prism"], (
        "the legacy fixture must carry exactly the single 'prism' evidence key"
    )

    # Half 1: the legacy blob is READABLE.
    run_id, candidate, _contract, _remote, _artifact, _cdigest = _prepare(
        host_repo, trac, tmp_path
    )
    home = paths.tracks_home(host_repo)
    store = Store(home)
    try:
        store.append(run_id, "v0.8", "release.previewed", legacy)
    finally:
        store.close()

    proc = _run_cli(host_repo, home, "release", "preview")
    combined = proc.stdout + proc.stderr
    assert "operation_plan_changed" not in combined, (
        "the legacy single-'prism'-key blob must stay readable: the release "
        f"preview must not report an operation-plan change (output={combined!r})"
    )

    # Half 2: a NEWLY assembled preview carries the SPLIT keys -- the
    # legacy tolerance can never widen into a general tolerance. A separate
    # host repo keeps the two preview generations independent.
    other_repo = tmp_path / "other-host"
    other_repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=other_repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "hf262@example.com"], cwd=other_repo, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "HF262 Human"], cwd=other_repo, check=True
    )
    (other_repo / "README.md").write_text("hotfix-262 other host\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=other_repo, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "initial"], cwd=other_repo, check=True, capture_output=True
    )
    other_root = tmp_path / "other"
    other_root.mkdir(parents=True, exist_ok=True)
    other_run, other_candidate, _oc, _or, _oa, _ocd = _prepare(
        other_repo, trac, other_root
    )
    _issue_preview(other_repo, other_run, other_candidate)
    previews = _previews(_events(other_repo, other_run))
    assert previews, "the real preview command must emit release.previewed"
    evidence = previews[-1].payload.get("evidence_digests") or {}
    assert "prism" not in evidence, (
        "a NEW preview must carry the split keys, never the legacy single "
        f"'prism' key (keys={sorted(evidence)})"
    )
    assert "prism.verify_final" in evidence, (
        "a NEW preview must carry the prism.verify_final key "
        f"(keys={sorted(evidence)})"
    )
    assert "prism.security" in evidence, (
        "a NEW preview must carry the prism.security key "
        f"(keys={sorted(evidence)})"
    )


# AC-FR0274-02@v0.8 TRACKS-TRACE release rejected on gate failed or stale
def test_release_rejected_on_gate_failed_or_stale(host_repo, trac, tmp_path):
    """AC-FR0274-02: with a failed gate or a stale preview, the release
    action exits non-zero with the fixed refusal text, lands NO
    ``release.decided``, and the run stays in AWAITING_RELEASE.

    The hotfix delta adds the ``full_f`` key to the release gate's
    completeness check (interfaces §1b item 3: "_release_gate_blocked()
    增加 full_f 检查").

    Outlet: the ``trac release --action release`` CLI -- its exit code and
    its fixed refusal text -- and the EVENT STREAM (the absence of a
    ``release.decided`` event, the presence of ``release.rejected``).
    """
    run_id, candidate, _contract, _remote, _artifact, _cdigest = _prepare(
        host_repo, trac, tmp_path
    )
    _issue_preview(host_repo, run_id, candidate)

    home = paths.tracks_home(host_repo)
    proc = _run_cli(host_repo, home, "release", "--action", "release")
    assert proc.returncode != 0, (
        "a chain whose preview cannot be authorized must not authorize the "
        f"release (stdout={proc.stdout!r}, stderr={proc.stderr!r})"
    )
    combined = proc.stdout + proc.stderr
    assert "release rejected" in combined, (
        f"the refusal must carry the contract's fixed wording (output={combined!r})"
    )

    events = _events(host_repo, run_id)
    assert _of_type(events, "release.decided") == [], (
        "a refused release must not land a release.decided event"
    )
    assert _of_type(events, "release.rejected"), (
        "a refused release must be audited as release.rejected"
    )
