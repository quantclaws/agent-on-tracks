
## 1. The Diagram

全流程 canonical stage 序列（也是 workflow.py 状态机的顶层状态集，不得增设同级阶段）：

```
M-START → M-STORY → M-SPEC → M-ACC → M-REQ-APPROVAL → M-DESIGN
→ M-IMPL → M-TEST → M-VERIFY → M-SECURITY → M-RELEASE → M-PUBLISH → M-MILESTONE
```

| 阶段           | 主要作者/执行者                         | 评审 / Human 参与                    | 权威退出条件                           |
| -------------- | --------------------------------------- | ------------------------------------ | -------------------------------------- |
| M-START        | Runtime 建 release foundation           | Human 发起                           | 资源身份一致且可恢复                   |
| M-STORY        | Scribe                                  | Sage 独立评审；Human 裁决+评审       | review 闭合，Human Go                  |
| M-SPEC         | Sage                                    | Lex 独立评审；Human 评审             | 语义+程序校验通过（含 FR≤30）          |
| M-ACC          | Sage                                    | Lex 独立评审；Human 评审             | 覆盖+程序校验通过                      |
| M-REQ-APPROVAL | Runtime 生成 baseline preview           | **Human Approve/Return**             | approval 绑定三件套 digest             |
| M-DESIGN       | Archer（三文档 + machine contracts）    | Prism 独立评审；Human 可选、允许缺席 | Prism + 程序校验通过，不等 Human       |
| M-IMPL         | Archer 拆 task graph；Devon 逐 task RGR | Prism 评 Red checkpoint 与最终 range | 全部 task 完成，lineage/trace/孤岛闭合 |
| M-TEST         | Shield（integration/e2e）               | Prism                                | 测试资产齐备且执行通过                 |
| M-VERIFY       | Runtime 冻结 candidate                  | Prism 整体一致性复审                 | 全量回归+CI+build/artifact gate 通过   |
| M-SECURITY     | Runtime 程序扫描；Judge 语义审计        | Judge                                | security gate 通过或合法 policy skip   |
| M-RELEASE      | Runtime 生成发布预览                    | **Human Release/Delay/Return**       | release approval 绑定 candidate        |
| M-PUBLISH      | Runtime 执行发布副作用                  | —                                    | 幂等外部操作+发布后验证完成            |
| M-MILESTONE    | Runtime 收尾；Librarian 可选提炼        | —                                    | trace 闭包、归档、清理完成             |

**仅有的两个 Human gate stage**：M-REQ-APPROVAL（批准需求 baseline）与 M-RELEASE（授权发布副作用）。Human 在其它阶段仍可做产品决定，但不增设新 gate stage。Planning/Red/Green/Refactor 是 M-IMPL 内部 checkpoint，不升级为 stage。

## 2. 全流程不变量

1. **Runtime 是唯一流程 authority**：只有 Runtime 可创建/推进/回拨阶段、授予写权限、执行 commit/push、外部 API、CI、发布和归档副作用。
2. **Agent 只产出专业语义或代码**：不 commit/push、不写 PASS artifact、不改变 Issue/run 状态。
3. **Human 不承担技术决策**：Agent 不把架构/测试/实现/CI/修复方案当选择题推给 Human；只有产品意图、需求范围、发布时机、授权与不可逆外部冲突需要 Human。
4. **技术设计可按流程修订**：M-IMPL→M-SECURITY 期间任何 Agent 可提有锚点的 design-gap advisory；Archer+Prism 双确认即回 M-DESIGN，不需 Human；涉及产品意图/需求范围才回 M-SPEC/M-ACC 并经 Human 批准。
5. **所有结果绑定身份**：task/diff/review/test/CI/artifact/finding/gate 均绑定 baseline digest + commit + attempt + actor；上游变化使受影响结果 stale。
6. **Agent 自报不是证据**：“tests passed”、命令输出摘要或聊天文本不能推进阶段；Runtime 必须从权威程序出口重新执行或读取证据（即 arch.md 的“永不信自述”）。
7. **实现发生在当前 release branch**：普通 feature 不建 per-task branch/worktree，Runtime 串行授予单写者 lease；仅 hotfix 建隔离 `fix/{issue}` 分支。
8. **失败不伪报成功**：失败/取消/超时/缺失/skip/不确定均不得标记 PASS（fail closed）。
9. **下一步只由纯函数决定**：`decide(current_state, validated_result, program_evidence, workflow)` 选择 success edge 或合法返回；Agent/reviewer 的 recommendation 仅作诊断，永不构成跳转命令，任何一方不能直接命名目标 stage。

> **界面通道说明**：流程中所有 Human 动作（裁决、评审、批准）都建模为事件；网页/CLI 只是事件的输入通道适配器。下文按网页交互描述，但 v1 可用 CLI + 本地编辑器跑通全流程（按钮→trac 命令，网页编辑→直接改文件，编辑权/写锁→Runtime 基于 git 工作区状态裁定）。

## 3. M-START (创建新的 release)

**目的**：建立 release 工作基础——release 分支、projects/v{x}/ 目录、初始 story.md（含原始需求）。

**进入条件**：Human 发起新 release 请求。

### 3.1. 子状态机

