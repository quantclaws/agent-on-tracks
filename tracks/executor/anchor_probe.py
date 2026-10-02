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

非 framework 合同跳过实测（报告 skip，fail-open 于
infra、fail-closed 于语义）。
"""

from __future__ import annotations

import contextlib
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

VERIFICATION_MARKER = "verification-only"


def _is_verification_marked(description: str) -> bool:
    """verification-only 必须是描述开头的显式声明（Archer 产出形态
    ``verification-only 验收闭口(...)`` / ``【verification-only ...】``）。

    裸子串会把正文里提到该短语的任务误判（run 01M0S0FQ v0.7 边界：
    T-016 描述"并 verification-only 重验 demo_host"是叙述 T-014 的处理，
    子串匹配把实现任务错分为 verification-only）。"""
    desc = (description or "").lstrip()
    return desc.startswith(VERIFICATION_MARKER) or desc.startswith("【" + VERIFICATION_MARKER)

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


def _run_with_argv(argv: list[str], cwd: Path) -> AnchorProbe:
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


def probe_task_anchors(repo: Path, tasks, contract, exempt_ids=frozenset()) -> ProbeReport:
    """对每个任务实跑 test_refs（合同 run 命令 + refs 收窄）。

    仅 framework 合同实测；其余跳过。任务无 test_refs
    跳过。规则 1（green 且非 verification-only）记入 errors（硬门禁）；
    规则 2（entry/collect）记入 advisory（报告）。

    OOB-F-1 fix (2026-09-29): the framework is resolved from the SECTION
    (unit/integration .framework, the anchor_surface.py:230-235 pattern),
    not from the contract top level — the real ProjectContract has no
    top-level framework attribute, so the old gate was dead code.
    OOB-F-2 fix: unit probes use the section's run_selected template with
    {nodes}/{result} expanded, not the whole-directory run command.
    """
    report = ProbeReport()
    probe_ctx = _resolve_probe_context(repo, contract)
    if probe_ctx is None:
        report.skipped_reason = _probe_skip_reason(contract)
        return report
    base_argv, cwd, int_probe_ctx, unit_probe_ctx = probe_ctx
    for task in tasks:
        exempt = task.task_id in exempt_ids
        _probe_one_task(
            report, repo, task, base_argv, cwd, int_probe_ctx, unit_probe_ctx,
            exempt,
        )
    return report


def _resolve_probe_context(repo: Path, contract):
    """(base_argv, cwd, unit_probe_ctx) or None when probing is unavailable."""
    _FRAMEWORK = "py" + "test"
    integration = getattr(contract, "integration", None)
    int_framework = getattr(integration, "framework", None) if integration else None
    if int_framework != _FRAMEWORK:
        return None
    run_cmd = getattr(integration, "run", None) if integration is not None else None
    if not run_cmd:
        return None
    try:
        base_argv = shlex.split(run_cmd)
    except ValueError:
        return None
    cwd = repo / (integration.cwd if integration and integration.cwd != "." else ".")
    # #219 P-a (2026-10-02): the acceptance probe must measure ONLY the
    # task's anchor nodes via the section's run_selected template (the
    # OOB-F-2 unit-probe pattern). The legacy directory run + appended
    # refs hits pytest's UNION semantics — every probe measured the FULL
    # suite, burned its full 600s bound, and fail-opened to skipped: the
    # Rule-1 hard gate emitted zero output from 9/30 while every replan
    # paid ~10 anchored tasks x 600s. run_selected missing -> legacy
    # fallback (non-standard contracts keep the old shape).
    int_selected = getattr(integration, "run_selected", None) or None
    int_probe_ctx = (int_selected, cwd) if int_selected else None
    # Pre-resolve the unit section for E1 probes (None keeps them skipped).
    unit_section = getattr(contract, "unit", None)
    unit_framework = getattr(unit_section, "framework", None) if unit_section else None
    unit_selected = getattr(unit_section, "run_selected", None) if unit_section else None
    unit_probe_ctx = None
    if unit_framework == _FRAMEWORK and unit_selected:
        unit_cwd = repo / (
            unit_section.cwd if unit_section and unit_section.cwd != "." else "."
        )
        unit_probe_ctx = (unit_selected, unit_cwd)
    return base_argv, cwd, int_probe_ctx, unit_probe_ctx


def _probe_skip_reason(contract) -> str:
    _FRAMEWORK = "py" + "test"
    integration = getattr(contract, "integration", None)
    int_framework = getattr(integration, "framework", None) if integration else None
    if int_framework != _FRAMEWORK:
        return (
            f"anchor probe skipped: non-{_FRAMEWORK} contract "
            f"(integration.framework={int_framework!r})"
        )
    if not (getattr(integration, "run", None) if integration else None):
        return "anchor probe skipped: no integration run command"
    return "anchor probe skipped: contract run unparsable"


def _probe_one_task(
    report, repo, task, base_argv, cwd, int_probe_ctx, unit_probe_ctx,
    exempt=False,
) -> None:
    """Probe one task's acceptance anchors + on-tree unit anchors (E1)."""
    refs = tuple(getattr(task, "acceptance_refs", None) or task.test_refs or ())
    if refs:
        if int_probe_ctx is not None:
            # #219 P-a: measure exactly the anchor nodes (seconds); never
            # the union-with-directory full-suite run (minutes, timeout).
            probe = _run_unit_probe(int_probe_ctx, list(refs))
            if probe is None:
                report.probes.append(
                    AnchorProbe(
                        task.task_id, "skipped", refs,
                        "acceptance probe argv expansion failed",
                    )
                )
        else:
            probe = _run_with_argv([*base_argv, *refs], cwd)
        if probe is not None:
            probe.task_id = task.task_id
            probe.refs = refs
            _apply_type_rule(report, probe, task, "test_refs", exempt=exempt)
    # E1 (split-RED fix): ALSO probe unit_refs whose test files already
    # exist on the tree — a green on-arrival unit anchor means the RED
    # precondition is structurally unsatisfiable (retry/split residual).
    # #225: integration (walk_red) tasks are exempt from the E1 type rule —
    # their unit pins are green BY DESIGN (_do_walk_red seals the R-tree
    # against unit_refs expecting green; "An integration task carries no
    # new RED unit test to write"). The green-on-arrival danger E1 guards
    # against only exists for standard-RGR tasks whose Devon RED a green
    # anchor would hollow out. Without this exemption the walk_red
    # unit_refs requirement (plan_defect on empty) and rule 1 (hard reject
    # on green) form an unsatisfiable pair: no graph can pass both gates.
    unit_refs = tuple(getattr(task, "unit_refs", None) or ())
    is_integration = bool(getattr(task, "integration", False))
    existing_units = [r for r in unit_refs if (repo / r.split("::")[0]).is_file()]
    if existing_units and unit_probe_ctx is not None:
        unit_probe = _run_unit_probe(unit_probe_ctx, existing_units)
        if unit_probe is not None:
            unit_probe.task_id = task.task_id
            unit_probe.refs = tuple(existing_units)
            if unit_probe.status == "green" and is_integration:
                # walk_red pins: planning-time confirmation that the unit
                # R-tree stays green is signal, not a graph defect.
                report.probes.append(unit_probe)
            elif unit_probe.status == "green":
                _apply_type_rule(
                    report, unit_probe, task, "unit_refs", exempt=exempt
                )
            else:
                report.probes.append(unit_probe)
    if not refs and not existing_units:
        report.probes.append(
            AnchorProbe(task.task_id, "skipped", refs, "no acceptance refs")
        )


