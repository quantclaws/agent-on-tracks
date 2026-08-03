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

from tracks.discuss import writer
from tracks.discuss.locate import token_for
from tracks.discuss.parser import parse_threads
from tracks.effects.opencode import OpencodeBackend

STANDIN = '''#!/usr/bin/env python3
import os, sys, json, time, signal
behavior = os.environ.get("FAKE_OPENCODE_BEHAVIOR", "edit_target")
target = os.environ.get("FAKE_OPENCODE_TARGET")
extra = os.environ.get("FAKE_OPENCODE_EXTRA")
docs = os.environ.get("FAKE_OPENCODE_DOCS")
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
if behavior in ("edit_docs", "edit_docs_partial", "edit_docs_extra") and docs:
    listed = docs.split(",")
    if behavior == "edit_docs_partial":
        listed = listed[:-1]  # skip the last doc: incomplete doc-set
    for path in listed:
        open(path, "a").write("\\nagent edit\\n")
if behavior == "edit_docs_extra" and extra:
    open(extra, "a").write("\\nscratch\\n")
sys.stdout.write(json.dumps({"type": "result", "status": "done"})); sys.exit(0)
'''

DESIGN_DOCS = ("architecture.md", "interfaces.md", "test-plan.md")


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


def test_triage_does_not_require_target_diff(
        fake_opencode, target_doc, host_repo, monkeypatch):
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "no_edit")
    out = backend(host_repo).act("scribe", "TRIAGE", "story.md", target_doc)
    assert out["status"] == "done"
    assert out["diff_ref"] is None


def test_reviewer_without_questions_passes_without_target_diff(
        fake_opencode, target_doc, host_repo, monkeypatch):
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "no_edit")
    out = backend(host_repo).act("sage", "SAGE_REVIEW", "story.md", target_doc)
    assert out["status"] == "done"
    assert out["verdict"] == "pass"
    assert out["diff_ref"] is None


def test_reviewer_open_discussion_requests_revision(
        fake_opencode, target_doc, host_repo, monkeypatch):
    text = target_doc.read_text(encoding="utf-8")
    target_doc.write_text(writer.start(text, 5, "Sage", "Which output policy?"),
                          encoding="utf-8")
    subprocess.run(["git", "add", "story.md"], cwd=host_repo, check=True)
    subprocess.run(["git", "commit", "-m", "add open discussion"], cwd=host_repo,
                   check=True, capture_output=True)
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "no_edit")
    out = backend(host_repo).act("sage", "SAGE_REVIEW", "story.md", target_doc)
    assert out["status"] == "done"
    assert out["verdict"] == "revise"


def test_in_repo_write_is_allowed_not_over_reach(
        fake_opencode, target_doc, host_repo, monkeypatch):
    """The repo root is trusted (FR-030 sandbox): a run-produced file INSIDE the
    working tree (tmp/lock/agent scratch) is NOT over-reach, it is accepted."""
    scratch = host_repo / "agent_scratch.tmp"
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_extra")
    monkeypatch.setenv("FAKE_OPENCODE_TARGET", str(target_doc))
    monkeypatch.setenv("FAKE_OPENCODE_EXTRA", str(scratch))
    out = backend(host_repo).act("scribe", "DRAFT", "story.md", target_doc)
    assert out["status"] == "done"
    assert "over_reach" not in out.get("failure_class", "")
    # The agent's in-repo scratch file survives (allowed write).
    assert scratch.exists()


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


def test_console_input_is_forwarded_only_when_explicitly_configured(host_repo, monkeypatch):
    """A normal backend call does not invent Human input for the Agent."""
    seen = []

    class Process:
        pid = 123
        returncode = 0

        def communicate(self, input, timeout):
            seen.append(input)
            return "{}", ""

    monkeypatch.setattr(
        "tracks.effects.opencode.subprocess.Popen",
        lambda *args, **kwargs: Process(),
    )
    monkeypatch.delenv("TRAC_AGENT_CONSOLE_INPUT", raising=False)
    backend(host_repo)._run("Sage", "normal trac assignment")
    assert seen == [None]

    monkeypatch.setenv("TRAC_AGENT_CONSOLE_INPUT", "actor=Test-Actor\nanswer=CLI")
    backend(host_repo)._run("Sage", "explicit console assignment")
    assert seen[-1] == "actor=Test-Actor\nanswer=CLI"


