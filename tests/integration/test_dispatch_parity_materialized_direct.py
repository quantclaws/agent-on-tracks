"""IF-ENVELOPE-002: validate actual selected materials before external execution."""
import json
from pathlib import Path

import pytest

from tests.integration.test_opencode_backend import (
    DESIGN_DOCS,
    commit_trio,
    design_assignment,
)
from tests.integration.test_opencode_backend import design_vdir as design_vdir
from tests.integration.test_opencode_backend import fake_opencode as fake_opencode
from tracks import paths
from tracks.effects.fake import FakeBackend
from tracks.effects.opencode import OpencodeBackend
from tracks.executor.executor import Executor
from tracks.kernel.envelope import build_assignment_envelope
from tracks.kernel.events import Command
from tracks.store import Store

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("tamper", [None, "agent", "skill", "template", "fake_backend"])
def test_materialized_parity_gates_external_invocation_and_restores_user_files(
    host_repo, trac, design_vdir, fake_opencode, monkeypatch, tmp_path, tamper
):
    assert trac("init").returncode == 0
    assert trac("start", "v0.8", stdin="actual material parity").returncode == 0
    commit_trio(host_repo, [design_vdir / doc for doc in DESIGN_DOCS])
    human = host_repo / ".opencode/agents/Prism.md"
    human.parent.mkdir(parents=True, exist_ok=True)
    human.write_text("# Human-owned agent\n")
    original = human.read_bytes()
    marker = tmp_path / "external-invoked"
    script = Path(fake_opencode) / "opencode"
    text = script.read_text()
    script.write_text(text.replace(
        "import os, sys, json, time, signal",
        "import os, sys, json, time, signal\n"
        "import hashlib\n"
        "from pathlib import Path\n"
        "materials = ['.opencode/agents/Prism.md', "
        "'.opencode/skills/tracks-prism-design/SKILL.md', "
        "'.opencode/templates/architecture.md']\n"
        f"Path({str(marker)!r}).write_text(json.dumps(["
        "{'path': str(Path(p).resolve()), 'sha256': hashlib.sha256(Path(p).read_bytes()).hexdigest()} "
        "for p in materials]))",
        1,
    ))
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", "no_edit")
    monkeypatch.setenv("FAKE_OPENCODE_FINAL_TEXT", '```tracks-envelope\n{"envelope":{"kind":"prism:review","version":2},"payload":{"verdict":"pass"}}\n```')
    methods = {"agent": "_materialize", "skill": "_materialize_skill",
               "template": "_materialize_template"}
    if tamper in methods:
        original_method = getattr(OpencodeBackend, methods[tamper])

        def corrupt_after_materialization(self, *args, **kwargs):
            info = original_method(self, *args, **kwargs)
            assert info is not None
            dest = Path(info["dest"])
            material = dest.read_text()
            if "tracks-envelope:v2" in material:
                altered = material.replace("tracks-envelope:v2", "tracks-envelope:v999")
            elif material.startswith("---\n"):
                altered = material.replace("---\n", "---\nenvelope: tracks-envelope:v999\n", 1)
            else:
                altered = "---\nenvelope: tracks-envelope:v999\n---\n" + material
            dest.write_text(altered)
            return info

        monkeypatch.setattr(OpencodeBackend, methods[tamper], corrupt_after_materialization)
    if tamper == "fake_backend":
        monkeypatch.setattr(FakeBackend, "envelope_version", 999)
    assignment = design_assignment("PRISM_REVIEW")
    assignment.update(envelope=build_assignment_envelope("prism:review"), envelope_version=2,
                      skills=["tracks-prism-design"], template_kind="architecture")
    store = Store(paths.tracks_home(host_repo))
    try:
        run = store.active_run()
        executor = Executor(store, host_repo, run)
        executor.backend = OpencodeBackend(host_repo, "v0.2")
        cmd = Command(kind="dispatch_agent", params={"role": "prism", "substate": "PRISM_REVIEW"},
                      command_id="MATERIAL-PARITY")
        executor._dispatch_agent_backend(cmd, store.state(run), None, "prism", "PRISM_REVIEW",
                                         None, None, assignment)
        events = [e for e in store.events(run) if e.command_id == cmd.command_id]
        assert human.read_bytes() == original
        if tamper is None:
            assert marker.exists()
            parity = [e for e in events if e.type == "dispatch.parity"]
            assert len(parity) == 1
            audit = json.dumps(parity[0].payload, sort_keys=True)
            for material in json.loads(marker.read_text()):
                assert material["path"] in audit
                assert material["sha256"] in audit
            assert not any(e.type == "dispatch.rejected" for e in events)
        else:
            assert not marker.exists(), "invalid actual face must prevent external invocation"
            assert not any(e.type == "dispatch.parity" for e in events), (
                "failed material checks cannot publish a complete parity success"
            )
            rejected = [e for e in events if e.type == "dispatch.rejected"]
            assert rejected and rejected[-1].payload["reason"] == "version_parity_mismatch"
            assert not any(e.type == "outcome.received" for e in events)
    finally:
        store.close()
