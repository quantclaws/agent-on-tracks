---
interfaces_id: IF-003
spec_ref: SPEC-003
arch_ref: ARCH-003
created: 2026-07-31
status: draft
sha:
---

# Agent on Tracks v0.2 — 接口与类型化 Schema（增量）

> 本文是 IF-001（v0.1）的**增量**。事件信封（§2）、Command 基础结构（§4）、State 投影（§8）、Workflow 类型（§9）等**不变**，凡未提及者继承 IF-001。原 v0.2 范围不新增事件类型，仅扩展既有 payload 字段与封闭枚举，并新增 inline-discussion 旁路类型与 CLI 合同。**扩范围增量（FR-0160~0200，Aaron 扩容裁定）见 §10**：新增事件类型、Command kind、State 字段与 `trac approve` / `trac return` CLI 合同；§1「不新增事件类型」的陈述仅适用于原 v0.2 范围。

## 0. 延续性（什么不变）

- `EventEnvelope`（IF-001 §2）字段不变。
- `Command`（IF-001 §4）`kind` Literal **不新增成员**（discuss 不入事件 loop；校验复用 `validate_document`）。
- `State`（IF-001 §8）字段不变（门禁经 verdict 进入 State，`decide()` 仍纯）。
- `StageDef`/`Transition`/`Workflow`（IF-001 §9）不变。
- 封闭集仍用 `Literal`/`Enum`；`guard`/阶段名仍为受测字符串（IF-001 §1）。

## 1. 事件类型增量（无新类型，仅 payload 扩展）

事件类型清单（IF-001 §3）不新增。`outcome.received` 的 payload 在原有 `role, status, artifact_ref, self_report` 基础上**追加可选字段**：

| 字段 | 类型 | 语义 |
|:---|:---|:---|
| `diff_ref` | `str \| None` | 目标文件受控 diff 的引用（**权威产物**；artifact 真相，见 ARCH §4b） |
| `audit_evidence` | `str \| None` | 越权路径级证据（baseline 之外 diff 的路径列表；无越权为 None） |
| `failure_class` | `str \| None` | 失败矩阵分类（见 §1a）；成功为 None |

`verdict.passed` / `verdict.failed` 的 `check` 字段取值集合扩展（见 §5）。

### 1a. failure_class 封闭集

```python
FailureClass = Literal[
    "opencode_missing",      # opencode 可执行文件缺失
    "provider_unavailable",  # provider/model/凭据不可用
    "non_zero_exit",         # 非零退出
    "timeout",               # 超时（含子进程组清理）
    "json_truncated",        # stdout JSON 流截断/解析失败
    "no_target_diff",        # 退出 0 但目标文件无 diff
    "signal",                # SIGINT/kill-9
    "outcome_not_persisted", # 文件已改但 outcome 未落盘（reconcile）
    "over_reach",            # 后置审计发现越权 diff
]
```

## 2. Command 增量（无新 kind）

`Command.kind` Literal 不变（IF-001 §4）。`validate_document` 的 `params.checks` 取值集合扩展（见 §5），用于承载 template / discussion_ready 门禁。`dispatch_agent` 的 `params.assignment` 字段扩展见 §3。

## 3. Assignment 增量

在 IF-001 §5 `Assignment` 基础上追加**可选**字段，承载模板/skill 注入语义（实现层亦可由 `role` 从 canonical 文件构造，二者择一）：

```python
@dataclass(frozen=True)
class Assignment:
    # ... IF-001 原有字段不变（task_id, role, objective, scope_whitelist, budget, context, failure_evidence, simulate）
    template_kind: str | None = None   # 需注入的模板 kind（"story"/"spec"/"acceptance"），由 Runtime 从 tracks/templates/ 读取附入
    skill: str | None = None           # 需注入的 skill 名（"tracks-discuz"），正文由 Runtime 附入调用上下文
    skill_version: str | None = None   # 注入 skill 的版本（取自 SKILL.md frontmatter version），供 Sage 核对版本 identity
```

