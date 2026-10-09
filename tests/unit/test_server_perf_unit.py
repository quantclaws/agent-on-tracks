"""#225 perf contracts: watermark-invalidated projection caches, batched git
history reads, and the serve-time cache warmup.

The UI-facing reads must stay in the tens-to-hundreds of ms on a LAN. The
three structural fixes these tests pin:

- ``_run_events`` / ``_state_of`` cache by (repo, run_id) and invalidate on
  the append-only ``MAX(seq)`` watermark, so a replay only happens when the
  run actually grew — and the folded result always reflects new events;
- the git material history reads run as TWO subprocesses (log triple scan +
  one ``cat-file --batch``) and return exactly what the old per-commit
  ``git show`` N+1 returned, missing refs degrading to None;
- ``trac serve`` warms the caches in a background thread so the first
  request does not pay the cold replay.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

from tracks import paths
from tracks.server import projections
from tracks.server.api_query import (
    _commit_trio_digests,
    _git,
    _git_log_triples,
    _git_show_batch,
)
from tracks.store import Store

_SERVICE_SCHEMA = """
CREATE TABLE projects (project_id TEXT PRIMARY KEY, repo_path TEXT UNIQUE,
    version TEXT, registered_by TEXT, registered_at TEXT);
CREATE TABLE commands (command_id TEXT PRIMARY KEY, kind TEXT, params_json TEXT,
    params_digest TEXT, idempotency_key TEXT UNIQUE, actor TEXT,
    actor_class TEXT, surface TEXT, project_id TEXT, run_id TEXT,
    status TEXT, claim_generation INTEGER, result_json TEXT,
    created_at TEXT, updated_at TEXT);
CREATE TABLE service_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT,
    type TEXT, run_id TEXT, payload TEXT);
"""

_VERSION = "v1.0"
_TRIO = ("story.md", "spec.md", "acceptance.md")


def _seed_repo_run(repo: Path, run_id: str, events: list[dict]) -> None:
    store = Store(paths.tracks_home(repo))
    try:
        for event in events:
            store.append(run_id, _VERSION, event["type"], event.get("payload", {}))
    finally:
        store.close()


def _seed_home(home: Path, repo: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(home / "service.db")
    try:
        conn.executescript(_SERVICE_SCHEMA)
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            ("proj-1", str(repo), _VERSION, "tester", "2026-10-09T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "gitrepo"
    repo.mkdir()
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "perf@example.com"),
        ("config", "user.name", "Perf Tester"),
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    vdir = repo / ".tracks" / "projects" / _VERSION
    vdir.mkdir(parents=True)
    for round_ in range(1, 4):
        for name in _TRIO:
            (vdir / name).write_text(
                f"---\nenvelope: tracks-envelope:v2\n---\n\n# round {round_} {name}\n",
                encoding="utf-8",
            )
        subprocess.run(
            ["git", "add", ".tracks"], cwd=repo, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", f"material round {round_}"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
    (repo / "README.md").write_text("noise commit\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "noise outside the version dir"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return repo


# -- watermark-invalidated caches (#225) ---------------------------------------


def test_run_events_cache_reuses_until_the_run_grows(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo_run(
        repo,
        "run-cache",
        [
            {"type": "story.requested", "payload": {"raw_chars": 1}},
            {"type": "stage.entered", "payload": {"stage": "M-IMPL"}},
        ],
    )

    first = projections._run_events(str(repo), "run-cache")
    second = projections._run_events(str(repo), "run-cache")
    assert second is first, "an unchanged watermark must serve the cached list"

    _seed_repo_run(
        repo, "run-cache", [{"type": "stage.exited", "payload": {"stage": "M-IMPL"}}]
    )
    third = projections._run_events(str(repo), "run-cache")
    assert third is not first, "a new event must invalidate the cache"
    assert [event.type for event in third][-1] == "stage.exited", (
        "the invalidated read must include the appended event"
    )


def test_state_cache_reflects_appended_events(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo_run(
        repo,
        "run-state",
        [
            {"type": "story.requested", "payload": {"raw_chars": 1}},
            {"type": "stage.entered", "payload": {"stage": "M-STORY"}},
        ],
    )

    before = projections._state_of(str(repo), "run-state")
    assert before.stage == "M-STORY", "the initial fold derives the first stage"

    _seed_repo_run(
        repo,
        "run-state",
        [{"type": "stage.entered", "payload": {"stage": "M-DESIGN"}}],
    )
    after = projections._state_of(str(repo), "run-state")
    assert after.stage == "M-DESIGN", (
        "the cached state must be re-folded after new events land"
    )


def test_projection_caches_are_bounded(tmp_path: Path):
    cache: dict = {}
    for index in range(projections._CACHE_CAP + 8):
        projections._cache_put(cache, ("repo", f"run-{index}"), (index, None))
    assert len(cache) == projections._CACHE_CAP, "the cache must respect its cap"
    assert ("repo", "run-0") not in cache, "the oldest entries age out first"
    assert ("repo", f"run-{projections._CACHE_CAP + 7}") in cache


# -- batched git history reads (#225) ------------------------------------------


def test_git_show_batch_matches_per_ref_show(tmp_path: Path):
    repo = _git_repo(tmp_path)
    triples = _git_log_triples(repo, f".tracks/projects/{_VERSION}")
    assert triples and all(len(t) == 3 for t in triples)
    assert {"Perf Tester"} <= {author for _, _, author in triples}, (
        "the log scan must carry the commit author through"
    )

    specs = [f"{sha}:.tracks/projects/{_VERSION}/{name}" for sha, _, _ in triples for name in _TRIO]
    batch = _git_show_batch(repo, specs)
    for spec in specs:
        single = _git(repo, "show", spec)
        assert batch[spec] == single, f"batched content must equal git show: {spec}"

    missing = _git_show_batch(repo, ["deadbeefdeadbeef:no/such/file.md"])
    assert missing["deadbeefdeadbeef:no/such/file.md"] is None, (
        "a missing ref degrades to None like the per-ref call did"
    )


def test_commit_trio_digests_match_per_commit_computation(tmp_path: Path):
    repo = _git_repo(tmp_path)
    rel_dir = f".tracks/projects/{_VERSION}"
    triples = _git_log_triples(repo, rel_dir)
    shas = [sha for sha, _, _ in triples]

    batched = _commit_trio_digests(repo, shas, rel_dir)
    for sha in shas:
        texts = [_git(repo, "show", f"{sha}:{rel_dir}/{name}") for name in _TRIO]
        if all(text is not None for text in texts):
            from tracks.server.api_query import _trio_digest

            assert batched[sha] == _trio_digest(texts), (
                f"batched trio digest must equal the per-commit fold: {sha}"
            )


# -- serve-time warmup (#225) ---------------------------------------------------


def test_serve_cache_warmup_populates_the_state_cache(tmp_path: Path):
    from tracks.cli.serve_cmd import _start_cache_warmup

    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo_run(
        repo,
        "run-warm",
        [
            {"type": "story.requested", "payload": {"raw_chars": 1}},
            {"type": "stage.entered", "payload": {"stage": "M-IMPL"}},
        ],
    )
    home = tmp_path / "home"
    _seed_home(home, repo)

    thread = _start_cache_warmup(home)
    thread.join(timeout=30)
    assert not thread.is_alive(), "the warmup thread must finish"
    assert (str(repo), "run-warm") in projections._STATE_CACHE, (
        "startup must fold the registered runs so the first request is warm"
    )
