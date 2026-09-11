"""Frozen IF-ENVELOPE-002 checks for complete declarations and missing replies."""
from __future__ import annotations

import pytest

from tracks.kernel.envelope import check_envelope_parity

pytestmark = pytest.mark.integration
FACES = ("prompt", "agent", "skill", "fake_backend", "real_backend", "validator")


# AC-FR0279-01: all six declared surfaces must be checked before dispatch.
@pytest.mark.parametrize("bad", [None, "", "garbage", "tracks-envelope:v1"])
def test_parity_rejects_undeclared_or_invalid_surface(bad):
    faces = dict.fromkeys(FACES, "tracks-envelope:v2")
    faces["skill"] = bad
    assert check_envelope_parity(faces)["consistent"] is False


def test_parity_requires_all_six_surfaces():
    faces = dict.fromkeys(FACES, "tracks-envelope:v2")
    assert check_envelope_parity(faces)["consistent"] is True
    del faces["agent"]
    assert check_envelope_parity(faces)["consistent"] is False


# AC-FR0278-02/AC-NFR0145-02: declared replies cannot escape format handling.


