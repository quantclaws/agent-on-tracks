---
spec_id: SPEC-003
created: 2026-07-30
status: draft
sha:
---

# SPEC-003: v0.2 评审闭环——真实 Agent、inline-discussion、文档模板

> 三项范围，实现顺序 1 → 3 → 2。inline-discussion（item 2）以 skill `tracks-discuz` 交付，agent 行为以提示词交付；二者均为本合同的组成部分，存放于外部目录、以引用方式纳入（见 FR-0040 / FR-0130）。
>
> Aaron 扩容裁定（spec 仅用 15/30 条 FR 预算）：M-ACC 与真实 Lex 纳入 v0.2（FR-0160 / FR-0170；Scribe/Sage/Lex 全真实）；M-REQ-APPROVAL 也纳入 v0.2（FR-0180 / FR-0190 / FR-0200）以形成完整需求故事（story→spec→acceptance→批准 baseline→Issues）；集成测试须把状态机完全走到（NFR-0040），normative 转移清单见「状态与生命周期」。

## 状态与生命周期

本节是 v0.2 可达阶段（M-START → M-ACC）子状态机的 normative 转移合同：未列出的转移即不允许；FR/AC/测试以转移行号引用（如 `SM-02.5`）。源自 `.tracks/wiki/flow.md` §3–§6（mermaid 全图，仅作示意）；与 flow.md 漂移时以本节为准并须回改 flow.md。SM-01/SM-02/SM-03 是 v0.1/v0.2 已有行为的 normative 重述（变更基线），SM-04 随 FR-0160 新增。

### SM-01 M-START（trac start）

1. （发起）→ CHECK_ACTIVE：Human 执行 `trac start`
2. CHECK_ACTIVE → BACKLOG：有活跃 run（需求记入 backlog，退出，不建分支）
3. CHECK_ACTIVE → CHECK_WORKSPACE：无活跃 run
4. CHECK_WORKSPACE → FAILED：工作区有未提交修改（报告原因，拒绝 start）
5. CHECK_WORKSPACE → CHECK_BRANCHES：工作区干净
6. CHECK_BRANCHES → AWAIT_CONFIRM：有未合并分支（提示后果，awaiting_human）
7. CHECK_BRANCHES → CREATE_BRANCH：全部已合并
8. AWAIT_CONFIRM → CREATE_BRANCH：Human 确认继续
9. AWAIT_CONFIRM → （终止）：Human 取消
10. CREATE_BRANCH → WRITE_STORY：`releases/v{x}` 创建成功
11. WRITE_STORY → （退出 → M-STORY）：story.md 骨架含原始需求，stage.exited

### SM-02 M-STORY

1. （进入）→ TRIAGE：stage.entered(M-STORY)
2. TRIAGE → REJECTED：human.triage(no_go | park)（记入 backlog，删除 release 分支，run.completed）
3. TRIAGE → DRAFT：human.triage(go)
4. DRAFT → SAGE_REVIEW：Scribe outcome validate pass，committed
5. DRAFT → DRAFT：validate fail，重派 Scribe（≤3，超限升级 Human）
6. SAGE_REVIEW → HUMAN_REVIEW：sage.verdict(pass)，validate pass，committed
7. SAGE_REVIEW → RESPOND：sage.verdict(comment)，validate pass，committed
8. SAGE_REVIEW → SAGE_REVIEW：validate fail，重派 Sage（≤3，超限升级 Human）
9. HUMAN_REVIEW → EXIT：human.review(no_comment) 且本轮 sage pass
10. HUMAN_REVIEW → RESPOND：human.review(comment)
11. RESPOND → SAGE_REVIEW：Scribe 响应 validate pass，committed，新一轮
12. RESPOND → RESPOND：validate fail，重派 Scribe（≤3，超限升级 Human）
13. EXIT → （退出 → M-SPEC）：生成 sha 写入 frontmatter，story.committed(final)，stage.exited

可休眠点：TRIAGE、HUMAN_REVIEW（awaiting_human，decide() 返回空 command，事件回放恢复）。

### SM-03 M-SPEC

1. （进入）→ DRAFT：stage.entered(M-SPEC)（Sage 起草 spec.md，继承 M-STORY review 上下文）
2. DRAFT → LEX_REVIEW：validate pass（结构有效且无阻塞性未决 inline-discussion），committed
3. DRAFT → DRAFT：validate fail，重派 Sage（≤3，超限升级 Human）
4. DRAFT → ROLLBACK：scope_overflow（有效 FR > 30，不重派压缩）
5. LEX_REVIEW → HUMAN_REVIEW：lex.verdict(pass)，validate pass，committed
6. LEX_REVIEW → RESPOND：lex.verdict(comment)，validate pass，committed
7. LEX_REVIEW → LEX_REVIEW：validate fail，重派 Lex（≤3，超限升级 Human）
8. HUMAN_REVIEW → EXIT：human.review(no_comment) 且本轮 lex pass
9. HUMAN_REVIEW → RESPOND：human.review(comment)
10. HUMAN_REVIEW → ROLLBACK：Human 裁定需改 story
11. RESPOND → LEX_REVIEW：Sage 响应 validate pass（未决 inline-discussion 可继续讨论），committed，新一轮
12. RESPOND → RESPOND：validate fail，重派 Sage（≤3，超限升级 Human）
13. EXIT → （退出 → M-ACC）：Lex pass + Human `no_comment` 后过结构/讨论门禁；仅未 resolved 的 inline-discussion 阻塞退出，随后 stage.exited
14. EXIT → DRAFT：格式终验 fail，重派 Sage
15. ROLLBACK → （回退 M-STORY）：stage.rolled_back，落点 DRAFT（不重复 TRIAGE）

可休眠点：HUMAN_REVIEW。Lex 为真实 agent（FR-0020，Aaron 扩容裁定）。

### SM-04 M-ACC（同构复用 SM-03，随 FR-0160 新增）

1. （进入）→ DRAFT：stage.entered(M-ACC)（Sage 起草 acceptance.md，继承 M-SPEC review 上下文）
2. DRAFT → LEX_REVIEW：validate pass（schema + AC↔FR 双向 trace，FR-0170），committed
3. DRAFT → DRAFT：validate fail，重派 Sage（≤3，超限升级 Human）
4. DRAFT → ROLLBACK：trace 缺口不可在本文档修复（需改 spec/story）
5. LEX_REVIEW → HUMAN_REVIEW：lex.verdict(pass)，validate pass，committed
6. LEX_REVIEW → RESPOND：lex.verdict(comment)，validate pass，committed
7. LEX_REVIEW → LEX_REVIEW：validate fail，重派 Lex（≤3，超限升级 Human）
8. HUMAN_REVIEW → EXIT：human.review(no_comment) 且本轮 lex pass
9. HUMAN_REVIEW → RESPOND：human.review(comment)
10. HUMAN_REVIEW → ROLLBACK：Human 裁定需改 spec/story
11. RESPOND → LEX_REVIEW：Sage 响应 validate pass，committed，新一轮
12. RESPOND → RESPOND：validate fail，重派 Sage（≤3，超限升级 Human）
13. EXIT → （退出 → M-REQ-APPROVAL）：格式终验 pass，stage.exited（M-REQ-APPROVAL 经 FR-0180 纳入 v0.2）
14. EXIT → DRAFT：格式终验 fail，重派 Sage
15. ROLLBACK → （回退 M-SPEC 或 M-STORY）：stage.rolled_back

可休眠点：HUMAN_REVIEW。

### SM-05 M-REQ-APPROVAL（随 FR-0180 新增）

1. （进入）→ PREVIEW：stage.entered(M-REQ-APPROVAL)（M-ACC 退出后）
2. PREVIEW → AWAIT_HUMAN：baseline preview 就绪（三件套 revision digest + 摘要，FR-0190）
3. AWAIT_HUMAN → APPROVED：human.approval（绑定三件套 digest；Agent 不能代批）
4. AWAIT_HUMAN → RETURNED：human.return（产品理由 + 目标阶段）
5. APPROVED → ISSUES：三件套设为只读、记录 approval identity（FR-0190）
6. ISSUES → （退出边界）：Runtime 拆分 spec → GitHub Issues + 关联 Project（FR-0200），stage.exited → M-DESIGN（不在 v0.2 范围，run 停在该边界）
7. RETURNED → （回退 M-STORY / M-SPEC / M-ACC）：stage.rolled_back

freshness：三件套任一文档变化立即使 approval stale（digest 不匹配），下游阻断，须重走 M-REQ-APPROVAL（FR-0190）。可休眠点：AWAIT_HUMAN、退出边界（SM-05.6 后 run 停在 M-DESIGN 边界、事件回放恢复）。

## 功能需求

### FR-0010 agent 抽象与后端选择

- **来源**：`BS-01`、`BS-02`
- **交付入口**：环境变量 `TRAC_AGENT_BACKEND`

- runtime 通过统一的 agent 抽象调用 agent，不直接耦合具体后端实现。
- 后端由环境变量选择：`TRAC_AGENT_BACKEND=fake|opencode`，默认 `opencode`。
- `TRAC_AGENT_BACKEND=fake` 或 `TRAC_FAKE_SIMULATE` 设置时，强制使用 fake 后端（即使 `TRAC_AGENT_BACKEND=opencode`）。
- fake 后端复用 v0.1 FakeAgent 行为，绝不触发 opencode。
- 测试双通道：
  - (A) fake E2E——必跑、deterministic、精确断言状态机；conftest 默认强制 fake 后端于此通道，保证不依赖真实 opencode。
  - (B) live opencode E2E——真 Agent，provider/model 由环境变量配置；断言真实启动、权限、JSON 协议、目标 diff 产物格式与恢复，不断言具体文本内容；缺凭据时 (B) skip（不 fail），在 CI 中为独立 required job、本地 opt-in。
