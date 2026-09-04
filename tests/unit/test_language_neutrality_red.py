"""T-008 RED (expanded scope): NFR-0147 language-neutrality cleanup anchors.

architecture NFR-0147 / §3.5: kernel/executor runtime code must be free of
host-language tokens (``pytest|junit|java|venv|wheel|pip``, word boundary,
case-insensitive) outside the declared allowed zones. The attempt-3 T-008
escalation ruling (b) adds three diagnosed files to this task's scope:

1. tracks/kernel/contracts.py — instruction text embeds a ``.venv/bin/...``
   interpreter path (comment/wording-level rewrite).
2. tracks/executor/test_select.py — argv0 fallback hardcodes venv python
   names (``_resolve_argv0`` language-specific detection).
3. tracks/executor/anchor_surface.py — ``.venv/`` skip constant, pytest
   framework detection keyed on literal framework names, and the
   ``.venv/bin/python`` probe resolution (language-neutral form or migration
   out of the executor surface).

The attempt-4 breaker re-diagnosis adds the remaining three diagnosed files
(executor.py x13 was re-shouldered onto T-042 and is cleaned there):

4. tracks/executor/worktree.py — runtime-asset constant and setup prose
   keyed on the ``.venv`` literal.
5. tracks/executor/quality_gate.py — the ``_VENV_PYTHON_RELS`` argv0 set,
   the ``_PYTHON`` interpreter constant, and a gate docstring (the command
   construction must resolve through the IF-HOSTCONTRACT-001
   language/toolchain/install configuration instead).
6. tracks/executor/m_impl_runtime.py — the M-IMPL guard triple embeds
   ``.venv/bin/python`` interpreter paths directly.

Each anchor scans exactly one named file and fails while a banned token
remains, listing ``file:line:token`` for the revision loop. A scanner
control proves the sweep itself is live: it must flag all six tokens on a
synthetic probe and yield zero hits on clean text (guards against a hollow
scanner that matches nothing).
"""

from __future__ import annotations

import re
from pathlib import Path

NEUTRALITY_TOKENS = re.compile(
    r"\b(pytest|junit|java|venv|wheel|pip)\b",
    re.IGNORECASE,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

_TARGET_FILES = (
    "tracks/kernel/contracts.py",
    "tracks/executor/test_select.py",
    "tracks/executor/anchor_surface.py",
)

# attempt-4 breaker re-diagnosis: the remaining three diagnosed files owned
# by this task (executor.py x13 belongs to T-042 and is cleaned there).
_ATTEMPT4_FILES = (
    "tracks/executor/worktree.py",
    "tracks/executor/quality_gate.py",
    "tracks/executor/m_impl_runtime.py",
)


def _fail(message: str) -> None:
    raise AssertionError(f"assertion failure: {message}")


def _scan_text(source: str, origin: str) -> list[str]:
    hits: list[str] = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        for token in NEUTRALITY_TOKENS.findall(line):
            hits.append(f"{origin}:{lineno}:{token.lower()}")
    return hits


def _token_hits(relative_path: str) -> list[str]:
    source = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
    return _scan_text(source, relative_path)


def test_kernel_contracts_instructions_language_neutral():
    hits = _token_hits(_TARGET_FILES[0])
    if hits:
        _fail(f"NFR-0147 banned tokens remain in kernel contracts: {hits}")


def test_test_select_argv0_resolution_language_neutral():
    hits = _token_hits(_TARGET_FILES[1])
    if hits:
        _fail(f"NFR-0147 banned tokens remain in test_select: {hits}")


def test_anchor_surface_language_neutral():
    hits = _token_hits(_TARGET_FILES[2])
    if hits:
        _fail(f"NFR-0147 banned tokens remain in anchor_surface: {hits}")


def test_worktree_runtime_assets_language_neutral():
    hits = _token_hits(_ATTEMPT4_FILES[0])
    if hits:
        _fail(f"NFR-0147 banned tokens remain in worktree: {hits}")


def test_quality_gate_command_construction_language_neutral():
    hits = _token_hits(_ATTEMPT4_FILES[1])
    if hits:
        _fail(f"NFR-0147 banned tokens remain in quality_gate: {hits}")


def test_m_impl_runtime_guard_triple_language_neutral():
    hits = _token_hits(_ATTEMPT4_FILES[2])
    if hits:
        _fail(f"NFR-0147 banned tokens remain in m_impl_runtime: {hits}")


def test_scanner_targets_are_real_nonempty_modules():
    for rel in (*_TARGET_FILES, *_ATTEMPT4_FILES):
        path = _REPO_ROOT / rel
        if not path.is_file():
            _fail(f"neutrality anchor target missing (vacuous sweep): {rel}")
        if path.stat().st_size == 0:
            _fail(f"neutrality anchor target emptied (vacuous sweep): {rel}")


def test_neutrality_scanner_flags_all_tokens_and_spares_clean_text():
    dirty = _scan_text(
        'TOKENS = "pytest junit java venv wheel pip"\n', "probe.py"
    )
    flagged = {hit.rsplit(":", 1)[-1] for hit in dirty}
    if flagged != {"pytest", "junit", "java", "venv", "wheel", "pip"}:
        _fail(
            "scanner control must flag all six tokens, "
            f"got {sorted(flagged)}"
        )
    if _scan_text("CLEAN = 'language free sweep'\n", "clean.py"):
        _fail("scanner control: clean text must yield zero hits")
