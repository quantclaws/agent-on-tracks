"""Executor layout gate integration (FR-0120, regression 8f7cf04).

``_do_validate_document`` must gate the Archer layout contract ONLY when the
doc is architecture.md during M-DESIGN: a missing/empty [layout.devon]/
[layout.shield] fails with check=layout, a valid contract passes, and
interfaces.md / test-plan.md (or architecture.md in any other stage) never
trigger the layout check.
"""

import pytest

from tests.integration.result_checkpoint_support import _init_workspace
from tracks import paths
from tracks.executor import Executor
from tracks.kernel.events import Command
from tracks.store import new_ulid


def _contract(layout_ok):
    body = (
        "\n".join(
            [
                "[integration]",
                'framework = "pytest"',
                'paths = ["tests/integration/"]',
                'collect = ".venv/bin/python -m pytest --collect-only -q tests/integration/"',
                'run = ".venv/bin/python -m pytest tests/integration/ --tb=short -q"',
                'cwd = "."',
            ]
        )
        + "\n"
    )
    if layout_ok:
        body += (
            "\n[layout]\n\n"
            "[layout.devon]\n"
            'writable = ["tracks/", "tests/unit/"]\n\n'
            "[layout.shield]\n"
            'writable = ["tests/integration/"]\n'
        )
    return body


def _run_validate(tmp_path, stage, doc, layout_ok=True):
    repo, home, store, run_id, vdir = _init_workspace(tmp_path, "v0.1")
    (vdir / doc).write_text("---\nsha:\n---\n\n# doc\n", encoding="utf-8")
    contract = paths.project_toml_path(home)
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text(_contract(layout_ok), encoding="utf-8")
    store.append(run_id, "v0.1", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.1", "stage.entered", {"stage": stage})
    ex = Executor(store, repo, run_id)
    cmd = Command(
        kind="validate_document", params={"doc": doc, "checks": []}, command_id=new_ulid()
    )
    ex._do_validate_document(cmd, store.state(run_id), None, False)
    return [e for e in store.events(run_id) if e.type in ("verdict.passed", "verdict.failed")]


def test_m_design_architecture_valid_layout_passes(tmp_path):
    verdicts = _run_validate(tmp_path, "M-DESIGN", "architecture.md", layout_ok=True)
    assert len(verdicts) == 1
    assert verdicts[0].type == "verdict.passed"


def test_m_design_architecture_missing_layout_fails(tmp_path):
    verdicts = _run_validate(tmp_path, "M-DESIGN", "architecture.md", layout_ok=False)
    assert len(verdicts) == 1
    failure = verdicts[0]
    assert failure.type == "verdict.failed"
    assert failure.payload["check"] == "layout"
    assert "[layout]" in failure.payload["reason"]


def test_m_design_interfaces_does_not_trigger_layout(tmp_path):
    verdicts = _run_validate(tmp_path, "M-DESIGN", "interfaces.md", layout_ok=False)
    assert len(verdicts) == 1
    assert verdicts[0].type == "verdict.passed"


def test_m_design_test_plan_does_not_trigger_layout(tmp_path):
    verdicts = _run_validate(tmp_path, "M-DESIGN", "test-plan.md", layout_ok=False)
    assert len(verdicts) == 1
    assert verdicts[0].type == "verdict.passed"


@pytest.mark.parametrize(
    "stage",
    [
        "M-STORY",
        "M-SPEC",
        "M-ACC",
        "M-REQ-APPROVAL",
        "M-TEST",
        "M-IMPL",
    ],
)
def test_architecture_in_other_stages_does_not_trigger_layout(tmp_path, stage):
    verdicts = _run_validate(tmp_path, stage, "architecture.md", layout_ok=False)
    assert len(verdicts) == 1
    assert verdicts[0].type == "verdict.passed"
