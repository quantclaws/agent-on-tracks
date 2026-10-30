"""IF-UIPERF-001 RED repair: the release-decision todo navigates to the run timeline.

interfaces §1g item 5 allows a todo card to navigate to the run timeline or a
decision modal.  The frozen §8 e2e anchor
``test_hotfix262_release_ui.py::test_todos_cards_clickable_readable`` binds the
run timeline (``/runs/{run_id}``), so the AWAITING_RELEASE branch of
``projections_core._todo_for`` must return the run timeline rather than the
``/runs/{run_id}/release`` decision face.  The current tree returns the
decision face, which leaves the e2e card click at a URL that never reaches the
timeline.

Expected value comes from the frozen §8 e2e anchor / interfaces §1g item 5,
never from the implementation under test.
"""

from __future__ import annotations

from tracks.kernel.machine import State
from tracks.server.projections_core import _todo_for

_RUN_ID = "run-hf262-todo"


def _awaiting_release_state() -> State:
    return State(
        run_id=_RUN_ID,
        version="v1.0",
        stage="M-RELEASE",
        substate="AWAITING_RELEASE",
    )


def test_release_decision_todo_action_ref_is_run_timeline():
    todo = _todo_for(_RUN_ID, _awaiting_release_state(), {"version": "v1.0"})
    assert todo is not None, "an AWAITING_RELEASE run is a legitimate human todo"
    assert todo.get("kind") == "release_decision"
    assert todo.get("action_ref") == f"/runs/{_RUN_ID}", (
        "the release-decision card must navigate to the run timeline "
        f"(got {todo.get('action_ref')!r})"
    )
