---
test_plan_id: TP-003
spec_ref: SPEC-003
arch_ref: ARCH-003
created: 2026-07-31
status: draft
sha:
---

# Agent on Tracks v0.2 — 测试计划

- **Related acceptance**: `.tracks/projects/v0.2/acceptance.md`
- **Related interfaces**: `.tracks/projects/v0.2/interfaces.md`（断言依据，见 §6.5）

> 延续 TP-001（v0.1）的分层与纪律；v0.2 新增**双通道 E2E**（fake deterministic + live opencode）与 **opencode 外部依赖分层**（§5）。凡未提及者继承 TP-001。

## 1. Stance and Boundaries

### 1.1 黑盒立场

测试只声明**系统外可观察**的方法。可观察对象限于：

- CLI 端点（`trac` 各子命令，含新增 `trac discuss` / `trac validate`）的 stdout/stderr/exit code。
- 持久化数据：`.tracks/runtime/tracks.db` 的 `events` 表（断言主源）、投影表。
- 文档文件：`.tracks/projects/<ver>/{story,spec,acceptance}.md`（含 inline-discussion blockquote）。
- `trac discuss query` 的 JSON 输出（`DiscussQuery`，IF-003 §6）。
- git 状态（分支、提交、工作区 diff）。

### 1.2 非对象（测试不直接依赖）

`discuss/parser.py` 内部数据结构、`OpencodeBackend` 内部 prompt 构造、`audit.py` 内部 baseline 表示、State 内部字段——这些不作为断言落点；需观察者由 interfaces.md 提供出口（§6.5）。

### 1.3 作弊模式与纪律

继承 TP-001 §7 与模板 §1.3/§1.4（CI 强制 AC 追踪、断言禁忌、测试变更分类、testability fallback）。**禁止**"impl 与 spec 不符 → 改测试"。

### 1.4 测试分工

- Unit：实现者（Devon，R-G-R 随实现提交）。
- Integration / E2E：测试负责人（Shield），覆盖 interfaces.md 跨模块合同与用户旅程。
- Ground Truth（§3）：未参与被测实现者或第三方参考。

## 2. 分层策略与目录结构

| 层 | 目录 | 职责 | 频率 |
|:---|:-----|:-----|:-----|
| unit | `tests/unit/` | discuss parser/locate/writer、templating、validate（template/discussion_ready）、**check_trace（FR-0170）**、**M-ACC / M-REQ-APPROVAL 新 reducer/decide 分支（SM-04/05）**、后端选择逻辑、events 字段扩展 | 每次保存 |
| integration | `tests/integration/` | OpencodeBackend（fake opencode stand-in）+ audit 协作、discuss+validate 门禁协作、runtime gate（discussion_ready verdict）、**M-ACC validate+trace 门禁协作**、**M-REQ-APPROVAL Human gate（awaiting halt / approve / return）** | 每次提交 |
| e2e（fake 通道） | `tests/e2e/` | deterministic 工作流（继承 TP-001）+ discuss CLI 旅程 + validate 门禁 + **M-ACC 起草/评审旅程 + approve/return/boundary 旅程** | 每次提交（CI 必跑） |
| e2e（live 通道） | `tests/e2e_live/` | 真 opencode + env provider/model，断言协议/权限/目标 diff/格式/恢复，**不断言文本** | 缺凭据 skip；CI 独立 required job、本地 opt-in |

```
tests/
├── unit/
│   ├── test_discuss_parser.py     # 全文扫描、canonical/人工写法等价、说明标签不误判、fenced code 跳过
│   ├── test_discuss_locate.py     # L0-L3、fail-closed 歧义（→ ambiguous 不写）
│   ├── test_discuss_writer.py     # canonical 输出、空行分隔、状态权限一致性
│   ├── test_discuss_model.py      # Thread/DiscussQuery 类型
│   ├── test_discuss_cli.py        # discuss CLI 子命令（unit 层）
│   ├── test_discuss_gate.py       # discussion_ready 门禁（unit 层）
│   ├── test_templating.py         # 模板加载（按 kind）
│   ├── test_validate.py           # template 结构校验（unit 层）
│   ├── test_trace.py              # check_trace 双向覆盖/孤儿清单/line:N（FR-0170）
│   ├── test_machine_acc.py        # M-ACC reducer/decide 分支（SM-04）
│   ├── test_machine_approval.py   # M-REQ-APPROVAL reducer/decide 分支（SM-05）
│   ├── test_backend_select.py     # TRAC_AGENT_BACKEND / TRAC_FAKE_SIMULATE 边界注入
│   ├── test_cli_approval.py       # trac approve/return CLI 拒绝（C-02/C-03）
│   ├── test_digest.py             # digest 算法/边界标签拼接（D-01/D-02）
│   ├── test_events_validate.py    # 事件 schema 校验
│   └── test_deliverables.py       # `trac check deliverables` 门禁单测（四交付物 + version + IQ）
├── integration/
│   ├── test_opencode_backend.py   # fake opencode stand-in：物化、prompt 构造、diff 为产物、失败矩阵 8 类、重派 reconcile
│   ├── test_executor_reconcile.py # reconcile（D-11/D-13 悬挂清理、outcome 扩展字段）
│   ├── test_store.py              # 事件存储/投影
│   ├── test_github_effects.py     # Issues 副作用 + per-item reconcile（stand-in，D-04~D-06）
│   └── test_trace_gate.py         # acceptance 恒跑 trace → verdict.failed(trace) → 重派/≤3 升级 Human
├── e2e/                           # fake 通道（deterministic，CI 必跑）
│   ├── test_happy_path.py         # M-START→M-SPEC 止 + M-STORY reviewer 回路（test_story_review_loop）
│   ├── test_full_journey.py       # 全流程：M-STORY→M-SPEC→M-ACC→M-REQ-APPROVAL→boundary
│   ├── test_rejection.py          # REJECTED（no-go/park，参数化）
│   ├── test_retry_escalation.py   # validate fail 重派带证据 + 3 次升级 awaiting_human
│   ├── test_respond_paths.py      # RESPOND 回路（Sage comment / Human revise + 错误状态拒绝）
│   ├── test_scope_overflow.py     # scope_overflow → ROLLBACK
│   ├── test_start_guards.py       # dirty/reinit/empty + active-run backlog + 未合并分支 confirm（SM-01）
│   ├── test_approval_journey.py   # approve/return + AWAIT_HUMAN 休眠回放 + stale 阻断（SM-05）
│   ├── test_recovery.py           # opencode 失败矩阵 reconcile（物化/临时目录清理）
│   ├── test_acc_journey.py        # 【待补】SM-04 起草/评审/trace 失败重派/回退全旅程 e2e
│   └── test_exit_gate.py          # 退出关重派 e2e：over-reach / no-target-diff 后重派→attempt 递增→3 次→awaiting_human
├── e2e_live/                      # live 通道（真 opencode，缺凭据 skip）
│   └── test_live_agent.py         # 真启动/权限/JSON 协议/目标 diff/格式/恢复，不断言文本
├── ground_truth/
│   └── discuss_reference.py       # inline-discussion 解析/定位参考实现（不 import tracks）
└── conftest.py                    # ~ TP-001 + fake 后端强制（fake 通道）/ live opt-in（live 通道）
```

