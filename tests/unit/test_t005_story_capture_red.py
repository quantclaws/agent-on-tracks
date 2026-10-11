"""T-005 RED: M-START bootstrap story capture extension (IF-MSTART-001).

Devon-owned unit RED for the T-005 delivery slice (architecture §1.0.6 /
interfaces §1g.2): the run-plane bootstrap in
``tracks/supervisor/worker_main.py`` must honor the ``title`` and
``story_form`` create_run params the develop path carries — the
Scribe-suggested title prefills the story frontmatter, and the capture is
rendered per the input form (raw seed → §1-verbatim skeleton, complete
story → adopted as-is, never wrapped into §1, never rewritten).

The failing nodes pin the §1g.2 contracts this task must deliver:

- ``title`` prefills the story.md frontmatter (AC-FR0339-01: 标题由 Scribe
  确定并写入 frontmatter title) while a raw seed still enters the §1
  blockquote verbatim;
- a machine-classified complete story is adopted as the initial story
  document byte-for-byte — the input's own frontmatter stands, no §1
  wrapping, no placeholder substitution (§1f.4/§1g.2).

M-IMPL RED discipline: today's bootstrap always renders the plain skeleton
(the title placeholder stays for Scribe, no form distinction), so both
nodes fail as plain assertion failures against the documented §1g.2
outcome — no stubs, no assembly errors, nothing mocked: the real git repo,
the real service.db and the real bootstrap call run in-process
(``templating.render_story_capture`` already exists upstream; this task
wires the run-plane capture to it).

AC: FR-0339 — TRACKS-TRACE IF-MSTART-001.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.unit.helpers import git_repo
from tracks import paths, templating
from tracks.frontmatter import split_frontmatter
from tracks.supervisor import worker_main
from tracks.supervisor.db import ServiceDB

_VERSION = "v0.3"
_RUN = "R1"


def _seed_create_run(tmp_path: Path, params: dict) -> tuple[Path, ServiceDB]:
    """A real service plane holding the run's create_run intent row."""
    base = tmp_path / "host"
    base.mkdir(parents=True, exist_ok=True)
    repo = git_repo(base)
    home = tmp_path / "service-home"
    home.mkdir(parents=True, exist_ok=True)
    db = ServiceDB(home)
    db.register_command(
        {
            "command_id": "C-CREATE",
            "kind": "create_run",
            "params_json": json.dumps(params),
            "params_digest": "0" * 64,
            "idempotency_key": "idem-create",
            "actor": "human",
            "actor_class": "human",
            "surface": "http",
            "project_id": "P1",
            "run_id": _RUN,
        }
    )
    return repo, db


def _bootstrapped_story(repo: Path, db: ServiceDB) -> str:
    worker_main._bootstrap_run_plane(db, repo, _RUN)
    story = paths.version_dir(paths.tracks_home(repo), _VERSION) / "story.md"
    assert story.exists(), "fixture: the bootstrap must capture story.md (v0.9 baseline)"
    return story.read_text(encoding="utf-8")


# AC-FR0339-01@v0.11 TRACKS-TRACE IF-MSTART-001 scribe title prefills frontmatter
def test_develop_title_prefills_story_frontmatter(tmp_path: Path):
    repo, db = _seed_create_run(
        tmp_path,
        {
            "version": _VERSION,
            "story": "给工作台加一个导出按钮",
            "title": "工作台导出按钮",
            "story_form": "raw",
        },
    )
    text = _bootstrapped_story(repo, db)
    head, _body = split_frontmatter(text)
    assert "title: 工作台导出按钮" in head, (
        "the Scribe-suggested title of the develop path must prefill the story"
        " frontmatter (§1g.2); today the capture leaves the placeholder for"
        " Scribe instead"
    )
    assert "> 给工作台加一个导出按钮" in text, (
        "a raw seed still enters the skeleton §1 verbatim (§1f.4)"
    )


# AC-FR0339-01@v0.11 TRACKS-TRACE IF-MSTART-001 complete story adopted as-is
def test_develop_complete_story_adopted_verbatim(tmp_path: Path):
    complete = (
        templating.load_template("story")
        .replace("S-NNN", "S-042")
        .replace("{YYYY-MM-DD}", "2026-10-12")
        .replace("{一句话标题}", "既有能力盘点")
    )
    assert templating.classify_story_input(complete) == "complete", (
        "fixture: the sample must be machine-classified complete (§1f.4)"
    )
    repo, db = _seed_create_run(
        tmp_path,
        {
            "version": _VERSION,
            "story": complete,
            "title": "不应改写既有标题的建议标题",
            "story_form": "complete",
        },
    )
    text = _bootstrapped_story(repo, db)
    assert text.rstrip() == complete.rstrip(), (
        "a complete story is adopted as the initial story document as-is — never"
        " wrapped into §1, never rewritten or reordered, and its own frontmatter"
        " title stands over the suggestion (§1g.2)"
    )
