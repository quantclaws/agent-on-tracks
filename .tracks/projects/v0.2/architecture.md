---
architecture_id: ARCH-003
spec_ref: SPEC-003
arch_ref: ARCH-001
created: 2026-07-31
status: draft
sha:
---

# Agent on Tracks v0.2 — 架构

> 本文是 ARCH-001（v0.1）的**增量延伸**。v0.1 的内核机制（事件溯源、单一生产路径、四增长轴分包、单写者锁、per-kind reconcile）**全部保持不变**；v0.2 只在既有包内新增文件、往既有枚举追加成员，不推翻布局。凡未提及者，一律继承 ARCH-001。

## 0. 延续性声明（什么不变）

| v0.1 合同（ARCH-001） | v0.2 状态 |
|:---|:---|
| 唯一工作流生产路径 `cli → kernel.runtime.run_loop → effects.executor.execute`（§1） | **不变**。状态变更命令仍只此一路 |
| 四增长轴分包 kernel/workflows/effects/checks（§2） | **不变**，effects/ 与 checks/ 按预期缓慢生长；新增一个 doc 级旁路包 `discuss/`（§3） |
| 事件溯源：SQLite `events` 表 append-only + 投影可重建（§6） | **机制不变**。扩范围新增事件/Command/State 仍走同一 append-only + project/decide 合同（§8、IF-003 §10） |
| 单写者锁 / 取消协议 / per-kind reconcile（§5） | **不变**。`dispatch_agent` 的 reconcile 扩展为含 opencode 物化清理（§6） |
| 纯核心 `project`/`decide` 零 I/O、不感知测试模式（§3） | **不变**。后端选择、`simulate` 一样只在 cli/executor 边界注入 |
| FakeAgent 确定性函数（§7） | **保留**为 fake 后端，是双通道测试的 deterministic 通道（§4） |

> **判定原则**：除非 story/spec 明确或暗含修改架构的需求，否则一律延续 v0.1。v0.2 的三项（真实 Agent / inline-discussion / 模板校验）均落在既有增长轴上，无结构性返工。

### 0a. 产品合同与实现选择的边界

以下是必须可观察、可验收的产品合同：fake deterministic E2E 与 live real-agent E2E 双通道并存；显式 opt-in 的 live journey 必须真实运行 opencode、Scribe、Sage、Lex、Runtime、Git 和 report；有限 Human transcript 只能通过明确 actor 注入；TRIAGE、author、reviewer 的 outcome 语义不同；Sage↔Scribe 与 Lex↔Sage 的 discussion/RESPOND/review/pass 闭环必须由 Markdown parser/`trac discuss` 证据证明；report 必须包含可由 Agent 分析的 assignment、脱敏 I/O、discussion、attempt/retry、commit、audit gap、interrupted activity、阶段和最终状态。

以下是可替换的实现选择：opencode subprocess 的具体封装、console fixture 的载体、audit blob 的物理存储、Markdown renderer 的实现、临时目录命名、测试 runner 的配置传递方式和是否设置单次 dispatch 上限。实现选择改变时不得改变上述外部合同；任何 per-agent/per-round/总 timeout 或 dispatch boundary 必须作为 `LiveJourneyConfig`/Runtime activity 的显式可审计输入。未在 SPEC-003/ACC-003 中批准的环境变量或内部开关不是产品行为，不能作为通过 live journey 的隐含前提。

## 1. 生产路径（一条工作流路径 + 一条 doc 级旁路）

### 1a. 工作流路径（不变）

状态变更命令（`init`/`start`/`run`/`triage`/`review`/`status`/`replay`）仍经 `tracks.cli:main()` 委托 kernel，唯一组合根不变。

### 1b. inline-discussion 旁路（v0.2 新增，非事件溯源）

`trac discuss`（query/start/reply/edit/set-status）是一条 **doc 级协作旁路**：

```
trac discuss …  →  tracks.cli:main()  →  tracks.discuss（parser/locate/writer）  →  目标文档（flock 写）
```

