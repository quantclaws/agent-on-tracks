"""Reviewer re-check exit rule (IF-REVIEW-002, §1p.2 / ARCH-011 §1.0.13).

The machine-side narrowing through the real review backend seam (the same
``OpencodeBackend.act`` surface the executor dispatches): a content-verified
review whose only unresolved threads were initiated by others must complete
as ``pass`` (no findings-less revision), while the reviewer's own revise
stays anchored in a traceable self-initiated thread.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from tests._support.opencode_backend_fixtures import STANDIN
from tests.integration.helpers import make_repo
from tests.integration.result_checkpoint_support import _valid_spec_text
from tracks.effects.opencode import OpencodeBackend

pytestmark = pytest.mark.integration

# A spec whose only open thread was initiated by another reviewer (@Sage) —
# under the narrowed rule this thread is never a revise basis for Lex.
_OTHER_THREAD_SPEC = _valid_spec_text() + (
    "\n> **Sage:** 范围问题：§1n.4 的边界值请确认。\n"
)

# A spec whose open thread is the reviewer's own finding (@Lex) — the revise
# route stays legal and the anchor stays traceable.
_OWN_THREAD_SPEC = _valid_spec_text() + (
    "\n> **Lex:** 自有发现：边界缺失，请补 §3。\n"
)


@pytest.fixture()
def review_repo(tmp_path: Path, monkeypatch):
    """A host repo with the fake opencode binary first on PATH (L2 stand-in)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "opencode"
    exe.write_text(STANDIN, encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return make_repo(tmp_path)


def _review(review_repo: Path, spec_text: str) -> tuple[dict, Path]:
    """One LEX_REVIEW through the backend seam over the given spec text."""
    vdir = review_repo / ".tracks" / "projects" / "v0.1"
    vdir.mkdir(parents=True, exist_ok=True)
    spec = vdir / "spec.md"
    spec.write_text(spec_text, encoding="utf-8")
    out = OpencodeBackend(review_repo, "v0.1").act(
        "lex", "LEX_REVIEW", "spec.md", spec
    )
    return out, spec


# AC-FR0347-02@v0.11 TRACKS-TRACE pass without self findings; revise anchored
def test_pass_without_self_findings_revise_anchored(review_repo):
    """AC-FR0347-02: a content-verified review whose only unresolved threads
    were initiated by others completes as ``pass`` — no findings-less
    revision fires; a revise from the reviewer's own findings stays legal and
    traceable in the self-initiated anchored thread on the document."""
    from tracks.discuss.parser import parse_threads

    # leg 1: the only pending thread is @Sage's — not the reviewer's own
    out, spec = _review(review_repo, _OTHER_THREAD_SPEC)
    assert out["status"] == "done", (
        f"a non-self pending thread must not fail the review (§1p.2): "
        f"{out.get('self_report')!r}"
    )
    assert out["verdict"] == "pass", (
        f"a non-self pending thread must not flip the verdict to revise "
        f"(§1p.2): {out!r}"
    )

    # leg 2: the reviewer's own revise stays legal and anchored
    out2, spec2 = _review(review_repo, _OWN_THREAD_SPEC)
    assert out2["status"] == "done", (
        f"the reviewer's own revise must complete: {out2.get('self_report')!r}"
    )
    assert out2["verdict"] == "revise", (
        f"an own-findings revise keeps the revise route (§1p.1): {out2!r}"
    )
    threads = parse_threads(spec2.read_text(encoding="utf-8"))
    own = [
        thread
        for thread in threads
        if "Lex" in thread.root_text and thread.status in ("open", "reopen")
    ]
    assert own, (
        f"the revise must stay anchored in the self-initiated thread "
        f"(§1p.1 可追溯): {[t.root_text[:40] for t in threads]!r}"
    )
