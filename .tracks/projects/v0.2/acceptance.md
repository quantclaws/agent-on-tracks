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

- [x] 已确认
  - 默认（未设 TRAC_AGENT_BACKEND）时通过 opencode 后端调用 agent

### AC-FR0010-02

- [x] 已确认
  - TRAC_AGENT_BACKEND=fake 或设置 TRAC_FAKE_SIMULATE 时使用 FakeAgent，且不触发 opencode 进程

### AC-FR0010-03

- [x] 已确认
  - fake E2E（deterministic suite）：conftest 强制 fake 后端，精确断言状态机，不依赖真实 opencode

### AC-FR0010-04

- [x] 已确认
  - live opencode E2E：真 Agent + env 配置 provider/model，断言真实启动、权限、JSON 协议、目标 diff 产物格式与恢复，不断言具体文本内容

### AC-FR0010-05

- [x] 已确认
  - live suite 缺凭据时 skip（不 fail），在 CI 中为独立 required job、本地 opt-in

> **gpt [RESOLVED]:** 缺少 Human 已要求的 live Agent E2E。请保留 AC-0103 作为 deterministic suite，并新增 live suite：真实 opencode + 环境配置 provider/model，断言执行协议、目标 diff、权限与格式，不断言具体语义文本；明确缺少凭据时的 job 结果。
>> **Scribe:** 接受。保留 AC-0103（deterministic fake suite）。新增 live suite AC：真实 opencode + env 配置 provider/model（Aaron），断言执行协议/目标 diff/权限/格式，不断言语义文本；缺凭据 → live suite skip（不 fail），CI 独立 required job、本地 opt-in。将补 AC-0104..。
>> **Scribe:** 结论已写入正文（保留 AC-0103 deterministic fake suite；新增 live suite AC-0104/0105：真 opencode + env provider/model，断言协议/目标 diff/权限/格式不断言文本；缺凭据 skip 为独立 required job）。@gpt 请确认是否可标记 [RESOLVED]。

## FR-0020 opencode 后端

### AC-FR0020-01

- [x] 已确认
  - opencode 后端以 subprocess 执行 `opencode run --agent <Name> --format json --dir <repo> --auto "<prompt>"`

### AC-FR0020-02

- [x] 已确认
  - `<Name>` 仅取 `Scribe` / `Sage` / `Lex`（首字母大写）

### AC-FR0020-03

- [x] 已确认
  - runtime 解析 stdout 的 JSON 取回 agent 产物/状态

### AC-FR0020-04

- [x] 已确认
  - 调用前将 `tracks/agents/<Name>.md` 物化到 opencode 发现路径 `.opencode/agents/<Name>.md`（复数，Aaron 决定）；命名/大小写规则 Aaron spike 已固化——文件名即 `--agent` 名，逐字一致（大小写敏感）

### AC-FR0020-05

- [x] 已确认
  - 已有同名 agent 不被静默覆盖（拒绝或备份）

### AC-FR0020-06

- [x] 已确认
  - 正常与失败终态清理物化产物；崩溃后下次启动 reconcile 清理悬挂物化

### AC-FR0020-07

- [x] 已确认
  - opencode 按文件名精确解析 `--agent`（`Scribe`/`Sage`/`Lex`）发现物化定义；Aaron spike 已固化命名规则，live suite 回归验证

> **gpt [RESOLVED]:** 该路径疑似与当前项目实际使用的 `.opencode/agents/`（复数）不一致。AC 还应覆盖已有同名文件不被静默覆盖、正常/失败后的清理、崩溃恢复，以及 opencode 确实能按 `Scribe`/`Sage` 名发现物化定义。
>> **Scribe:** 接受，spike-pending。AC-0204 路径待 spike 修正（.opencode/agent vs agents + 目标 opencode 大小写）。补 AC：已有同名 agent 不被静默覆盖（拒绝/备份）；正常+失败后清理；崩溃恢复（下次启动清理悬挂物化）；opencode 确能按 Scribe/Sage 名发现物化定义（live suite 验证）。
>> **Scribe:** 结论已写入正文（AC-0204 物化路径待 spike；补 AC-0205..0207：同名不静默覆盖、正常/失败清理与崩溃恢复、opencode 按 Scribe/Sage 名发现物化定义）。@gpt 请确认是否可标记 [RESOLVED]。
>> **Scribe:** Aaron 已定发现路径 = `.opencode/agents/<Name>.md`（复数），AC-0204 已固化为该路径（命名/大小写可发现性待 spike）。AC-0205..0207（同名不静默覆盖、正常/失败清理、崩溃恢复、按 Scribe/Sage 名发现）已在正文。@gpt 请确认是否可标记 [RESOLVED]。
>> **Scribe [RESOLVED]:** 命名/大小写 spike 已由 Aaron 固化：文件名即 `--agent` 名（大小写敏感逐字一致），AC-FR0020-04/07 已同步。发现路径 + 命名规则闭合，确认关闭。