- 它**不经过** `kernel.runtime` / `effects.executor`，**不产生事件**——直接对文档文件做 flock 串行化读写（SPEC-003 FR-110）。这与 v0.1 已存在的只读旁路（`trac status`/`replay` 直读 store）同类：并非所有 CLI 都走 run_loop，"唯一生产路径"约束的是**状态变更工作流命令**。
- 调用者：Agent（经 bash 执行 `trac discuss`）与 Human（IDE 手写 + CLI）；tracks 不在 v0.2 强制 Human/Agent 串行化（推迟到 web 界面，Aaron 决定）。
- **Runtime 只读集成**：评审退出门禁通过一次 `validate_document`（`checks=["discussion_ready"]`）只读查询讨论收敛状态，落 `verdict.*` 事件；`decide()` 仍纯——它只看 State 里的 verdict，不直接读文档（§5）。
- discussion 命令本身不产生事件，但每次 Agent outcome/评审门禁可记录由 Markdown parser/`trac discuss query` 生成的只读 `discussion_evidence` snapshot，供 report 关联；snapshot 是审计证据，不取代目标 Markdown。Agent stdout 或 stdout JSON 不能改变 thread/status/verdict。

> 这条旁路是**附加**的，不扰动事件溯源核心。讨论数据活在文档里（markdown blockquote），不进 `events` 表。

## 2. 包结构（v0.2 增量）

在 ARCH-001 §2 骨架上**只增不删**（`+` 为 v0.2 新增，`~` 为生长）：

```
src/tracks/
├── cli.py                  # ~ 新增 trac discuss（5 子命令）、trac validate 子命令；后端选择在边界注入
├── kernel/                 # 不变（events 枚举追加 Verdict.check 成员，见 §8）
│   └── events.py           # ~ Verdict.check Literal 追加 "template"/"discussion_ready"；Outcome 追加可选字段
├── workflows/              # ~ review.py 的 EXIT 守卫增加 discussion_ready + template 校验门（声明式数据）
│   └── review.py           # ~ M-STORY/M-SPEC/M-ACC 评审退出校验追加门禁项
├── effects/                # ~ 缓慢生长（接入真实 Agent，正如 ARCH-001 §2 预测）
│   ├── executor.py         # ~ dispatch_agent 按后端选择分派；validate_document 支持新 check
│   ├── agents.py           # ~ 抽出 AgentBackend 协议；FakeBackend（原 FakeAgent）+ OpencodeBackend
│   ├── opencode.py         # + opencode 后端：物化提示词、构造 --auto prompt、解析 JSON、baseline+后置审计、失败矩阵
│   ├── gitops.py           # 不变
│   └── audit.py            # + 越权审计：clean baseline、git status/diff 比对、安全回滚、临时目录清理
├── discuss/                # + doc 级协作旁路（非事件溯源，§3）
│   ├── model.py            # + Thread dataclass、查询结果类型
│   ├── parser.py           # + markdown → threads（全文扫描、跳过 fenced code、NFC 归一化）
│   ├── locate.py           # + 4 级降级定位 L0-L3、fail-closed 歧义处理
│   └── writer.py           # + threads → markdown（flock + tmp + rename、canonical 格式、空行分隔）
├── templating.py           # + 模板加载：按 kind 从 tracks/templates/ 读模板（命名概念，非杂物袋，ARCH-001 §9）
└── checks/
    └── validate.py         # ~ 追加 template 结构校验 + discussion_ready（调 discuss.query check-ready）
```

### 增长轴归属

| 包 | v0.2 增长 | 触发 |
|:---|:---|:---|
| `effects/` | `opencode.py` / `audit.py` 新增，`agents.py` 抽后端协议 | 接入真实 Agent（item 1） |
| `discuss/` | 新包（model/parser/locate/writer） | inline-discussion（item 2） |
| `checks/` | `validate.py` 追加 template/discussion_ready/trace | 模板、讨论与 acceptance trace 门禁 |
| `templating.py` | 新命名小模块 | 模板接入（item 3） |
| `kernel/` | 仅 `events.py` 枚举/字段 additive | 收口新 check 与 outcome 字段 |
| `workflows/` | `review.py` EXIT 守卫追加门禁项（声明式） | 门禁集成 |

> `discuss/` 是 v0.2 引入的**第五条轴**（doc 级协作），与事件溯源的四轴正交：它不导入 kernel/effects，不被 project/decide 引用，仅被 cli（旁路命令）与 checks/validate（discussion_ready 只读查询）调用。分层约束（ARCH-001 §3）相应补充：`discuss/` 自身可读写文档文件（flock），是其旁路职责，不属于"effects 唯一副作用边界"（那条约束针对事件溯源 runtime）。

