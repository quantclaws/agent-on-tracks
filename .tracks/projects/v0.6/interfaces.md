---
interfaces_id: IF-006
spec_ref: SPEC-006
arch_ref: ARCH-006
created: 2026-08-19
status: draft
sha:
---

# v0.6 - 接口与类型化 Schema

本文是 IF-005（v0.5）的增量延伸。事件信封（IF-001 §2）、Command 基础结构（IF-001 §4）、State 投影（IF-001 §8）、Workflow 类型（IF-001 §9）、discuss 旁路（IF-003 §6/§7a）、M-IMPL 合同（IF-005 §1a–§1m）、live evidence 与 release-evidence（IF-005 §1j/§2d/§3h）、doc-comment-first 与 quarantine（IF-005 §1k–§1m）等不变；凡未提及者继承 IF-001/IF-003/IF-004/IF-005。v0.6 新增 hotfix 工作流合同：HOTFIX-TRIAGE 入口子状态机、issue 读取通道、fix/{issue} 分支与基线继承、run 并存与恢复、M-TEST 空 Shield 增量放行、M-IMPL 场景 B stale reconcile、缺口与退出路由。

## 0. 延续性（什么不变）

- `EventEnvelope` / `Command` / `Assignment` / `Outcome` / `StageDef` 结构与既有封闭集成员不变；v0.6 只做追加式扩展（§1a/§1b/§3a）。
- 既有 EVENT_TYPES / COMMAND_KINDS 成员、M-IMPL 21 子状态封闭集、`_NEXT_STAGE` 链、RedClass 封闭集、`trac validate`/`trac check` CLI 合同全部不变；v0.6 的 stage 值 `M-HOTFIX-TRIAGE` 不进 `_NEXT_STAGE` 链（非 canonical 顶层阶段）。
- v0.4 的 8 个、v0.5 的 12 个 IF- 标识不可变、不可复用；v0.6 增补 IF-HOTFIX-001～010 共 10 个标识（§5；R4 增 IF-HOTFIX-010）。
- `trac check trace` 对 feature 版本的文件级闭合语义逐字节不变；hotfix 计划级闭合只在「run 为 hotfix 且 `increment.declared` 在事件流」时启用（§1f）。
- BS-06 design-trace 的扫描范围合同（R3 增补，§2f）：inline-discussion blockquote 行是评审线程而非计划内容，不参与 test-plan 的 layer/IF- 归属扫描；§8 表格行作为唯一机器可读覆盖来源的地位不变（fail-closed）。
- hotfix 版本目录继承基线文档只读解析合同（R4 增补，§1i / IF-HOTFIX-010）：hotfix 项目目录只含 delta 三文档、无 acceptance.md/interfaces.md（FR-0241-02 source approval 不复制），canonical validator 经共享纯函数只读引用目标版本目录的对应文档；feature 版本目录的 validator 校验语义逐字节不变（dir-name 身份门控）。
- `.tracks/projects/project.toml` 测试执行合同（路径/schema/命令）不变；质量守卫栈合同不变（ARCH-006 §4.2）。
- 单写者锁（`runtime/lock`，O_CREAT|O_EXCL + holder PID）合同不变：被持有时 stderr `runtime lock held by pid <N>` + exit 1、零事件（FR-0242-04 直接复用）。

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

（`baseline.inherited` 为第 6 个新事件类型，与表首 5 个共同构成 v0.6 新事件封闭集。）

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
- **生效时点**：合同随本 revision 冻结。实现状态——§3.9 过渡路径 (b) 的带外应用在 R3 后由 commit ae5b7af 回退（为 T-012 标准 RGR 正式归档让路），T-012 至今未启动；当前工作树 `_visible_plan_lines` **未**剔除 `>` 开头行（Archer 不写实现代码，§2f 代码实现属 Devon T-012），`trac validate --file test-plan.md` 因而在历史 Prism 讨论块引用行上报 11 项 missing IF- attribution 假失败（§2f 合同已冻结但代码实现未归档的过渡态，非设计缺陷；详见 ARCH-006 §3.9 R4 实测更新）。M-DESIGN EXIT 的 validate 通过依赖 §2f 合同的带外重应用（运营端，R3 先例）或 T-012 落地——二者任一恢复 `_visible_plan_lines` 的 `>` 排除即消除该假失败类，合同语义不变。M-IMPL 重规划仍按 ARCH-006 §1.0.5 batch B/C 的 verification-only 吸收该 scope 文件的正式归档。

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

### 4b. CLI 出口

| 命令 | 可观察输出 | 关联 AC |
|:---|:---|:---|
| trac hotfix ...（五种形态，待实现） | §2a 精确 stdout/stderr/exit | AC-FR0240-01/02/03/06, AC-FR0241-04 |
| `trac status` | §2b 状态行（branch/scenario/issue）+ `suspended:` 行 + terminal 行 | AC-FR0240-01/06, AC-FR0242-01/02/03, AC-NFR0110-01/02/03, AC-FR0246-01/02 |
| `trac approve`（hotfix awaiting 形态） | human.approval 落事件 + 退出路由输出 | AC-FR0248-01 |
| `trac validate --file test-plan.md`（hotfix 目录） | 层归属/跨版本引用校验结果 + exit | AC-FR0244-02 |
| `trac check trace [--json]` | 跨版本闭合 + hotfix_scope 字段 | AC-FR0244-03/04 |
| `trac replay <run>` / `trac report --run-id <run> --format md` | hotfix 全旅程序列 + 锚定/分支/boundary 证据 | AC-FR0240-07, AC-FR0246-03 |

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

每个 IF- 标识代表一个可独立实现的接口合同，是 test-plan §8「IF- 归属」列的唯一合法取值来源。v0.4/v0.5 已建立的 20 个标识不可变、不可复用；此处列出 cross-reference 条目使 validator 可解析（定义仍以 IF-004/IF-005 §5 为准），并增补 10 个新标识。

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

**有效性校验机制**（design-trace validator 扩展，承自 IF-004/IF-005 §5）：validator 解析本注册表与 IF-004/IF-005 §5 注册表构建已定义 IF- 集合；test-plan §8 每条 integration/e2e 行的 IF- 归属必须非空且已注册；v0.6 的 10 个新标识生效后不可复用/重定义。
