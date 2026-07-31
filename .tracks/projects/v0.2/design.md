# Agent on Tracks v0.2 扩范围 — 实现设计（FR-0160~0200 + NFR-0040）

> 状态：已评审冻结（Prism inline-discussion 裁定 D-01~D-07 + C-01~C-04，原
> `【待确认】` 项全部定案，见各节讨论线程）。范围：把 v0.2 从「M-START→M-STORY→M-SPEC」
> 扩展到「→M-ACC→M-REQ-APPROVAL→(M-DESIGN 边界)」。normative 合同以 `spec.md`
> （SM-04/05、FR-0160~0200）与 `acceptance.md`（AC-FR0160~0200、AC-NFR0040）为准；
> 本文是实现设计，与规范漂移时以规范为准并回改本文。

## 0. 延续性（什么不变）

- 事件溯源内核不变：`project(events)->State` 折叠 + `decide(state)->Command|None` 纯函数
  （NFR-02）。无时钟/无 I/O/无 env，所有非确定性的唯一入口是事件。
- 写前日志（FR-30）+ 每 kind execute/reconcile（D-13）不变；新增 command kind 同样先
  `command.issued` 再执行、结果事件闭合，崩溃经 command_id 探针 reconcile。
- 后端 seam 不变：`AgentBackend.act()`（生产 OpencodeBackend / 确定 FakeBackend）；
  M-ACC 评审复用 Lex 真实 agent（FR-0020 已扩容为真实）。
- FR-150 格式门禁不变（outcome 即校验 `checks=["template"]`、退出门禁
  `["template","discussion_ready"]`、only YES means YES）。M-ACC 在其上**附加** FR-0170 trace。
- inline-discussion 旁路（`discuss/`）不变，M-ACC 评审照常可用。

**变更面**：`kernel/machine.py`（新阶段/子状态/事件/reducer/decide 分支）、
`executor/executor.py`（阶段转移表 + 新 command handler + acceptance.committed）、
`executor/validate.py`（FR-0170 trace）、`kernel/events.py`（新事件类型登记）、
`cli/main.py`（`trac approve` / `trac return`）、新增 `effects/github.py`（FR-0200）。

## 1. 阶段转移表（executor 单一事实来源）

`_do_write_frontmatter`（EXIT seal）发出 `stage.exited` 后，按当前阶段决定下一
`stage.entered`（或终止）。把现有「M-STORY→M-SPEC，else→run.completed」改为显式表：

| 当前阶段 | EXIT 后 | 终止? |
|:---|:---|:---|
| M-STORY | `stage.entered(M-SPEC)` | 否 |
| M-SPEC | `stage.entered(M-ACC)` | 否（**变更**：原为 run.completed） |
| M-ACC | `stage.entered(M-REQ-APPROVAL)` | 否 |
| M-REQ-APPROVAL | `run.completed(terminal_state="boundary")` | 是（停在 M-DESIGN 边界，SM-05.6） |

实现：executor 内一张 `_NEXT_STAGE = {"M-STORY":"M-SPEC","M-SPEC":"M-ACC",
"M-ACC":"M-REQ-APPROVAL"}` 字典；查不到（M-REQ-APPROVAL）→ `run.completed`，
`terminal_state="boundary"`（可休眠、事件回放恢复）。回退不经此表（走
`rollback_stage`，§6）。

## 2. FR-0170 — acceptance 双向覆盖 trace 校验（纯函数，先做）

validate.py 新增纯函数（与 `check_spec_items` 同级，无 I/O，可单测）：

```python
def check_trace(spec_text: str, acc_text: str) -> list:
    """FR-0170 AC↔FR 双向覆盖（均硬错误）。返回 line:N 消息，[] = 通过。
    讨论块忽略（与模板校验同）。仅用于 acceptance 文档种类。"""
```

规则（spec FR-0170）：
1. **正向**：spec 每条 `### FR-XXXX`/`### NFR-XXXX`（复用 `_spec_items` 取 ID）须在
   acceptance 有对应 `## FR-XXXX`/`## NFR-XXXX` 章节，且章节内 ≥1 条
   `### AC-FRXXXX-YY`/`### AC-NFRXXXX-YY`。缺章节或章节内无 AC → 孤儿（报 FR ID + spec `line:N`）。
2. **反向**：acceptance 每条 `### AC-FRXXXX-YY`/`### AC-NFRXXXX-YY` 回指的 FR/NFR 须在
   spec 存在。回指失败 → 孤儿（报 AC ID + acceptance `line:N`）。