```mermaid
stateDiagram-v2
    direction TB

    [*] --> CHECK_ACTIVE : Human 发起 start

    CHECK_ACTIVE : 检查是否有活跃 run
    CHECK_ACTIVE --> BACKLOG : 有活跃 run
    CHECK_ACTIVE --> CHECK_WORKSPACE : 无活跃 run

    BACKLOG : 新需求记入 backlog
    BACKLOG --> [*] : 退出, 不创建分支

    CHECK_WORKSPACE : 检查工作区是否干净
    CHECK_WORKSPACE --> FAILED : 有未提交修改
    CHECK_WORKSPACE --> CHECK_BRANCHES : 干净

    FAILED : 报告原因, 拒绝 start
    FAILED --> [*]

    CHECK_BRANCHES : 检查本地分支是否全部并入 main
    CHECK_BRANCHES --> AWAIT_CONFIRM : 有未合并分支
    CHECK_BRANCHES --> CREATE_BRANCH : 全部已合并

    AWAIT_CONFIRM : awaiting_human
    AWAIT_CONFIRM : 提示未合并分支与后果
    AWAIT_CONFIRM --> CREATE_BRANCH : Human 确认继续
    AWAIT_CONFIRM --> [*] : Human 取消

    CREATE_BRANCH : 从 main 创建 releases/v{x}
    CREATE_BRANCH --> WRITE_STORY : 分支创建成功

    WRITE_STORY : 创建 projects/v{x}/story.md
    WRITE_STORY : 写入原始需求
    WRITE_STORY --> [*] : stage.exited -> M-STORY
```

> 可休眠点：AWAIT_CONFIRM。
> 退出条件：release 分支存在 + story.md 含原始需求 + stage.entered(M-START) 已落盘。

## 4. M-STORY

**目的**：将原始需求转化为人类已裁定、机器可校验的 story.md。本阶段唯一产物是文档，不产生代码。

**进入条件**：M-START 通过（release 分支已创建，`projects/v{x}/` 就位）；原始需求已写入 story.md

### 4.1. 子状态机

```mermaid
stateDiagram-v2
    direction TB

    [*] --> TRIAGE : stage.entered(M-STORY)

    TRIAGE : dispatch Scribe 探索
    TRIAGE : 检查与已有 story 的关系
    TRIAGE : 提出 GO / NO-GO / PARK

    TRIAGE --> REJECTED : human.triage(no_go | park)
    REJECTED : 记入 backlog, 删除 release 分支
    REJECTED --> [*] : run.completed

    TRIAGE --> DRAFT : human.triage(go)

    DRAFT : dispatch Scribe
    DRAFT : 探索 + interview Human
    DRAFT : 按 template 写 story.md

    DRAFT --> SAGE_REVIEW : outcome -> validate pass, committed
    DRAFT --> DRAFT : validate fail, 重派 Scribe (<=3)

    SAGE_REVIEW : dispatch Sage 评审
    SAGE_REVIEW : 直接修改 + inline-comments
    SAGE_REVIEW : sage.verdict(pass | comment)

    SAGE_REVIEW --> HUMAN_REVIEW : verdict(pass), validate pass, committed
    SAGE_REVIEW --> RESPOND : verdict(comment), validate pass, committed
    SAGE_REVIEW --> SAGE_REVIEW : validate fail, 重派 Sage (<=3)

    HUMAN_REVIEW : awaiting_human
    HUMAN_REVIEW : Human 直接修改 + inline-comments
    HUMAN_REVIEW : human.review(comment | no_comment)

    HUMAN_REVIEW --> EXIT : no_comment 且本轮 sage pass
    HUMAN_REVIEW --> RESPOND : comment, validate, committed

    RESPOND : dispatch Scribe
    RESPOND : 输入 = Sage diff + Human diff
    RESPOND : + 全部 inline-comments

    RESPOND --> SAGE_REVIEW : validate pass, committed, 新一轮
    RESPOND --> RESPOND : validate fail, 重派 Scribe (<=3)

    EXIT : 生成 sha, 写入 frontmatter
    EXIT : story.committed(final)
    EXIT --> [*] : stage.exited -> M-SPEC
```

> validate = schema 校验 + scope 检查（只动 story.md）。失败带工具原始输出重派同一作者，上限 3 次，超限升级 Human。
> 可休眠点：TRIAGE 等人类裁决、HUMAN_REVIEW 等人类评审。decide() 返回空 command，run 挂起，事件回放恢复。
> 退出硬条件：同一轮收齐 human.review(no_comment) + sage.verdict(pass)。


### 4.2. story.md 结构契约（机器可校验）

- frontmatter：`story_id` / `title` / `created` / `status` / `sha`（`title`：人类引用 story 的有意义名字，非编号；start 时留空，Scribe 起草时写入非空）
- 固定章节(见 templates/story.md)
- 本阶段不设规模限制。release 规模由需求阶段控制：一次 release 最多 30 条功能需求，超出则从 story 重新拆起
- 行为种子须预答"将来用什么断言验证我"。断言是产品级可观察事实（"用户结账后总额减少 20%"），不是技术命令（"curl | jq"）——技术验证路径由 Sage 在 M-ACC 选择。写不出断言的种子说明需求太模糊，须在 interview 中追问到可断言为止

### 4.3. 事件清单

```
story.requested / stage.entered / assignment.dispatched /
interview.started / interview.ended / outcome.received /
verdict.passed | verdict.failed / story.committed /
writelock.granted | writelock.released /
review.round_started / human.triage(go|no_go|park) /
human.review(comment|no_comment) / sage.verdict(pass|comment) /
stage.exited
```


### 4.4. 硬规则

1. 人类裁定与评审结论都是一等事件：没有 `human.triage(go)`，且未收齐 `human.review(no_comment)` + `sage.verdict(pass)`，decide() 不会产出进入 M-SPEC 的 command。story.md 里的分流表格只是事件的投影展示。
2. 单写者纪律：任一时刻只有一个 actor 持有编辑权（顺序评审天然保证）；Scribe/Sage/Human 的所有落盘变更均由 Runtime 提交为独立 commit。
3. **可休眠**：等待人类时 run 停在 awaiting_human，decide() 返回空 command 但 run 未终结，进程可退出；人类回来时从事件回放恢复继续。不得在内存里悬挂等待状态（Agent 会话状态的保留需单独设计，见 Aaron 注记）。

## 5. M-SPEC

**目的**：将已裁定的 story 转化为可断言的功能/非功能需求（spec.md）。Sage 创建 spec 文档，人类和 Lex 共同评审。