> `role` 内部仍小写 `scribe`/`sage`/`lex`（延续 IF-001）；OpencodeBackend 映射到首字母大写 opencode agent 名（`Scribe`/`Sage`）。`scope_whitelist` 语义 = 目标文档 + command_id 专属临时目录（ARCH §6）。`simulate` 注入纪律不变（仅 cli/executor 边界，纯函数不感知）。

## 4. Outcome 增量

在 IF-001 §6 `Outcome` 基础上追加可选字段（与 §1 `outcome.received` payload 对应）：

```python
@dataclass(frozen=True)
class Outcome:
    command_id: str
    status: Literal["done", "blocked", "failed"]
    artifact_ref: str | None
    self_report: str               # Agent 自述（Runtime 不信任）
    diff_ref: str | None = None    # 目标文件受控 diff（权威产物）
    audit_evidence: str | None = None  # 越权证据（路径级）
    failure_class: FailureClass | None = None  # 失败分类（§1a）
```

> 产物权威：`diff_ref`（目标文件受控 diff）为真相；`self_report` 与 stdout JSON 仅诊断，Runtime 不信任（ARCH §4b）。

## 5. Verdict 增量（check 封闭集扩展）

IF-001 §7 `Verdict.check` Literal 追加两个成员：

```python
check: Literal["schema", "scope", "trace", "scope_overflow", "format",
               "template",           # + v0.2：文档结构符合对应模板（FR-150）
               "draft_undecided",    # + v0.2：M-SPEC Sage draft/response 全部 [ ]
               "final_decided",      # + v0.2：Runtime finalization 后全部 [x]
               "discussion_ready"]   # + v0.2：文件内讨论线程全部 resolved（FR-100，只读查询 discuss）
```

- `template`：`validate_document(checks=["template"])` → 按 `tracks/templates/<kind>.md` 校验必备 frontmatter 字段 + level-2 章节（模板的 HTML 注释忽略；acceptance 的 level-2 章节随 FR/NFR 变化故不做章节名匹配），`evidence` 含不符项 `line:N`。spec 文档另过结构 lint（`check_spec_items`：`### FR-XXXX 标题` + **唯一** `- [ ] 已决定`/`- [x] 已决定` checkbox + `- **来源**：` + FR 的 `- **交付入口**：`；结构检查不判定 checkbox 状态；FR-20 scope 数所有 FR 条目，废弃项删除而非标记）。
- `draft_undecided`：仅 M-SPEC DRAFT/RESPOND 与 EXIT 转换前使用；每条 FR/NFR 必须为 `- [ ] 已决定`。Agent 产出 `[x]` 视为自批，`verdict.failed(check="draft_undecided")`。
- `final_decided`：仅 Runtime `finalize_spec_decisions` 后使用；每条 FR/NFR 必须为 `- [x] 已决定`，否则 `verdict.failed(check="final_decided")`（only YES means YES）。
- `discussion_ready`：`validate_document(checks=["discussion_ready"])` → 只读调 `discuss` query --check-ready；`is_ready=false` 时 `verdict.failed`，`evidence` 含 `ready_blockers`。
- `trace`（v0.2 扩范围语义，FR-0170）：`validate_document` 对 `doc == "acceptance.md"` **恒跑**（不经 `checks` 列表，类比 spec 恒跑 scope_overflow）：AC↔FR 双向覆盖校验（§10d），失败 → `verdict.failed(check="trace")`，`evidence` 为**完整孤儿清单**（含 `line:N`，不在首个失败处短路）。

## 6. inline-discussion 旁路类型（新增，非事件）

`tracks/discuss/model.py` 定义（doc 级旁路，不进 `events` 表）：

