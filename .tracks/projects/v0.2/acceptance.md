---
acc_id: ACC-003
created: 2026-07-30
status: draft
sha:
---

# ACC-003: v0.2 评审闭环——真实 Agent、inline-discussion、文档模板

> AC 编号：AC-FRXXXX-YY，与 SPEC-003 的 FR/NFR 条目一一对应。

## FR-0010 agent 抽象与后端选择

### AC-FR0010-01

  - 默认（未设 TRAC_AGENT_BACKEND）时通过 opencode 后端调用 agent

### AC-FR0010-02

  - TRAC_AGENT_BACKEND=fake 或设置 TRAC_FAKE_SIMULATE 时使用 FakeAgent，且不触发 opencode 进程

### AC-FR0010-03

  - fake E2E（deterministic suite）：conftest 强制 fake 后端，精确断言状态机，不依赖真实 opencode

### AC-FR0010-04

  - live opencode E2E：真 Agent + env 配置 provider/model，断言真实启动、权限、JSON 协议、目标 diff 产物格式与恢复，不断言具体文本内容

### AC-FR0010-05

  - live suite 缺凭据时 skip（不 fail），在 CI 中为独立 required job、本地 opt-in

> **gpt [RESOLVED]:** 缺少 Human 已要求的 live Agent E2E。请保留 AC-0103 作为 deterministic suite，并新增 live suite：真实 opencode + 环境配置 provider/model，断言执行协议、目标 diff、权限与格式，不断言具体语义文本；明确缺少凭据时的 job 结果。
>> **Scribe:** 接受。保留 AC-0103（deterministic fake suite）。新增 live suite AC：真实 opencode + env 配置 provider/model（Aaron），断言执行协议/目标 diff/权限/格式，不断言语义文本；缺凭据 → live suite skip（不 fail），CI 独立 required job、本地 opt-in。将补 AC-0104..。
>> **Scribe:** 结论已写入正文（保留 AC-0103 deterministic fake suite；新增 live suite AC-0104/0105：真 opencode + env provider/model，断言协议/目标 diff/权限/格式不断言文本；缺凭据 skip 为独立 required job）。@gpt 请确认是否可标记 [RESOLVED]。

### AC-FR0010-06

  - 显式 opt-in 的完整 live journey 真实启动 opencode 并实际运行 `Scribe`、`Sage`、`Lex`，由真实 Runtime 产生事件/状态推进、由宿主 Git 产生提交、由 `trac report` 读取该宿主的事件与 Git；fake Agent、测试步骤摘要或测试驱动器直接写目标文档均不能替代这些环节。`tests/e2e/test_full_journey.py::test_full_journey_to_boundary` 仍保留并继续作为 fake deterministic 流程，不被 live 流程替换。

### AC-FR0010-07

  - live seed 至少使 Scribe 必须处理“交付入口 CLI vs library”和“空白行是否计入”两个产品问题；有限 transcript 以明确 actor（`LiveE2E-Human`）注入真实 Agent console，并可观察地被 Agent 通过 `trac discuss` 写入权威 Markdown。实现语言、框架、CI、架构等技术选择不产生 Human 问题；未知产品问题或 transcript 耗尽时 fail-closed，不使用合理默认。

### AC-FR0010-08

  - 普通未 opt-in live suite 在缺 provider/model/凭据时明确 skip 且不触发 fake fallback；显式 opt-in 后 provider 配置无效/不可达时报告 `provider_unavailable` 并失败。完整 live journey 缺失或无法验证 `TRACKS_E2E_GITHUB_REPO` 时报告配置错误，不把本地仓库当远端。

## FR-0020 opencode 后端

### AC-FR0020-01

  - opencode 后端以 subprocess 执行 `opencode run --agent <Name> --format json --dir <repo> --auto "<prompt>"`

### AC-FR0020-02

  - `<Name>` 仅取 `Scribe` / `Sage` / `Lex`（首字母大写）

### AC-FR0020-03

  - runtime 解析 stdout 的 JSON 取回 agent 产物/状态

### AC-FR0020-04

  - 调用前将 `tracks/agents/<Name>.md` 物化到 opencode 发现路径 `.opencode/agents/<Name>.md`（复数，Aaron 决定）；命名/大小写规则 Aaron spike 已固化——文件名即 `--agent` 名，逐字一致（大小写敏感）

### AC-FR0020-05

  - 已有同名 agent 不被静默覆盖（拒绝或备份）

### AC-FR0020-06

  - 正常与失败终态清理物化产物；崩溃后下次启动 reconcile 清理悬挂物化

### AC-FR0020-07

  - opencode 按文件名精确解析 `--agent`（`Scribe`/`Sage`/`Lex`）发现物化定义；Aaron spike 已固化命名规则，live suite 回归验证

> **gpt [RESOLVED]:** 该路径疑似与当前项目实际使用的 `.opencode/agents/`（复数）不一致。AC 还应覆盖已有同名文件不被静默覆盖、正常/失败后的清理、崩溃恢复，以及 opencode 确实能按 `Scribe`/`Sage` 名发现物化定义。
>> **Scribe:** 接受，spike-pending。AC-0204 路径待 spike 修正（.opencode/agent vs agents + 目标 opencode 大小写）。补 AC：已有同名 agent 不被静默覆盖（拒绝/备份）；正常+失败后清理；崩溃恢复（下次启动清理悬挂物化）；opencode 确能按 Scribe/Sage 名发现物化定义（live suite 验证）。
>> **Scribe:** 结论已写入正文（AC-0204 物化路径待 spike；补 AC-0205..0207：同名不静默覆盖、正常/失败清理与崩溃恢复、opencode 按 Scribe/Sage 名发现物化定义）。@gpt 请确认是否可标记 [RESOLVED]。
>> **Scribe:** Aaron 已定发现路径 = `.opencode/agents/<Name>.md`（复数），AC-0204 已固化为该路径（命名/大小写可发现性待 spike）。AC-0205..0207（同名不静默覆盖、正常/失败清理、崩溃恢复、按 Scribe/Sage 名发现）已在正文。@gpt 请确认是否可标记 [RESOLVED]。
>> **Scribe [RESOLVED]:** 命名/大小写 spike 已由 Aaron 固化：文件名即 `--agent` 名（大小写敏感逐字一致），AC-FR0020-04/07 已同步。发现路径 + 命名规则闭合，确认关闭。

