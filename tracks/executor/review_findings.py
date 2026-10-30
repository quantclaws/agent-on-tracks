"""IF-BLOCKREV-001 blocking-review-findings predicate.

`has_blocking_findings` returns True iff any finding carries
severity in {"blocker", "high", "critical"} (design-delta INV-3).

verdict == "pass" with blocking findings is a contradictory verdict =
schema/protocol violation: rejected fail-closed at ingest; downstream
consumers treat `verdict != "pass" OR has_blocking_findings(findings)`
as failed (ARCH-012-HF262 §2).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

BLOCKING_SEVERITIES = frozenset({"blocker", "high", "critical"})


def has_blocking_findings(findings: Iterable[Mapping] | None) -> bool:
    """True when any finding carries a blocking severity.

    Unknown severities (including non-strings and missing keys) are not
    blocking: the closed set is the contract, so a typo cannot silently
    widen the gate.
    """
    if findings is None:
        return False
    return any(
        isinstance(finding, Mapping)
        and finding.get("severity") in BLOCKING_SEVERITIES
        for finding in findings
    )


def verdict_failed(payload: Mapping) -> bool:
    """Downstream hard invariant: only a clean ``pass`` is success.

    ``verdict != "pass"`` or a blocking finding (INV-3) is failure. Every
    downstream consumer (release preview/authorization/status) shares this
    one rule instead of re-deriving the invariant.
    """
    return payload.get("verdict") != "pass" or has_blocking_findings(
        payload.get("findings")
    )