3. 输出**完整孤儿清单**（不在首个失败处短路），供 `verdict.failed(trace)` 一次性反馈。

解析复用：spec 侧复用 `_spec_items`（已跳过 fenced code、按 ≤3 级标题切块）取 FR/NFR ID
集合；acceptance 侧新增轻量扫描——`^## (N?FR-\d{4})\b` 记章节及其行号区间，
`^### AC-(N?FR\d{4})-\d+\b` 记 AC 及其行号、归属章节。讨论块（`>` 起始行）跳过。

**接线**：`validate_document` 对 `doc=="acceptance.md"` **恒跑** trace（类比 spec 恒跑
scope_overflow，**不经 checks 列表**），从同目录读 `spec.md` 文本：

```python
if doc == "acceptance.md":
    spec_path = path.parent / "spec.md"
    if not spec_path.exists():
        return ("trace", "acceptance validate requires spec.md in same dir")
    issues = check_trace(spec_path.read_text(encoding="utf-8"), text)
    if issues:
        return ("trace", "; ".join(issues))
```

verdict 的 `check` 字段 = `"trace"`（IF-003 §5 check 封闭集新增 `trace`）。因此 M-ACC 的
outcome / 退出门禁 checks 仍为 `["template"]` / `["template","discussion_ready"]`（与 M-STORY/
M-SPEC 同），trace 在 acceptance 上自动附加。`trac validate --file acceptance.md` 同样自动
触发 trace（cmd_validate 对 acceptance 读同目录 spec.md）。

## 3. FR-0160 — M-ACC 阶段（同构复用 M-SPEC）

> **Prism [RESOLVED]:** Prism：C-04 FR-0160「继承 M-SPEC review 上下文」无需新机制——M-ACC 起草 dispatch 给 Sage 的上下文 = 工作区已 commit 的 spec.md（内含已 RESOLVED 的 inline-discussion 线程本就在文档内），沿用 M-SPEC 起草继承 M-STORY 上下文的同一装配路径。建议 §3 点一句实现口径。
>> **Archer:** Archer：同意 C-04。§3 已按此口径收敛——M-ACC 起草 dispatch 给 Sage 的上下文 = 工作区已 commit 的 spec.md 全文（已 RESOLVED 的 inline-discussion 线程本就内联在文档中），沿用 M-SPEC 起草继承 M-STORY 上下文的同一 prompt 装配路径，无新机制。@Prism 复核后请 RESOLVED。

machine.py 增量（最小改动，复用 M-SPEC 评审回路）：

- **stage 注释/映射**：`State.stage` 注释加 `M-ACC`。`_on_stage_entered` 的 substate 表加
  `"M-ACC": "DRAFT"`。
- **`_decide_draft` 泛化**：把硬编码二分支换成阶段表：
  ```python
  _STAGE_ROLE_DOC = {"M-STORY": ("scribe", "story.md"),
                     "M-SPEC":  ("sage",   "spec.md"),
                     "M-ACC":   ("sage",   "acceptance.md")}
  role, doc = _STAGE_ROLE_DOC[stage]
  committed = {"M-STORY": s.story_committed, "M-SPEC": s.spec_committed,
               "M-ACC": s.acceptance_committed}[stage]
  ```
- **评审子状态**：M-ACC 用 `LEX_REVIEW`（Lex 真实评审 acceptance），与 M-SPEC 同构。
  `_on_lex_verdict` 现含 `s.spec_committed = False`（RESPOND 重置）——须按阶段区分重置
  `spec_committed` 还是 `acceptance_committed`（按 `s.stage`）。
- **新 State 字段**：`acceptance_committed: bool = False`。
- **新事件 + reducer**：`acceptance.committed`（payload 同 spec.committed：commit_sha /
  acceptance_sha / final）。`_on_acceptance_committed`：`s.acceptance_committed=True`；
  非 final → `substate="LEX_REVIEW"` + `_reset_review`（镜像 `_on_spec_committed`）。
- **`_decide_exit` 泛化**：doc 取自 `_STAGE_ROLE_DOC[stage][1]`（M-ACC→acceptance.md）。
  退出门禁 checks 各阶段同为 `["template","discussion_ready"]`；M-ACC 的 trace 由
  `validate_document` 对 acceptance 恒跑自动附加（§2），无需进 checks。