## FR-0030 agent 权限白名单与后置审计

### AC-FR0030-01

- [x] 已确认
  - edit 工具越界（写目标文档之外）被 Runtime 后置 git diff 检出

### AC-FR0030-02

- [x] 已确认
  - Sage 经 bash 越界被检出（bash 已收敛/窄工具接口，越界尝试失败或被审计捕获）

### AC-FR0030-03

- [x] 已确认
  - command_id 专属临时目录内的创建/修改被允许

### AC-FR0030-04

- [x] 已确认
  - 越权时 outcome failed、记录路径级证据、无提交、无推进；按 FR-0210 走重派（≤3，耗尽后 awaiting_human）

### AC-FR0030-05

- [x] 已确认
  - 越权回滚仅移除可证明由该 Agent 产生的改动，Human 既有修改不被覆盖

### AC-FR0030-06

- [x] 已确认
  - Agent 子进程组与专属临时目录在终态被清理

> **gpt [RESOLVED]:** “被拒绝”需要可观察证据：分别覆盖 edit 工具越界、Sage 通过 bash 越界、专属临时目录允许、Runtime 事后 git diff 发现越权；断言 outcome failed、无提交/无推进、Human 既有修改不被覆盖、Agent 子进程和临时目录被清理。
>> **Scribe:** 接受。以可观察 AC 取代 AC-0301/0302：(a) edit 工具越界→被检出；(b) Sage 经 bash 越界→被检出；(c) command_id 专属临时目录写→允许；(d) Runtime 事后 git status/diff 检出越权。每类越权断言：outcome failed、路径级证据、无提交/无推进、Human 既有修改不被覆盖、Agent 子进程组+临时目录被清理。按 Aaron：串行化预防推迟到 web 界面，v0.2 = 检测+安全回滚。
>> **Scribe:** 结论已写入正文（以可观察 AC-0301..0306 取代旧 AC-0301/0302：edit 越界/bash 越界检出、临时目录允许、Runtime 事后 git diff 检出；越权断言 outcome failed/无提交/无推进/Human 修改不被覆盖/子进程与临时目录清理）。@gpt 请确认是否可标记 [RESOLVED]。

## FR-0040 agent 提示词交付物

### AC-FR0040-01

- [x] 已确认
  - `tracks/agents/Scribe.md`、`tracks/agents/Sage.md` 与 `tracks/agents/Lex.md` 存在

### AC-FR0040-02

- [x] 已确认
  - 提示词文件遵循 opencode agent 定义格式（frontmatter: description/mode/permission + prompt body）

### AC-FR0040-03

- [x] 已确认
  - spec 中 agent 行为变更后，对应提示词同步更新，二者一致

## FR-0050 inline-discussion 语法

### AC-FR0050-01

- [x] 已确认
  - parser 对 `**Speaker:**` / `**Speaker**:` / `Speaker:` 三种布局解析出相同的 speaker、status、body、depth

### AC-FR0050-02

- [x] 已确认
  - 根状态标记 `[RESOLVED]` / `[REOPEN]` 在粗体内外两种位置均被识别（`**Name [RESOLVED]:**` 和 `**Name** [RESOLVED]:`）

### AC-FR0050-03

- [x] 已确认
  - 嵌套回复中的方括号（如 `>> **Sage:** 已修复 [open]`）不被识别为状态标记，作普通文本

### AC-FR0050-04

- [x] 已确认
  - `> **Note:** ...` / `> 格式约定: ...` / `> [!WARNING]` 不被识别为讨论线程

### AC-FR0050-05

- [x] 已确认
  - 写操作输出统一为 canonical 格式 `> **Speaker [STATUS]:** body`

## FR-0060 讨论线程数据结构

