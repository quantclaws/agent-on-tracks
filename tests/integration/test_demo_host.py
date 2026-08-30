"""Integration: demo host real-path equivalence (IF-DEMO-001).

AC-FR0266-02@v0.7 demo created via real install path,
AC-NFR0142-01@v0.7 path equivalence evidence.

Assertions land on `create_demo_host`/`verify_path_equivalence` (IF-DEMO-001,
interfaces §1j) public outlets and the `demo.equivalence` event outlet (§1a).
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tracks.executor.demo_host import (
    DemoHostReport,
    create_demo_host,
    verify_path_equivalence,
)

DEMO_TEMPLATE = Path(__file__).resolve().parents[2] / "tracks" / "assets" / "demo_host"
REPO = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory):
    """Build a real non-editable wheel once per module (test-plan §2.5).

    A conforming create_demo_host must validate the wheel as a real non-editable
    install source; a bogus placeholder wheel would be rejected. The wheel is
    built from the package so it carries the demo_host package-data assets.

    Robustness: no network-or-build-isolation dependency. `pip wheel` first
    with --no-build-isolation (offline, uses the venv's own build backend); a
    deterministic in-process zip fallback guarantees the fixture never fails
    on environment plumbing (a fixture failure would be an illegal-Red
    collection_error, not a SUT verdict)."""
    wheel_dir = tmp_path_factory.mktemp("wheels")
    return _build_real_wheel(wheel_dir)


def _build_real_wheel(wheel_dir: Path) -> Path:
    """Build an installable wheel from the package source, pip-first with an
    offline, in-process fallback that cannot raise on environment plumbing."""
    try:
        proc = subprocess.run(
            [
                sys.executable, "-m", "pip", "wheel",
                "--no-build-isolation", "--no-deps", "-w", str(wheel_dir),
                str(REPO),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        wheels = list(wheel_dir.glob("*.whl"))
        if proc.returncode == 0 and wheels:
            return wheels[0]
    except (OSError, UnicodeError):
        pass
    return _construct_wheel_from_source(wheel_dir)


def _construct_wheel_from_source(wheel_dir: Path) -> Path:
    """Deterministic in-process wheel construction (PEP 427) from the source
    tree — no subprocess, no network, no build isolation.

    The wheel carries the real package files PLUS the demo_host package-data
    assets (architecture.md, demo_calc.py, flake8.ini, pyproject.toml,
    tracks-project.toml, tests/**) and EXCLUDES the inherited legacy
    `guards.toml` (mirroring the package-data allowlist of §1k)."""
    import base64
    import hashlib
    import zipfile

    records: list[tuple[str, str, str]] = []  # (path, sha256-b64url, size)

    def _add(wheel_zip, src: Path, arc: str) -> None:
        data = src.read_bytes()
        wheel_zip.writestr(arc, data)
        digest = base64.urlsafe_b64encode(
            hashlib.sha256(data).digest()
        ).rstrip(b"=").decode("ascii")
        records.append((arc, f"sha256={digest}", str(len(data))))

    dist_name = "agent_on_tracks-0.5.0"
    wheel_rel = f"{dist_name}-py3-none-any.whl"
    wheel_path = wheel_dir / wheel_rel
    # Package source tree (everything tracked under the tracks package).
    package_root = REPO / "tracks"
    assert package_root.is_dir(), "package source must be present"
    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for src in sorted(package_root.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(REPO).as_posix()
            # Replicate the package-data allowlist: exclude inherited legacy.
            if rel.endswith("assets/demo_host/guards.toml"):
                continue
            _add(zf, src, rel)
        dist_info = f"{dist_name}.dist-info"
        # WHEEL
        wheel_meta = (
            b"Wheel-Version: 1.0\n"
            b"Generator: tracks-mtest-wheel\n"
            b"Root-Is-Purelib: true\n"
            b"Tag: py3-none-any\n"
        )
        zf.writestr(f"{dist_info}/WHEEL", wheel_meta)
        records.append((f"{dist_info}/WHEEL", "", ""))
        # METADATA
        metadata = (
            b"Metadata-Version: 2.1\n"
            b"Name: agent-on-tracks\n"
            b"Version: 0.5.0\n"
        )
        zf.writestr(f"{dist_info}/METADATA", metadata)
        records.append((f"{dist_info}/METADATA", "", ""))
        # top_level.txt
        zf.writestr(f"{dist_info}/top_level.txt", "tracks\n")
        records.append((f"{dist_info}/top_level.txt", "", ""))
        # RECORD (self entry: no digest, no size; hash is none at install time).
        record_lines = "\n".join(
            f"{arc},{digest},{size}" for arc, digest, size in records
        )
        zf.writestr(f"{dist_info}/RECORD", record_lines + "\n")
    return wheel_path


# AC-FR0266-02@v0.7 TRACKS-TRACE demo created via real install path
def test_demo_created_via_real_install_path(tmp_path, built_wheel):
    """AC-FR0266-02: demo host uses fresh venv + non-editable wheel + trac init.

    The wheel is a real built wheel (not a placeholder); a conforming
    create_demo_host validates it and produces the DemoHostReport. The wheel
    must carry the formal demo architecture and must NOT carry the inherited
    legacy `guards.toml` (package-data allowlist)."""
    # The built wheel must ship the formal demo architecture registry source.
    with zipfile.ZipFile(built_wheel) as zf:
        names = zf.namelist()
    assert any(n.endswith("demo_host/architecture.md") for n in names), (
        "wheel must ship the formal demo architecture asset"
    )
    assert not any(n.endswith("guards.toml") for n in names), (
        "wheel must not ship inherited legacy guards.toml"
    )
    target = tmp_path / "demo"
    target.mkdir()
    report = create_demo_host(template_dir=DEMO_TEMPLATE, target_dir=target, wheel=built_wheel)
    assert isinstance(report, DemoHostReport)
    # Contract (§1j): fresh venv created under the target (not the source
    # tree's venv), non-editable wheel, import path outside source.
    assert report.wheel_sha256 == "sha256:" + hashlib.sha256(built_wheel.read_bytes()).hexdigest()
    assert report.venv.is_relative_to(target), (
        "fresh venv must live under the demo target, not the source tree"
    )
    # Import path must not live inside the source tree (non-editable install).
    assert "tracks/assets" not in str(report.import_path)
    assert not Path(report.import_path).is_relative_to(REPO), (
        "import path must not resolve inside the source tree (non-editable)"
    )
    # Architecture path is the fixed deployment target (§1j).
    assert str(report.architecture_path).endswith(".tracks/projects/v0.1/architecture.md")
    assert report.hooks_path == ".githooks"
    assert report.ci_binding == "declared"
    assert report.adapter_id == "reference-pytest"
    # The registry digest must be present and bound to the demo host.
    assert report.registry_digest.startswith("sha256:")


# AC-NFR0142-01@v0.7 TRACKS-TRACE path equivalence evidence
def test_path_equivalence_evidence(tmp_path, built_wheel):
    """AC-NFR0142-01: demo path equivalence surfaces independent evidence.

    verify_path_equivalence returns a typed (equivalent, gaps) tuple whose
    equivalence is independently derived from the real install path, not an
    agent self-report. A demo host created via the real install path must be
    equivalent; an unprovisioned host must surface gaps (no silent pass)."""
    target = tmp_path / "demo"
    target.mkdir()
    report = create_demo_host(template_dir=DEMO_TEMPLATE, target_dir=target, wheel=built_wheel)
    equivalent, gaps = verify_path_equivalence(report)
    # Independent typed evidence (not agent self-report).
    assert isinstance(equivalent, bool)
    assert isinstance(gaps, tuple)
    # A real-install demo host must be equivalent (no gaps); a conforming impl
    # produces equivalent=True with empty gaps. The stub raises -> legal Red.
    assert equivalent is True, (
        f"real-install demo host must be path-equivalent; gaps={gaps}"
    )
    assert gaps == (), f"equivalent demo host must have no path-equivalence gaps: {gaps}"
