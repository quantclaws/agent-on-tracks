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

from tracks import templating
from tracks.discuss import writer
from tracks.discuss.locate import token_for
from tracks.discuss.parser import parse_threads
from tracks.effects.opencode import (
    OpencodeBackend,
    OpencodeError,
    _scaffold_declared_paths,
    _skill_names,
)

STANDIN = '''#!/usr/bin/env python3
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

# Minimal design-doc frontmatter + a Scaffold 宣言 section whose bullets the
# parameterized test injects (batch B audit parses `- path —` bullets).
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
    """Mirrors the machine's M-DESIGN assignment: Archer DRAFT/RESPOND carries
    the template trio and the multi-skill list (batch B); Prism's review
    dispatch drafts nothing and keeps the single-skill shape."""
    assignment = {"kind": substate, "template_kind": None,
                  "skill": "tracks-discuz", "skill_version": "0.2",
                  "docs": list(DESIGN_DOCS)}
    if substate in ("DRAFT", "RESPOND"):
        assignment["templates"] = ["architecture", "interfaces", "test-plan"]
        assignment["skills"] = ["tracks-discuz", "tracks-quality-guards"]
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


@pytest.mark.parametrize("substate", ["DRAFT", "RESPOND"])
def test_archer_author_undeclared_scaffold_write_fails(
        substate, fake_opencode, design_vdir, host_repo, monkeypatch):
    """batch B tightens the M-DESIGN author audit (DRAFT and RESPOND alike): an
    in-repo write beside the trio that the freshly-written architecture.md
    Scaffold 宣言 does not enumerate is a classified failure (never silent),
    and the write is rolled back. Single-doc stages keep repo-root trust (see
    the Scribe test)."""
    docs = [design_vdir / name for name in DESIGN_DOCS]
    (design_vdir / "architecture.md").write_text(
        ARCH_WITH_SCAFFOLD.format(bullets="- pyproject.toml — build config"),
        encoding="utf-8")
    scratch = host_repo / "agent_scratch.tmp"
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_docs_extra")
    monkeypatch.setenv("FAKE_OPENCODE_DOCS", ",".join(map(str, docs)))
    monkeypatch.setenv("FAKE_OPENCODE_EXTRA", str(scratch))
    out = backend(host_repo).act("archer", substate, None, None,
                                 assignment=design_assignment(substate))
    assert out["status"] == "failed"
    assert out["failure_class"] == "undeclared_scaffold"
    assert "agent_scratch.tmp" in out["self_report"]
    assert out["audit_evidence"] == "undeclared_scaffold: agent_scratch.tmp"
    assert not scratch.exists()  # the undeclared write is rolled back
    assert "architecture.md" in out["diff_ref"]  # the product was captured


def test_archer_draft_undeclared_write_inside_new_directory_fails(
        fake_opencode, design_vdir, host_repo, monkeypatch):
    """File granularity (not directory entries) is judged: an undeclared file
    inside the same fully-untracked directory tree as the doc-set must not hide
    behind the collapsed `dir/` status entry."""
    docs = [design_vdir / name for name in DESIGN_DOCS]
    (design_vdir / "architecture.md").write_text(
        ARCH_WITH_SCAFFOLD.format(bullets="- pyproject.toml — build config"),
        encoding="utf-8")
    evil = design_vdir / "evil.txt"  # sibling of the docs, undeclared
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_docs_extra")
    monkeypatch.setenv("FAKE_OPENCODE_DOCS", ",".join(map(str, docs)))
    monkeypatch.setenv("FAKE_OPENCODE_EXTRA", str(evil))
    out = backend(host_repo).act("archer", "DRAFT", None, None,
                                 assignment=design_assignment("DRAFT"))
    assert out["status"] == "failed"
    assert out["failure_class"] == "undeclared_scaffold"
    assert ".tracks/projects/v0.2/evil.txt" in out["self_report"]
    assert not evil.exists()  # rolled back


def test_archer_draft_declared_scaffold_write_passes(
        fake_opencode, design_vdir, host_repo, monkeypatch):
    """The manifest is the contract, not a ban: a write enumerated as a
    `- path —` bullet of the freshly-written Scaffold 宣言 is accepted."""
    docs = [design_vdir / name for name in DESIGN_DOCS]
    (design_vdir / "architecture.md").write_text(
        ARCH_WITH_SCAFFOLD.format(
            bullets="- pyproject.toml — build config\n"
                    "- agent_scratch.tmp — declared scaffold file"),
        encoding="utf-8")
    scratch = host_repo / "agent_scratch.tmp"
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_docs_extra")
    monkeypatch.setenv("FAKE_OPENCODE_DOCS", ",".join(map(str, docs)))
    monkeypatch.setenv("FAKE_OPENCODE_EXTRA", str(scratch))
    out = backend(host_repo).act("archer", "DRAFT", None, None,
                                 assignment=design_assignment("DRAFT"))
    assert out["status"] == "done"
    assert out.get("failure_class") is None
    assert scratch.exists()  # the declared scaffold file survives


def test_archer_draft_ground_truth_write_exempt_without_declaration(
        fake_opencode, design_vdir, host_repo, monkeypatch):
    """tests/ground_truth/** is the standing exception (b): ground truth must
    exist and be fully implemented, so its writes pass undeclared."""
    docs = [design_vdir / name for name in DESIGN_DOCS]
    (design_vdir / "architecture.md").write_text(
        ARCH_WITH_SCAFFOLD.format(bullets="- pyproject.toml — build config"),
        encoding="utf-8")
    gt_dir = host_repo / "tests" / "ground_truth"
    gt_dir.mkdir(parents=True)
    ground_truth = gt_dir / "reference.py"
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_docs_extra")
    monkeypatch.setenv("FAKE_OPENCODE_DOCS", ",".join(map(str, docs)))
    monkeypatch.setenv("FAKE_OPENCODE_EXTRA", str(ground_truth))
    out = backend(host_repo).act("archer", "DRAFT", None, None,
                                 assignment=design_assignment("DRAFT"))
    assert out["status"] == "done"
    assert out.get("failure_class") is None
    assert ground_truth.exists()


def test_prism_review_write_audit_stays_repo_root_trusted(
        fake_opencode, design_vdir, host_repo, monkeypatch):
    """The scaffold audit binds AUTHOR dispatches only: a reviewer dispatch
    over the same multi-doc set keeps the plain repo-root trust — an extra
    in-repo write is accepted, never undeclared_scaffold."""
    docs = [design_vdir / name for name in DESIGN_DOCS]
    commit_trio(host_repo, docs)
    scratch = host_repo / "agent_scratch.tmp"
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_docs_extra")
    monkeypatch.setenv("FAKE_OPENCODE_DOCS", ",".join(map(str, docs)))
    monkeypatch.setenv("FAKE_OPENCODE_EXTRA", str(scratch))
    out = backend(host_repo).act("prism", "PRISM_REVIEW", None, None,
                                 assignment=design_assignment("PRISM_REVIEW"))
    assert out["status"] == "done"
    assert out.get("failure_class") is None
    assert scratch.exists()


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


# -- template materialization (live run043: a host repo has no tracks/templates/,
#    so document templates travel with the dispatch exactly like the agent
#    definition and skill, ARCH §4c) --------------------------------------------


def template_paths(host_repo):
    tdir = host_repo / ".opencode" / "templates"
    return [tdir / f"{kind}.md"
            for kind in ("architecture", "interfaces", "test-plan")]


def test_archer_design_draft_materializes_and_cleans_templates(
        fake_opencode, design_vdir, host_repo, monkeypatch):
    """One Archer DRAFT materializes all three design templates into the host
    repo (the fake agent refuses to draft with any of them missing) and the
    cleanup removes them after the dispatch."""
    docs = [design_vdir / name for name in DESIGN_DOCS]
    templates = template_paths(host_repo)
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_docs")
    monkeypatch.setenv("FAKE_OPENCODE_DOCS", ",".join(map(str, docs)))
    monkeypatch.setenv("FAKE_OPENCODE_TEMPLATES", ",".join(map(str, templates)))
    out = backend(host_repo).act("archer", "DRAFT", None, None,
                                 assignment=design_assignment("DRAFT"))
    assert out["status"] == "done"  # every template existed during the run
    for path in templates:
        assert not path.exists()  # cleanup removes what the Runtime created


def test_materialized_templates_are_byte_identical_and_cleaned(host_repo):
    """The template materialize/cleanup cycle matches agents/skills: a
    byte-identical copy of each canonical template, removed on cleanup."""
    be = backend(host_repo)
    infos: list = []
    be._materialize_templates(design_assignment("DRAFT"), infos)
    try:
        assert [info["dest"].name for info in infos] == list(DESIGN_DOCS)
        for info in infos:
            canonical = templating.template_path(info["dest"].stem)
            assert info["dest"].read_bytes() == canonical.read_bytes()
    finally:
        for info in infos:
            be._cleanup_materialized(info)
    for info in infos:
        assert not info["dest"].exists()


def test_template_materialize_backs_up_and_restores_human_template(
        fake_opencode, design_vdir, host_repo, monkeypatch):
    """A Human's pre-existing template at the destination is backed up and
    restored, never clobbered — the same contract as agent definitions."""
    tdir = host_repo / ".opencode" / "templates"
    tdir.mkdir(parents=True)
    human = tdir / "interfaces.md"
    human.write_text("HUMAN TEMPLATE\n", encoding="utf-8")
    docs = [design_vdir / name for name in DESIGN_DOCS]
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_docs")
    monkeypatch.setenv("FAKE_OPENCODE_DOCS", ",".join(map(str, docs)))
    out = backend(host_repo).act("archer", "DRAFT", None, None,
                                 assignment=design_assignment("DRAFT"))
    assert out["status"] == "done"
    assert human.read_text(encoding="utf-8") == "HUMAN TEMPLATE\n"
    # the Runtime's own copies are gone; the Human file stays
    assert not (tdir / "architecture.md").exists()
    assert not (tdir / "test-plan.md").exists()


def test_single_doc_stage_materializes_its_template_kind(
        fake_opencode, host_repo, monkeypatch):
    """A single-doc stage keeps the template_kind path: Sage DRAFT gets the
    spec template materialized, the prompt names the location, and cleanup
    removes it afterwards."""
    spec = host_repo / "spec.md"
    spec.write_text("---\nspec_id: SPEC-001\nsha:\n---\n\n# Spec\n",
                    encoding="utf-8")
    subprocess.run(["git", "add", "spec.md"], cwd=host_repo, check=True)
    subprocess.run(["git", "commit", "-m", "spec skeleton"], cwd=host_repo,
                   check=True, capture_output=True)
    template = host_repo / ".opencode" / "templates" / "spec.md"
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_target")
    monkeypatch.setenv("FAKE_OPENCODE_TARGET", str(spec))
    monkeypatch.setenv("FAKE_OPENCODE_TEMPLATES", str(template))
    assignment = {"kind": "DRAFT", "template_kind": "spec",
                  "skill": "tracks-discuz", "skill_version": "0.2"}
    out = backend(host_repo).act("sage", "DRAFT", "spec.md", spec,
                                 assignment=assignment)
    assert out["status"] == "done"  # the template existed during the run
    assert ".opencode/templates/" in out["agent_io"]["prompt"]
    assert not template.exists()


def test_assignment_prompt_names_the_materialized_templates(
        fake_opencode, design_vdir, host_repo, monkeypatch):
    """The rendered prompt tells the agent where the Runtime placed the
    templates and that drafts must conform (frontmatter included)."""
    docs = [design_vdir / name for name in DESIGN_DOCS]
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_docs")
    monkeypatch.setenv("FAKE_OPENCODE_DOCS", ",".join(map(str, docs)))
    out = backend(host_repo).act("archer", "DRAFT", None, None,
                                 assignment=design_assignment("DRAFT"))
    prompt = out["agent_io"]["prompt"]
    assert ".opencode/templates/" in prompt
    for name in DESIGN_DOCS:
        assert name in prompt
    assert "frontmatter" in prompt


def test_missing_canonical_template_fails_loudly(
        fake_opencode, target_doc, host_repo, monkeypatch):
    """A requested kind without a canonical template is a classified dispatch
    failure (parity with a missing canonical prompt), never a silent skip;
    already-materialized templates are still cleaned up."""
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_target")
    monkeypatch.setenv("FAKE_OPENCODE_TARGET", str(target_doc))
    assignment = {"kind": "DRAFT", "templates": ["spec", "no-such-template"],
                  "skill": "tracks-discuz", "skill_version": "0.2"}
    out = backend(host_repo).act("scribe", "DRAFT", "story.md", target_doc,
                                 assignment=assignment)
    assert out["status"] == "failed"
    assert out["failure_class"] == "opencode_missing"
    assert "no-such-template" in out["self_report"]
    assert not (host_repo / ".opencode" / "templates" / "spec.md").exists()


def test_materialize_templates_missing_kind_raises(host_repo):
    infos: list = []
    with pytest.raises(OpencodeError) as excinfo:
        backend(host_repo)._materialize_templates(
            {"templates": ["bogus-kind"]}, infos)
    assert excinfo.value.failure_class == "opencode_missing"
    assert "bogus-kind" in str(excinfo.value)
    assert infos == []  # nothing was registered for cleanup


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


# -- batch B: multi-skill materialization + scaffold manifest audit ------------


def skill_path(host_repo, name):
    return host_repo / ".opencode" / "skills" / name / "SKILL.md"


def test_multi_skill_materialization_and_cleanup_restores(host_repo):
    """A ``skills`` list materializes every named skill (byte-identical to the
    canonical SKILL.md) and cleanup removes each one, like agents/templates."""
    be = backend(host_repo)
    infos: list = []
    be._materialize_skills(design_assignment("DRAFT"), infos)
    try:
        assert sorted(info["dest"].parent.name for info in infos) == [
            "tracks-discuz", "tracks-quality-guards"]
        for info in infos:
            canonical = be._canonical.parent / "skills" / \
                info["dest"].parent.name / "SKILL.md"
            assert info["dest"].read_bytes() == canonical.read_bytes()
    finally:
        for info in infos:
            be._cleanup(info)
    assert all(not info["dest"].exists() for info in infos)


def test_multi_skill_materialization_end_to_end(
        fake_opencode, design_vdir, host_repo, monkeypatch):
    """During the dispatch BOTH skills are present in the host repo's
    .opencode/skills/ (the fake agent refuses to run with any missing), and
    the cleanup removes both afterwards."""
    docs = [design_vdir / name for name in DESIGN_DOCS]
    skills = [skill_path(host_repo, name)
              for name in ("tracks-discuz", "tracks-quality-guards")]
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "edit_docs")
    monkeypatch.setenv("FAKE_OPENCODE_DOCS", ",".join(map(str, docs)))
    monkeypatch.setenv("FAKE_OPENCODE_SKILLS", ",".join(map(str, skills)))
    out = backend(host_repo).act("archer", "DRAFT", None, None,
                                 assignment=design_assignment("DRAFT"))
    assert out["status"] == "done"  # the stand-in saw both skills during run
    assert not skills[0].exists() and not skills[1].exists()  # cleaned up


def test_single_skill_backward_compat(fake_opencode, design_vdir, host_repo,
                                      monkeypatch):
    """The legacy single ``skill`` string keeps working untouched: only that
    skill materializes (no skills list), and cleanup removes it."""
    docs = [design_vdir / name for name in DESIGN_DOCS]
    commit_trio(host_repo, docs)
    assignment = design_assignment("PRISM_REVIEW")
    assert "skills" not in assignment and assignment["skill"] == "tracks-discuz"
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "no_edit")
    monkeypatch.setenv("FAKE_OPENCODE_DOCS", ",".join(map(str, docs)))
    monkeypatch.setenv("FAKE_OPENCODE_SKILLS",
                       str(skill_path(host_repo, "tracks-discuz")))
    out = backend(host_repo).act("prism", "PRISM_REVIEW", None, None,
                                 assignment=assignment)
    assert out["status"] == "done"
    assert not skill_path(host_repo, "tracks-discuz").exists()
    assert not (host_repo / ".opencode" / "skills"
                / "tracks-quality-guards").exists()


def test_skill_names_resolution():
    assert _skill_names({"skills": ["a", "b"]}) == ["a", "b"]
    assert _skill_names({"skill": "a"}) == ["a"]  # legacy single-skill shape
    assert _skill_names({"skills": []}) == []  # explicit list wins over skill
    assert _skill_names({"skills": ["a", None], "skill": "z"}) == ["a"]
    assert _skill_names({}) == [] and _skill_names(None) == []


def test_scaffold_declared_paths_parser():
    text = ARCH_WITH_SCAFFOLD.format(
        bullets="- pyproject.toml — build config\n"
                "- src/pkg/__init__.py — package root (kind: stub)\n"
                "<!-- - hidden.md — comment bullets never declare -->\n")
    assert _scaffold_declared_paths(text) == {"pyproject.toml",
                                              "src/pkg/__init__.py"}
    # a bullet-shaped line in the NEXT level-2 section is not a declaration
    extended = text + "\n## 6. 附录\n\n- later.md — outside the manifest\n"
    assert _scaffold_declared_paths(extended) == {"pyproject.toml",
                                                  "src/pkg/__init__.py"}
    assert _scaffold_declared_paths("# arch\n\n## 1. 模块边界\n") == set()
    # numbered and unnumbered headings both match
    unnumbered = text.replace("## 2. Scaffold 宣言", "## Scaffold 宣言")
    assert _scaffold_declared_paths(unnumbered) == {"pyproject.toml",
                                                    "src/pkg/__init__.py"}


def test_scaffold_declared_paths_strips_backticks():
    # run060 exact format: `- `path` — purpose（kind: X）` - the captured group
    # keeps the surrounding backticks; the parser must strip them so the
    # declared set holds clean repo-relative paths. The separator after the
    # path is an em-dash (U+2014), matching _SCAFFOLD_BULLET's anchor.
    text = ARCH_WITH_SCAFFOLD.format(
        bullets="- `code_stats/__init__.py` — 包初始化（kind: stub）\n"
                "- `pyproject.toml` — 构建配置（kind: config）\n")
    assert _scaffold_declared_paths(text) == {"code_stats/__init__.py",
                                              "pyproject.toml"}


def test_scaffold_declared_paths_plain_bullets_regression():
    # plain bullets without any quoting still parse (regression guard).
    text = ARCH_WITH_SCAFFOLD.format(
        bullets="- pyproject.toml — build config\n"
                "- src/pkg/__init__.py — package root (kind: stub)\n")
    assert _scaffold_declared_paths(text) == {"pyproject.toml",
                                              "src/pkg/__init__.py"}


def test_scaffold_declared_paths_strips_quotes():
    # double-quoted variant strips just like backticks; single quotes too.
    text = ARCH_WITH_SCAFFOLD.format(
        bullets='- "code_stats/cli.py" — CLI entry (kind: stub)\n'
                "- 'code_stats/counter.py' — counter (kind: stub)\n")
    assert _scaffold_declared_paths(text) == {"code_stats/cli.py",
                                              "code_stats/counter.py"}


def test_scaffold_declared_paths_mixed_manifest():
    # a real manifest mixes backticked and plain bullets; the declared set is
    # the union of clean paths.
    text = ARCH_WITH_SCAFFOLD.format(
        bullets="- `code_stats/__init__.py` — 包初始化（kind: stub）\n"
                "- pyproject.toml — 构建配置（kind: config）\n"
                "- `.flake8` — lint 配置（kind: config）\n")
    assert _scaffold_declared_paths(text) == {"code_stats/__init__.py",
                                              "pyproject.toml",
                                              ".flake8"}


def test_scaffold_declared_paths_drops_empty_after_strip():
    # a bullet whose captured token is ONLY backticks/quotes collapses to an
    # empty path after stripping and must be dropped (not added as "").
    text = ARCH_WITH_SCAFFOLD.format(
        bullets="- `` — placeholder bullet with empty token\n"
                "- `code_stats/__init__.py` — real file（kind: stub）\n")
    assert _scaffold_declared_paths(text) == {"code_stats/__init__.py"}