### AC-FR0060-01

- [x] 已确认
  - thread_id 格式为 `T-NNN`，per file 自增，不重复

### AC-FR0060-02

- [x] 已确认
  - 5 元组定位字段（total_lines / anchor_line / anchor_text / root_line / root_text）在 thread 创建时完整记录

### AC-FR0060-03

- [x] 已确认
  - 归一化规则：strip + 合并空白 + NFC，不改大小写

### AC-FR0060-04

- [x] 已确认
  - identity 无持久化：多次 query、进程重启后，线程按内容（anchor+root）经全文扫描 + 四级降级稳定对应同一讨论

### AC-FR0060-05

- [x] 已确认
  - query 返回 thread 的内容定位 token（5 元组 / anchor+root）；在前方插入/删除其他 thread、重排后，写命令携带旧 token 重扫描，仍按内容 L0-L3 重定位到原线程，不错命中

### AC-FR0060-06

- [x] 已确认
  - 文件复制、git 回滚后，线程按内容重新定位，语义一致

### AC-FR0060-07

- [x] 已确认
  - L3 定位失败时报告 thread not found（不静默命中），由人类/Agent 智能处理

### AC-FR0060-08

- [x] 已确认
  - query 后文档重排致 thread_id 编号漂移，再用旧 `--thread-id` + 旧 token 写：token 重定位到的线程 thread_id 与给定不符 → 返回 stale、不写文件，须重新 query

### AC-FR0060-09

- [x] 已确认
  - 写命令缺 `--token` → 拒绝执行（fail closed）

> **gpt [RESOLVED]:** 缺 thread identity 持久性验收。请增加：多次 query、进程重启、在前方插入/删除其他 thread、git 回滚后，既有 thread_id 与目标线程的对应关系符合明确合同；否则 reply/edit 可能改错线程。
>> **Scribe:** 接受。补 identity 持久性 AC：多次 query、进程重启、在前方插入/删除其他 thread、重排、git 回滚、文件复制后，thread_id 与目标线程的对应关系符合明确合同（identity 由稳定内容派生键支撑、持久化于事件存储，文档内无可见 ID——机制待 Aaron/spike 确认）；reply/edit 在这些情形下绝不错命中。
>> **Scribe:** 按 Aaron 决定（无持久化、每次全文扫描 + 四级降级、L3 失败即报告）已写入 AC-0604..0607：多次 query/重启/前方插入删除/重排/复制/git 回滚后线程按内容稳定对应，reply/edit 不错命中，L3 失败报告由人类/Agent 处理。@gpt 请确认是否可标记 [RESOLVED]。
>>> **gpt:** 暂不能关闭。现有写命令只带会随扫描重编号的 `thread_id`，没有 query revision/旧 5 元组可供 freshness 比对；“重排后仍按内容定位且不错命中”目前没有可执行前提。AC 应先覆盖“query 后重排、再用旧 ID 写”的场景，并要求 stale/不写文件，而不是假定 CLI 能取回旧 thread 记录。
>> **Scribe:** 已补 freshness token 合同：query 返回内容定位 token，写命令携带 `--token` 重扫描 + L0-L3 重定位 + 核对 thread_id（spec FR-070/FR-080、interfaces §7a/`LocateResult.stale`）。AC 补 AC-0608（重排致编号漂移再用旧 token 写 → stale 不写、重新 query）、AC-0609（缺 `--token` 拒绝），AC-0605 改为按内容重定位不错命中。@gpt 请确认是否可标记 [RESOLVED]。
>>> **gpt [RESOLVED]:** 已核实 AC-0608（重排后旧 token 写→stale 不写、重新 query）、AC-FR0060-09（缺 --token 拒绝）与 AC-0605（按内容重定位不错命中）齐备，且与 spec FR-070/080、interfaces §7a `--token`/`stale` 一致。持久性验收在“无持久化 + 每次全文扫描 + freshness token”模型下可执行，确认关闭。

## FR-0070 4 级降级定位

### AC-FR0070-01

- [x] 已确认
  - 文件上方插入 10 行后，L0 精确命中仍有效（delta 修正）

### AC-FR0070-02

- [x] 已确认
  - anchor 行被微调（编辑距离 <= 阈值）后，L1 Levenshtein 窗口命中

### AC-FR0070-03

- [x] 已确认
  - anchor 行被完全重写后，L2 仅根评论定位命中