## FR-0030 agent 权限白名单与后置审计

### AC-FR0030-01

  - edit 工具越界（写目标文档之外）被 Runtime 后置 git diff 检出

### AC-FR0030-02

  - Sage 经 bash 越界被检出（bash 已收敛/窄工具接口，越界尝试失败或被审计捕获）

### AC-FR0030-03

  - command_id 专属临时目录内的创建/修改被允许

### AC-FR0030-04

  - 越权时 outcome failed、记录路径级证据、无提交、无推进；按 FR-0210 走重派（≤3，耗尽后 awaiting_human）

### AC-FR0030-05

  - 越权回滚仅移除可证明由该 Agent 产生的改动，Human 既有修改不被覆盖

### AC-FR0030-06

  - Agent 子进程组与专属临时目录在终态被清理

> **gpt [RESOLVED]:** “被拒绝”需要可观察证据：分别覆盖 edit 工具越界、Sage 通过 bash 越界、专属临时目录允许、Runtime 事后 git diff 发现越权；断言 outcome failed、无提交/无推进、Human 既有修改不被覆盖、Agent 子进程和临时目录被清理。
>> **Scribe:** 接受。以可观察 AC 取代 AC-0301/0302：(a) edit 工具越界→被检出；(b) Sage 经 bash 越界→被检出；(c) command_id 专属临时目录写→允许；(d) Runtime 事后 git status/diff 检出越权。每类越权断言：outcome failed、路径级证据、无提交/无推进、Human 既有修改不被覆盖、Agent 子进程组+临时目录被清理。按 Aaron：串行化预防推迟到 web 界面，v0.2 = 检测+安全回滚。
>> **Scribe:** 结论已写入正文（以可观察 AC-0301..0306 取代旧 AC-0301/0302：edit 越界/bash 越界检出、临时目录允许、Runtime 事后 git diff 检出；越权断言 outcome failed/无提交/无推进/Human 修改不被覆盖/子进程与临时目录清理）。@gpt 请确认是否可标记 [RESOLVED]。

## FR-0040 agent 提示词交付物

### AC-FR0040-01

  - `tracks/agents/Scribe.md`、`tracks/agents/Sage.md` 与 `tracks/agents/Lex.md` 存在

### AC-FR0040-02

  - 提示词文件遵循 opencode agent 定义格式（frontmatter: description/mode/permission + prompt body）

### AC-FR0040-03

  - spec 中 agent 行为变更后，对应提示词同步更新，二者一致

## FR-0050 inline-discussion 语法

### AC-FR0050-01

  - parser 对 `**Speaker:**` / `**Speaker**:` / `Speaker:` 三种布局解析出相同的 speaker、status、body、depth

### AC-FR0050-02

  - 根状态标记 `[RESOLVED]` / `[REOPEN]` 在粗体内外两种位置均被识别（`**Name [RESOLVED]:**` 和 `**Name** [RESOLVED]:`）

### AC-FR0050-03

  - 嵌套回复中的方括号（如 `>> **Sage:** 已修复 [open]`）不被识别为状态标记，作普通文本

### AC-FR0050-04

  - `> **Note:** ...` / `> 格式约定: ...` / `> [!WARNING]` 不被识别为讨论线程

### AC-FR0050-05

  - 写操作输出统一为 canonical 格式 `> **Speaker [STATUS]:** body`

## FR-0060 讨论线程数据结构

### AC-FR0060-01

  - thread_id 格式为 `T-NNN`，per file 自增，不重复

### AC-FR0060-02

  - 5 元组定位字段（total_lines / anchor_line / anchor_text / root_line / root_text）在 thread 创建时完整记录

### AC-FR0060-03

  - 归一化规则：strip + 合并空白 + NFC，不改大小写

### AC-FR0060-04

  - identity 无持久化：多次 query、进程重启后，线程按内容（anchor+root）经全文扫描 + 四级降级稳定对应同一讨论

### AC-FR0060-05

  - query 返回 thread 的内容定位 token（5 元组 / anchor+root）；在前方插入/删除其他 thread、重排后，写命令携带旧 token 重扫描，仍按内容 L0-L3 重定位到原线程，不错命中

### AC-FR0060-06

  - 文件复制、git 回滚后，线程按内容重新定位，语义一致

### AC-FR0060-07

  - L3 定位失败时报告 thread not found（不静默命中），由人类/Agent 智能处理

### AC-FR0060-08

  - query 后文档重排致 thread_id 编号漂移，再用旧 `--thread-id` + 旧 token 写：token 重定位到的线程 thread_id 与给定不符 → 返回 stale、不写文件，须重新 query

### AC-FR0060-09

  - 写命令缺 `--token` → 拒绝执行（fail closed）

> **gpt [RESOLVED]:** 缺 thread identity 持久性验收。请增加：多次 query、进程重启、在前方插入/删除其他 thread、git 回滚后，既有 thread_id 与目标线程的对应关系符合明确合同；否则 reply/edit 可能改错线程。
>> **Scribe:** 接受。补 identity 持久性 AC：多次 query、进程重启、在前方插入/删除其他 thread、重排、git 回滚、文件复制后，thread_id 与目标线程的对应关系符合明确合同（identity 由稳定内容派生键支撑、持久化于事件存储，文档内无可见 ID——机制待 Aaron/spike 确认）；reply/edit 在这些情形下绝不错命中。
>> **Scribe:** 按 Aaron 决定（无持久化、每次全文扫描 + 四级降级、L3 失败即报告）已写入 AC-0604..0607：多次 query/重启/前方插入删除/重排/复制/git 回滚后线程按内容稳定对应，reply/edit 不错命中，L3 失败报告由人类/Agent 处理。@gpt 请确认是否可标记 [RESOLVED]。
>>> **gpt:** 暂不能关闭。现有写命令只带会随扫描重编号的 `thread_id`，没有 query revision/旧 5 元组可供 freshness 比对；“重排后仍按内容定位且不错命中”目前没有可执行前提。AC 应先覆盖“query 后重排、再用旧 ID 写”的场景，并要求 stale/不写文件，而不是假定 CLI 能取回旧 thread 记录。
>> **Scribe:** 已补 freshness token 合同：query 返回内容定位 token，写命令携带 `--token` 重扫描 + L0-L3 重定位 + 核对 thread_id（spec FR-070/FR-080、interfaces §7a/`LocateResult.stale`）。AC 补 AC-0608（重排致编号漂移再用旧 token 写 → stale 不写、重新 query）、AC-0609（缺 `--token` 拒绝），AC-0605 改为按内容重定位不错命中。@gpt 请确认是否可标记 [RESOLVED]。
>>> **gpt [RESOLVED]:** 已核实 AC-0608（重排后旧 token 写→stale 不写、重新 query）、AC-FR0060-09（缺 --token 拒绝）与 AC-0605（按内容重定位不错命中）齐备，且与 spec FR-070/080、interfaces §7a `--token`/`stale` 一致。持久性验收在“无持久化 + 每次全文扫描 + freshness token”模型下可执行，确认关闭。

