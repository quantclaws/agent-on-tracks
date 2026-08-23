---
interfaces_id: IF-006
spec_ref: SPEC-006
arch_ref: ARCH-006
created: 2026-08-19
status: draft
sha:
---

# v0.6 - 接口与类型化 Schema

本文是 IF-005（v0.5）的增量延伸。事件信封（IF-001 §2）、Command 基础结构（IF-001 §4）、State 投影（IF-001 §8）、Workflow 类型（IF-001 §9）、discuss 旁路（IF-003 §6/§7a）、M-IMPL 合同（IF-005 §1a–§1m）、live evidence 与 release-evidence（IF-005 §1j/§2d/§3h）、doc-comment-first 与 quarantine（IF-005 §1k–§1m）等不变；凡未提及者继承 IF-001/IF-003/IF-004/IF-005。v0.6 新增 hotfix 工作流合同：HOTFIX-TRIAGE 入口子状态机、issue 读取通道、fix/{issue} 分支与基线继承、run 并存与恢复、M-TEST 空 Shield 增量放行、M-IMPL 场景 B stale reconcile、缺口与退出路由。【R6 增补，D-41】测试执行选择语义合同：节点分类与四种选择（§1j）、证据四元组与复用/stale（§1k）、失败台账事件溯源与 FULL 链收敛（§1l）、测试命令独家所有权 schema（§1m；v6 增补 `{result}` 逐节点结果机器可读通道与 JUnit 解析/覆盖纯函数）、nightly CI 契约（§1n）。

## 0. 延续性（什么不变）

- `EventEnvelope` / `Command` / `Assignment` / `Outcome` / `StageDef` 结构与既有封闭集成员不变；v0.6 只做追加式扩展（§1a/§1b/§3a）。
- 既有 EVENT_TYPES / COMMAND_KINDS 成员、M-IMPL 21 子状态封闭集、`_NEXT_STAGE` 链、RedClass 封闭集、`trac validate`/`trac check` CLI 合同全部不变；v0.6 的 stage 值 `M-HOTFIX-TRIAGE` 不进 `_NEXT_STAGE` 链（非 canonical 顶层阶段）。
- v0.4 的 8 个、v0.5 的 12 个 IF- 标识不可变、不可复用；v0.6 增补 IF-HOTFIX-001～010 共 10 个标识（§5；R4 增 IF-HOTFIX-010）。
- `trac check trace` 对 feature 版本的文件级闭合语义逐字节不变；hotfix 计划级闭合只在「run 为 hotfix 且 `increment.declared` 在事件流」时启用（§1f）。
- BS-06 design-trace 的扫描范围合同（R3 增补，§2f）：inline-discussion blockquote 行是评审线程而非计划内容，不参与 test-plan 的 layer/IF- 归属扫描；§8 表格行作为唯一机器可读覆盖来源的地位不变（fail-closed）。
- hotfix 版本目录继承基线文档只读解析合同（R4 增补，§1i / IF-HOTFIX-010）：hotfix 项目目录只含 delta 三文档、无 acceptance.md/interfaces.md（FR-0241-02 source approval 不复制），canonical validator 经共享纯函数只读引用目标版本目录的对应文档；feature 版本目录的 validator 校验语义逐字节不变（dir-name 身份门控）。
- `.tracks/projects/project.toml` 测试执行合同：宿主合同继续由 Runtime 消费；其 D-41 扩展（run_selected 键、`{result}` 占位符、[nightly] 段）经原子实现切片交付（§1m/§1n，v3/v6），本文档不对该文件工作树内容作不变性断言；质量守卫栈合同不变（ARCH-006 §4.2）。
- 单写者锁（`runtime/lock`，O_CREAT|O_EXCL + holder PID）合同不变：被持有时 stderr `runtime lock held by pid <N>` + exit 1、零事件（FR-0242-04 直接复用）。
- 【R6】COMMAND_KINDS 零新增：选择与执行由既有门禁 handler（RED_CHECK/GREEN_GATE/REFACTOR_GATE/ISLAND_GATE_2）内部产出，kernel decide 控制流不变；EVENT_TYPES 追加 6 个选择/证据/台账/FULL 链事件（§1j–§1l），全部 append-only。
- 【R6】`trac check trace` / `trac validate --file` 对 feature 版本的语义不受 R6 影响；分类/选择是 Runtime 执行期行为，不改变文档校验器（contract 完整性校验追加 [nightly]/run_selected 必填项除外，见 §1n）。
- 【R6，v3 修订；v6 再修订】`.tracks/projects/project.toml` 的 run_selected 键（并发 flag 与 `--junitxml={result}` 内嵌于命令字符串，无独立 workers/dist 键）与 [nightly] 段的 schema 冻结于 §1m/§1n；每条 run/run_selected 各含 `{result}` 占位符恰好一次（run_selected 另含 `{nodes}` 恰好一次，§1m v6）；validator schema 激活、合同键扩展（[unit]、全部三层 run_selected、[nightly]）与 prompt/runtime 变更是**一个原子实现切片**——本文档轮不拥有该文件的并发工作树编辑（由另一授权 agent 持有）；schema 切片激活后 run_selected 缺失或占位符计数不合法 = contract_error fail-closed（§1m，无合成调用回退）。

## 1. 跨模块合同

### 1a. 新事件类型（EVENT_TYPES 追加，IF-HOTFIX-002）

事件由 executor 产出、kernel reducer 消费；`modules` 列标注实现/消费模块（跨模块接口 = 2+ 模块，Shield 须有 integration 覆盖）。

| 事件类型 | payload | 发出者 | modules | 备注 |
|:---|:---|:---|:---|:---|
| `hotfix.requested` | `issue: int`, `scenario: "post-release"\|"dev"` | cli（cmd_hotfix，writer lock 内） | cli, kernel, executor, report | SM-01.1；run 于入口即建立；REJECTED 也保留（§1c 终态语义） |
| `triage.prechecked` | `issue: int`, `issue_type: str\|None`, `scenario: str`, `status: "pass"\|"rejected"`, `reason: str\|None`, `next: str\|None`, `target_version: str\|None`, `active_branch: str\|None` | executor（`_do_precheck_hotfix`） | executor, kernel, cli, report | SM-01.2/.3/.4；`reason` 取值封闭（§1c REJECTEDReason）；pass 时含 target_version/active_branch |
| `anchor.validated` | `acs: list[str]`, `source: "sage"\|"human"`, `attempt: int`, `rationale_refs: list[str]` | executor（`_do_validate_anchor` / `complete via cmd_hotfix anchor`） | executor, kernel, cli, report | SM-01.5/.8；`acs` 元素为跨版本引用（§1e）；rationale_refs 为 Sage outcome 的逐条出处 blob 引用 |
| `human.anchor` | `mode: "manual"\|"feature_route"`, `acs: list[str]\|None`, `issue: int`, `actor: str` | cli（cmd_hotfix 子动作，submit_human_result） | cli, kernel, executor, report | SM-01.8/.9；manual 携带 acs（随后程序校验），feature_route 无 acs |
| `increment.declared` | `shield: "empty"`, `unit_rows: list[dict]`, `trace_status: "pass"`, `basis: "delta-test-plan"` | executor（hotfix M-TEST EXIT 门禁） | executor, kernel, report | FR-0244-04 空 Shield 增量放行依据；`unit_rows` = delta §8 全部 unit 行（AC@version + IF + 建议 test 名） |
| `test.baseline_captured` | `status: "passed"\|"failed"`, `baseline_id: str`, `baseline_tree: str`, `layers: ["unit","integration","e2e"]`, `nodes_count: int`, `empty_baseline: bool`, `node_digest_blob: str\|None`, `errors: list[str]` | executor（M-TEST 入口，首个 Shield WRITE 派发之前，R1 快照机制） | executor, kernel, report | IF-SELECT-001 R1 快照；本 run 进入 M-TEST 时对**继承测试树**（prior-to-Shield 当前工作树）做 unit+integration+e2e 全量 collect 并逐节点记源/体 digest；`baseline_id = sha256(canonical_json({tree, node_digests}))`（stamped identity）；`node_digest_blob` 指向 §3a 逐节点 digest 表；首次项目可合法捕获空集（`empty_baseline=true` 仅由成功 capture 产生——**缺失 capture ≠ 空 baseline**）；capture 失败 = fail-closed 路由上游/设计/合同缺陷（不静默空置 R1）；WAL/replay 复用同一 stamped capture（§1j） |
| `test.selected` | `scope: "r2_delta"\|"task_if"\|"select_diff"\|"full"`, `basis: str`, `nodes_count: int`, `nodes_blob: str`, `baseline: str`, `commit: str`, `tree_stamp: str`, `selection_id: str`, `task_id: str\|None`, `task_ifs: list[str]\|None` | executor（门禁 handler：RED_CHECK/GREEN_GATE/REFACTOR_GATE/ISLAND_GATE_2） | executor, kernel, report | IF-SELECT-002；`selection_id = sha256(canonical_json({scope,basis,nodes,baseline,commit,tree_stamp}))`（NFR-0130）；`tree_stamp` = 确定性 dirty-aware 工作树内容 stamp（§1j），与 `commit` 分离——同节点集合同 HEAD 不同未提交内容不得共享 selection identity；`nodes_blob` 指向逐节点表（§3a）；FULL scope 的选择事件与 `full.executed` 相邻成对 |
| `full.executed` | `round: "FULL_1"\|"FULL_F"\|"fallback_full"`, `suite: ["unit","integration","e2e"]`, `passed: bool`, `failed_nodes: list[str]`, `command_echo: dict[layer→argv]`, `evidence_ids: list[str]`, `serves_as_full_f: bool`, `outcomes_ref: str\|None`（v6：归一化逐节点结果 blob）, `gate: "ISLAND_GATE_2"` | executor（m_impl_runtime.py ISLAND_GATE_2） | executor, kernel, report | IF-FULLCHAIN-001；`serves_as_full_f=true` 标注 fallback 充当（FR-0253-04）与干净首轮充当（§1.0.6.5 退化情形）；`command_echo` 是 IF-RUNCONTRACT-001 审计比对对象；逐节点结果权威 = `{result}` JUnit XML（§1m v6），台账消费 `outcomes_ref` 归一化记录 |
| `ledger.opened` | `node: str`, `failure_signature: str`, `state: "OPEN"`, `selection_id: str`, `evidence_id: str`, `reason: str` | executor（ISLAND_GATE_2 台账记账） | executor, kernel, report | IF-LEDGER-001；每个失败节点一条 append-only 条目（FR-0253-01）；台账身份 = `(node, failure_signature)`——同节点新签名新建身份、同签名重启先前身份 |
| `ledger.transitioned` | `node: str`, `from: "OPEN"\|"CLASSIFIED"\|"FIXED"\|"PROVEN"\|"STALE"`, `to: 同左`, `attempt: int\|None`, `actor: str\|None`, `reason: str` | executor（DIAGNOSE/重派路径接线） | executor, kernel, report | IF-LEDGER-001；封闭转移集：OPEN→CLASSIFIED→FIXED→PROVEN、OPEN\|CLASSIFIED\|FIXED→STALE（上游变化）、STALE→OPEN（reconcile 后重驱）、FIXED→OPEN（证明失败：该条目 SELECT_DIFF/fallback 重跑同签名失败，重开重分类/重修）、PROVEN→OPEN（reopen：FULL_F 重现已 PROVEN 的同一 `(node, failure_signature)`）；未列出转移非法 fail-closed |
| `evidence.reused` | `kind: "green"\|"full_f"`, `reused_evidence_ids: list[str]`, `identity_basis: {tree, command, env, selection_id}`, `consumer_gate: str` | executor（REFACTOR_GATE / M-VERIFY 注册后） | executor, kernel, report | IF-EVIDENCE-001；复用判定唯一判据 = 四元组全一致 ∧ 无 STALE（NFR-0130-02）；引用被复用证据的身份（FR-0252/FR-0254） |
| `evidence.staled` | `targets: list[{kind: "selection"\|"evidence"\|"ledger", ref: str}]`, `reason: str` | executor（上游变化检测） | executor, kernel, report | IF-EVIDENCE-001；上游变化（设计 revision / task IF 集合 / baseline 重算）触发；STALE 传播共用同一实现函数（NFR-0130-02 不由各阶段自定变体） |

既有事件的 payload 扩展（成员追加，向后兼容）：

