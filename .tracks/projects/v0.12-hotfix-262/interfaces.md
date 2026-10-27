---
envelope: tracks-envelope:v2
interfaces_id: IF-012-HF262
spec_ref: SPEC-012
arch_ref: ARCH-012-HF262
created: 2026-10-27
status: draft
sha:
---

# v0.12-hotfix-262 — 接口与类型化 Schema：发布证据权威、阻塞评审、attention 停顿、进程边界 env、阶段事件与 UI

本文是 IF-012 的 hotfix delta。变更来源 = `.tracks/projects/v0.12.1/design-delta.md`（已批准）。本文只写外部可观察契约；不变量 INV-1..INV-10 的权威文本在 design-delta §1，本文按接缝引用。

## 0. 延续性（什么不变）

- IF-001～IF-012 全部合同逐字继承；既有 IF 标识不可重定义或复用；本文只追加 IF-HOTFIX262-* 新标识（§5）。
- tracks.db EVENT_TYPES 封闭集不变（不新增事件类型）；CLI 子命令集合、参数面、退出码不变；HTTP 端点集合与响应 schema 不变（本 delta 修订投影行为/性能与 payload 内字段来源，不增删端点与字段形状）。
- `.tracks/projects/project.toml` 测试执行合同逐字继承（零 bytes 变更）。
- 既有事件的既有 payload 键语义不变；本 delta 只在既有事件上追加/修正**消费端判定**与新约束（如 `full.executed` 保留 `serves_as_full_f` 与 `full_f_eligible` 双键——两键在 v0.12.0 已存在，本 delta 不删键）。

### 0.1 合同变更清单（显式声明）

| # | 变更 | 旧合同（v0.12.0 行为） | 新合同（v0.12.1） | 理由 |
|:--|:--|:--|:--|:--|
| 1 | FULL_F 权威 | 消费者各自读 `serves_as_full_f`（可被历史不一致 payload 误导） | 唯一共享谓词 `is_authoritative_full_f` 判定；`serves_as_full_f` 单独不构成权威（INV-1） | design-delta B02 |
| 2 | candidate 绑定 | closure harvest 可取最后 `mutation.manifest` | 只从 `candidate.frozen` / `milestone.trace_closed` 派生（INV-2） | B02 |
| 3 | 发布证据键 | 单一 `prism` 键 | 新 preview/authorization 必需 `full_f/ci/prism.verify_final/prism.security/security` 五键；`_evidence_key` 仅 `verify_final`/`security` scope 有值（INV-4）；历史单键 blob 可读（INV-5） | B01 |
| 4 | 阻塞评审 | `verdict=pass` 可携带 blocker/high/critical findings | pass∧blocking = 协议违规，ingest fail-closed 拒绝；下游 `verdict!="pass"∨has_blocking_findings` 一律判 failed（INV-3） | B03 |
| 5 | 凭据 | 缺失 token 可循环重试/吞为 issue_not_found | `GITHUB_TOKEN` 唯一 canonical；缺失败仅诊断 hint；未决 attention 停 loop；去重 ≤1 次（INV-6/7） | M01 |
| 6 | 子进程 env | 各站点各自 strip（denylist 思维） | 唯一 `host_process_env()` allowlist；denylist 废止；`[host-contract].env_allowlist` 逃生口（INV-8） | M02 |
| 7 | 阶段事件 | 发布尾段无显式 stage 事件 | `stage.exited/entered` 在 M-RELEASE→M-PUBLISH→M-MILESTONE 三点 exactly-once 发射；legacy run 由投影/UI 合成回退（INV-9） | M03 |
| 8 | UI/perf | ac-chain 无条件重放；timeline 含全量 evidence；选中节点覆写背景色；backlog 可显示 ULID；Todos 分散 | ac-chain 空注册短路；slim 收紧（<50KB/<500ms）；选中只 outline ring；backlog 可读标题永不 ULID；统一 Todos inbox | UI/perf |

