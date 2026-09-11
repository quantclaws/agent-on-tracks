"""In-place release-pipeline repair and Known Issue policy (FR-0286).

Defects found in M-VERIFY/M-SECURITY/M-PUBLISH/M-MILESTONE are repaired
inside the current run: classification only decides who repairs (Devon
RED-first for behaviour defects, verification-only for gate defects, Archer
advisory consultation for CVEs, controlled contract revision for contract
defects). No automatic rollback to M-DESIGN/M-PLANNING is ever produced. A
fix that lands a new commit creates a new candidate and the full M-VERIFY
chain re-walks (SM-01.20). When the repair budget (default 3) is exhausted
with Prism confirming the attribution unchanged, product-quality defects may
be registered as Known Issues (waiver + next-version backlog); mechanism
failures and security findings are excluded and must be fixed or abandoned.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal

RepairDiscipline = Literal["red_first", "verification_only", "cve_advisory", "contract_delta"]

# FR-0286 §2 closed mapping: defect_class -> (owner, discipline). The owner
# for gate/contract classes is decided by the repair executor (verification-
# only re-run / controlled revision), not fixed here.
_CLASSIFICATION: dict[str, tuple[str, str]] = {
    "behavior": ("Devon", "red_first"),
    "gate": ("Runtime", "verification_only"),
    "cve": ("Archer", "cve_advisory"),
    "contract": ("Runtime", "contract_delta"),
}

# FR-0286 §6 exclusions: these NEVER become Known Issues (zero known issue
# for security; publish-mechanism failures must be fixed or abandoned).
_NOT_PRODUCT_DEFECT = frozenset({"mechanism_failure", "security_finding"})

# AC reference token (an unfixed known issue is bound to the AC(s) it waives).
_AC_TOKEN = re.compile(r"AC-(?:N?FR)\d{4}-\d{2}")

_ROUNDS: dict[str, int] = {}
_KNOWN_ISSUES: list[dict] = []


def classify_defect(finding: dict, context: dict) -> dict:
    """Map a gate/scan/review finding to {defect_class, owner, discipline}.

    Closed defect set: behaviour | gate | cve | contract (§1.0.14 table B).
    An unknown finding fails closed: no defect_class is guessed, the result
    carries failed_class=true so the caller blocks instead of routing.
    """
    kind = str((finding or {}).get("kind", "")).strip()
    if kind in _CLASSIFICATION:
        owner, discipline = _CLASSIFICATION[kind]
        return {
            "defect_class": kind,
            "owner": owner,
            "discipline": discipline,
        }
    # fail closed: unknown/missing classification never routes a repair
    return {
        "defect_class": None,
        "owner": None,
        "discipline": None,
        "failed_class": True,
        "reason": "unknown_defect_class",
    }


def open_repair_round(
    run_id: str,
    classification: dict,
    budget: int,
    candidate_sha: str | None = None,
    trigger_event_seq: int | None = None,
) -> dict:
    """Emit repair.round_started {round, budget, classification}; never a
    stage.rolled_back — in-place repair only (AC-FR0286-01).

    §1a row 28 closed payload: when the caller supplies ``candidate_sha`` /
    ``trigger_event_seq`` they are forwarded so the repair-family event binds
    the frozen candidate (NFR-0143-01) and stays replayable to its trigger.

    FR-0286 §4: the repair budget is finite (status renders round=<n>/3).
    Once the used rounds reach the budget, a further call surfaces budget
    exhaustion (rounds_exhausted=true) so the caller routes to irreparable
    (AC-FR0286-04) instead of emitting an unbounded fresh round.
    """
    round_no = _ROUNDS.get(run_id, 0) + 1
    _ROUNDS[run_id] = round_no
    result: dict = {
        "event": "repair.round_started",
        "run_id": run_id,
        "round": round_no,
        "budget": budget,
        "classification": dict(classification or {}),
    }
    if candidate_sha is not None:
        result["candidate_sha"] = candidate_sha
    if trigger_event_seq is not None:
        result["trigger_event_seq"] = trigger_event_seq
    if round_no > budget:
        result["rounds_exhausted"] = True
    return result


def assert_frozen_tests_untouched(repo, before_digests: dict) -> None:
    """Frozen int/e2e must not change across a repair round (FR-0286 §10).

    A registered frozen file whose content differs OR that is MISSING from
    the repo is a frozen-test breach: both fail closed as a contract
    assertion, never as an incidental OSError.
    """
    repo_path = Path(repo) if repo is not None and str(repo) != "repo" else Path.cwd()
    for rel, digest in (before_digests or {}).items():
        path = repo_path / rel
        try:
            current = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as err:
            raise AssertionError(
                f"frozen test {rel} is missing from the repo — a frozen-test "
                f"contract breach (FR-0286 §10)"
            ) from err
        if current != digest:
            raise AssertionError(
                f"frozen test {rel} changed across a repair round "
                f"(before={digest} after={current})"
            )


def mark_fix_new_candidate(run_id: str, old_candidate: str) -> dict:
    """Emit evidence.staled(reason=fix_new_candidate) and trigger the full
    M-VERIFY re-walk on the new candidate (SM-01.20, AC-FR0286-03)."""
    return {
        "event": "evidence.staled",
        "reason": "fix_new_candidate",
        "run_id": run_id,
        "old_candidate": old_candidate,
    }


def judge_irreparable(rounds_used: int, budget: int, prism_attribution: dict) -> bool:
    """Budget exhausted AND Prism confirms attribution unchanged, or the fix
    exceeds controlled contract revision, or the dependency has no fix.

    AC-FR0286-04: the two alternate C-class triggers judge irreparable on
    their own, regardless of remaining budget.
    """
    attribution = prism_attribution or {}
    if attribution.get("no_fix_available") is True:
        return True
    if attribution.get("exceeds_contract_revision") is True:
        return True
    if attribution.get("attribution_unchanged") is not True:
        return False
    return rounds_used >= budget


def register_known_issue(repo, attribution: dict, candidate_sha: str) -> dict:
    """Prism-confirmed product-quality defect -> GitHub issue with the
    known-issue label linked to candidate + evidence; rejects mechanism
    failures and security findings (not_product_defect).

    FR-0286 §5 waiver association: the registration carries the task and the
    AC/node refs it waives (``task_id``/``ac_refs``/``node_refs``) so the
    machine projection can close the task and the FULL gate can exclude the
    bound nodes without re-deriving the association from prose. The caller
    may pin ``issue_number`` from the durable stream (the process-local
    counter cannot number across the M7 handover boundary)."""
    attribution = attribution or {}
    kind = str(attribution.get("kind", ""))
    item = attribution.get("item_or_ac", "")
    ac_refs = [str(ac) for ac in (attribution.get("ac_refs") or []) if str(ac).strip()]
    node_refs = [str(n) for n in (attribution.get("node_refs") or []) if str(n).strip()]
    task_id = attribution.get("task_id")
    if not item and ac_refs:
        item = ac_refs[0]
    if kind in _NOT_PRODUCT_DEFECT:
        # §1a row 29 pair payload: BOTH faces carry the closed members —
        # issue_number/url nullable on the rejected face, plus the pair label
        # and evidence refs; the rejection additionally carries reason.
        return {
            "event": "known_issue.rejected",
            "issue_number": None,
            "url": None,
            "label": "known-issue",
            "evidence_refs": [],
            "kind": kind,
            "item_or_ac": item,
            "candidate_sha": candidate_sha,
            "task_id": task_id,
            "ac_refs": list(ac_refs),
            "node_refs": list(node_refs),
            "reason": "not_product_defect",
        }
    pinned = attribution.get("issue_number")
    if isinstance(pinned, int) and not isinstance(pinned, bool):
        issue_number = int(pinned)
    else:
        issue_number = len(_KNOWN_ISSUES) + 100
    registration = {
        "event": "known_issue.registered",
        "label": "known-issue",
        "issue_number": issue_number,
        "url": f"https://github.com/acme/host/issues/{issue_number}",
        "item_or_ac": item,
        "candidate_sha": candidate_sha,
        "task_id": task_id,
        "ac_refs": list(ac_refs),
        "node_refs": list(node_refs),
        "evidence_refs": [f"repair:{kind}"],
        "fixed": False,
    }
    _KNOWN_ISSUES.append(registration)
    return registration


def _event_type(event) -> str:
    if isinstance(event, dict):
        return str(event.get("type") or "")
    return str(getattr(event, "type", "") or "")


def _event_payload(event) -> dict:
    payload = event.get("payload") if isinstance(event, dict) else getattr(event, "payload", None)
    return dict(payload) if isinstance(payload, dict) else {}


def registered_known_issues(events) -> list[dict]:
    """Every unfixed ``known_issue.registered`` payload, in stream order.

    The event stream is the durable authority (FR-0286 §5 informed consent):
    process-local registries do not survive the M7 handover regime, and the
    release preview must list every open waiver even after a restart.
    """
    return [
        _event_payload(event)
        for event in events
        if _event_type(event) == "known_issue.registered"
        and not _event_payload(event).get("fixed")
    ]


def _registration_associations(registration: dict) -> tuple[set[str], set[str]]:
    """(AC refs, node refs) carried by one registration payload."""
    acs: set[str] = set()
    nodes: set[str] = set()
    for ac in registration.get("ac_refs") or ():
        token = str(ac).strip()
        if token:
            acs.add(token)
    for node in registration.get("node_refs") or ():
        token = str(node).strip()
        if token:
            nodes.add(token)
    item = str(registration.get("item_or_ac") or "")
    acs.update(match.group(0) for match in _AC_TOKEN.finditer(item))
    nodes.update(
        token for token in item.split() if "::" in token and token.startswith("tests/")
    )
    return acs, nodes


def waived_associations(events) -> dict:
    """AC/node/task refs waived by registered known issues (single truth).

    The extraction is purely event-derived so the preview listing, the FULL
    required-green judgment and the machine projection all consume the same
    association. ``item_or_ac`` prose may embed AC tokens; ``ac_refs`` /
    ``node_refs`` are the structured carriers.
    """
    acs: set[str] = set()
    nodes: set[str] = set()
    tasks: set[str] = set()
    for registration in registered_known_issues(events):
        registration_acs, registration_nodes = _registration_associations(registration)
        acs |= registration_acs
        nodes |= registration_nodes
        task_id = registration.get("task_id")
        if task_id:
            tasks.add(str(task_id))
    return {"acs": acs, "nodes": nodes, "tasks": tasks}


def _preview_entry(registration: dict) -> dict:
    return {
        "issue": f"acme/host#{registration.get('issue_number')}",
        "waiver": registration.get("item_or_ac") or "AC-FR0286-05",
        "candidate_sha": registration.get("candidate_sha"),
        "task_id": registration.get("task_id"),
        "ac_refs": list(registration.get("ac_refs") or []),
    }


def list_known_issues_for_preview(run_id: str, events=None) -> list[dict]:
    """Every unfixed known issue MUST appear in the preview; an unlisted one
    blocks release (informed consent, FR-0286 §5).

    With ``events`` the listing is rebuilt from the durable stream (the
    executor's authority across process handovers); without it the
    process-local registry is the best available in-process view (module
    surface parity).
    """
    if events is None:
        source = [dict(entry) for entry in _KNOWN_ISSUES if not entry.get("fixed")]
    else:
        source = registered_known_issues(events)
    return [_preview_entry(entry) for entry in source]
