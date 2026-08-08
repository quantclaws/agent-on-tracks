"""Item 10: FakeBackend revision marker must be tied to explicit FR-11
revise/failure evidence, not to an arbitrary call counter.

Two scenarios:
- Second shield write WITHOUT evidence (ordinary identical redispatch):
  no revision marker appended.
- Second shield write WITH evidence (FR-11 failure evidence driving the
  re-dispatch): revision marker present, attributed to the evidence.
"""

from tests.integration.result_checkpoint_support import _setup_m_test
from tests.m_test_support import make_m_test_dispatch_cmd
from tracks.effects.fake import FakeBackend


def _first_shield_write(ex, repo):
    """Perform the first Shield write and return the content of a test file."""
    ex.issue(make_m_test_dispatch_cmd())
    test_file = repo / "tests" / "integration" / "test_ac_fr0010_01.py"
    assert test_file.exists()
    return test_file.read_text(encoding="utf-8")


def test_second_shield_write_without_evidence_no_revision_marker(tmp_path):
    """A second shield write with NO FR-11 evidence (ordinary identical
    redispatch) must NOT append a revision marker."""
    ex, store, run_id = _setup_m_test(tmp_path)
    ex.backend = FakeBackend(ex.repo, "v0.4")
    repo = ex.repo

    first = _first_shield_write(ex, repo)
    test_file = repo / "tests" / "integration" / "test_ac_fr0010_01.py"

    ex.backend = FakeBackend(ex.repo, "v0.4")
    ex.issue(make_m_test_dispatch_cmd())
    second = test_file.read_text(encoding="utf-8")

    assert second == first, (
        "second shield write without evidence must not append a revision "
        "marker; got extra content: " + repr(second[len(first) :])
    )


def test_second_shield_write_with_evidence_has_revision_marker(tmp_path):
    """A second shield write WITH FR-11 failure evidence must append a
    revision marker attributed to that evidence."""
    ex, store, run_id = _setup_m_test(tmp_path)
    ex.backend = FakeBackend(ex.repo, "v0.4")
    repo = ex.repo

    first = _first_shield_write(ex, repo)
    test_file = repo / "tests" / "integration" / "test_ac_fr0010_01.py"

    ex.backend = FakeBackend(ex.repo, "v0.4")
    evidence = {"check": "no_diff", "reason": "result requires a diff", "attempt": 1}
    ex.issue(make_m_test_dispatch_cmd(evidence=evidence))
    second = test_file.read_text(encoding="utf-8")

    assert len(second) > len(first), (
        "second shield write with evidence must append a revision marker"
    )
    marker = second[len(first) :]
    assert "revision" in marker.lower() or "no_diff" in marker.lower(), (
        "revision marker must be attributed to the FR-11 evidence; got: " + repr(marker)
    )
