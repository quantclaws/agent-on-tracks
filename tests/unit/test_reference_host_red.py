"""T-024 RED: reference host module contract -- IF-REFERENCE-001 (interfaces
§1n "Reference host" + "真实物化验收合同"; FR-0282 / NFR-0149 / FR-0267 /
FR-0278 faces).

Pins the two still-undelivered stubs in tracks/executor/reference_host.py
(the NotImplementedError("IF-REFERENCE-001") bodies):

1. Materialization lands an isolated Python env per the closed deployment
   set -- a fresh venv under the target (outside the source tree), an
   explicitly non-editable wheel install, template sources deployed to the
   contracted destinations (repo root; ``.tracks/projects/project.toml``;
   ``.tracks/projects/v0.1/architecture.md``; ``.github/workflows/ci.yml``)
   and the real remote bound (interfaces §1n 部署落点封闭集, Maestro ruling
   T-003 A; Python specifics stay in assets -- NFR-0147 allowed zone).
2. A missing remote binding degrades to needs_attention, never to a local
   success -- ``remote_url=None`` without explicit simulation must report
   needs_attention with an identifiable reason (interfaces §1n
   "needs_attention 不降级"; NFR-0149-01 credential-missing counting).
3. Same-shape journey acceptance -- ``verify_reference_equivalence``
   accepts a report whose event chain matches the tracks release journey
   shape (candidate.frozen → … → milestone.sealed → run.completed) and
   rejects a diverged chain with identifiable reasons (the module-side
   half of the deferred journey anchor, test-plan §8 AC-FR0282-01).

Event/chain fixtures follow the in-repo convention: journey chains are
ordered ``{"kind": ...}`` dicts (kernel/release.py producer chain,
interfaces §2b ``trac run`` output kinds).

All three anchors fail on the current baseline with assertion_failure on
the contract token (NotImplementedError stub bodies are converted to
assertion failures). Only unit tests are added (RED discipline, manifest
red_test_paths = tests/unit); no production file is touched.
"""

from __future__ import annotations

from pathlib import Path

from tracks.executor import reference_host

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


def _wheel(tmp_path: Path) -> Path:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    wheel = wheelhouse / "project-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"PK\x03\x04")
    return wheel


# AC-FR0282-03@v0.8 TRACKS-TRACE IF-REFERENCE-001 isolated env materialization
def test_reference_host_materializes_isolated_python_env(tmp_path):
    """A bound remote materializes the closed deployment set into an
    isolated env under the target: fresh venv, explicitly non-editable
    wheel, contracted destinations, real remote binding (interfaces §1n;
    NFR-0147 Python-details-in-assets)."""
    template = _template(tmp_path)
    target = tmp_path / "reference-env"
    report = _create(template, target, _wheel(tmp_path), _REMOTE)
    if not isinstance(report, dict):
        _fail(f"create_reference_host must return a dict report, got {type(report)!r}")
    if report.get("status") == "needs_attention":
        _fail(
            f"a bound remote must not degrade to needs_attention, got {report!r}"
        )
    venv = report.get("venv")
    if not isinstance(venv, (str, Path)) or not str(venv).startswith(str(target)):
        _fail(
            "report must bind a fresh env under the isolated target dir, "
            f"got venv={venv!r} target={str(target)!r}"
        )
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
    if report.get("remote") != _REMOTE:
        _fail(
            f"report must bind the real remote {_REMOTE!r}, got "
            f"remote={report.get('remote')!r} (Maestro ruling T-003 A)"
        )


# AC-FR0282-02@v0.8 TRACKS-TRACE IF-REFERENCE-001 missing remote needs_attention
def test_reference_host_missing_credentials_needs_attention(tmp_path):
    """remote_url=None without explicit simulation reports needs_attention
    with an identifiable reason -- never a local-success downgrade
    (interfaces §1n needs_attention 不降级; NFR-0149-01)."""
    template = _template(tmp_path)
    target = tmp_path / "reference-env-nocred"
    report = _create(template, target, _wheel(tmp_path), None)
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
    diverged = {
        "events": [{"kind": kind} for kind in _JOURNEY_CHAIN[:-1]]
    }  # drop the terminal milestone.sealed seal
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
            f"link, got {reasons!r}"
        )
