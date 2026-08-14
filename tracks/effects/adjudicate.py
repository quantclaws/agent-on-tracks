"""Over-reach adjudication: dispatch the reviewer to arbitrate (FR-0120).

When the auditor detects over-reach, the reviewer for that role decides
whether the writes are a legitimate scope extension (e.g., a new test
directory that project.toml should have declared) or genuine over-reach
(e.g., modifying product code). This replaces the old mechanical rollback
that caused infinite retry loops when the authorization config was stale.
"""

from __future__ import annotations

import re

# Who reviews whom (mirrors machine.py StageDef reviewer mapping).
_REVIEWER_FOR = {
    "archer": "prism",
    "devon": "prism",
    "shield": "prism",
    "sage": "lex",
    "scribe": "sage",
}


def reviewer_for(role: str) -> str | None:
    """Return the reviewer agent name for a given role, or None."""
    return _REVIEWER_FOR.get(role)


def parse_adjudication(stdout: str) -> tuple[bool, str]:
    """Parse reviewer's ALLOW/DENY verdict from stdout text."""
    for line in stdout.splitlines():
        m = re.match(r"\s*(ALLOW|DENY)\s*:\s*(.*)", line, re.IGNORECASE)
        if m:
            return m.group(1).upper() == "ALLOW", m.group(2).strip()
    return False, "no ALLOW/DENY verdict found"


def adjudicate(backend, role: str, over_paths: list[str]) -> bool:
    """Dispatch reviewer to adjudicate over-reach. Returns True if allowed.

    Fail-closed: any error (missing reviewer, dispatch failure, unparseable
    verdict) returns False, so the auditor proceeds with rollback.
    """
    reviewer = reviewer_for(role)
    if reviewer is None:
        return False
    prompt = (
        f"You are the reviewer for {role}. The auditor flagged over-reach: "
        f"{role} wrote to paths outside its allowed scope.\n\n"
        f"Over-reach paths:\n  " + "\n  ".join(over_paths) + "\n\n"
        "Decide: are these writes a legitimate scope extension (e.g., a new "
        "test directory that project.toml should have declared), or genuine "
        "over-reach (e.g., modifying product code)?\n\n"
        "Reply with exactly one line:\n"
        "  ALLOW: <one-sentence reason>\n"
        "or\n"
        "  DENY: <one-sentence reason>"
    )
    try:
        proc = backend._run(reviewer, prompt)
        allowed, _ = parse_adjudication(proc.stdout or "")
        return allowed
    except Exception:
        return False
