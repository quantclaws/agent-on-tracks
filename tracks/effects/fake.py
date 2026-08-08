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

import os
import re
import time
from pathlib import Path

from tracks import paths, templating
from tracks.frontmatter import split_frontmatter

# spec items the fake acceptance draft must cover (FR-0170 trace)
_SPEC_ITEM = re.compile(r"^### (N?FR-\d{4})[ \t]*(.*)$", re.M)
# AC items the fake test-plan must attribute a test layer (BS-06 design trace)
_ACC_ITEM = re.compile(r"^### (AC-[A-Z0-9]+-\d+)\b", re.M)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_FENCE = re.compile(r"^\s*(```|~~~)")
_LAYERS = ("integration", "e2e")
_TASK_AC_ID = re.compile(r"^AC-(?:N?FR)\d{4}-\d{2}$")
_TASK_IF_ID = re.compile(r"^IF-[A-Z]+-\d{3}$")
_SCAFFOLD_HEADING = re.compile(
    r"^##\s+(?:\d+(?:\.\d+)*\.?\s+)?Scaffold 宣言\s*$", re.M
)
_CLI_HEADING = re.compile(
    r"^##\s+(?:\d+(?:\.\d+)*\.?\s+)?CLI 接口合同\s*$", re.M
)
_SCAFFOLD_RESERVED_ROOTS = frozenset({".git", ".opencode", ".tracks"})
# FR-0210 exit gate tokens: a failed agent run is not a produced document.
_FAILED_TOKENS = ("over_reach", "timeout", "no_target_diff",
                  "non_zero_exit", "json_truncated", "fail")

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


