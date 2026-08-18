#!/usr/bin/env python3
"""Generate hotfix_fix_commit_leaks_to_main.patch for AC-FR0245-01."""
from __future__ import annotations
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CLI = REPO / "tracks" / "cli" / "main.py"
OUT = REPO / "tests" / "counterexamples" / "v0.6"

body = CLI.read_text(encoding="utf-8")

run_noop = '''def _cmd_run_noop(repo: Path, *args: str) -> int:
    """COUNTEREXAMPLE scaffolding: no-op `trac run` (returns 0)."""
    print("run: (noop)")
    return 0


'''

leak = '''def _cmd_hotfix_leak(repo: Path, *args: str) -> int:
    """COUNTEREXAMPLE AC-FR0245-01: creates fix/{issue} with RGR commits
    (trailers Tracks-Issue=42) BUT leaks the identical patch onto main
    (different parent -> different SHA, same patch-id) - violating
    topological isolation (fix commits must be absent from the active
    branch)."""
    import subprocess as _sp
    def g(*a):
        _sp.run(["git", *a], cwd=repo, check=True, capture_output=True)
    issue = args[0] if args else "42"
    g("checkout", "-b", f"fix/{issue}")
    (repo / "fix.txt").write_text("the fix\\n", encoding="utf-8")
    g("add", "fix.txt")
    g("commit", "-m", f"fix hotfix #{issue}\\n\\nTracks-Issue={issue}")
    g("checkout", "main")
    (repo / "PLACEHOLDER").write_text("x\\n", encoding="utf-8")
    g("add", "PLACEHOLDER")
    g("commit", "-m", "placeholder")
    (repo / "fix.txt").write_text("the fix\\n", encoding="utf-8")
    g("add", "fix.txt")
    g("commit", "-m", f"fix hotfix #{issue}\\n\\nTracks-Issue={issue}")
    g("checkout", f"fix/{issue}")
    print(f"run hotfix-{issue} hotfix.requested issue={issue} scenario=post-release")
    return 0


'''

new_body = body.replace('USAGE = (', run_noop + leak + 'USAGE = (', 1)
new_body = new_body.replace('"run": (cmd_run, None),', '"run": (_cmd_run_noop, None),', 1)
new_body = new_body.replace(
    '"check": (cmd_check, None),\n}',
    '"check": (cmd_check, None),\n    "hotfix": (_cmd_hotfix_leak, None),\n}',
    1)

CLI.write_text(new_body, encoding="utf-8")
diff = subprocess.run(
    ["git", "diff", "--no-color", "--", str(CLI)],
    cwd=REPO, capture_output=True, text=True, check=True,
).stdout
subprocess.run(["git", "checkout", "--", str(CLI)], cwd=REPO, check=True)
(OUT / "hotfix_fix_commit_leaks_to_main.patch").write_text(diff, encoding="utf-8")
print(f"wrote ({len(diff.splitlines())} lines)")