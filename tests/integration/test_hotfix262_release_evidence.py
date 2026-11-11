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
  refusal text, and the absence of a ``release.decided`` event;
* the ``trac status`` CLI -- the operator-visible ``rejected:`` fragment and
  the retained ``AWAITING_RELEASE`` substate.

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


def _ineligible_full_f_payload() -> dict:
    """The recorded 5896-shape: an INELIGIBLE full execution at a FOREIGN commit.

    The shape is transcribed from interfaces §1a item 1 (INV-1): the
    authority needs ``passed`` AND ``full_f_eligible`` AND an authorized
    round AND the candidate binding AND non-empty ``evidence_ids`` AND a
    consistent identity quadruple.  This payload breaks the eligibility and
    the candidate binding, so the authority refuses it -- exactly what
    makes the ``full_f`` key absent from the release evidence set.
    """
    foreign = "ee781bf" + "0" * 33
    identity = {
        "tree": "tree-5896",
        "command": ["pytest"],
        "env": "env-5896",
        "selection_id": "sel-5896",
    }
    return {
        "round": "FULL_F",
        "passed": True,
        "full_f_eligible": False,
        "serves_as_full_f": True,
        "execution_commit": foreign,
        "evidence_ids": ["ev-5896-a"],
        "selection_id": identity["selection_id"],
        "identity": dict(identity),
        "identity_basis": [
            identity["tree"],
            list(identity["command"]),
            identity["env"],
            identity["selection_id"],
        ],
    }


def _new_host(tmp_path: Path, name: str = "host") -> Path:
    """An isolated host repo the ``trac`` fixture is NOT bound to.

    The shared ``trac`` fixture always runs its subprocess with
    ``cwd=host_repo`` -- the fixture's own repo -- so a second repository
    must be driven through its OWN CLI invocation (the same public CLI, with
    the second repo's TRACKS_HOME and cwd).  The legacy read-back case needs
    that independence: the two preview generations must not share a store.
    """
    repo = tmp_path / name
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "hf262@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "HF262 Human"], cwd=repo, check=True)
    (repo / "README.md").write_text("hotfix-262 other host\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "initial"], cwd=repo, check=True, capture_output=True
    )
    return repo


def _trac_for(repo: Path):
    """A ``trac``-shaped callable bound to *repo* (not to the fixture's repo)."""

    def run(*args: str, stdin=None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["TRACKS_HOME"] = str(paths.tracks_home(repo))
        env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run(
            [sys.executable, "-m", "tracks.cli.main", *args],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            input=stdin,
        )

    return run


def _legacy_generation(current_preview: dict, home: Path) -> dict:
    """The pre-v0.12.1 generation of a preview: the SAME inputs, but the
    evidence set recorded under the single pre-split ``"prism"`` key.

    The shape is the one interfaces §1b item 4 names: a blob persisted before
    v0.12.1 carried one ``"prism"`` digest.  It is derived from the run's
    OWN current preview (same candidate, same artifact, same plan) so the
    read-back exercises the KEY-SCHEMA difference alone -- no candidate
    drift, no plan change, no blob mismatch.
    """
    legacy = {key: value for key, value in current_preview.items() if key != "blob_ref"}
    split = current_preview.get("evidence_digests") or {}
    legacy["evidence_digests"] = {"prism": split["prism.security"]}
    blob = Store(home).write_audit_blob(legacy)
    assert blob is not None
    legacy["blob_ref"] = f".tracks/runtime/blobs/{blob}"
    return legacy


# AC-FR0270-02@v0.8 TRACKS-TRACE missing required keys block release
def test_missing_required_keys_block_release(host_repo, trac, tmp_path):
    """AC-FR0270-02: a chain whose CI evidence does not bind the candidate
    cannot authorize a release -- the ``ci`` key is one of the five
    required keys, so the incomplete evidence set blocks the release.

    Contract: interfaces §1b item 1 ("五键全部在场且有效；完备性检查
    无条件执行") and item 5 ("缺任一键 → preview invalid / release 命令
    ``error: release rejected — gate failed or preview stale``").

    Outlet: the ``release.previewed`` EVENT -- the preview's own
    ``evidence_digests`` key set -- plus the ``trac status`` CLI's
    ``ci=mismatch`` fragment and the ``trac release --action release`` CLI.

    The Arrange seeds the COMPLETE five-key chain (the shared premise), then
    adds a CI readback whose head SHA does NOT match the frozen candidate --
    the AC's own ``head SHA``/``candidate SHA`` mismatch clause.  The
    control half asserts the complete chain DOES assemble a preview carrying
    every required key, so a failure cannot be attributed to an arrangement
    that never assembles a preview at all.
    """
    run_id, candidate, _contract, _remote, _artifact, _cdigest = _prepare(
        host_repo, trac, tmp_path
    )
    home = paths.tracks_home(host_repo)

    # Control: the complete five-key chain assembles a preview.
    _issue_preview(host_repo, run_id, candidate)
    previews = _previews(_events(host_repo, run_id))
    assert previews, "the real preview command must emit release.previewed"
    complete = previews[-1].payload.get("evidence_digests") or {}
    missing_in_control = [key for key in _REQUIRED_KEYS if key not in complete]
    assert missing_in_control == [], (
        "the complete five-key chain must carry every required key "
        f"(missing {missing_in_control}; present {sorted(complete)})"
    )

    # The deviation: a CI readback bound to a FOREIGN head.  The CI
    # evidence no longer binds the candidate, so the ``ci`` key drops out of
    # the evidence set and the preview can no longer authorize a release.
    store = Store(home)
    try:
        store.append(
            run_id,
            "v0.8",
            "ci.run_observed",
            {
                "status": "failed",
                "repo": "local/preview-host",
                "workflow": "123",
                "run_id": 19,
                "head_sha": "f" * 40,
                "candidate_sha": candidate,
                "conclusion": "failure",
                "required_checks": ["required-ci"],
                "api_verified": False,
                "reason": "mismatch",
            },
        )
    finally:
        store.close()

    status = _run_cli(host_repo, home, "status")
    status_text = status.stdout + status.stderr
    assert "ci=mismatch" in status_text, (
        "a CI readback whose head SHA does not match the candidate must be "
        f"reported as a mismatch (status={status_text!r})"
    )

    proc = _run_cli(host_repo, home, "release", "--action", "release")
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        "an evidence chain missing a required key must not authorize the "
        f"release (stdout={proc.stdout!r}, stderr={proc.stderr!r})"
    )
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


