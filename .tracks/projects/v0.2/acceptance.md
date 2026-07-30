---
acc_id: ACC-003
created: 2026-07-30
status: draft
sha:
---

# ACC-003: v0.2 评审闭环——真实 Agent、inline-discussion、文档模板

> AC 编号与 SPEC-003 的 FR 对应：AC-<FR 号/10>xx。三项范围按 Item 1 / 2 / 3 组织。

## Item 1: 真实 Agent 调用（Scribe / Sage）

### FR-010 agent 抽象与后端选择

- AC-0101: 默认（未设 TRAC_AGENT_BACKEND）时通过 opencode 后端调用 agent
- AC-0102: TRAC_AGENT_BACKEND=fake 或设置 TRAC_FAKE_SIMULATE 时使用 FakeAgent，且不触发 opencode 进程
- AC-0103: E2E 测试夹具（conftest）强制 fake 后端，测试不依赖真实 opencode

> **gpt [OPEN]:** 缺少 Human 已要求的 live Agent E2E。请保留 AC-0103 作为 deterministic suite，并新增 live suite：真实 opencode + 环境配置 provider/model，断言执行协议、目标 diff、权限与格式，不断言具体语义文本；明确缺少凭据时的 job 结果。

### FR-020 opencode 后端

- AC-0201: opencode 后端以 subprocess 执行 `opencode run --agent <Name> --format json --dir <repo> --auto "<prompt>"`
- AC-0202: `<Name>` 仅取 `Scribe` / `Sage`（首字母大写）
- AC-0203: runtime 解析 stdout 的 JSON 取回 agent 产物/状态
- AC-0204: 调用前将 `tracks/agents/<Name>.md` 物化到目标 repo 的 `.opencode/agent/<Name>.md`

> **gpt [OPEN]:** 该路径疑似与当前项目实际使用的 `.opencode/agents/`（复数）不一致。AC 还应覆盖已有同名文件不被静默覆盖、正常/失败后的清理、崩溃恢复，以及 opencode 确实能按 `Scribe`/`Sage` 名发现物化定义。

### FR-030 agent 权限白名单

- AC-0301: agent 仅可编辑本次任务的目标文档
- AC-0302: 目标文档之外的写操作被拒绝

> **gpt [OPEN]:** “被拒绝”需要可观察证据：分别覆盖 edit 工具越界、Sage 通过 bash 越界、专属临时目录允许、Runtime 事后 git diff 发现越权；断言 outcome failed、无提交/无推进、Human 既有修改不被覆盖、Agent 子进程和临时目录被清理。

### FR-040 agent 提示词交付物

- AC-0401: `tracks/agents/Scribe.md` 与 `tracks/agents/Sage.md` 存在
- AC-0402: 提示词文件遵循 opencode agent 定义格式（frontmatter: description/mode/permission + prompt body）
- AC-0403: spec 中 agent 行为变更后，对应提示词同步更新，二者一致

## Item 2: inline-discussion 协议

### FR-050 inline-discussion 语法

- AC-0501: parser 对 `**Speaker:**` / `**Speaker**:` / `Speaker:` 三种布局解析出相同的 speaker、status、body、depth
- AC-0502: 根状态标记 `[RESOLVED]` / `[REOPEN]` 在粗体内外两种位置均被识别（`**Name [RESOLVED]:**` 和 `**Name** [RESOLVED]:`）
- AC-0503: 嵌套回复中的方括号（如 `>> **Sage:** 已修复 [open]`）不被识别为状态标记，作普通文本
- AC-0504: `> **Note:** ...` / `> 格式约定: ...` / `> [!WARNING]` 不被识别为讨论线程
- AC-0505: 写操作输出统一为 canonical 格式 `> **Speaker [STATUS]:** body`

### FR-060 讨论线程数据结构

- AC-0601: thread_id 格式为 `T-NNN`，per file 自增，不重复
- AC-0602: 5 元组定位字段（total_lines / anchor_line / anchor_text / root_line / root_text）在 thread 创建时完整记录
- AC-0603: 归一化规则：strip + 合并空白 + NFC，不改大小写

> **gpt [OPEN]:** 缺 thread identity 持久性验收。请增加：多次 query、进程重启、在前方插入/删除其他 thread、git 回滚后，既有 thread_id 与目标线程的对应关系符合明确合同；否则 reply/edit 可能改错线程。

### FR-070 4 级降级定位

- AC-0701: 文件上方插入 10 行后，L0 精确命中仍有效（delta 修正）
- AC-0702: anchor 行被微调（编辑距离 <= 阈值）后，L1 Levenshtein 窗口命中
- AC-0703: anchor 行被完全重写后，L2 仅根评论定位命中
- AC-0704: thread 被删除后，L3 返回 not found + 建议操作

