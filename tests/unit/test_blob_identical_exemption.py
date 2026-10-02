"""#223 (2026-10-02): the GREEN regression gate exempts bit-identical blobs.

Live fire (run 01M3E7SAANXKW1V73W8B8Q3G86, T-005 GREEN, RULING
exoneration): the R file lived only on the side ref; the dispatch base
lacked it; the worktree seed re-imported the UNTOUCHED file; the
base-relative captured diff listed it as a new-file entry and the
regression gate convicted Devon of mutating R-frozen tests it never
touched (twice — the second after the ruling's redeliver_green)."""

from __future__ import annotations

from tracks.executor import m_impl_testops as mod
from tracks.executor.m_impl_testops import MImplTestOpsMixin

_R = "RSHA"
_P = "tests/unit/test_x_red.py"


class _Proc:
    def __init__(self, rc, out):
        self.returncode = rc
        self.stdout = out


class _Git:
    def __init__(self, answers):
        self.answers = answers

    def __call__(self, repo, *argv, check=False):
        return self.answers.get(" ".join(argv), _Proc(1, ""))


class _Exec(MImplTestOpsMixin):
    repo = "/repo"

    def _last_devon_outcome(self):
        return {"changed_paths": []}

    def _validated_diff(self, phase, state):
        return None, f"diff --git a/{_P} b/{_P}"

    def _parse_diff_paths(self, _d):
        return {_P}


def _patch(monkeypatch, ls_out, ho_out):
    answers = {
        f"ls-tree {_R} -- {_P}": _Proc(0, ls_out),
        f"hash-object -- /repo/{_P}": _Proc(0, ho_out),
        f"cat-file -e {_R}:{_P}": _Proc(0, ""),
    }
    monkeypatch.setattr(mod, "git", _Git(answers))


def test_untouched_r_file_exempt(monkeypatch):
    """Worktree blob == R blob (new-file artifact): no regression."""
    _patch(
        monkeypatch,
        f"100644 blob a4401ff\t{_P}\n",
        "a4401ff\n",
    )
    assert _Exec()._green_regression_tests(_R, None) == []


def test_mutated_r_file_still_caught(monkeypatch):
    """Worktree blob != R blob: the true-positive face is unchanged."""
    _patch(
        monkeypatch,
        f"100644 blob a4401ff\t{_P}\n",
        "deadbeef\n",
    )
    assert _Exec()._green_regression_tests(_R, None) == [_P]


def test_unverifiable_comparison_not_exempt(monkeypatch):
    """git failure on either side must NOT silently exempt (fail-open to the
    legacy conviction, never to a pass)."""
    monkeypatch.setattr(
        mod, "git", _Git({f"cat-file -e {_R}:{_P}": _Proc(0, "")})
    )
    assert _Exec()._green_regression_tests(_R, None) == [_P]
