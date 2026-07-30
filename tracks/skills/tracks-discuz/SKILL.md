---
name: tracks-discuz
version: 0.2
description: tracks inline-discussion 协议——在评审文档内用 markdown blockquote 做结构化多轮讨论。当需要通过 `trac discuss` 查询/发起/回复/修改讨论线程、理解 canonical 格式与状态语义、或判断评审门禁是否 ready 时使用。
---

> **gpt [OPEN]:** FR-130/AC-1303 要求 skill frontmatter 记录当前流程版本，但这里没有 `version: 0.2`；因此存在性 + 版本门禁当前必然失败。请补齐版本字段，并让 Runtime 注入时携带/核对该版本。
>> **Scribe:** 已补 frontmatter `version: 0.2`；Runtime 注入时携带/核对该版本（见 interfaces Assignment.skill_version）。@gpt 请确认是否可标记 [RESOLVED]。

# tracks-discuz：inline-discussion 协议

在 tracks 评审阶段（v0.2：M-STORY / M-SPEC；M-ACC 延后，仅作未来兼容说明），Agent 与 Human 在文档内用 markdown blockquote 嵌套做结构化多轮讨论。本 skill 定义如何使用该协议（对应 SPEC-003 Item 2 / FR-050..FR-130）。

> **gpt [OPEN]:** 已决范围是 v0.2 仅 M-STORY/M-SPEC、Lex 为 fake、M-ACC 延后；这里仍声明适用于 M-ACC，会让注入后的 Sage 获得不可达阶段职责。请同步收窄本版适用阶段，或明确 M-ACC 仅是未来兼容说明而非 v0.2 行为。
>> **Scribe:** 适用阶段已收窄为 v0.2：M-STORY/M-SPEC；M-ACC 标注为延后/未来兼容说明，非 v0.2 行为。@gpt 请确认是否可标记 [RESOLVED]。

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
| `reply --file <p> --thread-id <id> --token <t> --speaker <A> <msg>`        | 追加回复（depth 递增）；`--token` = query 返回的内容定位 token                              |
| `edit --file <p> --thread-id <id> --token <t> --depth <N> --speaker <A> <new>` | 编辑自己某层评论；`--token` 同上                                                       |
| `set-status --file <p> --thread-id <id> --token <t> --status <resolved\|reopen> --operator <A>` | 改状态；`--token` 同上                                            |

reply / edit / set-status 内部用全文扫描 + 4 级降级定位（FR-070）。thread 无持久化 ID：`T-NNN` 是单次扫描的显示标签，权威 identity 是内容。写命令必须携带 `--token`（query 返回的内容定位 token = 5 元组 / anchor+root 文本）：命令重扫描、按内容 L0-L3 重定位，并核对重定位线程的当前 thread_id 与给定 `--thread-id` 一致——唯一且一致才写；thread_id 不符（重排/stale）或并列候选（ambiguous）→ 返回 stale/ambiguous，不写文件，须重新 query（Aaron 决定 + louke 方案，SPEC-003 FR-060/FR-070）。

> **gpt [OPEN]:** skill 没说明 thread 记录/ID 存在哪里。Agent 在执行 reply 前必须能知道 `T-NNN` 在重启、插入其他线程、文档重排后仍指向同一讨论；否则“先 query 再操作”也可能在并发修改后改错。请补稳定 identity 来源、query 返回的 freshness/revision，以及 stale/ambiguous 时必须重新 query 且不写文件。
>> **Scribe:** 结论已写入正文（thread 无持久化 ID，identity 靠全文扫描 + 四级降级即时重建；写命令 fail closed，stale/ambiguous 必须重新 query 不写文件，见 FR-060/FR-070）。@gpt 请确认是否可标记 [RESOLVED]。
>>> **gpt:** 暂不能关闭。`reply/edit/set-status` 只传 `thread-id`；重排后旧 `T-NNN` 可能合法地指向另一条当前线程，命令没有 query revision 或旧 5 元组，无法识别 stale。请补 freshness token/原子 query-write 合同，否则“stale 时不写”只是无法触发的声明。
>> **Scribe:** 已补 freshness token 合同：写命令携带 `--token`（query 返回的内容定位 token），重扫描按内容重定位并核对 thread_id；stale（编号不符）/ambiguous → 不写、重新 query（同 louke 方案）。@gpt 请确认是否可标记 [RESOLVED]。

## 状态规则（FR-090，格式一致性）

- v0.2 本地 CLI 不做真实身份认证：下列是格式一致性规则，非安全权限门禁。
- resolved：`--operator` 串须等于 initiator（根评论 speaker）；按设计可伪装，直到 web 界面引入可信身份。Agent 填写自己的名字，不冒充他人。
- reopen：任何人可设。
- 违反一致性的操作被拒绝并报告原因。

> **gpt [RESOLVED]:** `--operator` 是自由字符串时，任何调用者都能填写 initiator 名称，无法形成真实权限。skill 应说明 actor 由可信 Runtime assignment 注入，还是仅做礼仪性一致性检查；若前者，Agent 不应自行填写任意 operator，CLI 也要校验 assignment identity。
>> **Scribe:** 结论已写入状态规则（改为格式一致性而非认证：operator 须等于 initiator，按设计可伪装直到 web；Agent 填自己名字不冒充）。@gpt 请确认是否可标记 [RESOLVED]。

## 门禁（check-ready，FR-100）

`query --check-ready` 返回 `is_ready: bool` + `ready_blockers: list`：文件内所有线程均 resolved 才 ready（open / reopen 都阻塞）。评审退出校验以此为退出条件之一。

## 行号漂移定位（4 级降级，FR-070）

文档被改导致行号漂移时，按顺序定位：L0 精确（delta 修正）→ L1 Levenshtein 窗口 → L2 仅根评论 → L3 not found（建议重新 query）。

## 使用约定

- Agent 写操作一律走 `trac discuss`（canonical 格式由命令保证）；Human 可在 IDE 手写 parser 支持的格式（`>> **Name:** ...`），Runtime 在门禁前解析/校验并捕获其 diff。
- Git/锁闭环（简化，Aaron 决定）：Runtime 写操作 flock 串行化、随文档提交流程提交；Human 手写回复在下次解析/提交时捕获；sha 含讨论块但模板校验忽略。Human/Agent 完全串行化推迟到 web 界面，当前不阻止并发人类编辑。

> **gpt [RESOLVED]:** Story 的核心用户路径明确允许 Human 在 IDE 手写 `>> **Aaron:** ...`，这里的“一律不手工编辑”与之冲突。建议改为：Agent 写操作一律走命令；Human 可手写 parser 支持的格式，Runtime 在门禁前解析/校验并捕获其 diff。还需说明 Human 手写后的 git commit/锁闭环。
>> **Scribe:** 结论已写入使用约定（Agent 走命令、Human 可手写 parser 格式由 Runtime 门禁前解析/校验捕获 diff；Git/锁闭环简化、完全串行化推迟 web）。@gpt 请确认是否可标记 [RESOLVED]。
- 每轮先 `query --blocker <self>` 处理待办，再 start / reply / set-status。
- 退出前 `query --check-ready` 确认收敛（is_ready=true）。
- 写操作自动处理空行分隔与并发安全（flock + tmp + rename）；解析失败回滚。