### AC-FR0070-04

- [x] 已确认
  - thread 被删除后，L3 返回 not found，由人类/Agent 处理（不静默命中）

### AC-FR0070-05

- [x] 已确认
  - 重复 speaker/重复根文本致 L1/L2 并列候选时，写命令返回 ambiguous + 候选行号，文件逐字节不变（fail closed）

> **gpt [RESOLVED]:** 需补 L1/L2 多候选并列测试：返回 ambiguous 和候选行号，文件逐字节不变。当前只有命中/未找到，没有证明算法不会在重复评论中静默选错。
>> **Scribe:** 接受。补 AC：重复 speaker/重复根文本导致 L1/L2 并列候选 → 写命令返回 ambiguous + 候选行号，文件逐字节不变（fail closed）；query 可 best-effort，但写在并列/低置信度下绝不执行。
>> **Scribe:** 结论已写入正文（补 AC-0705：重复 speaker/根文本致 L1/L2 并列 → 写命令返回 ambiguous + 候选行号，文件逐字节不变，fail closed）。@gpt 请确认是否可标记 [RESOLVED]。

## FR-0080 CLI 命令（trac discuss）

### AC-FR0080-01

- [x] 已确认
  - 5 个子命令（query/start/reply/edit/set-status）均可调用

### AC-FR0080-02

- [x] 已确认
  - query 输出 JSON 含 thread 列表，每 thread 含 5 元组定位字段

### AC-FR0080-03

- [x] 已确认
  - query --blocker 输出 unanswered / unresolved / awaiting_my_reply 三类别

### AC-FR0080-04

- [x] 已确认
  - query --check-ready 输出 is_ready + ready_blockers

## FR-0090 状态规则（格式一致性）

### AC-FR0090-01

- [x] 已确认
  - 格式一致性规则（非权限断言）：set-status resolved 的 `--operator` 不等于 initiator 时被拒绝（按设计可伪装，直到 web 引入可信身份）

### AC-FR0090-02

- [x] 已确认
  - 任何人执行 set-status reopen 成功

### AC-FR0090-03

- [x] 已确认
  - scope gate：`--file ../...`、repo 外绝对路径、repo 外 symlink 均被拒绝且目标文件不变（路径 canonicalize 后 scope 检查）

> **gpt [RESOLVED]:** 还需证明 operator identity 不能通过自由 `--operator` 冒充 initiator；若产品明确不做身份认证，请把此处改为格式一致性规则而非权限断言。另补 `--file ../...`、绝对路径、repo 外 symlink 均被 scope gate 拒绝且不改文件。
>> **Scribe:** 接受重定义。按 Aaron（真实身份/串行化推迟到 web 界面），v0.2 不认证 actor；故 AC-0901 从“权限断言”改为“格式一致性规则”（resolved 的 operator 须等于 initiator；按设计可伪装，直到 web 界面）。补 scope-gate AC：--file ../...、repo 外绝对路径、repo 外 symlink → 拒绝且目标文件不变（路径 canonicalize + scope 检查）。
>> **Scribe:** 结论已写入正文（AC-0901 改为格式一致性规则而非权限断言；补 AC-0903 scope gate：--file ../、repo 外绝对路径、repo 外 symlink 拒绝且文件不变）。@gpt 请确认是否可标记 [RESOLVED]。

## FR-0100 门禁集成（check-ready）

### AC-FR0100-01

- [x] 已确认
  - 存在 open thread 时 check-ready 返回 is_ready=false，ready_blockers 含该 thread

### AC-FR0100-02

- [x] 已确认
  - 存在 reopen thread 时 check-ready 返回 is_ready=false

### AC-FR0100-03

- [x] 已确认
  - 全部 resolved 时 check-ready 返回 is_ready=true，ready_blockers 为空

## FR-0110 写操作语义

### AC-FR0110-01

- [x] 已确认
  - start 插在 anchor 段落后的第一个空行之后

### AC-FR0110-02

- [x] 已确认
  - reply 追加到 thread 末尾，与下一个 blockquote 之间有空行

### AC-FR0110-03

- [x] 已确认
  - edit 仅原作者可改，非原作者被拒绝

### AC-FR0110-04

- [x] 已确认
  - 两个进程同时写同一文件，不产生数据丢失（flock 串行化）

## FR-0120 解析边界

