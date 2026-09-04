"""M4 anchor-satisfiability static rules (convergence plan 2026-09-05).

Birth-time interception of the two mechanically recognizable
unsatisfiable-anchor classes that burned T-042 (~40 dispatches,
2026-09-04):

- **HEAD-equality across runtime activity** (rule 1): a test captures
  ``rev-parse HEAD``, drives the runtime (walk / ``trac run`` / commits),
  then asserts equality against the stale capture. The walker journey
  itself commits (init scaffold, stage seals, agent commits, phase0
  seeds), so the target moves (test_verify_candidate.py:78: pre-walk
  anchor blamed a nonexistent wrong-tree defect). A capture that stays
  adjacent to its assertion (no runtime activity between) is stable and
  passes.
- **Count-equality between event snapshots** (rule 1):
  ``len(after) == len(before)`` on two lists derived from the event log
  with runtime activity between their captures -- replay/repair
  legitimately emit events, so the count moves
  (test_verify_candidate.py:150 vs test_inplace_repair.py rewalk:
  mutually exclusive anchors, ping-ponged to budget exhaustion).
  Deterministic-scenario counts (``len(events_of_type) == const``) are
  NOT flagged: the existing suite uses that shape legitimately and a
  gate that fires on legitimate output is a tax, not a guard (M6
  lesson).

Rule 2 (CLI-half assertions must be event-existence / payload-predicate
/ status-rendering) is the general form these two patterns instantiate;
rule 3 (two anchors asserting opposite outcomes on the same runtime
path) is not texturally recognizable -- both live in the Shield skill
discipline, with rule 3 additionally caught at runtime by the S1
oscillation detector (tracks.executor.oscillation).

Line-level suppression for legitimate cases: append ``#
tracks-anchor-ok`` to the physical line.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_SUPPRESS = "# tracks-anchor-ok"
# Runtime activity between two captures: the moving-target risk only
# materializes when the runtime (or the host repo) legitimately advances
# state between capture and assertion.
_ACTIVITY = re.compile(r"trac\(|walk_|subprocess|commit|_git\(|git, ")


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
    return ""


def _is_event_log_call(node: ast.AST) -> bool:
    return _call_name(node) == "event_log"


def _activity_between(lines: list[str], a: int, b: int) -> bool:
    lo, hi = sorted((a, b))
    return any(_ACTIVITY.search(ln) for ln in lines[lo : hi - 1])


def _collect_bindings(tree: ast.Module) -> tuple[dict, dict, dict]:
    """Pass 1: name -> defining line for the three binding classes."""
    event_sources: dict[str, int] = {}
    event_lists: dict[str, int] = {}
    head_vars: dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.targets[0], ast.Name):
            continue
        target = node.targets[0].id
        value = node.value
        if _is_event_log_call(value):
            event_sources[target] = node.lineno
        elif isinstance(value, ast.ListComp):
            _bind_event_list(value, target, node.lineno, event_sources, event_lists)
        else:
            _bind_head_var(value, target, node.lineno, head_vars)
    return event_sources, event_lists, head_vars


def _bind_event_list(value, target, lineno, event_sources, event_lists) -> None:
    for gen in value.generators:
        if _is_event_log_call(gen.iter):
            event_lists[target] = lineno
            return
        if isinstance(gen.iter, ast.Name) and gen.iter.id in event_sources:
            event_lists[target] = lineno
            return


def _bind_head_var(value, target, lineno, head_vars) -> None:
    if not isinstance(value, ast.Call):
        return
    consts = [
        a.value
        for a in value.args
        if isinstance(a, ast.Constant) and isinstance(a.value, str)
    ]
    if "rev-parse" in consts and "HEAD" in consts:
        head_vars[target] = lineno


def _event_len_names(side: ast.AST, event_lists: dict[str, int]) -> list[str]:
    """The event-derived list names inside one ``len(...)`` operand."""
    if _call_name(side) != "len" or not side.args:
        return []
    arg = side.args[0]
    if isinstance(arg, ast.Name) and arg.id in event_lists:
        return [arg.id]
    return []


def _snapshot_count_violation(node: ast.Compare, event_lists, lines) -> str | None:
    """len(A) == len(B) between two event-log snapshots with activity
    between the captures."""
    names: list[str] = []
    for side in (node.left, *node.comparators):
        if _call_name(side) == "len" and side.args:
            arg = side.args[0]
            if isinstance(arg, ast.Name) and arg.id in event_lists:
                names.append(arg.id)
    if len(names) != 2:
        return None
    if not _activity_between(lines, event_lists[names[0]], event_lists[names[1]]):
        return None
    return (
        f"line {node.lineno}: count-equality between event-log snapshots "
        f"({ast.unparse(node)}) -- runtime activity between the captures "
        "legitimately moves the count (rule 1, M4)"
    )


def _head_equality_violation(node: ast.Compare, head_vars, lines) -> str | None:
    """Equality against a rev-parse HEAD capture that runtime activity
    has rendered stale."""
    for side in (node.left, *node.comparators):
        if (
            isinstance(side, ast.Name)
            and side.id in head_vars
            and _activity_between(lines, head_vars[side.id], node.lineno)
        ):
            return (
                f"line {node.lineno}: equality on a rev-parse HEAD "
                f"capture ({ast.unparse(node)}) with runtime activity "
                "between capture and assertion -- HEAD moved "
                "(rule 1, M4)"
            )
    return None


def _violations(tree: ast.Module, lines: list[str]) -> list[str]:
    _event_sources, event_lists, head_vars = _collect_bindings(tree)
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
            continue
        if 0 < node.lineno <= len(lines) and _SUPPRESS in lines[node.lineno - 1]:
            continue
        violation = _snapshot_count_violation(node, event_lists, lines)
        if violation is None:
            violation = _head_equality_violation(node, head_vars, lines)
        if violation is not None:
            violations.append(violation)
    return violations


def anchor_static_violations(path: Path) -> list[str]:
    """The rule-1 violations in one test module (empty when clean).

    Fail-open on unreadable/unparseable sources: collection/execution
    catches those classes with far better diagnostics (§1.0.4 fail-closed
    stays THEIR contract; this lint only rules on what it can parse).
    """
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
    except (OSError, SyntaxError, ValueError):
        return []
    return _violations(tree, src.splitlines())