- **executor `_emit_committed`**：三分支 story/spec/acceptance，分别发
  `story.committed`/`spec.committed`/`acceptance.committed`。
- **转移**：M-SPEC EXIT→M-ACC、M-ACC EXIT→M-REQ-APPROVAL（§1 表）。
- **回退**：M-ACC `stage.rolled_back` 目标 M-SPEC 或 M-STORY（SM-04.15）；
  `_on_stage_rolled_back` 现重置 `spec_committed/story_committed`——须也重置
  `acceptance_committed`。trace 缺口（FR-0170 失败且不可在 acceptance 修复）→ Human 裁定
  回退（经 `human.review(comment)` 或升级后人工 `rollback_stage`）。
- **M-SPEC review 上下文继承**（FR-0160）：Sage 起草 acceptance 的 prompt 装配沿用现有
  起草通道——spec.md 全文（含 seal 后正文）作为输入文档进入 prompt；M-SPEC 评审的结论性
  上下文已固化在 spec 正文与其 review 讨论记录（`discuss/` 旁路文件）中，起草 prompt 附带
  同 run 的 `discuss/` 现存记录即可，无新机制（Inc-2 实现时点到即可）。

validate 失败重派同一作者（Sage）≤3 超限升级 Human——复用现有 `_on_verdict_failed`
attempt 机制，无需新逻辑。

## 4. FR-0180 — M-REQ-APPROVAL 阶段（Human 审批门禁）

新阶段，**非评审回路**（无 agent 起草/评审），核心是 Human gate。

子状态（SM-05）：`PREVIEW → AWAIT_HUMAN → APPROVED → ISSUES →（退出边界）`，
或 `AWAIT_HUMAN → RETURNED →（回退）`。

- **`_on_stage_entered`**：表加 `"M-REQ-APPROVAL": "PREVIEW"`。
- **新 State 字段**：`preview_ready: bool`、`approved: bool`、`returned: bool`、
  `approval_digest: str|None`、`approval_actor: str|None`、`issues_created: bool`、
  `return_target: str|None`（RETURNED 的目标阶段）。
- **新事件 + reducer**：
  - `preview.generated`（payload：digest、summary）→ `s.preview_ready=True`，
    `substate="AWAIT_HUMAN"`，`s.awaiting="approval"`，`status="awaiting_human"`。
  - `human.approval`（payload：actor、digest、ts）→ `s.approved=True`、`s.awaiting=None`、
    `status="active"`、`substate="APPROVED"`、记 `approval_digest/actor`。
  - `human.return`（payload：reason、to_stage）→ `s.returned=True`、`s.awaiting=None`、
    `status="active"`、`return_target=to_stage`。
  - `approval.recorded`（payload：actor、digest、ts、readonly=true）→ 记账（FR-0190）**并
    `substate="ISSUES"`**（SM-05.5：记录 approval identity 即 APPROVED→ISSUES 转移，
    使 ISSUES 成为可观测物化状态，供 NFR-0040 断言）。
  - `issues.created`（payload：digest、issues 映射、project）→ `s.issues_created=True`。
- **decide 分支**（新增）：
  ```
  PREVIEW   : 未 preview_ready → Command(generate_preview)
  AWAIT_HUMAN: 返回 None（awaiting="approval"，decide 顶部已 halt）
  APPROVED  : Command(record_approval)（其结果事件 approval.recorded → substate=ISSUES，SM-05.5）
  ISSUES    : 未 issues_created → Command(create_issues)
              否则 → Command(write_frontmatter / exit)（SM-05.6；§1：M-REQ-APPROVAL→run.completed boundary）
  RETURNED  : Command(rollback_stage, to_stage=return_target)
  ```
- **Human gate 硬规则**（FR-0180）：无 `human.approval` 事件，`decide()` 绝不产出进入
  下游（M-DESIGN）的 command。`AWAIT_HUMAN` 时 `awaiting="approval"` → decide 顶部
  `if s.awaiting: return None` 天然 halt。Agent 无 `approve`/`return` 能力（仅 CLI Human 动作）。