| 事件 | 扩展字段 | modules | 备注 |
|:---|:---|:---|:---|
| `run.completed` | `terminal_state` 取值集合追加 `"rejected"` / `"feature_route"` / `"ac_gap"` / `"spec_gap"`（既有 `"boundary"` 不变） | executor, kernel, cli, report | hotfix 四个终态；boundary 语义沿用 v0.5 AC-FR0160-03 |
| `run.completed`（hotfix boundary） | payload 追加 `restored_active_run: str\|None`, `restored_branch: str\|None` | executor, kernel | 分支恢复 checkout 审计（ARCH-006 §3.5） |
| `verdict.failed` | `check` 取值集合追加 `"anchor_invalid"` | executor, kernel | SAGE_TRIAGE 锚定校验失败（重派 ≤3） |
| `prism.verdict`（M-DESIGN PRISM_REVIEW） | payload 追加 `anchor_verdict: "upheld"\|"overturned"`（缺省 `"upheld"`） | executor, kernel | overturned → 回 M-HOTFIX-TRIAGE/SAGE_TRIAGE（FR-0243-03，不消费 M-DESIGN 预算） |
| `backlog.recorded` | payload 追加可选 `issue: int`, `decision: "feature_route"\|"ac_gap"\|"spec_gap"`（既有 `"queued"` 不变） | cli, executor, kernel, report | FEATURE_ROUTE 与中段退出的去向记录 |
| `stage.rolled_back`（hotfix） | `to_stage` 可为 `"M-HOTFIX-TRIAGE"`，`reason` 追加 `"anchor_overturned"` | kernel, executor, report | anchor 推翻回滚路由 |
| `baseline.frozen`（hotfix M-IMPL） | payload 追加 `scenario_branch_head: str\|None`（场景 B 活跃分支 HEAD） | executor, kernel | stale 检测输入审计（IF-HOTFIX-008） |
| `baseline.inherited`（新事件，归本表） | `target_version: str`, `baseline_digest: str`, `anchor_acs: list[str]`, `baseline_doc_paths: list[str]` | executor（`_do_complete_hotfix_entry`） | executor, kernel, report | source approval 记录；不复制文件、不重新批准（FR-0241-02）。`target_version`+`baseline_doc_paths` 是 IF-HOTFIX-010 只读基线解析的权威记录（目录名编码为其持久投影） |
| `test.collected`（R6 payload 扩展） | 追加 `inherited_r1: int`, `delta_r2: int`, `removed: int`, `failures: list[{node, error_class}]`, `per_node_blob: str`；`passed=false` 时 failures 非空 | executor（M-TEST COLLECT） | executor, kernel, report | FR-0250-01 全量 collect + 逐节点结果 + R1/R2 分类计数（IF-SELECT-001）；`per_node_blob` 指向 §3a 逐节点表 |
| `red.validated`（R6 payload 扩展） | 追加 `selection_id: str`, `nodes_blob: str`（被选节点逐条判定表引用）, `outcomes_ref: str`（v6：归一化逐节点结果 blob——`{result}` JUnit 解析+精确覆盖校验后的持久形态） | executor（RED_CHECK） | executor, kernel, report | FR-0250-02：证据绑定 selection identity；被选集 ⊆ R2/T-DELTA 由 replay 可复核；合法 Red 判定输入 = JUnit testcase failure/error 详情（§1m v6），stdout/stderr 不作权威 |
| `green.committed`（R6 payload 扩展） | 追加 `evidence_ids: list[str]`（GREEN_GATE 证据引用） | executor（GREEN_COMMIT） | executor, kernel, report | FR-0252-01 REFACTOR 复用的定位锚 |

（R6 新增第 7–13 个 v0.6 事件类型：`test.baseline_captured` / `test.selected` / `full.executed` / `ledger.opened` / `ledger.transitioned` / `evidence.reused` / `evidence.staled`，与表首 6 个共同构成 v0.6 新事件封闭集。）

### 1b. Command kind 增量（COMMAND_KINDS 追加，IF-HOTFIX-002 / IF-HOTFIX-003~005）

命令由 kernel `_decide_hotfix_triage` 产出、executor handler 执行：

```python
kind: Literal[...,               # IF-001/IF-003/IF-004/IF-005 原有成员不变
      "precheck_hotfix",         # SM-01.1-.4：issue fetch + scenario/活跃分支/目标版本定位（确定性，无 LLM）
      "validate_anchor",         # SM-01.5/.6：锚定引用逐条跨版本存在性校验
      "complete_hotfix_entry"]   # SM-01.10：fix/{issue} 分支 + 基线继承 + stage.entered(M-DESIGN)（原子）
```

`dispatch_agent`（sage, substate=SAGE_TRIAGE）复用既有 kind；三个新 kind 遵守写前日志 + per-kind execute/reconcile（D-13）：`triage.prechecked(pass)` / `anchor.validated` / `branch.created(fix/N)` 事件即幂等标记，重启后已成功则跳过。

### 1c. State 字段增量与封闭集（IF-001 §8 追加，IF-HOTFIX-002）

```python
# v0.6 hotfix (SM-01 HOTFIX-TRIAGE, FR-0240~0248)
hotfix_issue: int | None = None          # hotfix 的 GitHub issue 号（需求追踪身份）
hotfix_scenario: str | None = None       # "post-release" | "dev"
hotfix_target_version: str | None = None # 继承基线的目标版本（如 "v0.5"）
hotfix_anchor_acs: list | None = None    # 锚定 AC 集合（跨版本引用，§1e）
hotfix_precheck_passed: bool = False     # PRECHECK 通过（triage.prechecked pass）
hotfix_anchor_validated: bool = False    # 锚定校验通过（anchor.validated）
hotfix_branch: str | None = None         # "fix/{issue}"
baseline_inherited: bool = False         # baseline.inherited 已产出
# v0.6 R6 (D-41 selection semantics, FR-0250~0255)
active_selection_id: str | None = None   # 最近一次 test.selected 的 selection identity
full_chain_round: str | None = None      # FULL_1 | FULL_F | fallback_full | None（FULL 链进行中）
ledger_open: int = 0                     # 台账非终态条目计数（OPEN|CLASSIFIED|FIXED|STALE）
ledger_rebuilt: bool = False             # 重启后 rebuild_ledger 已重建（幂等标记）
```

- `stage` 取值追加 `"M-HOTFIX-TRIAGE"`（非 canonical 顶层阶段，不进 `_NEXT_STAGE`；由 cmd_hotfix 写入 `stage.entered`，出口为 `M-DESIGN` 或终态）。
- HOTFIX-TRIAGE substate 封闭集：`PRECHECK | SAGE_TRIAGE | AWAIT_HUMAN`（3 个子状态；ANCHORED/REJECTED/FEATURE_ROUTE 是转移不是驻留子状态）。
- `awaiting` 取值追加 `"hotfix_triage"`（AWAIT_HUMAN 驻留时 `status="awaiting_human"`）。
- REJECTED 原因封闭集（`triage.prechecked.reason`）：

```python
HotfixRejectionReason = Literal[
    "not_bug",                 # issue labels 不含 bug
    "issue_not_found",         # issue 不存在（404 / 种子缺失）
    "issue_fetch_failed",      # 网络/鉴权失败（fail-closed，可重试）
    "scenario_invalid",        # --scenario 取值非法（缺省在 CLI 层拦截，不建 run）
    "no_active_release_branch",# dev 场景无活跃 release 分支
    "baseline_not_locatable",  # 目标版本三件套未批准/不可定位（保留补全后重试路径）
    "fix_branch_exists",       # fix/{issue} 分支已存在（防半建 run 串写）
]
```

- 每个 `triage.prechecked(status="rejected")` payload 必含 `reason`（上述封闭集之一）与 `next`（人可读下一步：补全 issue 后重试 / 改走 `trac start` / 处理分支占用）。

### 1d. executor/hotfix.py 纯函数与 effects/github.py 读通道（IF-HOTFIX-003/004/005）

**modules**: executor/hotfix.py（实现）、executor/executor.py（消费，`_do_precheck_hotfix` / `_do_validate_anchor` / `_do_complete_hotfix_entry`）、effects/github.py（issue 数据源）、cli/main.py（cmd_hotfix 装配）——跨模块接口，须有 integration 覆盖。

```python
@dataclass(frozen=True)
class HostIssue:
    number: int
    title: str
    body: str
    labels: tuple[str, ...]
    @property
    def is_bug(self) -> bool: ...        # labels 含 "bug"（§3.1 ARCH-006 约定）

@dataclass(frozen=True)
class PrecheckReport:
    status: Literal["pass", "rejected"]
    reason: HotfixRejectionReason | None
    next: str | None
    target_version: str | None           # pass 时必填（如 "v0.5"）
    active_branch: str | None            # dev 场景 pass 时必填（如 "releases/v0.6"）

def precheck_hotfix(
    issue: HostIssue | None,             # fetch_issue 结果；None = fetch 失败（含分类）
    scenario: str,                       # "post-release" | "dev"
    repo_branches: list[str],            # git branch --list 输出集
    active_run_branch: str | None,       # 活跃 run 的 branch（无活跃 run 则 None）
    projects_dir: Path,                  # .tracks/projects
    approved_versions: set[str],         # events 表 approval.recorded 覆盖的版本集
) -> PrecheckReport:
    """IF-HOTFIX-003 PRECHECK 确定性预检（纯函数，无 LLM）。
    规则 P-1..P-5（ARCH-006 §3.2）：issue type=bug、scenario 合法、dev 场景活跃
    release 分支（活跃 run branch 或 HEAD 为 releases/*）、目标版本已批准基线
    （dev=分支名版本；post-release=已批准最高版本，数值组比较）、fix/{issue} 未占用。
    fetch 失败（issue=None）-> issue_not_found | issue_fetch_failed。"""

def locate_target_version(
    scenario: str, active_branch: str | None,
    projects_dir: Path, approved_versions: set[str],
) -> str | None:
    """IF-HOTFIX-005 目标版本定位（纯函数）。dev: releases/vX.Y -> vX.Y（须已批准）；
    post-release: approved_versions 中数值组最大者。不可定位 -> None。"""

def parse_issue_hints(body: str) -> dict:
    """IF-HOTFIX-003 bug issue template 可选字段解析（纯函数，确定性正则）。
    返回 {"version": str | None, "fr_nfr": list[str]}；字段缺失返回空值，
    PRECHECK 不因此 fail（仅作 Sage 锚定辅助语料）。"""

def parse_anchor_refs(acs: list[str]) -> list[tuple[str, str]]:
    """IF-HOTFIX-004 锚定引用解析（纯函数）："AC-FR0030-01@v0.5" -> ("AC-FR0030-01", "v0.5")。
    语法不合规（缺 @version / id 格式错）-> ValueError("IF-HOTFIX-004:<detail>")。"""

def validate_anchor_refs(
    refs: list[tuple[str, str]], projects_dir: Path,
) -> tuple[bool, list[str]]:
    """IF-HOTFIX-004 锚定程序校验（纯函数 + 文件只读）。
    每条 (ac_id, version)：projects_dir/<version>/acceptance.md 必须存在且含
    "### <ac_id>" 标题。返回 (all_exist, missing)，missing 形如
    "AC-FR9999-99@v0.5 not in .tracks/projects/v0.5/acceptance.md"。"""

def complete_hotfix_entry(
    repo: Path, run_id: str, issue: int, scenario: str,
    target_version: str, anchor_acs: list[str],
) -> dict:
    """IF-HOTFIX-005 ANCHORED 原子入口完成（副作用，executor 侧）：
    create_branch(fix/{issue}, base=main|活跃分支) + checkout + baseline.inherited
    记录 + stage.entered(M-DESIGN)。任一步失败 fail-closed（不残留半建 run：
    分支创建失败即 run.completed(rejected, reason=fix_branch_creation_failed)）。
    幂等标记 = branch.created(fix/N) + baseline.inherited 事件。"""
```

effects/github.py 增量（读通道）：

```python
class FakeIssueBackend:
    def fetch_issue(self, issue_number: int) -> HostIssue | None:
        """读 .tracks/runtime/host-issues.json（§3b schema）；键缺失 -> None(not_found)。"""

class GithubBackend:
    def fetch_issue(self, issue_number: int) -> HostIssue | None:
        """GET /repos/{gh_repo}/issues/{n}；404 -> None；网络/鉴权 -> GithubIssuesError。"""
```

