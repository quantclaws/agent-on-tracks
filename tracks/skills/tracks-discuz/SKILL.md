---
envelope: tracks-envelope:v2
name: tracks-discuz
version: 0.2
description: tracks inline-discussion 协议——在评审文档内用 markdown blockquote 做结构化多轮讨论。当需要通过 `trac discuss` 查询/发起/回复/修改讨论线程、理解 canonical 格式与状态语义、或判断评审门禁是否 ready 时使用。
---

# tracks-discuz：inline-discussion 协议

在 tracks 评审/修订阶段（M-STORY / M-SPEC / M-ACC / M-DESIGN；使用方为全部 tracks 角色——Scribe / Sage / Lex / Archer / Prism，由 Runtime 注入调用上下文），Agent 与 Human 在文档内用 markdown blockquote 嵌套做结构化多轮讨论。本 skill 定义如何使用该协议。

## canonical 格式（写操作唯一输出）

```markdown
> **Speaker [STATUS]:** comment body
>> **Speaker:** reply body
```

- 根评论 depth=1（单个 `>`）。**depth 编码"回复谁"**：depth=N 的评论回复其上方最近的 depth=N-1 评论；要回复某条具体回复，就再加一层 `>`：

  ```markdown
  > **Human:** I don't know                    # depth 1：根（议题）
  >> **Sage:** please read line 5 and revise   # depth 2：回复 Human 的根
  >>> **Scribe:** done                         # depth 3：回复 Sage 那条（不是 Human）
  >> **Human:** thanks                         # depth 2：回复根，与 Sage 平级
  ```

  上例 `>>> **Scribe:**` 回复的是 Sage（上方最近的 depth 2），不是根。若写成 `>> **Scribe:**`（depth 2）则回复的是 Human 的根、与 Sage 平级——**Sage 的请求就无人应答**。"某条回复有没有人理" = 它有没有 depth+1 的下级回复。
- 状态仅 3 种：open / resolved / reopen，状态标记仅根评论行有效，嵌套回复中的方括号作普通文本。
- @提及（独立语义，与 depth 正交）：speaker tag 可写 `**@Speaker:**`（与 `**Speaker:**` 等价），body 中 `@Name` 收集到 mentioned_agents。**`@Name` 表示"请求 Name 增加一个回答"，不表示"当前回复是对谁的回复"**（那是 depth）。`--blocker` 的 awaiting_my_reply = 被 @mention 请求且该评论尚无下级回复。

parser 同时兼容人手写（解析等价）：`> **Name:** body` / `> **Name**: body` / `> **Name** [RESOLVED]: body` / `> Name: body`（Name 为 ASCII identifier）/ 带前导缩进。不识别无冒号 bold、说明标签（Note/Warning/Tip…）、无 speaker 的普通 blockquote。

## 命令（trac discuss）

| 子命令                                                                     | 用途                                                                                       |
| -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `query --file <p> [模式] [过滤] [--check-ready] [--compact]` | 查询线程；模式 `--inbox A` / `--thread-id T` / `--summary-only` 互斥；过滤 `--initiator A` / `--blocker A` / `--status s`；详见下文 |
| `start --file <p> --anchor-line <N> --speaker <A> <msg>`                   | 在 anchor 段落后发起 open 根评论                                                          |
| `reply --file <p> --thread-id <id> --token <t> --speaker <A> [--reply-to-token <ct>] <msg>` | 追加回复；`--reply-to-token`（query 返回的 comment token_str）指定回复到哪条评论（depth=父+1，插在其子树后），省略 = 回复根（depth 2）；`--token` = 线程 token_str |
| `edit --file <p> --thread-id <id> --token <t> --depth <N> --speaker <A> <new>` | 编辑自己某层评论；`--token` 同上                                                       |
| `set-status --file <p> --thread-id <id> --token <t> --status <resolved\|reopen> --operator <A>` | 改状态；`--token` 同上                                            |

reply / edit / set-status 内部用全文扫描 + 4 级降级定位（见下文）。thread 无持久化 ID：`T-NNN` 是单次扫描的显示标签，权威 identity 是内容。写命令必须携带 `--token`（query 返回的 `token_str`）：命令重扫描、按内容 L0-L3 重定位，并核对重定位线程的当前 thread_id 与给定 `--thread-id` 一致——唯一且一致才写；thread_id 不符（重排/stale）或并列候选（ambiguous）→ 返回 stale/ambiguous，不写文件，须重新 query。

