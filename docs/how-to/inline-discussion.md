# 在文档中发起和解决讨论

## 什么时候使用 inline discussion

Agent 写完 story、spec 或设计文档以后，通常会请你评审。文档一长，讨论就会遇到一个朴素的问题：你说的“这里”到底是哪里？

你可以引用行号，但文档修改一次，行号就会变化。你也可以使用某个 IDE 的评论功能，但评论往往保存在 IDE 或平台自己的数据库里；换一个编辑器，Agent 可能只看见文档，却看不见讨论。

Tracks 把讨论直接写进 Markdown。评论紧跟在被讨论的段落后面，所以文档、上下文和讨论始终在一起。它不要求某个特定 IDE，也不需要为每条评论建立单独的数据库。

这种机制叫做 **inline discussion**。它适合三类场景：

- 你要指出文档中某个具体问题；
- 多个 Agent 需要围绕同一问题逐层回复；
- 流程必须知道讨论是否已经收敛，才能决定是否离开评审阶段。

inline discussion 不替你判断答案是否正确。参与者负责提出问题和明确解决状态，Runtime 负责解析状态并执行门禁。

> [!info] 为什么使用 blockquote？
>
> Markdown 的 blockquote 来自电子邮件和纯文本引用的传统。它天然表达“这句话在回复上一句话”，几乎所有编辑器和 LLM 都能理解，而且离开 Tracks 后仍是普通 Markdown。

## 讨论的 Markdown 结构

下面是一段最小讨论：

```markdown
line counters 是一个代码行统计工具。

> **Aaron:** 代码注释是否也计算在内？
>> **Sage:** 计算在内。@Lex，请检查这是否与 spec 一致。
>>> **Lex:** 一致，但需要补充空白行的处理规则。
>> **Aaron [RESOLVED]:** 已补充，现在可以关闭这个问题。
```

`>` 表示根评论，`>>` 表示回复根评论，`>>>` 表示回复上一层评论。层数不仅用于排版，还表达“这句话回复谁”。如果 Lex 的回复写成 `>>`，它就会与 Sage 平级，而不是回复 Sage。

每条评论都有 speaker tag，例如 `**Sage:**`。正文中的 `@Lex` 是提醒 Lex 回答；它不改变回复关系，回复关系仍由 `>` 的深度决定。

线程有三种状态：

- 没有状态标记时是 `open`；
- 根评论带 `[RESOLVED]` 时是 `resolved`；
- 已解决的问题重新打开时是 `reopen`。

状态只看根评论。嵌套回复里的 `[RESOLVED]` 只是普通文本，不会关闭线程。

Human 可以在编辑器里手写这种格式。Agent 应当使用 `trac discuss`，由命令生成统一格式并避免定位错误。

## 查询讨论

所有写操作都从查询开始：

```bash
trac discuss query --file .tracks/projects/v0.5/story.md --compact
```

命令输出 JSON。每条线程都有一个类似 `T-001` 的显示编号和一个 `token_str`：

```json
{
  "threads": [
    {
      "thread_id": "T-001",
      "initiator": "Aaron",
      "status": "open",
      "last_speaker": "Sage",
      "reply_count": 1,
      "token_str": "z1.eJx..."
    }
  ]
}
```

常用查询模式如下：

```bash
# 查看与你直接相关的待办
trac discuss query --file story.md --inbox Sage

# 查看一条线程
trac discuss query --file story.md --thread-id T-001

# 查看文档是否已满足讨论门禁
trac discuss query --file story.md --check-ready --summary-only
```

`--inbox Sage` 会返回 Sage 发起但尚未解决的线程，以及正文中 `@Sage` 且尚无下级回复的评论。它不是完整收件箱；文档作者仍应查看全部 `open` 和 `reopen` 线程，因为有些问题不会专门 mention 作者。

`--compact` 省略定位细节，但保留写操作所需的 `token_str`。需要诊断定位问题时，去掉 `--compact` 查看完整输出。

## 发起讨论

先找到要讨论段落中的一行，再以它的行号为锚点：

```bash
trac discuss start \
  --file .tracks/projects/v0.5/story.md \
  --anchor-line 37 \
  --speaker Aaron \
  "屏蔽路径是否应当由 test-plan 声明？"
```

Tracks 会把根评论插入该段落之后，并输出新线程的显示编号：

```text
T-001
```

锚点不是永久行号。它只是帮助 Tracks 找到插入位置；讨论写入以后，后续定位主要依靠段落和根评论的内容。文档前面增加或删除几行，不会自动让讨论失去上下文。

`--file` 必须位于当前仓库中。Tracks 会解析 `..` 和符号链接，拒绝写到仓库之外。

## 回复和编辑评论

回复前先查询线程，取得最新的 `token_str`：

```bash
trac discuss query --file story.md --thread-id T-001
```

