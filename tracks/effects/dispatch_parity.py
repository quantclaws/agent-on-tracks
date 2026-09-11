"""Declared-dispatch six-face parity seam (IF-ENVELOPE-002).

Small coherent prepare/check/execute seam shared by the Runtime and the real
backend. It is the single place where the declared faces are collected and
compared by the Runtime-owned kernel gate:

- static faces (collected on EVERY declared dispatch, before any backend
  I/O): ``assignment`` (bare-int face), ``backend_fake`` and ``backend_real``
  (independent class literals, BOTH collected even when only one backend is
  selected — one stale backend declaration blocks both modes) and the
  ``validator`` (the kernel authority itself, never aliased into a face).
- artifact faces (collected by the materialization owner at dispatch time,
  from the ACTUAL materialized bytes, never from canonical sources): the
  agent definition, every selected skill, every selected template — each a
  distinct face name (``agent:<Name>``, ``skill:<name>``, ``template:<kind>``)
  because every face has a distinct identity and ALL selected artifacts must
  agree, not just the first.

Boundaries kept: no file I/O beyond the pre-spawn readback of artifacts this
backend itself wrote; no duplicate materialization; no Executor host object;
the kernel check stays in kernel.envelope (single truth); a face never
imports the authority to fabricate its declaration.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from tracks.kernel.envelope import (
    ENVELOPE_VERSION,
    PARITY_STAGED,
    _parity_version,
    check_envelope_parity,
    scan_face_token,
)

# The static (non-artifact) parity faces collected on every declared dispatch.
STATIC_PARITY_FACES = ("assignment", "backend_fake", "backend_real", "validator")

# Failed-result classification for a parity rejection (readback face or
# artifact-face mismatch); the Runtime maps it to
# ``dispatch.rejected reason=version_parity_mismatch`` and never to a
# semantic success.
PARITY_FAILURE_CLASS = "version_parity_mismatch"


def agent_face(name: str) -> str:
    """Face name of one materialized agent definition."""
    return f"agent:{name}"


def skill_face(name: str) -> str:
    """Face name of one materialized skill."""
    return f"skill:{name}"


def template_face(kind: str) -> str:
    """Face name of one materialized prompt template."""
    return f"template:{kind}"


def is_declared(assignment: object) -> bool:
    """True when the dispatch assignment declares the kernel envelope."""
    return isinstance(assignment, dict) and bool(assignment.get("envelope"))


def bind_artifact(face: str, path: Path, data: bytes) -> dict:
    """One prepare-manifest entry: the exact bytes WRITTEN to ``path``.

    Binds face -> path + sha256 + the frontmatter token scanned from those
    actual bytes. Never scans a canonical source here: the manifest is
    evidence about the materialized copy the agent will actually read.
    """
    return {
        "face": face,
        "path": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "token": scan_face_token(data.decode("utf-8", errors="replace")),
    }


def readback_mismatches(entries: list[dict]) -> list[dict]:
    """Readback immediately before spawn: re-read every prepared artifact
    from disk and require byte-equality with the prepared bytes (the token
    is carried by the same bytes, so a hash match pins both). Detects a
    concurrent overwrite / raced edit / post-write tamper BEFORE any agent
    execution."""
    mismatches: list[dict] = []
    for entry in entries or []:
        try:
            actual = Path(entry["path"]).read_bytes()
        except OSError as exc:
            mismatches.append(
                {
                    "face": entry["face"],
                    "path": entry["path"],
                    "reason": "unreadable",
                    "detail": str(exc),
                }
            )
            continue
        digest = hashlib.sha256(actual).hexdigest()
        if digest != entry["sha256"]:
            mismatches.append(
                {
                    "face": entry["face"],
                    "path": entry["path"],
                    "reason": "modified",
                    "expected_sha256": entry["sha256"],
                    "actual_sha256": digest,
                }
            )
    return mismatches


def assignment_face_value(assignment: dict | None) -> object:
    """The assignment face value the kernel compares.

    The AUTHORITATIVE declaration lives inside ``assignment["envelope"]``
    (built by ``build_assignment_envelope``): its ``version`` (or machine
    ``token``) IS the face — a genuine declaration needs no duplicate
    ``envelope_version`` key. The legacy bare-int ``envelope_version`` is
    only a redundant convenience copy and, when present, must AGREE with the
    header (it may never override it; a contradicting duplicate is reported
    by :func:`envelope_declaration_error` as a "conflict" mismatch).
    Returns the raw header version/token (the kernel normalizes both the
    bare int and the ``tracks-envelope:vN`` token), or the legacy
    ``envelope_version`` when no envelope header exists (undeclared/legacy
    dispatches keep their staged semantics).
    """
    envelope = (assignment or {}).get("envelope")
    if isinstance(envelope, dict):
        version = envelope.get("version")
        if version is not None:
            return version
        token = envelope.get("token")
        if token is not None:
            return token
    return (assignment or {}).get("envelope_version")


def envelope_declaration_error(assignment: dict | None) -> dict | None:
    """One assignment-face mismatch when the envelope DECLARATION itself is
    malformed or self-conflicting; None when it is a well-formed single
    version (the kernel then compares the derived face normally).

    - any present non-None ``envelope`` that is not an object is malformed:
      a truthy wrong-typed declaration (``"junk"``, ``[]``, ``2``, ``True``)
      is declared, so it enters the enforced gate and may never fall back to
      the legacy ``envelope_version`` copy;
    - a declaration header carrying NEITHER ``version`` NOR ``token`` is
      malformed — a missing header version inside a declaration rejects;
    - a header whose ``version`` and ``token`` normalize to different
      versions is self-conflicting;
    - a redundant ``envelope_version`` that normalizes to a different
      version than the authoritative header is a conflicting duplicate and
      rejects (it may never override the declaration).
    An explicit ``None``/missing key is undeclared/legacy and validates
    nothing (staged semantics); garbled/unknown header tokens are NOT
    reported here: the derived face is handed to the kernel, which rejects
    them as invalid/version mismatches.
    """
    envelope = (assignment or {}).get("envelope")
    if envelope is None:
        return None  # undeclared / legacy assignment: nothing to validate
    if not isinstance(envelope, dict):
        return {
            "face": "assignment",
            "version": None,
            "reason": "malformed",
            "detail": (
                "envelope declaration must be an object, got "
                f"{type(envelope).__name__}"
            ),
        }
    header_version = envelope.get("version")
    header_token = envelope.get("token")
    resolved = header_version if header_version is not None else header_token
    if resolved is None:
        return {
            "face": "assignment",
            "version": None,
            "reason": "malformed",
            "detail": "envelope declaration has no version/token header",
        }
    derived = _parity_version(resolved)
    if derived is None:
        return None  # garbled/unknown: the kernel rejects the derived face
    if (
        header_version is not None
        and header_token is not None
        and _parity_version(header_token) != derived
    ):
        return {
            "face": "assignment",
            "version": resolved,
            "reason": "conflict",
            "detail": (
                f"envelope version={header_version!r} contradicts "
                f"token={header_token!r}"
            ),
        }
    duplicate = (assignment or {}).get("envelope_version")
    if duplicate is not None and _parity_version(duplicate) != derived:
        return {
            "face": "assignment",
            "version": duplicate,
            "reason": "conflict",
            "detail": (
                f"envelope_version={duplicate!r} contradicts envelope header "
                f"version={resolved!r} (the declaration is authoritative)"
            ),
        }
    return None


def _declaration_mismatches(assignment: dict | None) -> list[dict]:
    """The assignment-face mismatch for a malformed/conflicting declaration,
    or an empty list when the declaration is well-formed."""
    error = envelope_declaration_error(assignment)
    return [] if error is None else [error]


def static_parity_referenced(assignment: dict | None) -> dict:
    """The static face map, collected from independent declarations only.

    The assignment face is DERIVED from the real declaration header
    (``assignment.envelope.version``/``token`` — the authoritative single
    copy), never from the redundant legacy ``envelope_version`` key alone.
    Both backend literals come from their classes (imported lazily to keep
    module import order neutral); neither is derived from the kernel
    authority and neither is collapsed into the selected backend.
    """
    from tracks.effects.fake import FakeBackend  # noqa: PLC0415
    from tracks.effects.opencode import OpencodeBackend  # noqa: PLC0415

    return {
        "assignment": assignment_face_value(assignment),
        "backend_fake": FakeBackend.envelope_version,
        "backend_real": OpencodeBackend.envelope_version,
        "validator": ENVELOPE_VERSION,
    }


def static_parity_check(assignment: dict | None) -> dict:
    """Runtime-owned static gate over the four non-artifact faces.

    Declared dispatch: all four static faces are REQUIRED (a partial map
    never passes an enforced dispatch) and a malformed/conflicting envelope
    declaration rejects on its own evidence. Undeclared dispatch: the
    EXPLICIT legacy opt-out (:data:`PARITY_STAGED`) keeps today's staged
    semantics (faces that declare nothing are skipped, only declared
    mismatches block) — permissiveness is never the kernel default.
    """
    referenced = static_parity_referenced(assignment)
    required = STATIC_PARITY_FACES if is_declared(assignment) else PARITY_STAGED
    verdict = check_envelope_parity(referenced, required_faces=required)
    mismatches = [*verdict["mismatches"], *_declaration_mismatches(assignment)]
    return {
        "consistent": not mismatches,
        "mismatches": mismatches,
        "referenced": referenced,
    }


@dataclass(frozen=True)
class PreparedContext:
    """The narrowly typed prepared context handed to the execution step.

    ``failure`` is a complete failed-result dict (parity rejection: the
    caller returns it verbatim — no agent spawn, cleanup still runs).
    ``evidence`` is the audit payload of a PASSED gate: consistent face map,
    the artifact manifest (path + sha + token bound to actual bytes) and the
    prompt digest actually handed to the spawn.
    """

    ok: bool
    failure: dict | None = None
    evidence: dict | None = None


def check_prepared_parity(
    assignment: dict | None,
    entries: list[dict],
    expected_faces: list[str],
    prompt: str | None = None,
    readback: list[dict] | None = None,
) -> dict:
    """Run the Runtime-owned kernel gate over the prepared dispatch.

    ``entries`` is the artifact manifest bound during preparation; each entry
    contributes its face token to the referenced map. ``expected_faces`` are
    the faces this dispatch SELECTED for consumption (agent definition +
    every selected skill/template): a selection whose materialization
    produced no entry is a genuinely absent REQUIRED face, never silently
    skipped, and no file/version evidence is invented for it. Readback
    mismatches (path+hash drift between preparation and execution) fail the
    dispatch on their own evidence. A malformed/conflicting envelope
    declaration (missing header version, or a redundant ``envelope_version``
    that contradicts ``envelope.version``/``token``) rejects on its own
    evidence too.
    """
    referenced = static_parity_referenced(assignment)
    for entry in entries or []:
        referenced[entry["face"]] = entry.get("token")
    expected = list(expected_faces or [])
    required = (*STATIC_PARITY_FACES, *dict.fromkeys(expected))
    for face in expected:
        if face not in referenced:
            referenced[face] = None
    mismatches = [*list(readback or []), *_declaration_mismatches(assignment)]
    evidence: dict = {
        "consistent": False,
        "mismatches": mismatches,
        "referenced": referenced,
        "manifest": list(entries or []),
    }
    if prompt is not None:
        # The prompt digest is provenance bound to the exact spawn input —
        # it is evidence about WHAT was executed, not a version face, so it
        # never enters the referenced face map the kernel compares.
        evidence["prompt_sha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    verdict = check_envelope_parity(referenced, required_faces=required)
    evidence["consistent"] = verdict["consistent"] and not mismatches
    evidence["mismatches"] = [*verdict["mismatches"], *mismatches]
    return evidence


def parity_failure_result(
    assignment: dict | None,
    mismatches: list[dict],
    evidence: dict | None = None,
) -> dict:
    """The failed dispatch result for a rejected declared dispatch.

    Raised BEFORE any agent execution: no external invocation, no semantic
    success, no fabricated reply. ``parity_rejected`` is the Runtime's
    marker for emitting ``dispatch.rejected reason=version_parity_mismatch``.
    """
    envelope = (assignment or {}).get("envelope")
    kind = envelope.get("kind") if isinstance(envelope, dict) else None
    mismatches = mismatches or []
    return {
        "status": "failed",
        "artifact_ref": None,
        "failure_class": PARITY_FAILURE_CLASS,
        "self_report": "declared-dispatch version parity gate failed before agent execution",
        "audit_evidence": json.dumps(mismatches, ensure_ascii=False, sort_keys=True),
        "parity_rejected": True,
        "parity": {
            "kind": kind,
            "mismatches": mismatches,
            "referenced": (evidence or {}).get("referenced"),
            "manifest": (evidence or {}).get("manifest", []),
        },
    }


def parity_evidence(verdict: dict) -> dict:
    """The provenance payload a PASSED prepared dispatch attaches to its
    result: the consistent face map, the artifact manifest and the prompt
    digest bound to the exact spawn input."""
    return {
        "consistent": True,
        "referenced": verdict.get("referenced", {}),
        "manifest": list(verdict.get("manifest") or []),
        "prompt_sha256": verdict.get("prompt_sha256"),
    }