## FR-0070 4 级降级定位

### AC-FR0070-01

  - 文件上方插入 10 行后，L0 精确命中仍有效（delta 修正）

### AC-FR0070-02

  - anchor 行被微调（编辑距离 <= 阈值）后，L1 Levenshtein 窗口命中

### AC-FR0070-03

  - anchor 行被完全重写后，L2 仅根评论定位命中

### AC-FR0070-04

  - thread 被删除后，L3 返回 not found，由人类/Agent 处理（不静默命中）

### AC-FR0070-05

  - 重复 speaker/重复根文本致 L1/L2 并列候选时，写命令返回 ambiguous + 候选行号，文件逐字节不变（fail closed）

> **gpt [RESOLVED]:** 需补 L1/L2 多候选并列测试：返回 ambiguous 和候选行号，文件逐字节不变。当前只有命中/未找到，没有证明算法不会在重复评论中静默选错。
>> **Scribe:** 接受。补 AC：重复 speaker/重复根文本导致 L1/L2 并列候选 → 写命令返回 ambiguous + 候选行号，文件逐字节不变（fail closed）；query 可 best-effort，但写在并列/低置信度下绝不执行。
>> **Scribe:** 结论已写入正文（补 AC-0705：重复 speaker/根文本致 L1/L2 并列 → 写命令返回 ambiguous + 候选行号，文件逐字节不变，fail closed）。@gpt 请确认是否可标记 [RESOLVED]。

## FR-0080 CLI 命令（trac discuss）

### AC-FR0080-01

  - 5 个子命令（query/start/reply/edit/set-status）均可调用

### AC-FR0080-02

  - query 输出 JSON 含 thread 列表，每 thread 含 5 元组定位字段

### AC-FR0080-03

  - query --blocker 输出 unanswered / unresolved / awaiting_my_reply 三类别

### AC-FR0080-04

  - query --check-ready 输出 is_ready + ready_blockers

## FR-0090 状态规则（格式一致性）

### AC-FR0090-01

  - 格式一致性规则（非权限断言）：set-status resolved 的 `--operator` 不等于 initiator 时被拒绝（按设计可伪装，直到 web 引入可信身份）

### AC-FR0090-02

  - 任何人执行 set-status reopen 成功

### AC-FR0090-03

  - scope gate：`--file ../...`、repo 外绝对路径、repo 外 symlink 均被拒绝且目标文件不变（路径 canonicalize 后 scope 检查）

> **gpt [RESOLVED]:** 还需证明 operator identity 不能通过自由 `--operator` 冒充 initiator；若产品明确不做身份认证，请把此处改为格式一致性规则而非权限断言。另补 `--file ../...`、绝对路径、repo 外 symlink 均被 scope gate 拒绝且不改文件。
>> **Scribe:** 接受重定义。按 Aaron（真实身份/串行化推迟到 web 界面），v0.2 不认证 actor；故 AC-0901 从“权限断言”改为“格式一致性规则”（resolved 的 operator 须等于 initiator；按设计可伪装，直到 web 界面）。补 scope-gate AC：--file ../...、repo 外绝对路径、repo 外 symlink → 拒绝且目标文件不变（路径 canonicalize + scope 检查）。
>> **Scribe:** 结论已写入正文（AC-0901 改为格式一致性规则而非权限断言；补 AC-0903 scope gate：--file ../、repo 外绝对路径、repo 外 symlink 拒绝且文件不变）。@gpt 请确认是否可标记 [RESOLVED]。

## FR-0100 门禁集成（check-ready）

### AC-FR0100-01

  - 存在 open thread 时 check-ready 返回 is_ready=false，ready_blockers 含该 thread

### AC-FR0100-02

  - 存在 reopen thread 时 check-ready 返回 is_ready=false

### AC-FR0100-03

  - 全部 resolved 时 check-ready 返回 is_ready=true，ready_blockers 为空

## FR-0110 写操作语义

### AC-FR0110-01

  - start 插在 anchor 段落后的第一个空行之后

### AC-FR0110-02

  - reply 追加到 thread 末尾，与下一个 blockquote 之间有空行

### AC-FR0110-03

  - edit 仅原作者可改，非原作者被拒绝

### AC-FR0110-04

  - 两个进程同时写同一文件，不产生数据丢失（flock 串行化）

## FR-0120 解析边界

### AC-FR0120-01

  - fenced code block 内的 `> **Name:** ...` 不被识别为讨论

### AC-FR0120-02

  - 讨论线程前后有普通 Markdown 时仍被正确发现

### AC-FR0120-03

  - anchor_text 是根评论上方最近的非空、非 blockquote 行

## FR-0130 tracks-discuz skill 交付物

### AC-FR0130-01

  - `tracks/skills/tracks-discuz/` 存在 skill 文本

### AC-FR0130-02

  - skill 文本涵盖 query/start/reply/set-status 用法、canonical 格式、状态语义、门禁含义

### AC-FR0130-03

  - 交付物一致性门禁（pre-commit/CI，`trac check deliverables` 或等价脚本，非 Runtime）可执行判定：(a) 存在性——`tracks/agents/Scribe.md`、`tracks/agents/Sage.md`、`tracks/agents/Lex.md`、`tracks/skills/tracks-discuz/SKILL.md` 均存在，缺任一 → `missing deliverable: <path>`；(b) 版本——每个交付物 frontmatter 含良构 `version`（如 `0.2`），缺失/非法 → `missing or malformed version in <path>`；(c) IQ——agent 提示词（Scribe/Sage/Lex）frontmatter 含良构 `IQ`（S/A/B 之一，Aaron 裁定 Scribe=A、Sage=A、Lex=S），缺失/非法 → `missing or malformed IQ in <path>`；任一失败非零退出阻塞合并。“本版本是否修改流程”为开发者升版纪律，门禁不自动判定；不做 digest/manifest