## 1. 跨模块合同

### 1a. FULL_F 权威谓词（IF-HOTFIX262-FULLF-001）

**modules**：`tracks/executor/full_f_authority.py`、`tracks/executor/m_impl_full_chain.py`、`tracks/executor/verify_gates.py`、`tracks/executor/release_preview.py`、`tracks/executor/release_authorization.py`、`tracks/cli/validate_cmd.py`。

1. `is_authoritative_full_f(payload, candidate_sha=None) -> bool` 是「该 `full.executed` 是权威 FULL_F 证明」的唯一仲裁。判定 = 全部合取：`payload.passed is True` ∧ `payload.full_f_eligible is True` ∧ `payload.round ∈ {"FULL_1"（空 ledger）, "fallback_full", "FULL_F"}` ∧ （`candidate_sha is None` ∨ `payload.execution_commit == candidate_sha` ∨ 绑定的 `evidence.reused(kind="full_f")` 其源事件 `execution_commit == candidate_sha`）∧ `evidence_ids` 非空 ∧ identity 四元组（`selection_id`）一致。`payload.serves_as_full_f` 单独**不构成**权威。
2. `_finalize_full_round` 继续在 payload 中同时写 `serves_as_full_f` 与 `full_f_eligible`（v0.12.0 已存在的双键，不删不改语义）。
3. closure harvest 的 `candidate_digest` 只从 `candidate.frozen` / `milestone.trace_closed` 派生；`_merge_stream_event` 不得因任何 truthy `serves_as_full_f` 覆写既有绑定。
4. 可观察：重放 v0.12.0 事件切片时，seq 7362（`44c14de`，FULL_F，eligible）被信用；seq 5896（`ee781bf`，ineligible，外 commit）被拒绝；每条 AC 的 `full_pass_evidence` 绑定到 7362。

### 1b. 发布证据键完备性与 split prism 键（IF-HOTFIX262-RELEV-001）

**modules**：`tracks/executor/release_preview.py`、`tracks/executor/release_authorization.py`、`tracks/cli/release_cmd.py`。

1. 新候选的新 preview/authorization 无效，除非 `full_f`（或同候选绑定的 `evidence.reused(kind="full_f")`）、`ci`、`prism.verify_final`、`prism.security`、`security` 五键全部在场且有效；完备性检查无条件执行（不因 barrier/before 为空而跳过）。
2. `_evidence_key(scope)`：`scope="verify_final"` → `prism.verify_final`；`scope="security"` → `prism.security`；其余 scope → `None`（M-DESIGN/M-TEST/M-IMPL verdict 永不进入发布证据）。
3. `_gate_status()` 增加 `full_f_ok`；`_release_gate_blocked()` 增加 `full_f` 检查。
4. legacy read-back（INV-5）：v0.12.1 之前持久化的 `release.previewed` blob 携带单一 `"prism"` 键仍可读；`preview_inputs_match` 按 blob 记录的键集 schema 比对；split 键只对新装配的 preview 与未发布的 authorization 强制；无 DB 迁移。
5. 可观察：缺任一键 → preview invalid / release 命令 `error: release rejected — gate failed or preview stale`（exit≠0，不落 `release.decided`，状态保持 AWAITING_RELEASE）；legacy blob 读回不产生 `operation_plan_changed`。

### 1c. 阻塞评审 fail-closed（IF-HOTFIX262-REVIEW-001）

**modules**：`tracks/executor/review_findings.py`、`tracks/executor/publish_face.py`、`tracks/executor/verdict_face.py`、`tracks/executor/result_payload.py`、`tracks/executor/release_preview.py`、`tracks/executor/release_authorization.py`、`tracks/cli/release_cmd.py`、`tracks/executor/milestone_chain.py`。

