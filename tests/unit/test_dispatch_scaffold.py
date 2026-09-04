"""T-042 dispatch scaffolds: parity gate + failure-evidence injection seam.

Run 01M19FJVES7G113RD8QXXY3PQZ died twice on AttributeError at dispatch:
the in-flight wiring called _dispatch_parity_ok / _inject_failure_evidence
without definitions. These scaffolds keep the pipeline alive (True /
identity) until the real T-001 face (E) semantics land.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tests.unit.helpers import git_repo
from tracks.executor.executor import Executor
from tracks.kernel.events import Command
from tracks.store import Store


def _bare_executor(tmp_path: Path) -> Executor:
    repo = git_repo(tmp_path, gitignore=True)
    store = Store(paths_home(repo))
    return Executor(store, repo, "RUN")


def paths_home(repo: Path) -> Path:
    from tracks import paths

    return paths.tracks_home(repo)


def test_dispatch_parity_ok_defaults_true(tmp_path):
    ex = _bare_executor(tmp_path)
    cmd = Command(kind="dispatch_agent", params={"role": "devon", "substate": "RED"})
    assert ex._dispatch_parity_ok(cmd, "T-001", {"phase": "red"}) is True


def test_inject_failure_evidence_identity(tmp_path):
    ex = _bare_executor(tmp_path)
    assignment = {"phase": "green", "task_id": "T-001"}
    state = SimpleNamespace(current_task_id="T-001")
    cmd = Command(kind="dispatch_agent", params={"role": "devon", "substate": "GREEN"})
    out = ex._inject_failure_evidence(assignment, "devon", state, "T-001", cmd)
    assert out is assignment


def test_dispatch_agent_backend_no_attribute_error(tmp_path):
    """The exact production crash: a real dispatch must not die on the
    undefined scaffolds before reaching the backend."""
    ex = _bare_executor(tmp_path)

    class _Backend:
        def act(self, *args, **kwargs):
            return {"status": "failed", "failure_class": "agent_error", "self_report": "bounded"}

    ex.backend = _Backend()
    cmd = Command(
        kind="dispatch_agent",
        params={"role": "devon", "substate": "RED", "assignment": {"phase": "red"}},
    )
    state = SimpleNamespace(
        stage="M-IMPL",
        substate="RED",
        current_task_id="T-001",
        current_attempt=0,
        current_manifest={"allowed_paths": ["tracks/"]},
        hotfix_issue=None,
        doc_gaps={},
    )
    try:
        ex._do_dispatch_agent(cmd, state, "T-001", False)
    except AttributeError as exc:
        raise AssertionError(f"dispatch died on scaffold: {exc}") from exc


def test_format_error_shortcircuit_defaults_false(tmp_path):
    ex = _bare_executor(tmp_path)
    cmd = Command(kind="dispatch_agent", params={"role": "devon", "substate": "RED"})
    result = {"status": "done", "self_report": "ok"}
    assert ex._format_error_shortcircuit(result, cmd, "T-001") is False


def test_done_path_dispatch_end_to_end_no_attribute_error(tmp_path):
    """End-to-end smoke for the done path: a reviewer (non-writer) dispatch
    whose backend returns status=done must traverse the full handler —
    including the 2397 format-error seam — without any scaffold
    AttributeError, and emit outcome.received(status=done)."""
    from tests.unit.helpers import m_impl_started_task
    from tracks.effects.fake import FakeBackend

    class _DoneFake(FakeBackend):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.calls = 0

        def act(self, *a, **kw):
            self.calls += 1
            return {"status": "done", "self_report": "done verdict", "verdict": "pass"}

    repo = git_repo(tmp_path, gitignore=True)
    store, task = m_impl_started_task(repo)
    fake = _DoneFake(repo, "v0.5")
    ex = Executor(store, repo, "RUN")
    ex.backend = fake
    cmd = Command(
        "dispatch_agent", {"role": "prism", "substate": "PRISM_FINAL"}, command_id="C-PF"
    )
    try:
        ex._dispatch_agent_backend(
            cmd, store.state("RUN"), task["task_id"], "prism", "PRISM_FINAL", None, None, {}
        )
    except AttributeError as exc:
        raise AssertionError(f"done-path dispatch died on scaffold: {exc}") from exc
    assert fake.calls == 1
    outs = [e for e in store.events("RUN") if e.type == "outcome.received"]
    assert outs and outs[-1].payload.get("status") == "done"
