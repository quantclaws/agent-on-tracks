#!/usr/bin/env python3
"""Generate combined counterexample patches for test 3.
A correct IF-HOTFIX-004 validate_anchor_refs is scaffolding (non-deviant);
the deviation is the cmd_hotfix handler's bad_anchor->redispatch behavior."""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
EXEC = REPO / "tracks" / "executor" / "hotfix.py"
CLI = REPO / "tracks" / "cli" / "main.py"
OUT = REPO / "tests" / "counterexamples" / "v0.6"


def make_combined(name: str, extra: str, handler: str) -> None:
    # Part A: implement validate_anchor_refs correctly (contract-conforming scaffolding).
    ex = EXEC.read_text(encoding="utf-8")
    ex_new = ex.replace(
        '    raise NotImplementedError("IF-HOTFIX-004: validate_anchor_refs")',
        '''    # Scaffolding (contract-conforming): every (ac_id, version) must
    # resolve to an existing projects_dir/<version>/acceptance.md
    # containing a "### <ac_id>" heading.
    import re as _re
    missing = []
    for ac_id, version in refs:
        acc = projects_dir / version / "acceptance.md"
        if not acc.exists() or not _re.search(r"^### " + _re.escape(ac_id) + r"\\s*$",
                                              acc.read_text(encoding="utf-8"), _re.M):
            missing.append(f"{ac_id}@{version} not in .tracks/projects/{version}/acceptance.md")
    return (not missing, missing)''',
        1,
    )
    EXEC.write_text(ex_new, encoding="utf-8")

    # Part B: register deviant cmd_hotfix + no-op cmd_run.
    cli = CLI.read_text(encoding="utf-8")
    run_noop = '''def _cmd_run_noop(repo: Path, *args: str) -> int:
    """COUNTEREXAMPLE scaffolding: no-op `trac run` (returns 0)."""
    print("run: (noop)")
    return 0


'''
    cli_new = cli.replace('USAGE = (', run_noop + extra + 'USAGE = (', 1)
    cli_new = cli_new.replace('"run": (cmd_run, None),', '"run": (_cmd_run_noop, None),', 1)
    cli_new = cli_new.replace(
        '"check": (cmd_check, None),\n}',
        f'"check": (cmd_check, None),\n    "hotfix": ({handler}, None),\n}}',
        1)
    CLI.write_text(cli_new, encoding="utf-8")

    diff_exec = subprocess.run(
        ["git", "diff", "--no-color", "--", str(EXEC)],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    diff_cli = subprocess.run(
        ["git", "diff", "--no-color", "--", str(CLI)],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    subprocess.run(["git", "checkout", "--", str(EXEC), str(CLI)], cwd=REPO, check=True)
    (OUT / f"{name}.patch").write_text(
        diff_exec + "\n" + diff_cli, encoding="utf-8"
    )
    print(f"wrote {name}.patch (exec={len(diff_exec.splitlines())} cli={len(diff_cli.splitlines())})")


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

make_combined("hotfix_anchor_no_redispatch", no_redispatch, "_cmd_hotfix_no_redispatch")
make_combined("hotfix_anchor_auto_feature_backlog", auto_feature, "_cmd_hotfix_auto_feature")
print("\nDone.")