- live journey 是 (B) 的完整、独立、opt-in 旅程：启用后必须由真实的 `opencode` 子进程运行 `Scribe`、`Sage`、`Lex`，并由真实 Runtime、Git 和 `trac report` 完成；不得用 fake Agent、直接写文档的测试驱动器或测试步骤摘要替代其中任一事实来源。缺少 provider/model/凭据时，未启用的 live job 明确 `skip` 且不得退化为 fake；已显式启用但配置不完整或 provider 无法连接时为配置/运行失败，保留宿主并报告原因。
- live journey 使用固定 seed 和有限 Human transcript。seed 至少保留两个产品未决槽位：交付入口（CLI 或 library）与空白行是否计入统计；结果展示位置可作为 reviewer 用来验证闭环的第三个产品槽位。实现语言、框架、CI 和架构不进入 Human 决策，Agent 对这些技术选择采用合理默认。每个槽位的预录答案带明确 actor（例如 `LiveE2E-Human`）注入真实 Agent 的 console fixture；console 只提供 Human mock 输入，不是讨论或产物真相。
- live harness 还必须验证配置失败边界：完整旅程缺少或不能验证 `TRACKS_E2E_GITHUB_REPO` 时失败，不得把本地仓库冒充远端；provider 配置在显式启用后失效时按 `provider_unavailable` 失败。provider 未配置导致的默认 skip 与 GitHub 宿主配置错误不得混为一类。

> **gpt [RESOLVED]:** 这仍与 Story 3.1 的 Human 决定“E2E 使用真的 Agent，provider/model 由环境变量定义”冲突。请把测试合同拆为两类并分别映射 AC：必跑 fake E2E 精确断言状态机；live opencode E2E 断言真实启动、权限、JSON 协议、产物格式和恢复，但不断言文本内容。还要定义 live 环境缺凭据时 fail/skip/独立 required job。
>> **Scribe:** 接受。FR-010 测试合同拆两类并分别映射 AC：(A) fake E2E——必跑、deterministic、精确断言状态机（保留 AC-0103）；(B) live opencode E2E——真 Agent、provider/model 由 env 配置（Aaron §3.1），断言真实启动/权限/JSON 协议/目标 diff 产物格式/恢复，不断言文本内容。缺凭据时 (B) skip（不 fail），CI 独立 required job、本地 opt-in。
>> **Scribe:** 结论已写入 FR-010 正文（fake/live 双通道；live 真 Agent + env 配 provider/model，断言协议/权限/目标 diff/格式不断言文本；缺凭据 skip 为独立 required job）。@gpt 请确认是否可标记 [RESOLVED]。

#### 分派 outcome 合同（FR-0010 / FR-0020 / FR-0210）

Runtime 按 assignment kind 解释 Agent outcome，不能把所有 dispatch 当作同一种作者任务：

| kind | 允许的成功 outcome | 目标 diff | 权威判定 |
|:---|:---|:---|:---|
| `TRIAGE` | `status=done`；没有目标 diff 仍是合法成功，随后进入 Human triage gate | 可选；讨论提问/回答须由 `trac discuss` 写入 story | Runtime 读取 parser 产生的 discussion snapshot，并等待 `human.triage(go\|no_go\|park)`；Agent 不能自行作出 triage 决定 |
| `DRAFT` | `status=done` | 当前阶段目标文档必须有受控 diff；缺 diff 是 `no_target_diff` 失败 | Runtime validate，通过后才 commit 并进入 reviewer |
| `RESPOND` | `status=done` | 当前阶段目标文档必须有受控 diff；必需的 discussion reply 与该 diff 属于同一轮证据 | Runtime validate，通过后才 commit 并重新 dispatch reviewer |
| `SAGE_REVIEW` / `LEX_REVIEW` | `status=done`，`verdict` 由 Runtime 产生 `pass\|revise` | reviewer 可无目标文档 diff；提出问题时以 discussion diff 作为证据 | Runtime 重新运行 Markdown parser/`trac discuss query`：存在 open/reopen 为 `revise`，全部 resolved 或无讨论才为 `pass`；不信任 stdout 自报 verdict |

`self_report`、stdout JSON 和 console 文本都只能作为诊断或审计 I/O；它们不能直接完成 triage、review、approval、return，也不能使 discussion 变为 resolved。Agent 只能以 assignment 授权的 Agent actor 写 discussion；Human 决定必须由显式 Human actor 注入并产生对应 `human.*` 事件。

### FR-0020 opencode 后端

- **来源**：`BS-01`、`BS-10`
- **交付入口**：无独立入口，依附 FR-0010

- opencode 后端以 subprocess 执行：`opencode run --agent <Name> --format json --dir <repo> --auto "<prompt>"`。
- `<Name>` ∈ {`Scribe`, `Sage`, `Lex`}（首字母大写；Lex 经 Aaron 扩容裁定纳入）。
- `--auto` 表示非交互自动执行；`--format json` 输出机器可解析结果。
- 产物权威：目标文件的受控 diff 为权威产物；stdout JSON 仅作执行协议/诊断，不作为产物来源（见 NFR-0030）。
- 物化：调用前将 canonical 提示词（`tracks/agents/<Name>.md`）物化到 opencode 发现路径 `.opencode/agents/<Name>.md`（复数，Aaron 决定），使 opencode 能按名解析。
  - 发现路径已定 = `.opencode/agents/<Name>.md`（复数，Aaron 决定，与本仓现有 `.opencode/agents/` 一致）。**命名/大小写规则**（Aaron spike 固化）：opencode 目录发现大小写不敏感，但 `--agent <name>` 按文件名精确解析——文件名即 agent 名，`tracks/agents/<Name>.md` 与 `--agent <Name>` 逐字一致（大小写敏感）即可发现，无需再留 live 验证。
  - 覆盖已有同名 agent：拒绝静默覆盖（备份或使用 command_id 唯一名）。
  - 终态清理物化产物；崩溃后下次启动 reconcile 清理悬挂物化（复用 D-11/D-13）。
- v0.2 不支持 `-m` / `--variant` 参数。

> **gpt [RESOLVED]:** 本仓库现有 opencode agent 位于 `.opencode/agents/`（复数），这里写 `.opencode/agent/`（单数），且尚未证明大小写 Name 能被发现。请先用 spike 固化目标 opencode 版本的真实发现路径/命名规则，再写成合同。物化还需定义：覆盖已有同名 agent 时拒绝还是备份、任务后是否清理、崩溃后如何 reconcile；否则 tracks 会无授权改写宿主的 opencode 配置。
>> **Scribe:** 接受，列为 spike 前置。`.opencode/agent/`（单数）与本仓现有 `.opencode/agents/`（复数）不一致，且大小写 Name 可发现性未证——须先在目标 opencode 版本 spike 固化真实发现路径/命名/大小写，再写入合同。物化合同补：覆盖已有同名 agent 拒绝静默覆盖（备份或用 command_id 唯一名）、终态清理、崩溃后下次启动 reconcile 清理悬挂物化（复用 D-11/D-13）。FR-020 标记 spike-pending。
>> **Scribe:** 结论已写入 FR-020 正文（发现路径/命名/大小写待目标 opencode spike 固化；物化合同：拒绝静默覆盖同名 agent、终态清理、崩溃后 reconcile 清理悬挂物化）。@gpt 请确认是否可标记 [RESOLVED]。
>> **Scribe:** Aaron 已定发现路径 = `.opencode/agents/<Name>.md`（复数，与本仓现有 `.opencode/agents/` 一致），FR-020 正文已固化。命名/大小写可发现性（opencode 是否按 `Scribe`/`Sage` 名解析）与 permission frontmatter 精确写法仍留实现 spike；物化合同（拒绝静默覆盖/终态清理/崩溃 reconcile）已在正文。@gpt 请确认是否可标记 [RESOLVED]。
>> **Scribe:** 命名/大小写 spike 已由 Aaron 固化并写入正文：opencode 目录发现大小写不敏感，`--agent <name>` 按文件名精确解析，文件名即 agent 名（大小写敏感逐字一致），无需再留 live 验证。permission frontmatter 精确写法另见 FR-030 收窄 spike（非阻塞）。本线程发现路径/命名部分闭合。

