"""Integration: reference host materialization (FR-0282, IF-REFERENCE-001).

b93 §8.1 bootstrap contract: the CLI half is driven by the shared walker
(``walk_and_release``) — bare ``trac run`` bootstrap is forbidden. The
module-level halves assert the delivered IF-REFERENCE-001 contract faces
(interfaces §1n real materialization / needs_attention / same-shape
acceptance; NFR-0147 kernel language neutrality is pinned by the dedicated
test_kernel_language_neutrality integration face). The journey anchor now
drives the real materialization: a built tracks 0.5.0 wheel, a fresh venv
with a real non-editable install, ``trac init`` through the installed
interpreter and a local bare remote probed with ``git ls-remote`` — then the
real host release walk must be accepted by the same-shape comparator.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e.helpers import walk_and_release, walk_to_m_impl_parked
from tracks.executor.host_contract import load_host_contract
from tracks.executor.reference_host import create_reference_host, verify_reference_equivalence

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ASSETS = _REPO_ROOT / "tracks" / "assets" / "reference_host"

_JOURNEY_CHAIN = (
    "candidate.frozen",
    "local_gate.passed",
    "ci.run_observed",
    "prism.verdict",
    "security.assessed",
    "release.previewed",
    "release.decided",
    "publish.planned",
    "publish.executed",
    "milestone.trace_closed",
    "milestone.sealed",
    "refs.cleaned",
    "run.completed",
)

_CLOSED_SET = {
    "pyproject.toml": "pyproject.toml",
    "tracks-project.toml": ".tracks/projects/project.toml",
    "architecture.md": ".tracks/projects/v0.1/architecture.md",
    "ci.yml": ".github/workflows/ci.yml",
}


def _clean_env() -> dict:
    """Subprocess env without the test runner's PYTHONPATH (wheel isolation)."""
    return {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    return proc


def _target_repo(tmp_path: Path) -> Path:
    """A fresh host repo the reference deployment can bind and init into."""
    target = tmp_path / "refhost"
    target.mkdir()
    _git(target, "init", "-b", "main")
    _git(target, "config", "user.email", "ref@example.com")
    _git(target, "config", "user.name", "Reference Host")
    (target / "README.md").write_text("reference host\n", encoding="utf-8")
    _git(target, "add", "README.md")
    _git(target, "commit", "-m", "init")
    return target


def _bare_remote(tmp_path: Path) -> Path:
    bare = tmp_path / "reference-remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(bare)], check=True, capture_output=True
    )
    return bare


def _assert_closed_set(report: dict) -> None:
    deployed = report.get("deployed")
    assert isinstance(deployed, dict)
    for source, destination in _CLOSED_SET.items():
        landed = deployed.get(source)
        assert isinstance(landed, (str, Path)) and str(landed).endswith(destination), (
            f"closed deployment set violated: {source!r} must land at {destination!r}, got {landed!r}"
        )


