"""RED unit artifacts bind to the task's whole R family, not the latest slot.

Regression for run 01M0S0FQ T-016 (v0.7 boundary, 2026-08-28): the re-scoped
task declared the reach test (pinned in earlier family slots 6/8) in
unit_refs while the latest RED re-pin (slot 9) carried only the new
obligation's test; a current-slot-only ``_require_r_artifacts`` fail-closed
the legal multi-generation task at SELECT_TASK.
"""

from types import SimpleNamespace

from tracks.executor.m_impl_runtime import MImplRuntimeMixin
from tracks.executor.test_select import TestSelectError


class _FakeStore:
    def __init__(self, events):
        self._events = events

    def events(self, run_id):
        return self._events


def _runtime(family, tree_paths):
    """Minimal MImplRuntimeMixin instance: event store with the given
    red.checkpointed family and a _path_in_tree stub keyed by sha."""
    rt = object.__new__(MImplRuntimeMixin)
    rt.run_id = "RUN"
    rt.store = _FakeStore(
        [
            SimpleNamespace(
                type="red.checkpointed",
                payload={"task_id": "T-016", "r_sha": sha},
            )
            for sha in family
        ]
    )
    rt._path_in_tree = lambda sha, path: path in tree_paths.get(sha, ())
    return rt


def test_artifact_in_earlier_family_slot_passes():
    # slot 9 (current) has only the new test; the reach test lives in slot 6
    rt = _runtime(
        family=["slot6", "slot9"],
        tree_paths={"slot6": ("tests/unit/test_reach_package_data.py",), "slot9": ("tests/unit/test_t016_adapter_neutrality.py",)},
    )
    rt._require_r_artifacts(
        ["tests/unit/test_reach_package_data.py::test_explicit"],
        "slot9",
        "T-016",
    )


def test_artifact_nowhere_fails_closed():
    rt = _runtime(
        family=["slot9"],
        tree_paths={"slot9": ("tests/unit/test_t016_adapter_neutrality.py",)},
    )
    try:
        rt._require_r_artifacts(
            ["tests/unit/test_missing.py::test_x"], "slot9", "T-016"
        )
    except TestSelectError as exc:
        assert "absent from immutable R" in str(exc)
    else:
        raise AssertionError("expected TestSelectError")


def test_no_r_family_at_all_fails_closed():
    rt = _runtime(family=[], tree_paths={})
    try:
        rt._require_r_artifacts(["tests/unit/test_x.py::t"], "", "T-016")
    except TestSelectError:
        pass
    else:
        raise AssertionError("expected TestSelectError")


def test_family_excludes_other_tasks_and_duplicates():
    rt = object.__new__(MImplRuntimeMixin)
    rt.run_id = "RUN"
    rt.store = _FakeStore(
        [
            SimpleNamespace(type="red.checkpointed", payload={"task_id": "T-015", "r_sha": "other"}),
            SimpleNamespace(type="red.checkpointed", payload={"task_id": "T-016", "r_sha": "slot6"}),
            SimpleNamespace(type="red.checkpointed", payload={"task_id": "T-016", "r_sha": "slot6"}),
            SimpleNamespace(type="red.checkpointed", payload={"task_id": "T-016", "r_sha": "slot9"}),
        ]
    )
    assert rt._task_r_family_shas("T-016") == ["slot6", "slot9"]
