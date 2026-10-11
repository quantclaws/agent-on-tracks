"""T-007 RED: flow.md v0.11 contract text + reviewer re-check exit (IF-SPLIT-001,
IF-REVIEW-002; AC-FR0339-03 / AC-FR0346-02 / AC-FR0347-01 / AC-FR0347-02).

Devon-owned unit RED for the v0.11 T-007 slice (ARCH-011 §1.0.12 pinned text +
§1.0.13 / interfaces §1o/§1p):

- ``.tracks/wiki/flow.md`` lands the §1.0.12 pinned revisions: §3 M-START
  reorders INTAKE -> PANEL ahead of CHECK_ACTIVE/CREATE_BRANCH (Scribe
  evaluation precedes branch creation; three sources, four-action panel, the
  three non-develop exits open no run and create no branch), §4.1 TRIAG gains
  the fixed-columns note, new §4.6 pins the two TRIAGE report columns, and
  §4.5-4/§5.3-4 add the reviewer re-check clause;
- ``tracks/effects/opencode_review.py`` narrows the LEX_REVIEW revise branch
  to the reviewer's OWN pending findings threads (interfaces §1p.2, initiator
  matching the review role): unresolved threads initiated by someone else no
  longer constitute a revise ground — a content-validated review passes and
  thread confirmation stays with the initiator; the Human-only park branch
  and the verdict vocabulary closed set are unchanged;
- ``tracks/agents/Scribe.md`` carries the two-column TRIAGE report discipline
  and ``Sage.md``/``Lex.md``/``Prism.md`` carry the reviewer re-check
  discipline (same source as §1.0.12).

RED discipline: every node below fails as a real ``AssertionError`` on the
current tree — the v0.11 flow.md text, the §4.6 section and the agent clauses
are absent, and the LEX_REVIEW exit still answers ``revise`` for pending
threads initiated by another reviewer. Fixtures are real tracks/discuss
documents parsed by the product parser; no stub tokens, no assembly errors,
no skips. The acceptance half (frozen integration anchors) is Shield-owned
and deliberately not pinned here.

AC: FR-0339-03, FR-0346-02, FR-0347-01, FR-0347-02 — TRACKS-TRACE
IF-SPLIT-001, IF-REVIEW-002.
"""

from __future__ import annotations

from pathlib import Path

from tracks.effects.opencode_review import OpencodeReviewMixin
from tracks.kernel.machine_verdicts import _PARK_VERDICT

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FLOW_MD = _REPO_ROOT / ".tracks" / "wiki" / "flow.md"

# Threads initiated by another reviewer (Sage) — not the LEX reviewer's own
# findings: under §1p.2 they never constitute a revise ground (verdict=pass).
NON_OWN_PENDING_DOC = (
    "# spec\n"
    "\n"
    "> **Sage:** this section needs rework before GO.\n"
)
# The reviewer's own findings threads (root speaker == Lex) keep the revise
# route — an anchored, traceable findings thread owned by the reviewer.
OWN_PENDING_DOC = (
    "# spec\n"
    "\n"
    "> **Lex:** @Sage must rework this section.\n"
    "\n"
    "> **Lex [reopen]:** still waiting on the revision.\n"
)
# Human-adjudicated pending threads only: the v0.10 park branch (IF-010 §1s),
# semantics unchanged by the v0.11 narrowing.
HUMAN_PENDING_DOC = (
    "# spec\n"
    "\n"
    "> **Lex [open]:** the rollout stop condition must be decided by @Human.\n"
)


def _md_section(text: str, heading_prefix: str) -> str:
    """Slice one markdown section — the heading line starting with
    ``heading_prefix`` up to the next heading of the same or higher level —
    so the pinned-text asserts stay scoped to their own section. ``""`` when
    the section does not exist yet (the v0.11 RED shape)."""
    lines = text.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith(heading_prefix)), None
    )
    if start is None:
        return ""
    level = len(heading_prefix) - len(heading_prefix.lstrip("#"))
    end = len(lines)
    for i in range(start + 1, len(lines)):
        line = lines[i]
        depth = len(line) - len(line.lstrip("#"))
        if line.lstrip().startswith("#") and 0 < depth <= level:
            end = i
            break
    return "\n".join(lines[start:end])


def _flow_text() -> str:
    return _FLOW_MD.read_text(encoding="utf-8")


def _agent_text(name: str) -> str:
    return (_REPO_ROOT / "tracks" / "agents" / name).read_text(encoding="utf-8")


def _doc(tmp_path: Path, name: str, body: str) -> Path:
    doc = tmp_path / name / "spec.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(body, encoding="utf-8")
    return doc


# -- flow.md §3: Scribe evaluation reorders ahead of CREATE_BRANCH ------------


# AC-FR0339-03@v0.11 TRACKS-TRACE IF-MSTART-001 §3.1 v0.11 reorder: INTAKE/PANEL land before CHECK_ACTIVE/CREATE_BRANCH
def test_flowmd_mstart_reorder_puts_intake_panel_before_create_branch():
    sec3 = _md_section(_flow_text(), "## 3. M-START")
    machine = _md_section(sec3, "### 3.1.")

    # §3 entry condition carries the v0.11 reorder declaration and the
    # explicit-entry split (unified intake only; hotfix has its own entry).
    assert "Scribe 评估先于 CREATE_BRANCH" in sec3
    assert "统一入口" in sec3
    assert "不推荐 hotfix" in sec3

    # §3.1 starts at INTAKE (three sources -> Scribe evaluation) and only the
    # panel's develop action reaches CHECK_ACTIVE.
    assert "[*] --> INTAKE" in machine
    assert "三种来源" in machine
    assert "dispatch Scribe 评估" in machine
    assert "四动作" in machine
    assert "选进入开发" in machine

    # The three non-develop panel exits are terminals: no run, no branch.
    assert "不开 run, 不建分支" in machine
    assert "ARCHIVED" in machine
    assert "REMOVED" in machine

    # CREATE_BRANCH happens after the panel: v{N+1} from main, Scribe titled.
    assert "Scribe 定标题写入 frontmatter title" in machine
    assert "releases/v{N+1}" in machine

    # Section tail notes: dormancy during evaluation + the SM-01 intake
    # state machine (unlisted transitions are disallowed).
    assert "INTAKE→PANEL 期间" in sec3
    assert "统一入口状态机（SM-01）" in sec3


