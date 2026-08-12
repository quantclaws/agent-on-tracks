---
story_id: S-001
title: 真实 OpencodeBackend Devon 旅程与发布证据
created: 2026-08-12
status: draft
sha: 39dfb854ff6d9fc2844ab39fc0e8c67d4df0be2eec503a1df4ab1e7b7b33a899
---

# S-001: 真实 OpencodeBackend Devon 旅程与发布证据

<!--
  - §1 逐字记录 Human 原始输入，不修改、不转述。
  - §3 每条操作路径复制 3.1 的小节结构（3.2、3.3……）。修改类路径的「变更基线」必须写清
    当前行为与本次变更——下游（Sage/Archer/Shield/Devon）基于此描述变更，而非从零设计；
    新增路径写“无（新增路径）”。
  - §4 只提取需要下游继续展开的用户结果和重要边界，不枚举普通微交互；按路径顺序统一编号
    BS-01、BS-02……（不按路径分组）。
  - BS 编号文法（FR-0130）：`### BS-XX`（两位补零，按核心操作路径顺序统一编号，不按路径分组）。
    ID 一经分配不可变、不可复用；删除的 BS 留 tombstone。
  - 跨版本引用（FR-0130）：本版本内引用保持短格式；跨版本引用且存在歧义时附加版本限定
    `AC-FRXXXX-YY@<version>`（如 `AC-FR0010-01@v0.1`）。限定语法 opt-in，parser 不强制。
  - §5 各项没有时写“无”；Out-of-Scope 只记录明确排除或为防止明显范围扩张而必须记录的事项。
  - §6 每个问题一个段落，粗体一句话开头（格式见正文占位）；不编号、机器不校验。只写无法
    可靠推导、且不同答案会显著改变价值/范围/权限/数据安全/合规或产生不可逆后果的产品问题；
    技术选择不写。没有则整节写“无”。
  - §7 重要风险只写会改变范围或使 story 不成立的。

  不得使用超过3行以上的表格；可改用列表。
 -->

## 1. 原始输入

> v0.5 Q-04 release blocker repair: add a real OpencodeBackend Devon end-to-end M-IMPL journey that executes at least one task through RED, GREEN, REFACTOR, review, ISLAND_GATE_2 and boundary; bind auditable agent I/O and event evidence to the current candidate SHA; fake backend, simulated outcomes, and manually inserted stage events are not acceptable release evidence; make current successful live evidence a release prerequisite while allowing credential-less routine CI to skip the opt-in test.

> **Scribe [RESOLVED]:** TRIAGE blocker 3（不满足前置时的 release 行为与不可逆后果）：seed 要求 'credential-less routine CI 跳过 opt-in test'，但 release 必须以 live evidence 为前置。请问当 live evidence 缺失/过期/SHA 不匹配时，release 是什么行为？方向 A) fail closed——release 硬阻塞，Human 不可绕过（对应 flow §13.2 'Human 不能用发布确认绕过失败的门禁'）；B) Human 凭产品理由可 override（对应 flow Human release gate 的 return/delay 语义）；C) 仅 warning。不同答案决定不可逆发布副作用前的边界。请 Human 裁定。推荐 A，因为 seed 明确 fake/simulated/manual 证据不可接受，需 fail closed 才能守住 '不可接受' 语义。
>> **Maestro:** 裁定 A。live evidence 缺失、失败、过期、candidate SHA 不匹配，或证据来自 FakeBackend/simulated/manual events 时一律 fail closed；Human release approval 不得绕过。该结论由 seed 的 not acceptable 与 flow §13.2 可直接推导，属于程序门禁，不再升级 Human。
>> **Scribe:** Maestro 的裁定已落在 §3.2 第 70-72 行、BS-06 第 119-121 行及 §5 第 138 行：缺失、失败、过期、SHA 不匹配或非真实来源均 fail closed，Human release approval 不得绕过；失败后可重跑恢复。

