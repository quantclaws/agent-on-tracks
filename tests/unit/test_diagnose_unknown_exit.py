"""Unit: M2 DIAGNOSE forensic package + the unknown honest exit.

Convergence plan 2026-09-05 M1-S2/M2:

- The kernel DIAGNOSE vocabulary admits ``unknown`` (Prism could not
  reproduce on the forensic package -- it must NOT guess an owner) and
  ``diagnosis_exhausted`` (repeated unknown attribution -- Archer RULING
  carries the paired-delta decision, no writer attempt consumed).
- The executor streak/forensics-ref scans are pure event-log arithmetic.
- The conftest forensics hook captures the live git state of the host
  repo before teardown and is inert without TRAC_FORENSICS_DIR.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

from tracks.kernel import m_impl
from tracks.kernel.machine import State

# -- kernel routing ----------------------------------------------------------


def _state_in_diagnose() -> State:
    st = State()
    st.substate = "DIAGNOSE"
    st.current_attempt = 1
    return st


def test_diagnose_classifications_admit_unknown():
    assert "unknown" in m_impl.DIAGNOSE_CLASSIFICATIONS


def test_route_unknown_stays_diagnose_and_consumes_attempt():
    st = _state_in_diagnose()
    m_impl._route_m_impl_diagnose(st, "unknown")
    assert st.substate == "DIAGNOSE"
    # an empty diagnosis is still a spent round
    assert st.current_attempt == 2


def test_route_unknown_resets_reviewer_flag():
    st = _state_in_diagnose()
    st.reviewer_dispatched = True
    m_impl._route_m_impl_diagnose(st, "unknown")
    assert st.reviewer_dispatched is False


def test_route_diagnosis_exhausted_sets_ruling_without_attempt():
    st = _state_in_diagnose()
    m_impl._route_m_impl_diagnose(st, "diagnosis_exhausted")
    assert st.substate == "RULING"
    # NOT a writer failure: the attempt budget must stay intact.
    assert st.current_attempt == 1


def test_route_diagnosis_exhausted_resets_doc_flags():
    # Silent-exit regression (live 2026-09-05): a stale doc_dispatched
    # left over from the interrupted GREEN dispatch must be cleared --
    # otherwise _decide_m_impl_ruling awaits a phantom outcome forever
    # and the loop exits with the run parked in RULING.
    st = _state_in_diagnose()
    st.doc_dispatched = True
    m_impl._route_m_impl_diagnose(st, "diagnosis_exhausted")
    assert st.doc_dispatched is False
    cmd = m_impl._decide_m_impl_ruling(st)
    assert cmd is not None and cmd.kind == "dispatch_agent"


def test_route_contract_conflict_resets_doc_flags():
    st = State()
    st.substate = "GREEN_GATE"
    st.current_attempt = 2
    st.doc_dispatched = True
    m_impl._route_m_impl_gate_failure(st, "contract_conflict", None)
    assert st.substate == "RULING"
    assert st.doc_dispatched is False
    assert st.current_attempt == 2
    cmd = m_impl._decide_m_impl_ruling(st)
    assert cmd is not None and cmd.kind == "dispatch_agent"


def test_ruling_objective_names_diagnosis_exhaustion():
    st = _state_in_diagnose()
    st.substate = "RULING"
    st.doc_dispatched = False
    st.last_failure = {
        "check": "diagnosis_exhausted",
        "reason": "repeated unknown attribution (S2)",
        "evidence": "forensic package: .tracks/runtime/blobs/abc",
    }
    cmd = m_impl._m_impl_archer_ruling_dispatch(st)
    assert "diagnosis-exhausted" in cmd.params["objective"]
    assert cmd.params["evidence"]["check"] == "diagnosis_exhausted"


def test_ruling_objective_defaults_to_oscillation():
    st = _state_in_diagnose()
    st.substate = "RULING"
    st.doc_dispatched = False
    st.last_failure = {
        "check": "contract_conflict",
        "reason": "anchor oscillation (S1)",
        "evidence": '{"oscillation": {"healed": ["a"], "newly_red": ["b"]}}',
    }
    cmd = m_impl._m_impl_archer_ruling_dispatch(st)
    assert "oscillating anchor pair" in cmd.params["objective"]


# -- executor streak / forensics-ref scans -----------------------------------


class _Ev:
    def __init__(self, type: str, payload: dict | None):
        self.type = type
        self.payload = payload


class _Store:
    def __init__(self, events):
        self._events = events

    def events(self, run_id):
        return list(self._events)


def _executor_with_events(events):
    from tracks.executor.executor import Executor

    ex = object.__new__(Executor)
    ex.store = _Store(events)
    ex.run_id = "run"
    return ex


def test_unknown_streak_false_on_first_unknown():
    ex = _executor_with_events(
        [
            _Ev(
                "verdict.failed",
                {"task_id": "T-1", "check": "impl_defect", "evidence": "{}"},
            )
        ]
    )
    assert ex._diagnose_unknown_streak("T-1") is False


def test_unknown_streak_true_on_repeated_unknown():
    ex = _executor_with_events(
        [
            _Ev(
                "verdict.failed",
                {"task_id": "T-1", "check": "unknown", "evidence": "x"},
            )
        ]
    )
    assert ex._diagnose_unknown_streak("T-1") is True


def test_unknown_streak_scopes_to_task():
    ex = _executor_with_events(
        [
            _Ev(
                "verdict.failed",
                {"task_id": "T-OTHER", "check": "unknown", "evidence": "x"},
            )
        ]
    )
    assert ex._diagnose_unknown_streak("T-1") is False


def test_latest_forensics_ref_from_gate_evidence():
    ex = _executor_with_events(
        [
            _Ev(
                "verdict.failed",
                {
                    "task_id": "T-1",
                    "check": "impl_defect",
                    "evidence": json.dumps(
                        {
                            "selection_id": "s",
                            "failed_nodes": ["a"],
                            "forensics_ref": ".tracks/runtime/blobs/deadbeef",
                        }
                    ),
                },
            )
        ]
    )
    assert ex._latest_forensics_ref("T-1") == ".tracks/runtime/blobs/deadbeef"


def test_latest_forensics_ref_none_without_package():
    ex = _executor_with_events(
        [
            _Ev(
                "verdict.failed",
                {"task_id": "T-1", "check": "impl_defect", "evidence": "prose"},
            )
        ]
    )
    assert ex._latest_forensics_ref("T-1") is None


# -- forensics capture (conftest hook payload) --------------------------------


def _git(repo, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def _init_repo(path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    (path / "f.txt").write_text("x", encoding="utf-8")
    _git(path, "add", "f.txt")
    _git(path, "commit", "-qm", "init")


def test_capture_failure_forensics_writes_package(tmp_path, monkeypatch):
    from tests._support.forensics import capture_failure_forensics

    host = tmp_path / "host"
    _init_repo(host)
    root = tmp_path / "forensics"
    monkeypatch.setenv("TRAC_FORENSICS_DIR", str(root))
    monkeypatch.setenv("TRAC_FORENSICS_PRESERVE", "0")
    item = SimpleNamespace(nodeid="tests/integration/test_x.py::test_y")
    rep = SimpleNamespace(when="call", longreprtext="AssertionError: boom")
    record = capture_failure_forensics(item, rep, host)
    assert record is not None
    assert record["nodeid"].endswith("test_y")
    assert record["git_head"]
    assert "boom" in record["assertion_context"]
    blob = json.loads(
        (root / "failures" / "tests_integration_test_x.py_test_y.json").read_text(
            encoding="utf-8"
        )
    )
    assert blob["git_head"] == record["git_head"]


def test_capture_failure_forensics_inert_without_env(tmp_path, monkeypatch):
    from tests._support.forensics import capture_failure_forensics

    monkeypatch.delenv("TRAC_FORENSICS_DIR", raising=False)
    host = tmp_path / "host"
    _init_repo(host)
    item = SimpleNamespace(nodeid="n")
    rep = SimpleNamespace(when="call", longreprtext="")
    assert capture_failure_forensics(item, rep, host) is None
