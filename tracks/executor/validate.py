"""Document format validation (FR-11/FR-19/FR-20, FR-150).

v0.1 checks (D-16): file exists + frontmatter parseable ("schema"), and for
spec.md the FR-count scope gate (> 30 valid FRs -> "scope_overflow", FR-20).

v0.2 adds the ``template`` check (FR-150): a document's required frontmatter
fields and level-2 sections must match its kind template (tracks/templates/).
``check_template`` is pure (reads only the file + its template) and returns a
list of ``line:N`` non-conformance messages (empty = valid); it will back
``trac validate`` and the outcome / exit-gate validation. Pure w.r.t. state.
``check_trace`` (FR-0170) is the AC<->FR bidirectional coverage check; it runs
always-on for acceptance.md (not via the checks list, like scope_overflow).

v0.3 adds the design trio kinds (architecture / interfaces / test-plan,
flow.md §8): the ``template`` check works generically from their templates,
and ``check_design_trace`` (BS-06) is the design ``trace`` check — every AC
of acceptance.md must carry a test-layer attribution (unit/integration/e2e)
in test-plan.md. Unlike the acceptance trace it is checks-list gated. Design
docs reserve blockquotes for inline-discussion threads, so the design-kinds
``template`` check also rejects leftover template-guidance blockquotes
(live run044): the marker set is derived from the design templates' guidance
comments (single source). Design docs additionally reject fabricated ``trac``
invocations (live run045): every ``trac <token>`` in the doc text (code
fences included) must name a subcommand that actually exists
(``TRAC_SUBCOMMANDS``, kept in parity with tracks/cli/main.py USAGE).

Spec item grammar (kept in sync with templates/spec.md; the template itself
carries no format prose — this module IS the format contract): every item is
``### FR-XXXX 标题`` / ``### NFR-XXXX 标题`` (uppercase, 4-digit zero-padded,
unique ID; obsolete items are deleted, IDs never reused), with a
``- **来源**：`` field and (for FRs) a ``- **交付入口**：`` field. Decisions are
recorded in inline discussions; only unresolved discussions block review exit.
All present FR items count toward FR_LIMIT. Template HTML comments are ignored;
acceptance level-2 sections vary per FR/NFR so are not name-checked.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

from tracks import templating
from tracks.discuss.delta import is_discussion_delta
from tracks.discuss.gate import check_ready
from tracks.executor.validation_shared import (
    _ACC_AC,  # noqa: F401  (re-export for backward compat)
    _ACC_SECTION,  # noqa: F401  (re-export for backward compat)
    _HEADING,
    _HTML_COMMENT,
    _acc_scan,
    _strip_comments,
)
from tracks.frontmatter import split_frontmatter

FR_LIMIT = 30

# spec item grammar: loose head (to find/flag malformed items) + strict form.
_ITEM_HEAD = re.compile(r"^###\s+((?:N?FR)-\d+)\b(.*)$", re.IGNORECASE)
_ITEM_OK = re.compile(r"^### (?:FR|NFR)-\d{4} \S")

_KIND_BY_FILE = {
    "story.md": "story",
    "spec.md": "spec",
    "acceptance.md": "acceptance",
    "test-plan.md": "test-plan",
    "prd.md": "prd",
    # v0.3 design trio (flow.md §8 / BS-03)
    "architecture.md": "architecture",
    "interfaces.md": "interfaces",
}
_NUM_PREFIX = re.compile(r"^\d+(?:\.\d+)*\.?\s+")
_FENCE = re.compile(r"^\s*(```|~~~)")

# run044 guidance-blockquote guard: conditional-section guidance lives in the
# design templates' HTML comments as '**Marker**:' lines (When needed /
# Lifecycle); the same bold prefix as a blockquote in a delivered doc is
# leftover template guidance that the discuss parser would misread as an open
# inline-discussion thread.
_DESIGN_KINDS = ("architecture", "interfaces", "test-plan")
_BOLD_MARKER = r"\*\*([A-Za-z][A-Za-z0-9 ]*?)(?::\*\*|\*\*\s*:)"
_COMMENT_MARKER = re.compile(r"^\s*" + _BOLD_MARKER, re.M)
_BQ_GUIDANCE = re.compile(r"^\s*>+\s*" + _BOLD_MARKER)
_BQ = re.compile(r"^\s*(>+)\s*(.*)$")

# FR-0130 BS-XX heading grammar (story.md): two-digit, unique ID.
_BS_ITEM_HEAD = re.compile(r"^###\s+(BS-\d+)\b(.*)$", re.IGNORECASE)
_BS_ITEM_OK = re.compile(r"^### BS-\d{2} \S")

# FR-0130 cross-version qualified reference: AC-FRXXXX-YY@vX.Y
_VERSION_QUALIFIED = re.compile(r"AC-(?:N?FR)\d{4}-\d{2}@(v\d+\.\d+)")

# run045 contract realism: canonical `trac` subcommand set — keep in sync with
# the USAGE constant in tracks/cli/main.py (tests/unit/test_validate.py pins
# the parity). A design doc invoking `trac <token>` with any other token
# fabricates tooling (run045 finding: `trac agent archer ci-scan` never
# existed); to-be-created tooling must be marked as a foundation task instead.
TRAC_SUBCOMMANDS = frozenset({
    "approve", "check", "discuss", "init", "replay", "report", "retry",
    "return", "review", "run", "start", "status", "triage", "validate",
})
_TRAC_CALL = re.compile(r"\btrac\s+([A-Za-z][A-Za-z0-9_-]*)")


def validate_document(path: Path, doc: str, checks=None) -> tuple[str, str] | None:
    """Return (check, reason) on the first failure, None when valid.

    schema (+ spec scope_overflow) always run (v0.1 D-16 + FR-20); ``checks``
    adds the v0.2 gate 'discussion_ready' (FR-100), which blocks only when
    inline discussions remain unresolved.
    """
    checks = checks or []
    if not path.exists():
        return ("schema", "missing file")
    text = path.read_text(encoding="utf-8")
    head, body = split_frontmatter(text)
    if not head:
        return ("schema", "no frontmatter")
    steps = (
        (_scope_failure, (doc, body)),
        (_trace_failure, (path, doc)),
        (_design_trace_failure, (path, doc, checks)),
        (_test_tasks_failure, (path, doc, checks)),
        (_template_failure, (path, checks)),
        (_discussion_failure, (text, checks)),
    )
    for check, args in steps:
        failure = check(*args)
        if failure:
            return failure
    return None


def _scope_failure(doc: str, body: str):
    if doc != "spec.md":
        return None
    n = _valid_fr_count(body)
    return ("scope_overflow", f"{n} FRs > {FR_LIMIT}") if n > FR_LIMIT else None


def _trace_failure(path: Path, doc: str):
    if doc != "acceptance.md":
        return None
    issues = check_trace_file(path)
    return ("trace", "; ".join(issues)) if issues else None


def _design_trace_failure(path: Path, doc: str, checks: list):
    if doc != "test-plan.md" or "trace" not in checks:
        return None
    issues = check_design_trace_file(path)
    return ("trace", "; ".join(issues)) if issues else None


def _test_tasks_failure(path: Path, doc: str, checks: list):
    if doc != "test-plan.md" or "test_tasks" not in checks:
        return None
    issues = check_test_tasks_contract_file(path)
    return ("test_tasks", "; ".join(issues)) if issues else None


def _template_failure(path: Path, checks: list):
    if "template" not in checks:
        return None
    issues = check_template(path)
    return ("template", "; ".join(issues)) if issues else None


def _discussion_failure(text: str, checks: list):
    if "discussion_ready" not in checks:
        return None
    ready, blockers = check_ready(text)
    if ready:
        return None
    return ("discussion_ready", "unresolved threads: " + ", ".join(blockers))


def _spec_items(text: str) -> list:
    """``(line_no, heading, block_lines)`` per FR/NFR item; fenced code skipped,
    any heading (level <= 3) that is not itself an item closes the open block."""
    items: list = []
    cur = None
    fence = False
    for i, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        if _ITEM_HEAD.match(line):
            cur = (i, line, [])
            items.append(cur)
        elif (m := _HEADING.match(line)) and len(m.group(1)) <= 3:
            cur = None
        elif cur:
            cur[2].append(line)
    return items


def _valid_fr_count(text: str) -> int:
    """FR-20 scope count: all FR items (obsolete ones are deleted, not marked;
    NFRs never count)."""
    return sum(
        1 for _, heading, _ in _spec_items(text)
        if _ITEM_HEAD.match(heading).group(1).upper().startswith("FR-")
    )


def check_trace(spec_text: str, acc_text: str) -> list:
    """FR-0170 AC<->FR bidirectional coverage (both hard errors). Returns the
    complete orphan list with ``line:N`` messages (no short-circuit); [] = pass.

    Forward: every spec ``### FR-XXXX``/``### NFR-XXXX`` needs an acceptance
    ``## FR-XXXX`` section containing >=1 ``### AC-FRXXXX-YY`` back-referencing
    it (orphan -> item ID + spec line:N). Reverse: every acceptance AC must
    back-reference an existing spec item (orphan -> AC ID + acceptance line:N).
    Discussion blocks ('>' lines) and fenced code are ignored. No I/O."""
    spec_ids: dict = {}
    for line_no, heading, _ in _spec_items(spec_text):
        spec_ids.setdefault(_ITEM_HEAD.match(heading).group(1).upper(), line_no)
    sections, acs = _acc_scan(acc_text)
    covered = {section for _, ref, _, section in acs if section == ref}
    issues = [
        f"line:{line_no} {item_id} has no '## {item_id}' section in acceptance"
        if item_id not in sections else
        f"line:{line_no} {item_id} acceptance section has no AC item for it"
        for item_id, line_no in spec_ids.items()
        if item_id not in sections or item_id not in covered
    ]
    issues += [
        f"line:{line_no} {ac_id} refers to missing {ref} in spec"
        for ac_id, ref, line_no, _ in acs if ref not in spec_ids
    ]
    return issues


def check_trace_file(path: Path) -> list:
    """FR-0170 trace for an on-disk acceptance doc (reads the sibling spec.md).
    Used by validate_document (always-on for acceptance.md) and trac validate."""
    spec_path = path.parent / "spec.md"
    if not spec_path.exists():
        return ["line:1 acceptance validate requires spec.md in same dir"]
    return check_trace(spec_path.read_text(encoding="utf-8"),
                       path.read_text(encoding="utf-8"))


from tracks.executor.test_tasks import (  # noqa: E402,F401
    _extract_if_registry,
    check_design_trace,
    check_design_trace_file,
    check_test_tasks,
    check_test_tasks_contract_file,
    parse_test_tasks,
    required_ac_ids,
)


def check_spec_items(text: str) -> list:
    """FR-150 spec item lint — the machine-enforced half of the spec format.

    Checks per item: strict heading form, unique ID, a '- **来源**：' field,
    and (for FRs) a '- **交付入口**：' field. Decisions live in discussions;
    discussion_ready is checked separately. Returns ``line:N`` messages.
    """
    issues: list = []
    seen: dict = {}
    for line_no, heading, block in _spec_items(text):
        item_id = _ITEM_HEAD.match(heading).group(1).upper()
        if not _ITEM_OK.match(heading):
            issues.append(f"line:{line_no} bad item heading {heading!r}"
                          " (expect '### FR-XXXX 标题', uppercase, 4-digit)")
        if item_id in seen:
            issues.append(f"line:{line_no} duplicate id {item_id}"
                          f" (first at line:{seen[item_id]})")
        seen.setdefault(item_id, line_no)
        if not any(ln.startswith("- **来源**：") for ln in block):
            issues.append(f"line:{line_no} {item_id} missing '- **来源**：' field")
        if (item_id.startswith("FR-")
                and not any(ln.startswith("- **交付入口**：") for ln in block)):
            issues.append(f"line:{line_no} {item_id} missing '- **交付入口**：' field")
    return issues


def _story_bs_items(text: str) -> list:
    """``(line_no, heading, block_lines)`` per BS-XX item in story.md; fenced
    code skipped, any heading (level <= 3) that is not itself an item closes
    the open block."""
    items: list = []
    cur = None
    fence = False
    for i, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        if _BS_ITEM_HEAD.match(line):
            cur = (i, line, [])
            items.append(cur)
        elif (m := _HEADING.match(line)) and len(m.group(1)) <= 3:
            cur = None
        elif cur:
            cur[2].append(line)
    return items


def check_story_items(text: str) -> list:
    """FR-0130 story item lint - BS-XX grammar enforcement.

    Checks per item: strict heading form (``### BS-XX 标题``, two-digit,
    uppercase), unique ID. ID immutability/tombstone rules are enforced by the
    trace tool (FR-0080); this validates the heading grammar. Returns
    ``line:N`` messages.
    """
    issues: list = []
    seen: dict = {}
    for line_no, heading, _block in _story_bs_items(text):
        item_id = _BS_ITEM_HEAD.match(heading).group(1).upper()
        if not _BS_ITEM_OK.match(heading):
            issues.append(
                f"line:{line_no} bad item heading {heading!r}"
                " (expect '### BS-XX 标题', uppercase, 2-digit)"
            )
        if item_id in seen:
            issues.append(
                f"line:{line_no} duplicate id {item_id}"
                f" (first at line:{seen[item_id]})"
            )
        seen.setdefault(item_id, line_no)
    return issues


def _norm_heading(line: str) -> str | None:
    """Normalized level-2 heading text (number prefix stripped), or None."""
    m = _HEADING.match(line)
    if not m or len(m.group(1)) != 2:  # only '##' (level 2)
        return None
    return _NUM_PREFIX.sub("", m.group(2)).strip()


def _fm_fields(head: str) -> set:
    out = set()
    for line in head.splitlines():
        if ":" in line and not line.startswith("---"):
            out.add(line.split(":", 1)[0].strip())
    return out


def _design_guidance_markers() -> frozenset:
    """Guidance marker set, derived from the design trio templates (single
    source): '**Marker**:' lines inside their HTML guidance comments are the
    converted conditional-section guidance (When needed / Lifecycle)."""
    markers: set = set()
    for kind in _DESIGN_KINDS:
        try:
            tpl_text = templating.load_template(kind)
        except FileNotFoundError:
            continue
        for comment in _HTML_COMMENT.findall(tpl_text):
            markers.update(_COMMENT_MARKER.findall(comment))
    return frozenset(markers)


def _guidance_blockquote_issues(text: str) -> list:
    """Leftover template-guidance blockquotes (run044): delivered design docs
    reserve blockquotes for inline-discussion threads, so any blockquote whose
    bold prefix matches a guidance marker fails the template check. HTML
    comment and fenced-code lines are ignored. Returns ``line:N`` messages."""
    markers = _design_guidance_markers()
    if not markers:
        return []
    hidden: set = set()
    for m in _HTML_COMMENT.finditer(text):
        first = text.count("\n", 0, m.start()) + 1
        hidden.update(range(first, first + m.group(0).count("\n") + 1))
    issues: list = []
    fence = False
    for line_no, line in enumerate(text.splitlines(), start=1):
        if line_no in hidden:
            continue
        if _FENCE.match(line):
            fence = not fence
            continue
        if fence:
            continue
        if (m := _BQ_GUIDANCE.match(line)) and m.group(1) in markers:
            issues.append(f"line:{line_no} template guidance blockquote left "
                          f"in doc ('{m.group(1)}')")
    return issues


def _trac_command_issues(text: str) -> list:
    """run045 fabricated-command guard: every ``trac <token>`` invocation in
    the doc text (code fences included — fabricated commands hide there) must
    name a subcommand from ``TRAC_SUBCOMMANDS``. HTML comments are ignored.
    Returns ``line:N`` messages (true file line numbers)."""
    hidden: set = set()
    for m in _HTML_COMMENT.finditer(text):
        first = text.count("\n", 0, m.start()) + 1
        hidden.update(range(first, first + m.group(0).count("\n") + 1))
    issues: list = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if line_no in hidden:
            continue
        for token in _TRAC_CALL.findall(line):
            if token not in TRAC_SUBCOMMANDS:
                issues.append(
                    f"line:{line_no} unknown trac subcommand {token!r} "
                    "(no such command; mark to-be-created tooling as a "
                    "foundation task)")
    return issues


# Legacy story profile (pre-latest-template structure). The latest template
# (tracks/templates/story.md) carries: 原始输入 / 用户意图 / 核心操作路径 /
# 行为种子 / 范围、约束与例外 / 开放产品决定 / 必要性与风险. Legacy stories
# use a different section set: 原始输入 / 用户意图 / 需求描述(suffixable) /
# 工作项 / 开放产品决定 / 范围、约束与例外 / 分流建议. The product contract:
# a complete legacy story is not force-migrated for latest-template diffs.
# Strong legacy signal = >=2 legacy-only level-2 headings present (工作项 /
# 分流建议 / 需求描述…); then the full legacy required-set is enforced.
_LEGACY_ONLY_EXACT = ("工作项", "分流建议")
_LEGACY_REQUIRED_EXACT = (
    "原始输入", "用户意图", "工作项",
    "开放产品决定", "范围、约束与例外", "分流建议",
)
_LEGACY_REQUIRED_PREFIX = "需求描述"


def _legacy_signal_count(doc_secs: set) -> int:
    n = sum(1 for h in _LEGACY_ONLY_EXACT if h in doc_secs)
    n += sum(1 for s in doc_secs if s.startswith(_LEGACY_REQUIRED_PREFIX))
    return n


def _legacy_section_issues(doc_secs: set) -> list:
    issues = [f"line:1 missing section '{s}'"
              for s in _LEGACY_REQUIRED_EXACT if s not in doc_secs]
    if not any(s.startswith(_LEGACY_REQUIRED_PREFIX) for s in doc_secs):
        issues.append(f"line:1 missing section '{_LEGACY_REQUIRED_PREFIX}'")
    return sorted(issues)


def check_template(path: Path) -> list:
    """FR-150 'template' check.

    Required frontmatter fields and level-2 sections must match the kind
    template (HTML comments ignored); spec docs additionally pass the item lint
    (``check_spec_items``). Acceptance level-2 sections vary per FR/NFR, so only
    frontmatter is name-checked there. Design trio docs additionally reject
    leftover template-guidance blockquotes (run044) and fabricated ``trac``
    subcommand invocations (run045). Returns ``line:N ...`` messages;
    [] = valid.

    Story legacy compatibility: a complete legacy-structured story (strong
    legacy signal = >=2 legacy-only level-2 headings) is validated against the
    legacy required-section set, not force-migrated to the latest template.
    """
    kind = _KIND_BY_FILE.get(path.name)
    if kind is None:
        return [f"line:1 no template mapping for {path.name!r}"]
    try:
        tpl_text = templating.load_template(kind)
    except FileNotFoundError:
        return [f"line:1 no template for kind {kind!r}"]
    if not path.exists():
        return ["line:1 missing file"]
    text = path.read_text(encoding="utf-8")
    tpl_head, tpl_body = split_frontmatter(_strip_comments(tpl_text))
    head, body = split_frontmatter(_strip_comments(text))
    issues = [
        f"line:1 missing frontmatter field '{f}'"
        for f in sorted(_fm_fields(tpl_head) - _fm_fields(head))
    ]
    if kind != "acceptance":  # acceptance sections vary per FR/NFR (FR-150)
        tpl_secs = {s for s in (_norm_heading(ln) for ln in tpl_body.splitlines()) if s}
        doc_secs = {s for s in (_norm_heading(ln) for ln in body.splitlines()) if s}
        if kind == "story" and _legacy_signal_count(doc_secs) >= 2:
            issues += _legacy_section_issues(doc_secs)
        else:
            issues += [f"line:1 missing section '{s}'" for s in sorted(tpl_secs - doc_secs)]
    if kind == "spec":
        issues += check_spec_items(text)  # true file line numbers (full text scan)
    if kind == "story":  # FR-0130: BS-XX grammar enforcement
        issues += check_story_items(text)
    if kind in _DESIGN_KINDS:  # run044: blockquote = discussion thread only
        issues += _guidance_blockquote_issues(text)
        # run045: no fabricated `trac` calls (full text -> true file line numbers)
        issues += _trac_command_issues(text)
    return issues


# -- v0.5 pipeline helpers (diff policy, digests, staged changes) -----------

def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=check)


def has_diff(repo, doc_path, base_sha):
    """Check if doc has any diff vs base_sha."""
    proc = _git(repo, "diff", base_sha, "--", str(doc_path), check=False)
    return bool(proc.stdout.strip())


def _load_base_text(repo, doc_path, base_sha):
    """Load base version of doc from git, or None if not found."""
    rel_path = str(doc_path.relative_to(repo.resolve()))
    base_proc = subprocess.run(
        ["git", "show", f"{base_sha}:{rel_path}"], cwd=repo,
        capture_output=True,
    )
    if base_proc.returncode != 0:
        return None
    return base_proc.stdout


def is_discussion_diff(repo, doc_path, base_sha):
    """Check if the diff of doc vs base_sha is only canonical discussion
    changes (blockquote threads parseable by the discuss parser).

    Thin I/O wrapper around ``tracks.discuss.delta.is_discussion_delta``:
    loads the base text from git at *base_sha*, reads the current on-disk
    content, then delegates to the pure delta check.  Returns False when the
    base text cannot be loaded (untracked path) or the current file is
    unreadable.
    """
    base_text = _load_base_text(repo, doc_path, base_sha)
    if base_text is None:
        return False
    try:
        current_text = doc_path.read_bytes()
    except OSError:
        return False
    return is_discussion_delta(base_text, current_text)


def capture_digests(doc_paths):
    """sha256 of each artifact file content at capture time, for anti-tamper
    re-verification at checkpoint. ``doc_paths`` is {doc_name: Path}."""
    digests = {}
    for doc, path in doc_paths.items():
        if path.exists():
            digests[doc] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def verify_digests(doc_paths, submitted_digests):
    """Re-verify artifact digests. Returns (doc, path) for the first drifted
    artifact, or (None, None) if all match."""
    for doc, submitted in submitted_digests.items():
        path = doc_paths.get(doc)
        if path and path.exists():
            current = hashlib.sha256(path.read_bytes()).hexdigest()
            if current != submitted:
                return doc, path
    return None, None


def has_staged_changes(repo, doc_paths):
    """Check if any doc path has uncommitted changes."""
    for path in doc_paths.values():
        proc = _git(repo, "status", "--porcelain", "--", str(path),
                    check=False)
        if proc.stdout.strip():
            return True
    return False
