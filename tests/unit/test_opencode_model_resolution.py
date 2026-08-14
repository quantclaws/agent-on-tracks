"""OpencodeBackend model resolution: two-layer dispatch policy (spec §3.1,
ARCH §4a) - (1) TRAC_AGENT_MODEL via self.model wins; (2) None means opencode
resolves its own configured default (no --model flag). Model selection is an
opencode-config concern, never hardcoded by tracks."""

from tests.unit.helpers import capture_popen_cmd
from tracks.effects.opencode import OpencodeBackend


def _capture_cmd(monkeypatch, backend, name="Scribe", prompt="p"):
    captured = capture_popen_cmd(monkeypatch)
    backend._run(name, prompt)
    return captured["cmd"]


def test_no_model_means_no_model_flag(tmp_path, monkeypatch):
    """Layer 2: when self.model is None, no --model flag is emitted; opencode
    resolves its own configured default."""
    backend = OpencodeBackend(tmp_path, "v0.1")
    cmd = _capture_cmd(monkeypatch, backend, "Scribe")
    assert "--model" not in cmd


def test_explicit_model_passes_model_flag(tmp_path, monkeypatch):
    """Layer 1 wins: explicit self.model (TRAC_AGENT_MODEL via select_backend)
    is passed as --model <value> to opencode run."""
    backend = OpencodeBackend(tmp_path, "v0.1", model="litellm/deepseek-v4-flash")
    cmd = _capture_cmd(monkeypatch, backend, "Scribe")
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "litellm/deepseek-v4-flash"


def test_model_none_does_not_read_canonical_files(tmp_path, monkeypatch):
    """Model resolution never touches canonical agent files: model selection is
    an opencode-config concern, not an IQ-routing concern."""
    backend = OpencodeBackend(tmp_path, "v0.1")
    backend._canonical = tmp_path / "no-such-agents"
    cmd = _capture_cmd(monkeypatch, backend, "Scribe")
    assert "--model" not in cmd
