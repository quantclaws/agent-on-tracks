---
bug_id: BUG-NNN
created: {YYYY-MM-DD}
severity: {P0 blocker / P1 critical / P2 normal / P3 minor}
status: draft
sha:
---

# BUG-NNN — Bug Report & Fix

## Reproduction Steps

1. ...
2. ...
3. Observed behavior: ...
4. Expected behavior: ...

## Root Cause

<!-- Root cause located by Devon -->

## Fix Plan

<!-- Fix strategy -->

## Phase 1: Red (Bug reproduction test)

- **Test file**: ...
- **CI status**: Red OK (confirmed the bug can be captured by the test)
- **Commit**: `fix: green – BUG-{Number} {description}` or `refactor: BUG-{Number} {description}`

## Phase 2: Green (Fix)

- **Fixed files**: ...
- **Test result**: PASS OK
- **Commit**: `fix: green – BUG-{Number} {description}`

## Phase 3: Refactor

- **Refactor scope**: ...
- **Commit**: `refactor: BUG-{Number} {description}`

## Shield Regression Check

- [ ] Full test suite passes
- [ ] No new bugs introduced
- [ ] Original bug confirmed fixed
