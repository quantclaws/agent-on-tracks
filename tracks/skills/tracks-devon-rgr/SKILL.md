---
envelope: tracks-envelope:v2
name: tracks-devon-rgr
version: 0.2
description: Devon M-IMPL 单一 RGR 阶段操作清单。当 Devon 按 assignment.phase 执行 red/green/refactor 之一、需要 fail-closed 校验、phase-specific 写约束或 evidence 自检时使用。
---

# tracks-devon-rgr：Devon 单一 RGR 阶段操作清单

Devon 每次 dispatch 只执行 `assignment.phase` 指定的**一个** RGR 阶段（red | green | refactor），完成后立即停止，绝不跑完整 RGR 循环。本 skill 是 Devon 的唯一操作 skill；Devon 不发起/回复讨论线程（不注入 tracks-discuz）。

输出格式与 evidence 字段权威：见 core 的条件式 envelope 合同与 assignment 注入的 `evidence_contract`——本 skill 不复制、不示例化 JSON schema。

## 1. Fail-closed 校验（所有阶段）

开始前验证 assignment 包含以下键，缺失任一则 **fail closed**（返回 `stale|scope_gap|design_gap|requirement_gap`），不得猜测：

| 键 | 用途 | 所有阶段 |
| --- | --- | --- |
| `task_id` | 当前 task identity | 必需 |
| `phase` | `red` \| `green` \| `refactor` | 必需 |
| `if_ids` | 本 task 接口 IF ID 列表 | 必需 |
| `ac_refs` | 验收条件引用 | 必需 |
| `test_refs` / `unit_refs` / `acceptance_refs` | 测试锚点（合并/分家合同；`unit_refs` 可为空——空列表不豁免通用 RED 义务：在 `red_test_paths` 下自行为本 task 的 IF 写失败测试，R manifest 会捕获它们） | 必需 |
| `commands` | test/guard/静态检查命令（assignment/project contract 提供的具体命令） | 必需 |
| `manifest` | `allowed_paths` + `forbidden_paths`（+ `red_test_paths` 等阶段写域，由 assignment `layout`/manifest 声明） | 必需 |
| `pre_dirty_snapshot` | dispatch 前快照 identity | 必需 |
| `result_identity` | baseline/candidate identity | 必需 |
| `r_tree_identity` | 不可变 R 基线 identity | GREEN/REFACTOR 必需 |

`acceptance_refs` 是验收锚点（Shield 冻结资产，对桩合法红），永远不是 Devon 的工件；GREEN 门会要求它们转绿，这是验收要求而非交付物。

## 2. 阶段操作清单

### RED（phase=red）

1. 确认 `assignment.phase == "red"`（DEVON-RED-1）。
2. **只添加/修改 unit test**；产品代码**禁止**修改（DEVON-RED-2）。
3. 禁止触碰：integration/e2e、tests assets、frozen Shield tests、`.tracks/projects/**`、task state、Issues、git history（DEVON-RED-3）。
4. 在 `manifest.red_test_paths`（RED 专用写域，具体目录由 assignment `layout`/manifest 声明）内写 failing unit test。`manifest.allowed_paths` 是 GREEN 阶段的 impl scope，RED 阶段不要写它（也不要写 allowed_paths 列出的文件）（DEVON-RED-4）。
5. 用 `assignment.commands` 提供的 unit 命令运行授权 unit test，看到目标失败——失败必须落在被测行为/桩的合同 token 上，而非装配错误（DEVON-RED-5）。
6. 交付前自检（DEVON-RED-6）：对本次改动文件运行 `assignment.commands` / project contract `[lint].check` 声明的静态检查命令；非零退出必须修复后再交付——RED_GATE 会以 `check=lint` 拒绝（不消耗 attempt）。
7. 按程序性自审核对 DEVON-RED-1..6 全部实际执行后，立即停止交付。不继续 Green（DEVON-RED-7）。

### GREEN（phase=green）

1. 确认 `assignment.phase == "green"` 且 `r_tree_identity` 存在（DEVON-GRN-1）。
2. **最小产品实现**使 R tests 通过；production 接入真实 composition root（DEVON-GRN-2）。
3. R tests 和所有 frozen tests **不可变**：不修改、skip、xfail、降低断言（DEVON-GRN-3）。
4. 只在 `manifest.allowed_paths` 范围内写（DEVON-GRN-4）。
5. 用 `assignment.commands` 提供的 unit/guard 命令自检；不 commit/push（DEVON-GRN-5）。
6. 交付前自检 lint（DEVON-GRN-6）：对本次改动的产品文件运行 `[lint].check` 声明的命令；非零退出必须修复——GREEN_GATE 会以 `check=lint` 拒绝（不消耗 attempt）。
7. no_change 语义（DEVON-GRN-7）：若评审 findings 无需代码改动（实现已在基线），可返回显式 `no_change` + reason。此时 `changed_paths` **必须**为空，`no_change_reason` 非空，pre/post identity 如实填写。**禁止**声称有 changed_paths 而 pre/post identity 相同——runtime 会比对 identity 并 fail-closed。
8. 按程序性自审核对 DEVON-GRN-1..7 后，立即停止交付。不继续 Refactor（DEVON-GRN-8）。

### REFACTOR（phase=refactor）

1. 确认 `assignment.phase == "refactor"` 且 `r_tree_identity` 存在（DEVON-REF-1）。
2. 在 Green 后重构，**保持 Green 行为不变**（DEVON-REF-2）。
3. 可返回显式 `no_change` + reason（若无需重构），语义同 DEVON-GRN-7：changed_paths 为空 + no_change_reason 非空 + identity 如实（DEVON-REF-3）。
4. 不得做 public-interface 变更，除非有上游 route 授权（DEVON-REF-4）。
5. 运行 manifest / `assignment.commands` 声明的 quality guards（DEVON-REF-5）。
6. 按程序性自审核对 DEVON-REF-1..5 后，立即停止交付（DEVON-REF-6）。

## 3. 命令与写域约定

- 一切 test/guard/静态检查命令来自 `assignment.commands` / project contract；不假设语言、虚拟环境、测试框架或并行参数，不发明命令。
- 实际落盘路径由 assignment `layout` / `manifest`（`allowed_paths`、`forbidden_paths`、`red_test_paths`）声明。
- bash 仅运行 manifest 允许的读取/build/unit/guards 命令；不运行 integration/e2e，不 commit/push，不运行 `trac gate`/`trac return`/`trac retry` 等流程命令。

## 4. Evidence 自检（assignment 权威）

- 最终回复按 assignment `evidence_contract`（或 Runtime validator）要求回显全部必填 evidence 字段，不遗漏、不伪造；字段名/类型/枚举/classification vocabulary 以 assignment 注入为权威，本 skill 不固定 schema。
- 不得伪造 PASS/stage/commit/identity；pre/post identity 如实填写，Runtime 会复算校验。
- 不 commit/push、管理 Issues 或触碰 task state。