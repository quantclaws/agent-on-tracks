"""Guard registry parity checker and deployment generator (IF-GUARD-002).

Implements three-way parity comparison between Runtime / pre-commit / CI
execution points of the same canonical quality guard registry, and generates
the host's ``.githooks/pre-commit`` and ``.github/workflows/ci.yml``
deployment files with consistent ``TRACKS_GUARD_REGISTRY`` digests.

- ``check_parity`` compares every guard entry's command against the three
  execution points (AC-FR0258-02, NFR-0141-01): argv0 is resolved using the
  same shared rule (``test_select._resolve_argv0``) but the remaining argv
  is compared exactly.  ``--exit-zero``, missing guards, and command/
  scope/threshold drift all produce ``GuardMismatch`` records that fail
  closed.
- ``deploy_guard_configs`` writes the hook and CI workflow, embeds
  ``TRACKS_GUARD_REGISTRY=<registry.digest>`` in both, and returns
  ``artifact_digests`` (sha256 of the generated output bytes, §1e).
"""

from __future__ import annotations

import hashlib
import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path

from tracks.executor.guard_registry import (
    GuardDeployment,
    GuardMismatch,
    GuardRegistry,
    ParityReport,
)
from tracks.executor.test_select import audit_no_concurrency_injection


def check_parity(
    registry: GuardRegistry,
    runtime_commands: Mapping[str, Sequence[str]],
    pre_commit_path: Path,
    ci_workflow_path: Path,
    cwd: Path,
) -> ParityReport:
    """Compare each guard entry across the three execution points.

    argv0 uses the shared resolution rule (``_resolve_argv0``) but the
    remaining argv is compared exactly.  ``--exit-zero``, missing guards, and
    command drift are all ``GuardMismatch`` records that fail closed.
    """
    mismatches: list[GuardMismatch] = []

    pre_commit_commands = _parse_shell_commands(pre_commit_path)
    ci_commands = _parse_ci_commands(ci_workflow_path)

    for entry in registry.entries:
        candidates = {"pre_commit": pre_commit_commands, "ci": ci_commands}
        rcmd = runtime_commands.get(entry.guard_id)
        if rcmd is None:
            rcmd = runtime_commands.get(entry.required_check)
        candidates["runtime"] = () if rcmd is None else (_command_argv(rcmd),)
        for place in entry.execution_points:
            expected = _command_argv(entry.command)
            if place == "pre_commit" and _is_hook_runner(entry):
                expected = ("set", "-e")
            mismatches.extend(_compare_against_candidates(
                place, entry.guard_id, expected, candidates.get(place, ()), cwd
            ))

    return ParityReport(
        registry_digest=registry.digest,
        runtime_match=not any(m.place == "runtime" for m in mismatches),
        pre_commit_match=not any(m.place == "pre_commit" for m in mismatches),
        ci_match=not any(m.place == "ci" for m in mismatches),
        mismatches=tuple(mismatches),
    )


def _exit_zero_mismatch(place: str, guard_id: str) -> list[GuardMismatch]:
    """A ``--exit-zero`` command is always a fail-closed mismatch
    (AC-FR0258-02), never silently accepted."""
    return [
        GuardMismatch(
            place=place, guard_id=guard_id, kind="exit_zero",
            detail=f"guard {guard_id} has --exit-zero in {place}",
        )
    ]


def _missing_mismatch(place: str, guard_id: str) -> list[GuardMismatch]:
    """A guard absent from one execution point is a fail-closed mismatch."""
    return [
        GuardMismatch(
            place=place, guard_id=guard_id, kind="missing",
            detail=f"guard {guard_id} not found in {place} commands",
        )
    ]


def _compare_against_candidates(
    place: str, guard_id: str, expected: tuple[str, ...],
    candidates: Sequence[Sequence[str]], cwd: Path,
) -> list[GuardMismatch]:
    """Compare one guard's command against all parsed candidates.

    Picks the best candidate: an exact match yields no mismatch; a candidate
    containing ``--exit-zero`` is a fail-closed mismatch; otherwise the guard
    is reported missing/drifted.
    """
    for candidate in candidates:
        try:
            ok = audit_no_concurrency_injection(list(expected), list(candidate), cwd)
        except Exception:
            ok = False
        if ok:
            return []  # exact match found

    # No exact match: check for exit-zero first
    for candidate in candidates:
        if any("--exit-zero" in arg for arg in candidate):
            return _exit_zero_mismatch(place, guard_id)

    # Drift or missing
    closest = next(iter(candidates), None)
    if closest is not None:
        return _compare_command(place, guard_id, expected, closest, cwd)
    return _missing_mismatch(place, guard_id)