`select_issue_backend` 选择逻辑不变（fake/env 条件同既有）。fake 通道 `TRAC_FAKE_SIMULATE` 不注入 fetch 失败（fetch 失败经种子文件缺失构造）。

### 1e. 跨版本 AC 引用语法（IF-HOTFIX-004 / IF-HOTFIX-007）

- 语法：`AC-(N?FR)\d{4}-\d{2}@v\d+\.\d+`（如 `AC-FR0030-01@v0.5`）；`@` 后为 `.tracks/projects/` 下存在的版本目录名。
- 与既有 trace 标记语法（`# AC-FRXXXX-YY@<version> TRACKS-TRACE`）同一前缀；v0.6 起 `@<version>` 的解析目标从「当前版本」扩展为「所指版本」：`trac check trace` 与 `trac validate --file test-plan.md` 对每个带 `@` 的引用在所指版本 acceptance.md 中做存在性校验（feature 版本自身的 §8 行仍指向当前版本，行为不变）。
- hotfix run 不分配新 AC 编号：锚定集合、delta test-plan §8 行、回归用例标记全部使用跨版本引用（FR-0244-03）。

### 1f. M-TEST 空 Shield 增量放行合同（IF-HOTFIX-007，ARCH-006 §3.6）

**modules**: kernel/m_test.py（EXIT 路由）、executor/executor.py（increment.declared 产出 + 门禁执行）、executor/validate.py + executor/test_tasks.py（delta §8 层归属校验）、checks/trace.py（hotfix 计划级闭合）——跨模块接口，须有 integration 覆盖。

- **delta test-plan §8 扩展（仅 hotfix delta plan 合法）**：layer 词表扩展 `unit`；`trac validate --file test-plan.md` 在 hotfix 版本目录（目录名匹配 `{version}-hotfix-{issue}`）下允许 unit 行并校验其 AC@version 引用与 IF 归属；feature 版本目录下 unit 行仍为硬错误（不变量）。
- **放行判定**（hotfix run 的 M-TEST EXIT）：§8 无 integration/e2e 行且 ≥1 unit 行 → 不派发 Shield WRITE（无 test.written/test.committed 活动）、产出 `increment.declared` 事件、`trac check trace` 在 hotfix scope（锚定 AC 集合）以「§8 声明 unit 行 + 既有文件标记」闭合，exit 0 才 EXIT。
- **M-IMPL 物化闭环**：task graph 携带 delta §8 unit 行；TASK_REVIEW 的 AC trace 校验断言「声明的 unit 用例文件存在且标记一致」；ISLAND_GATE_2 跑全量文件级 `trac check trace`。声明-文件不一致 fail closed（check=`trace_mismatch`，verdict.failed 封闭集追加该成员）。

### 1g. M-IMPL 场景 B baseline 输入扩展（IF-HOTFIX-008）

**modules**: executor/m_impl_runtime.py（BASELINE digest 输入）、kernel/m_impl.py（NEEDS_ATTENTION 路由复用）——跨模块接口，须有 integration 覆盖。

- hotfix run（`state.hotfix_scenario == "dev"`）的 BASELINE digest 追加输入：活跃 release 分支当前 HEAD SHA（`git rev-parse {active_branch}`）。活跃分支推进 → 重算 digest 不匹配 → `baseline.frozen(status="stale", scenario_branch_head=...)` → NEEDS_ATTENTION（既有路由，`trac status` 报告 needs_attention + 冲突证据）。此即 flow.md §16.3.3 要求保留的 reconcile 冲突路径（merge 本身不执行）。
- post-release 场景无此输入（base=main，与活跃 run 独立）。

### 1h. dispatch 物化字段集扩展（FR-0190 第 9 项追加，IF-HOTFIX-004/005/006）

```python
# assignment payload 追加（hotfix 相关派发；None 对非 hotfix 派发不变）
{
    "anchor_acs": list[str] | None,      # Sage SAGE_TRIAGE 输出回读 / Archer M-DESIGN 输入 / Devon 提交 provenance
    "target_version": str | None,        # 继承基线的目标版本
    "baseline_doc_paths": list[str] | None,  # 目标版本三件套 + 设计三文档只读路径（M-DESIGN delta 输入）
    "anchor_hints": dict | None,         # parse_issue_hints 结果（Sage 辅助语料）
    "hotfix_issue": int | None,          # hotfix issue 号（Devon G commit trailers 的 Tracks-Issue 来源）
}
```

agent 不得自行搜索/猜这些字段（FR-0190 纪律不变）；无效输入（如 hotfix M-DESIGN 派发缺 anchor_acs）不调用 backend，failed outcome=`stub_gap`。

### 1i. hotfix 版本目录继承基线文档只读解析（IF-HOTFIX-010，R4）

**modules**: `executor/test_tasks.py`（共享纯函数 `resolve_inherited_baseline_docs` + `check_design_trace_file` / `check_test_tasks_contract_file` 消费）、`executor/validate.py`（`trac validate --file` 消费侧）、`checks/trace.py`（hotfix 计划级闭合的 `projects_dir / version` 查找锚定同一目标版本目录）——跨模块接口，须有 integration 覆盖。

- **合同**：`resolve_inherited_baseline_docs(doc_path: Path) -> tuple[Path, Path]` 为纯函数（无 I/O 副作用，仅路径构造）：
  - `doc_path.parent.name` 匹配 `v\d+\.\d+-hotfix-\d+`（hotfix 版本目录，身份同 capabilities `_HOTFIX_VERSION_RE`）→ 解析 `target_version = "v{M}.{m}"`，返回 `(doc_path.parent.parent / target_version / "acceptance.md", doc_path.parent.parent / target_version / "interfaces.md")`（只读引用，不复制文件，符合 FR-0241-02 source approval）。
  - 其余（feature 版本目录 `v{M}.{m}` 或非版本目录）→ 返回 `(doc_path.parent / "acceptance.md", doc_path.parent / "interfaces.md")`（与既有 `path.parent / ...` 行为逐字节一致，不变量由 dir-name 身份门控保证）。
- **消费**：`check_design_trace_file` 与 `check_test_tasks_contract_file`（test_tasks.py）将其硬编码的 `path.parent / "acceptance.md"` / `path.parent / "interfaces.md"` 替换为经 `resolve_inherited_baseline_docs(path)` 返回的路径；后续存在性检查与读取不变。目标版本目录或其 acceptance.md/interfaces.md 缺失 → fail-closed（既有存在性检查触发，错误信息指明「继承基线不可定位」+ target_version）。
- **同一解析钉住**：`checks/trace.py` 的 hotfix 计划级闭合 `_anchor_ref_errors` 经 `projects_dir / version / acceptance.md` 查找（既有，IF-HOTFIX-007），其中 `projects_dir = doc_path.parent.parent`——与本 resolver 锚定同一目标版本目录，一致由构造保证。M-DESIGN EXIT（`trac validate --file` + design-trace）与 M-IMPL TASK_REVIEW/ISLAND_GATE_2（`trac check trace` 文件级）消费同一解析。
- **权威性**：`baseline.inherited` 事件（§4a 行）的 `target_version` + `baseline_doc_paths` 是该只读引用的权威记录；目录名编码是 `complete_hotfix_entry` 按该事件构造的持久投影，二者一致由构造保证——文件级 validator 无需读事件流（保持 validate 的文件级纯度）。
- **不变量**：feature 版本目录的 validator 校验语义逐字节不变（dir-name 身份门控；非 hotfix 目录永不进入跨版本解析路径）。

### 1j. 节点分类与选择语义（IF-SELECT-001/002，R6；ARCH-006 §1.0.6.1/.2）

**modules**: executor/test_select.py（实现）、kernel/m_test.py（COLLECT/RED_CHECK 消费）、executor/m_impl_runtime.py（GREEN_GATE/REFACTOR_GATE/ISLAND_GATE_2 消费）——跨模块接口，须有 integration 覆盖。

```python
Layer = Literal["unit", "integration", "e2e"]
NodeClass = Literal["r1", "r2", "removed"]   # R1/T-HIST | R2/T-DELTA | REMOVED（fail-closed 测试资产删除，非良性分类）

@dataclass(frozen=True)
class BaselineAssets:
    nodes: frozenset[str]                            # baseline 冻结测试资产的节点集（node id）
    node_digests: Mapping[str, str]                  # 逐节点源/体 digest（node id -> sha256；非整文件 digest）

@dataclass(frozen=True)
class TestBaselineSnapshot:
    """R1 快照（test.baseline_captured 的可执行形态，§1a）：M-TEST 入口、首个
    Shield WRITE 派发之前，对继承测试树（prior-to-Shield 当前工作树）做
    unit+integration+e2e 全量 collect 后冻结的逐节点 digest 投影。"""
    baseline_id: str                       # sha256(canonical_json({tree, node_digests}))——stamped identity
    baseline_tree: str                     # 捕获时树身份（commit SHA 或未提交工作树内容 digest）
    node_digests: Mapping[str, str]        # node id -> 逐节点源/体 digest（三层全集；非整文件 digest）
    layers: tuple[Layer, ...]              # 恒为 ("unit", "integration", "e2e")
    empty_baseline: bool                   # True 仅当成功 capture 且零节点（首次项目合法空集）

def capture_test_baseline(
    collect_layer_nodes: Callable[[Layer], list[str]],  # layer -> 该层可 collect 节点 id（contract 三层 paths 全量 collect）
    node_digest: Callable[[str], str],                  # node id -> 节点源/体 digest
    tree_identity: str,                                 # 捕获时树身份
) -> TestBaselineSnapshot:
    """R1 快照捕获（纯编排，I/O 经注入的 callable）：三层全量 collect + 逐节点
    digest + stamped identity。任一层 collect/import 失败由调用方路由 fail-closed
    （test.baseline_captured(status=failed) → verdict.failed 契约/设计缺陷 → 上游），
    绝不以「collect 失败 ⇒ 空快照」继续——静默空置 R1 是缺陷行为。
    首次项目零节点 = 合法 empty_baseline=True 快照。同输入复跑一致（确定性）。"""

def collect_node_source_digests(
    repo: Path,                                        # 宿主仓库根（node id 相对路径的锚点）
    nodeids: Iterable[str],                            # node id（``file.py::func[param]`` 形态）
) -> dict[str, str]:
    """逐节点源 digest 的真实实现（capture_test_baseline 之 node_digest 注入位的
    可执行形态，v6 增补）：按 nodeid 定位物理文件中该函数 def 的源代码段做 sha256
    ——同文件不同函数 digest 各异（编辑单个函数体只改变该节点 digest，未变兄弟
    节点 digest 逐字节不变；整文件 digest 是缺陷行为）；同一函数的参数化节点共享
    同一源 digest；同输入复跑一致。文件缺失或无法定位函数定义 => fail-closed，
    绝不整文件兜底。"""

class MissingBaselineCaptureError(Exception):
    """COLLECT 分类时刻本 run 无已持久化的 test.baseline_captured(passed)：
    fail-closed——禁止以既往事件或 post-WRITE 可变树「猜测」baseline（§4a
    分类输入唯一来源 = 本 run pre-WRITE stamped 快照）。"""

def classify_nodes(
    baseline: BaselineAssets,                        # 冻结测试资产 = 本 run persisted TestBaselineSnapshot（首轮 run 为合法空集）
    current_nodes: Iterable[str],                    # 当前工作树逐层 collect 全集（unit+integration+e2e）
    current_node_digests: Mapping[str, str],         # node id -> 节点源/体 digest
) -> dict[str, NodeClass]:
    """IF-SELECT-001 确定性分类（纯函数，逐节点 digest，禁止整文件 digest）：
    "r1": node∈baseline ∧ 节点 digest 一致（同文件未变兄弟节点保持 r1）；
    "r2": node∉baseline（新增）∨ 节点 digest 变化——恰好且仅为这两类；
          变更 support/fixture 不参与分类（仍参与全量 collect/import，当前 r2 测试在其上执行；
          未变历史行为的回归等待 M-IMPL FULL 链与 nightly CI）；
    "removed": node∈baseline ∧ ∉current——fail-closed 测试资产删除（调用方必须路由
          test contract defect 并阻断 M-TEST 退出，不得静默注销）。同输入复跑一致。"""

class EmptyR2SelectionError(Exception):
    """feature M-TEST 的 SELECT_R2 结果为空：fail-closed，不 vacuous 通过。"""

def require_nonempty_r2_selection(
    scope: str,                                      # 仅接受 "r2_delta"（其他 scope 非法 fail-closed）
    selected: Sequence[str],
    *, allow_explicit_unit_increment: bool,          # hotfix FR-0244 显式 unit-only 增量声明旁路位
) -> list[str]:
    """M-TEST SELECT_R2 放行判定（kernel m_test RED_CHECK 消费）：
    allow_explicit_unit_increment=False（feature）∧ selected 为空 =>
    EmptyR2SelectionError；=True（hotfix increment.declared）空集放行返回原样。
    非空选择集一律原样返回。"""

def make_selection_id(
    scope: str, basis: str, nodes: Iterable[str], baseline: str, commit: str,
    tree_stamp: str = "",
) -> str:
    """IF-SELECT-002 selection identity（纯函数）：
    sha256(canonical_json({scope,basis,nodes,baseline,commit,tree_stamp}))，
    NFR-0130 唯一判据输入。``tree_stamp`` = 确定性 dirty-aware 工作树内容
    stamp（executor seam：相关源/测试/配置路径；干净 → HEAD commit 身份；
    脏 → 对变更路径当前内容 sha256——.tracks/、缓存、venv、构建产物等
    Runtime 状态永不入 stamp，WAL/replay 复算一致），与 ``commit`` 分离：
    同节点集合同 HEAD 不同脏内容 => 不同 selection identity（证据复用洞）。"""

def select_r2(classification: Mapping[str, NodeClass]) -> list[str]:
    """SELECT_R2（scope=r2_delta）：全部 r2 节点，稳定排序。M-TEST RED_CHECK 消费。
    调用方纪律：结果经 require_nonempty_r2_selection 判定放行——feature 空选择集
    = fail-closed（不 vacuous 通过）；hotfix unit-only 显式增量声明旁路保留（FR-0244）。
    执行仍仅限实际 R2 节点：R1 节点（含历史 unit）永不进入 M-TEST 执行记录。"""

def select_task(
    red_unit_manifest: Iterable[str],             # task 不可变 R commit 的 Runtime 捕获 RED artifact manifest（unit 节点）
    green_touched_unit_files: Iterable[str],      # 本 task GREEN 触达的 unit 文件
    current_unit_nodes: Iterable[str],            # 当前 unit collect 全集（GREEN-touched 文件 -> 节点展开）
    task_ifs: Iterable[str],                      # task 声明实现的 IF 集合
    int_green_index: Mapping[str, Iterable[str]], # IF -> integration 变绿条件命中节点（test-plan §8 IF 行；§8 只含 integration/e2e）
) -> list[str]:
    """SELECT_TASK（scope=task_if）：targeted unit = RED artifact manifest unit 节点 ∪ GREEN-touched
    unit 文件内节点（均 Runtime 捕获，确定性；unit 归属不经 test-plan §8——§8 IF 行只含 integration/e2e）；
    integration = task IF 归属命中；不含 e2e、不含 R1 全量。GREEN_GATE / REFACTOR_GATE 同 scope 重跑消费。"""

@dataclass(frozen=True)
class DiffSelection:
    nodes: list[str]
    reliable: bool                      # False => 回退 FULL（fallback_full）
    basis: str

def select_diff(
    fixed_entry: Mapping,               # 单条台账 FIXED 条目（含 node/selection_id/evidence_id）
    repair_touched_files: Iterable[str],# 该条目修复 commit 触达文件
    import_graph: Callable[[str], set[str]],  # 模块级 import 可达分析（复用 trac check reach 分析器）
) -> DiffSelection:
    """SELECT_DIFF（scope=select_diff）：该条目失败节点本身 ∪ 触达文件 import 可达命中的节点；
    每条目 FIXED 后立即执行其 SELECT_DIFF（per bug/fix）——通过 FIXED→PROVEN，
    同签名失败 FIXED→OPEN 重分类/重修；推导不可靠（空集歧义/图不可得）=> reliable=False，
    调用方回退 FULL（fallback 结果逐条目落转移）。多条目 union/batching 仅在每条目的
    选择与证据仍可单独归因时为可选优化，非规范语义。"""
```

