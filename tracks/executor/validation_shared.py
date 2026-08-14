"""Shared scanner primitives for document validation (FR-0170/BS-06/D-28).

Extracted from ``validate.py`` to break the circular import between
``validate.py`` and ``test_tasks.py``: ``test_tasks`` needs ``_acc_scan``,
``_strip_comments``, and ``_HEADING``, while ``validate`` imports the higher-
level test-task contract functions back from ``test_tasks``. Hosting the
genuinely shared low-level scanner helpers here lets both modules import them
without either depending on the other at module-init time.

This module has no dependency on ``validate`` or ``test_tasks`` (only ``re``),
so it can be imported first in any order.
"""

from __future__ import annotations

import re

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


def _strip_comments(text: str) -> str:
    """Drop HTML comment blocks (template guidance / commented-out conditional
    sections must not be read as required content)."""
    return _HTML_COMMENT.sub("", text)


# FR-0170 acceptance grammar: '## FR-XXXX' coverage section + '### AC-FRXXXX-YY'
# item whose embedded ID back-references the spec FR/NFR.
_ACC_SECTION = re.compile(r"^## (N?FR-\d{4})\b")
_ACC_AC = re.compile(r"^### (AC-(N?FR)(\d{4})-\d+)\b")

_HEADING = re.compile(r"^(#+)\s+(.*\S)\s*$")


def _acc_scan(acc_text: str) -> tuple[dict, list]:
    """Acceptance-side scan: ``({section_id: line_no}, [(ac_id, ref_id, line_no,
    section_id)])``. Fenced code and discussion blocks ('>' lines) skipped;
    a non-section level-2 heading closes the open section."""
    sections: dict = {}
    acs: list = []
    fence = False
    current = None
    for i, line in enumerate(acc_text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence or line.lstrip().startswith(">"):
            continue
        if m := _ACC_SECTION.match(line):
            current = m.group(1).upper()
            sections.setdefault(current, i)
        elif m := _ACC_AC.match(line):
            ref = f"{m.group(2)}-{m.group(3)}".upper()
            acs.append((m.group(1), ref, i, current))
        elif (h := _HEADING.match(line)) and len(h.group(1)) == 2:
            current = None
    return sections, acs
