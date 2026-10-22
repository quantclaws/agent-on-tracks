"""``trac install`` / ``trac version`` command faces (IF-INSTALL-001 /
IF-VERSIONVIS-001 / IF-GLOBALCLI-001).

Success/failure outlets are the locked CLI contract (interfaces §1c.4/§1c.5
and §2a): install prints exactly one success line or a closed-reason stderr
line; version reports the global or project scope from the receipt.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tracks import __version__, install, project_runtime
from tracks.install import INSTALL_USAGE as INSTALL_USAGE

VERSION_USAGE = "usage: trac version [--json]"


def _install_failure(reason: str, detail: str) -> int:
    print(f"trac install: {reason}: {detail}", file=sys.stderr)
    return 1


def cmd_install(repo: Path, *args: str) -> int:
    try:
        target, version, artifact = install.parse_install_args(repo, args)
        installed = install.install_project(target, version, artifact)
    except install.InstallError as exc:
        return _install_failure(exc.reason, exc.detail)
    print(f"installed tracks {installed.version} at {installed.target}")
    return 0


def _version_flags(args: tuple[str, ...]) -> bool:
    as_json = False
    for argument in args:
        if argument == "--json":
            as_json = True
        else:
            raise install.InstallError("usage", VERSION_USAGE)
    return as_json


def cmd_version(repo: Path, *args: str) -> int:
    try:
        as_json = _version_flags(args)
        root = project_runtime.find_project(repo)
        if root is None:
            return _global_version(as_json)
        receipt = project_runtime.read_receipt(root)
    except project_runtime.ProjectCorrupt as exc:
        print(f"install_corrupt: {exc}", file=sys.stderr)
        return 1
    except install.InstallError as exc:
        print(exc.detail, file=sys.stderr)
        return 1
    return _project_version(root, receipt, as_json)


def _global_version(as_json: bool) -> int:
    if as_json:
        print(json.dumps({"scope": "global", "version": __version__}))
    else:
        print(f"trac {__version__} (global)")
    return 0


def _project_version(root: Path, receipt: dict, as_json: bool) -> int:
    site_packages = root / receipt["site_packages"]
    if as_json:
        print(
            json.dumps(
                {
                    "scope": "project",
                    "version": receipt["version"],
                    "project_root": str(root),
                    "site_packages": str(site_packages),
                }
            )
        )
    else:
        print(f"trac {receipt['version']} (project {root})")
    return 0
