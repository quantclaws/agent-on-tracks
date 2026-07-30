---
story_id: S-003
title: inline-discussion
created: 2026-07-30
status: draft
sha:
---

## 原始输入

迁移自 louke v0.4-004-quote-dialogue + v0.7-003-inline-discussion-protocol。

在 tracks 的评审阶段（M-STORY / M-SPEC / M-ACC），Sage/Lex 与 Human 需要在文档内进行结构化的多轮讨论。当前 flow.md 中提到的 "inline-discussion" 机制没有实现。本 story 定义并实现这个机制：inline-discussion 协议。

## 用户意图

- Human 在 IDE 中打开 spec.md / acceptance.md，看到 Sage 或 Lex 留下的提问，直接用 markdown blockquote 回复，不需要任何额外工具或插件
- Human 也可以主动对文档任意段落发起提问，由 Agent 在下一轮回答
- Agent 通过 `trac discuss` 命令查询、创建、回复、修改讨论线程，不直接手工编辑 blockquote
- 讨论有明确的状态（open / resolved / reopen），Runtime 在评审门禁中检查"所有讨论已 resolved"作为退出条件之一
- 当文档被修改导致行号漂移时，Agent 仍能通过内容定位找到之前的讨论线程

## 核心操作路径

1. Sage 评审 spec.md 时，对某段落有疑问，执行 `trac discuss start --file spec.md --anchor-line 42 --speaker Sage "这里的 FR-030 覆盖范围是否包含 NFR？"`
2. Runtime 在 anchor 段落后插入格式化的 blockquote 根评论，状态为 open
3. Human 在 IDE 中打开 spec.md，看到 `> **Sage:** ...`，在下方手动写 `>> **Aaron:** 不包含，NFR 单独处理`
4. 下一轮 Sage 执行 `trac discuss query --file spec.md --blocker Sage`，看到 awaiting_my_reply 类别中有 Aaron 的回复
5. Sage 执行 `trac discuss reply --file spec.md --thread-id T-001 --speaker Sage "收到，已更新 FR-030 描述"`
6. Sage 确认问题解决后执行 `trac discuss set-status --file spec.md --thread-id T-001 --status resolved --operator Sage`
7. Runtime 在评审退出校验中执行 `trac discuss query --file spec.md --check-ready`，确认所有 thread 已 resolved

## 行为种子

| 行为                     | 验证断言（将来用什么事实验证我）                                                        |
| ------------------------ | --------------------------------------------------------------------------------------- |
| 创建讨论线程             | 执行 start 后，文档中出现格式化的 blockquote 根评论，包含 speaker 标识和 open 状态      |
| 回复讨论线程             | 执行 reply 后，文档中根评论下方出现嵌套回复，depth 递增                                 |
| 状态管理权限             | 非发起人尝试设置 resolved 时被拒绝；任何人可以设置 reopen                               |
| 行号漂移后定位           | 在讨论线程创建后向文件上方插入若干行，reply/set-status 仍能通过内容定位找到正确线程     |
| 人类手写兼容             | Human 在 IDE 中手写 `> **Aaron:** ...` 或 `> Aaron: ...`，parser 均能正确识别为讨论线程 |
| 门禁检查                 | 存在 open 状态的 thread 时，check-ready 返回 false 并列出阻塞项                         |
| 说明性 blockquote 不误判 | `> **Note:** ...` 或 `> 格式约定: ...` 不被识别为讨论线程                               |
| 代码块内不解析           | fenced code block 内的同形 blockquote 不被识别为讨论线程                                |
| 并发写入安全             | 两个进程同时对同一文件执行写操作，不产生数据丢失或格式损坏                              |

## 范围排除

- 不做 Web UI 的讨论渲染（v1 是 CLI + IDE 编辑）
- 不做 @mention 通知推送（parser 识别 @mention 语法但不触发通知）
- 不做讨论线程的跨文件关联
- 不做讨论历史的版本化追溯（git 本身提供）
- 不做 12 个 agent prompt 的迁移（那是 louke 的事，tracks 的 agent prompt 从头写）
