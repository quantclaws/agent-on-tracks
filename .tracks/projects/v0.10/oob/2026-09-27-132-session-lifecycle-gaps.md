# OOB 修复记录：#132 会话生命周期在默认配置下的两个失效点

> 状态：reviewed（Prism verdict: pass，3 advisory 已处置），2026-09-27。执行：ZCode（Human Aaron 授权：计划/实施/Prism 审核）。
> Prism 审核记录：envelope verdict pass（session ses_f1d47248cffelF4wwchY5CfKQT，全输出 blob `oob-132-review-input.diff` 同目录评审链）；advisory A1/A2 已补测试 `test_classify_run_routes_by_work_unit_key_and_preserves_class`；A3（200K 上限较已知 1M 窗口激进，属有意的保守默认——窗口不可解析是常态而非例外，宁可早压缩）在此记录为裁定。
> 背景：#132 的三层机制（D-42，2026-09-19 OOB 已实施）+ 派发看门狗（`TRAC_AGENT_TIMEOUT` 无产出超时）在代码里**已存在**；本 OOB 修复的是 v0.10 run 01M3E7SAANXKW1V73W8B8Q3G86 实测暴露的两个"设计已定、接线已死"失效点。计划稿：`../planning/oob-132-plan.md`（范围经代码核证后收窄）。

## 事故复述

Archer 设计派发会话膨胀至 319K token 后连续撞 `inference exceeds tpm/rpm limit`（napi/kimi-k3）：85 分钟派发 exit 1 → 30s 退避重派 → 14 分钟再 exit 1 → M7 换手。注册表实测：`archer:design` 319K / `lex:LEX_REVIEW` 400K，`compacted` 均为 false——**压缩闭环从未触发，硬崩清会话从未发生**。

## G1：压缩闭环在默认配置下是死路

`_maybe_compact` 以 `self._effective_model(name)` 查模型窗口；`TRAC_AGENT_MODEL` 未设且无 per-agent 配置时该值为 None → `_model_ctx_limits().get("")` 必然落空 → fail-open 直接 return。D-42 的"解析失败 → 跳过压缩"把**默认配置**（最常见形态）整个排除在三层机制之外。

**修复**：窗口不可解析时回退绝对上限 `TRAC_SESSION_COMPACT_ABS_TOKENS`（默认 200,000；≤0 恢复旧 fail-open 逃生）。可解析窗口仍优先（85% 阈值不变）。

## G2：硬崩清会话是死接线

`_recover_run_error` 的 non_zero_exit 清会话分支（2026-09-18 裁定）写在 `_await_run` 的异常路径里，但该路径只承接 `_finalize_run` 的 timeout 类异常；`non_zero_exit`/`provider_unavailable`/`signal` 由 `_check_json` 抛出——而它在 act() 里被**直接调用**，异常上抛不经会话健康缝。单测直接测 `_recover_run_error`（通过），接线断了没人看见。

**修复**：新增 `_classify_run(proc, name, key)`——`_check_json` 的异常先喂 `_recover_run_error` 再原样上抛；act() 改走该入口。瞬态类（timeout/provider/signal）语义不变（D-39 续传）。

## 若两修复在位，本次事故的轨迹

- 第一次 exit 1 → G2 清会话 → 重派冷启动（小上下文，B92 自包含注入承载状态）→ 不再撞墙；即便冷启动后再度增长 → G1 在 85%×200K 前触发压缩/弃会话。85 分钟白烧与 M7 换手均不会发生。

## 验证

- 单测：`tests/unit/test_opencode_session_reuse.py` 新增 5 例（接线 non_zero_exit 清会话、瞬态保留、绝对上限触发/关闭逃生、可解析窗口优先）；既有 `test_maybe_compact_below_threshold_or_unknown_model_is_noop` 按新契约修订（显式关兜底才保持旧 fail-open）。
- `tests/integration/test_phase0_quality_seal.py::test_guard_hardened_violations_zero_revisions_audited` 在**未含本修复的干净基线**上同样失败（预先存在，疑与工作树上 Archer 的 pyproject/合同未提交修改或基线漂移相关）——非本 OOB 回归，移交 v0.10 旅程的 M-TEST/M-VERIFY 处置。

## 边界与回退

- 不改 kernel、不改 act() 协议、不动 D-42 键结构；纯 effects 层。
- 逃生开关：`TRAC_SESSION_COMPACT_ABS_TOKENS=0`（恢复 G1 旧行为）；G2 语义即 2026-09-18 已裁定语义，无新增开关。
- 会话记忆损失面：与既有裁定一致（"runtime 传任务，agent 重建自己的探索"）。