class FakeBackend:
    """Deterministic AgentBackend (v0.1 FakeAgent, relocated to effects/)."""

    def __init__(self, repo: Path, version: str):
        self.repo = repo
        self.version = version
        self._calls: dict = {}
        self._design_revisions = 0

    def token(self, key_left: str, key_right: str, default: str) -> str:
        key = f"{key_left}:{key_right}"
        seq = _simulate_map().get(key, default).split("|")
        n = self._calls.get(key, 0)
        self._calls[key] = n + 1
        return seq[min(n, len(seq) - 1)].strip()

    def act(self, role: str, substate: str, doc: str | None,
            doc_path: Path | None, assignment: dict | None = None) -> dict:
        if substate == "TRIAGE":
            return {"status": "done", "artifact_ref": None,
                    "self_report": "explored raw requirement"}
        if role == "shield":
            return self._act_shield(substate, assignment)
        if substate in ("DRAFT", "RESPOND"):
            return self._act_draft(role, substate, doc, doc_path, assignment)
        verdict = self.token(role, substate, "pass")
        result = {"status": "done", "artifact_ref": None,
                  "self_report": f"review: {verdict}", "verdict": verdict}
        # v0.5 ResultCheckpoint: non-pass verdicts must produce a diff
        # (discussion annotation) so the pipeline can checkpoint it.
        if verdict in ("revise", "comment"):
            annotate_path = doc_path
            if annotate_path is None and assignment and assignment.get("docs"):
                annotate_path = self._design_vdir() / assignment["docs"][0]
            if annotate_path and annotate_path.exists():
                self._annotate_review(annotate_path, role, verdict)
                result["diff_ref"] = "annotation"
        # D-29 triple ②: M-TEST Prism echoes the criteria-pack identity.
        if role == "prism" and substate == "PRISM_REVIEW":
            assigned_pack = (assignment or {}).get("criteria_pack")
            if assigned_pack:
                result["criteria_pack"] = dict(assigned_pack)
        return result

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

    # -- M-TEST (Shield writes tests; FR-0020/FR-0120) ------------------------

    def _act_shield(self, substate: str, assignment: dict | None) -> dict:
        """Shield writes collectable, legit-Red test files for every required
        (integration|e2e) AC in the host project's test-plan, with long-format
        markers. Tokens: ``fail``/``over_reach`` simulate a failed outcome;
        ``illegit_red`` writes an ImportError-raising test (illegit Red);
        ``short_marker`` writes a short-format marker (trace gate fails).

        D-28: the assignment's ``test_tasks`` (Runtime-parsed from test-plan
        §8) is the only input. Missing or malformed task data is a failed
        outcome; this backend never re-derives it from the design documents."""
        token = self.token("shield", substate, "ok")
        if token in _FAILED_TOKENS:
            return {"status": "failed", "artifact_ref": None,
                    "failure_class": "agent_failed" if token == "fail" else token,
                    "audit_evidence": f"simulated {token}",
                    "self_report": f"shield exit gate failed: {token}"}
        tasks = assignment.get("test_tasks") if isinstance(assignment, dict) else None
        if not self._valid_test_tasks(tasks):
            return {
                "status": "failed",
                "artifact_ref": None,
                "failure_class": "invalid_test_tasks",
                "audit_evidence": (
                    "assignment.test_tasks must be a non-empty list of "
                    "{ac_id, layers, if_ids}"
                ),
                "self_report": "Shield assignment.test_tasks is missing or malformed",
            }
        required = [(task["ac_id"], layer)
                    for task in tasks for layer in task["layers"]]
        tests_dir = self.repo / "tests"
        for subdir in ("integration", "e2e", "assets", "counterexamples"):
            (tests_dir / subdir).mkdir(parents=True, exist_ok=True)
        for ac_id, layer in required:
            self._write_test_file(tests_dir, ac_id, layer, token)
        return {"status": "done", "artifact_ref": str(tests_dir),
                "self_report": f"wrote {len(required)} test files ({token})"}

    @staticmethod
    def _valid_test_tasks(tasks: object) -> bool:
        if not isinstance(tasks, list) or not tasks:
            return False
        seen: set[str] = set()
        for task in tasks:
            if not isinstance(task, dict):
                return False
            ac_id = task.get("ac_id")
            layers = task.get("layers")
            if_ids = task.get("if_ids")
            if (not isinstance(ac_id, str) or not _TASK_AC_ID.fullmatch(ac_id)
                    or ac_id in seen):
                return False
            if (not isinstance(layers, list) or not layers
                    or any(layer not in _LAYERS for layer in layers)
                    or len(set(layers)) != len(layers)):
                return False
            if (not isinstance(if_ids, list) or not if_ids
                    or any(not isinstance(if_id, str)
                           or not _TASK_IF_ID.fullmatch(if_id)
                           for if_id in if_ids)
                    or len(set(if_ids)) != len(if_ids)):
                return False
            seen.add(ac_id)
        return True

    def _write_test_file(self, tests_dir: Path, ac_id: str, layer: str,
                         token: str) -> None:
        subdir = tests_dir / layer
        slug = ac_id.lower().replace("-", "_")
        fname = f"test_{slug}.py"
        # R-1 marker: standalone comment line directly above the test def,
        # mandatory TRACKS-TRACE token (FR-0080/FR-0130, revision log R-1).
        if token == "short_marker":
            marker = f"# {ac_id} TRACKS-TRACE short format (no @version)"
        else:
            marker = f"# {ac_id}@{self.version} TRACKS-TRACE {layer} test"
        if token == "illegit_red":
            # Import inside the test body so collection passes but the test
            # fails at runtime with ImportError (illegit Red -> DIAGNOSE).
            body = f"{marker}\ndef test_{slug}():\n"
            body += "    import nonexistent_module  # illegit Red\n"
        elif token == "mixed_red":
            # R1-01 per-section classification: integration layer fails with
            # a legit assertion_failure; e2e layer fails with an illegit
            # ImportError. Overall verdict must be invalid (DIAGNOSE).
            body = f"{marker}\ndef test_{slug}():\n"
            if layer == "integration":
                body += "    assert False  # legit Red (assertion_failure)\n"
            else:
                body += "    import nonexistent_module  # illegit Red\n"
        elif token == "pass_red":
            # Test passes instead of failing (unexpected pass -> DIAGNOSE).
            body = f"{marker}\ndef test_{slug}():\n"
            body += "    pass\n"
        else:
            token_stmt = 'NotImplementedError("IF-MTEST-001")'
            body = f"{marker}\ndef test_{slug}():\n"
            body += f"    raise {token_stmt}\n"
        (subdir / fname).write_text(body, encoding="utf-8")

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

    def _write_design(self, token: str, scaffold: list[dict] | None = None) -> Path:
        """Write architecture.md + interfaces.md + test-plan.md (one DRAFT
        covers all three). Tokens: template_broken drops architecture.md's
        last required section (template gate fails); trace_orphan leaves the
        last acceptance AC without a test-layer attribution (BS-06 fails)."""
        scaffold = scaffold or []
        vdir = self._design_vdir()
        arch = self._design_doc("architecture")
        if scaffold:
            arch = self._append_section_lines(
                arch, _SCAFFOLD_HEADING, self._scaffold_lines(scaffold)
            )
        if token == "template_broken":
            idx = max(i for i, ln in enumerate(arch.splitlines())
                      if ln.startswith("## "))
            arch = "\n".join(arch.splitlines()[:idx]) + "\n"
        (vdir / "architecture.md").write_text(arch, encoding="utf-8")
        interfaces = self._design_doc("interfaces")
        interface_lines = self._interface_lines(scaffold)
        if interface_lines:
            interfaces = self._append_section_lines(
                interfaces, _CLI_HEADING, interface_lines
            )
        (vdir / "interfaces.md").write_text(interfaces, encoding="utf-8")
        (vdir / "test-plan.md").write_text(
            self._design_doc("test-plan") + self._ac_coverage(token),
            encoding="utf-8")
        self._materialize_fake_scaffold(scaffold)
        self._write_project_contract()
        return vdir

    def _write_project_contract(self) -> None:
        """Write a demo host test execution contract at
        ``.tracks/project/project.toml`` (v0.4: pytest framework)."""
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

    def _ac_coverage(self, token: str) -> str:
        """BS-06 section: every acceptance AC with a layer attribution."""
        acc = self._design_vdir() / "acceptance.md"
        acs = (_ACC_ITEM.findall(acc.read_text(encoding="utf-8"))
               if acc.exists() else [])
        if token == "trace_orphan":
            acs = acs[:-1]
        rows = "\n".join(
            f"| {ac_id} | {_LAYERS[i % len(_LAYERS)]} | "
            f"test_{ac_id.lower().replace('-', '_')} | IF-MTEST-001 |"
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