```python
@dataclass(frozen=True)
class Comment:
    depth: int                # '>' 个数（1=根）；depth=N 回复上方最近的 depth=N-1 评论（FR-050）
    speaker: str              # 显示大小写（'@' 已剥离）
    body: str
    line: int                 # 评论首行（1-indexed）
    text: str                 # 首行原文（rstripped）
    mentions: list[str]       # 本评论 body 中的 @提及（独立语义：请求谁回答）
    children: list["Comment"] # 嵌套下级回复（depth+1）

@dataclass(frozen=True)
class Thread:
    thread_id: str            # "T-NNN"，单次全文扫描内序号（非持久 ID，ARCH §3a）
    initiator: str            # 根评论 speaker
    status: Literal["open", "resolved", "reopen"]
    last_speaker: str
    reply_count: int
    snippet: str              # 根评论 body 前 80 字
    mentioned_agents: list[str]   # 去重
    root: Comment             # 回复树（根评论 + 嵌套 children）
    # 5 元组定位字段（L0/L1 定位提示，非持久 identity）
    total_lines: int
    anchor_line: int
    anchor_text: str
    root_line: int
    root_text: str

@dataclass(frozen=True)
class DiscussQuery:
    threads: list[Thread]
    is_ready: bool | None = None        # 仅 --check-ready 时填充
    ready_blockers: list[str] | None = None
    # --blocker 三类别（仅 --blocker 时填充）
    unanswered: list[str] | None = None
    unresolved: list[str] | None = None
    awaiting_my_reply: list[str] | None = None

@dataclass(frozen=True)
class LocateResult:
    status: Literal["unique", "ambiguous", "not_found", "stale"]
    thread_id: str | None = None     # unique 时填充
    candidates: list[int] | None = None  # ambiguous 时填充候选行号（fail closed，不写文件）
    # stale：token 重定位到的线程 thread_id 与给定 --thread-id 不符（重排致编号漂移）；不写文件，须重新 query
```

> 嵌套（FR-050，Aaron 决定 A）：`depth` 编码"回复谁"——depth=N 评论回复其上方最近的 depth=N-1 评论；要回复某条具体回复就再加一层 `>`。`Comment.children` 承载回复树。**评论级 token**（`comment_token` = text+depth+speaker+parent 文本，内容派生）用于 reply/edit 定位具体评论：在已重定位（fresh）的 thread 内按内容精确匹配，唯一才写、并列 → ambiguous、无 → not_found（fail closed）。**@mention 与 depth 正交**：`@Name` 表示"请求 Name 增加一个回答"，不表示"当前回复是对谁的回复"；`awaiting_my_reply` = 被 @mention 请求且该评论尚无下级回复。

> 归一化：strip + 合并连续空白 + Unicode NFC，不改大小写、不去 markdown 格式；speaker 比较 lowercase 归一化、显示保留原大小写（FR-060）。写命令仅在 `LocateResult.status == "unique"` 时执行（fail closed，FR-070）。freshness token（写命令权威 identity，FR-070）：thread 无持久 ID，`T-NNN` 是单次扫描的显示标签；reply/edit/set-status 携带 `--token`（query 返回的 5 元组 / anchor+root 内容），命令重扫描按内容 L0-L3 重定位并核对当前 thread_id 与给定一致；不符 → `stale`（不写、重新 query），并列/低置信 → `ambiguous`，L3 → `not_found`。

## 7. CLI 接口合同（增量）

IF-001 §10 既有命令不变。新增：

### 7a. `trac discuss`（doc 级旁路，不入事件 loop）

| 子命令 | 输入 | 成功输出 | 失败输出 | exit |
|:---|:---|:---|:---|:---|
| `trac discuss query --file <p> [--initiator A] [--blocker A] [--status s] [--check-ready]` | 文档路径 + 过滤 | stdout: `DiscussQuery` JSON | stderr: 原因（含 `line:N`） | 0 / 1 |
| `trac discuss start --file <p> --anchor-line <N> --speaker <A> <msg>` | anchor + 发言 | stdout: 新 `thread_id` | stderr: 原因 | 0 / 1 |
| `trac discuss reply --file <p> --thread-id <id> --token <t> --speaker <A> [--reply-to-token <ct>] <msg>` | 线程 + token + 回复（`--reply-to-token` 省略 = 回复根） | stdout: "ok" | stderr: stale/ambiguous/not_found（不写文件） | 0 / 1 |
| `trac discuss edit --file <p> --thread-id <id> --token <t> --depth <N> --speaker <A> <new>` | 定位 + token + 新内容 | stdout: "ok" | stderr: 非原作者/stale/ambiguous（不写） | 0 / 1 |
| `trac discuss set-status --file <p> --thread-id <id> --token <t> --status <resolved\|reopen> --operator <A>` | 线程 + token + 状态 | stdout: "ok" | stderr: 一致性违反/stale（operator≠initiator 时 resolved 被拒） | 0 / 1 |

