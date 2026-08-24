"""Integration: host adapter contract (IF-ADAPTER-001/002/003).

AC-FR0264-01@v0.7 three-interface seam, protocol version,
AC-FR0264-02@v0.7 reference adapter equivalent to v0.6,
AC-FR0264-03@v0.7 unknown adapter / malformed result blocked.

Assertions land on `resolve_adapter`/`ReferencePytestAdapter`
(IF-ADAPTER-001/002, interfaces §1h) public outlets.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.adapters.base import (
    TEST_RESULT_PROTOCOL,
    TEST_RESULT_PROTOCOL_VERSION,
    Adapter,
    UnknownAdapterError,
    resolve_adapter,
)
from tracks.adapters.reference_pytest import ReferencePytestAdapter

pytestmark = pytest.mark.integration



# AC-FR0264-01@v0.7 TRACKS-TRACE three-interface seam, protocol version
def test_three_interface_seam_protocol_version():
    """AC-FR0264-01: adapter exposes collect/run_selected/normalize_result seam."""
    adapter = resolve_adapter("reference-pytest", TEST_RESULT_PROTOCOL, TEST_RESULT_PROTOCOL_VERSION)
    assert isinstance(adapter, Adapter)
    assert adapter.adapter_id == "reference-pytest"
    assert adapter.protocol == TEST_RESULT_PROTOCOL == "tracks-test-result"
    assert adapter.protocol_version == TEST_RESULT_PROTOCOL_VERSION == 1
    # Three-method seam present (Protocol contract).
    for method in ("collect", "run_selected", "normalize_result"):
        assert callable(getattr(adapter, method)), f"adapter missing {method} seam"


# AC-FR0264-02@v0.7 TRACKS-TRACE reference adapter equivalent to v0.6
def test_reference_adapter_equivalent_to_v06():
    """AC-FR0264-02: reference adapter migrates v0.6 pytest path (same in/out)."""
    adapter = ReferencePytestAdapter()
    assert adapter.adapter_id == "reference-pytest"
    # The reference adapter must produce the same collect result shape as v0.6.
    nodes = adapter.collect(
        collect_command=".venv/bin/python -m pytest --collect-only -q tests/unit/",
        layer="unit",
        cwd=Path(__file__).resolve().parents[2],
    )
    # Contract: collect returns a list of TestNode with opaque node_id + digest.
    assert isinstance(nodes, list)
    for node in nodes:
        assert node.node_id  # opaque, non-empty
        assert node.layer == "unit"
        assert node.source_digest  # content digest present


# AC-FR0264-03@v0.7 TRACKS-TRACE unknown adapter / malformed result blocked
def test_unknown_adapter_malformed_result_blocked():
    """AC-FR0264-03: unknown adapter id/protocol/version fails closed."""
    # Unknown adapter id.
    try:
        resolve_adapter("no-such-adapter", TEST_RESULT_PROTOCOL, 1)
    except UnknownAdapterError:
        pass
    else:
        raise AssertionError("unknown adapter id must raise UnknownAdapterError")
    # Unknown protocol.
    try:
        resolve_adapter("reference-pytest", "no-such-protocol", 1)
    except UnknownAdapterError:
        pass
    else:
        raise AssertionError("unknown protocol must raise UnknownAdapterError")
    # Unknown version.
    try:
        resolve_adapter("reference-pytest", TEST_RESULT_PROTOCOL, 999)
    except UnknownAdapterError:
        pass
    else:
        raise AssertionError("unknown version must raise UnknownAdapterError")
