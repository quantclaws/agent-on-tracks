"""Unit: M4 anchor-satisfiability static rules (convergence plan 2026-09-05).

Rule 1's two mechanically recognizable unsatisfiable-anchor classes must
be flagged at birth (M-TEST WRITE / SHIELD_FIX), with the suppression
comment and the legitimate assertion shapes staying clean:
- HEAD-equality is flagged only when runtime activity (walk / trac run /
  commit) sits between the capture and the assertion;
- count-equality only between two event-log snapshots with activity
  between the captures (deterministic ``len(x) == const`` counting is
  the existing suite's legitimate shape and stays clean).
"""

from __future__ import annotations

from tracks.checks.anchor_lint import anchor_static_violations


def _lint(tmp_path, source: str) -> list[str]:
    path = tmp_path / "test_x.py"
    path.write_text(source, encoding="utf-8")
    return anchor_static_violations(path)


def test_head_equality_across_activity_flagged(tmp_path):
    v = _lint(
        tmp_path,
        '''
def test_x(host_repo, trac, event_log):
    head = _git(host_repo, "rev-parse", "HEAD")
    walk_to_m_impl_parked(trac)
    events = event_log()
    frozen = [e for e in events if e["type"] == "candidate.frozen"]
    assert frozen[0]["payload"]["candidate_sha"] == head
''',
    )
    assert len(v) == 1 and "rev-parse HEAD" in v[0]


def test_head_equality_adjacent_capture_clean(tmp_path):
    # The T-042 :78 FIX shape: capture the anchor AFTER the walk,
    # adjacent to the assertion -- no activity between, target stable.
    v = _lint(
        tmp_path,
        '''
def test_x(host_repo, trac, event_log):
    walk_to_m_impl_parked(trac)
    head = _git(host_repo, "rev-parse", "HEAD")
    events = event_log()
    frozen = [e for e in events if e["type"] == "candidate.frozen"]
    assert frozen[0]["payload"]["candidate_sha"] == head
''',
    )
    assert v == []


def test_count_equality_between_snapshots_flagged(tmp_path):
    v = _lint(
        tmp_path,
        '''
def test_x(trac, event_log):
    walk_to_m_impl_parked(trac)
    frozen_before = [e for e in event_log() if e["type"] == "candidate.frozen"]
    trac("run")
    events_after = event_log()
    frozen_after = [e for e in events_after if e["type"] == "candidate.frozen"]
    assert len(frozen_after) == len(frozen_before)
''',
    )
    assert len(v) == 1 and "count-equality" in v[0]


def test_count_equality_without_activity_clean(tmp_path):
    # Two snapshots with no runtime activity between: same window,
    # pointless but stable -- not the moving-target anti-pattern.
    v = _lint(
        tmp_path,
        '''
def test_x(event_log):
    a = [e for e in event_log() if e["type"] == "x"]
    b = [e for e in event_log() if e["type"] == "x"]
    assert len(a) == len(b)
''',
    )
    assert v == []


def test_deterministic_const_count_clean(tmp_path):
    # The existing suite's legitimate shape: counting events of a type
    # in a fixed scenario (no second snapshot, no activity in between).
    v = _lint(
        tmp_path,
        '''
def test_x(trac, event_log):
    trac("run")
    events = event_log()
    fails = [e for e in events if e["type"] == "commit.rejected"]
    assert len(fails) == 1
''',
    )
    assert v == []


def test_pure_function_len_not_flagged(tmp_path):
    v = _lint(
        tmp_path,
        '''
def test_x():
    drifted = [_event("a"), _event("b")]
    violations = collect_binding_violations(drifted, "c")
    assert len(violations) == 1
''',
    )
    assert v == []


def test_event_existence_and_payload_predicate_not_flagged(tmp_path):
    v = _lint(
        tmp_path,
        '''
def test_x(trac, event_log):
    walk_to_m_impl_parked(trac)
    events = event_log()
    stale = [e for e in events if e["type"] == "candidate.stale"]
    assert stale, "must appear"
    assert any(s["payload"].get("reason") == "candidate_drift" for s in stale)
    assert stale[0]["payload"]["clean_tree"] is True
''',
    )
    assert v == []


def test_suppression_comment_quiets_line(tmp_path):
    v = _lint(
        tmp_path,
        '''
def test_x(trac, event_log):
    frozen_before = [e for e in event_log()]
    trac("run")
    frozen_after = [e for e in event_log()]
    assert len(frozen_after) == len(frozen_before)  # tracks-anchor-ok
''',
    )
    assert v == []