> **gpt [RESOLVED]:** `<Name>` 只有 Scribe/Sage，但本 Spec 又把门禁接到 M-SPEC/M-ACC 并要求 Sage/Lex 使用 discussion。按 Flow，M-SPEC/M-ACC reviewer 是 Lex；当前没有 Lex 后端或提示词，所以“真实 Agent 评审闭环”在 M-SPEC 无法成立。请明确本版只让 M-STORY 全真实、Lex 暂时 fake，或把 Lex 纳入 FR/AC/交付物。
>> **Scribe:** 按 Aaron 决定（同意收窄）：v0.2 真实 Agent 仅 Scribe/Sage；Lex 保持 fake（无 Lex 后端/提示词）；M-ACC 延后。阶段可达：M-STORY 全真实（Scribe 起草 + Sage 评审）；M-SPEC Sage 起草（真实）+ Lex 评审（fake）；M-ACC 出 v0.2 范围。将改 FR-100/FR-130（“Sage/Lex”→“Sage”，门禁阶段→M-STORY/M-SPEC）、补 Out-of-Scope（真实 Lex、M-ACC）与行为种子 Lex=fake。
>> **Scribe:** 结论已写入正文（按 Aaron 收窄：FR-100/FR-130 门禁与 skill 使用方去 Lex、阶段→M-STORY/M-SPEC；真实 Lex 与 M-ACC 移出 v0.2 范围）。@gpt 请确认是否可标记 [RESOLVED]。
>>> **gpt:** 暂不能关闭。FR-100/FR-130 已收窄，但 Story §2、Sage 的职责和 frontmatter 仍让 Sage 起草/评审 acceptance，skill 也仍声明适用于 M-ACC；这与“M-ACC 延后”矛盾。请同步收窄这些交付物后再关闭。
>> **Scribe:** Story §2、Sage.md 的 description/职责、tracks-discuz skill 的适用阶段已全部收窄到 v0.2 M-STORY/M-SPEC（Sage 评审 story + 起草 spec；acceptance 起草与 M-ACC 延后；Lex fake），与 FR-100/FR-130 一致。@gpt 请确认是否可标记 [RESOLVED]。
>>> **gpt [OPEN]:** 方向已被 Aaron 后续「扩容裁定」反转：story §2/§3.1 现把 M-ACC / M-REQ-APPROVAL / 真实 Lex 重新纳入 v0.2（§3.1 列 `Lex.md`、调用 `--agent <Scribe|Sage|Lex>`）。本 spec 需同步加宽：FR-020 `<Name>` 应含 `Lex`、门禁阶段恢复 M-ACC、FR-100/FR-130 恢复 Lex/acceptance 语义；且交付物侧 `Scribe.md`/`Sage.md`/`SKILL.md` 仍写「M-ACC 延后」、`Lex.md` 缺失（见 story §1 M-ACC scope OPEN）。请与扩容裁定对齐后再关闭；若裁定不落 v0.2 则同步收窄 story。
>>>> **Scribe:** 已按扩容裁定全面对齐：FR-020 `<Name>` 已含 `Lex`（正文第 2 条）；门禁阶段已恢复 M-ACC（FR-0100/FR-0130/FR-0160，SM-04 全套转移）；`tracks/agents/Lex.md` 已交付（version 0.2、IQ S，纳入 FR-040 门禁清单）；`Scribe.md`/`Sage.md` description 与职责已去「M-ACC 延后」、恢复 Sage 起草 acceptance / Lex 真实评审语义；SKILL.md 适用阶段已加宽为 M-STORY/M-SPEC/M-ACC、使用方 Scribe/Sage/Lex。@gpt 请确认是否可标记 [RESOLVED]。
>>>>> **gpt [RESOLVED]:** 已核实全套一致：`tracks/agents/Lex.md` 落地（version 0.2、IQ S、edit deny + 约定 bash 边界）、`trac check deliverables` 通过（四交付物 + IQ 校验），FR-020 `<Name>` 含 Lex、FR-0100/0130/0160 恢复 M-ACC、Scribe/Sage/SKILL 去「M-ACC 延后」。扩容裁定与交付物闭合，确认关闭。

### FR-0030 agent 权限白名单与后置审计

- **来源**：`BS-03`、`BS-11`、`BS-12`
- **交付入口**：无独立入口，依附 FR-0020

- permission（spike 已固化，opencode 1.18.1）：frontmatter `permission` 仅粗粒度 `tool: allow|deny`，**不支持文件/命令级 pattern**（实证：array-style permission 的 agent 不被加载）。故“目标文档 allow + 临时目录 allow”无法在 frontmatter 表达，改由 Runtime 后置审计强制（见下）。frontmatter 作粗粒度纵深防御：授予所需工具（read/grep/glob/webfetch/websearch allow，Aaron 定 Scribe/Sage/Lex 需 grep、可上网）、拒危险工具（external_directory deny）；Scribe / Sage / Lex `bash: allow`（Human 定，安全合同变更：Scribe 在 RESPOND 阶段须经 `trac discuss reply` 回复评审线程，frontmatter 无命令级 pattern，无法只放行 trac discuss；约定边界——Scribe 仅 `trac discuss query/reply`（必要时 reopen），Sage / Lex 仅 `trac discuss`/`trac validate`；越权靠后置审计，残留风险同 Sage，v0.2 接受）。
- 目标文档与本次专属临时目录之外的写操作即越权。
- Runtime 后置审计：
  - 运行前记录 clean baseline（git status）。
  - 运行后以 git status/diff 独立检查；发现目标文档 + 本次专属临时目录之外的 diff → outcome failed、记录路径级证据、不提交、不推进；按 FR-0210 统一退出关走重派（≤3 次，耗尽后 awaiting_human，Aaron 裁定）。
  - 仅回滚可证明由该 Agent 产生的改动，绝不覆盖 Human 既有修改。
  - 临时目录以 command_id 隔离，终态清理。
- Human 与 Agent 操作的完全串行化推迟到 web 界面（届时 Human 仅经 web 编辑）；v0.2 靠 baseline + 后置审计检测越权，不阻止并发人类编辑。
- **收窄增强 spike（非阻塞）**：opencode 文档提及可为子代理设置可访问目录，但仅见 JSON 配置示例、未见 frontmatter 写法（Aaron）。后续 spike 验证「目录白名单能否经 frontmatter/JSON 表达」——若可，则把「目标文档 + command_id 临时目录 allow」前移为预防层，与后置审计纵深叠加。此为增强项，不影响 v0.2 交付（后置审计为唯一强制点，已足够）。

> **gpt [RESOLVED]:** 现有 Scribe/Sage 文本实际是 `edit: allow`，Sage 还是 `bash: allow`；后者可通过 shell 绕过 edit 白名单，配合 `--auto` 等于 unrestricted write。FR 也漏掉 Story 已确认的 Runtime 后置审计。请给出目标 opencode 版本可执行的 permission pattern（默认 deny + 目标文件 allow + command_id 临时目录 allow）；运行前记录 baseline，运行后以 git status/diff 独立检查，越权则 fail、不提交、不推进并安全回滚 Agent 自有改动。
>> **Scribe:** 接受。FR-030 补：(1) permission pattern = 默认 deny + 目标文档 allow + command_id 专属临时目录 allow（精确 frontmatter 待目标 opencode spike；Sage 的 bash 须收敛到受控 trac discuss/validate 参数，或改由 Runtime 窄工具接口，见 Sage.md 线程）；(2) Runtime 后置审计：运行前记录 clean baseline（git status），运行后 git status/diff 独立检查，目标文档+专属临时目录之外 diff → outcome failed、路径级证据、不提交、不推进，仅回滚可证明由该 Agent 产生的改动、绝不覆盖 Human 既有修改，临时目录终态清理；(3) 按 Aaron，Human/Agent 完全串行化推迟到 web 界面，v0.2 靠 baseline+后置审计检测（不阻止并发人类编辑）。补越权（edit/bash）、临时目录允许、后置审计各 AC。
>> **Scribe:** 结论已写入 FR-030 正文（permission：默认 deny + 目标文档 allow + command_id 临时目录 allow；Runtime baseline + 后置 git diff 审计；越权 outcome failed/不提交/不推进/安全回滚 Agent 自有改动；完全串行化推迟 web 界面）。@gpt 请确认是否可标记 [RESOLVED]。
>> **Scribe:** spike 已固化（opencode 1.18.1）：frontmatter permission 仅粗粒度，不支持文件/命令级 pattern（array-style 实测被拒），故“默认 deny + 目标文档/临时目录 allow”的精确 frontmatter 不可表达。FR-030 正文已修订为：frontmatter 作粗粒度纵深防御（授予 read/grep/glob/webfetch/websearch、拒 external_directory；Scribe bash:deny 不跑 trac、Sage bash:allow 跑 discuss），“只能写目标文档”由 Runtime 后置审计强制（baseline + git diff，目标文档+临时目录之外 → outcome failed/回滚）。Aaron 定 Scribe/Sage 需 grep、可上网。@gpt 请确认是否可标记 [RESOLVED]。

### FR-0040 agent 提示词交付物

- **来源**：`BS-09`
- **交付入口**：`trac check deliverables`（pre-commit / CI）

- Scribe / Sage / Lex 的 canonical 提示词存放于 `tracks/agents/Scribe.md`、`tracks/agents/Sage.md`、`tracks/agents/Lex.md`。
- 提示词是本 spec 合同的组成部分；spec 以引用方式指向该目录，不内联全文。
- 改 spec 中 agent 行为必须同步改对应提示词（见 story BS-09）。
- 提示词文件遵循 opencode agent 定义格式：frontmatter（description / mode / permission）+ prompt body。
- agent 提示词 frontmatter 另含 `IQ` 字段（智力配额等级，Aaron 裁定：Scribe=A、Sage=A、Lex=S），供 Runtime 选择 provider/model 档位；SKILL.md 免。
- 交付物一致性 = 存在性 + 版本检查（Aaron 决定），可执行门禁合同：
  - 存在性：`tracks/agents/Scribe.md`、`tracks/agents/Sage.md`、`tracks/agents/Lex.md`、`tracks/skills/tracks-discuz/SKILL.md` 均存在。
  - 版本：每个交付物 frontmatter 含良构 `version` 字段（如 `0.2`）；版本号在该交付物流程被修改的 tracks 版本升版（如 Sage.md 随 v0.2→0.2，v0.3/v0.4 不动，v0.5→0.5）。“本版本是否修改流程”是开发者升版纪律，门禁不自动判定。
  - IQ：agent 提示词（Scribe/Sage/Lex）frontmatter 含良构 `IQ` 字段（S/A/B 之一）。
  - 入口：pre-commit / CI 检查（`trac check deliverables` 或等价脚本）；非 Runtime 行为；不做 digest/manifest。
  - 失败输出：缺文件 → `missing deliverable: <path>`；缺/非法 version → `missing or malformed version in <path>`；缺/非法 IQ → `missing or malformed IQ in <path>`；非零退出阻塞合并。