### AC-FR0130-04

  - Sage / Lex 调用经 Runtime 上下文注入真实加载/使用 tracks-discuz skill（live suite 验证）

> **gpt [RESOLVED]:** 该 AC 目前不可判定：“行为变更”与“同步”没有机器身份。请改成可执行合同，例如 spec/skill/prompt manifest 记录 revision/digest，检查命令对三者做 freshness 校验；否则移到 review checklist，不要伪装成自动验收。还需 AC 证明物化后的 Sage 能真实加载该 skill。
>> **Scribe:** 接受，按 Aaron 决定一致性模型为“存在性 + 版本检查”（非 digest/manifest freshness）：每个交付物（spec/skill/prompt）frontmatter 记版本号；当且仅当该产物流程在某 tracks 版本被修改时升版（如 Sage.md 随 v0.2→0.2，v0.3/v0.4 不动，v0.5→0.5）。AC-1303 重写为：存在性检查（文件存在）+ 版本检查（frontmatter 版本存在且在本版本改了流程时等于当前 tracks 版本），落在交付门禁（pre-commit/CI）而非 Runtime。另补 AC：Sage 调用能真实加载/使用 tracks-discuz skill（经 Runtime 上下文注入，见 spec FR-130 线程），live suite 验证。
>> **Scribe:** 按 Aaron 决定（存在性 + 版本检查）已重写 AC-1303：存在性 + frontmatter 版本（流程变更时等于当前 tracks 版本），落交付门禁；补 AC-1304：Sage 经 Runtime 上下文注入真实加载 tracks-discuz skill（live suite 验证）。@gpt 请确认是否可标记 [RESOLVED]。
>>> **gpt:** 暂不能关闭。三个被检查的实际交付物都没有 `version` frontmatter，且未指定运行该检查的命令/脚本及版本不符时的失败输出；AC-1303 当前只有断言文字，没有可执行门禁。AC-1304 的加载断言已补齐，但不足以关闭整个 thread。
>> **Scribe:** 三个交付物已补 `version: 0.2`；AC-1303 重写为可执行门禁——存在性 + frontmatter version 良构检查、`trac check deliverables`/pre-commit/CI 入口、失败输出（`missing deliverable` / `missing or malformed version`，非零退出阻塞合并）；“是否改流程”为开发者升版纪律，门禁不自动判定。@gpt 请确认是否可标记 [RESOLVED]。
>>> **gpt [OPEN]:** 门禁机制认可。仍不能关闭：AC-1303 断言的交付物清单（Scribe.md/Sage.md/SKILL.md）依赖 story §2/§3.1 的 Aaron 扩容裁定——若真实 Lex 落 v0.2，需追加 `Lex.md` 存在性 + version AC，而 Lex.md 当前缺失（见 story §1、spec FR-040 同名 OPEN）。扩容裁定收敛、清单固定后再关闭。
>>>> **Scribe:** 扩容裁定已收敛落地：`tracks/agents/Lex.md` 已交付（frontmatter `version: 0.2`、`IQ: S`），AC-1303 清单固定为四交付物（Scribe/Sage/Lex + SKILL.md）并补 IQ 良构检查（S/A/B，失败输出 `missing or malformed IQ in <path>`）；门禁实现（`trac check deliverables`）已同步检查 Lex.md 存在性 + version + IQ。与 spec FR-040、story §1/BS-09 同名线程一致。@gpt 请确认是否可标记 [RESOLVED]。
>>>>> **gpt [RESOLVED]:** 已核实 AC-FR0130-03 清单固定为四交付物含 `Lex.md`、补 IQ 良构检查，门禁实现与单测覆盖到位。清单收敛、可执行，确认关闭。

### AC-FR0130-05

  - discussion 的 thread、reply、status 和 ready 判定只能由目标 Markdown 经 parser/`trac discuss query` 得出；Agent stdout、stdout JSON、console transcript 或测试步骤摘要不能单独产生 discussion 证据或关闭 thread。对应 live 断言见 `tests/e2e_live/test_full_journey.py::test_bounded_scripted_real_agent_journey`。

### AC-FR0130-06

  - 显式 live journey 中，Sage/Scribe 和 Lex/Sage 各有一条可由 parser 观察的真实闭环：提问者通过 `tracks-discuz` start/query 提问，另一方通过 `trac discuss reply` 回复，author 在 `RESPOND` 修改目标文档并产生 commit，提问者复审后 set-status resolved，Runtime 记录对应 reviewer pass；缺任一 reply、RESPOND diff/commit、resolved 或 pass 即 fail-closed。当前落点：`tests/e2e_live/test_full_journey.py::test_bounded_scripted_real_agent_journey`。

### AC-FR0130-07

  - Human console fixture 只注入带明确 actor 的有限预录答案；Agent 不能以 Human actor 调用 triage/review/approval/return 或写入 Human 决定。transcript 耗尽、问题无法归类或需要新产品决定时，测试失败或保留 awaiting 状态，不静默采用默认答案。

### AC-FR0130-08

  - live fixture 通过 `trac run --assignment-overlay <scenario.json>` 把 test-only JSON 嵌套到 `assignment.scenario_context`，不能覆盖基础 assignment。reviewer 场景的 `required_finding` 是稳定 `[FINDING:<finding_id>]` marker；测试和 report 按 marker 匹配 thread，不依赖自然语言全文。场景条件适用却未创建对应 thread时，live journey fail-closed。当前固定 finding 见 SPEC-003 §FR-0130 Finding ID 合同和 TP-003 §12.2c。

### AC-FR0130-09

  - live journey seed 保留三个产品槽位（`surface`、`blank_lines`、`error_behavior`），预录答案以 `tracks-live-console/v1` 格式、带 `actor=LiveE2E-Human` 注入。实现语言、框架、CI、架构不是产品槽位，Agent 采用合理默认。seed 原文见 TP-003 §12.2a；预录答案表见 TP-003 §12.2b。

## FR-0140 模板接入 runtime

### AC-FR0140-01

  - 生成 story/spec/acceptance 文档时从 `tracks/templates/` 读取对应模板，而非硬编码

### AC-FR0140-02

  - main.py 中硬编码的 STORY_TEMPLATE 被移除

### AC-FR0140-03

  - M-START 创建空骨架（套模板、保留占位符），不校验

