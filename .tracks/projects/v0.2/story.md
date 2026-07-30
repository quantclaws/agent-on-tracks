---
story_id: S-003
title: v0.2 评审闭环——真实 Agent、inline-discussion、文档模板
created: 2026-07-30
status: draft
sha:
---

# S-003: v0.2 评审闭环——真实 Agent、inline-discussion、文档模板

## 1. 原始输入

> v0.2 做三件事（实现顺序 1 → 3 → 2）：
>
> 1. **接入真正的 Scribe 和 Sage。** v0.1 只有 FakeAgent。v0.2 让 Scribe（写文档）和 Sage（评审）成为真实 agent，通过 opencode 子 agent 调用：`opencode run --agent Scribe --format json --dir <repo> --auto "<prompt>"`。tracks 位于 opencode 之上，不是 opencode 插件；tracks 最终作为 web server 运行，CLI 只是当前传输层，runtime 必须传输无关。Agent 名首字母大写（Scribe / Sage）。Agent 后端用环境变量选择：`TRAC_AGENT_BACKEND=fake|opencode`（默认 opencode），`TRAC_FAKE_SIMULATE` 强制 fake（测试），conftest 为 E2E 强制 fake。接受 `--auto`；agent 的 `permission:` 收敛到白名单（只能编辑目标文档）。暂不做 `-m` / `--variant`。
> 2. **inline-discussion 协议 + skill。** 评审阶段（M-STORY / M-SPEC / M-ACC）Sage/Lex 与 Human 在文档内做结构化多轮讨论（markdown blockquote 嵌套）。迁移自 louke v0.4-004-quote-dialogue + v0.7-003-inline-discussion-protocol。
> 3. **story/spec 模板 + 格式校验落脚点。** `tracks/templates/` 已有模板但没接进 runtime（`cmd_start` 用 main.py 硬编码的 STORY_TEMPLATE；`validate_document` 是 D-16 直通）。v0.2 把模板接进 runtime，并确定格式校验落在哪里。

> **gpt [OPEN]:** 本段同时规定“conftest 为 E2E 强制 fake”，而 3.1 的 Human 批注明确要求 E2E 使用真实 Agent，两者不能同时作为唯一 E2E 策略。建议明确两条测试通道：必跑的 deterministic fake E2E（精确断言工作流）+ 使用可配置 provider/model 的 live opencode E2E（只断言启动、权限、产物格式、退出与恢复，不断言具体文本）；并规定缺少凭据时是 fail、skip 还是独立 required job。
>> **Scribe:** 采纳双通道，并与 Aaron 在 §3.1 的决定（E2E 用真 Agent、provider/model 走环境变量）合并：(A) deterministic fake E2E——默认必跑、精确断言工作流；(B) live opencode E2E——真 Agent，provider/model 由 env 配置，只断言启动/权限/产物格式/退出/恢复，不断言具体文本。缺凭据时 (B) skip（不 fail），CI 中为独立 required job、本地 opt-in。据此改写 §1 与 §4.2，消除“conftest 强制 fake”与“真 Agent E2E”的冲突（fake 为默认通道，live 为独立通道）。

> **gpt [OPEN]:** Item 2/用户意图把协议用于 M-ACC 和 Lex，但本 Story 没有实现 M-ACC，也只接入真实 Scribe/Sage，没有 Lex 后端或提示词。若保持当前范围，M-ACC 行为不可达、M-SPEC 的 Lex 评审仍不是真实 Agent。请二选一：把真实 Lex/M-ACC 纳入范围与行为种子，或把 v0.2 可达范围收窄到 M-STORY/M-SPEC，并明确 Lex 暂时 fake。
>> **Scribe:** 这是范围决定，需 Aaron 拍板；Agent 推荐收窄：v0.2 真实 Agent 仅 Scribe/Sage，可达评审阶段收窄到 M-STORY/M-SPEC；Lex 保持 fake（无 Lex 后端/提示词），M-ACC 延后。将修正 §2 用户意图与 §3.3（“Sage/Lex”→“Sage”，M-ACC 移入 §4.3），并在行为种子明确 Lex=fake。@Aaron 请确认：收窄 vs. 纳入真实 Lex/M-ACC。
>>> **Aaron:** 同意收窄

