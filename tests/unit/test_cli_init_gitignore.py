"""trac init host-root .gitignore management (runtime byproducts).

B59 follow-up (2026-08-27): the runtime's pytest collect/run commands
materialize __pycache__/ and .pytest_cache/ in the host tree; init manages
a marked ignore block at the host root so every git-status-based gate
(B59 freeze, quarantine, drift) stops seeing them. Same idempotent
scaffolding doctrine as the .tracks-level gitignores.
"""

from __future__ import annotations

from tests.unit.helpers import git, git_repo
from tracks.cli.main import HOST_BYPRODUCTS_MARKER, cmd_init


def test_init_appends_managed_block_to_existing_host_gitignore(tmp_path):
    """A host that already carries its own .gitignore keeps it verbatim; the
    managed block is appended after it, and the result is committed (so
    `trac start`'s clean-tree gate sees a committed host)."""
    repo = git_repo(tmp_path)
    (repo / ".gitignore").write_text(".venv\n", encoding="utf-8")

    assert cmd_init(repo) == 0

    text = (repo / ".gitignore").read_text(encoding="utf-8")
    assert text.startswith(".venv\n")
    assert text.index(".venv") < text.index(HOST_BYPRODUCTS_MARKER)
    assert "__pycache__/" in text and "*.py[cod]" in text
    assert ".pytest_cache/" in text
    assert not git(repo, "status", "--porcelain").stdout.strip()


def test_init_managed_block_is_idempotent(tmp_path):
    """Re-running init never duplicates the block and never rewrites the
    file (byte-identical after the second call)."""
    repo = git_repo(tmp_path)
    cmd_init(repo)
    first = (repo / ".gitignore").read_text(encoding="utf-8")

    cmd_init(repo)

    assert (repo / ".gitignore").read_text(encoding="utf-8") == first
    assert first.count(HOST_BYPRODUCTS_MARKER) == 1


def test_init_creates_gitignore_with_block_when_absent(tmp_path):
    """A host without any .gitignore gets the managed block alone."""
    repo = git_repo(tmp_path)
    assert not (repo / ".gitignore").exists()

    cmd_init(repo)

    text = (repo / ".gitignore").read_text(encoding="utf-8")
    assert text.strip().startswith(HOST_BYPRODUCTS_MARKER)
    assert not git(repo, "status", "--porcelain").stdout.strip()
