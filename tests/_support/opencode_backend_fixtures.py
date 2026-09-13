"""L2 OpenCode stand-in executable and shared host fixtures."""

import os
import stat
import subprocess

import pytest

from tracks.discuss import writer
from tracks.discuss.locate import token_for
from tracks.discuss.parser import parse_threads
from tracks.effects.opencode import (
    OpencodeBackend,
)

STANDIN = """#!/usr/bin/env python3
import os, sys, json, time, signal
behavior = os.environ.get("FAKE_OPENCODE_BEHAVIOR", "edit_target")
target = os.environ.get("FAKE_OPENCODE_TARGET")
extra = os.environ.get("FAKE_OPENCODE_EXTRA")
docs = os.environ.get("FAKE_OPENCODE_DOCS")
required_templates = os.environ.get("FAKE_OPENCODE_TEMPLATES")
required_skills = os.environ.get("FAKE_OPENCODE_SKILLS")
if required_skills:
    # Same contract for skills: every skill the assignment names must be
    # materialized before the agent runs (multi-skill dispatches, batch B).
    for required in required_skills.split(","):
        if not os.path.exists(required):
            sys.stderr.write("missing skill " + required + "\\n")
            sys.exit(1)
if required_templates:
    # The agent runs AFTER the Runtime materialized the assignment's templates:
    # any missing file is a materialization failure, never a silent absence.
    for required in required_templates.split(","):
        if not os.path.exists(required):
            sys.stderr.write("missing template " + required + "\\n")
            sys.exit(1)
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
if behavior == "jsonl_with_log":
    if target:
        open(target, "a").write("\\nagent edit\\n")
    sys.stdout.write("[Opencode Logger] Plugin initialized!\\n")
    sys.stdout.write(json.dumps({"type": "step_start"}) + "\\n")
    sys.stdout.write(json.dumps({"type": "step_finish"}) + "\\n")
    sys.exit(0)
if behavior == "session_stateful":
    # D-39 (#44) session-reuse harness: appends this invocation's argv to the
    # marker file (assert whether --session was passed); a --session request
    # under FAKE_OPENCODE_SESSION_DROP mimics a stale session id ("Error:
    # Session not found", exit 0 — exactly what real opencode prints);
    # otherwise emits a JSON event stream carrying a stable sessionID.
    marker = os.environ.get("FAKE_OPENCODE_SESSION_MARKER")
    if marker:
        with open(marker, "a") as fh:
            fh.write(json.dumps(sys.argv[1:]) + "\\n")
    if "--session" in sys.argv and os.environ.get("FAKE_OPENCODE_SESSION_DROP"):
        sys.stdout.write("Error: Session not found\\n")
        sys.exit(0)
    sid = os.environ.get("FAKE_OPENCODE_SESSION_ID", "ses_fake_stable_0001")
    sys.stdout.write(json.dumps({"type": "step_start", "sessionID": sid}) + "\\n")
    sys.stdout.write(json.dumps({"type": "step_finish", "sessionID": sid}) + "\\n")
    sys.exit(0)
if behavior == "session_prose":
    # Prism review B1 counter-example: a SUCCESSFUL JSON event stream whose
    # agent text mentions the trigger phrases ("session not found", "context
    # window") — content, never a signal. Health checks must not fire.
    marker = os.environ.get("FAKE_OPENCODE_SESSION_MARKER")
    if marker:
        with open(marker, "a") as fh:
            fh.write(json.dumps(sys.argv[1:]) + "\\n")
    sid = os.environ.get("FAKE_OPENCODE_SESSION_ID", "ses_fake_prose_0001")
    sys.stdout.write(json.dumps({"type": "step_start", "sessionID": sid}) + "\\n")
    sys.stdout.write(json.dumps({
        "type": "text",
        "part": {
            "type": "text",
            "sessionID": sid,
            "text": "Reviewing the session-reuse feature: the 'session not found' "
                    "fallback and the context window overflow heuristic are both "
                    "discussed in this very sentence.",
        },
    }) + "\\n")
    sys.exit(0)
if behavior in ("edit_target", "edit_extra") and target:
    open(target, "a").write("\\nagent edit\\n")
if behavior == "edit_extra" and extra:
    open(extra, "a").write("\\nover-reach\\n")
if behavior in ("edit_docs", "edit_docs_partial", "edit_docs_extra") and docs:
    listed = docs.split(",")
    if behavior == "edit_docs_partial":
        listed = listed[:-1]  # skip the last doc: incomplete doc-set
    for path in listed:
        open(path, "a").write("\\nagent edit\\n")
if behavior == "edit_docs_extra" and extra:
    open(extra, "a").write("\\nscratch\\n")
if behavior == "shield_reply" and docs:
    for path in docs.split(","):
        open(path, "a").write("\\n> **Shield:** question about coverage\\n")
    if extra:
        os.makedirs(os.path.dirname(extra), exist_ok=True)
        open(extra, "w").write("# test file\\n")
if behavior == "shield_test" and extra:
    os.makedirs(os.path.dirname(extra), exist_ok=True)
    open(extra, "w").write("# test file\\n")
if behavior == "shield_body_edit" and docs:
    for path in docs.split(","):
        open(path, "a").write("\\nagent edit\\n")
    if extra:
        os.makedirs(os.path.dirname(extra), exist_ok=True)
        open(extra, "w").write("# test file\\n")
if behavior == "shield_pre_dirty_reply" and docs:
    # Agent appends a canonical discussion reply to pre-dirty body content.
    for path in docs.split(","):
        open(path, "a").write("\\n> **Shield:** reply on pre-dirty body\\n")
    if extra:
        os.makedirs(os.path.dirname(extra), exist_ok=True)
        open(extra, "w").write("# test file\\n")
if behavior == "shield_replace_body_plus_reply" and docs:
    # Agent removes/replaces Human body content then adds a discussion reply.
    for path in docs.split(","):
        open(path, "w").write("---\\nsha:\\n---\\n\\n# replaced\\n\\n> **Shield:** reply\\n")
    if extra:
        os.makedirs(os.path.dirname(extra), exist_ok=True)
        open(extra, "w").write("# test file\\n")
if behavior == "shield_asset_edit_plus_doc_edit" and docs:
    # Pre-dirty test asset modified by agent AND invalid doc edit.
    if extra:
        with open(extra, "a") as f:
            f.write("\\nagent asset edit\\n")
    for path in docs.split(","):
        open(path, "a").write("\\nagent body edit\\n")
if behavior == "shield_doc_to_symlink" and docs:
    # Agent replaces a regular assignment doc with a symlink pointing outside.
    target = os.environ.get("FAKE_OPENCODE_SYMLINK_TARGET", "/tmp/evil")
    for path in docs.split(","):
        if os.path.exists(path):
            os.unlink(path)
        os.symlink(target, path)
if behavior == "shield_doc_to_dir_symlink" and docs:
    # Agent replaces a regular assignment doc with a directory symlink.
    target = os.environ.get("FAKE_OPENCODE_SYMLINK_TARGET", "/tmp")
    for path in docs.split(","):
        if os.path.exists(path):
            os.unlink(path)
        os.symlink(target, path)
if behavior == "shield_doc_to_dangling_symlink" and docs:
    # Agent replaces a regular assignment doc with a dangling symlink.
    for path in docs.split(","):
        if os.path.exists(path):
            os.unlink(path)
        os.symlink("/nonexistent/evil/target", path)
if behavior == "quota_error":
    # Simulate LLM quota exceeded mid-stream: stderr carries the provider
    # error, stdout has valid JSON events, but the final text is truncated
    # (not a valid JSON manifest) because the stream was interrupted.
    # The caller must set FAKE_OPENCODE_FINAL_TEXT to a non-JSON string.
    sys.stderr.write(
        "stream error: AI_APICallError: code=4008 "
        "msg=Your requests have exceeded the quota.\\n")
_final_text = os.environ.get("FAKE_OPENCODE_FINAL_TEXT", json.dumps({
    "artifact_manifest": {"include": [{
        "path": "tests/integration/default.py",
        "kind": "integration",
        "role": "required",
    }]},
    "suggested_commit_message": "M-TEST: add tests",
}))
sys.stdout.write(json.dumps({"type": "text", "part": {"text": "progress"}}) + "\\n")
sys.stdout.write(json.dumps({"type": "text", "part": {"text": _final_text}}) + "\\n")
sys.exit(0)
"""