1. `has_blocking_findings(findings) -> bool`：任一 finding 的 `severity ∈ {"blocker","high","critical"}` 即真。
2. `verdict == "pass"` ∧ `has_blocking_findings` = 矛盾 verdict = schema/协议违规。ingest 点（`_publish_security_review_verdict`、`_publish_verify_final_verdict`、`_emit_verdict`、`_m_test_prism_payload`）fail-closed 拒绝：落 `verdict.failed` / `attention.required`，消费该 attempt，永不静默降级为 `revise`，永不发射 passing `prism.verdict`，不修改 verdict 本体。
3. 下游硬不变量：`publish_face._assess_security_with_review`、`release_preview._evidence_is_success`、`release_authorization._prism_status/_security_status`、`release_cmd._release_gate_blocked/_prism_final_status`、`milestone_chain._do_close_milestone` 一律以 `verdict != "pass" ∨ has_blocking_findings(findings)` 判 failed。
4. 可观察：注入 pass+blocker 后事件流无 passing `prism.verdict`；`trac status` 报告对应 failed/blocked；重放 seq 7342（SEC-001）后 security 门失败。

### 1d. attention 停顿、去重与凭据 preflight（IF-HOTFIX262-ATTN-001）

**modules**：`tracks/executor/run_loop.py`、`tracks/executor/release_tail.py`、`tracks/supervisor/service_intake.py`、`tracks/supervisor/intake.py`。

1. `GITHUB_TOKEN` 是唯一 canonical 凭据；无 `GITHUB_ACCESS_TOKEN`/`GH_TOKEN` 回退。`GITHUB_TOKEN` 未设置且 `GITHUB_ACCESS_TOKEN` 或 `GH_TOKEN` 在 `os.environ` 时，preflight 只发可行动诊断 hint（`found GITHUB_ACCESS_TOKEN in environment; export GITHUB_TOKEN="$GITHUB_ACCESS_TOKEN"`）。
2. `_run_loop_preflight()` 每次 loop 迭代检查 `_pending_operator_attention(state)`；未决且非 auto-retryable 的 `attention.required` 使 loop 干净停在 `needs_attention`（≤1 次发射，无 `CommandStallError` 中止）。
3. `_ensure_issue_backend` 去重：stream 上已存在未决 `attention.required(area="issue_creation", reason="missing_token")` 时不重发。
4. 凭据 preflight 点：M-REQ-APPROVAL→ISSUES 入口、`submit_intake(source="issue")`、`create_run(hotfix_*)`；missing-token 不再被吞为 `issue_not_found`（报告 `issue_fetch_failed` 携分类指引）。
5. 可观察：缺 token 时恰好 ≤1 条 `attention.required`，status 报 `needs_attention: issue_creation missing_credentials ... reason=missing_token next="export GITHUB_TOKEN=…; trac run --resume"`；无 1,808 式紧循环。

### 1e. 进程边界 env allowlist（IF-HOTFIX262-HOSTENV-001）

**modules**：`tracks/executor/host_env.py`、10 个既有 subprocess 站点（`quality_gate.execute_gate_command`/`_run_check`、`test_execute._run_layer_command`、`test_collect._run_contract_sections`、`helpers.run_collect_command`、`result_audit._collect_test_modules`、`host_contract._run_declared`、`security._run_command`、`closure_evidence._execute_selected_nodes`、`m_impl_dispatch._run_host_commit_hook`）。