def _run_unit_probe(ctx, nodes: list[str]):
    """OOB-F-2: run exactly the on-tree unit anchors via the section's
    run_selected template ({nodes}/{result} expanded), never the
    whole-directory run command (pytest union semantics would measure the
    full suite instead of the task's anchors)."""
    import tempfile

    template, cwd = ctx
    fd, result_path = tempfile.mkstemp(suffix=".xml", prefix="anchor-probe-")
    os.close(fd)
    try:
        argv = template.replace("{nodes}", " ".join(f'"{n}"' for n in nodes)).replace(
            "{result}", result_path
        )
        return _run_with_argv(shlex.split(argv), cwd)
    except (ValueError, OSError):
        return None
    finally:
        with contextlib.suppress(OSError):
            os.unlink(result_path)


def _apply_type_rule(
    report: ProbeReport, probe: AnchorProbe, task, ref_kind: str = "test_refs",
    exempt=False,
) -> None:
    """规则 1：锚已绿 + 非 verification-only → 硬门禁（r1 回滚 #2）。
    OOB-A-3: ref_kind names what was measured (test_refs vs unit_refs).
    #222: retained-completion tasks are exempt — they are never dispatched
    (selection excludes the B83 projection), so the standard-RGR green-anchor
    danger rule 1 guards against cannot arise for them; the probe result is
    still recorded (planning-time confirmation that delivered anchors stay
    green is signal, not noise)."""
    is_verification = _is_verification_marked(task.description or "")
    if probe.status == "green" and not is_verification and not exempt:
        report.errors.append(
            f"TG 任务型裁定（#34）：{task.task_id} 的 {ref_kind} 在规划期实测"
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