DESIGN_DOCS = ("architecture.md", "interfaces.md", "test-plan.md")


ARCH_WITH_SCAFFOLD = (
    "---\narchitecture_id: ARCH-001\nspec_ref: SPEC-001\ncreated: 1970-01-01\n"
    "status: draft\nsha:\n---\n\n# design\n\n"
    "## 2. Scaffold 宣言\n\n{bullets}\n\n"
    "## 4. 交付与运行合同（machine contracts）\n\n- contract\n"
)


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
    subprocess.run(
        ["git", "commit", "-m", "story skeleton"], cwd=host_repo, check=True, capture_output=True
    )
    return doc


def backend(host_repo):
    return OpencodeBackend(host_repo, "v0.2")


def design_assignment(substate):
    """Mirrors the machine's M-DESIGN assignment: Archer DRAFT/RESPOND carries
    the template trio and the multi-skill list (batch B); Prism's review
    dispatch drafts nothing and carries its own multi-skill list (D-29 design
    criteria pack)."""
    assignment = {
        "kind": substate,
        "template_kind": None,
        "skill": "tracks-discuz",
        "skill_version": "0.2",
        "docs": list(DESIGN_DOCS),
    }
    if substate in ("DRAFT", "RESPOND"):
        assignment["templates"] = ["architecture", "interfaces", "test-plan"]
        assignment["skills"] = ["tracks-discuz", "tracks-quality-guards"]
        assignment.pop("skill")
        assignment.pop("skill_version")
    elif substate == "PRISM_REVIEW":
        assignment["skills"] = ["tracks-discuz", "tracks-prism-design"]
        assignment.pop("skill")
        assignment.pop("skill_version")
    return assignment


