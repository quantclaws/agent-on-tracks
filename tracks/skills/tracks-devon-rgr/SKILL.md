---
name: tracks-devon-rgr
version: 0.1
description: Devon M-IMPL 单一 RGR 阶段操作清单与输出 schema。当 Devon 按 assignment.phase 执行 red/green/refactor 之一、需要 fail-closed 校验、phase-specific 写约束或结构化 outcome 输出时使用。
---

# tracks-devon-rgr：Devon 单一 RGR 阶段操作清单

Devon 每次 dispatch 只执行 `assignment.phase` 指定的**一个** RGR 阶段（red | green | refactor），完成后立即停止。绝不在一个 assignment 内跑完整 RGR 循环。本 skill 是 Devon 的唯一操作 skill，替代不相关的 tracks-discuz（Devon 不发起/回复讨论线程）。

## 1. Fail-closed 校验（所有阶段）

开始前验证 assignment 包含以下键，缺失任一则 **fail closed**（返回 `stale|scope_gap|design_gap|requirement_gap`），不得猜测：

| 键 | 用途 | 所有阶段 |
| --- | --- | --- |
| `task_id` | 当前 task identity | 必需 |
| `phase` | `red` \| `green` \| `refactor` | 必需 |
| `if_ids` | 本 task 接口 IF ID 列表 | 必需 |
| `ac_refs` | 验收条件引用 | 必需 |
| `test_refs` | 授权 unit test 目标 | 必需 |
| `commands` | test/guard 命令（venv） | 必需 |
| `manifest` | `allowed_paths` + `forbidden_paths` | 必需 |
| `pre_dirty_snapshot` | dispatch 前快照 identity | 必需 |
| `result_identity` | baseline/candidate identity | 必需 |
| `r_tree_identity` | 不可变 R 基线 identity | GREEN/REFACTOR 必需 |

## 2. 阶段操作清单

### RED（phase=red）

1. 确认 `assignment.phase == "red"`。
2. **只添加/修改 unit test**。产品代码**禁止**修改。
3. 禁止触碰：integration/e2e、tests/assets、frozen Shield tests、`.tracks/projects/**`、task state、Issues、git history。
4. 在 `manifest.allowed_paths` 范围内写 unit test。
5. 运行授权 unit test，看到目标失败（失败落在被测行为/桩的合同 token 上，非装配错误）。
6. 完成后立即停止。不继续 Green。

### GREEN（phase=green）

1. 确认 `assignment.phase == "green"` 且 `r_tree_identity` 存在。
2. **最小产品实现**使 R tests 通过。production 接入真实 composition root。
3. R tests 和所有 frozen tests **不可变**：不修改、skip、xfail、降低断言。
4. 只在 `manifest.allowed_paths` 范围内写。
5. 不 commit/push。
6. 完成后立即停止。不继续 Refactor。

### REFACTOR（phase=refactor）

1. 确认 `assignment.phase == "refactor"` 且 `r_tree_identity` 存在。
2. 在 Green 后重构，**保持 Green 行为不变**。
3. 可返回显式 `no_change` + reason（若无需重构）。
4. 不得做 public-interface 变更，除非有上游 route 授权。
5. 运行 manifest 声明的 quality guards。
6. 完成后立即停止。

## 3. 命令约定

- Python：`.venv/bin/python -m pytest -n 4`（保留 project-required `--dist` mode，如 `--dist loadscope`）。
- bash 仅运行 manifest 允许的读取/build/unit/guards 命令。
- 不运行 integration/e2e，不 commit/push，不运行 `trac gate`/`trac return`/`trac retry`。

## 4. 输出 schema（结构化 outcome/audit evidence）

**交付纪律（2026-08-15，run 01KZTHE7 T-001 三连 red_invalid 教训）**：你的**最终回复必须以一个裸 JSON object 结束**——这是 Runtime 唯一的证据提取源（取你最后一条 text 消息中的 JSON）。散文总结、Markdown 报告、清单勾选（"✅ All Tasks Complete"）都**不构成交付**，无论工作做得多好，缺 JSON 即 red_invalid、attempt 作废。JSON 放在回复最末尾、独立成块、不加代码围栏以外的装饰。

输出必须包含以下字段：

```json
{
  "phase": "red|green|refactor",
  "changed_paths": ["path/to/file", "..."],
  "commands": [{"cmd": "...", "result": "pass|fail", "output_summary": "..."}],
  "manifest_compliance": true,
  "pre_identity": "...",
  "post_identity": "...",
  "r_identity": "... (GREEN/REFACTOR only)",
  "no_change_reason": "... (REFACTOR only, if applicable)",
  "implemented_if_ids": ["IF-001", "..."],
  "advisories": ["isolation|scope_gap|..."]
}
```

不得伪造 PASS/stage/commit。不得 commit/push、管理 Issues 或触碰 task state。
