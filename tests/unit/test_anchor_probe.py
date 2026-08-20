"""B33（#34）：规划期锚点实测单元测试。

分类器纯函数 + 用 stub 合同命令（真 subprocess、离线确定性）实测
probe_task_anchors 的三类签名与硬门禁规则（green 且非 verification-only
→ error）。回滚 #2（PRISM-PLAN-R1-01）的机械化拦截回归。
"""

from pathlib import Path

from tracks.executor.anchor_probe import (
    AnchorProbe,
    ProbeReport,
    classify_probe_output,
    probe_summary,
    probe_task_anchors,
)

# -- classify_probe_output ------------------------------------------------------


def test_classifier_green():
    assert classify_probe_output(0, "3 passed", "") == "green"


def test_classifier_assertion_red():
    assert classify_probe_output(1, "FAILED test_x - assert 1 == 2", "") == "assertion_red"
    assert classify_probe_output(1, "E   assert False", "") == "assertion_red"


def test_classifier_entry_or_collect_error():
    for text in (
        "usage: trac hotfix <issue>",
        "ERROR: not found: tests/integration/test_new.py::test_x",
        "no tests ran in 0.01s",
        "errors during collection",
        "ModuleNotFoundError: No module named 'tracks.cli'",
    ):
        assert classify_probe_output(2, text, "") == "entry_or_collect_error", text


def test_classifier_unrecognized_failure_is_conservative():
    # 无法识别的失败 → 排序信号（保守报告，不当合法红）
    assert classify_probe_output(1, "weird crash", "") == "entry_or_collect_error"


# -- probe_task_anchors（stub 合同命令，真 subprocess） ---------------------------


def _stub_repo(tmp_path: Path, script: str) -> Path:
    repo = tmp_path / "host"
    repo.mkdir()
    (repo / "probe_stub.py").write_text(script, encoding="utf-8")
    contract = repo / ".tracks" / "projects" / "project.toml"
    contract.parent.mkdir(parents=True)
    contract.write_text(
        "[integration]\n"
        'framework = "pytest"\n'
        'paths = ["tests/integration/"]\n'
        'collect = "python3 probe_stub.py --collect"\n'
        'run = "python3 probe_stub.py"\n'
        'cwd = "."\n',
        encoding="utf-8",
    )
    return repo


class _Contract:
    pass


def _contract(run_cmd: str, framework: str = "pytest", cwd: str = "."):
    c = _Contract()
    c.framework = framework
    section = _Contract()
    section.run = run_cmd
    section.cwd = cwd
    c.integration = section
    return c


def _task(tid, refs, description):
    class T:
        pass

    t = T()
    t.task_id = tid
    t.test_refs = refs
    t.description = description
    return t


# stub：argv 中的 ref 名决定签名（green/red/missing/timeout）
_STUB = """import sys
refs = [a for a in sys.argv[1:] if not a.startswith("-")]
if any("missing" in r for r in refs):
    print("usage: trac hotfix <issue> --scenario ..."); sys.exit(2)
if any("red" in r for r in refs):
    print("FAILED test_red - assert 1 == 2"); print("E   assert 1 == 2"); sys.exit(1)
print("1 passed"); sys.exit(0)
"""


def test_probe_green_task_claiming_rgr_is_hard_error(tmp_path):
    repo = _stub_repo(tmp_path, _STUB)
    tasks = [
        _task("T-003", ["tests/integration/test_green.py"], "【标准 RGR】实现 M-TEST 变体"),
        _task("T-001", ["tests/integration/test_green.py"], "【verification-only §1.0.3】验证"),
    ]
    report = probe_task_anchors(repo, tasks, _contract("python3 probe_stub.py"))
    # T-003：green + 声称标准 RGR → 硬门禁（r1 回滚 #2 机械化）
    assert len(report.errors) == 1
    assert "T-003" in report.errors[0]
    assert "verification-only" in report.errors[0]
    # T-001：green + verification-only → 合法，无 error
    statuses = {p.task_id: p.status for p in report.probes}
    assert statuses == {"T-003": "green", "T-001": "green"}


def test_probe_assertion_red_is_legal_no_error(tmp_path):
    repo = _stub_repo(tmp_path, _STUB)
    tasks = [_task("T-004", ["tests/integration/test_red.py"], "【标准 RGR】实现 X")]
    report = probe_task_anchors(repo, tasks, _contract("python3 probe_stub.py"))
    assert report.errors == []
    assert report.probes[0].status == "assertion_red"
    assert report.advisory() == []


def test_probe_entry_error_is_advisory_not_blocking(tmp_path):
    """TG-2 信号：报告（PRISM_PLAN 机器证据），不硬拒——测试未写出的
    标准 RGR 任务合法地呈现同一签名，静态不可区分。"""
    repo = _stub_repo(tmp_path, _STUB)
    tasks = [_task("T-003", ["tests/integration/test_missing.py"], "【标准 RGR】实现 X")]
    report = probe_task_anchors(repo, tasks, _contract("python3 probe_stub.py"))
    assert report.errors == []
    assert report.probes[0].status == "entry_or_collect_error"
    adv = report.advisory()
    assert len(adv) == 1 and "T-003" in adv[0] and "depends_on" in adv[0]


def test_probe_skips_non_pytest_contract(tmp_path):
    repo = _stub_repo(tmp_path, _STUB)
    tasks = [_task("T-009", ["tests/x.py"], "【标准 RGR】")]
    report = probe_task_anchors(repo, tasks, _contract("go test ./...", framework="go"))
    assert report.errors == []
    assert "non-pytest" in report.skipped_reason
    assert probe_summary(report)["skipped_reason"] == report.skipped_reason


def test_probe_skips_task_without_refs(tmp_path):
    repo = _stub_repo(tmp_path, _STUB)
    tasks = [_task("T-000", [], "【标准 RGR】")]
    report = probe_task_anchors(repo, tasks, _contract("python3 probe_stub.py"))
    assert report.errors == []
    assert report.probes[0].status == "skipped"


def test_probe_infra_failure_skips_not_blocks(tmp_path):
    """runner 本体缺失（infra）→ 跳过实测（B35：infra 不门禁语义）。"""
    repo = _stub_repo(tmp_path, _STUB)
    tasks = [_task("T-005", ["tests/integration/test_green.py"], "【标准 RGR】")]
    report = probe_task_anchors(
        repo, tasks, _contract("definitely_missing_runner_bin --anything")
    )
    assert report.errors == []
    assert report.probes[0].status == "skipped"
    assert "probe infra failure" in report.probes[0].detail


def test_probe_summary_shape(tmp_path):
    repo = _stub_repo(tmp_path, _STUB)
    tasks = [
        _task("T-003", ["tests/integration/test_missing.py"], "【标准 RGR】"),
    ]
    report = probe_task_anchors(repo, tasks, _contract("python3 probe_stub.py"))
    summary = probe_summary(report)
    assert summary["skipped_reason"] == ""
    assert summary["probes"][0]["task_id"] == "T-003"
    assert summary["probes"][0]["status"] == "entry_or_collect_error"
    assert len(summary["advisory"]) == 1


def test_requires_verification_only_property():
    assert AnchorProbe("T", "green").requires_verification_only is True
    assert AnchorProbe("T", "assertion_red").requires_verification_only is False


def test_empty_report_advisory():
    assert ProbeReport().advisory() == []