**进入条件**：M-STORY 通过（`human.triage(go)` + `human.review(no_comment)` + `sage.verdict(pass)` 收齐）。

### 5.1. 子状态机

```mermaid
stateDiagram-v2
    direction TB

    [*] --> DRAFT : stage.entered(M-SPEC)

    DRAFT : dispatch Sage 写 spec.md
    DRAFT : 继承 M-STORY review 上下文
    DRAFT : 按 template 生成 FR/NFR

    DRAFT --> LEX_REVIEW : validate pass, committed
    DRAFT --> DRAFT : validate fail, 重派 Sage (<=3)
    DRAFT --> ROLLBACK : scope_overflow (FR > 30)

    LEX_REVIEW : dispatch Lex 评审
    LEX_REVIEW : 直接修改 + inline-comments
    LEX_REVIEW : lex.verdict(pass | comment)

    LEX_REVIEW --> HUMAN_REVIEW : verdict(pass), validate pass, committed
    LEX_REVIEW --> RESPOND : verdict(comment), validate pass, committed
    LEX_REVIEW --> LEX_REVIEW : validate fail, 重派 Lex (<=3)

    HUMAN_REVIEW : awaiting_human
    HUMAN_REVIEW : Human 直接修改 + inline-comments
    HUMAN_REVIEW : human.review(comment | no_comment)

    HUMAN_REVIEW --> EXIT : no_comment 且本轮 lex pass
    HUMAN_REVIEW --> RESPOND : comment, validate, committed
    HUMAN_REVIEW --> ROLLBACK : Human 裁定需改 story

    RESPOND : dispatch Sage
    RESPOND : 输入 = Lex diff + Human diff
    RESPOND : + 全部 inline-comments

    RESPOND --> LEX_REVIEW : validate pass, committed, 新一轮
    RESPOND --> RESPOND : validate fail, 重派 Sage (<=3)

    EXIT : 格式终验 (退出门禁)
    EXIT --> [*] : pass -> stage.exited -> M-ACC
    EXIT --> DRAFT : fail -> 重派 Sage

    ROLLBACK : stage.rolled_back
    ROLLBACK --> [*] : 回退 M-STORY 重新拆分
```

> validate = schema（FR/NFR 编号、章节结构）+ scope + story->spec 覆盖 trace。失败带工具原始输出重派同一作者，上限 3 次，超限升级 Human。
> scope_overflow（FR > 30）不重派压缩，直接 rolled_back 回 M-STORY 重新拆分。回退落点为 **DRAFT**（不重复 TRIAGE——triage 裁决已存在，重问违反"人类不做技术决策"）。
> 可休眠点：HUMAN_REVIEW。
> 退出硬条件：同一轮收齐 human.review(no_comment) + lex.verdict(pass) + 格式终验通过。
> Lex 评审协议与 M-STORY 的 Sage 评审同构；M-ACC 复用本节状态机（文档对象换为 acceptance.md，validate 规则见 M-ACC 节）。

### 5.2. 事件清单

```
stage.entered / assignment.dispatched / outcome.received /
verdict.passed | verdict.failed(scope_overflow|schema|trace) /
spec.committed / review.round_started /
human.review(comment|no_comment) / lex.verdict(pass|comment) /
stage.rolled_back / stage.exited
```

### 5.3. 硬规则

1. 退出 M-SPEC 需同一轮收齐 `human.review(no_comment)` + `lex.verdict(pass)` + 格式终验 `verdict.passed`。
2. 单写者纪律同 M-STORY：任一时刻只有一个 actor 持有编辑权，所有落盘变更由 Runtime 提交为独立 commit。
3. 作者响应评论的角色固定为文档作者本人（story->Scribe，spec->Sage），reviewer 不直接改稿。

## 6. M-ACC

**目的**：为 spec 中每条需求生成外部可观察、可判定 PASS/FAIL 的验收标准（acceptance.md）。

**进入条件**：M-SPEC 通过（语义评审结束 + 格式终验通过）。

### 6.1. 子状态机

状态机同 M-SPEC（§5.1），差异点：

- 文档对象：`acceptance.md`（替代 `spec.md`）
- 作者：Sage（继承 M-SPEC 上下文）
- reviewer：Lex + Human
- validate 规则：schema + scope + **AC↔FR 双向覆盖 trace**（每条 FR 至少一条 AC，每条 AC 回指存在的 FR，孤立项 = `verdict.failed(trace)`）
- 每条 AC 必须是产品级可观察断言，承接 story 行为种子，不得引入新产品行为
- 退出门禁：格式终验（同 M-SPEC）
- ROLLBACK 目标：M-SPEC 或 M-STORY（`stage.rolled_back`）

```mermaid
stateDiagram-v2
    direction TB

    [*] --> DRAFT : stage.entered(M-ACC)

    DRAFT : dispatch Sage 写 acceptance.md
    DRAFT : 继承 M-SPEC 上下文

    DRAFT --> LEX_REVIEW : validate pass, committed
    DRAFT --> DRAFT : validate fail, 重派 Sage (<=3)
    DRAFT --> ROLLBACK : trace 不可修复 -> 回 M-SPEC/M-STORY

    LEX_REVIEW : dispatch Lex 评审
    LEX_REVIEW : lex.verdict(pass | comment)

    LEX_REVIEW --> HUMAN_REVIEW : verdict(pass), validate pass, committed
    LEX_REVIEW --> RESPOND : verdict(comment), validate pass, committed
    LEX_REVIEW --> LEX_REVIEW : validate fail, 重派 Lex (<=3)

    HUMAN_REVIEW : awaiting_human
    HUMAN_REVIEW : human.review(comment | no_comment)

    HUMAN_REVIEW --> EXIT : no_comment 且本轮 lex pass
    HUMAN_REVIEW --> RESPOND : comment, validate, committed
    HUMAN_REVIEW --> ROLLBACK : Human 裁定需改 spec/story

    RESPOND : dispatch Sage
    RESPOND : 输入 = Lex diff + Human diff

    RESPOND --> LEX_REVIEW : validate pass, committed, 新一轮
    RESPOND --> RESPOND : validate fail, 重派 Sage (<=3)

    EXIT : 格式终验
    EXIT --> [*] : pass -> stage.exited -> M-REQ-APPROVAL
    EXIT --> DRAFT : fail -> 重派 Sage

    ROLLBACK : stage.rolled_back
    ROLLBACK --> [*] : 回退 M-SPEC 或 M-STORY
```

