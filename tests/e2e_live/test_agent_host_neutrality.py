"""Live host-neutrality canary (b91 Q6): four roles dispatched against two
non-Python host profiles with a real model.

Opt-in and skip-by-default: every test resolves ``live_backend`` (which skips
with ``LIVE_SKIPPED`` when ``TRAC_LIVE_PROVIDER/MODEL/BASE_URL/API_KEY`` is
absent) and runs through the installed-wheel ``InstalledBackend``. The host
toolchain (node-style vs go-style) exists ONLY as fixture contract/assignment
data — the machine does not need node/go installed.

Assertions are machine-observable only (never prose, never text equivalence):
outcome classification/events, the written file set, diff scope against the
assignment manifest, and evidence/manifest consumption through the same
runtime validators the executor uses. Structured REVISE outcomes are legal;
format failures and over-reach are not.
"""

from __future__ import annotations

import subprocess

import pytest

from tracks.executor.m_impl_runtime import (
    _devon_path_scope_error,
    _m_impl_red_classification_error,
)
from tracks.kernel.contracts import WRITE_MANIFEST_CONTRACT

DESIGN_DOCS = ("architecture.md", "interfaces.md", "test-plan.md")
M_IMPL_CONTEXT_DOCS = (
    "story.md",
    "spec.md",
    "acceptance.md",
    "architecture.md",
    "interfaces.md",
    "test-plan.md",
)

# Host profiles live ONLY here (fixture contract data). No test below requires
# the named toolchain to be installed on the machine.
HOST_PROFILES = {
    "node": {
        "language": "typescript",
        "framework": "node",
        "run_command": "npm test",
        "test_dir": "test/",
    },
    "go": {
        "language": "go",
        "framework": "go",
        "run_command": "go test ./...",
        "test_dir": "tests/",
    },
}

# Backend result protocol keys (IF-003 outcome + D-35 review + Devon evidence).
OUTCOME_KEYS = {
    "status",
    "artifact_ref",
    "self_report",
    "diff_ref",
    "audit_evidence",
    "failure_class",
    "agent_io",
    "verdict",
    "discussion_evidence",
    "materialization_evidence",
    "artifact_manifest",
    "suggested_commit_message",
    "criteria_pack",
    "review_summary",
    "findings",
    "review_body",
    "defect_classification",
    "diagnosis",
    "phase",
    "changed_paths",
    "commands",
    "results",
    "manifest_compliance",
    "pre_identity",
    "post_identity",
    "r_identity",
    "no_change_reason",
    "implemented_if_ids",
    "result_identity",
    "advisories",
}

OVERREACH_CLASSES = {"over_reach", "undeclared_scaffold"}
FORMAT_CLASSES = {"manifest_malformed", "revise_without_findings"}
INFRA_CLASSES = {"provider_unavailable", "timeout", "filesystem", "abnormal_step_finish"}


def _outcome_protocol(out: dict, label: str) -> dict:
    """Protocol sanity only — classification and key shape, never agent text."""
    assert out.get("status") in ("done", "failed"), f"{label}: {out.get('status')}"
    unexpected = set(out) - OUTCOME_KEYS
    assert not unexpected, f"{label}: unexpected outcome keys {sorted(unexpected)}"
    if out["status"] == "failed":
        failure_class = out.get("failure_class")
        assert failure_class, f"{label}: failed outcome must carry a failure_class"
        assert failure_class not in OVERREACH_CLASSES, (
            f"{label}: over-reach is never a legal live outcome ({failure_class})"
        )
        assert failure_class not in FORMAT_CLASSES, (
            f"{label}: format failure is never a legal live outcome ({failure_class})"
        )
        if failure_class in INFRA_CLASSES:
            pytest.skip(f"LIVE_SKIPPED: infra failure {failure_class} during {label}")
    return out


def _changed_paths(repo) -> set[str]:
    """Repo-relative paths added/modified/untracked after a dispatch."""
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    paths = set()
    for line in status.splitlines():
        if not line.strip():
            continue
        prefix, path = line[:2], line[3:].strip()
        if prefix == "??":
            paths.add(path.rstrip("/"))
        else:
            paths.add(path.strip('"'))
    return paths


def _assert_written_within(repo, allowed_dirs: tuple[str, ...], label: str) -> None:
    """Every written path must stay inside the assignment-declared scope."""
    allowed = tuple(d.rstrip("/") + "/" for d in allowed_dirs)
    for path in sorted(_changed_paths(repo)):
        assert any(path == d.rstrip("/") or path.startswith(d) for d in allowed), (
            f"{label}: write escaped the assignment scope: {path}"
        )


@pytest.fixture(params=sorted(HOST_PROFILES), ids=lambda p: f"{p}-style")
def neutral_host(live_backend, request):
    """A disposable live host whose project contract + layout declare the
    profile toolchain; no node/go binary is required."""
    host = live_backend.host
    profile = HOST_PROFILES[request.param]
    projects = host / ".tracks" / "projects"
    projects.mkdir(parents=True, exist_ok=True)
    (projects / "project.toml").write_text(
        f"[contract]\n"
        f'language = "{profile["language"]}"\n'
        f'framework = "{profile["framework"]}"\n'
        f'run = "{profile["run_command"]}"\n'
        f"\n[layout]\n"
        f"\n[layout.devon]\n"
        f'writable = ["{profile["test_dir"]}"]\n'
        f"\n[layout.shield]\n"
        f'writable = ["{profile["test_dir"]}"]\n',
        encoding="utf-8",
    )
    vdir = projects / "v0.2"
    vdir.mkdir(parents=True, exist_ok=True)
    for name in (*DESIGN_DOCS, *M_IMPL_CONTEXT_DOCS):
        (vdir / name).write_text(
            f"---\nname: {name}\nversion: 0.1\n---\n\n# {name.removesuffix('.md')}\n\nTBD\n",
            encoding="utf-8",
        )
    (host / ".gitignore").write_text(".opencode/\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=host, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "neutral host fixture"], cwd=host, check=True, capture_output=True
    )
    return host, profile