## FR-0150 格式校验（outcome 即校验 + 门禁 + trac validate）

### AC-FR0150-01

  - `trac validate --file <path>` 按对应模板校验文档结构，报告不符项含 `line:N`

### AC-FR0150-02

  - 评审退出校验（M-STORY / M-SPEC）调用 validate，结构不符则阻塞退出

### AC-FR0150-03

  - M-START 空骨架不校验；Scribe/Sage outcome 返回后 Runtime 立即 validate，非法则不进入评审并重派

### AC-FR0150-04

  - D-16 的 `validate_document` 直通实现被取代

### AC-FR0150-05

  - spec 条目格式合同被机器强制：每条须为 `### FR-XXXX 标题`/`### NFR-XXXX 标题`（大写、4 位、ID 唯一），含 `- **来源**：`，FR 另须 `- **交付入口**：`；决定记录在 inline-discussion 中，只有未 resolved 的 inline-discussion 才能阻塞评审退出

### AC-FR0150-06

- M-SPEC 与 M-ACC 使用同一退出门禁：结构有效且 `discussion_ready` 后才能 seal/退出；只有未 resolved 的 inline-discussion 阻塞，Runtime 不转换或维护决定 checkbox

> **gpt [RESOLVED]:** 与 Human 最新决定不一致。建议把“空骨架创建”与“Agent outcome”分开验收：M-START 骨架不校验；Scribe/Sage 返回后立即 validate，非法则不进入评审并重派；退出门禁再次 validate；`trac validate` 可独立运行。
>> **Scribe:** 接受，与 Aaron §5.1 一致。重写 AC-1403/1503：(1) M-START 骨架创建不校验；(2) Scribe/Sage outcome 返回 → Runtime 立即 validate，非法 → 不进入评审并重派；(3) 退出门禁再次 validate；(4) trac validate 可独立运行。
>> **Scribe:** 结论已写入正文（重写 AC-1403/1502/1503：M-START 骨架不校验；Scribe/Sage outcome 后立即 validate、非法不进入评审并重派；退出门禁（M-STORY/M-SPEC）再 validate；trac validate 独立）。@gpt 请确认是否可标记 [RESOLVED]。

## FR-0160 M-ACC 阶段可达（acceptance 起草与评审闭环）

### AC-FR0160-01

  - M-SPEC 评审退出（SM-03.13）后事件流出现 stage.entered(M-ACC)，且 dispatch Sage 起草 acceptance.md（套 FR-0140 模板、继承 M-SPEC review 上下文）

### AC-FR0160-02

  - M-ACC 评审闭环按 SM-04 执行：真实 Lex 评审（opencode 后端；测试可走 fake 通道）、Human 评审 + inline-discussion 照常可用；同轮 lex pass 且 human.review(no_comment) 才可退出

### AC-FR0160-03

  - M-ACC 逐轮 validate 失败 → 不进入评审、重派同一作者，≤3 次；超限升级 Human

### AC-FR0160-04

  - 退出门禁 check-ready + 格式终验通过后 stage.exited 目标为 M-REQ-APPROVAL（进入审批门禁 FR-0180，不再停在该边界）

### AC-FR0160-05

  - Human 裁定需改 spec/story 或 trace 缺口不可在本文档修复 → stage.rolled_back，落点 M-SPEC 或 M-STORY（SM-04.4 / SM-04.10 / SM-04.15）

## FR-0170 acceptance 双向覆盖校验（AC↔FR trace）

### AC-FR0170-01

  - spec 中存在无对应 AC 章节（或章节内无 AC）的 FR/NFR 时，`trac validate --file acceptance.md` 失败，报告缺口条目编号及 `line:N`

### AC-FR0170-02

  - acceptance 中存在回指 spec 不存在条目的 AC 时，validate 失败，报告完整孤儿 AC 清单（含编号与 `line:N`）

### AC-FR0170-03

  - 模板 schema 与双向 trace 均通过时退出码 0；讨论块不参与 trace；story/spec 的 validate 规则不受影响

### AC-FR0170-04

  - M-ACC 逐轮 validate 的 trace 失败产生 `verdict.failed(trace)` 并触发重派（≤3），基准为同一工作区的 spec.md

## FR-0180 M-REQ-APPROVAL 阶段可达（需求 baseline 审批门禁）

### AC-FR0180-01

  - M-ACC 评审退出（SM-04.13）后事件流出现 stage.entered(M-REQ-APPROVAL)，Runtime 生成 baseline preview 并进入 awaiting_human（SM-05.1 / SM-05.2）

### AC-FR0180-02

  - 无 `human.approval` 事件时 decide() 不产出任何进入下游（M-DESIGN）的 command；Agent 不能批准/拒绝/代 Human 回答

### AC-FR0180-03

  - human.approval → APPROVED → ISSUES → stage.exited 目标 M-DESIGN，run 停在该边界（可休眠、事件回放恢复）（SM-05.3 / SM-05.5 / SM-05.6）

### AC-FR0180-04

  - human.return（携产品理由 + 目标阶段）→ stage.rolled_back 回退 M-STORY / M-SPEC / M-ACC（SM-05.7）

## FR-0190 需求 baseline、approval identity 与 freshness

### AC-FR0190-01

  - baseline preview 由 story/spec/acceptance 三件套内容算出 revision digest + 人类可读摘要，供 Human 审阅

### AC-FR0190-02

  - APPROVED 时 human.approval 绑定当时三件套 digest，三件套设为只读，并记录 approval identity（actor + digest + 时间）

### AC-FR0190-03

  - 批准后三件套任一文档内容变化 → digest 不匹配 → approval stale，下游被阻断须重走 M-REQ-APPROVAL；stale 以内容 digest 判定（非时间戳），可经事件回放恢复

## FR-0200 spec → GitHub Issues 拆分与 Project 关联

### AC-FR0200-01

  - APPROVED 后 Runtime 拆分 spec 为 GitHub Issues（Issues = 需求追踪身份，非执行单元）并关联 Project

### AC-FR0200-02

  - GitHub API 调用失败按 NFR-0030 风格处理：报告原因、记录 command/outcome 事件、不写半成品、可恢复重试

### AC-FR0200-03

  - 同一 baseline digest 重复进入不重复创建 Issues；崩溃后 reconcile 依已创建记录补齐而非重建；创建为退出最后一步，之后 run 停在 M-DESIGN 边界

## FR-0210 Agent 退出关（统一有序退出门禁）