比例目标延续 TP-001：unit ≈ 50%、integration ≈ 30%、e2e ≈ 20%。

## 3. Ground Truth（discuss 算法正确性）

inline-discussion 解析与 4 级降级定位属"规则/算法正确性"，须用独立参考（模板 §3）：

- `tests/ground_truth/discuss_reference.py`：**不 import `tracks.*`**（CI 静态检查），仅标准库 + `tests/assets/`  fixtures。
- 解析 ground truth：手工标注的 markdown fixture（含 canonical/人工写法、说明标签、fenced code、嵌套回复），参考实现独立产出 thread 集，与 `discuss/parser.py` 输出比对。
- 定位 ground truth：构造行号漂移/anchor 微调/重写/并列候选 fixture，参考实现给出预期 L0-L3 命中或 ambiguous/not_found，与 `locate.py` 比对（**重复 speaker/根文本 → ambiguous，文件逐字节不变**）。

### 3a. check_trace ground truth（FR-0170）

trace 属"规则正确性"，同样用手工标注 fixture（期望硬编码于 fixture 侧，不用被测实现计算）：

- 全覆盖 pass（每 FR/NFR 有章节且 ≥1 AC；每 AC 回指存在）→ `[]`。
- 正向孤儿：spec 有 FR 但 acceptance 缺 `## FR-XXXX` 章节 / 章节存在但内无 AC → 报 FR ID + spec `line:N`。
- 反向孤儿：AC 回指 spec 不存在的 FR/NFR → 报 AC ID + acceptance `line:N`。
- 不误判：讨论块（`>` 行）与 fenced code 内的假 FR/AC ID 忽略。
- 完整清单：多处孤儿一次全报（不短路），顺序稳定。

## 4. 双通道 E2E

### 4a. fake 通道（deterministic，CI 必跑）

conftest 强制 fake 后端（`TRAC_AGENT_BACKEND=fake`）。继承 TP-001 §4 前进性不变量：每条路径末态落在 `stage.exited` / `run.completed`·`run.parked` / `awaiting_human` 之一，绝不停在挂起。FakeAgent（FakeBackend）经 `simulate` 选分支，e2e 断言轨迹与事件时序，不断言文档语义。

> **断言变更（扩范围，design §10 风险项）**：M-SPEC 退出语义从 `run.completed` 改为 `stage.entered(M-ACC)`。既有 `test_happy_path.py` 结尾断言（`stage.exited + run.completed`）随 Inc-2 拆分为两个 e2e：`test_happy_path.py`（M-START→M-SPEC 止，结尾断言 `stage.exited(M-SPEC) + stage.entered(M-ACC)`）与 `test_full_journey.py`（走到 M-REQ-APPROVAL 边界，结尾断言 `run.completed(terminal_state="boundary")`）。

### 4b. live 通道（真 opencode，缺凭据 skip）

`tests/e2e_live/`，provider/model 由 env 配置（Aaron §3.1）。**只断言**：真实启动、权限白名单生效（目标文档外写被拒）、JSON 协议可解析、目标 diff 为产物且格式合规、退出与恢复；**不断言**具体文本内容。缺凭据 → **skip（不 fail）**，CI 中为独立 required job、本地 opt-in（AC-0105）。

> live 通道证明"真 agent 管路对接正确"，fake 通道证明"runtime 本身正确"；两者 AC 不重叠（模板 §6.3）。

## 5. 外部依赖分层测试（opencode）

opencode 是外部依赖，按模板 §6 三层金字塔：

| 层 | 名 | 时间 | 覆盖 | 默认 |
|:---|:---|:---|:---|:---|
| L1 | deterministic sim | 虚拟 | fake 后端穷举工作流路径（业务 AC） | ✅ CI |
| L2 | contract sim | 虚拟 | **fake opencode stand-in**（实现 `opencode run --format json` 协议的服务/脚本），OpencodeBackend 与之交互，覆盖物化/JSON 解析/失败矩阵/权限合同 AC | ✅ CI |
| L3 | real env smoke | 真实 | 真 opencode 单往返 smoke（≤1 派发），覆盖真实发现/权限/产物 | ❌ nightly/manual（缺凭据 skip） |

