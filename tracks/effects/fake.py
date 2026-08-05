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
_LAYER_RE = re.compile(r"\b(unit|integration|e2e)\b", re.I)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_FENCE = re.compile(r"^\s*(```|~~~)")
_LAYERS = ("unit", "integration", "e2e")
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
            return self._act_draft(role, substate, doc, doc_path)
        verdict = self.token(role, substate, "pass")
        result = {"status": "done", "artifact_ref": None,
                  "self_report": f"review: {verdict}", "verdict": verdict}
        # D-29 triple ②: M-TEST Prism echoes the criteria-pack identity.
        if role == "prism" and substate == "PRISM_REVIEW":
            assigned_pack = (assignment or {}).get("criteria_pack")
            if assigned_pack:
                result["criteria_pack"] = dict(assigned_pack)
        return result

    def _act_draft(self, role: str, substate: str, doc: str | None,
                   doc_path: Path | None) -> dict:
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
            return self._act_design(substate, token)
        self._write_stage_doc(doc, doc_path, token)
        return {"status": "done", "artifact_ref": str(doc_path),
                "self_report": f"wrote {doc} ({token})"}

    def _act_design(self, substate: str, token: str) -> dict:
        # BS-03/Decision A: one assignment covers all three design docs.
        if substate == "RESPOND":
            vdir = self._revise_design(token)
            report = f"revised design trio ({token})"
        else:
            vdir = self._write_design(token)
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

    # -- M-TEST (Shield writes tests; FR-0020/FR-0120) ------------------------

    def _act_shield(self, substate: str, assignment: dict | None) -> dict:
        """Shield writes collectable, legit-Red test files for every required
        (integration|e2e) AC in the host project's test-plan, with long-format
        markers. Tokens: ``fail``/``over_reach`` simulate a failed outcome;
        ``illegit_red`` writes an ImportError-raising test (illegit Red);
        ``short_marker`` writes a short-format marker (trace gate fails)."""
        token = self.token("shield", substate, "ok")
        if token in _FAILED_TOKENS:
            return {"status": "failed", "artifact_ref": None,
                    "failure_class": "agent_failed" if token == "fail" else token,
                    "audit_evidence": f"simulated {token}",
                    "self_report": f"shield exit gate failed: {token}"}
        vdir = self._design_vdir()
        required = self._required_ac_layers(vdir)
        tests_dir = self.repo / "tests"
        for subdir in ("integration", "e2e", "assets", "counterexamples"):
            (tests_dir / subdir).mkdir(parents=True, exist_ok=True)
        for ac_id, layer in required:
            self._write_test_file(tests_dir, ac_id, layer, token)
        return {"status": "done", "artifact_ref": str(tests_dir),
                "self_report": f"wrote {len(required)} test files ({token})"}

    def _required_ac_layers(self, vdir: Path) -> list[tuple[str, str]]:
        """Return [(ac_id, layer)] for integration|e2e ACs from the host
        project's acceptance.md + test-plan.md AC Coverage section."""
        acc = vdir / "acceptance.md"
        plan = vdir / "test-plan.md"
        if not acc.exists() or not plan.exists():
            return []
        acs = _ACC_ITEM.findall(acc.read_text(encoding="utf-8"))
        plan_text = plan.read_text(encoding="utf-8")
        out: list[tuple[str, str]] = []
        for ac_id in acs:
            layer = self._layer_of(ac_id, plan_text)
            if layer in ("integration", "e2e"):
                out.append((ac_id, layer))
        return out

    @staticmethod
    def _layer_of(ac_id: str, plan_text: str) -> str | None:
        for ln in plan_text.splitlines():
            if ac_id in ln:
                m = _LAYER_RE.search(ln)
                if m:
                    return m.group(1).lower()
        return None

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
            out.append(line)
        return "\n".join(out) + "\n"

    def _write_design(self, token: str) -> Path:
        """Write architecture.md + interfaces.md + test-plan.md (one DRAFT
        covers all three). Tokens: template_broken drops architecture.md's
        last required section (template gate fails); trace_orphan leaves the
        last acceptance AC without a test-layer attribution (BS-06 fails)."""
        vdir = self._design_vdir()
        arch = self._design_doc("architecture")
        if token == "template_broken":
            idx = max(i for i, ln in enumerate(arch.splitlines())
                      if ln.startswith("## "))
            arch = "\n".join(arch.splitlines()[:idx]) + "\n"
        (vdir / "architecture.md").write_text(arch, encoding="utf-8")
        (vdir / "interfaces.md").write_text(self._design_doc("interfaces"),
                                            encoding="utf-8")
        (vdir / "test-plan.md").write_text(
            self._design_doc("test-plan") + self._ac_coverage(token),
            encoding="utf-8")
        return vdir

    def _ac_coverage(self, token: str) -> str:
        """BS-06 section: every acceptance AC with a layer attribution."""
        acc = self._design_vdir() / "acceptance.md"
        acs = (_ACC_ITEM.findall(acc.read_text(encoding="utf-8"))
               if acc.exists() else [])
        if token == "trace_orphan":
            acs = acs[:-1]
        rows = "\n".join(f"- {ac_id}: {_LAYERS[i % len(_LAYERS)]}"
                         for i, ac_id in enumerate(acs))
        return "\n## AC Coverage\n\n" + rows + "\n"

    def _revise_design(self, token: str) -> Path:
        """RESPOND: revise the committed trio (Prism comments incorporated);
        the docs must stay validate-clean for the new PRISM_REVIEW round."""
        if token == "trace_orphan":
            return self._write_design(token)
        vdir = self._design_vdir()
        self._design_revisions += 1
        note = (f"\n\nArcher revision {self._design_revisions}: addressed "
                "Prism review comments (fake deterministic revision).\n")
        for name in ("architecture.md", "interfaces.md", "test-plan.md"):
            path = vdir / name
            if not path.exists():  # reconcile-safe: rebuild a missing doc
                self._write_design(token)
                return vdir
            path.write_text(path.read_text(encoding="utf-8") + note,
                            encoding="utf-8")
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