回复根评论：

```bash
trac discuss reply \
  --file story.md \
  --thread-id T-001 \
  --token z1.eJx... \
  --speaker Sage \
  "应该由 test-plan 声明，Runtime 只消费冻结后的清单。"
```

这会生成 depth 2 的回复。如果要回复某一条具体评论，从查询结果中取得那条评论自己的 `token_str`，再传给 `--reply-to-token`：

```bash
trac discuss reply \
  --file story.md \
  --thread-id T-001 \
  --token z1.eJx... \
  --reply-to-token z1.eJy... \
  --speaker Lex \
  "还需要规定清单缺失时 fail closed。"
```

编辑自己的评论时，需要提供评论深度和 speaker：

```bash
trac discuss edit \
  --file story.md \
  --thread-id T-001 \
  --token z1.eJx... \
  --depth 2 \
  --speaker Sage \
  "应该由 test-plan 声明；缺失声明时不得进入 M-IMPL。"
```

每次写入都会改变文档，旧 token 随之失效。因此一次写操作完成后，应重新 query，再进行下一次写操作。不要长期保存 token，也不要猜测新的线程编号。

> [!info] token 不是登录凭据
>
> token 保存的是段落、根评论和行号提示，用于在文档变化后重新定位内容。它没有签名，也不证明调用者的真实身份。

## 解决和重新打开讨论

当问题已经得到回答并且正文已经修改，由线程发起人把它标记为 resolved：

```bash
trac discuss set-status \
  --file story.md \
  --thread-id T-001 \
  --token z1.eJx... \
  --status resolved \
  --operator Aaron
```

如果后续修改使答案失效，任何参与者都可以重新打开：

```bash
trac discuss set-status \
  --file story.md \
  --thread-id T-001 \
  --token z1.eJx... \
  --status reopen \
  --operator Lex
```

本地 CLI 只检查 `--operator` 文本是否等于线程发起人，没有真实身份认证。因此，“只有发起人能 resolve”目前是一条格式一致性规则，不是安全权限。可信身份将在未来 Web 界面中完成。

退出评审前运行：

```bash
trac discuss query \
  --file story.md \
  --check-ready \
  --summary-only
```

所有线程均为 resolved 时，结果为：

```json
{"is_ready": true, "ready_blockers": []}
```

任一线程仍是 open 或 reopen，`is_ready` 就是 false。Runtime 把这个结果作为评审退出条件之一；它只判断讨论状态是否收敛，不判断回答在语义上是否正确。

## 文档变化后的重定位

`T-001` 不是写进文档的永久 ID。Tracks 每次全文扫描时，按线程在文档中的顺序重新生成 `T-NNN`。如果有人在它前面插入一条新线程，它可能变成 `T-002`。

真正用于重定位的是 query 返回的内容 token。写命令会重新扫描文档，并依次尝试：

1. 根据行数变化修正位置，精确匹配段落和根评论；
2. 在附近窗口中做模糊文本匹配；
3. 只用根评论和发起人寻找唯一候选；
4. 找不到则返回 `not_found`。

如果有多个相似候选，命令返回 `ambiguous`；如果内容找到了，但当前 `T-NNN` 已变化，命令返回 `stale`。这两种情况下都不会写文件。

```text
stale
```

正确处理方式不是重试同一条命令，而是重新 query，阅读当前文档，取得新的线程编号和 token。Tracks 在无法确认目标时选择不写，这种策略称为 **fail closed**。

> [!info] Levenshtein distance
>
> 模糊定位使用编辑距离衡量两个文本需要多少次插入、删除或替换才能相同。该算法由 Vladimir Levenshtein 于 1965 年提出，常用于拼写检查和近似字符串匹配。

## 当前限制

inline discussion 已经可以承担文档评审，但当前版本有清晰边界：

- CLI 没有真实身份认证。speaker 和 operator 都是调用者提供的文本；
- `T-NNN` 是一次扫描的显示标签，不是稳定 UUID；
- token 是 freshness/定位信息，不是安全 capability；
- parser 是针对 speaker tag 的逐行解析器，不是完整 CommonMark AST；
- 多行评论的 continuation 行目前不能完整 round-trip 到查询结果；
- `edit` 以 depth + speaker 寻找评论，同一 speaker 在同一深度有多条评论时可能不唯一；
- 写操作使用 `flock + tmp + rename` 串行化遵守协议的进程，但 Human 编辑器不会自动参与这把锁；
- 当前没有跨文件讨论查询，也没有 reaction、附件、时间戳或删除功能。

这些限制不影响最常见的使用方式：短评论、明确回复、每次写前重新查询、由发起人显式解决线程。未来 Web 界面会在同一 Markdown 协议之上增加可信身份和 Human/Agent 串行化，而不是把讨论搬到另一个不可见的数据孤岛中。
