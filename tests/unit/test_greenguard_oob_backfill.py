"""#212 OOB（2026-09-28，Prism 方案）：守卫通道 + oob.accepted 补账。

IF-GREENGUARD-001 §1u.3（interfaces §1u，test-plan §8.1 唯一权威）：
- 守卫节点通过 → guard_verified（合法，不触发 unexpected_pass）；
- 守卫节点失败/缺席 → fail-closed（guard_failed / guard_absent）；
- 未声明节点维持 unexpected_pass；OOB 文件豁免（oob_verified）不变。

#212：观察指针窗口结构性漏掉静默期 OOB 提交（后续 result 基线先行吸收），
补账通道从 run 首个 checkpoint 下界扫描带 trailer 提交、与已记账集合做差，
补发同构 oob.accepted（幂等）。
"""

import subprocess
from pathlib import Path

import pytest


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )


def _commit(repo, msg, trailer=None):
    (repo / "f.txt").write_text(msg, encoding="utf-8")
    _git(repo, "add", "-A")
    body = f"{msg}\n\nTracks-OOB: {trailer}" if trailer else msg
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", body)


# -- _version_guard_nodes（§8.1 解析） ------------------------------------------


def test_guard_nodes_parse_from_section_8_1(tmp_path):
    from tracks.executor.test_execute import _version_guard_nodes

    plan = tmp_path / "test-plan.md"
    plan.write_text(
        "## 8. AC Coverage\n\n| AC id | layer | test | IF |\n\n"
        "### 8.1 Arrival-green guard nodes（到达即绿守卫清单）\n\n"
        "前言文字，非清单行。\n\n"
        "- `tests/integration/test_a.py::test_guard_one` — AC-X — IF-Y\n"
        "- `tests/integration/test_b.py::test_guard_two` — AC-Z — IF-W\n"
        "- 无反引号的行不解析\n"
        "\n---\n\n## 9. 后续章节\n\n- `tests/e2e/other.py::test_not_guard`\n",
        encoding="utf-8",
    )
    assert _version_guard_nodes(tmp_path) == {
        "tests/integration/test_a.py::test_guard_one",
        "tests/integration/test_b.py::test_guard_two",
    }


def test_guard_nodes_missing_plan_or_section_is_empty(tmp_path):
    from tracks.executor.test_execute import _version_guard_nodes

    assert _version_guard_nodes(tmp_path) == set()
    plan = tmp_path / "test-plan.md"
    plan.write_text("## 8. AC Coverage\n\n无 §8.1\n", encoding="utf-8")
    assert _version_guard_nodes(tmp_path) == set()


# -- _record_layer_outcomes 分类矩阵 -------------------------------------------


class _Case:
    def __init__(self, status, detail=""):
        self.status = status
        self.detail = detail


def _classify(nodes, cases, oob_files=None, guards=None):
    from tracks.executor.test_execute import ExecTestRunMixin

    mapping = dict(zip(nodes, cases, strict=True))
    outcomes, findings = [], []
    legit = ExecTestRunMixin._record_layer_outcomes(
        nodes, mapping, outcomes, findings, oob_files or set(), guards or set()
    )
    return legit, [o["classification"] for o in outcomes]


def test_guard_pass_classifies_guard_verified_legit():
    legit, classes = _classify(
        ["tests/integration/test_a.py::test_g"],
        [_Case("passed")],
        guards={"tests/integration/test_a.py::test_g"},
    )
    assert legit is True
    assert classes == ["guard_verified"]


def test_guard_fail_classifies_guard_failed_not_legit():
    legit, classes = _classify(
        ["tests/integration/test_a.py::test_g"],
        [_Case("failed", "AssertionError: guard regression")],
        guards={"tests/integration/test_a.py::test_g"},
    )
    assert legit is False
    assert classes == ["guard_failed"]


def test_unlisted_pass_stays_unexpected_pass():
    legit, classes = _classify(
        ["tests/unit/test_x.py::test_p"], [_Case("passed")]
    )
    assert legit is False
    assert classes == ["unexpected_pass"]


def test_oob_file_pass_stays_oob_verified():
    legit, classes = _classify(
        ["tests/unit/test_x.py::test_p"],
        [_Case("passed")],
        oob_files={"tests/unit/test_x.py"},
    )
    assert legit is True
    assert classes == ["oob_verified"]


