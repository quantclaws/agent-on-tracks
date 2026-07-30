---
spec_id: SPEC-NNN
task_id: {T-XXX}
status: draft
sha:
---

# {Feature Title} — Task Execution Log

## Phase 1: Red

- **Test file**: ...
- **Test case ID**: ...
- **CI status**: Red OK
- **Failure reason**: points to a feature yet to be implemented (not a bug in the test itself)
- **Commit**: `feat: green – {Number} {description}` or `refactor: {description}`

## Phase 2: Green

- **Implementation files**: ...
- **Implementation strategy**: ...
- **Test result**: all PASS OK
- **Commit**: `feat: green – {Number} {description}`

## Phase 3: Refactor

- **Refactor scope**: ...
- **Test result**: still all PASS OK
- **Lint/type check**: no errors OK
- **Commit**: `refactor: {description}`

## Runtime Quality Gate

- [ ] Red phase complete (test failed first)
- [ ] Green phase complete (associated tests pass)
- [ ] Refactor phase complete (no lint/type errors)
- [ ] Code committed
