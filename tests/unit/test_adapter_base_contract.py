"""IF-ADAPTER-001 language-neutral host adapter base contract (T-001 RED unit).

Pins the base adapter seam declared in interfaces.md §1h *before* the GREEN
implementation lands:

- ``TEST_RESULT_PROTOCOL`` / ``TEST_RESULT_PROTOCOL_VERSION`` are the
  versioned ``tracks-test-result`` channel the Runtime consumes exclusively
  (AC-FR0264-01: adapter contract three-interface seam + versioned protocol).
- ``resolve_adapter`` resolves the declared reference adapter
  (``reference-pytest`` / ``tracks-test-result`` / v1) into a conforming
  ``Adapter`` (adapter_id/protocol/protocol_version), or raises
  ``UnknownAdapterError`` for any unknown id/protocol/version -- the kernel
  never enumerates host languages (AC-FR0264-03, NFR-0141).
- the project loader surfaces the ``[adapter]`` declaration as the adapter
  surface it exposes to the Runtime, so Runtime consumes tracks-test-result
  v1 without a host-framework branch (AC-FR0264-01; interfaces §1h/§3).

Each assertion fails today because the GREEN behaviour is not yet implemented
(resolver/loader seam missing) -- this is the M-IMPL RED on the contract.
"""

from __future__ import annotations

import pytest

from tracks.adapters.base import (
    TEST_RESULT_PROTOCOL,
    TEST_RESULT_PROTOCOL_VERSION,
    UnknownAdapterError,
    resolve_adapter,
)
from tracks.project import load_contract

# AC-FR0264-01@v0.7 TRACKS-TRACE three-interface seam resolves reference adapter


def test_resolve_adapter_reference_declaration_resolves_conforming_adapter():
    adapter = resolve_adapter(
        "reference-pytest", TEST_RESULT_PROTOCOL, TEST_RESULT_PROTOCOL_VERSION
    )
    assert adapter is not None
    assert adapter.adapter_id == "reference-pytest"
    assert adapter.protocol == TEST_RESULT_PROTOCOL
    assert adapter.protocol_version == TEST_RESULT_PROTOCOL_VERSION


# AC-FR0264-03@v0.7 TRACKS-TRACE unknown id/protocol/version fail closed
@pytest.mark.parametrize(
    ("adapter_id", "protocol", "version"),
    [
        ("no-such-adapter", TEST_RESULT_PROTOCOL, TEST_RESULT_PROTOCOL_VERSION),
        ("reference-pytest", "not-tracks-test-result", TEST_RESULT_PROTOCOL_VERSION),
        ("reference-pytest", TEST_RESULT_PROTOCOL, 999),
    ],
)
def test_resolve_adapter_unknown_id_protocol_version_fails_closed(
    adapter_id, protocol, version
):
    with pytest.raises(UnknownAdapterError):
        resolve_adapter(adapter_id, protocol, version)


# -- project loader: expose the declared [adapter] as the only adapter surface --


def _write_contract(repo, body):
    toml = repo / ".tracks" / "projects" / "project.toml"
    toml.parent.mkdir(parents=True, exist_ok=True)
    toml.write_text(body, encoding="utf-8")
    return toml


_UINT_SECTION = (
    "[unit]\n"
    'framework = "pytest"\n'
    'paths = ["tests/unit/"]\n'
    'collect = ".venv/bin/python -m pytest --collect-only -q tests/unit/"\n'
    'run = ".venv/bin/python -m pytest tests/unit/ -q -n 8 --dist loadscope '
    '--junitxml={result}"\n'
    'run_selected = ".venv/bin/python -m pytest {nodes} -q -n 8 --dist loadscope '
    '--junitxml={result}"\n'
    'cwd = "."\n\n'
)

_INTEGRATION_SECTION = (
    "[integration]\n"
    'framework = "pytest"\n'
    'paths = ["tests/integration/"]\n'
    'collect = ".venv/bin/python -m pytest --collect-only -q tests/integration/"\n'
    'run = ".venv/bin/python -m pytest tests/integration/ -q -n 8 --dist loadscope '
    '--junitxml={result}"\n'
    'run_selected = ".venv/bin/python -m pytest {nodes} -q -n 8 --dist loadscope '
    '--junitxml={result}"\n'
    'cwd = "."\n\n'
)

_E2E_SECTION = (
    "[e2e]\n"
    'framework = "pytest"\n'
    'paths = ["tests/e2e/"]\n'
    'collect = ".venv/bin/python -m pytest --collect-only -q tests/e2e/"\n'
    'run = ".venv/bin/python -m pytest tests/e2e/ -q -n 8 --dist loadscope '
    '--junitxml={result}"\n'
    'run_selected = ".venv/bin/python -m pytest {nodes} -q -n 8 --dist loadscope '
    '--junitxml={result}"\n'
    'cwd = "."\n\n'
)