def test_unknown_role_returns_provider_unavailable(fake_opencode, target_doc, host_repo):
    out = backend(host_repo).act("unknown_role", "DRAFT", "spec.md", target_doc)
    assert out["status"] == "failed"
    assert out["failure_class"] == "provider_unavailable"


def test_lex_edits_target_like_other_agents(fake_opencode, target_doc, host_repo, monkeypatch):
    """Lex is a real opencode agent (FR-020, Aaron 扩容裁定), same pipeline."""
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_target")
    monkeypatch.setenv("FAKE_OPENCODE_TARGET", str(target_doc))
    out = backend(host_repo).act("lex", "LEX_REVIEW", "spec.md", target_doc)
    assert out["status"] == "done"
    assert "agent edit" in out["diff_ref"]
    # materialized Lex.md is cleaned up after the run
    assert not (host_repo / ".opencode" / "agents" / "Lex.md").exists()


# -- M-DESIGN (Archer/Prism): the multi-doc opencode contract -----------------


def design_assignment(substate):
    return {"kind": substate, "template_kind": None,
            "skill": "tracks-discuz", "skill_version": "0.2",
            "docs": list(DESIGN_DOCS)}


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
    subprocess.run(["git", "add", "."], cwd=host_repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "design trio"], cwd=host_repo,
                   check=True, capture_output=True)


def test_archer_design_draft_accepts_the_doc_trio(
        fake_opencode, design_vdir, host_repo, monkeypatch):
    """A multi-doc M-DESIGN DRAFT is legitimate: ONE Archer dispatch writes all
    three docs under .tracks/projects/{version}/, the diff covers the whole
    doc-set, and the materialized agent + skill are cleaned up."""
    docs = [design_vdir / name for name in DESIGN_DOCS]
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_docs")
    monkeypatch.setenv("FAKE_OPENCODE_DOCS", ",".join(map(str, docs)))
    out = backend(host_repo).act("archer", "DRAFT", None, None,
                                 assignment=design_assignment("DRAFT"))
    assert out["status"] == "done"
    assert all(path.read_text(encoding="utf-8").endswith("\nagent edit\n")
               for path in docs)
    # the authoritative diff spans the entire doc set
    for name in DESIGN_DOCS:
        assert name in out["diff_ref"]
    assert out["artifact_ref"] == str(design_vdir)
    assert not (host_repo / ".opencode" / "agents" / "Archer.md").exists()
    # skill materialization applies to Archer exactly as to Scribe/Sage/Lex
    assert not (host_repo / ".opencode" / "skills" / "tracks-discuz"
                / "SKILL.md").exists()


def test_archer_design_draft_incomplete_trio_fails(
        fake_opencode, design_vdir, host_repo, monkeypatch):
    """The doc-set contract stays strict: a DRAFT that produces only part of
    the trio has no product for the missing doc -> no_target_diff."""
    docs = [design_vdir / name for name in DESIGN_DOCS]
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_docs_partial")
    monkeypatch.setenv("FAKE_OPENCODE_DOCS", ",".join(map(str, docs)))
    out = backend(host_repo).act("archer", "DRAFT", None, None,
                                 assignment=design_assignment("DRAFT"))
    assert out["status"] == "failed"
    assert out["failure_class"] == "no_target_diff"
    assert "test-plan.md" in out["self_report"]  # the missing doc is named


