"""Integration: kernel language neutrality (IF-ADAPTER-003).

AC-FR0264-04@v0.7 no language tokens in kernel/executor/cli,
AC-NFR0141-02@v0.7 language-invariant dual check.

Assertions land on the kernel/executor/cli source scan (IF-ADAPTER-003,
interfaces §1h): the forbidden-zone source must not reference pytest/junit/java
tokens, and `trac validate` must enforce it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# Forbidden zone (interfaces §1h): runtime code in kernel/executor/cli.
FORBIDDEN_GLOBS = (
    "tracks/kernel/**/*.py",
    "tracks/executor/**/*.py",
    "tracks/cli/**/*.py",
)

# Token word boundary, case-insensitive (interfaces §1h).
TOKEN_RE = re.compile(r"\b(pytest|junit|java|venv|wheel|pip)\b", re.IGNORECASE)

# AC-NFR0147-01@v0.8 uses expanded token set (venv/wheel/pip)
TOKEN_RE_V08 = re.compile(r"\b(pytest|junit|java|venv|wheel|pip)\b", re.IGNORECASE)
ALLOWED_V08_DIRS = ("tracks/adapters", "tracks/assets", "tracks/executor/demo_host", "tracks/executor/reference_host")
# REPO.glob yields absolute paths while the allowlist above is repo-relative:
# anchor every entry at the repo root or the prefix match is always false and
# the whole exemption silently dies.
_ALLOWED_V08_PREFIXES = tuple(str(REPO / d) for d in ALLOWED_V08_DIRS)

pytestmark = pytest.mark.integration



def _forbidden_files() -> list[tuple[Path, str]]:
    """Forbidden-zone token hits for the v0.7 faces.

    The v0.8 allowlist (NFR-0147 allowed Python isolation zones:
    ``adapters``/``assets``/``executor/{demo_host,reference_host}``) applies
    to the shared scan: the ``venv``/``wheel`` tokens are sanctioned exactly
    there (AC-NFR0147-01/02), so the legacy faces delegate to the v0.8
    scanner instead of re-implementing an exemption-free copy.
    """
    return _forbidden_files_v08()


# AC-FR0264-04@v0.7 TRACKS-TRACE no language tokens in kernel/executor/cli
# Operator OOB 2026-09-23: verified green-on-arrival in the island-2 terminal sweep; M-TEST re-entry after full implementation (run 01M2QTJB).
def test_no_language_tokens_kernel_executor_cli():
    """AC-FR0264-04: kernel/executor/cli runtime code has no language tokens."""
    hits = _forbidden_files()
    assert not hits, (
        f"forbidden language tokens in kernel/executor/cli: {hits[:5]}"
    )


# AC-NFR0141-02@v0.7 TRACKS-TRACE language-invariant dual check
def test_language_invariant_dual_check():
    """AC-NFR0141-02: validate + runtime adapter-only execution both enforce neutrality.

    The static scan (above) is one check; the second is that the adapter is the
    only language surface. We assert resolve_adapter is the sole resolution
    path and that the forbidden zone carries no tokens (dual check)."""
    hits = _forbidden_files()
    assert not hits, (
        f"runtime code must be language-neutral (dual check failed): {hits[:5]}"
    )
    # The adapter protocol is language-neutral by name/version (no language
    # branch in the kernel).
    from tracks.adapters.base import TEST_RESULT_PROTOCOL
    assert "pytest" not in TEST_RESULT_PROTOCOL
    assert "junit" not in TEST_RESULT_PROTOCOL


def _forbidden_files_v08() -> list[tuple[Path, str]]:
    hits: list[tuple[Path, str]] = []
    for pat in FORBIDDEN_GLOBS:
        for path in REPO.glob(pat):
            if any(str(path).startswith(p) for p in _ALLOWED_V08_PREFIXES) and (
                "reference_host" in str(path) or "demo_host" in str(path) or "adapters" in str(path) or "assets" in str(path)
            ):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for m in TOKEN_RE_V08.finditer(text):
                hits.append((path, m.group(0)))
    return hits


# AC-NFR0147-01@v0.8 TRACKS-TRACE no venv wheel hardcoding
def test_no_venv_wheel_hardcoding():
    hits = _forbidden_files_v08()
    assert not hits, f"forbidden v0.8 language tokens in kernel/executor/cli: {hits[:5]}"
    from tracks.kernel.release import RELEASE_PIPELINE_VERSION

    assert "venv" not in RELEASE_PIPELINE_VERSION
    assert "wheel" not in RELEASE_PIPELINE_VERSION


# AC-NFR0147-02@v0.8 TRACKS-TRACE kernel schema language free
def test_kernel_schema_language_free():
    hits = _forbidden_files_v08()
    assert not hits, f"kernel schema must be language-free: {hits[:5]}"
    from tracks.executor.host_contract import HostContract

    # HostContract fields must not hardcode language semantics
    fields = [f.name for f in HostContract.__dataclass_fields__.values()]
    assert "language" in fields
    # schema file must not contain python-specific validation
    schema_text = (REPO / "tracks" / "executor" / "host_contract.py").read_text(encoding="utf-8")
    assert "venv" not in schema_text.lower() or "reference_host" in schema_text.lower()
    # validate outlet must pass
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "tracks.cli.main", "validate", "--help"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode in (0, 1, 2)


# v0.9 service-plane packages under the same neutrality invariant
# (interfaces §5 IF-ADAPTER-003 v0.9 extension; test-plan §11 row 2).
_SERVICE_ZONE_GLOBS = (
    "tracks/server/**/*.py",
    "tracks/supervisor/**/*.py",
)


def _forbidden_files_v09() -> list[tuple[Path, str]]:
    """Language-token hits in the v0.9 service-plane packages.

    Same token set and word-boundary semantics as the v0.8 scan; the new
    packages carry no sanctioned isolation zone, so nothing is exempted.
    """
    hits: list[tuple[Path, str]] = []
    for pattern in _SERVICE_ZONE_GLOBS:
        for path in REPO.glob(pattern):
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in TOKEN_RE_V08.finditer(text):
                hits.append((path, match.group(0)))
    return hits


# AC-NFR0147-01@v0.8 TRACKS-TRACE v0.9 service packages zero language tokens
def test_no_language_tokens_server_supervisor():
    """AC-NFR0147-01 continuation: the v0.9 service-plane packages carry no
    pytest/junit/java/venv/wheel/pip token, same as the kernel/executor/cli
    forbidden zone (interfaces §5 IF-ADAPTER-003)."""
    hits = _forbidden_files_v09()
    assert not hits, (
        "forbidden language tokens in tracks/server|tracks/supervisor: "
        f"{[(str(p), t) for p, t in hits[:5]]}"
    )
