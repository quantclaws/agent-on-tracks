"""RGR git operations (FR-0070/FR-0080/FR-0120/FR-0200, IF-IMPL-004).

Red ref creation (compare-and-set), Green commit creation (parent=B +
trailers), Red classification (reuses v0.4 RedClass closed set), and lineage
proof (ref + trailer + event sequence triple, NOT Git ancestry per R-1).
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass

from tracks.executor.helpers import classify_red

__all__ = [
    "RedRef",
    "GreenCommit",
    "LineageProof",
    "classify_red",
    "create_red_ref",
    "create_green_commit",
    "verify_lineage",
    "red_base_sha",
]


@dataclass(frozen=True)
class RedRef:
    ref: str
    sha: str
    created: bool


@dataclass(frozen=True)
class GreenCommit:
    sha: str
    parent: str
    trailers: dict


@dataclass(frozen=True)
class LineageProof:
    r_before_g: bool
    r_ref_exists: bool
    g_trailers_valid: bool
    event_order_valid: bool


_TRAILER_LINE = re.compile(r"^([A-Za-z0-9-]+):\s+(.*)$")


def _parse_trailers(message: str) -> dict:
    """Parse the trailer block at the end of a commit message exactly."""
    trailers: dict[str, str] = {}
    for line in reversed(message.rstrip("\n").splitlines()):
        match = _TRAILER_LINE.match(line)
        if match is None:
            break
        trailers[match.group(1)] = match.group(2).strip()
    return trailers


def _trailers_match(
    message: str,
    task_id: str,
    attempt: int,
    r_sha: str | None,
    issue_number: int | None,
    ac_refs: list[str] | None,
) -> bool:
    trailers = _parse_trailers(message)
    expected = {"Tracks-Task": task_id, "Tracks-Attempt": str(attempt)}
    if r_sha is not None:
        expected["Tracks-R"] = r_sha
    if issue_number is not None:
        expected["Tracks-Issue"] = str(issue_number)
    if ac_refs is not None:
        expected["Tracks-AC"] = ",".join(ac_refs)
    return all(trailers.get(key) == value for key, value in expected.items())


def create_red_ref(
    repo: str,
    run_id: str,
    task_id: str,
    attempt: int,
    test_diff: str,
    base_sha: str,
) -> RedRef:
    """FR-0070 create private commit R + git ref (compare-and-set).

    ref = refs/trac/rgr/{run}/{task}/{attempt}/red
    First git rev-parse to check if ref exists; if exists ->
    RedRef(created=False), open new attempt.
    If not exists -> git commit-tree (test_diff as tree patch on base_sha) +
    git update-ref.
    R is immutable (BS-06): same attempt retry overwriting R fails
    compare-and-set.
    """
    if not test_diff.strip():
        raise ValueError("cannot create Red ref without a captured test diff")
    ref = f"refs/trac/rgr/{run_id}/{task_id}/{attempt}/red"
    date = _base_commit_date(repo, base_sha)
    r_sha = _commit_diff(
        repo,
        test_diff,
        base_sha,
        _red_message(task_id, attempt),
        date=date,
    )
    existing = _rev_parse(repo, ref)
    if existing is None:
        _git(repo, "update-ref", ref, r_sha)
        return RedRef(ref=ref, sha=r_sha, created=True)
    if existing == r_sha:
        return RedRef(ref=ref, sha=existing, created=False)
    raise RuntimeError(
        f"immutable ref {ref} already points to {existing}, cannot overwrite with {r_sha}"
    )


def red_base_sha(repo: str, r_sha: str) -> str | None:
    """FR-0120 replay-safe base derivation: B = the R commit's parent.

    The formal G commit's parent must be derived from the immutable R commit
    (never blindly from the current HEAD), so a crash after the branch update
    but before ``green.committed`` reconciles to the same G. Returns None when
    the R commit is not resolvable (broken lineage -> fail closed).
    """
    if not isinstance(r_sha, str) or not r_sha.strip():
        return None
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", f"{r_sha}^"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def create_green_commit(
    repo: str,
    run_id: str,
    task_id: str,
    attempt: int,
    impl_diff: str,
    base_sha: str,
    r_sha: str,
    issue_number: int,
    ac_refs: list[str],
) -> GreenCommit:
    """FR-0120 create formal commit G (parent=B + trailers).

    git commit-tree (impl_diff as tree patch on base_sha) with trailers:
      Tracks-Task: {task_id}
      Tracks-Attempt: {attempt}
      Tracks-R: {r_sha}
      Tracks-Issue: {issue_number}
      Tracks-AC: {ac_refs}
    G parent=B (no Git ancestry topology assertion, R is not G's ancestor, R-1).
    G is deterministic like R (author/committer dates inherit the base commit's
    committer date), so a crash after the branch is updated to G but before
    ``green.committed`` is persisted reconciles to the same G commit.
    """
    if not impl_diff.strip():
        raise ValueError("cannot create Green commit without a captured implementation diff")
    trailers = {
        "Tracks-Task": task_id,
        "Tracks-Attempt": str(attempt),
        "Tracks-R": r_sha,
        "Tracks-Issue": str(issue_number),
        "Tracks-AC": ",".join(ac_refs),
    }
    message = _green_message(task_id, attempt, r_sha, issue_number, ac_refs)
    g_sha = _commit_diff(
        repo,
        impl_diff,
        base_sha,
        message,
        date=_base_commit_date(repo, base_sha),
    )
    return GreenCommit(sha=g_sha, parent=base_sha, trailers=trailers)


def verify_lineage(
    repo: str,
    run_id: str,
    task_id: str,
    attempt: int,
    g_sha: str,
    events: list,
    *,
    issue_number: int | None = None,
    ac_refs: list[str] | None = None,
) -> LineageProof:
    """FR-0120 R-before-G lineage proof (pure function + git read-only).

    No Git ancestry topology assertion (R-1): both R and G have B as parent.
    Lineage is jointly proven by ref + trailer + event sequence.  Trailers are
    parsed exactly (never substring key existence): Task/Attempt come from the
    positional args, R from the immutable red ref at
    ``refs/trac/rgr/{run}/{task}/{attempt}/red``, and Issue / combined
    Tracks-AC from the optional expected ``issue_number`` and ``ac_refs``
    (backward-compatible callers may omit the latter two).
    """
    ref = f"refs/trac/rgr/{run_id}/{task_id}/{attempt}/red"
    r_sha = _rev_parse(repo, ref)
    r_ref_exists = r_sha is not None
    message = _git_text(repo, "log", "--format=%B", "-1", g_sha)
    g_trailers_valid = _trailers_match(
        message,
        task_id,
        attempt,
        r_sha,
        issue_number,
        ac_refs,
    )
    event_order_valid = _check_event_order(events, task_id, attempt)
    r_before_g = r_ref_exists and g_trailers_valid and event_order_valid
    return LineageProof(
        r_before_g=r_before_g,
        r_ref_exists=r_ref_exists,
        g_trailers_valid=g_trailers_valid,
        event_order_valid=event_order_valid,
    )


# -- git helpers -------------------------------------------------------------


def _git(repo: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _git_text(repo: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout


def _rev_parse(repo: str, ref: str) -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", ref],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def _base_commit_date(repo: str, base_sha: str) -> str:
    return _git(repo, "log", "-1", "--format=%cI", base_sha)


def _commit_diff(
    repo: str,
    diff_text: str,
    base_sha: str,
    message: str,
    *,
    date: str | None = None,
) -> str:
    tree_sha = _build_tree(repo, diff_text, base_sha)
    env = dict(os.environ)
    if date is not None:
        env.update(
            {
                "GIT_AUTHOR_NAME": "Tracks",
                "GIT_AUTHOR_EMAIL": "tracks@local",
                "GIT_AUTHOR_DATE": date,
                "GIT_COMMITTER_NAME": "Tracks",
                "GIT_COMMITTER_EMAIL": "tracks@local",
                "GIT_COMMITTER_DATE": date,
            }
        )
    proc = subprocess.run(
        ["git", "commit-tree", tree_sha, "-p", base_sha, "-m", message],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _build_tree(repo: str, diff_text: str, base_sha: str) -> str:
    if not diff_text.strip():
        return _git(repo, "rev-parse", f"{base_sha}^{{tree}}")
    index_path = _temp_index(repo, base_sha)
    diff_path = _write_temp(diff_text, ".diff")
    try:
        env = dict(os.environ, GIT_INDEX_FILE=index_path)
        subprocess.run(
            ["git", "apply", "--cached", "--whitespace=nowarn", diff_path],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        return _git_env(repo, env, "write-tree")
    finally:
        _safe_unlink(index_path)
        _safe_unlink(diff_path)


def _temp_index(repo: str, base_sha: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".idx")
    os.close(fd)
    env = dict(os.environ, GIT_INDEX_FILE=path)
    subprocess.run(
        ["git", "read-tree", base_sha],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return path


def _git_env(repo: str, env: dict, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _write_temp(text: str, suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    return path


def _safe_unlink(path: str) -> None:
    with contextlib.suppress(OSError):
        os.unlink(path)


def _red_message(task_id: str, attempt: int) -> str:
    return f"RED: {task_id} attempt {attempt}"


def _green_message(
    task_id: str,
    attempt: int,
    r_sha: str,
    issue_number: int,
    ac_refs: list[str],
) -> str:
    return (
        f"GREEN: {task_id}\n\n"
        f"Tracks-Task: {task_id}\n"
        f"Tracks-Attempt: {attempt}\n"
        f"Tracks-R: {r_sha}\n"
        f"Tracks-Issue: {issue_number}\n"
        f"Tracks-AC: {','.join(ac_refs)}\n"
    )


def _check_event_order(events: list, task_id: str, attempt: int) -> bool:
    red_seq = _event_seq(events, "red.checkpointed", task_id, attempt)
    green_seq = _event_seq(events, "green.committed", task_id, attempt)
    if red_seq is None or green_seq is None:
        return False
    return red_seq < green_seq


def _event_seq(events: list, event_type: str, task_id: str, attempt: int) -> int | None:
    for event in events:
        if event.get("type") != event_type:
            continue
        payload = event.get("payload", {})
        if _event_matches(payload, task_id, attempt):
            return event.get("seq")
    return None


def _event_matches(payload: dict, task_id: str, attempt: int) -> bool:
    tid_ok = payload.get("task_id") is None or payload["task_id"] == task_id
    att_ok = payload.get("attempt") is None or payload["attempt"] == attempt
    return tid_ok and att_ok