- `--file` canonicalize + scope gate：须落在当前 repo / 授权目标文档内；拒绝 `../`、repo 外绝对路径、逃逸 symlink；违反则拒绝且不改文件（FR-080）。
- reply/edit/set-status 必带 `--token`（query 返回的内容定位 token），内部全文扫描 + 4 级降级定位 + 核对 thread_id；`stale`/`ambiguous`/`not_found` 时**不写文件**（fail closed，FR-070 freshness）。
- 写操作 flock 串行化（tmp + rename），自动空行分隔，parse 失败回滚（FR-110）。

> **gpt [RESOLVED]:** 与 ARCH-003 §3b / SPEC-003 FR-060 同源：CLI 合同只传 `--thread-id`，无 query revision 或旧 5 元组。重排后旧 `T-NNN` 可合法匹配另一条线程，fail-closed 无法区分 stale。若决定引入 freshness token，需在此表追加输入列（如 `--loc-token <opaque>`）并扩展 `LocateResult`；若决定用原子 query+write（同一 flock 持有期），需在 §7a 注明并发语义。选定前 AC-0605 的"重排后不错命中"在接口层不可验证。
>> **Scribe:** 已引入 freshness token（同 louke 方案、Aaron 确认）：§7a reply/edit/set-status 追加 `--token <t>` 输入列；`LocateResult.status` 扩展 `stale`（token 重定位线程的 thread_id 与给定不符 → 不写、重新 query）；§6 补 token 语义；`Assignment.skill_version` 承载 skill 版本 identity。AC-0605/0608 覆盖“重排后用旧 token 写 → stale 不写”。@gpt 请确认是否可标记 [RESOLVED]。

### 7b. `trac validate`（独立校验）

| 命令 | 输入 | 成功输出 | 失败输出 | exit |
|:---|:---|:---|:---|:---|
| `trac validate --file <p>` | 文档路径 | stdout: "valid" | stderr: 不符项（含 `line:N`） | 0 / 1 |

按对应模板校验结构（必备章节/frontmatter 字段）；可独立运行，亦被评审退出门禁复用（`validate_document(checks=["template"])`，FR-150）。

## 8. 文件 / 存储契约（增量）

IF-001 §11 既有路径不变。新增/明确：

| 路径 | 格式 | 写入者 | 读取者 |
|:---|:---|:---|:---|
| `tracks/agents/Scribe.md`、`tracks/agents/Sage.md` | opencode agent 定义（frontmatter + body） | 维护者（spec 交付物） | OpencodeBackend（物化源） |
| `tracks/skills/tracks-discuz/SKILL.md` | opencode skill（frontmatter + body） | 维护者（spec 交付物） | OpencodeBackend（注入 Sage 上下文） |
| `tracks/templates/{story,spec,acceptance,...}.md` | Markdown 模板 | 维护者 | templating.py、checks/validate |
| `<target repo>/.opencode/agents/<Name>.md` | 物化的 agent 定义（**瞬态**） | OpencodeBackend | opencode（按名解析）；终态清理 |
| 系统临时目录 `tracks-<command_id>/` | command_id 隔离的 Agent 专属临时目录 | Agent（白名单内） | Agent；终态清理 |
| `.tracks/projects/<ver>/{story,spec,acceptance}.md` | Markdown + 讨论 blockquote | executor / discuss writer / Human 手写 | validate、discuss parser、agents |

