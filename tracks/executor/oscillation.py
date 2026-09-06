"""M1-S1 振荡检测：签名路由器的核心签名（收敛简化方案 2026-09-05）。

机械判据（纯事件日志算术，无 agent 参与）：同一 task 相邻两次
GREEN_GATE verdict.failed(impl_defect) 的失败锚点集合发生对调——
上次失败的锚点转绿（healed≠∅）∧ 上次通过的锚点转红（newly_red≠∅）
∧ 仍有共同失败（common≠∅，同一失败族仍在场，排除"两次全然不同的
失败"的误报）。

对调特征 = 锚点互斥/契约矛盾：任何单写者重试在两个锚点之间
ping-pong，budget 必烧穿（实证：T-042 的 test_verify_candidate.py:150
↔ test_inplace_repair.py::test_fix_new_candidate_rewalks_verify，
2026-09-04，约 40 次派发空转）。检出即路由契约权威（Archer
RULING，双边可视），跳过一切写者重试。

时点契约：检测发生在**第二次**失败落 verdict 之前——当前失败的
锚点来自 TaskSelectionFailure.evidence（尚未入库），上一次失败从
事件流取最近一条。中间若出现该 task 的 verdict.passed（如 retry 后
转绿）即重置：振荡只定义在相邻失败之间，跨成功窗口不比。
"""

from __future__ import annotations

import json

OSCILLATION_CHECK = "contract_conflict"


def _node_id(node) -> str:
    """归一化单个失败锚点为稳定字符串 id。

    GREEN_GATE 的 TaskSelectionFailure.evidence 里 failed_nodes 是
    [{"node": ..., "status": ..., "detail": ...}] 的 dict 列表；锚点
    id 取其 "node" 字段。非 dict 条目按字符串处理。
    """
    if isinstance(node, dict):
        return str(node.get("node") or "")
    return str(node) if node else ""


def parse_failed_nodes(evidence) -> list[str]:
    """从 verdict.failed 的 evidence（JSON 字符串或 dict）解析失败锚点。

    非 JSON/缺 failed_nodes/空列表一律返回 []（fail-closed：解析不出
    锚点就不参与检测，绝不猜）。
    """
    if isinstance(evidence, str):
        try:
            data = json.loads(evidence)
        except (ValueError, TypeError):
            return []
    elif isinstance(evidence, dict):
        data = evidence
    else:
        return []
    if not isinstance(data, dict):
        return []
    nodes = data.get("failed_nodes")
    if not isinstance(nodes, list):
        return []
    ids = [_node_id(node) for node in nodes]
    return [node for node in ids if node]


def detect(prev: list[str], curr: list[str]) -> dict | None:
    """相邻两次失败锚点集合的对调检测（S1 签名）。

    三条件合取：healed≠∅ ∧ newly_red≠∅ ∧ common≠∅。命中返回
    {"healed", "newly_red", "common"}（排序稳定，事件 payload 可
    replay），否则 None。
    """
    prev_set, curr_set = set(prev), set(curr)
    healed = sorted(prev_set - curr_set)
    newly_red = sorted(curr_set - prev_set)
    common = sorted(prev_set & curr_set)
    if healed and newly_red and common:
        return {"healed": healed, "newly_red": newly_red, "common": common}
    return None


def last_failed_nodes(events: list[dict], task_id: str) -> list[str] | None:
    """事件流中该 task 最近一次失败（impl_defect 或 contract_conflict）的锚点集合。

    events: [{"type": ..., "payload": {...}}, ...]（seq 升序）。从尾
    向前扫：先遇到该 task 的 verdict.passed（check=green）即返回
    None——成功重置振荡窗口；先遇到可解析的失败即返回其锚点。
    无历史失败返回 None（首败必放行，永不误报）。

    OOB 2026-09-05（基线推进修复）：contract_conflict（S1 自身的落库
    verdict）也推进基线——否则 S1 触发后基线被永久跳过，replan 合法
    带回 GREEN_GATE 时，新失败永远与振荡前的陈旧基线比较，同一签名
    无限重触发（run 01M19FJVES7G113RD8QXXY3PQZ seq 3016==3054 实证）。
    该 verdict 的 evidence 自带 failed_nodes（发射侧同批修复）。
    """
    for ev in reversed(events):
        if ev.get("type") not in ("verdict.failed", "verdict.passed"):
            continue
        payload = ev.get("payload") or {}
        if payload.get("task_id") != task_id:
            continue
        if ev.get("type") == "verdict.passed":
            return None
        if payload.get("check") not in ("impl_defect", "contract_conflict"):
            continue
        nodes = parse_failed_nodes(payload.get("evidence"))
        if nodes:
            return nodes
    return None


def detect_oscillation(
    events: list[dict],
    task_id: str,
    current_evidence,
    prior_oscillation: dict | None = None,
) -> dict | None:
    """S1 完整判定：当前失败（未入库）vs 最近一次已入库失败。

    当前失败锚点解析不出（fail-closed）或无前次失败时返回 None。

    OOB 2026-09-06（可逆性判据）：仅 healed∧newly_red∧common 的换血
    形态会把写手的**正常施工扰动**（修绿一批、碰红另一批——大任务每轮
    局部进展的常态）误判为契约冲突，把每一轮都升级成权威战争、写手被
    饿死（run 01M19FJVES7G113RD8QXXY3PQZ：05:09-07:03 两小时零写手
    派发）。真乒乓的判据是**同一批锚点来回换边**：``prior_oscillation``
    （上一次 S1 检出的 {healed, newly_red}，由调用方从 blob 解析传入——
    本函数纯、无 I/O）与当前签名的换边集合有交集才升级。首次换边
    （prior_oscillation=None，或无交集）是普通 impl_defect，写手按
    证据再试一轮；锚点第二次换边才路由权威。对昨日真互斥首案：第二次
    换边检出——多付一轮写手重试，换掉对一切局部进展的误升级。
    """
    current_nodes = parse_failed_nodes(current_evidence)
    if not current_nodes:
        return None
    prev_nodes = last_failed_nodes(events, task_id)
    if not prev_nodes:
        return None
    signature = detect(prev_nodes, current_nodes)
    if signature is None:
        return None
    if prior_oscillation is None:
        return signature  # first-ever firing: legacy semantics
    prior_healed = set(prior_oscillation.get("healed") or [])
    prior_newly_red = set(prior_oscillation.get("newly_red") or [])
    reversible = bool(
        set(signature["healed"]) & prior_newly_red
        or set(signature["newly_red"]) & prior_healed
    )
    if not reversible:
        return None  # fresh churn, not a repeat swap: the writer's domain
    return signature