1. `host_process_env(extra=None, contract_allowlist=()) -> dict` 是所有 runtime 派生宿主子进程的唯一 env 来源；denylist 废止（`quality_gate.host_test_env()` 不再独立存在为真相源）。
2. base allowlist（跨语言工具链）：`PATH HOME USER LOGNAME SHELL TMPDIR TEMP TMP LANG LC_ALL LC_CTYPE SYSTEMROOT SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE XDG_CACHE_HOME CI VIRTUAL_ENV PYTHONPATH PYTHONHASHSEED PYTHONDONTWRITEBYTECODE UV_CACHE_DIR PIP_CACHE_DIR CARGO_HOME RUSTUP_HOME RUSTFLAGS NODE_PATH NVM_DIR VOLTA_HOME PLAYWRIGHT_BROWSERS_PATH GOPATH GOROOT GOCACHE GOMODCACHE GOPROXY JAVA_HOME GRADLE_USER_HOME` + 测试尺寸旋钮 `TRAC_FORENSICS_DIR TRAC_BATTERY_TIMEOUT`。
3. strip 集：`GITHUB_*`、`GH_*`、`TRAC_GITHUB_*`、`TRAC_SERVE_*`、`TRAC_AGENT_*`、`TRAC_FAKE_*`、`GIT_DIR`、`GIT_WORK_TREE`、`GIT_INDEX_FILE`、`*_TOKEN`、`*_SECRET`、`*_API_KEY`。
4. escape hatch：`project.toml` 的 `[host-contract].env_allowlist = [...]` 声明宿主非密钥变量。
5. 可观察：跨语言探针（Python/uv、Node、cargo、go、java）经每个站点 spawn 的子进程只见到 allowlist 变量；工具链功能正常。

### 1f. 阶段事件与 legacy 时间线回退（IF-HOTFIX262-STAGE-001）

**modules**：`tracks/executor/publish_runtime.py`、`tracks/executor/milestone_chain.py`、`tracks/kernel/release.py`、`tracks/server/projections_timeline.py`、`tracks/server/static/app/timeline.js`。

1. 发射（INV-9，既非新事件类型，是既有 `stage.exited/entered` 的补发）：`stage.exited(M-RELEASE)` + `stage.entered(M-PUBLISH)` 在该批首个 `publish.planned` 之前恰好一次（visit 守卫，`retry_tail` 重入安全）；`stage.exited(M-PUBLISH)` + `stage.entered(M-MILESTONE)` 在进入 `_do_close_milestone`、`milestone.trace_closed` 之前（`_block_incomplete_publish` 成功之后）恰好一次；`stage.exited(M-MILESTONE)` 在完成时（`run.completed(terminal_state="released")` 前/同时）发射。
2. kernel substate 语义保持（entered 在 `publish.planned` 之前使 `_on_stage_entered` reset 一致）；`kernel/release.py` reducer 仍是状态权威。
3. legacy 回退（投影 + UI，合成不持久化）：`publish.planned` ∧ `current=="M-RELEASE"` ⇒ 合成关闭 M-RELEASE / 开启 M-PUBLISH；`milestone.trace_closed` ∧ `current=="M-PUBLISH"` ⇒ 合成关闭 M-PUBLISH / 开启 M-MILESTONE；`run.completed(terminal_state="released")` ∧ `current=="M-MILESTONE"` ⇒ 合成关闭 M-MILESTONE 为 `"done"`。显式事件优先于合成。
4. 可观察：新 run 事件流含恰一次的转移序列；legacy run 的时间线/ac-chain 渲染同样的 visits 且 M-MILESTONE closed=done、`stageWorkflowWindow` 非空。

### 1g. UI 性能与可读性（IF-HOTFIX262-UI-001）

**modules**：`tracks/server/projections_core.py`、`tracks/server/projections_run.py`、`tracks/server/projections_timeline.py`、`tracks/server/static/styles.css`、`tracks/server/static/app/timeline.js`、`releases.js`、`views.js`。

