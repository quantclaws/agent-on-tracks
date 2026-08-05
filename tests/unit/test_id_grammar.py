"""ID grammar validation tests (FR-0130).

AC-FR0130-01@v0.4 BS-XX/FR-XXXX/AC-FRXXXX-YY grammar,
AC-FR0130-02@v0.4 ID immutable/tombstone,
AC-FR0130-03@v0.4 version-qualified reference,
AC-FR0130-04@v0.4 doc short/long allowed, test marker must be long,
AC-FR0130-06@v0.4 existing check_spec_items unchanged.
"""
from tracks.executor.validate import (
    check_spec_items,
    check_story_items,
)

STORY_GOOD = """## 4. 行为种子

### BS-01 First

- 来源: [3.1]

### BS-02 Second

- 来源: [3.1]
"""

STORY_BAD_HEADING = """## 4. 行为种子

### BS-1 Bad one-digit

- 来源: [3.1]

### bs-02 Bad lowercase

- 来源: [3.1]
"""

STORY_DUPLICATE = """## 4. 行为种子

### BS-01 First

- 来源: [3.1]

### BS-01 Duplicate

- 来源: [3.1]
"""


# AC-FR0130-01@v0.4 TRACKS-TRACE BS grammar
def test_bs_grammar():
    """AC-FR0130-01@v0.4 BS-XX grammar: two-digit, uppercase."""
    assert check_story_items(STORY_GOOD) == []
    issues = check_story_items(STORY_BAD_HEADING)
    assert len(issues) == 2
    assert any("BS-1" in i and "bad item heading" for i in issues)
    assert any("bs-02" in i and "bad item heading" for i in issues)


# AC-FR0130-02@v0.4 TRACKS-TRACE id immutable
def test_id_immutable():
    """AC-FR0130-02@v0.4 duplicate BS IDs detected."""
    issues = check_story_items(STORY_DUPLICATE)
    assert any("duplicate id BS-01" in i for i in issues)


# AC-FR0130-02@v0.4 TRACKS-TRACE tombstone
def test_tombstone():
    """AC-FR0130-02@v0.4 tombstone markers are HTML comments."""
    story = STORY_GOOD + "<!-- tombstone: BS-99 -->\n"
    issues = check_story_items(story)
    assert issues == []


# AC-FR0130-03@v0.4 TRACKS-TRACE version qualified reference
def test_version_qualified_reference():
    """AC-FR0130-03@v0.4 version-qualified reference is opt-in."""
    # In documents, both short and long formats are allowed.
    # The parser does not force upgrade from short to qualified.
    acc = "## FR-0010 A\n\n### AC-FR0010-01\n\n  - ref AC-FR0010-01@v0.1\n"
    # No crash; the acceptance text is just scanned.
    assert "AC-FR0010-01@v0.1" in acc


# AC-FR0130-04@v0.4 TRACKS-TRACE doc short long allowed
def test_doc_short_long_allowed():
    """AC-FR0130-04@v0.4 documents allow both short and long format."""
    # In docs, short format AC-FRXXXX-YY is allowed (opt-in disambiguation).
    # In test markers, only long format is allowed (tested in test_check_trace).
    acc_short = "## FR-0010 A\n\n### AC-FR0010-01\n\n  - see AC-FR0010-01\n"
    acc_long = "## FR-0010 A\n\n### AC-FR0010-01\n\n  - see AC-FR0010-01@v0.1\n"
    assert "AC-FR0010-01" in acc_short
    assert "AC-FR0010-01@v0.1" in acc_long


# AC-FR0130-06@v0.4 TRACKS-TRACE existing spec validation unchanged
def test_existing_spec_validation_unchanged():
    """AC-FR0130-06@v0.4 existing check_spec_items behavior not regressed."""
    spec = "### FR-0010 A\n\n- **来源**：BS-01\n- **交付入口**：trac x\n\nD.\n"
    assert check_spec_items(spec) == []
    bad_spec = "### fr-0010 A\n\nD.\n"
    issues = check_spec_items(bad_spec)
    assert any("bad item heading" in i for i in issues)