# -- flow.md §4: TRIAGE fixed report columns (§4.1 note + §4.6 section) -------


# AC-FR0346-02@v0.11 TRACKS-TRACE IF-SPLIT-001 §4.1 TRIAG note + §4.6 two fixed report columns, report-only
def test_flowmd_triage_fixed_columns_section_lands():
    text = _flow_text()
    triage_node = _md_section(text, "### 4.1.")
    columns = _md_section(text, "### 4.6.")

    # §4.1 TRIAG node description gains the fixed-columns pointer line.
    assert "报告固定栏目" in triage_node
    assert "重复检测 + 冲突检测" in triage_node
    assert "§4.6" in triage_node

    # §4.6 pins the two columns and their rules (§1.0.12 pinned text).
    assert "TRIAGE 报告固定栏目" in columns
    assert "FR-0346" in columns
    # duplicates column: cross-version references to existing FR/AC, NO-GO
    # or rewrite-as-enhancement suggestion.
    assert "跨版本引用指认既有 FR/AC" in columns
    assert "改写为增强" in columns
    # conflicts column: new-vs-existing pairs; GO needs explicit awareness.
    assert "冲突对" in columns
    assert "显式确认已知晓冲突" in columns
    # trigger rule: every TRIAGE, explicit "none" when empty; report-only —
    # no runtime interception, no automatic NO-GO.
    assert "无发现时栏目显式记" in columns
    assert "不做运行时拦截或自动 NO-GO" in columns


# -- flow.md §4.5-4 / §5.3-4: reviewer re-check clause ------------------------


# AC-FR0347-01@v0.11 TRACKS-TRACE IF-REVIEW-002 §4.5-4/§5.3-4 reviewer re-check clause lands verbatim
def test_flowmd_reviewer_recheck_clause_lands_in_both_sections():
    hard_story = _md_section(_flow_text(), "### 4.5.")
    hard_spec = _md_section(_flow_text(), "### 5.3.")
    clause_tokens = (
        "评审人复核合同",
        "Sage/Lex/Prism",
        "非本人发起",
        "不构成 revise 依据",
        "内容验证通过即 pass",
        "线程确认留给其发起人",
        "revise 仅限评审人自有新发现",
        "开锚定线程",
        "FR-0347",
    )
    for token in clause_tokens:
        assert token in hard_story, f"§4.5 硬规则 missing {token!r}"
        assert token in hard_spec, f"§5.3 硬规则 missing {token!r}"
    # §5.3-4 declares itself same-source with §4.5-4.
    assert "§4.5-4" in hard_spec


# -- LEX_REVIEW exit: revise narrows to the reviewer's own findings threads ---


# AC-FR0347-02@v0.11 TRACKS-TRACE IF-REVIEW-002 non-own pending threads pass; own findings revise; park unchanged
def test_lex_review_verdict_narrows_revise_to_own_pending_threads(tmp_path):
    # Unresolved thread initiated by Sage (not the reviewer): a content
    # validated review passes — the thread is its initiator's business.
    non_own = _doc(tmp_path, "non-own", NON_OWN_PENDING_DOC)
    result = OpencodeReviewMixin._enrich_discussion({}, [non_own], "LEX_REVIEW", True)
    assert result["verdict"] == "pass"
    assert not result.get("pending_threads")

    # The reviewer's own pending findings threads keep the revise route.
    own = _doc(tmp_path, "own", OWN_PENDING_DOC)
    result = OpencodeReviewMixin._enrich_discussion({}, [own], "LEX_REVIEW", True)
    assert result["verdict"] == "revise"
    assert not result.get("pending_threads")

    # Human-adjudicated pending only: the park branch is unchanged and stays
    # ahead of the narrowing (v0.10 §1s semantics preserved).
    human = _doc(tmp_path, "human", HUMAN_PENDING_DOC)
    result = OpencodeReviewMixin._enrich_discussion({}, [human], "LEX_REVIEW", True)
    assert _PARK_VERDICT == "pass-pending-human-threads"
    assert result["verdict"] == _PARK_VERDICT
    assert result.get("pending_threads")


# -- agent dispatch-contract discipline ---------------------------------------


# AC-FR0346-02@v0.11 TRACKS-TRACE IF-SPLIT-001 Scribe dispatch contract carries the two-column discipline
def test_scribe_contract_carries_triage_fixed_columns_discipline():
    text = _agent_text("Scribe.md")

    assert "重复检测" in text
    assert "冲突检测" in text
    assert "栏目" in text


# AC-FR0347-01@v0.11 TRACKS-TRACE IF-REVIEW-002 reviewer contracts carry the re-check discipline
# AC-FR0347-02@v0.11 TRACKS-TRACE IF-REVIEW-002 revise only on own anchored findings, per contract
def test_reviewer_contracts_carry_recheck_discipline():
    for name in ("Sage.md", "Lex.md", "Prism.md"):
        text = _agent_text(name)
        assert "非本人发起" in text, f"{name} missing the re-check clause"
        assert "不构成 revise 依据" in text, f"{name} missing the revise ground"