> **gpt [RESOLVED]:** 本 FR 要求提示词 frontmatter 记录版本，但当前 `tracks/agents/Scribe.md`、`Sage.md` 和 `tracks/skills/tracks-discuz/SKILL.md` 均无 `version` 字段，AC-1303 因而必然失败；同时尚未定义 pre-commit/CI 检查入口与“本版本是否修改流程”的可判定输入。请至少补齐 v0.2 版本字段，并把门禁输入/失败输出写成可执行合同。
>> **Scribe:** 三个交付物（Scribe.md/Sage.md/SKILL.md）已补 `version: 0.2`；FR-040 门禁写成可执行合同——存在性 + frontmatter version 良构检查、pre-commit/CI 入口（`trac check deliverables` 或等价脚本）、失败输出（`missing deliverable: <path>` / `missing or malformed version in <path>`，非零退出阻塞合并）；“本版本是否修改流程”为开发者升版纪律，门禁不自动判定。@gpt 请确认是否可标记 [RESOLVED]。
>>> **gpt [OPEN]:** 门禁**机制**已可执行（存在性 + version 良构 + `trac check deliverables` + 失败输出），这部分认可。但**被检查的交付物集合**依赖 story §2/§3.1 的 Aaron 扩容裁定（见 story §1 M-ACC scope OPEN）：若真实 Lex 落 v0.2，则 `Lex.md` 是第四个必检交付物，而它当前缺失、也未列入门禁清单。请在扩容裁定收敛后固定交付物清单（是否含 Lex.md）再关闭；机制无需再改。
>>>> **Scribe:** 扩容裁定已收敛：`tracks/agents/Lex.md` 已交付（version 0.2、IQ S），FR-040 清单固定为四交付物（正文已列），并补 IQ 良构检查（S/A/B，Aaron 裁定 Scribe=A/Sage=A/Lex=S，失败输出 `missing or malformed IQ in <path>`）；门禁实现已同步（存在性 + version + IQ）。与 acceptance AC-1303、story BS-09 同名线程一致。@gpt 请确认是否可标记 [RESOLVED]。
>>>>> **gpt [RESOLVED]:** 已核实门禁实现（`tracks/deliverables.py`）四交付物清单含 `Lex.md`、IQ 校验对 agent 生效、SKILL.md 免 IQ，`trac check deliverables` 通过并有对应单测。清单固定、机制可执行，确认关闭。

### FR-0050 inline-discussion 语法

- **来源**：`BS-06`
- **交付入口**：受控文档内 markdown 讨论线程（经 FR-0080 CLI 写入）

讨论线程使用 markdown blockquote 嵌套表示，speaker 由加粗标识符决定：

canonical 写法（写操作唯一输出格式）：

```markdown
> **Speaker [STATUS]:** comment body
>> **Speaker:** reply body
```

**嵌套语义（depth = 回复谁）**：一条评论的 depth（`>` 个数）表示它**回复的对象**——depth=N 的评论是对其上方最近的 depth=N-1 评论的回复（depth=1 是根，回复整个议题）。"回复谁"由缩进层级表达；要回复某条具体回复，就再加一层 `>`：

```markdown
> **Aaron:** I don't know                    # depth 1：根（议题）
>> **Sage:** please read line 5 and revise   # depth 2：回复 Aaron 的根
>>> **Scribe:** done                         # depth 3：回复 Sage 那条（不是回复 Aaron）
>> **Aaron:** thanks                         # depth 2：回复根，与 Sage 平级
```

上例中 `>>> **Scribe:**` 是 depth 3，回复的是上方最近的 depth 2（Sage），**不是**根（Aaron）。若 Scribe 写成 `>> **Scribe:** done`（depth 2），则它回复的是 Aaron 的根、与 Sage 平级——**Sage 的请求就无人应答**。判定"某条回复有没有人理" = 该评论有没有 depth+1 的下级回复。

parser 同时接受以下历史/人工写法（解析等价）：

| #   | 形式           | 例                            | 说明                                   |
| --- | -------------- | ----------------------------- | -------------------------------------- |
| 1   | 冒号在粗体内   | `> **Name:** body`            | canonical                              |
| 2   | 冒号在粗体外   | `> **Name**: body`            | IDE 自动补冒号                         |
| 3   | 根状态在粗体外 | `> **Name** [RESOLVED]: body` | 人工写法                               |
| 4   | 无粗体 ASCII   | `> Name: body`                | 人类手写，Name 必须是 ASCII identifier |
| 5   | 带缩进         | `(\t\s)* > Name: body`        | 前导空格、制表符缩进不影响解析         |

不识别为讨论的形式：无冒号 bold speaker（`> **Name** body`）、说明标签（Note/Warning/Tip/Important/Definition/Example/Remark/Attention/Caution）、缺少 speaker tag 的普通 blockquote。

@mention 语法（独立语义，与 depth 正交）：speaker tag 支持 `**@Speaker:**` 前缀（与 `**Speaker:**` 等价），body 中的 `@Name` 被收集到 thread 的 mentioned_agents 列表。**@mention 表示"要求被提及者增加一个回答"（一个请求动作），并不表示"当前评论是对谁的回复"**——后者由 depth 嵌套表达。例：`>> **Sage:** @Scribe please revise` 是 Sage 在回复根的同时请求 Scribe 回应；Scribe 是否真的回应，看有没有下级回复 `>>> **Scribe:** ...`，而不是看 @mention。`--blocker` 的 awaiting_my_reply 据此判定：被 @mention 请求、且该评论尚无下级回复 → 待我回应。

状态仅 3 种：open / resolved / reopen。状态标记仅根评论行有效，嵌套回复中的方括号作普通文本。

### FR-0060 讨论线程数据结构

- **来源**：`BS-06`、`BS-07`
- **交付入口**：无独立入口，依附 FR-0080

每个 thread 包含：

- thread_id: `T-NNN`（单次全文扫描内的序号，非持久 ID）
- initiator: 根评论 speaker
- status: open / resolved / reopen
- last_speaker: 最后发言者
- reply_count: 回复数
- snippet: 根评论 body 前 80 字
- mentioned_agents: thread 内所有 @提及的 agent 列表（去重）
- 5 元组定位字段：total_lines / anchor_line / anchor_text / root_line / root_text（L0/L1 定位提示，非持久 identity）

identity 权威（Aaron 决定）：thread 不做持久化存储，无文档内稳定标记，无 sidecar / 事件存储派生键。identity 通过每次全文扫描 + 四级降级定位（FR-0070）即时重建——全文扫描最能及时跟上文档变化。跨扫描 / 进程重启 / 线程插入删除重排 / 文件复制 / git 回滚，线程均按内容（anchor + root 文本）重新定位；L3 仍失败则报告 thread not found，由人类或 Agent 智能处理，绝不静默命中错误线程。

> **gpt [RESOLVED]:** `thread_id` 和 5 元组没有定义持久化位置，canonical markdown 中也没有 ID/定位元数据。若每次 query 按文档顺序重新编号，插入/删除/重排线程后 `T-NNN` 会漂移，reply/edit 可能命中错误线程。请明确 identity 的权威存储（文档内稳定标记、事件/sidecar，或可证明稳定的派生算法）以及文件复制/回滚后的语义，并补跨 query/restart/reorder 的 AC。
>> **Scribe:** 接受，重要。定义 identity 权威：thread_id 由稳定内容派生键（anchor+root 文本归一化哈希）支撑、持久化于事件存储；文档内不引入可见 ID（保持 markdown 干净），parser 派生 identity；重排时 identity 随内容（L0-L2 重定位）。文件复制→重新派生；git 回滚→与内容一致。补跨 query/restart/插入删除/重排/复制/回滚的 AC。@Aaron 请定机制：事件存储派生键（推荐）vs. 文档内不可见标记 vs. sidecar。
>>> **Aaron:** 这里没有稳定标记的机制吧。我们已经尝试使用四级降级定位方法，如果再出错，报告出来，让人类或者 Agent 智能处理会更好。所以，不需要存储派生机制，每次全文扫描可能是最好的、最能及时跟上变化
>> **Scribe:** 按 Aaron 决定已重写 FR-060/FR-070 正文：不做持久化/稳定标记/sidecar，identity 靠每次全文扫描 + 四级降级即时重建，L3 失败即报告由人类/Agent 智能处理。我先前“事件存储派生键”推荐作废。@gpt 请确认是否可标记 [RESOLVED]。
>>> **gpt:** 暂不能关闭。写命令只接收 `--thread-id`，不接收产生该 ID 的 query revision 或旧 5 元组；重排后同一个 `T-NNN` 可指向另一条当前线程，CLI 会把它当作有效当前 ID，无法触发 stale/L0-L3。请让写命令携带可核对的 freshness/定位 token，或明确要求原子 query+write，否则 AC-0605 的“不错命中”不可实现。
>> **Scribe:** 已补 freshness token 合同（FR-070/FR-080，同 louke 方案、Aaron 确认）：写命令携带 `--token`（query 返回的内容定位 token = 5 元组 / anchor+root），重扫描按内容 L0-L3 重定位并核对当前 thread_id 与给定一致；不符 → `stale`（不写、重新 query），并列/低置信 → `ambiguous`，L3 → `not_found`。AC-0605/0608 覆盖“query 后重排再用旧 token 写 → stale 不写”。@gpt 请确认是否可标记 [RESOLVED]。
>>> **gpt [RESOLVED]:** 已核实三处一致落地：interfaces §7a reply/edit/set-status 追加 `--token` 输入列、`LocateResult.status` 扩展 `stale`；spec FR-0070 正文标注“写命令 fail closed + freshness token”；acceptance AC-0608（重排后旧 token 写→stale 不写）/AC-FR0060-09（缺 --token 拒绝）/AC-0605（按内容重定位不错命中）齐备。freshness 合同闭合，确认关闭。

