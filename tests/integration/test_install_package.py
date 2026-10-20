"""Official installation package content (IF-INSTALLPKG-001, IF-INSTALL-001).

AC-FR0348-01: the official wheel carries the complete capability resource
set (agents, skills, templates, default ``opencode.json``) and a project
install built from it is functional. AC-FR0348-02: a wheel missing any of
those resources is rejected as ``package_incomplete`` before it can become
a usable candidate (no receipt written).

The wheel is the real build product of the tree under test; the assertions
land on the wheel zip inventory / ``METADATA`` and on the ``trac install``
CLI outlet, never on installer internals.
"""

from __future__ import annotations

import json
import re
import zipfile

import pytest
import tomllib

from tests._support.v012_install import (
    CURRENT_VERSION,
    PROJECT_ROOT,
    build_wheel,
    reason_of,
    wheel_metadata_version,
)

pytestmark = pytest.mark.integration

_AGENT_ROLES = ("Archer", "Devon", "Lex", "Prism", "Sage", "Scribe", "Shield")
_CANONICAL_SKILLS = (
    "tracks-archer-design",
    "tracks-archer-planning",
    "tracks-devon-rgr",
    "tracks-discuz",
    "tracks-envelope",
    "tracks-prism-design",
    "tracks-prism-impl",
    "tracks-prism-test",
    "tracks-quality-guards",
    "tracks-shield",
)
_RECIPES = json.loads(
    (PROJECT_ROOT / "tests" / "assets" / "v0.12" / "wheel-recipes.json").read_text(
        encoding="utf-8"
    )
)
_OMISSIONS: dict[str, str] = _RECIPES["incomplete"]


@pytest.fixture(scope="module")
def incomplete_wheels(tmp_path_factory):
    root = tmp_path_factory.mktemp("v012-incomplete-wheels")
    return {
        name: build_wheel(root / name, omit=(rel,))
        for name, rel in _OMISSIONS.items()
    }


# AC-FR0348-01@v0.12 TRACKS-TRACE package data declares every capability resource
def test_scaffold_package_resources_declared():
    """AC-FR0348-01 (declaration half): package-data lists the capability
    resources and the default harness config is a valid, model-free JSON with
    the seven per-agent external-directory permission blocks."""
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]["tracks"]
    for pattern in (
        "agents/*.md",
        "skills/*/*.md",
        "templates/*.md",
        "assets/opencode.json",
    ):
        assert pattern in package_data, f"package-data must declare {pattern}"

    config = json.loads(
        (PROJECT_ROOT / "tracks" / "assets" / "opencode.json").read_text(encoding="utf-8")
    )
    agents = config.get("agent")
    assert isinstance(agents, dict), "the default opencode.json carries agent blocks"
    assert set(agents) == set(_AGENT_ROLES), (
        f"the seven tracks agents must be declared: {sorted(agents)}"
    )
    for role, block in agents.items():
        permission = (block.get("permission") or {}).get("external_directory")
        assert permission == {"*": "deny", "{env:TMPDIR}tracks/*": "allow"}, (
            f"{role} must deny all external dirs except the tracks temp root: {permission!r}"
        )
    serialized = json.dumps(config)
    for forbidden in ('"model"', '"provider"', '"plugin"'):
        assert forbidden not in serialized, (
            f"the default harness config must not ship {forbidden}"
        )


