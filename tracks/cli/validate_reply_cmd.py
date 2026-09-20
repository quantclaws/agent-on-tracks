"""``trac validate-reply --file PATH --kind KIND`` command face.

#174 agent-side self-verification (OOB 2026-09-20, run 01M2QTJB): the agent
writes its two-layer envelope to the dispatch's result_path with
``json.dump`` and runs this command BEFORE replying (see
``kernel.contracts.result_file_contract`` ``verify_command``). Zero exit =
the file parses and validates against the kind schema; any failure exits 1
with the concrete reason on stderr.

Extracted as its own module for C0302: ``tracks.cli.main`` only registers
the ``validate-reply`` entry, like ``validate``/``check`` live in
``validate_cmd``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tracks.kernel.envelope import ENVELOPE_VERSION, EnvelopeFormatError, validate_envelope

from .common import _err

_USAGE = "usage: trac validate-reply --file <path> --kind <kind>"


def _parse_validate_reply_args(args: list[str]) -> tuple[str, str] | None:
    """Parse ``--file PATH --kind KIND`` (either order); None on misuse."""
    file_path: str | None = None
    kind: str | None = None
    i = 0
    while i < len(args):
        if args[i] == "--file" and i + 1 < len(args):
            file_path = args[i + 1]
            i += 2
        elif args[i] == "--kind" and i + 1 < len(args):
            kind = args[i + 1]
            i += 2
        else:
            return None
    if not file_path or not kind:
        return None
    return file_path, kind


def cmd_validate_reply(repo: Path, *args) -> int:
    """Read PATH as JSON, validate the envelope against KIND, report."""
    del repo  # pure file read + kernel validation: no repo context needed.
    parsed = _parse_validate_reply_args(list(args))
    if parsed is None:
        return _err(_USAGE)
    file_path, kind = parsed
    try:
        text = Path(file_path).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"validate-reply error: cannot read {file_path}: {exc}", file=sys.stderr)
        return 1
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"validate-reply error: malformed_json: not JSON: {exc}", file=sys.stderr)
        return 1
    try:
        validate_envelope(payload, kind)
    except EnvelopeFormatError as exc:
        print(f"validate-reply error: {exc.kind}: {exc.detail}", file=sys.stderr)
        return 1
    print(f"ok: {kind} v{ENVELOPE_VERSION}")
    return 0
