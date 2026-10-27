"""IF-HOSTENV-001 process-boundary env allowlist (interface stub).

`host_process_env` is the single allowlist wrapper for every
runtime-spawned host subprocess (design-delta INV-8). Denylist is
abolished as a source of truth.

Base allowlist (cross-language toolchain):
  PATH HOME USER LOGNAME SHELL TMPDIR TEMP TMP LANG LC_ALL LC_CTYPE
  SYSTEMROOT SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE XDG_CACHE_HOME
  CI VIRTUAL_ENV PYTHONPATH PYTHONHASHSEED PYTHONDONTWRITEBYTECODE
  UV_CACHE_DIR PIP_CACHE_DIR CARGO_HOME RUSTUP_HOME RUSTFLAGS NODE_PATH
  NVM_DIR VOLTA_HOME PLAYWRIGHT_BROWSERS_PATH GOPATH GOROOT GOCACHE
  GOMODCACHE GOPROXY JAVA_HOME GRADLE_USER_HOME
Test sizing knobs: TRAC_FORENSICS_DIR TRAC_BATTERY_TIMEOUT

Strip set:
  GITHUB_* GH_* TRAC_GITHUB_* TRAC_SERVE_* TRAC_AGENT_* TRAC_FAKE_*
  GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE *_TOKEN *_SECRET *_API_KEY

Escape hatch: [host-contract].env_allowlist in project.toml for
host-declared non-secret vars.

Stub for Shield contract tests; behavior implemented by Devon
(ARCH-012-HF262 §2).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def host_process_env(
    extra: Mapping[str, str] | None = None,
    contract_allowlist: Iterable[str] = (),
) -> dict:
    raise NotImplementedError("IF-HOSTENV-001")