**R1 快照机制纪律（test.baseline_captured，§1a；v5 增补）**：

- **捕获时点与时序**：Runtime 在进入 M-TEST 时、本 run 首个 Shield WRITE 派发之前执行 `capture_test_baseline` 并持久化 `test.baseline_captured(passed)`（含 `node_digest_blob`）；WRITE 之后的既有全量 COLLECT 捕获当前三层 inventory，并以**该已持久化快照**为唯一分类 baseline。分类 baseline 的来源是 prior-to-Shield 当前 run 的 stamped capture——既不是既往 run 的事件猜测，也不是 WRITE 后可变树的事后重算（二者均为缺陷行为）。
- **空层归一化**：capture 时点 Shield 尚未写入，fresh feature 项目的声明测试路径可能全部尚不存在——Runtime 在 section cwd 有效时于执行 collect 前将该层记为空集；路径存在但 pytest 返回 rc4 属合同/usage 缺陷并 fail-closed，只有 rc5 是执行后的合法零节点结果。逐节点 digest 经 `collect_node_source_digests`（v6）从物理源码函数段计算（同文件兄弟隔离、参数化共享、确定性）。
- **缺失 = fail-closed**：COLLECT 分类时刻找不到本 run 已持久化的 passed 快照 ⇒ `MissingBaselineCaptureError`（contract error 路由），绝不以「空集」继续。
- **capture 失败 = fail-closed 上游/设计路由**：任一层 collect/import 失败时 `test.baseline_captured(failed)` → `verdict.failed(check=baseline_defect, target_stage=M-DESIGN, artifact_disposition=rollback)`——继承树在 Shield 未写入前就不可 import 属上游/设计/合同缺陷，不是 Shield 重派事项；静默空置 R1 继续门禁是缺陷行为。
- **WAL/replay 复用同一 stamped capture**：中断/重启/replay 后的分类与选择必须复用本 run 已持久化的同一 `baseline_id`（`test.selected.baseline == test.baseline_captured.baseline_id` 可复核）；恢复路径不得从可变工作树重新 capture。

### 1k. 证据四元组与复用/stale 判定（IF-EVIDENCE-001，R6；ARCH-006 §1.0.6.3）

**modules**: executor/test_select.py（实现）、executor/m_impl_runtime.py 与 kernel/m_test.py/m_impl.py（各门禁消费同一函数）——跨模块接口，须有 integration 覆盖。

```python
@dataclass(frozen=True)
class EvidenceIdentity:
    tree: str             # commit SHA 或未提交门禁的工作区内容 digest
    command: tuple[str, ...]  # 实际执行 argv（contract 替换后逐字展开）
    env: str              # 环境指纹：解释器版本+pytest 版本+TRAC_* 影响位图摘要
    selection_id: str     # 本次选择的 identity（§1j）

def evidence_identity(ident: EvidenceIdentity, node: str, result: str,
                      attempt: int, actor: str) -> str:
    """IF-EVIDENCE-001 证据 id（纯函数）：sha256(canonical_json(四元组+node/result/attempt/actor))。"""

def reuse_allowed(
    evidence: EvidenceIdentity, current: EvidenceIdentity,
    stale_refs: AbstractSet[str],
) -> bool:
    """复用判定唯一实现（全门禁共用，NFR-0130-02 不由各阶段自定变体）：
    四元组逐项相等 ∧ 相关引用不在 stale_refs ⇒ True；任一不一致（stale）⇒ False。"""

def emit_stale_propagation(upstream_change: Mapping) -> list[dict]:
    """上游变化（设计 revision / task IF 集合 / baseline 重算）-> evidence.staled targets
    [{kind: selection|evidence|ledger, ref}]；STALE 传播唯一入口。"""
```

### 1l. 失败台账事件溯源与 FULL 链收敛（IF-LEDGER-001 / IF-FULLCHAIN-001，R6；ARCH-006 §1.0.6.4/.5）

**modules**: executor/test_select.py（重建/判定纯函数）、executor/m_impl_runtime.py（FULL 链循环 + ledger.* 记账接线）、kernel/m_impl.py（出口门禁消费台账状态）——跨模块接口，须有 integration 覆盖。

- **台账状态机（封闭转移集，未列出即非法 fail-closed）**：

```
OPEN → CLASSIFIED → FIXED → PROVEN      # 正向收敛（ledger.transitioned 逐转移落事件）
OPEN | CLASSIFIED | FIXED → STALE       # 上游变化（evidence.staled 同源触发）
STALE → OPEN                            # reconcile 后重驱（回到收敛轨道）
FIXED → OPEN                            # 证明失败：该条目 SELECT_DIFF/fallback 重跑同签名失败——重开重分类/重修
PROVEN → OPEN                           # reopen：FULL_F 重现已 PROVEN 的同一 (node, failure_signature)——重启先前身份并重证
```

- **台账身份**：条目身份 = `(node, failure_signature)`（`ledger.opened` 携带 failure_signature）；同一节点的新 failure signature 新建身份（新 `ledger.opened` 条目），同一 signature 重启先前身份（`PROVEN→OPEN` reopen）。clean 谓词与回放对 reopen 语义一致。

- **WAL/replay**：`ledger.opened` / `ledger.transitioned` 先落事件再据以判定推进；重启后由 `rebuild_ledger(events)` 回放重建，重建结果与中断前一致，FULL 链从重建状态精确续跑（重建含 reopen 转移；续跑不重复与重建状态一致的历史执行）。

```python
LedgerStateValue = Literal["OPEN", "CLASSIFIED", "FIXED", "PROVEN", "STALE"]

def rebuild_ledger(events: Iterable[Mapping]) -> dict[str, LedgerStateValue]:
    """IF-LEDGER-001 WAL 回放重建（纯函数）：按 seq 应用 opened/transitioned；
    非法转移/未知状态 -> LedgerCorruptionError（fail-closed，不猜测语义）。"""

def ledger_is_clean(state: Mapping[str, LedgerStateValue]) -> bool:
    """干净 ⇔ 非空 ∧ 全部条目 == "PROVEN"；空台账仅在 FULL_1 干净路径视为收敛。
    未知/缺失/STALE 条目一律 False（脏台账不跳过 FULL_F、不产出 stage.exited(M-IMPL)）。"""

def run_full_chain(
    execute_full: Callable[[str], Mapping],              # round -> full.executed payload
    open_entries: Callable[[list[str]], None],           # 失败节点 ledger.opened
    drive_diagnose_fix: Callable[[str], bool],           # 单条目：既有 DIAGNOSE/重派路径至 FIXED
    entry_diff_selection: Callable[[Mapping], DiffSelection],  # 单条 FIXED 条目 -> 其确定性 SELECT_DIFF
    prove_entry: Callable[[Mapping, DiffSelection], bool],     # 重跑该条目选择集；False = 同签名失败
) -> Literal["exited"]:
    """IF-FULLCHAIN-001 无界收敛循环（ARCH-006 §1.0.6.5 伪代码的唯一权威实现位）：
    FULL_1 -> 失败逐条 ledger.opened -> 逐条目修复-证明（每条 FIXED 后立即 SELECT_DIFF：
    通过 FIXED→PROVEN；同签名失败 FIXED→OPEN 重修）或 fallback_full 逐条目落转移
    （通过 FIXED→PROVEN、同签名失败 FIXED→OPEN，clean 充当 FULL_F）->
    全部 PROVEN 且无 STALE 后 FULL_F 干净出口；新失败循环，无 loop-count cap、
    无超限放弃；仅 per-agent attempt 预算照旧消费。
    serves_as_full_f 标注 fallback 充当与干净首轮充当（退化情形）。多条目 union/batching
    仅在每条目的选择/证据可单独归因时为可选优化，非规范语义。"""
```

### 1m. 测试命令独家所有权 schema 与 Runtime 解析边界（IF-RUNCONTRACT-001，R6；ARCH-006 §1.0.6.7）

**modules**: executor/test_select.py（resolve_command/audit/JUnit 解析与覆盖校验实现）、executor/validate.py（contract 完整性校验，v6 含占位符计数）、`.tracks/projects/project.toml`（contract 载体；其键扩展由原子实现切片交付——validator schema 激活、合同扩展与 prompt/runtime 变更一个切片，本文档不拥有该文件的并发工作树编辑）——跨模块接口，须有 integration 覆盖。

