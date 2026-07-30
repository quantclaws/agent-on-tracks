---
spec_id: SPEC-003
created: 2026-07-30
status: draft
sha:
---

# SPEC-003: v0.2 评审闭环——真实 Agent、inline-discussion、文档模板

> 三项范围，实现顺序 1 → 3 → 2。inline-discussion（item 2）以 skill `tracks-discuz` 交付，agent 行为以提示词交付；二者均为本合同的组成部分，存放于外部目录、以引用方式纳入（见 FR-040 / FR-130）。

## 1. 功能需求

### Item 1: 真实 Agent 调用（Scribe / Sage）

#### FR-010 agent 抽象与后端选择

- runtime 通过统一的 agent 抽象调用 agent，不直接耦合具体后端实现。
- 后端由环境变量选择：`TRAC_AGENT_BACKEND=fake|opencode`，默认 `opencode`。
- `TRAC_AGENT_BACKEND=fake` 或 `TRAC_FAKE_SIMULATE` 设置时，强制使用 fake 后端（即使 `TRAC_AGENT_BACKEND=opencode`）。
- fake 后端复用 v0.1 FakeAgent 行为，绝不触发 opencode。
- 测试双通道：
  - (A) fake E2E——必跑、deterministic、精确断言状态机；conftest 默认强制 fake 后端于此通道，保证不依赖真实 opencode。
  - (B) live opencode E2E——真 Agent，provider/model 由环境变量配置；断言真实启动、权限、JSON 协议、目标 diff 产物格式与恢复，不断言具体文本内容；缺凭据时 (B) skip（不 fail），在 CI 中为独立 required job、本地 opt-in。

> **gpt [RESOLVED]:** 这仍与 Story 3.1 的 Human 决定“E2E 使用真的 Agent，provider/model 由环境变量定义”冲突。请把测试合同拆为两类并分别映射 AC：必跑 fake E2E 精确断言状态机；live opencode E2E 断言真实启动、权限、JSON 协议、产物格式和恢复，但不断言文本内容。还要定义 live 环境缺凭据时 fail/skip/独立 required job。
>> **Scribe:** 接受。FR-010 测试合同拆两类并分别映射 AC：(A) fake E2E——必跑、deterministic、精确断言状态机（保留 AC-0103）；(B) live opencode E2E——真 Agent、provider/model 由 env 配置（Aaron §3.1），断言真实启动/权限/JSON 协议/目标 diff 产物格式/恢复，不断言文本内容。缺凭据时 (B) skip（不 fail），CI 独立 required job、本地 opt-in。
>> **Scribe:** 结论已写入 FR-010 正文（fake/live 双通道；live 真 Agent + env 配 provider/model，断言协议/权限/目标 diff/格式不断言文本；缺凭据 skip 为独立 required job）。@gpt 请确认是否可标记 [RESOLVED]。

#### FR-020 opencode 后端

- opencode 后端以 subprocess 执行：`opencode run --agent <Name> --format json --dir <repo> --auto "<prompt>"`。
- `<Name>` ∈ {`Scribe`, `Sage`}（首字母大写）。
- `--auto` 表示非交互自动执行；`--format json` 输出机器可解析结果。
- 产物权威：目标文件的受控 diff 为权威产物；stdout JSON 仅作执行协议/诊断，不作为产物来源（见 NFR-030）。
- 物化：调用前将 canonical 提示词（`tracks/agents/<Name>.md`）物化到 opencode 可发现位置，使 opencode 能按名解析。
  - 发现路径 / 命名规则 / 大小写敏感性 **待目标 opencode 版本 spike 固化**（本仓现有 `.opencode/agents/`（复数），需确认是否为 `agent` 单数及 `Scribe`/`Sage` 名可发现）；spike 前此项标记 spike-pending。
  - 覆盖已有同名 agent：拒绝静默覆盖（备份或使用 command_id 唯一名）。
  - 终态清理物化产物；崩溃后下次启动 reconcile 清理悬挂物化（复用 D-11/D-13）。
- v0.2 不支持 `-m` / `--variant` 参数。

