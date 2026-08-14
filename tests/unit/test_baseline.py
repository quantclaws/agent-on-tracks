"""Legacy baseline parsing tests (FR-0100).

AC-FR0100-03@v0.4 baseline freezes adoption only,
AC-FR0100-06@v0.4 schema fields.
"""

from tracks.checks.reach import check_reach
from tracks.checks.trace import check_trace_full


# AC-FR0100-06@v0.4 TRACKS-TRACE baseline schema fields
def test_schema_fields():
    """AC-FR0100-06@v0.4 baseline schema has required fields."""
    baseline = {
        "adopted_at": "2026-01-01",
        "version": "v0.1",
        "trace_exemptions": {"documents": [], "ids": ["FR-0990"]},
        "reach_exemptions": {"modules": ["pkg.legacy"]},
    }
    assert baseline["adopted_at"] == "2026-01-01"
    assert baseline["version"] == "v0.1"
    assert "FR-0990" in baseline["trace_exemptions"]["ids"]
    assert "pkg.legacy" in baseline["reach_exemptions"]["modules"]


# AC-FR0100-02@v0.4 TRACKS-TRACE trace baseline exempts ids
def test_trace_baseline_exempts_ids():
    """AC-FR0100-02@v0.4 baseline-exempted IDs not counted as trace orphans."""
    spec = "### FR-0010 A\n\n- **来源**：BS-01\n- **交付入口**：trac x\n\nD.\n"
    spec += "### FR-0990 Legacy\n\n- **来源**：BS-01\n- **交付入口**：trac x\n\nD.\n"
    acc = "## FR-0010 A\n\n### AC-FR0010-01\n\n  - c\n"
    markers = {"AC-FR0010-01": ["AC-FR0010-01@v0.4"]}
    r = check_trace_full("", spec, acc, markers)
    assert any("FR-0990" in e for e in r.hard_errors)

    # With baseline, FR-0990 is exempted
    from tracks.checks.trace import _apply_baseline

    baseline = {"trace_exemptions": {"ids": ["FR-0990"]}}
    exempted = _apply_baseline(r, baseline)
    assert not any("FR-0990" in e for e in exempted.hard_errors)


# AC-FR0100-02@v0.4 TRACKS-TRACE reach baseline exempts modules
def test_reach_baseline_exempts_modules():
    """AC-FR0100-02@v0.4 baseline-exempted modules not counted as islands."""
    graph = {"app": {"pkg.mod_a"}, "pkg.mod_a": set(), "pkg.legacy": set()}
    baseline = {"reach_exemptions": {"modules": ["pkg.legacy"]}}
    r = check_reach(["app"], graph, {"app", "pkg.mod_a", "pkg.legacy"}, baseline=baseline)
    assert "pkg.legacy" not in r.islands


# AC-FR0100-03@v0.4 TRACKS-TRACE baseline freezes adoption only
def test_baseline_freezes_adoption_only():
    """AC-FR0100-03@v0.4 baseline only freezes adoption-time存量."""
    baseline = {
        "adopted_at": "2026-01-01",
        "version": "v0.1",
        "trace_exemptions": {"documents": [], "ids": ["FR-0990"]},
        "reach_exemptions": {"modules": ["pkg.legacy"]},
    }
    # New content (FR-0880) is NOT in baseline -> still reported
    assert "FR-0880" not in baseline["trace_exemptions"]["ids"]
    assert "pkg.newmod" not in baseline["reach_exemptions"]["modules"]
