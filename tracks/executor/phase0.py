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
        for node in _planned_node_parts(planned_node):
            gap = _node_gap(ac, node, collected_node_digests, persisted_evidence)
            if gap is not None:
                gaps.append(gap)
    return gaps


def _node_gap(
    ac: str,
    node: str,
    collected_node_digests: Mapping[str, str],
    persisted_evidence: Mapping[str, str],
) -> TraceGap | None:
    """Classify one planned node's evidence state (the FR-0256 branches), or
    None when the node is collected with identity and evidence (no gap).

    An empty collect inventory degenerates ``node_missing`` into
    ``identity_unrecoverable`` (AC-FR0256-03: no identity can be recovered
    for any planned node -> BLOCKED)."""
    if node not in collected_node_digests:
        reason = "identity_unrecoverable" if not collected_node_digests else "node_missing"
    elif not collected_node_digests[node]:
        reason = "identity_unrecoverable"
    elif node not in persisted_evidence:
        reason = "marker_only"
    else:
        return None
    return TraceGap(ac=ac, reason=reason, planned_node_id=node)


def _planned_node_parts(planned_node: str) -> list[str]:
    """The node ids of one planned binding cell (FR-0256 scan input).

    #207: Shield's multi-test coverage rows write ``nodeA + nodeB`` (e.g. a
    v0.9 web AC bound to an integration case plus the e2e journey). The scan
    must classify each member on its own — treating the joined cell as one
    id never matched any collected node and blocked phase0 on the first
    baseline that carries such rows (run 01M3E7SAANXKW1V73W8B8Q3G86)."""
    parts = [part.strip() for part in str(planned_node).split(" + ")]
    return [part for part in parts if part] or [str(planned_node)]


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
