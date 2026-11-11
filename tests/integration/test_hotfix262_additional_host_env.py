"""Real child-process regressions for SEC-HF262-001 (INV-8).

Observe environment key names only; injected credentials are synthetic.
The anchor runs through its pytest/plugin path and lint through its declared
command. Neither subprocess nor environment helper is mocked.
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

from tests.integration.test_hotfix262_host_env import _write_host_contract
from tests.unit.host_env_allowlist_support import MINIMAL_CONTRACT, STRIP_SAMPLES
from tracks.executor import anchor_surface
from tracks.executor.m_impl_testops import MImplTestOpsMixin
from tracks.project import load_contract

pytestmark = pytest.mark.integration


@pytest.fixture(params=[False, True], ids=["base", "contract-escape"])
def probe_host(tmp_path, monkeypatch, request):
    repo = tmp_path / "host"
    repo.mkdir()
    contract = _write_host_contract(repo)
    header = MINIMAL_CONTRACT
    if request.param:
        header += 'env_allowlist = ["HOST_BOARD", "GITHUB_TOKEN", "VENDOR_API_KEY"]\n'
    contract.write_text(header + contract.read_text(), encoding="utf-8")
    for name in STRIP_SAMPLES:
        monkeypatch.setenv(name, "synthetic::secret")
    monkeypatch.setenv("OPERATOR_PRIVATE", "synthetic::private")
    monkeypatch.setenv("HOST_BOARD", "synthetic::board")
    return repo, bool(request.param)


def _assert_boundary(keys, escape):
    keys = set(keys)
    assert not keys.intersection(STRIP_SAMPLES), sorted(keys.intersection(STRIP_SAMPLES))
    assert "OPERATOR_PRIVATE" not in keys
    assert ("HOST_BOARD" in keys) is escape
    assert {"PATH", "HOME"} <= keys


# AC-FR0315-01@v0.9 TRACKS-TRACE SEC-HF262-001 isolated anchor child
def test_anchor_child_strips_credentials_and_preserves_overlay(probe_host):
    repo, escape = probe_host
    output = repo / "observed.json"
    test_file = repo / "test_probe.py"
    test_file.write_text(
        "import json, os\nfrom pathlib import Path\n"
        "def test_probe():\n"
        "    Path('observed.json').write_text(json.dumps({\n"
        "        'keys': sorted(os.environ), 'path': os.environ.get('PYTHONPATH'),\n"
        "        'surface': bool(os.environ.get('TRACKS_SURFACE_OUT'))}))\n",
        encoding="utf-8",
    )
    node = "test_probe.py::test_probe"
    sidecar = anchor_surface.collect_anchor_surface(
        repo, [node], load_contract(repo), jobs=1
    )
    assert sidecar["anchors"][node]["outcome"] == "pass"
    observed = json.loads(output.read_text())
    _assert_boundary(observed["keys"], escape)
    plugin_root = str(Path(anchor_surface.__file__).resolve().parents[2])
    assert observed["path"].split(anchor_surface.os.pathsep) == [str(repo), plugin_root]
    assert observed["surface"] is True


# AC-FR0315-01@v0.9 TRACKS-TRACE SEC-HF262-001 declared lint child
def test_declared_lint_child_strips_credentials_and_retains_findings(probe_host):
    repo, escape = probe_host
    probe = repo / "lint_probe.py"
    probe.write_text(
        "import json, os, sys\n"
        "print(json.dumps({'keys': sorted(os.environ), 'paths': sys.argv[1:]}))\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    contract = repo / ".tracks/projects/project.toml"
    command = shlex.join([sys.executable, str(probe)])
    with contract.open("a") as stream:
        stream.write("\n[lint]\ncheck = " + json.dumps(command) + "\n")
    host = MImplTestOpsMixin()
    host.repo = repo
    host._LINT_TIMEOUT_SECONDS = 30
    findings, note = host._run_declared_lint(["src/delivered.py"])
    assert note == ""
    assert findings is not None
    observed = json.loads(findings)
    _assert_boundary(observed["keys"], escape)
    assert observed["paths"] == ["src/delivered.py"]