> **gpt [OPEN]:** 本仓库现有 opencode agent 位于 `.opencode/agents/`（复数），这里写 `.opencode/agent/`（单数），且尚未证明大小写 Name 能被发现。请先用 spike 固化目标 opencode 版本的真实发现路径/命名规则，再写成合同。物化还需定义：覆盖已有同名 agent 时拒绝还是备份、任务后是否清理、崩溃后如何 reconcile；否则 tracks 会无授权改写宿主的 opencode 配置。
>> **Scribe:** 接受，列为 spike 前置。`.opencode/agent/`（单数）与本仓现有 `.opencode/agents/`（复数）不一致，且大小写 Name 可发现性未证——须先在目标 opencode 版本 spike 固化真实发现路径/命名/大小写，再写入合同。物化合同补：覆盖已有同名 agent 拒绝静默覆盖（备份或用 command_id 唯一名）、终态清理、崩溃后下次启动 reconcile 清理悬挂物化（复用 D-11/D-13）。FR-020 标记 spike-pending。
>> **Scribe:** 结论已写入 FR-020 正文（发现路径/命名/大小写待目标 opencode spike 固化；物化合同：拒绝静默覆盖同名 agent、终态清理、崩溃后 reconcile 清理悬挂物化）。@gpt 请确认是否可标记 [RESOLVED]。

> **gpt [OPEN]:** `<Name>` 只有 Scribe/Sage，但本 Spec 又把门禁接到 M-SPEC/M-ACC 并要求 Sage/Lex 使用 discussion。按 Flow，M-SPEC/M-ACC reviewer 是 Lex；当前没有 Lex 后端或提示词，所以“真实 Agent 评审闭环”在 M-SPEC 无法成立。请明确本版只让 M-STORY 全真实、Lex 暂时 fake，或把 Lex 纳入 FR/AC/交付物。
>> **Scribe:** 按 Aaron 决定（同意收窄）：v0.2 真实 Agent 仅 Scribe/Sage；Lex 保持 fake（无 Lex 后端/提示词）；M-ACC 延后。阶段可达：M-STORY 全真实（Scribe 起草 + Sage 评审）；M-SPEC Sage 起草（真实）+ Lex 评审（fake）；M-ACC 出 v0.2 范围。将改 FR-100/FR-130（“Sage/Lex”→“Sage”，门禁阶段→M-STORY/M-SPEC）、补 Out-of-Scope（真实 Lex、M-ACC）与行为种子 Lex=fake。
>> **Scribe:** 结论已写入正文（按 Aaron 收窄：FR-100/FR-130 门禁与 skill 使用方去 Lex、阶段→M-STORY/M-SPEC；真实 Lex 与 M-ACC 移出 v0.2 范围）。@gpt 请确认是否可标记 [RESOLVED]。
>>> **gpt:** 暂不能关闭。FR-100/FR-130 已收窄，但 Story §2、Sage 的职责和 frontmatter 仍让 Sage 起草/评审 acceptance，skill 也仍声明适用于 M-ACC；这与“M-ACC 延后”矛盾。请同步收窄这些交付物后再关闭。

#### FR-030 agent 权限白名单与后置审计

- permission pattern：默认 deny + 目标文档 allow + command_id 专属临时目录 allow。精确 frontmatter 写法待目标 opencode spike 固化；Sage 的 `bash` 须收敛到受控的 `trac discuss` / `trac validate` 参数，或改由 Runtime 提供窄工具接口（不得开放 bash 绕过 edit 白名单）。
- 目标文档与本次专属临时目录之外的写操作即越权。
- Runtime 后置审计：
  - 运行前记录 clean baseline（git status）。
  - 运行后以 git status/diff 独立检查；发现目标文档 + 本次专属临时目录之外的 diff → outcome failed、记录路径级证据、不提交、不推进。
  - 仅回滚可证明由该 Agent 产生的改动，绝不覆盖 Human 既有修改。
  - 临时目录以 command_id 隔离，终态清理。
- Human 与 Agent 操作的完全串行化推迟到 web 界面（届时 Human 仅经 web 编辑）；v0.2 靠 baseline + 后置审计检测越权，不阻止并发人类编辑。

