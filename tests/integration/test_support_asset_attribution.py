"""Integration regressions for M-TEST Shield WRITE support-asset attribution.

Bug 1 fix: ``_m_test_write_payload`` now attributes all Shield-created/
modified non-ignored regular files under ``tests/`` by content identity,
not only ``.py``. Support assets (``*.patch``, ``*.json``, kill-manifest)
are checkpointed with the test-authority commit. A support-only revision
(no test module targets) is permitted when an existing M-TEST suite is
present and the host project contract's collect command verifies it; a
support-only WRITE with no suite fails closed.

RED cases:
1. Shield writes test module + untracked .patch/.json -> all attributed.
2. Support-only revision with existing committed suite -> passes via
   contract collection; support assets checkpointed.
3. Support-only WRITE with no existing suite -> fail closed with no
   ``test.written``; collection attribution requires a project contract.
"""

from tests.integration.helpers import g
from tests.integration.result_checkpoint_support import (
    _setup_m_test,
    _ShieldBackend,
)
from tests.m_test_support import make_m_test_dispatch_cmd
from tracks import paths

_PATCH_CONTENT = (
    "--- a/tests/integration/test_a.py\n"
    "+++ b/tests/integration/test_a.py\n"
    "@@ -1,2 +1,3 @@\n"
    " def test_a():\n"
    "-    assert True\n"
    "+    raise NotImplementedError('IF-MTEST-001')\n"
)

_JSON_CONTENT = '{"kill_manifest": {"AC-FR0010-01": "counterexample_001"}}\n'

_TEST_A_CONTENT = (
    "# AC-FR0010-01@v0.4 TRACKS-TRACE integration test\n"
    "def test_a():\n"
    "    raise NotImplementedError('IF-MTEST-001')\n"
)

_EXISTING_TEST = "def test_existing():\n    assert True\n"


def _commit_existing_suite(repo):
    """Create and commit an existing test suite under tests/integration/."""
    test_path = repo / "tests" / "integration" / "test_existing.py"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(_EXISTING_TEST, encoding="utf-8")
    g(repo, "add", str(test_path))
    g(repo, "commit", "-m", "existing test suite")


# -- RED 1: test module + untracked .patch/.json all attributed -----------


def test_shield_write_attributes_patch_and_json_alongside_test_module(tmp_path):
    """Shield writes test_a.py + fix.patch + data.json: all three must
    appear in result_checkpoint artifacts/allowed_paths and in the
    checkpoint commit."""
    ex, store, run_id = _setup_m_test(tmp_path)
    repo = ex.repo

    ex.backend = _ShieldBackend(
        repo,
        {
            "tests/integration/test_a.py": _TEST_A_CONTENT,
            "tests/counterexamples/fix.patch": _PATCH_CONTENT,
            "tests/assets/data.json": _JSON_CONTENT,
        },
    )
    ex.issue(make_m_test_dispatch_cmd())
    ex.run_pipeline()

    outcomes = [e for e in store.events(run_id) if e.type == "outcome.received"]
    assert outcomes, "outcome.received must be emitted"
    rc = outcomes[-1].payload.get("result_checkpoint", {})
    artifacts = rc.get("artifacts", [])
    assert "tests/integration/test_a.py" in artifacts, (
        f"test module must be attributed; artifacts={artifacts}"
    )
    assert "tests/counterexamples/fix.patch" in artifacts, (
        f"untracked .patch must be attributed; artifacts={artifacts}"
    )
    assert "tests/assets/data.json" in artifacts, (
        f"untracked .json must be attributed; artifacts={artifacts}"
    )

    written = [e for e in store.events(run_id) if e.type == "test.written"]
    assert written, "test.written must be published"
    names = g(repo, "show", "--format=", "--name-only", "HEAD").splitlines()
    assert "tests/integration/test_a.py" in names
    assert "tests/counterexamples/fix.patch" in names, (
        f".patch must be in checkpoint commit; files={names}"
    )
    assert "tests/assets/data.json" in names, f".json must be in checkpoint commit; files={names}"


