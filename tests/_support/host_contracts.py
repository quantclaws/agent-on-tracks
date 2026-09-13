"""Declared-host-contract fixtures for the release-chain integration anchors.

The contract is appended to the host ``.tracks/projects/project.toml`` (after
the fake M-DESIGN drive materialized the test execution sections) through the
shared walker's ``pre_seed_hook``, so it is committed with the phase0
premises and consumed by the real M-VERIFY/M-SECURITY producers — never a
fixture-fabricated event.
"""

from __future__ import annotations

from pathlib import Path

# Minimal D-41 test execution contract (the sections ``trac validate``'s
# canonical project.toml loader requires) so direct-handler anchors can carry
# a declared [host-contract.*] table on a validate-clean canonical contract.
TEST_EXECUTION_CONTRACT = """\
[unit]
framework = "pytest"
paths = ["tests/unit/"]
collect = ".venv/bin/python -m pytest --collect-only -q tests/unit/"
run = ".venv/bin/python -m pytest tests/unit/ --tb=short -q --junitxml={result}"
run_selected = ".venv/bin/python -m pytest {nodes} --tb=short -q --junitxml={result}"
cwd = "."

[integration]
framework = "pytest"
paths = ["tests/integration/"]
collect = ".venv/bin/python -m pytest --collect-only -q tests/integration/"
run = ".venv/bin/python -m pytest tests/integration/ --tb=short -q --junitxml={result}"
run_selected = ".venv/bin/python -m pytest {nodes} --tb=short -q --junitxml={result}"
cwd = "."

[e2e]
framework = "pytest"
paths = ["tests/e2e/"]
collect = ".venv/bin/python -m pytest --collect-only -q tests/e2e/"
run = ".venv/bin/python -m pytest tests/e2e/ --tb=short -q --junitxml={result}"
run_selected = ".venv/bin/python -m pytest {nodes} --tb=short -q --junitxml={result}"
cwd = "."

[nightly]
schedule = "0 3 * * *"
workflow = ".github/workflows/nightly.yml"
job = "nightly-regression"
layers = ["unit", "integration", "e2e"]
purpose = "current FULL suite regression; not a local gate"
"""


def gate_block(kind: str, command: str) -> str:
    """One executable ``[[host-contract.local_gate]]`` declaration."""
    return (
        "[[host-contract.local_gate]]\n"
        f'kind = "{kind}"\n'
        'source = "command"\n'
        f'command = "{command}"\n'
        f'categories = ["{kind}"]\n'
        'result_channel = "exit_code"\n'
        "timeout_seconds = 60\n"
    )


def declared_contract_toml(
    *,
    gate_command: str = "true",
    scan_command: str = "true",
    extra_gates: tuple[tuple[str, str], ...] = (),
) -> str:
    """A well-formed declared contract: passing gates + declared scan + CI.

    ``gate_command``/``scan_command`` inject the real failure premises (the
    command actually runs); ``extra_gates`` adds more declared kinds.
    """
    body = (
        "[host-contract]\n"
        "version = 1\n"
        'language = "python"\n'
        'toolchain = "cpython>=3.10"\n'
        'install = "true"\n\n'
        + gate_block("quality", gate_command)
    )
    for kind, command in extra_gates:
        body += "\n" + gate_block(kind, command)
    body += (
        "\n[[host-contract.security_scan]]\n"
        'id = "declared-scan"\n'
        'tool = "true"\n'
        'install = "true"\n'
        f'command = "{scan_command}"\n'
        'result_channel = "exit_code"\n'
        'threshold = "violations=0"\n'
        "timeout_seconds = 60\n\n"
        "[host-contract.ci]\n"
        'repo_env = "TRAC_GITHUB_REPO"\n'
        'workflow = "ci.yml"\n'
        "required_checks = []\n"
        'conclusion = "success"\n\n'
        "[host-contract.operations.feature]\n"
        'steps = ["merge:main"]\n'
    )
    return body


def complete_declared_contract_toml() -> str:
    """The complete declared asset set of AC-FR0281-01.

    language/toolchain/install + the quality guard declaration (scope via
    categories) + trace/reach/anti_slop gates + version scheme + build/
    artifact + post-install smoke + security policy + CI binding + the
    journey operation plan. The pinned tool/config digest/threshold half is
    carried by the canonical guard registry the quality gate references
    (asserted against this repo's materialized contract in the AC anchor);
    collect/run_selected live in the same materialized file's three-layer
    test contract sections.
    """
    body = (
        "[host-contract]\n"
        "version = 1\n"
        'language = "python"\n'
        'toolchain = "cpython>=3.10"\n'
        'install = "true"\n\n'
        + gate_block("quality", "true")
        + "\n" + gate_block("trace", "true")
        + "\n" + gate_block("reach", "true")
        + "\n" + gate_block("anti_slop", "true")
    )
    body += (
        "\n[host-contract.version_scheme]\n"
        'feature_tag = "v{minor}.0"\n'
        'patch_line = "v{minor}.{n}"\n'
        'prerelease_tag = "v{minor}.{n}-pre"\n'
        "\n[host-contract.build]\n"
        'command = "true"\n'
        'artifact = ".tracks/projects/project.toml"\n'
        'result_channel = "exit_code"\n'
        "timeout_seconds = 60\n"
        "\n[host-contract.smoke]\n"
        'steps = ["true"]\n'
        'result_channel = "exit_code"\n'
        "timeout_seconds = 60\n"
        "\n[[host-contract.security_scan]]\n"
        'id = "declared-scan"\n'
        'tool = "true"\n'
        'install = "true"\n'
        'command = "true"\n'
        'result_channel = "exit_code"\n'
        'threshold = "violations=0"\n'
        "timeout_seconds = 60\n\n"
        "[host-contract.ci]\n"
        'repo_env = "TRAC_GITHUB_REPO"\n'
        'workflow = "ci.yml"\n'
        "required_checks = []\n"
        'conclusion = "success"\n\n'
        "[host-contract.operations.feature]\n"
        'steps = ["merge:main"]\n'
    )
    return body


def declare_host_contract(repo: Path, body: str | None = None) -> None:
    """Append the declared contract to the host project contract."""
    contract = repo / ".tracks" / "projects" / "project.toml"
    text = contract.read_text(encoding="utf-8")
    assert "[host-contract]" not in text
    contract.write_text(
        text.rstrip("\n") + "\n\n" + (body or declared_contract_toml()),
        encoding="utf-8",
    )
