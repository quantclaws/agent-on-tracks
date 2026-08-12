"""Requirement baseline digest + preview summary (FR-0190, design D-01).

`revision_digest` is pure content addressing: sha256 over the three doc body
shas joined with fixed labels in fixed order (story -> spec -> acc). Bodies are
frontmatter-stripped (`doc_body_sha`) so sealing a sha never self-invalidates,
and the labelled join removes concatenation-boundary collisions. No timestamps
participate, so staleness is replayable from content alone (D-02/D-03).

`m_impl_baseline_digest` extends the trio to cover design docs, project
contract, approval/issue evidence, and frozen test paths (flow.md §10 BASELINE).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from tracks.frontmatter import doc_body_sha, split_frontmatter

_TRIO = (("story", "story.md"), ("spec", "spec.md"), ("acc", "acceptance.md"))
_DESIGN_DOCS = (
    ("architecture", "architecture.md"),
    ("interfaces", "interfaces.md"),
    ("test_plan", "test-plan.md"),
)
_SPEC_ITEM = re.compile(r"^### (?:FR|NFR)-\d{4}\b", re.M)
_AC_ITEM = re.compile(r"^### AC-N?FR\d{4}-\d+\b", re.M)


def revision_digest(vdir: Path) -> str:
    joined = "\n".join(f"{label}:{doc_body_sha(vdir / doc)}" for label, doc in _TRIO)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def m_impl_baseline_digest(
    vdir: Path,
    repo: Path,
    *,
    approval_digest: str = "",
    issue_evidence: str = "",
    frozen_test_paths: list[str] | None = None,
    branch: str = "",
    tip: str = "",
    design_checkpoint: str = "",
) -> str:
    """M-IMPL baseline digest (flow.md §10 BASELINE): sha256 over trio + design
    docs + project contract + approval/issue evidence + frozen test paths,
    bound to the current branch name, branch tip, and M-DESIGN checkpoint
    commit identity (identity binding, D-05).

    No clock/timestamps participate; the digest is replayable from content
    alone. Missing design docs are labelled ``missing`` so their absence is
    detectable (a valid M-IMPL entry requires all six docs to exist)."""
    parts: list[str] = []
    for label, doc in (*_TRIO, *_DESIGN_DOCS):
        parts.append(f"{label}:{_doc_digest(vdir / doc)}")
    from tracks import paths as _paths
    contract_path = _paths.project_toml_path(_paths.tracks_home(repo))
    parts.append(f"contract:{_file_digest(contract_path)}")
    parts.append(f"approval:{approval_digest or 'missing'}")
    parts.append(f"issues:{_canonical_evidence(issue_evidence) or 'missing'}")
    frozen_test_paths = sorted(frozen_test_paths or [])
    parts.append("frozen_tests:" + _frozen_paths_digest(repo, frozen_test_paths))
    parts.append(f"branch:{branch or 'missing'}")
    parts.append(f"tip:{tip or 'missing'}")
    parts.append(f"design_checkpoint:{design_checkpoint or 'missing'}")
    joined = "\n".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def m_impl_baseline_missing(
    vdir: Path,
    repo: Path,
    *,
    approval_digest: str = "",
    issue_evidence: str = "",
    frozen_test_paths: list[str] | None = None,
    contract_valid: bool = True,
    branch: str = "",
    tip: str = "",
    design_checkpoint: str = "",
) -> tuple[str, ...]:
    """Return deterministic M-IMPL baseline freshness failures.

    The caller supplies the result of parsing the host contract so this module
    stays independent from the project-contract loader.  Missingness is part
    of the result, rather than being inferred from an omitted digest field.
    """
    missing: list[str] = []
    for _, doc in (*_TRIO, *_DESIGN_DOCS):
        if not (vdir / doc).is_file():
            missing.append(doc)
    if not contract_valid:
        missing.append(".tracks/project/project.toml")
    if not approval_digest:
        missing.append("approval.recorded")
    if not issue_evidence:
        missing.append("issues.created")
    if not branch:
        missing.append("branch.name")
    if not tip:
        missing.append("branch.tip")
    if not design_checkpoint:
        missing.append("design.checkpointed")
    for path in sorted(frozen_test_paths or []):
        if not (repo / path).exists():
            missing.append(path)
    return tuple(missing)


def m_impl_baseline_summary(vdir: Path) -> str:
    """Human-readable summary of the M-IMPL baseline inputs."""
    parts: list[str] = [baseline_summary(vdir)]
    for _, doc in _DESIGN_DOCS:
        path = vdir / doc
        if path.exists():
            _, body = split_frontmatter(path.read_text(encoding="utf-8"))
            parts.append(f"{doc}: {_title(body)}")
        else:
            parts.append(f"{doc}: missing")
    return "; ".join(parts)


def _title(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return "untitled"


def baseline_summary(vdir: Path) -> str:
    parts = []
    for _, doc in _TRIO:
        path = vdir / doc
        if not path.exists():
            parts.append(f"{doc}: missing")
            continue
        _, body = split_frontmatter(path.read_text(encoding="utf-8"))
        entry = f"{doc}: {_title(body)}"
        if doc == "spec.md":
            entry += f" [{len(_SPEC_ITEM.findall(body))} FR/NFR]"
        elif doc == "acceptance.md":
            entry += f" [{len(_AC_ITEM.findall(body))} AC]"
        parts.append(entry)
    return "; ".join(parts)


def _doc_digest(path: Path) -> str:
    if not path.is_file():
        return "missing"
    try:
        return doc_body_sha(path)
    except (OSError, UnicodeDecodeError):
        return "unreadable"


def _file_digest(path: Path) -> str:
    if not path.is_file():
        return "missing"
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "unreadable"


def _frozen_paths_digest(repo: Path, paths: list[str]) -> str:
    entries: list[str] = []
    for raw in paths:
        path = repo / raw
        if path.is_file():
            entries.append(f"{raw}:file:{_file_digest(path)}")
            continue
        if not path.is_dir():
            entries.append(f"{raw}:missing")
            continue
        files = [candidate for candidate in sorted(path.rglob("*"))
                 if candidate.is_file() and not any(
                     part in {"__pycache__", ".pytest_cache", ".mypy_cache"}
                     for part in candidate.relative_to(path).parts)]
        if not files:
            entries.append(f"{raw}:empty")
            continue
        for candidate in files:
            rel = candidate.relative_to(repo)
            entries.append(f"{rel}:file:{_file_digest(candidate)}")
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def _canonical_evidence(value: object) -> str:
    if value in (None, "", {}, [], ()):
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