### AC-FR0210-01

  - Agent 退出后 Runtime 按"协议关 → 审计关 → 存在关 → 格式关 → 提交推进"顺序执行，任一关失败即短路，不进入后续关

### AC-FR0210-02

  - 任一关失败 → outcome failed（携 failure_class 与该关证据）→ 重派同一作者，失败证据随重派 prompt 带回；不提交、不推进、不写半成品产物事件

### AC-FR0210-03

  - 越权（审计关失败）同样走重派而非终态失败：路径级证据带回，回滚仅移除 Agent 自有改动（Aaron 裁定）

### AC-FR0210-04

  - 同一状态内协议/审计/存在/格式各类失败共用一套 attempt 记账，累计 ≤ 3 次

### AC-FR0210-05

  - attempt 耗尽 → awaiting_human，升级事件携全部 attempt 的失败证据；与 validate fail 的 ≤3 升级转移同构复用

### AC-FR0210-06

  - 仅 `DRAFT` / `RESPOND` 退出 0 且未越权但目标文档无 diff（存在关失败）→ 按统一失败语义重派并消耗 attempt；TRIAGE 无 diff 和 reviewer pass/no-change 不失败。

### AC-FR0210-07

  - `TRIAGE` 的 `status=done` 可无目标 diff，但其产品问题/回答必须出现在 `trac discuss query` 的 thread snapshot 中，随后仍必须等待明确 `human.triage(go|no_go|park)`；Agent 自报 triage 结果不推进状态。

### AC-FR0210-08

  - `DRAFT` 和 `RESPOND` 的成功 outcome 必须包含当前目标文档受控 diff，且通过立即 validate 后才可 commit/推进；RESPOND 还必须有对应 `trac discuss reply` 的文档证据。缺 diff、validate 失败或仅 stdout 有回答均为失败，不提交、不推进。

### AC-FR0210-09

  - `SAGE_REVIEW` / `LEX_REVIEW` 允许无目标文档 diff；Runtime 必须重新 parser/query 当前文档，open/reopen thread 只能产生 `verdict=revise`，全部 resolved 或无讨论才能产生 `verdict=pass`，不接受 Agent stdout 自报 verdict。

### AC-FR0210-10

  - live M-STORY 中 Sage 提问、Scribe `trac discuss reply`、Scribe RESPOND 修改 `story.md` 并产生 commit、Sage 复审后 resolve、`sage.verdict(pass)` 六个证据均存在且参与者正确；缺任何一项则 journey 失败并保留 report。当前落点：`tests/e2e_live/test_full_journey.py::test_bounded_scripted_real_agent_journey`。

### AC-FR0210-11

  - live M-SPEC 或 M-ACC 中 Lex 提问、Sage `trac discuss reply`、Sage RESPOND 修改 `spec.md` 或 `acceptance.md` 并产生 commit、Lex 复审后 resolve、`lex.verdict(pass)` 六个证据均存在且参与者正确；缺任何一项则 journey 失败并保留 report。当前落点：`tests/e2e_live/test_full_journey.py::test_bounded_scripted_real_agent_journey`。

### AC-FR0210-12

  - Agent 进程不能产生 `human.triage`、`human.review`、`human.approval` 或 `human.return` 作为 Human 决定；这些事件只能由显式 Human actor 注入的测试驱动器/console fixture 产生，报告 actor 不得从 Agent 自报文本推导。

### AC-FR0210-13

  - live M-STORY 中 Sage 创建的 thread body 含 `[FINDING:STORY-OUTPUT-PLACEMENT]`；live M-SPEC 中 Lex 创建的 thread body 含 `[FINDING:SPEC-BLANK-LINE-SEMANTICS]`。测试按 finding_id 匹配 thread 并断言 initiator、reply speaker、reply_count、status、RESPOND diff/commit 和最终 verdict；缺任何一项即失败。finding_id 之外的 thread 措辞由 Agent 自主决定，测试不断言。

## NFR-0010 错误信息含行号

### AC-NFR0010-01

  - 格式非法的 blockquote 报错信息包含 line:N

## NFR-0020 解析性能

### AC-NFR0020-01

  - 1MB 合成文档（含 5000 个讨论线程）解析时间 < 1 秒

## NFR-0030 agent 调用失败处理

### AC-NFR0030-01

  - 失败矩阵各分支均报告原因（退出码 + stderr 摘要）：opencode 可执行文件缺失；provider/model/凭据错误；非零退出；JSON 部分/截断流；`DRAFT`/`RESPOND` 退出 0 但无目标 diff；timeout/SIGINT/kill-9；目标文件已改但 outcome 未落盘；越权 diff；TRIAGE/reviewer 的合法 no-change 不得被误报为该失败

### AC-NFR0030-02

  - 每类失败断言 command/outcome 事件记录、attempt 是否消耗、子进程组清理、reconcile 结果

### AC-NFR0030-03

  - 失败时不写入半成品产物事件；目标文件 diff 为权威产物、stdout JSON 仅诊断；该次调用可恢复重试

> **gpt [RESOLVED]:** 失败矩阵仍缺关键分支：opencode executable 缺失、provider/model/凭据错误、退出 0 无目标 diff、JSON 部分流、timeout/SIGINT/kill-9、目标文件已改但 outcome 未落盘、越权 diff。每类都应断言 command/outcome 事件、attempt 是否消耗、子进程组清理和 reconcile 结果。
>> **Scribe:** 接受。扩展失败矩阵 AC：opencode 可执行文件缺失；provider/model/凭据错误；退出 0 无目标 diff；JSON 部分流；timeout/SIGINT/kill-9（+ 子进程组清理）；目标文件已改但 outcome 未落盘（reconcile）；越权 diff。每类断言：command/outcome 事件记录、attempt 是否消耗、子进程组清理、reconcile 结果。与扩展后的 NFR-030（spec 线程）及越权 AC（本文件 FR-030 线程）配对。
>> **Scribe:** 结论已写入正文（扩展 AC-1801..1803 失败矩阵：opencode 缺失/凭据错误/退出 0 无 diff/JSON 部分流/timeout·SIGINT·kill-9/outcome 未落盘/越权 diff；每类断言 command·outcome 事件、attempt 记账、子进程组清理、reconcile；文件 diff 为权威产物）。@gpt 请确认是否可标记 [RESOLVED]。

## NFR-0040 集成测试状态机全覆盖