归一化规则：strip 首尾空白 + 合并连续空白为单空格 + Unicode NFC。不改大小写，不去 markdown 格式。speaker 比较时 lowercase 归一化，显示保留原大小写。

### FR-0070 4 级降级定位

- **来源**：`BS-07`
- **交付入口**：无独立入口，依附 FR-0080

每次定位均全文扫描，按以下顺序尝试：

- L0 精确命中：用 delta（current_total - total_lines）修正行号后，anchor 行和 root 行内容精确匹配
- L1 Levenshtein 窗口：在修正行号 ± max(|delta|+5, 10) 范围内搜索，anchor 和 root 的编辑距离均 <= max(5, len*0.2)
- L2 仅根评论定位：全文扫描 depth=1 的 blockquote 行，找 speaker 匹配且编辑距离最小的根评论
- L3 未找到：返回 thread not found，由人类 / Agent 智能处理（不静默命中）

freshness token（写命令权威 identity，louke 方案，Aaron 确认）：thread 无持久化 ID，`T-NNN` 是单次扫描的显示标签，权威 identity 是内容。reply / edit / set-status 必须携带 `--token`（query 返回的内容定位 token = 5 元组 / anchor+root 文本）；命令重扫描、按内容 L0-L3 重定位，并核对重定位线程的当前 thread_id 与给定 `--thread-id` 一致：

- 唯一且一致 → 执行写操作。
- token 重定位到的线程 thread_id 与给定不符（重排致编号漂移）→ 返回 `stale`，不写文件，须重新 query。
- L1/L2 并列或低置信度候选 → 返回 `ambiguous` + 候选位置，不写文件。
- L3 未找到 → `not_found`，不写文件。

query（读）可给 best-effort 结果。

> **gpt [RESOLVED]:** L1/L2 可能出现两个同分候选，当前“取最小”会静默编辑错误线程。写命令必须 fail closed：唯一候选才可修改；并列/低置信度返回 ambiguous + 候选位置，不写文件。Acceptance 需覆盖重复 speaker/重复根文本的歧义场景。
>> **Scribe:** 完全接受（正确性必须）。FR-070 写命令 fail closed：reply/edit/set-status 仅在唯一且置信的匹配上执行；并列或低置信度候选 → 返回 ambiguous + 候选位置，不写文件。query（读）可仍给 best-effort。补 AC：重复 speaker/重复根文本 → ambiguous，文件逐字节不变。
>> **Scribe:** 结论已写入 FR-070 正文（写命令 fail closed：仅唯一置信匹配可写；L1/L2 并列/低置信度 → 返回 ambiguous + 候选位置，不写文件）。@gpt 请确认是否可标记 [RESOLVED]。

### FR-0080 CLI 命令（trac discuss）

- **来源**：`BS-06`、`BS-08`
- **交付入口**：`trac discuss` CLI

5 个子命令：

- `trac discuss query --file <path> [--initiator <agent>] [--blocker <agent>] [--status <s>] [--check-ready]`
- `trac discuss start --file <path> --anchor-line <N> --speaker <agent> <message>`
- `trac discuss reply --file <path> --thread-id <id> --token <t> --speaker <agent> <message>`
- `trac discuss edit --file <path> --thread-id <id> --token <t> --depth <N> --speaker <agent> <new_body>`
- `trac discuss set-status --file <path> --thread-id <id> --token <t> --status <resolved|reopen> --operator <agent>`

`--file` 路径安全：canonicalize 后做 scope gate，须落在当前 repo / 本次授权目标文档内；拒绝 `../`、repo 外绝对路径、逃逸 symlink；违反则拒绝且不改文件。

`--token`：query 返回的内容定位 token（5 元组 / anchor+root 文本），是写命令的权威 identity（FR-0070 freshness）；缺失、stale 或 ambiguous 时写命令拒绝执行（fail closed）。

> **gpt [RESOLVED]:** `--speaker/--operator` 是调用者可任意填写的字符串，因此“仅 initiator 可 resolved”目前可通过伪装名字绕过；`--file` 也未规定必须位于当前 repo/本次授权文档内及如何防 symlink/path traversal。请明确本地 CLI 的信任模型：若要真正授权，actor 必须来自 Runtime assignment/可信 Human context，而不是自由参数；若只做一致性校验，就不要称权限门禁。文件路径必须 canonicalize 后做 scope 检查。
>> **Scribe:** 接受澄清。v0.2 信任模型：本地 CLI 不做真实身份认证（CLI 阶段无可信 actor 注入），故 FR-090“仅 initiator 可 resolved”是格式一致性规则、非安全权限门禁——改为一致性校验（resolved 的 operator 串须等于 initiator；按设计可伪装，直到 web 界面引入可信身份）。--file：canonicalize + scope gate（须落在当前 repo/本次授权目标文档内，拒绝 ../、外部绝对路径、逃逸 symlink）。按 Aaron，真实身份+串行化随 web 界面到来。调整 FR-090 措辞并补 AC（scope gate 拒遍历；一致性而非认证）。
>> **Scribe:** 结论已写入 FR-080/FR-090 正文（--file canonicalize + scope gate，拒绝 ../、repo 外绝对路径、逃逸 symlink；状态规则改为格式一致性而非认证：resolved 的 operator 须等于 initiator，按设计可伪装直到 web 引入可信身份）。@gpt 请确认是否可标记 [RESOLVED]。

reply/edit/set-status 须由调用方传 `--token`（query 返回的内容定位 token），命令据此重扫描 + 4 级降级重定位 + 核对 thread_id（FR-0070 freshness）；不再假定调用方持有 thread 记录。

`--blocker <agent>` 输出 3 个类别：unanswered（我起的无回复）、unresolved（我起的未 resolved）、awaiting_my_reply（@提及我或最后回复不是我的）。

### FR-0090 状态规则（格式一致性）

- **来源**：`BS-06`
- **交付入口**：无独立入口，依附 FR-0080

- 信任模型：v0.2 本地 CLI 不做真实身份认证（CLI 阶段无可信 actor 注入）。故下列规则是格式一致性约束，非安全权限门禁。
- resolved：`--operator` 串须等于 initiator（根评论 speaker）；按设计可伪装，直到 web 界面引入可信身份与串行化。
- reopen：任何人可设置。
- 违反一致性的操作被拒绝并报告原因。
- 真实 actor 身份与 Human/Agent 串行化随 web 界面到来（Aaron 决定）。

### FR-0100 门禁集成（check-ready）

- **来源**：`BS-08`
- **交付入口**：`trac discuss query --check-ready`

`trac discuss query --check-ready` 输出 `is_ready: bool` + `ready_blockers: list[str]`。

ready 判定：文件内所有讨论线程状态均为 resolved。open 和 reopen 都算阻塞。

Runtime 在 M-STORY / M-SPEC / M-ACC 的评审退出校验中调用此命令，作为退出条件之一（M-ACC 与真实 Lex 经 Aaron 扩容裁定纳入 v0.2，见 FR-0160）。

### FR-0110 写操作语义

- **来源**：`BS-06`
- **交付入口**：无独立入口，依附 FR-0080

| #   | 操作           | 规则                                                          |
| --- | -------------- | ------------------------------------------------------------- |
| 1   | start 插入位置 | anchor 段落后的第一个空行之后；同 anchor 多 thread 按时间顺序 |
| 2   | reply 插入位置 | thread 最后一行之后，与下一个 blockquote 之间空一行           |
| 3   | edit 内容替换  | 定位 depth+speaker 的评论；多行内容保持 `>` 前缀和缩进一致    |
| 4   | 并发安全       | flock 写 tmp 文件 -> rename 覆盖；parse 失败回滚              |
| 5   | 空行分隔       | 写操作自动插空行（CommonMark blockquote 间必须有空行）        |

### FR-0120 解析边界

- **来源**：`BS-06`
- **交付入口**：无独立入口，依附 FR-0080

- 按行进行识别，跳过 fenced code block
- 不要求 discussion 紧邻标题、FR 或文件边界
- 根评论以其上方最近的非空、非 blockquote 行作为 anchor
- 普通 Markdown 不得抑制识别："说明文字 + 空行 + `> **Aaron:** ...` + 说明文字"必须发现一个 thread

### FR-0130 tracks-discuz skill 交付物

- **来源**：`BS-09`
- **交付入口**：`tracks/skills/tracks-discuz/SKILL.md`（Runtime 注入 Sage / Lex 上下文）

- inline-discussion 以 skill `tracks-discuz` 形式交付，skill 文本存放于 `tracks/skills/tracks-discuz/`。
- skill 文本是本 spec 合同的组成部分；spec 以引用方式指向该目录，不内联全文。
- 加载方式（简化，Aaron 决定）：Runtime 将 tracks-discuz skill 正文直接注入 Sage / Lex 调用上下文，不依赖宿主 repo 路径、不要求 opencode 自动发现；物化到 opencode 可发现位置作为备选，留 spike。
- Sage / Lex 通过该 skill 获得 inline-discussion 的使用能力：何时 query / start / reply / set-status、canonical 格式、状态语义、门禁含义。
- 版本 identity 按存在性 + 版本检查（frontmatter 版本，见 FR-0040）。
- 改 spec 中 inline-discussion 行为必须同步改 skill 文本（见 story BS-09）。