project.toml 扩展 schema（原子实现切片激活形态；扁平段与现行 project.toml `[integration]`/`[e2e]` 同风格——既有段追加 `run_selected` 成员并新增 `[unit]` 段与 `[nightly]` 段；integration/e2e 同构）：

```toml
[unit]
run = ".venv/bin/python -m pytest tests/unit/ --tb=short -q -n 8 --dist loadscope --junitxml={result}"
run_selected = ".venv/bin/python -m pytest {nodes} --tb=short -q -n 8 --dist loadscope --junitxml={result}"
# worker/dist 并发 flag 与结果写入 flag（--junitxml={result}）作为 Archer 选定 argv
# 内嵌于命令字符串；不设独立 workers/dist 键（避免与命令字符串双权威）。
# v6：每条 run/run_selected 各含 {result} 恰好一次——Runtime 提供的唯一可写
# JUnit XML 路径；run_selected 另含 {nodes} 恰好一次。Runtime 只替换这两个
# 占位符 + cwd/argv0 解析，永不注入 junit flag 或并发参数。
```

- **Archer 独家所有权**：扁平 `[unit]`/`[integration]`/`[e2e]` 段各自的 `run`、`run_selected` 命令字符串全部由 machine contract 定义，worker/dist 并发 flag 内嵌于命令字符串；任何角色不得在 contract 之外指定并发参数。
- **结果占位符计数校验（v6）**：每条 `run` 与 `run_selected` 模板各含 `{result}` **恰好一次**（`run_selected` 另含 `{nodes}` **恰好一次**）——缺失或出现多次 = contract_error fail-closed（loader 加载即拒绝，`trac validate` 非零退出）；Runtime 替换封闭集 = 声明的 `{nodes}`/`{result}` 加 cwd/argv0 解析，**永不注入 `--junitxml` 等 junit flag**（结果写入 flag 只能由 Archer 内嵌），永不注入并发。
- **逐节点结果权威（v6）**：每次测试执行的结果权威是 `{result}` 所指 JUnit XML——testcase 节点身份必须恰好覆盖本次被选集合（全量 run 时 = 本次 collect 的 FULL 全集）；文件缺失/畸形、身份重复、被选节点缺席、多余节点一律 contract_error fail-closed；stdout/stderr 仅为日志，永不作为逐节点分类依据。逐节点合法 Red 从 testcase 的 failure/error 详情推导（pass/skipped/xfailed 遵循既有合法 Red 规则：被选 R2 节点 pass/skip = 非法意外通过）；FULL 链失败台账消费同一份归一化逐节点记录（§3a outcomes blob）。
- **结果生命周期（v6）**：`{result}` 路径位于 Runtime temp/blob staging、按 run/command 唯一；不入树身份（不参与 tree identity/digest 与越权归属）；执行后、temp 清理前归一化持久化为 outcomes blob（事件携 `outcomes_ref` 引用）；WAL replay 发现无已持久化结果时重跑该命令——绝不把缺失结果当作通过。
- **run_selected 缺失 = contract_error fail-closed（schema 切片激活后）**：切片激活后任一层缺 `run_selected` 键时 Runtime 拒绝该层选择执行——**永不向 `run` 追加 nodeid、永不合成任何调用**（无回退参数化路径）；`trac validate` 非零退出。
- **Runtime 解析边界（可执行 API，v5 修订；v6 签名增补）**：Runtime 侧命令构造与审计只经由以下纯函数（executor/test_select.py），全部显式接收被选节点与结果路径并对期望/实际两侧应用**同一 argv0 解析规则**：

```python
def resolve_selected_command(
    template: str,                # contract 层的 run_selected 命令字符串（含 {nodes} 与 {result} 占位符）
    nodes: Sequence[str],         # 本次选择的被选节点（稳定排序后替换；逐节点 shell-quote、空格连接）
    result_path: str | Path,      # Runtime 提供的唯一可写 JUnit XML 路径（temp/blob staging）
    cwd: str | Path,              # 执行目录（相对 repo root 在此解析）
) -> tuple[str, ...]:
    """IF-RUNCONTRACT-001 选择命令解析（v6）：{nodes}/{result} 替换 -> shlex 分词 ->
    argv0 相对 cwd 解析为可执行路径；返回可直接 subprocess 执行的 argv。不注入任何
    contract 之外参数（-n/--dist/-p xdist/worker 数/--junitxml 等）。模板占位符计数
    不合法（{nodes}/{result} 缺失或多次）由 contract loader 前置拒绝。"""

def audit_no_concurrency_injection(
    expected_argv: Sequence[str],  # 期望侧 argv（通常 = resolve_selected_command 的产物）
    actual_argv: Sequence[str],    # 实际执行 argv（command_echo / 执行审计回显）
    cwd: str | Path,
) -> bool:
    """并发/junit 注入审计：对 expected 与 actual 两侧应用同一 argv0 解析规则后逐字比对；
    不等（含任何追加 flag/参数，含 Runtime 注入的 --junitxml/-n/--dist）=> False
    （fail-closed）。两侧必须经同一解析才可比，禁止对期望侧与实际侧使用不同解析路径。"""

def audit(
    template: str, nodes: Sequence[str], result_path: str | Path,
    actual_argv: Sequence[str], cwd: str | Path,
) -> bool:
    """组合便利形态（v6）：audit_no_concurrency_injection(
        resolve_selected_command(template, nodes, result_path, cwd), actual_argv, cwd)。"""

@dataclass(frozen=True)
class JUnitCase:
    nodeid: str                                        # testcase 身份（classname+name 归一化为 node id）
    status: Literal["passed", "failed", "error", "skipped"]   # xfailed/skipped 归一化 skipped
    detail: str | None                                 # failure/error 的 message/text（合法 Red 判定输入）

class JUnitResultError(Exception):
    """{result} 结果文件不可用作逐节点权威：文件缺失/XML 畸形/testcase 身份缺失或重复/
    覆盖不精确（被选缺席∨多余节点）/空 records 对非空选择 => contract_error fail-closed。"""

def parse_junit_result(path: str | Path) -> list[JUnitCase]:
    """IF-RUNCONTRACT-001 逐节点结果解析（纯函数，I/O 仅限读该文件，v6）：JUnit XML ->
    逐 testcase 记录；文件缺失或 XML 畸形或 testcase 无身份 => JUnitResultError。"""

def require_exact_node_coverage(
    cases: Iterable[JUnitCase], selected: Sequence[str],
) -> dict[str, JUnitCase]:
    """覆盖判定（纯函数，v6）：testcase 身份多重集 == selected 多重集（全量 run 时
    selected = collect FULL 全集）=> 通过并返回 node -> record 映射；重复身份、被选缺席、
    多余节点、空 records 对非空选择 => JUnitResultError fail-closed。"""
```

- **审计比对（两侧同一 argv0 解析）**：实际 argv 经 `full.executed.command_echo` 与门禁执行审计事件回显，`audit(template, nodes, result_path, actual_argv, cwd)` 程序化复核——被选节点与结果路径显式参与期望侧展开，期望/实际两侧经同一 argv0 解析后逐字比对，不等 fail-closed（FR-0255-01）；执行后的 `{result}` 文件经 `parse_junit_result` + `require_exact_node_coverage` 校验后才可作为逐节点分类输入（v6）。

### 1n. nightly CI 契约合同（IF-NIGHTLY-001，R6；ARCH-006 §1.0.6.8）

**modules**: executor/validate.py（[nightly] 必填校验）、`.github/workflows/nightly.yml`（随原子实现切片交付）、`.tracks/projects/project.toml` [nightly] 段——契约存在性可审计面（本版 = contract 文件 + validate 校验）。

```toml
[nightly]
schedule = "0 3 * * *"              # cron（UTC），Archer 裁量值
workflow = ".github/workflows/nightly.yml"
job = "nightly-regression"
layers = ["unit", "integration", "e2e"]   # 当前完整 FULL 套件 R1+R2（D-18 周期回归层）
purpose = "current FULL suite (R1+R2) regression; result fetch future; not a local gate"
```

- **存在性审计（本版 = contract 文件 + validate 校验）**：`trac validate` contract 完整性校验追加 `[nightly]` 四键必填（缺失非零退出）；本版不承诺 nightly 派发/回读事件——其可审计面就是 contract 文件与 validate 校验，dispatch/readback 事件随 result fetch future 一并考虑（FR-0255-02）。
- **取回推迟**：nightly 结果取回通道本版明确不实现（spec 范围排除 result fetch future）；nightly 结果不是当前本地门禁——red.validated/GREEN_GATE/FULL 链/M-VERIFY 复用对其零依赖（负断言 AC-FR0255-02）。workflow 文件随原子实现切片交付；CI 侧 job 与本地门禁解耦。

## 2. CLI 接口合同

### 2a. trac hotfix（新命令：入口形态 + AWAIT_HUMAN 子动作，IF-HOTFIX-001；待实现 Devon foundation task——下表「调用」列为合同语法）

| # | 调用 | 输入 | 前置校验 | 成功输出（stdout） | 失败输出 | exit |
|:---|:---|:---|:---|:---|:---|:---|
| 1 | trac hotfix <issue> --scenario post-release\|dev | issue 正整数 + scenario | 工作树干净；writer lock 空闲 | 逐行 run 事件（`run <id> hotfix.requested ...` …）+ 终态行 `run <id>: stage=M-DESIGN ... branch=fix/N scenario=<s>` | 见 #2/#3 | 0 |
| 2 | 同上（PRECHECK 失败） | — | — | `run <id> triage.prechecked REJECTED (<reason>)` + `  next: <人可读下一步>` | stderr 同 reason | 1 |
| 3 | trac hotfix <issue>（缺 --scenario） | — | — | — | stderr：`--scenario is required: post-release (released) or dev (in-development); rerun with explicit --scenario`（询问 Human，不推断、不建 run、无事件） | 2 |
| 4 | trac hotfix anchor AC-FRXXXX-YY@v ... [...] | ≥1 个跨版本 AC 引用 | active run 处于 `awaiting=hotfix_triage` | `anchor recorded: <acs>` + 后续入口完成事件 | 非 awaiting 形态 / 引用校验失败：stderr 原因 | 0 / 1 |
| 5 | trac hotfix feature-route | — | 同 #4 | `feature route recorded; issue <N> -> backlog` | 同 #4 | 0 / 1 |

- 形态 #1 同步驱动整个 triage（PRECHECK → SAGE_TRIAGE dispatch → 校验 → ANCHORED 入口完成），中断于 AWAIT_HUMAN 时打印 `run <id>: awaiting=awaiting_human origin=hotfix-triage issue=<N>`（exit 0，非错误）。
- 形态 #4 走 submit_human_result（ResultCheckpoint 管线）：`human.anchor(mode=manual)` → `validate_anchor` → 通过则 `complete_hotfix_entry`；不实引用 exit 1 且仍停留 AWAIT_HUMAN（可重试）。
- 形态 #5：`human.anchor(mode=feature_route)` → `backlog.recorded {issue, decision: feature_route}` → `run.completed(feature_route)`；不建 fix 分支、无 dangling worktree。
- `--scenario` 缺省提示（#3）是非阻塞询问：不建 run、不写事件，操作者显式重跑。

### 2b. `trac status`（扩展输出，IF-HOTFIX-006 / NFR-0110）

既有输出不变。v0.6 扩展：

- active run 为 hotfix run 时状态行追加 `branch=fix/{N} scenario={post-release|dev} issue={N}`（入口子状态机活跃期间为 `stage=M-HOTFIX-TRIAGE substate=PRECHECK|SAGE_TRIAGE` 或 `awaiting=awaiting_human origin=hotfix-triage issue=<N>`）。
- 存在其余非 completed run 时追加行 `suspended: run=<id> stage=<stage> substate=<sub> branch=<branch>`（每个挂起 run 一行；投影可重建）。
- completed hotfix run：`run=<id>: completed terminal=<boundary|rejected|feature_route|ac_gap|spec_gap> branch=fix/<N> scenario=<s>`；boundary 不报告「已发布」。

### 2c. `trac approve`（扩展 awaiting 形态，IF-HOTFIX-009 / FR-0248）

既有 M-REQ-APPROVAL 行为不变。扩展：active run 处于 hotfix DIAGNOSE 产生的 `awaiting=escalation` 且 `last_failure.check ∈ {ac_gap, spec_gap}` 时，`trac approve [--actor NAME]` 记录 `human.approval`（AC 要求的 Human 决定证据）→ `backlog.recorded {decision: ac_gap|spec_gap, issue}` → `run.completed(terminal_state=ac_gap|spec_gap)`。其他 awaiting 形态拒绝（exit 1 + stderr 原因）。

