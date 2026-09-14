"""T-021 RED (plan/idempotency-key/reconcile hardening round): direct pins
for the write-ahead planner and the idempotency-key formula (IF-PUBLISH-002,
FR-0275/NFR-0144, interfaces §1h).

The publish module face is implemented and heavily pinned elsewhere (reconcile
red ×3, guards ×11, runtime-direct ×10, effects coverage); the faces below
were previously reachable only indirectly through the runtime handler.  They
pin the pure contract directly:

- ``plan_operations``: one write-ahead record per declared step, each bound
  to the preview digest and carrying its own idempotency key; a malformed
  step fails closed to an empty plan (never a guessed partial); a merge step
  bound to a prepared sync product publishes the product identity fields.
- ``operation_idempotency_key``: sha256 over the canonical
  {preview_digest, kind, target} composition — deterministic and distinct
  per component.
- ``reconcile_operation``: an explicit remote disagreement (matches=False)
  conflicts even without digest comparison (IF-PUBLISH-002: remote decides).
"""

from __future__ import annotations

from tracks.executor.publish import (
    operation_idempotency_key,
    plan_operations,
    reconcile_operation,
)


def test_plan_operations_writes_a_record_per_step():
    """Every declared KIND:TARGET step becomes one write-ahead record bound
    to the preview digest with its own idempotency key (FR-0275-01)."""
    records = plan_operations(
        {"steps": ["merge:main", "tag:v0.8.0"]}, "sha256:preview"
    )
    assert [r["operation_kind"] for r in records] == ["merge", "tag"], (
        f"assertion failure: one record per declared step, got {records!r}"
    )
    assert [r["target"] for r in records] == ["main", "v0.8.0"]
    for record in records:
        assert record["preview_digest"] == "sha256:preview", (
            f"assertion failure: record must bind the preview digest, got {record!r}"
        )
        assert record["idempotency_key"].startswith("sha256:"), (
            f"assertion failure: record must carry the idempotency key, got {record!r}"
        )
    assert records[0]["idempotency_key"] != records[1]["idempotency_key"], (
        f"assertion failure: distinct targets must mint distinct keys, got {records!r}"
    )


def test_plan_operations_malformed_step_fails_closed():
    """A step without the KIND:TARGET shape fails closed to an empty plan —
    never a guessed partial plan (FR-0275-04 malformed face)."""
    for malformed in (
        {"steps": ["merge"]},
        {"steps": [":target"]},
        {"steps": ["merge:"]},
        {"steps": ["merge:main", 7]},
        "not-a-dict",
    ):
        records = plan_operations(malformed, "sha256:preview")
        assert records == [], (
            f"assertion failure: malformed plan {malformed!r} must fail closed "
            f"to an empty plan, got {records!r}"
        )


def test_plan_operations_merge_carries_sync_product_identity():
    """A merge step bound to a prepared sync product carries the product
    identity fields into the planned record (FR-0277-02)."""
    product = {
        "target": "main",
        "baseline_sha": "b" * 40,
        "source_candidate_sha": "c" * 40,
        "product_sha": "p" * 40,
        "product_tree": "t" * 40,
        "evidence_digests": {"full_f": "sha256:e"},
    }
    records = plan_operations(
        {"steps": ["merge:main"], "sync_products": [product]}, "sha256:preview"
    )
    assert len(records) == 1, f"assertion failure: one merge record, got {records!r}"
    for field in (
        "baseline_sha",
        "source_candidate_sha",
        "product_sha",
        "product_tree",
    ):
        assert records[0][field] == product[field], (
            f"assertion failure: merge record must carry {field} from the sync "
            f"product, got {records[0]!r}"
        )


def test_operation_idempotency_key_formula_is_canonical_and_distinct():
    """NFR-0144: idempotency_key = sha256(canonical {preview_digest, kind,
    target}) — deterministic, prefixed, and distinct per component."""
    k1 = operation_idempotency_key("sha256:p", "tag", "v0.8.0")
    assert k1.startswith("sha256:"), (
        f"assertion failure: key must be sha256-prefixed, got {k1!r}"
    )
    assert k1 == operation_idempotency_key("sha256:p", "tag", "v0.8.0"), (
        "assertion failure: the key must be deterministic"
    )
    assert operation_idempotency_key("sha256:p", "tag", "v0.8.1") != k1, (
        "assertion failure: a different target must mint a different key"
    )
    assert operation_idempotency_key("sha256:p", "merge", "v0.8.0") != k1, (
        "assertion failure: a different kind must mint a different key"
    )
    assert operation_idempotency_key("sha256:q", "tag", "v0.8.0") != k1, (
        "assertion failure: a different preview must mint a different key"
    )


def test_reconcile_conflicts_on_explicit_remote_disagreement():
    """IF-PUBLISH-002: an explicit remote disagreement (matches=False)
    conflicts even without digest comparison — the remote decides."""
    verdict = reconcile_operation(
        {"idempotency_key": "sha256:k", "target": "t", "kind": "tag"},
        {"exists": True, "matches": False},
    )
    assert verdict == "conflict", (
        f"assertion failure: an explicit remote disagreement must conflict, "
        f"got {verdict!r}"
    )
