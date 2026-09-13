"""IF-GUARD-002 guard parity + deployment (T-007 RED unit).

Pins the parity/deployment contract declared in interfaces.md §1e *before*
the GREEN implementation lands in the new ``guard_parity.py`` module:

- ``check_parity`` compares Runtime / pre-commit / CI execution points of the
  same canonical registry: any missing guard, command/scope/threshold drift,
  or ``--exit-zero`` fallback is a ``GuardMismatch`` that fails closed
  (AC-FR0258-02, NFR-0141-01); argv0 uses the shared resolution rule but the
  REST of the argv is compared exactly (§1e).
- ``deploy_guard_configs`` writes ``.githooks/pre-commit`` and
  ``.github/workflows/ci.yml`` embedding ``TRACKS_GUARD_REGISTRY=<digest>``,
  and returns ``artifact_digests`` over the generated output bytes -- the
  deployment record / hook / CI must all carry the SAME registry digest
  (AC-FR0258-03).

Each assertion fails today because ``check_parity`` and
``deploy_guard_configs`` are ``NotImplementedError("IF-GUARD-002")`` stubs in
``tracks/executor/guard_registry.py`` -- the M-IMPL RED on the contract.
"""

from __future__ import annotations

from tracks.executor.guard_registry import (
    GuardEntry,
    GuardRegistry,
    check_parity,
    deploy_guard_configs,
)


def _registry() -> GuardRegistry:
    return GuardRegistry(
        version=1,
        host="tracks",
        entries=(
            GuardEntry(
                guard_id="lint-format",
                category="lint_format",
                tool="ruff",
                tool_version="0.16.0",
                command=(".venv/bin/python", "-m", "ruff", "check", "tracks"),
                config_paths=("pyproject.toml",),
                config_sections=("tool.ruff",),
                config_digest="sha256:" + "1" * 64,
                scope=("tracks",),
                threshold="violations=0",
                timeout_seconds=300,
                failure_policy="fail_closed",
                execution_points=("runtime", "pre_commit", "ci"),
                required_check="lint",
            ),
        ),
        digest="sha256:" + "2" * 64,
    )


# AC-FR0258-02@v0.7 TRACKS-TRACE three execution points parity
def test_check_parity_matching_three_points_returns_match(tmp_path):
    cmd = (".venv/bin/python", "-m", "ruff", "check", "tracks")
    pre_commit = tmp_path / "pre-commit"
    pre_commit.write_text(".venv/bin/python -m ruff check tracks\n")
    ci = tmp_path / "ci.yml"
    ci.write_text("run: .venv/bin/python -m ruff check tracks\n")

    report = check_parity(
        _registry(),
        runtime_commands={"lint-format": cmd},
        pre_commit_path=pre_commit,
        ci_workflow_path=ci,
        cwd=tmp_path,
    )
    assert report.registry_digest == _registry().digest
    assert report.runtime_match is True
    assert report.pre_commit_match is True
    assert report.ci_match is True
    assert report.mismatches == ()


# AC-FR0258-02@v0.7 TRACKS-TRACE command drift is a fail-closed mismatch
def test_check_parity_runtime_command_drift_is_mismatch(tmp_path):
    pre_commit = tmp_path / "pre-commit"
    pre_commit.write_text("ruff check tracks\n")
    ci = tmp_path / "ci.yml"
    ci.write_text("run: ruff check tracks\n")

    report = check_parity(
        _registry(),
        runtime_commands={"lint-format": (".venv/bin/python", "-m", "ruff", "check", "src-only")},
        pre_commit_path=pre_commit,
        ci_workflow_path=ci,
        cwd=tmp_path,
    )
    assert report.runtime_match is False
    assert any(m.guard_id == "lint-format" for m in report.mismatches)


# AC-FR0258-02@v0.7 TRACKS-TRACE --exit-zero is always a mismatch (no soft guard)
def test_check_parity_exit_zero_never_matches(tmp_path):
    pre_commit = tmp_path / "pre-commit"
    pre_commit.write_text("ruff check tracks --exit-zero\n")
    ci = tmp_path / "ci.yml"
    ci.write_text("run: ruff check tracks --exit-zero\n")

    report = check_parity(
        _registry(),
        runtime_commands={"lint-format": (".venv/bin/python", "-m", "ruff", "check", "tracks")},
        pre_commit_path=pre_commit,
        ci_workflow_path=ci,
        cwd=tmp_path,
    )
    assert report.pre_commit_match is False
    assert report.ci_match is False
    flagged = [m for m in report.mismatches if "exit-zero" in m.detail]
    assert flagged


