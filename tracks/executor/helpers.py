"""Module-level helpers extracted from executor.py: git utilities, agent
dispatch evidence, and test-failure classification (IF-004 §1g)."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from tracks.store import Store


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc


def _commit_if_staged(repo: Path, message: str) -> subprocess.CompletedProcess | None:
    """Attempt to commit staged changes (check=False). Returns None if nothing
    was staged, or the CompletedProcess so the caller can inspect ``.returncode``
    for pre-commit hook rejection without crashing."""
    if git(repo, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return None
    return git(repo, "commit", "-m", message, check=False)


def _hook_output(proc: subprocess.CompletedProcess, limit: int = 4096) -> str:
    """Combined stdout+stderr from a failed ``git commit``, truncated."""
    combined = (proc.stdout or "") + (proc.stderr or "")
    return combined[:limit]


def _agent_io_evidence(store: Store, assignment: dict, captured: dict) -> dict:
    """Persist input/output evidence without making it a workflow failure."""
    input_payload = dict(assignment)
    for key in ("prompt", "console_input"):
        if key in captured:
            input_payload[key] = captured[key]
    input_ref = store.write_audit_blob(input_payload)
    output_raw = json.dumps(captured, ensure_ascii=False, sort_keys=True)
    output_ref = store.write_audit_blob(captured)
    digest = hashlib.sha256(output_raw.encode("utf-8")).hexdigest()
    gaps = []
    if input_ref is None:
        gaps.append("assignment_blob_write_failed")
    if output_ref is None:
        gaps.append("agent_output_blob_write_failed")
    evidence = {
        "input_ref": input_ref,
        "output_ref": output_ref,
        "output_digest": digest,
        "output_summary": {
            "stdout_bytes": captured.get("stdout_bytes", 0),
            "stderr_bytes": captured.get("stderr_bytes", 0),
        },
        "audit_completeness": "partial" if gaps else "complete",
    }
    if gaps:
        evidence["audit_gaps"] = gaps
    return evidence


def _dispatch_payload(store: Store, params: dict, result: dict) -> dict:
    payload = {
        "role": params["role"],
        "status": result["status"],
        "artifact_ref": result.get("artifact_ref"),
        "self_report": result["self_report"],
    }
    captured = result.get("agent_io")
    if captured is not None:
        payload["agent_io"] = _agent_io_evidence(store, dict(params), captured)
    for key in ("diff_ref", "audit_evidence", "failure_class", "verdict",
                "discussion_evidence"):
        if result.get(key) is not None:
            payload[key] = result[key]
    return payload


# IF-004 §1g RedClass closed set (legit = the test fails for the right reason).
_LEGIT_RED = frozenset({"assertion_failure", "stub_token_failure", "symbol_missing"})
# DIAGNOSE classification -> target stage for rollback/rewrite.
_DIAGNOSE_TARGET = {"test_defect": "M-TEST", "stub_gap": "M-DESIGN",
                    "ac_gap": "M-ACC", "spec_gap": "M-SPEC"}
# pytest short-traceback E-prefix assertion line (``E   assert ...``).
_ASSERT_E_LINE = re.compile(r"^E\s+assert\b", re.MULTILINE)
# Failure keywords -> illegit Red class (collection/syntax/fixture/import).
_ILLEGIT_KEYWORDS = (
    "ImportError", "ModuleNotFoundError", "SyntaxError",
    "FixtureLookupError", "collection error", "ERROR collecting",
)


def classify_red(test_id: str, returncode: int, stdout: str, stderr: str) -> str:
    """FR-0050 classify a single test failure (IF-004 §1g, pure).

    Based on returncode + keyword matching (no traceback structure parsing):
    - ``NotImplementedError("IF-`` -> ``stub_token_failure`` (legit).
    - ``AssertionError`` or pytest ``E   assert`` line -> ``assertion_failure``
      (legit).
    - ImportError/ModuleNotFoundError/SyntaxError/FixtureLookupError/collection
      error -> ``collection_error`` (illegit).
    - returncode == 0 (test should fail but passed) -> ``unexpected_pass``.
    - otherwise -> ``unclassified`` (illegit, enters DIAGNOSE).
    """
    combined = f"{stdout}\n{stderr}"
    if returncode == 0:
        return "unexpected_pass"
    if 'NotImplementedError("IF-' in combined or "NotImplementedError('IF-" in combined:
        return "stub_token_failure"
    if "AssertionError" in combined or _ASSERT_E_LINE.search(combined):
        return "assertion_failure"
    for kw in _ILLEGIT_KEYWORDS:
        if kw in combined:
            return "collection_error"
    if "AttributeError" in combined or "NameError" in combined:
        return "symbol_missing"
    return "unclassified"


def _parse_collected_count(stdout: str) -> int:
    """Parse the test count from pytest --collect-only -q output (lines
    like ``N tests collected`` or ``N errors``). Sums across sections."""
    m = re.findall(r"(\d+) tests? collected", stdout)
    if m:
        return sum(int(n) for n in m)
    m = re.findall(r"(\d+) errors?", stdout)
    return sum(int(n) for n in m) if m else 0


def _short_detail(output: str, limit: int = 200) -> str:
    """Truncate pytest output for the red.validated findings detail field."""
    output = output.strip()
    return output[:limit] + ("..." if len(output) > limit else "")
