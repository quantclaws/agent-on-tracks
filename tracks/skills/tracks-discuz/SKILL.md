---
name: tracks-discuz
description: tracks inline-discussion 协议——在评审文档内用 markdown blockquote 做结构化多轮讨论。当需要通过 `trac discuss` 查询/发起/回复/修改讨论线程、理解 canonical 格式与状态语义、或判断评审门禁是否 ready 时使用。
---

# tracks-discuz：inline-discussion 协议

在 tracks 评审阶段（M-STORY / M-SPEC / M-ACC），Agent 与 Human 在文档内用 markdown blockquote 嵌套做结构化多轮讨论。本 skill 定义如何使用该协议（对应 SPEC-003 Item 2 / FR-050..FR-130）。

## canonical 格式（写操作唯一输出）

```markdown
> **Speaker [STATUS]:** comment body
>> **Speaker:** reply body
```

- 根评论 depth=1（单个 `>`），回复 depth 递增（`>>`、`>>>`…）。
- 状态仅 3 种：open / resolved / reopen，状态标记仅根评论行有效，嵌套回复中的方括号作普通文本。
- @提及：speaker tag 可写 `**@Speaker:**`（与 `**Speaker:**` 等价）；body 中 `@Name` 会被收集到 mentioned_agents。

parser 同时兼容人手写（解析等价）：`> **Name:** body` / `> **Name**: body` / `> **Name** [RESOLVED]: body` / `> Name: body`（Name 为 ASCII identifier）/ 带前导缩进。不识别无冒号 bold、说明标签（Note/Warning/Tip…）、无 speaker 的普通 blockquote。

## 命令（trac discuss）

| 子命令                                                                     | 用途                                                                                       |
| -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `query --file <p> [--initiator A] [--blocker A] [--status s] [--check-ready]` | 查询线程；`--blocker` 输出 unanswered / unresolved / awaiting_my_reply；`--check-ready` 输出 is_ready + ready_blockers |
| `start --file <p> --anchor-line <N> --speaker <A> <msg>`                   | 在 anchor 段落后发起 open 根评论                                                          |
| `reply --file <p> --thread-id <id> --speaker <A> <msg>`                    | 追加回复（depth 递增）                                                                     |
| `edit --file <p> --thread-id <id> --depth <N> --speaker <A> <new>`         | 编辑自己某层评论                                                                           |
| `set-status --file <p> --thread-id <id> --status <resolved\|reopen> --operator <A>` | 改状态                                                                              |

reply / edit / set-status 内部用 5 元组 + 4 级降级定位（FR-070），调用方无需传定位字段（从 thread 记录读取）。

## 状态权限（FR-090）

- resolved：仅 initiator（根评论 speaker）可设。
- reopen：任何人可设。
- 违反权限的操作被拒绝并报告原因。

## 门禁（check-ready，FR-100）

`query --check-ready` 返回 `is_ready: bool` + `ready_blockers: list`：文件内所有线程均 resolved 才 ready（open / reopen 都阻塞）。评审退出校验以此为退出条件之一。

## 行号漂移定位（4 级降级，FR-070）

文档被改导致行号漂移时，按顺序定位：L0 精确（delta 修正）→ L1 Levenshtein 窗口 → L2 仅根评论 → L3 not found（建议重新 query）。

## 使用约定

- 讨论一律走 `trac discuss`，不手工编辑 blockquote（canonical 格式由命令保证）。
- 每轮先 `query --blocker <self>` 处理待办，再 start / reply / set-status。
- 退出前 `query --check-ready` 确认收敛（is_ready=true）。
- 写操作自动处理空行分隔与并发安全（flock + tmp + rename）；解析失败回滚。