### 6.2. 事件清单

同 M-SPEC 同构，文档事件为 `acceptance.committed`；回退事件 `stage.rolled_back` 目标可为 M-SPEC 或 M-STORY。

## 7. M-REQ-APPROVAL

**目的**：Human 将 story/spec/acceptance 三件套批准为本轮需求 baseline，之后才允许进入设计/实现。

**进入条件**：story、spec、acceptance 均通过 review。

### 7.1. 子状态机

```mermaid
stateDiagram-v2
    direction TB

    [*] --> PREVIEW : stage.entered(M-REQ-APPROVAL)

    PREVIEW : Runtime 生成 baseline preview
    PREVIEW : 三件套 revision digest + 摘要

    PREVIEW --> AWAIT_HUMAN : preview 就绪

    AWAIT_HUMAN : awaiting_human
    AWAIT_HUMAN : Human 审阅三件套

    AWAIT_HUMAN --> APPROVED : human.approval (绑定三件套 digest)
    AWAIT_HUMAN --> RETURNED : human.return (产品理由 + 目标阶段)

    APPROVED : 三件套设为只读
    APPROVED : 记录 approval identity
    APPROVED --> ISSUES : 创建 GitHub Issues + 关联 Project

    ISSUES : Runtime 拆分 spec -> Issues
    ISSUES : Issues = 需求追踪身份 (非执行单元)
    ISSUES --> [*] : stage.exited -> M-DESIGN

    RETURNED : stage.rolled_back
    RETURNED --> [*] : 回退 M-STORY/M-SPEC/M-ACC
```

> 可休眠点：AWAIT_HUMAN。
> Freshness：三件套任一文档变化立即使 approval stale（digest 不匹配），下游被阻断，必须重新走 M-REQ-APPROVAL。

### 7.2. 硬规则

1. **Agent 不能批准、拒绝或代替 Human 回答**；没有 `human.approval` 事件，decide() 不产出任何进入设计/实现阶段的 command。
2. **Freshness**：三件套任一文档内容变化立即使 approval stale，下游工作被阻断。
3. Issues 是需求追踪身份，不是执行单元；实施切片（task graph）在 M-IMPL Planning 才产生，二者映射但不互相冒充。

## 8. M-DESIGN

**目的**：Archer 产出 Test Plan、Architecture、Interfaces 三文档及宿主项目 machine contracts（至少覆盖 integration/e2e、GitHub CI、pre-commit、release version、build/artifact、发布恢复）；Prism 独立评审。这是纯技术阶段：Human 是可选 reviewer，允许缺席，意见不是批准门禁。

**进入条件**：`human.approval` 对当前三件套 digest 有效（M-REQ-APPROVAL 通过且未 stale）。

### 8.1. 子状态机

```mermaid
stateDiagram-v2
    direction TB

    [*] --> DRAFT : stage.entered(M-DESIGN)

    DRAFT : dispatch Archer
    DRAFT : 三文档 + machine contracts
    DRAFT : Human 可选对话 (不阻塞)

    DRAFT --> PRISM_REVIEW : validate pass, committed
    DRAFT --> DRAFT : validate fail, 重派 Archer (<=3)

    PRISM_REVIEW : dispatch Prism 独立评审
    PRISM_REVIEW : prism.verdict(pass | revise)

    PRISM_REVIEW --> EXIT : verdict(pass)
    PRISM_REVIEW --> RESPOND : verdict(revise)

    RESPOND : dispatch Archer 响应 Prism 意见
    RESPOND --> PRISM_REVIEW : validate pass, committed, 新一轮
    RESPOND --> RESPOND : validate fail, 重派 Archer (<=3)

    EXIT : 设计 revision = 实现基线
    EXIT --> [*] : stage.exited -> M-IMPL
```

> validate = 文档结构 + AC→Test Plan 分层覆盖（每条 AC 有 test layer 归属）+ contracts 清单完整性。
> **不等待 Human 批准**：退出只要求 validate pass + prism.verdict(pass)。Human 缺席不阻塞，建议由 Archer 技术裁量。
> 可休眠点：无（Human 不参与门禁）。

### 8.2. 事件清单

`stage.entered` / `assignment.dispatched` / `outcome.received` / `verdict.passed|failed(schema|trace)` / `design.committed` / `prism.verdict(pass|revise)` / `review.round_started` / `stage.exited` / `stage.rolled_back`

### 8.3. 硬规则

1. Human 无技术否决权也无到场义务；退出只要求 `verdict.passed`（程序）+ `prism.verdict(pass)`。
2. contracts 由 Archer 设计，**安装/更新/回读副作用只归 Runtime**；Devon 不得安装或修改 hook 绕过门禁。
3. 设计文档与 contracts 均有 revision/digest，修订使依赖旧 revision 的下游证据 stale。

## 9. M-IMPL

**目的**：Archer 把需求/设计基线拆成可独立验证的 implementation task graph；Devon 逐 task 以 Red→Green→Refactor 完成实现；Prism 在 Red checkpoint 与最终 task range 两处独立评审。

**进入条件**：M-DESIGN 通过（`prism.verdict(pass)` + 程序校验通过）。

### 9.1. 子状态机