`token_str` 是 opaque 自包含字符串（`z1.<base64url(zlib(json))>`），编码 thread 5 元组（total_lines / anchor_line / anchor_text / root_line / root_text）或 comment 4 元组（text / depth / speaker / parent?）。query 为每个 thread 和 comment 同时返回 `token_str` 与 legacy `token` dict（`--compact` 时仅返回 `token_str`）。写命令的 `--token` / `--reply-to-token` 接受三种传输形式：`token_str`（z1，推荐）、inline JSON（`{...}`，向后兼容）、legacy base64url（无前缀，向后兼容）。

### query 模式与过滤

| 模式 / 过滤 | 用途 | 输出 |
| --- | --- | --- |
| `--inbox <Speaker>` | 个人待办快查 | unanswered ∪ unresolved ∪ awaiting_my_reply（compact，doc order，去重） |
| `--thread-id <T-NNN>` | 定点查看某线程 | 单线程（compact） |
| `--check-ready --summary-only` | 门禁快查 | 仅 `is_ready` + `ready_blockers`（无 threads） |
| 默认 full | 诊断 | 全部线程 + token dict + location fields |
| `--compact`（full 模式） | 省略 token dict / location | 仅 `token_str` |
| `--initiator A` / `--status s` / `--blocker A` | full 模式过滤 | 按 initiator / status / blocker 筛选 |

- `--inbox` / `--thread-id` / `--summary-only` 三者互斥；`--summary-only` 须与 `--check-ready` 同用。
- `--inbox` 是**个人快查过滤器**，不是 author 的完整收件箱：它仅返回与 Speaker 直接相关的线程（自己发起的 unanswered / unresolved + 被 @mention 请求且无下级回复的）。`@Name` 语义是"请求 Name 增加一个回答"，不等同于"Name 的全部待办"。author 须查看全部 open/reopen 线程（用 `--status open` / `--status reopen` 或默认 full `--compact`），无论是否 @mention 自己——此为 role-specific 纪律，由各 agent prompt 约定。

### canonical query → reply 示例

```bash
# 1. query 拿到 thread token_str（--inbox / --thread-id / 默认 full 均可）
trac discuss query --file story.md --thread-id T-001
# -> {"threads":[{"thread_id":"T-001","token_str":"z1.eJx...","root":{...}}]}

# 2. reply 到根（depth 2）：把 thread token_str 传给 --token
trac discuss reply --file story.md --thread-id T-001 \
  --token z1.eJx... --speaker Scribe "已修复 line 5"

# 3. reply 到某条评论（depth = 父+1）：把 comment token_str 传给 --reply-to-token
trac discuss reply --file story.md --thread-id T-001 \
  --token z1.eJx... --reply-to-token z1.eJy... --speaker Scribe "回复 Sage"
```

## 状态规则（格式一致性）

- v0.2 本地 CLI 不做真实身份认证：下列是格式一致性规则，非安全权限门禁。
- resolved：`--operator` 串须等于 initiator（根评论 speaker）；按设计可伪装，直到 web 界面引入可信身份。Agent 填写自己的名字，不冒充他人。
- reopen：任何人可设。
- 违反一致性的操作被拒绝并报告原因。

## 门禁（check-ready）

`query --check-ready` 返回 `is_ready: bool` + `ready_blockers: list`：文件内所有线程均 resolved 才 ready（open / reopen 都阻塞）。评审退出校验以此为退出条件之一。

## 行号漂移定位（4 级降级）

文档被改导致行号漂移时，按顺序定位：L0 精确（delta 修正）→ L1 Levenshtein 窗口 → L2 仅根评论 → L3 not found（建议重新 query）。

## 使用约定

- Agent 写操作一律走 `trac discuss`（canonical 格式由命令保证）；Human 可在 IDE 手写 parser 支持的格式（`>> **Name:** ...`），Runtime 在门禁前解析/校验并捕获其 diff。
- Git/锁闭环（简化实现）：Runtime 写操作 flock 串行化、随文档提交流程提交；Human 手写回复在下次解析/提交时捕获；sha 含讨论块但模板校验忽略。Human/Agent 完全串行化推迟到 web 界面，当前不阻止并发人类编辑。
- 每轮先 `query --inbox <self>` 快查个人待办（author 须另查全部 open/reopen，见上文），再 start / reply / set-status。
- 退出前 `query --check-ready --summary-only` 确认收敛（is_ready=true）。
- 写操作自动处理空行分隔与并发安全（flock + tmp + rename）；解析失败回滚。
