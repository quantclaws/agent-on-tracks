# Task Plan

## Task List

| ID | Task description | Related test | Target file | Depends on | Parallel marker | Status |
| --- | --- | --- | --- | --- | --- | --- |
| T-001 | Build the baseline projection | AC-FR0020-01 | tracks/kernel/machine.py | T-002 |  | pending |
| T-002 | Validate the task graph | AC-FR0030-02 | tracks/kernel/machine.py | T-001 | [P] | pending |

## Dependency Graph

```text
T-001 -> T-002 -> T-001
```

## Acceptance Coverage

| AC | Covered by |
| --- | --- |
| AC-FR0020-01 | T-001 |
| AC-FR0030-02 |  |

The fixture intentionally contains a dependency cycle, an overlapping target
file, and an acceptance coverage gap.  It is used only as deterministic input
for the public `trac validate --file` contract.