## 2. 用户意图

- 用户想让 tracks 的评审阶段（M-STORY / M-SPEC / M-ACC）真正跑起来：Scribe 真实地写出 story/spec，Sage 真实地评审，而不是 v0.1 的 FakeAgent 占位。
- 当前受阻于三处：(a) 没有真实 agent 调用 seam（只有 FakeAgent）；(b) inline-discussion 协议未实现，Sage/Human 无法在文档内结构化讨论；(c) 模板没接进 runtime，文档格式无校验落脚点。
- 完成后能看到：`trac start` 调起真实 Scribe（opencode）写出 story.md；Sage 评审并通过 inline-discussion 与 Human 多轮讨论；文档按模板校验；整条评审闭环用真实 agent 端到端跑通（开发/测试时可用 fake 后端）。

> **gpt [OPEN]:** 这里的用户命令顺序与现有 Flow 不一致：`trac start` 只捕获 stdin 原始需求并进入 M-STORY，Scribe 在后续 `trac run`、Human triage go 后才起草 Story。请按真实 CLI 序列重写完成结果，否则实现和 E2E 会分别采用两条路径。
>> **Scribe:** 接受，是我把 CLI 序列写错。正确序列：trac start 仅捕获 stdin 原始需求并进入 M-STORY → Human triage go → trac run 调起 Scribe 起草 Story。将按此重写 §2 完成结果与 §3.x 路径，避免实现/E2E 走两条路径。

## 3. 核心操作路径

### 3.0. 产品现状（修改类 Story 必填）

| 路径              | 现状                                                                                                         | 本 Story 变更                                                                        |
| ----------------- | ------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------ |
| Agent 调用        | 仅 FakeAgent（executor/fake_agent.py），无真实 agent seam                                                    | 引入 agent 抽象 + opencode 后端（subprocess `opencode run`），env 选择 fake/opencode |
| 文档模板          | tracks/templates/ 有模板但未接入；cmd_start 用 main.py 硬编码 STORY_TEMPLATE；validate_document 是 D-16 直通 | 模板接入 runtime；确定格式校验落脚点                                                 |
| inline-discussion | flow.md 提到但未实现                                                                                         | 实现协议 + `trac discuss` 命令 + 门禁集成                                            |

> 下游 Spec/Architecture/Acceptance 必须基于此现状描述变更，而非从零设计。

### 3.1. 真实 Agent 调用（Scribe / Sage）

- **变更性质**：新增（agent 抽象 + opencode 后端）+ 替换（调 agent 的入口从 FakeAgent 切到抽象）
- **产品现状**：executor 直接持有 FakeAgent；无后端选择。
- **用户起点**：用户执行 `trac start` / 进入评审阶段。
- **入口/触发**：runtime 在需要 agent 产出/评审时，通过 agent 抽象调用对应 agent。
- **交付物**：Scribe / Sage 的提示词是本 Story 交付物，存放于 `tracks/agents/`（`Scribe.md` / `Sage.md`），属 spec 合同的一部分；因文本较长，spec 以引用方式指向该目录，不内联全文。改 spec 必须同步改这些提示词。

> **gpt [OPEN]:** 仅把提示词放进 `tracks/agents/` 不能保证 `opencode run --agent Scribe` 能发现它；当前 opencode 从其 agent 配置位置解析 `--agent`。Story 需要补齐安装/注册/临时物化的用户路径、版本来源和清理语义，否则第一条真实 Agent 命令会因找不到 Scribe/Sage 而中断。
>> **Scribe:** 接受。Spec FR-020 已含“调用前把 tracks/agents/<Name>.md 物化到目标 repo 的 .opencode/agent/<Name>.md”，但 Story 需补用户可见路径：物化时机（每次调用前）、版本来源（tracks/agents/ 随 tracks 版本固定）、清理语义（终态清理或幂等覆盖）。将补入 §3.1 步骤并新增行为种子；精确物化/清理机制留 spike。

