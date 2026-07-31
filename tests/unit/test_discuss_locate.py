"""inline-discussion locate (FR-070 4-level degrade + freshness).

Covers AC-FR0060-05 (relocate by content, no wrong hit), AC-FR0060-07, AC-FR0070-04 (not_found),
AC-FR0060-08 (stale on reorder drift), AC-FR0070-01..03 (L0/L1/L2), AC-FR0070-05 (ambiguous).
"""
from tracks.discuss.locate import _levenshtein, locate, token_for
from tracks.discuss.parser import parse_threads


def _token(text, idx=0):
    return token_for(parse_threads(text)[idx])


def test_levenshtein_basic():
    assert _levenshtein("same", "same") == 0
    assert _levenshtein("", "abc") == 3
    assert _levenshtein("kitten", "sitting") == 3


def test_unique_match():
    text = "# H\n\n> **Aaron:** comment"
    r = locate(text, _token(text), "T-001")
    assert r.status == "unique" and r.thread_id == "T-001"


def test_l0_delta_correction():
    # AC-FR0070-01: insert 10 lines above; L0 still hits via delta correction
    orig = "# H\n\n> **Aaron:** comment"
    tok = _token(orig)
    shifted = "\n".join(f"line{i}" for i in range(10)) + "\n# H\n\n> **Aaron:** comment"
    r = locate(shifted, tok, "T-001")
    assert r.status == "unique" and r.thread_id == "T-001"


def test_l1_anchor_tweaked():
    # AC-FR0070-02: anchor edited within threshold -> L1 window hit
    orig = "# Heading\n\n> **Aaron:** comment"
    tweaked = "# HeadinX\n\n> **Aaron:** comment"
    assert locate(tweaked, _token(orig), "T-001").status == "unique"


def test_l2_anchor_rewritten():
    # AC-FR0070-03: anchor fully rewritten -> L2 root-only hit
    orig = "# Heading\n\n> **Aaron:** comment"
    rewritten = "# COMPLETELY DIFFERENT ANCHOR TEXT\n\n> **Aaron:** comment"
    assert locate(rewritten, _token(orig), "T-001").status == "unique"


def test_l3_deleted_not_found():
    # AC-FR0060-07 / AC-FR0070-04: thread deleted -> not_found (no silent hit)
    orig = "# H\n\n> **Aaron:** comment"
    assert locate("# H\n\nplain text only", _token(orig), "T-001").status == "not_found"


def test_ambiguous_duplicate_root():
    # AC-FR0070-05: duplicate speaker/root -> ambiguous + candidate line numbers
    orig = "# X\n\n> **Aaron:** dup"
    dup = "# Y\n\n> **Aaron:** dup\n\n> **Aaron:** dup"
    r = locate(dup, _token(orig), "T-001")
    assert r.status == "ambiguous"
    assert r.candidates == (3, 5)


def test_stale_on_reorder():
    # AC-FR0060-08: a thread inserted before drifts Aaron T-001 -> T-002 -> stale
    orig = "# H\n\n> **Aaron:** comment"
    reordered = "# H\n\n> **Zed:** newfirst\n\n> **Aaron:** comment"
    assert locate(reordered, _token(orig), "T-001").status == "stale"
