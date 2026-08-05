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

# Runtime dependencies that the executor's M-TEST collection/RED_CHECK subprocess
# (`[sys.executable, "-m", "pytest"]`, architecture.md §3.2) requires to be
# importable in an installed wheel environment. A `--no-deps` install would leave
# these absent and break a normal `trac run` (FR-0030/0050), so the install
# contract must pull declared dependencies.
RUNTIME_IMPORT_PROBES = ("pytest",)

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


def _wheel_metadata(wheel: Path) -> str:
    """Return the METADATA file from a wheel archive."""
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_name = next(
            name for name in names
            if name.endswith(".dist-info/METADATA")
        )
        return archive.read(metadata_name).decode("utf-8")


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

    # The wheel METADATA must declare pytest as a runtime dependency: the
    # M-TEST executor shells out to `[sys.executable, "-m", "pytest"]` during a
    # normal `trac run` (architecture.md §3.2), so pytest is not dev-only.
    metadata = _wheel_metadata(wheel)
    assert "Requires-Dist: pytest" in metadata, (
        f"wheel METADATA must declare pytest as a runtime dependency; "
        f"got METADATA:\n{metadata}"
    )

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

    # Install the wheel WITHOUT --no-deps: the installed environment must satisfy
    # the wheel's declared runtime dependencies so that `trac run` M-TEST
    # collection/RED_CHECK can invoke `[sys.executable, "-m", "pytest"]` (the
    # build step may still use --no-deps; the install step must not).
    install = _run(
        [str(isolated_python), "-m", "pip", "install", str(wheel)],
        tmp_path,
        env,
    )
    assert install.returncode == 0, install.stderr
    assert (isolated_bin / "trac").is_file()

    # Probe that every runtime import the executor relies on resolves inside the
    # isolated environment (not the workspace venv). A `--no-deps` install would
    # fail here with "No module named pytest".
    for module in RUNTIME_IMPORT_PROBES:
        probe = _run(
            [str(isolated_python), "-c", f"import {module}; print({module}.__file__)"],
            tmp_path,
            env,
        )
        assert probe.returncode == 0, (
            f"runtime import {module!r} failed in isolated venv after wheel "
            f"install (install must pull declared dependencies, not use "
            f"--no-deps); stderr={probe.stderr!r}"
        )
        assert Path(probe.stdout.strip()).is_relative_to(isolated_venv / "lib"), (
            f"{module} resolved outside the isolated venv: {probe.stdout!r}"
        )

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


def test_wheel_install_command_never_uses_no_deps():
    """The wheel *install* command must never use --no-deps.

    architecture.md §3.2 makes the executor invoke
    ``[sys.executable, "-m", "pytest"]`` during a normal ``trac run`` (M-TEST
    collection/RED_CHECK, FR-0030/0050). pytest is therefore a runtime
    dependency, and the isolated-wheel install (both the deterministic
    distribution test and the live e2e harness) must pull declared dependencies.
    A ``--no-deps`` install would leave pytest absent and break M-TEST.

    The ``pip wheel`` *build* step may still use ``--no-deps``; only the
    ``pip install`` step is constrained. This test guards against regression by
    inspecting the install commands in both call sites.
    """
    import ast

    harness = PROJECT_ROOT / "tests" / "e2e_live" / "harness.py"
    this_file = Path(__file__)

    def _install_commands_uses_no_deps(source: str, filename: str) -> list[str]:
        """Return any ``pip install ... --no-deps`` command literals in source."""
        tree = ast.parse(source, filename=filename)
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.List):
                continue
            elements = [e.value for e in node.elts if isinstance(e, ast.Constant)]
            if "install" not in elements or "pip" not in elements:
                continue
            if "--no-deps" in elements:
                offenders.append(ast.unparse(node))
        return offenders

    for path in (harness, this_file):
        source = path.read_text(encoding="utf-8")
        offenders = _install_commands_uses_no_deps(source, str(path))
        assert not offenders, (
            f"wheel install command in {path.name} must not use --no-deps "
            f"(runtime pytest dependency would be skipped); offenders:\n"
            + "\n".join(offenders)
        )
