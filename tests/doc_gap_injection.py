"""Parent-side installer for the dispatch-window doc-delta hook (T-007 fix).

Why this exists (PRISM-V05-R2-02): the doc-comment ACs (FR-0234/FR-0235/
FR-0236/FR-0237, NFR-0090) require the document delta to land INSIDE the
dispatch window — after the runtime's pre-dispatch document-identity
snapshot, before outcome validation.  Writing the delta before ``trac run``
only changes the baseline: interfaces.md §1k defines the baseline as the
document identity *before this dispatch* and pre-dispatch discussions do not
retrigger the pause.  The injection point is therefore an interpreter-startup
hook inside the ``trac run`` subprocess: ``tests/_doc_gap_hook/sitecustomize.py``
wraps ``tracks.effects.fake_shield.FakeShieldMixin._act_shield`` so the delta
is appended right after the dispatched Shield agent finishes its work — the
"outcome-injection" construction the finding prescribes.  An optional
``baseline`` lands at the fake DESIGN seam instead (pre-dispatch thread for
AC-FR0234-02, human worktree pre-dirty for AC-FR0236-01).

The shared ``trac`` fixture (tests/conftest.py) builds each subprocess
environment from ``os.environ`` at call time, so arming is pure test-process
monkeypatching — no conftest change:

* ``arm_doc_delta(monkeypatch, path=..., text=..., baseline=...)`` sets
  ``TRAC_TEST_DOC_DELTA`` and puts ``tests/_doc_gap_hook`` at the FRONT of
  the ``trac`` subprocess PYTHONPATH (existing PYTHONPATH preserved).  Under
  a coverage run, tests/conftest.py prepends ``_SUBCOV_DIR`` itself, so the
  installer also rewrites that module-level constant to keep the hook dir
  first; the hook then chain-loads the subprocess-coverage sitecustomize via
  runpy, so coverage instrumentation still starts.
* ``assert_doc_delta_landed(host_repo, path, text)`` replaces the old silent
  ``if tp_path.exists():`` guards: after the journey the document must exist
  AND carry the delta, proving the scenario was actually constructed inside
  the dispatch window (the hook itself raises inside ``trac run`` if the
  document is missing at fire time).

Deterministic channel only: the hook wraps the FAKE backend's agent action,
never the runtime's detection/validation under test.  The live channel never
sets ``TRAC_TEST_DOC_DELTA``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent / "_doc_gap_hook"

#: Shield-permitted design document (interfaces.md §1k role scope).
TRACKS_DOC_PLAN = ".tracks/projects/v0.5/test-plan.md"


def arm_doc_delta(
    monkeypatch,
    *,
    path: str,
    text: str,
    substate: str = "WRITE",
    once: bool = True,
    baseline: dict | None = None,
) -> None:
    """Arm the in-subprocess doc-delta hook for the next ``trac`` calls.

    *path*/*text* describe the delta the dispatched Shield agent applies to
    its own outcome (appended at the ``_act_shield`` seam — inside the
    dispatch window).  *baseline*, when given, may carry ``path``/``text``
    (appended to a design document at the fake design seam — pre-dispatch
    content) and/or ``touch`` (``{relpath: content}`` worktree files written
    at the same seam — human-side pre-dirty, never staged).
    """
    config: dict = {
        "target": "shield",
        "path": path,
        "text": text,
        "substate": substate,
        "once": once,
    }
    if baseline is not None:
        config["baseline"] = baseline
    monkeypatch.setenv("TRAC_TEST_DOC_DELTA", json.dumps(config))

    hook_dir = str(HOOK_DIR)
    parts = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    if hook_dir not in parts:
        parts.insert(0, hook_dir)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(parts))

    # Under a coverage run tests/conftest.py prepends _SUBCOV_DIR to the
    # subprocess PYTHONPATH (module-global read at fixture-call time).  Keep
    # the hook dir ahead of it so the hook wins the sitecustomize import and
    # chain-loads the coverage sitecustomize itself.
    import tests.conftest as _conftest

    subcov = _conftest._SUBCOV_DIR
    if subcov and not subcov.startswith(hook_dir + os.pathsep):
        monkeypatch.setattr(
            _conftest, "_SUBCOV_DIR", hook_dir + os.pathsep + subcov, raising=True
        )


def assert_doc_delta_landed(host_repo: Path, path: str, text: str) -> Path:
    """Assert the journey proved the scenario: document exists + delta landed.

    Replaces the vacuous ``if tp_path.exists():`` guards PRISM-V05-R2-02
    flagged: a doc-comment scenario that was never constructed must fail the
    test, not silently skip the delta.
    """
    doc = host_repo / path
    assert doc.is_file(), (
        f"design document {path} must exist after the journey — the doc-comment "
        "scenario cannot be constructed without it"
    )
    content = doc.read_text(encoding="utf-8")
    assert text in content, (
        f"doc delta must land on {path} inside the dispatch window — the hook "
        "did not fire (check tests/_doc_gap_hook PYTHONPATH wiring)"
    )
    return doc
