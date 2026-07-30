---
acc_id: ACC-003
created: 2026-07-30
status: draft
sha:
---

## FR-010 inline-discussion 语法

- AC-0101: parser 对 `**Speaker:**` / `**Speaker**:` / `Speaker:` 三种布局解析出相同的 speaker、status、body、depth
- AC-0102: 根状态标记 `[RESOLVED]` / `[REOPEN]` 在粗体内外两种位置均被识别（`**Name [RESOLVED]:**` 和 `**Name** [RESOLVED]:`）
- AC-0103: 嵌套回复中的方括号（如 `>> **Sage:** 已修复 [open]`）不被识别为状态标记，作普通文本
- AC-0104: `> **Note:** ...` / `> 格式约定: ...` / `> [!WARNING]` 不被识别为讨论线程
- AC-0105: 写操作输出统一为 canonical 格式 `> **Speaker [STATUS]:** body`

## FR-020 讨论线程数据结构

- AC-0201: thread_id 格式为 `T-NNN`，per file 自增，不重复
- AC-0202: 5 元组定位字段（total_lines / anchor_line / anchor_text / root_line / root_text）在 thread 创建时完整记录
- AC-0203: 归一化规则：strip + 合并空白 + NFC，不改大小写

## FR-030 4 级降级定位

- AC-0301: 文件上方插入 10 行后，L0 精确命中仍有效（delta 修正）
- AC-0302: anchor 行被微调（编辑距离 <= 阈值）后，L1 Levenshtein 窗口命中
- AC-0303: anchor 行被完全重写后，L2 仅根评论定位命中
- AC-0304: thread 被删除后，L3 返回 not found + 建议操作

## FR-040 CLI 命令

- AC-0401: 5 个子命令（query/start/reply/edit/set-status）均可调用
- AC-0402: query 输出 JSON 含 thread 列表，每 thread 含 5 元组定位字段
- AC-0403: query --blocker 输出 unanswered / unresolved / awaiting_my_reply 三类别
- AC-0404: query --check-ready 输出 is_ready + ready_blockers

## FR-050 状态权限

- AC-0501: 非 initiator 执行 set-status resolved 被拒绝，报告"仅发起人可关闭"
- AC-0502: 任何人执行 set-status reopen 成功

## FR-060 门禁集成

- AC-0601: 存在 open thread 时 check-ready 返回 is_ready=false，ready_blockers 含该 thread
- AC-0602: 存在 reopen thread 时 check-ready 返回 is_ready=false
- AC-0603: 全部 resolved 时 check-ready 返回 is_ready=true，ready_blockers 为空

## FR-070 写操作语义

- AC-0701: start 插在 anchor 段落后的第一个空行之后
- AC-0702: reply 追加到 thread 末尾，与下一个 blockquote 之间有空行
- AC-0703: edit 仅原作者可改，非原作者被拒绝
- AC-0704: 两个进程同时写同一文件，不产生数据丢失（flock 串行化）

## FR-080 解析边界

- AC-0801: fenced code block 内的 `> **Name:** ...` 不被识别为讨论
- AC-0802: 讨论线程前后有普通 Markdown 时仍被正确发现
- AC-0803: anchor_text 是根评论上方最近的非空、非 blockquote 行

## NFR-010 错误信息含行号

- AC-0901: 格式非法的 blockquote 报错信息包含 line:N

## NFR-020 解析性能

- AC-1001: 1MB 合成文档（含 5000 个讨论线程）解析时间 < 1 秒
