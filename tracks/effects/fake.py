"""Deterministic FakeBackend (NFR-01) — first-class executor stand-in, not a mock.

Moved from tracks/executor/fake_agent.py (v0.1 FakeAgent) into the effects
boundary (ARCH-003 §4); behavior is unchanged. Implements ``AgentBackend``.

Behavior injection: TRAC_FAKE_SIMULATE, read ONLY at the cli/executor boundary
(interfaces §5 note); project()/decide() never see it. Format: semicolon- or
comma-separated `key=token` entries, key = "role:substate" (or "validator:doc").
A token may be a `|`-separated sequence consumed one per call (last one
sticks), e.g. `sage:SAGE_REVIEW=comment|pass`. Defaults: document work "ok",
reviews "pass". Token "hang" blocks (recovery/lock tests, AC-27a).
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from tracks import paths, templating
from tracks.capabilities import supports_m_impl
from tracks.effects.backend import SHIELD_LAYERS
from tracks.effects.devon_patch import DevonPatchMixin, append_text, unified_patch
from tracks.effects.fake_shield import _FAILED_TOKENS, FakeShieldMixin, _ac_slug
from tracks.frontmatter import split_frontmatter

# spec items the fake acceptance draft must cover (FR-0170 trace)
_SPEC_ITEM = re.compile(r"^### (N?FR-\d{4})[ \t]*(.*)$", re.M)
# AC items the fake test-plan must attribute a test layer (BS-06 design trace)
_ACC_ITEM = re.compile(r"^### (AC-[A-Z0-9]+-\d+)\b", re.M)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_FENCE = re.compile(r"^\s*(```|~~~)")
_SCAFFOLD_HEADING = re.compile(
    r"^##\s+(?:\d+(?:\.\d+)*\.?\s+)?Scaffold 宣言\s*$", re.M
)
_COVERAGE_HEADING = re.compile(
    r"^##\s+(?:\d+(?:\.\d+)*\.?\s+)?AC Coverage\s*$", re.M
)
_CLOSURE_HEADING = re.compile(
    r"^###\s+1\.2\s+Required AC closure \(ISLAND_GATE_1\)\s*$", re.M
)
_CLI_HEADING = re.compile(
    r"^##\s+(?:\d+(?:\.\d+)*\.?\s+)?CLI 接口合同\s*$", re.M
)
_IF_REGISTRY = re.compile(
    r"^##\s+(?:\d+(?:\.\d+)*\.?\s+)?IF Registry\s*$", re.M
)
_SCAFFOLD_RESERVED_ROOTS = frozenset({".git", ".opencode", ".tracks"})
# M-IMPL v0.5 reach declarations: the Fake M-DESIGN declares and materializes
# the reach entrypoint manifest as one canonical architecture Scaffold config
# artifact (ResultCheckpoint stages it like the project contract; reach.py
# reads the same path). Valid only for the Fake fixture — production designs
# never declare it, so production reach semantics are unchanged.
_REACH_ENTRIES_RELATIVE = ".tracks/reach-entries.txt"
_REACH_ENTRY_SCAFFOLD_LINE = (
    "- .tracks/reach-entries.txt — Fake M-IMPL reach entrypoints (config)"
)
# FR-0210 exit gate tokens: a failed agent run is not a produced document.
# (shared with the Fake Shield mixin; see fake_shield.py)
_PRISM_REVIEW_SUBSTATES = (
    "PRISM_REVIEW", "PRISM_PLAN", "PRISM_RED", "PRISM_FINAL", "DIAGNOSE",
)
_DEVON_PHASES = ("red", "green", "refactor")
_DEVON_FAILURE_TOKENS = frozenset((*_FAILED_TOKENS, "hang"))

SPEC_TEMPLATE = """# {version} — 功能规格（FakeAgent 草案）

## 功能需求

### FR-0010 覆盖 story 目标

- **来源**：story §目标
- **交付入口**：无独立入口，依附 story

由 story.md 派生的确定性规格草案。

## 非功能需求

### NFR-0010 确定性输出

- **来源**：story §目标