@pytest.fixture(scope="module")
def tracks_wheel(tmp_path_factory) -> Path:
    """The real tracks 0.5.0 wheel, built offline (local setuptools only).

    Built from a per-worker source COPY: two xdist workers running
    setuptools against the same source tree race on the shared build/
    artifacts (flaky ERROR under -n4), while copying is a few MB.
    """
    src = tmp_path_factory.mktemp("wheel_src")
    shutil.copytree(
        _REPO_ROOT / "tracks",
        src / "tracks",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copy2(_REPO_ROOT / "pyproject.toml", src / "pyproject.toml")
    wheelhouse = tmp_path_factory.mktemp("wheelhouse")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--disable-pip-version-check",
            "-w",
            str(wheelhouse),
            str(src),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"tracks wheel build failed: {proc.stderr[-800:]}"
    import tracks as _tracks

    wheels = sorted(
        wheelhouse.glob(f"agent_on_tracks-{_tracks.__version__}-*.whl")
    )
    assert wheels, (
        f"wheel build produced no {_tracks.__version__} wheel: "
        f"{list(wheelhouse.iterdir())}"
    )
    return wheels[0]


@pytest.fixture
def materialized(tmp_path: Path, tracks_wheel: Path) -> tuple[Path, Path, dict]:
    """Materialize the reference host from the real wheel and a bare remote."""
    target = _target_repo(tmp_path)
    remote = _bare_remote(tmp_path)
    report = create_reference_host(_ASSETS, target, tracks_wheel, str(remote))
    assert report.get("status") == "ok", f"materialization must succeed, got {report!r}"
    return target, remote, report


# AC-FR0282-01@v0.8 TRACKS-TRACE reference host journey same shape with candidate binding
def test_reference_host_journey_same_shape(
    host_repo, trac, event_log, ci_echo_standin, materialized, tracks_wheel
):
    target, remote, report = materialized

    # Real materialization contract (§1n 真实物化验收合同): fresh venv,
    # non-editable wheel install, the install-usability init witness and the
    # probed remote binding — all under the isolated target.
    venv = report.get("venv")
    assert isinstance(venv, (str, Path)) and str(venv).startswith(str(target))
    assert report.get("install") == "non-editable"
    assert report.get("remote") == str(remote)
    assert report.get("wheel") == str(tracks_wheel)
    _assert_closed_set(report)

    python = Path(str(venv)) / "bin" / "python"
    assert python.exists(), f"fresh venv python missing: {python}"
    installed = subprocess.run(
        [
            str(python),
            "-c",
            "import importlib.metadata as m, tracks; "
            "print(m.version('agent-on-tracks')); print(tracks.__file__)",
        ],
        cwd=target,
        capture_output=True,
        text=True,
        env=_clean_env(),
    )
    assert installed.returncode == 0, installed.stderr
    import tracks as _tracks

    assert _tracks.__version__ in installed.stdout, installed.stdout
    assert str(Path(str(venv))) in installed.stdout, (
        f"the import must resolve to the venv's non-editable install, got {installed.stdout!r}"
    )

    # init witness: the installed trac initialized the isolated host in place.
    assert report["init"]["returncode"] == 0
    assert (target / ".tracks" / "projects" / "project.toml").exists()
    started = subprocess.run(
        [str(python), "-m", "tracks.cli.main", "start", "v0.1"],
        cwd=target,
        capture_output=True,
        text=True,
        input="构建一个事件溯源运行时\n",
        env=_clean_env(),
    )
    assert started.returncode == 0, started.stderr
    assert "started" in started.stdout, started.stdout

    # Same-shape acceptance: the canonical chain is accepted; a diverged
    # chain is rejected with identifiable reasons.
    same_shape = {"events": [{"kind": kind} for kind in _JOURNEY_CHAIN]}
    ok, reasons = verify_reference_equivalence(same_shape)
    assert ok is True and reasons == (), f"same-shape journey must be accepted, got {(ok, reasons)!r}"
    diverged = {"events": [{"kind": kind} for kind in _JOURNEY_CHAIN[:-1]]}
    ok, reasons = verify_reference_equivalence(diverged)
    assert ok is False and reasons, f"diverged journey must be rejected with reasons, got {(ok, reasons)!r}"

    # CLI half: the real walked release run produces the isomorphic journey
    # chain. The filtered chain carries repeated kinds (review rounds, repair
    # re-runs), so acceptance is the ordered-subsequence shape check — not
    # pairwise equality.
    run_id = walk_and_release(trac, host_repo)
    assert trac("run").returncode == 0  # publish + M-MILESTONE
    events = event_log(run_id)
    kinds = [e["type"] for e in events if e["type"] in _JOURNEY_CHAIN]
    assert set(_JOURNEY_CHAIN) <= set(kinds), (
        f"reference host must produce every chain link, got {kinds}"
    )
    ok, reasons = verify_reference_equivalence(
        {"events": [{"kind": kind} for kind in kinds]}
    )
    assert ok is True and reasons == (), (
        f"the walked release chain must be same-shape, got {(ok, reasons)!r} from {kinds}"
    )


# AC-FR0282-02@v0.8 TRACKS-TRACE missing credentials needs_attention for reference remote
def test_missing_credentials_needs_attention(host_repo, trac, event_log, tmp_path, monkeypatch):
    # Module half (§1n needs_attention 不降级): remote_url=None without
    # explicit simulation degrades to needs_attention with an identifiable
    # reason — never a local-success downgrade, and never materialization.
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
    target = tmp_path / "refhost2"
    report = create_reference_host(
        _ASSETS, target, tmp_path / "unused.whl", None
    )
    assert isinstance(report, dict)
    assert report.get("status") == "needs_attention", (
        f"a missing remote binding must report needs_attention, got {report!r}"
    )
    reason = str(report.get("reason", ""))
    assert "remote" in reason or "credential" in reason, (
        f"needs_attention reason must identify the missing remote/credential, got {reason!r}"
    )
    assert not (target / ".venv").exists(), (
        "a missing remote binding must not materialize anything"
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
def test_python_details_isolated(materialized):
    # Module half: the materialized deployment lands the contract carrier at
    # its mapped path, and a missing contract path fails closed. (The loader
    # ValueError face for malformed carriers is the unit-contract fail-closed
    # set; the success-load face needs a real carrier schema and is not
    # presumed here.)
    target, _remote, report = materialized
    contract_path = target / ".tracks" / "projects" / "project.toml"
    assert contract_path.exists(), (
        f"the contract carrier must materialize at the mapped path, got {report!r}"
    )
    _assert_closed_set(report)
    try:
        load_host_contract(target.parent / "definitely-missing.toml")
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