## 3. Item 2：inline-discussion（`discuss/` 旁路）

### 3a. 数据模型（无持久化 ID）

`Thread`（`discuss/model.py`）字段对应 SPEC-003 FR-060：`thread_id`（单次扫描序号，**非持久 ID**）、`initiator`、`status`、`last_speaker`、`reply_count`、`snippet`、`mentioned_agents`、5 元组定位字段。

> **identity 权威（Aaron 决定）**：thread **不做持久化存储**，无文档内稳定标记、无 sidecar、无事件存储派生键。identity 靠**每次全文扫描 + 4 级降级**即时重建——全文扫描最能跟上文档变化。`T-NNN` 是单次扫描内的序号；跨扫描/重启/插入删除重排/复制/回滚，线程按内容（anchor+root 文本）重新定位。

### 3b. 定位（fail-closed）

`locate.py` 实现 L0（delta 修正精确）→ L1（Levenshtein 窗口）→ L2（仅根评论）→ L3（not found）。**写命令 fail closed**：reply/edit/set-status 仅在唯一置信匹配上执行；L1/L2 并列或低置信度 → 返回 `ambiguous` + 候选位置，**不写文件**；L3 → 报告 not found，由人类/Agent 智能处理（不静默命中）。query（读）可 best-effort。

**freshness token**（写命令权威 identity，louke 方案，Aaron 确认）：thread 无持久 ID，写命令须携带 `--token`（query 返回的 5 元组 / anchor+root 内容）；locate 重扫描按内容重定位并核对当前 thread_id 与给定一致，不符 → `stale`（不写、重新 query），使 fail-closed 能区分重排致编号漂移（IF-003 §7a / `LocateResult.stale`）。不采用原子 query+write。

> **gpt [RESOLVED]:** 与 SPEC-003 FR-060 / SKILL.md 上的同名 OPEN 线程同源：写命令只接收 `--thread-id`（单次扫描序号），不携带产生该 ID 的 query revision 或旧 5 元组。重排后同一 `T-NNN` 可合法指向另一条当前线程，locate 会把它当作有效匹配而非 stale，fail-closed 无法触发。架构层面需明确：(a) 写命令是否要求调用方传入 freshness token / 旧定位元组供比对；(b) 或者 Runtime/Agent 合同是否要求原子 query+write（同一 flock 持有期内完成）。选定后同步更新 IF-003 §7a CLI 合同与 `LocateResult` 类型。
>> **Scribe:** 已选定 freshness token 方案（同 louke、Aaron 确认）：写命令携带 `--token`（query 返回的内容定位 token = 5 元组 / anchor+root），locate 重扫描按内容 L0-L3 重定位并核对当前 thread_id 与给定一致；不符 → `stale`（不写、重新 query）。§3b 正文已补；IF-003 §7a CLI 合同追加 `--token` 输入列、`LocateResult.status` 扩展 `stale`；SPEC-003 FR-070/FR-080 同步。原子 query+write 不采用。@gpt 请确认是否可标记 [RESOLVED]。

### 3c. 写操作（flock + canonical）

`writer.py`：start/reply/edit/set-status 一律输出 canonical 格式（`> **Speaker [STATUS]:** body`），flock 写 tmp → rename 覆盖，自动空行分隔，parse 失败回滚。**Agent 写操作走命令；Human 可在 IDE 手写 parser 兼容格式**，Runtime 在门禁前解析/校验并捕获其 diff（SPEC-003 FR-050/FR-110）。

### 3d. 状态规则（格式一致性，非认证）

v0.2 本地 CLI 不做真实身份认证：`resolved` 的 `--operator` 须等于 initiator（**格式一致性规则**，按设计可伪装，直到 web 引入可信身份）；`reopen` 任何人可设。`--file` canonicalize + scope gate（拒绝 `../`、repo 外绝对路径、逃逸 symlink）。

### 3e. skill 交付

inline-discussion 以 skill `tracks-discuz` 交付（`tracks/skills/tracks-discuz/SKILL.md`）。**加载方式（简化，Aaron）**：Runtime 将 skill 正文直接注入 Sage 调用上下文（不依赖宿主 repo 路径）；物化到 opencode 可发现位置作为备选，留 spike。