```mermaid
stateDiagram-v2
    direction TB

    [*] --> BASELINE : stage.entered(M-IMPL)

    BASELINE : Runtime 重算 baseline
    BASELINE : 三件套 + 设计三文档 digest
    BASELINE : contracts + Issues + branch + approval

    BASELINE --> PLANNING : baseline current
    BASELINE --> NEEDS_ATTENTION : 缺失/stale/冲突

    NEEDS_ATTENTION : 等待 reconcile 或 return upstream
    NEEDS_ATTENTION --> BASELINE : 已 reconcile
    NEEDS_ATTENTION --> [*] : rolled_back

    PLANNING : dispatch Archer 拆 task graph
    PLANNING : 纵向切片 + scope 白名单 + 预算

    PLANNING --> ISLAND_GATE_1 : validate pass (DAG/scope/coverage)
    PLANNING --> PLANNING : validate fail, 重派 Archer

    ISLAND_GATE_1 : 每条 required AC 六项检查
    ISLAND_GATE_1 : owner/surface/composition/wiring/test/evidence

    ISLAND_GATE_1 --> PRISM_PLAN : 闭合
    ISLAND_GATE_1 --> PLANNING : verdict.failed(island)

    PRISM_PLAN : dispatch Prism 评审切片
    PRISM_PLAN --> TASK_DISPATCH : prism.verdict(pass)
    PRISM_PLAN --> PLANNING : revise -> Archer
    PRISM_PLAN --> [*] : 设计缺口 -> M-DESIGN
    PRISM_PLAN --> [*] : 需求缺口 -> M-SPEC/M-ACC (Human 确认)

    TASK_DISPATCH : Runtime 按依赖选 ready task
    TASK_DISPATCH : 单写者 lease + 创建 manifest
    TASK_DISPATCH --> RED : task.started

    RED : dispatch Devon (phase=red)
    RED : 只添加 unit/contract test
    RED : 不碰产品代码

    RED --> RED_GATE : outcome (test-only diff)
    RED_GATE : Runtime 校验预期失败
    RED_GATE : 行为断言失败 / symbol 缺失
    RED_GATE --> RED_CHECKPOINT : 合法 Red
    RED_GATE --> RED : 非法 Red, 重派 Devon

    RED_CHECKPOINT : 创建私有 commit R
    RED_CHECKPOINT : refs/trac/rgr/{run}/{task}/{attempt}/red
    RED_CHECKPOINT --> PRISM_RED : red.checkpointed

    PRISM_RED : dispatch Prism 评 B..R
    PRISM_RED --> GREEN : prism.verdict(pass) 绑定 R
    PRISM_RED --> RED : revise -> 新 attempt

    GREEN : 从 R tree 恢复工作区
    GREEN : dispatch Devon (phase=green)
    GREEN : 最小实现, R 测试不可改

    GREEN --> GREEN_GATE : outcome
    GREEN_GATE : targeted + 全部历史单测
    GREEN_GATE : lint/format/type/static + 合同
    GREEN_GATE --> GREEN_COMMIT : 全过
    GREEN_GATE --> GREEN : 失败, 重派 Devon

    GREEN_COMMIT : 创建正式 commit G (parent=B)
    GREEN_COMMIT : trailers 记录 R/task identity
    GREEN_COMMIT --> REFACTOR : green.committed

    REFACTOR : dispatch Devon (phase=refactor)
    REFACTOR : 可返回 no-change + 理由
    REFACTOR --> REFACTOR_GATE : outcome

    REFACTOR_GATE : 重跑 Green 全部检查
    REFACTOR_GATE --> TASK_REVIEW : 通过 (committed | no_change)
    REFACTOR_GATE --> REFACTOR : 失败, 重派
    REFACTOR_GATE --> [*] : 动 public interface -> upstream

    TASK_REVIEW : Runtime 校验 task range
    TASK_REVIEW : write scope/secret/AC trace
    TASK_REVIEW : B/R/G/(Refactor) lineage + budget

    TASK_REVIEW --> PRISM_FINAL : 校验通过
    TASK_REVIEW --> GREEN : budget/scope fail -> Devon

    PRISM_FINAL : dispatch Prism 评完整 range + lineage
    PRISM_FINAL --> TASK_DONE : prism.verdict(pass)
    PRISM_FINAL --> GREEN : revise (实现) -> Devon
    PRISM_FINAL --> RED : revise (Red 测试) -> 新 lineage

    TASK_DONE : task.completed
    TASK_DONE --> TASK_DISPATCH : 还有 ready task
    TASK_DONE --> ISLAND_GATE_2 : 全部 task 完成

    ISLAND_GATE_2 : 最终孤岛闭合复查
    ISLAND_GATE_2 --> [*] : 通过 -> stage.exited -> M-TEST
    ISLAND_GATE_2 --> PLANNING : verdict.failed(island)
```

> 可休眠点：每个 phase 边界都是事件，重启从 lineage + 事件回放恢复到精确 phase。
> Red 测试先于实现是程序可验证的 lineage 事实（B/R/G commit 拓扑），不是 Agent 自报。
> 同一 attempt 重试只能得到同一 R（compare-and-set），否则产生新 attempt；旧 attempt 不被改写。

### 9.2. 事件清单

`stage.entered` / `baseline.frozen` / `taskgraph.committed` / `task.started` / `writelock.granted|released` / `red.checkpointed` / `prism.verdict(pass|revise)` / `green.committed` / `refactor.committed|no_change` / `verdict.failed(red_invalid|regression|budget|island|scope)` / `task.completed` / `stage.exited` / `stage.rolled_back`

### 9.3. 硬规则

1. Devon 只能编辑 manifest 授权文件；不碰需求/设计 artifact、task 状态、Git history、Issue；不 commit/push（正式 commit 均由 Runtime 创建）。
2. Red 测试先于实现是程序可验证的 lineage 事实（B/R/G commit 拓扑），不是 Agent 自报。
3. 同一 attempt 重试只能得到同一 R（compare-and-set），否则产生新 attempt；旧 attempt 不被改写。
4. 可休眠：每个 phase 边界都是事件，重启从 lineage + 事件回放恢复到精确 phase。

