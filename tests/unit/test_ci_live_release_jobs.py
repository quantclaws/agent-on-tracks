"""CI workflow live/release job contract tests (FR-0233, T-017).

Unit-level RED tests pinning architecture.md §4.3 CI contract onto
``.github/workflows/ci.yml``: the independent opt-in ``live-opencode``
routine job and the milestone ``release-evidence`` hard gate. They parse
the workflow YAML as plain text (no third-party dependency — the project
ships zero runtime dependencies) and assert the documented contract
tokens, which the ci-skeleton does not yet define.

AC-FR0233-01@v0.5 routine credential-less skip is explicit (LIVE_SKIPPED),
AC-FR0233-02@v0.5 opt-in live journey runs the real e2e_live suite,
AC-FR0233-03@v0.5 release verification explicitly runs the release-evidence
check; fake/skip never substitutes.
"""

from __future__ import annotations

import re
from pathlib import Path

CI = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"

ROUTINE_CHECKS = ("lint", "coverage", "test", "deliverables", "trace", "reach")
LIVE_JOB = "live-opencode"
RELEASE_JOB = "release-evidence"
LIVE_SUITE = "tests/e2e_live/test_m_impl_release_evidence.py"
RELEASE_CHECK = "trac check release-evidence --json"
SKIP_TOKEN = "LIVE_SKIPPED: missing"


def _text() -> str:
    assert CI.exists(), f"missing CI workflow file: {CI}"
    return CI.read_text(encoding="utf-8")


def _job_blocks(text: str) -> dict[str, str]:
    """Map each top-level job id under ``jobs:`` to its indented block text."""
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    in_jobs = False
    for line in text.splitlines():
        if line == "jobs:":  # only keys after this line are jobs
            in_jobs = True
            current = None
            continue
        if not in_jobs:
            continue
        job = re.match(r"^  ([A-Za-z0-9][A-Za-z0-9-]*):\s*$", line)
        if job:  # a two-space-indented, colon-terminated key starts a job
            current = job.group(1)
            blocks[current] = []
        elif re.match(r"^\S", line):  # any column-0 line ends the jobs section
            current = None
        elif current is not None:
            blocks[current].append(line)
    return {name: "\n".join(lines) for name, lines in blocks.items()}


def _top_level_block(text: str, key: str) -> str:
    """Return the indented body of a top-level ``key:`` mapping."""
    grab = False
    out: list[str] = []
    for line in text.splitlines():
        if line == f"{key}:":
            grab = True
            continue
        if grab:
            if line and not line[0].isspace():
                break
            out.append(line)
    return "\n".join(out)


def _live_block() -> str:
    blocks = _job_blocks(_text())
    assert LIVE_JOB in blocks, (
        f"ci.yml must define the independent opt-in live job `{LIVE_JOB}` "
        "(architecture.md §4.3); jobs present: " + ", ".join(sorted(blocks))
    )
    return blocks[LIVE_JOB]


def _release_block() -> str:
    blocks = _job_blocks(_text())
    assert RELEASE_JOB in blocks, (
        f"ci.yml must define the milestone hard-gate job `{RELEASE_JOB}` "
        "(architecture.md §4.3); jobs present: " + ", ".join(sorted(blocks))
    )
    return blocks[RELEASE_JOB]


# AC-FR0233-02@v0.5 TRACKS-TRACE opt-in live job defined
def test_live_opencode_job_defined():
    """The independent, opt-in, non-required live job must exist as a job."""
    _live_block()  # asserts existence with a contract-aligned message


# AC-FR0233-02@v0.5 TRACKS-TRACE explicit workflow_dispatch opt-in run
def test_workflow_dispatch_trigger_present():
    """`workflow_dispatch` lets an operator explicitly opt into the live run."""
    on_block = _top_level_block(_text(), "on")
    assert "workflow_dispatch" in on_block, (
        "ci.yml `on:` must include workflow_dispatch for explicit opt-in live "
        "runs (architecture.md §4.3)"
    )


# AC-FR0233-02@v0.5 TRACKS-TRACE weekly schedule anti-rot run
def test_weekly_schedule_trigger_present():
    """A weekly schedule runs the real channel to prevent channel rot."""
    on_block = _top_level_block(_text(), "on")
    assert "schedule:" in on_block, "ci.yml `on:` must include a weekly schedule"
    assert "cron" in on_block, "the schedule must declare a cron expression"