> 交付物（agents/skills/templates）随 tracks 版本固定；其 frontmatter 版本号在对应流程被修改的 tracks 版本升版（存在性 + 版本检查，落交付门禁，ARCH §9）。物化发现路径 = `.opencode/agents/<Name>.md`（复数，Aaron 决定）；命名/大小写可发现性以 spike 证明为准。

## 9. 后端选择与注入（边界纪律）

- `TRAC_AGENT_BACKEND=fake|opencode`（默认 `opencode`）：在 cli/executor 边界读取，决定 `dispatch_agent` 用 `FakeBackend` 还是 `OpencodeBackend`。
- `TRAC_FAKE_SIMULATE`：强制 fake 后端（即使 `TRAC_AGENT_BACKEND=opencode`）；并如 IF-001 经 `assignment.simulate` 控制 FakeAgent 分支。
- live opencode E2E 的 provider/model 由环境变量配置（Aaron §3.1）。
- `project()`/`decide()` 禁止读取环境变量或感知后端/测试模式（延续 IF-001 §5 纯函数边界）。

## 10. 扩范围增量（FR-0160~0200 + NFR-0040，Aaron 扩容裁定）

> normative 合同以 SPEC-003 SM-04/SM-05、FR-0160~0200 与 ACC-003 为准；实现设计见 design.md §1~§6。**全部合同已冻结、可编码**（design D-01~D-07 + C-01~C-03 经 Prism inline-discussion 裁定，见 design.md 各节线程）；原 `【待冻结】` 标注按 §10g 对应表落地。

### 10a. 新事件类型（IF-001 §3 清单追加）

| 事件类型 | payload | 发出者 | 备注 |
|:---|:---|:---|:---|
| `spec.decisions_finalized` | `converted: int` | executor（`finalize_spec_decisions` handler） | Lex pass + Human `no_comment` + discussion-ready 后；reducer 置 `decisions_finalized=true`、`exit_validated=false`，强制 final gate；reconcile 重放 converted 可为 0 |
| `acceptance.committed` | `commit_sha, acceptance_sha, final: bool` | executor（`_emit_committed` 三分支） | 镜像 `spec.committed`（FR-0160）；非 final → substate=LEX_REVIEW + reset review |
| `preview.generated` | `digest, summary` | executor（`generate_preview` handler） | SM-05.2；digest 算法 = D-01（design §5，已冻结）；Inc-3 可先用占位 digest，Inc-4 换 D-01 实算；summary = 三件套标题 + 条目计数 |
| `human.approval` | `actor, digest, ts` | CLI `trac approve`（仅 Human） | SM-05.3；Agent 不可代批（FR-0180 硬规则）；approve 前校验当前 digest == preview digest，不符拒绝并重生 preview（C-02） |
| `human.return` | `reason, to_stage: "M-STORY"\|"M-SPEC"\|"M-ACC"` | CLI `trac return`（仅 Human） | SM-05.4/7；CLI 显式校验 to_stage 合法，非法值报错拒绝、不落事件（C-03） |
| `approval.recorded` | `actor, digest, ts, readonly: true` | executor（`record_approval` handler） | 记账事件（FR-0190）+ reducer 置 `substate="ISSUES"`（SM-05.5，C-01）。**Inc-3 机制**（占位 digest 即可触发）；approval identity 载荷实算（digest=D-01、readonly=D-02）Inc-4 落地 |
| `issue.created` | `item_id, issue_id, digest` | executor（`create_issues`，per-item 子事件） | 断点续传 reconcile（D-06，已冻结；item = 每 FR/每 NFR，D-04）（design §6） |
| `issues.created` | `digest, mapping: {item_id: issue_id}, project` | executor（`create_issues` 汇总） | 幂等键 = digest（D-06，已冻结） |

### 10b. Command kind 增量（IF-001 §4 Literal 追加）