- **L2 stand-in 责任合同**（模板 §6.4）：实现 opencode CLI 协议（读 `--agent`/`--auto`、输出约定 JSON、按 permission 编辑目标文件）；**不实现** tracks 业务。用于 AC-0201..0207（物化/发现/清理）、AC-1801..1803（失败矩阵）等在无真 opencode 时的合同级验证。
- **边界铁律**（模板 §6.2）：可替换 opencode（外部依赖），**不可 mock** tracks 自身的 parser/locate/audit/decide（被测对象）。

## 6. AC → 测试层映射

| AC 范围 | 层 | 测试文件 |
|:---|:---|:---|
| AC-0101/0102（后端选择/fake 强制） | unit + e2e | test_backend_select.py, test_happy_path.py |
| AC-0103（fake deterministic suite） | e2e | test_happy_path.py |
| AC-0104/0105（live suite + 缺凭据 skip） | e2e_live | test_live_agent.py |
| AC-0201..0207（opencode 后端/物化/发现/清理） | integration（L2 stand-in）+ e2e_live（L3） | test_opencode_backend.py, test_live_agent.py |
| AC-0301..0306（越权可观察：edit/bash/临时目录/后置审计/回滚/清理） | integration + e2e | test_opencode_backend.py::test_over_reach_detected_and_rolled_back, # 重派链路待补 e2e |
| AC-0401..0403（agent 提示词交付物存在/格式/同步） | unit（交付门禁） | 交付门禁检查（pre-commit/CI） |
| AC-0501..0505（inline-discussion 语法） | unit + ground_truth | test_discuss_parser.py |
| AC-0601..0609（thread 结构 + identity 无持久化/全文扫描/重排/回滚/L3 报告 + freshness token stale/缺 token 拒写） | unit + ground_truth | test_discuss_parser.py, test_discuss_locate.py |
| AC-0701..0705（4 级降级 + fail-closed 歧义） | unit + ground_truth | test_discuss_locate.py |
| AC-0801..0804（discuss CLI 5 子命令/blocker/check-ready） | unit | test_discuss_cli.py |
| AC-0901..0903（状态一致性 + scope gate） | unit + e2e | test_discuss_writer.py, test_discuss_cli.py |
| AC-1001..1003（门禁 ready 判定） | unit + integration | test_discuss_gate.py, test_discuss_cli.py |
| AC-1101..1104（写操作语义/并发 flock） | unit + integration | test_discuss_writer.py |
| AC-1201..1203（解析边界） | unit + ground_truth | test_discuss_parser.py |
| AC-1301..1304（skill 交付物存在/内容/版本/Sage 加载） | unit + e2e_live | 交付门禁检查, test_live_agent.py |
| AC-1401..1403（模板接入/去硬编码/M-START 骨架不校验） | unit + e2e | test_templating.py, test_validation_gate.py |
| AC-FR0150-01..06（trac validate/结构 lint/spec 与 acceptance 统一 discussion_ready 门禁） | unit + integration + e2e | test_validate.py（结构）, test_trace.py（acceptance trace）, test_decide.py::test_spec_and_acceptance_exit_use_only_discussion_gate, test_happy_path.py（final seal 前通过 discussion_ready） |
| AC-1601（错误信息含 line:N） | unit | test_discuss_parser.py, test_validate.py |
| AC-1701（解析性能 < 1MB/1s） | unit（性能） | test_discuss_parser.py |
| AC-1801..1803（失败矩阵/事件·attempt·清理·reconcile/diff 权威） | integration（L2）+ e2e | test_opencode_backend.py, test_recovery.py |
| AC-FR0160-01..05（M-ACC 阶段可达/评审闭环/退出→M-REQ-APPROVAL/回退） | unit + integration + e2e | test_machine_acc.py, test_trace_gate.py, # 非 happy e2e 待补 |
| AC-FR0170-01..04（AC↔FR 双向 trace/孤儿清单/line:N/恒跑接线） | unit + ground_truth + integration | test_trace.py, test_trace_gate.py |
| AC-FR0180-01..04（Human gate/approve/return/Agent 不可代批） | unit + e2e | test_machine_approval.py, test_approval_journey.py |
| AC-FR0190-01..03（digest/approval identity/stale 阻断下游）已冻结 D-01~D-03+C-02 | unit + e2e | test_digest.py（D-01 算法/固定标签拼接）, test_approval_journey.py::test_stale_blocks_downstream, ::test_approve_digest_mismatch_regenerates_preview（C-02） |
| AC-FR0200-01..03（Issues 拆分/Project 关联/幂等 reconcile）已冻结 D-04~D-07 | integration（stand-in）+ e2e | test_github_effects.py（D-04 粒度/D-05 env 边界/D-06 per-item reconcile 断点续传；fake 通道不触网） |
| AC-NFR0040-01..03（SM-01~05 全覆盖/非 happy path 必含/清单核对） | 全层 | §10 转移覆盖清单（合入前逐条核对） |

> 交付门禁注：AC-0401..0403 / AC-1301..1304 的存在性 + 版本检查门禁已可执行——三个交付物（Scribe.md/Sage.md/SKILL.md）已补 `version: 0.2`，门禁入口 `trac check deliverables`（pre-commit/CI）与失败输出见 SPEC-003 FR-040 / ACC-003 AC-1303。