### 2d. `trac check trace`（跨版本解析扩展，IF-HOTFIX-007 / IF-TRACE-001/002 扩展）

- 既有扫描/闭合语义不变；带 `@<version>` 的标记解析到所指版本 acceptance.md 做存在性校验（引用不实 = 硬错误）。
- hotfix 计划级闭合（§1f）：仅当事件流含本 run 的 `increment.declared` 时，锚定 AC 集合的闭合可由 delta §8 声明的 unit 行满足；否则闭合仅认文件标记。`--json` 输出追加可选字段 `hotfix_scope: {"run_id", "anchor_acs", "declared_unit_rows"}`（非 hotfix run 无此字段）。

### 2e. E-01/E-02 交互合同（hotfix 入口与观察）

| # | surface/context | 可观察状态 | 用户动作与可用条件 | 结果与继续/返回 |
|:---|:---|:---|:---|:---|
| 1 | 宿主 repo 工作区，trac hotfix <issue> --scenario ... | 逐行 run 事件（hotfix.requested → triage.prechecked → anchor.validated → stage.entered(M-DESIGN)） | 工作树干净；issue 为宿主 repo bug | ANCHORED 后可 `trac run` 续跑（FR-0242）；REJECTED 见 #2 |
| 2 | REJECTED 后终端 | `triage.prechecked REJECTED (<reason>)` + `next:` 行 + exit 1 | — | 无活跃 hotfix run、无 fix 分支；补全 issue 后重试（新 run）或改走 `trac start` |
| 3 | AWAIT_HUMAN，`trac status` | `awaiting=awaiting_human origin=hotfix-triage issue=<N>` | trac hotfix anchor <AC@ver ...>（人工锚定）或 trac hotfix feature-route（确认转 feature） | anchor → 校验 → M-DESIGN 继续；feature-route → backlog、无分支 |
| 4 | hotfix run 活跃期，`trac status` | `run=<id> stage=<...> branch=fix/<N> scenario=<s>` + `suspended:` 行 | 只读 | 操作者可分辨 `trac run` 续跑哪个 run；挂起 run 可 `trac replay/report` |
| 5 | boundary 后 | `terminal=boundary branch=fix/N scenario=<s>`；工作树已切回恢复 active 的 run 分支 | `trac run` 续跑 feature run | fix 分支保留修复结果，「修复完成、待发布」 |
| 6 | 并发第二个 trac 命令 | stderr `runtime lock held by pid <N>` | 任何时刻 | exit 1，不推进派发；持锁命令结束后可重试 |

### 2f. design-trace 扫描范围：inline-discussion 排除（R3 增补；IF-VALIDATE-001 v0.6 扩展）

**modules**: executor/test_tasks.py（可见行提取与归属判定）、executor/validate.py（`trac validate --file test-plan.md` 消费侧）——跨模块接口，须有 integration 覆盖（经 validate CLI 出口，AC-FR0244-02 既有行的 IF-VALIDATE-001 归属已覆盖该出口；新排除行为的细粒度用例为 Devon unit 义务，test-plan §10）。

- **合同**：`trac validate --file test-plan.md` 与 M-DESIGN EXIT 的 design-trace 检查（`check_design_trace`，BS-06/FR-0140）在提取「可见行」时，除既有 HTML 注释与 fenced code 剔除外，**同时剔除 inline-discussion blockquote 行**（以 `>` 开头的行）：讨论线程是评审期旁路（模板指引与 tracks-discuz 协议的既定语义），不是计划内容；其正文中的 AC id + tests 路径字样既不构成 layer 归属声明，也不构成 IF- 归属声明，不触发「integration/e2e AC missing IF- attribution」与「has no layer attribution」的判定。
- **fail-closed 保持**：layer 与 IF- 归属的判定面收窄为非 blockquote 可见行（§8 表格行为权威来源）；AC 的归属声明写进 blockquote 不产生任何计数效果（「has no layer attribution」照常失败）。`required_ac_ids` 的 layer 判定共享同一可见行提取，同步收窄（blockquote 内的 layer 词不再把 AC 判为 required）——§8 表格行仍是 D-28 唯一机器可读 AC 覆盖来源，D-28 语义不变。
- **`trac check trace` 不受影响**：其 AC↔测试标记闭合按 §8 表格行解析（row-based），不含逐行归属扫描；IF-TRACE-001/002 合同不变。
- **生效时点**：合同随本 revision 冻结。代码实现状态（工作树 vs HEAD、T-012 标准 RGR 归档、运营端带外应用）是 Devon/Runtime 的归档职责，非设计合同事项；设计文档不承载代码级工作树快照跟踪（R3/R4 的实测记录已随工作树变化多次失真，R5 撤销该跟踪）。当前事实（截至本 revision）：工作树 `_visible_plan_lines` 已反映本合同（剔除 `>` 开头行），`trac validate --file test-plan.md` 为 valid；若工作树代码偏离 §2f，门禁以 validate 失败自暴露（实现回归，非设计缺口），按合同由 Devon/Runtime 修复。M-IMPL 重规划按 ARCH-006 §1.0.5 batch B/C 的 verification-only 吸收该 scope 文件的正式归档。

## 3. 文件 / 存储契约

### 3a. 路径契约

| 路径 | 格式 | 写入者 | 读取者 |
|:---|:---|:---|:---|
| `tracks/kernel/hotfix.py` | Python 模块（接口桩 → Devon 实现） | Devon（实现） | kernel/machine.py、tests |
| `tracks/executor/hotfix.py` | Python 模块（接口桩 → Devon 实现） | Devon（实现） | executor/executor.py、cli/main.py、tests |
| `.tracks/projects/{ver}-hotfix-{issue}/` | 目录（hotfix run 项目目录；delta 三文档、tasks.json/tasks.md 落此） | Runtime（cmd_hotfix 创建）/ Archer（delta 文档）/ Devon（tasks.json 投影） | executor、validate、report |
| `.tracks/runtime/host-issues.json` | JSON（fake 通道宿主 issue 种子，§3b） | 测试/操作者（fake 语料准备） | FakeIssueBackend.fetch_issue |
| `fix/{issue}` | git 分支（base=main 或活跃 release 分支） | Runtime（complete_hotfix_entry，唯一 branch authority） | 测试（git branch/log 断言）、M-IMPL RGR |
| `refs/trac/rgr/{run}/{task}/{attempt}/red` | git ref（不变，落在 fix/{issue} 旅程） | executor（不变） | 不变 |
| `tracks/executor/test_select.py` | Python 模块（R6：分类/选择/身份/台账/命令审计纯函数，接口桩 → Devon 实现） | Devon（实现） | kernel/m_test.py、kernel/m_impl.py、executor/m_impl_runtime.py、tests |
| `.tracks/runtime/blobs/selection/{run}/{seq}-nodes.json` | JSON（逐节点 `{node, layer, class: r1\|r2\|removed}` 表 / 选择节点表；test.collected.per_node_blob 与 test.selected.nodes_blob 引用） | executor（COLLECT/门禁 handler） | report、replay 审计、tests |
| `.tracks/runtime/blobs/baseline/{run}/{seq}-node-digests.json` | JSON（R1 快照逐节点 digest 表 `[{node, layer, digest}]`，三层全集；test.baseline_captured.node_digest_blob 引用，§1j/§1a v5） | executor（M-TEST 入口 capture） | COLLECT 分类、replay 审计、tests |
| `.tracks/runtime/blobs/results/{run}/{seq}-outcomes.json` | JSON（v6：归一化逐节点结果表 `[{node, status: passed\|failed\|error\|skipped, detail_ref?}]`——`{result}` JUnit XML 经 parse_junit_result+require_exact_node_coverage 校验后的持久形态；red.validated/green/full.executed 的 outcomes_ref 引用；FULL 链台账消费同一记录） | executor（门禁 handler，temp 清理前落盘） | report、replay 审计、FULL 链台账、tests |
| Runtime temp staging `{result}` 路径 | JUnit XML（按 run/command 唯一的可写路径；执行后归一化至上一行 blob 即清理；不入树身份、不入 git） | Runtime（subprocess 执行时替换 {result}） | 门禁 handler（执行后立即解析校验） |

### 3b. host-issues.json schema（fake 通道，IF-HOTFIX-003）

```json
{
  "42": {"title": "crash on ...", "body": "### 版本\nv0.5\n### 对应 FR/NFR\nFR-0030\n<症状正文>",
         "labels": ["bug"]},
  "99": {"title": "feature request ...", "body": "...", "labels": ["enhancement"]}
}
```

- 顶层键 = issue 号（十进制字符串）；值含 title/body/labels；`is_bug` ⟺ labels 含 `"bug"`。
- 仅 fake issue backend 读取（真实通道走 REST）；文件缺失 = 空集（所有 fetch → not_found）。不入 git（.tracks/runtime/ 已 gitignore）。

### 3c. dispatch 物化与 deliverables

- §1h 物化字段经既有 `command.issued` 事件可审计；hotfix 相关 dispatch（sage:SAGE_TRIAGE、archer:M-DESIGN、devon:*）payload 完整性沿用 FR-0190 校验。
- `tracks/agents/*.md` deliverables 集不变（无新 agent）；Sage 既有 agent 定义承接 SAGE_TRIAGE substate（无 deliverable 变更）。

## 4. 可观察出口（测试断言基础）

test-plan 的断言只能落在以下外部可观察出口（§6.5 闭环）。

### 4a. 事件出口（events 表）

| 事件 | 可观察字段 | 关联 AC |
|:---|:---|:---|
| `hotfix.requested` | issue, scenario | AC-FR0240-01 |
| `triage.prechecked` | issue, issue_type, scenario, status, reason, next, target_version, active_branch | AC-FR0240-02/03, AC-NFR0100-01 |
| `anchor.validated` | acs, source, attempt, rationale_refs | AC-FR0240-04, AC-NFR0100-02 |
| `verdict.failed(anchor_invalid)` | check, attempt（重派计数） | AC-FR0240-05, AC-NFR0100-02 |
| `human.anchor` | mode, acs, issue, actor | AC-FR0240-06 |
| `backlog.recorded` | issue, decision | AC-FR0241-04, AC-FR0248-01 |
| `branch.created`（fix/{issue}） | branch_name, base, commit_sha | AC-FR0241-01 |
| `baseline.inherited` | target_version, baseline_digest, anchor_acs, baseline_doc_paths | AC-FR0241-02 || `stage.entered`（M-DESIGN / M-HOTFIX-TRIAGE） | stage | AC-FR0241-03, AC-FR0240-01 |
| `stage.rolled_back`（anchor_overturned） | from_stage, to_stage, reason | AC-FR0243-03 |
| `prism.verdict`（anchor_verdict） | verdict, anchor_verdict | AC-FR0243-02/03 |
| `increment.declared` | shield, unit_rows, trace_status | AC-FR0244-04 |
| `test.written` / `test.committed`（缺席证据） | 空（空 Shield 增量时无此二事件） | AC-FR0244-04 |
| `red.validated`（RED-first） | findings（回归用例先 RED） | AC-FR0244-01 |
| `baseline.frozen`（scenario_branch_head / stale） | status, scenario_branch_head | AC-FR0245-02 |
| `green.committed` / `red.checkpointed`（fix 分支） | g_sha/r_sha/ref/trailers（Tracks-Issue=hotfix issue） | AC-FR0245-01 |
| `stage.exited(M-IMPL)` + `run.completed(boundary)` | terminal_state, restored_active_run, restored_branch | AC-FR0246-01/02, AC-FR0242-02 |
| `run.completed(rejected|feature_route|ac_gap|spec_gap)` | terminal_state | AC-FR0240-03, AC-FR0241-04, AC-FR0248-01 |
| `human.approval`（ac_gap/spec_gap 前置） | actor | AC-FR0248-01 |
| `command.issued`（sage:SAGE_TRIAGE / hotfix M-DESIGN） | params.assignment 的 anchor 语料字段（§1h） | AC-FR0240-04, AC-FR0243-01 |
| `test.baseline_captured`（R6 v5） | status, baseline_id, baseline_tree, layers(unit+int+e2e), nodes_count, empty_baseline, node_digest_blob（seq 先于本 run 首个 Shield WRITE 派发；test.selected.baseline == baseline_id 可复核） | AC-FR0250-01 |
| `test.selected` | scope, basis, nodes_count, nodes_blob, baseline, commit, selection_id, task_id/task_ifs | AC-FR0250-02, AC-FR0251-01/02, AC-FR0253-03, AC-NFR0130-01 |
| `test.collected`（R6 扩展） | inherited_r1, delta_r2, removed, failures, per_node_blob | AC-FR0250-01 |
| `red.validated`（R6 扩展） | selection_id, nodes_blob（⊆R2/T-DELTA 可复核）, outcomes_ref（v6：JUnit 解析+精确覆盖后的归一化逐节点结果 blob） | AC-FR0250-02, AC-FR0255-01 |
| `full.executed` | round, suite, passed, failed_nodes, command_echo, evidence_ids, serves_as_full_f, outcomes_ref（v6） | AC-FR0253-01/04/05, AC-FR0254-02, AC-FR0255-01 |
| `ledger.opened` / `ledger.transitioned` | node, state/from/to, selection_id, evidence_id, attempt, actor, reason | AC-FR0253-01/02, AC-NFR0120-01/02 |
| `evidence.reused` | kind(green\|full_f), reused_evidence_ids, identity_basis, consumer_gate | AC-FR0252-01, AC-FR0254-01, AC-NFR0130-02 |
| `evidence.staled` | targets(kind, ref), reason | AC-FR0251-02, AC-NFR0130-02 |
| `green.committed`（R6 扩展） | evidence_ids（REFACTOR 复用定位锚） | AC-FR0252-01 |