## 10. M-TEST

**目的**：Shield 按 Test Plan 编写 integration/e2e 测试；Prism 先审测试合约，Runtime 独立执行。

**进入条件**：M-IMPL 退出条件成立（事件：`stage.exited(M-IMPL)`）。

### 10.1. 子状态机

```mermaid
stateDiagram-v2
    direction TB

    [*] --> DISPATCH : stage.entered(M-TEST)

    DISPATCH : Runtime 按 Test Plan 创建 Shield tasks
    DISPATCH : 每条 AC 的 layer + observable interface

    DISPATCH --> WRITE : tasks 就绪

    WRITE : dispatch Shield 写测试
    WRITE : integration + e2e
    WRITE : 不选新框架/不降层/不改产品代码

    WRITE --> PRISM_REVIEW : outcome

    PRISM_REVIEW : dispatch Prism 审测试合约
    PRISM_REVIEW : 忠于 AC + 公开出口断言 + 无伪测试

    PRISM_REVIEW --> EXECUTE : prism.verdict(pass)
    PRISM_REVIEW --> WRITE : revise -> Shield

    EXECUTE : Runtime 独立执行 integration/e2e
    EXECUTE : 记录 runner/环境/commit/结果/覆盖 AC

    EXECUTE --> EXIT : 全部通过
    EXECUTE --> DIAGNOSE : 有失败

    DIAGNOSE : 失败四路分流 (Prism diagnostic)
    DIAGNOSE --> WRITE : 测试/fixture 错 -> Shield
    DIAGNOSE --> [*] : 实现错 -> M-IMPL task (保留测试 patch)
    DIAGNOSE --> [*] : 接口/架构不足 -> M-DESIGN
    DIAGNOSE --> [*] : AC/Spec 缺口 -> M-ACC/M-SPEC

    EXIT : Runtime 创建受控测试 commit
    EXIT : 更新 AC trace
    EXIT --> [*] : stage.exited -> M-VERIFY
```

> "测试错还是实现错"的分流永不交给 Human；需语义判断时分派 Prism diagnostic review。
> 修复后重跑受影响测试并要求 Prism 对新 revision 重新 review。

### 10.2. 事件清单

`stage.entered` / `assignment.dispatched` / `outcome.received` / `prism.verdict(pass|revise)` / `test.executed(passed|failed)` / `verdict.failed(test_defect|impl_defect)` / `test.committed` / `stage.exited` / `stage.rolled_back`

### 10.3. 硬规则

1. Shield 不 git add/commit/push、不判定 PASS；执行结果以 Runtime 复跑为准。
2. "测试错还是实现错"的分流永不交给 Human。

## 11. M-VERIFY

**目的**：冻结 release candidate，跑完整本地权威质量链 + GitHub CI + 版本/构建物验证，Prism 做整体一致性复审。

**进入条件**：M-TEST 通过（事件：`stage.exited(M-TEST)`）。

### 11.1. 子状态机

```mermaid
stateDiagram-v2
    direction TB

    [*] --> FREEZE : stage.entered(M-VERIFY)

    FREEZE : 检查工作区/lineage/证据 current
    FREEZE : 冻结 candidate commit
    FREEZE --> LOCAL_GATES : candidate.frozen
    FREEZE --> NEEDS_ATTENTION : 未归属修改/证据 stale

    NEEDS_ATTENTION : 等待 reconcile
    NEEDS_ATTENTION --> FREEZE : 已 reconcile

    LOCAL_GATES : format/lint/static/type
    LOCAL_GATES : pre-commit all-files
    LOCAL_GATES : 全部单测 + integration + e2e
    LOCAL_GATES : AC trace + 反 slop (reach/ratio/dup)
    LOCAL_GATES : 真实 build + 全部 artifact

    LOCAL_GATES --> VERSION_VERIFY : 全过
    LOCAL_GATES --> [*] : 失败 -> 按 finding 归属返回上游

    VERSION_VERIFY : 准备/校验版本源 -> build
    VERSION_VERIFY : 枚举 artifact + 提取版本比对
    VERSION_VERIFY : 从安装/运行出口复核

    VERSION_VERIFY --> CI : artifact.verified
    VERSION_VERIFY --> [*] : 缺失/不一致 -> 上游

    CI : push candidate 触发 GitHub workflow
    CI : API 关联 repo/workflow/commit/run/jobs

    CI --> PRISM_FINAL : ci.run_observed(passed)
    CI --> CI : 权限/网络 -> reconcile 重试
    CI --> [*] : failed/cancelled/timeout -> 上游

    PRISM_FINAL : dispatch Prism 整体复审
    PRISM_FINAL : 跨 task 漂移/重复/回归风险

    PRISM_FINAL --> [*] : prism.verdict(pass) -> stage.exited -> M-SECURITY
    PRISM_FINAL --> [*] : revise -> Devon/Shield/上游 (新 candidate 重跑)
```

> 所有 gate 必须对**同一** candidate PASS；不存在"部分基于旧 commit 的绿色"。
> CI 结果以 API 回读为准，永不采信 Agent 转述。
> 局部 test selector 只供开发反馈，不替代全量 gate；"与本次需求无关"不是排除历史测试的理由。

### 11.2. 事件清单

`stage.entered` / `candidate.frozen` / `check.executed(各 gate)` / `ci.run_observed(passed|failed)` / `artifact.verified` / `prism.verdict(pass|revise)` / `verdict.failed(...)` / `stage.exited` / `stage.rolled_back`

### 11.3. 硬规则

1. 所有 gate 必须对**同一** candidate PASS；不存在"部分基于旧 commit 的绿色"。
2. CI 结果以 API 回读为准，永不采信 Agent 转述。

