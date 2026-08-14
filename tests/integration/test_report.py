"""Workflow report integration tests (FR-0220/NFR-0050)."""

import subprocess

from tracks.discuss import writer
from tracks.discuss.locate import token_for
from tracks.discuss.parser import parse_threads
from tracks.report import generate_report
from tracks.store import Store, new_ulid


def test_report_cli_writes_static_files(trac, host_repo, tmp_path):
    store = Store(host_repo / ".tracks")
    run_id = new_ulid()
    store.append(run_id, "v0.2", "story.requested", {"raw_chars": 10})
    store.close()

    output = tmp_path / "report"
    result = trac("report", "--run-id", run_id, "--output", str(output))

    report_md = output / "report.md"
    index_html = output / "index.html"
    assert result.returncode == 0
    assert report_md.is_file()
    assert index_html.is_file()
    assert result.stdout.splitlines() == [
        f"report: {report_md}",
        f"html: {index_html}",
    ]


def test_report_uses_current_git_host_and_escapes_agent_output(host_repo, tmp_path):
    home = host_repo / ".tracks"
    store = Store(home)
    run_id = new_ulid()
    command_id = new_ulid()
    store.append(run_id, "v0.2", "story.requested", {"raw_chars": 10})
    store.append(run_id, "v0.2", "stage.entered", {"stage": "M-STORY"})
    store.append(
        run_id,
        "v0.2",
        "command.issued",
        {
            "command": {
                "kind": "dispatch_agent",
                "params": {"role": "lex", "doc": "story.md", "attempt": 1},
                "command_id": command_id,
            }
        },
        command_id=command_id,
    )
    store.append(
        run_id,
        "v0.2",
        "outcome.received",
        {"role": "lex", "status": "ok", "self_report": "<script>alert(1)</script>"},
        command_id=command_id,
    )
    store.append(
        run_id,
        "v0.2",
        "story.committed",
        {"commit_sha": "deadbeef"},
        command_id=command_id,
    )
    store.close()
    db_before = (home / "runtime" / "tracks.db").read_bytes()

    before = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=host_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    report_md, index_html = generate_report(host_repo, run_id, tmp_path / "report")
    after = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=host_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    markdown = report_md.read_text(encoding="utf-8")
    html = index_html.read_text(encoding="utf-8")
    assert "dispatch_agent" in markdown
    assert "deadbeef" in markdown
    assert "local: git show deadbeef" in markdown
    assert "<script>" in markdown
    payload = (
        'payload=`{"role": "lex", "self_report": "<script>alert(1)</script>", "status": "ok"}`'
    )
    assert payload in markdown
    assert "marked v15.0.7" in html
    assert 'fetch("report.md")' in html
    assert "<article" in html
    assert "data-renderer" not in html
    assert "<script>alert(1)</script>" not in html
    assert before == after
    assert db_before == (home / "runtime" / "tracks.db").read_bytes()


def test_report_marks_unclosed_activity_interrupted(host_repo, tmp_path):
    store = Store(host_repo / ".tracks")
    run_id = new_ulid()
    command_id = new_ulid()
    store.append(
        run_id,
        "v0.2",
        "command.issued",
        {"command": {"kind": "dispatch_agent", "params": {}, "command_id": command_id}},
        command_id=command_id,
    )
    store.close()

    report_md, _ = generate_report(host_repo, run_id, tmp_path / "report")
    assert "status=`interrupted`" in report_md.read_text(encoding="utf-8")


def test_report_hides_ordinary_successful_validate(host_repo, tmp_path):
    store = Store(host_repo / ".tracks")
    run_id = new_ulid()
    command_id = new_ulid()
    store.append(
        run_id,
        "v0.2",
        "command.issued",
        {
            "command": {
                "kind": "validate_document",
                "params": {"doc": "story.md"},
                "command_id": command_id,
            }
        },
        command_id=command_id,
    )
    store.append(
        run_id,
        "v0.2",
        "verdict.passed",
        {"check": "schema"},
        command_id=command_id,
    )
    store.close()

    report_md, _ = generate_report(host_repo, run_id, tmp_path / "report")
    report = report_md.read_text(encoding="utf-8")
    assert "validate_document" not in report


def test_report_expands_agent_blobs_and_discussion_participants(host_repo, tmp_path):
    home = host_repo / ".tracks"
    version_dir = home / "projects" / "v0.2"
    version_dir.mkdir(parents=True)
    story = version_dir / "story.md"
    text = "---\nstatus: draft\n---\n\n# Story\n\nOutput location is undecided.\n"
    text = writer.start(text, 6, "Sage", "Where should the output be shown?")
    thread = parse_threads(text)[0]
    text = writer.reply(text, thread.thread_id, token_for(thread), "Scribe", "Use CLI stdout.")
    thread = parse_threads(text)[0]
    text = writer.set_status(text, thread.thread_id, token_for(thread), "resolved", "Sage")
    story.write_text(text, encoding="utf-8")

    store = Store(home)
    run_id = new_ulid()
    command_id = new_ulid()
    store.append(run_id, "v0.2", "story.requested", {"raw_chars": 10})
    store.append(run_id, "v0.2", "stage.entered", {"stage": "M-STORY"})
    store.append(
        run_id,
        "v0.2",
        "command.issued",
        {
            "command": {
                "kind": "dispatch_agent",
                "params": {
                    "role": "sage",
                    "substate": "SAGE_REVIEW",
                    "doc": "story.md",
                    "stage": "M-STORY",
                    "attempt": 1,
                    "review_round": 1,
                },
                "command_id": command_id,
            }
        },
        command_id=command_id,
    )
    input_ref = store.write_audit_blob(
        {"role": "sage", "substate": "SAGE_REVIEW", "prompt": "review story"}
    )
    output_ref = store.write_audit_blob(
        {
            "stdout": "captured sage stdout",
            "stderr": "captured sage stderr",
            "stdout_bytes": 22,
            "stderr_bytes": 22,
        }
    )
    store.append(
        run_id,
        "v0.2",
        "outcome.received",
        {
            "role": "sage",
            "status": "done",
            "artifact_ref": None,
            "self_report": "reviewed",
            "verdict": "pass",
            "agent_io": {
                "input_ref": input_ref,
                "output_ref": output_ref,
                "audit_completeness": "complete",
            },
        },
        command_id=command_id,
    )
    store.append(
        run_id,
        "v0.2",
        "story.committed",
        {"commit_sha": "deadbeef", "final": False},
        command_id=command_id,
    )
    store.close()

    report_md, _ = generate_report(host_repo, run_id, tmp_path / "report")
    report = report_md.read_text(encoding="utf-8")
    assert "assignment expanded" in report
    assert "captured sage stdout" in report
    assert "captured sage stderr" in report
    assert "initiator=`Sage`" in report
    assert "reply speaker=`Scribe`" in report
    assert "status=`resolved`" in report
    assert "reply_count=`1`" in report
    assert "local: git show deadbeef" in report
