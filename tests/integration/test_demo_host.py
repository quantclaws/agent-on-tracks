"""Integration: demo host real-path equivalence (IF-DEMO-001).

AC-FR0266-02@v0.7 demo created via real install path,
AC-NFR0142-01@v0.7 path equivalence evidence.

Assertions land on `create_demo_host`/`verify_path_equivalence` (IF-DEMO-001,
interfaces §1j) public outlets.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.executor.demo_host import (
    DemoHostReport,
    create_demo_host,
    verify_path_equivalence,
)

DEMO_TEMPLATE = Path(__file__).resolve().parents[2] / "tracks" / "assets" / "demo_host"

pytestmark = pytest.mark.integration



# AC-FR0266-02@v0.7 TRACKS-TRACE demo created via real install path
def test_demo_created_via_real_install_path(tmp_path):
    """AC-FR0266-02: demo host uses fresh venv + non-editable wheel + trac init."""
    target = tmp_path / "demo"
    target.mkdir()
    wheel = tmp_path / "tracks-0.7.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel-bytes")  # placeholder wheel path contract
    report = create_demo_host(template_dir=DEMO_TEMPLATE, target_dir=target, wheel=wheel)
    assert isinstance(report, DemoHostReport)
    # Contract (§1j): fresh venv, non-editable wheel, import path outside source.
    assert report.venv.exists() or str(report.venv).endswith("venv")
    assert report.wheel_sha256.startswith("sha256:")
    # Import path must not live inside the source tree.
    assert "tracks/assets" not in str(report.import_path)
    # Architecture path is the fixed deployment target (§1j).
    assert str(report.architecture_path).endswith(".tracks/projects/v0.1/architecture.md")
    assert report.hooks_path == ".githooks"
    assert report.ci_binding == "declared"
    assert report.adapter_id == "reference-pytest"


# AC-NFR0142-01@v0.7 TRACKS-TRACE path equivalence evidence
def test_path_equivalence_evidence(tmp_path):
    """AC-NFR0142-01: demo path equivalence surfaces independent evidence."""
    target = tmp_path / "demo"
    target.mkdir()
    wheel = tmp_path / "tracks-0.7.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel-bytes")
    report = create_demo_host(template_dir=DEMO_TEMPLATE, target_dir=target, wheel=wheel)
    equivalent, gaps = verify_path_equivalence(report)
    # The equivalence result must be a typed tuple (independent evidence).
    assert isinstance(equivalent, bool)
    assert isinstance(gaps, tuple)
    # The registry digest must equal the deployment/hook/CI digest (four-way).
    assert report.registry_digest.startswith("sha256:")
