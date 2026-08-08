# Inline-discussion 协议参考

## Speaker tag

Canonical comment：

```markdown
> **Speaker [STATUS]:** body
>> **Speaker:** reply
```

Parser 接受三种等价 speaker tag：

```markdown
> **Name:** body
> **Name**: body
> Name: body
```

加粗形式可使用 Unicode 和空格；plain 形式的 Name 仅允许 ASCII identifier。可选前导 `@` 会从 speaker 名中移除，显示大小写保留。

以下不会形成 comment：普通 blockquote、无冒号加粗、`[!WARNING]` admonition，以及 Note/Warning/Tip/Important/Definition/Example/Remark/Attention/Caution 等说明标签。

Body 中 `@Name` 收集 mention，当前只支持 ASCII identifier。Mention 表示请求回答，不决定回复关系。

## Thread 与 comment

Comment 字段包括 depth、speaker、body、line、原始首行、mentions 与 children。

Root depth 为 1。Depth N 的 comment 挂到它上方最近一个 depth<N 的 comment。缺少中间深度时采用 best-effort，例如 depth 3 可以直接挂到 root。

Thread 是一次全文扫描得到的不可变视图，包含：

- `thread_id`
- `initiator`
- `status`
- `last_speaker`
- `reply_count`
- `snippet`
- `mentioned_agents`
- root comment tree
- anchor/root 的五个定位提示

Anchor 是 root 上方最近的非空、非 blockquote 行；fenced code 中内容不参与 thread 解析。Snippet 为 root body 规范化后的前 80 个字符。

规范化会 trim、折叠连续空白并做 Unicode NFC，不改变大小写和 Markdown。

## 状态

状态只由 root comment 决定：

| 状态 | Canonical marker | 是否阻塞 ready |
|---|---|---|
| open | 无 marker | 是 |
| resolved | `[RESOLVED]` | 否 |
| reopen | `[REOPEN]` | 是 |

Reply 中的状态文本不改变 thread。

Document ready 当且仅当全部 thread 为 resolved；无 thread 的文档 ready。Runtime 的 `discussion_ready` validator 与 `trac discuss query --check-ready` 使用同一规则。

Set-status 的 resolved 要求 operator 文本等于 initiator，reopen 允许任何 operator。当前没有真实身份认证，这只是格式一致性规则。

## Identity 与 token

`T-NNN` 按文档顺序在每次扫描时生成，不写入 Markdown，也不进入 event store。它只是显示标签，不是稳定 identity。

Thread token 是五元组：

```text
total_lines / anchor_line / anchor_text / root_line / root_text
```

Comment token 包含：

```text
text / depth / speaker / parent
```

CLI 推荐传输形式为 `z1.<base64url(zlib(json))>` 的 `token_str`；inline JSON 与 legacy base64url 仍兼容。Compact 查询省略 legacy dict 与定位字段，但保留 token_str。

Token 只验证 shape，用于内容重定位和 freshness。它无签名、无 file/repo binding、不证明调用者身份，不是安全 capability。

定位成功后，如果当前 T-NNN 与请求不一致，返回 stale 并拒绝写入。

## 定位算法

写命令重新扫描全文，按以下顺序定位 thread：

**L0 精确定位**：根据文件总行数变化计算全局 delta，要求修正后的 root line、root text 与 anchor text 全部相等。

**L1 窗口模糊定位**：在预计位置附近，以 Levenshtein distance 比较 root 与 anchor。距离阈值为最小 5 或文本长度的 20%。

**L2 root-only**：忽略 anchor 与行号，限定相同 initiator，选择 root text 距离最小的唯一候选。

**L3 not found**：以上均未命中。

唯一候选返回 `unique`；多个候选返回 `ambiguous`。Comment 定位更严格，只做 text+depth+speaker+parent 精确匹配。

Writer 仅在 unique 时修改文件。Malformed、stale、ambiguous、not_found 都 fail closed，目标文件保持不变。

## 当前限制

Inline discussion 是文档层 bypass：内容保存在 Markdown，不产生新的 Runtime event，也没有 sidecar discussion database。

当前限制：

- 无真实身份认证与授权；
- T-NNN 不持久；
- token 不签名；
- parser 是逐行规则，不是完整 CommonMark AST；
- 多行 continuation 无法完整 round-trip 到 query body；
- edit 以 depth+speaker 寻找评论，重复组合可能不唯一；
- 写锁使用 POSIX `fcntl.flock`，不支持 Windows；
- Human 编辑器不会自动参与 flock；
- 无跨文件查询、删除、附件、reaction 或时间戳。

写操作采用 `<file>.lock`、临时文件和 rename。Lock/tmp 文件由项目 `.gitignore` 排除。协议保证遵守同一 writer 的进程串行化，不保证多人编辑器实时协作。