> **gpt [RESOLVED]:** AC-1505 不存在。acceptance.md FR-150 只有 AC-1501..1504（trac validate 独立 / 门禁再校验 / outcome 即校验+重派 / D-16 取代）。请修正映射为 AC-1501..1504，或若确需第五条 AC（例如"validate 报告含 line:N"），先在 acceptance.md 补上再引用。另：AC-0403 与 AC-1303 的交付门禁映射假设版本字段与检查脚本已存在，但对应 OPEN 线程尚未关闭（Scribe.md/Sage.md/SKILL.md 均无 `version`，门禁入口未定义）；建议在映射表标注"待 OPEN 线程关闭后可执行"。
>> **Scribe:** 已修正：(1) AC-1505 不存在 → 映射改为 AC-1501..1504（trac validate 独立/outcome 即校验+重派/门禁再校验/D-16 取代）；(2) 顺带把 FR-060 映射更新为 AC-0601..0609（本轮新增 AC-0608 stale / AC-0609 缺 token 拒写）；(3) AC-0401..0403 / AC-1301..1304 交付门禁已可执行——三个交付物已补 `version: 0.2`，门禁入口 `trac check deliverables`（pre-commit/CI）+ 失败输出见 SPEC FR-040 / ACC AC-1303，映射表已加注。@gpt 请确认是否可标记 [RESOLVED]。

> 每个 AC ≥1 测试、每个测试 ≥1 AC（CI 闭合）；跨模块合同（interfaces.md `modules` 列 ≥2）至少一个 integration 测试。

## 7. 共享 Fixture（conftest.py）

继承 TP-001 §5（`host_repo` / `trac` / `event_log`），新增：

```python
@pytest.fixture
def fake_backend(monkeypatch):
    """fake 通道：强制 TRAC_AGENT_BACKEND=fake，E2E 不触发 opencode。"""

@pytest.fixture
def fake_opencode(monkeypatch, host_repo):
    """L2 contract sim：安装 fake opencode stand-in（实现 run --format json 协议）到 PATH。"""

@pytest.fixture
def live_enabled():
    """live 通道：检测 provider/model 凭据；缺失则 pytest.skip（不 fail，AC-0105）。"""
```

公共断言辅助延续 `tests/helpers.py`（`assert_event` / `assert_exit` / `parse_frontmatter`），新增 `parse_discuss_threads(path)`（经 `trac discuss query` JSON，**不** import tracks 内部）。

## 8. CI Gate

```bash
trac agent archer ci-scan \
  --acceptance .tracks/projects/v0.2/acceptance.md \
  --tests tests/
```

校验项：AC 引用闭合（每 AC ≥1 测试、每测试 ≥1 AC）；反模式静态扫描（§1.3）；覆盖率 ≥95%；ground_truth 隔离（不 import tracks）；交付物存在性 + 版本检查（spec/skill/agent 提示词）。fake 通道（L1/L2）CI 默认跑；live 通道（L3）缺凭据 skip、独立 required job。

## 9. Judge Review Checklist

- [ ] 双通道 AC 不重叠（fake 断言状态机/live 断言协议不断言文本）
- [ ] opencode 外部依赖 L1/L2/L3 分层且标记正确，L3 不以无 issue 的 skip 规避
- [ ] discuss 解析/定位有独立 ground truth（不 import tracks）
- [ ] 越权 AC 有可观察证据（outcome failed/无提交/Human 修改不被覆盖/清理）
- [ ] 失败矩阵各分支断言 command/outcome 事件 + attempt + 子进程组清理 + reconcile
- [ ] 每个 AC 可回溯到测试代码；interfaces.md 出口与测试断言闭合
- [ ] e2e 限于 happy path，边界/错误归 integration/unit
- [ ] §10 SM-01~05 转移覆盖清单逐条有测试且通过（NFR-0040，合入前核对）

## 10. SM 转移覆盖清单（NFR-0040，normative 依据 SPEC-003「状态与生命周期」）

> 每条转移 ≥1 测试走到一次；清单内测试须存在且通过（机器化 trace 工具属 v0.3，本版合入前人工核对）。测试列为**计划落点**（file::case 前缀），实现时可加后缀细分但不得留空行缺口。`【待冻结】` 行随 Inc-4/5 冻结后补测试名。