def test_guard_absent_declared_but_not_executed_fails_closed(tmp_path):
    """§1u.2：§8.1 声明但不在执行选择集 → guard_absent → invalid。"""
    from tracks.executor.test_execute import _version_guard_nodes

    plan = tmp_path / "test-plan.md"
    plan.write_text(
        "### 8.1 Arrival-green guard nodes\n\n"
        "- `tests/integration/test_missing.py::test_declared_not_run`\n",
        encoding="utf-8",
    )
    assert _version_guard_nodes(tmp_path) == {
        "tests/integration/test_missing.py::test_declared_not_run"
    }


# -- _oob_backfill_acceptances（#212 补账） -------------------------------------


class _FakeStore:
    def __init__(self, events):
        self._events = events

    def events(self, run_id):
        return list(self._events)


class _Ev:
    def __init__(self, type_, payload):
        self.type = type_
        self.payload = payload


class _Recorder:
    def __init__(self):
        self.emitted = []

    def __call__(self, ev_type, payload, **kw):
        self.emitted.append((ev_type, payload))


def _executor_with(store, emitted):
    from tracks.executor.executor import Executor

    ex = Executor.__new__(Executor)
    ex.store = store
    ex.repo = Path("/nonexistent")  # backfill 在 lower=None 时不会碰 git
    return ex


def test_backfill_noop_without_checkpoint_bound(tmp_path):
    from tracks.executor.observe import ExecObserveMixin

    mixin = ExecObserveMixin()
    mixin.store = _FakeStore([_Ev("oob.accepted", {"sha": "a" * 40})])
    mixin.repo = tmp_path
    mixin.run_id = "R"
    mixin._emit = lambda *a, **k: pytest.fail("no emission expected")
    assert mixin._oob_backfill_acceptances() == {"a" * 40}


def test_backfill_emits_missing_trailer_commits_idempotent(tmp_path):
    from tracks.executor.observe import ExecObserveMixin

    repo = tmp_path / "main"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "base")  # 将作为 checkpoint 下界
    base = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    _commit(repo, "oob fix one", "repair A")
    _commit(repo, "plain commit no trailer")
    _commit(repo, "oob fix two", "repair B")

    mixin = ExecObserveMixin()
    mixin.repo = repo
    mixin.run_id = "R"
    mixin.store = _FakeStore([_Ev("result.checkpointed", {"commit_sha": base})])
    emitted = []
    mixin._emit = lambda t, p, **k: emitted.append((t, p))

    recorded = mixin._oob_backfill_acceptances()
    shas = [p["sha"] for t, p in emitted if t == "oob.accepted"]
    assert len(shas) == 2
    assert all(t == "oob.accepted" for t, _ in emitted)
    assert recorded == set(shas)
    reasons = {p["reason"] for _, p in emitted}
    assert reasons == {"repair A", "repair B"}

    # 幂等：第二次调用（事件流已含记账）不再发射
    mixin.store = _FakeStore(
        [_Ev("result.checkpointed", {"commit_sha": base})]
        + [_Ev("oob.accepted", {"sha": s}) for s in shas]
    )
    emitted.clear()
    assert mixin._oob_backfill_acceptances() == set(shas)
    assert emitted == []


def test_guard_skipped_is_not_guard_verified():
    """OOB212-B2：合同只豁免 passing 守卫——skipped 是执行缺席，fail-closed。"""
    legit, classes = _classify(
        ["tests/integration/test_a.py::test_g"],
        [_Case("skipped", "deselected")],
        guards={"tests/integration/test_a.py::test_g"},
    )
    assert legit is False
    assert classes == ["guard_failed"]


def test_guard_absence_findings_production_path():
    """OOB212-B3：guard_absent 的生产路径（§8.1 声明不在执行集）。"""
    from tracks.executor.test_execute import ExecTestRunMixin

    guards = {"tests/integration/test_missing.py::test_gone"}
    seen = {"tests/integration/test_a.py::test_other"}
    findings = ExecTestRunMixin._guard_absence_findings(guards, seen)
    assert len(findings) == 1
    assert findings[0]["classification"] == "guard_absent"
    assert findings[0]["test_id"] == "tests/integration/test_missing.py::test_gone"
    assert ExecTestRunMixin._guard_absence_findings(seen, seen) == []
