# Synthetic task-plan fixture: cycle + overlap + coverage gap

Deterministic fixture for `trac validate --file task-plan.md` (FR-0030/FR-0180,
IF-IMPL-003). Fixes three known defect structures:

- dependency cycle: T-101 -> T-102 -> T-101
- overlapping target paths: T-101 and T-102 both target `tracks/example/io.py`
- coverage gap: required AC-FR0030-01 is not linked by any task's AC column

## Task List

| ID | Task description | Related test | Target file | Depends on | Parallel marker | Status |
|---|---|---|---|---|---|---|
| T-101 | Implement scope A | tests/integration/test_scope.py | tracks/example/io.py | T-102 | | open |
| T-102 | Implement scope B | tests/integration/test_cycle.py | tracks/example/io.py | T-101 | | open |
| T-103 | Unrelated task | tests/integration/test_other.py | tracks/example/other.py | | | open |

## Dependency Graph

- T-101 -> T-102
- T-102 -> T-101

## Required AC coverage

- AC-FR0030-02: T-101
- AC-FR0030-03: T-103
- AC-FR0030-01: (none -- not covered by any task)