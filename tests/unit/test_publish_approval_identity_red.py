"""M-VERIFY repair RED: the publish approval reads the human actor KIND.

Live block (candidate c721298, M-PUBLISH): the v0.11 UI release decision
audits the effective display name as its actor (v0.10 §1m.3) — the
publish approval compared that name against the CLI's historic literal
``human`` and answered ``publish.failed reason=not_approved``, so the
authorized release could never publish (the decider parks by design:
"a fix means a new preview/decision"). The release.decided payload now
carries ``actor_kind`` (the command's actor class; the CLI face defaults
``human``) and the approval accepts the kind for the named case.

AC: IF-PUBLISH-001 / v0.10 §1m.3 — a human release decision authorizes
the publish on every channel; an agent identity never does.
"""

from __future__ import annotations

from types import SimpleNamespace

from tracks.executor import publish_runtime as pr
from tracks.supervisor import gate_exec


def _ev(seq: int, etype: str, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(seq=seq, type=etype, payload=payload)


def _preview(seq: int = 1, digest: str = "d") -> SimpleNamespace:
    return _ev(seq, "release.previewed", {"preview_digest": digest})


def _decided(actor: str, *, kind: str | None = None, seq: int = 2) -> SimpleNamespace:
    payload = {
        "candidate_sha": "c" * 40,
        "preview_digest": "d",
        "action": "release",
        "actor": actor,
    }
    if kind is not None:
        payload["actor_kind"] = kind
    return _ev(seq, "release.decided", payload)


def test_named_human_decision_is_approved():
    """The web decision's named actor + human kind authorizes the publish."""
    preview = _preview()
    assert pr._approval_error(
        [preview, _decided("Aaron", kind="human")], preview, "d", "c" * 40
    ) is None, (
        "a named human decision (actor_kind=human, §1m.3) must authorize the"
        " publish — the literal-name comparison blocked the v0.11 UI release"
    )


def test_named_decision_without_kind_stays_closed():
    """Fail closed: a bare name with no class proof is not a human approval."""
    preview = _preview()
    assert pr._approval_error(
        [preview, _decided("Aaron")], preview, "d", "c" * 40
    ) == "not_approved"


def test_agent_kind_stays_rejected():
    """An agent identity never authorizes the publish."""
    preview = _preview()
    assert pr._approval_error(
        [preview, _decided("devon", kind="agent")], preview, "d", "c" * 40
    ) == "not_approved"


def test_cli_literal_decision_still_approved():
    """The CLI face's historic literal keeps authorizing (unchanged)."""
    preview = _preview()
    assert pr._approval_error(
        [preview, _decided("human")], preview, "d", "c" * 40
    ) is None


def test_worker_gate_execution_threads_the_actor_kind():
    """gate_exec hands the command's actor class to the release core."""
    seen: dict = {}

    def recorder(repo, run_id, params, actor, actor_kind="human"):
        seen.update({"actor": actor, "actor_kind": actor_kind})
        return None, None

    original = gate_exec._HANDLERS.get("record_release_decision")
    gate_exec._HANDLERS["record_release_decision"] = recorder
    try:
        gate_exec.execute_gate_command(
            None, "RUN", "record_release_decision", {}, "Aaron", "human"
        )
    finally:
        if original is not None:
            gate_exec._HANDLERS["record_release_decision"] = original
    assert seen == {"actor": "Aaron", "actor_kind": "human"}, (
        f"the worker's actor class must reach the release core: {seen!r}"
    )


def test_decide_release_payload_carries_the_named_actor_and_kind(tmp_path):
    """The end-to-end payload the publish approval reads (worker path)."""
    from tests.unit.test_release_authorization_direct import _host
    from tracks.cli.release_cmd import _decide_release, _release_gate, _ReleaseRequest

    repo, store, run_id, candidate, _digest, _path, _preview_payload = _host(tmp_path)
    try:
        gate = _release_gate(repo, repo / ".tracks", store, run_id)
        assert gate is not None, "the seeded host must expose the release gate"
        payload = gate.preview.payload or {}
        rc = _decide_release(
            store,
            run_id,
            gate,
            _ReleaseRequest(action="release", reason=None, target=None),
            payload,
            actor="Aaron",
            actor_kind="human",
        )
        assert rc is None, "the seeded release decision must land"
        decided = [e for e in store.events(run_id) if e.type == "release.decided"]
        assert decided, "release.decided must land"
        decision = decided[-1].payload
        assert decision["actor"] == "Aaron", decision
        assert decision["actor_kind"] == "human", decision
        # the publish approval reads exactly this payload and stays current
        assert pr._approval_error(
            [gate.preview, decided[-1]], gate.preview, payload["preview_digest"], candidate
        ) is None, "the named human decision must pass the publish approval"
    finally:
        store.close()