- **CLI**（`cli/main.py`）：
  - `trac approve [--actor NAME]` → 校验当前在 M-REQ-APPROVAL/AWAIT_HUMAN，计算当前三件套
    digest，发 `human.approval{actor,digest,ts}`。**preview/approve digest 一致性
    （C-02，已冻结）**：approve 时校验「当前重算 digest == `preview.generated` 时 digest」，
    不符（AWAIT_HUMAN 期间三件套被改）→ 拒绝本次 approve（**非 fail run**）并重生 preview
    供 Human 复审，堵「看旧 preview 批新 digest」窗口。一致性校验自动化，是否批准仍属 Human。
  - `trac return --to <M-STORY|M-SPEC|M-ACC> --reason TEXT` → 发
    `human.return{reason,to_stage}`。除 CLI choices 枚举外，**显式校验**
    `to_stage ∈ {M-STORY, M-SPEC, M-ACC}`（SM-05.7），拒绝前向/自身阶段。
  - 二者经 store.append 落事件（同 `trac triage`/`trac review` 模式），不直接改状态。

> **Prism [RESOLVED]:** Prism 评审（1 应修 + 2 次要）：C-01（应修）物化 ISSUES 子状态——现 approval.recorded / issues.created 均不改 substate，导致 M-REQ-APPROVAL 缺 ISSUES 状态，与 normative SM-05（ISSUES 为独立状态 + SM-05.5/5.6）及 NFR-0040「每状态均被测试走到」相抵。裁定：approval.recorded reducer 设 substate=ISSUES（即 SM-05.5 记录 approval identity 触发的 APPROVED→ISSUES），decide 的 ISSUES 分支未 issues_created→create_issues 否则→write_frontmatter/exit（SM-05.6），APPROVED 分支只做 record_approval。C-02 trac approve 重算当前三件套 digest 与 preview 时 digest 比对，不一致则拒绝本次 approve（非 fail run）并重生 preview 供 Human 复审，堵「看旧 preview 批新 digest」窗口（一致性校验自动化，是否批准仍属 Human）。C-03 trac return --to 强制 ∈ {M-STORY, M-SPEC, M-ACC}（SM-05.7），其它值 CLI 报错拒绝、不落事件。
>> **Archer:** Archer：三项均采纳。C-01 §4 已物化——approval.recorded reducer 置 substate=ISSUES（SM-05.5），decide APPROVED 分支只发 record_approval、ISSUES 分支未 issues_created→create_issues 否则→exit（SM-05.6），子状态五元封闭集 {PREVIEW, AWAIT_HUMAN, APPROVED, ISSUES, RETURNED} 与 interfaces §10c 一致。C-02 由【待确认】转正：trac approve 重算当前三件套 digest 与 preview 时 digest 比对，不一致→拒绝本次 approve（不 fail run）并重生 preview 供 Human 复审；§10 第 8 项随之冻结。C-03 §4 已显式校验 to_stage ∈ {M-STORY, M-SPEC, M-ACC}，非法值 CLI 报错拒绝、不落事件。@Prism 复核后请 RESOLVED。

## 5. FR-0190 — baseline digest、approval identity、freshness（已冻结 D-01~D-03）

- **digest 算法（D-01）**：`revision_digest = sha256_hex("story:" + doc_body_sha(story.md)
  + "\nspec:" + doc_body_sha(spec.md) + "\nacc:" + doc_body_sha(acceptance.md))`。复用现有
  `frontmatter.doc_body_sha`（已剥 frontmatter，防 seal sha 自引用）；先各自 body-sha 再带
  固定标签拼接以消除拼接边界碰撞，顺序 story→spec→acc 固定。摘要（summary）= 三件套标题 +
  各自条目计数（FR/NFR 数）的人类可读串。
- **approval identity**：`human.approval` 事件绑定当时 digest；`approval.recorded` 记
  `{actor, digest, ts, readonly:true}`。actor 来自 `trac approve --actor` 或 git user。
- **readonly（D-02）**：逻辑只读——「approved digest 快照 + 入口 stale 校验」，**不用文件
  权限、不 chmod**（事件溯源友好，保 `decide()` 纯、可回放）：进入下游（M-DESIGN 及之后）
  前重算 digest，与 `approval_digest` 不符 → `approval stale` → 阻断、须重走 M-REQ-APPROVAL。
- **freshness / stale 传播范围（D-03）**：三件套任一内容变化 → digest 不匹配 → approval
  stale。v0.2 **仅阻断下游**（M-DESIGN+），**不自动失效已建 Issues**（Issues 与 digest 的
  再同步属 v0.4 需求追踪注册表职责；本版 `issues.created` 记录其创建时 digest，供 v0.4 对账）。
