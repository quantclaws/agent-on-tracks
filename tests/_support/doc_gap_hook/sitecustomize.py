"""Interpreter-startup hook: dispatch-window doc-delta injection (T-007 fix).

Loaded via PYTHONPATH only when ``tests/_support/doc_gap_injection.py`` puts this
directory on a ``trac run`` subprocess's PYTHONPATH.  No-op unless
``TRAC_TEST_DOC_DELTA`` is set, so ordinary subprocesses are unaffected.

Why a sitecustomize hook: the doc-delta must land INSIDE the agent dispatch
window (after the runtime's pre-dispatch document snapshot, before outcome
validation) so the doc-comment-first contract (interfaces.md §1k/§1m,
AC-FR0234-01/02) can observe it.  Writing the delta before ``trac run`` only
changes the baseline and can never trigger detection — exactly the vacuous
pattern PRISM-V05-R2-02 flagged.  The hook wraps ``FakeShieldMixin._act_shield``
in the subprocess (deterministic fake backend only; the live channel never
sets ``TRAC_TEST_DOC_DELTA``), so the append happens while the dispatched
Shield agent "is working".

Chain-loading: Python imports only the FIRST ``sitecustomize`` found on
sys.path.  When this hook wins the lookup (layer conftest files preserves
caller PYTHONPATH precedence under coverage), it executes the next
``sitecustomize.py`` later on sys.path — e.g. the subprocess-coverage hook in
``tests/_subprocess_coverage`` — so coverage instrumentation still starts.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_MARKER = "_doc_gap_hook_installed"


def _chain_next_sitecustomize() -> None:
    """Execute the next sitecustomize.py appearing later on sys.path."""
    own_dir = Path(__file__).resolve().parent
    seen_own = False
    for entry in sys.path:
        if not entry:
            continue
        try:
            candidate_dir = Path(entry).resolve()
        except OSError:
            continue
        if candidate_dir == own_dir:
            seen_own = True
            continue
        if not seen_own:
            continue
        candidate = candidate_dir / "sitecustomize.py"
        if candidate.is_file():
            import runpy

            runpy.run_path(str(candidate), run_name="sitecustomize_chained")
            return


def _install_doc_delta_hook() -> None:
    if getattr(sys, _MARKER, False):
        return
    setattr(sys, _MARKER, True)

    raw = os.environ.get("TRAC_TEST_DOC_DELTA", "")
    if not raw:
        return  # ordinary subprocess — nothing to inject

    config = json.loads(raw)  # malformed config must fail loud, not silently
    target = config.get("target", "shield")
    if target != "shield":
        raise ValueError(f"TRAC_TEST_DOC_DELTA target {target!r} is not wired")
    for key in ("path", "text"):
        if not config.get(key):
            raise ValueError(f"TRAC_TEST_DOC_DELTA requires a non-empty {key!r}")
    fire_substate = config.get("substate", "WRITE")
    fire_once = bool(config.get("once", True))
    rel_path = config["path"]
    text = config["text"]
    baseline = config.get("baseline") or {}

    from tracks.effects.fake import FakeBackend
    from tracks.effects.fake_shield import FakeShieldMixin

    state = {"fired": 0, "baseline_fired": 0}

    original = FakeShieldMixin._act_shield

    def _act_shield_with_doc_delta(self, substate, assignment):
        result = original(self, substate, assignment)
        if substate == fire_substate and (not fire_once or state["fired"] == 0):
            state["fired"] += 1
            doc = self.repo / rel_path
            if not doc.is_file():
                raise RuntimeError(
                    f"doc-delta hook target missing after dispatch: {doc} "
                    "(the delta must land inside the dispatch window, after the "
                    "document exists)"
                )
            doc.write_text(
                doc.read_text(encoding="utf-8") + text, encoding="utf-8"
            )
        return result

    FakeShieldMixin._act_shield = _act_shield_with_doc_delta

    if baseline:
        # Baseline seam: applied as part of the fake DESIGN agent's own work
        # (right after the design documents are written), so it is part of the
        # document baseline the next dispatched outcome is diffed against —
        # a pre-dispatch thread (AC-FR0234-02) or human-side worktree pre-dirty
        # (AC-FR0236-01), never attributable to the Shield outcome.
        original_write_design = FakeBackend._write_design

        def _write_design_with_baseline(self, token, scaffold=None):
            result = original_write_design(self, token, scaffold)
            if state["baseline_fired"] == 0:
                state["baseline_fired"] += 1
                base_path = baseline.get("path")
                if base_path:
                    doc = self.repo / base_path
                    if not doc.is_file():
                        raise RuntimeError(
                            f"baseline hook target missing after design write: {doc}"
                        )
                    doc.write_text(
                        doc.read_text(encoding="utf-8")
                        + baseline["text"],
                        encoding="utf-8",
                    )
                for rel, content in (baseline.get("touch") or {}).items():
                    touched = self.repo / rel
                    touched.parent.mkdir(parents=True, exist_ok=True)
                    touched.write_text(content, encoding="utf-8")
            return result

        FakeBackend._write_design = _write_design_with_baseline


try:
    _install_doc_delta_hook()
finally:
    _chain_next_sitecustomize()
