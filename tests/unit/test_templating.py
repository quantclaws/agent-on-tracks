"""Document templating (ARCH-003 §5a / SPEC-003 FR-140).

templating reads canonical templates from tracks/templates/ by kind and renders
the M-START story skeleton (raw requirement verbatim in §1, other placeholders
preserved). Isolated here: wiring into cmd_start / the template check is a later
increment.
"""
import pytest

from tracks import templating


def test_load_template_story():
    text = templating.load_template("story")
    assert "## 1. 原始输入" in text
    assert "## 2. 用户意图" in text


def test_load_template_unknown_raises():
    with pytest.raises(FileNotFoundError):
        templating.load_template("bogus")


def test_render_story_skeleton_fills_fields():
    text = templating.render_story_skeleton("做一个X", "2026-07-31")
    assert "story_id: S-001" in text
    assert "created: 2026-07-31" in text
    assert "> 做一个X" in text
    # other sections / placeholders preserved for Scribe
    assert "## 2. 用户意图" in text
    assert "{一句话标题}" in text


def test_render_story_skeleton_custom_id():
    text = templating.render_story_skeleton("x", "2026-01-01", "S-042")
    assert "story_id: S-042" in text
    assert "# S-042:" in text
    assert "S-NNN" not in text


def test_render_story_skeleton_multiline_blockquote():
    text = templating.render_story_skeleton("第一行\n第二行", "2026-07-31")
    assert "> 第一行" in text
    assert "> 第二行" in text


def test_render_story_skeleton_no_placeholder_left():
    text = templating.render_story_skeleton("原始需求文本", "2026-07-31")
    assert "{用户原始输入，逐字记录，不修改或转述}" not in text