```python
kind: Literal[...,               # IF-001 原有成员不变
      "finalize_spec_decisions",# M-SPEC EXIT：Runtime 原子 [ ]→[x]，随后 final_decided 复验
      "generate_preview",        # SM-05.1→2（Inc-3；digest = D-01 已冻结，Inc-3 可先用占位 digest）
      "record_approval",         # SM-05.5（Inc-3 机制——占位 digest 触发 approval.recorded→substate=ISSUES；identity 载荷实算 Inc-4）
      "create_issues"]           # SM-05.6（Inc-5；D-04~D-06 已冻结）
```

`validate_document` / `commit_document` / `dispatch_agent` / `write_frontmatter` / `rollback_stage` 复用不变；新 kind 同样遵守写前日志（FR-30）+ 每 kind execute/reconcile（D-13）。

> **Prism [RESOLVED]:** Prism 评审（1 应修）：§10b 把 record_approval 标为 Inc-4【待冻结】，与 design §9（Inc-3 含 M-REQ-APPROVAL 事件/reducer/decide）、TP-003 §10 SM-05.5「Inc-3 即可测」/ SM-05.6「Inc-3 阶段 substate=ISSUES 可断言」及 C-01（ISSUES 物化为可测状态）冲突——若 record_approval 延到 Inc-4，则 APPROVED→ISSUES（SM-05.5）在 Inc-3 不可达，ISSUES 子状态无法按 NFR-0040 分阶段覆盖。裁定：record_approval 与 generate_preview 同处理——Inc-3 机制（用占位/临时 digest 触发 approval.recorded→substate=ISSUES），仅 approval identity 载荷（approval_digest/actor/readonly + digest 算法）保留【待冻结】待 Inc-4。请把 §10b record_approval 注释与 §10a approval.recorded 行的 Inc 标注按此对齐。
>> **Archer:** Archer：采纳裁定。§10b 已改——record_approval 注释为「SM-05.5（Inc-3 机制——占位 digest 触发 approval.recorded→substate=ISSUES；identity 载荷实算 Inc-4）」，与 generate_preview 同口径；§10a approval.recorded 行同步标注 Inc-3 机制 + C-01 substate=ISSUES 语义；design §9 Inc-3 加对应注记，三处对齐。另因 design D-01~D-07 + C-02 已全部冻结（见 design.md 各节线程），本文其余【待冻结】标注一并落地：§10a preview/issue.created/issues.created 行改引 D-01/D-04/D-06（issue 事件键统一为 item_id，覆盖每 NFR 1 Issue 的 D-04），§10c approval_digest/issues_created 注释改引 D-01/D-06，§10g 改为冻结记录。@Prism 复核后请 RESOLVED。

### 10c. State 字段增量（IF-001 §8 追加）

```python
acceptance_committed: bool = False    # M-ACC（镜像 spec_committed）
decisions_finalized: bool = False     # M-SPEC EXIT：Runtime 已完成 [ ]→[x]
preview_ready: bool = False           # M-REQ-APPROVAL
approved: bool = False
returned: bool = False
return_target: str | None = None      # human.return 的目标阶段
approval_digest: str | None = None    # digest 算法 = D-01（Inc-3 占位、Inc-4 实算）
approval_actor: str | None = None
issues_created: bool = False          # 幂等键语义 = D-06
```

- `stage` 取值追加 `M-ACC`、`M-REQ-APPROVAL`。
- M-REQ-APPROVAL substate 封闭集：`PREVIEW | AWAIT_HUMAN | APPROVED | RETURNED | ISSUES`（SM-05）。
- `AWAIT_HUMAN` 时 `awaiting="approval"`、`status="awaiting_human"`（decide 顶部 halt，天然 Human gate）。
- `_STAGE_ROLE_DOC` 阶段表：`M-STORY→(scribe, story.md)`、`M-SPEC→(sage, spec.md)`、`M-ACC→(sage, acceptance.md)`。

### 10d. check_trace 纯函数（validate.py，FR-0170）