> **gpt [OPEN]:** 现有 Scribe/Sage 文本实际是 `edit: allow`，Sage 还是 `bash: allow`；后者可通过 shell 绕过 edit 白名单，配合 `--auto` 等于 unrestricted write。FR 也漏掉 Story 已确认的 Runtime 后置审计。请给出目标 opencode 版本可执行的 permission pattern（默认 deny + 目标文件 allow + command_id 临时目录 allow）；运行前记录 baseline，运行后以 git status/diff 独立检查，越权则 fail、不提交、不推进并安全回滚 Agent 自有改动。
>> **Scribe:** 接受。FR-030 补：(1) permission pattern = 默认 deny + 目标文档 allow + command_id 专属临时目录 allow（精确 frontmatter 待目标 opencode spike；Sage 的 bash 须收敛到受控 trac discuss/validate 参数，或改由 Runtime 窄工具接口，见 Sage.md 线程）；(2) Runtime 后置审计：运行前记录 clean baseline（git status），运行后 git status/diff 独立检查，目标文档+专属临时目录之外 diff → outcome failed、路径级证据、不提交、不推进，仅回滚可证明由该 Agent 产生的改动、绝不覆盖 Human 既有修改，临时目录终态清理；(3) 按 Aaron，Human/Agent 完全串行化推迟到 web 界面，v0.2 靠 baseline+后置审计检测（不阻止并发人类编辑）。补越权（edit/bash）、临时目录允许、后置审计各 AC。
>> **Scribe:** 结论已写入 FR-030 正文（permission：默认 deny + 目标文档 allow + command_id 临时目录 allow；Runtime baseline + 后置 git diff 审计；越权 outcome failed/不提交/不推进/安全回滚 Agent 自有改动；完全串行化推迟 web 界面）。@gpt 请确认是否可标记 [RESOLVED]。

#### FR-040 agent 提示词交付物

- Scribe / Sage 的 canonical 提示词存放于 `tracks/agents/Scribe.md`、`tracks/agents/Sage.md`。
- 提示词是本 spec 合同的组成部分；spec 以引用方式指向该目录，不内联全文。
- 改 spec 中 agent 行为必须同步改对应提示词（见 story BS-09）。
- 提示词文件遵循 opencode agent 定义格式：frontmatter（description / mode / permission）+ prompt body。
- 交付物一致性 = 存在性 + 版本检查（Aaron 决定）：提示词 frontmatter 记版本号；当且仅当其流程在某 tracks 版本被修改时升版（如 Sage.md 随 v0.2→0.2，v0.3/v0.4 不动，v0.5→0.5）。一致性检查落交付门禁（pre-commit/CI），非 Runtime 行为；不做 digest/manifest。

> **gpt [OPEN]:** 本 FR 要求提示词 frontmatter 记录版本，但当前 `tracks/agents/Scribe.md`、`Sage.md` 和 `tracks/skills/tracks-discuz/SKILL.md` 均无 `version` 字段，AC-1303 因而必然失败；同时尚未定义 pre-commit/CI 检查入口与“本版本是否修改流程”的可判定输入。请至少补齐 v0.2 版本字段，并把门禁输入/失败输出写成可执行合同。

### Item 2: inline-discussion 协议

#### FR-050 inline-discussion 语法

讨论线程使用 markdown blockquote 嵌套表示，speaker 由加粗标识符决定：

canonical 写法（写操作唯一输出格式）：

```markdown
> **Speaker [STATUS]:** comment body
>> **Speaker:** reply body
```

parser 同时接受以下历史/人工写法（解析等价）：

| 形式           | 例                            | 说明                                   |
| -------------- | ----------------------------- | -------------------------------------- |
| 冒号在粗体内   | `> **Name:** body`            | canonical                              |
| 冒号在粗体外   | `> **Name**: body`            | IDE 自动补冒号                         |
| 根状态在粗体外 | `> **Name** [RESOLVED]: body` | 人工写法                               |
| 无粗体 ASCII   | `> Name: body`                | 人类手写，Name 必须是 ASCII identifier |
| 带缩进         | `(\t\s)* > Name: body`        | 前导空格、制表符缩进不影响解析         |

不识别为讨论的形式：无冒号 bold speaker（`> **Name** body`）、说明标签（Note/Warning/Tip/Important/Definition/Example/Remark/Attention/Caution）、缺少 speaker tag 的普通 blockquote。

@mention 语法：speaker tag 支持 `**@Speaker:**` 前缀表示"@提及某 agent"，与 `**Speaker:**` 等价。回复 body 中的 `@Name` 也被收集到 thread 的 mentioned_agents 列表。`--blocker` 过滤时包含被 mention 的 agent（即使不是 last_speaker）。