> **Scribe [RESOLVED]:** TRIAGE blocker 2（candidate SHA 的权威定义与'当前/绑定'判定）：seed 要求 'bind auditable agent I/O and event evidence to the current candidate SHA'。v0.5 停在 M-IMPL 边界，flow.md 的 candidate freeze 属于未来 M-VERIFY。请问这里 'current candidate SHA' 指什么、如何判定新鲜度、以及 release 时如何校验证据 SHA 与候选一致？方向 A) candidate SHA = live run 时的 git HEAD（宿主仓库基线 commit），'current' = 与 release/校验时刻 HEAD 一致，不一致或缺失即 fail；B) candidate SHA = 特定 frozen candidate 标识（需要引入本版之外的 freeze 机制）；C) 只记录 run 时 SHA 不强制与 release 时刻比对。请 Human 裁定。推荐 A，因为它最贴合 '当前候选' 语义且不引入 M-VERIFY 之外的新机制。
>> **Maestro:** 裁定 A。v0.5 未实现 M-VERIFY candidate freeze，因此 candidate_sha 定义为真实 live run 启动时被测 tracks 仓库的 git HEAD；验证时必须等于待发布分支 HEAD。任一后续 commit 使证据 stale，必须对新 HEAD 重跑。证据同时绑定 run_id、backend=opencode、Agent I/O refs/digests 与事件区间。
>> **Scribe:** Maestro 的裁定已落在 §3.2 第 68-72 行、BS-03 第 101-103 行与 BS-05 第 113-115 行：candidate SHA 是 live run 启动时被测 tracks 仓库 HEAD，必须等于待发布分支 HEAD；证据绑定 run_id、backend=opencode、I/O refs/digests 与事件区间，后续 commit 使证据 stale。

> **Scribe [RESOLVED]:** TRIAGE go-判定前的 blocker 1（发布前置的落地载体，与 v0.5 范围边界冲突）：seed 要求 'make current successful live evidence a release prerequisite'，但 v0.5 spec/acceptance 明确只停在 M-IMPL→M-VERIFY 边界（FR-0160 BS-14，不实现 M-VERIFY/M-RELEASE，flow.md §13 M-RELEASE 为未来 Human gate）。请问这个 'release prerequisite' 的强制点落在哪里？方向 A) 在本 v0.5 内新增一个程序门禁/命令（如 trac check release-evidence），在边界出口校验 live evidence 存在、新鲜且绑定当前 candidate SHA；B) 仅将 live evidence 产出并记录为可审计工件，'作为 release 前置' 以文档/人工核对形式声明，不建自动 gate；C) 把本 story 范围扩展到实现 release gate。不同答案会显著改变本 story 的 scope。请 Human 裁定。推荐 A，因为它与 'release blocker repair' 意图一致且可程序验证。
>> **Maestro:** 裁定 A，但不扩展到完整 M-RELEASE：在 v0.5 增加程序化 release-evidence 检查（命令名由 Sage/Archer 按现有 CLI 约定确定），验证 current live evidence；M-IMPL boundary 仍是产品流程边界。routine CI 无凭据可 skip live test，但发布候选的 release verification 必须显式执行该检查并 fail closed。
>> **Scribe:** Maestro 的裁定已落在 §3.2 第 65-72 行、BS-08 第 131-133 行与 §5 第 137-139 行：在 v0.5 增加程序化 release-evidence 检查，但不实现完整 M-VERIFY/M-RELEASE；发布验证必须显式执行并 fail closed，例行 credential-less CI 仍可 skip。

## 2. 用户意图

- 面向使用 tracks CLI 和 CI 的开发者/发布操作者，验证 v0.5 的 M-IMPL 不只是由 FakeBackend 模拟可用，而是真实 OpencodeBackend 驱动的 Devon 旅程也能完成并留下可审计结果。
- 当前受阻：v0.5 已有 FakeBackend 的 M-IMPL 完整路径，OpencodeBackend 也有真实进程与分层 live smoke 能力，但尚未把真实 Devon 从一个 task 的 RED、GREEN、REFACTOR、review 一直跑到 ISLAND_GATE_2 和 M-IMPL 边界；因此没有可以作为发布依据的真实、当前且可追溯证据。
- 完成后：操作者从现有终端入口选择 opt-in live 旅程，能在公开的运行状态、事件、Git 结果和审计证据中看到真实 Devon 完成至少一个 task；随后可执行 release-evidence 检查，只有当前成功的 live 证据才满足发布前置。没有凭据的例行 CI 仍可跳过该 opt-in 测试，但跳过不等于发布证据。