# -- RED 2: support-only revision with existing suite passes -------------


def test_support_only_revision_with_existing_suite_passes(tmp_path):
    """A Shield revision that changes only support assets (no .py) passes
    when an existing M-TEST suite is present. Collection is validated
    through the host project contract; support assets are checkpointed."""
    ex, store, run_id = _setup_m_test(tmp_path)
    repo = ex.repo
    _commit_existing_suite(repo)

    ex.backend = _ShieldBackend(
        repo,
        {
            "tests/counterexamples/fix.patch": _PATCH_CONTENT,
            "tests/assets/data.json": _JSON_CONTENT,
        },
    )
    ex.issue(make_m_test_dispatch_cmd())
    ex.run_pipeline()

    outcomes = [e for e in store.events(run_id) if e.type == "outcome.received"]
    assert outcomes, "outcome.received must be emitted"
    rc = outcomes[-1].payload.get("result_checkpoint", {})
    artifacts = rc.get("artifacts", [])
    assert "tests/counterexamples/fix.patch" in artifacts, (
        f"support .patch must be attributed; artifacts={artifacts}"
    )
    assert "tests/assets/data.json" in artifacts, (
        f"support .json must be attributed; artifacts={artifacts}"
    )
    assert not any(a.endswith(".py") for a in artifacts), (
        f"no .py files should be attributed; artifacts={artifacts}"
    )

    written = [e for e in store.events(run_id) if e.type == "test.written"]
    assert written, "test.written must be published for support-only revision"
    state = store.state(run_id)
    assert state.substate == "COLLECT", (
        f"support-only revision must reach COLLECT; substate={state.substate}"
    )
    names = g(repo, "show", "--format=", "--name-only", "HEAD").splitlines()
    assert "tests/counterexamples/fix.patch" in names, (
        f".patch must be in checkpoint commit; files={names}"
    )
    assert "tests/assets/data.json" in names, f".json must be in checkpoint commit; files={names}"


# -- RED 3: support-only WRITE with no suite fails closed ----------------


def test_support_only_write_with_no_suite_fails_closed(tmp_path):
    """A support-only WRITE with no existing test suite and no project
    contract fails closed: no test.written and no COLLECT."""
    ex, store, run_id = _setup_m_test(tmp_path)
    repo = ex.repo
    paths.project_toml_path(paths.tracks_home(repo)).unlink()

    ex.backend = _ShieldBackend(
        repo,
        {
            "tests/counterexamples/fix.patch": _PATCH_CONTENT,
            "tests/assets/data.json": _JSON_CONTENT,
        },
    )
    ex.issue(make_m_test_dispatch_cmd())
    ex.run_pipeline()

    written = [e for e in store.events(run_id) if e.type == "test.written"]
    assert not written, "test.written must NOT be published (fail closed)"
    state = store.state(run_id)
    assert state.substate != "COLLECT", f"must not reach COLLECT; substate={state.substate}"


def test_support_only_write_with_contract_but_no_suite_fails_closed(tmp_path):
    """A support-only WRITE with a project contract but no existing test
    suite: contract collection returns non-zero (no tests collected) ->
    fail closed with check=collection."""
    ex, store, run_id = _setup_m_test(tmp_path)
    repo = ex.repo
    (repo / "tests" / "integration").mkdir(parents=True, exist_ok=True)

    ex.backend = _ShieldBackend(
        repo,
        {
            "tests/counterexamples/fix.patch": _PATCH_CONTENT,
        },
    )
    ex.issue(make_m_test_dispatch_cmd())
    ex.run_pipeline()

    failures = [
        e
        for e in store.events(run_id)
        if e.type == "verdict.failed" and e.payload.get("check") == "collection"
    ]
    assert failures, "support-only WRITE with contract but no suite must fail closed"
    assert "host project contract" in failures[0].payload.get("reason", ""), (
        f"failure reason must identify the host project contract; "
        f"reason={failures[0].payload.get('reason')}"
    )
    written = [e for e in store.events(run_id) if e.type == "test.written"]
    assert not written, "test.written must NOT be published (fail closed)"
