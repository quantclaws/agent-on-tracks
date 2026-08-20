"""D-36 浅版 OOB 提交通道（#43）单元测试：trailer 识别与范围解析。"""

import subprocess
from pathlib import Path

from tracks.effects import oob
from tracks.kernel.events import EVENT_TYPES


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "host"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    return repo


def _commit(repo: Path, name: str, content: str, subject: str, body: str | None = None) -> str:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _git(repo, "add", name)
    if body is not None:
        _git(repo, "commit", "-m", subject, "-m", body)
    else:
        _git(repo, "commit", "-m", subject)
    return _git(repo, "rev-parse", "HEAD").strip()


# -- head_sha -----------------------------------------------------------------


def test_head_sha_returns_full_sha(tmp_path):
    repo = _repo(tmp_path)
    sha = oob.head_sha(repo)
    assert sha is not None and len(sha) == 40


def test_head_sha_none_on_empty_repo(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    assert oob.head_sha(repo) is None


# -- commits_since / trailer 识别 ----------------------------------------------


def test_commits_since_orders_and_flags_trailer_commits(tmp_path):
    repo = _repo(tmp_path)
    base = oob.head_sha(repo)
    _commit(repo, "a.md", "a\n", "plain change")
    _commit(repo, "b.md", "b\n", "operator fix", "Tracks-OOB: mid-run runtime fix")

    commits = oob.commits_since(repo, base)
    assert [c["oob"] for c in commits] == [False, True]
    assert commits[1]["reason"] == "mid-run runtime fix"
    assert commits[1]["subject"] == "operator fix"

    only_oob = oob.oob_commits(repo, base)
    assert [c["sha"] for c in only_oob] == [commits[1]["sha"]]


def test_trailer_variants(tmp_path):
    repo = _repo(tmp_path)
    base = oob.head_sha(repo)
    # 空 reason 不构成声明
    _commit(repo, "a.md", "a\n", "empty trailer", "Tracks-OOB:")
    # 多字节 reason
    _commit(repo, "b.md", "b\n", "中文 reason", "Tracks-OOB: 修复运行时误判")

    commits = oob.commits_since(repo, base)
    assert commits[0]["oob"] is False
    assert commits[1]["oob"] is True
    assert commits[1]["reason"] == "修复运行时误判"


def test_commits_since_empty_or_invalid_range(tmp_path):
    repo = _repo(tmp_path)
    head = oob.head_sha(repo)
    assert oob.commits_since(repo, None) == []
    assert oob.commits_since(repo, "") == []
    assert oob.commits_since(repo, "deadbeef" * 5) == []
    assert oob.commits_since(repo, head) == []


# -- changed_paths -------------------------------------------------------------


def test_changed_paths_lists_commit_files(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "src/x.py", "x\n", "add x")
    _commit(repo, "src/y z.txt", "y\n", "add yz")  # 路径含空格
    sha = oob.head_sha(repo)
    assert oob.changed_paths(repo, sha) == {"src/y z.txt"}


# -- 事件类型注册 ---------------------------------------------------------------


def test_oob_accepted_in_closed_event_set():
    assert "oob.accepted" in EVENT_TYPES
