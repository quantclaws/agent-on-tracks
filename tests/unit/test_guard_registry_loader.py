"""IF-GUARD-001 canonical quality guard registry loader/validator (T-006 RED).

Pins the loader/validator seam declared in interfaces.md §1e/§1k *before* the
GREEN implementation lands:

- ``load_guard_registry`` parses the canonical ``[quality_registry]`` block in
  the architecture.md §4.2 ``toml`` fence into a ``GuardRegistry`` (version,
  host, exactly the eight ``GUARD_CATEGORIES`` entries, stable registry
  digest), and fails closed on zero/multiple/wrong-table/malformed blocks
  (AC-FR0258-01 single source of truth; AC-FR0259-01 revise routing).
- ``validate_guard_registry`` reports hard errors (category gap/duplicate,
  unpinned tool, ``--exit-zero``, non-fail_closed policy, missing required
  check, absent config file, config-digest drift) and returns empty errors for
  a complete, consistent registry (AC-FR0258-04 no silent migration gap;
  AC-FR0259-01 registry completeness).

Each assertion fails today because ``load_guard_registry`` and
``validate_guard_registry`` are ``NotImplementedError("IF-GUARD-001")`` stubs
-- the M-IMPL RED on the contract.
"""

from __future__ import annotations

import hashlib

import pytest

from tracks.executor.guard_registry import (
    GUARD_CATEGORIES,
    GuardEntry,
    GuardRegistry,
    load_guard_registry,
    validate_guard_registry,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest(text: str) -> str:
    return "sha256:" + _sha(text)


_HEADING = "### 4.2 Canonical quality guard registry"


def _guard_block(
    guard_id: str,
    category: str,
    *,
    tool: str = "ruff",
    tool_version: str = "0.16.0",
    command: str = ".venv/bin/ruff check tracks tests",
    config_path: str = "pyproject.toml",
    config_digest: str = "sha256:0000000000000000000000000000000000000000000000000000000000000000",
    failure_policy: str = "fail_closed",
    required_check: str = "lint",
) -> str:
    return "\n".join(
        [
            "[[quality_guard]]",
            f'id = "{guard_id}"',
            f'category = "{category}"',
            f'tool = "{tool}"',
            f'tool_version = "{tool_version}"',
            f'command = "{command}"',
            f'config_paths = ["{config_path}"]',
            'config_sections = ["tool.ruff"]',
            f'config_digest = "{config_digest}"',
            'scope = ["tracks", "tests"]',
            'threshold = "violations=0"',
            "timeout_seconds = 300",
            f'failure_policy = "{failure_policy}"',
            'execution_points = ["runtime", "pre_commit", "ci"]',
            f'required_check = "{required_check}"',
            "",
        ]
    )


def _registry_body(
    guards: list[str],
    *,
    version: int = 1,
    host: str = "tracks",
) -> str:
    return (
        "[quality_registry]\n"
        f"version = {version}\n"
        f'host = "{host}"\n\n'
        + "\n".join(guards)
    )


def _architecture_file(tmp_path, toml_block: str) -> str:
    body = (
        "## 3. Earlier\n"
        "\n"
        f"{_HEADING}\n"
        "\n"
        "```toml\n"
        f"{toml_block}"
        "```\n"
        "\n"
        "## 5. Next section\n"
    )
    path = tmp_path / "architecture.md"
    path.write_text(body, encoding="utf-8")
    return str(path)


def _eight_guard_blocks(config_digest: str) -> list[str]:
    spec = [
        ("lint-format", "lint_format"),
        ("static-semantic", "static_analysis"),
        ("cognitive-complexity", "cognitive_complexity"),
        ("file-length", "file_length"),
        ("method-length-locals", "method_length_locals"),
        ("duplication", "duplication"),
        ("coverage-threshold", "coverage_threshold"),
        ("hooks-runner-ci-required-checks", "hooks_runner_ci_required_checks"),
    ]
    return [
        _guard_block(gid, cat, config_digest=config_digest)
        for gid, cat in spec
    ]


# -- load_guard_registry: parse + stable digest + fail-closed ----------------

def test_load_guard_registry_parses_valid_registry(tmp_path):
    config_digest = _digest("content-a")
    registry = load_guard_registry(
        _architecture_file(tmp_path, _registry_body(_eight_guard_blocks(config_digest)))
    )

    assert isinstance(registry, GuardRegistry)
    assert registry.version == 1
    assert registry.host == "tracks"
    assert len(registry.entries) == 8
    assert {e.category for e in registry.entries} == set(GUARD_CATEGORIES)
    assert registry.entries[0].guard_id == "lint-format"
    assert registry.entries[0].config_digest == config_digest


def test_load_guard_registry_digest_is_stable_and_input_sensitive(tmp_path):
    path = _architecture_file(
        tmp_path, _registry_body(_eight_guard_blocks(_digest("content-a")))
    )
    first = load_guard_registry(path)
    second = load_guard_registry(path)
    assert first.digest == second.digest
    # Contract (interfaces §1e/§1k): registry digest uses the same sha256:
    # prefix convention as config digests; the frozen acceptance asserts
    # digest.startswith("sha256:").  The remainder is 64 lowercase hex.
    assert first.digest.startswith("sha256:")
    int(first.digest[len("sha256:"):], 16)

    mutated = _registry_body(
        [
            _guard_block(
                "lint-format",
                "lint_format",
                tool_version="9.9.9",
                config_digest=_digest("content-a"),
            ),
            *_eight_guard_blocks(_digest("content-a"))[1:],
        ]
    )
    altered = load_guard_registry(_architecture_file(tmp_path, mutated))
    assert altered.digest != first.digest


@pytest.mark.parametrize(
    "block",
    [
        "[quality_registry]\n" 'host = "tracks"\n' + "\n",  # zero guards
        "[guard_registry]\n" 'version = 1\n' + "\n",  # wrong first table
    ],
)
def test_load_guard_registry_fails_closed_on_malformed_block(tmp_path, block):
    with pytest.raises(ValueError):
        load_guard_registry(_architecture_file(tmp_path, block))


def test_load_guard_registry_fails_closed_on_zero_registry_blocks(tmp_path):
    body = (
        "## 3. Earlier\n\n"
        f"{_HEADING}\n\n"
        "```python\nx = 1\n```\n\n"
        "## 5. Next\n"
    )
    path = tmp_path / "architecture.md"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError):
        load_guard_registry(str(path))