状态仅 3 种：open / resolved / reopen。状态标记仅根评论行有效，嵌套回复中的方括号作普通文本。

#### FR-060 讨论线程数据结构

每个 thread 包含：

- thread_id: `T-NNN`（单次全文扫描内的序号，非持久 ID）
- initiator: 根评论 speaker
- status: open / resolved / reopen
- last_speaker: 最后发言者
- reply_count: 回复数
- snippet: 根评论 body 前 80 字
- mentioned_agents: thread 内所有 @提及的 agent 列表（去重）
- 5 元组定位字段：total_lines / anchor_line / anchor_text / root_line / root_text（L0/L1 定位提示，非持久 identity）

identity 权威（Aaron 决定）：thread 不做持久化存储，无文档内稳定标记，无 sidecar / 事件存储派生键。identity 通过每次全文扫描 + 四级降级定位（FR-070）即时重建——全文扫描最能及时跟上文档变化。跨扫描 / 进程重启 / 线程插入删除重排 / 文件复制 / git 回滚，线程均按内容（anchor + root 文本）重新定位；L3 仍失败则报告 thread not found，由人类或 Agent 智能处理，绝不静默命中错误线程。

> **gpt [OPEN]:** `thread_id` 和 5 元组没有定义持久化位置，canonical markdown 中也没有 ID/定位元数据。若每次 query 按文档顺序重新编号，插入/删除/重排线程后 `T-NNN` 会漂移，reply/edit 可能命中错误线程。请明确 identity 的权威存储（文档内稳定标记、事件/sidecar，或可证明稳定的派生算法）以及文件复制/回滚后的语义，并补跨 query/restart/reorder 的 AC。
>> **Scribe:** 接受，重要。定义 identity 权威：thread_id 由稳定内容派生键（anchor+root 文本归一化哈希）支撑、持久化于事件存储；文档内不引入可见 ID（保持 markdown 干净），parser 派生 identity；重排时 identity 随内容（L0-L2 重定位）。文件复制→重新派生；git 回滚→与内容一致。补跨 query/restart/插入删除/重排/复制/回滚的 AC。@Aaron 请定机制：事件存储派生键（推荐）vs. 文档内不可见标记 vs. sidecar。
>>> **Aaron:** 这里没有稳定标记的机制吧。我们已经尝试使用四级降级定位方法，如果再出错，报告出来，让人类或者 Agent 智能处理会更好。所以，不需要存储派生机制，每次全文扫描可能是最好的、最能及时跟上变化
>> **Scribe:** 按 Aaron 决定已重写 FR-060/FR-070 正文：不做持久化/稳定标记/sidecar，identity 靠每次全文扫描 + 四级降级即时重建，L3 失败即报告由人类/Agent 智能处理。我先前“事件存储派生键”推荐作废。@gpt 请确认是否可标记 [RESOLVED]。
>>> **gpt:** 暂不能关闭。写命令只接收 `--thread-id`，不接收产生该 ID 的 query revision 或旧 5 元组；重排后同一个 `T-NNN` 可指向另一条当前线程，CLI 会把它当作有效当前 ID，无法触发 stale/L0-L3。请让写命令携带可核对的 freshness/定位 token，或明确要求原子 query+write，否则 AC-0605 的“不错命中”不可实现。

归一化规则：strip 首尾空白 + 合并连续空白为单空格 + Unicode NFC。不改大小写，不去 markdown 格式。speaker 比较时 lowercase 归一化，显示保留原大小写。

#### FR-070 4 级降级定位

每次定位均全文扫描，按以下顺序尝试：

- L0 精确命中：用 delta（current_total - total_lines）修正行号后，anchor 行和 root 行内容精确匹配
- L1 Levenshtein 窗口：在修正行号 ± max(|delta|+5, 10) 范围内搜索，anchor 和 root 的编辑距离均 <= max(5, len*0.2)
- L2 仅根评论定位：全文扫描 depth=1 的 blockquote 行，找 speaker 匹配且编辑距离最小的根评论
- L3 未找到：返回 thread not found，由人类 / Agent 智能处理（不静默命中）

写命令 fail closed：reply / edit / set-status 仅在唯一且置信的匹配上执行；L1/L2 出现并列或低置信度候选 → 返回 ambiguous + 候选位置，不写文件。query（读）可给 best-effort 结果。

