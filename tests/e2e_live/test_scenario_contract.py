"""Deterministic scenario contract tests for the fast-path live test agents.

Asserts that shield-test-draft and prism-test-review scenarios carry the
required fast-path instructions and exact file paths, and that forbidden
instructions (RESPOND-style discussion loops in DRAFT, collectable-by-pytest)
are absent. Reads only the scenario JSON files; no live provider required.
"""
from __future__ import annotations

import json

from tests.e2e_live.harness import SCENARIO_DIR

_SHIELD_DRAFT = SCENARIO_DIR / "shield-test-draft.json"
_PRISM_REVIEW = SCENARIO_DIR / "prism-test-review.json"

_SHIELD_REQUIRED_FRAGMENTS = (
    "./code-stats",
    "assignment.test_tasks",
    "tests/integration/test_code_stats_contract.py",
    "tests/e2e/test_code_stats_happy.py",
    "exactly two files",
    "TRACKS-TRACE",
    "tmp_path",
    "Runtime owns evidence",
    "5 minutes",
)

_SHIELD_FORBIDDEN_FRAGMENTS = (
    "If Prism has opened discussion threads",
    "trac discuss query",
    "collectable by pytest",
)

_SHIELD_MUST_FORBID_TOPICS = (
    ".tracks/runtime",
    "git history",
    "install logs",
    "sibling runs",
)

_PRISM_REQUIRED_FRAGMENTS = (
    "tests/integration/test_code_stats_contract.py",
    "tests/e2e/test_code_stats_happy.py",
    "criteria pack",
    "REVISE",
    "pass/revise",
)

_PRISM_MUST_FORBID_TOPICS = (
    ".tracks/runtime",
    "git history",
)


def _load_required_action(path):
    return json.loads(path.read_text(encoding="utf-8"))["required_action"]


def test_shield_draft_scenario_resides_in_scenarios_dir():
    assert _SHIELD_DRAFT.resolve().parent == SCENARIO_DIR.resolve()


def test_shield_draft_carries_required_fast_path_fragments():
    action = _load_required_action(_SHIELD_DRAFT)
    for fragment in _SHIELD_REQUIRED_FRAGMENTS:
        assert fragment in action, (
            f"shield-test-draft missing required fragment: {fragment!r}"
        )


def test_shield_draft_omits_forbidden_fragments():
    action = _load_required_action(_SHIELD_DRAFT)
    for fragment in _SHIELD_FORBIDDEN_FRAGMENTS:
        assert fragment not in action, (
            f"shield-test-draft carries forbidden fragment: {fragment!r}"
        )


def test_shield_draft_explicitly_forbids_runtime_and_history_inspection():
    action = _load_required_action(_SHIELD_DRAFT)
    for topic in _SHIELD_MUST_FORBID_TOPICS:
        assert topic in action, (
            f"shield-test-draft must explicitly forbid: {topic!r}"
        )


def test_shield_draft_explicitly_forbids_running_pytest():
    action = _load_required_action(_SHIELD_DRAFT)
    assert "do not run pytest" in action.lower()


def test_shield_draft_names_exactly_two_test_files():
    action = _load_required_action(_SHIELD_DRAFT)
    assert action.count("tests/integration/test_code_stats_contract.py") == 1
    assert action.count("tests/e2e/test_code_stats_happy.py") == 1


def test_prism_review_scenario_resides_in_scenarios_dir():
    assert _PRISM_REVIEW.resolve().parent == SCENARIO_DIR.resolve()


def test_prism_review_carries_required_fast_path_fragments():
    action = _load_required_action(_PRISM_REVIEW)
    for fragment in _PRISM_REQUIRED_FRAGMENTS:
        assert fragment in action, (
            f"prism-test-review missing required fragment: {fragment!r}"
        )


def test_prism_review_explicitly_forbids_runtime_and_history_inspection():
    action = _load_required_action(_PRISM_REVIEW)
    for topic in _PRISM_MUST_FORBID_TOPICS:
        assert topic in action, (
            f"prism-test-review must explicitly forbid: {topic!r}"
        )


def test_prism_review_explicitly_forbids_running_pytest():
    action = _load_required_action(_PRISM_REVIEW)
    assert "do not run pytest" in action.lower()


def test_prism_review_names_exactly_two_test_files():
    action = _load_required_action(_PRISM_REVIEW)
    assert action.count("tests/integration/test_code_stats_contract.py") == 1
    assert action.count("tests/e2e/test_code_stats_happy.py") == 1
