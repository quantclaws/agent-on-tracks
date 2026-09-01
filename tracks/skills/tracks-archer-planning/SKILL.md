---
name: tracks-archer-planning
version: 0.1
description: Archer M-IMPL PLANNING 专用方法论 - tasks.json schema v2 任务图、锚点分家、scope_boundary 写域、depends_on 可满足性、存在性门禁、预算约束与 PLAN 层标准（仅 Archer 的 M-IMPL PLANNING 阶段触发）
---

# tracks-archer-planning — M-IMPL PLANNING 图约束与 PLAN 层标准

只供 role=archer 且处于 M-IMPL PLANNING 时使用。本 skill 承载任务图的全部详尽约束与阶段标准；宿主中立：实际落盘路径、命令与目录取值由 assignment / project contract 提供，本 skill 不固化任何宿主技术栈。

## 输入合同

- 输入：冻结基线、M-DESIGN 三文档与六元组 closure、Runtime 注入的锚点依赖数据。
- M-DESIGN 产出的六元组是 ISLAND_GATE_1 的输入合同：PLANNING 期把它当作既定事实复核（缺漏回退设计阶段补齐），不在规划期重新发明设计。
- 唯一权威产物是 `.tracks/projects/<version>/tasks.json`（协议资产路径）。

## tasks.json schema v2

- 根标记 `"schema": 2`；旧的合并 `test_refs` 字段已禁用，不得出现在任何 task 中。
- 每 task 用两个显式字段分家声明测试锚点：`unit_refs` 与 `acceptance_refs`。
- 每 task 另声明 `scope_boundary`（写域白名单）、`depends_on`（依赖任务集合）与本 task 实现的 IF- 集合。
- 修订轮（Prism revise 后重派）必须直接编辑 tasks.json 本体；返回前自查 findings 已逐条落进 tasks.json。

## 锚点分家：unit_refs / acceptance_refs

- `unit_refs`：本 task 的 RED 义务（逻辑层：unit，可为空列表）——通用 RED 义务已由 R commit manifest 覆盖 unit 层，仅在需要点名特定单测时声明。
- `acceptance_refs`：本 task 的验收锚点（逻辑层：integration，非空）——对应 test-plan §8 的 integration 行目标，本 task 落地后必须转绿；默认落盘约定为 tests/integration，实际路径由 assignment / project contract 运行时提供。
- §8 行内的 e2e 层目标是终态覆盖锚点，由 ISLAND_GATE_2 / FULL 全量兜底，绝不写进任何 task 的 `acceptance_refs`；混合行（integration + e2e）的验收归属只看其 integration 项。
- 若某任务因此没有可声明的 integration 目标，说明该 §8 行的 integration 项已由依赖链前序任务覆盖：要么合并进那些任务，要么重新拆分出自己可转绿的 integration 切片，不得用 e2e 顶替。
- 锚点归属判据：一个 §8 多 IF 行的锚点声明给「使其可行绿的那个任务」——通常是交付该行最后一个 IF 的任务（依赖序上最后落地的 owner）；结构上不可能在本 task GREEN 时刻转绿的锚点不得声明给它。

## scope_boundary（写域白名单）

- `scope_boundary` 声明的是实现者将要修改的文件，必须完全落在 project contract 声明的实现者可写范围内；实际范围取值由 assignment / project contract 提供。
- `acceptance_refs` 指向的验收测试文件是验收锚点，绝不进入 scope_boundary——它们由 Shield 在 M-TEST 阶段编写，实现者无权修改。实现某 IF 合同的任务，其 scope 是那些产品文件，而不是验收它的测试文件。
- scope 完整性判据：一个任务的 `scope_boundary` 必须包含「让本任务全部 `acceptance_refs` 转绿所必需修改的每一个文件」。
- facade 接线所有权：若 scaffold stub 的公共入口（facade 函数/类）与其行为实现被拆到不同文件，「实现行为的任务」与「在该公共入口接通它的任务」是同一个任务——两个文件都进该任务的 scope_boundary；禁止把 facade 划给前置任务、把实现划给后置任务的配对切分。
- import 便利不得扩大写域：包标记/re-export 模块不在任何任务 scope 内时，应使用直接模块导入；不要为"import 好写"把包标记文件塞进 scope_boundary，除非它本身就是本任务声明的公共入口。
- 宁宽勿碎：当两个任务必须共享文件时，合并为一个任务（双 IF 声明），而不是按文件行数或职责纯度再切；runtime 的 scope gate 禁止任何两任务文件重叠，唯一安全的原子边界是"文件集合并集"。