## 3. 核心操作路径

### 3.1. 真实 OpencodeBackend Devon 的 M-IMPL 旅程

- **变更基线**：修改/扩展 — 当前 v0.5 的 FakeBackend 端到端旅程可以从 M-TEST EXIT 走过 M-IMPL 并停在边界；OpencodeBackend 已能承载真实 agent 进程和单次 live smoke，但没有真实 Devon 驱动的完整 M-IMPL 旅程及其发布级证据。本次保留既有 M-IMPL 流程语义，补上真实 backend 的端到端验证路径。
- **入口/触发**：操作者在待验证分支的当前工作区，从既有 `trac run`/live 测试入口显式 opt in，并提供真实 OpencodeBackend 所需的凭据；运行从 M-TEST 已退出、测试资产已冻结的 M-IMPL BASELINE 开始。

1. Runtime 用真实 OpencodeBackend 派发 Devon，至少选择一个可执行 task；该 task 必须先经过 RED，再进入 GREEN 和 REFACTOR，而不是由 FakeBackend、模拟 outcome 或人工补写 stage event 代替。
2. 真实旅程完成对应的 review、task 完成以及 ISLAND_GATE_2 所需的程序检查；操作者通过现有终端活动、状态/事件记录和 Git 结果看到旅程确实经过这些阶段。
3. 运行成功后，M-IMPL 发出边界退出结果；同时保留该次真实 agent I/O、事件和 Git/运行审计证据，供下一条路径的 release-evidence 检查消费。

- **完成结果**：成功时至少一个 task 的真实 RGR 旅程和 M-IMPL 边界结果可从公开运行记录验证，操作者继续执行 release-evidence 检查；失败、取消或证据不完整时只显示失败/未完成状态，不产生可用于发布的成功证据，修复后应对当前候选重新运行。

### 3.2. 当前 live 证据的发布前置检查

- **变更基线**：新增 — 当前流程在 M-IMPL→M-VERIFY 边界结束，尚没有一个程序化检查把“真实且当前的 live evidence”作为发布前置；M-VERIFY/M-RELEASE 仍是后续流程阶段。本次增加的是边界外可调用的证据检查，不把 v0.5 扩展成完整发布阶段。
- **入口/触发**：真实旅程完成后，或发布操作者准备验证候选时，通过现有 `trac` CLI/CI 检查入口，对待发布分支的当前候选运行 release-evidence 检查。

1. 检查读取最近一次成功的真实 live 旅程及其审计记录，确认记录来自 `backend=opencode`，并能追溯到该次运行的 agent I/O、事件区间和相关 Git 结果。
2. 将 candidate SHA 定义为 live run 启动时被测 tracks 仓库的 git HEAD，并与检查时待发布分支 HEAD 比对；live run 之后任何 commit 都使旧证据失效，必须对新 HEAD 重跑。
3. 检查通过时，在 CLI/CI 的可见结果中报告发布前置满足；缺失、失败、过期、SHA 不匹配，或证据来自 FakeBackend、simulated outcome、manual stage event 时，报告未满足及可恢复的下一步，并阻止发布继续使用该证据。

- **完成结果**：通过结果只证明“当前候选有真实成功 live evidence”，不代替未来的 M-VERIFY/M-RELEASE；失败结果是 fail closed，Human 的发布确认不能绕过，操作者可在当前 HEAD 重新执行真实旅程后再次检查。

### 3.3. 无凭据的例行 CI 与 opt-in 例外

- **变更基线**：修改 — 当前测试计划已允许缺少真实 provider 凭据时跳过 live 通道，例行 CI 仍以确定性的 unit/integration/e2e 通道为主；但当前没有把“跳过”与“发布证据不可用”明确分开。本次保留例行 CI 的可运行性，同时明确发布候选的证据要求。
- **入口/触发**：例行 CI 在没有 live provider 凭据，或在具备凭据而显式启用 live 通道时运行同一套检查。