- **可重现/可回放**：digest 纯由内容算出（无时间戳参与判定），事件回放可重建 approval
  状态与 stale 判定。

> **Prism [RESOLVED]:** Prism 裁定（本节【待确认】冻结）：D-01 digest = sha256_hex（「story:」+ doc_body_sha(story.md) +「\nspec:」+ doc_body_sha(spec.md) +「\nacc:」+ doc_body_sha(acceptance.md)）；复用现有 frontmatter.doc_body_sha（已剥 frontmatter，防 seal sha 自引用），先各自 body-sha 再带固定标签拼接以消除拼接边界碰撞，顺序 story→spec→acc 固定。D-02 readonly = 逻辑只读（approved-digest 快照 + 入口 stale 校验），不用文件权限、不 chmod，保 decide() 纯、可回放。D-03 stale 仅阻断下游（M-DESIGN+），不自动失效已建 Issues（再同步归 v0.4；issues.created 记创建时 digest 供对账）。请据此去掉本节【待确认】。
>> **Archer:** Archer：D-01/02/03 全部采纳，§5【待确认】已按裁定去除。D-01 digest = sha256_hex("story:"+doc_body_sha(story.md)+"\nspec:"+doc_body_sha(spec.md)+"\nacc:"+doc_body_sha(acceptance.md))，复用 frontmatter.doc_body_sha（剥 frontmatter 防 seal sha 自引用），固定标签+固定顺序消除拼接边界碰撞。D-02 readonly = 逻辑只读（approved-digest 快照 + 入口 stale 校验），不 chmod，decide() 保持纯函数可回放。D-03 stale 仅阻断 M-DESIGN+ 下游，不自动失效已建 Issues；issues.created 记创建时 digest 供 v0.4 对账。Inc-4 解锁。@Prism 复核后请 RESOLVED。

## 6. FR-0200 — spec → GitHub Issues 拆分与 Project 关联（已冻结 D-04~D-07）

外部副作用，按 NFR-0030 风格处理失败：

- **触发**：APPROVED 且 `approval.recorded` 后，`create_issues` command（§4 decide）。
- **拆分粒度（D-04）**：**每 FR / 每 NFR 各 1 Issue**（对称）。`### FR-XXXX 标题` → Issue
  标题「[FR-XXXX] 标题」、`### NFR-XXXX 标题` → 「[NFR-XXXX] 标题」；body = 该条正文 +
  其 AC 清单 + baseline digest。Issues = 需求追踪身份（非执行单元）。
- **Project 关联（D-05）**：创建后加入指定 GitHub Project。project 由 env 指定
  （`GITHUB_TOKEN` + `TRAC_GITHUB_PROJECT`），**仅在 `effects/github.py` 效应边界读、
  不进 `decide()`**；fake 通道未设 env 用确定 stand-in、不触网。
- **effects 边界**：新增 `effects/github.py`（类比 `effects/opencode.py` 的边界纪律）：
  - `create_issue(title, body, labels) -> issue_id`、`add_to_project(issue_id, project)`。
  - 认证经 env（`GITHUB_TOKEN`）；网络失败分类（auth / network / rate_limit / not_found）。
  - FakeBackend 通道提供确定 stand-in（写本地 `issues.json` 或返回固定 ID），E2E fake 通道
    不触网（继承 conftest fake 强制）。
- **失败处理**（NFR-0030 风格）：报告原因、发 command/outcome 事件、**不写半成品**、
  可恢复重试（恢复语义见 D-06 断点续传）。
- **幂等 / reconcile（D-06）**：**per-item 断点续传**——每建一个 Issue 立即落
  `issue.created{item_id, issue_id, digest}` 子事件，全部完成后落
  `issues.created{digest, mapping:{item_id: issue_id}}` 汇总；崩溃后 reconcile 按 `item_id`
  去重、**仅补齐缺失、绝不重建**；同一 baseline digest 重复进入（事件回放/重入）→ 见
  `issues_created=True` 即整体跳过。
- **v0.4 关系（D-07）**：本版仅创建 Issues + 记 digest/mapping，**不建持久 FR↔Issue 映射
  注册表**（属 v0.4 trace/reach）。`issues.created` 的 mapping 事件为 v0.4 提供对账原料。
- **范围边界**：Issues 创建为 M-REQ-APPROVAL 退出最后一步；创建后 `run.completed
  (terminal_state="boundary")`，不推进 M-DESIGN/实现。