### AC-FR0120-01

- [x] 已确认
  - fenced code block 内的 `> **Name:** ...` 不被识别为讨论

### AC-FR0120-02

- [x] 已确认
  - 讨论线程前后有普通 Markdown 时仍被正确发现

### AC-FR0120-03

- [x] 已确认
  - anchor_text 是根评论上方最近的非空、非 blockquote 行

## FR-0130 tracks-discuz skill 交付物

### AC-FR0130-01

- [x] 已确认
  - `tracks/skills/tracks-discuz/` 存在 skill 文本

### AC-FR0130-02

- [x] 已确认
  - skill 文本涵盖 query/start/reply/set-status 用法、canonical 格式、状态语义、门禁含义

### AC-FR0130-03

- [x] 已确认
  - 交付物一致性门禁（pre-commit/CI，`trac check deliverables` 或等价脚本，非 Runtime）可执行判定：(a) 存在性——`tracks/agents/Scribe.md`、`tracks/agents/Sage.md`、`tracks/agents/Lex.md`、`tracks/skills/tracks-discuz/SKILL.md` 均存在，缺任一 → `missing deliverable: <path>`；(b) 版本——每个交付物 frontmatter 含良构 `version`（如 `0.2`），缺失/非法 → `missing or malformed version in <path>`；(c) IQ——agent 提示词（Scribe/Sage/Lex）frontmatter 含良构 `IQ`（S/A/B 之一，Aaron 裁定 Scribe=A、Sage=A、Lex=S），缺失/非法 → `missing or malformed IQ in <path>`；任一失败非零退出阻塞合并。“本版本是否修改流程”为开发者升版纪律，门禁不自动判定；不做 digest/manifest

### AC-FR0130-04

- [x] 已确认
  - Sage / Lex 调用经 Runtime 上下文注入真实加载/使用 tracks-discuz skill（live suite 验证）

> **gpt [RESOLVED]:** 该 AC 目前不可判定：“行为变更”与“同步”没有机器身份。请改成可执行合同，例如 spec/skill/prompt manifest 记录 revision/digest，检查命令对三者做 freshness 校验；否则移到 review checklist，不要伪装成自动验收。还需 AC 证明物化后的 Sage 能真实加载该 skill。
>> **Scribe:** 接受，按 Aaron 决定一致性模型为“存在性 + 版本检查”（非 digest/manifest freshness）：每个交付物（spec/skill/prompt）frontmatter 记版本号；当且仅当该产物流程在某 tracks 版本被修改时升版（如 Sage.md 随 v0.2→0.2，v0.3/v0.4 不动，v0.5→0.5）。AC-1303 重写为：存在性检查（文件存在）+ 版本检查（frontmatter 版本存在且在本版本改了流程时等于当前 tracks 版本），落在交付门禁（pre-commit/CI）而非 Runtime。另补 AC：Sage 调用能真实加载/使用 tracks-discuz skill（经 Runtime 上下文注入，见 spec FR-130 线程），live suite 验证。
>> **Scribe:** 按 Aaron 决定（存在性 + 版本检查）已重写 AC-1303：存在性 + frontmatter 版本（流程变更时等于当前 tracks 版本），落交付门禁；补 AC-1304：Sage 经 Runtime 上下文注入真实加载 tracks-discuz skill（live suite 验证）。@gpt 请确认是否可标记 [RESOLVED]。
>>> **gpt:** 暂不能关闭。三个被检查的实际交付物都没有 `version` frontmatter，且未指定运行该检查的命令/脚本及版本不符时的失败输出；AC-1303 当前只有断言文字，没有可执行门禁。AC-1304 的加载断言已补齐，但不足以关闭整个 thread。
>> **Scribe:** 三个交付物已补 `version: 0.2`；AC-1303 重写为可执行门禁——存在性 + frontmatter version 良构检查、`trac check deliverables`/pre-commit/CI 入口、失败输出（`missing deliverable` / `missing or malformed version`，非零退出阻塞合并）；“是否改流程”为开发者升版纪律，门禁不自动判定。@gpt 请确认是否可标记 [RESOLVED]。
>>> **gpt [OPEN]:** 门禁机制认可。仍不能关闭：AC-1303 断言的交付物清单（Scribe.md/Sage.md/SKILL.md）依赖 story §2/§3.1 的 Aaron 扩容裁定——若真实 Lex 落 v0.2，需追加 `Lex.md` 存在性 + version AC，而 Lex.md 当前缺失（见 story §1、spec FR-040 同名 OPEN）。扩容裁定收敛、清单固定后再关闭。
>>>> **Scribe:** 扩容裁定已收敛落地：`tracks/agents/Lex.md` 已交付（frontmatter `version: 0.2`、`IQ: S`），AC-1303 清单固定为四交付物（Scribe/Sage/Lex + SKILL.md）并补 IQ 良构检查（S/A/B，失败输出 `missing or malformed IQ in <path>`）；门禁实现（`trac check deliverables`）已同步检查 Lex.md 存在性 + version + IQ。与 spec FR-040、story §1/BS-09 同名线程一致。@gpt 请确认是否可标记 [RESOLVED]。
>>>>> **gpt [RESOLVED]:** 已核实 AC-FR0130-03 清单固定为四交付物含 `Lex.md`、补 IQ 良构检查，门禁实现与单测覆盖到位。清单收敛、可执行，确认关闭。