1. `_chain_rows()`：`acs = _registry_acs(...)`；`if not acs: return []` 必须先于 `_run_events()`/`project_state()`；惠及 `/api/runs/{id}/ac-chain` 与 `project_run_detail`。0-AC run 的 ac-chain 响应 <5ms。
2. slim timeline：`_SLIM_KEYS` 去掉 `"evidence"`（stageRoundsPanel 懒取全量）；收紧 `_SLIM_MAX_ROWS`；slim 尾部之外不读 `$ref` blob；slim 行过滤到 workflow-bearing 事件类型 + 选中阶段 dispatch 事件。目标 slim payload <50KB、p95 <500ms；首页 timeline 懒加载。
3. 选中样式：`.stage-node-selected` / `[data-selected="true"]` 去除 `background/color` 覆写；只用 outline ring（`outline: 2px solid var(--accent); outline-offset: 2px;` 或 box-shadow）；`state-*` 颜色保留；追加 `aria-selected="true"` / `aria-current="step"`。
4. backlog 标题：`_backlog_group(conn, repo_path)` 以 `intake_id` join `intakes.report_json.title`；`service_intake` 此后在 `backlog.recorded` payload 存 `title`；fallback = 首行非空内容（strip `#`/`-`/`*`/`>`）截断；最终 fallback = 本地化 `待办条目 (<version/date>)`；**永不渲染 ULID**。`releases.js` 渲染 `display_title`。
5. Todos inbox：`project_todos()` 统一覆盖 (a) `awaiting ∈ {triage, review, review_pending_threads, approval, escalation, rollback, hotfix_triage}` 且 `substate == "AWAITING_RELEASE"`；(b) 未决 `attention.required` blocks；(c) pending `evaluated` intakes。列表项 `{version, title, stage, kind_label, summary/context, updated_ts, action_ref}`；`views.js` 渲染可点击可读卡片（`<version> · <title> — <stage> · <kind_label>`）导航到 run timeline / decision modal；保留 `data-testid="todo-${todo.run_id}"`。

### 1h. 临时引用清理（IF-HOTFIX262-CLEANUP-001）

**modules**：`tracks/executor/milestone.py`。

1. `clean_temp_refs` 除 `refs/trac/tmp/*` 外追加清理 `refs/trac/rgr/{run_id}/*` 与 detached 临时 worktree / 陈旧 `.tracks/runtime/staging`；断言 `remaining == 0`。
2. 显式排除：`tracks/checks/release_evidence.py::_REQUIRED_PHASES` 不接入 v0.12.1 阻塞门禁（hotfix/verification-only run 会误阻断）；留 v0.13。

## 2. CLI 接口合同

本 delta 不新增/变更 CLI 命令、参数面与退出码；全部 CLI 语义继承 IF-012 §2。受影响命令的可观察**行为**变化仅在失败/拒绝语义：

| # | 命令 | 变化 |
|:--|:--|:--|
| 1 | `trac release --action release` | 前置缺五键之一或 preview stale 时仍 exit≠0 + 固定文案（既有文案不变）；新增 `full_f` 缺失也走同一拒绝路径 |
| 2 | `trac run --resume`（凭据恢复后） | missing-token 恢复路径不变；缺 token 时不再紧循环，停在 `needs_attention` |
| 3 | `trac hotfix` / `trac triage`（issue 取文） | missing-token 分类为 `issue_fetch_failed`（不再是 `issue_not_found`） |

## 3. 文件 / 存储契约

| # | 路径 | 格式/写入者 | 读取者/生命周期 |
|:--|:--|:--|:--|
| 1 | `tracks/executor/full_f_authority.py` | Python 模块（stub→实现） | executor/cli 消费者 |
| 2 | `tracks/executor/review_findings.py` | Python 模块（stub→实现） | ingest 点与下游门 |
| 3 | `tracks/executor/host_env.py` | Python 模块（stub→实现） | 10 个 subprocess 站点 |
| 4 | `.tracks/projects/v0.12/errata-v0.12.0.md` | Markdown 数据 artifact（**发布期**提交，不属 M-DESIGN scaffold；design-delta §7.6） | human 评审；release notes 附加 |
| 5 | `tracks.db` | 既有事件存储 | 只追加，不改写历史（INV-10） |
| 6 | `intakes.report_json.title` / `backlog.recorded` payload `title` | 既有 SQLite/事件 | `_backlog_group` join；历史条目无 title 走 fallback |