def test_archer_draft_in_repo_scratch_write_stays_allowed(
        fake_opencode, design_vdir, host_repo, monkeypatch):
    """Repo-root trust (FR-030 sandbox) is unchanged for multi-doc DRAFT: an
    in-repo scratch write beside the trio is accepted, never over-reach — the
    doc-set change must not silently tighten or weaken the audit."""
    docs = [design_vdir / name for name in DESIGN_DOCS]
    scratch = host_repo / "agent_scratch.tmp"
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_docs_extra")
    monkeypatch.setenv("FAKE_OPENCODE_DOCS", ",".join(map(str, docs)))
    monkeypatch.setenv("FAKE_OPENCODE_EXTRA", str(scratch))
    out = backend(host_repo).act("archer", "DRAFT", None, None,
                                 assignment=design_assignment("DRAFT"))
    assert out["status"] == "done"
    assert "over_reach" not in (out.get("failure_class") or "")
    assert scratch.exists()  # the agent's in-repo scratch file survives


@pytest.mark.parametrize("role,name,substate", [
    ("archer", "Archer", "DRAFT"),
    ("prism", "Prism", "PRISM_REVIEW"),
])
def test_design_agents_materialize_restore_human_agent(
        role, name, substate, fake_opencode, design_vdir, host_repo, monkeypatch):
    """Materialize/cleanup cycle (ARCH §4c) works identically for Archer and
    Prism: a Human's pre-existing agent definition is restored, never lost."""
    agents_dir = host_repo / ".opencode" / "agents"
    agents_dir.mkdir(parents=True)
    human = agents_dir / f"{name}.md"
    human.write_text("HUMAN AGENT\n", encoding="utf-8")
    docs = [design_vdir / doc for doc in DESIGN_DOCS]
    commit_trio(host_repo, docs)  # committed trio: Prism reviews, Archer revises
    monkeypatch.setenv("FAKE_OPENCODE_DOCS", ",".join(map(str, docs)))
    monkeypatch.setenv(
        "FAKE_OPENCODE_BEHAVIOR",
        "edit_docs" if role == "archer" else "no_edit",
    )
    out = backend(host_repo).act(role, substate, None, None,
                                 assignment=design_assignment(substate))
    assert out["status"] == "done"
    assert human.read_text(encoding="utf-8") == "HUMAN AGENT\n"


def test_prism_review_passes_on_settled_design_trio(
        fake_opencode, design_vdir, host_repo, monkeypatch):
    """Prism is a real reviewer agent: verdict comes from the discussion state
    of the whole doc-set (no open threads -> pass), and Prism.md is cleaned up."""
    docs = [design_vdir / doc for doc in DESIGN_DOCS]
    commit_trio(host_repo, docs)
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "no_edit")
    out = backend(host_repo).act("prism", "PRISM_REVIEW", None, None,
                                 assignment=design_assignment("PRISM_REVIEW"))
    assert out.get("failure_class") is None  # pass without threads stays legal
    assert out["status"] == "done"
    assert out["verdict"] == "pass"
    assert out["diff_ref"] is None
    assert out["discussion_evidence"]["ready"] is True
    assert not (host_repo / ".opencode" / "agents" / "Prism.md").exists()


def test_prism_review_open_thread_in_any_doc_requests_revision(
        fake_opencode, design_vdir, host_repo, monkeypatch):
    """An open discussion thread in ANY doc of the set blocks the verdict —
    the discussion gate spans the whole trio, not a single target doc."""
    docs = [design_vdir / doc for doc in DESIGN_DOCS]
    commit_trio(host_repo, docs)
    blocked = docs[1]
    blocked.write_text(
        writer.start(blocked.read_text(encoding="utf-8"), 5, "Prism",
                     "Which module owns the rollback contract?"),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "no_edit")
    out = backend(host_repo).act("prism", "PRISM_REVIEW", None, None,
                                 assignment=design_assignment("PRISM_REVIEW"))
    assert out["status"] == "done"
    assert out["verdict"] == "revise"
    assert out["discussion_evidence"]["ready"] is False


# -- DRAFT thread-closure audit (live run041: verify closure from program facts,
#    flow.md invariant 6 / arch.md 永不信自述) ----------------------------------


