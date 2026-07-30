"""OpencodeBackend tests — L2 stand-in (test-plan §6).

opencode is an EXTERNAL dependency: it is stood-in by a fake `opencode`
executable that mimics `opencode run` (edits the target, emits JSON), so the real
subprocess pipeline (materialize -> subprocess -> JSON -> target diff -> audit)
is exercised without a live model. We never mock tracks' own code.
"""
import os
import shutil
import stat
import subprocess

import pytest

from tracks.effects.opencode import OpencodeBackend

STANDIN = '''#!/usr/bin/env python3
import os, sys, json, time, signal
behavior = os.environ.get("FAKE_OPENCODE_BEHAVIOR", "edit_target")
target = os.environ.get("FAKE_OPENCODE_TARGET")
extra = os.environ.get("FAKE_OPENCODE_EXTRA")
if behavior == "sleep":
    time.sleep(30); sys.exit(0)
if behavior == "self_kill":
    os.kill(os.getpid(), signal.SIGKILL)
if behavior == "exit_1":
    sys.stderr.write("boom\\n"); sys.exit(1)
if behavior == "provider_error":
    sys.stderr.write("provider model credentials unavailable\\n"); sys.exit(1)
if behavior == "bad_json":
    if target:
        open(target, "a").write("\\nagent edit\\n")
    sys.stdout.write("{not valid json"); sys.exit(0)
if behavior in ("edit_target", "edit_extra") and target:
    open(target, "a").write("\\nagent edit\\n")
if behavior == "edit_extra" and extra:
    open(extra, "a").write("\\nover-reach\\n")
sys.stdout.write(json.dumps({"type": "result", "status": "done"})); sys.exit(0)
'''


@pytest.fixture
def fake_opencode(tmp_path, monkeypatch):
    """Put a fake `opencode` first on PATH (L2 stand-in)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "opencode"
    exe.write_text(STANDIN, encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


@pytest.fixture
def target_doc(host_repo):
    doc = host_repo / "story.md"
    doc.write_text("---\ntitle:\n---\n\n# Demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "story.md"], cwd=host_repo, check=True)
    subprocess.run(["git", "commit", "-m", "story skeleton"], cwd=host_repo,
                   check=True, capture_output=True)
    return doc


def backend(host_repo, timeout=10):
    return OpencodeBackend(host_repo, "v0.2", timeout=timeout)


def test_happy_path_edits_target_and_captures_diff(
        fake_opencode, target_doc, host_repo, monkeypatch):
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_target")
    monkeypatch.setenv("FAKE_OPENCODE_TARGET", str(target_doc))
    out = backend(host_repo).act("scribe", "DRAFT", "story.md", target_doc)
    assert out["status"] == "done"
    assert out["failure_class"] is None if "failure_class" in out else True
    assert "agent edit" in out["diff_ref"]
    # materialized agent definition is cleaned up (terminal cleanup, ARCH §4c)
    assert not (host_repo / ".opencode" / "agents" / "Scribe.md").exists()


def test_opencode_missing(target_doc, host_repo, monkeypatch, tmp_path):
    empty = tmp_path / "emptybin"
    empty.mkdir()
    os.symlink(shutil.which("git"), empty / "git")  # keep git; drop opencode
    monkeypatch.setenv("PATH", str(empty))  # no opencode anywhere
    out = backend(host_repo).act("scribe", "DRAFT", "story.md", target_doc)
    assert out["status"] == "failed"
    assert out["failure_class"] == "opencode_missing"


def test_non_zero_exit(fake_opencode, target_doc, host_repo, monkeypatch):
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "exit_1")
    out = backend(host_repo).act("scribe", "DRAFT", "story.md", target_doc)
    assert out["status"] == "failed"
    assert out["failure_class"] == "non_zero_exit"


def test_provider_unavailable(fake_opencode, target_doc, host_repo, monkeypatch):
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "provider_error")
    out = backend(host_repo).act("sage", "SAGE_REVIEW", "story.md", target_doc)
    assert out["status"] == "failed"
    assert out["failure_class"] == "provider_unavailable"


def test_timeout_kills_and_classifies(fake_opencode, target_doc, host_repo, monkeypatch):
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "sleep")
    out = backend(host_repo, timeout=1).act("scribe", "DRAFT", "story.md", target_doc)
    assert out["status"] == "failed"
    assert out["failure_class"] == "timeout"


def test_signal(fake_opencode, target_doc, host_repo, monkeypatch):
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "self_kill")
    out = backend(host_repo).act("scribe", "DRAFT", "story.md", target_doc)
    assert out["status"] == "failed"
    assert out["failure_class"] == "signal"


def test_json_truncated(fake_opencode, target_doc, host_repo, monkeypatch):
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "bad_json")
    monkeypatch.setenv("FAKE_OPENCODE_TARGET", str(target_doc))
    out = backend(host_repo).act("scribe", "DRAFT", "story.md", target_doc)
    assert out["status"] == "failed"
    assert out["failure_class"] == "json_truncated"


def test_no_target_diff(fake_opencode, target_doc, host_repo, monkeypatch):
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "no_edit")
    monkeypatch.setenv("FAKE_OPENCODE_TARGET", str(target_doc))
    out = backend(host_repo).act("scribe", "DRAFT", "story.md", target_doc)
    assert out["status"] == "failed"
    assert out["failure_class"] == "no_target_diff"


def test_over_reach_detected_and_rolled_back(
        fake_opencode, target_doc, host_repo, monkeypatch):
    extra = host_repo / "EXTRA.md"
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_extra")
    monkeypatch.setenv("FAKE_OPENCODE_TARGET", str(target_doc))
    monkeypatch.setenv("FAKE_OPENCODE_EXTRA", str(extra))
    out = backend(host_repo).act("scribe", "DRAFT", "story.md", target_doc)
    assert out["status"] == "failed"
    assert out["failure_class"] == "over_reach"
    assert "EXTRA.md" in out["audit_evidence"]
    assert not extra.exists()  # agent-produced over-reach rolled back


def test_materialize_backs_up_and_restores_human_agent(
        fake_opencode, target_doc, host_repo, monkeypatch):
    agents_dir = host_repo / ".opencode" / "agents"
    agents_dir.mkdir(parents=True)
    human = agents_dir / "Scribe.md"
    human.write_text("HUMAN AGENT\n", encoding="utf-8")
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_target")
    monkeypatch.setenv("FAKE_OPENCODE_TARGET", str(target_doc))
    out = backend(host_repo).act("scribe", "DRAFT", "story.md", target_doc)
    assert out["status"] == "done"
    # Human's pre-existing agent is restored, not clobbered (ARCH §4c)
    assert human.read_text(encoding="utf-8") == "HUMAN AGENT\n"


def test_unknown_role_is_fake_only(fake_opencode, target_doc, host_repo):
    out = backend(host_repo).act("lex", "LEX_REVIEW", "spec.md", target_doc)
    assert out["status"] == "failed"
    assert out["failure_class"] == "provider_unavailable"