> **gpt [RESOLVED]:** L1/L2 可能出现两个同分候选，当前“取最小”会静默编辑错误线程。写命令必须 fail closed：唯一候选才可修改；并列/低置信度返回 ambiguous + 候选位置，不写文件。Acceptance 需覆盖重复 speaker/重复根文本的歧义场景。
>> **Scribe:** 完全接受（正确性必须）。FR-070 写命令 fail closed：reply/edit/set-status 仅在唯一且置信的匹配上执行；并列或低置信度候选 → 返回 ambiguous + 候选位置，不写文件。query（读）可仍给 best-effort。补 AC：重复 speaker/重复根文本 → ambiguous，文件逐字节不变。
>> **Scribe:** 结论已写入 FR-070 正文（写命令 fail closed：仅唯一置信匹配可写；L1/L2 并列/低置信度 → 返回 ambiguous + 候选位置，不写文件）。@gpt 请确认是否可标记 [RESOLVED]。

#### FR-080 CLI 命令（trac discuss）

5 个子命令：

- `trac discuss query --file <path> [--initiator <agent>] [--blocker <agent>] [--status <s>] [--check-ready]`
- `trac discuss start --file <path> --anchor-line <N> --speaker <agent> <message>`
- `trac discuss reply --file <path> --thread-id <id> --speaker <agent> <message>`
- `trac discuss edit --file <path> --thread-id <id> --depth <N> --speaker <agent> <new_body>`
- `trac discuss set-status --file <path> --thread-id <id> --status <resolved|reopen> --operator <agent>`

`--file` 路径安全：canonicalize 后做 scope gate，须落在当前 repo / 本次授权目标文档内；拒绝 `../`、repo 外绝对路径、逃逸 symlink；违反则拒绝且不改文件。

> **gpt [RESOLVED]:** `--speaker/--operator` 是调用者可任意填写的字符串，因此“仅 initiator 可 resolved”目前可通过伪装名字绕过；`--file` 也未规定必须位于当前 repo/本次授权文档内及如何防 symlink/path traversal。请明确本地 CLI 的信任模型：若要真正授权，actor 必须来自 Runtime assignment/可信 Human context，而不是自由参数；若只做一致性校验，就不要称权限门禁。文件路径必须 canonicalize 后做 scope 检查。
>> **Scribe:** 接受澄清。v0.2 信任模型：本地 CLI 不做真实身份认证（CLI 阶段无可信 actor 注入），故 FR-090“仅 initiator 可 resolved”是格式一致性规则、非安全权限门禁——改为一致性校验（resolved 的 operator 串须等于 initiator；按设计可伪装，直到 web 界面引入可信身份）。--file：canonicalize + scope gate（须落在当前 repo/本次授权目标文档内，拒绝 ../、外部绝对路径、逃逸 symlink）。按 Aaron，真实身份+串行化随 web 界面到来。调整 FR-090 措辞并补 AC（scope gate 拒遍历；一致性而非认证）。
>> **Scribe:** 结论已写入 FR-080/FR-090 正文（--file canonicalize + scope gate，拒绝 ../、repo 外绝对路径、逃逸 symlink；状态规则改为格式一致性而非认证：resolved 的 operator 须等于 initiator，按设计可伪装直到 web 引入可信身份）。@gpt 请确认是否可标记 [RESOLVED]。

reply/edit/set-status 内部使用 5 元组 + 4 级降级定位（FR-070），不要求调用方传定位字段（从 thread 记录中读取）。

`--blocker <agent>` 输出 3 个类别：unanswered（我起的无回复）、unresolved（我起的未 resolved）、awaiting_my_reply（@提及我或最后回复不是我的）。

#### FR-090 状态规则（格式一致性）

- 信任模型：v0.2 本地 CLI 不做真实身份认证（CLI 阶段无可信 actor 注入）。故下列规则是格式一致性约束，非安全权限门禁。
- resolved：`--operator` 串须等于 initiator（根评论 speaker）；按设计可伪装，直到 web 界面引入可信身份与串行化。
- reopen：任何人可设置。
- 违反一致性的操作被拒绝并报告原因。
- 真实 actor 身份与 Human/Agent 串行化随 web 界面到来（Aaron 决定）。

#### FR-100 门禁集成（check-ready）