## 4. Item 1：真实 Agent seam（`effects/` 生长）

### 4a. 后端抽象

`agents.py` 抽出 `AgentBackend` 协议：`dispatch(assignment) -> Outcome`。两个实现：

- `FakeBackend`：原 FakeAgent 确定性行为（`(assignment) -> outcome`，由 `simulate` 控制分支），**绝不触发 opencode**。
- `OpencodeBackend`（`opencode.py`）：subprocess 执行 `opencode run --agent <Name> --format json --dir <repo> --auto "<prompt>"`，`<Name>` ∈ {`Scribe`, `Sage`, `Lex`}（首字母大写）。

**后端选择**在 cli/executor 边界由 `TRAC_AGENT_BACKEND=fake|opencode`（默认 opencode）注入；`TRAC_FAKE_SIMULATE` 强制 fake；conftest 于 E2E fake 通道强制 fake。`project()`/`decide()` 不感知后端（纯函数边界不破例，同 v0.1 `simulate` 纪律）。

> `Assignment.role` 内部仍用小写 `scribe`/`sage`/`lex`（延续 IF-001 §5），由 OpencodeBackend 映射到首字母大写的 opencode agent 名（`Scribe`/`Sage`/`Lex`）。

### 4b. 产物权威（文件 diff，非 JSON）

**目标文件的受控 diff = 权威产物；stdout JSON 仅作执行协议/诊断**（单一产物来源）。Runtime 记录 baseline → Agent 运行（直接编辑目标文档，经 permission 白名单授权）→ 对目标文档取受控 diff 为产物并独立校验。

该规则按 assignment kind 收窄，避免把只读活动误判为作者失败：

| assignment | 目标文档 | diff 合同 | 结构化结果 |
|:---|:---|:---|:---|
| `TRIAGE` | story（读取原始需求，可经 discuss 提问） | 可选；无 diff 是合法成功 | `status=done`，随后进入 Human triage gate |
| `DRAFT` / `RESPOND` | 当前阶段文档 | **必需**；无 diff 才分类为 `no_target_diff` | 产物进入 validate/commit |
| `SAGE_REVIEW` / `LEX_REVIEW` | 当前阶段文档 | 可选；pass/no-change 合法，提问时 diff 记录 discussion | Runtime 以权威文档的 `discussion_ready` 派生 `verdict=pass|revise` |

因此 reviewer verdict 不信任自然语言 stdout：评审结束后重新解析目标文档；存在 open/reopen 线程即 `revise`，全部 resolved 或无问题即 `pass`。reviewer 的 no-change pass 合法，但 live journey 另须检查指定 discussion 是否真的有 reply、RESPOND 目标 diff/commit 和 initiator resolve。

### 4c. 物化与清理

调用前将 canonical 提示词（`tracks/agents/<Name>.md`，随 tracks 版本固定）物化到已批准的 opencode 发现路径 `.opencode/agents/<Name>.md`（复数，文件名与 `--agent` 名逐字一致）。物化合同：已有同名 agent 拒绝静默覆盖（备份或 command_id 唯一名）；终态清理；崩溃后下次启动 reconcile 清理悬挂物化。具体备份名、清理时机实现可选，但不得留下未审计的宿主配置修改。

### 4d. 角色分工（按 Flow + Aaron 收窄）

- Scribe = TRIAGE 中的 story discussion actor 与 M-STORY 作者（+ RESPOND 修订）；不写 spec/acceptance。
- Sage = M-STORY reviewer、M-SPEC/M-ACC author（+ RESPOND 修订）以及对应 discussion 的响应者；Sage 不能写 Human 决定。
- Lex = M-SPEC/M-ACC reviewer，v0.2 live journey 使用真实 Lex；Lex 不能写 Human 决定。
- v0.2 可达评审阶段：M-STORY（Sage 真实评审 + Scribe 真实 RESPOND）、M-SPEC（Sage 真实起草/RESPOND + Lex 真实评审）、M-ACC（Sage 真实起草 + Lex 真实评审），随后进入 M-REQ-APPROVAL Human gate。