## 12. M-SECURITY

**目的**：程序安全扫描 + Judge 语义安全审计；findings 四路归因。

**进入条件**：M-VERIFY 通过（事件：`stage.exited(M-VERIFY)`）。

### 12.1. 子状态机

```mermaid
stateDiagram-v2
    direction TB

    [*] --> SCAN : stage.entered(M-SECURITY)

    SCAN : Runtime 程序扫描
    SCAN : secret scan + dependency/SCA + SAST

    SCAN --> POLICY_CHECK : scan.executed(pass)
    SCAN --> [*] : scan fail -> 按 finding 归属上游

    POLICY_CHECK : 读取项目 policy revision
    POLICY_CHECK --> SKIPPED : policy 禁用深度审计
    POLICY_CHECK --> JUDGE : policy 要求审计

    SKIPPED : 记录 skipped_by_policy + digest + 依据
    SKIPPED --> [*] : stage.exited -> M-RELEASE

    JUDGE : dispatch Judge 语义审计
    JUDGE : 输入 candidate diff/代码/架构/依赖/威胁边界

    JUDGE --> EXIT : judge.verdict(pass)
    JUDGE --> ATTRIBUTE : judge.verdict(findings)

    ATTRIBUTE : findings 四路归因
    ATTRIBUTE --> [*] : 实现漏洞 -> Devon (M-IMPL)
    ATTRIBUTE --> [*] : 安全测试缺失 -> Shield (M-TEST)
    ATTRIBUTE --> [*] : 架构边界错 -> M-DESIGN
    ATTRIBUTE --> [*] : 产品合同缺失 -> M-SPEC/M-ACC

    EXIT : security gate 通过
    EXIT --> [*] : stage.exited -> M-RELEASE
```

> 修复后必须重过受影响的 M-IMPL/M-TEST、**完整 M-VERIFY** 与 Judge 复审。
> critical/high finding 不可 waiver；涉及认证/权限/secret/支付/敏感数据的变更不得静默跳过。

### 12.2. 事件清单

`stage.entered` / `scan.executed` / `judge.verdict(pass|findings)` / `security.skipped_by_policy` / `verdict.failed(security)` / `stage.exited` / `stage.rolled_back`

## 13. M-RELEASE（Human 发布门禁）

**目的**：Human 授权不可逆发布副作用。

**进入条件**：M-SECURITY 通过（或合法 policy skip）。

### 13.1. 子状态机

```mermaid
stateDiagram-v2
    direction TB

    [*] --> PREVIEW : stage.entered(M-RELEASE)

    PREVIEW : Runtime 生成发布预览
    PREVIEW : version/candidate/branch/tag
    PREVIEW : 变化/证据/artifact/风险/副作用

    PREVIEW --> GATE_CHECK : preview 就绪

    GATE_CHECK : 全部不可 waiver gate PASS?
    GATE_CHECK --> AWAIT_HUMAN : 是
    GATE_CHECK --> [*] : 否 -> 上游 (Human 不能绕过)

    AWAIT_HUMAN : awaiting_human
    AWAIT_HUMAN : human.release(release | delay | return)

    AWAIT_HUMAN --> [*] : release -> stage.exited -> M-PUBLISH
    AWAIT_HUMAN --> DELAYED : delay
    AWAIT_HUMAN --> [*] : return -> 上游 (产品理由)

    DELAYED : 保持 candidate 等待
    DELAYED --> AWAIT_HUMAN : Human 重新触发
```

> candidate/artifact/证据/发布计划任一变化 → 旧 approval stale，必须新 preview。
> 可休眠点：AWAIT_HUMAN、DELAYED。

### 13.2. 硬规则

1. Agent 可总结证据，不能代替 Human 作发布决定，也不得把技术风险选择推给 Human。
2. Human 不能用发布确认绕过失败的门禁。

## 14. M-PUBLISH

**目的**：Runtime 执行不可逆外部副作用（merge、tag、artifact 发布、GitHub Release、部署）；幂等 + write-ahead + reconcile。

**进入条件**：M-RELEASE 通过（`human.release(release)` 绑定 preview digest）。

### 14.1. 子状态机

```mermaid
stateDiagram-v2
    direction TB

    [*] --> RECONFIRM : stage.entered(M-PUBLISH)

    RECONFIRM : 复认 required CI + release approval current
    RECONFIRM --> OPS_LOOP : current
    RECONFIRM --> NEEDS_ATTENTION : stale

    NEEDS_ATTENTION : 等待 reconcile
    NEEDS_ATTENTION --> RECONFIRM : 已 reconcile

    OPS_LOOP : 选下一个未完成 operation
    OPS_LOOP : merge -> tag -> artifact -> Release -> deploy

    OPS_LOOP --> ISSUE_CMD : 有未完成 op
    OPS_LOOP --> VERIFY_PUBLISH : 全部完成

    ISSUE_CMD : 查当前事实 + 写 command.issued (write-ahead)
    ISSUE_CMD --> EXECUTE_OP : 已落盘

    EXECUTE_OP : Runtime 执行外部操作
    EXECUTE_OP --> OPS_LOOP : operation.completed
    EXECUTE_OP --> RECONCILE : operation.unknown
    EXECUTE_OP --> [*] : operation.failed -> 按 policy

    RECONCILE : 查事实判断 op 实际状态
    RECONCILE --> OPS_LOOP : 已成功 -> 跳过
    RECONCILE --> NEEDS_ATTENTION : 仍未知

    VERIFY_PUBLISH : 从真实安装/部署/运行出口复核
    VERIFY_PUBLISH : 公开版本 + smoke

    VERIFY_PUBLISH --> [*] : publish.verified -> stage.exited -> M-MILESTONE
    VERIFY_PUBLISH --> RECOVER : 验证失败

    RECOVER : 按 rollback/forward-fix policy
    RECOVER --> OPS_LOOP : 自动恢复步骤
    RECOVER --> NEEDS_ATTENTION : 需凭据/外部所有权 -> Human
```

