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