### 4b. CLI 出口

| 命令 | 可观察输出 | 关联 AC |
|:---|:---|:---|
| trac hotfix ...（五种形态，待实现） | §2a 精确 stdout/stderr/exit | AC-FR0240-01/02/03/06, AC-FR0241-04 |
| `trac status` | §2b 状态行（branch/scenario/issue）+ `suspended:` 行 + terminal 行 | AC-FR0240-01/06, AC-FR0242-01/02/03, AC-NFR0110-01/02/03, AC-FR0246-01/02 |
| `trac approve`（hotfix awaiting 形态） | human.approval 落事件 + 退出路由输出 | AC-FR0248-01 |
| `trac validate --file test-plan.md`（hotfix 目录） | 层归属/跨版本引用校验结果 + exit | AC-FR0244-02 |
| `trac check trace [--json]` | 跨版本闭合 + hotfix_scope 字段 | AC-FR0244-03/04 |
| `trac replay <run>` / `trac report --run-id <run> --format md` | hotfix 全旅程序列 + 锚定/分支/boundary 证据 | AC-FR0240-07, AC-FR0246-03 |
| `trac replay` / `trac report`（R6 扩展） | selection/evidence/ledger/FULL 链身份链（test.selected/full.executed/ledger.*/evidence.* 全 payload） | AC-FR0253-02, AC-NFR0120-01, AC-NFR0130-01 |
| `trac validate`（R6 contract 完整性扩展） | 缺 [nightly] 必填键、任一层 run_selected 键或占位符计数不合法（{result}/{nodes} 缺失或多次，v6）时非零退出（contract_error fail-closed；无合成调用回退） | AC-FR0255-02 |
| `trac status`（R6 FULL 链行） | FULL 链进行中追加 `full_chain: round=<r> ledger=open:N proven:M`；boundary 后无该行 | AC-FR0253-05, AC-FR0254-01 |

### 4c. 文件 / Git 出口

| 路径 | 可观察内容 | 关联 AC |
|:---|:---|:---|
| `.tracks/projects/{ver}-hotfix-{issue}/` | delta 三文档 + tasks.json 存在；story/spec/acceptance 不存在 | AC-FR0243-01, AC-FR0241-02 |
| `.tracks/runtime/host-issues.json` | §3b schema（fake 语料） | AC-FR0240-02/03 |
| `git branch --list fix/{N}` | REJECTED/FEATURE_ROUTE 后不存在；ANCHORED 后存在且 base 正确 | AC-FR0240-03, AC-FR0241-01, AC-NFR0100-03 |
| `git log --oneline fix/{N} ^main`（场景 A） | 创建后为空（base=main HEAD） | AC-FR0241-01 |
| `git log fix/{N}`（M-IMPL 后） | 含修复提交（R/G/refactor + trailers Tracks-Issue） | AC-FR0245-01 |
| 活跃分支 / feature 工作区 | 不含 fix/{N} 的修复提交 | AC-FR0245-01 |
| `.tracks/runtime/tracks.db` events 表 | hotfix 事件 append-only；drop 投影后重建一致 | AC-FR0240-07, AC-NFR0100-04 |

## 5. IF Registry

每个 IF- 标识代表一个可独立实现的接口合同，是 test-plan §8「IF- 归属」列的唯一合法取值来源。v0.4/v0.5 已建立的 20 个标识不可变、不可复用；此处列出 cross-reference 条目使 validator 可解析（定义仍以 IF-004/IF-005 §5 为准），并增补 10 个 hotfix 标识与 R6 的 7 个 D-41 选择语义标识。

### IF-MTEST-001 M-TEST 测试收集合同（承自 IF-004 §5，定义不变）

### IF-MTEST-002 M-TEST 测试执行合同（承自 IF-004 §5，定义不变；hotfix RED-first 复用）

### IF-SHIELD-001 Shield 测试编写合同（承自 IF-004 §5，定义不变）

### IF-TRACE-001 trace 需求追踪合同（承自 IF-004 §5；v0.6 扩展 @version 跨版本解析）

### IF-TRACE-002 trace 闭合检查合同（承自 IF-004 §5；v0.6 扩展 hotfix 计划级闭合，见 IF-HOTFIX-007）

### IF-REACH-001 reach 模块可达性合同（承自 IF-004 §5，定义不变）

### IF-REACH-002 reach 孤岛检查合同（承自 IF-004 §5，定义不变）

### IF-VALIDATE-001 trac validate 文档校验合同（承自 IF-004 §5；v0.6 扩展 hotfix delta plan 层归属校验 + §2f design-trace 扫描范围排除 inline-discussion blockquote 行 + §1i hotfix 版本目录继承基线文档只读解析）

### IF-IMPL-001 M-IMPL kernel 状态机合同（承自 IF-005 §5，定义不变；hotfix run 复用）

### IF-IMPL-002 M-IMPL executor handler 合同（承自 IF-005 §5，定义不变；hotfix run 复用）

### IF-IMPL-003 task graph 解析与校验合同（承自 IF-005 §5，定义不变）

### IF-IMPL-004 RGR git 操作合同（承自 IF-005 §5，定义不变）

### IF-IMPL-005 质量门禁分层执行合同（承自 IF-005 §5，定义不变）

### IF-IMPL-006 三 worktree 方案合同（承自 IF-005 §5，定义不变）

### IF-IMPL-007 tasks.json/tasks.md 真相源合同（承自 IF-005 §5，定义不变）

### IF-DEVON-001 Devon agent 接入与 manifest 越界审计合同（承自 IF-005 §5，定义不变）

### IF-LIVE-001 真实 OpencodeBackend 旅程与 evidence 绑定合同（承自 IF-005 §5，定义不变）

### IF-RELEASE-001 当前候选 release-evidence 检查合同（承自 IF-005 §5，定义不变）

### IF-DOCGAP-001 outcome 文档评论优先检查与裁定合同（承自 IF-005 §5，定义不变）

### IF-QUARANTINE-001 outcome 授权变化隔离与新 attempt 恢复合同（承自 IF-005 §5，定义不变）

### IF-HOTFIX-001 hotfix CLI 入口合同

- **合同**：§2a trac hotfix 五形态（入口 / REJECTED / 缺 scenario 询问 / anchor / feature-route）的输入、前置、精确输出与 exit code；入口形态同步驱动 triage 并打印逐行事件；AWAIT_HUMAN 中断为非错误。
- **实现模块**：cli/main.py。
- **对应 §section**：§2a, §2e。
- **关联 FR**：FR-0240-01/02/03/06, FR-0241-04。

### IF-HOTFIX-002 HOTFIX-TRIAGE kernel 子状态机合同

- **合同**：§1a 事件 reducer + §1b Command 产出 + §1c State 字段 + `_decide_hotfix_triage` 控制流（PRECHECK/SAGE_TRIAGE/AWAIT_HUMAN 封闭集 + 三终态转移）+ 重派预算（≤3）+ anchor 推翻回滚路由（M-DESIGN → M-HOTFIX-TRIAGE）+ append-only/投影重建。
- **实现模块**：kernel/hotfix.py, kernel/machine.py（接线）, kernel/events.py。
- **对应 §section**：§1a, §1b, §1c。
- **关联 FR**：FR-0240-01/04/05/06/07, FR-0241-04, FR-0243-03, NFR-0100-02/03/04。

### IF-HOTFIX-003 PRECHECK 程序预检合同

- **合同**：§1d `HostIssue`/`PrecheckReport`/`precheck_hotfix`/`parse_issue_hints` + effects/github.py `fetch_issue`（fake 种子/真实 REST）+ REJECTED 原因封闭集 + `bug` label 约定 + 确定性（无 LLM 派发记录）。
- **实现模块**：executor/hotfix.py, effects/github.py, executor/executor.py（`_do_precheck_hotfix`）。
- **对应 §section**：§1c（原因集）, §1d, §3b。
- **关联 FR**：FR-0240-01/02/03, NFR-0100-01。

### IF-HOTFIX-004 Sage 锚定与程序校验合同

- **合同**：SAGE_TRIAGE 派发物化（anchor 语料字段 §1h）+ `parse_anchor_refs`/`validate_anchor_refs` 跨版本存在性校验 + `anchor.validated`/`verdict.failed(anchor_invalid)` + 重派 ≤3 + NO_ANCHOR → AWAIT_HUMAN（不自动转 feature）+ fake `sage:SAGE_TRIAGE` token 集。
- **实现模块**：executor/hotfix.py, executor/executor.py（dispatch/validate handler）, effects/fake.py。
- **对应 §section**：§1a, §1d, §1e, §1h。
- **关联 FR**：FR-0240-04/05, NFR-0100-02。

### IF-HOTFIX-005 fix/{issue} 分支、基线继承与 hotfix 项目目录合同

- **合同**：`complete_hotfix_entry` 原子入口完成（create_branch base 按场景 + checkout + `baseline.inherited` source approval 记录 + `stage.entered(M-DESIGN)`）+ hotfix 版本身份 `{ver}-hotfix-{issue}` 与能力继承（capabilities 后缀解析）+ 不创建需求阶段产物。
- **实现模块**：executor/hotfix.py, executor/executor.py, capabilities.py, kernel/machine.py。
- **对应 §section**：§1a（baseline.inherited）, §1d, §3a。
- **关联 FR**：FR-0241-01/02/03, FR-0243-01, NFR-0100-03。

### IF-HOTFIX-006 run 并存、单一 active 与恢复合同

- **合同**：hotfix run 建立即 active（active_run 语义不变）+ 挂起 run 投影/suspended 观察 + boundary 分支恢复 checkout（restored_active_run/restored_branch）+ 单写者锁并发拒绝（复用，报持有者 PID）+ 崩溃恢复幂等 reconcile + `trac status` §2b 输出。
- **实现模块**：cli/main.py, executor/executor.py, store/store.py（投影，schema 不变）, kernel/hotfix.py（reconcile 标记）。
- **对应 §section**：§2b, §2e。
- **关联 FR**：FR-0242-01/02/03/04, FR-0246-01/03, NFR-0110-01/02/03, NFR-0100-04。

### IF-HOTFIX-007 M-TEST hotfix 变体合同（RED-first、跨版本 trace、空 Shield 增量放行）

- **合同**：回归用例 RED-first（复用 RED_CHECK，unexpected_pass 非法）+ delta §8 层归属扩展（unit 仅 hotfix 合法）+ 跨版本 AC 引用闭合（`trac check trace`/`trac validate` @version 解析）+ 空 Shield 增量放行（`increment.declared` + 计划级闭合 + M-IMPL 物化闭环 `trace_mismatch`）。
- **实现模块**：kernel/m_test.py, executor/executor.py, executor/validate.py, executor/test_tasks.py, checks/trace.py, effects/fake_shield.py。
- **对应 §section**：§1e, §1f, §2d。
- **关联 FR**：FR-0244-01/02/03/04。