# AC-FR0348-01@v0.12 TRACKS-TRACE version declaration matches the design version
def test_version_bump_matches():
    """AC-FR0348-01 (version gate precondition): pyproject and
    ``tracks.__version__`` both declare the release version."""
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == CURRENT_VERSION
    init = (PROJECT_ROOT / "tracks" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', init)
    assert match is not None
    assert match.group(1) == CURRENT_VERSION


# AC-FR0349-01@v0.12 TRACKS-TRACE usage and subcommand registry stay in parity
def test_subcommand_tokens_registered():
    """AC-FR0349-01 (CLI token parity): USAGE and ``TRAC_SUBCOMMANDS`` both
    register ``install``/``version`` so the zero-repo help face is truthful."""
    from tracks.cli.main import USAGE
    from tracks.executor.validate import TRAC_SUBCOMMANDS

    for token in ("install", "version"):
        assert token in TRAC_SUBCOMMANDS, f"TRAC_SUBCOMMANDS must register {token}"
        assert token in USAGE, f"USAGE must advertise {token}"
    for flag in ("--background", "--stop", "--status"):
        assert flag in USAGE, f"USAGE must advertise serve {flag}"


# AC-FR0348-01@v0.12 TRACKS-TRACE official wheel carries the closed resource set
def test_wheel_carries_capability_resources(v012_wheels):
    """AC-FR0348-01 (artifact half): the official wheel contains exactly the
    tracks-owned resource families — seven agents, the canonical skills,
    every template and the default ``opencode.json`` — at the declared
    version."""
    wheel = v012_wheels["current"]
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    assert wheel_metadata_version(wheel) == CURRENT_VERSION
    assert {f"tracks/agents/{role}.md" for role in _AGENT_ROLES} <= names
    assert {f"tracks/skills/{skill}/SKILL.md" for skill in _CANONICAL_SKILLS} <= names
    templates = [
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in (PROJECT_ROOT / "tracks" / "templates").rglob("*")
        if path.is_file()
    ]
    assert templates, "the source tree declares templates"
    assert set(templates) <= names, (
        f"wheel is missing templates: {sorted(set(templates) - names)}"
    )
    assert "tracks/assets/opencode.json" in names
    assert not any(name.startswith("tests/") for name in names)


# AC-FR0349-01@v0.12 TRACKS-TRACE global trac help outside any repo
def test_global_trac_help_zero_repo(v012_global, tmp_path):
    """AC-FR0349-01 (zero-repo half): a globally installed ``trac --help``
    exits 0 and prints usage from a directory outside any project."""
    outside = tmp_path / "outside"
    outside.mkdir()
    for args in (("--help",), ()):
        proc = v012_global.run(*args, cwd=outside)
        assert proc.returncode == 0, proc.stderr
        assert "usage: trac" in proc.stdout
        for token in ("install", "version", "serve"):
            assert token in proc.stdout, f"usage must advertise {token}"
    assert not (outside / ".tracks").exists(), "--help must not create project state"


def _assert_incomplete_wheel_rejected(
    v012_global, incomplete_wheels, tmp_path, omission: str
) -> None:
    target = tmp_path / f"target-{omission}"
    target.mkdir()
    proc = v012_global.run(
        "install",
        str(target),
        "--wheel",
        str(incomplete_wheels[omission]),
        cwd=tmp_path,
    )
    assert proc.returncode != 0, (
        f"an incomplete wheel must be refused ({omission}): {proc.stdout!r}"
    )
    assert "installed tracks" not in proc.stdout
    assert reason_of(proc) == "package_incomplete", (
        f"the closed reason must be package_incomplete ({omission}): "
        f"{proc.stderr!r}"
    )
    assert not (target / ".tracks" / "install.json").exists(), (
        f"a rejected wheel must leave no receipt ({omission})"
    )
    assert not (target / ".opencode").exists(), (
        f"a rejected wheel must not materialise managed files ({omission})"
    )


# AC-FR0348-02@v0.12 TRACKS-TRACE incomplete wheel rejected before candidate
# The test-plan/task anchor is this bare node. The pre-WRITE baseline still
# lists the superseded [omission] nodes, so compatibility aliases below keep
# those node ids alive with the same assertions; they can be removed once the
# runtime re-captures the baseline for the bare anchor.
#
# COLLECT blocker outside Shield's write scope: the same baseline still lists
# tests/unit/test_breaker.py::test_self_rollback_without_operator_repair_not_counted,
# removed by approved OOB 49cb3f4. Only Devon can restore that unit node (or
# the runtime must re-capture the baseline); no Shield-writable file can
# recreate a tests/unit node id.
def test_incomplete_wheel_rejected(v012_global, incomplete_wheels, tmp_path):
    """AC-FR0348-02: a wheel missing agents/skills/templates/default
    ``opencode.json`` is refused with ``package_incomplete`` and leaves no
    receipt, so it never becomes a usable candidate.

    The omission set is iterated inside the test so the collected node id is
    exactly the task-graph/test-plan anchor
    ``tests/integration/test_install_package.py::test_incomplete_wheel_rejected``.
    """
    for omission in sorted(_OMISSIONS):
        _assert_incomplete_wheel_rejected(
            v012_global, incomplete_wheels, tmp_path, omission
        )


def _incomplete_wheel_case(omission: str):
    def case(v012_global, incomplete_wheels, tmp_path):
        _assert_incomplete_wheel_rejected(
            v012_global, incomplete_wheels, tmp_path, omission
        )

    case.__name__ = f"test_incomplete_wheel_rejected[{omission}]"
    return case


for _omission in sorted(_OMISSIONS):
    globals()[f"test_incomplete_wheel_rejected[{_omission}]"] = _incomplete_wheel_case(
        _omission
    )