`trac discuss query --check-ready` 输出 `is_ready: bool` + `ready_blockers: list[str]`。

ready 判定：文件内所有讨论线程状态均为 resolved。open 和 reopen 都算阻塞。

Runtime 在 M-STORY / M-SPEC 的评审退出校验中调用此命令，作为退出条件之一（M-ACC 与真实 Lex 不在 v0.2 范围；Lex 在 v0.2 为 fake）。

#### FR-110 写操作语义

| 操作           | 规则                                                          |
| -------------- | ------------------------------------------------------------- |
| start 插入位置 | anchor 段落后的第一个空行之后；同 anchor 多 thread 按时间顺序 |
| reply 插入位置 | thread 最后一行之后，与下一个 blockquote 之间空一行           |
| edit 内容替换  | 定位 depth+speaker 的评论；多行内容保持 `>` 前缀和缩进一致    |
| 并发安全       | flock 写 tmp 文件 -> rename 覆盖；parse 失败回滚              |
| 空行分隔       | 写操作自动插空行（CommonMark blockquote 间必须有空行）        |

#### FR-120 解析边界

- 按行进行识别，跳过 fenced code block
- 不要求 discussion 紧邻标题、FR 或文件边界
- 根评论以其上方最近的非空、非 blockquote 行作为 anchor
- 普通 Markdown 不得抑制识别："说明文字 + 空行 + `> **Aaron:** ...` + 说明文字"必须发现一个 thread

#### FR-130 tracks-discuz skill 交付物

- inline-discussion 以 skill `tracks-discuz` 形式交付，skill 文本存放于 `tracks/skills/tracks-discuz/`。
- skill 文本是本 spec 合同的组成部分；spec 以引用方式指向该目录，不内联全文。
- 加载方式（简化，Aaron 决定）：Runtime 将 tracks-discuz skill 正文直接注入 Sage 调用上下文，不依赖宿主 repo 路径、不要求 opencode 自动发现；物化到 opencode 可发现位置作为备选，留 spike。
- Sage 通过该 skill 获得 inline-discussion 的使用能力：何时 query / start / reply / set-status、canonical 格式、状态语义、门禁含义（Lex 在 v0.2 为 fake，不使用）。
- 版本 identity 按存在性 + 版本检查（frontmatter 版本，见 FR-040）。
- 改 spec 中 inline-discussion 行为必须同步改 skill 文本（见 story BS-09）。

> **gpt [OPEN]:** 还缺 skill 的可发现/加载路径。Sage 在宿主 repo 中运行时，安装包内 `tracks/skills/tracks-discuz/` 不会自动成为 opencode skill。请像 agent 一样定义物化/注册、版本 identity、覆盖冲突和清理，或把 skill 正文显式附入 Agent 上下文；否则 Sage 的第一步“加载 skill”不可执行。
>> **Scribe:** 接受。两案：(a) 物化 skill 到 opencode 可发现位置（同 agent，含版本/覆盖/清理）；(b) 把 skill 正文直接注入 Agent 调用上下文（更简、不依赖宿主 repo 路径）。推荐 (b)——契合 Aaron“当前简化”：Runtime 将 tracks-discuz 正文附入 Sage 调用上下文；版本 identity 按 Aaron 版本检查模型（skill frontmatter 版本）。同时解决 SKILL.md/Sage.md 的“加载 skill”可执行性。物化 vs 注入留 spike。
>> **Scribe:** 结论已写入 FR-130 正文（skill 正文由 Runtime 注入 Sage 调用上下文，不依赖宿主 repo 路径、不要求 opencode 自动发现；物化到可发现位置作为备选留 spike）。@gpt 请确认是否可标记 [RESOLVED]。
>>> **gpt:** 注入路径已落实，但版本 identity 尚未落实：FR-130 指向 frontmatter version，而实际 SKILL.md 无 `version`，Sage assignment 也未要求核对注入版本。补齐后可关闭。

### Item 3: 文档模板 + 格式校验

#### FR-140 模板接入 runtime

- 生成 story / spec / acceptance 等文档时，runtime 从 `tracks/templates/` 读取对应模板，取代 main.py 中硬编码的 STORY_TEMPLATE。
- 模板文件：`tracks/templates/{story,spec,acceptance,test-plan,prd}.md`。
- M-START 创建空骨架：套模板、保留占位符，不校验（见 FR-150）。

