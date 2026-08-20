#!/usr/bin/env python3
"""Generate hotfix counterexample patches for test 2 (stale), 3 (no redispatch), 4 (auto feature)."""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CLI = REPO / "tracks" / "cli" / "main.py"
OUT = REPO / "tests" / "counterexamples" / "v0.6"


def make_patch(name: str, extra: str, handler: str) -> None:
    body = CLI.read_text(encoding="utf-8")
    run_noop = '''def _cmd_run_noop(repo: Path, *args: str) -> int:
    """COUNTEREXAMPLE scaffolding: no-op `trac run` (returns 0)."""
    print("run: (noop)")
    return 0


'''
    new_body = body.replace('\nUSAGE = (\n', '\n' + run_noop + extra + '\nUSAGE = (\n', 1)
    new_body = new_body.replace('"run": (cmd_run, None),', '"run": (_cmd_run_noop, None),', 1)
    new_body = new_body.replace(
        '"check": (cmd_check, None),\n}',
        f'"check": (cmd_check, None),\n    "hotfix": ({handler}, None),\n}}',
        1)
    CLI.write_text(new_body, encoding="utf-8")
    diff = subprocess.run(
        ["git", "diff", "--no-color", "--", str(CLI)],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    subprocess.run(["git", "checkout", "--", str(CLI)], cwd=REPO, check=True)
    (OUT / f"{name}.patch").write_text(diff, encoding="utf-8")
    print(f"wrote {name}.patch ({len(diff.splitlines())} lines)")


# Patch 2: baseline always fresh (never stale)
never_stale = '''def _cmd_hotfix_never_stale(repo: Path, *args: str) -> int:
    """COUNTEREXAMPLE AC-FR0245-02: hotfix dev run whose BASELINE always
    reports baseline.frozen(status="fresh") - never stale, even when the
    active release branch advances."""
    from tracks import paths
    from tracks.store.store import Store
    store = Store(paths.tracks_home(repo))
    issue = args[0] if args else "50"
    run_id = f"hotfix-{issue}"
    store.append(run_id=run_id, version="v0.6", type="hotfix.requested",
                 payload={"issue": int(issue), "scenario": "dev"})
    store.append(run_id=run_id, version="v0.6", type="triage.prechecked",
                 payload={"issue": int(issue), "scenario": "dev", "status": "pass",
                          "target_version": "v0.6"})
    store.append(run_id=run_id, version="v0.6", type="stage.entered",
                 payload={"stage": "M-IMPL"})
    store.append(run_id=run_id, version="v0.6", type="baseline.frozen",
                 payload={"status": "fresh", "scenario_branch_head": "x"})
    store.close()
    print(f"run {run_id} hotfix.requested issue={issue} scenario=dev")
    return 0


'''

make_patch("hotfix_baseline_fresh_never_stale", never_stale, "_cmd_hotfix_never_stale")

# Patch 3: no redispatch (only 1 verdict.failed)
no_redispatch = '''def _cmd_hotfix_no_redispatch(repo: Path, *args: str) -> int:
    """COUNTEREXAMPLE AC-FR0240-05: with sage:SAGE_TRIAGE=bad_anchor the
    entry emits only ONE verdict.failed(anchor_invalid, attempt=1) and
    parks - never drives the 3-attempt redispatch sequence."""
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
    return 0


'''

make_patch("hotfix_anchor_no_redispatch", no_redispatch, "_cmd_hotfix_no_redispatch")

# Patch 4: auto feature backlog (verdict.failed + backlog.recorded)
auto_feature = '''def _cmd_hotfix_auto_feature(repo: Path, *args: str) -> int:
    """COUNTEREXAMPLE NFR-0100-03 / AC-FR0240-05: with bad_anchor the entry
    emits 3x verdict.failed AND auto-routes to FEATURE_ROUTE
    (backlog.recorded) - violating the no-auto-feature-on-exhaustion rule."""
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
    return 0


'''

make_patch("hotfix_anchor_auto_feature_backlog", auto_feature, "_cmd_hotfix_auto_feature")

print("\nDone.")