## FR-0140 模板接入 runtime

### AC-FR0140-01

- [x] 已确认
  - 生成 story/spec/acceptance 文档时从 `tracks/templates/` 读取对应模板，而非硬编码

### AC-FR0140-02

- [x] 已确认
  - main.py 中硬编码的 STORY_TEMPLATE 被移除

### AC-FR0140-03

- [x] 已确认
  - M-START 创建空骨架（套模板、保留占位符），不校验

## FR-0150 格式校验（outcome 即校验 + 门禁 + trac validate）

### AC-FR0150-01

- [x] 已确认
  - `trac validate --file <path>` 按对应模板校验文档结构，报告不符项含 `line:N`

### AC-FR0150-02

- [x] 已确认
  - 评审退出校验（M-STORY / M-SPEC）调用 validate，结构不符则阻塞退出

### AC-FR0150-03

- [x] 已确认
  - M-START 空骨架不校验；Scribe/Sage outcome 返回后 Runtime 立即 validate，非法则不进入评审并重派

### AC-FR0150-04

- [x] 已确认
  - D-16 的 `validate_document` 直通实现被取代

### AC-FR0150-05

- [x] 已确认
  - spec 条目格式合同被机器强制：每条须为 `### FR-XXXX 标题`/`### NFR-XXXX 标题`（大写、4 位、ID 唯一），含 `- **来源**：`，FR 另须 `- **交付入口**：`；决定记录在 inline-discussion 中，只有未 resolved 的 inline-discussion 才能阻塞评审退出

### AC-FR0150-06

- M-SPEC 与 M-ACC 使用同一退出门禁：结构有效且 `discussion_ready` 后才能 seal/退出；只有未 resolved 的 inline-discussion 阻塞，Runtime 不转换或维护决定 checkbox

> **gpt [RESOLVED]:** 与 Human 最新决定不一致。建议把“空骨架创建”与“Agent outcome”分开验收：M-START 骨架不校验；Scribe/Sage 返回后立即 validate，非法则不进入评审并重派；退出门禁再次 validate；`trac validate` 可独立运行。
>> **Scribe:** 接受，与 Aaron §5.1 一致。重写 AC-1403/1503：(1) M-START 骨架创建不校验；(2) Scribe/Sage outcome 返回 → Runtime 立即 validate，非法 → 不进入评审并重派；(3) 退出门禁再次 validate；(4) trac validate 可独立运行。
>> **Scribe:** 结论已写入正文（重写 AC-1403/1502/1503：M-START 骨架不校验；Scribe/Sage outcome 后立即 validate、非法不进入评审并重派；退出门禁（M-STORY/M-SPEC）再 validate；trac validate 独立）。@gpt 请确认是否可标记 [RESOLVED]。

## FR-0160 M-ACC 阶段可达（acceptance 起草与评审闭环）

### AC-FR0160-01

- [x] 已确认
  - M-SPEC 评审退出（SM-03.13）后事件流出现 stage.entered(M-ACC)，且 dispatch Sage 起草 acceptance.md（套 FR-0140 模板、继承 M-SPEC review 上下文）

### AC-FR0160-02

- [x] 已确认
  - M-ACC 评审闭环按 SM-04 执行：真实 Lex 评审（opencode 后端；测试可走 fake 通道）、Human 评审 + inline-discussion 照常可用；同轮 lex pass 且 human.review(no_comment) 才可退出

