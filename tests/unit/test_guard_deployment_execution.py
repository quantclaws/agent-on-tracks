"""Generated hooks must preserve declared argv and stop after a failed guard."""

import shlex
import subprocess
import sys
from dataclasses import replace

import pytest

from tests.unit.test_guard_parity_helper import _registry
from tracks.executor.guard_registry import deploy_guard_configs


@pytest.mark.parametrize('string_command', [True, False])
def test_generated_hook_executes_quoted_declared_arguments(tmp_path, string_command):
    registry = _registry()
    argv = (sys.executable, '-c', 'from pathlib import Path; Path("observed").write_text("two words")')
    entry = replace(registry.entries[0], command=shlex.join(argv) if string_command else argv)
    deployment = deploy_guard_configs(replace(registry, entries=(entry,)), tmp_path)
    result = subprocess.run(['sh', str(deployment.pre_commit_path)], cwd=tmp_path, capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
    assert (tmp_path / 'observed').read_text() == 'two words'


def test_generated_hook_stops_at_first_failure(tmp_path):
    registry = _registry()
    entries = (replace(registry.entries[0], command=('false',)),
               replace(registry.entries[0], guard_id='after', command=('touch', 'must-not-run')))
    deployment = deploy_guard_configs(replace(registry, entries=entries), tmp_path)
    result = subprocess.run(['sh', str(deployment.pre_commit_path)], cwd=tmp_path, capture_output=True)
    assert result.returncode != 0
    assert not (tmp_path / 'must-not-run').exists()


def test_generated_hook_only_runs_precommit_declarations(tmp_path):
    registry = _registry()
    entries = (
        replace(registry.entries[0], command=('touch', 'local')),
        replace(registry.entries[0], guard_id='coverage', command=('touch', 'ci-only'),
                execution_points=('runtime', 'ci')),
    )
    deployment = deploy_guard_configs(replace(registry, entries=entries), tmp_path)
    result = subprocess.run(['sh', str(deployment.pre_commit_path)], cwd=tmp_path, capture_output=True)
    assert result.returncode == 0
    assert (tmp_path / 'local').exists()
    assert not (tmp_path / 'ci-only').exists()


def test_generated_hook_does_not_recursively_invoke_itself(tmp_path):
    registry = _registry()
    runner = replace(registry.entries[0], category='hooks_runner_ci_required_checks',
                     guard_id='runner', command='sh .githooks/pre-commit',
                     execution_points=('pre_commit', 'ci'))
    deployment = deploy_guard_configs(replace(registry, entries=(runner,)), tmp_path)
    hook = deployment.pre_commit_path.read_text()
    assert 'sh .githooks/pre-commit' not in hook
    assert 'set -e' in hook
    assert 'sh .githooks/pre-commit' in deployment.ci_workflow_path.read_text()


def test_parity_checks_only_declared_execution_points(tmp_path):
    from tracks.executor.guard_registry import check_parity

    registry = _registry()
    entries = (
        replace(registry.entries[0], command='true'),
        replace(registry.entries[0], guard_id='ci-only', command='false',
                execution_points=('ci',)),
        replace(registry.entries[0], guard_id='runner', command='sh .githooks/pre-commit',
                category='hooks_runner_ci_required_checks', execution_points=('pre_commit', 'ci')),
    )
    registry = replace(registry, entries=entries)
    deployment = deploy_guard_configs(registry, tmp_path)
    report = check_parity(registry, {'lint-format': 'true'}, deployment.pre_commit_path,
                          deployment.ci_workflow_path, tmp_path)
    assert report.mismatches == ()
    assert report.runtime_match and report.pre_commit_match and report.ci_match
