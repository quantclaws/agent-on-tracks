"""GitHub Issues effect boundary (FR-0200, D-04~D-06): stand-in failure
injection, attempt escalation, and per-item reconcile breakpoint resume.
"""
import json

from tests.e2e.helpers import walk_to_await_human
from tracks.effects.github import issue_items

_HEAD = "---\nstatus: draft\nsha:\n---\n"


def test_issue_items_split_and_body(tmp_path):
    # D-04: one Issue per FR/NFR — [ID] title, body = item text + AC list + digest
    (tmp_path / "spec.md").write_text(
        _HEAD + "# 规格\n\n## 功能需求\n\n### FR-0010 甲能力\n\n正文甲。\n\n"
        "## 非功能需求\n\n### NFR-0020 乙约束\n\n正文乙。\n", encoding="utf-8")
    (tmp_path / "acceptance.md").write_text(
        _HEAD + "# 验收\n\n## FR-0010 甲能力\n\n### AC-FR0010-01 可观察\n\n"
        "## NFR-0020 乙约束\n\n### AC-NFR0020-01 可度量\n", encoding="utf-8")
    items = issue_items(tmp_path, "d" * 64)
    assert [(i, t) for i, t, _ in items] == [
        ("FR-0010", "[FR-0010] 甲能力"), ("NFR-0020", "[NFR-0020] 乙约束")]
    fr_body = items[0][2]
    assert "正文甲。" in fr_body
    assert "- AC-FR0010-01 可观察" in fr_body
    assert f"baseline digest: {'d' * 64}" in fr_body
    assert "- AC-NFR0020-01 可度量" in items[1][2]


def _approved_gate(trac):
    run_id = walk_to_await_human(trac)
    assert trac("approve", "--actor", "Aaron").returncode == 0
    return run_id


def test_standin_failure_escalates_preserving_progress(host_repo, trac, event_log):
    # NFR-0030 style: item 1 lands, item 2 fails 3 times -> escalation.
    # Progress is never rolled back or rebuilt (D-06).
    run_id = _approved_gate(trac)
    r = trac("run", simulate="github:create=ok|fail")
    assert r.returncode == 0, r.stderr
    assert "awaiting=escalation" in r.stdout
    evs = event_log(run_id)
    created = [e for e in evs if e["type"] == "issue.created"]
    assert [e["payload"]["item_id"] for e in created] == ["FR-0010"]
    fails = [e for e in evs if e["type"] == "outcome.received"
             and e["payload"].get("role") == "github"]
    assert len(fails) == 3
    assert all(e["payload"]["status"] == "failed" for e in fails)
    assert all(e["payload"]["failure_class"] == "network" for e in fails)
    types_ = [e["type"] for e in evs]
    assert "issues.created" not in types_  # no half-written summary
    assert "run.completed" not in types_
    issues = json.loads((host_repo / ".tracks" / "runtime" / "issues.json")
                        .read_text(encoding="utf-8"))
    assert len(issues) == 1  # the created issue survives, nothing duplicated


def test_per_item_resume_never_rebuilds(host_repo, trac, event_log):
    # D-06 breakpoint resume: ok|fail|ok — the retry skips the already-created
    # item and only fills the gap; the summary mapping is complete.
    run_id = _approved_gate(trac)
    r = trac("run", simulate="github:create=ok|fail|ok")
    assert r.returncode == 0, r.stderr
    evs = event_log(run_id)
    created = [e for e in evs if e["type"] == "issue.created"]
    assert sorted(e["payload"]["item_id"] for e in created) == \
        ["FR-0010", "NFR-0010"]
    fail_seq = [e["seq"] for e in evs if e["type"] == "outcome.received"
                and e["payload"].get("status") == "failed"]
    assert len(fail_seq) == 1  # exactly one failure, then resume
    assert created[0]["seq"] < fail_seq[0] < created[1]["seq"]
    summary = [e for e in evs if e["type"] == "issues.created"]
    assert len(summary) == 1
    mapping = summary[0]["payload"]["mapping"]
    assert sorted(mapping) == ["FR-0010", "NFR-0010"]
    assert len(set(mapping.values())) == 2  # distinct issue ids, no rebuild
    issues = json.loads((host_repo / ".tracks" / "runtime" / "issues.json")
                        .read_text(encoding="utf-8"))
    assert sorted(issues) == sorted(mapping.values())
    assert evs[-1]["type"] == "run.completed"
    assert evs[-1]["payload"]["terminal_state"] == "boundary"