> **gpt [OPEN]:** 需补 L1/L2 多候选并列测试：返回 ambiguous 和候选行号，文件逐字节不变。当前只有命中/未找到，没有证明算法不会在重复评论中静默选错。

### FR-080 CLI 命令

- AC-0801: 5 个子命令（query/start/reply/edit/set-status）均可调用
- AC-0802: query 输出 JSON 含 thread 列表，每 thread 含 5 元组定位字段
- AC-0803: query --blocker 输出 unanswered / unresolved / awaiting_my_reply 三类别
- AC-0804: query --check-ready 输出 is_ready + ready_blockers

### FR-090 状态权限

- AC-0901: 非 initiator 执行 set-status resolved 被拒绝，报告"仅发起人可关闭"
- AC-0902: 任何人执行 set-status reopen 成功

> **gpt [OPEN]:** 还需证明 operator identity 不能通过自由 `--operator` 冒充 initiator；若产品明确不做身份认证，请把此处改为格式一致性规则而非权限断言。另补 `--file ../...`、绝对路径、repo 外 symlink 均被 scope gate 拒绝且不改文件。

### FR-100 门禁集成

- AC-1001: 存在 open thread 时 check-ready 返回 is_ready=false，ready_blockers 含该 thread
- AC-1002: 存在 reopen thread 时 check-ready 返回 is_ready=false
- AC-1003: 全部 resolved 时 check-ready 返回 is_ready=true，ready_blockers 为空

### FR-110 写操作语义

- AC-1101: start 插在 anchor 段落后的第一个空行之后
- AC-1102: reply 追加到 thread 末尾，与下一个 blockquote 之间有空行
- AC-1103: edit 仅原作者可改，非原作者被拒绝
- AC-1104: 两个进程同时写同一文件，不产生数据丢失（flock 串行化）

### FR-120 解析边界

- AC-1201: fenced code block 内的 `> **Name:** ...` 不被识别为讨论
- AC-1202: 讨论线程前后有普通 Markdown 时仍被正确发现
- AC-1203: anchor_text 是根评论上方最近的非空、非 blockquote 行

### FR-130 tracks-discuz skill 交付物

- AC-1301: `tracks/skills/tracks-discuz/` 存在 skill 文本
- AC-1302: skill 文本涵盖 query/start/reply/set-status 用法、canonical 格式、状态语义、门禁含义
- AC-1303: spec 中 inline-discussion 行为变更后，skill 文本同步更新，二者一致

> **gpt [OPEN]:** 该 AC 目前不可判定：“行为变更”与“同步”没有机器身份。请改成可执行合同，例如 spec/skill/prompt manifest 记录 revision/digest，检查命令对三者做 freshness 校验；否则移到 review checklist，不要伪装成自动验收。还需 AC 证明物化后的 Sage 能真实加载该 skill。

## Item 3: 文档模板 + 格式校验

### FR-140 模板接入 runtime

- AC-1401: 生成 story/spec/acceptance 文档时从 `tracks/templates/` 读取对应模板，而非硬编码
- AC-1402: main.py 中硬编码的 STORY_TEMPLATE 被移除
- AC-1403: 生成时套用模板骨架（占位符保留），生成时不强制校验

### FR-150 格式校验（门禁 + trac validate）

- AC-1501: `trac validate --file <path>` 按对应模板校验文档结构，报告不符项含 `line:N`
- AC-1502: 评审退出校验（M-STORY / M-SPEC / M-ACC）调用 validate，结构不符则阻塞退出
- AC-1503: 生成时不强制校验（空骨架不误报）
- AC-1504: D-16 的 `validate_document` 直通实现被取代

> **gpt [OPEN]:** 与 Human 最新决定不一致。建议把“空骨架创建”与“Agent outcome”分开验收：M-START 骨架不校验；Scribe/Sage 返回后立即 validate，非法则不进入评审并重派；退出门禁再次 validate；`trac validate` 可独立运行。

## 非功能需求

### NFR-010 错误信息含行号

- AC-1601: 格式非法的 blockquote 报错信息包含 line:N

### NFR-020 解析性能

- AC-1701: 1MB 合成文档（含 5000 个讨论线程）解析时间 < 1 秒

### NFR-030 agent 调用失败处理

- AC-1801: opencode 非零退出 / 超时 / stdout JSON 解析失败时，报告失败原因（含退出码与 stderr 摘要）
- AC-1802: 失败时不写入半成品产物事件，该次调用可恢复重试

> **gpt [OPEN]:** 失败矩阵仍缺关键分支：opencode executable 缺失、provider/model/凭据错误、退出 0 无目标 diff、JSON 部分流、timeout/SIGINT/kill-9、目标文件已改但 outcome 未落盘、越权 diff。每类都应断言 command/outcome 事件、attempt 是否消耗、子进程组清理和 reconcile 结果。
