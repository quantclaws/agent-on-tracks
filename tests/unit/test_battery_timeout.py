"""Battery wall-clock bound (test-compression P0-2, 2026-10-02).

A pathological layer command (real-backend journey on the infra backoff
ladder) must fail closed in bounded time instead of hanging run_tests for
hours (live fire: run 01M3E7SAANXKW1V73W8B8Q3G86, 2026-10-01, a 2h+ hung
battery whose main thread sat in the backoff ladder's time_sleep)."""

import subprocess

import pytest

from tracks.executor.test_execute import _battery_timeout_seconds


def test_timeout_formula_base_and_per_node():
    assert _battery_timeout_seconds(0) == 900
    assert _battery_timeout_seconds(1) == 960
    assert _battery_timeout_seconds(10) == 1500


def test_timeout_env_overrides_and_floor(monkeypatch):
    monkeypatch.setenv("TRAC_BATTERY_TIMEOUT_BASE", "60")
    monkeypatch.setenv("TRAC_BATTERY_TIMEOUT_PER_NODE", "1")
    assert _battery_timeout_seconds(30) == 90
    # garbage env falls back to defaults, never crashes
    monkeypatch.setenv("TRAC_BATTERY_TIMEOUT_BASE", "not-a-number")
    assert _battery_timeout_seconds(0) == 900
    # negative node counts clamp to the base; the total never goes below 60s
    assert _battery_timeout_seconds(-5) >= 60


def test_timeout_expired_routes_test_select_error(monkeypatch, tmp_path):
    """The subprocess.TimeoutExpired branch must surface as TestSelectError
    (contract_error channel: fail-closed, no author attempt burned) and
    record the timeout in the layer logs."""

    from tracks.executor import test_execute as te

    class _Boom:
        def __init__(self, *a, **k):
            exc = subprocess.TimeoutExpired(cmd="pytest", timeout=960)
            exc.stdout = b"partial"  # subprocess.run attaches these post-hoc
            exc.stderr = b""
            raise exc

    monkeypatch.setattr(te.subprocess, "run", _Boom)

    class _Cmd:
        command_id = "CMD"

    class _Section:
        cwd = "."
        run_selected = "pytest {nodes}"

    executor = te.ExecTestRunMixin.__new__(te.ExecTestRunMixin)
    executor.repo = tmp_path
    executor._result_staging_path = lambda *a: tmp_path / "x.xml"
    logs = {}
    with pytest.raises(te.TestSelectError, match="battery_timeout"):
        executor._run_layer_command(
            _Cmd(), "integration", _Section(), ["n1", "n2"], [], logs
        )
    assert logs["integration"]["battery_timeout"] == 900 + 60 * 2
    assert logs["integration"]["returncode"] is None