1. 无凭据时，CI 只跳过 opt-in 的真实 Devon 旅程并清楚报告 skipped，其他例行检查照常执行；不得把 skip 伪装成 live success。
2. 有凭据且启用时，CI 执行真实旅程并报告成功、失败或取消；成功产出可供发布前置检查核验的 evidence，失败不产出成功证据。
3. 发布验证始终显式执行 release-evidence 检查；例行 CI 的 fake/simulated 结果或 credential-less skip 都不能替代它。

- **完成结果**：日常开发不会因没有凭据而被迫连接真实 provider；发布操作者仍能从检查结果区分 skipped、failed 和 current success，并知道缺少 current live evidence 时必须回到 3.1，而不是绕过门禁。

## 4. 行为种子

### BS-01 真实 OpencodeBackend 执行

- EARS: `WHEN 操作者为 M-IMPL live 旅程显式提供真实 provider 凭据并 opt in, THE 系统 SHALL 使用真实 OpencodeBackend 派发 Devon 至少完成一个 task，而不是使用 FakeBackend、模拟 outcome 或人工 stage event`
- 来源: [3.1 / 用户明确要求 / 重要推导]
- 说明: 保护“release blocker repair”真正验证生产 agent 通道，而不是只验证模拟通道。

### BS-02 阶段旅程可验证

- EARS: `WHEN 真实 Devon task 开始执行, THE 系统 SHALL 让操作者从公开运行结果验证该 task 依次经过 RED、GREEN、REFACTOR、review、task 完成、ISLAND_GATE_2 并到达 M-IMPL boundary`
- 来源: [3.1 / 用户明确要求 / v0.5 M-IMPL 既有流程事实]
- 说明: 保护从入口到边界的完整用户旅程，避免以单次 agent smoke 冒充阶段集成。

### BS-03 审计证据绑定

- EARS: `WHEN 真实 live 旅程成功完成, THE 系统 SHALL 保存可审计的 agent I/O 与事件证据，并将其关联到该次 run、真实 opencode backend、事件区间及 candidate SHA`
- 来源: [3.1 / 3.2 / 用户明确要求 / 现有 OpencodeBackend 审计与脱敏惯例]
- 说明: 保护发布判断可以回溯到实际运行，而不是依赖 agent 的自述。

### BS-04 证据真实性边界

- EARS: `IF live 旅程由 FakeBackend、simulated outcome、manual stage event 驱动，或旅程未成功到达边界, THE 系统 SHALL 不将其标记为可用于发布的 live evidence`
- 来源: [3.1 / 3.2 / 用户明确要求]
- 说明: 保护证据真实性和失败不伪报成功。

### BS-05 当前候选 SHA 校验

- EARS: `WHEN release-evidence 检查运行, THE 系统 SHALL 将 candidate SHA 判定为 live run 启动时的 git HEAD，并要求它与待发布分支当前 HEAD 一致`
- 来源: [3.2 / Maestro 对 TRIAGE blocker 2 的裁定 / 重要推导]
- 说明: 保护“current candidate”语义；后续 commit 会使旧证据 stale，必须重跑。

### BS-06 发布前置硬阻塞

- EARS: `IF live evidence 缺失、失败、过期、candidate SHA 不匹配，或证据来源为 FakeBackend、simulated outcome、manual stage event, THE 系统 SHALL fail closed 阻止该候选以此证据继续发布，且 Human release approval SHALL NOT 绕过该结果`
- 来源: [3.2 / Maestro 对 TRIAGE blocker 3 的裁定 / flow.md §13.2]
- 说明: 保护不可逆发布副作用之前的真实性门禁；失败后仍可在当前 HEAD 重跑并恢复。

### BS-07 无凭据例行 CI 跳过