### AC-FR0160-03

- [x] 已确认
  - M-ACC 逐轮 validate 失败 → 不进入评审、重派同一作者，≤3 次；超限升级 Human

### AC-FR0160-04

- [x] 已确认
  - 退出门禁 check-ready + 格式终验通过后 stage.exited 目标为 M-REQ-APPROVAL（进入审批门禁 FR-0180，不再停在该边界）

### AC-FR0160-05

- [x] 已确认
  - Human 裁定需改 spec/story 或 trace 缺口不可在本文档修复 → stage.rolled_back，落点 M-SPEC 或 M-STORY（SM-04.4 / SM-04.10 / SM-04.15）

## FR-0170 acceptance 双向覆盖校验（AC↔FR trace）

### AC-FR0170-01

- [x] 已确认
  - spec 中存在无对应 AC 章节（或章节内无 AC）的 FR/NFR 时，`trac validate --file acceptance.md` 失败，报告缺口条目编号及 `line:N`

### AC-FR0170-02

- [x] 已确认
  - acceptance 中存在回指 spec 不存在条目的 AC 时，validate 失败，报告完整孤儿 AC 清单（含编号与 `line:N`）

### AC-FR0170-03

- [x] 已确认
  - 模板 schema 与双向 trace 均通过时退出码 0；讨论块不参与 trace；story/spec 的 validate 规则不受影响

### AC-FR0170-04

- [x] 已确认
  - M-ACC 逐轮 validate 的 trace 失败产生 `verdict.failed(trace)` 并触发重派（≤3），基准为同一工作区的 spec.md

## FR-0180 M-REQ-APPROVAL 阶段可达（需求 baseline 审批门禁）

### AC-FR0180-01

- [x] 已确认
  - M-ACC 评审退出（SM-04.13）后事件流出现 stage.entered(M-REQ-APPROVAL)，Runtime 生成 baseline preview 并进入 awaiting_human（SM-05.1 / SM-05.2）

### AC-FR0180-02

- [x] 已确认
  - 无 `human.approval` 事件时 decide() 不产出任何进入下游（M-DESIGN）的 command；Agent 不能批准/拒绝/代 Human 回答

### AC-FR0180-03

- [x] 已确认
  - human.approval → APPROVED → ISSUES → stage.exited 目标 M-DESIGN，run 停在该边界（可休眠、事件回放恢复）（SM-05.3 / SM-05.5 / SM-05.6）

### AC-FR0180-04

- [x] 已确认
  - human.return（携产品理由 + 目标阶段）→ stage.rolled_back 回退 M-STORY / M-SPEC / M-ACC（SM-05.7）

## FR-0190 需求 baseline、approval identity 与 freshness

### AC-FR0190-01

- [x] 已确认
  - baseline preview 由 story/spec/acceptance 三件套内容算出 revision digest + 人类可读摘要，供 Human 审阅

### AC-FR0190-02

- [x] 已确认
  - APPROVED 时 human.approval 绑定当时三件套 digest，三件套设为只读，并记录 approval identity（actor + digest + 时间）

### AC-FR0190-03

- [x] 已确认
  - 批准后三件套任一文档内容变化 → digest 不匹配 → approval stale，下游被阻断须重走 M-REQ-APPROVAL；stale 以内容 digest 判定（非时间戳），可经事件回放恢复

## FR-0200 spec → GitHub Issues 拆分与 Project 关联

### AC-FR0200-01

- [x] 已确认
  - APPROVED 后 Runtime 拆分 spec 为 GitHub Issues（Issues = 需求追踪身份，非执行单元）并关联 Project

### AC-FR0200-02

- [x] 已确认
  - GitHub API 调用失败按 NFR-0030 风格处理：报告原因、记录 command/outcome 事件、不写半成品、可恢复重试

### AC-FR0200-03

- [x] 已确认
  - 同一 baseline digest 重复进入不重复创建 Issues；崩溃后 reconcile 依已创建记录补齐而非重建；创建为退出最后一步，之后 run 停在 M-DESIGN 边界

## FR-0210 Agent 退出关（统一有序退出门禁）

### AC-FR0210-01

- [x] 已确认
  - Agent 退出后 Runtime 按"协议关 → 审计关 → 存在关 → 格式关 → 提交推进"顺序执行，任一关失败即短路，不进入后续关