def test_load_guard_registry_fails_closed_on_missing_config_digest(tmp_path):
    # Each guard block is generated with the exact default sha256:0000 literal
    # so the line-strip reliably removes the config_digest field.
    blocks = _eight_guard_blocks("sha256:" + "0" * 64)
    lines = _registry_body(blocks).replace(
        'config_digest = "sha256:0000000000000000000000000000000000000000000000000000000000000000"\n',
        "",
    )
    assert "config_digest =" not in lines
    with pytest.raises(ValueError):
        load_guard_registry(_architecture_file(tmp_path, lines))


# -- validate_guard_registry: hard-error surface (IF-GUARD-001) --------------

def _entry(
    guard_id: str,
    category: str,
    *,
    tool: str = "ruff",
    tool_version: str = "0.16.0",
    command: str = ".venv/bin/ruff check tracks tests",
    config_paths: tuple[str, ...] = ("pyproject.toml",),
    config_digest: str = "sha256:" + "1" * 64,
    failure_policy: str = "fail_closed",
    required_check: str = "lint",
) -> GuardEntry:
    return GuardEntry(
        guard_id=guard_id,
        category=category,
        tool=tool,
        tool_version=tool_version,
        command=command,
        config_paths=config_paths,
        config_sections=("tool.ruff",),
        config_digest=config_digest,
        scope=("tracks",),
        threshold="violations=0",
        timeout_seconds=300,
        failure_policy=failure_policy,
        execution_points=("runtime",),
        required_check=required_check,
    )


def _registry(*entries: GuardEntry) -> GuardRegistry:
    return GuardRegistry(version=1, host="tracks", entries=entries, digest="0" * 64)


