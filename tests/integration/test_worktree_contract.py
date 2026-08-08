"""Three-worktree temporal isolation through IF-IMPL-006."""

import subprocess
from pathlib import Path

import pytest

from tracks.executor.worktree import (
    cleanup_worktree,
    create_devon_worktree,
    create_gate_worktree,
    create_test_authority_worktree,
)


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _worktree_paths(repo):
    output = _git(repo, "worktree", "list", "--porcelain")
    return {
        Path(line.removeprefix("worktree "))
        for line in output.splitlines()
        if line.startswith("worktree ")
    }


@pytest.mark.integration
# AC-FR0070-01@v0.5 TRACKS-TRACE Devon candidate predates Shield bundle
# AC-FR0070-05@v0.5 TRACKS-TRACE three worktrees isolate frozen tests
# AC-FR0100-02@v0.5 TRACKS-TRACE manifest rebase and cleanup
# AC-FR0110-02@v0.5 TRACKS-TRACE gates combine candidate and authority
def test_three_worktree_composition_and_cleanup(host_repo):
    base = _git(host_repo, "rev-parse", "HEAD")
    original = _worktree_paths(host_repo)
    authority = create_test_authority_worktree(str(host_repo), base, "RUN")
    authority_path = (_worktree_paths(host_repo) - original).pop()
    (authority_path / "tests" / "integration").mkdir(parents=True)
    (authority_path / "tests" / "integration" / "test_frozen.py").write_text(
        "from pathlib import Path\n\ndef test_frozen():\n"
        "    assert Path(__file__).is_file()\n",
        encoding="utf-8",
    )
    _git(authority_path, "add", "tests/integration/test_frozen.py")
    _git(authority_path, "commit", "-m", "freeze tests")
    frozen = _git(authority_path, "rev-parse", "HEAD")
    devon = create_devon_worktree(str(host_repo), base, "RUN", "T-001")
    authority_and_original = _worktree_paths(host_repo)
    devon_path = (authority_and_original - original - {authority_path}).pop()
    assert not (devon_path / "tests" / "integration" / "test_frozen.py").exists()
    devon_diff = """diff --git a/tracks/new.py b/tracks/new.py
new file mode 100644
--- /dev/null
+++ b/tracks/new.py
@@ -0,0 +1 @@
+VALUE = 1
"""
    gate = create_gate_worktree(
        str(host_repo), base, frozen, devon_diff, "RUN", "T-001"
    )
    gate_path = (
        _worktree_paths(host_repo) - original - {authority_path, devon_path}
    ).pop()
    assert (gate_path / "tests" / "integration" / "test_frozen.py").is_file()
    assert (gate_path / "tracks" / "new.py").is_file()
    cleanup_worktree(gate)
    cleanup_worktree(devon)
    cleanup_worktree(authority)
    assert _worktree_paths(host_repo) == original
