"""OpencodeBackend model resolution: three-layer dispatch policy (spec §3.1,
ARCH §4a) - (1) TRAC_AGENT_MODEL via self.model, (2) agent IQ frontmatter
mapped via IQ_MODEL, (3) opencode's own configured default (no --model)."""

import pytest

from tests.unit.helpers import capture_popen_cmd
from tracks.effects.opencode import IQ_MODEL, OpencodeBackend


def _capture_cmd(monkeypatch, backend, name="Scribe", prompt="p"):
    captured = capture_popen_cmd(monkeypatch)
    backend._run(name, prompt)
    return captured["cmd"]


def _write_agent(canonical, name, body):
    canonical.mkdir(parents=True, exist_ok=True)
    path = canonical / f"{name}.md"
    path.write_text(body, encoding="utf-8")
    return path


def test_iq_a_resolves_to_model_when_model_none(tmp_path, monkeypatch):
    """Layer 2: an A-IQ agent resolves to ark/glm-5.2 when self.model is None
    (policy: A -> ark/glm-5.2). Uses the real shipped Scribe.md (IQ: A)."""
    backend = OpencodeBackend(tmp_path, "v0.1")
    assert (backend._canonical / "Scribe.md").exists()
    cmd = _capture_cmd(monkeypatch, backend, "Scribe")
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == IQ_MODEL["A"] == "ark/glm-5.2"


def test_explicit_model_overrides_iq_mapping(tmp_path, monkeypatch):
    """Layer 1 wins: explicit self.model (TRAC_AGENT_MODEL via select_backend)
    overrides the agent's IQ mapping even when IQ would resolve a model
    (Scribe IQ: A -> ark/glm-5.2 is bypassed for the explicit value)."""
    backend = OpencodeBackend(tmp_path, "v0.1",
                              model="litellm/deepseek-v4-flash")
    cmd = _capture_cmd(monkeypatch, backend, "Scribe")
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "litellm/deepseek-v4-flash"


def test_missing_canonical_file_adds_no_model_flag(tmp_path, monkeypatch):
    """Layer 2 tolerance: a missing canonical definition file -> no IQ -> no
    --model flag (opencode resolves its own configured default)."""
    backend = OpencodeBackend(tmp_path, "v0.1")
    backend._canonical = tmp_path / "no-such-agents"
    cmd = _capture_cmd(monkeypatch, backend, "Scribe")
    assert "--model" not in cmd


@pytest.mark.parametrize("body", [
    "---\ndescription: x\n---\nbody\n",
    "no frontmatter at all\n",
    "---\nIQ: S\n---\nbody\n",
], ids=["no-iq-line", "no-frontmatter", "iq-absent-from-map"])
def test_frontmatter_parse_is_tolerant(tmp_path, monkeypatch, body):
    """Layer 2 tolerance: a frontmatter without an IQ line, no frontmatter at
    all, and an IQ absent from IQ_MODEL (e.g. S, TBD) all resolve to no
    --model flag (opencode default)."""
    backend = OpencodeBackend(tmp_path, "v0.1")
    backend._canonical = tmp_path / "agents"
    _write_agent(backend._canonical, "Scribe", body)
    cmd = _capture_cmd(monkeypatch, backend, "Scribe")
    assert "--model" not in cmd
