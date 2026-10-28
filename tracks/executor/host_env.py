"""IF-HOSTENV-001 process-boundary env allowlist (design-delta INV-8).

``host_process_env`` is the single environment source for every
runtime-spawned host subprocess. It is an allowlist, not a denylist: the
child receives the cross-language toolchain variables declared below plus
the host's explicit escape hatch, and nothing else. The retired denylist
could only enumerate the leaks someone already knew about.

Base allowlist (cross-language toolchain):
  PATH HOME USER LOGNAME SHELL TMPDIR TEMP TMP LANG LC_ALL LC_CTYPE
  SYSTEMROOT SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE XDG_CACHE_HOME
  CI VIRTUAL_ENV PYTHONPATH PYTHONHASHSEED PYTHONDONTWRITEBYTECODE
  UV_CACHE_DIR PIP_CACHE_DIR CARGO_HOME RUSTUP_HOME RUSTFLAGS NODE_PATH
  NVM_DIR VOLTA_HOME PLAYWRIGHT_BROWSERS_PATH GOPATH GOROOT GOCACHE
  GOMODCACHE GOPROXY JAVA_HOME GRADLE_USER_HOME

Test-sizing knobs: TRAC_FORENSICS_DIR TRAC_BATTERY_TIMEOUT

Strip set (always wins, including over ``extra`` and the escape hatch):
  GITHUB_* GH_* TRAC_GITHUB_* TRAC_SERVE_* TRAC_AGENT_* TRAC_FAKE_*
  GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE *_TOKEN *_SECRET *_API_KEY

Escape hatch: ``[host-contract].env_allowlist`` in project.toml names
host-declared non-secret variables. ``contract_env_allowlist`` resolves it
for a repo; a missing or malformed contract yields the empty tuple (the
strictly more restrictive choice), never a wider environment.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

BASE_ALLOWLIST = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TMPDIR",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "SYSTEMROOT",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "XDG_CACHE_HOME",
        "CI",
        "VIRTUAL_ENV",
        "PYTHONPATH",
        "PYTHONHASHSEED",
        "PYTHONDONTWRITEBYTECODE",
        "UV_CACHE_DIR",
        "PIP_CACHE_DIR",
        "CARGO_HOME",
        "RUSTUP_HOME",
        "RUSTFLAGS",
        "NODE_PATH",
        "NVM_DIR",
        "VOLTA_HOME",
        "PLAYWRIGHT_BROWSERS_PATH",
        "GOPATH",
        "GOROOT",
        "GOCACHE",
        "GOMODCACHE",
        "GOPROXY",
        "JAVA_HOME",
        "GRADLE_USER_HOME",
        "TRAC_FORENSICS_DIR",
        "TRAC_BATTERY_TIMEOUT",
    }
)

_STRIP_PREFIXES = (
    "GITHUB_",
    "GH_",
    "TRAC_GITHUB_",
    "TRAC_SERVE_",
    "TRAC_AGENT_",
    "TRAC_FAKE_",
)
_STRIP_NAMES = frozenset({"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"})
_STRIP_SUFFIXES = ("_TOKEN", "_SECRET", "_API_KEY")


def _stripped(name: str) -> bool:
    """True when *name* belongs to the closed operator-secret strip set."""
    return (
        name in _STRIP_NAMES
        or name.startswith(_STRIP_PREFIXES)
        or name.endswith(_STRIP_SUFFIXES)
    )


def host_process_env(
    extra: Mapping[str, str] | None = None,
    contract_allowlist: Iterable[str] = (),
) -> dict[str, str]:
    """Return the allowlisted environment for a runtime-spawned host process.

    ``extra`` is the explicit runtime overlay (hook root/bin, worktree
    PYTHONPATH) and overrides base values. ``contract_allowlist`` is the
    host's declared escape hatch. The strip set is closed over both: a named
    operator secret never reaches the child even if an overlay or the host
    contract asks for it.
    """
    allowed = BASE_ALLOWLIST | frozenset(contract_allowlist)
    env = {
        name: value
        for name, value in os.environ.items()
        if name in allowed and not _stripped(name)
    }
    for name, value in (extra or {}).items():
        if not _stripped(name):
            env[name] = value
    return env


def contract_env_allowlist(repo: str | Path | None) -> tuple[str, ...]:
    """Resolve ``[host-contract].env_allowlist`` for the host project *repo*.

    Walks up from *repo* to the project root so sites whose cwd is a nested
    section still see the single contract. A missing/unreadable/malformed
    contract returns the empty tuple: the escape hatch can only widen what
    the host explicitly declared, never fail open.
    """
    if repo is None:
        return ()
    from tracks import paths
    from tracks.executor.host_contract import load_host_contract

    root = Path(repo)
    candidates = [
        base / ".tracks" / "projects" / "project.toml"
        for base in (root, *root.parents)
    ]
    try:
        relocated = paths.project_toml_path(paths.tracks_home(root))
    except (OSError, ValueError):
        relocated = None
    if relocated is not None and relocated not in candidates:
        candidates.append(relocated)
    for path in candidates:
        if not path.is_file():
            continue
        try:
            return tuple(load_host_contract(path).env_allowlist)
        except (OSError, ValueError):
            return ()
    return ()
