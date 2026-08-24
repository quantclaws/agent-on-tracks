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
    raise NotImplementedError("IF-AUTH-001")


def judge_authenticity(
    ac_ref: str,
    category: BehaviourCategory,
    bound_nodes: Sequence[str],
    outcomes: Mapping[str, TestRunResult],
    legal_failure_kinds: Set[str],
    counterexample_experiment: Mapping | None,
) -> AuthenticityJudgement:
    raise NotImplementedError("IF-AUTH-001/IF-AUTH-002")
