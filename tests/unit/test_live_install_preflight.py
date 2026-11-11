"""Live acceptance setup must accept shipped host assets and reject runner leaks."""

import zipfile

import pytest

from tests._support.runtime_resources import RUNTIME_RESOURCE_PATHS
from tests.e2e_live.harness import install_opencode_shim, wheel_resources


def _wheel(tmp_path, *extra):
    path = tmp_path / "candidate.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name in RUNTIME_RESOURCE_PATHS:
            archive.writestr("tracks/" + name, "packaged resource\n")
        for name in extra:
            archive.writestr(name, "fixture\n")
    return path


# AC-FR0348-01@v0.12 TRACKS-TRACE live wheel preflight accepts product-owned host tests
def test_live_preflight_accepts_shipped_host_tests(tmp_path):
    wheel = _wheel(
        tmp_path,
        "tracks/assets/demo_host/tests/unit/test_demo_calc.py",
        "tracks/assets/reference_host/tests/e2e/test_host_journey.py",
    )
    assert set(wheel_resources(wheel)) == set(RUNTIME_RESOURCE_PATHS)


# AC-FR0348-02@v0.12 TRACKS-TRACE live wheel preflight rejects leaking acceptance drivers
@pytest.mark.parametrize("leaked", [
    "tests/e2e_live/test_full_journey.py",
    "tracks/e2e_live/driver.py",
    "tracks/assets/demo_host/scenarios/approved-outcome.json",
    "tracks/assets/reference_host/tests/scenarios/approved-outcome.json",
    "tracks/assets/unapproved_host/tests/test_bypass.py",
])
def test_live_preflight_rejects_acceptance_runner_files(tmp_path, leaked):
    with pytest.raises(RuntimeError, match="test-only files leaked"):
        wheel_resources(_wheel(tmp_path, leaked))


# AC-FR0348-01@v0.12 TRACKS-TRACE retrying live setup reuses its external harness link
def test_live_setup_reuses_existing_harness_link(tmp_path):
    executable = tmp_path / "external-opencode"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    first = install_opencode_shim(tmp_path / "artifacts", executable)
    second = install_opencode_shim(tmp_path / "artifacts", executable)
    assert first == second
    assert first[1].resolve() == executable
    assert executable.read_text(encoding="utf-8") == "#!/bin/sh\nexit 0\n"


# AC-FR0348-01@v0.12 TRACKS-TRACE changing the external CLI replaces only its owned link
def test_live_setup_never_overwrites_an_external_executable(tmp_path):
    previous = tmp_path / "opencode-old"
    replacement = tmp_path / "opencode-new"
    previous.write_bytes(b"old executable")
    replacement.write_bytes(b"new executable")
    install_opencode_shim(tmp_path / "artifacts", previous)
    _, shim = install_opencode_shim(tmp_path / "artifacts", replacement)
    assert shim.resolve() == replacement
    assert previous.read_bytes() == b"old executable"
    assert replacement.read_bytes() == b"new executable"
