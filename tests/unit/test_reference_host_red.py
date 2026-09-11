"""T-024 RED: reference host module contract -- IF-REFERENCE-001 (interfaces
§1n "Reference host" + "真实物化验收合同"; FR-0282 / NFR-0149 / FR-0267 /
FR-0278 faces).

Pins the reference host materialization contract in
tracks/executor/reference_host.py:

1. Real materialization lands an isolated Python env per the closed
   deployment set -- a fresh ``python -m venv`` under the target (outside
   the source tree), a real non-editable wheel install through that venv's
   pip, ``trac init`` executed by the installed interpreter (the
   install-usability witness), template sources deployed to the contracted
   destinations (repo root; ``.tracks/projects/project.toml``;
   ``.tracks/projects/v0.1/architecture.md``;
   ``.github/workflows/ci.yml``) and the real remote probed/bound
   (interfaces §1n 部署落点封闭集, Maestro ruling T-003 A; Python specifics
   stay in assets -- NFR-0147 allowed zone).
2. A missing remote binding degrades to needs_attention, never to a local
   success -- ``remote_url=None`` without explicit simulation must report
   needs_attention with an identifiable reason (interfaces §1n
   "needs_attention 不降级"; NFR-0149-01 credential-missing counting).
3. Same-shape journey acceptance -- ``verify_reference_equivalence``
   accepts a report whose event chain matches the tracks release journey
   shape (candidate.frozen → … → run.completed, repeated re-run/repair
   kinds tolerated) and rejects a diverged chain with identifiable reasons
   (the module-side half of the journey anchor, test-plan §8 AC-FR0282-01).

Event/chain fixtures follow the in-repo convention: journey chains are
ordered ``{"kind": ...}`` dicts (kernel/release.py producer chain,
interfaces §2b ``trac run`` output kinds).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tracks.executor import reference_host

_REPO_ROOT = Path(__file__).resolve().parents[2]

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


def _fail(message: str) -> None:
    raise AssertionError(f"assertion failure: {message}")


def _create(template_dir: Path, target_dir: Path, wheel: Path, remote_url):
    create = getattr(reference_host, "create_reference_host", None)
    if create is None:
        _fail(
            "executor/reference_host.py missing create_reference_host export "
            "(IF-REFERENCE-001 interfaces §1n materialization contract)"
        )
    try:
        return create(template_dir, target_dir, wheel, remote_url)
    except NotImplementedError as err:
        _fail(f"create_reference_host not implemented ({err})")


def _verify(report):
    verify = getattr(reference_host, "verify_reference_equivalence", None)
    if verify is None:
        _fail(
            "executor/reference_host.py missing verify_reference_equivalence "
            "export (IF-REFERENCE-001 same-shape acceptance contract)"
        )
    try:
        return verify(report)
    except NotImplementedError as err:
        _fail(f"verify_reference_equivalence not implemented ({err})")


def _template(tmp_path: Path) -> Path:
    template = tmp_path / "template"
    (template / "tests").mkdir(parents=True)
    for name in (
        "pyproject.toml",
        "flake8.ini",
        "host_calc.py",
        "tracks-project.toml",
        "architecture.md",
    ):
        (template / name).write_text(f"[{name}]\n", encoding="utf-8")
    (template / "ci.yml").write_text("jobs: {}\n", encoding="utf-8")
    return template


def _target_repo(tmp_path: Path) -> Path:
    target = tmp_path / "reference-env"
    target.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=target, check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.email", "ref@example.com")
    git("config", "user.name", "Reference Host")
    (target / "README.md").write_text("reference env\n", encoding="utf-8")
    git("add", "README.md")
    git("commit", "-m", "init")
    return target


def _bare_remote(tmp_path: Path) -> Path:
    bare = tmp_path / "reference-remote.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    return bare


@pytest.fixture(scope="module")
def tracks_wheel(tmp_path_factory) -> Path:
    """The real tracks wheel: non-editable install source (built offline)."""
    wheelhouse = tmp_path_factory.mktemp("unit-wheelhouse")
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
            str(_REPO_ROOT),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        _fail(f"tracks wheel build failed: {proc.stderr[-800:]}")
    wheels = sorted(wheelhouse.glob("agent_on_tracks-0.5.0-*.whl"))
    if not wheels:
        _fail(f"wheel build produced no 0.5.0 wheel: {list(wheelhouse.iterdir())}")
    return wheels[0]


# AC-FR0282-03@v0.8 TRACKS-TRACE IF-REFERENCE-001 isolated env materialization
def test_reference_host_materializes_isolated_python_env(tmp_path, tracks_wheel):
    """A bound remote materializes the closed deployment set into an
    isolated env under the target: fresh venv, real non-editable wheel
    install, ``trac init`` witness, contracted destinations, probed real
    remote binding (interfaces §1n; NFR-0147 Python-details-in-assets)."""
    template = _template(tmp_path)
    target = _target_repo(tmp_path)
    remote = _bare_remote(tmp_path)
    report = _create(template, target, tracks_wheel, str(remote))
    if not isinstance(report, dict):
        _fail(f"create_reference_host must return a dict report, got {type(report)!r}")
    if report.get("status") != "ok":
        _fail(
            f"a bound, reachable remote must materialize successfully, got {report!r}"
        )
    venv = report.get("venv")
    if not isinstance(venv, (str, Path)) or not str(venv).startswith(str(target)):
        _fail(
            "report must bind a fresh env under the isolated target dir, "
            f"got venv={venv!r} target={str(target)!r}"
        )
    python = Path(str(venv)) / "bin" / "python"
    if not python.exists():
        _fail(f"the fresh venv interpreter must exist, got {python!r}")
    if report.get("install") != "non-editable":
        _fail(
            "report must carry an explicitly non-editable wheel install, "
            f"got install={report.get('install')!r}"
        )
    deployed = report.get("deployed")
    if not isinstance(deployed, dict):
        _fail(f"report must carry the deployed mapping, got {deployed!r}")
    closed_set = {
        "pyproject.toml": "pyproject.toml",
        "tracks-project.toml": ".tracks/projects/project.toml",
        "architecture.md": ".tracks/projects/v0.1/architecture.md",
        "ci.yml": ".github/workflows/ci.yml",
    }
    for source, destination in closed_set.items():
        landed = deployed.get(source)
        if not isinstance(landed, (str, Path)) or not str(landed).endswith(
            destination
        ):
            _fail(
                f"closed deployment set violated: {source!r} must land at "
                f"{destination!r}, got {landed!r} (interfaces §1n 部署落点封闭集)"
            )
    if report.get("remote") != str(remote):
        _fail(
            f"report must bind the real remote {str(remote)!r}, got "
            f"remote={report.get('remote')!r} (Maestro ruling T-003 A)"
        )
    if report.get("init", {}).get("returncode") != 0:
        _fail(f"the installed trac must init the materialized host, got {report!r}")
    if not (target / ".tracks" / "runtime").exists():
        _fail("trac init must scaffold .tracks/runtime in the materialized host")


# AC-FR0282-02@v0.8 TRACKS-TRACE IF-REFERENCE-001 missing remote needs_attention
def test_reference_host_missing_credentials_needs_attention(tmp_path):
    """remote_url=None without explicit simulation reports needs_attention
    with an identifiable reason -- never a local-success downgrade
    (interfaces §1n needs_attention 不降级; NFR-0149-01)."""
    template = _template(tmp_path)
    target = tmp_path / "reference-env-nocred"
    report = _create(template, target, tmp_path / "unused.whl", None)
    if not isinstance(report, dict):
        _fail(f"create_reference_host must return a dict report, got {type(report)!r}")
    if report.get("status") != "needs_attention":
        _fail(
            "a missing remote binding must report status=needs_attention, "
            f"got {report.get('status')!r} in {report!r}"
        )
    reason = str(report.get("reason", ""))
    if "remote" not in reason and "credential" not in reason:
        _fail(
            "needs_attention reason must identify the missing remote/"
            f"credential binding, got {reason!r}"
        )
    if (target / ".venv").exists():
        _fail("a missing remote binding must not materialize anything")


# AC-FR0282-02@v0.8 TRACKS-TRACE IF-REFERENCE-001 unreachable remote not pretended
def test_reference_host_unreachable_remote_needs_attention(tmp_path):
    """A bound-but-unreachable remote must fail the ``git ls-remote`` probe
    and report needs_attention (remote_unavailable) instead of pretending
    the binding (interfaces §1n 真实远程绑定; NFR-0149-01)."""
    template = _template(tmp_path)
    target = tmp_path / "reference-env-unreachable"
    wheel = tmp_path / "wheels" / "project-0.1.0-py3-none-any.whl"
    wheel.parent.mkdir()
    wheel.write_bytes(b"PK\x03\x04")
    report = _create(template, target, wheel, str(tmp_path / "no-such-remote.git"))
    if report.get("status") != "needs_attention":
        _fail(
            "an unreachable remote must report needs_attention, "
            f"got {report!r}"
        )
    if "remote" not in str(report.get("reason", "")):
        _fail(
            "the reason must identify the unavailable remote, "
            f"got {report.get('reason')!r}"
        )
    if (target / ".venv").exists():
        _fail("an unreachable remote must not materialize a venv")


# AC-FR0282-01@v0.8 TRACKS-TRACE IF-REFERENCE-001 install failure is structured
def test_reference_host_failed_install_is_structured(tmp_path):
    """A wheel that cannot be installed yields a structured failure report
    (status/reason/detail) and never a fabricated status=ok -- the install
    step is the witness, not the report plan (interfaces §1n 真实物化)."""
    template = _template(tmp_path)
    target = _target_repo(tmp_path)
    remote = _bare_remote(tmp_path)
    broken = tmp_path / "broken-0.1.0-py3-none-any.whl"
    broken.write_bytes(b"PK\x03\x04")  # zip magic only: pip must reject it
    report = _create(template, target, broken, str(remote))
    if report.get("status") != "failed":
        _fail(f"a failed wheel install must be a structured failure, got {report!r}")
    if report.get("reason") != "wheel_install_failed":
        _fail(
            "the failure must identify the wheel install step, "
            f"got reason={report.get('reason')!r}"
        )
    if not report.get("detail"):
        _fail(f"the failure report must carry the pip diagnostic, got {report!r}")
    if (target / ".tracks").exists() or (target / "pyproject.toml").exists():
        _fail(
            "a failed install must not deploy the closed set or init the host"
        )


# AC-FR0282-01@v0.8 TRACKS-TRACE IF-REFERENCE-001 same-shape journey acceptance
def test_reference_host_journey_event_chain_shape():
    """verify_reference_equivalence accepts a report whose journey chain
    matches the tracks release shape and rejects a diverged chain with
    identifiable reasons (module-side half of the journey anchor,
    test-plan §8 AC-FR0282-01)."""
    same_shape = {"events": [{"kind": kind} for kind in _JOURNEY_CHAIN]}
    verdict = _verify(same_shape)
    if (
        not isinstance(verdict, tuple)
        or len(verdict) != 2
        or not isinstance(verdict[0], bool)
        or not isinstance(verdict[1], tuple)
    ):
        _fail(
            "verify_reference_equivalence must return (bool, tuple-of-str), "
            f"got {verdict!r}"
        )
    if verdict[0] is not True or verdict[1] != ():
        _fail(
            f"the same-shape journey chain must be accepted, got {verdict!r}"
        )
    # Re-run kinds (review rounds / repair re-runs) are tolerated.
    repeats = {"events": [{"kind": kind} for kind in _JOURNEY_CHAIN]}
    repeats["events"].insert(3, {"kind": "prism.verdict"})
    verdict = _verify(repeats)
    if verdict[0] is not True or verdict[1] != ():
        _fail(
            f"repeated chain kinds must not break same-shape acceptance, got {verdict!r}"
        )
    diverged = {
        "events": [{"kind": kind} for kind in _JOURNEY_CHAIN[:-1]]
    }  # drop the terminal run.completed link
    ok, reasons = _verify(diverged)
    if ok is not False or not reasons:
        _fail(
            f"a diverged journey chain must be rejected with reasons, got "
            f"{(ok, reasons)!r}"
        )
    joined = " ".join(str(r) for r in reasons)
    if "milestone.sealed" not in joined:
        _fail(
            "rejection reasons must identify the missing milestone.sealed "
            f"seal, got {reasons!r}"
        )
