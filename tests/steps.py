"""Structured test-step logging (NDJSON, one file per test).

Goal: let a *separate* Agent replay what a test did — every CLI invocation,
fixture setup, and assertion step with a monotonic sequence number and wall-clock
timing — and independently judge whether steps are missing or out of order.

Format (NDJSON, one record per line):

    {"seq":1,"ts_ms":1234567890,"step":"trac init","status":"ok","detail":"..."}

Contracts:
- ``seq`` starts at 1 and increments once per ``step()`` call (both for the
  fixture and any sub-calls), so the log is a total order.
- ``ts_ms`` is ``time.time()*1000`` at record time.
- ``step`` is a short, stable label (Agent-consumable key).
- ``status`` is ``ok`` normally, ``fail`` when an assertion later in the test
  marks the step as the failure point.
- ``detail`` is free-form (stdout/stderr/returncode/params), never secrets.

Location: ``$TRACKS_LIVE_STEPS_DIR`` (or ``.tracks/steps`` under the repo root)
/<pytest nodeid sanitized>.steps.ndjson. One file per test; each test appends
to its own file (never truncated mid-run so a crashed test still leaves logs).
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path


def steps_dir() -> Path:
    """Root dir for step logs; override via env for CI artifact collection."""
    override = os.environ.get("TRACKS_STEPS_DIR", "").strip()
    if override:
        p = Path(override)
        p.mkdir(parents=True, exist_ok=True)
        return p
    # Fall back to a temp location: never pollute the host repo.
    base = Path(os.environ.get("TMPDIR", "/tmp")) / "tracks-steps"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _node_id(node: str) -> str:
    return node.replace("::", "__").replace("/", "_").replace(".py", "")


class StepLog:
    """Append-only NDJSON writer for one test's steps."""

    def __init__(self, nodeid: str, truncate: bool = False):
        self.path = steps_dir() / f"{_node_id(nodeid)}.steps.ndjson"
        # A fresh test run for the same nodeid starts seq at 1; an interrupted
        # prior run (crash) is preserved, so a new run appends with a marker.
        self._seq = 0
        if truncate and self.path.exists():
            self.path.unlink()
        self._fh = self.path.open("a", encoding="utf-8")

    def step(self, step: str, status: str = "ok", **detail) -> None:
        self._seq += 1
        rec = {"seq": self._seq, "ts_ms": int(time.time() * 1000), "step": step, "status": status}
        if detail:
            # Values may be non-JSON (e.g. CompletedProcess); coerce safely.
            rec["detail"] = _safe(detail)
        try:
            self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._fh.flush()
        except OSError:
            pass  # logging must never crash a test

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._fh.close()


def _safe(obj):
    if isinstance(obj, dict):
        return {k: _safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe(v) for v in obj]
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)