临时产物：无新增；`refs/trac/rgr/{run_id}/*` 与 `.tracks/runtime/staging` 由 cleanup 覆盖（§1h）。

## 4. 可观察出口（测试断言基础）

### 4a. 事件 / CLI 出口

| # | outlet | assertions |
|:--|:--|:--|
| 1 | `full.executed` / `evidence.reused` 事件流（replay） | 7362-shape 信用、5896-shape 拒绝；STALE 无复用；candidate_digest 绑定 frozen/trace_closed |
| 2 | `release.previewed` / `release.decided` | 新 preview 必含五键；缺键拒绝无 `release.decided`；legacy 单键 blob 可读 |
| 3 | `prism.verdict` / `verdict.failed` / `attention.required` | pass+blocking 注入后无 passing verdict、有 failed/attention、attempt 被消费 |
| 4 | `attention.required(area="issue_creation", reason="missing_token")` | ≤1 条；hint 含 `export GITHUB_TOKEN`；loop 停 `needs_attention` |
| 5 | `stage.exited/entered`（M-RELEASE/M-PUBLISH/M-MILESTONE） | 恰好一次、INV-9 顺序、retry_tail 下不重复 |
| 6 | `refs.cleaned` + `git for-each-ref refs/trac/tmp refs/trac/rgr` | 为空；`remaining == 0` |

### 4b. 子进程 env 出口

| # | outlet | assertions |
|:--|:--|:--|
| 1 | 10 站点 spawn 的子进程 env（探针打印） | strip 集成员缺席；allowlist 工具链变量在场；工具链命令成功 |

### 4c. HTTP / UI 出口

| # | outlet | assertions |
|:--|:--|:--|
| 1 | `GET /api/runs/{id}/ac-chain`（0-AC run） | <5ms；空 rows；无事件重放副作用 |
| 2 | `GET /api/runs/{id}/timeline?slim=1` | payload <50KB；p95 <500ms；slim 行无全量 evidence |
| 3 | legacy run 的 timeline/ac-chain 投影 | 合成 visits；M-MILESTONE closed=done；与 UI 一致 |
| 4 | 选中 stage 节点 DOM | 无背景/前景覆写；outline ring 在场；`aria-selected`/`aria-current` 在场；`state-*` 色保留 |
| 5 | backlog sidebar 条目 | `display_title` 可读；任何路径不渲染 ULID |
| 6 | Todos inbox 卡片 | 覆盖三来源；字段齐备；`data-testid="todo-*"` 保留；可点击导航 |

## 5. IF Registry

### IF-HOTFIX262-FULLF-001 FULL_F 权威与 candidate 绑定合同

- **合同**：§1a——`is_authoritative_full_f` 唯一仲裁；双键保留；candidate 只从 frozen/trace_closed 派生；replay 校正 7362/5896。
- **modules**：tracks/executor/full_f_authority.py, tracks/executor/m_impl_full_chain.py, tracks/executor/verify_gates.py, tracks/executor/release_preview.py, tracks/executor/release_authorization.py, tracks/cli/validate_cmd.py。
- **关联**：AC-FR0267-01@v0.8、AC-FR0268-01/02/03@v0.8、AC-FR0276-01@v0.8。

### IF-HOTFIX262-RELEV-001 发布证据键完备性合同

- **合同**：§1b——五键必需；`_evidence_key` scope 映射；`_gate_status.full_f_ok`；legacy 单键 read-back。
- **modules**：tracks/executor/release_preview.py, tracks/executor/release_authorization.py, tracks/cli/release_cmd.py。
- **关联**：AC-FR0270-02@v0.8、AC-FR0273-01/02@v0.8、AC-FR0274-02@v0.8。

### IF-HOTFIX262-REVIEW-001 阻塞评审 fail-closed 合同

