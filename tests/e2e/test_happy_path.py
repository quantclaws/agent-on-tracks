"""E2E happy path (TP-003 §4/4a): init → start → run/triage/review → M-SPEC
exit → stage.entered(M-ACC). The remaining walk (M-ACC seal, M-REQ-APPROVAL,
boundary completion) lives in test_full_journey.py.

Asserts external observables only: exit codes, stdout, event rows, git state,
file contents. Ends with the NFR-04 drop-and-rebuild check (AC-N04a).
"""
import hashlib
import re
import sqlite3
import subprocess


def git_out(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def parse_frontmatter(path):
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    end = text.index("\n---\n", 4)
    head, body = text[4:end], text[end + 5 :]
    fields = {}
    for line in head.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fields[k.strip()] = v.strip()
    return fields, body


def types(events):
    return [e["type"] for e in events]


def test_happy_path(host_repo, trac, event_log):
    # 1. trac init (AC-01a, AC-01c)
    r = trac("init")
    assert r.returncode == 0, r.stderr
    for sub in ("projects", "runtime", "wiki"):
        assert (host_repo / ".tracks" / sub).is_dir()
    assert git_out(host_repo, "status", "--porcelain") == ""

    # idempotent re-init (AC-01b): no error, no new commit
    head_before = git_out(host_repo, "rev-parse", "HEAD")
    assert trac("init").returncode == 0
    assert git_out(host_repo, "rev-parse", "HEAD") == head_before

    # 2. start (AC-02a, AC-04a, AC-05a, AC-06a)
    r = trac("start", "v0.1", stdin="构建一个事件溯源运行时")
    assert r.returncode == 0, r.stderr
    run_id = re.search(r"run (\S+) started", r.stdout).group(1)
    assert "releases/v0.1" in git_out(host_repo, "branch", "--list", "releases/v0.1")
    assert git_out(host_repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "releases/v0.1"
    story = host_repo / ".tracks" / "projects" / "v0.1" / "story.md"
    fm, body = parse_frontmatter(story)
    assert fm["status"] == "draft" and "构建一个事件溯源运行时" in body
    assert git_out(host_repo, "status", "--porcelain") == ""
    evs = types(event_log(run_id))
    assert "stage.entered" in evs and "stage.exited" in evs
    assert "branch.created" in evs  # FR-04: branch creation is a logged command

    # empty stdin rejected (AC-02b)
    r = trac("start", "v0.2", stdin="")
    assert r.returncode == 1 and ("stdin" in r.stderr or "空" in r.stderr)

    # 3. run → TRIAGE dispatch, clean stop at human gate (AC-07a)
    r = trac("run")
    assert r.returncode == 0, r.stderr
    issued = [e for e in event_log(run_id) if e["type"] == "command.issued"]
    cmd = issued[-1]["payload"]["command"]
    assert cmd["kind"] == "dispatch_agent" and cmd["params"]["role"] == "scribe"

    # wrong-state triage rejected later; correct one first (AC-08a)
    r = trac("triage", "go")
    assert r.returncode == 0, r.stderr
    assert any(
        e["type"] == "human.triage" and e["payload"]["decision"] == "go"
        for e in event_log(run_id)
    )
    # not awaiting triage anymore (AC-08b)
    assert trac("triage", "go").returncode == 1

    # 5. run → DRAFT → SAGE_REVIEW → HUMAN_REVIEW (AC-10a, AC-13a, AC-14a)
    r = trac("run")
    assert r.returncode == 0, r.stderr
    evs = event_log(run_id)
    ts = types(evs)
    assert "story.committed" in ts and "sage.verdict" in ts
    fm, _ = parse_frontmatter(story)
    assert fm["title"]  # Scribe named it (AC-10a)
    assert "awaiting=review" in r.stdout

    # 6-7. review no-comment → EXIT M-STORY, into M-SPEC (AC-15a, AC-17a, AC-18a, AC-21a)
    assert trac("review", "no-comment").returncode == 0
    r = trac("run")
    assert r.returncode == 0, r.stderr
    evs = event_log(run_id)
    assert any(
        e["type"] == "stage.exited" and e["payload"]["stage"] == "M-STORY" for e in evs
    )
    # AC-17a: three-way equality on the final story commit
    fm, body = parse_frontmatter(story)
    body_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    finals = [
        e for e in evs
        if e["type"] == "story.committed" and e["payload"].get("final")
    ]
    assert len(finals) == 1
    assert re.fullmatch(r"[0-9a-f]{64}", fm["sha"])
    assert fm["sha"] == finals[0]["payload"]["story_sha"] == body_sha
    spec = host_repo / ".tracks" / "projects" / "v0.1" / "spec.md"
    assert spec.exists()  # AC-18a
    assert any(
        e["type"] == "stage.entered" and e["payload"]["stage"] == "M-SPEC" for e in evs
    )
    assert any(
        e["type"] == "lex.verdict" and e["payload"]["verdict"] == "pass" for e in evs
    )  # AC-21a

    # 8-9. review no-comment → EXIT M-SPEC, into M-ACC (AC-22a, FR-0160)
    assert trac("review", "no-comment").returncode == 0
    r = trac("run")
    assert r.returncode == 0, r.stderr
    evs = event_log(run_id)
    assert any(
        e["type"] == "stage.exited" and e["payload"]["stage"] == "M-SPEC" for e in evs
    )
    fm, body = parse_frontmatter(spec)
    body_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    finals = [
        e for e in evs
        if e["type"] == "spec.committed" and e["payload"].get("final")
    ]
    assert len(finals) == 1  # never matches the DRAFT commit (R4-02)
    assert re.fullmatch(r"[0-9a-f]{64}", fm["sha"])
    assert fm["sha"] == finals[0]["payload"]["spec_sha"] == body_sha
    assert "- [x] 已决定" in body and "- [ ] 已决定" not in body
    finalized = [e for e in evs if e["type"] == "spec.decisions_finalized"]
    assert len(finalized) == 1 and finalized[0]["payload"]["converted"] >= 1
    finalize_i = evs.index(finalized[0])
    human_i = max(
        i for i, e in enumerate(evs)
        if e["type"] == "human.review" and e["payload"]["action"] == "no_comment"
    )
    final_commit_i = evs.index(finals[0])
    assert human_i < finalize_i < final_commit_i  # Runtime, never Agent, checks the boxes
    assert "seal spec.md sha" in git_out(host_repo, "log", "-5", "--format=%s")
    acceptance = host_repo / ".tracks" / "projects" / "v0.1" / "acceptance.md"
    assert acceptance.exists()  # Sage drafted acceptance in M-ACC
    assert any(
        e["type"] == "stage.entered" and e["payload"]["stage"] == "M-ACC" for e in evs
    )
    assert "awaiting=review" in r.stdout

    # 9b. TP-003 §4a: the happy path stops here — M-SPEC exit semantics changed
    # from run.completed to stage.entered(M-ACC) (SM-03.13); the walk through
    # M-ACC and the M-REQ-APPROVAL boundary lives in test_full_journey.py.

    # AC-30a: every command.issued precedes its result event
    for e in evs:
        if e["type"] == "command.issued":
            continue
        if e["command_id"]:
            issue_seqs = [
                x["seq"] for x in evs
                if x["type"] == "command.issued" and x["command_id"] == e["command_id"]
            ]
            assert issue_seqs and min(issue_seqs) < e["seq"]

    # 10. status reports the active M-ACC run (AC-24a)
    r = trac("status")
    assert r.returncode == 0 and run_id in r.stdout
    assert "stage=M-ACC" in r.stdout and "awaiting=review" in r.stdout

    # 11. replay ≡ status (AC-25a, AC-26a)
    r = trac("replay", run_id)
    assert r.returncode == 0, r.stderr
    assert len([ln for ln in r.stdout.splitlines() if "\t" in ln]) == len(evs)
    assert "stage=M-ACC" in r.stdout and "awaiting=review" in r.stdout
    assert trac("replay", "nonexistent").returncode == 1  # AC-25b

    # NFR-04 (AC-N04a): drop projections, status/replay still fold from events
    db = host_repo / ".tracks" / "runtime" / "tracks.db"
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM runs")
    conn.execute("DELETE FROM backlog")
    conn.commit()
    conn.close()
    r = trac("status")
    assert r.returncode == 0 and "stage=M-ACC" in r.stdout and run_id in r.stdout
    r = trac("replay", run_id)
    assert r.returncode == 0 and "awaiting=review" in r.stdout

    # AC-N05a: no singular `.track` residue
    assert not (host_repo / ".track").exists()