1. runtime 依据 `TRAC_AGENT_BACKEND` 选择后端（默认 opencode；测试 fake）。
2. opencode 后端以 subprocess 执行 `opencode run --agent <Scribe|Sage> --format json --dir <repo> --auto "<prompt>"`。
3. agent 的 `permission:` 白名单限定其只能编辑目标文档。
4. runtime 解析 `--format json` 输出，取回 agent 产物。
   > **Aaron:** 这一步要求e2e测试时使用真的 Agent。真 Agent 使用的provider/model 可通过环境变量定义
5. 用户在目标文档中看到 agent 真实产出（story/spec 或评审意见）。

> **gpt [OPEN]:** 需要明确产物权威出口和失败路径。Agent 已被授权直接编辑目标文件，同时 runtime 又解析 JSON“取回产物”；两者谁是真相必须唯一。建议 JSON 仅作为执行事件/诊断，Runtime 以受控文件 diff 为产物并独立校验。另请增加 opencode 不存在、provider/model 不可用、非零退出、超时、JSON 流截断、无产物、SIGINT/kill-9 的行为种子，并复用 D-11/D-13 的悬挂命令恢复语义。
>> **Scribe:** 接受“文件 diff 为产物真相、JSON 仅诊断”——与 Aaron 的权限模型（Agent 直接编辑目标文档）一致：Runtime 记录 baseline → Agent 运行 → 对目标文档取受控 diff 为产物并独立校验，JSON 仅作执行事件/诊断。将按清单补失败行为种子（opencode 缺失、provider/model 不可用、非零退出、超时、JSON 截断、无产物、SIGINT/kill-9），复用 D-11/D-13 悬挂命令恢复语义。

- **完成结果**：评审阶段由真实 agent 驱动；fake 后端仍可用于开发/测试。
- **继续/返回**：产物进入后续评审/门禁；失败可恢复重试。

### 3.2. 文档模板 + 格式校验

- **变更性质**：修改（模板接入）+ 新增（校验落脚点）
- **产品现状**：模板存在于 tracks/templates/ 但未接入；cmd_start 硬编码 STORY_TEMPLATE；validate_document 直通（D-16）。
- **用户起点**：`trac start` 生成文档；评审阶段校验文档。
- **入口/触发**：生成文档时套用模板；门禁处校验格式。

1. 生成文档时从 tracks/templates/ 读取对应模板（取代硬编码）。
2. 确定格式校验的落脚点（哪个阶段/命令执行校验）。
3. 校验文档结构是否符合模板。
4. 用户在评审/门禁处看到校验结果。

> **gpt [OPEN]:** “确定格式校验落脚点”已经被 5.1 的 Human 决定取代，不应继续作为开放实现目标。请把最终合同直接写回本路径：M-START 原始骨架不校验；Scribe/Sage 每次生成完成后必须产出规范文档并由 Runtime 校验；评审退出门禁再次强制校验；`trac validate` 提供独立检查。还需给新增 CLI 补行为种子。
>> **Scribe:** 接受，且 Aaron 在 §5.1 的决定细化了早先的 B+C。最终合同：(1) M-START 原始骨架不校验；(2) Scribe/Sage 每次生成必须产出规范文档、Runtime 生成后即校验（Aaron：生成时必须规范）；(3) 评审退出门禁再次强制校验；(4) trac validate 独立检查。将把“确定落脚点”从开放目标改为已定合同写回 §3.2，更新 §5.1 Human 决定，并为 trac validate 补行为种子。

- **完成结果**：文档由模板生成且有格式校验。
- **继续/返回**：校验失败给出修复指引。

### 3.3. inline-discussion 协议

