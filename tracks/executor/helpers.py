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


def _scoped_commit_if_staged(
    repo: Path, message: str, paths: list[Path | str] | None = None
) -> subprocess.CompletedProcess | None:
    """Attempt to commit staged changes (check=False), scoped to ``paths``
    when given: the runtime commits only files it deliberately staged and
    never sweeps unrelated operator-staged content into its commits
    (AC-FR0236-01 attribution; same path-scoped pattern as the doc-gap
    revision commit). Returns None if nothing was staged for the scoped
    paths, or the CompletedProcess so the caller can inspect
    ``.returncode`` for pre-commit hook rejection without crashing.
    The executor composition surface re-exports it for callers that
    lazy-import it to avoid the ResultCheckpoint import cycle."""
    cached = ["diff", "--cached", "--quiet"]
    if paths:
        cached += ["--", *[str(p) for p in paths]]
    if git(repo, *cached, check=False).returncode == 0:
        return None
    cmd = ["commit", "-m", message]
    if paths:
        cmd += ["--only", "--", *[str(p) for p in paths]]
    return git(repo, *cmd, check=False)


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
    for key in (
        "diff_ref",
        "audit_evidence",
        "failure_class",
        "verdict",
        "discussion_evidence",
        "phase",
        "changed_paths",
        "commands",
        "results",
        "manifest_compliance",
        "pre_identity",
        "post_identity",
        "r_identity",
        "no_change_reason",
        "implemented_if_ids",
        "result_identity",
    ):
        if result.get(key) is not None:
            payload[key] = result[key]
    return payload


# IF-004 §1g RedClass closed set (legit = the test fails for the right reason).
_LEGIT_RED = frozenset({"assertion_failure", "stub_token_failure", "symbol_missing"})
# DIAGNOSE classification -> target stage for rollback/rewrite.
_DIAGNOSE_TARGET = {
    "test_defect": "M-TEST",
    "stub_gap": "M-DESIGN",
    "ac_gap": "M-ACC",
    "spec_gap": "M-SPEC",
    "impl_defect": "M-IMPL",
    # #89: plan_defect routes to M-IMPL PLANNING (Archer replan) — same
    # stage, explicit entry for audit-trail precision.
    "plan_defect": "M-IMPL",
}
# test short-traceback E-prefix assertion line (``E   assert ...``).
_ASSERT_E_LINE = re.compile(r"^E\s+assert\b", re.MULTILINE)
# Failure keywords -> illegit Red class (collection/syntax/fixture/import).
_ILLEGIT_KEYWORDS = (
    "ImportError",
    "ModuleNotFoundError",
    "SyntaxError",
    "FixtureLookupError",
    "collection error",
    "ERROR collecting",
)


def classify_red(test_id: str, returncode: int, stdout: str, stderr: str) -> str:
    """FR-0050 classify a single test failure (IF-004 §1g, pure).

    Based on returncode + keyword matching (no traceback structure parsing):
    - ``NotImplementedError("IF-`` -> ``stub_token_failure`` (legit).
    - ``AssertionError`` or the host runner's ``E   assert`` line -> ``assertion_failure``
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


def classify_red_detail(detail: str, status: str | None = None) -> str:
    """D-41 (v6) per-node legal-Red classification over the ``{result}`` test result
    failure/error detail (IF-RUNCONTRACT-001; stdout/stderr are never the
    authority). Same closed taxonomy as :func:`classify_red`, plus the final
    review status-awareness pin (FA-1):

    - stub token / symbol-missing keywords -> legit Red (explicit contract
      tokens stay legit even on ``<error>`` records);
    - infrastructure/collection keywords are checked BEFORE AssertionError:
      an infra failure that merely mentions an assertion never reads as
      behavioral Red;
    - an ``<error>``-status record (``status="error"``) is ALWAYS illegit
      (``collection_error``) unless an explicit contract stub-token or
      symbol-missing keyword applies -- a setup/fixture/import error is
      never the behavioral-Red default;
    - passed/skipped records are classified by the caller (``unexpected_pass``);
    - a FAILED record with no explicit assertion/stub-token/symbol keyword is
      NOT a behavioral Red by default (FRB-A): an ordinary failure message
      such as ``ValueError: body blew up`` carries no contract signal, so it
      defaults to the illegit ``unclassified`` and enters DIAGNOSE;
    - callers without a test result record status (``status=None``) keep the legacy
      behavioral-Red default (``assertion_failure``).
    """
    combined = detail or ""
    if 'NotImplementedError("IF-' in combined or "NotImplementedError('IF-" in combined:
        return "stub_token_failure"
    for kw in _ILLEGIT_KEYWORDS:
        if kw in combined:
            return "collection_error"
    if "AssertionError" in combined or _ASSERT_E_LINE.search(combined):
        return "assertion_failure"
    if "AttributeError" in combined or "NameError" in combined:
        return "symbol_missing"
    if status == "error":
        # An error-status record is a collection/setup/import failure, never
        # a validated behavioral Red (final review pin FA-1).
        return "collection_error"
    if status == "failed":
        # FRB-A: an ordinary failed record (no assertion/E-line, no stub
        # token, no symbol keyword) is not a legit behavioral Red.
        return "unclassified"
    return "assertion_failure"


def parse_collected_nodes(stdout: str) -> list[str]:
    """Parse the host runner's ``--collect-only -q`` stdout into node ids (D-41).

    One node id per line (``path.py::func[param]``); trailer/summary lines
    without a ``::`` separator are ignored. Order follows the collector's
    output; callers sort before canonical use.
    """
    nodes = []
    for line in (stdout or "").splitlines():
        node = line.strip()
        if "::" in node and not node.startswith("=") and not node.startswith("_"):
            nodes.append(node)
    return nodes


def _parse_collected_count(stdout: str) -> int:
    """Parse the test count from test --collect-only -q output.

    Handles two formats:
    - Older test runner: trailing ``N tests collected`` line.
    - test runner 9.1+: per-file ``path/to/test.py: N`` lines with no trailer.
    Also catches ``N errors`` in either format.
    """
    m = re.findall(r"(\d+) tests? collected", stdout)
    if m:
        return sum(int(n) for n in m)
    m = re.findall(r"^.*\.py:\s*(\d+)\s*$", stdout, re.MULTILINE)
    if m:
        return sum(int(n) for n in m)
    m = re.findall(r"(\d+) errors?", stdout)
    return sum(int(n) for n in m) if m else 0


def _short_detail(output: str, limit: int = 200) -> str:
    """Truncate test output for the red.validated findings detail field."""
    output = output.strip()
    return output[:limit] + ("..." if len(output) > limit else "")
