---
interfaces_id: IF-003
spec_ref: SPEC-003
arch_ref: ARCH-003
created: 2026-07-31
status: draft
sha:
---

# Agent on Tracks v0.2 — 接口与类型化 Schema（增量）

> 本文是 IF-001（v0.1）的**增量**。事件信封（§2）、Command 基础结构（§4）、State 投影（§8）、Workflow 类型（§9）等**不变**，凡未提及者继承 IF-001。v0.2 不新增事件类型，仅扩展既有 payload 字段与封闭枚举，并新增 inline-discussion 旁路类型与 CLI 合同。

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
               "discussion_ready"]   # + v0.2：文件内讨论线程全部 resolved（FR-100，只读查询 discuss）
```

- `template`：`validate_document(checks=["template"])` → 按 `tracks/templates/<kind>.md` 校验必备章节/frontmatter 字段，`evidence` 含不符项 `line:N`。
- `discussion_ready`：`validate_document(checks=["discussion_ready"])` → 只读调 `discuss` query --check-ready；`is_ready=false` 时 `verdict.failed`，`evidence` 含 `ready_blockers`。

## 6. inline-discussion 旁路类型（新增，非事件）

`tracks/discuss/model.py` 定义（doc 级旁路，不进 `events` 表）：

```python
@dataclass(frozen=True)
class Thread:
    thread_id: str            # "T-NNN"，单次全文扫描内序号（非持久 ID，ARCH §3a）
    initiator: str            # 根评论 speaker
    status: Literal["open", "resolved", "reopen"]
    last_speaker: str
    reply_count: int
    snippet: str              # 根评论 body 前 80 字
    mentioned_agents: list[str]   # 去重
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

> 归一化：strip + 合并连续空白 + Unicode NFC，不改大小写、不去 markdown 格式；speaker 比较 lowercase 归一化、显示保留原大小写（FR-060）。写命令仅在 `LocateResult.status == "unique"` 时执行（fail closed，FR-070）。freshness token（写命令权威 identity，FR-070）：thread 无持久 ID，`T-NNN` 是单次扫描的显示标签；reply/edit/set-status 携带 `--token`（query 返回的 5 元组 / anchor+root 内容），命令重扫描按内容 L0-L3 重定位并核对当前 thread_id 与给定一致；不符 → `stale`（不写、重新 query），并列/低置信 → `ambiguous`，L3 → `not_found`。

## 7. CLI 接口合同（增量）

IF-001 §10 既有命令不变。新增：

### 7a. `trac discuss`（doc 级旁路，不入事件 loop）

| 子命令 | 输入 | 成功输出 | 失败输出 | exit |
|:---|:---|:---|:---|:---|
| `trac discuss query --file <p> [--initiator A] [--blocker A] [--status s] [--check-ready]` | 文档路径 + 过滤 | stdout: `DiscussQuery` JSON | stderr: 原因（含 `line:N`） | 0 / 1 |
| `trac discuss start --file <p> --anchor-line <N> --speaker <A> <msg>` | anchor + 发言 | stdout: 新 `thread_id` | stderr: 原因 | 0 / 1 |
| `trac discuss reply --file <p> --thread-id <id> --token <t> --speaker <A> <msg>` | 线程 + token + 回复 | stdout: "ok" | stderr: stale/ambiguous/not_found（不写文件） | 0 / 1 |
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