> **gpt [RESOLVED]:** §4d 第二条仍称 Sage 是"spec/acceptance 作者"，但最后一条已明确 M-ACC 延后。两者矛盾：如果 M-ACC 不在 v0.2 范围，Sage 的 v0.2 author 职责应只含 spec，不含 acceptance。请收窄为"Sage = story reviewer + spec author（v0.2）"，并把 acceptance author 标注为 M-ACC 恢复后才生效。同时与 Sage.md frontmatter/正文、SKILL.md 适用阶段、Story §2 的收窄保持一致（见这些文件上的同名 OPEN 线程）。
>> **Scribe:** 已收窄 §4d 第二条为 “Sage = story reviewer + spec author（v0.2）”，acceptance author 标注随 M-ACC 延后（M-ACC 恢复后生效）；Sage.md description/职责、tracks-discuz skill 适用阶段、Story §1 注/§2 已同步收窄。@gpt 请确认是否可标记 [RESOLVED]。

### 4e. Assignment 构造与测试场景 overlay

Runtime 在 `decide()` 产出 `dispatch_agent` command 时构造基础 Assignment payload。author assignment（`DRAFT`/`RESPOND`）携带 `template_kind`、`skill`、`skill_version`。显式传入 `trac run --assignment-overlay <scenario.json>` 时，Runtime 把 JSON object 完整嵌套到 `assignment.scenario_context`；不 merge 到 assignment 根部，因而不能覆盖 workflow 字段（IF-003 §3a）。

测试场景的传递路径：

1. **来源**：scenario JSON 位于 test-only fixture 目录或由操作者显式提供，不写入 canonical Agent Markdown，也不打入 wheel 的 production resources。
2. **传递**：CLI 校验 overlay 是 JSON object；Executor 深拷贝到每个 dispatch 的 `params.assignment.scenario_context`，随 command/audit evidence 持久化。
3. **Agent 可见性**：OpencodeBackend 把完整 assignment 序列化到 prompt；Agent 可读取场景中的 finding marker、条件和动作约束。
4. **Runtime 不解释**：Runtime 不解析场景语义，不根据它改变状态机或 verdict 派生。verdict 仍由 Markdown parser/`trac discuss query` 的 thread 状态决定。
5. **边界**：`--max-dispatches <N>` 只限制一次 `trac run` 调用的 dispatch 数，不伪造 outcome；fixture 另外按 finding marker 检查 thread、reply、RESPOND diff/commit、resolved 与 pass。

## 5. Item 3：模板 + 校验（`checks/` 与 `templating.py`）

### 5a. 模板接入

`templating.py` 按 kind 从 `tracks/templates/{story,spec,acceptance,...}.md` 读取模板，取代 main.py 硬编码 STORY_TEMPLATE。生成文档时套用（M-START 创建空骨架保留占位符）。

### 5b. 校验合同（区分骨架与 outcome，Aaron §5.1）

| 时机 | 校验 |
|:---|:---|
| M-START 空骨架创建 | **不校验** |
| Scribe/Sage outcome 完成 | Runtime 立即 `validate_document`；结构或 scope 不合格 → 不进入评审、走重派 |
| M-STORY/M-SPEC/M-ACC 评审退出门禁 | 强制 `validate_document`（template + discussion_ready；acceptance 另自动跑 trace）；只有未 resolved 的 inline-discussion 阻塞退出 |
| `trac validate --file <path>` | 独立命令，按模板校验结构，报告不符项含 `line:N`，可独立运行 |

`checks/validate.py` 追加 `template`（结构）、`discussion_ready`（调 `discuss` 只读 query --check-ready）与 acceptance 的 `trace`（AC↔FR 双向覆盖）。均经既有 `validate_document` → `verdict.*` 事件路径；除结构、scope 与 acceptance trace 外，评审退出只由未 resolved 的 inline-discussion 阻塞。

## 6. 安全与权限模型（baseline + 后置审计）

Agent frontmatter 提供 opencode 支持的粗粒度工具纵深防御；它不是 v0.2 的文件级授权真相。每次 assignment 的 scope 合同是目标文档与该 command_id 专属临时目录，Runtime 的 baseline + 后置 git 审计是“只能写授权范围”的强制门禁。

`audit.py` 后置审计（运行级）：