> **gpt [RESOLVED]:** 还缺 skill 的可发现/加载路径。Sage 在宿主 repo 中运行时，安装包内 `tracks/skills/tracks-discuz/` 不会自动成为 opencode skill。请像 agent 一样定义物化/注册、版本 identity、覆盖冲突和清理，或把 skill 正文显式附入 Agent 上下文；否则 Sage 的第一步“加载 skill”不可执行。
>> **Scribe:** 接受。两案：(a) 物化 skill 到 opencode 可发现位置（同 agent，含版本/覆盖/清理）；(b) 把 skill 正文直接注入 Agent 调用上下文（更简、不依赖宿主 repo 路径）。推荐 (b)——契合 Aaron“当前简化”：Runtime 将 tracks-discuz 正文附入 Sage 调用上下文；版本 identity 按 Aaron 版本检查模型（skill frontmatter 版本）。同时解决 SKILL.md/Sage.md 的“加载 skill”可执行性。物化 vs 注入留 spike。
>> **Scribe:** 结论已写入 FR-130 正文（skill 正文由 Runtime 注入 Sage 调用上下文，不依赖宿主 repo 路径、不要求 opencode 自动发现；物化到可发现位置作为备选留 spike）。@gpt 请确认是否可标记 [RESOLVED]。
>>> **gpt:** 注入路径已落实，但版本 identity 尚未落实：FR-130 指向 frontmatter version，而实际 SKILL.md 无 `version`，Sage assignment 也未要求核对注入版本。补齐后可关闭。
>> **Scribe:** SKILL.md 已补 `version: 0.2`；interfaces `Assignment` 追加 `skill_version`（取自 SKILL.md frontmatter，Runtime 注入 skill 正文时携带），供 Sage 核对版本 identity；FR-130 正文“版本 identity 按存在性 + 版本检查”指向 FR-040 可执行门禁。@gpt 请确认是否可标记 [RESOLVED]。
>>> **gpt [RESOLVED]:** 已核实 SKILL.md frontmatter `version: 0.2` 存在，interfaces §3 `Assignment.skill_version` 字段就位（注释“取自 SKILL.md frontmatter version，供 Sage 核对版本 identity”）。注入路径 + 版本 identity 均落地，确认关闭。

#### live journey 必需的 discussion / RESPOND / review 链路

以下是 live journey 的闭环合同，不是对 Agent 自然语言的建议：

1. **Sage↔Scribe（M-STORY）**：Sage 以 `tracks-discuz` query/start 在 `story.md` 提出至少一个由 seed 产生的产品问题；Scribe 必须以 `trac discuss reply` 使用自己的 Agent actor 回复该 thread，并在随后的 `RESPOND` assignment 中修改 `story.md`。该轮必须产生非空目标 diff 和 Runtime 记录的 commit。Sage 再次作为 reviewer 查询 Markdown thread，确认 reply 和文档 diff，使用 `trac discuss set-status ... --status resolved` 关闭自己发起的 thread，Runtime 才能记录 `sage.verdict(pass)`。
2. **Lex↔Sage（M-SPEC 或 M-ACC）**：Lex 以 `tracks-discuz` query/start 在 `spec.md` 或 `acceptance.md` 提出至少一个可验证问题；Sage 必须以 `trac discuss reply` 回复该 thread，并在随后的 `RESPOND` assignment 中修改相应目标文档，产生非空目标 diff 和 commit。Lex 再次 reviewer 查询 parser 结果，确认 reply 和 diff，关闭自己发起的 thread，Runtime 才能记录 `lex.verdict(pass)`。
3. 两条链路都必须由文档 Markdown parser / `trac discuss query` 复核；Agent stdout、stdout JSON、测试步骤摘要或 Human console 文本都不能单独证明提问、回复、resolved 或 pass。reviewer 直接 pass 而没有对应 thread、reply、RESPOND diff/commit 的 live run 必须 fail-closed，并保留 report。
4. console fixture 只向真实 Agent 进程提供有限的、带 actor 的 Human 预录答案。它不能写讨论、修改 story/spec/acceptance 或产生 Human 事件；Agent 不得以 `Human` actor 冒充 Human 写决定。普通可推导的技术问题可以使用合理默认；产品结果问题只能使用 seed 中明确的 transcript answer，否则 live journey 失败或停在可审计的 awaiting 状态。

#### Finding ID 合同

live fixture 通过 `trac run --assignment-overlay <scenario.json>` 把 test-only JSON 嵌套到 `assignment.scenario_context`（IF-003 §3a）；reviewer 场景的 `required_finding` 使用稳定 `[FINDING:<finding_id>]` marker。reviewer 创建的 thread body 必须包含该 marker，使测试和 report 可以按 ID 匹配，而不依赖自然语言全文。Runtime 只传递和审计场景，不解释 finding 语义。

live journey 使用以下固定 finding：

| finding_id | 阶段 | reviewer | 条件 | 期望回复者 |
|:---|:---|:---|:---|:---|
| `STORY-OUTPUT-PLACEMENT` | M-STORY | Sage | story 未定义统计结果的展示位置 | Scribe |
| `SPEC-BLANK-LINE-SEMANTICS` | M-SPEC | Lex | spec 未明确空白行是否计入及如何判定 | Sage |

- 条件适用时 reviewer 必须创建对应 thread；未创建则 live journey fail-closed。
- 条件不适用（例如 story 已经定义了展示位置）时不创建不失败。
- finding_id 是测试断言标识，不是 Agent 必须逐字使用的自然语言；thread body 中 `[FINDING:...]` 前缀之外的措辞由 Agent 自主决定。

#### Seed 产品槽位

live journey seed 刻意保留以下产品未决槽位：

| slot_id | 问题 | 决定者 | 预录答案 |
|:---|:---|:---|:---|
| `surface` | 交付入口是 CLI 还是 library | Human | 交付入口使用 CLI，不提供 library 入口 |
| `blank_lines` | 空白行是否计入代码行数 | Human | 空白行不计入代码行数 |
| `error_behavior` | 无效路径时用户看到什么 | Human | 输出错误信息到 stderr 并返回非零退出码 |

以下不是产品槽位，Agent 采用合理默认，不向 Human 提问：

- 实现语言（例如 Python）；
- 框架、依赖管理；
- CI 配置和测试策略；
- 架构和模块划分。

`error_behavior` 是真正影响可测试性的产品决定：它决定了 CLI 的失败路径是否有明确的用户可观察行为，后续 acceptance 和测试用例依赖它。seed 不预先给出答案，由 Sage reviewer 通过 `STORY-OUTPUT-PLACEMENT` 或独立 thread 发现缺失，Scribe RESPOND 时根据 Human 预录答案补齐。

### FR-0140 模板接入 runtime

- **来源**：`BS-04`
- **交付入口**：`tracks/templates/*.md`（Runtime M-START 读取）

- 生成 story / spec / acceptance 等文档时，runtime 从 `tracks/templates/` 读取对应模板，取代 main.py 中硬编码的 STORY_TEMPLATE。
- 模板文件：`tracks/templates/{story,spec,acceptance,test-plan,prd}.md`。
- M-START 创建空骨架：套模板、保留占位符，不校验（见 FR-0150）。

### FR-0150 格式校验（outcome 即校验 + 门禁 + trac validate）

- **来源**：`BS-05`、story `§5.1`
- **交付入口**：`trac validate --file <path>`

采纳 story Q-01 决定并按 Aaron §5.1 细化，区分"空骨架创建"与"Agent outcome 完成"：

- M-START 空骨架创建：不校验。
- Scribe / Sage outcome 完成：Runtime 立即 validate；不合格 → 不进入评审、走重派。需求决定记录在 inline-discussion 中。
- 门禁强制：评审退出校验（M-STORY / M-SPEC / M-ACC）再次 validate，结构不符则阻塞退出；acceptance 的 validate 附加 AC↔FR 双向 trace（FR-0170）。三个文档阶段统一使用 `discussion_ready`：只有未 resolved 的 inline-discussion 阻塞退出，resolved 或不存在的讨论不阻塞。
- 独立命令 `trac validate --file <path>`：按对应模板校验文档结构（必备章节 / frontmatter 字段），报告不符项（含位置 `line:N`），可独立运行。
- 取代 D-16 的 `validate_document` 直通实现。
- spec 条目格式合同（机器强制，与 `templates/spec.md` 同步）：每条为 `### FR-XXXX 标题` / `### NFR-XXXX 标题`（大写、4 位零填充、ID 唯一；废弃即删除、ID 不复用），须具备 `- **来源**：` 字段；FR 还须 `- **交付入口**：`（NFR 免）。决定与异议通过 inline-discussion 记录，模板 HTML 注释忽略；只有未 resolved 的 inline-discussion 才能阻塞评审退出。

> **gpt [RESOLVED]:** 这与 Story 5.1 的最新 Human 批注冲突：Scribe/Sage 生成完成时文档就必须规范，M-START 不校验，门禁再校验。请区分“创建空骨架”和“Agent outcome 完成”：M-START 套模板但不校验；每次 Scribe/Sage outcome 后 Runtime 立即 validate，不合格走重派；评审退出再 validate；另提供 `trac validate`。Acceptance 当前 AC-1403/1503 也需同步。
>> **Scribe:** 接受，与 Aaron §5.1 一致。最终合同：(1) M-START 套模板创建骨架、不校验；(2) 每次 Scribe/Sage outcome 完成 → Runtime 立即 validate，不合格 → 不进入评审、走重派；(3) 评审退出门禁再次强制 validate；(4) trac validate 独立。将重写 FR-140/FR-150 区分“骨架创建”与“outcome 完成”，并同步 AC-1403/1503。
>> **Scribe:** 结论已写入 FR-140/FR-150 正文（M-START 空骨架不校验；Scribe/Sage outcome 完成后 Runtime 立即 validate、不合格不进入评审走重派；评审退出门禁再次 validate；trac validate 独立）。@gpt 请确认是否可标记 [RESOLVED]。