def test_validate_guard_registry_valid_registry_returns_no_errors(tmp_path):
    # A valid registry's referenced config files must exist on disk with
    # matching sha256 bytes digests (validator compares declared digest to the
    # actual file bytes).  Write pyproject.toml first and declare its digest.
    (tmp_path / "pyproject.toml").write_text("tool = {}\n", encoding="utf-8")
    config_digest = _digest("tool = {}\n")
    all_eight = [
        _entry(gid, cat, config_digest=config_digest) for gid, cat in [
            ("lint-format", "lint_format"),
            ("static-semantic", "static_analysis"),
            ("cognitive-complexity", "cognitive_complexity"),
            ("file-length", "file_length"),
            ("method-length-locals", "method_length_locals"),
            ("duplication", "duplication"),
            ("coverage-threshold", "coverage_threshold"),
            ("hooks-runner-ci-required-checks", "hooks_runner_ci_required_checks"),
        ]
    ]
    validated = validate_guard_registry(_registry(*all_eight), tmp_path)
    assert isinstance(validated, tuple)
    assert validated == ()


def test_validate_guard_registry_reports_missing_category(tmp_path):
    # Only seven entries -> coverage_threshold is the one category absent.
    seven = [
        _entry(gid, cat) for gid, cat in [
            ("lint-disable", "lint_format"),
            ("static-semantic", "static_analysis"),
            ("cognitive-complexity", "cognitive_complexity"),
            ("file-length", "file_length"),
            ("method-length-locals", "method_length_locals"),
            ("duplication", "duplication"),
            ("hooks-runner-ci-required-checks", "hooks_runner_ci_required_checks"),
        ]
    ]
    missing = set(GUARD_CATEGORIES) - {e.category for e in seven}
    assert missing == {"coverage_threshold"}  # the fixture really omits it
    errors = validate_guard_registry(_registry(*seven), tmp_path)
    assert any("coverage_threshold" in e for e in errors)


def test_validate_guard_registry_reports_duplicate_category(tmp_path):
    dup = [
        _entry("a", "lint_format"),
        _entry("b", "lint_format"),  # duplicate category
        _entry("c", "static_analysis"),
        _entry("d", "cognitive_complexity"),
        _entry("e", "file_length"),
        _entry("f", "method_length_locals"),
        _entry("g", "duplication"),
        _entry("h", "coverage_threshold"),
    ]
    errors = validate_guard_registry(_registry(*dup), tmp_path)
    assert any("lint_format" in e for e in errors)


def test_validate_guard_registry_reports_unpinned_tool(tmp_path):
    unpinned = _entry("t", "lint_format", tool_version="")
    errors = validate_guard_registry(_registry(unpinned), tmp_path)
    assert any("pinned" in e.lower() or "tool_version" in e.lower() for e in errors)


def test_validate_guard_registry_reports_exit_zero(tmp_path):
    exit_zero = _entry(
        "t", "lint_format", command=".venv/bin/ruff check --exit-zero src"
    )
    errors = validate_guard_registry(_registry(exit_zero), tmp_path)
    assert any("exit-zero" in e for e in errors)


def test_validate_guard_registry_reports_non_fail_closed_policy(tmp_path):
    lax = _entry("t", "lint_format", failure_policy="warn_only")
    errors = validate_guard_registry(_registry(lax), tmp_path)
    assert any("fail_closed" in e for e in errors)


def test_validate_guard_registry_reports_missing_required_check(tmp_path):
    no_check = _entry("t", "lint_format", required_check="")
    errors = validate_guard_registry(_registry(no_check), tmp_path)
    assert any("required_check" in e.lower() for e in errors)


def test_validate_guard_registry_reports_absent_config_file(tmp_path):
    # re-dependency resolve: config file not present at repo root.
    missing = _entry(
        "t",
        "lint_format",
        config_paths=("does-not-exist.toml",),
    )
    errors = validate_guard_registry(_registry(missing), tmp_path)
    assert any("does-not-exist.toml" in e for e in errors)


def test_validate_guard_registry_reports_config_digest_drift(tmp_path):
    (tmp_path / "pyproject.toml").write_text("real-bytes", encoding="utf-8")
    # Declared digest does not match the actual file bytes -> fixtures must
    # name the real file (not an absent path) so the only error is drift.
    drifted = _entry(
        "t",
        "lint_format",
        config_digest="sha256:" + "e" * 64,  # does not match file bytes
    )
    errors = validate_guard_registry(_registry(drifted), tmp_path)
    assert any("digest" in e.lower() for e in errors)
