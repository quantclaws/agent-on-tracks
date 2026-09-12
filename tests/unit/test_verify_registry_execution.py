"""AC-FR0269-01/02: registry-backed quality must execute every declared category."""

import sys
from dataclasses import replace
from pathlib import Path

import pytest

from tests.unit.test_guard_registry_loader import GUARD_CATEGORIES, _guard_block, _sha
from tests.unit.test_verify_gates_coverage import _cmd, _Host
from tracks.executor.host_contract import LocalGateDecl
from tracks.kernel.machine import State


def _seed(tmp_path, *, failure=False):
    host = _Host(tmp_path)
    host.store.home = host.repo / '.tracks'
    arch = host.store.home / 'projects/v0.8/architecture.md'
    arch.parent.mkdir(parents=True)
    (host.repo / 'config.toml').write_text('config')
    (host.repo / 'guard.py').write_text(
        'import sys\nfrom pathlib import Path\n'
        'with Path("observed").open("a") as out: out.write(sys.argv[1]+"\\n")\n'
        'sys.exit(int(sys.argv[2]))\n'
    )
    blocks = []
    for category in GUARD_CATEGORIES:
        command = f'{sys.executable} guard.py {category} {int(failure and category == "duplication")}'
        blocks.append(_guard_block(category, category, command=command,
                                   config_path='config.toml', config_digest='sha256:' + _sha('config')))
    arch.write_text('### 4.2 Canonical quality guard registry\n```toml\n'
                    '[quality_registry]\nversion=1\nhost="test"\n' + '\n'.join(blocks) + '\n```\n')
    project = Path(__file__).resolve().parents[2] / '.tracks/projects/project.toml'
    (host.repo / '.tracks/projects/project.toml').write_text(
        project.read_text().replace('check = ".venv/bin/ruff check"', 'check = "true"')
    )
    gate = LocalGateDecl('quality', 'guard_registry', '',
                         ('lint_format', 'duplication'), 'exit_code', 30)
    return host, arch, gate


def _run(host, gate):
    return host._run_one_local_gate(_cmd(), 'SHA', 'D', None, State(version='v0.8'),
                                    0, gate, {'version': 'v0.8'})


@pytest.mark.parametrize('failure', [False, True])
def test_registry_executes_selected_categories_and_keeps_each_result(tmp_path, failure):
    host, _arch, gate = _seed(tmp_path, failure=failure)
    assert _run(host, gate) is (not failure)
    assert (host.repo / 'observed').read_text().splitlines() == ['lint_format', 'duplication']
    payload = host.emitted[-1][1]
    assert payload['candidate_sha'] == 'SHA'
    summary = payload['normalized_result']['summary']
    assert summary['registry_digest'].startswith('sha256:')
    assert [entry['category'] for entry in summary['guards']] == list(gate.categories)
    assert all(entry['command_echo'] for entry in summary['guards'])


@pytest.mark.parametrize('defect', ['missing', 'drift', 'unknown', 'empty'])
def test_registry_refuses_unverifiable_declaration_before_execution(tmp_path, defect):
    host, arch, gate = _seed(tmp_path)
    if defect == 'missing':
        arch.unlink()
    elif defect == 'drift':
        (host.repo / 'config.toml').write_text('changed')
    else:
        gate = replace(gate, categories=('unknown',) if defect == 'unknown' else ())
    assert _run(host, gate) is False
    assert host.emitted[-1][0] == 'local_gate.failed'
    assert not (host.repo / 'observed').exists()
