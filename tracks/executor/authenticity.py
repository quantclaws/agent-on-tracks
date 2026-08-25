"""Test-authenticity declarations (IF-AUTH-001/002)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from typing import Literal

from tracks.adapters.base import TestRunResult

BehaviourCategory = Literal["new", "existing"]


@dataclass(frozen=True)
class AuthenticityJudgement:
    ac: str
    category: BehaviourCategory
    red: Literal["legal", "illegal", "none"]
    green_allowed: bool
    counterexample_kill: Literal["verified", "missing", "none"]
    unrelated_nodes: tuple[str, ...]
    blocked_reason: str | None


def classify_behaviour(
    ac_ref: str,
    if_refs: Sequence[str],
    baseline_acs: Set[str],
    baseline_ifs: Set[str],
) -> BehaviourCategory:
    """Classify a behaviour as "new" (not in frozen baseline) or "existing".

    A behaviour is "new" when its AC or any of its IFs is absent from the
    frozen baseline (AC-FR0260-01: new behaviour requires legal Red on the
    frozen baseline).  It is "existing" when both the AC and all IFs are
    present in the baseline (AC-FR0260-04: coexists with D-41 selection).
    """
    if ac_ref not in baseline_acs:
        return "new"
    for if_ref in if_refs:
        if if_ref not in baseline_ifs:
            return "new"
    return "existing"


# Map from the short label returned by _classify_detail to the long-form
# alias used by some callers (e.g. the R unit test uses "assertion_failure"
# while the frozen integration test uses "assertion").
_KIND_ALIASES = {
    "assertion": "assertion_failure",
    "token": "stub_token_failure",
}


def _classify_detail(detail: str | None) -> str | None:
    """Lightweight Red classification from a failure detail (IF-AUTH-001).

    Returns the short failure kind label (``assertion``, ``token``,
    ``symbol_missing``, or ``None``) by keyword matching.  The legal check
    in ``_classify_outcomes`` also accepts the long-form aliases
    (``assertion_failure``, ``stub_token_failure``) via ``_KIND_ALIASES``.
    """
    if not detail:
        return None
    if "NotImplementedError(\"IF-" in detail or "NotImplementedError('IF-" in detail:
        return "token"
    if "AssertionError" in detail or "assert" in detail:
        return "assertion"
    if "AttributeError" in detail or "NameError" in detail:
        return "symbol_missing"
    return None


def _is_legal(kind: str, legal_failure_kinds: Set[str]) -> bool:
    """True when the failure kind is in the legal set, also accepting the
    long-form alias (e.g. ``assertion`` matches ``assertion_failure``)."""
    if kind in legal_failure_kinds:
        return True
    alias = _KIND_ALIASES.get(kind)
    return alias is not None and alias in legal_failure_kinds


def _classify_outcomes(
    outcomes: Mapping[str, TestRunResult],
    bound_set: set[str],
    legal_failure_kinds: Set[str],
) -> tuple[list[str], bool, str | None]:
    """Classify outcomes into unrelated, legal red, and illegal reason."""
    unrelated: list[str] = []
    legal_red_found = False
    illegal_reason: str | None = None
    for node_id, result in outcomes.items():
        if result.status not in ("failed", "error"):
            continue
        if node_id not in bound_set:
            unrelated.append(node_id)
            continue
        kind = _classify_detail(result.detail)
        if kind is not None and _is_legal(kind, legal_failure_kinds):
            legal_red_found = True
        else:
            illegal_reason = (
                f"node {node_id} failure kind {kind} is not in legal set "
                f"{legal_failure_kinds}"
            )
    return unrelated, legal_red_found, illegal_reason


def _check_broad_mutation(
    counterexample_experiment: Mapping | None, ac_ref: str
) -> str | None:
    """Return an illegal reason when the counterexample references a different
    AC (broad mutation, AC-FR0260-05)."""
    if counterexample_experiment is None:
        return None
    ce_ac = counterexample_experiment.get("ac")
    if ce_ac is not None and ce_ac != ac_ref:
        return (
            f"counterexample references a different AC ({ce_ac} != {ac_ref}) "
            f"— broad mutation rejected"
        )
    return None


def _no_bound_node_failure(
    outcomes: Mapping[str, TestRunResult], bound_set: set[str]
) -> bool:
    """True when no bound node has a failed/error status."""
    return not any(
        result.status in ("failed", "error")
        for node_id, result in outcomes.items()
        if node_id in bound_set
    )


def _judge_new(
    ac_ref: str, category: BehaviourCategory,
    legal_red_found: bool, illegal_reason: str | None,
    unrelated: list[str], outcomes: Mapping[str, TestRunResult],
    bound_set: set[str],
) -> AuthenticityJudgement:
    """Judge a new-behaviour outcome."""
    if legal_red_found and illegal_reason is None:
        return AuthenticityJudgement(
            ac=ac_ref, category=category,
            red="legal", green_allowed=False,
            counterexample_kill="none",
            unrelated_nodes=tuple(sorted(unrelated)),
            blocked_reason=None,
        )
    if _no_bound_node_failure(outcomes, bound_set):
        return AuthenticityJudgement(
            ac=ac_ref, category=category,
            red="none", green_allowed=False,
            counterexample_kill="none",
            unrelated_nodes=tuple(sorted(unrelated)),
            blocked_reason=None,
        )
    return AuthenticityJudgement(
        ac=ac_ref, category=category,
        red="illegal" if illegal_reason else "none",
        green_allowed=False,
        counterexample_kill="none",
        unrelated_nodes=tuple(sorted(unrelated)),
        blocked_reason=illegal_reason or "no legal Red found",
    )


def judge_authenticity(
    ac_ref: str,
    category: BehaviourCategory,
    bound_nodes: Sequence[str],
    outcomes: Mapping[str, TestRunResult],
    legal_failure_kinds: Set[str],
    counterexample_experiment: Mapping | None,
) -> AuthenticityJudgement:
    """Judge the authenticity of a Red/Green outcome (IF-AUTH-001)."""
    bound_set = set(bound_nodes)
    unrelated, legal_red_found, illegal_reason = _classify_outcomes(
        outcomes, bound_set, legal_failure_kinds
    )
    ce_reason = _check_broad_mutation(counterexample_experiment, ac_ref)
    if ce_reason:
        illegal_reason = ce_reason

    if category == "new":
        return _judge_new(
            ac_ref, category, legal_red_found, illegal_reason,
            unrelated, outcomes, bound_set,
        )
    return AuthenticityJudgement(
        ac=ac_ref, category=category,
        red="none", green_allowed=True,
        counterexample_kill="none",
        unrelated_nodes=tuple(sorted(unrelated)),
        blocked_reason=None,
    )
