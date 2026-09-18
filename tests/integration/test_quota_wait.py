"""Quota waits (IF-WAIT-001)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracks.supervisor.waiting import WaitPolicy, classify_wait

pytestmark = pytest.mark.integration


def _samples() -> dict:
    asset = Path(__file__).resolve().parents[1] / "assets" / "v0.9" / "quota_samples.json"
    return json.loads(asset.read_text(encoding="utf-8"))


# AC-FR0299-01@v0.9 TRACKS-TRACE known reset auto resume
def test_known_reset_auto_resume():
    """AC-FR0299-01: known reset persists retry_at and auto-resumes at reset."""
    samples = _samples()
    spec = classify_wait({"error": "quota_exceeded", "reset_at": samples["known_reset"]["reset_at"]})
    assert spec.known_reset is True
    assert spec.retry_at == samples["known_reset"]["reset_at"]


# AC-FR0299-02@v0.9 TRACKS-TRACE unknown reset probe plan no countdown
def test_unknown_reset_probe_plan_no_countdown():
    """AC-FR0299-02: unknown reset yields bounded probe plan, no countdown."""
    samples = _samples()
    spec = classify_wait({"error": "rate_limited"})
    assert spec.known_reset is False
    assert spec.retry_at is None
    assert spec.backoff is not None
    assert spec.backoff["interval_s"] >= WaitPolicy().initial_s
    assert samples["unknown_reset"]["known_reset"] is False