- **变更性质**：新增
- **产品现状**：无（flow.md 提到但未实现）。
- **用户起点**：评审阶段，Sage/Lex 或 Human 对文档某段落有疑问。
- **入口/触发**：`trac discuss` 命令 + IDE 内手写 blockquote。
- **交付物**：inline-discussion 以 skill `tracks-discuz` 形式交付，skill 文本存放于 `tracks/skills/tracks-discuz/`，属 spec 合同的一部分；因文本较长，spec 以引用方式指向该目录，不内联全文。Sage / Lex 通过该 skill 使用 inline-discussion。改 spec 必须同步改 skill 文本。

> **gpt [OPEN]:** Story 说“迁移自 louke”并引用 canonical 格式、5 元组和 L0-L3，但没有参考文档清单或最小协议摘要，单读 Story 无法判断迁移是否完整。请增加明确的 canonical 来源，并在 Story 保留用户可见语义：thread identity、状态权限、ready 条件、定位失败时的结果；算法细节可留给 Spec。
>> **Scribe:** 接受。将补 canonical 来源（louke v0.4-004-quote-dialogue + v0.7-003-inline-discussion-protocol，给出文档引用），并在 Story 保留用户可见语义：thread identity（T-NNN per file）、状态权限（resolved 仅发起人）、ready 条件（全部 resolved）、定位失败结果（L3 not found + 建议重查）；Levenshtein 阈值/5 元组等算法细节留 Spec。
>> **Aaron:** Louke https://github.com/zillionare/louke, 文档在releases/v0.14.0 branch

1. Sage 执行 `trac discuss start --file spec.md --anchor-line 42 --speaker Sage "..."`，Runtime 在 anchor 段落后插入 open 根评论。
2. Human 在 IDE 看到 `> **Sage:** ...`，手写 `>> **Aaron:** ...` 回复。
3. Sage 执行 `trac discuss query --file spec.md --blocker Sage`，看到 awaiting_my_reply。
4. Sage `reply` / 解决后 `set-status ... resolved`。
5. Runtime 在评审退出校验执行 `trac discuss query --check-ready`，确认所有 thread resolved。

> **gpt [OPEN]:** 这里混合了 Runtime 写操作与 Human 在 IDE 的未提交编辑，但没有说明 Git/锁闭环。请明确 discuss start/reply/set-status 是否由 Runtime 提交、Human 手写回复何时被捕获提交、并发写如何拒绝，以及模板校验/sha 是否忽略或包含讨论块；否则用户能看到讨论，却可能因脏工作区或 sha 漂移无法退出评审。
>> **Scribe:** 接受，需补 Git/锁闭环。拟定：(a) discuss start/reply/set-status 为 Runtime 写操作、flock 串行化（FR-110）、落入工作区；是否由 Runtime 自动提交属产品决定，倾向随正常文档提交流程提交（@Aaron 确认）；(b) Human 在 IDE 的手写回复在下次解析/提交时捕获（parser 兼容人工格式）；(c) 并发写由 flock 拒绝/串行化；(d) sha 包含讨论块（属文档一部分），但模板校验忽略讨论块（非模板结构）。据此补 §3.3 与行为种子。
>>> **Aaron:** 串行化。但当前人类编辑会脱离 tracks的控制，无法保证串行化，等 web 界面出现，人类只能通过 web 界面来编辑，到时候串行化就有了技术手段。当前可以简化处理。

- **完成结果**：文档内结构化多轮讨论；门禁以"全部 resolved"为退出条件之一。
- **继续/返回**：行号漂移时按内容定位（4 级降级）找回 thread。

### 3.4. 行为种子

#### 3.4.1. BS-01 真实 agent 产出

- EARS: `WHEN runtime 需要 agent 产出且 TRAC_AGENT_BACKEND=opencode, THE 系统 SHALL 通过 opencode subprocess 调起对应 agent 并取回其产物`
- 来源: [3.1 / 约束]
- 说明: 保护"评审阶段由真实 agent 驱动"的用户结果。

#### 3.4.2. BS-02 后端可切换且测试隔离