def _compare_command(
    place: str,
    guard_id: str,
    expected: tuple[str, ...],
    actual: Sequence[str] | None,
    cwd: Path,
) -> list[GuardMismatch]:
    """Report a mismatch when the actual command does not equal the expected
    (argv0 resolved, rest exact).  ``None`` actual -> missing.
    ``--exit-zero`` in the actual command is always a fail-closed mismatch
    (AC-FR0258-02), regardless of argv equality."""
    if actual is None:
        return _missing_mismatch(place, guard_id)
    # --exit-zero is always a fail-closed mismatch, never silently accepted
    if any("--exit-zero" in arg for arg in actual):
        return _exit_zero_mismatch(place, guard_id)
    try:
        ok = audit_no_concurrency_injection(list(expected), list(actual), cwd)
    except Exception:
        ok = False
    if not ok:
        return [
            GuardMismatch(
                place=place,
                guard_id=guard_id,
                kind="command_drift",
                detail=f"guard {guard_id} command differs in {place} "
                f"(expected {expected}, got {actual})",
            )
        ]
    return []


def _parse_shell_commands(path: Path) -> tuple[tuple[str, ...], ...]:
    """Parse a shell script into argv tuples (one per non-comment, non-empty
    shell line)."""
    commands: list[tuple[str, ...]] = []
    if not path.exists():
        return tuple(commands)
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        argv = tuple(shlex.split(stripped))
        if argv:
            commands.append(argv)
    return tuple(commands)


def _parse_ci_commands(path: Path) -> tuple[tuple[str, ...], ...]:
    """Parse a CI workflow into argv tuples from ``run:`` lines."""
    commands: list[tuple[str, ...]] = []
    if not path.exists():
        return tuple(commands)
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("run:"):
            cmd_part = stripped[len("run:"):].strip()
            if cmd_part:
                argv = tuple(shlex.split(cmd_part))
                commands.append(argv)
    return tuple(commands)


def _command_argv(command) -> tuple[str, ...]:
    """Normalize either registry command representation."""
    argv = shlex.split(command) if isinstance(command, str) else list(command)
    if not argv or any(not isinstance(arg, str) for arg in argv):
        raise ValueError("guard command must contain a nonempty argv")
    return tuple(argv)


def _shell_command(command) -> str:
    return shlex.join(_command_argv(command))


def _is_hook_runner(entry) -> bool:
    return (entry.category == "hooks_runner_ci_required_checks"
            and _command_argv(entry.command) == ("sh", ".githooks/pre-commit"))


def deploy_guard_configs(
    registry: GuardRegistry, target_repo: Path
) -> GuardDeployment:
    """Write the host's pre-commit hook and CI workflow.

    Both files embed ``TRACKS_GUARD_REGISTRY=<registry.digest>`` so the
    three-way deployment record / hook / CI all carry the same digest
    (AC-FR0258-03).  ``artifact_digests`` are sha256 over the generated
    output bytes (interfaces §1e).
    """
    hook_dir = target_repo / ".githooks"
    hook_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hook_dir / "pre-commit"

    ci_dir = target_repo / ".github" / "workflows"
    ci_dir.mkdir(parents=True, exist_ok=True)
    ci_path = ci_dir / "ci.yml"

    # Pre-commit hook
    hook_lines = [
        "#!/bin/sh",
        "set -e",
        f"# TRACKS_GUARD_REGISTRY={registry.digest}",
        "",
    ]
    for entry in registry.entries:
        if "pre_commit" in entry.execution_points and not _is_hook_runner(entry):
            hook_lines.append(_shell_command(entry.command))

    hook_content = "\n".join(hook_lines) + "\n"
    hook_path.write_text(hook_content, encoding="utf-8")
    hook_path.chmod(0o755)

    # CI workflow
    ci_lines = [
        "name: Quality Guard",
        "on: [push, pull_request]",
        "jobs:",
        "  quality:",
        "    runs-on: ubuntu-latest",
        "    env:",
        f"      TRACKS_GUARD_REGISTRY: {registry.digest}",
        "    steps:",
        "      - uses: actions/checkout@v4",
    ]
    for entry in registry.entries:
        if "ci" not in entry.execution_points:
            continue
        ci_lines.append(f"      - name: {entry.guard_id}")
        ci_lines.append(f"        run: {_shell_command(entry.command)}")

    ci_content = "\n".join(ci_lines) + "\n"
    ci_path.write_text(ci_content, encoding="utf-8")

    # Artifact digests
    artifact_digests = {
        ".githooks/pre-commit": hashlib.sha256(hook_content.encode("utf-8")).hexdigest(),
        ".github/workflows/ci.yml": hashlib.sha256(ci_content.encode("utf-8")).hexdigest(),
    }

    return GuardDeployment(
        registry_digest=registry.digest,
        pre_commit_path=hook_path,
        ci_workflow_path=ci_path,
        artifact_digests=artifact_digests,
    )