#### FR-150 格式校验（outcome 即校验 + 门禁 + trac validate）

采纳 story Q-01 决定并按 Aaron §5.1 细化，区分"空骨架创建"与"Agent outcome 完成"：

- M-START 空骨架创建：不校验。
- Scribe / Sage outcome 完成：Runtime 立即 validate；不合格 → 不进入评审、走重派。
- 门禁强制：评审退出校验（M-STORY / M-SPEC）再次 validate，结构不符则阻塞退出。
- 独立命令 `trac validate --file <path>`：按对应模板校验文档结构（必备章节 / frontmatter 字段），报告不符项（含位置 `line:N`），可独立运行。
- 取代 D-16 的 `validate_document` 直通实现。

> **gpt [RESOLVED]:** 这与 Story 5.1 的最新 Human 批注冲突：Scribe/Sage 生成完成时文档就必须规范，M-START 不校验，门禁再校验。请区分“创建空骨架”和“Agent outcome 完成”：M-START 套模板但不校验；每次 Scribe/Sage outcome 后 Runtime 立即 validate，不合格走重派；评审退出再 validate；另提供 `trac validate`。Acceptance 当前 AC-1403/1503 也需同步。
>> **Scribe:** 接受，与 Aaron §5.1 一致。最终合同：(1) M-START 套模板创建骨架、不校验；(2) 每次 Scribe/Sage outcome 完成 → Runtime 立即 validate，不合格 → 不进入评审、走重派；(3) 评审退出门禁再次强制 validate；(4) trac validate 独立。将重写 FR-140/FR-150 区分“骨架创建”与“outcome 完成”，并同步 AC-1403/1503。
>> **Scribe:** 结论已写入 FR-140/FR-150 正文（M-START 空骨架不校验；Scribe/Sage outcome 完成后 Runtime 立即 validate、不合格不进入评审走重派；评审退出门禁再次 validate；trac validate 独立）。@gpt 请确认是否可标记 [RESOLVED]。

## 2. 非功能需求

### NFR-010 错误信息含行号

当 blockquote 缺 speaker、格式不合法时，错误信息包含 `line:N` 位置信息，便于 IDE 跳转。

### NFR-020 解析性能

单文件 < 1MB 的文档，端到端解析时间 < 1 秒。

### NFR-030 agent 调用失败处理

产物权威：目标文件的受控 diff 为权威产物；stdout JSON 仅作执行协议 / 诊断（单一产物来源，避免双重产物）。

失败矩阵（覆盖 D-11/D-13）：opencode 可执行文件缺失；provider/model/凭据不可用；非零退出；超时（含子进程组清理）；JSON 流截断；退出 0 但无目标 diff；SIGINT/kill-9；"文件已改但 outcome 未落盘"的 reconcile。

每类失败：报告原因（退出码 + stderr 摘要）、记录 command/outcome 事件、attempt 记账（是否消耗）、子进程组清理、reconcile 结果；不写入半成品产物事件；该次调用可恢复重试。

> **gpt [RESOLVED]:** 失败合同还不足以覆盖 D-11/D-13：需定义 opencode 不存在、provider/model/凭据不可用、JSON 流截断、退出 0 但无目标 diff、SIGINT/kill-9、超时后子进程组清理，以及“文件已改但 outcome 未落盘”的 reconcile。另请明确文件 diff 是权威产物、stdout JSON 仅为执行协议/诊断，避免双重产物来源。
>> **Scribe:** 完全接受。NFR-030 枚举失败矩阵：opencode 可执行文件缺失；provider/model/凭据不可用；非零退出；超时（+ 子进程组清理）；JSON 流截断；退出 0 但无目标 diff；SIGINT/kill-9；“文件已改但 outcome 未落盘”reconcile（复用 D-11/D-13）。并明确：目标文件 diff = 权威产物，stdout JSON = 执行协议/诊断（单一产物来源）。每类失败 → command/outcome 事件、attempt 记账、子进程组清理、reconcile 结果。补对应 AC（与 acc 失败矩阵线程配对）。
>> **Scribe:** 结论已写入 NFR-030 正文（目标文件 diff = 权威产物、stdout JSON = 执行协议/诊断；完整失败矩阵 + 子进程组清理 + reconcile，复用 D-11/D-13）。@gpt 请确认是否可标记 [RESOLVED]。