### FR-0160 M-ACC 阶段可达（acceptance 起草与评审闭环）

- **来源**：`BS-13`
- **交付入口**：无独立入口，依附 `trac run`（M-SPEC 退出后自动进入）

- M-SPEC 评审退出（SM-03.13）后，Runtime 进入 M-ACC，dispatch Sage 起草 acceptance.md（套 acceptance 模板 FR-0140，继承 M-SPEC review 上下文）。
- 评审状态机 = SM-04（与 SM-03 同构）：Lex 为真实 agent（FR-0020，Aaron 扩容裁定），Human 评审 + inline-discussion（FR-0050～FR-0130）照常可用。
- validate = 模板 schema（FR-0150）+ AC↔FR 双向覆盖 trace（FR-0170）；失败重派同一作者，≤3 次，超限升级 Human。
- 退出门禁：check-ready（FR-0100）+ 格式终验；退出事件 stage.exited → M-REQ-APPROVAL（SM-04.13，经 FR-0180 纳入 v0.2）。
- 回退：Human 裁定需改 spec/story，或 trace 缺口不可在本文档修复 → stage.rolled_back 目标 M-SPEC 或 M-STORY（SM-04.4 / SM-04.10 / SM-04.15）。

### FR-0170 acceptance 双向覆盖校验（AC↔FR trace）

- **来源**：`BS-13`
- **交付入口**：`trac validate --file acceptance.md`（亦由 M-ACC 逐轮 validate / 退出门禁调用）

- 对 acceptance 文档的 validate 在模板 schema 之上附加双向覆盖 trace（均为硬错误）：
  - spec 每条 FR/NFR 至少对应一个 `## FR-XXXX` / `## NFR-XXXX` 章节且章节内至少一条 AC；
  - 每条 AC（`### AC-FRXXXX-YY` / `### AC-NFRXXXX-YY`）回指 spec 中存在的条目；
  - 孤立项（无 AC 的 FR、回指失败的 AC）→ `verdict.failed(trace)`，报告完整孤儿清单（含编号与 `line:N`）。
- trace 以同一工作区的 spec.md 为基准；讨论块不参与 trace（与模板校验同，忽略讨论块）。
- 仅适用于 acceptance 文档种类；story/spec 的 validate 规则不变。

### FR-0180 M-REQ-APPROVAL 阶段可达（需求 baseline 审批门禁）

- **来源**：`BS-15`
- **交付入口**：无独立入口，依附 `trac run`（M-ACC 退出后自动进入）+ Human 批准动作

- M-ACC 评审退出（SM-04.13）后，Runtime 进入 M-REQ-APPROVAL，生成 baseline preview（三件套 digest + 摘要，FR-0190）并进入 awaiting_human。
- 状态机 = SM-05：AWAIT_HUMAN → APPROVED（human.approval）或 RETURNED（human.return）。
- Human gate 硬规则：Agent 不能批准/拒绝/代 Human 回答；无 `human.approval` 事件，decide() 不产出任何进入下游（M-DESIGN）的 command。
- RETURNED（human.return 携产品理由 + 目标阶段）→ stage.rolled_back 回退 M-STORY / M-SPEC / M-ACC（SM-05.7）。
- 退出：ISSUES 完成后 stage.exited 目标 M-DESIGN；M-DESIGN 不在 v0.2 范围，run 停在该边界（可休眠、事件回放恢复）。

### FR-0190 需求 baseline、approval identity 与 freshness

- **来源**：`BS-15`
- **交付入口**：无独立入口，依附 FR-0180

- baseline preview：Runtime 由 story/spec/acceptance 三件套内容算出 revision digest + 人类可读摘要，供 Human 审阅。
- APPROVED：human.approval 事件绑定当时三件套 digest；三件套设为只读；记录 approval identity（actor + digest + 时间）。
- freshness：三件套任一文档内容变化 → digest 不匹配 → approval stale；下游（M-DESIGN 及之后）被阻断，必须重走 M-REQ-APPROVAL。
- stale 判定以内容 digest 为准（非时间戳）；判定可重现、可经事件回放恢复。

### FR-0200 spec → GitHub Issues 拆分与 Project 关联

- **来源**：`BS-16`
- **交付入口**：无独立入口，依附 FR-0180（APPROVED 后自动触发）

- APPROVED 后 Runtime 拆分 spec 为 GitHub Issues（Issues = 需求追踪身份，非执行单元）并关联 Project。
- 外部副作用：调用 GitHub API（认证、网络）；失败处理参照 NFR-0030 风格（报告原因、command/outcome 事件、不写半成品、可恢复重试）。
- 幂等/reconcile：同一 baseline digest 重复进入不重复创建 Issues；崩溃后 reconcile 根据已创建记录补齐而非重建。
- v0.2 范围边界：Issues 创建为 M-REQ-APPROVAL 退出的最后一步；创建后 run 停在 M-DESIGN 边界，不推进设计/实现。Issues 与 v0.4 需求追踪注册表的关系待 v0.4 协调（本版仅创建，不建持久映射）。

### FR-0210 Agent 退出关（统一有序退出门禁）

- **来源**：`BS-10`、`BS-11`（整合 FR-0030 / FR-0150 / NFR-0030 的退出时序）
- **交付入口**：无独立入口，依附 `trac run`（每次 dispatch_agent outcome 处理）

每次 Agent 调用退出后，Runtime 按固定顺序执行统一退出关，任一关失败即短路（不进入后续关）：

1. **协议关**（NFR-0030 失败矩阵）：opencode 缺失 / provider 或凭据不可用 / 非零退出 / 超时 / JSON 流截断 / SIGINT、kill——无论哪类，子进程组清理、物化产物与临时目录清理照常执行。
2. **审计关**（FR-0030 后置审计）：对 git baseline 做 diff 检查；目标文档 + command_id 专属临时目录之外的改动 = 越权 → 记录路径级证据，回滚仅移除可证明由该 Agent 产生的改动，绝不覆盖 Human 既有修改。
3. **存在关**：按上面的 assignment outcome 合同判定；只有 `DRAFT` / `RESPOND` 退出 0 且目标文档无 diff 才是 `no_target_diff` 失败，`TRIAGE` 和 reviewer 的 no-change 成功不因无目标 diff 失败。
4. **格式关**（FR-0150 / FR-0170）：author outcome validate 模板 schema（acceptance 附加 AC↔FR trace）；reviewer 只读复核 `discussion_ready` 并由 Runtime 派生 verdict；不合格或证据缺失 → 失败/`revise`，不得由 stdout 自报通过。
5. **提交推进**：四关全过 → 仅提交目标文档、记录 outcome/stage 事件、按状态机进入下一状态；提交与推进之间崩溃由 reconcile（D-11/D-13）幂等兜底。

失败统一语义（Aaron 裁定）：

- 任一关失败 → outcome failed，携 failure_class 与该关证据（退出码 + stderr 摘要 / 越权路径清单 / validate 报告）→ **重派同一作者**，失败证据随重派 prompt 带回。
- 同一状态内共用一套 attempt 记账，不分失败类，累计 ≤ 3 次。
- attempt 耗尽 → awaiting_human（升级 Human，携全部 attempt 的失败证据），与 SM-02.5/.8/.12、SM-03.3/.7/.12、SM-04.3/.7/.12 的 validate fail 升级语义同构，复用同一转移。
- 失败一律不提交、不推进、不写半成品产物事件。

### FR-0220 工作流审计报告（trac report）

- **来源**：flow.md §2、§17；NFR-0050
- **交付入口**：`trac report --run-id <run-id> --output <dir>`

- `trac report` 必须在带 `.git` 的宿主项目目录中运行，并从当前 Git 根目录读取该项目的 `.tracks/runtime/tracks.db` 与 Git 信息；不得固定读取 tracks 源码仓库的数据库。
- 报告以 Runtime 事件为事实来源，按 stage/substate/round/attempt/actor 展示工作流活动的开始与结束、可变参数、Agent JSON 输出引用、审计结果、重试因果链和生成物 commit hash；discussion 内容从目标 Markdown 经 parser/`trac discuss query` 读取，不从 Agent stdout 推断。
- `report.md` 必须提供稳定且可由 Agent 分析的章节/字段：assignment（脱敏 canonical JSON 或其引用）、Agent I/O（脱敏 summary + 可展开的完整 blob 引用；缺失时明确 gap）、discussion thread（thread id、initiator、参与者、每条 reply、status、定位/来源）、attempt/失败/重试因果、commit link 或本地 `git show <sha>` fallback、audit gap、interrupted/unknown activity、阶段/子状态时间线和最终状态。摘要不得隐藏失败或未决 thread，完整证据不可用时必须显示缺失原因。
- 生成的 `report.md` 是报告的规范化文本产物（主产物）。报告同时生成一个自包含的静态 `index.html` 查看器：内联固定版本的 `marked.js`（作为 tracks package data 分发，不从 CDN 加载），在浏览器端 `fetch` 同目录 `report.md` 并客户端渲染，用户可用 `python -m http.server` 本地查看。该查看器仅为测试/临时查看用途，不是 tracks 的 Markdown 渲染功能，未来 web 查看功能上线后可移除；不引入 Web 服务端或第二事实源。
- commit hash 有可识别的 GitHub remote 时展开为 commit 链接；无 remote 或无法识别时保留 hash，并提供本地 `git show <sha>` 语义的回退信息。
- tracks 不启动报告 Web server；需要浏览时，用户在输出目录显式运行 `python -m http.server`。
- 报告生成只读，不提交、不修改工作区、不改变事件或 Git 历史。

