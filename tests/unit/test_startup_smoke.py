"""#87：trac run 启动烟测单元测试。

T-003 半成品增量（IndentationError + 跨模块改名不一致）物化到主树后，trac
本体 ImportError 无法启动，操作者只能手工 git checkout 恢复。合同：cmd_run
进入 loop 前对全部 tracks/**/*.py（含在飞未提交）做 ast.parse 语法级烟测，
坏树立即清晰停车（banner + loop.aborted 审计事件），不启动 runtime。
"""

import subprocess
from pathlib import Path

from tracks.cli.main import _startup_smoke_error, cmd_run
from tracks.executor.executor import Executor
from tracks.store import Store

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_BROKEN_CONTENT = """\
def foo():
    x = 1
        y = 2  # IndentationError
"""


def _repo(tmp_path: Path, with_git: bool = False, user: bool = False) -> Path:
    repo = tmp_path / "host"
    repo.mkdir()
    if with_git:
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    if user:
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    return repo


def _with_tracks_pkg(repo: Path, broken: str | None = None) -> Path:
    pkg = repo / "tracks"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "good.py").write_text("X = 1\n", encoding="utf-8")
    if broken is not None:
        (pkg / "broken.py").write_text(broken, encoding="utf-8")
    return repo


def _active_run(repo: Path, run_id: str = "RUN-SMOKE") -> Store:
    home = repo / ".tracks"
    home.mkdir(exist_ok=True)
    store = Store(home)
    store.append(run_id, "v0.1", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.1", "stage.entered", {"stage": "M-STORY"})
    return store


# ---------------------------------------------------------------------------
# _startup_smoke_error 纯函数
# ---------------------------------------------------------------------------


def test_clean_tree_returns_none(tmp_path):
    repo = _with_tracks_pkg(_repo(tmp_path))
    assert _startup_smoke_error(repo) is None


def test_no_tracks_returns_none(tmp_path):
    assert _startup_smoke_error(_repo(tmp_path)) is None


def test_broken_untracked_file_detected(tmp_path):
    """在飞未提交的 .py 文件语法错误 → 被检测到。"""
    repo = _with_tracks_pkg(_repo(tmp_path), broken=_BROKEN_CONTENT)
    result = _startup_smoke_error(repo)
    assert result is not None
    summary, files = result
    assert "tracks/broken.py" in summary
    assert any("tracks/broken.py" in f for f in files)


def test_broken_tracked_file_detected(tmp_path):
    """tracked 文件工作树被改坏（#87 现场）→ 被检测到。"""
    repo = _with_tracks_pkg(_repo(tmp_path, with_git=True, user=True))
    (repo / "tracks" / "broken.py").write_text("X = 0\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracks/"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)
    (repo / "tracks" / "broken.py").write_text(_BROKEN_CONTENT, encoding="utf-8")
    result = _startup_smoke_error(repo)
    assert result is not None
    summary, files = result
    assert "tracks/broken.py" in summary
    assert any("tracks/broken.py" in f for f in files)


def test_broken_file_listed_in_files(tmp_path):
    """broken_files 列表元素是相对路径描述（含错误行号）。"""
    repo = _with_tracks_pkg(_repo(tmp_path), broken=_BROKEN_CONTENT)
    _, files = _startup_smoke_error(repo)
    assert len(files) >= 1
    entry = files[0]
    assert "tracks/broken.py" in entry
    assert "IndentationError" in entry or "indent" in entry.lower()
    assert "line " in entry


def test_good_tracked_files_not_blocked(tmp_path):
    """已跟踪的正常文件不触发烟测。"""
    repo = _with_tracks_pkg(_repo(tmp_path, with_git=True))
    assert _startup_smoke_error(repo) is None


# ---------------------------------------------------------------------------
# cmd_run 停车路径
# ---------------------------------------------------------------------------


def test_cmd_run_parks_on_broken_tree(tmp_path):
    """坏树：cmd_run 返回 1，r stderr banner 含损件文件名。"""
    repo = _with_tracks_pkg(_repo(tmp_path, with_git=True), broken=_BROKEN_CONTENT)
    _active_run(repo)
    rc = cmd_run(repo)
    assert rc == 1


def test_cmd_run_prints_banner_on_broken_tree(tmp_path, capsys):
    repo = _with_tracks_pkg(_repo(tmp_path, with_git=True), broken=_BROKEN_CONTENT)
    _active_run(repo)
    cmd_run(repo)
    err = capsys.readouterr().err
    assert "trac run 拒绝启动" in err
    assert "tracks/broken.py" in err
    assert "git checkout HEAD" in err
    assert "GREEN_GATE" in err


def test_cmd_run_appends_loop_aborted_on_broken_tree(tmp_path):
    """坏树：loop.aborted 事件落盘，reason=startup_smoke。"""
    repo = _with_tracks_pkg(_repo(tmp_path, with_git=True), broken=_BROKEN_CONTENT)
    store = _active_run(repo)
    cmd_run(repo)
    events = list(store.events("RUN-SMOKE"))
    aborted = [e for e in events if e.type == "loop.aborted"]
    assert len(aborted) == 1
    ev = aborted[-1]
    assert ev.payload["reason"] == "startup_smoke"
    assert "tracks/broken.py" in ev.payload["detail"]
    assert any("tracks/broken.py" in f for f in ev.payload["files"])


def test_cmd_run_good_tree_proceeds(tmp_path, monkeypatch):
    """好树：cmd_run 返回 0，不落 loop.aborted。"""
    repo = _with_tracks_pkg(_repo(tmp_path, with_git=True))
    store = _active_run(repo)
    # 让 loop 立即返回（不实际派发）
    monkeypatch.setattr(Executor, "run_loop", lambda self: store.state(self.run_id))
    rc = cmd_run(repo)
    assert rc == 0
    events = list(store.events("RUN-SMOKE"))
    assert not any(e.type == "loop.aborted" for e in events)


# ---------------------------------------------------------------------------
# 红绿
# ---------------------------------------------------------------------------


def test_red_green_smoke_is_the_gate(tmp_path, monkeypatch):
    """红绿：注释掉烟测调用（mock 返回 None）→ 坏树不被拦截。"""
    repo = _with_tracks_pkg(_repo(tmp_path, with_git=True), broken=_BROKEN_CONTENT)
    store = _active_run(repo)
    monkeypatch.setattr("tracks.cli.run_cmd._startup_smoke_error", lambda repo: None)
    monkeypatch.setattr(Executor, "run_loop", lambda self: store.state(self.run_id))
    rc = cmd_run(repo)
    assert rc == 0


# ---------------------------------------------------------------------------
# review follow-ups: unreadable/binary files fail CLOSED (#87 review (f))
# ---------------------------------------------------------------------------


def test_startup_smoke_binary_py_fails_closed(tmp_path):
    """A binary/damaged .py (UnicodeDecodeError, not SyntaxError) must park
    the loop, not crash the CLI."""
    repo = _repo(tmp_path, with_git=True)
    (repo / "tracks").mkdir()
    (repo / "tracks" / "binary.py").write_bytes(b"\xff\xfe\x00binary")
    err = _startup_smoke_error(repo)
    assert err is not None
    _, broken = err
    assert any("binary.py" in b and "cannot read" in b for b in broken)


def test_startup_smoke_permission_denied_fails_closed(tmp_path):
    """An unreadable (permission) .py must park the loop, not crash."""
    repo = _repo(tmp_path, with_git=True)
    (repo / "tracks").mkdir()
    target = repo / "tracks" / "locked.py"
    target.write_text("x = 1\n", encoding="utf-8")
    target.chmod(0o000)
    try:
        err = _startup_smoke_error(repo)
        assert err is not None
        _, broken = err
        assert any("locked.py" in b and "cannot read" in b for b in broken)
    finally:
        target.chmod(0o644)
