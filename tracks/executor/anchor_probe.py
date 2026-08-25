"""B33（#34）：规划期锚点实测——§1.0.5 任务型裁定的机械化。

r1 双重回滚实证（run 01M0AMKV）：
- 回滚 #2（PRISM-PLAN-R1-01）：T-003 被排成 preset-anchor，但其 scope
  的实现已随前轮 GREEN 落盘——锚点实测已绿。anchor_red 对已绿锚判
  red_invalid，事件流停车；人工发现时已烧 5.5h。
- 回滚 #1：T-003 的冻结测试驱动 trac hotfix CLI，而 CLI 接线排在未派
  发的 T-007——排序缺陷在 5.5 小时后才以 stub_gap 回滚爆发。

本模块在 taskgraph commit 时对每个任务实跑其 test_refs（规划期测量，
非推测），按签名分类：

- ``green``：全部通过 → 任务型必须是 verification-only（标准 RGR 无
  合法 RED 锚）——**硬门禁**（回滚 #2 类，零误报：实测绿即无可实现）。
- ``assertion_red``：行为断言失败 → 标准 RGR 的合法红（任务自身 scope
  待实现）。
- ``entry_or_collect_error``：入口缺失/收集错误（usage/collect 特征）
  → TG-2 排序缺陷信号——**报告**（taskgraph.committed payload +
  stderr），作为 PRISM_PLAN 复核的机器证据（§1.0.5 判据），不硬拒
  （测试未写出的标准 RGR 任务合法地呈现同一签名，静态不可区分）。

非 pytest 合同（framework 字段）跳过实测（报告 skip，fail-open 于
infra、fail-closed 于语义）。
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

VERIFICATION_MARKER = "verification-only"

_LEGIT_RED_MARKERS = ("e   assert", "assertionerror", "failed")
_ENTRY_ERROR_MARKERS = (
    "usage:",
    "no tests ran",
    "not found",
    "error: file or directory",
    "errors during collection",
    "error collecting",
    "modulenotfounderror",
    "importerror",
    "exit code 2",
)


@dataclass
class AnchorProbe:
    """一个任务 test_refs 的规划期实测结果。"""

    task_id: str
    status: str  # green | assertion_red | entry_or_collect_error | skipped
    refs: tuple[str, ...] = ()
    detail: str = ""
    classification_hint: str = ""  # 机器证据摘要（进 committed payload）

    @property
    def requires_verification_only(self) -> bool:
        return self.status == "green"


@dataclass
class ProbeReport:
    """整张任务图的实测报告。"""

    probes: list[AnchorProbe] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped_reason: str = ""

    def advisory(self) -> list[str]:
        """TG-2 排序缺陷信号（报告用，不硬拒）。"""
        return [
            f"TG-2 signal: {p.task_id} test_refs fail with entry/collect "
            f"signature at planning time ({p.detail[:120]}) — if the missing "
            f"surface is delivered by a later task, add it to depends_on; "
            f"if the task itself wires it, this is its legal pre-RED state"
            for p in self.probes
            if p.status == "entry_or_collect_error"
        ]


def classify_probe_output(rc: int, stdout: str, stderr: str) -> str:
    """签名分类：green / assertion_red / entry_or_collect_error。

    纯函数（单测覆盖）。rc==0 即 green；失败时按输出特征区分行为断言
    （合法红）与入口/收集错误（排序缺陷信号）。
    """
    if rc == 0:
        return "green"
    text = f"{stdout or ''}\n{stderr or ''}".lower()
    if any(m in text for m in _ENTRY_ERROR_MARKERS):
        return "entry_or_collect_error"
    if any(m in text for m in _LEGIT_RED_MARKERS):
        return "assertion_red"
    return "entry_or_collect_error"  # 无法识别的失败按排序信号报告（保守）


def _run_one_probe(argv: list[str], cwd: Path) -> AnchorProbe:
    """执行单任务探测并分类（infra 失败 → skipped，B35 语义）。"""
    try:
        proc = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=600
        )
        status = classify_probe_output(
            proc.returncode, proc.stdout or "", proc.stderr or ""
        )
        detail = (proc.stdout or proc.stderr or "").strip()[:400]
    except (OSError, subprocess.TimeoutExpired) as exc:
        status, detail = "skipped", f"probe infra failure: {exc}"
    return AnchorProbe("", status, (), detail)


def probe_task_anchors(repo: Path, tasks, contract) -> ProbeReport:
    """对每个任务实跑 test_refs（合同 run 命令 + refs 收窄）。

    仅 pytest 合同（framework 字段）实测；其余跳过。任务无 test_refs
    跳过。规则 1（green 且非 verification-only）记入 errors（硬门禁）；
    规则 2（entry/collect）记入 advisory（报告）。
    """
    report = ProbeReport()
    if getattr(contract, "framework", None) != "pytest":
        report.skipped_reason = (
            f"anchor probe skipped: non-pytest contract "
            f"(framework={getattr(contract, 'framework', None)!r})"
        )
        return report
    section = getattr(contract, "integration", None)
    run_cmd = getattr(section, "run", None) if section is not None else None
    if not run_cmd:
        report.skipped_reason = "anchor probe skipped: no integration run command"
        return report
    try:
        base_argv = shlex.split(run_cmd)
    except ValueError as exc:
        report.skipped_reason = f"anchor probe skipped: contract run unparsable ({exc})"
        return report
    cwd = repo / (section.cwd if section and section.cwd != "." else ".")
    for task in tasks:
        # B50 (#65): probe the task's ACCEPTANCE anchors -- Shield-owned
        # integration nodes that exist at planning time. Legacy graphs map
        # their test_refs here; unit_refs are Devon's not-yet-written RED
        # artifacts and probing them would only emit entry/collect noise.
        refs = tuple(getattr(task, "acceptance_refs", None) or task.test_refs or ())
        if not refs:
            report.probes.append(
                AnchorProbe(task.task_id, "skipped", refs, "no acceptance refs")
            )
            continue
        probe = _run_one_probe([*base_argv, *refs], cwd)
        probe.task_id = task.task_id
        probe.refs = refs
        _apply_type_rule(report, probe, task)
    return report


def _apply_type_rule(report: ProbeReport, probe: AnchorProbe, task) -> None:
    """规则 1：锚已绿 + 非 verification-only → 硬门禁（r1 回滚 #2）。"""
    is_verification = VERIFICATION_MARKER in (task.description or "")
    if probe.status == "green" and not is_verification:
        report.errors.append(
            f"TG 任务型裁定（#34）：{task.task_id} 的 test_refs 在规划期实测"
            f"已全部通过（green），但任务型不是 verification-only——标准 "
            f"RGR/preset-anchor 对已绿锚无合法 RED（anchor_red 将判 "
            f"red_invalid 停车，r1 回滚 #2 的机械化拦截）。改排 "
            f"verification-only 或移除该任务（PRISM-PLAN-R1-01 类缺陷）"
        )
    report.probes.append(probe)


def probe_summary(report: ProbeReport) -> dict:
    """进 taskgraph.committed payload 的机器证据摘要。"""
    return {
        "skipped_reason": report.skipped_reason,
        "probes": [
            {
                "task_id": p.task_id,
                "status": p.status,
                "refs": list(p.refs),
                "detail": p.detail[:200],
            }
            for p in report.probes
        ],
        "advisory": report.advisory(),
    }
