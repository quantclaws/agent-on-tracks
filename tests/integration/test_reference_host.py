"""Integration: reference host materialization (FR-0282, IF-REFERENCE-001).

b93 §8.1 bootstrap contract: the CLI half is driven by the shared walker
(``walk_to_m_impl_parked``) — bare ``trac run`` bootstrap is forbidden. The
module-level halves assert the delivered IF-REFERENCE-001 contract faces
(interfaces §1n materialization / needs_attention / same-shape acceptance;
NFR-0147 kernel language neutrality is pinned by the dedicated
test_kernel_language_neutrality integration face). The journey event-chain
assertions stay legal Red against the unwired release producers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.helpers import walk_to_m_impl_parked
from tracks.executor.host_contract import load_host_contract
from tracks.executor.reference_host import create_reference_host, verify_reference_equivalence

pytestmark = pytest.mark.integration

_REMOTE = "git@github.com:example-org/reference-host.git"

_JOURNEY_CHAIN = (
    "candidate.frozen",
    "local_gate.passed",
    "ci.run_observed",
    "prism.verdict",
    "security.assessed",
    "awaiting_release",
    "publish.executed",
    "milestone.sealed",
    "run.completed",
)


def _template(tmp_path: Path) -> Path:
    template = tmp_path / "template"
    (template / "tests").mkdir(parents=True)
    for name in ("pyproject.toml", "flake8.ini", "host_calc.py", "tracks-project.toml", "architecture.md"):
        (template / name).write_text(f"[{name}]\n", encoding="utf-8")
    (template / "ci.yml").write_text("jobs: {}\n", encoding="utf-8")
    return template


def _wheel(tmp_path: Path) -> Path:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    wheel = wheelhouse / "project-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"PK\x03\x04")
    return wheel


_CLOSED_SET = {
    "pyproject.toml": "pyproject.toml",
    "tracks-project.toml": ".tracks/projects/project.toml",
    "architecture.md": ".tracks/projects/v0.1/architecture.md",
    "ci.yml": ".github/workflows/ci.yml",
}


def _assert_closed_set(report: dict) -> None:
    deployed = report.get("deployed")
    assert isinstance(deployed, dict)
    for source, destination in _CLOSED_SET.items():
        landed = deployed.get(source)
        assert isinstance(landed, (str, Path)) and str(landed).endswith(destination), (
            f"closed deployment set violated: {source!r} must land at {destination!r}, got {landed!r}"
        )


# AC-FR0282-01@v0.8 TRACKS-TRACE reference host journey same shape with candidate binding
def test_reference_host_journey_same_shape(host_repo, trac, event_log, tmp_path):
    # Module half (§1n 真实物化验收合同): a bound remote materializes the
    # closed deployment set into an isolated env under the target.
    template = _template(tmp_path)
    target = tmp_path / "refhost"
    report = create_reference_host(template, target, _wheel(tmp_path), _REMOTE)
    assert isinstance(report, dict)
    assert report.get("status") != "needs_attention", (
        f"a bound remote must not degrade to needs_attention, got {report!r}"
    )
    venv = report.get("venv")
    assert isinstance(venv, (str, Path)) and str(venv).startswith(str(target))
    assert report.get("install") == "non-editable"
    _assert_closed_set(report)
    assert report.get("remote") == _REMOTE

    # Same-shape acceptance: the tracks release journey chain is accepted;
    # a diverged chain is rejected with identifiable reasons.
    same_shape = {"events": [{"kind": kind} for kind in _JOURNEY_CHAIN]}
    ok, reasons = verify_reference_equivalence(same_shape)
    assert ok is True and reasons == (), f"same-shape journey must be accepted, got {(ok, reasons)!r}"
    diverged = {"events": [{"kind": kind} for kind in _JOURNEY_CHAIN[:-1]]}
    ok, reasons = verify_reference_equivalence(diverged)
    assert ok is False and reasons, f"diverged journey must be rejected with reasons, got {(ok, reasons)!r}"

    # CLI half: the walked run must produce the isomorphic journey chain.
    # Legal Red: the release-chain producers are not wired on this baseline.
    walk_to_m_impl_parked(trac)
    events = event_log()
    found = [e["type"] for e in events if e["type"] in _JOURNEY_CHAIN]
    assert found == list(_JOURNEY_CHAIN), (
        f"reference host must produce isomorphic chain {list(_JOURNEY_CHAIN)}, got {found}"
    )


# AC-FR0282-02@v0.8 TRACKS-TRACE missing credentials needs_attention for reference remote
def test_missing_credentials_needs_attention(host_repo, trac, event_log, tmp_path, monkeypatch):
    # Module half (§1n needs_attention 不降级): remote_url=None without
    # explicit simulation degrades to needs_attention with an identifiable
    # reason — never a local-success downgrade.
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
    template = _template(tmp_path)
    target = tmp_path / "refhost2"
    report = create_reference_host(template, target, _wheel(tmp_path), None)
    assert isinstance(report, dict)
    assert report.get("status") == "needs_attention", (
        f"a missing remote binding must report needs_attention, got {report!r}"
    )
    reason = str(report.get("reason", ""))
    assert "remote" in reason or "credential" in reason, (
        f"needs_attention reason must identify the missing remote/credential, got {reason!r}"
    )

    # CLI half: without credentials the walked run must surface
    # attention.required for the remote and never a successful release.
    walk_to_m_impl_parked(trac)
    events = event_log()
    decided = [e for e in events if e["type"] == "release.decided"]
    assert not decided, "no release.decided may exist without a bound remote"
    attention = [e for e in events if e["type"] == "attention.required"]
    assert attention, "missing credentials must land attention.required"
    assert any(
        a["payload"].get("reason") in ("remote_unavailable", "missing_credentials", "missing_token")
        for a in attention
    ), f"attention reasons must identify the credential gap, got {[a['payload'] for a in attention]!r}"


# AC-FR0282-03@v0.8 TRACKS-TRACE python details isolated to reference host assets
def test_python_details_isolated(host_repo, trac, event_log, tmp_path):
    # Module half: the materialized deployment lands the contract carrier at
    # its mapped path, and a missing contract path fails closed. (The loader
    # ValueError face for malformed carriers is the unit-contract fail-closed
    # set; the success-load face needs a real carrier schema and is not
    # presumed here.)
    template = _template(tmp_path)
    target = tmp_path / "refhost3"
    report = create_reference_host(template, target, _wheel(tmp_path), _REMOTE)
    assert isinstance(report, dict)
    contract_path = target / ".tracks" / "projects" / "project.toml"
    assert contract_path.exists(), (
        f"the contract carrier must materialize at the mapped path, got {report!r}"
    )
    try:
        load_host_contract(tmp_path / "definitely-missing.toml")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("a missing host contract must fail closed")

    # Python specifics stay in the allowed asset zone (NFR-0147): the
    # reference host module and assets directory are the sanctioned homes.
    # Kernel language neutrality is pinned by the dedicated integration face
    # (test_kernel_language_neutrality.py ALLOWED_V08_DIRS).
    assert Path("tracks/executor/reference_host.py").exists()
    assert Path("tracks/assets/reference_host").exists()
