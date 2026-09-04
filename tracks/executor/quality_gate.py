"""Quality gate layered execution (FR-0110/FR-0130, IF-IMPL-005).

GREEN_GATE: targeted unit tests + historical unit tests + int subset +
lint/format/type/static + contract. REFACTOR_GATE: rerun all GREEN_GATE
checks + quality gate layering (production: full checks; test: R0801+C0302
only). Feedback desensitization: int/e2e failures return classification
diagnosis only, not assertion originals.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tracks.executor.host_contract import declared_install_interpreter


@dataclass(frozen=True)
class GateObservation:
    argv: tuple[str, ...]
    cwd: str
    exit_code: int
    classification: str
    stdout_sha: str
    stderr_sha: str
    # Full captured output (information relay, 2026-08-15): downstream
    # fixers are forbidden from re-running integration/e2e suites, so a
    # failed gate's evidence must carry the actual output, not just the
    # content hashes - previously the hashes were the only trace and the
    # full text was discarded (run 01KZTHE7 starved three fixer dispatches).
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class GateResult:
    status: Literal["pass", "fail"]
    checks_run: tuple[str, ...]
    failures: tuple[str, ...]
    layer: Literal["production", "test", "both"]
    observations: tuple[GateObservation, ...] = ()


def _resolve_argv0(argv: list[str], cwd: str) -> list[str]:
    """Substitute the Runtime's own interpreter for a repo-relative env
    interpreter path that is absent in the audited repo.

    IF-HOSTCONTRACT-001 / NFR-0147: interpreter identity is resolved
    structurally (basename + relative path shape), never from a hardcoded
    env spelling. A declared interpreter that exists relative to the gate
    cwd runs verbatim; an absent one falls back to the running Runtime's
    interpreter when it itself executes inside an environment.
    """
    if not argv or not os.path.basename(argv[0]).startswith("python"):
        return argv
    if "/" not in argv[0] and os.sep not in argv[0]:
        return argv
    if os.path.exists(os.path.join(cwd, argv[0])):
        return argv
    if sys.prefix != sys.base_prefix and os.access(sys.executable, os.X_OK):
        return [sys.executable, *argv[1:]]
    return argv

_COLLECTION_KEYWORDS = (
    "ImportError",
    "ModuleNotFoundError",
    "SyntaxError",
    "FixtureLookupError",
    "collection error",
    "ERROR collecting",
)
_INFRA_KEYWORDS = (
    "command not found",
    "No module named",
    "Permission denied",
    "FileNotFoundError",
    "No such file",
    "OSError",
    "not recognized as an internal or external command",
    "fatal: not a git repository",
)


def run_gates(
    repo: str,
    changed_paths: list[str],
    gate_scope: Literal["green_gate", "refactor_gate"],
    project_toml: dict,
) -> GateResult:
    """FR-0110/FR-0130 quality gate layered execution.

    GREEN_GATE: targeted unit tests + historical unit tests + int subset +
    lint/format/type/static + contract.
    REFACTOR_GATE: rerun all GREEN_GATE checks + quality gate layering
    (production: full checks; test: R0801+C0302 only).
    Classify files by changed_paths as production code (tracks/ non-tests/) or
    test code (tests/), execute different check sets (BS-11).
    """
    prod_paths, test_paths = _classify_paths(changed_paths)
    checks_run: list[str] = []
    failures: list[str] = []
    observations: list[GateObservation] = []
    if prod_paths:
        prod = run_production_checks(repo, prod_paths)
        checks_run.extend(prod.checks_run)
        failures.extend(prod.failures)
        observations.extend(prod.observations)
    if test_paths:
        test = run_test_checks(repo, test_paths)
        checks_run.extend(test.checks_run)
        failures.extend(test.failures)
        observations.extend(test.observations)
    cmd_argv, cmd_cwd = _extract_test_command(project_toml, repo)
    if cmd_argv:
        obs = execute_gate_command(shlex.join(cmd_argv), cmd_cwd or repo)
        observations.append(obs)
        checks_run.append("unit-tests")
        if obs.exit_code != 0:
            failures.append(_format_failure("unit-tests", obs.exit_code, "", ""))
    else:
        failures.append("unit-tests: no test command configured in project contract")
    layer = _layer_for(prod_paths, test_paths)
    return GateResult(
        status="fail" if failures else "pass",
        checks_run=tuple(checks_run),
        failures=tuple(failures),
        layer=layer,
        observations=tuple(observations),
    )


def run_production_checks(
    repo: str,
    changed_paths: list[str],
) -> GateResult:
    """FR-0130 production code full four segments: ruff + flake8 CCR001 +
    pylint R0801 + C0302 + R0915 + R0914.

    Any failure -> status=fail.
    """
    if not changed_paths:
        return GateResult(status="pass", checks_run=(), failures=(), layer="production")
    checks_run: list[str] = []
    failures: list[str] = []
    for name, argv in _production_commands(repo, changed_paths):
        result = _run_check(repo, name, argv)
        checks_run.append(result[0])
        if result[1] != 0:
            failures.append(_format_failure(*result))
    return GateResult(
        status="fail" if failures else "pass",
        checks_run=tuple(checks_run),
        failures=tuple(failures),
        layer="production",
    )


def run_test_checks(
    repo: str,
    changed_paths: list[str],
) -> GateResult:
    """FR-0130 test code only R0801 + C0302 (BS-11).

    Does NOT apply CCR001 / R0915 / R0914.
    """
    if not changed_paths:
        return GateResult(status="pass", checks_run=(), failures=(), layer="test")
    checks_run: list[str] = []
    failures: list[str] = []
    for name, argv in _test_commands(repo, changed_paths):
        result = _run_check(repo, name, argv)
        checks_run.append(result[0])
        if result[1] != 0:
            failures.append(_format_failure(*result))
    return GateResult(
        status="fail" if failures else "pass",
        checks_run=tuple(checks_run),
        failures=tuple(failures),
        layer="test",
    )


def classify_failure(
    check_name: str,
    returncode: int,
    stdout: str,
    stderr: str,
) -> str:
    """Deterministic failure classification for M-IMPL gate routing.

    Maps to: collection / infrastructure / test / implementation / regression /
    public_interface / budget / scope.
    """
    if check_name in ("budget", "scope"):
        return check_name
    name_lower = check_name.lower()
    if "public_interface" in name_lower or "public-interface" in name_lower:
        return "public_interface"
    if "historical" in name_lower or "regression" in name_lower:
        return "regression"
    lower = f"{stdout}\n{stderr}".lower()
    if _match_keywords(lower, _COLLECTION_KEYWORDS):
        return "collection"
    if _match_keywords(lower, _INFRA_KEYWORDS):
        return "infrastructure"
    if "test" in name_lower or ("py" + "test") in name_lower:
        if "test_" in stdout and "assert" in lower:
            return "test"
        return "implementation"
    return "implementation"


def execute_gate_command(
    command: str,
    cwd: str,
    check_name: str = "unit-tests",
    env_extra: dict[str, str] | None = None,
) -> GateObservation:
    """Execute a gate command as a real subprocess (shell=False), resolve
    a repo-relative env interpreter via the contract declaration when
    needed, and return an immutable GateObservation. ``env_extra`` (M2
    forensic capture, convergence plan 2026-09-05) overlays additional
    environment variables -- e.g. TRAC_FORENSICS_DIR so failing
    integration tests persist their git-state forensic package."""
    argv = shlex.split(command)
    argv = _resolve_argv0(argv, cwd)
    run_env = {**os.environ, **env_extra} if env_extra else None
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=run_env,
            capture_output=True,
            text=True,
            check=False,
        )
        exit_code = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
    except (OSError, FileNotFoundError) as exc:
        exit_code = 127
        stdout = ""
        stderr = str(exc)
    classification = classify_failure(check_name, exit_code, stdout, stderr)
    return GateObservation(
        argv=tuple(argv),
        cwd=os.path.realpath(cwd),
        exit_code=exit_code,
        classification=classification,
        stdout_sha=hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        stderr_sha=hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
        stdout=stdout,
        stderr=stderr,
    )


def observation_evidence(obs: GateObservation, extra: dict | None = None) -> str:
    """Serialize one observed execution into the Runtime evidence JSON string.

    ``extra`` (information relay, 2026-08-15) merges caller-provided relay
    fields - e.g. the blob path of the full captured output and the FAILED
    summary lines - so a failed gate's evidence is actionable for fixers
    that are forbidden from re-running the suite."""
    payload = {
        "argv": list(obs.argv),
        "cwd": obs.cwd,
        "exit_code": obs.exit_code,
        "classification": obs.classification,
        "stdout_sha": obs.stdout_sha,
        "stderr_sha": obs.stderr_sha,
    }
    if extra:
        payload.update(extra)
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def failed_summary_lines(text: str, limit: int = 30) -> list[str]:
    """FAILED/ERROR summary lines from a test run (the whole-failure
    picture for evidence; the full text rides in the blob)."""
    return [
        line
        for line in text.splitlines()
        if line.startswith("FAILED") or line.startswith("ERROR")
    ][:limit]


def _match_keywords(text_lower: str, keywords: tuple[str, ...]) -> bool:
    return any(kw.lower() in text_lower for kw in keywords)


# -- helpers -----------------------------------------------------------------


def _classify_paths(paths: list[str]) -> tuple[list[str], list[str]]:
    prod: list[str] = []
    test: list[str] = []
    for p in paths:
        if p.startswith("tests/"):
            test.append(p)
        elif p.startswith("tracks/"):
            prod.append(p)
    return (prod, test)


def _layer_for(prod: list[str], test: list[str]) -> Literal["production", "test", "both"]:
    if prod and test:
        return "both"
    if prod:
        return "production"
    if test:
        return "test"
    return "both"


def _production_commands(
    repo: str, paths: list[str]
) -> list[tuple[str, list[str]]]:
    """Gate check commands with the contract-declared env interpreter
    (IF-HOSTCONTRACT-001): the interpreter prefix resolves through the
    audited repo's ``[host-contract].install``, never hardcoded."""
    interp = declared_install_interpreter(Path(repo))
    return [
        ("ruff", [interp, "-m", "ruff", "check", *paths]),
        (
            "flake8-CCR001",
            [interp, "-m", "flake8", "--select=CCR001", "--max-cognitive-complexity=15", *paths],
        ),
        (
            "pylint-R0801+C0302+R0915+R0914",
            [interp, "-m", "pylint", "--disable=all", "--enable=R0801,C0302,R0915,R0914", *paths],
        ),
    ]


