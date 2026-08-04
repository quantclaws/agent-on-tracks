"""check_trace_full pure function tests (FR-0080).

AC-FR0080-01@v0.4 full chain, AC-FR0080-02@v0.4 FR<->AC hard errors,
AC-FR0080-03@v0.4 AC<->test hard errors, AC-FR0080-04@v0.4 BS->FR warning,
AC-FR0080-05@v0.4 no short circuit, AC-FR0080-06@v0.4 short format rejected,
AC-FR0080-07@v0.4 NOT_FOUND no silent fallback, AC-FR0080-08@v0.4 duplicate IDs,
AC-FR0080-09@v0.4 tombstone not orphan, AC-FR0130-04@v0.4 test marker must be long.
"""
from tracks.checks.trace import check_trace_full

STORY = """## 4. 行为种子

### BS-01 First

- 来源: [3.1]

### BS-02 Second

- 来源: [3.1]
"""

SPEC = """### FR-0010 Feature A

- **来源**：BS-01
- **交付入口**：trac check

Desc.

### FR-0020 Feature B

- **来源**：BS-02
- **交付入口**：trac check

Desc.
"""

ACC = """## FR-0010 Feature A

### AC-FR0010-01

  - cond

### AC-FR0010-02

  - cond

## FR-0020 Feature B

### AC-FR0020-01

  - cond
"""


def test_full_chain_pass():
    """AC-FR0080-01@v0.4 full chain bidirectional orphan detection pass."""
    markers = {
        "AC-FR0010-01": ["AC-FR0010-01@v0.4"],
        "AC-FR0010-02": ["AC-FR0010-02@v0.4"],
        "AC-FR0020-01": ["AC-FR0020-01@v0.4"],
    }
    r = check_trace_full(STORY, SPEC, ACC, markers)
    assert r.status == "pass"
    assert r.hard_errors == ()


def test_fr_ac_hard_errors():
    """AC-FR0080-02@v0.4 FR without AC and AC referencing non-existent FR."""
    spec = SPEC + "### FR-0030 Orphan\n\n- **来源**：BS-01\n- **交付入口**：trac x\n\nDesc.\n"
    acc = ACC + "## FR-0990 Ghost\n\n### AC-FR0990-01\n\n  - ghost\n"
    markers = {
        "AC-FR0010-01": ["AC-FR0010-01@v0.4"],
        "AC-FR0010-02": ["AC-FR0010-02@v0.4"],
        "AC-FR0020-01": ["AC-FR0020-01@v0.4"],
        "AC-FR0990-01": ["AC-FR0990-01@v0.4"],
    }
    r = check_trace_full(STORY, spec, acc, markers)
    assert r.status == "fail"
    assert any("FR-0030" in e and "has no AC item" for e in r.hard_errors)
    assert any("AC-FR0990-01" in e and "non-existent FR-0990" for e in r.hard_errors)


def test_ac_test_hard_errors():
    """AC-FR0080-03@v0.4 AC without marker and marker pointing at non-existent AC."""
    markers = {
        "AC-FR0010-01": ["AC-FR0010-01@v0.4"],
        "AC-FR0010-02": ["AC-FR0010-02@v0.4"],
        "AC-FR0020-01": ["AC-FR0020-01@v0.4"],
        "AC-FR0020-99": ["AC-FR0020-99@v0.4"],
    }
    r = check_trace_full(STORY, SPEC, ACC, markers)
    assert r.status == "fail"
    assert any("AC-FR0020-99" in e and "non-existent" for e in r.hard_errors)


def test_bs_fr_warning_only():
    """AC-FR0080-04@v0.4 BS->FR warning does not change exit code."""
    markers = {
        "AC-FR0010-01": ["AC-FR0010-01@v0.4"],
        "AC-FR0010-02": ["AC-FR0010-02@v0.4"],
        "AC-FR0020-01": ["AC-FR0020-01@v0.4"],
    }
    r = check_trace_full(STORY, SPEC, ACC, markers)
    assert r.status == "pass"
    assert len(r.warnings) > 0
    assert all("BS->FR weak link" in w for w in r.warnings)


