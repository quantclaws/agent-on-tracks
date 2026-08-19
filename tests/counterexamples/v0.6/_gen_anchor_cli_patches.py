#!/usr/bin/env python3
"""Regenerate hotfix_anchor_no_redispatch.patch and hotfix_anchor_auto_feature_backlog.patch
as CLI-only mutations (drop the stale validate_anchor_refs stub hunk).

The original patches included a hotfix.py hunk that replaced the stub body of
validate_anchor_refs — that stub is now implemented (M-IMPL GREEN), so the hunk
no longer applies. The CLI handler part (registering cmd_hotfix + no-op cmd_run)
is still valid against the current tree.

Run from repo root: python3 tests/counterexamples/v0.6/_gen_anchor_cli_patches.py
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CLI = REPO / "tracks" / "cli" / "main.py"
OUT = REPO / "tests" / "counterexamples" / "v0.6"


def make_patch(name: str, edits: list[tuple[str, str]]) -> None:
    """Write `name`.patch by applying `edits` (old->new) to cli/main.py, git diff, revert."""
    body = CLI.read_text(encoding="utf-8")
    for old, new in edits:
        assert old in body, f"old string not found in {CLI} for {name}: {old!r}"
        body = body.replace(old, new, 1)
    CLI.write_text(body, encoding="utf-8")
    diff = subprocess.run(
        ["git", "diff", "--no-color", "--", str(CLI)],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    subprocess.run(["git", "checkout", "--", str(CLI)], cwd=REPO, check=True)
    (OUT / f"{name}.patch").write_text(diff, encoding="utf-8")
    print(f"wrote {name}.patch ({len(diff.splitlines())} lines)")


# 1. hotfix_anchor_no_redispatch: cmd_hotfix emits only 1 verdict.failed plus a
#    no-op cmd_run (scaffolding). Kill target: test_anchor_invalid_redispatch.
make_patch(
    "hotfix_anchor_no_redispatch",
    [
        # (a) insert handler functions before USAGE
        (
            '    return _err("usage: trac check <deliverables|trace|reach|release-evidence>")',
            '''    return _err("usage: trac check <deliverables|trace|reach|release-evidence>")


def _cmd_run_noop(repo: Path, *args: str) -> int:
    """COUNTEREXAMPLE scaffolding: no-op `trac run` (returns 0)."""
    print("run: (noop)")
    return 0


def _cmd_hotfix_no_redispatch(repo: Path, *args: str) -> int:
    """COUNTEREXAMPLE AC-FR0240-05: with bad_anchor the entry emits only
    ONE verdict.failed(anchor_invalid, attempt=1) and parks — never drives
    the 3-attempt redispatch sequence required by IF-HOTFIX-002."""
    from tracks import paths
    from tracks.store.store import Store
    store = Store(paths.tracks_home(repo))
    issue = args[0] if args else "42"
    run_id = f"hotfix-{issue}"
    store.append(run_id=run_id, version="v0.5", type="hotfix.requested",
                 payload={"issue": int(issue), "scenario": "post-release"})
    store.append(run_id=run_id, version="v0.5", type="verdict.failed",
                 payload={"check": "anchor_invalid", "attempt": 1})
    store.close()
    print(f"run {run_id} hotfix.requested issue={issue} scenario=post-release")
    print(f"run {run_id}: awaiting=awaiting_human origin=hotfix-triage issue={issue}")
    return 0''',
        ),
        # (b) replace cmd_run registration with the no-op scaffolding
        (
            '    "run": (cmd_run, None),',
            '    "run": (_cmd_run_noop, None),',
        ),
        # (c) register the hotfix command
        (
            '    "check": (cmd_check, None),\n}',
            '    "check": (cmd_check, None),\n    "hotfix": (_cmd_hotfix_no_redispatch, None),\n}',
        ),
    ],
)

# 2. hotfix_anchor_auto_feature_backlog: cmd_hotfix emits 3x verdict.failed AND
#    backlog.recorded (auto feature route, violating no-auto-feature rule).
make_patch(
    "hotfix_anchor_auto_feature_backlog",
    [
        (
            '    return _err("usage: trac check <deliverables|trace|reach|release-evidence>")',
            '''    return _err("usage: trac check <deliverables|trace|reach|release-evidence>")


def _cmd_run_noop(repo: Path, *args: str) -> int:
    """COUNTEREXAMPLE scaffolding: no-op `trac run` (returns 0)."""
    print("run: (noop)")
    return 0


def _cmd_hotfix_auto_feature(repo: Path, *args: str) -> int:
    """COUNTEREXAMPLE NFR-0100-03 / AC-FR0240-05: with bad_anchor the entry
    emits 3x verdict.failed AND auto-routes to FEATURE_ROUTE via
    backlog.recorded — violating the no-auto-feature-on-exhaustion rule."""
    from tracks import paths
    from tracks.store.store import Store
    store = Store(paths.tracks_home(repo))
    issue = args[0] if args else "42"
    run_id = f"hotfix-{issue}"
    store.append(run_id=run_id, version="v0.5", type="hotfix.requested",
                 payload={"issue": int(issue), "scenario": "post-release"})
    for attempt in (1, 2, 3):
        store.append(run_id=run_id, version="v0.5", type="verdict.failed",
                     payload={"check": "anchor_invalid", "attempt": attempt})
    store.append(run_id=run_id, version="v0.5", type="backlog.recorded",
                 payload={"issue": int(issue), "decision": "feature_route"})
    store.append(run_id=run_id, version="v0.5", type="run.completed",
                 payload={"terminal_state": "feature_route"})
    store.close()
    print(f"run {run_id} hotfix.requested issue={issue} scenario=post-release")
    print(f"run {run_id}: awaiting=awaiting_human origin=hotfix-triage issue={issue}")
    return 0''',
        ),
        (
            '    "run": (cmd_run, None),',
            '    "run": (_cmd_run_noop, None),',
        ),
        (
            '    "check": (cmd_check, None),\n}',
            '    "check": (cmd_check, None),\n    "hotfix": (_cmd_hotfix_auto_feature, None),\n}',
        ),
    ],
)

print("\nAll CLI anchor patches regenerated.")