- EARS: `IF TRAC_AGENT_BACKEND=fake 或 TRAC_FAKE_SIMULATE 设置, THE 系统 SHALL 使用 FakeAgent 而不调用 opencode`
- 来源: [3.1 / 非常规要求]
- 说明: 保护开发/测试不依赖真实 opencode。

#### 3.4.3. BS-03 agent 权限白名单

- EARS: `WHEN agent 被调起, THE 系统 SHALL 将其可写范围限定为目标文档`
- 来源: [3.1 / 约束]
- 说明: 保护仓库其余部分不被 agent 误改。

> **Aaron:** runtime 也要在 Agent 运行结束后，通过 git status 检查Agent 有没有超越权限修改文件。对所有的 Agent，自动授权可以在系统临时目录中，创建目录和文件，修改自己创建的目录和文件。

> **gpt [OPEN]:** 还缺“发现越权后怎么办”的外部结果。建议规定：执行前记录 clean baseline；执行后发现目标文档和本次专属临时目录之外的 diff，即 outcome failed、记录路径级证据、不提交、不推进；只回滚可证明由该 Agent 产生的改动，绝不覆盖 Human 既有修改；临时目录使用 command_id 隔离并在终态清理。请为越权和临时目录例外各补行为种子。
>> **Scribe:** 接受，并并入 Aaron 在 §3.4.3 的决定。最终合同：(1) 执行前记录 clean baseline（git status）；(2) 所有 Agent 自动授权系统临时目录（command_id 隔离、仅自己的目录/文件，Aaron）；(3) 执行后 git status 检查，目标文档+本次专属临时目录之外的 diff → outcome failed、记录路径级证据、不提交、不推进；(4) 仅回滚可证明由该 Agent 产生的改动，绝不覆盖 Human 既有修改；(5) 临时目录终态清理。将为越权检测与临时目录例外各补行为种子，并更新 BS-03。
>> **Aaron:** 应该让 Human 与 Agent 操作串行化，这样就可以区分了。串行化控制等 web 界面加上后再实施。目前没有技术条件实现，因为人类可以随时改文件。

#### 3.4.4. BS-04 模板生成文档

- EARS: `WHEN 生成 story/spec 文档, THE 系统 SHALL 套用 tracks/templates/ 中对应模板而非硬编码`
- 来源: [3.2]
- 说明: 保护文档结构一致。

#### 3.4.5. BS-05 格式校验落脚点

- EARS: `WHEN 文档进入门禁, THE 系统 SHALL 按模板校验其结构并报告不符项`
- 来源: [3.2 / 重要推导]
- 说明: 保护"文档可被下游可靠消费"。

#### 3.4.6. BS-06 inline-discussion 创建/回复/状态

- EARS: `WHEN 执行 discuss start/reply/set-status, THE 系统 SHALL 按 canonical blockquote 格式写入并维护 open/resolved/reopen 状态`
- 来源: [3.3]
- 说明: 保护文档内讨论可读可机读。

#### 3.4.7. BS-07 行号漂移定位

- EARS: `IF 文档被修改导致行号漂移, THE 系统 SHALL 通过 4 级降级（L0-L3）按内容找回 thread`
- 来源: [3.3 / 重要边界]
- 说明: 保护讨论在文档演化中不丢失。

#### 3.4.8. BS-08 门禁 ready 判定

- EARS: `WHEN 评审退出校验执行 discuss query --check-ready, THE 系统 SHALL 仅当所有 thread resolved 时返回 ready`
- 来源: [3.3 / 约束]
- 说明: 保护"讨论未收敛不退出评审"。

#### 3.4.9. BS-09 交付物随 spec 同步

- EARS: `WHEN spec 中 inline-discussion 或 agent 行为被修改, THE 系统 SHALL 同步更新 tracks/skills/tracks-discuz/ 的 skill 文本与 tracks/agents/ 的提示词，使三者一致`
- 来源: [3.1 / 3.3 / 约束]
- 说明: 保护"spec、skill 文本、agent 提示词"三者不脱节（skill 与提示词是 spec 合同的一部分）。

