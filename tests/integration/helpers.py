"""Shared helpers for integration tests."""
import subprocess
from pathlib import Path

from tracks import paths

_TEST_SUFFIXES = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs",
    ".cs", ".rb", ".php", ".c", ".cc", ".cpp", ".h", ".hpp",
    ".kt", ".swift",
})


def g(repo, *args):
    """Run a git command in *repo*, returning stdout."""
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def make_repo(tmp_path):
    """Create a minimal git repo at tmp_path/host and return the Path."""
    repo = tmp_path / "host"
    repo.mkdir()
    g(repo, "init", "-b", "main")
    g(repo, "config", "user.email", "t@example.com")
    g(repo, "config", "user.name", "T")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    g(repo, "add", "README.md")
    g(repo, "commit", "-m", "initial")
    return repo


def setup_trace_repo(tmp_path: Path, scenario: str = "clean") -> Path:
    """Set up a trace fixture in tmp_path/.tracks/projects/v0.4/."""
    home = paths.tracks_home(tmp_path)
    vdir = paths.version_dir(home, "v0.4")
    vdir.mkdir(parents=True, exist_ok=True)
    fixtures = (
        Path(__file__).resolve().parent.parent
        / "assets" / "trace_fixtures" / scenario
    )
    for name in ("story.md", "spec.md", "acceptance.md"):
        (vdir / name).write_text(
            (fixtures / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(exist_ok=True)
    src_tests = fixtures / "tests"
    if src_tests.exists():
        for tf in sorted(src_tests.rglob("*")):
            if tf.is_file() and tf.suffix in _TEST_SUFFIXES:
                (tests_dir / tf.name).write_text(
                    tf.read_text(encoding="utf-8"), encoding="utf-8"
                )
    return tmp_path


def write_baseline(repo: Path, ids=None, modules=None):
    """Write a legacy-baseline.json in .tracks/."""
    import json
    home = paths.tracks_home(repo)
    home.mkdir(parents=True, exist_ok=True)
    bp = home / "legacy-baseline.json"
    bp.write_text(json.dumps({
        "adopted_at": "2026-01-01",
        "version": "v0.1",
        "trace_exemptions": {"documents": [], "ids": ids or []},
        "reach_exemptions": {"modules": modules or []},
    }), encoding="utf-8")


# -- M-TEST shared helpers (de-duplicate integration/e2e test setup) -----------

def walk_to_m_test(trac):
    """Approve the awaiting-human run and return the run_id; the next
    ``trac run`` enters M-DESIGN -> M-TEST."""
    from tests.e2e.helpers import walk_to_await_human
    run_id = walk_to_await_human(trac)
    assert trac("approve", "--actor", "Aaron").returncode == 0
    return run_id


def m_test_events(evs):
    """Slice events from the first stage.entered(M-TEST) onward."""
    start = next(i for i, e in enumerate(evs) if e["type"] == "stage.entered"
                 and e["payload"]["stage"] == "M-TEST")
    return evs[start:]


def assert_no_human_events_in_m_test(evs):
    """Assert no human.* events appear during M-TEST (BS-05)."""
    m_test_evs = m_test_events(evs)
    human_evs = [e for e in m_test_evs if e["type"].startswith("human.")]
    assert not human_evs


_M_TEST_EVENT_TYPES = (
    "test.collected", "prism.verdict", "red.validated",
    "test.committed", "stage.exited", "run.completed",
)


def assert_sm01_event_sequence(types_seq):
    """Assert the M-TEST SM-01 milestone event types are all present."""
    for evt in _M_TEST_EVENT_TYPES:
        assert evt in types_seq