| 转移 | 内容摘要 | 层 | 测试 |
|:---|:---|:---|:---|
| SM-01.1–.11 | trac start：backlog / 脏工作区拒绝 / 未合并分支确认·取消 / 建分支写骨架 | e2e | test_start_guards.py::test_active_run_queues_to_backlog（SM-01.2）, ::test_dirty_worktree_rejected（SM-01.4）, ::test_unmerged_branch_requires_confirm（SM-01.6/.8/.9）, ::test_reinit_is_idempotent, ::test_empty_stdin_rejected；建分支写骨架 → test_happy_path.py |
| SM-02.1–.3 | 进入 TRIAGE；no_go\|park → REJECTED；go → DRAFT | e2e | test_happy_path.py::test_triage_go, test_full_journey.py; REJECTED → test_happy_path.py::test_triage_reject |
| SM-02.4–.5 | Scribe 起草 validate pass/fail 重派（≤3 升级） | unit + e2e | test_machine_acc.py（既有）, test_retry_escalation.py::test_failed_validation_redispatches_with_evidence |
| SM-02.6–.12 | SAGE_REVIEW/HUMAN_REVIEW/RESPOND 评审回路 | e2e | test_happy_path.py::test_story_review_loop |
| SM-02.13 | EXIT → M-SPEC（story.committed(final)） | e2e | test_happy_path.py |
| SM-03.1–.3 | M-SPEC 起草 validate pass/fail（结构/template 门禁，决定记录在 inline-discussion） | unit + e2e | test_events_validate.py::test_spec_items_need_only_source_and_delivery_metadata, test_happy_path.py |
| SM-03.4/.15 | scope_overflow → ROLLBACK → M-STORY | integration + e2e | 既有 scope_overflow 测试（test_scope_overflow.py / test_store.py） |
| SM-03.5–.12 | LEX_REVIEW/HUMAN_REVIEW/RESPOND 回路 | e2e | test_happy_path.py::test_spec_review_loop |
| SM-03.13 | EXIT → `discussion_ready` 门禁（仅未 resolved inline-discussion 阻塞）→ **M-ACC** | unit + integration + e2e | test_decide.py::test_spec_and_acceptance_exit_use_only_discussion_gate, test_happy_path.py（final seal 前通过 discussion_ready）, test_full_journey.py |
| SM-03.14 | 格式终验 fail → DRAFT | e2e | test_retry_escalation.py::test_three_failures_escalate（格式终验 fail 逻辑同 SM-02.5 重派升级，复用同一测试） |
| SM-04.1 | 进入 M-ACC → DRAFT（Sage 起草 acceptance） | unit + e2e | test_machine_acc.py::test_enter_draft, # 待补 e2e（目前仅 unit 覆盖） |
| SM-04.2 | DRAFT → LEX_REVIEW（validate + trace pass, committed） | unit + e2e | test_machine_acc.py, # 待补 e2e |
| SM-04.3 | validate/trace fail 重派 Sage（≤3 升级） | integration | test_trace_gate.py::test_trace_fail_redispatch |
| SM-04.4 | trace 缺口不可修复 → ROLLBACK | unit + e2e | test_machine_acc.py::test_trace_rollback, # 待补 e2e |
| SM-04.5–.7 | LEX_REVIEW pass/comment/fail 重派 Lex | unit + e2e | test_machine_acc.py, # 待补 e2e::test_lex_loop |
| SM-04.8–.10 | HUMAN_REVIEW no_comment/comment/rollback | unit + e2e | test_machine_acc.py, # 待补 e2e::test_human_review |
| SM-04.11–.12 | RESPOND 回路 validate pass/fail | unit | test_machine_acc.py::test_respond_loop |
| SM-04.13 | EXIT → M-REQ-APPROVAL | e2e | test_full_journey.py |
| SM-04.14 | 格式终验 fail → DRAFT | unit | test_machine_acc.py::test_exit_format_fail |
| SM-04.15 | ROLLBACK → M-SPEC 或 M-STORY（重置 committed 标志） | unit + e2e | test_machine_acc.py::test_rollback_targets, # 待补 e2e |
| SM-05.1–.2 | 进入 PREVIEW → preview.generated → AWAIT_HUMAN | unit + e2e | test_machine_approval.py, test_approval_journey.py |
| SM-05.3 | human.approval → APPROVED（Agent 不可代批：无事件则 decide halt） | unit + e2e | test_machine_approval.py::test_human_gate_halt, test_approval_journey.py::test_approve |
| SM-05.3a（C-02） | approve 时 digest ≠ preview digest → 拒绝本次 approve（不落事件、不 fail run）+ 重生 preview | unit（CLI）+ e2e | test_cli_approval.py::test_approve_digest_mismatch_rejected, test_approval_journey.py::test_approve_digest_mismatch_regenerates_preview |
| SM-05.4/.7 | human.return → RETURNED → 回退目标阶段 | unit + e2e | test_machine_approval.py, test_approval_journey.py::test_return |
| SM-05.7a（C-03） | trac return 非法 to_stage（前向/自身/未知）→ CLI 报错拒绝、不落事件 | unit（CLI） | test_cli_approval.py::test_return_invalid_to_stage_rejected |
| SM-05.5 | APPROVED → ISSUES（approval.recorded reducer 置 substate="ISSUES"，Inc-3 即可测——record_approval 归 Inc-3 机制、占位 digest，IF-003 §10b 已对齐） | unit + e2e | test_machine_approval.py::test_approval_recorded_to_issues, test_approval_journey.py；approval identity 实算断言（D-01/D-02）Inc-4 补 test_digest.py |
| SM-05.6 | ISSUES → 退出边界（Issues 创建 + run.completed(boundary)，D-04~D-06 已冻结） | integration + e2e | test_github_effects.py（stand-in 失败 + per-item reconcile 断点续传必含，Inc-5）；boundary 终态先由 test_full_journey.py 覆盖（Inc-3 阶段 ISSUES 分支在 create_issues 前直接 exit→boundary，substate="ISSUES" 可断言） |
| FR-0190 stale | approval 后改三件套 → digest 不符 → 下游阻断（D-03：仅阻断 M-DESIGN+，不失效 Issues） | e2e | test_approval_journey.py::test_stale_blocks_downstream（Inc-4） |
| 休眠回放 | TRIAGE / HUMAN_REVIEW / AWAIT_HUMAN / boundary 休眠后事件回放恢复 | e2e | test_recovery.py（既有）+ test_approval_journey.py::test_replay_await_human |