_NIGHTLY_SECTION = (
    "[nightly]\n"
    'schedule = "0 3 * * *"\n'
    'workflow = ".github/workflows/nightly.yml"\n'
    'job = "nightly-regression"\n'
    'layers = ["unit", "integration", "e2e"]\n'
    'purpose = "scheduled FULL-suite regression"\n\n'
)

_ADAPTER_SECTION = (
    "[adapter]\n"
    'id = "reference-pytest"\n'
    'protocol = "tracks-test-result"\n'
    "version = 1\n\n"
)


def _body_with_adapter() -> str:
    return (
        _INTEGRATION_SECTION
        + _UINT_SECTION
        + _E2E_SECTION
        + _NIGHTLY_SECTION
        + _ADAPTER_SECTION
    )


# AC-FR0264-01/03@v0.7 TRACKS-TRACE loader exposes only the versioned adapter
# surface (no host-framework branch): Runtime consumes tracks-test-result v1.
def test_project_loader_exposes_only_tracks_test_result_v1(tmp_path):
    _write_contract(tmp_path, _body_with_adapter())
    contract = load_contract(tmp_path)

    adapter = contract.adapter
    assert adapter is not None, "loader must surface the declared [adapter]"
    assert adapter.id == "reference-pytest"
    assert adapter.protocol == TEST_RESULT_PROTOCOL
    assert adapter.version == TEST_RESULT_PROTOCOL_VERSION


# A malformed [adapter] declaration (missing/non-matching keys) must be
# contract_error fail-closed at load time: an undeclared host contract must
# never degrade into a silently unsupported surface the Runtime could guess at
# (AC-FR0264-03: Runtime only consumes the declared versioned protocol).
def test_project_loader_malformed_adapter_declaration_fails_closed(tmp_path):
    _write_contract(
        tmp_path,
        _body_with_adapter().replace('version = 1\n\n', 'version = "1"\n\n'),
    )
    contract = load_contract(tmp_path)
    # RED: the loader surfaces no [adapter] attribute today, so the malformed
    # declaration is not rejected — `contract.adapter` raises AttributeError
    # (contract token gap: the loader must either parse+expose or fail closed).
    assert contract.adapter is None


# AC-FR0264-01@v0.7 TRACKS-TRACE no [adapter]: prevented from guess-branching.
# A pre-v0.7 contract without [adapter] stays loadable but exposes no adapter
# surface (backward compatible; known-ness still enforced by resolve_adapter).
def test_project_loader_absent_adapter_remains_none(tmp_path):
    _write_contract(
        tmp_path,
        _body_with_adapter().replace(_ADAPTER_SECTION, ""),
    )
    contract = load_contract(tmp_path)
    assert contract.adapter is None


# AC-FR0264-01@v0.7 TRACKS-TRACE loader exposes ONLY tracks-test-result v1
# (task: "project loader 只向 Runtime 暴露 tracks-test-result v1，不引入宿主
# 框架分支"): an unknown id/protocol/version must fail closed at the loader
# seam, never degrade into a survivable AdapterDeclaration the Runtime could
# guess at.  RED: the loader surface is absent today, so ``contract.adapter``
# raises AttributeError before known-ness can be enforced (contract token gap).
@pytest.mark.parametrize(
    "declaration",
    [
        'id = "unknown-ruby"\n',
        'protocol = "some-other-protocol"\n',
        "version = 999\n",
    ],
)
def test_project_loader_unknown_adapter_declaration_fails_closed(tmp_path, declaration):
    if declaration.startswith("id"):
        mutated = _body_with_adapter().replace('id = "reference-pytest"\n', declaration)
    elif declaration.startswith("protocol"):
        mutated = _body_with_adapter().replace('protocol = "tracks-test-result"\n', declaration)
    else:
        mutated = _body_with_adapter().replace("version = 1\n", declaration)
    _write_contract(tmp_path, mutated)
    contract = load_contract(tmp_path)
    # Faithful fail-closed surface: an unknown declaration must NOT be coerced
    # into, or faithfully surfaced as, a tracks-test-result v1 adapter the
    # Runtime could consume; it must fail closed at the loader seam (None).
    # RED: the loader surface is absent today, so ``contract.adapter`` raises
    # AttributeError before known-ness can be enforced (contract token gap).
    assert contract.adapter is None
