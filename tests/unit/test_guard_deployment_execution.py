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
