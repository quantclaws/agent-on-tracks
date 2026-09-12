"""FakeBackend M-DESIGN document manufacturing (trio, scaffold, contracts).

Extracted from ``fake.py`` for module-size compliance (C0302). The
``FakeDesignMixin`` owns the canonical-template projections (story/spec/
acceptance/design trio) and the Fake fixture's scaffold + project contract.
"""

from __future__ import annotations

import re
from pathlib import Path

from tracks import paths, templating
from tracks.capabilities import supports_m_impl
from tracks.effects.backend import SHIELD_LAYERS
from tracks.effects.fake_shield import _ac_slug
from tracks.frontmatter import split_frontmatter

# spec items the fake acceptance draft must cover (FR-0170 trace)
_SPEC_ITEM = re.compile(r"^### (N?FR-\d{4})[ \t]*(.*)$", re.M)
# AC items the fake test-plan must attribute a test layer (BS-06 design trace)
_ACC_ITEM = re.compile(r"^### (AC-[A-Z0-9]+-\d+)\b", re.M)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_FENCE = re.compile(r"^\s*(```|~~~)")
_SCAFFOLD_HEADING = re.compile(r"^##\s+(?:\d+(?:\.\d+)*\.?\s+)?Scaffold 宣言\s*$", re.M)
_COVERAGE_HEADING = re.compile(r"^##\s+(?:\d+(?:\.\d+)*\.?\s+)?AC Coverage\s*$", re.M)
_CLOSURE_HEADING = re.compile(r"^###\s+1\.2\s+Required AC closure \(ISLAND_GATE_1\)\s*$", re.M)
_CLI_HEADING = re.compile(r"^##\s+(?:\d+(?:\.\d+)*\.?\s+)?CLI 接口合同\s*$", re.M)
_IF_REGISTRY = re.compile(r"^##\s+(?:\d+(?:\.\d+)*\.?\s+)?IF Registry\s*$", re.M)
_SCAFFOLD_RESERVED_ROOTS = frozenset({".git", ".opencode", ".tracks"})
# M-IMPL v0.5 reach declarations: the Fake M-DESIGN declares and materializes
# the reach entrypoint manifest as one canonical architecture Scaffold config
# artifact (ResultCheckpoint stages it like the project contract; reach.py
# reads the same path). Valid only for the Fake fixture — production designs
# never declare it, so production reach semantics are unchanged.
_REACH_ENTRIES_RELATIVE = ".tracks/reach-entries.txt"
_REACH_ENTRY_SCAFFOLD_LINE = "- .tracks/reach-entries.txt — Fake M-IMPL reach entrypoints (config)"

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