def test_no_short_circuit():
    """AC-FR0080-05@v0.4 orphan list complete, no short-circuit."""
    spec = SPEC + "### FR-0030 Orphan1\n\n- **来源**：BS-01\n- **交付入口**：trac x\n\nD.\n"
    spec += "### FR-0040 Orphan2\n\n- **来源**：BS-01\n- **交付入口**：trac x\n\nD.\n"
    markers = {
        "AC-FR0010-01": ["AC-FR0010-01@v0.4"],
        "AC-FR0010-02": ["AC-FR0010-02@v0.4"],
        "AC-FR0020-01": ["AC-FR0020-01@v0.4"],
    }
    r = check_trace_full(STORY, spec, ACC, markers)
    assert r.status == "fail"
    assert any("FR-0030" in e for e in r.hard_errors)
    assert any("FR-0040" in e for e in r.hard_errors)


def test_short_format_rejected():
    """AC-FR0080-06@v0.4 short-format marker triggers hard error."""
    markers = {
        "AC-FR0010-01": ["AC-FR0010-01@v0.4"],
        "AC-FR0010-02": ["AC-FR0010-02"],
        "AC-FR0020-01": ["AC-FR0020-01@v0.4"],
    }
    r = check_trace_full(STORY, SPEC, ACC, markers)
    assert r.status == "fail"
    assert any("short format" in e for e in r.hard_errors)


def test_not_found_no_silent_fallback():
    """AC-FR0080-07@v0.4 long-format marker referencing non-existent AC -> NOT_FOUND."""
    markers = {
        "AC-FR0010-01": ["AC-FR0010-01@v0.4"],
        "AC-FR0010-02": ["AC-FR0010-02@v0.4"],
        "AC-FR0020-01": ["AC-FR0020-01@v0.4"],
        "AC-FR0099-01": ["AC-FR0099-01@v0.4"],
    }
    r = check_trace_full(STORY, SPEC, ACC, markers)
    assert r.status == "fail"
    assert any("AC-FR0099-01" in e and "non-existent" for e in r.hard_errors)


def test_duplicate_ids():
    """AC-FR0080-08@v0.4 duplicate FR/AC IDs trigger hard error with both line:N."""
    dup_spec = SPEC + "### FR-0010 Duplicate\n\n- **来源**：BS-01\n- **交付入口**：trac x\n\nD.\n"
    dup_acc = ACC + "### AC-FR0010-01\n\n  - dup\n"
    markers = {
        "AC-FR0010-01": ["AC-FR0010-01@v0.4"],
        "AC-FR0010-02": ["AC-FR0010-02@v0.4"],
        "AC-FR0020-01": ["AC-FR0020-01@v0.4"],
    }
    r = check_trace_full(STORY, dup_spec, dup_acc, markers)
    assert r.status == "fail"
    assert any("duplicate FR-0010" in e for e in r.hard_errors)
    assert any("duplicate AC-FR0010-01" in e for e in r.hard_errors)


def test_tombstone_not_orphan():
    """AC-FR0080-09@v0.4 tombstone IDs not counted as orphans."""
    story = STORY + "<!-- tombstone: BS-99 -->\n"
    spec = SPEC + "<!-- tombstone: FR-0990 -->\n"
    acc = ACC + "<!-- tombstone: AC-FR0990-01 -->\n"
    markers = {
        "AC-FR0010-01": ["AC-FR0010-01@v0.4"],
        "AC-FR0010-02": ["AC-FR0010-02@v0.4"],
        "AC-FR0020-01": ["AC-FR0020-01@v0.4"],
    }
    r = check_trace_full(story, spec, acc, markers)
    assert r.status == "pass"
    assert not any("FR-0990" in e for e in r.hard_errors)
    assert not any("BS-99" in w for w in r.warnings)


def test_test_marker_must_be_long():
    """AC-FR0130-04@v0.4 test marker must use long format (with @version)."""
    markers = {
        "AC-FR0010-01": ["AC-FR0010-01@v0.4"],
        "AC-FR0010-02": ["AC-FR0010-02@v0.4"],
        "AC-FR0020-01": ["AC-FR0020-01"],
    }
    r = check_trace_full(STORY, SPEC, ACC, markers)
    assert r.status == "fail"
    assert any("short format" in e for e in r.hard_errors)


def test_stable_order():
    """AC-NFR0020-02@v0.4 output order is stable and reproducible."""
    markers = {
        "AC-FR0010-01": ["AC-FR0010-01@v0.4"],
        "AC-FR0010-02": ["AC-FR0010-02@v0.4"],
        "AC-FR0020-01": ["AC-FR0020-01@v0.4"],
    }
    r1 = check_trace_full(STORY, SPEC, ACC, markers)
    r2 = check_trace_full(STORY, SPEC, ACC, markers)
    assert r1 == r2
    assert r1.hard_errors == tuple(sorted(r1.hard_errors))
    assert r1.warnings == tuple(sorted(r1.warnings))
