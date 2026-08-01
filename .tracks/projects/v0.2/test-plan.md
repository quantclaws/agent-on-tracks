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
| SM-03.1–.3 | M-SPEC 起草 validate pass/fail；Sage DRAFT/RESPOND 必须全部 `[ ]`，提前 `[x]` 拒绝 | unit + e2e | test_events_validate.py::test_spec_decision_checks_are_stage_specific, test_happy_path.py |
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