- EARS: `WHERE 例行 CI 没有真实 provider 凭据, THE 系统 SHALL 允许跳过 opt-in live 测试并继续例行 CI，但 SHALL 不把该 skip 或 fake/simulated 结果当作 current successful live evidence`
- 来源: [3.3 / 用户明确要求 / Maestro 对 TRIAGE blocker 1 的裁定]
- 说明: 在保持日常 CI 可运行的同时，不削弱发布候选的证据要求。

### BS-08 发布前置满足

- EARS: `WHEN current successful live evidence 与待发布分支 HEAD、运行身份和审计记录全部匹配, THE 系统 SHALL 在现有 CLI/CI 检查结果中报告 release prerequisite satisfied`
- 来源: [3.2 / 用户明确要求 / 重要推导]
- 说明: 让操作者能看到可继续发布验证的明确结果，而不必从日志自行猜测。

## 5. 范围、约束与例外

- **必须保持的产品约束**：交付面沿用现有 terminal CLI 与 CI 入口；M-IMPL 仍从 M-TEST EXIT 进入并停在 M-IMPL→M-VERIFY boundary，不实现 M-VERIFY、M-RELEASE 或后续发布副作用；M-IMPL 的阶段退出仍以程序证据为依据，不新增 Human 门禁。FakeBackend 继续服务确定性例行测试，但其结果不能成为 release evidence。candidate SHA 按真实 live run 启动时的 tracks 仓库 HEAD 定义，并须与待发布分支当前 HEAD 相等。成功 live evidence 是发布前置；agent I/O/事件证据必须可审计且沿用现有凭据脱敏边界。
- **非常规要求**：发布验证对 live evidence 采用 fail-closed，缺失、失败、过期、SHA 不匹配或非真实来源均硬阻塞，Human release approval 不能绕过；这是有意偏离“人工发布确认可处理问题”的宽松路径，用来守住 seed 明确要求的 fake/simulated/manual evidence 不可接受。另一方面，credential-less routine CI 有意允许只跳过 opt-in live 测试；该例外只保护日常 CI 可运行，不改变发布候选必须有 current successful live evidence 的要求。
- **Out-of-Scope**：完整 M-VERIFY/M-RELEASE/M-PUBLISH 阶段、candidate freeze 机制和发布副作用；强制所有例行 CI 或所有开发者提供真实 provider 凭据；把 live 旅程扩展为覆盖全部生产 task（本 story 只要求至少一个 task 的完整旅程）；以 FakeBackend、simulated outcome 或手工插入事件生成替代证据；新增 UI/API 等非既有 CLI/CI 交付面。

## 6. 开放产品决定

无。交付面可由宿主项目已有的 `trac` CLI、事件/状态输出和 CI 入口可靠确定；失败策略、candidate SHA 语义、release prerequisite 的强制性及 credential-less skip 均已由用户输入、既有 flow/测试合同和 TRIAGE 裁定确定。具体检查命名和实现方式留给后续规格/设计阶段，不改变产品结果。

## 7. 必要性与风险

- **既有能力**：v0.5 spec/acceptance 已定义 M-IMPL 的 RED→GREEN→REFACTOR、review、ISLAND_GATE_2 与 boundary；FakeBackend 已用于确定性旅程；OpencodeBackend 已提供真实 subprocess、目标 diff、agent I/O 审计和凭据脱敏；现有 `trac run`、状态/事件记录、Git R/G lineage 及 `tests/e2e_live` 的 credential-aware live 通道可作为入口和观察基础。
- **冲突**：当前 v0.5 产品流程停在 M-IMPL→M-VERIFY，flow.md 把 Human release gate 放在未来 M-RELEASE；本 story 同时要求 live evidence 成为 release prerequisite。已裁定的兼容方式是只增加独立的程序化 release-evidence 检查和证据合同，不提前实现 M-VERIFY/M-RELEASE，也不改变 M-IMPL boundary。
- **重要风险**：真实 provider 不可用或 live 旅程不完整时，发布候选将没有可接受证据而被硬阻塞；若当前 HEAD 比对、agent I/O/事件审计或来源真实性无法从公开结果验证，则无法证明“current successful live evidence”，story 不成立。后续实现必须保留失败证据和可重跑路径，不能用 skip、旧 SHA 或人工事件填补缺口。