# AC-FR0233-01@v0.5 TRACKS-TRACE credential-less skip is explicit
def test_live_skip_reported_on_missing_credentials():
    """Missing credentials must report LIVE_SKIPPED, never disguise as success."""
    block = _live_block()
    assert SKIP_TOKEN in block, (
        f"the live job must emit `{SKIP_TOKEN}` on missing credentials and must "
        "not produce success evidence (AC-FR0233-01, architecture.md §4.3)"
    )


# AC-FR0233-02@v0.5 TRACKS-TRACE opt-in live job runs the real journey
def test_live_job_runs_real_e2e_live_suite():
    """When opted in, the live job runs the real e2e_live journey, not a fake."""
    block = _live_block()
    assert LIVE_SUITE in block, (
        f"the live job must run the real `{LIVE_SUITE}` journey when "
        "credentials are present (AC-FR0233-02)"
    )


# AC-FR0233-02@v0.5 TRACKS-TRACE live channel receives and scopes secrets
def test_live_job_receives_secrets_and_does_not_leak_them():
    """Provider credentials are injected into the live job and nowhere else."""
    text = _text()
    blocks = _job_blocks(text)
    live = _live_block()  # asserts the live job exists
    assert "secrets." in live, (
        "the live job must reference provider secrets so it can run the real "
        "journey when present (architecture.md §4.3)"
    )
    for job, block in blocks.items():
        if job in (LIVE_JOB, RELEASE_JOB):
            continue
        assert "secrets." not in block, (
            f"secrets leaked into non-live job `{job}`; provider credentials "
            "must be injected only into live channel jobs (architecture.md §4.3)"
        )
    assert "secrets." not in _top_level_block(text, "env"), (
        "secrets must not live in top-level env; scope them to live jobs only"
    )


# AC-FR0233-01@v0.5 TRACKS-TRACE live job is not a routine required check
def test_live_job_is_not_a_routine_required_check():
    """Routine merge checks must not depend on the opt-in live job."""
    blocks = _job_blocks(_text())
    _live_block()  # ensure the job exists before asserting it stays optional
    for job in ROUTINE_CHECKS:
        needs = re.search(r"^\s*needs:.*$", blocks.get(job, ""), re.MULTILINE)
        if needs:
            assert LIVE_JOB not in needs.group(0), (
                f"routine required check `{job}` must not depend on the opt-in "
                "live job (AC-FR0233-01)"
            )


# AC-FR0233-03@v0.5 TRACKS-TRACE milestone release-evidence job defined
def test_release_evidence_job_defined():
    """The milestone hard-gate release-evidence job must exist."""
    _release_block()  # asserts existence with a contract-aligned message


# AC-FR0233-03@v0.5 TRACKS-TRACE release gate runs real live journey first
def test_release_evidence_runs_real_live_suite_first():
    """The milestone job runs the real live journey before checking evidence."""
    block = _release_block()
    assert LIVE_SUITE in block, (
        f"the release-evidence job must run the real `{LIVE_SUITE}` journey "
        "first; fake/simulated/skip cannot substitute (AC-FR0233-03)"
    )


# AC-FR0233-03@v0.5 TRACKS-TRACE release gate explicitly runs the check
def test_release_evidence_runs_explicit_check():
    """Release verification always explicitly runs trac check release-evidence."""
    block = _release_block()
    assert RELEASE_CHECK in block, (
        f"the release-evidence job must explicitly invoke `{RELEASE_CHECK}`; "
        "routine fake/skip results cannot replace it (AC-FR0233-03)"
    )


# AC-FR0233-03@v0.5 TRACKS-TRACE release gate fails closed
def test_release_evidence_job_is_a_hard_gate():
    """Missing credentials, skip, fake or stale must fail, not bypass."""
    block = _release_block()
    assert "continue-on-error" not in block, (
        "release-evidence must not use continue-on-error; it is a hard gate"
    )
    assert "|| true" not in block, (
        "release-evidence commands must not be bypassed with `|| true`"
    )


# AC-FR0233-03@v0.5 TRACKS-TRACE milestone DAG runs after routine checks
def test_release_evidence_runs_after_routine_checks():
    """The milestone job runs only after the routine required checks succeed."""
    block = _release_block()
    needs = re.search(r"^\s*needs:\s*\[?([^\n\]]*)", block, re.MULTILINE)
    assert needs, (
        "release-evidence must declare `needs:` on the routine required checks "
        "(architecture.md §4.3 milestone DAG)"
    )
    referenced = needs.group(1)
    missing = [job for job in ROUTINE_CHECKS if job not in referenced]
    assert not missing, (
        "release-evidence `needs:` must reference all routine required checks; "
        f"missing: {missing}"
    )