> tag、Release、registry publish、merge、部署不得由 Agent 执行或用聊天文本模拟。
> 中断后逐项 reconcile：已成功 operation 不重复执行；不重打不同 tag、不覆盖不可变 artifact。
> 全部必需操作 + 公开版本验证成功前，状态保持 `publishing`/`needs_attention`，不得标记完成。

### 14.2. 事件清单

`stage.entered` / `command.issued` / `operation.completed|failed|unknown` / `publish.verified` / `stage.exited`

## 15. M-MILESTONE

**目的**：trace 最终闭包 + 幂等收尾归档；可选 Librarian 知识提炼。

**进入条件**：M-PUBLISH 通过（`publish.verified`）。

### 15.1. 子状态机

```mermaid
stateDiagram-v2
    direction TB

    [*] --> TRACE_CLOSE : stage.entered(M-MILESTONE)

    TRACE_CLOSE : 每条 FR/NFR -> AC -> test -> task
    TRACE_CLOSE : -> commit -> CI -> artifact -> release
    TRACE_CLOSE : 全链路无 missing/failed/stale

    TRACE_CLOSE --> ARCHIVE : trace.closed
    TRACE_CLOSE --> NEEDS_ATTENTION : 有缺口

    NEEDS_ATTENTION : 等待 reconcile
    NEEDS_ATTENTION --> TRACE_CLOSE : 已补齐

    ARCHIVE : 关闭 Issues + release Project
    ARCHIVE : 归档 run/attempts/reviews/gates
    ARCHIVE : 归档 events/diffs/RGR lineage/CI/security/publish
    ARCHIVE : 项目移入只读历史
    ARCHIVE : 删除 refs/trac/rgr/...

    ARCHIVE --> LIBRARIAN : archive.completed (DoD 要求)
    ARCHIVE --> EXIT : archive.completed (无 DoD)
    ARCHIVE --> ARCHIVE : 归档失败 -> 重试 (不回滚发布)

    LIBRARIAN : dispatch Librarian 提炼知识/文档
    LIBRARIAN : 只改授权 wiki/docs
    LIBRARIAN --> EXIT : 完成

    EXIT : run.completed
    EXIT --> [*] : stage.exited (流程结束)
```

> 发布成功但归档/清理失败 → 保持 `closing` 重试；不得回滚真实发布事实或创建第二次发布。

### 15.2. 事件清单

`stage.entered` / `trace.closed` / `archive.completed` / `run.completed` / `stage.exited`

## 16. Bug Fix / Hotfix 变体

1. `bug_fix` 只适用于已发布产品相对既有 approved Spec/AC 的**实现偏差**；Runtime 先验证 Issue、source contract、目标版本、可复现失败；实际是新行为 → 退出 hotfix，进 backlog/new feature。
2. `quick_rgr` 继承 source requirement approval；`design_required` 还需形成当前 M-DESIGN revision。涉及 public interface/数据迁移/安全边界/跨模块设计 → 先走 M-DESIGN；拿不准时由 Archer/Prism 技术判断，不让 Human 选架构路径。
3. 两条路径都复用 `M-IMPL → M-TEST → M-VERIFY → M-SECURITY/policy → M-RELEASE → M-PUBLISH → M-MILESTONE`；开发反馈可按影响收窄，但 M-VERIFY 的历史回归与 required CI 不能省。
4. Hotfix 不因“改动小”自动跳过 Prism；review 深度可按风险调整，但必须有独立语义 review 和权威回归证据。
5. Runtime 是唯一 branch/worktree authority：创建隔离 `fix/{issue}`，防止与 active release 串写；发布后同步修复到 main 与受影响的 active release，冲突 → `needs_attention`。

## 17. 通用返回、修改与恢复规则

### 17.1. Return Upstream

- Agent 只能返回有证据和锚点的 gap advisory；Runtime 根据 workflow 定义校验合法目标并移动流程。路由决定的机器可读形式是 FailureDecision（classification、target stage/owner、artifact disposition、new attempt kind、human_required），与 transition 一起 append-only 写入事件日志。
- 回 M-DESIGN：task graph/架构/接口/Test Plan/技术合同需要改变 → Archer+Prism 裁定，不需 Human。
- 回 M-SPEC/M-ACC：用户结果、权限、范围、数据后果、不可逆语义需要改变 → Runtime 先展示影响，Human 批准；修订后重新完成 review 与 M-REQ-APPROVAL。
- 返回后 Runtime 保留历史，但将目标之后的 task graph、baselines、lineage、commits/reviews、candidate、CI/artifact/security/release approval 标记 stale/superseded；不得继续复用旧绿色证据。

### 17.2. Human 或外部工具直接修改

- Runtime 在每个写 lease 和 program gate 前比较 workspace 与 baseline，识别 Human/外部直接修改的 diff，不静默覆盖。
- 符合 baseline 且过当前 review/gates → 可纳入受控 commit；有问题 → 相应 Agent 通过 discussion 提出，不把“Human 写的”当自动批准；改变 baseline 或来源不明 → 停止当前 task，要求合法 return-upstream 或 reconcile。

### 17.3. Retry、Waiver 与取消

- retry 只重试幂等/reconcile-safe 的 operation；每次 attempt 独立 identity，旧 attempt 不改写。
- waiver 只适用于 policy 明确列出的非关键检查，绑定 actor/理由/范围/candidate/到期条件。**不可 waiver**：M-REQ-APPROVAL、release approval、trace/freshness、required CI、artifact version、critical security、发布身份。
- Human 可取消未发布的 run：Runtime 停止新分派、处理当前 lease、保留审计、清理资源；已产生外部发布事实后只能进发布恢复/关闭，不得用取消抹除历史。