### IF-HOTFIX-008 M-IMPL hotfix 变体合同（隔离分支与场景 B stale reconcile）

- **合同**：hotfix M-IMPL 复用 IF-IMPL-001~007；场景 B BASELINE digest 追加活跃分支 HEAD（stale → NEEDS_ATTENTION 冲突路径）+ fix/{issue} 隔离（提交落 fix 分支、活跃分支/feature 工作区无）+ boundary 终态 + 无发布事件 + 分支恢复。
- **实现模块**：executor/m_impl_runtime.py, executor/executor.py, kernel/m_impl.py。
- **对应 §section**：§1g。
- **关联 FR**：FR-0245-01/02, FR-0246-01/02/03, FR-0242-02。

### IF-HOTFIX-009 缺口与退出路由合同

- **合同**：anchor 推翻回 SAGE_TRIAGE（不消费 M-DESIGN 预算）+ stub_gap 回 hotfix 自己的 M-DESIGN（无 Human 技术门）+ ac_gap/spec_gap → awaiting_human → `trac approve` 扩展（human.approval 前置）→ backlog + 终态退出 + fake `prism:PRISM_REVIEW=anchor_overturned` token。
- **实现模块**：kernel/machine.py, kernel/m_impl.py, cli/main.py, executor/executor.py, effects/fake.py。
- **对应 §section**：§1a（扩展 payload）, §2c。
- **关联 FR**：FR-0243-02/03, FR-0247-01/02, FR-0248-01, FR-0241-04。

### IF-HOTFIX-010 hotfix 版本目录继承基线文档只读解析合同

- **合同**：§1i 共享纯函数 `resolve_inherited_baseline_docs(doc_path) -> tuple[Path, Path]`——hotfix 版本目录（`parent.name` 匹配 `v\d+\.\d+-hotfix-\d+`）从目录名解析 target_version、只读返回目标版本目录的 acceptance.md/interfaces.md（不复制，FR-0241-02）；feature/非版本目录返回同目录路径（逐字节不变，dir-name 身份门控）。`check_design_trace_file` / `check_test_tasks_contract_file` 经该 resolver 定位基线文档；目标缺失 fail-closed。`checks/trace.py` hotfix 计划级闭合的 `projects_dir / version` 查找锚定同一目标版本目录（M-DESIGN EXIT 与 M-IMPL TASK_REVIEW/ISLAND 同一解析）。
- **实现模块**：executor/test_tasks.py, executor/validate.py（消费侧）, checks/trace.py（一致锚定）。
- **对应 §section**：§1i, §4a（baseline.inherited 权威记录）。
- **关联 FR**：FR-0243-01, FR-0244-02, FR-0241-02。

### IF-SELECT-001 测试节点分类合同（R6；v5 增补 R1 快照机制）

- **合同**：§1j `classify_nodes` 确定性分类（逐节点 digest，禁止整文件 digest）——R2/T-DELTA（新增 ∨ 同一 nodeid 节点源/体 digest 变化，恰好且仅为这两类）/ R1/T-HIST（baseline 节点 ∧ 节点 digest 一致；同文件未变兄弟节点保持 R1；变更 support/fixture 不参与分类但仍参与全量 collect/import，未变历史行为的回归等待 M-IMPL FULL 链与 nightly CI）/ REMOVED（baseline 有当前无 = fail-closed 测试资产删除：路由 test contract defect、阻断 M-TEST 退出，不静默注销）；分类输入 = **本 run pre-WRITE 持久化的 `test.baseline_captured` stamped 快照**（`TestBaselineSnapshot`/`capture_test_baseline`：M-TEST 入口、首个 Shield WRITE 派发前对继承树做 unit+integration+e2e 全量 collect + 逐节点 digest + baseline/tree identity；首次项目可合法空集；缺失 capture = `MissingBaselineCaptureError` fail-closed，capture 失败 = 上游/设计/合同 fail-closed 路由，WAL/replay 复用同一 stamped capture）+ 当前三层 inventory，同输入复跑一致；结果经 test.collected payload（r1/r2/removed 计数 + per_node_blob）落事件可审计。
- **实现模块**：executor/test_select.py, kernel/m_test.py（COLLECT 消费）。
- **对应 §section**：§1j, §1a（test.baseline_captured）, §3a（per_node_blob / node-digests blob）, §4a。
- **关联 FR**：FR-0250-01。

### IF-SELECT-002 选择语义与 selection identity 合同（R6）

- **合同**：§1j 四种选择封闭清单——SELECT_R2（scope=r2_delta，全部 delta 节点；feature M-TEST 空选择集 fail-closed，hotfix unit-only 显式增量旁路保留）、SELECT_TASK（scope=task_if，targeted unit 来自 task 不可变 R commit 的 Runtime 捕获 RED artifact manifest 与 GREEN-touched unit 文件 ∪ task IF 归属命中的 integration，不含 e2e/R1 全量）、SELECT_DIFF（scope=select_diff，单条 FIXED 条目的确定性差分受影响集，每条目 FIXED 后立即执行；不可靠回退 FULL）、FULL（scope=full，三层全量）；每次选择以 test.selected 落事件携带 selection_id = sha256(canonical_json({scope,basis,nodes,baseline,commit,tree_stamp}))（tree_stamp = 确定性 dirty-aware 工作树内容 stamp，与 commit 分离——同节点集合同 HEAD 不同未提交内容不得共享 selection identity）；上游变化经 evidence.staled 置 stale 且不复用为通过依据。
- **实现模块**：executor/test_select.py, kernel/m_test.py, executor/m_impl_runtime.py。
- **对应 §section**：§1j, §1k, §4a。
- **关联 FR**：FR-0250-02, FR-0251-01/02, FR-0253-03, NFR-0130-01。

### IF-EVIDENCE-001 证据四元组与复用/stale 判定合同（R6）

- **合同**：§1k evidence_identity 四元组（tree/command/env/selection_id）+ node/result/attempt/actor；`reuse_allowed` 为全门禁共用唯一实现（四元组全一致 ∧ 无 STALE 才可复用）；evidence.reused（kind=green|full_f）与 evidence.staled 落事件 append-only；REFACTOR 复用 Green（FR-0252）与 M-VERIFY 复用 FULL_F（FR-0254，注册后生效）消费同一判据。
- **实现模块**：executor/test_select.py, executor/m_impl_runtime.py, kernel/m_test.py, kernel/m_impl.py。
- **对应 §section**：§1k, §4a。
- **关联 FR**：FR-0250-03, FR-0252-01/02/03, FR-0254-01, NFR-0130-01/02。

### IF-LEDGER-001 失败台账事件溯源合同（R6）

- **合同**：§1l 台账身份 = `(node, failure_signature)`（同签名重启先前身份、新签名新建身份）；状态机封闭转移集 OPEN→CLASSIFIED→FIXED→PROVEN、OPEN|CLASSIFIED|FIXED→STALE、STALE→OPEN、FIXED→OPEN（证明失败：SELECT_DIFF/fallback 重跑同签名失败，重开重分类/重修）、PROVEN→OPEN（reopen：FULL_F 重现已 PROVEN 同一签名；未列出非法 fail-closed）；ledger.opened/transitioned 先落事件再判定（WAL）；rebuild_ledger 回放重建（含全部闭合转移）与中断前一致、FULL 链精确续跑；未知/缺失/STALE 一律不视为干净（不跳过 FULL_F、不产出 stage.exited(M-IMPL)，触发原因落事件）。
- **实现模块**：executor/test_select.py, executor/m_impl_runtime.py, kernel/m_impl.py。
- **对应 §section**：§1l, §4a。
- **关联 FR**：FR-0253-01/02, NFR-0120-01/02。

### IF-FULLCHAIN-001 FULL 链收敛合同（R6）

- **合同**：§1l run_full_chain 无界收敛循环——FULL_1 → 失败逐条 ledger.opened → **逐条目**修复-证明（每条 FIXED 后立即其确定性 SELECT_DIFF：通过 FIXED→PROVEN、同签名失败 FIXED→OPEN 重分类/重修）或 fallback_full 逐条目落转移（通过 FIXED→PROVEN、同签名失败 FIXED→OPEN，clean 充当 FULL_F）→ 全部 PROVEN 且无 STALE 后 FULL_F 干净出口；新失败追加台账任意多轮循环，无 loop-count cap、无超限放弃，仅 per-agent attempt 预算照旧；serves_as_full_f 标注 fallback 充当与干净首轮充当；多条目 union/batching 仅在每条目选择/证据可单独归因时为可选优化（非规范语义）；FULL 本地执行仅发生在 ISLAND_GATE_2（AC-FR0254-02 负断言）。
- **实现模块**：executor/m_impl_runtime.py, executor/test_select.py, kernel/m_impl.py（出口门禁）。
- **对应 §section**：§1l, §4a。
- **关联 FR**：FR-0253-01/04/05, FR-0254-02。

### IF-RUNCONTRACT-001 测试命令独家所有权与 Runtime 解析合同（R6；v6 增补结果通道）

- **合同**：§1m project.toml 扩展 schema——扁平 `[unit]`/`[integration]`/`[e2e]` 段（与现行 project.toml 同风格）各自声明 run/run_selected 命令字符串，worker/dist 并发 flag 与结果写入 flag（`--junitxml={result}`）作为 Archer 选定 argv 内嵌于命令字符串（无独立 workers/dist 键）；每条 run/run_selected 各含 `{result}` 恰好一次（run_selected 另含 `{nodes}` 恰好一次），缺失或重复 = contract_error fail-closed（v6）；schema 切片激活后 run_selected 缺失 = contract_error fail-closed（Runtime 永不向 run 追加 nodeid、永不合成调用，无回退参数化路径）；Runtime 命令构造与审计仅经可执行纯函数（v5 API、v6 签名）——`resolve_selected_command(template, nodes, result_path, cwd) -> argv`（显式接收被选节点与结果路径，{nodes}/{result} 替换 + argv0 相对 cwd 解析）、`audit_no_concurrency_injection(expected_argv, actual_argv, cwd) -> bool`（期望/实际两侧应用**同一 argv0 解析规则**后逐字比对，不等 fail-closed）与组合形态 `audit(template, nodes, result_path, actual_argv, cwd)`；永不注入 -n/--dist/worker 并发、永不注入 --junitxml 等 junit flag；实际 argv 经 command_echo 审计事件回显供程序化复核；执行结果的权威是 `{result}` JUnit XML——testcase 身份恰好覆盖被选集（全量 run = collect FULL 全集），缺失/畸形/重复/覆盖不精确 = contract_error fail-closed（parse_junit_result + require_exact_node_coverage 纯函数），stdout/stderr 仅为日志；结果路径 temp/blob staging 按 run/command 唯一、不入树身份，temp 清理前归一化为 outcomes blob（事件携 outcomes_ref），WAL replay 无持久化结果即重跑。
- **实现模块**：executor/test_select.py, executor/validate.py, executor/m_impl_runtime.py（执行接线）。
- **对应 §section**：§1m, §3a, §4a（full.executed.command_echo / outcomes_ref）。
- **关联 FR**：FR-0255-01, FR-0250-03。

### IF-NIGHTLY-001 nightly CI 契约合同（R6）

- **合同**：§1n project.toml [nightly] 段（schedule/workflow/job/layers/purpose）声明当前完整 FULL 套件（R1+R2 全部节点，unit+integration+e2e）的周期回归 job——历史回归责任强调 R1/T-HIST，但 nightly 运行的是当前 FULL 而非仅 R1 子集；trac validate contract 完整性校验追加必填键（缺失非零，schema 切片激活后生效）；本版可审计面 = contract 文件 + validate 校验（不承诺 nightly 派发/回读事件）；nightly 结果取回通道本版不实现（result fetch future），本地门禁对其零依赖（负断言）。
- **实现模块**：executor/validate.py（校验侧），.github/workflows/nightly.yml（随原子实现切片交付）。
- **对应 §section**：§1n, §7（CI Gate 注记）。
- **关联 FR**：FR-0255-02, FR-0250-02。

**有效性校验机制**（design-trace validator 扩展，承自 IF-004/IF-005 §5）：validator 解析本注册表与 IF-004/IF-005 §5 注册表构建已定义 IF- 集合；test-plan §8 每条 integration/e2e 行的 IF- 归属必须非空且已注册；v0.6 的 10 个 hotfix 标识与 R6 的 7 个选择语义标识生效后不可复用/重定义。