> **Prism [RESOLVED]:** Prism 确认（收尾）：TP-003 分层/双通道/ground truth/SM-01~05 转移覆盖清单完整；AC-FR0160-01..05 / FR0170-01..04 / FR0180-01..04 / FR0190-01..03 / FR0200-01..03 / NFR0040-01..03 与 acceptance.md 逐一对应、无悬空引用（AC-1505 类问题已排查）。一个依赖项：§10 SM-05.5「Inc-3 即可测」成立的前提是 IF-003 §10b 把 record_approval 归 Inc-3（见 interfaces.md 对应线程），二者须一并对齐，否则 ISSUES 分支在 Inc-3 不可达。【待冻结】行（FR-0190 stale、FR-0200 Issues/reconcile）随 design D-01~D-07 / C-02 冻结后补测试名。测试计划整体通过。
>> **Archer:** Archer：确认收尾。依赖项已解除——IF-003 §10b 已按 interfaces T-002 裁定把 record_approval 归 Inc-3 机制（占位 digest），SM-05.5「Inc-3 即可测」成立，§10 该行已注明对齐。原【待冻结】行随 design D-01~D-07 + C-02 冻结已补测试名：FR-0190 → test_digest.py + test_approval_journey.py::test_stale_blocks_downstream / ::test_approve_digest_mismatch_regenerates_preview；FR-0200/SM-05.6 → test_github_effects.py（stand-in 失败 + per-item reconcile 断点续传必含）。另按 design.md T-005 增补两行显式覆盖：SM-05.3a（C-02 digest-mismatch 拒绝并重生 preview）与 SM-05.7a（C-03 非法 to_stage CLI 拒绝不落事件），映射表 AC-FR0190/0200 行同步去除【待冻结】。@Prism 复核后请 RESOLVED。

## 11. 工作流审计报告测试（FR-0220 / NFR-0050）

### 11.1 断言边界

- 报告测试只断言外部可观察的 `report.md`、`index.html`、exit code、Git 工作区和链接输出；不直接依赖报告生成器的内部对象。
- Runtime event store 是报告的 ground truth；`tests/steps.py` 仍是测试步骤摘要，不作为报告事实来源。
- Agent JSON 断言脱敏、引用存在和结构字段；不对 Agent 文本内容做固定语义断言。

### 11.2 测试分层

| AC | 测试层 | 计划测试 |
|:---|:---|:---|
| AC-FR0220-01 | integration | `test_report.py::test_report_uses_current_git_host_and_escapes_agent_output` |
| AC-FR0220-02 | integration | `test_report.py::test_report_uses_current_git_host_and_escapes_agent_output`、`::test_report_expands_agent_blobs_and_discussion_participants` |
| AC-FR0220-03 | integration | `test_report.py::test_report_uses_current_git_host_and_escapes_agent_output`（local fallback）；GitHub remote URL 独立 case 待补 |
| AC-FR0220-04 | integration | `test_report.py::test_report_cli_writes_static_files`、`::test_report_uses_current_git_host_and_escapes_agent_output` |
| AC-FR0220-05 | integration | `test_report.py::test_report_uses_current_git_host_and_escapes_agent_output`（DB/worktree read-only） |
| AC-NFR0050-01 | integration | `test_report.py::test_report_marks_unclosed_activity_interrupted` |
| AC-NFR0050-02 | planned gap | UTC/localized time 与 blob reference 独立 case 待补 |
| AC-NFR0050-03 | unit + integration | `test_agent_io.py::test_redact_masks_credentials_and_preserves_non_sensitive_fields`、`test_report.py::test_report_uses_current_git_host_and_escapes_agent_output` |
| AC-NFR0050-04 | planned gap | overreach rollback/retry chain 独立 report case 待补 |
| AC-NFR0050-05 | unit + integration | `test_agent_io.py::test_audit_blob_failure_is_partial_without_dangling_references`、live report gap 断言 |
| AC-NFR0050-06 | planned gap | actor derivation table 独立 case 待补 |
| AC-NFR0060-01 | e2e_live fixture | `test_full_journey.py::test_bounded_scripted_real_agent_journey`（`live_github_repo` fail-closed） |
| AC-NFR0060-02 | e2e_live | `test_full_journey.py::test_bounded_scripted_real_agent_journey`（`ls-remote` 前后审计） |
| AC-NFR0060-03 | e2e_live | `test_full_journey.py::test_bounded_scripted_real_agent_journey`（保留并打印宿主/report/remote/branch） |
| AC-NFR0060-04 | e2e_live | `test_full_journey.py::test_bounded_scripted_real_agent_journey` |

### 11.3 Agent I/O 捕获专项

- 使用 stand-in opencode 输出多条 NDJSON，断言 Runtime 保存全部脱敏记录而非仅 `self_report`；大输出走 blob，小型 outcome 只持 ref/digest。
- 覆盖 JSON、NDJSON、非法/截断流和 audit blob 写入失败；最后一种情况下业务 outcome 不变、无悬空 ref、报告显示 gap。
- 输入断言以 canonical Assignment JSON 为准，不要求把固定目标路径等上下文重复写入每个活动。
- 脱敏 ground truth 使用包含 API key、Authorization header、普通业务参数的固定 fixture，证明敏感字段消失而非敏感字段保留。

## 12. Live E2E 宿主与完整旅程

### 12.1 宿主配置合同

- live E2E 必须读取 `TRACKS_E2E_GITHUB_REPO`；该值为可丢弃的 GitHub 仓库，缺失时报告明确配置错误，不把本地仓库默认为远端。
- 测试根目录本身创建为 Git 宿主 repo，`.tracks/runtime/tracks.db` 和 commit history 由该宿主提供；测试完成后不得主动删除本地宿主目录。
- GitHub token 只授权可丢弃测试仓库；测试脚本配置唯一预期 remote/临时分支并传入 Agent/fixture。通用 bash 无法形成 branch 级安全强制，故结束后必须用 `gh` 回读 refs，发现预期分支之外的远端变化即失败并报告。
- provider/model 凭据缺失沿用 live 通道的 skip 合同；测试宿主 repo 缺失是配置错误，不得以 skip 伪造有效 live 测试。
- fixture 创建宿主后立即打印 `LIVE_E2E_HOST=<absolute path>`；结束时再次打印 host、report_dir、remote_repo、branch。即使失败/超时也不得主动删除本地宿主。

