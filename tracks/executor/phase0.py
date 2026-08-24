"""Phase 0 baseline/coverage/seal declarations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence, Set
from dataclasses import dataclass


@dataclass(frozen=True)
class TraceGap:
    ac: str
    reason: str
    planned_node_id: str | None


@dataclass(frozen=True)
class CoverageJudgement:
    passed: bool
    ratio: float
    threshold: float
    excluded_sources: tuple[str, ...]
    reason: str | None


@dataclass(frozen=True)
class SealManifest:
    baseline_version: str
    document_digests: Mapping[str, str]
    frozen_test_digests: Mapping[str, str]
    marks: tuple[str, ...]
    environment_contract_digest: str
    seal_id: str


def scan_trace_gaps(
    baseline_version: str,
    approved_acs: Iterable[str],
    planned_bindings: Mapping[str, str],
    collected_node_digests: Mapping[str, str],
    persisted_evidence: Mapping[str, str],
) -> list[TraceGap]:
    raise NotImplementedError("IF-PHASE-001")


def judge_real_coverage(
    ratio: float, threshold: float, excluded_sources: Sequence[str]
) -> CoverageJudgement:
    raise NotImplementedError("IF-PHASE-002")


def validate_registered_marks(
    declared_marks: Set[str], required_marks: Set[str]
) -> tuple[str, ...]:
    raise NotImplementedError("IF-PHASE-002")


def build_seal_manifest(
    baseline_version: str,
    document_digests: Mapping[str, str],
    frozen_test_digests: Mapping[str, str],
    marks: Sequence[str],
    environment_contract_digest: str,
) -> SealManifest:
    raise NotImplementedError("IF-PHASE-003")
