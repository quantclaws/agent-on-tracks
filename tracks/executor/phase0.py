"""Phase 0 baseline/coverage/seal declarations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence, Set
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class TraceGap:
    ac: str
    reason: Literal["marker_only", "node_missing", "identity_unrecoverable"]
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
    """Separate marker-only from real collected-node evidence (FR-0256).

    For each approved AC that has a planned binding, classify the gap:
    - ``node_missing`` when the planned node is absent from the collect
      inventory (AC-FR0256-03: unrecoverable -> BLOCKED).
    - ``identity_unrecoverable`` when the node is collected but its identity
      digest is absent or invalid (cannot be recovered).
    - ``marker_only`` when the node IS collected with a valid identity but
      has no persisted machine evidence (AC-FR0256-02: marker closures stay
      fail).
    """
    approved = set(approved_acs)
    gaps: list[TraceGap] = []
    for ac, planned_node in planned_bindings.items():
        if ac not in approved:
            continue
        if planned_node not in collected_node_digests:
            if not collected_node_digests:
                # Empty collect inventory: no identity can be recovered for
                # any planned node (AC-FR0256-03 -> BLOCKED).
                gaps.append(
                    TraceGap(
                        ac=ac,
                        reason="identity_unrecoverable",
                        planned_node_id=planned_node,
                    )
                )
            else:
                gaps.append(
                    TraceGap(
                        ac=ac,
                        reason="node_missing",
                        planned_node_id=planned_node,
                    )
                )
        elif not collected_node_digests[planned_node]:
            gaps.append(
                TraceGap(
                    ac=ac,
                    reason="identity_unrecoverable",
                    planned_node_id=planned_node,
                )
            )
        elif planned_node not in persisted_evidence:
            gaps.append(
                TraceGap(
                    ac=ac,
                    reason="marker_only",
                    planned_node_id=planned_node,
                )
            )
    return gaps


def judge_real_coverage(
    ratio: float, threshold: float, excluded_sources: Sequence[str]
) -> CoverageJudgement:
    """Real-coverage hard gate (IF-PHASE-002, AC-FR0257-01).  Delegates to the
    implementation in ``phase0_quality.py`` to keep the facade thin."""
    from tracks.executor.phase0_quality import judge_real_coverage as _impl

    return _impl(ratio, threshold, excluded_sources)


def validate_registered_marks(
    declared_marks: Set[str], required_marks: Set[str]
) -> tuple[str, ...]:
    """Marks-registration hard gate (IF-PHASE-002, AC-FR0257-03).  Delegates
    to the implementation in ``phase0_quality.py``."""
    from tracks.executor.phase0_quality import validate_registered_marks as _impl

    return _impl(declared_marks, required_marks)


def build_seal_manifest(
    baseline_version: str,
    document_digests: Mapping[str, str],
    frozen_test_digests: Mapping[str, str],
    marks: Sequence[str],
    environment_contract_digest: str,
) -> SealManifest:
    """Construct a stable seal manifest (IF-PHASE-003, AC-FR0257-04/05).

    Delegates to the implementation in ``phase0_seal.py`` to keep the facade
    thin.  The ``seal_id`` is ``sha256(canonical_json(remaining fields))``.
    """
    from tracks.executor.phase0_seal import build_seal_manifest as _impl

    return _impl(
        baseline_version, document_digests, frozen_test_digests,
        marks, environment_contract_digest,
    )