## depends_on 与可满足性

- 承载验收锚点的任务必须声明 depends_on，覆盖其锚点所 exercise 的每一个实现模块的 owner 任务（含传递闭包）；锚点直接调用的模块的 owner 未先落地，锚点将在无人转绿的时刻失败，浪费整个 RED/GREEN 循环。
- Runtime 注入的 anchor_surface 数据把每个锚点按静态解析（ast_modules）与动态加载（dynamic_modules）拆分依赖清单：静态解析映射到文件后若属于某任务 scope 且不在本任务 depends_on 传递闭包内，commit 时硬拒，必须补边；动态加载独有的依赖仅进 advisory，不硬拒。
- depends_on 必须由注入数据推导，不做文件名猜测；修订轮自查先读注入的缺边清单，对每个承载锚点的任务逐锚点检查，缺边就补边——这是图结构缺陷，改解释或改 tasks.md 投影都不是修复。

## 存在性门禁与 tasks.md 投影

- scope_boundary 的每个路径必须「存在于仓库树」或「在冻结设计文档（architecture.md / test-plan.md）中逐字声明为交付文件」，两者皆非则硬拒（幻觉路径拦截）。
- tasks.md 是 runtime 从 tasks.json 渲染的只读投影，Archer 绝不直接写 tasks.md；篡改当轮判违约并回滚，只改 tasks.json。

## 预算 / batch / parallel

- 任务图遵守 assignment 声明的任务预算、批大小与并行度约束（具体数值由 assignment 提供）；超出预算时优先合并同域任务而不是削掉验收锚点。
- 图的调度顺序由依赖结构决定：空 depends_on 的任务最先调度，因此承载锚点的任务绝不能空依赖。

## 讨论协议（展开）

- 讨论一律经 Runtime 注入的讨论机制（相关 skill 与命令由 Runtime 注入上下文提供），不手工编辑 blockquote。
- 禁止对自建 inline 讨论线程自评 resolved；finding 仅能由非作者以新证据关闭。
- Prism 发起的线程由 Prism 关闭；修订轮先处理待办，再改 tasks.json 本体。

## 质量标准（PLAN 层）

- PLAN-01 tasks.json 唯一权威：schema v2 根标记完整，修订直接编辑本体。
- PLAN-02 锚点分家正确：unit_refs 可空、acceptance_refs 非空且只含 integration 逻辑层，e2e 不入锚点。
- PLAN-03 锚点归属正确：声明给使其可行绿的最后 owner。
- PLAN-04 scope 合规：仅含实现者可写文件、无重叠、锚点不入 scope、完整性判据满足。
- PLAN-05 依赖可满足：锚点显式引用的所有 owner 均在 depends_on 传递闭包内，静态缺边清零。
- PLAN-06 存在性通过：scope 每个路径存在于仓库树或被冻结设计声明。
- PLAN-07 tasks.md 只读投影：Archer 不写 tasks.md。
- PLAN-08 预算合规：预算/批/并行约束满足且不牺牲锚点覆盖。

## 退出前自审（程序性动作，引用 ID）

- tasks.json 已按 findings 逐条修改并保存？（PLAN-01）
- 锚点分家与归属已逐任务复核？（PLAN-02 / PLAN-03）
- scope_boundary 白名单、重叠与完整性已自查？（PLAN-04）
- depends_on 缺边清单已清零？（PLAN-05）
- 存在性门禁已通过？（PLAN-06）
- tasks.md 未被直接写入？（PLAN-07）
- 预算与批/并行约束已满足？（PLAN-08）
- 讨论待办已处理且未代操作他人线程？（见「讨论协议」）