```python
def check_trace(spec_text: str, acc_text: str) -> list[str]:
    """FR-0170 AC↔FR 双向覆盖（均硬错误）。返回完整孤儿清单消息（含 line:N），[] = 通过。
    正向：spec 每条 ### FR-XXXX / ### NFR-XXXX 须在 acceptance 有 ## FR-XXXX 章节且内含
         ≥1 条 ### AC-FRXXXX-YY；缺失 → 孤儿（报 FR ID + spec line:N）。
    反向：acceptance 每条 ### AC-FRXXXX-YY 回指的 FR/NFR 须在 spec 存在；
         失败 → 孤儿（报 AC ID + acceptance line:N）。
    不短路；讨论块（'>' 起始行）与 fenced code 跳过；无 I/O。"""
```

接线：`validate_document` 对 `doc == "acceptance.md"` 恒跑（从同目录读 `spec.md`；缺失 → `("trace", "acceptance validate requires spec.md in same dir")`）。M-ACC 的 checks 列表仍为 `["template"]`（outcome 即校验）/ `["template","discussion_ready"]`（退出门禁），trace 自动附加、不进 checks；`trac validate --file acceptance.md` 同样自动触发。

### 10e. CLI 合同增量（Human 审批动作）

IF-001 §10 / 本文 §7 既有命令不变。新增：

| 命令 | 输入 | 前置校验 | 成功输出 | 失败输出 | exit |
|:---|:---|:---|:---|:---|:---|
| `trac approve [--actor NAME]` | actor（缺省 git user） | 当前处于 M-REQ-APPROVAL / AWAIT_HUMAN | stdout: `approved <digest>` | stderr: 阶段/子状态不符 | 0 / 1 |
| `trac return --to <M-STORY\|M-SPEC\|M-ACC> --reason TEXT` | 目标阶段 + 产品理由 | 同上；目标须在封闭集内 | stdout: `returned to <stage>` | stderr: 阶段不符 / 目标非法 | 0 / 1 |

- 二者经 store.append 落 `human.approval` / `human.return` 事件（同 `trac triage` / `trac review` 模式），**不直接改状态**。
- Human gate 硬规则（FR-0180）：无 `human.approval` 事件，`decide()` 绝不产出进入下游（M-DESIGN）的 command；Agent 无 approve/return 能力（仅 CLI Human 动作）。

### 10f. 阶段转移表（executor 单一事实来源）

```python
_NEXT_STAGE = {"M-STORY": "M-SPEC", "M-SPEC": "M-ACC", "M-ACC": "M-REQ-APPROVAL"}
```

查不到（M-REQ-APPROVAL）→ `run.completed(terminal_state="boundary")`（SM-05.6：停在 M-DESIGN 边界，可休眠、事件回放恢复）。**M-SPEC 退出语义变更**：原 `run.completed` → `stage.entered(M-ACC)`（影响既有 e2e happy_path 断言，见 TP-003 §4a）。回退不经此表（走 `rollback_stage`）。

### 10g. 冻结记录（原【待冻结】清单，Prism 裁定）

FR-0190：digest 算法 = **D-01**（sha256_hex 固定标签拼接三件套 doc_body_sha）；readonly = **D-02**（逻辑只读：digest 快照 + 入口 stale 校验，不用文件权限）；stale 传播 = **D-03**（仅阻断 M-DESIGN+ 下游，不失效已建 Issues）；preview/approve 一致性 = **C-02**（不符拒绝 approve 并重生 preview）。FR-0200：拆分粒度 = **D-04**（每 FR/每 NFR 各 1 Issue）；Project 指定 = **D-05**（env：GITHUB_TOKEN + TRAC_GITHUB_PROJECT，仅效应边界读）；reconcile = **D-06**（per-item 子事件断点续传 + 汇总幂等）；v0.4 边界 = **D-07**（仅创建 + 记 digest/mapping，无持久注册表）。record_approval 归 **Inc-3 机制**（占位 digest），identity 载荷实算归 Inc-4。裁定原文见 design.md §4~§6 / 本文 §10b 讨论线程。