### 12.2 完整 live 旅程

新增 `tests/e2e_live/test_full_journey.py`，以固定 seed 真实运行完整需求流程。

#### 12.2a Seed 原文

```text
构建一个简单的代码行数统计工具。

用户指定一个文件路径，工具输出该文件的代码行数。

以下产品决定尚未做出，需要在需求评审中澄清：
- 交付入口是 CLI 还是 library；
- 空白行是否计入代码行数；
- 指定路径无效时用户看到什么。

实现语言、框架、CI、架构和测试策略由 Agent 采用合理默认，不交给 Human 决定。
```

seed 刻意保留三个产品槽位（见 SPEC-003 §FR-0130 Seed 产品槽位），使 reviewer 有真实缺陷可发现。seed 不预先给出答案；答案只存在于 live fixture 的有限 transcript 中。

#### 12.2b 产品槽位与预录答案

| slot_id | 问题 | 决定者 | 预录答案（`LiveE2E-Human`） |
|:---|:---|:---|:---|
| `surface` | 交付入口是 CLI 还是 library | Human | 交付入口使用 CLI，不提供 library 入口。 |
| `blank_lines` | 空白行是否计入代码行数 | Human | 空白行不计入代码行数。 |
| `error_behavior` | 无效路径时用户看到什么 | Human | 输出错误信息到 stderr 并返回非零退出码。 |

以下不是产品槽位，Agent 采用合理默认：实现语言、框架、依赖、CI、架构、测试策略。

预录答案以 `tracks-live-console/v1` 格式注入，每行 `answer.<slot_id>=<答案>`，带 `actor=LiveE2E-Human`。transcript 有限；用尽、问题无法归类或需要新产品决定时 fail-closed，不静默采用默认。

#### 12.2c Finding ID 与 scenario overlay

live harness 从 `tests/e2e_live/scenarios/` 选择 test-only JSON，并通过 `trac run --assignment-overlay <scenario.json>` 嵌套到 `assignment.scenario_context`（IF-003 §3a）；scenario 不进入 wheel 的 production resources，也不能覆盖基础 assignment。live journey 使用以下固定 finding：

| finding_id | 阶段 | reviewer | 条件 | 期望回复者 |
|:---|:---|:---|:---|:---|
| `STORY-OUTPUT-PLACEMENT` | M-STORY SAGE_REVIEW | Sage | story 未定义统计结果的展示位置（stdout / 文件 / 返回值） | Scribe |
| `SPEC-BLANK-LINE-SEMANTICS` | M-SPEC LEX_REVIEW | Lex | spec 未明确空白行判定规则（空字符串 / 仅空白字符 / 含注释） | Sage |

- reviewer 创建的 thread body 必须以 `[FINDING:<finding_id>]` 开头。
- 条件适用时未创建 thread → live journey fail-closed，保留 report。
- 条件不适用时不创建不失败。
- 测试按 finding_id 匹配 thread，不断言 thread body 的其余自然语言。

#### 12.2d 旅程流程

```text
trac start（seed 原文）
→ Scribe TRIAGE：读取 seed，识别产品槽位，用 trac discuss 提问
→ live fixture 注入 surface/blank_lines/error_behavior 预录答案到 console
→ Scribe 用 trac discuss reply 写入回答并 resolve 自己的 thread
→ trac triage go
→ Scribe DRAFT story
→ Sage SAGE_REVIEW：scenario_context 含 [FINDING:STORY-OUTPUT-PLACEMENT]
  → Sage 发现 story 未定义结果展示位置
  → Sage trac discuss start [FINDING:STORY-OUTPUT-PLACEMENT]
  → verdict=revise（open thread）
→ Scribe RESPOND：
  → trac discuss query --blocker Scribe
  → trac discuss reply（采纳 Human 预录答案：结果打印到 stdout）
  → 修改 story 正文，产生 diff
  → Runtime commit story
→ Sage 再次 SAGE_REVIEW：
  → 确认 reply 和 diff
  → trac discuss set-status resolved --operator Sage
  → verdict=pass
→ trac review no-comment
→ Sage DRAFT spec
→ Lex LEX_REVIEW：scenario_context 含 [FINDING:SPEC-BLANK-LINE-SEMANTICS]
  → Lex 发现 spec 未明确空白行判定
  → Lex trac discuss start [FINDING:SPEC-BLANK-LINE-SEMANTICS]
  → verdict=revise
→ Sage RESPOND：
  → trac discuss query --blocker Sage
  → trac discuss reply（明确空白行 = 仅含空白字符的行，不计入）
  → 修改 spec，产生 diff
  → Runtime commit spec
→ Lex 再次 LEX_REVIEW：
  → 确认 reply 和 diff
  → trac discuss set-status resolved --operator Lex
  → verdict=pass
→ trac review no-comment
→ Sage DRAFT acceptance
→ Lex LEX_REVIEW（无额外 required finding；可 pass 或自主提问）
→ trac review no-comment
→ M-REQ-APPROVAL
→ trac approve --actor LiveE2E-Human
→ trac run → run.completed(boundary)
→ trac report --run-id <id> --output report
```

#### 12.2e 断言合同

测试必须断言以下证据，缺任何一项即失败并保留宿主和 report：