def seed_thread(doc, speaker, resolved=False):
    """Give the target doc one discussion thread INITIATED by ``speaker``
    (anchored at the heading line, the repo-wide convention in these tests)."""
    text = writer.start(doc.read_text(encoding="utf-8"), 5, speaker,
                        "Which delivery surface?")
    if resolved:
        thread = parse_threads(text)[0]
        text = writer.set_status(text, thread.thread_id, token_for(thread),
                                 "resolved", speaker)
    doc.write_text(text, encoding="utf-8")


def commit_doc(host_repo, name, message="seed discussion thread"):
    subprocess.run(["git", "add", name], cwd=host_repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=host_repo, check=True,
                   capture_output=True)


def test_draft_own_thread_left_open_fails(
        fake_opencode, target_doc, host_repo, monkeypatch):
    """run041 family: an author DRAFT that finishes without resolving its own
    discussion thread must fail from the doc's program facts — the diff audit
    alone (the story changed) is not enough; the failure names the thread so
    the retry re-dispatch carries it as evidence."""
    seed_thread(target_doc, "Scribe")
    commit_doc(host_repo, "story.md")
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_target")
    monkeypatch.setenv("FAKE_OPENCODE_TARGET", str(target_doc))
    out = backend(host_repo).act("scribe", "DRAFT", "story.md", target_doc)
    assert out["status"] == "failed"
    assert out["failure_class"] == "unresolved_threads"
    assert "T-001" in out["self_report"]
    assert out["audit_evidence"] == "unresolved_threads: T-001"
    assert "agent edit" in out["diff_ref"]  # the diff product was captured


def test_draft_own_thread_resolved_passes(
        fake_opencode, target_doc, host_repo, monkeypatch):
    """Same dispatch with the author's thread closed: the pass path is
    unchanged (the audit keys on unresolved, not merely self-initiated)."""
    seed_thread(target_doc, "Scribe", resolved=True)
    commit_doc(host_repo, "story.md")
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_target")
    monkeypatch.setenv("FAKE_OPENCODE_TARGET", str(target_doc))
    out = backend(host_repo).act("scribe", "DRAFT", "story.md", target_doc)
    assert out["status"] == "done"
    assert out.get("failure_class") is None
    assert "agent edit" in out["diff_ref"]


def test_reviewer_dispatch_leaving_open_thread_unaffected(
        fake_opencode, target_doc, host_repo, monkeypatch):
    """Reviewer dispatches are EXEMPT: a finding thread legitimately stays
    open for the author (verdict semantics unchanged)."""
    seed_thread(target_doc, "Sage")
    commit_doc(host_repo, "story.md")
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "no_edit")
    out = backend(host_repo).act("sage", "SAGE_REVIEW", "story.md", target_doc)
    assert out["status"] == "done"
    # revise anchored by the reviewer's own finding thread stays legal
    assert out.get("failure_class") is None
    assert out["verdict"] == "revise"  # open thread blocks readiness, as before


def test_respond_dispatch_leaving_open_thread_unaffected(
        fake_opencode, target_doc, host_repo, monkeypatch):
    """RESPOND is EXEMPT: the author's threads legitimately stay open until the
    other party's next round — an open self-initiated thread is not a failure."""
    seed_thread(target_doc, "Scribe")
    commit_doc(host_repo, "story.md")
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_target")
    monkeypatch.setenv("FAKE_OPENCODE_TARGET", str(target_doc))
    out = backend(host_repo).act("scribe", "RESPOND", "story.md", target_doc)
    assert out["status"] == "done"
    assert out.get("failure_class") != "unresolved_threads"


@pytest.mark.parametrize("blocked", DESIGN_DOCS)
def test_design_draft_open_own_thread_in_any_doc_fails(
        blocked, fake_opencode, design_vdir, host_repo, monkeypatch):
    """M-DESIGN trio is doc-set-aware: an Archer-initiated thread left open in
    ANY of the three docs fails the whole dispatch, naming doc and thread id."""
    docs = [design_vdir / name for name in DESIGN_DOCS]
    seeded = design_vdir / blocked
    seeded.write_text("---\nsha:\n---\n\n# design\n", encoding="utf-8")
    seed_thread(seeded, "Archer")
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_docs")
    monkeypatch.setenv("FAKE_OPENCODE_DOCS", ",".join(map(str, docs)))
    out = backend(host_repo).act("archer", "DRAFT", None, None,
                                 assignment=design_assignment("DRAFT"))
    assert out["status"] == "failed"
    assert out["failure_class"] == "unresolved_threads"
    assert f"{blocked}:T-001" in out["self_report"]