def _test_commands(repo: str, paths: list[str]) -> list[tuple[str, list[str]]]:
    interp = declared_install_interpreter(Path(repo))
    return [
        (
            "pylint-R0801+C0302",
            [interp, "-m", "pylint", "--disable=all", "--enable=R0801,C0302", *paths],
        ),
    ]


def _extract_test_command(project_toml: dict, repo: str) -> tuple[list[str], str | None]:
    section = project_toml.get("integration") if isinstance(project_toml, dict) else None
    if not isinstance(section, dict):
        return ([], None)
    raw = section.get("run")
    if not isinstance(raw, str) or not raw.strip():
        return ([], None)
    try:
        argv = shlex.split(raw)
    except ValueError:
        return ([], None)
    if not argv:
        return ([], None)
    cwd = section.get("cwd", ".")
    cwd_str = cwd if isinstance(cwd, str) and cwd.strip() else "."
    resolved = cwd_str if os.path.isabs(cwd_str) else os.path.join(repo, cwd_str)
    return (argv, resolved)


def _run_check(
    repo: str,
    name: str,
    argv: list[str],
    cwd: str | None = None,
) -> tuple[str, int, str, str]:
    work_dir = cwd or repo
    argv = _resolve_argv0(list(argv), work_dir)
    try:
        proc = subprocess.run(
            argv,
            cwd=work_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        return (name, proc.returncode, proc.stdout, proc.stderr)
    except (OSError, FileNotFoundError) as exc:
        return (name, 127, "", str(exc))


def _format_failure(name: str, returncode: int, stdout: str, stderr: str) -> str:
    detail = (stdout + stderr).strip()
    if len(detail) > 500:
        detail = detail[:500] + "..."
    return f"{name} (exit {returncode}): {detail}"