1. **Sage↔Scribe 闭环**：story.md 中存在 initiator=Sage、body 含 `[FINDING:STORY-OUTPUT-PLACEMENT]` 的 thread；reply speaker 含 Scribe；`reply_count > 0`；status=resolved；RESPOND outcome 有 `diff_ref`；story commit 事件存在；最终 `sage.verdict(pass)`。
2. **Lex↔Sage 闭环**：spec.md 中存在 initiator=Lex、body 含 `[FINDING:SPEC-BLANK-LINE-SEMANTICS]` 的 thread；reply speaker 含 Sage；`reply_count > 0`；status=resolved；RESPOND outcome 有 `diff_ref`；spec commit 事件存在；最终 `lex.verdict(pass)`。
3. **Human 预录答案落地**：story.md 的 discussion reply 中可检索到 CLI、stdout、空白行不计入、stderr/非零退出码等关键语义。
4. **Report 完整性**：report.md 含 assignment 展开、Agent I/O 摘要、discussion thread/reply/status、attempt、commit、audit gap、interrupted activity、阶段和最终状态。
5. **远端审计**：`git ls-remote` 前后一致（无意外远端副作用）。
6. **宿主保留**：测试结束打印 `LIVE_E2E_HOST`、`LIVE_E2E_REPORT_DIR`、`LIVE_E2E_REMOTE`、`LIVE_E2E_BRANCH`。

#### 12.2f 通用纪律

- 该测试替换当前 live 通道中仅调用单个 Lex/Agent 的 smoke 作为主要 live workflow 证据；`tests/e2e/test_full_journey.py` 的 fake 全流程保留，继续承担每次 CI 的 deterministic 回归。
- 旅程的每一轮都通过 Runtime 产生事件和 commit；测试断言审计报告能解释 Agent 输入/输出、discussion、commit、越权审计、失败重派和成功原因。
- live 测试不固定断言 Agent 生成的完整自然语言文本；只对预设产品 slot 和 finding_id 做关键词/ID 匹配。无法分类的问题 fail-closed，并打印原问题与保留的宿主路径。
- Human 产品回答只由 live fixture 以明确 actor `LiveE2E-Human` 注入真实 Agent 子进程的有限 console transcript；Agent 必须再用 `trac discuss reply` 写入权威文档。`trac triage`、`trac review`、`trac approve` 仍由测试驱动器调用，不等待交互式 Human；普通 trac 运行不隐式注入 console。
- 每个 Human review gate 前查询当前文档并要求所有已观察线程均为 resolved；存在 discussion 时还必须有 reply，证明 author/reviewer 闭环而非直接改状态。reviewer 必须发现 seed 中刻意保留的缺陷并提问；若模型直接 pass，测试带 report 失败，不静默跳过。
- 测试标记为独立 opt-in（不进入普通 fake suite）；设置 per-agent timeout、每次 `trac run` timeout 和整条旅程总 timeout，三者共同限制 discussion/review 循环。超限即失败并保留宿主，供报告最后状态和未收敛 discussion，不无限循环。

### 12.3 现有 live smoke 的保留范围

现有 `test_real_startup_and_json_protocol`、权限、provider error、target diff 等测试保留为低成本 backend contract smoke；`test_lex_review_runs` 不再作为完整流程证据，改为对 Lex reviewer 的 no-change/discussion 语义进行独立验证。

## 13. 可安装发行物与 live E2E 安装边界

### 13.1 安装合同

- live E2E 开始前由测试使用当前项目的 `.venv/bin/python` 构建临时标准 wheel；不得把源码目录直接作为运行时 package，也不得使用 editable install。
- 测试在每个临时宿主内创建隔离虚拟环境，使用该 wheel 安装 `agent-on-tracks`，并以该环境的 `trac`/`python -m tracks.cli.main` 驱动旅程。测试命令移除 workspace `PYTHONPATH`；workspace `.venv/bin` 只可作为外部 `opencode` 可执行文件的显式工具来源，不得提供 tracks import。
- 安装后 probe 必须确认 `tracks.__file__` 位于隔离环境、`trac` 入口可执行，并确认以下 package data 存在：
  - `tracks/agents/Scribe.md`
  - `tracks/agents/Sage.md`
  - `tracks/agents/Lex.md`
  - `tracks/skills/tracks-discuz/SKILL.md`
  - `tracks/templates/*.md` 与 report renderer 资源
- `OpencodeBackend` 使用安装环境中的 canonical agents；dispatch 前把对应角色定义物化到测试宿主 `.opencode/agents/<Name>.md`，与安装包中的字节一致。测试断言 opencode 发现的是该文件，结束后断言清理/恢复，不在 backend `_prompt` 复制角色职责。

### 13.2 新增测试映射

| AC | 测试类型 | 测试落点 |
|:---|:---|:---|
| AC-NFR0070-01 | integration + e2e_live | `tests/integration/test_distribution.py::test_wheel_is_installable_and_contains_runtime_resources`、`test_full_journey.py::test_bounded_scripted_real_agent_journey` |
| AC-NFR0070-02 | integration + e2e_live | `tests/integration/test_distribution.py::test_wheel_is_installable_and_contains_runtime_resources`、`tests/e2e_live/test_full_journey.py::test_live_environment_removes_workspace_import_hooks` |
| AC-NFR0070-03 | integration + e2e_live | `tests/integration/test_distribution.py::test_wheel_is_installable_and_contains_runtime_resources`、`tests/e2e_live/test_full_journey.py::test_bounded_scripted_real_agent_journey` |
| AC-NFR0070-04 | e2e_live | `tests/e2e_live/test_full_journey.py::test_bounded_scripted_real_agent_journey` |

### 13.3 失败与保留

- wheel 构建、隔离环境创建、pip 安装、probe、入口执行或 package data 校验任何一步失败，live job 失败并保留临时宿主、安装日志和 report；不 skip、不 fallback 到 fake。
- 普通未配置 live provider 的 suite 仍按 NFR-0060 skip；安装合同只在 opt-in live job 中执行。