# -- reviewer revise audit (live run042: a revise verdict must anchor every
#    blocking finding via `trac discuss start`; a bare revise is classified,
#    never trusted — verdict pass stays legal, authors are exempt) -------------


def test_reviewer_revise_with_own_open_finding_thread_unchanged(
        fake_opencode, design_vdir, host_repo, monkeypatch):
    """A revise anchored by the reviewer's own open finding thread stays the
    legal done/revise outcome — the audit requires anchored findings, not a
    settled doc-set, and keys on unresolved (a resolved thread by the same
    reviewer does not count)."""
    docs = [design_vdir / name for name in DESIGN_DOCS]
    commit_trio(host_repo, docs)
    seed_thread(design_vdir / "architecture.md", "Prism")
    seed_thread(design_vdir / "test-plan.md", "Prism", resolved=True)
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "no_edit")
    out = backend(host_repo).act("prism", "PRISM_REVIEW", None, None,
                                 assignment=design_assignment("PRISM_REVIEW"))
    assert out.get("failure_class") is None  # anchored finding satisfies audit
    assert out["status"] == "done"
    assert out["verdict"] == "revise"


def test_reviewer_revise_without_findings_fails(
        fake_opencode, design_vdir, host_repo, monkeypatch):
    """run042 family: verdict=revise but the reviewer INITIATED zero threads —
    a bare outcome Archer's RESPOND has nothing anchored to answer. Classified
    failure (failed-outcome retry path: attempt consumed, evidence named), and
    no verdict leaks into the outcome for the executor to emit."""
    docs = [design_vdir / name for name in DESIGN_DOCS]
    commit_trio(host_repo, docs)
    seed_thread(design_vdir / "interfaces.md", "Archer")  # open, not Prism's
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "no_edit")
    out = backend(host_repo).act("prism", "PRISM_REVIEW", None, None,
                                 assignment=design_assignment("PRISM_REVIEW"))
    assert out["status"] == "failed"
    assert out["failure_class"] == "revise_without_findings"
    assert "trac discuss start" in out["self_report"]
    assert out["audit_evidence"].startswith("revise_without_findings:")
    assert "Prism" in out["audit_evidence"]
    assert out.get("verdict") is None


def test_single_doc_reviewer_revise_without_findings_fails(
        fake_opencode, target_doc, host_repo, monkeypatch):
    """The audit is doc-set-agnostic: a single-doc reviewer revise whose only
    blocking thread was initiated by the author fails the same way."""
    seed_thread(target_doc, "Scribe")
    commit_doc(host_repo, "story.md")
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "no_edit")
    out = backend(host_repo).act("sage", "SAGE_REVIEW", "story.md", target_doc)
    assert out.get("verdict") is None  # the failed outcome emits no verdict
    assert out["status"] == "failed"
    assert out["failure_class"] == "revise_without_findings"


def test_author_dispatch_unaffected_by_revise_finding_audit(
        fake_opencode, design_vdir, host_repo, monkeypatch):
    """Authors are exempt: an Archer RESPOND whose own thread stays open is
    legal — the finding-anchor audit binds reviewer dispatches only."""
    docs = [design_vdir / name for name in DESIGN_DOCS]
    commit_trio(host_repo, docs)
    seed_thread(design_vdir / "architecture.md", "Archer")
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_docs")
    monkeypatch.setenv("FAKE_OPENCODE_DOCS", ",".join(map(str, docs)))
    out = backend(host_repo).act("archer", "RESPOND", None, None,
                                 assignment=design_assignment("RESPOND"))
    assert out["status"] == "done"
    assert out.get("failure_class") is None