@pytest.fixture
def design_vdir(host_repo):
    """The version dir Archer writes the design trio into."""
    vdir = host_repo / ".tracks" / "projects" / "v0.2"
    vdir.mkdir(parents=True)
    return vdir


def commit_trio(host_repo, docs):
    """Committed design trio — the state Prism reviews (the kernel commits all
    three docs before entering PRISM_REVIEW)."""
    for path in docs:
        path.write_text("---\nsha:\n---\n\n# design\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=host_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "design trio"], cwd=host_repo, check=True, capture_output=True
    )


def template_paths(host_repo):
    tdir = host_repo / ".opencode" / "templates"
    return [tdir / f"{kind}.md" for kind in ("architecture", "interfaces", "test-plan")]


def seed_thread(doc, speaker, resolved=False):
    """Give the target doc one discussion thread INITIATED by ``speaker``
    (anchored at the heading line, the repo-wide convention in these tests)."""
    text = writer.start(doc.read_text(encoding="utf-8"), 5, speaker, "Which delivery surface?")
    if resolved:
        thread = parse_threads(text)[0]
        text = writer.set_status(text, thread.thread_id, token_for(thread), "resolved", speaker)
    doc.write_text(text, encoding="utf-8")


def commit_doc(host_repo, name, message="seed discussion thread"):
    subprocess.run(["git", "add", name], cwd=host_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=host_repo, check=True, capture_output=True)


def skill_path(host_repo, name):
    return host_repo / ".opencode" / "skills" / name / "SKILL.md"