def test_live_archer_and_prism_host_neutral(neutral_host, live_backend, steps):
    """Archer authors the design trio and Prism reviews it on a non-Python
    host contract: the host-neutral prompts must drive real dispatches whose
    writes stay inside the declared doc set."""
    host, profile = neutral_host
    docs = list(DESIGN_DOCS)
    steps.step("prepared neutral host", framework=profile["framework"])

    author = live_backend.act(
        "archer",
        "DRAFT",
        None,
        None,
        {
            "kind": "DRAFT",
            "template_kind": None,
            "templates": [d.removesuffix(".md") for d in docs],
            "skills": ["tracks-discuz", "tracks-archer-design", "tracks-quality-guards"],
            "docs": docs,
        },
    )
    steps.step(
        "archer DRAFT dispatch",
        status=author["status"],
        failure_class=author.get("failure_class"),
    )
    _outcome_protocol(author, f"archer/DRAFT[{profile['framework']}]")
    if author["status"] == "done":
        assert author.get("diff_ref"), "done author dispatch must carry the target diff"
    _assert_written_within(host, (".tracks/", ".opencode/"), f"archer[{profile['framework']}]")

    reviewer = live_backend.act(
        "prism",
        "PRISM_REVIEW",
        None,
        None,
        {
            "kind": "PRISM_REVIEW",
            "skills": ["tracks-discuz", "tracks-prism-design"],
            "docs": docs,
        },
    )
    steps.step(
        "prism PRISM_REVIEW dispatch",
        status=reviewer["status"],
        failure_class=reviewer.get("failure_class"),
    )
    _outcome_protocol(reviewer, f"prism/PRISM_REVIEW[{profile['framework']}]")
    if reviewer["status"] == "done":
        assert reviewer.get("verdict") in ("pass", "revise"), reviewer.get("verdict")
    _assert_written_within(host, (".tracks/", ".opencode/"), f"prism[{profile['framework']}]")


def test_live_devon_and_shield_host_neutral(neutral_host, live_backend, steps):
    """Devon RED and Shield WRITE on a non-Python host contract: evidence and
    artifact manifest are consumed through the runtime validators, and every
    write stays inside the assignment manifest / declared layout."""
    host, profile = neutral_host
    test_dir = profile["test_dir"]
    steps.step("prepared neutral host", framework=profile["framework"])

    red = live_backend.act(
        "devon",
        "RED",
        None,
        None,
        {
            "kind": "RED",
            "phase": "red",
            "skills": ["tracks-devon-rgr"],
            "task_id": "T-001",
            "if_ids": ["IF-MTEST-001"],
            "ac_refs": ["AC-FR0001-01"],
            "test_refs": [f"{test_dir}test_widget.py::test_widget"],
            "commands": [profile["run_command"]],
            "manifest": {
                "allowed_paths": [f"{test_dir.rstrip('/')}/**"],
                "forbidden_paths": [".tracks/projects/**"],
            },
            "pre_dirty_snapshot": {},
            "result_identity": "result-1",
        },
    )
    steps.step("devon RED dispatch", status=red["status"], failure_class=red.get("failure_class"))
    _outcome_protocol(red, f"devon/RED[{profile['framework']}]")
    if red["status"] == "done":
        error = _m_impl_red_classification_error(red)
        assert error is None, f"runtime rejected live Devon RED evidence: {error}"
        scope_error = _devon_path_scope_error(
            red.get("changed_paths") or [],
            allowed={f"{test_dir.rstrip('/')}/**"},
            forbidden={".tracks/projects/**"},
            devon_test_dirs=[test_dir.rstrip("/")],
        )
        assert scope_error is None, f"live Devon RED wrote outside manifest: {scope_error}"
    _assert_written_within(
        host, (test_dir, ".tracks/", ".opencode/"), f"devon[{profile['framework']}]"
    )

    writer = live_backend.act(
        "shield",
        "WRITE",
        None,
        None,
        {
            "kind": "WRITE",
            "skills": ["tracks-discuz", "tracks-shield"],
            "docs": list(M_IMPL_CONTEXT_DOCS),
            "manifest_contract": dict(WRITE_MANIFEST_CONTRACT),
            "test_tasks": [
                {"ac_id": "AC-FR0001-01", "layers": ["integration"], "if_ids": ["IF-MTEST-001"]}
            ],
        },
    )
    steps.step(
        "shield WRITE dispatch",
        status=writer["status"],
        failure_class=writer.get("failure_class"),
    )
    _outcome_protocol(writer, f"shield/WRITE[{profile['framework']}]")
    if writer["status"] == "done":
        manifest = writer.get("artifact_manifest") or {}
        includes = manifest.get("artifact_manifest", manifest).get("include") or []
        for entry in includes:
            path = entry["path"]
            assert path.startswith(test_dir), (
                f"shield manifest path outside the declared layout: {path}"
            )
            assert (host / path).is_file(), f"manifest path not on disk: {path}"
    _assert_written_within(
        host, (test_dir, ".tracks/", ".opencode/"), f"shield[{profile['framework']}]"
    )
