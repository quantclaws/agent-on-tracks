---
spec_id: SPEC-NNN
created: {YYYY-MM-DD}
status: draft
sha:
---

# {Feature Title} — Task Plan

## Task List

| ID | Task description | Related test | Target file | Depends on | Parallel marker | Status |
|----|---------|---------|---------|------|---------|------|
| T-001 | ... | UT-001 | src/foo.ts | — | [P] | todo |
| T-002 | ... | UT-002 | src/bar.ts | T-001 | | todo |

- `[P]` = can run in parallel
- `—` means no dependency, can start first

## Dependency Graph

```
T-001 ──→ T-002 ──→ T-004
  │
  └──→ T-003 [P] ──→ T-004
```

## Runtime Review Result

- [ ] Each task is associated with the correct test case
- [ ] Dependencies are marked correctly
- [ ] Parallel markers are reasonable (no file conflicts)
- [ ] No missing spec requirements
