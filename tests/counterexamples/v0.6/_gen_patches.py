#!/usr/bin/env python3
"""Generate counterexample patches for v0.6 hotfix tests.

Each patch replaces a frozen stub's ``raise NotImplementedError(...)``
with a minimal contract-deviant implementation. Applying the patch lets
the bound test proceed past the stub to its assertions, which then fail
(killed) - proving the assertions have discriminating power.

Run from repo root: python3 tests/counterexamples/v0.6/_gen_patches.py
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HOTFIX = REPO / "tracks" / "executor" / "hotfix.py"
OUT = REPO / "tests" / "counterexamples" / "v0.6"


def make_patch(name: str, old: str, new: str) -> None:
    """Write `name`.patch by applying `old`->`new` to hotfix.py, git diff, revert."""
    body = HOTFIX.read_text(encoding="utf-8")
    assert old in body, f"old not found for {name}"
    HOTFIX.write_text(body.replace(old, new, 1), encoding="utf-8")
    diff = subprocess.run(
        ["git", "diff", "--no-color", "--", str(HOTFIX)],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    subprocess.run(["git", "checkout", "--", str(HOTFIX)], cwd=REPO, check=True)
    (OUT / f"{name}.patch").write_text(diff, encoding="utf-8")
    print(f"wrote {name}.patch ({len(diff.splitlines())} lines)")


# 1. precheck_hotfix returns pass without target_version (kills test_precheck_pass)
make_patch(
    "precheck_pass_status_no_target",
    '    raise NotImplementedError("IF-HOTFIX-003: precheck_hotfix")',
    '''    from tracks.executor.hotfix import PrecheckReport  # noqa: F401
    # COUNTEREXAMPLE: returns "pass" without target_version (contract requires it).
    return PrecheckReport(status="pass", reason=None, next=None,
                          target_version=None, active_branch=None)''',
)

# 2. precheck_hotfix returns rejected with bogus reason (kills test_precheck_rejection_matrix)
make_patch(
    "precheck_rejected_bogus_reason",
    '    raise NotImplementedError("IF-HOTFIX-003: precheck_hotfix")',
    '''    from tracks.executor.hotfix import PrecheckReport  # noqa: F401
    # COUNTEREXAMPLE: rejected with a reason NOT in the closed set.
    return PrecheckReport(status="rejected", reason="bogus_reason",
                          next="retry", target_version=None, active_branch=None)''',
)

# 3. parse_anchor_refs returns wrong tuple (kills test_sage_anchor_validated)
make_patch(
    "parse_anchor_refs_wrong_tuple",
    '    raise NotImplementedError("IF-HOTFIX-004: parse_anchor_refs")',
    '''    # COUNTEREXAMPLE: drops the @version, returns ("AC-FR0030-01", "")
    # instead of ("AC-FR0030-01", "v0.5") (contract: @version is required).
    return [(ac.split("@")[0], "") for ac in acs]''',
)

# 4. validate_anchor_refs returns all_exist=True on missing refs (kills test_anchor_invalid_redispatch)
make_patch(
    "validate_anchor_refs_false_pass",
    '    raise NotImplementedError("IF-HOTFIX-004: validate_anchor_refs")',
    '''    # COUNTEREXAMPLE: always returns (True, []) - claims all refs
    # exist even when acceptance.md has no such heading (contract: miss -> False).
    return (True, [])''',
)

# 5. complete_hotfix_entry returns wrong branch name (kills test_anchored_creates_isolated_fix_branch)
make_patch(
    "complete_hotfix_entry_wrong_branch",
    '    raise NotImplementedError("IF-HOTFIX-005: complete_hotfix_entry")',
    '''    # COUNTEREXAMPLE: returns "fix/wrong" instead of "fix/{issue}"
    # (contract: branch name must be fix/{issue}).
    return {"branch": "fix/wrong", "target_version": target_version}''',
)

# 6. HostIssue.is_bug returns True for non-bug (kills test_precheck_rejection_matrix via non-bug case)
make_patch(
    "hostissue_is_bug_always_true",
    '        raise NotImplementedError("IF-HOTFIX-003: HostIssue.is_bug")',
    '''        # COUNTEREXAMPLE: always True - non-bug issues pass PRECHECK P-1
        # (contract: is_bug iff "bug" in labels).
        return True''',
)

print("\nAll patches generated.")