> **Prism [RESOLVED]:** Prism 裁定（本节【待确认】冻结）：D-04 拆分粒度 = 每 FR / 每 NFR 各 1 Issue（对称），标题 [FR-XXXX] 标题 / [NFR-XXXX] 标题，body = 该条正文 + 其 AC 清单 + baseline digest。D-05 Project 由 env 指定（GITHUB_TOKEN + TRAC_GITHUB_PROJECT），仅在 effects/github.py 效应边界读、不进 decide()；fake 通道未设 env 用确定 stand-in、不触网。D-06 reconcile = per-item 断点续传：每建一个 Issue 立即落 issue.created{item_id, issue_id, digest} 子事件，全部完成后落 issues.created{digest, mapping} 汇总；崩溃后按 item_id 去重、仅补齐缺失、绝不重建，同 digest 重入见 issues_created=True 即整体跳过。D-07 v0.2 只创建 + 记 digest/mapping，不建持久 FR↔Issue 注册表（mapping 事件即 v0.4 对账原料）。请据此去掉本节【待确认】。
>> **Archer:** Archer：D-04/05/06/07 全部采纳，§6【待确认】已按裁定去除。D-04 每 FR / 每 NFR 各 1 Issue（对称），标题 [FR-XXXX]/[NFR-XXXX] + body = 正文 + AC 清单 + baseline digest。D-05 GITHUB_TOKEN + TRAC_GITHUB_PROJECT 仅在 effects/github.py 边界读、不进 decide()；fake 通道无 env 用确定 stand-in、不触网。D-06 per-item 断点续传：每建一个 Issue 即落 issue.created{item_id, issue_id, digest}，完成后落 issues.created{digest, mapping} 汇总；崩溃 reconcile 按 item_id 去重仅补齐、绝不重建，同 digest 重入见 issues_created=True 整体跳过。D-07 本版不建持久 FR↔Issue 注册表，mapping 事件为 v0.4 对账原料。Inc-5 解锁（live 仍依赖凭据）。@Prism 复核后请 RESOLVED。

## 7. NFR-0040 — 状态机全覆盖（测试设计）

- test-plan 维护「转移 `SM-XX.N` → 测试用例」清单，逐条对应、无缺口；清单内测试须存在且通过。
  机器化 trace 工具属 v0.3，本版用 test-plan 人工清单（合入前检查）。
- 每条 SM-01~05 转移 ≥1 测试走到一次。非 happy path 必含（spec NFR-0040）：
  validate fail 重派、≤3 超限升级 Human、REJECTED（SM-02.2）、scope_overflow 回退
  （SM-03.4/15）、格式终验 fail（SM-03.14/SM-04.14）、**trace 失败（SM-04.3/4）**、
  **M-REQ-APPROVAL RETURNED 回退（SM-05.7）**、**approval stale 阻断下游（FR-0190）**、
  **GitHub Issues 创建失败/reconcile（FR-0200）**、awaiting_human 休眠后事件回放恢复。
- 分层（test-plan §2）：unit（check_trace、digest、新 reducer/decide 分支）；integration
  （M-ACC validate+trace 门禁协作、M-REQ-APPROVAL Human gate）；e2e fake 通道（M-ACC 起草/
  评审旅程、approve/return 旅程、stale 阻断、Issues stand-in 失败/reconcile）。

## 8. 事件 / Command / State 增量汇总

**新事件类型**（events.py 登记）：`acceptance.committed`、`preview.generated`、
`human.approval`、`human.return`、`approval.recorded`、`issue.created`（子）、
`issues.created`（汇总）。

**新 Command kind**：`generate_preview`、`record_approval`、`create_issues`。
（`validate_document`/`commit_document`/`write_frontmatter`/`rollback_stage` 复用。）

**新 State 字段**：`acceptance_committed`、`preview_ready`、`approved`、`returned`、
`approval_digest`、`approval_actor`、`issues_created`、`return_target`。

**IF-003 §5 check 封闭集**：新增 `trace`。

## 9. 实现增量排序（建议）

1. **Inc-1 FR-0170**：`check_trace` 纯函数 + validate_document acceptance 接线 + `trace`
   check + unit 测试。（无状态机改动，最先、最独立，M-ACC 的前置。）
2. **Inc-2 FR-0160 M-ACC**：machine.py 阶段/子状态/`_STAGE_ROLE_DOC`/acceptance.committed
   + executor 转移表 M-SPEC→M-ACC + `_emit_committed` 三分支 + 退出门禁 trace + e2e 旅程。
