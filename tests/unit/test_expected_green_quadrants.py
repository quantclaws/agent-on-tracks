"""#211 gap 2 (2026-10-02): RED_CHECK re-entry expectation quadrants.

After a mid-implementation rollback, M-TEST re-entry must anchor node
expectations to colors MEASURED at the re-entry capture (not the stale
first-freeze marking). Quadrants (priority guard > oob > retained >
red-default):

  green-now + expected-green -> retained_green (legal)
  red-now   + expected-green -> regression     (fail-closed, Shield/DIAGNOSE)
  green-now + expected-red   -> unexpected_pass (unchanged vacuous guard)
  red-now   + expected-red   -> classic legal red

Live fire: run 01M3E7SAANXKW1V73W8B8Q3G86 seq 3667 — 19 anchors of
delivered-and-retained features classified unexpected_pass and deadlocked
the re-entry freeze across 3 burned rewrite attempts."""

from types import SimpleNamespace

from tracks.executor.test_execute import ExecTestRunMixin


def _case(status, detail=""):
    return SimpleNamespace(status=status, detail=detail)


def _classify(node, case, *, guards=(), oob=(), expected=()):
    return ExecTestRunMixin._classify_node(
        node, case, set(guards), set(oob), frozenset(expected)
    )


def test_green_now_expected_green_is_retained():
    n = "tests/integration/test_a.py::test_delivered"
    assert _classify(n, _case("passed"), expected=[n]) == "retained_green"
    assert _classify(n, _case("skipped"), expected=[n]) == "retained_green"


def test_red_now_expected_green_is_regression():
    n = "tests/integration/test_a.py::test_delivered"
    klass = _classify(
        n, _case("failed", "AssertionError: broke"), expected=[n]
    )
    assert klass == "regression"


def test_green_now_expected_red_stays_unexpected_pass():
    n = "tests/integration/test_a.py::test_not_yet"
    assert _classify(n, _case("passed"), expected=[]) == "unexpected_pass"


def test_red_now_expected_red_stays_classic():
    n = "tests/integration/test_a.py::test_not_yet"
    klass = _classify(n, _case("failed", "AssertionError: assert"))
    assert klass not in ("retained_green", "regression", "unexpected_pass")


def test_priority_guard_beats_expected_green():
    n = "tests/integration/test_g.py::test_guard"
    # a guard in the expected-green set keeps guard semantics (stronger)
    assert _classify(n, _case("passed"), guards=[n], expected=[n]) == "guard_verified"
    assert _classify(n, _case("failed"), guards=[n], expected=[n]) == "guard_failed"


def test_priority_oob_beats_retained():
    n = "tests/integration/test_oob.py::test_x"
    assert _classify(n, _case("passed"), oob=["tests/integration/test_oob.py"], expected=[n]) == "oob_verified"


def test_layer_outcome_legality_takes_retained():
    """retained_green is legal (all-legit true); regression is not."""
    n = "tests/integration/test_a.py::test_delivered"
    mapping = {n: _case("passed")}
    outcomes, findings = [], []
    assert ExecTestRunMixin._record_layer_outcomes(
        [n], mapping, outcomes, findings, set(), set(), frozenset({n})
    )
    assert findings[0]["classification"] == "retained_green"

    mapping_red = {n: _case("failed", "AssertionError")}
    outcomes2, findings2 = [], []
    assert not ExecTestRunMixin._record_layer_outcomes(
        [n], mapping_red, outcomes2, findings2, set(), set(), frozenset({n})
    )
    assert findings2[0]["classification"] == "regression"


def test_empty_expectation_zero_behavior_change():
    """First entry (no expected set): legacy classifications untouched."""
    n = "tests/integration/test_a.py::test_x"
    assert _classify(n, _case("passed")) == "unexpected_pass"
    outcomes, findings = [], []
    assert not ExecTestRunMixin._record_layer_outcomes(
        [n], {n: _case("passed")}, outcomes, findings, set(), set()
    )


def test_expected_green_blob_roundtrip(tmp_path, monkeypatch):
    """Prism B2 coverage: the capture-side write and the RED_CHECK-side read
    must meet through the STORE channel (TRACKS_HOME-redirect safe), not a
    repo-relative path."""

    from tracks.store.store import Store

    monkeypatch.chdir(tmp_path)
    store = Store(tmp_path / "home")
    green = {"tests/integration/test_a.py::x", "tests/unit/test_b.py::y"}
    sha = store.write_audit_blob(sorted(green))
    assert sha, "blob write failed"

    class _Exec(ExecTestRunMixin):
        repo = str(tmp_path)
        run_id = "RUN"

        def __init__(self):
            self.store = store
            self._events = [
                SimpleNamespace(
                    type="test.baseline_captured",
                    payload={
                        "status": "passed",
                        "expected_green_blob": f".tracks/runtime/blobs/{sha}",
                    },
                )
            ]

        def events(self, _run_id):
            return list(self._events)

    exec_ = _Exec()
    # the reader scans self.store.events(run_id) — route the real Store to
    # the fake event stream regardless of its native API surface
    monkeypatch.setattr(store, "events", lambda _rid: exec_._events, raising=False)
    assert exec_._expected_green_nodes() == frozenset(green)
