"""IF-HOTFIX262-REVIEW-001 blocking-review-findings predicate (interface stub).

`has_blocking_findings` returns True iff any finding carries
severity in {"blocker", "high", "critical"} (design-delta INV-3).

verdict == "pass" with blocking findings is a contradictory verdict =
schema/protocol violation: rejected fail-closed at ingest; downstream
consumers treat `verdict != "pass" OR has_blocking_findings(findings)`
as failed. Stub for Shield contract tests; behavior implemented by Devon
(ARCH-012-HF262 §2).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

BLOCKING_SEVERITIES = frozenset({"blocker", "high", "critical"})


def has_blocking_findings(findings: Iterable[Mapping]) -> bool:
    raise NotImplementedError("IF-HOTFIX262-REVIEW-001")