FakeAgent 输出确定、可复现。
"""


def _simulate_map() -> dict:
    raw = os.environ.get("TRAC_FAKE_SIMULATE", "")
    out: dict = {}
    for part in raw.replace(",", ";").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _story_title(body: str) -> str:
    """Coherent title: the §1 原始输入 request, else the first heading."""
    in_raw = False
    for line in body.splitlines():
        if line.strip() == "## 1. 原始输入":
            in_raw = True
            continue
        if in_raw:
            if line.startswith("#"):  # reached the next section
                break
            if line.startswith(">") and line[1:].strip():
                return line[1:].strip()[:60]
    first = next((ln for ln in body.splitlines() if ln.strip()), "untitled")
    return (first.lstrip("# ").strip() or "untitled")[:60]


class FakeBackend(DevonPatchMixin, FakeShieldMixin):
    """Deterministic AgentBackend (v0.1 FakeAgent, relocated to effects/)."""

    def __init__(self, repo: Path, version: str):
        self.repo = repo
        self.version = version
        self._calls: dict = {}
        self._design_revisions = 0
        self._shield_writes = 0

    def token(self, key_left: str, key_right: str, default: str) -> str:
        key = f"{key_left}:{key_right}"
        seq = _simulate_map().get(key, default).split("|")
        n = self._calls.get(key, 0)
        self._calls[key] = n + 1
        return seq[min(n, len(seq) - 1)].strip()

    def act(self, role: str, substate: str, doc: str | None,
             doc_path: Path | None, assignment: dict | None = None) -> dict:
        no_diff = self._act_no_diff(role, substate)
        if no_diff is not None:
            return no_diff
        if role == "devon":
            data = assignment if isinstance(assignment, dict) else {}
            return self._act_m_impl_devon(substate, data)
        return self._act_legacy(role, substate, doc, doc_path, assignment)

    def _act_legacy(self, role: str, substate: str, doc: str | None,
                    doc_path: Path | None, assignment: dict | None) -> dict:
        if substate == "TRIAGE":
            return {"status": "done", "artifact_ref": None,
                    "self_report": "explored raw requirement"}
        if role == "shield":
            return self._act_shield(substate, assignment)
        if substate in ("DRAFT", "RESPOND"):
            return self._act_draft(role, substate, doc, doc_path, assignment)
        if self._is_m_impl_planning(role, substate):
            return self._act_m_impl_planning(assignment)
        verdict = self.token(role, substate, "pass")
        result = {"status": "done", "artifact_ref": None,
                  "self_report": f"review: {verdict}", "verdict": verdict}
        # v0.5 ResultCheckpoint: non-pass verdicts must produce a diff
        # (discussion annotation) so the pipeline can checkpoint it.
        if verdict in ("revise", "comment"):
            self._maybe_annotate_review(doc_path, assignment, role, verdict,
                                        result)
        # D-29: Prism echoes the assigned criteria-pack identity.
        if role == "prism" and substate in _PRISM_REVIEW_SUBSTATES:
            assigned_pack = (assignment or {}).get("criteria_pack")
            if assigned_pack:
                result["criteria_pack"] = dict(assigned_pack)
        # defect_classification injection (for testing rollback routing)
        if (role == "prism" and substate in _PRISM_REVIEW_SUBSTATES
                and verdict != "pass"):
            dc = _simulate_map().get("prism:defect_classification")
            if dc:
                result["defect_classification"] = dc
        return result

    def _act_no_diff(self, role: str, substate: str) -> dict | None:
        """v0.5 no_diff peer review: canned outcomes for explain/review."""
        if substate == "NO_DIFF_EXPLAIN":
            return {"status": "done", "artifact_ref": None,
                    "self_report": "work was already committed in a prior "
                                   "commit; no new diff produced"}
        if substate == "NO_DIFF_REVIEW":
            verdict = self.token(role, "NO_DIFF_REVIEW", "pass")
            return {"status": "done", "artifact_ref": None,
                    "verdict": verdict,
                    "self_report": f"no_diff review: {verdict}"}
        return None

    def _maybe_annotate_review(self, doc_path, assignment, role, verdict,
                               result):
        """Annotate the review doc in-place so non-pass verdicts produce a
        diff for the ResultCheckpoint pipeline."""
        annotate_path = doc_path
        if annotate_path is None and assignment and assignment.get("docs"):
            annotate_path = self._design_vdir() / assignment["docs"][0]
        if annotate_path and annotate_path.exists():
            self._annotate_review(annotate_path, role, verdict)
            result["diff_ref"] = "annotation"

    def _act_draft(self, role: str, substate: str, doc: str | None,
                   doc_path: Path | None, assignment: dict | None = None) -> dict:
        token = self.token(role, substate, "ok")
        if token in _FAILED_TOKENS:
            fclass = "agent_failed" if token == "fail" else token
            return {"status": "failed", "artifact_ref": None,
                    "failure_class": fclass,
                    "audit_evidence": f"simulated {fclass}",
                    "self_report": f"agent exit gate failed: {fclass}"}
        if token == "hang":
            time.sleep(600)  # blocked agent: lock-contention path (AC-27a)
        if role == "archer":
            scaffold, failure = self._validated_fake_scaffold(assignment)
            if failure is not None:
                return failure
            return self._act_design(substate, token, scaffold)
        self._write_stage_doc(doc, doc_path, token)
        if substate == "RESPOND" and doc_path and doc_path.exists():
            self._resolve_open_threads(doc_path)
        return {"status": "done", "artifact_ref": str(doc_path),
                "self_report": f"wrote {doc} ({token})"}

    def _act_design(self, substate: str, token: str,
                    scaffold: list[dict] | None = None) -> dict:
        # BS-03/Decision A: one assignment covers all three design docs.
        scaffold = scaffold or []
        if substate == "RESPOND":
            vdir = self._revise_design(token, scaffold)
            report = f"revised design trio ({token})"
        else:
            vdir = self._write_design(token, scaffold)
            report = f"wrote design trio ({token})"
        return {"status": "done", "artifact_ref": str(vdir),
                "self_report": report}

    def _write_stage_doc(self, doc: str | None, doc_path: Path | None,
                         token: str) -> None:
        if doc == "story.md":
            self._write_story(doc_path)
        elif doc == "acceptance.md":
            self._write_acceptance(doc_path, token)
        else:
            self._write_spec(doc_path, token)

    @staticmethod
    def _annotate_review(doc_path: Path, role: str, verdict: str) -> None:
        """Append a canonical discussion annotation to the doc so the
        checkpoint pipeline has a diff to stage for non-pass verdicts."""
        text = doc_path.read_text(encoding="utf-8")
        name = role.capitalize()
        annotation = f"\n\n> **{name}:** review annotation ({verdict})\n"
        doc_path.write_text(text + annotation, encoding="utf-8")

    @staticmethod
    def _resolve_open_threads(doc_path: Path) -> None:
        """Resolve all open discussion threads in the doc.

        Simulates the author addressing reviewer comments during RESPOND:
        changes ``> **Name:**`` (open) to ``> **Name [resolved]:**`` so the
        EXIT ``discussion_ready`` gate passes."""
        text = doc_path.read_text(encoding="utf-8")
        changed = re.sub(
            r'^(>\s*\*\*@?[^*\[\]]+?)\s*:\*\*',
            r'\1 [resolved]:**',
            text, flags=re.M)
        if changed != text:
            doc_path.write_text(changed, encoding="utf-8")

    # -- M-DESIGN (Archer draft/revise; BS-03/BS-06) -------------------------

    def _validated_fake_scaffold(
        self, assignment: dict | None,
    ) -> tuple[list[dict], dict | None]:
        """Validate the test-only scaffold contract before any design write."""
        context = assignment.get("scenario_context") if isinstance(assignment, dict) else None
        if not isinstance(context, dict) or "fake_scaffold" not in context:
            return [], None
        raw_entries = context["fake_scaffold"]
        if not isinstance(raw_entries, list) or not raw_entries:
            return [], self._invalid_fake_scaffold(
                "fake_scaffold must be a non-empty list"
            )

        entries: list[dict] = []
        seen: set[str] = set()
        repo_root = self.repo.resolve()
        for index, raw_entry in enumerate(raw_entries):
            entry, reason = self._validate_fake_scaffold_entry(
                raw_entry, index, repo_root, seen
            )
            if reason is not None:
                return [], self._invalid_fake_scaffold(reason)
            assert entry is not None
            entries.append(entry)
        return entries, None

    def _validate_fake_scaffold_entry(
        self, raw_entry: object, index: int, repo_root: Path, seen: set[str],
    ) -> tuple[dict | None, str | None]:
        if not isinstance(raw_entry, dict):
            return None, f"entry {index} must be an object"
        path = raw_entry.get("path")
        content = raw_entry.get("content")
        mode = raw_entry.get("mode")
        description = raw_entry.get("description", "declared executable stub")
        if not isinstance(path, str) or not path.strip():
            return None, f"entry {index} path must be a non-empty string"
        if not isinstance(content, str):
            return None, f"entry {index} content must be a string"
        if mode != "executable":
            return None, f"entry {index} mode must be 'executable'"
        if (not isinstance(description, str) or not description.strip()
                or "\n" in description or "\r" in description):
            return None, f"entry {index} description must be a single line"
        try:
            raw_path = Path(path)
            if (raw_path.is_absolute() or ".." in raw_path.parts
                    or any(char.isspace() for char in path)):
                raise ValueError("path must be a clean repo-relative path")
            resolved = (self.repo / raw_path).resolve(strict=False)
            relative = resolved.relative_to(repo_root)
        except (OSError, RuntimeError, ValueError):
            return None, f"entry {index} path escapes repository: {path!r}"
        if (not relative.parts
                or relative.parts[0] in _SCAFFOLD_RESERVED_ROOTS):
            return None, f"entry {index} path is not allowed: {path!r}"
        canonical = str(relative)
        if canonical in seen:
            return None, f"duplicate path: {canonical}"
        candidate = self.repo / relative
        if (self._has_symlink_component(relative)
                or candidate.is_dir()):
            return None, f"entry {index} path is not a regular file: {path!r}"
        seen.add(canonical)
        return {
            "path": canonical,
            "content": content,
            "mode": mode,
            "description": description.strip(),
        }, None

    def _has_symlink_component(self, relative: Path) -> bool:
        candidate = self.repo
        for part in relative.parts:
            candidate /= part
            if candidate.is_symlink():
                return True
        return False

    @staticmethod
    def _invalid_fake_scaffold(reason: str) -> dict:
        return {
            "status": "failed",
            "artifact_ref": None,
            "failure_class": "invalid_fake_scaffold",
            "audit_evidence": reason,
            "self_report": f"invalid fake_scaffold: {reason}",
        }

    @staticmethod
    def _append_section_lines(text: str, heading: re.Pattern,
                              lines: list[str]) -> str:
        """Append missing lines inside one existing level-2 section."""
        match = heading.search(text)
        if not match:
            return text
        rest = text[match.end():]
        next_heading = re.search(r"^##\s", rest, re.M)
        section_end = match.end() + (
            next_heading.start() if next_heading else len(rest)
        )
        section = text[match.end():section_end]
        existing = set(section.splitlines())
        missing = [line for line in lines if line not in existing]
        if not missing:
            return text
        body = section.strip()
        if body:
            body += "\n\n"
        body += "\n".join(missing)
        tail = text[section_end:].lstrip("\n")
        return text[:match.end()] + "\n\n" + body + "\n\n" + tail

    @staticmethod
    def _scaffold_lines(scaffold: list[dict]) -> list[str]:
        return [
            f"- {entry['path']} — {entry['description']}"
            for entry in scaffold
        ]

    @staticmethod
    def _interface_lines(scaffold: list[dict]) -> list[str]:
        if any(entry["path"] == "code-stats" for entry in scaffold):
            return [
                "- `./code-stats` -> IF-MTEST-001 (public CLI stub; "
                "implementation deferred)."
            ]
        return []

    def _materialize_fake_scaffold(self, scaffold: list[dict]) -> None:
        for entry in scaffold:
            path = self.repo / entry["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(entry["content"], encoding="utf-8")
            path.chmod(0o755)

    def _ensure_design_scaffold(self, vdir: Path, scaffold: list[dict]) -> None:
        if not scaffold:
            return
        architecture = vdir / "architecture.md"
        architecture.write_text(
            self._append_section_lines(
                architecture.read_text(encoding="utf-8"),
                _SCAFFOLD_HEADING,
                self._scaffold_lines(scaffold),
            ),
            encoding="utf-8",
        )
        interface_lines = self._interface_lines(scaffold)
        if interface_lines:
            interfaces = vdir / "interfaces.md"
            interfaces.write_text(
                self._append_section_lines(
                    interfaces.read_text(encoding="utf-8"),
                    _CLI_HEADING,
                    interface_lines,
                ),
                encoding="utf-8",
            )
        self._materialize_fake_scaffold(scaffold)
        self._write_project_contract()
        if self._reach_entries_supported():
            self._write_reach_entries(vdir)

    def _design_vdir(self) -> Path:
        return paths.version_dir(paths.tracks_home(self.repo), self.version)

    def _design_doc(self, kind: str) -> str:
        """Template-conformant body: the canonical template with its HTML
        guidance comments removed (all required frontmatter fields and
        level-2 sections are present by construction). Blockquote lines are
        dropped: the discuss parser reads `> **Name:** ...` as inline-discussion
        threads, and unresolved threads would block the discussion_ready gate."""
        out: list = []
        fence = False
        for line in _HTML_COMMENT.sub("", templating.load_template(kind)).splitlines():
            if _FENCE.match(line):
                fence = not fence
            if not fence and line.lstrip().startswith(">"):
                continue
            if kind == "architecture" and line.lstrip().startswith("- {path} —"):
                continue
            out.append(line)
        return "\n".join(out) + "\n"

    def _island_closure_lines(self, interfaces: str) -> list[str]:
        """Build one concrete closure line per acceptance AC."""
        from tracks.executor.taskgraph import _requirement_ref  # noqa: PLC0415
        from tracks.executor.test_tasks import (  # noqa: PLC0415
            _extract_if_registry,
            _known_ac_ids,
        )

        acceptance = self._design_vdir() / "acceptance.md"
        if not acceptance.is_file():
            return []
        ac_ids = sorted(_known_ac_ids(acceptance.read_text(encoding="utf-8")))
        if_ids = _extract_if_registry(interfaces)
        if not ac_ids or not if_ids:
            return []
        registered = sorted(if_ids)
        lines = []
        for index, ac_id in enumerate(ac_ids):
            slug = _ac_slug(ac_id)
            layer = SHIELD_LAYERS[index % len(SHIELD_LAYERS)]
            if_id = registered[index % len(registered)]
            lines.append(
                f"- **{_requirement_ref(ac_id)}** owner=FakeBackend "
                "surface=CLI composition=FakeBackend._write_design "
                f"wiring={ac_id}:acceptance.md->architecture.md->interfaces.md "
                f"test={layer}:tests/{layer}/test_{slug}.py "
                f"evidence=cmd:.venv/bin/python_-m_pytest_tests/{layer}/"
                f"test_{slug}.py_-q;output:exit-0-passed {if_id}"
            )
        return lines

    def _write_design(self, token: str, scaffold: list[dict] | None = None) -> Path:
        """Write architecture.md + interfaces.md + test-plan.md (one DRAFT
        covers all three). Tokens: template_broken drops architecture.md's
        last required section (template gate fails); trace_orphan leaves the
        last acceptance AC without a test-layer attribution (BS-06 fails)."""
        scaffold = scaffold or []
        vdir = self._design_vdir()
        interfaces = self._design_doc("interfaces")
        interface_lines = self._interface_lines(scaffold)
        if interface_lines:
            interfaces = self._append_section_lines(
                interfaces, _CLI_HEADING, interface_lines
            )
        interfaces = self._append_section_lines(
            interfaces, _IF_REGISTRY,
            ["### IF-MTEST-001 M-TEST 接口"],
        )
        arch = self._design_doc("architecture")
        if scaffold:
            arch = self._append_section_lines(
                arch, _SCAFFOLD_HEADING, self._scaffold_lines(scaffold)
            )
        arch = self._append_section_lines(
            arch, _CLOSURE_HEADING, self._island_closure_lines(interfaces)
        )
        if self._reach_entries_supported() and self._reach_entry_lines():
            arch = self._append_section_lines(
                arch, _SCAFFOLD_HEADING, [_REACH_ENTRY_SCAFFOLD_LINE]
            )
        if token == "template_broken":
            idx = max(i for i, ln in enumerate(arch.splitlines())
                      if ln.startswith("## "))
            arch = "\n".join(arch.splitlines()[:idx]) + "\n"
        (vdir / "architecture.md").write_text(arch, encoding="utf-8")
        (vdir / "interfaces.md").write_text(interfaces, encoding="utf-8")
        plan_body = self._drop_coverage_section(self._design_doc("test-plan"))
        (vdir / "test-plan.md").write_text(
            plan_body.rstrip("\n") + "\n" + self._ac_coverage(token),
            encoding="utf-8")
        self._materialize_fake_scaffold(scaffold)
        self._write_project_contract()
        if self._reach_entries_supported():
            self._write_reach_entries(vdir)
        return vdir

    def _drop_coverage_section(self, text: str) -> str:
        """Drop the template's ``## 8. AC Coverage`` section (heading onward);
        ``_ac_coverage`` regenerates the real machine-readable table with the
        actual acceptance AC rows, so the delivered doc carries exactly one."""
        m = _COVERAGE_HEADING.search(text)
        return text[:m.start()] if m else text

    def _write_project_contract(self) -> None:
        """Write a demo host test execution contract at
        ``.tracks/projects/project.toml`` (v0.4: pytest framework)."""
        toml_path = paths.project_toml_path(paths.tracks_home(self.repo))
        toml_path.parent.mkdir(parents=True, exist_ok=True)
        toml_path.write_text(
            '[integration]\n'
            'framework = "pytest"\n'
            'paths = ["tests/integration/"]\n'
            'collect = ".venv/bin/python -m pytest --collect-only -q tests/integration/"\n'
            'run = ".venv/bin/python -m pytest tests/integration/ --tb=short -q"\n'
            'cwd = "."\n\n'
            '[e2e]\n'
            'framework = "pytest"\n'
            'paths = ["tests/e2e/"]\n'
            'collect = ".venv/bin/python -m pytest --collect-only -q tests/e2e/"\n'
            'run = ".venv/bin/python -m pytest tests/e2e/ --tb=short -q"\n'
            'cwd = "."\n',
            encoding="utf-8",
        )

    def _reach_entries_supported(self) -> bool:
        """Reach entrypoint declarations are an M-IMPL (v0.5) artifact of the
        Fake fixture; older flow versions (v0.1/v0.4) and malformed versions
        end at the M-TEST boundary and must not declare extra config files."""
        return supports_m_impl(self.version)

    def _reach_entry_lines(self) -> list[str]:
        """Deterministic reach entrypoint module names for every acceptance AC:
        one dotted module per AC matching Fake Archer PLANNING's production
        path ``tracks/impl/{ac_slug}.py`` (``ac_slug`` = lowercased hyphen-
        free AC id). Pure function of acceptance.md content (NFR-01/02)."""
        from tracks.executor.test_tasks import _known_ac_ids  # noqa: PLC0415

        acc = self._design_vdir() / "acceptance.md"
        if not acc.is_file():
            return []
        ac_ids = sorted(_known_ac_ids(acc.read_text(encoding="utf-8")))
        return [
            f"tracks.impl.{_ac_slug(ac_id)}"
            for ac_id in ac_ids
        ]

    def _write_reach_entries(self, vdir: Path | None = None) -> None:
        """Materialize ``.tracks/reach-entries.txt`` (empty-safe) so check_reach
        can see the Fake impl modules once Green files exist on the release
        branch. The Scaffold 宣言 bullet (_REACH_ENTRY_SCAFFOLD_LINE) declares
        it; ResultCheckpoint stages the committed artifact with the design trio."""
        lines = self._reach_entry_lines()
        if not lines:
            return
        reach_path = self.repo / _REACH_ENTRIES_RELATIVE
        reach_path.parent.mkdir(parents=True, exist_ok=True)
        reach_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _ac_coverage(self, token: str) -> str:
        """BS-06 section: every acceptance AC with a layer attribution.

        The ``test`` cell carries the full repo-relative path
        ``tests/unit/test_{slug}.py`` — the same path Fake Archer PLANNING
        writes into each task's ``test_refs`` (FR-0210: test_refs must trace
        back to test-plan §8)."""
        acc = self._design_vdir() / "acceptance.md"
        acs = (_ACC_ITEM.findall(acc.read_text(encoding="utf-8"))
               if acc.exists() else [])
        if token == "trace_orphan":
            acs = acs[:-1]
        rows = "\n".join(
            f"| {ac_id} | {SHIELD_LAYERS[i % len(SHIELD_LAYERS)]} | "
            f"tests/unit/test_{_ac_slug(ac_id)}.py "
            f"| IF-MTEST-001 |"
            for i, ac_id in enumerate(acs)
        )
        return (
            "\n## 8. AC Coverage\n\n"
            "| AC id | layer | test | IF |\n|---|---|---|---|\n"
            + rows + "\n"
        )

    def _revise_design(self, token: str,
                       scaffold: list[dict] | None = None) -> Path:
        """RESPOND: revise the committed trio (Prism comments incorporated);
        the docs must stay validate-clean for the new PRISM_REVIEW round."""
        scaffold = scaffold or []
        if token == "trace_orphan":
            return self._write_design(token, scaffold)
        vdir = self._design_vdir()
        self._design_revisions += 1
        note = (f"\n\nArcher revision {self._design_revisions}: addressed "
                "Prism review comments (fake deterministic revision).\n")
        for name in ("architecture.md", "interfaces.md", "test-plan.md"):
            path = vdir / name
            if not path.exists():  # reconcile-safe: rebuild a missing doc
                self._write_design(token, scaffold)
                return vdir
        self._ensure_design_scaffold(vdir, scaffold)
        for name in ("architecture.md", "interfaces.md", "test-plan.md"):
            path = vdir / name
            path.write_text(path.read_text(encoding="utf-8") + note,
                            encoding="utf-8")
            self._resolve_open_threads(path)
        return vdir

    # -- M-IMPL (Devon phase-separated RGR) -----------------------------------

    def _act_m_impl_devon(self, substate: str, assignment: dict) -> dict:
        """Return one deterministic Devon phase outcome without filesystem I/O."""
        error = self._devon_assignment_error(assignment)
        if error is not None:
            return self._devon_contract_failure(error)
        phase = assignment["phase"]
        token = self.token("devon", phase.upper(), "ok")
        if token in _DEVON_FAILURE_TOKENS or (
                token == "stub_token_failure" and phase != "red"):
            return self._devon_token_failure(assignment, phase, token)
        if phase == "refactor":
            return self._devon_refactor(assignment, token)
        if phase == "red":
            return self._devon_red(assignment, token)
        return self._devon_green(assignment, token)

    @staticmethod
    def _devon_assignment_error(assignment: dict) -> str | None:
        """Validate Devon's materialized assignment before deriving a patch."""
        for checker in (
            FakeBackend._devon_required_error,
            FakeBackend._devon_value_error,
            FakeBackend._devon_manifest_error,
        ):
            error = checker(assignment)
            if error is not None:
                return error
        return None

    @staticmethod
    def _devon_required_error(assignment: dict) -> str | None:
        required = (
            "task_id", "if_ids", "ac_refs", "test_refs", "commands",
            "manifest", "phase", "pre_dirty_snapshot", "result_identity",
        )
        missing = [key for key in required
                   if key not in assignment or assignment[key] is None]
        phase = assignment.get("phase")
        if phase not in _DEVON_PHASES:
            missing.append("phase")
        if phase in ("green", "refactor") and not assignment.get(
                "r_tree_identity"):
            missing.append("r_tree_identity")
        if missing:
            return "missing or invalid: " + ", ".join(dict.fromkeys(missing))
        return None

    @staticmethod
    def _devon_value_error(assignment: dict) -> str | None:
        phase = assignment["phase"]
        if not isinstance(assignment["task_id"], str) \
                or not assignment["task_id"].strip():
            return "task_id must be a non-empty string"
        for key in ("if_ids", "ac_refs", "test_refs"):
            if not FakeBackend._devon_string_list(assignment[key]):
                return f"{key} must be a non-empty string list"
        if not FakeBackend._devon_commands_valid(assignment["commands"]):
            return "commands must contain at least one command"
        snapshot = assignment["pre_dirty_snapshot"]
        if (not isinstance(snapshot, (dict, list, tuple, str))
                or isinstance(snapshot, str) and not snapshot.strip()
                or isinstance(snapshot, (list, tuple)) and not snapshot):
            return "pre_dirty_snapshot has an invalid type"
        if (not isinstance(assignment["result_identity"], str)
                or not assignment["result_identity"].strip()):
            return "result_identity must be a non-empty string"
        if phase in ("green", "refactor") and (
                not isinstance(assignment["r_tree_identity"], str)
                or not assignment["r_tree_identity"].strip()):
            return "r_tree_identity must be a non-empty string"
        return None

    @staticmethod
    def _devon_manifest_error(assignment: dict) -> str | None:
        manifest = assignment["manifest"]
        if not isinstance(manifest, dict):
            return "manifest must be an object"
        for key in ("allowed_paths", "forbidden_paths"):
            paths_value = manifest.get(key)
            if not FakeBackend._devon_manifest_paths_valid(paths_value):
                return f"manifest.{key} must be a non-empty path list"
        if not FakeBackend._devon_test_refs_valid(assignment["test_refs"]):
            return "test_refs must target repo-relative tests/unit paths"
        return None

    @staticmethod
    def _devon_string_list(value: object) -> bool:
        return isinstance(value, (list, tuple)) and bool(value) and all(
            isinstance(item, str) and item.strip()
            and "\n" not in item and "\r" not in item
            for item in value
        )

    @staticmethod
    def _devon_commands_valid(value: object) -> bool:
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, dict):
            return bool(value) and all(
                FakeBackend._devon_commands_valid(item)
                for item in value.values()
            )
        if isinstance(value, (list, tuple)):
            return bool(value) and all(
                FakeBackend._devon_commands_valid(item) for item in value
            )
        return False

    @staticmethod
    def _devon_manifest_paths_valid(value: object) -> bool:
        return isinstance(value, (list, tuple)) and bool(value) and all(
            FakeBackend._devon_manifest_path_valid(item) for item in value
        )

    @staticmethod
    def _devon_manifest_path_valid(value: object) -> bool:
        if not isinstance(value, str):
            return False
        path = value.strip().replace("\\", "/")
        return not (
            not path or path == "." or path.startswith("/")
            or any(char.isspace() for char in path)
            or "::" in path or ".." in path.split("/")
            or ("*" in path and not path.endswith("/**"))
        )

    @staticmethod
    def _devon_test_refs_valid(value: object) -> bool:
        if not FakeBackend._devon_string_list(value):
            return False
        return all(
            path.split("::", 1)[0].replace("\\", "/").startswith("tests/unit/")
            and ".." not in path.split("::", 1)[0].split("/")
            for path in value
        )

    @staticmethod
    def _devon_contract_failure(reason: str) -> dict:
        return {
            "status": "failed", "artifact_ref": None,
            "failure_class": "contract_error",
            "audit_evidence": f"contract_error: {reason}",
            "self_report": f"Devon assignment rejected: {reason}",
        }

    def _devon_token_failure(self, assignment: dict, phase: str,
                             token: str) -> dict:
        failure_class = "agent_failed" if token == "fail" else token
        if token == "hang":
            failure_class = "timeout"
        summary = f"simulated Devon {phase} failure: {token}"
        evidence = self._devon_evidence(
            assignment, phase, [],
            self._devon_commands(assignment, "", "fail", summary),
            [{"status": "fail", "classification": token,
              "output_summary": summary}],
            f"failure:{token}", [],
        )
        return {
            "status": "failed", "artifact_ref": None,
            "failure_class": failure_class, "audit_evidence": summary,
            "self_report": summary, **evidence,
        }

    def _devon_red(self, assignment: dict, token: str) -> dict:
        red_content, error = self._devon_red_content(assignment, token)
        if error is not None:
            return self._devon_contract_failure(error)
        test_path, body, verdict, summary = red_content
        existing_test, error = self._devon_existing_text(test_path)
        if error is not None:
            return self._devon_contract_failure(error)
        updated = append_text(existing_test, body)
        patch = unified_patch(test_path, existing_test, updated)
        evidence = self._devon_evidence(
            assignment, "red", [test_path],
            self._devon_commands(assignment, test_path, "fail", summary),
            [{"status": "fail", "classification": verdict,
              "output_summary": summary}],
            patch, list(assignment["if_ids"]),
        )
        result = {
            "status": "done", "artifact_ref": None, "diff_ref": patch,
            "self_report": f"fake Devon red produced {verdict}",
            "verdict": verdict, **evidence,
        }
        if token == "stub_token_failure":
            result["failure_class"] = "stub_token_failure"
        return result

    def _devon_red_content(
        self, assignment: dict, token: str,
    ) -> tuple[tuple[str, str, str, str] | None, str | None]:
        test_path, error = self._devon_test_path(assignment)
        if error is not None:
            return None, error
        production_path = self._devon_production_reference(assignment)
        existing, error = self._devon_existing_text(production_path)
        if error is not None:
            return None, error
        implementation = self._devon_implementation_text(
            existing, assignment["if_ids"][0],
        )
        if token == "stub_token_failure":
            body = self._devon_stub_test(assignment["if_ids"][0])
            verdict = "stub_token_failure"
            summary = (
                "stub_token_failure: NotImplementedError("
                f"\"{assignment['if_ids'][0]}\")"
            )
        else:
            body = self._devon_assertion_test(
                production_path, assignment["if_ids"][0], implementation,
            )
            verdict = "assertion_failure"
            summary = "assertion_failure: deterministic behavioral assertion"
        return (test_path, body, verdict, summary), None

    def _devon_green(self, assignment: dict, token: str) -> dict:
        production_path, error = self._devon_production_path(assignment)
        if error is not None:
            return self._devon_contract_failure(error)
        existing, error = self._devon_existing_text(production_path)
        if error is not None:
            return self._devon_contract_failure(error)
        updated = append_text(
            existing, self._devon_implementation_text(
                None, assignment["if_ids"][0],
            ),
        )
        patch = unified_patch(production_path, existing, updated)
        summary = f"pass: minimal implementation for {assignment['if_ids'][0]}"
        evidence = self._devon_evidence(
            assignment, "green", [production_path],
            self._devon_commands(assignment, production_path, "pass", summary),
            [{"status": "pass", "classification": "implementation",
              "output_summary": summary}],
            patch, list(assignment["if_ids"]),
        )
        return {
            "status": "done", "artifact_ref": None, "diff_ref": patch,
            "self_report": "fake Devon green produced minimal implementation",
            **evidence,
        }

    def _devon_refactor(self, assignment: dict, token: str) -> dict:
        reason = "no authorized behavior-preserving refactor is required"
        summary = f"pass: {reason}"
        evidence = self._devon_evidence(
            assignment, "refactor", [],
            self._devon_commands(assignment, "", "pass", summary),
            [{"status": "pass", "classification": "no_change",
              "output_summary": summary}],
            "no-change", list(assignment["if_ids"]), reason,
        )
        return {
            "status": "done", "artifact_ref": None,
            "self_report": f"fake Devon refactor: {reason}", **evidence,
        }

    # -- M-IMPL (Archer PLANNING; flow.md §10) --------------------------------

    @staticmethod
    def _is_m_impl_planning(role: str, substate: str) -> bool:
        """Archer PLANNING is the only M-IMPL substate the fake simulates."""
        return role == "archer" and substate == "PLANNING"

    def _act_m_impl_planning(self, assignment: dict | None = None) -> dict:
        """M-IMPL PLANNING: derive the implementation task graph from the
        frozen trio + design docs and write ``.tracks/projects/{version}/
        tasks.json`` as the Agent artifact (the Runtime never creates it).

        Required AC ids come from acceptance.md, valid IF ids from
        interfaces.md §5 IF Registry; the graph is one deterministic vertical
        slice per AC. ``archer:PLANNING`` failure tokens (``_FAILED_TOKENS``)
        return ``failed`` without writing a valid artifact. Output is a pure
        function of the doc contents (no clock/random), so the same docs
        yield byte-identical tasks.json.
        """
        token = self.token("archer", "PLANNING", "ok")
        if token in _FAILED_TOKENS:
            fclass = "agent_failed" if token == "fail" else token
            return {"status": "failed", "artifact_ref": None,
                    "failure_class": fclass,
                    "audit_evidence": f"simulated {fclass}",
                    "self_report": f"archer exit gate failed: {fclass}"}
        vdir = self._design_vdir()
        tasks_json, error = self._derive_task_graph(vdir)
        if error is not None:
            return {"status": "failed", "artifact_ref": None,
                    "failure_class": "invalid_taskgraph",
                    "audit_evidence": error,
                    "self_report": f"archer planning failed: {error}"}
        raw = json.dumps(tasks_json, indent=2) + "\n"
        (vdir / "tasks.json").write_text(raw, encoding="utf-8")
        return {"status": "done", "artifact_ref": str(vdir / "tasks.json"),
                "self_report": f"wrote {len(tasks_json['tasks'])} task(s) to "
                               "tasks.json",
                "audit_evidence": ("task graph derived from acceptance.md ACs "
                                   "and interfaces.md IF registry")}

    def _derive_task_graph(self, vdir: Path) -> tuple[dict | None, str | None]:
        """Build and self-validate the deterministic task graph.

        The Runtime taskgraph validators are imported lazily: importing
        ``tracks.executor`` at module scope would run ``executor/__init__.py``,
        which imports ``tracks.effects`` back into this package during import
        (effects <-> executor cycle). Function-level imports keep the effects
        boundary acyclic.
        """
        validators = self._taskgraph_validators()
        acc = vdir / "acceptance.md"
        ifc = vdir / "interfaces.md"
        if not acc.is_file():
            return None, "acceptance.md missing; cannot derive required ACs"
        if not ifc.is_file():
            return None, "interfaces.md missing; cannot derive IF registry"
        acs = sorted(validators["known_ac_ids"](acc.read_text(encoding="utf-8")))
        if not acs:
            return None, "acceptance.md contains no AC ids"
        registry = validators["extract_if_registry"](
            ifc.read_text(encoding="utf-8")
        )
        if registry is None:
            return None, "interfaces.md missing '## 5. IF Registry'"
        registry = sorted(registry)
        if not registry:
            return None, "interfaces.md §5 IF Registry is empty"
        data = self._taskgraph_data(acs, registry, validators["requirement_ref"])
        error = self._validate_taskgraph(data, acs, registry, validators)
        return (None, error) if error else (data, None)

    @staticmethod
    def _taskgraph_validators() -> dict:
        from tracks.executor.taskgraph import (  # noqa: PLC0415
            _requirement_ref,
            parse_tasks_json,
            validate_ac_coverage,
            validate_dag,
            validate_issue_numbers,
            validate_scope,
            validate_task_structure,
        )
        from tracks.executor.test_tasks import (  # noqa: PLC0415
            _extract_if_registry,
            _known_ac_ids,
        )
        return {
            "requirement_ref": _requirement_ref,
            "parse_tasks_json": parse_tasks_json,
            "validate_ac_coverage": validate_ac_coverage,
            "validate_dag": validate_dag,
            "validate_issue_numbers": validate_issue_numbers,
            "validate_scope": validate_scope,
            "validate_task_structure": validate_task_structure,
            "extract_if_registry": _extract_if_registry,
            "known_ac_ids": _known_ac_ids,
        }

    @staticmethod
    def _taskgraph_data(acs: list[str], registry: list[str], requirement_ref) -> dict:
        tasks = []
        for index, ac_id in enumerate(acs):
            if_id = registry[index % len(registry)]
            slug = _ac_slug(ac_id)
            tasks.append({
                "task_id": f"T-{index + 1:03d}",
                "issue_number": index + 1,
                "description": f"Implement {ac_id} as a vertical slice ({if_id})",
                "ac_refs": [ac_id], "fr_refs": [requirement_ref(ac_id)],
                "if_ids": [if_id],
                "test_refs": [f"tests/unit/test_{slug}.py::test_{slug}"],
                "scope_boundary": f"tracks/impl/{slug}.py, tests/unit/test_{slug}.py",
                "depends_on": [], "batch": "1", "parallel": False, "budget": 3,
            })
        return {"tasks": tasks}

    @staticmethod
    def _validate_taskgraph(data: dict, acs: list[str], registry: list[str],
                            validators: dict) -> str | None:
        raw = json.dumps(data, indent=2) + "\n"
        nodes, error = validators["parse_tasks_json"](raw)
        if error is not None:
            return error
        errors = validators["validate_task_structure"](nodes)
        valid, detail = validators["validate_dag"](nodes)
        if not valid:
            errors.append(detail)
        errors.extend(validators["validate_scope"](nodes)[1])
        errors.extend(validators["validate_ac_coverage"](nodes, acs, set(registry))[1])
        errors.extend(validators["validate_issue_numbers"](nodes)[1])
        return "; ".join(errors) if errors else None

    def _write_story(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        head, body = split_frontmatter(text)
        title = _story_title(body)
        lines = head.splitlines()
        for i, line in enumerate(lines):
            if not line.startswith("title:"):
                continue
            val = line.split(":", 1)[1].strip()
            if not val or val.startswith("{"):  # empty or unfilled placeholder
                lines[i] = f"title: {title}"
        head = "\n".join(lines) + "\n" if lines else ""
        # The M-START skeleton (templating.render_story_skeleton) already carries
        # every required section, so FakeAgent only fills the title (FR-150 gate).
        path.write_text(head + body, encoding="utf-8")

    def _write_spec(self, path: Path, token: str = "ok") -> None:
        if token == "scope_overflow":
            items = "\n".join(
                f"### FR-{i:04d} 需求 {i}\n\n- **来源**：story §目标\n"
                "- **交付入口**：无独立入口\n" for i in range(1, 32)
            )
            body = (
                f"# {self.version} — 功能规格（FakeAgent 过量草案）\n\n"
                "## 功能需求\n\n" + items
            )
            path.write_text(
                "---\nspec_id: SPEC-001\nstory_ref: S-001\nstatus: draft\nsha:\n---\n\n"
                + body,
                encoding="utf-8",
            )
            return
        if path.exists():
            return
        path.write_text(
            "---\n"
            "spec_id: SPEC-001\n"
            "created: 1970-01-01\n"
            "story_ref: S-001\n"
            "status: draft\n"
            "sha:\n"
            "---\n\n" + SPEC_TEMPLATE.format(version=self.version),
            encoding="utf-8",
        )

    def _write_acceptance(self, path: Path, token: str = "ok") -> None:
        """Deterministic acceptance draft: full AC coverage derived from the
        sibling spec.md (FR-0170). Token "trace_orphan" leaves the last spec
        item uncovered so the trace gate fails and re-dispatches."""
        spec = path.parent / "spec.md"
        items = _SPEC_ITEM.findall(spec.read_text(encoding="utf-8")) if spec.exists() else []
        if token == "trace_orphan":
            items = items[:-1]
        sections = "\n".join(
            f"## {iid} {title}\n\n"
            f"### AC-{iid.replace('-', '')}-01 外部可观察\n\n"
            f"- 条件：{title or iid} 可在系统外断言\n"
            for iid, title in items
        )
        path.write_text(
            "---\nacc_id: ACC-001\ncreated: 1970-01-01\nstatus: draft\nsha:\n---\n\n"
            f"# {self.version} — 验收标准（FakeAgent 草案）\n\n" + sections,
            encoding="utf-8",
        )