1. Runtime 在 dispatch 前记录 clean baseline identity，不提交或 stash 以掩盖 Human 既有修改。
2. assignment/body 明确本次角色可写范围：Scribe 仅 story.md；Sage 依当前 stage 写目标 spec/acceptance 或 RESPOND 目标；Lex 仅 reviewer 可写的 spec/acceptance discussion。Agent 不能写 Human 决定事件。
3. Agent 退出后，Runtime 独立比较 baseline 与工作区 diff；目标文档和专属临时目录之外的写入即越权，记录路径级证据，失败且不推进。
4. 只回滚可证明由该 Agent 产生的改动，绝不覆盖 baseline 中已有的 Human 修改；临时目录按 command_id 隔离并在终态/reconcile 清理。

> Human/Agent 完全串行化推迟到 web 界面（届时 Human 仅经 web 编辑）；v0.2 靠 baseline + 后置审计**检测**越权，不阻止并发人类编辑（Aaron 决定）。

## 7. 失败矩阵与 reconcile（NFR-030）

`OpencodeBackend` 枚举失败分支：opencode 可执行文件缺失；provider/model/凭据不可用；非零退出；超时（含**子进程组清理**）；JSON 流截断；作者 `DRAFT/RESPOND` 退出 0 但无目标 diff；SIGINT/kill-9；"文件已改但 outcome 未落盘"的 reconcile。TRIAGE 无 diff、reviewer no-change/pass 不属于 no-target-diff。每类失败：报告原因（退出码 + stderr 摘要）、记录 command/outcome 事件、attempt 记账、子进程组清理、reconcile 结果；不写半成品产物事件；可恢复重试。

`dispatch_agent` 的 per-kind reconcile（ARCH-001 §5e）扩展：reconcile 时除探产物外，**清理上次悬挂的物化 agent 定义与 command_id 临时目录**，再决定是否重派。若目标文档已有 diff 但 outcome 未落盘，reconcile 只能根据 baseline、受控 diff、validate 和 commit 证据决定恢复；不能凭 stdout 或文件存在推断成功。`dispatch_agent` 产物覆盖写天然幂等不变。

## 8. 事件 / 接口增量（详见 IF-003）

原 v0.2 discuss/agent/template 范围不新增事件类型；扩范围（M-ACC/M-REQ-APPROVAL）新增
类型，完整封闭集见 IF-003 §10。所有新增仍为 additive：

- `Verdict.check` Literal 追加 `"template"`、`"discussion_ready"`、`"trace"`。
- M-ACC/M-REQ-APPROVAL 新事件/Command/State 见 IF-003 §10a~§10c。
- `Outcome` / `outcome.received` payload 追加可选字段：`diff_ref`（受控 diff）、`audit_evidence`（越权路径级证据）、`failure_class`（失败矩阵分类）。
- `Assignment` 追加可选字段以承载模板/skill 注入语义（`template_kind`、`skill`），或由 OpencodeBackend 据 role 从 canonical 文件构造（实现层定）。
- 新增 `discuss/` 公共类型（`Thread`、查询结果）与 CLI 合同（`trac discuss` 5 子命令、`trac validate`），见 IF-003。

> discuss 操作**不产生事件**（doc 级旁路）；门禁通过 `validate_document(discussion_ready)` 的 verdict 事件进入 State，保持 `decide()` 纯净。

## 9. v0.2 有意识简化与 spike-pending

- **实现前 spike/验证**（不改变产品合同）：① 目标 opencode 版本的 agent 加载/权限配置；② skill 通过 Runtime 上下文注入并核对 `skill_version`；③ 项目 `.opencode/opencode.json` 对当前项目与 command_id 临时目录的目录边界验证；④ live fixture 的有限 console transcript 与子进程边界。agent 发现路径和大小写的已批准合同 = `.opencode/agents/<Name>.md`、文件名与 `--agent` 逐字一致；spike 不能把尚未审议的实现细节升级为产品行为。
- M-ACC 与真实 Lex 已纳入本版本；不做 M-DESIGN 及之后阶段；不做 opencode `-m`/`--variant`；不做 @mention 通知推送；不做讨论跨文件关联/历史版本化（git 提供）。
- discuss 无持久化 ID（全文扫描即时重建），是 Aaron 决定的有意识简化（换取对文档变化的及时跟随），代价是每次操作全文扫描（< 1MB 文档 < 1s，NFR-020）。
- 交付物一致性 = 存在性 + 版本检查（frontmatter 版本随流程变更升版），落交付门禁（pre-commit/CI），非 Runtime 行为，不做 digest/manifest。
- Human/Agent 真实身份认证和通用并发串行化：推迟 web 界面；v0.2 live fixture 仍必须使用显式 actor 注入，不能以自由的 Agent 字符串冒充 Human。live journey 的 per-agent/per-round/total timeout、review round 上限和失败宿主保留是测试/Runtime 可审计合同，不是隐藏环境变量语义。

