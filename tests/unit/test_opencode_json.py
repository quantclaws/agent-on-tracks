"""OpencodeBackend._parses_json: JSON stream classification for opencode
--format json stdout. The parser must tolerate non-JSON log lines (e.g.
"[Opencode Logger] Plugin initialized!") that opencode emits on stdout
alongside JSON event lines, while still rejecting truly truncated output."""

from tracks.effects.opencode import OpencodeBackend

_parses = OpencodeBackend._parses_json


def test_single_json_object_parses():
    assert _parses('{"type": "result", "status": "done"}') is True


def test_pure_ndjson_parses():
    out = '{"type": "step_start"}\n{"type": "step_finish"}'
    assert _parses(out) is True


def test_log_line_prefix_then_ndjson_parses():
    """Real opencode v1.18.1 emits a [Opencode Logger] line before JSON
    events on stdout. This must not be classified as json_truncated."""
    out = '[Opencode Logger] Plugin initialized!\n{"type": "step_start"}\n{"type": "step_finish"}'
    assert _parses(out) is True


def test_non_json_garbage_rejected():
    assert _parses("{not valid json") is False


def test_truncated_json_event_rejected():
    """A line starting with '{' that fails to parse is a truncated JSON
    event, not log noise -- must be rejected even if other lines parse."""
    out = '{"type": "step_start"}\n{"type": "tool_use", "partial":'
    assert _parses(out) is False


def test_log_line_only_no_json_rejected():
    assert _parses("[Opencode Logger] Plugin initialized!") is False


def test_empty_string_rejected():
    assert _parses("") is False
