"""Shape-aware final-reply JSON extraction (2026-08-27, run 01M0S0FQ T-015).

Regression: deepseek-v4-flash echoed the assignment context JSON (the
pre_dirty_snapshot path->sha mapping) AFTER its evidence manifest; the blind
last-wins pick grabbed the echo and the round died as "Devon evidence
missing". The extractor now prefers the last object carrying the required
manifest keys, with last-wins kept as the fallback.
"""

from __future__ import annotations

from tracks.effects.opencode import OpencodeBackend

MANIFEST = (
    '{"phase":"refactor","changed_paths":["tracks/executor/executor.py"],'
    '"commands":[],"manifest_compliance":true,"pre_identity":"a",'
    '"post_identity":"b","implemented_if_ids":[],"result_identity":"r"}'
)
CONTEXT_ECHO = (
    '{"tracks/executor/executor.py":"544fbdc","tracks/kernel/machine.py":"96fa3be"}'
)


def test_default_last_wins_is_unchanged():
    """No shape hint: blind last-wins, exactly the historical behavior."""
    text = f"report prose\n{MANIFEST}\nnote\n{CONTEXT_ECHO}"
    got = OpencodeBackend._first_json_object(text)
    assert sorted(got) == ["tracks/executor/executor.py", "tracks/kernel/machine.py"]


def test_shape_hint_beats_context_echo():
    """The context echo after the manifest no longer poisons the pick."""
    text = f"REFACTOR 完成。最终报告：\n```json\n{MANIFEST}\n```\n{CONTEXT_ECHO}"
    got = OpencodeBackend._first_json_object(text, ("phase", "changed_paths"))
    assert got is not None
    assert got["phase"] == "refactor"
    assert got["changed_paths"] == ["tracks/executor/executor.py"]


def test_shape_hint_falls_back_to_last_wins():
    """No object carries the required keys: last object overall is returned
    (callers keep their own missing-field handling)."""
    text = f"prose\n{CONTEXT_ECHO}"
    got = OpencodeBackend._first_json_object(text, ("phase",))
    assert got is not None
    assert "tracks/executor/executor.py" in got


def test_fast_path_pure_json_unchanged():
    got = OpencodeBackend._first_json_object(MANIFEST, ("phase",))
    assert got["phase"] == "refactor"
    got_no_hint = OpencodeBackend._first_json_object(MANIFEST)
    assert got_no_hint["phase"] == "refactor"


def test_shaped_pick_takes_last_matching_not_first():
    """Two matching objects: the LAST matching one wins (manifests are at
    the end of agent output; a stale earlier copy never shadows a newer
    final manifest)."""
    stale = '{"phase":"red","changed_paths":["old.py"]}'
    text = f"{stale}\n{MANIFEST}"
    got = OpencodeBackend._first_json_object(text, ("phase", "changed_paths"))
    assert got["phase"] == "refactor"