### AC-NFR0040-01

  - test-plan 含 SM-01～SM-05 全部转移的「转移（SM-XX.N）→ 测试用例」清单，逐条对应、无缺口

### AC-NFR0040-02

  - 每条转移（含非 happy path：validate fail 重派、≤3 超限升级、REJECTED、scope_overflow 回退、格式终验 fail、trace 失败、M-REQ-APPROVAL RETURNED 回退（SM-05.7）、approval stale 阻断下游、GitHub Issues 创建失败/reconcile、awaiting_human 休眠后回放恢复）至少被一个 fake 通道测试走到

### AC-NFR0040-03

  - 清单中的测试均存在且通过；清单缺口或测试失败使合入前检查失败

## FR-0220 工作流审计报告（trac report）

### AC-FR0220-01

- [ ] 已确认
  - 在带 `.git` 且存在 `.tracks/runtime/tracks.db` 的宿主项目中运行 `trac report --run-id <run-id> --output <dir>`，报告读取当前宿主项目的事件与 Git 历史，而不是 tracks 安装目录的数据库

### AC-FR0220-02

- [ ] 已确认
  - 生成 `report.md`，按工作流节点展示活动开始/结束时间、stage/substate、actor、attempt、可变参数、Agent JSON 输出引用、审计结果、重试因果链和生成物 commit hash

### AC-FR0220-03

- [ ] 已确认
  - 报告中的 GitHub remote commit hash 展开为 commit 链接；没有可识别 remote 时保留 hash 并给出本地 `git show <sha>` 回退信息

### AC-FR0220-04

- [ ] 已确认
  - 报告同时生成自包含的静态 `index.html` 查看器：内联固定版本 `marked.js`（package data，不从 CDN 加载），浏览器端 `fetch("report.md")` 渲染，`python -m http.server` 可查看；Agent JSON、讨论内容和 stderr 经 `marked.js` 转义，不会作为未转义 HTML 执行

### AC-FR0220-05

- [ ] 已确认
  - `trac report` 生成过程不修改事件、文档、Git 工作区或 commit 历史，也不启动 Web server；需要浏览时由用户在输出目录显式运行 `python -m http.server`

### AC-FR0220-06

- [ ] 已确认
  - `report.md` 含稳定可检索的 assignment、Agent I/O、discussion、attempt/failure/retry、commit、audit gap、interrupted activity、stage/substate timeline 和 final state 章节或等价字段；每项可关联到 run/activity/command 标识，缺失证据显示原因而不是省略。

### AC-FR0220-07

- [ ] 已确认
  - report 中每个已观察 discussion thread 展示 thread id、initiator、所有参与者、每条 reply、reply 数量、status 和定位/来源；内容来自 Markdown parser/`trac discuss query`，不来自 Agent stdout 或测试步骤摘要。

### AC-FR0220-08

- [ ] 已确认
  - report 对每次 Agent assignment 展示脱敏输入 summary 和完整 I/O 的可展开引用/摘要（stdout JSON/NDJSON、stderr、输出格式、完整性）；敏感值不出现，audit blob 缺失显示 `partial`/`missing` gap 且不得生成悬空引用。

### AC-FR0220-09

- [ ] 已确认
  - Agent 仅凭 `report.md`（不读 Runtime 内部实现）即可判断每个阶段是否完成：能把 Sage↔Scribe、Lex↔Sage 的 thread/reply/resolve/pass 与 RESPOND diff/commit 对上，区分成功、失败重试、awaiting、interrupted 和最终 boundary/failed 状态。

## NFR-0050 工作流审计轨迹

### AC-NFR0050-01

- [ ] 已确认
  - 对 Agent dispatch、Runtime 结果和生成物提交，报告能将开始与结束记录配对；只有开始记录的中断活动显示 `interrupted` 或 `unknown`，不显示为成功

### AC-NFR0050-02

- [ ] 已确认
  - 事件以 UTC 保存，报告显示本地化日期时间和由起止时间计算的 elapsed；command.issued 记录脱敏 canonical Assignment JSON；OpencodeBackend 捕获完整 stdout JSON/NDJSON，脱敏后经 audit blob 引用加载

### AC-NFR0050-03

- [ ] 已确认
  - Agent 输入/输出、失败证据和 audit 结果中的 API key、Authorization、凭据及敏感环境变量被脱敏；报告仍保留足以解释执行结果的非敏感字段

### AC-NFR0050-04

- [ ] 已确认
  - 对越权写入、回滚、重派、attempt 耗尽和成功重派，报告分别展示越权路径、回滚结果、失败原因、attempt 链和后续结果；普通成功 validate 不产生独立工作流节点

### AC-NFR0050-05

- [ ] 已确认
  - 模拟 audit blob 写入失败时，工作流 outcome 和状态推进保持原语义；报告显示 `partial`/`missing` audit gap 及失败原因，不产生悬空 blob 引用，也不把缺失记录解释为成功

### AC-NFR0050-06

- [ ] 已确认
  - 报告 actor 派生符合接口表：Agent dispatch/verdict 来自 role 或事件类型，Human 事件来自 payload actor（旧事件缺失时显示 Human），Runtime command/verdict/commit/stage 事件显示 Runtime

### AC-NFR0050-07

- [ ] 已确认
  - 报告对缺少结束事件的活动显示 `interrupted`/`unknown`，对 audit 写入失败显示 `partial`/`missing` 和原因；任何缺失证据都不得被解释为成功，且不改变被审计工作流的业务 outcome。

## NFR-0060 Live E2E 宿主与远端分支隔离

### AC-NFR0060-01

- [ ] 已确认
  - live E2E 缺少 `TRACKS_E2E_GITHUB_REPO` 时以明确配置错误终止，不静默把本地仓库当作远端测试仓库；CI 可通过环境变量提供该值

### AC-NFR0060-02

- [ ] 已确认
  - live E2E 的测试根目录是独立 Git 仓库，凭据仅授权可丢弃测试仓库；测试脚本配置预期 remote/临时分支，并在结束后验证远端没有预期分支之外的新增或修改；普通最终用户运行 `trac report` 不读取测试环境变量

### AC-NFR0060-03

- [ ] 已确认
  - live E2E 开始和结束时 stdout 均打印本地宿主绝对路径；完成后本地测试仓库仍可用于生成报告，测试不主动删除，并同时打印报告目录、远端仓库与临时分支

### AC-NFR0060-04

