"""Unit helpers: build EventEnvelope lists for pure-function tests (NFR-02)."""
from tracks.kernel.events import EventEnvelope


def ev(seq, type, payload=None, run_id="RUN", version="v0.1",
       command_id=None, task_id=None):
    return EventEnvelope(
        seq=seq, ts="2026-07-30T00:00:00+00:00", run_id=run_id,
        version=version, type=type, schema_version=1,
        command_id=command_id, task_id=task_id, payload=payload or {},
    )


def seq(*items):
    """items: (type, payload) tuples -> enumerated envelopes."""
    return [ev(i + 1, t, p) for i, (t, p) in enumerate(items)]


def capture_popen_cmd(monkeypatch,
                      target="tracks.effects.opencode.subprocess.Popen"):
    """Patch ``subprocess.Popen`` with a stub that records the argv of the next
    call and returns a minimal exit-0 process (empty JSON stdout). Returns the
    dict whose ``cmd`` key holds the captured argv list. Shared by the unit
    tests that assert on the opencode ``run`` command shape."""
    captured = {}

    class _StubProc:
        pid = 123
        returncode = 0

        def communicate(self, input=None, timeout=None):
            return "{}", ""

    def _fake_popen(*args, **kwargs):
        captured["cmd"] = args[0]
        return _StubProc()

    monkeypatch.setattr(target, _fake_popen)
    return captured