3. **Inc-3 FR-0180 M-REQ-APPROVAL Human gate**：新阶段/子状态/事件/reducer/decide + CLI
   approve/return + 转移 M-ACC→M-REQ-APPROVAL→boundary + e2e approve/return 旅程。
   `record_approval` 属 Inc-3 机制（占位 digest 触发 approval.recorded→substate=ISSUES，
   使 SM-05.5/5.6 在 Inc-3 可测）；approval identity 载荷实算归 Inc-4（IF-003 §10b）。
4. **Inc-4 FR-0190**：digest/preview/approval identity/freshness（D-01~D-03 + C-02 已冻结，
   可编码）。
5. **Inc-5 FR-0200**：effects/github.py + create_issues + 幂等/reconcile（D-04~D-07 已冻结，
   可编码；live 通道依赖 GITHUB_TOKEN）。
6. **NFR-0040** 测试清单随各 Inc 累积，最后补齐 SM 全覆盖核对。

> 全部增量无开放点，可按序编码（Prism 评审通过，裁定见各节讨论线程）。

## 10. 风险与未决

- **原 `【待确认】` 清单——已全部冻结**（Prism 裁定，逐项对应）：
  1. FR-0190 digest 算法 → **D-01**：sha256_hex 固定标签拼接三件套 doc_body_sha（§5）。
  2. FR-0190 readonly 实现 → **D-02**：逻辑只读，digest 快照 + 入口 stale 校验（§5）。
  3. FR-0190 stale 传播范围 → **D-03**：仅阻断下游，不失效已建 Issues（§5）。
  4. FR-0200 Issues 拆分粒度 → **D-04**：每 FR / 每 NFR 各 1 Issue（§6）。
  5. FR-0200 Project 指定方式 → **D-05**：env（GITHUB_TOKEN + TRAC_GITHUB_PROJECT），
     仅效应边界读（§6）。
  6. FR-0200 reconcile 粒度 → **D-06**：per-item 子事件断点续传 + 汇总（§6）。
  7. FR-0200 与 v0.4 注册表边界 → **D-07**：本版仅创建 + 记 digest/mapping（§6）。
  8. FR-0190 preview/approve digest 一致性 → **C-02**：不符拒绝本次 approve 并重生
     preview（§4 CLI）。
- **bootstrap 自举**（已确认）：tracks 自身仓库演练**默认 fake 通道**；live 通道 opt-in
  （显式 env + 凭据、CI 独立 job）。
- **M-SPEC 退出语义变更**（→M-ACC 而非 completed）影响现有 e2e happy_path（其断言
  `stage.exited + run.completed` 结尾）——Inc-2 须同步更新 happy_path 至 M-ACC/M-REQ-APPROVAL
  边界，或拆分「M-SPEC 止」与「全流程」两个 e2e（已确认，Inc-2 落地）。

> **Prism [RESOLVED]:** Prism 确认（无异议，收尾）：自举 GitHub 副作用走 fake-first、live opt-in（显式 env + 凭据、CI 独立 job），tracks 自身仓库演练默认 fake；M-SPEC 退出语义 completed→M-ACC 打断现有 happy_path e2e，由 Inc-2 同步（断言更新至新边界，或拆「M-SPEC 止」与「全流程」两条 e2e）；check 封闭集加 trace、§8 事件/Command/State 增量均确认。NFR-0040 test-plan 另需覆盖：ISSUES 状态 + SM-05.5/5.6、approve digest-mismatch 重生 preview、return 非法 to_stage 拒绝、Issues per-item reconcile 断点续传。设计整体通过。
>> **Archer:** Archer：确认一致，收尾项已落地。自举默认 fake、live opt-in（显式 env + 凭据、CI 独立 job）已写入 §10；happy_path e2e 由 Inc-2 同步（更新断言至新边界或拆「M-SPEC 止」/「全流程」两条）；check 封闭集 +trace、§8 增量不变。test-plan 已补四项 NFR-0040 覆盖：ISSUES 状态 + SM-05.5/5.6、approve digest-mismatch 拒绝并重生 preview、return 非法 to_stage 拒绝不落事件、Issues per-item reconcile 断点续传。§10【待确认】清单 8 项全部冻结（D-01~D-07 + C-02），§9 注记 Inc-4/5 解锁。@Prism 全部线程复核后可 RESOLVED。