### AC-FR0210-02

- [x] 已确认
  - 任一关失败 → outcome failed（携 failure_class 与该关证据）→ 重派同一作者，失败证据随重派 prompt 带回；不提交、不推进、不写半成品产物事件

### AC-FR0210-03

- [x] 已确认
  - 越权（审计关失败）同样走重派而非终态失败：路径级证据带回，回滚仅移除 Agent 自有改动（Aaron 裁定）

### AC-FR0210-04

- [x] 已确认
  - 同一状态内协议/审计/存在/格式各类失败共用一套 attempt 记账，累计 ≤ 3 次

### AC-FR0210-05

- [x] 已确认
  - attempt 耗尽 → awaiting_human，升级事件携全部 attempt 的失败证据；与 validate fail 的 ≤3 升级转移同构复用

### AC-FR0210-06

- [x] 已确认
  - 退出 0 且未越权但目标文档无 diff（存在关失败）→ 按统一失败语义重派，消耗 attempt

## NFR-0010 错误信息含行号

### AC-NFR0010-01

- [x] 已确认
  - 格式非法的 blockquote 报错信息包含 line:N

## NFR-0020 解析性能

### AC-NFR0020-01

- [x] 已确认
  - 1MB 合成文档（含 5000 个讨论线程）解析时间 < 1 秒

## NFR-0030 agent 调用失败处理

### AC-NFR0030-01

- [x] 已确认
  - 失败矩阵各分支均报告原因（退出码 + stderr 摘要）：opencode 可执行文件缺失；provider/model/凭据错误；非零退出；JSON 部分/截断流；退出 0 但无目标 diff；timeout/SIGINT/kill-9；目标文件已改但 outcome 未落盘；越权 diff

### AC-NFR0030-02

- [x] 已确认
  - 每类失败断言 command/outcome 事件记录、attempt 是否消耗、子进程组清理、reconcile 结果

### AC-NFR0030-03

- [x] 已确认
  - 失败时不写入半成品产物事件；目标文件 diff 为权威产物、stdout JSON 仅诊断；该次调用可恢复重试

> **gpt [RESOLVED]:** 失败矩阵仍缺关键分支：opencode executable 缺失、provider/model/凭据错误、退出 0 无目标 diff、JSON 部分流、timeout/SIGINT/kill-9、目标文件已改但 outcome 未落盘、越权 diff。每类都应断言 command/outcome 事件、attempt 是否消耗、子进程组清理和 reconcile 结果。
>> **Scribe:** 接受。扩展失败矩阵 AC：opencode 可执行文件缺失；provider/model/凭据错误；退出 0 无目标 diff；JSON 部分流；timeout/SIGINT/kill-9（+ 子进程组清理）；目标文件已改但 outcome 未落盘（reconcile）；越权 diff。每类断言：command/outcome 事件记录、attempt 是否消耗、子进程组清理、reconcile 结果。与扩展后的 NFR-030（spec 线程）及越权 AC（本文件 FR-030 线程）配对。
>> **Scribe:** 结论已写入正文（扩展 AC-1801..1803 失败矩阵：opencode 缺失/凭据错误/退出 0 无 diff/JSON 部分流/timeout·SIGINT·kill-9/outcome 未落盘/越权 diff；每类断言 command·outcome 事件、attempt 记账、子进程组清理、reconcile；文件 diff 为权威产物）。@gpt 请确认是否可标记 [RESOLVED]。

## NFR-0040 集成测试状态机全覆盖

### AC-NFR0040-01

- [x] 已确认
  - test-plan 含 SM-01～SM-05 全部转移的「转移（SM-XX.N）→ 测试用例」清单，逐条对应、无缺口

### AC-NFR0040-02

- [x] 已确认
  - 每条转移（含非 happy path：validate fail 重派、≤3 超限升级、REJECTED、scope_overflow 回退、格式终验 fail、trace 失败、M-REQ-APPROVAL RETURNED 回退（SM-05.7）、approval stale 阻断下游、GitHub Issues 创建失败/reconcile、awaiting_human 休眠后回放恢复）至少被一个 fake 通道测试走到

### AC-NFR0040-03

- [x] 已确认
  - 清单中的测试均存在且通过；清单缺口或测试失败使合入前检查失败
