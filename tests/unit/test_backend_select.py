"""Backend selection (effects boundary, IF-003 §9 / SPEC FR-020).

select_backend reads TRAC_AGENT_BACKEND / TRAC_FAKE_SIMULATE ONLY at the
boundary; decide()/project() never see it. The conftest autouse fixture forces
fake for the suite, so each test here sets the exact env it needs.
"""

import pytest

from tracks.effects import FakeBackend, select_backend
from tracks.effects.opencode import OpencodeBackend


def test_default_is_opencode(monkeypatch, tmp_path):
    monkeypatch.delenv("TRAC_AGENT_BACKEND", raising=False)
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    assert isinstance(select_backend(tmp_path, "v0.2"), OpencodeBackend)


def test_explicit_fake(monkeypatch, tmp_path):
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    assert isinstance(select_backend(tmp_path, "v0.2"), FakeBackend)


def test_explicit_opencode(monkeypatch, tmp_path):
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "opencode")
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    assert isinstance(select_backend(tmp_path, "v0.2"), OpencodeBackend)


def test_simulate_forces_fake_over_opencode(monkeypatch, tmp_path):
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "opencode")
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", "sage:SAGE_REVIEW=pass")
    assert isinstance(select_backend(tmp_path, "v0.2"), FakeBackend)


def test_unknown_backend_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "bogus")
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    with pytest.raises(ValueError):
        select_backend(tmp_path, "v0.2")


def test_model_env_unset_defaults_to_none(monkeypatch, tmp_path):
    monkeypatch.delenv("TRAC_AGENT_BACKEND", raising=False)
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    monkeypatch.delenv("TRAC_AGENT_MODEL", raising=False)
    backend = select_backend(tmp_path, "v0.2")
    assert isinstance(backend, OpencodeBackend)
    assert backend.model is None


def test_model_env_empty_defaults_to_none(monkeypatch, tmp_path):
    monkeypatch.delenv("TRAC_AGENT_BACKEND", raising=False)
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    monkeypatch.setenv("TRAC_AGENT_MODEL", "  ")
    backend = select_backend(tmp_path, "v0.2")
    assert isinstance(backend, OpencodeBackend)
    assert backend.model is None


def test_model_env_passed_to_opencode_backend(monkeypatch, tmp_path):
    monkeypatch.delenv("TRAC_AGENT_BACKEND", raising=False)
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    monkeypatch.setenv("TRAC_AGENT_MODEL", "litellm/deepseek-v4-flash")
    backend = select_backend(tmp_path, "v0.2")
    assert isinstance(backend, OpencodeBackend)
    assert backend.model == "litellm/deepseek-v4-flash"


def test_model_env_does_not_affect_fake(monkeypatch, tmp_path):
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")
    monkeypatch.setenv("TRAC_AGENT_MODEL", "litellm/deepseek-v4-flash")
    assert isinstance(select_backend(tmp_path, "v0.2"), FakeBackend)