# AC-FR0273-01@v0.8 TRACKS-TRACE preview requires full key set
def test_preview_requires_full_key_set(host_repo, trac, tmp_path):
    """AC-FR0273-01: the key set is derived per scope -- the ``verify_final``
    scope maps to ``prism.verify_final`` and the ``security`` scope to
    ``prism.security``; every other scope maps to no key at all.

    Contract: interfaces §1b item 2 (``_evidence_key(scope)``) and item 1
    (the five-key completeness set).

    Outlet: the ``release.previewed`` EVENT's ``evidence_digests`` -- the
    key set the preview actually assembled from the stream.
    """
    run_id, candidate, _contract, _remote, _artifact, _cdigest = _prepare(
        host_repo, trac, tmp_path
    )
    _issue_preview(host_repo, run_id, candidate)
    previews = _previews(_events(host_repo, run_id))
    assert previews, "the real preview command must emit release.previewed"
    evidence = previews[-1].payload.get("evidence_digests") or {}

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
    # The FULL_F key the hotfix adds is present too (interfaces §1b item 1).
    assert "full_f" in evidence, (
        f"the authoritative FULL_F proof must contribute the full_f key "
        f"(keys={sorted(evidence)})"
    )
    missing = [key for key in _REQUIRED_KEYS if key not in evidence]
    assert missing == [], (
        f"the new preview must carry every required key (missing {missing}; "
        f"present {sorted(evidence)})"
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

    The second half runs in its OWN host repo, driven through its OWN CLI
    invocation: the shared ``trac`` fixture always runs with the fixture's
    repo as its working directory, so a second repository must be driven
    through the same public CLI with the second repository's TRACKS_HOME --
    otherwise both preview generations would share one store and the
    asymmetry could not be observed at all.
    """
    legacy_fixture = _legacy_blob()
    assert list(legacy_fixture["evidence_digests"]) == ["prism"], (
        "the legacy fixture must carry exactly the single 'prism' evidence key"
    )

    # Half 1: the LEGACY generation of this run's own preview -- same
    # candidate, same artifact, same plan, recorded under the single
    # pre-split 'prism' key -- is READABLE.
    run_id, candidate, _contract, _remote, _artifact, _cdigest = _prepare(
        host_repo, trac, tmp_path
    )
    home = paths.tracks_home(host_repo)
    _issue_preview(host_repo, run_id, candidate)
    previews = _previews(_events(host_repo, run_id))
    assert previews, "the real preview command must emit release.previewed"
    legacy = _legacy_generation(previews[-1].payload, home)
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
    assert "status=awaiting_release" in combined, (
        "the legacy blob must read back as a readable (non-stale) preview "
        f"(output={combined!r})"
    )

    # Half 2: a NEWLY assembled preview carries the SPLIT keys -- the
    # legacy tolerance can never widen into a general tolerance. A separate
    # host repo keeps the two preview generations independent.
    other_repo = _new_host(tmp_path, "other-host")
    other_trac = _trac_for(other_repo)
    other_root = tmp_path / "other"
    other_root.mkdir(parents=True, exist_ok=True)
    other_run, other_candidate, _oc, _or, _oa, _ocd = _prepare(
        other_repo, other_trac, other_root
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
    ``release.decided`` event, the presence of ``release.rejected``), plus
    the ``trac status`` CLI's ``rejected:`` fragment.

    The Arrange: the complete five-key chain assembles a preview, and then
    an INELIGIBLE full execution at a FOREIGN commit lands after it.  The
    authority refuses that execution (interfaces §1a item 1), so the
    ``full_f`` key is no longer satisfied and the release gate refuses the
    decision.
    """
    run_id, candidate, _contract, _remote, _artifact, _cdigest = _prepare(
        host_repo, trac, tmp_path
    )
    _issue_preview(host_repo, run_id, candidate)
    home = paths.tracks_home(host_repo)

    store = Store(home)
    try:
        store.append(
            run_id, "v0.8", "full.executed", _ineligible_full_f_payload()
        )
    finally:
        store.close()

    proc = _run_cli(host_repo, home, "release", "--action", "release")
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        "a chain whose FULL_F evidence is no longer authoritative must not "
        f"authorize the release (stdout={proc.stdout!r}, stderr={proc.stderr!r})"
    )
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

    # The state stays AWAITING_RELEASE and the operator sees the refusal.
    status = _run_cli(host_repo, home, "status")
    status_text = status.stdout + status.stderr
    assert "substate=AWAITING_RELEASE" in status_text, (
        "a refused release must keep the run in AWAITING_RELEASE "
        f"(status={status_text!r})"
    )
    assert "security=passed" in status_text, (
        "the refusal must be attributable to the FULL_F key, not to a "
        f"security gate failure (status={status_text!r})"
    )
    assert "rejected: gate failed or preview stale" in status_text, (
        "the status must report the rejection (status="
        f"{status_text!r})"
    )