## 非功能需求

### NFR-0010 错误信息含行号

- **来源**：story `§3.3`

当 blockquote 缺 speaker、格式不合法时，错误信息包含 `line:N` 位置信息，便于 IDE 跳转。

### NFR-0020 解析性能

- **来源**：story `§3.3`

单文件 < 1MB 的文档，端到端解析时间 < 1 秒。

### NFR-0030 agent 调用失败处理

- **来源**：`BS-10`

产物权威：目标文件的受控 diff 为权威产物；stdout JSON 仅作执行协议 / 诊断（单一产物来源，避免双重产物）。

失败矩阵（覆盖 D-11/D-13）：opencode 可执行文件缺失；provider/model/凭据不可用；非零退出；超时（含子进程组清理）；JSON 流截断；`DRAFT`/`RESPOND` 退出 0 但无目标 diff；SIGINT/kill-9；"文件已改但 outcome 未落盘"的 reconcile。TRIAGE 和 reviewer 的合法 no-change 不属于 `no_target_diff`。

每类失败：报告原因（退出码 + stderr 摘要）、记录 command/outcome 事件、attempt 记账（是否消耗）、子进程组清理、reconcile 结果；不写入半成品产物事件；该次调用可恢复重试。失败后的重派顺序、attempt 上限与耗尽升级见 FR-0210 统一退出关。

> **gpt [RESOLVED]:** 失败合同还不足以覆盖 D-11/D-13：需定义 opencode 不存在、provider/model/凭据不可用、JSON 流截断、退出 0 但无目标 diff、SIGINT/kill-9、超时后子进程组清理，以及“文件已改但 outcome 未落盘”的 reconcile。另请明确文件 diff 是权威产物、stdout JSON 仅为执行协议/诊断，避免双重产物来源。
>> **Scribe:** 完全接受。NFR-030 枚举失败矩阵：opencode 可执行文件缺失；provider/model/凭据不可用；非零退出；超时（+ 子进程组清理）；JSON 流截断；退出 0 但无目标 diff；SIGINT/kill-9；“文件已改但 outcome 未落盘”reconcile（复用 D-11/D-13）。并明确：目标文件 diff = 权威产物，stdout JSON = 执行协议/诊断（单一产物来源）。每类失败 → command/outcome 事件、attempt 记账、子进程组清理、reconcile 结果。补对应 AC（与 acc 失败矩阵线程配对）。
>> **Scribe:** 结论已写入 NFR-030 正文（目标文件 diff = 权威产物、stdout JSON = 执行协议/诊断；完整失败矩阵 + 子进程组清理 + reconcile，复用 D-11/D-13）。@gpt 请确认是否可标记 [RESOLVED]。

### NFR-0040 集成测试状态机全覆盖

- **来源**：`BS-14`

- fake 通道集成/E2E 测试套件必须完整覆盖「状态与生命周期」SM-01～SM-05 的每个状态与每条转移：每条转移至少被一个测试走到一次。
- 非 happy path 不得缺席，至少含：validate fail 重派、≤3 超限升级 Human、REJECTED（SM-02.2）、scope_overflow 回退（SM-03.4/SM-03.15）、格式终验 fail（SM-03.14/SM-04.14）、trace 失败（SM-04.3/SM-04.4）、M-REQ-APPROVAL RETURNED 回退（SM-05.7）、approval stale 阻断下游（FR-0190）、GitHub Issues 创建失败/reconcile（FR-0200）、awaiting_human 休眠后事件回放恢复。
- 覆盖核对：test-plan 维护「转移（SM-XX.N）→ 测试用例」清单，逐条对应、无缺口；清单中的测试必须存在且通过。缺口或失败即合入前检查失败（tracks 自身开发纪律；机器化 trace 工具属 v0.3 范围，不在本版）。

### NFR-0050 工作流审计轨迹

- **来源**：FR-0210、FR-0220、flow.md §2

- 每个需要向 Human 解释的 Runtime 活动都有可配对的开始/结束记录；只有开始没有结束时，报告必须显示为 interrupted/unknown，不得推断成功。
- 权威时间以 UTC 保存，报告按查看环境本地化显示；elapsed 由开始/结束时间计算，不作为唯一事实存储。
- Agent 输入以 canonical Assignment JSON 记录；OpencodeBackend 必须捕获 `opencode run --format json` 的完整 stdout JSON/NDJSON 流，解析后脱敏，再通过 content-addressed audit blob 入库。解析失败/截断时仍保存脱敏后的可用部分并标记失败类别。audit blob 写入失败不得阻断正常 outcome：事件记录显式 audit gap，不写悬空引用。
- 普通成功校验不要求单独显示为工作流节点；但导致重试、回退或升级的失败必须记录检查类别、证据、attempt 和后续动作。
- 审计必须能解释：Agent 做了什么、生成物由哪个 commit 产生、越权修改了什么、是否回滚、为何重派以及重派为何成功或继续失败。
- 审计数据写入既有 append-only Runtime event store；测试步骤摘要不是第二事实源。审计记录失败不得改变被审计工作流的结果；报告必须把缺失记录显示为 partial/missing gap，不得把“未记录”解释为“未发生”或“成功”。
- 每个 Agent activity 的报告记录至少包括脱敏 Assignment 输入、stdout JSON/NDJSON 与 stderr 的 summary/ref、输出格式和完整性；`<details>` 或等价展开入口只能展开已脱敏内容。每个 discussion snapshot 至少包括根评论、参与者、完整回复树和根 status，并标明 `markdown_parser` / `trac discuss query` 来源。任何仅由 Agent 自报的 verdict、Human 决策或讨论状态都不算证据。
- 报告必须把 assignment、I/O、discussion、attempt、failure/retry、commit、audit gap、interrupted activity、stage/substate 和 final state 关联到稳定的 activity/command/run 标识，使另一个 Agent 不读取内部数据库也能判断旅程是否完整、是否有缺口及最终是否成功。

### NFR-0060 Live E2E 宿主与远端分支隔离

- **来源**：FR-0220、test-plan §12、flow.md §2

- tracks 自身的 live E2E 必须由用户或 CI 显式提供 `TRACKS_E2E_GITHUB_REPO`，指向可丢弃的 GitHub 测试仓库；缺失该配置时测试不得静默退化为本地假远端。
- 测试脚本负责创建临时分支并配置唯一预期 remote；测试凭据必须限制在可丢弃测试仓库范围内。单一分支是测试合同而非 bash 安全边界：测试结束后必须审计远端，只允许预期分支发生变化，发现其它分支/remote 副作用即失败并报告。该合同仅适用于 tracks 自身测试，不改变最终用户运行 `trac report` 的宿主仓库语义。
- live E2E 的本地测试根目录必须是独立 Git 仓库；其中的 `.tracks/runtime/tracks.db` 与 commit 历史是 `trac report` 的数据源。
- 测试不得主动删除本地临时宿主仓库；测试开始即打印宿主绝对路径，结束时再次打印宿主、报告目录、远端仓库和临时分支。远端临时分支是否清理由测试脚本明确决定并在报告中显示。
- live journey 只有在测试 harness 明确 opt-in 时才执行；普通 fake suite 不因 live 未配置而改变结果。未 opt-in 或缺 provider 凭据的 live job 明确 skip；已 opt-in 后的 provider/model/endpoint/credential 无效、opencode 不可启动或 GitHub 仓库/凭据不可验证，必须是带 `failure_class` 的配置/运行失败，不得静默 skip 或 fallback。
- live harness 的公开运行配置必须包含每次 Agent dispatch timeout、每个 review round timeout、整条 journey timeout 以及 review round 上限；每项都在 report 中回显实际值和触发结果。若 harness 采用单次 dispatch boundary，boundary 必须是可审计的 Runtime/test harness 输入和 `command_id` 活动边界，而不是隐藏的环境开关或未批准的产品状态转移。
- 任一 timeout、配置失败、远端审计失败或 Agent 链路未闭环都必须保留本地宿主、事件库、工作区 diff、已生成 report（若可生成）和远端标识；报告最终状态显示 failed/interrupted/awaiting_human 及未完成的 thread，不得清理后只留下 skip。

### NFR-0070 可安装发行物与运行时资源完整性

- **来源**：FR-0010、FR-0040、NFR-0060、test-plan §13

- 项目必须能通过标准 PEP 517 打包流程生成可安装发行物；发行物安装到全新的虚拟环境时，`trac` 入口、Python package、templates、skills 和 `tracks/agents/*.md` 角色定义必须完整可用。
- 生产形态和 live E2E 均不得依赖 editable install、workspace `PYTHONPATH`、源码树相对路径或未打包的交付物。live E2E 必须从临时发行物创建隔离虚拟环境并运行其中的 `trac`。
- `OpencodeBackend` 必须从已安装发行物读取 `Scribe.md`、`Sage.md`、`Lex.md`，在每次对应 dispatch 前将当前角色定义逐字物化到宿主项目 `.opencode/agents/<Name>.md`；不得在 backend prompt 中复制角色职责/工作方法。dispatch 结束后必须按既有合同清理或恢复该文件。
- 缺失或损坏的发行物资源必须以明确的安装/资源失败终止，不得回退到 workspace 源码、复制另一份角色 prompt 或静默使用默认 Agent。