# AC-FR0258-02@v0.7 TRACKS-TRACE a missing guard in one point is a mismatch
def test_check_parity_missing_guard_in_ci_is_mismatch(tmp_path):
    pre_commit = tmp_path / "pre-commit"
    pre_commit.write_text("ruff check tracks\n")
    ci = tmp_path / "ci.yml"
    ci.write_text("")  # empty CI file = no commands at all = missing

    report = check_parity(
        _registry(),
        runtime_commands={"lint-format": (".venv/bin/python", "-m", "ruff", "check", "tracks")},
        pre_commit_path=pre_commit,
        ci_workflow_path=ci,
        cwd=tmp_path,
    )
    assert report.ci_match is False
    assert any(m.kind == "missing" for m in report.mismatches)


# AC-FR0258-03@v0.7 TRACKS-TRACE deploy writes hook+CI with the SAME digest
def test_deploy_guard_configs_writes_consistent_digest(tmp_path):
    deployment = deploy_guard_configs(_registry(), tmp_path)

    # fixed deployment locations
    assert deployment.registry_digest == _registry().digest
    assert deployment.pre_commit_path == tmp_path / ".githooks" / "pre-commit"
    assert deployment.ci_workflow_path == tmp_path / ".github" / "workflows" / "ci.yml"

    hook = deployment.pre_commit_path.read_text()
    ci = deployment.ci_workflow_path.read_text()
    assert f"TRACKS_GUARD_REGISTRY={_registry().digest}" in hook
    # CI YAML writes env.TRACKS_GUARD_REGISTRY with colon-space format
    assert f"TRACKS_GUARD_REGISTRY: {_registry().digest}" in ci


# AC-FR0258-03@v0.7 TRACKS-TRACE artifact digests are over generated output
def test_deploy_guard_configs_artifact_digests_cover_output(tmp_path):
    import hashlib

    deployment = deploy_guard_configs(_registry(), tmp_path)
    assert set(deployment.artifact_digests) == {
        ".githooks/pre-commit",
        ".github/workflows/ci.yml",
    }
    for path, digest in deployment.artifact_digests.items():
        raw = (tmp_path / path).read_bytes()
        assert digest == hashlib.sha256(raw).hexdigest()


# AC-FR0258-03@v0.7 TRACKS-TRACE deploy with empty registry still produces
# consistent hook+CI (no entries = minimal files, but vital digest guarantees).
def test_deploy_guard_configs_empty_registry_produces_consistent_digest(tmp_path):
    import hashlib

    from tracks.executor.guard_registry import GuardRegistry

    empty = GuardRegistry(version=1, host="tracks", entries=(), digest="sha256:" + "3" * 64)
    deployment = deploy_guard_configs(empty, tmp_path)

    assert deployment.registry_digest == empty.digest
    assert deployment.pre_commit_path.exists()
    assert deployment.ci_workflow_path.exists()
    for path_key in (".githooks/pre-commit", ".github/workflows/ci.yml"):
        raw = (tmp_path / path_key).read_bytes()
        assert deployment.artifact_digests[path_key] == hashlib.sha256(raw).hexdigest()


# AC-FR0269-02@v0.8 TRACKS-TRACE fail-closed parity includes the declared command.
def test_parity_rejects_exit_zero_even_when_all_points_match(tmp_path):
    from dataclasses import replace

    original = _registry()
    command = (*original.entries[0].command, '--exit-zero')
    registry = replace(original, entries=(replace(original.entries[0], command=command),))
    deployment = deploy_guard_configs(registry, tmp_path)
    report = check_parity(
        registry, {'lint-format': command}, deployment.pre_commit_path,
        deployment.ci_workflow_path, tmp_path,
    )
    assert report.runtime_match is False
    assert report.pre_commit_match is False
    assert report.ci_match is False
    assert {m.place for m in report.mismatches if m.kind == 'exit_zero'} == {
        'runtime', 'pre_commit', 'ci',
    }
