---
spec_id: SPEC-003
created: 2026-07-30
status: draft
sha:
---

## 1. 功能需求

### FR-010 inline-discussion 语法

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

### FR-020 讨论线程数据结构

每个 thread 包含：

- thread_id: `T-NNN`（per file 自增序号）
- initiator: 根评论 speaker
- status: open / resolved / reopen
- last_speaker: 最后发言者
- reply_count: 回复数
- snippet: 根评论 body 前 80 字
- mentioned_agents: thread 内所有 @提及的 agent 列表（去重）
- 5 元组定位字段：total_lines / anchor_line / anchor_text / root_line / root_text

归一化规则：strip 首尾空白 + 合并连续空白为单空格 + Unicode NFC。不改大小写，不去 markdown 格式。speaker 比较时 lowercase 归一化，显示保留原大小写。

### FR-030 4 级降级定位

当行号漂移时，按以下顺序尝试定位 thread：

- L0 精确命中：用 delta（current_total - total_lines）修正行号后，anchor 行和 root 行内容精确匹配
- L1 Levenshtein 窗口：在修正行号 ± max(|delta|+5, 10) 范围内搜索，anchor 和 root 的编辑距离均 <= max(5, len*0.2)
- L2 仅根评论定位：全文扫描 depth=1 的 blockquote 行，找 speaker 匹配且编辑距离最小的根评论
- L3 未找到：返回 thread not found + 建议重新 query

### FR-040 CLI 命令（trac discuss）

5 个子命令：

- `trac discuss query --file <path> [--initiator <agent>] [--blocker <agent>] [--status <s>] [--check-ready]`
- `trac discuss start --file <path> --anchor-line <N> --speaker <agent> <message>`
- `trac discuss reply --file <path> --thread-id <id> --speaker <agent> <message>`
- `trac discuss edit --file <path> --thread-id <id> --depth <N> --speaker <agent> <new_body>`
- `trac discuss set-status --file <path> --thread-id <id> --status <resolved|reopen> --operator <agent>`

reply/edit/set-status 内部使用 5 元组 + 4 级降级定位（FR-030），不要求调用方传定位字段（从 thread 记录中读取）。

`--blocker <agent>` 输出 3 个类别：unanswered（我起的无回复）、unresolved（我起的未 resolved）、awaiting_my_reply（@提及我或最后回复不是我的）。

### FR-050 状态权限

- resolved：仅 initiator（根评论 speaker）可设置
- reopen：任何人可设置
- 违反权限的操作被拒绝并报告原因

### FR-060 门禁集成（check-ready）

`trac discuss query --check-ready` 输出 `is_ready: bool` + `ready_blockers: list[str]`。

ready 判定：文件内所有讨论线程状态均为 resolved。open 和 reopen 都算阻塞。

Runtime 在 M-STORY / M-SPEC / M-ACC 的评审退出校验中调用此命令，作为退出条件之一。

### FR-070 写操作语义

| 操作           | 规则                                                          |
| -------------- | ------------------------------------------------------------- |
| start 插入位置 | anchor 段落后的第一个空行之后；同 anchor 多 thread 按时间顺序 |
| reply 插入位置 | thread 最后一行之后，与下一个 blockquote 之间空一行           |
| edit 内容替换  | 定位 depth+speaker 的评论；多行内容保持 `>` 前缀和缩进一致    |
| 并发安全       | flock 写 tmp 文件 -> rename 覆盖；parse 失败回滚              |
| 空行分隔       | 写操作自动插空行（CommonMark blockquote 间必须有空行）        |

### FR-080 解析边界

- 按行进行识别，跳过 fenced code block
- 不要求 discussion 紧邻标题、FR 或文件边界
- 根评论以其上方最近的非空、非 blockquote 行作为 anchor
- 普通 Markdown 不得抑制识别："说明文字 + 空行 + `> **Aaron:** ...` + 说明文字"必须发现一个 thread

## 2. 非功能需求

### NFR-010 错误信息含行号

当 blockquote 缺 speaker、格式不合法时，错误信息包含 `line:N` 位置信息，便于 IDE 跳转。

### NFR-020 解析性能

单文件 < 1MB 的文档，端到端解析时间 < 1 秒。