class FakeDesignMixin:
    """M-DESIGN trio writes + scaffold/contract materialization."""

    def _act_design(self, substate: str, token: str, scaffold: list[dict] | None = None) -> dict:
        # BS-03/Decision A: one assignment covers all three design docs.
        scaffold = scaffold or []
        if substate == "RESPOND":
            vdir = self._revise_design(token, scaffold)
            report = f"revised design trio ({token})"
        else:
            vdir = self._write_design(token, scaffold)
            report = f"wrote design trio ({token})"
        return {"status": "done", "artifact_ref": str(vdir), "self_report": report}

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
            interfaces = self._append_section_lines(interfaces, _CLI_HEADING, interface_lines)
        interfaces = self._append_section_lines(
            interfaces,
            _IF_REGISTRY,
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
            arch = self._append_section_lines(arch, _SCAFFOLD_HEADING, [_REACH_ENTRY_SCAFFOLD_LINE])
        if token == "template_broken":
            idx = max(i for i, ln in enumerate(arch.splitlines()) if ln.startswith("## "))
            arch = "\n".join(arch.splitlines()[:idx]) + "\n"
        (vdir / "architecture.md").write_text(arch, encoding="utf-8")
        (vdir / "interfaces.md").write_text(interfaces, encoding="utf-8")
        plan_body = self._drop_coverage_section(self._design_doc("test-plan"))
        (vdir / "test-plan.md").write_text(
            plan_body.rstrip("\n") + "\n" + self._ac_coverage(token), encoding="utf-8"
        )
        self._materialize_fake_scaffold(scaffold)
        self._write_project_contract()
        if self._reach_entries_supported():
            self._write_reach_entries(vdir)
        return vdir

    def _revise_design(self, token: str, scaffold: list[dict] | None = None) -> Path:
        """RESPOND: revise the committed trio (Prism comments incorporated);
        the docs must stay validate-clean for the new PRISM_REVIEW round."""
        scaffold = scaffold or []
        if token == "trace_orphan":
            return self._write_design(token, scaffold)
        vdir = self._design_vdir()
        self._design_revisions += 1
        note = (
            f"\n\nArcher revision {self._design_revisions}: addressed "
            "Prism review comments (fake deterministic revision).\n"
        )
        for name in ("architecture.md", "interfaces.md", "test-plan.md"):
            path = vdir / name
            if not path.exists():  # reconcile-safe: rebuild a missing doc
                self._write_design(token, scaffold)
                return vdir
        self._ensure_design_scaffold(vdir, scaffold)
        for name in ("architecture.md", "interfaces.md", "test-plan.md"):
            path = vdir / name
            path.write_text(path.read_text(encoding="utf-8") + note, encoding="utf-8")
            self._resolve_open_threads(path)
        return vdir

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

    def _drop_coverage_section(self, text: str) -> str:
        """Drop the template's ``## 8. AC Coverage`` section (heading onward);
        ``_ac_coverage`` regenerates the real machine-readable table with the
        actual acceptance AC rows, so the delivered doc carries exactly one."""
        m = _COVERAGE_HEADING.search(text)
        return text[: m.start()] if m else text

    def _write_project_contract(self) -> None:
        """Write a demo host test execution contract at
        ``.tracks/projects/project.toml`` (v0.4: pytest framework).

        D-41 atomic schema slice (interfaces §1m/§1n): flat ``[unit]`` /
        ``[integration]`` / ``[e2e]`` sections each declare collect/run/
        run_selected, every run/run_selected template embeds ``{result}``
        exactly once (run_selected also ``{nodes}``), and the optional-layer
        ``[nightly]`` contract declares the scheduled FULL regression. The
        worker/dist concurrency flags and the ``--junitxml={result}`` result
        flag are embedded in the command strings (Archer/machine-contract
        owned); Runtime only substitutes the declared placeholders and never
        injects flags itself.

        Declares non-empty ``[layout.devon]``/``[layout.shield]`` writable
        lists (FR-0120): the M-DESIGN EXIT gate (validate_layout) fails the
        architecture.md verdict when the layout contract is missing, so the
        fake Archer mirrors the real host Python layout the runtime
        authorizes. Devon is writable under ``tracks/``/``tests/unit/``;
        Shield's writable list covers the test-asset directories the fake
        Shield writes into AND the ``[e2e]`` path this contract itself
        declares — the complete Shield writable scope of the current tracks
        host project contract (``tests/integration/``, ``tests/e2e/``,
        ``tests/e2e_live/``, ``tests/assets/``, ``tests/counterexamples/``)."""
        toml_path = paths.project_toml_path(paths.tracks_home(self.repo))
        toml_path.parent.mkdir(parents=True, exist_ok=True)

        def section(name: str) -> str:
            return (
                f"[{name}]\n"
                'framework = "pytest"\n'
                f'paths = ["tests/{name}/"]\n'
                f"collect = \".venv/bin/python -m pytest --collect-only -q tests/{name}/\"\n"
                f"run = \".venv/bin/python -m pytest tests/{name}/ --tb=short -q "
                '--junitxml={result}"\n'
                "run_selected = \".venv/bin/python -m pytest {nodes} --tb=short -q "
                '--junitxml={result}"\n'
                'cwd = "."\n\n'
            )

        toml_path.write_text(
            section("unit")
            + section("integration")
            + section("e2e")
            + "[nightly]\n"
            + 'schedule = "0 3 * * *"\n'
            + 'workflow = ".github/workflows/nightly.yml"\n'
            + 'job = "nightly-regression"\n'
            + 'layers = ["unit", "integration", "e2e"]\n'
            + "purpose = \"current FULL suite (R1+R2) regression; result fetch future; "
            'not a local gate"\n\n'
            "[layout]\n\n"
            "[layout.devon]\n"
            'writable = ["tracks/", "tests/unit/"]\n\n'
            "[layout.shield]\n"
            'writable = ["tests/integration/", "tests/e2e/", "tests/e2e_live/", '
            '"tests/assets/", "tests/counterexamples/"]\n',
            encoding="utf-8",
        )
        # Review-1 pairing: a declared layer's collect command must see a
        # COLLECTABLE path. A fresh fixture host repo has no tests/ tree, so
        # pytest would exit rc=4 (file or directory not found) -- a broken
        # declaration that fails M-TEST entry closed -- instead of the legal
        # rc=5 empty layer. Materialize each declared layer directory with
        # the contract itself (git does not track empty dirs, but the
        # working-tree presence is what collect consumes).
        for name in ("unit", "integration", "e2e"):
            (self.repo / "tests" / name).mkdir(parents=True, exist_ok=True)

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
        from tracks.executor.test_tasks import (  # noqa: PLC0415
            _known_ac_ids,
            parse_test_tasks,
            resolve_inherited_baseline_docs,
        )

        acc, _ = resolve_inherited_baseline_docs(self._design_vdir() / "test-plan.md")
        if not acc.is_file():
            return []
        ac_ids = sorted(_known_ac_ids(acc.read_text(encoding="utf-8")))
        hotfix_dir = self._design_vdir()
        if "-hotfix-" in hotfix_dir.name:
            plan = hotfix_dir / "test-plan.md"
            if plan.is_file():
                ac_ids = sorted(
                    {
                        task["ac_id"]
                        for task in parse_test_tasks(acc, plan)
                        if task.get("ac_id")
                    }
                )
        return [f"tracks.impl.{_ac_slug(ac_id)}" for ac_id in ac_ids]

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

        The ``test`` cell carries the full repo-relative path in its declared
        integration/e2e layer. D-41 keeps task unit RED refs under Runtime R
        ownership; they are not inferred from §8.

        IF-HOTFIX-010: a hotfix version dir carries only the delta trio — the
        acceptance is inherited from the target baseline and resolved via
        ``resolve_inherited_baseline_docs`` (the same read-only resolver the
        file-level validators use, precedent ``_reach_entry_lines``)."""
        from tracks.executor.test_tasks import resolve_inherited_baseline_docs

        acc, _ifc = resolve_inherited_baseline_docs(self._design_vdir() / "test-plan.md")
        acs = _ACC_ITEM.findall(acc.read_text(encoding="utf-8")) if acc.exists() else []
        if token == "trace_orphan":
            acs = acs[:-1]
        rows = []
        for i, ac_id in enumerate(acs):
            layer = SHIELD_LAYERS[i % len(SHIELD_LAYERS)]
            rows.append(
                f"| {ac_id} | {layer} | tests/{layer}/test_{_ac_slug(ac_id)}.py "
                "| IF-MTEST-001 |"
            )
        return (
            "\n## 8. AC Coverage\n\n"
            "| AC id | layer | test | IF |\n|---|---|---|---|\n"
            + "\n".join(rows)
            + "\n"
        )

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

    def _validated_fake_scaffold(
        self,
        assignment: dict | None,
    ) -> tuple[list[dict], dict | None]:
        """Validate the test-only scaffold contract before any design write."""
        context = assignment.get("scenario_context") if isinstance(assignment, dict) else None
        if not isinstance(context, dict) or "fake_scaffold" not in context:
            return [], None
        raw_entries = context["fake_scaffold"]
        if not isinstance(raw_entries, list) or not raw_entries:
            return [], self._invalid_fake_scaffold("fake_scaffold must be a non-empty list")

        entries: list[dict] = []
        seen: set[str] = set()
        repo_root = self.repo.resolve()
        for index, raw_entry in enumerate(raw_entries):
            entry, reason = self._validate_fake_scaffold_entry(raw_entry, index, repo_root, seen)
            if reason is not None:
                return [], self._invalid_fake_scaffold(reason)
            assert entry is not None
            entries.append(entry)
        return entries, None

    def _validate_fake_scaffold_entry(
        self,
        raw_entry: object,
        index: int,
        repo_root: Path,
        seen: set[str],
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
        if (
            not isinstance(description, str)
            or not description.strip()
            or "\n" in description
            or "\r" in description
        ):
            return None, f"entry {index} description must be a single line"
        try:
            raw_path = Path(path)
            if (
                raw_path.is_absolute()
                or ".." in raw_path.parts
                or any(char.isspace() for char in path)
            ):
                raise ValueError("path must be a clean repo-relative path")
            resolved = (self.repo / raw_path).resolve(strict=False)
            relative = resolved.relative_to(repo_root)
        except (OSError, RuntimeError, ValueError):
            return None, f"entry {index} path escapes repository: {path!r}"
        if not relative.parts or relative.parts[0] in _SCAFFOLD_RESERVED_ROOTS:
            return None, f"entry {index} path is not allowed: {path!r}"
        canonical = str(relative)
        if canonical in seen:
            return None, f"duplicate path: {canonical}"
        candidate = self.repo / relative
        if self._has_symlink_component(relative) or candidate.is_dir():
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
    def _append_section_lines(text: str, heading: re.Pattern, lines: list[str]) -> str:
        """Append missing lines inside one existing level-2 section."""
        match = heading.search(text)
        if not match:
            return text
        rest = text[match.end() :]
        next_heading = re.search(r"^##\s", rest, re.M)
        section_end = match.end() + (next_heading.start() if next_heading else len(rest))
        section = text[match.end() : section_end]
        existing = set(section.splitlines())
        missing = [line for line in lines if line not in existing]
        if not missing:
            return text
        body = section.strip()
        if body:
            body += "\n\n"
        body += "\n".join(missing)
        tail = text[section_end:].lstrip("\n")
        return text[: match.end()] + "\n\n" + body + "\n\n" + tail

    @staticmethod
    def _scaffold_lines(scaffold: list[dict]) -> list[str]:
        return [f"- {entry['path']} — {entry['description']}" for entry in scaffold]

    @staticmethod
    def _interface_lines(scaffold: list[dict]) -> list[str]:
        if any(entry["path"] == "code-stats" for entry in scaffold):
            return ["- `./code-stats` -> IF-MTEST-001 (public CLI stub; implementation deferred)."]
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
                f"### FR-{i:04d} 需求 {i}\n\n- **来源**：story §目标\n- **交付入口**：无独立入口\n"
                for i in range(1, 32)
            )
            body = f"# {self.version} — 功能规格（FakeAgent 过量草案）\n\n## 功能需求\n\n" + items
            path.write_text(
                "---\nspec_id: SPEC-001\nstory_ref: S-001\nstatus: draft\nsha:\n---\n\n" + body,
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