> **gpt [OPEN]:** BS-09 目前是开发流程愿望，不是可观察产品行为：测试无法知道一次 spec 修改是否“应该”同步哪些文本。请给三类产物稳定 revision/digest/manifest，并定义一致性检查的输入和失败输出；否则移到交付门禁而不是作为 Runtime 行为种子。
>> **Scribe:** 接受。BS-09 现状是流程愿望、非 Runtime 可观察行为。推荐改为交付门禁：给 spec/skill/agent 提示词三类产物稳定 digest/manifest，在 pre-commit/CI 一致性检查中定义输入与失败输出（而非 Runtime 行为种子）。@Aaron 请定：CI/pre-commit 交付门禁（推荐）vs. trac 运行时一致性检查。将据此重写或移除 BS-09。
>>> **Aaron:** 只做有与无和版本检查。比如，如果 v0.2涉及到 Sage 提示词修改，则 Sage.md 的版本应该是0.2 -- 记录在 frontmatter；v0.3,v0.4没有涉及 Sage 的流程修改，则 Sage.md 版本不动，v0.5涉及Sage流程修改，则Sage.md 内容要改，版本号升级到0.5

## 4. 范围、约束与例外

### 4.1. 必须保持的产品约束

- runtime 传输无关（CLI 现在，web server 将来）；agent 调用不得耦合 CLI。
- Agent 名首字母大写（Scribe / Sage）。
- 事件溯源合同不变（v0.1 的 append-only events + projections）。
- skill 文本（`tracks-discuz`）与 agent 提示词（Scribe / Sage）是 spec 合同的组成部分：改 spec 必须同步改这些文本；因文本较长，单独存放于 `tracks/skills/tracks-discuz/` 与 `tracks/agents/`，spec 以引用方式指向，不内联全文。

### 4.2. 非常规要求

- tracks 位于 opencode 之上（subprocess 调用），不做 opencode 插件/扩展。
- agent 后端用环境变量选择（TRAC_AGENT_BACKEND / TRAC_FAKE_SIMULATE），conftest 为 E2E 强制 fake。

### 4.3. Out-of-Scope

- 不做 web UI（v0.2 仍 CLI + IDE 编辑）。
- 不做 opencode `-m` / `--variant` 参数。
- 不做 @mention 通知推送（parser 识别但不触发通知）。
- 不做讨论线程跨文件关联、讨论历史版本化（git 提供）。
- 不迁移 louke 的 12 个 agent prompt（tracks agent prompt 从头写）。

## 5. 开放产品决定

### 5.1. Q-01 格式校验的落脚点（已决定）

- **为什么必须由 Human 决定**：校验落在生成时、门禁时还是独立命令，会改变用户何时看到校验失败、以及门禁严格度——这是产品结果差异。
- **可选方向**：A. 生成时即校验（写文档后立即报）；B. 门禁时校验（评审退出前统一报）；C. 独立 `trac validate` 命令。
- **Agent 推荐**：B + C（门禁强制 + 独立命令可单独跑），生成时不强制——理由：生成时文档常为空骨架，强制校验会误报；门禁是天然收敛点。
- **Human 决定**：采纳 B + C——门禁强制校验 + 独立 `trac validate` 命令可单独跑；生成时不强制。
> **Aaron:** Scribe 和  Sage生成时，文档就必须是规范的。M-START 时不校验。此外，门禁时校验。

> 其余技术选择（agent 抽象接口形状、json 输出解析细节等）属技术决定，不在此节。

## 6. 必要性、风险与分流建议

- **既有能力**：FakeAgent（可作 fake 后端基础）、tracks/templates/（模板已存在）、v0.1 事件溯源 runtime。
- **冲突**：无。
- **重要风险**：opencode subprocess 集成的输出解析与权限白名单是否如预期可控（需 spike 验证 `--format json` 输出与 permission 收敛）。
- **分流建议**：Go — 三项范围清晰、有既有基础、序列 1→3→2 明确；建议先对 opencode 调用做最小 spike 再展开实现。
