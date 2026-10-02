"""#221 (2026-10-02, Prism-adjudicated): if_ids closure checks the UNION of
the task's per-AC closure IF columns, not every row.

Live fire: the first legal multi-FR B94 integration task (T-011 carries
AC-FR0331-01/02 — closures name IF-TRACKER-001 — plus AC-FR0323-01 —
closure names IF-DOCSAVE-001) was rejected by the per-AC forall
quantifier ('IF-DOCSAVE-001 missing from closure' against the FR-0331
rows). The spec's ISLAND_GATE_1 contract lists six per-AC checks and no
per-AC IF-membership invariant; the forall was implementor overreach."""

from __future__ import annotations

from tracks.executor.taskgraph_validate import _task_closure_errors


def _arch(*rows):
    return "\n".join(rows).splitlines()


_SIX = ("- **FR-9001** owner=X surface=Y composition=Z wiring=W test=T "
        "evidence=E IF-AAA-001")
_SIX_B = ("- **FR-9002** owner=X surface=Y composition=Z wiring=W test=T "
          "evidence=E IF-BBB-001")


class _Task:
    task_id = "T-9"
    ac_refs = ("AC-FR9001-01", "AC-FR9002-01")
    if_ids = ("IF-AAA-001", "IF-BBB-001")


def test_union_semantics_multi_ac_task_passes():
    """Each IF in one closure row, task declares both: legal union, no error."""
    errors = _task_closure_errors(
        _Task(), _arch(_SIX, _SIX_B), ("owner=", "surface=", "composition=", "wiring=", "test=", "evidence=")
    )
    assert errors == [], errors


def test_truly_missing_if_still_rejected():
    """An IF absent from EVERY closure row must still fail (regression guard
    for the vacuous-pass concern: union != no check)."""

    class _T2(_Task):
        if_ids = ("IF-AAA-001", "IF-CCC-001")

    errors = _task_closure_errors(
        _T2(), _arch(_SIX, _SIX_B), ("owner=", "surface=", "composition=", "wiring=", "test=", "evidence=")
    )
    assert any("IF-CCC-001 missing from closure" in e for e in errors), errors
    assert not any("IF-AAA-001 missing" in e for e in errors)


def test_missing_closure_row_still_per_ac():
    """A missing six-tuple row keeps its per-AC error (unchanged semantics)."""

    class _T3(_Task):
        ac_refs = ("AC-FR9001-01", "AC-FR9999-01")
        if_ids = ()

    errors = _task_closure_errors(
        _T3(), _arch(_SIX), ("owner=", "surface=", "composition=", "wiring=", "test=", "evidence=")
    )
    assert any("six-tuple entry missing" in e for e in errors), errors
