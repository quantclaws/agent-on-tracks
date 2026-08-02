"""Verify the non-editable wheel contains all runtime resources."""
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from tests.runtime_resources import RUNTIME_RESOURCE_PATHS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CURRENT_PYTHON = Path(sys.executable).resolve()

PROBE = f"""
import json
import os
import site
from importlib.resources import files
from pathlib import Path

from tracks.effects.opencode import OpencodeBackend
import tracks

resource_paths = {RUNTIME_RESOURCE_PATHS!r}
package_root = files("tracks")
resource_bytes = {{}}
for resource_path in resource_paths:
    resource = package_root.joinpath(*resource_path.split("/"))
    assert resource.is_file(), resource_path
    resource_bytes[resource_path] = resource.read_bytes()

tracks_file = Path(tracks.__file__).resolve()
site_packages = [Path(path).resolve() for path in site.getsitepackages()]
assert any(tracks_file.is_relative_to(path) for path in site_packages)

host = Path(os.environ["PROBE_HOST"])
backend = OpencodeBackend(host, "nfr-0070")
materialized = backend._materialize("Scribe")
destination = Path(materialized["dest"])
try:
    assert destination.read_bytes() == resource_bytes["agents/Scribe.md"]
finally:
    backend._cleanup(materialized)
assert not destination.exists()

print(json.dumps({{"tracks_file": str(tracks_file),
                  "resources": sorted(resource_bytes)}}))
"""


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("VIRTUAL_ENV", None)
    return env


def _run(command: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_wheel_is_installable_and_contains_runtime_resources(tmp_path):
    assert sys.prefix != sys.base_prefix, "this test must run from a virtual environment"
    assert CURRENT_PYTHON.is_file()
    env = _clean_env()
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()

    build = _run(
        [
            str(CURRENT_PYTHON),
            "-m",
            "pip",
            "wheel",
            str(PROJECT_ROOT),
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheelhouse),
        ],
        tmp_path,
        env,
    )
    assert build.returncode == 0, build.stderr
    wheels = list(wheelhouse.glob("*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    assert {f"tracks/{path}" for path in RUNTIME_RESOURCE_PATHS} <= names
    assert not any(
        name.startswith(("tests/", "e2e_live/", "scenarios/")) for name in names
    )

    isolated_venv = tmp_path / "isolated-venv"
    create = _run(
        [str(CURRENT_PYTHON), "-m", "venv", str(isolated_venv)],
        tmp_path,
        env,
    )
    assert create.returncode == 0, create.stderr
    isolated_python = isolated_venv / "bin" / "python"
    isolated_bin = isolated_venv / "bin"

    install = _run(
        [str(isolated_python), "-m", "pip", "install", "--no-deps", str(wheel)],
        tmp_path,
        env,
    )
    assert install.returncode == 0, install.stderr
    assert (isolated_bin / "trac").is_file()

    console_env = _clean_env()
    console_env["PATH"] = f"{isolated_bin}{os.pathsep}{console_env['PATH']}"
    console_host = tmp_path / "console-host"
    console_host.mkdir()
    console = _run(["trac", "status"], console_host, console_env)
    assert console.returncode == 0, console.stderr
    assert "no runs yet" in console.stdout

    probe_host = tmp_path / "probe-host"
    probe_host.mkdir()
    probe_env = _clean_env()
    probe_env["PROBE_HOST"] = str(probe_host)
    probe = _run([str(isolated_python), "-c", PROBE], tmp_path, probe_env)
    assert probe.returncode == 0, probe.stderr
    result = json.loads(probe.stdout)
    tracks_file = Path(result["tracks_file"])
    assert tracks_file.is_relative_to(isolated_venv / "lib")
    assert not tracks_file.is_relative_to(PROJECT_ROOT)
    assert result["resources"] == sorted(RUNTIME_RESOURCE_PATHS)
