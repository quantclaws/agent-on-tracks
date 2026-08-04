"""Shared helpers for integration tests."""
from pathlib import Path

from tracks import paths


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
        for py in src_tests.rglob("*.py"):
            (tests_dir / py.name).write_text(
                py.read_text(encoding="utf-8"), encoding="utf-8"
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
