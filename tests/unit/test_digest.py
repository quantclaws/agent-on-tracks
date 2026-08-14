"""Revision digest ground truth (FR-0190, design D-01/D-02).

digest = sha256("story:<body-sha>\nspec:<body-sha>\nacc:<body-sha>") over
frontmatter-stripped bodies — sealing a sha never self-invalidates, fixed
labels+order kill concatenation-boundary collisions, no timestamps.
"""

import hashlib

import pytest

from tracks.baseline import baseline_summary, revision_digest
from tracks.frontmatter import doc_body_sha, set_frontmatter_field

_HEAD = "---\nstatus: draft\nsha:\n---\n"


@pytest.fixture
def vdir(tmp_path):
    (tmp_path / "story.md").write_text(_HEAD + "# 故事\n\nS", encoding="utf-8")
    (tmp_path / "spec.md").write_text(
        _HEAD + "# 规格\n\n### FR-0010 甲\n\n### NFR-0020 乙\n", encoding="utf-8"
    )
    (tmp_path / "acceptance.md").write_text(
        _HEAD + "# 验收\n\n## FR-0010 甲\n\n### AC-FR0010-01 可观察\n", encoding="utf-8"
    )
    return tmp_path


def test_digest_ground_truth(vdir):
    joined = "\n".join(
        f"{label}:{doc_body_sha(vdir / doc)}"
        for label, doc in (("story", "story.md"), ("spec", "spec.md"), ("acc", "acceptance.md"))
    )
    assert revision_digest(vdir) == hashlib.sha256(joined.encode()).hexdigest()


def test_frontmatter_seal_does_not_change_digest(vdir):
    # D-01: the EXIT seal writes `sha:` into frontmatter — the digest must not
    # depend on it, or sealing would invalidate the just-approved baseline.
    before = revision_digest(vdir)
    for doc in ("story.md", "spec.md", "acceptance.md"):
        set_frontmatter_field(vdir / doc, "sha", "f" * 64)
    assert revision_digest(vdir) == before


def test_any_body_change_changes_digest(vdir):
    base = revision_digest(vdir)
    seen = {base}
    for doc in ("story.md", "spec.md", "acceptance.md"):
        p = vdir / doc
        original = p.read_text(encoding="utf-8")
        p.write_text(original + "\n变更", encoding="utf-8")
        seen.add(revision_digest(vdir))
        p.write_text(original, encoding="utf-8")
    assert len(seen) == 4  # base + one distinct digest per doc


def test_fixed_labels_prevent_swap_collisions(vdir):
    # fixed label + fixed order: swapping story/spec bodies must not collide
    story, spec = vdir / "story.md", vdir / "spec.md"
    a, b = story.read_text(encoding="utf-8"), spec.read_text(encoding="utf-8")
    before = revision_digest(vdir)
    story.write_text(b, encoding="utf-8")
    spec.write_text(a, encoding="utf-8")
    assert revision_digest(vdir) != before


def test_baseline_summary_counts(vdir):
    summary = baseline_summary(vdir)
    assert "story.md: 故事" in summary
    assert "spec.md: 规格 [2 FR/NFR]" in summary
    assert "acceptance.md: 验收 [1 AC]" in summary