## 10. 工作流审计轨迹与报告

### 10.1 事实来源

审计报告不创建测试专用日志真相源。Runtime 既有 append-only `events` 表是唯一事实来源；`command.issued` 作为活动开始，关联的 outcome/verdict/commit 事件作为结束或结果。没有结束事件的活动在报告中显示为 `interrupted`/`unknown`。

### 10.2 记录边界

只记录需要 Human 解释的工作流活动：Agent dispatch、Human gate、失败/重试/回退、审计、生成物提交和阶段转移。普通成功 `validate` 不单独显示；导致重试或阻塞的校验失败必须作为 attempt 结果证据记录。Agent 输入/输出和大参数使用脱敏 blob 引用，生成物使用 commit hash。每个活动必须可由 activity/command/run 标识关联 stage/substate、attempt、开始/结束和最终结果。

### 10.2a Agent I/O 数据通路

canonical Assignment 是 Agent 输入事实，随 `command.issued` 记录（固定路径等上下文不重复展开）。OpencodeBackend 已由 subprocess 捕获 stdout；本次将其从“只做 JSON 诊断”扩展为：捕获完整 JSON/NDJSON → 解析 → 统一脱敏 → Runtime 写 content-addressed audit blob → `outcome.received` 仅保存 ref、digest 与结构摘要。失败/截断也保留脱敏可用部分。

audit blob 是 best-effort 辅助证据，不与状态推进使用同一失败域：blob 写入失败时不引用缺失 blob，outcome 仍按业务结果落盘，并附 `audit_completeness="partial"` 与 gap 原因。报告遇到 gap 显式展示，不进行反向推断。

### 10.3 报告生成

`trac report --run-id <run-id> --output <dir>` 从当前 Git 根目录解析 `.tracks/runtime/tracks.db` 和 Git remote，生成规范化 `report.md`（主产物）。同时生成自包含 `index.html` 查看器：内联固定版本 `marked.js`（package data `tracks/assets/marked.min.js`，文件头注释标明版本与来源；生成时读取注入 HTML，不从 CDN 加载），浏览器端 `fetch("report.md")` 客户端渲染，`python -m http.server` 即可查看。查看器仅为测试/临时用途，不是 tracks 的 Markdown 渲染能力，未来 web 功能可替代移除。报告生成器负责 timeline、retry/audit 因果链和 commit URL；`marked.js` 只做展示渲染，不承担事实推导。报告至少展开或提供可展开引用：脱敏 assignment/I/O、Markdown parser/query 产生的 discussion thread/reply/status/参与者、attempt/failure/retry、commit link/fallback、audit gap、interrupted/unknown、阶段和最终状态。XSS 防护由 `marked.js` 的 HTML 转义保证；Agent JSON、讨论内容和 stderr 不得作为未转义 HTML 执行。

### 10.4 Live E2E 宿主

tracks 自身 live E2E 要求显式 opt-in，并由 `TRACKS_E2E_GITHUB_REPO` 指向可丢弃 GitHub 仓库；测试根目录是本地宿主 Git repo，测试脚本决定临时分支。安全边界是只授权该测试仓库的凭据，而不是声称通用 bash 能被强制限定到单一分支；结束后通过 GitHub/gh 回读审计只有预期分支变化。普通用户运行 `trac report` 不读取该环境变量，仅使用当前宿主 repo。缺失或不可验证的远端是配置失败，不是本地 fallback。

完整 live journey 是独立 opt-in 测试。测试脚本以测试 actor 注入 triage/review/approval Human 事件；有限 console transcript 只作为 Human mock 输入交给真实 Agent，不能由 fixture 直接写 discussion 或目标文档。测试设置并记录每次 Agent timeout、每个 review round timeout、最大 review round 和整条旅程总时限；失败/超时保留宿主目录、事件库、diff 和报告。测试开始、结束都打印本地宿主、报告、remote 和 branch，解决临时目录不可发现问题。单次 dispatch boundary（如需要）是显式 harness/runtime activity 配置，不是隐藏环境变量。