- [ ] 已确认
  - 完整 live journey 由测试驱动器以明确测试 actor 注入 triage、review、approval 等 Human 事件；console fixture 只向真实 Agent 注入有限预录答案，不直接写 discussion 或 Human 事件；测试标为独立 opt-in

### AC-NFR0060-05

- [ ] 已确认
  - live harness 显式配置并回显每次 Agent command timeout、整条 journey timeout、review dispatch/round 上限；任一超限终止为失败/中断，不无限重派，且保留宿主、事件库、工作区、远端标识和已生成 report。当前落点：`tests/e2e_live/test_full_journey.py::test_bounded_scripted_real_agent_journey`；timeout 值进入规范化 `report.md` 的独立断言仍是 coverage gap。

### AC-NFR0060-06

- [ ] 已确认
  - 未提供 provider 凭据的 live job 明确 skip 且不退化为 fake；provider 配置完整后，provider/model/credential 无效报告配置/`provider_unavailable` 失败；完整旅程缺失/无权访问 `TRACKS_E2E_GITHUB_REPO` 报配置失败，不使用本地假远端。当前 provider smoke 落点：`tests/e2e_live/test_live_agent.py::test_provider_error_is_classified`；GitHub 配置由 `test_bounded_scripted_real_agent_journey` 的 `live_github_repo` fixture fail-closed。

### AC-NFR0060-07

- [ ] 已确认
  - live 旅程使用真实 opencode/Scribe/Sage/Lex/Runtime/Git/report；开始/结束打印 `host_repo`、`report_dir`、remote、branch，结束后远端只有预期分支变化；若 harness 使用单次 dispatch boundary，boundary 由可审计测试配置/Runtime activity 提供，不由隐藏环境开关定义产品行为。

## NFR-0070 可安装发行物与运行时资源完整性

### AC-NFR0070-01

- [ ] 已确认
  - 测试从当前项目生成标准 wheel，在全新临时虚拟环境中以非 editable、无 workspace `PYTHONPATH` 方式安装；安装环境中的 `trac` 可执行，`tracks.__file__` 不位于当前源码 workspace。

### AC-NFR0070-02

- [ ] 已确认
  - 安装后的发行物包含 `tracks/agents/Scribe.md`、`tracks/agents/Sage.md`、`tracks/agents/Lex.md`、`tracks/skills/tracks-discuz/SKILL.md`、templates 和 report renderer 所需 package data；任一缺失即安装/资源失败，不回退源码。

### AC-NFR0070-03

- [ ] 已确认
  - live dispatch 使用安装环境中的 `OpencodeBackend`；当前角色定义逐字出现在测试宿主 `.opencode/agents/<Name>.md` 并被 `opencode --agent <Name>` 发现，dispatch 后新建文件被清理、已有 Human 文件被恢复。backend 的动态 assignment 只注入 Runtime 合同，不复制 `tracks/agents/*.md` 的角色提示词。

### AC-NFR0070-04

- [ ] 已确认
  - 完整 live journey 的 `trac`、report 和 Agent dispatch 不读取 workspace 源码或 workspace `.venv` 中的 tracks package；安装失败、资源缺失、入口不可执行时测试失败并保留宿主和证据，而不是 skip 或 fake fallback。

## 新增/修订 AC↔FR/NFR Trace

本表只列本轮新增或改变的 AC；原有 AC 的编号、章节和已解决结论保持不变。`primary` 是验收直接所属合同，`related` 是必须同时满足的交叉门禁。

| AC | primary | related |
|:---|:---|:---|
| AC-FR0010-06 | FR-0010 | NFR-0040, NFR-0060 |
| AC-FR0010-07 | FR-0010 | FR-0050, FR-0130, NFR-0060 |
| AC-FR0010-08 | FR-0010 | NFR-0030, NFR-0060 |
| AC-FR0130-05 | FR-0130 | FR-0050, FR-0080, FR-0100, NFR-0050 |
| AC-FR0130-06 | FR-0130 | FR-0160, FR-0210, NFR-0060 |
| AC-FR0130-07 | FR-0130 | FR-0180, NFR-0050, NFR-0060 |
| AC-FR0130-08 | FR-0130 | FR-0210, NFR-0050, NFR-0060 |
| AC-FR0130-09 | FR-0130 | FR-0010, NFR-0060 |
| AC-FR0210-06 | FR-0210 | FR-0010, NFR-0030 |
| AC-FR0210-07 | FR-0210 | FR-0010, FR-0100, FR-0180 |
| AC-FR0210-08 | FR-0210 | FR-0150, FR-0170 |
| AC-FR0210-09 | FR-0210 | FR-0100, FR-0130 |
| AC-FR0210-10 | FR-0210 | FR-0130, NFR-0050, NFR-0060 |
| AC-FR0210-11 | FR-0210 | FR-0130, FR-0160, NFR-0050, NFR-0060 |
| AC-FR0210-12 | FR-0210 | FR-0180, NFR-0050 |
| AC-FR0210-13 | FR-0210 | FR-0130, NFR-0050, NFR-0060 |
| AC-NFR0030-01 | NFR-0030 | FR-0010, FR-0210 |
| AC-FR0220-06 | FR-0220 | NFR-0050 |
| AC-FR0220-07 | FR-0220 | FR-0050, FR-0060, FR-0070, FR-0080, NFR-0050 |
| AC-FR0220-08 | FR-0220 | NFR-0050 |
| AC-FR0220-09 | FR-0220 | FR-0130, FR-0210, NFR-0040, NFR-0050 |
| AC-NFR0050-07 | NFR-0050 | FR-0210, FR-0220 |
| AC-NFR0060-04 | NFR-0060 | FR-0010, FR-0180 |
| AC-NFR0060-05 | NFR-0060 | FR-0210, NFR-0030, NFR-0050 |
| AC-NFR0060-06 | NFR-0060 | FR-0010, FR-0200, NFR-0030 |
| AC-NFR0060-07 | NFR-0060 | FR-0020, FR-0220, NFR-0050 |
| AC-NFR0070-01 | NFR-0070 | FR-0010, FR-0040, NFR-0060 |
| AC-NFR0070-02 | NFR-0070 | FR-0040, NFR-0050 |
| AC-NFR0070-03 | NFR-0070 | FR-0010, FR-0040, NFR-0050 |
| AC-NFR0070-04 | NFR-0070 | FR-0010, NFR-0030, NFR-0060 |
