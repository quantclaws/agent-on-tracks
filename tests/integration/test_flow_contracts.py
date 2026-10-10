"""flow.md / dispatch-contract text anchors (IF-MSTART-001, IF-SPLIT-001,
IF-REVIEW-002, IF-WORKBENCH-001, IF-GREENGUARD-001).

The §1.0.12 pinned-text anchors: the v0.11 flow.md revisions (Scribe
evaluation before CREATE_BRANCH, TRIAGE fixed columns, reviewer re-check
clause) must be locatable sentence-by-sentence in the delivered
``.tracks/wiki/flow.md``, plus the arrival-green guard that ARCH-011 §0.1
explicitly inherits the v0.10 workbench rulings (test-plan §8.1).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]

_FLOW_MD = _REPO_ROOT / ".tracks" / "wiki" / "flow.md"
_ARCH_011 = _REPO_ROOT / ".tracks" / "projects" / "v0.11" / "architecture.md"

# §1.0.12 pinned clauses (verbatim authority); the anchors assert locatability
# of the contract sentences in the delivered flow.md sections.
_S3_PINS = (
    "Scribe 评估先于 CREATE_BRANCH",
    "统一入口不推荐 hotfix",
    "不开 run, 不建分支",
    "releases/v{N+1}",
    "Scribe 定标题写入 frontmatter title",
)
_S46_PINS = (
    "重复检测",
    "冲突检测",
    "无发现时栏目显式记「无」",
    "不做运行时拦截或自动 NO-GO",
    "GO 需显式确认已知晓冲突",
)
_S45_PINS = (
    "不构成 revise 依据",
    "内容验证通过即 pass",
    "线程确认留给其发起人",
    "必须开锚定线程",
)


def _flow_text() -> str:
    """The delivered flow.md; the file is the Devon-landed contract asset
    (interfaces §3 #4) — its absence is the contract-symbol gap itself."""
    return _FLOW_MD.read_text(encoding="utf-8")


def _section(text: str, number: str) -> str:
    """One top-level section's body (heading ``## N.`` through the next
    same-or-higher heading, subsections included)."""
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        stripped = line.lstrip("#").strip()
        if line.startswith("## ") and stripped.startswith(f"{number}."):
            start = index
            break
    assert start is not None, (
        f"flow.md must carry the §{number} section for the anchor"
    )
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    return "\n".join(body)


# AC-FR0339-03@v0.11 TRACKS-TRACE flowmd scribe evaluation precedes create branch
def test_flowmd_scribe_before_create_branch():
    """AC-FR0339-03 (contract-text half): flow.md §3 carries the v0.11 reorder
    — the INTAKE/PANEL states (Scribe evaluation, suggestion panel) precede
    CHECK_ACTIVE/CREATE_BRANCH, the terminal actions never build, and the
    branch derivation plus Scribe-titled frontmatter are pinned text."""
    text = _flow_text()
    section3 = _section(text, "3")
    for pin in _S3_PINS:
        assert pin in section3, (
            f"flow.md §3 must carry the pinned clause verbatim (§1.0.12 #1): {pin!r}"
        )
    # the reorder itself: the evaluation/panel states appear before the
    # branch creation state in the §3.1 substate machine text
    order = [section3.find(pin) for pin in ("PANEL", "CHECK_ACTIVE", "CREATE_BRANCH：")]
    assert all(pos >= 0 for pos in order), f"the §3.1 states must be present: {order!r}"
    assert order[0] < order[1] < order[2], (
        f"the substate machine must order PANEL before CHECK_ACTIVE before "
        f"CREATE_BRANCH (Scribe 评估先于 CREATE_BRANCH): {order!r}"
    )


# AC-FR0346-02@v0.11 TRACKS-TRACE triage columns contract text, no runtime gate
def test_triage_contract_columns_no_runtime_interception():
    """AC-FR0346-02: the duplicate/conflict column format and trigger rules are
    written into the TRIAGE dispatch contract (flow.md §4 node line + §4.6) —
    and the boundary clause pins detection to report columns only (no runtime
    interception, no automatic NO-GO)."""
    text = _flow_text()
    section4 = _section(text, "4")
    for pin in _S46_PINS:
        assert pin in section4, (
            f"flow.md §4 must carry the pinned column clause (§1.0.12 #3): {pin!r}"
        )
    # the TRIAGE node description carries the column pointer (§1.0.12 #2)
    assert "报告固定栏目" in section4 and "§4.6" in section4, (
        "the TRIAGE node line must point at the fixed-column contract"
    )


# AC-FR0347-01@v0.11 TRACKS-TRACE reviewer contract clause in flow protocol
def test_reviewer_contract_clause():
    """AC-FR0347-01: the reviewer re-check clause lives in the review protocol
    text (flow.md §4 hard rules and §5) — unresolved threads initiated by
    others are never a revise basis, content-verified reviews pass, thread
    confirmation stays with the initiator, and own findings anchor a thread."""
    text = _flow_text()
    section4 = _section(text, "4")
    section5 = _section(text, "5")
    for pin in _S45_PINS:
        assert pin in section4, (
            f"flow.md §4 hard rules must carry the clause (§1.0.12 #4): {pin!r}"
        )
        assert pin in section5, (
            f"flow.md §5 must carry the same clause (§1.0.12 #5): {pin!r}"
        )


# AC-NFR0158-02@v0.11 TRACKS-TRACE v010 rulings anchored in design (guard)
def test_v010_rulings_anchored():
    """AC-NFR0158-02 (guard half, test-plan §8.1): the design-time fact —
    ARCH-011 §0.1 explicitly inherits the five v0.10 workbench rulings — holds
    on arrival; the e2e regression half (test_v010_rulings_regression) carries
    the behavior red anchor. This node is an arrival-green guard: failure here
    means a real regression of the declared inheritance, fail-closed."""
    text = _ARCH_011.read_text(encoding="utf-8")
    for ruling in (
        "外壳钉视口",
        "极简设计令牌",
        "紧凑化",
        "pane bar",
        "自动保存",
    ):
        assert ruling in text, (
            f"ARCH-011 §0.1 must explicitly inherit the v0.10 ruling: {ruling!r}"
        )
    # the inheritance sentence names them as one continuing set (§0.1)
    assert "继续有效" in text, (
        "the rulings must be declared as continuing (v0.10 工作台裁定继续有效)"
    )
