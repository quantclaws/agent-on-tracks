"""Offline wheels of installed test tools, for real dependency installation tests."""

import base64
import csv
import hashlib
import io
import zipfile
from importlib.metadata import distribution
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def tool_wheelhouse(root: Path, package: str) -> Path:
    """Copy a pure-Python tool and its active runtime dependencies into wheels.

    The fresh host installs these through pip; no parent site-packages path,
    virtualenv symlink, package index, or preinstalled global tool is required.
    """
    root.mkdir(parents=True, exist_ok=True)
    pending, seen = [package], set()
    while pending:
        name = canonicalize_name(pending.pop())
        if name in seen:
            continue
        seen.add(name)
        dist = distribution(name)
        assert "Root-Is-Purelib: true" in dist.read_text("WHEEL"), name
        _write_tool_wheel(root, dist)
        for raw in dist.requires or ():
            requirement = Requirement(raw)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)
    return root


def _write_tool_wheel(root, dist):
    name = canonicalize_name(dist.metadata["Name"]).replace("-", "_")
    wheel = root / f"{name}-{dist.version}-py3-none-any.whl"
    records = []
    record_path = ""
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
        for member in dist.files or ():
            if ".." in member.parts or "__pycache__" in member.parts:
                continue
            path = dist.locate_file(member)
            if str(member).endswith(".dist-info/RECORD"):
                record_path = str(member)
            elif path.is_file():
                data = path.read_bytes()
                archive.writestr(str(member), data)
                digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
                records.append((str(member), "sha256=" + digest, len(data)))
        assert record_path, "installed distribution must carry its RECORD"
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerows([*records, (record_path, "", "")])
        archive.writestr(record_path, buffer.getvalue())