- **合同**：§1c——pass+blocking 协议违规；四 ingest 点拒绝；下游硬不变量。
- **modules**：tracks/executor/review_findings.py, tracks/executor/publish_face.py, tracks/executor/verdict_face.py, tracks/executor/result_payload.py, tracks/executor/release_preview.py, tracks/executor/release_authorization.py, tracks/cli/release_cmd.py, tracks/executor/milestone_chain.py。
- **关联**：AC-FR0271-01/02@v0.8、AC-FR0272-01/02@v0.8、AC-FR0040-03@v0.4、AC-FR0140-02@v0.5。

### IF-HOTFIX262-ATTN-001 attention 停顿与凭据合同

- **合同**：§1d——GITHUB_TOKEN canonical；hint-only；loop 停顿 ≤1 次；去重；issue_fetch_failed 分类。
- **modules**：tracks/executor/run_loop.py, tracks/executor/release_tail.py, tracks/supervisor/service_intake.py, tracks/supervisor/intake.py。
- **关联**：AC-FR0283-02@v0.8、AC-FR0298-02@v0.9、AC-FR0329-02@v0.10。

### IF-HOTFIX262-HOSTENV-001 进程边界 env allowlist 合同

- **合同**：§1e——唯一 allowlist；denylist 废止；strip 集封闭；escape hatch。
- **modules**：tracks/executor/host_env.py + 10 个 subprocess 站点。
- **关联**：AC-NFR0143-02@v0.8、AC-FR0315-01@v0.9。

### IF-HOTFIX262-STAGE-001 阶段事件与 legacy 回退合同

- **合同**：§1f——三点 exactly-once 发射；kernel reducer 权威；投影/UI 合成回退显式优先。
- **modules**：tracks/executor/publish_runtime.py, tracks/executor/milestone_chain.py, tracks/kernel/release.py, tracks/server/projections_timeline.py, tracks/server/static/app/timeline.js。
- **关联**：AC-FR0276-01@v0.8、AC-FR0335-01/02@v0.11、AC-FR0333-01@v0.11。

### IF-HOTFIX262-UI-001 UI 性能与可读性合同

- **合同**：§1g——ac-chain 短路；slim 收紧；选中环；backlog 标题；统一 Todos inbox。
- **modules**：tracks/server/projections_core.py, tracks/server/projections_run.py, tracks/server/projections_timeline.py, tracks/server/static/styles.css, tracks/server/static/app/timeline.js, tracks/server/static/app/releases.js, tracks/server/static/app/views.js。
- **关联**：AC-FR0335-01/02@v0.11、AC-NFR0150-02@v0.9、AC-FR0333-01@v0.11、AC-FR0340-01@v0.11、AC-FR0307-01@v0.9。

### IF-HOTFIX262-CLEANUP-001 临时引用清理合同

- **合同**：§1h——`refs/trac/rgr/{run_id}/*`、临时 worktree、staging 清理；`remaining == 0` 断言。
- **modules**：tracks/executor/milestone.py。
- **关联**：AC-FR0276-01@v0.8。

### 继承合同组（本 delta 零语义变更，聚合登记）

IF-001～IF-012 全部既有标识（含 IF-RELEASE-001..003、IF-VERIFY-001..005、IF-SECURITY-001、IF-PUBLISH-001..002、IF-MILESTONE-001..002、IF-ISSUE-001..002、IF-HOTFIX-001..011、IF-BACKLOG-001、IF-TIMELINE-001/002、IF-RUNCARD-001、IF-WEBUI-001、IF-QUERY-001、IF-SECRECY-001、IF-MTEST-001、IF-TRACE-001..003、IF-GREENGUARD-001 等）语义逐字不变；本 delta 引用其中 IF-WEBUI-001、IF-QUERY-001、IF-SECRECY-001、IF-HOTFIX-004/011、IF-MTEST-001、IF-GREENGUARD-001。
