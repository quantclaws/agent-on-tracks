---
interfaces_id: IF-005
spec_ref: SPEC-005
arch_ref: ARCH-005
created: 2026-08-09
status: draft
sha:
---

# v0.5 - 接口与类型化 Schema

本文是 IF-004（v0.4）的增量延伸。事件信封（IF-001 §2）、Command 基础结构（IF-001 §4）、State 投影（IF-001 §8）、Workflow 类型（IF-001 §9）、discuss 旁路类型与 CLI 合同（IF-003 §6/§7a）、`trac validate` CLI 合同（IF-003 §7b）等不变，凡未提及者继承 IF-001/IF-003/IF-004。v0.5 扩范围新增 M-IMPL 合同，并为 FR-0230～FR-0233/NFR-0080 增量定义 canonical live evidence schema、真实性边界与 `trac check release-evidence` text/JSON/exit 合同。

## 0. 延续性（什么不变）

- `EventEnvelope`（IF-001 §2）字段不变。
- `Command`（IF-001 §4）`kind` Literal 追加 M-IMPL 成员（§1b）；既有成员不变。
- `State`（IF-001 §8）追加 M-IMPL 字段（§1c）；既有字段不变。
- `StageDef`/`Transition`/`Workflow`（IF-001 §9）不变；M-IMPL 注册为 StageDef（initial_substate="BASELINE"，无 drafting_role/doc/reviewer--M-IMPL 的 Devon/Archer/Prism/Shield dispatch 由 `_decide_m_impl` 显式控制）。
- 封闭集仍用 `Literal`/`Enum`；`guard`/阶段名仍为受测字符串。
- `Assignment`（IF-003 §3）追加可选字段 `manifest`（§3a）；既有字段不变。
- `Outcome`（IF-003 §4）追加可选字段 `phase`（§3b）；既有字段不变。
- `discuss/` 旁路类型与 CLI 合同（IF-003 §6/§7a）不变。
- `trac validate` CLI 合同（IF-003 §7b）不变；`trac check` 三子命令不变；v0.5 扩展 `trac validate --file tasks.json` DAG/scope/AC 覆盖/IF- 有效性/issue number 有效性校验（§2a），新增 `trac retry` 命令（§2b）。
- 既有 `check_trace_full` / `check_trace_full_file`（IF-004 §1d）、`check_reach` / `check_reach_file`（IF-004 §1f）、`classify_red`（IF-004 §1g）行为不变；M-IMPL 的 `classify_red` 复用 v0.4 RedClass 封闭集，但 `stub_token_failure` 不在 M-IMPL 合法红之列（§1g 说明）。
- §5 IF- 标识注册表是 v0.4 建立的合同基础；v0.4 的 8 个 IF- 标识（IF-MTEST-001/002、IF-SHIELD-001、IF-TRACE-001/002、IF-REACH-001/002、IF-VALIDATE-001）不可变、不可复用；v0.5 §5 列出 cross-reference 条目供 validator 解析（定义仍以 IF-004 §5 为准），并增补 IF-IMPL-001~007、IF-DEVON-001、IF-LIVE-001 与 IF-RELEASE-001 共 10 个新标识。
- 既有 `agent_io` refs/digests、append-only events、R/G lineage 与 `trac report` 出口不变；新 live evidence bundle 只绑定并交叉核验这些既有事实，不改写它们。新增 IF-LIVE-001 与 IF-RELEASE-001，不复用既有 IF ID。

## 1. 跨模块合同

### 1a. 新事件类型（EVENT_TYPES 追加，IF-IMPL-001 / IF-IMPL-002）

事件由 executor（IF-IMPL-002）产出、kernel reducer（IF-IMPL-001）消费；两者共同实现该跨模块合同。

**modules** 列标注实现/消费该事件的模块（跨模块接口 = 2+ 模块，Shield 须有 integration 覆盖）。

| 事件类型 | payload | 发出者 | modules | 备注 |
|:---|:---|:---|:---|:---|
| `baseline.frozen` | `status: "current"\|"stale"\|"conflict"`, `digest: str`, `frozen_test_paths: list[str]` | executor（`_do_recompute_baseline`） | executor, kernel | SM-01.2/.3；current -> PLANNING，stale/conflict -> NEEDS_ATTENTION |
| `taskgraph.committed` | `task_count: int`, `task_ids: list[str]`, `validate_status: "pass"\|"fail"` | executor（`_do_validate_taskgraph`） | executor, kernel | SM-01.6；validate pass -> ISLAND_GATE_1，fail -> 重派 Archer |
| `task.started` | `task_id: str`, `attempt: int`, `manifest: dict` | executor（`_do_start_task`） | executor, kernel | SM-01.14；DAG ready task 选中 + writelock lease + manifest 创建 |
| `writelock.granted` | `task_id: str`, `lease_id: str` | executor（`_do_start_task`） | executor, kernel | SM-01.14；单写者 lease 落盘 |
| `writelock.released` | `task_id: str`, `lease_id: str` | executor（task 完成/失败后） | executor, kernel | SM-01.40；释放单写者 lease |
| `red.checkpointed` | `task_id: str`, `attempt: int`, `r_sha: str`, `ref: str` | executor（`_do_create_red_checkpoint`） | executor, kernel | SM-01.18；私有 commit R + git ref 创建 |
| `green.committed` | `task_id: str`, `attempt: int`, `g_sha: str`, `r_sha: str`, `trailers: dict` | executor（`_do_create_green_commit`） | executor, kernel | SM-01.30；正式 commit G（parent=B + trailers） |
| `refactor.committed` | `task_id: str`, `refactor_sha: str` | executor（`_do_run_refactor_gate`） | executor, kernel | SM-01.32；Refactor 产生改动并通过门禁 |
| `refactor.no_change` | `task_id: str`, `reason: str` | executor（`_do_run_refactor_gate`） | executor, kernel | SM-01.32；Devon 返回 no_change + 理由，门禁全绿 |
| `task.completed` | `task_id: str`, `attempt: int`, `lineage: dict` | executor（`_do_review_task` 后） | executor, kernel | SM-01.40；PRISM_FINAL pass 后，还有 ready task -> TASK_DISPATCH |

`prism.verdict` 事件（v0.3 既有，modules: executor, kernel）payload 的 `criteria_pack` 字段在 M-IMPL PRISM_PLAN/PRISM_RED/PRISM_FINAL/DIAGNOSE 四种派发时填充（反自述三件套②，BS-03）。`verdict.failed` 事件（v0.1 既有，modules: executor, kernel）payload 的 `check` 字段取值集合扩展（见 §1e）。`outcome.received` 事件（v0.1 既有，modules: executor, kernel）payload 追加 `phase` 字段（§3b）。

### 1b. Command kind 增量（COMMAND_KINDS 追加，IF-IMPL-001 / IF-IMPL-002）

命令由 kernel `_decide_m_impl`（IF-IMPL-001）产出、executor handler（IF-IMPL-002）执行；两者共同实现该跨模块合同。

```python
kind: Literal[...,               # IF-001/IF-003/IF-004 原有成员不变
      "recompute_baseline",      # SM-01.2：Runtime 重算 baseline digest + 冻结测试路径集
      "validate_taskgraph",      # SM-01.6：Runtime 解析 tasks.json + DAG/scope/AC 覆盖/IF- 有效性/issue number 有效性校验
      "check_island",            # SM-01.8：ISLAND_GATE_1 六元组复核
      "start_task",              # SM-01.14：DAG ready task 选 + writelock lease + manifest 创建
      "classify_red",            # SM-01.16：RED_GATE 合法红分类
      "create_red_checkpoint",   # SM-01.18：创建私有 commit R + git ref
      "run_task_gates",          # SM-01.22：GREEN_GATE targeted 单测 + int 子集 + lint
      "create_green_commit",     # SM-01.30：创建正式 commit G（parent=B + trailers）
      "run_refactor_gate",       # SM-01.32：REFACTOR_GATE 重跑 + 质量门禁分层
      "review_task",             # SM-01.35：TASK_REVIEW scope/secret/AC trace/lineage/budget
      "check_island_2",          # SM-01.42：ISLAND_GATE_2 trac check reach + 全量 int+e2e
      "create_test_commit"]      # SM-01.26：SHIELD_FIX 受控测试 commit
```

`dispatch_agent` / `validate_document` / `commit_document` / `rollback_stage` / `write_frontmatter` 复用不变；新 kind 同样遵守写前日志（FR-30）+ 每 kind execute/reconcile（D-13）。

### 1c. State 字段增量（IF-001 §8 追加，IF-IMPL-001）

```python
# M-IMPL (SM-01, FR-0010~0200)
m_impl_attempt: int = 0             # PLANNING/RED/GREEN/REFACTOR 各子状态复跑共享 <=3 预算
baseline_frozen: bool = False       # BASELINE 重算完成
baseline_digest: str | None = None  # baseline digest（三件套 + 设计三文档 + 冻结测试资产 + contracts + branch + approval）
frozen_test_paths: list | None = None  # 冻结测试路径集（integration/e2e/unit 层归属）
taskgraph_committed: bool = False   # tasks.json 解析 + 校验通过
taskgraph_tasks: list | None = None  # 解析后的 task list（ID/issue#/scope/IF-/depends_on/budget）
island_gate_1_passed: bool = False  # 六元组复核通过
current_task_id: str | None = None  # 当前正在执行的 task ID
current_task_attempt: int = 0       # 当前 task 的 RGR attempt 计数
writelock_granted: bool = False     # 单写者 lease 已获取
red_checkpointed: bool = False      # 私有 commit R + git ref 已创建
r_ref: str | None = None            # refs/trac/rgr/{run}/{task}/{attempt}/red
r_sha: str | None = None            # R commit SHA
red_classified: bool = False        # RED_GATE 分类完成
red_classifications: list | None = None  # 每条测试失败的 RedClass 分类
green_committed: bool = False       # 正式 commit G 已创建
g_sha: str | None = None            # G commit SHA
refactor_done: bool = False         # REFACTOR 完成（committed | no_change）
task_review_passed: bool = False    # TASK_REVIEW 校验通过
task_completed: bool = False        # task.completed 已产出
island_gate_2_passed: bool = False  # ISLAND_GATE_2 通过
diagnose_classification: str | None = None  # DIAGNOSE 四路路由分类（复用 v0.4 字段，M-IMPL 独立语义）
```

- `stage` 取值追加 `M-IMPL`。
- M-IMPL substate 封闭集：`BASELINE | NEEDS_ATTENTION | PLANNING | ISLAND_GATE_1 | PRISM_PLAN | TASK_DISPATCH | RED | RED_GATE | RED_CHECKPOINT | PRISM_RED | GREEN | GREEN_GATE | GREEN_COMMIT | REFACTOR | REFACTOR_GATE | TASK_REVIEW | PRISM_FINAL | TASK_DONE | ISLAND_GATE_2 | DIAGNOSE | SHIELD_FIX`（SM-01，21 个子状态）。
- `_STAGES` 增补 `StageDef(stage="M-IMPL", initial_substate="BASELINE")`（无 drafting_role/doc/reviewer--M-IMPL 的 Devon/Archer/Prism/Shield dispatch 由 `_decide_m_impl` 显式控制）。
- `_NEXT_STAGE` 增补 `"M-TEST": "M-IMPL"` 与 `"M-IMPL": "M-VERIFY"`。`"M-VERIFY"` 不在表中 -> `run.completed(terminal_state="boundary")`（M-VERIFY 未注册）。boundary 从 M-TEST->M-IMPL 移至 M-IMPL->M-VERIFY。

### 1d. executor/taskgraph.py 纯函数（FR-0030/FR-0180/FR-0220，IF-IMPL-003）

**modules**: executor/taskgraph.py（实现）、executor/executor.py（消费，`_do_validate_taskgraph`）、cli/main.py（消费，`trac validate --file tasks.json`）--跨模块接口，须有 integration 覆盖。

```python
@dataclass(frozen=True)
class TaskNode:
    task_id: str
    issue_number: int                 # GitHub issue number（正整数，FR-0180/FR-0220）
    description: str                  # 纵向切片描述 + 实现意图
    ac_refs: tuple[str, ...]          # 覆盖的 AC ID（如 AC-FR0030-01）
    fr_refs: tuple[str, ...]          # 关联 FR/NFR ID（如 FR-0030, NFR-0010）
    if_ids: tuple[str, ...]           # 实现的 IF- 集合
    test_refs: tuple[str, ...]        # test-plan §8 测试引用（file::case 标识符）
    scope_boundary: str               # manifest 授权范围（非预声明输出文件集；observed diff 为权威）
    depends_on: tuple[str, ...]       # 依赖的 task ID（[] 或 ["-"] = 无依赖，立即可调度）
    batch: str                        # 批次标记
    parallel: bool                    # [P] 标记（v0.5 串行，只记录）
    budget: int                       # attempt 预算（<=3，与 m_impl_attempt 共享）

@dataclass(frozen=True)
class TaskGraphReport:
    status: Literal["pass", "fail"]
    tasks: tuple[TaskNode, ...]
    errors: tuple[str, ...]           # DAG 环 / scope 重叠 / AC 覆盖缺口 / IF- 无效 / issue number 无效
    ac_coverage: dict[str, list[str]] # {ac_id: [task_id, ...]} required AC 覆盖映射

def parse_tasks_json(
    tasks_json_text: str,
) -> tuple[list[TaskNode], str | None]:
    """FR-0030/FR-0180 解析 tasks.json（task graph 唯一机器真相）。
    返回 (task_nodes, error)。容错解析（缺失必填字段产出错误，未知字段跳过）。
    tasks.json schema：task 对象数组，字段对应 TaskNode。
    tasks.md 仅为人类可读投影，由 Runtime 从 tasks.json 确定性生成（不作为解析来源）。"""

def validate_dag(
    tasks: list[TaskNode],
) -> tuple[bool, str | None]:
    """FR-0030/FR-0180 DAG 无环校验（Kahn's algorithm 拓扑排序）。
    返回 (is_acyclic, cycle_description)。环 -> (False, 'cycle: T-001->T-002->T-001')。"""

def validate_scope(
    tasks: list[TaskNode],
) -> tuple[bool, list[str]]:
    """FR-0030/FR-0180 scope 边界不重叠校验（manifest 白名单集合交集为空）。
    scope_boundary 是 manifest 授权范围（非预声明输出文件集）。
    返回 (no_overlap, overlap_errors)。重叠 -> (False, ['T-001 and T-002 scope overlap'])。"""

def validate_ac_coverage(
    tasks: list[TaskNode],
    required_acs: list[str],     # acceptance.md 中所有 required AC ID
    if_registry: set[str],       # interfaces.md §5 已定义的 IF- 标识集合
) -> tuple[bool, list[str]]:
    """FR-0030/FR-0180 required AC 覆盖闭合 + IF- 有效性校验。
    每个 required AC 至少被一个 task 的 ac_refs + if_ids 覆盖。
    返回 (all_covered, gap_errors)。缺口 -> (False, ['AC-FR0010-01 not covered by any task'])。
    task 声明的 IF- 标识必须在 if_registry 中（有效性校验，AC-FR0180-04 check 4）。"""

def validate_issue_numbers(
    tasks: list[TaskNode],
) -> tuple[bool, list[str]]:
    """FR-0180/FR-0220 issue number 有效性校验（AC-FR0180-04 check 5）。
    每个 task 的 issue_number 必须是正整数（>=1）。
    返回 (all_valid, errors)。无效 -> (False, ['T-001 issue_number=0 is not a positive integer'])。"""
```

### 1e. verdict.failed check 字段扩展（M-IMPL）

`verdict.failed` 事件 payload 的 `check` 字段取值集合追加 M-IMPL 特有成员：

```python
check: Literal[...,  # 既有：schema/scope/trace/scope_overflow/format/template/discussion_ready
                 #        criteria_pack_mismatch/test_defect/stub_gap/ac_gap/spec_gap
       "red_invalid",      # M-IMPL RED_GATE 非法红（collection/语法/fixture/import 错误、意外通过）
       "regression",       # M-IMPL GREEN_GATE/ISLAND_GATE_2 历史测试回归
       "budget",           # M-IMPL TASK_REVIEW budget 超限
       "island",           # M-IMPL ISLAND_GATE_1/2 六元组/孤岛不闭合
       "impl_defect",      # M-IMPL DIAGNOSE 路由：实现缺陷
       "lineage_broken",   # M-IMPL TASK_REVIEW B-R-G lineage 断裂
       "public_interface_changed"]  # M-IMPL REFACTOR_GATE 动 public interface
```

`verdict.failed(red_invalid|regression|budget|island|impl_defect|lineage_broken|public_interface_changed)` 事件 payload 另含 `target_stage: str`（回退目标：M-DESIGN/M-ACC/M-SPEC 或当前阶段重派）与 `task_id: str`（当前 task）。

### 1f. executor/rgr.py git 操作合同（FR-0070/FR-0120/FR-0200，IF-IMPL-004）

**modules**: executor/rgr.py（实现）、executor/executor.py（消费，`_do_create_red_checkpoint` / `_do_create_green_commit` / `_do_classify_red` / `_do_review_task`）--跨模块接口，须有 integration 覆盖。

```python
@dataclass(frozen=True)
class RedRef:
    ref: str          # refs/trac/rgr/{run}/{task}/{attempt}/red
    sha: str          # R commit SHA
    created: bool     # True=新建，False=已存在（compare-and-set 失败）

@dataclass(frozen=True)
class GreenCommit:
    sha: str          # G commit SHA
    parent: str       # B commit SHA（base）
    trailers: dict    # {"Tracks-Task": task_id, "Tracks-Attempt": attempt, "Tracks-R": r_sha, "Tracks-Issue": issue_number, "Tracks-AC": ac_refs}

@dataclass(frozen=True)
class LineageProof:
    r_before_g: bool          # R ref 存在 + G trailer 含 Tracks-R + 事件序列 red.checkpointed.seq < green.committed.seq
    r_ref_exists: bool        # git rev-parse refs/trac/rgr/.../red 成功
    g_trailers_valid: bool    # git log --format='%B' -1 <G> 含五个 trailer
    event_order_valid: bool   # red.checkpointed 事件 seq < green.committed 事件 seq

def create_red_ref(
    repo: str,          # git 仓库路径
    run_id: str,
    task_id: str,
    attempt: int,
    test_diff: str,     # test-only diff（Devon RED outcome）
    base_sha: str,      # B commit SHA（C_design）
) -> RedRef:
    """FR-0070 创建私有 commit R + git ref（compare-and-set）。
    ref = refs/trac/rgr/{run}/{task}/{attempt}/red
    先 git rev-parse 检查 ref 是否存在；已存在 -> RedRef(created=False)，开新 attempt。
    不存在 -> git commit-tree（test_diff as tree patch on base_sha）+ git update-ref。
    R 不可变（BS-06）：同一 attempt 重试改写 R 时 compare-and-set 失败。"""

def create_green_commit(
    repo: str,
    run_id: str,
    task_id: str,
    attempt: int,
    impl_diff: str,     # Devon GREEN outcome 的产品代码 diff
    base_sha: str,      # B commit SHA（parent=B）
    r_sha: str,         # R commit SHA（用于 trailer）
    issue_number: int,  # GitHub issue number（FR-0220，trailer Tracks-Issue）
    ac_refs: list[str], # AC/FR/NFR provenance（FR-0220，trailer Tracks-AC）
) -> GreenCommit:
    """FR-0120 创建正式 commit G（parent=B + trailers）。
    git commit-tree（impl_diff as tree patch on base_sha）with trailers:
      Tracks-Task: {task_id}
      Tracks-Attempt: {attempt}
      Tracks-R: {r_sha}
      Tracks-Issue: {issue_number}
      Tracks-AC: {ac_refs}
    G 的 parent=B（不作 Git ancestry 拓扑断言，R 不是 G 的祖先，R-1）。
    Tracks-Issue/Tracks-AC 在 R-3/R-4 新增：Devon 提交时在 trailers 中包含 issue# + FR/NFR/ACC provenance。"""

def classify_red(
    test_id: str, returncode: int, stdout: str, stderr: str,
) -> str:
    """FR-0080 M-IMPL RED_GATE 合法红分类（纯函数，复用 v0.4 RedClass 封闭集）。
    返回 RedClass（IF-004 §1g）。M-IMPL 合法红 = assertion_failure / symbol_missing。
    stub_token_failure 不在 M-IMPL 合法红之列（属 M-TEST 合同测试场景）。
    非法红 = collection_error / unexpected_pass / unclassified。"""

def verify_lineage(
    repo: str,
    run_id: str,
    task_id: str,
    attempt: int,
    g_sha: str,
    events: list,       # red.checkpointed + green.committed 事件（按 seq 排序）
) -> LineageProof:
    """FR-0120 R 先于 G 的 lineage 证明（纯函数 + git 只读）。
    不作 Git ancestry 拓扑断言（R-1）：R 与 G 均以 B 为父节点。
    lineage 由 ref + trailer + 事件序列三件联合证明。"""
```

### 1g. executor/quality_gate.py 分层执行合同（FR-0130，IF-IMPL-005）

**modules**: executor/quality_gate.py（实现）、executor/executor.py（消费，`_do_run_task_gates` / `_do_run_refactor_gate`）--跨模块接口，须有 integration 覆盖。

```python
@dataclass(frozen=True)
class GateResult:
    status: Literal["pass", "fail"]
    checks_run: tuple[str, ...]      # 执行的检查名（如 'ruff', 'flake8:CCR001', 'pylint:R0801'）
    failures: tuple[str, ...]        # 失败的检查 + 详情
    layer: Literal["production", "test", "both"]  # 执行的分层

def run_gates(
    repo: str,
    changed_paths: list[str],    # 本次 task 改动的文件路径
    gate_scope: Literal["green_gate", "refactor_gate"],
    project_toml: dict,          # .tracks/project/project.toml 解析后的合同
) -> GateResult:
    """FR-0110/FR-0130 质量门禁分层执行。
    GREEN_GATE：targeted 单测 + 历史单测 + int 子集 + lint/format/type/static + 合同。
    REFACTOR_GATE：重跑 GREEN_GATE 全部检查 + 质量门禁分层（生产全检查、测试仅 R0801+C0302）。
    按 changed_paths 分类文件为生产代码（tracks/ 下非 tests/）或测试代码（tests/ 下），
    分别执行不同检查集（BS-11）。"""

def run_production_checks(
    repo: str,
    changed_paths: list[str],
) -> GateResult:
    """FR-0130 生产代码完整四段：ruff + flake8 CCR001 + pylint R0801 + C0302 + R0915 + R0914。
    任一判失败 -> status=fail。"""

def run_test_checks(
    repo: str,
    changed_paths: list[str],
) -> GateResult:
    """FR-0130 测试代码仅 R0801 + C0302（BS-11）。
    不套用 CCR001 / R0915 / R0914。"""
```

### 1h. executor/worktree.py 三 worktree 方案合同（FR-0070，IF-IMPL-006）

**modules**: executor/worktree.py（实现）、executor/executor.py（消费，RED/GREEN/GREEN_GATE/REFACTOR_GATE/SHIELD_FIX handler）--跨模块接口，须有 integration 覆盖。

```python
@dataclass(frozen=True)
class WorktreeHandle:
    path: str           # worktree 挂载路径
    base_sha: str       # worktree 的 base commit
    kind: Literal["devon_candidate", "gate", "test_authority"]

def create_devon_worktree(
    repo: str,
    c_design_sha: str,      # M-DESIGN pass 后的共同基线
    run_id: str,
    task_id: str,
) -> WorktreeHandle:
    """FR-0070 创建 Devon candidate worktree（从 C_design）。
    Devon 在此 worktree 运行，天然不包含 Shield WRITE 之后生成的测试（BS-04 时间隔离）。
    git worktree add <path> <c_design_sha>。"""

def create_test_authority_worktree(
    repo: str,
    c_design_sha: str,
    run_id: str,
) -> WorktreeHandle:
    """FR-0070 创建 test-authority worktree。
    Shield 在此 worktree 冻结测试（frozen bundle），独立 commit。"""

def create_gate_worktree(
    repo: str,
    c_design_sha: str,
    frozen_bundle_sha: str,  # test-authority worktree 的 frozen test commit
    devon_diff: str,         # Devon candidate 的产品代码 diff
    run_id: str,
    task_id: str,
) -> WorktreeHandle:
    """FR-0070/FR-0110 创建 gate worktree（组合 C_design + frozen bundle + Devon candidate diff）。
    先 git worktree add 从 C_design，然后 checkout Devon candidate 的产品代码 diff，
    最后 cherry-pick 或 merge frozen test commit。
    frozen bundle 永不合入 Devon candidate（BS-04）。"""

def cleanup_worktree(
    handle: WorktreeHandle,
) -> bool:
    """FR-0070 phase 完成后清理 worktree（git worktree remove）。
    返回 True=成功清理，False=清理失败（产出 error 事件，不破坏既有 worktree）。"""
```

### 1i. DIAGNOSE 四路路由分类封闭集（FR-0150，IF-IMPL-001 / IF-IMPL-002）

**modules**: kernel/machine.py（消费 `verdict.failed(classification)` 路由）、executor/executor.py（产出 `verdict.failed` 事件）--跨模块接口，须有 integration 覆盖。

```python
DiagnoseRoute = Literal[
    "impl_defect",      # 实现缺陷 -> 回 GREEN 重派 Devon（SM-01.25）
    "test_defect",      # 测试缺陷 -> SHIELD_FIX dispatch Shield（SM-01.26）
    "stub_gap",         # 接口/架构不足 -> rollback_stage(M-DESIGN)（SM-01.27，不经 Human）
    "ac_gap",           # AC 缺口 -> rollback_stage(M-ACC)（SM-01.28，Human 批准后）
    "spec_gap",         # Spec 缺口 -> rollback_stage(M-SPEC)（SM-01.28，Human 批准后）
]
```

DIAGNOSE 分流结论落 `verdict.failed(reason)` 事件，`reason` 取值限于 `red_invalid` / `regression` / `budget` / `island` / `scope` / `test_defect` / `impl_defect`（FR-0150）。`stub_gap` / `ac_gap` / `spec_gap` 复用 v0.4 既有 `verdict.failed(check)` 的 `check` 字段（IF-004 §1e）。

### 1j. canonical live evidence bundle（FR-0230/FR-0231，IF-LIVE-001）

**modules**: executor/live_evidence.py（实现/写入）、executor/executor.py（boundary 消费）、effects/opencode.py（真实 backend 与 agent I/O 来源）、store/store.py（events/blobs 来源）、checks/release_evidence.py（读取/校验）--跨模块接口，须有 integration 覆盖；真实成功组合须有 e2e_live 覆盖。

```python
@dataclass(frozen=True)
class AgentIOReceipt:
    role: str
    phase: str                         # Devon 固定 RED|GREEN|REFACTOR；Prism 为 PRISM_RED|PRISM_FINAL
    task_id: str
    attempt: int
    command_seq: int
    outcome_seq: int
    input_ref: str                     # content-addressed blob relative ref
    input_sha256: str                  # 64 lowercase hex
    output_ref: str
    output_sha256: str                 # 64 lowercase hex
    audit_completeness: Literal["complete"]

@dataclass(frozen=True)
class CandidateArtifactEvidence:
    name: str                          # wheel basename
    sha256: str                        # actual installed wheel bytes SHA-256
    distribution: Literal["agent-on-tracks"]
    version: str
    installed_import_path: str         # resolved tracks.__file__
    source_tree_import: Literal[False]

@dataclass(frozen=True)
class ProvenanceEvidence:
    backend_class: Literal["OpencodeBackend"]
    fake_backend: Literal[False]
    trac_fake_simulate: Literal[False]
    assignment_overlay: Literal[False]
    assignment_simulation: Literal[False]
    event_origin: Literal["runtime"]

@dataclass(frozen=True)
class EventSequenceEvidence:
    first_seq: int
    last_seq: int
    events_ref: str                    # canonical event slice blob
    events_sha256: str                 # canonical JSON bytes SHA-256

@dataclass(frozen=True)
class RGRLineageEvidence:
    task_id: str
    attempt: int
    red_ref: str                       # refs/trac/rgr/{run}/{task}/{attempt}/red
    r_sha: str
    g_sha: str
    red_checkpoint_seq: int
    green_commit_seq: int
    trailers: dict[str, str]           # Tracks-Task/Attempt/R/Issue/AC

@dataclass(frozen=True)
class GateObservation:
    gate: Literal["RED_GATE", "GREEN_GATE", "REFACTOR_GATE", "TASK_REVIEW",
                  "PRISM_FINAL", "ISLAND_GATE_2"]
    status: Literal["pass"]
    seq: int
    source: Literal["runtime", "prism"]
    source_event_type: str
    command_id: str

@dataclass(frozen=True)
class BoundaryEvidence:
    stage: Literal["M-IMPL"]
    stage_exited_seq: int
    run_completed_seq: int
    terminal_state: Literal["boundary"]

@dataclass(frozen=True)
class LiveEvidenceBundle:
    schema_version: Literal["tracks.release-evidence/v1"]
    status: Literal["satisfied"]
    candidate_sha: str                 # live run 启动前 source checkout 完整 git HEAD
    candidate_artifact: CandidateArtifactEvidence
    run_id: str
    backend: Literal["opencode"]
    provenance: ProvenanceEvidence
    required_devon_phases: tuple[str, str, str]  # 精确 ("RED","GREEN","REFACTOR")
    agent_io: tuple[AgentIOReceipt, ...]
    event_sequence: EventSequenceEvidence
    rgr_lineage: RGRLineageEvidence
    gate_observations: tuple[GateObservation, ...]
    boundary: BoundaryEvidence

def bind_live_evidence(
    *, repo: str, run_id: str, candidate_sha: str,
    candidate_artifact: CandidateArtifactEvidence, backend_name: str,
    trac_fake_simulate: bool, assignment_overlay: bool,
    assignment_simulation: bool,
    events: list[dict],
) -> LiveEvidenceBundle:
    """从实际 Runtime/OpencodeBackend 事实绑定 bundle。
    FakeBackend、TRAC_FAKE_SIMULATE、assignment overlay/simulation、缺任一真实 dispatch receipt、
    agent I/O、gate、lineage 或 boundary 时 raise ValueError("IF-LIVE-001:<reason>")，
    调用者不得写 success bundle。不得从 agent self_report 补字段。"""

def write_live_evidence(
    repo: str, bundle: LiveEvidenceBundle, referenced_blobs: dict[str, bytes],
) -> str:
    """按 §3h canonical 路径原子写入 JSON 与 blobs，返回 evidence.json 相对路径。
    canonical JSON = UTF-8、sort_keys=True、separators=(",", ":")、末尾 LF。
    已存在同路径且 bytes 不同则 fail closed，不覆盖。"""
```

封闭不变量：

1. `agent_io` 至少含同一 `task_id`/`attempt` 对应的 Devon RED/GREEN/REFACTOR 三条 receipt，以及 Prism PRISM_RED/PRISM_FINAL receipt；每条 `command_seq < outcome_seq`，引用 blob SHA-256 必须与 bytes 相等且 `audit_completeness="complete"`。
2. `event_sequence.first_seq <= 所有 receipt/gate/lineage/boundary seq <= last_seq`；event slice 按 seq 严格递增、无重复，且 digest 匹配。
3. lineage 要求 `red_checkpoint_seq < green_commit_seq`、R ref 当前解析为 `r_sha`、G parent=B、五个 trailers 匹配 task/attempt/R/provenance。
4. 六个 gate 各出现一次并按旅程顺序递增；TASK_REVIEW 来源 Runtime，PRISM_FINAL 来源真实 Prism receipt；ISLAND_GATE_2 后才允许 boundary。
5. boundary 要求 `stage.exited(M-IMPL)` 后紧随该 run 的 `run.completed(terminal_state="boundary")`。人工只补这两个 event 仍因缺 dispatch receipt、blob、lineage/gates 失败。

Live launch provenance（只供 evidence 绑定，不改变普通 `trac run` 语义）：

| # | 环境变量 | 值 | 缺失/错误语义 |
|:---|:---|:---|:---|
| 1 | `TRAC_LIVE_CANDIDATE_SHA` | build 前 source checkout 的 40-hex full HEAD | 普通 run 可继续；不得写 release evidence |
| 2 | `TRAC_LIVE_ARTIFACT_SHA256` | 实际安装 wheel bytes 的 64-hex SHA-256 | 普通 run 可继续；不得写 release evidence |

两字段由 live harness 在 wheel hash、fresh venv install 与 import-path probe 通过后注入；Runtime 必须与安装环境 `.dist-info/direct_url.json` archive hash、distribution/version/import path 交叉核验。字段不得进入 agent assignment，Agent outcome/self-report 不得覆盖。

## 2. CLI 接口合同

### 2a. `trac validate --file tasks.json`（扩展校验范围，IF-IMPL-003 / IF-VALIDATE-001）

IF-003 §7b 既有 `trac validate --file <path>` 不变。v0.5 扩展：

- `trac validate --file tasks.json`：`check_design_trace` 扩展为校验 tasks.json 的 DAG 无环 / scope 边界不重叠 / required AC 覆盖闭合 / IF- 有效性 / issue number 有效性（FR-0180）。校验调用 `taskgraph.parse_tasks_json` + `taskgraph.validate_dag` + `taskgraph.validate_scope` + `taskgraph.validate_ac_coverage` + `taskgraph.validate_issue_numbers`（IF-IMPL-003）。任一不满足判失败（非零退出）并指出位置。tasks.md 为人类可读投影，由 Runtime 从 tasks.json 确定性生成（不作为校验来源）。
- 既有 IF- 归属校验（v0.4 FR-0140）行为不回归：每条 integration/e2e AC 的 IF- 归属非空且在 interfaces.md §5 已定义。
- `trac validate --file architecture.md` / `interfaces.md`：既有行为不变。

### 2b. `trac retry`（新增命令，NFR-0030-03）

| 命令 | 输入 | 前置校验 | 成功输出 | 失败输出 | exit |
|:---|:---|:---|:---|:---|:---|
| `trac retry [--clear-evidence]` | 当前 repo；活跃 run | `trac status` 报告 `status=awaiting_human` 且 `awaiting=escalation`（或 clean active 状态） | stdout: `human.retry event appended; escalation gate cleared; attempt budget reset` | stderr: `not in escalation state` 或错误原因 | 0=成功 / 1=非 escalation 状态或错误 |

- `trac retry` 追加 `human.retry` 事件到 events 表、清除 escalation gate（`status` 从 `awaiting_human` 回 `active`、`awaiting` 清空）、重置一份新的 ≤3 attempt 预算（`current_attempt` / `m_impl_attempt` 归零）、保留失败证据（`last_failure` 不清空，除非 `--clear-evidence`）。
- `trac retry` 不自动重新派发（Human 须再次 `trac run`）。
- `trac retry` 在非 escalation 状态下拒绝（退出非零）并提示原因。
- `trac retry --clear-evidence` 仅在 escalation 或 clean active 状态下允许，清空 `last_failure`。

### 2c. `trac status`（扩展输出，NFR-0030-02）

既有 `trac status` 输出不变。v0.5 扩展：

- M-IMPL 期间报告 `stage=M-IMPL` + 当前子状态（21 个子状态之一）。
- `trac status` 输出含当前 attempt 计数与最近失败类（`current_attempt` / `m_impl_attempt` + `last_failure.check`）。

### 2d. `trac check release-evidence [--json]`（FR-0232/FR-0233/NFR-0080，IF-RELEASE-001）

| # | 调用 | stdout | stderr | exit |
|:---|:---|:---|:---|:---|
| 1 | `trac check release-evidence` 满足 | 单行 `release-evidence: satisfied (backend=opencode, run <run_id>, candidate HEAD <full_sha>, branch <branch>)` | 空 | 0 |
| 2 | `trac check release-evidence` 不满足 | 第一行 `release-evidence: NOT satisfied — <reason_code>`；第二行 `  next: rerun the opt-in live journey at current HEAD, then re-check` | 空 | 1 |
| 3 | `trac check release-evidence --json` | 单行 canonical JSON（sort keys、紧凑 separators）：`{"backend":...,"candidate_sha":...,"evidence_path":...,"event_bounds":...,"reason_code":...,"run_id":...,"status":"satisfied|not_satisfied"}`；未知值为 `null` | 空 | 0 iff status=satisfied，否则 1 |
| 4 | 未知参数/组合 | 空 | `usage: trac check release-evidence [--json]` | 2 |

`reason_code` 封闭集：`ok | missing | stale | malformed | not_real | audit_incomplete | journey_incomplete`。判定和选择算法固定：

1. 以 `git rev-parse HEAD` 得 current full SHA；枚举 canonical root 下 `*/<run_id>/evidence.json`，只按 UTF-8 路径字节序排序，不读 mtime/clock。
2. 无任何 bundle → `missing`。无 current-SHA 目录 bundle但存在其他 SHA bundle → `stale`。
3. current-SHA 目录中按 `run_id` 字节序选最大项；JSON/schema/canonical path/digest 错 → `malformed`；bundle `candidate_sha != current HEAD` → `stale`。
4. backend/provenance 任一非真实（含 FakeBackend、`TRAC_FAKE_SIMULATE`、assignment overlay、assignment/scenario simulation、`event_origin != runtime`）→ `not_real`。
5. agent I/O refs/digests/completeness 错 → `audit_incomplete`；required phases、event bounds/order、lineage、gates、review、task completion、ISLAND_GATE_2 或 boundary 任一缺失/不匹配 → `journey_incomplete`。
6. 全部满足 → `ok`/`satisfied`。同一输入 bytes 与 Git HEAD 必须产生逐字节相同 JSON/text；检查只读，不 append event、不改 refs/commits/worktree、不构建或发布。

`branch` 只用于 text 展示，由 `git branch --show-current` 获取；detached HEAD 显示 `(detached)`，不影响 SHA 判定。Human approval 不是输入，不能改变 exit。

## 3. 文件 / 存储契约

### 3a. Assignment 增量（IF-IMPL-001 / IF-DEVON-001）

`Assignment`（IF-003 §3）追加可选字段：

```python
manifest: dict | None = None       # M-IMPL TASK_DISPATCH 时由 Runtime 填入（per-task 白名单）
                                  # {"allowed_paths": [绝对路径...],
                                  #  "forbidden_paths": [绝对路径...],
                                  #  "task_id": str, "attempt": int,
                                  #  "phase": "red"|"green"|"refactor"}
                                  # Devon 越界写（manifest 白名单之外）-> over_reach failure_class

phase: str | None = None           # M-IMPL Devon dispatch 时由 Runtime 填入
                                  # "red" | "green" | "refactor"
                                  # Devon 据 phase 决定写 unit test 还是产品代码

r_tree_identity: dict | None = None  # M-IMPL GREEN dispatch 时由 Runtime 填入
                                     # {"r_sha": str, "ref": str}
                                     # Devon GREEN 从 R tree 恢复 worktree，R 测试不可改

criteria_pack: dict | None = None  # M-IMPL Prism dispatch 时由 Runtime 填入（承自 v0.4 IF-004 §3a）
                                  # {"name": "tracks-prism-impl", "version": "0.1"}
                                  # 反自述三件套①：Prism 不自选，按 assignment 加载

test_tasks: list[dict] | None = None  # M-IMPL SHIELD_FIX 时由 Runtime 填入（承自 v0.4 IF-004 §3a）
                                      # Shield 按 test_tasks 修测试，不自衍 AC 层归属
```

Devon assignment 携带 `skills: ["tracks-prism-impl"]`（M-IMPL 评审判据包，Prism 消费；Devon 自身不消费判据包 skill，但 assignment 需要物化判据包 identity 供 Prism 反自述回读）。Devon assignment 携带 `docs: ["tasks.json", "interfaces", "architecture"]`（只读上下文）与 `manifest`（per-task 白名单）+ `phase`（red/green/refactor）+ `issue_number` + `ac_refs`（FR-0220：commit trailers provenance 来源）。

### 3b. Outcome 增量

`Outcome`（IF-003 §4）追加可选字段：

```python
phase: str | None = None          # Devon outcome 携带的 phase（red/green/refactor）
                                  # Runtime 回读核对：outcome.phase 与 assignment.phase 一致

no_change: bool | None = None     # REFACTOR Devon outcome 可含 no_change=True + 理由
                                  # BS-10：不强制产生改动，质量由门禁保证
```

### 3c. 路径契约

| 路径 | 格式 | 写入者 | 读取者 |
|:---|:---|:---|:---|
| `tracks/executor/taskgraph.py` | Python 模块 | Devon（实现） | executor/executor.py、cli/main.py、tests |
| `tracks/executor/rgr.py` | Python 模块 | Devon（实现） | executor/executor.py、tests |
| `tracks/executor/worktree.py` | Python 模块 | Devon（实现） | executor/executor.py、tests |
| `tracks/executor/quality_gate.py` | Python 模块 | Devon（实现） | executor/executor.py、tests |
| `tracks/agents/Devon.md` | opencode agent（frontmatter + body，无 permission 块） | 维护者（spec 交付物，v0.4 已创建） | OpencodeBackend（物化源）、check_deliverables |
| `tracks/skills/tracks-prism-impl/SKILL.md` | opencode skill（frontmatter + body） | 维护者（v0.4 已创建） | OpencodeBackend（M-IMPL Prism 派发物化到上下文） |
| `.tracks/project/project.toml` | TOML（canonical 宿主项目测试执行合同） | Archer（M-DESIGN 创建） | project.py（load_contract）、executor（collect/run 命令） |
| `.tracks/projects/{ver}/tasks.json` | JSON（task graph 唯一机器真相） | Archer（M-IMPL PLANNING） | executor（解析驱动 DAG 调度）、trac validate |
| `.tracks/projects/{ver}/tasks.md` | Markdown（人类可读投影，Runtime 确定性生成） | Runtime（从 tasks.json 生成） | trac report（per-task 进展重建）、Human 阅读 |
| `refs/trac/rgr/{run}/{task}/{attempt}/red` | git ref（指向私有 commit R） | executor（`_do_create_red_checkpoint`） | executor（lineage 证明）、tests（git rev-parse） |
| git commit G | git commit（parent=B + trailers） | executor（`_do_create_green_commit`） | tests（git log --format='%B'）、executor（lineage 证明） |
| git commit R | git commit（test-only diff on B） | executor（`_do_create_red_checkpoint`） | tests（git show）、executor（GREEN 从 R tree 恢复） |
| `.flake8` | INI 配置 | Archer（M-DESIGN 脚手架） + Devon（维护） | flake8、quality_gate.py |
| `pyproject.toml` | TOML | Archer（M-DESIGN 脚手架） + Devon（维护） | ruff、pylint、coverage、quality_gate.py |

### 3d. R ref 命名与不可变规则（FR-0070，BS-06）

R ref 命名：`refs/trac/rgr/{run_id}/{task_id}/{attempt}/red`。

- `{run_id}`：当前 run 的 ID（ULID）。
- `{task_id}`：当前 task 的 ID（如 `T-001`）。
- `{attempt}`：当前 RGR attempt 编号（从 1 开始）。

R ref 不可变（BS-06）：

- 创建时 compare-and-set：先 `git rev-parse` 检查 ref 是否存在，已存在则 `RedRef(created=False)`，开新 attempt。
- 同一 attempt 重试试图改写 R ref 时 compare-and-set 失败，旧 attempt 的 R ref 不被改写。
- `git rev-parse refs/trac/rgr/{run}/{task}/{attempt}/red` 在重试前后指向同一 SHA（AC-FR0070-04）。

### 3e. G commit trailer 格式（FR-0120/FR-0220）

G commit message 含五个 trailer（Git ≥ 2.32 `--trailer` 选项；低于 2.32 回退到手动构造 commit message）：

```
Tracks-Task: {task_id}
Tracks-Attempt: {attempt}
Tracks-R: {r_sha}
Tracks-Issue: {issue_number}
Tracks-AC: {ac_refs}
```

`Tracks-Issue` / `Tracks-AC` 在 R-3/R-4 新增（FR-0220）：Devon 提交时在 trailers 中包含 issue# + FR/NFR/ACC provenance，使后续 bug fix 保留 provenance。`ac_refs` 为逗号分隔的 AC/FR/NFR 标识列表。

`git log --format='%B' -1 <G_sha>` 输出含以上五个 trailer 行（AC-FR0120-01）。G 的 parent=B（`git log --format='%P' -1 <G_sha>` 输出 B 的 SHA，无 R SHA）。

### 3f. `.tracks/project/project.toml` canonical 路径约束（FR-0190 第 2 项）

canonical `.tracks/project/project.toml` 是唯一允许的 `.tracks/**` project contract 路径（AC-FR0190-03）。`project.py` 的 `load_contract` 强化路径校验：

- 只接受 `.tracks/project/project.toml`，其他 `.tracks/**` 路径 fail closed。
- 随设计 checkpoint 去重提交（同一 run 内不重复加载）。
- contract 中相对 `.venv/bin/python{,3}` 在宿主 worktree 不存在时，只能回退当前 Runtime 的 venv `sys.executable`，不得系统 Python（AC-FR0190-08）；项目自有 venv 存在时优先。

### 3g. dispatch 物化字段集（FR-0190 第 9 项）

Runtime dispatch 必须在 `command.issued` 前向 agent assignment 物化完整上下文（AC-FR0190-01）。物化字段集：

```python
# assignment payload 必须包含的物化字段（FR-0190 第 9 项）
{
    "target_doc": str | None,          # 绝对 target doc 路径（或 None for multi-doc）
    "doc_set": list[str] | None,       # 绝对 doc-set 路径（M-DESIGN 三文档）
    "role": str,                       # scribe/sage/lex/archer/prism/shield/devon
    "substate": str,                   # DRAFT/REVIEW/WRITE/PRISM_REVIEW/RED/GREEN/REFACTOR/...
    "attempt": int,                    # 当前 attempt 编号
    "review_round": int,               # 当前 review round
    "docs": list[str],                 # 只读上下文文档路径
    "templates": list[str] | None,     # 物化的模板路径
    "skills": list[str],               # 物化的 skill 路径
    "criteria_pack": dict | None,      # 判据包 identity（Prism 派发时）
    "test_tasks": list[dict] | None,   # Shield WRITE/SHIELD_FIX 时的 AC 层归属
    "manifest": dict | None,           # Devon 派发时的 per-task 白名单
    "phase": str | None,               # Devon 派发时的 phase（red/green/refactor）
    "r_tree_identity": dict | None,    # Devon GREEN 时的 R tree identity
    "issue_number": int | None,        # Devon 派发时的 GitHub issue number（FR-0220，trailer provenance 来源）
    "ac_refs": list[str] | None,       # Devon 派发时的 AC/FR/NFR provenance（FR-0220，trailer provenance 来源）
    "pre_dirty_snapshot": dict | None, # ResultCheckpoint 持久化的 pre-dirty 快照
    "result_identity": dict | None,    # result/checkpoint identity
}
```

agent 不得自行搜索/猜任何物化字段（FR-0190）。无效输入（如 Shield WRITE 时 `test_tasks` 为空）不调用 backend，failed outcome=`stub_gap`（AC-FR0190-05）。

### 3h. release evidence 路径与 JSON 存储合同（FR-0231/FR-0232）

| # | 路径 | 格式 | 写入者 | 读取者 |
|:---|:---|:---|:---|:---|
| 1 | `.tracks/runtime/release-evidence/v1/{candidate_sha}/{run_id}/evidence.json` | `LiveEvidenceBundle` canonical JSON | executor/live_evidence.py；live harness 仅可逐字节传输 | checks/release_evidence.py、操作者 |
| 2 | `.tracks/runtime/release-evidence/v1/{candidate_sha}/{run_id}/blobs/{sha256}` | 脱敏 agent I/O 或 canonical event slice 原始 bytes，文件名=SHA-256 | executor/live_evidence.py；live harness 仅可逐字节传输 | checks/release_evidence.py |

- `{candidate_sha}` 必须与 JSON 字段完全一致且为 `git rev-parse HEAD` 完整输出；`{run_id}` 必须与 JSON 一致。路径不一致判 `malformed`。
- bundle 是运行证据，不纳入 Git，不由 Human 手工创建。只有 `write_live_evidence` 可生成；isolated demo host 向 candidate checkout 的复制必须保持每个文件 bytes/digest，不得解析后重写。
- agent I/O 沿用 `redact` 后的 blob；API key/token 等 secret 不得进入 bundle。validator 只校验 digest/ref，不回显 blob 内容。
- 失败、取消、skip 或不完整 journey 不写 `status=satisfied` bundle；可以保留 events/report 诊断，但不放入 canonical release-evidence root。

## 4. 可观察出口（测试断言基础）

test-plan 的断言只能落在以下外部可观察出口（§6.5 闭环）：

### 4a. 事件出口（events 表）

| 事件 | 可观察字段 | 关联 AC |
|:---|:---|:---|
| `stage.entered(M-IMPL)` | stage | AC-FR0010-01 |
| `baseline.frozen` | status, digest, frozen_test_paths | AC-FR0020-01/03/04 |
| `stage.rolled_back` (NEEDS_ATTENTION) | from_stage, to_stage, reason | AC-FR0020-02 |
| `taskgraph.committed` | task_count, task_ids, validate_status | AC-FR0030-01/02 |
| `verdict.failed(island)` (ISLAND_GATE_1) | check=island | AC-FR0040-02 |
| `prism.verdict` (M-IMPL) | verdict, criteria_pack, diff_ref | AC-FR0050-01/02/04, AC-FR0090-01/02, AC-FR0140-02 |
| `verdict.failed` (criteria_pack_mismatch) | check=criteria_pack_mismatch | AC-FR0050-01 |
| `stage.rolled_back` (PRISM_PLAN 设计缺口) | from_stage, to_stage, reason | AC-FR0050-02 |
| `writelock.granted` | task_id, lease_id | AC-FR0060-01 |
| `task.started` | task_id, attempt, manifest | AC-FR0060-01/03 |
| `red.checkpointed` | task_id, attempt, r_sha, ref | AC-FR0070-03/04 |
| `verdict.failed(red_invalid)` | check=red_invalid | AC-FR0080-02 |
| `green.committed` | task_id, attempt, g_sha, r_sha, trailers | AC-FR0120-01/02, AC-FR0220-03 |
| `refactor.committed` | task_id, refactor_sha | AC-FR0130-01/03 |
| `refactor.no_change` | task_id, reason | AC-FR0130-01 |
| `verdict.failed(public_interface_changed)` | check=public_interface_changed | AC-FR0130-03 |
| `verdict.failed(budget\|scope\|lineage_broken)` | check, task_id | AC-FR0140-01 |
| `task.completed` | task_id, attempt, lineage | AC-FR0140-03 |
| `verdict.failed(impl_defect\|test_defect\|stub_gap\|ac_gap\|spec_gap)` | check, target_stage, task_id | AC-FR0150-01/02 |
| `test.committed` (SHIELD_FIX) | commit_sha, test_count | AC-FR0150-03 |
| `stage.exited(M-IMPL)` | stage | AC-FR0160-03 |
| `run.completed` | terminal_state="boundary" | AC-FR0160-03 |
| `stage.rolled_back` (DIAGNOSE) | from_stage, to_stage, reason | AC-FR0150-01/04 |
| `outcome.received` (devon) | role, status, phase, audit_evidence, failure_class | AC-FR0070-02, AC-FR0100-01/02, AC-FR0170-03 |
| `outcome.received` (shield) | role, status, audit_evidence, failure_class | AC-FR0150-03 |
| `command.issued` (dispatch_agent devon) | command.params.role, .substate, .assignment.manifest, .assignment.phase, .assignment.r_tree_identity, .assignment.criteria_pack, .assignment.issue_number, .assignment.ac_refs | AC-FR0070-01/02, AC-FR0100-01, AC-FR0050-01, AC-FR0220-03 |
| `command.issued` (dispatch_agent prism) | command.params.assignment.criteria_pack | AC-FR0050-01/04, AC-FR0090-01, AC-FR0140-02 |
| `human.retry` | (无 payload) | AC-NFR0030-03 |

### 4b. CLI 出口

| 命令 | 可观察输出 | 关联 AC |
|:---|:---|:---|
| `trac status` | stdout（stage=M-IMPL, substate=BASELINE/PLANNING/.../SHIELD_FIX, attempt 计数, failure 类） | AC-FR0010-02, AC-NFR0030-02 |
| `trac validate --file tasks.json` | stdout/stderr（DAG/scope/AC 覆盖/IF- 有效性/issue number 有效性校验结果）、exit code | AC-FR0180-02/04 |
| `trac retry` | stdout（`human.retry event appended...`）、exit code | AC-NFR0030-03 |
| `trac check reach` | stdout/stderr（孤岛清单）、`--json` ReachReport、exit code | AC-FR0160-01 |
| `trac check deliverables` | stdout/stderr（含 Devon.md）、exit code | AC-FR0170-02 |
| `trac report` | stdout（per-task RGR lineage + 进展） | AC-FR0030-03, AC-FR0140-03, AC-FR0150-02 |

### 4c. 文件出口

| 路径 | 可观察内容 | 关联 AC |
|:---|:---|:---|
| `.tracks/projects/{ver}/tasks.json` | task graph JSON schema 合规（TaskNode 字段 + DAG + scope + AC 覆盖 + IF- 有效性 + issue number） | AC-FR0030-01, AC-FR0180-01/02/04, AC-FR0220-01 |
| `.tracks/projects/{ver}/tasks.md` | 人类可读投影（Runtime 从 tasks.json 确定性生成） | AC-FR0030-03, AC-FR0180-03 |
| `refs/trac/rgr/{run}/{task}/{attempt}/red` | git ref 存在 + 指向 test-only diff commit | AC-FR0070-03/04 |
| git commit G | parent=B + trailers（Tracks-Task/Tracks-Attempt/Tracks-R/Tracks-Issue/Tracks-AC） | AC-FR0120-01/02, AC-FR0220-03 |
| git commit R | test-only diff on B | AC-FR0070-03 |
| `.tracks/project/project.toml` | TOML schema 合规（integration/e2e framework/paths/collect/run/cwd） | AC-FR0190-03 |
| `tracks/agents/Devon.md` | frontmatter version + IQ，无 permission 块 | AC-FR0170-02 |
| `tracks/skills/tracks-prism-impl/SKILL.md` | frontmatter name + version；内容为语义判据 | AC-FR0050-04 |
| git 工作区 | 工具运行前后无文件变化（NFR-0010） | AC-NFR0010-01 |
| `.tracks/runtime/tracks.db` events 表 | M-IMPL 事件 append-only | AC-NFR0020-01/02 |
| `.tracks/runtime/release-evidence/v1/{candidate_sha}/{run_id}/evidence.json` | canonical bundle：candidate artifact/SHA、run/backend/provenance、agent I/O refs/digests、event bounds、required Devon phases、RGR lineage、gate observations、boundary | AC-FR0230-01/02/03, AC-FR0231-01/02, AC-FR0232-01/02, AC-NFR0080-01/02 |
| `.tracks/runtime/release-evidence/v1/{candidate_sha}/{run_id}/blobs/{sha256}` | 脱敏 agent I/O/event slice content-addressed bytes | AC-FR0231-01/02, AC-FR0232-01, AC-NFR0080-01 |

### 4d. release-evidence CLI / CI 出口

| # | 出口 | 可观察字段/文本 | 关联 AC |
|:---|:---|:---|:---|
| 1 | `trac check release-evidence` | §2d 精确 text、exit 0/1/2 | AC-FR0232-01/02/03/04/05, AC-FR0233-03 |
| 2 | `trac check release-evidence --json` | status, reason_code, candidate_sha, run_id, backend, evidence_path, event_bounds | AC-FR0232-03/04, AC-NFR0080-01/02 |
| 3 | routine live pytest | 缺凭据 stdout/pytest reason 含 `LIVE_SKIPPED: missing <NAME>`，exit 0，无 bundle | AC-FR0233-01 |
| 4 | tag/release CI `release-evidence` job | live test `1 passed` + check `status=satisfied`；缺凭据/skip/check 非零均 job fail | AC-FR0233-02/03 |

## 5. IF Registry

每个 IF- 标识代表一个可独立实现的接口合同，是 test-plan §8「IF- 归属」列的唯一合法取值来源。design-trace validator（`check_design_trace`，FR-0140 扩展）校验 IF- 标识的**有效性**（非仅存在性）：test-plan §8 中出现的每个 IF- 标识必须在此注册表或 IF-004 §5 注册表中定义，未注册的标识判失败并指出位置。

IF- 标识的粒度对齐 architecture.md §1.1 增长轴（Devon 的实现 task 边界）：同一增长轴的扩展归为一个 IF- 标识，使 M-IMPL task 变绿子集划分可据此裁剪。

v0.4 已建立的 8 个 IF- 标识在 IF-004 §5 定义，合同不变、不可变、不可复用；此处列出 cross-reference 条目使 validator 可在当前版本注册表中解析（定义仍以 IF-004 §5 为准）。

### IF-MTEST-001 M-TEST 测试收集合同（承自 IF-004 §5，定义不变）

### IF-MTEST-002 M-TEST 测试执行合同（承自 IF-004 §5，定义不变）

### IF-SHIELD-001 Shield 测试编写合同（承自 IF-004 §5，定义不变）

### IF-TRACE-001 trace 需求追踪合同（承自 IF-004 §5，定义不变）

### IF-TRACE-002 trace 闭合检查合同（承自 IF-004 §5，定义不变）

### IF-REACH-001 reach 模块可达性合同（承自 IF-004 §5，定义不变）

### IF-REACH-002 reach 孤岛检查合同（承自 IF-004 §5，定义不变）

### IF-VALIDATE-001 trac validate 文档校验合同（承自 IF-004 §5，定义不变）

### IF-IMPL-001 M-IMPL kernel 状态机合同

- **合同**：M-IMPL kernel 状态机合同：§1a 事件 reducer + §1b Command 产出 + §1c State 字段 + SM-01 控制流（`_decide_m_impl`）+ StageDef 注册 + `_NEXT_STAGE` 接续（M-TEST->M-IMPL, M-IMPL->M-VERIFY boundary）+ §1i DIAGNOSE 四路路由分类。
- **实现模块**：kernel/machine.py, kernel/events.py, kernel/m_test.py（共享 helper）。
- **对应 §section**：§1a, §1b, §1c, §1i。
- **关联 FR**：FR-0010, FR-0020-02, FR-0030-02, FR-0040-01/02, FR-0050-01/02, FR-0060-01/02, FR-0070-01, FR-0080-02, FR-0090-02, FR-0140-02, FR-0150-01/02/04, FR-0160-02/03/04, NFR-0010, NFR-0020, NFR-0030。

### IF-IMPL-002 M-IMPL executor handler 合同

- **合同**：M-IMPL executor handler 合同：`_do_recompute_baseline` / `_do_validate_taskgraph` / `_do_check_island` / `_do_start_task` / `_do_classify_red` / `_do_create_red_checkpoint` / `_do_run_task_gates` / `_do_create_green_commit` / `_do_run_refactor_gate` / `_do_review_task` / `_do_check_island_2` / `_do_create_test_commit` 命令执行 + §1a 事件产出 + verdict.failed 携带 classification/target_stage/task_id + 判据包物化与回收（Prism dispatch 基础设施）+ dispatch 物化字段集（FR-0190）+ crash reconcile（FR-0200）。
- **实现模块**：executor/executor.py。
- **对应 §section**：§1a, §1e, §3f, §3g。
- **关联 FR**：FR-0020-01/03/04, FR-0030-01/03, FR-0040-01, FR-0050-01/04, FR-0060-01/02/03, FR-0070-01/03/04/05, FR-0080-01, FR-0090-01, FR-0100-01/02, FR-0110-01/02/03/04, FR-0120-01/02, FR-0130-01/02/03, FR-0140-01/03, FR-0150-01/02/03/04, FR-0160-01/02/03, FR-0190-01~10, FR-0200-01/02, NFR-0020, NFR-0030。

### IF-IMPL-003 task graph 解析与校验合同

- **合同**：`parse_tasks_json` / `validate_dag` / `validate_scope` / `validate_ac_coverage` / `validate_issue_numbers` 纯函数合同：解析 tasks.json（task graph 唯一机器真相），DAG 无环（Kahn's algorithm）、scope 边界不重叠（manifest 白名单交集为空，scope_boundary 是授权范围而非预声明输出文件集）、required AC 覆盖闭合（每个 required AC 至少被一个 task 的 ac_refs + if_ids 覆盖）+ IF- 标识有效性校验 + issue number 有效性校验（正整数 >=1）。
- **实现模块**：executor/taskgraph.py。
- **对应 §section**：§1d, §2a。
- **关联 FR**：FR-0030-02, FR-0180-01/02/04, FR-0210-01, FR-0220-01。

### IF-IMPL-004 RGR git 操作合同

- **合同**：`create_red_ref`（compare-and-set + R 不可变）+ `create_green_commit`（parent=B + trailers）+ `classify_red`（复用 v0.4 RedClass，M-IMPL 合法红 = assertion_failure/symbol_missing）+ `verify_lineage`（ref + trailer + 事件序列三件联合证明，不作 Git ancestry 断言）。
- **实现模块**：executor/rgr.py。
- **对应 §section**：§1f, §3d, §3e。
- **关联 FR**：FR-0070-03/04, FR-0080-01, FR-0120-01/02, FR-0200-02。

### IF-IMPL-005 质量门禁分层执行合同

- **合同**：`run_gates` / `run_production_checks` / `run_test_checks` 分层执行：GREEN_GATE targeted 单测 + 历史单测 + int 子集 + lint/format/type/static + 合同；REFACTOR_GATE 重跑 + 质量门禁分层（生产代码 ruff + flake8 CCR001 + pylint R0801/C0302/R0915/R0914；测试代码仅 R0801 + C0302）+ 反馈脱敏（int/e2e 失败只回分类诊断，不回断言原文）。
- **实现模块**：executor/quality_gate.py。
- **对应 §section**：§1g。
- **关联 FR**：FR-0110-01/02/03/04, FR-0130-01/02/03。

### IF-IMPL-006 三 worktree 方案合同

- **合同**：`create_devon_worktree`（从 C_design，Devon 天然不含 Shield tests）+ `create_test_authority_worktree`（Shield 冻结测试 frozen bundle）+ `create_gate_worktree`（组合 C_design + frozen bundle + Devon candidate diff）+ `cleanup_worktree`。frozen bundle 永不合入 Devon candidate（BS-04 时间隔离）。
- **实现模块**：executor/worktree.py。
- **对应 §section**：§1h。
- **关联 FR**：FR-0070-01/02/05, FR-0100-02, FR-0110-02, FR-0150-03。

### IF-IMPL-007 tasks.json/tasks.md 真相源合同

- **合同**：tasks.json 为 task graph 唯一机器真相（Runtime 解析驱动 DAG 调度），tasks.md 仅为人类可读投影由 Runtime 从 tasks.json 确定性生成、「当前完成了哪一步」进展投影入 events/db + `trac report` 可重建 per-task 进展 + `trac validate --file tasks.json` DAG/scope/AC 覆盖/IF- 有效性/issue number 有效性校验（与 IF-IMPL-003 协同）+ test-plan §8 测试归属边界（不复制到 tasks.json，FR-0210）。
- **实现模块**：executor/executor.py（tasks.md 生成）、executor/validate.py（tasks.json 校验）、tracks/templates/（模板）。
- **对应 §section**：§2a, §3c。
- **关联 FR**：FR-0030-03, FR-0180-01/02/03, FR-0210-01/02/03, FR-0220-01。

### IF-DEVON-001 Devon agent 接入与 manifest 越界审计合同

- **合同**：`AGENT_NAME["devon"] = "Devon"` + Devon.md deliverables 一致性（version + IQ frontmatter，无 permission 块 D-26）+ Auditor per-task manifest 白名单越界检测（越权写 -> `over_reach` failure_class -> git 回滚）+ Devon prompt/skill 物化与回收（与 Archer/Prism/Shield 同构）+ dispatch 物化字段（phase/manifest/r_tree_identity）。
- **实现模块**：effects/opencode.py, deliverables.py, effects/audit.py。
- **对应 §section**：§3a。
- **关联 FR**：FR-0070-02, FR-0100-01/02, FR-0170-01/02/03。

### IF-LIVE-001 真实 OpencodeBackend 旅程与 evidence 绑定合同

- **合同**：§1j `LiveEvidenceBundle`/`bind_live_evidence`/`write_live_evidence` + §3h canonical 存储；只接受 current candidate wheel 在隔离 demo host 上由真实 OpencodeBackend 产生的 Devon RED/GREEN/REFACTOR、真实 Prism review、Runtime gates、task completion、ISLAND_GATE_2 与 boundary，拒绝 fake/simulate/overlay/manual-event-only。
- **实现模块**：executor/live_evidence.py, executor/executor.py, effects/opencode.py, store/store.py；`tests/e2e_live` 为消费者而非实现模块。
- **对应 §section**：§1j, §3h, §4c/§4d。
- **关联 FR**：FR-0230-01/02/03, FR-0231-01/02, FR-0233-01/02, NFR-0080-01/02。

### IF-RELEASE-001 当前候选 release-evidence 检查合同

- **合同**：§2d `trac check release-evidence [--json]` 的 selection/reason/text/JSON/exit/read-only 合同；§3h bundle/blob 读取；current HEAD 严格等值，任何 stale/non-real/incomplete 均 fail closed。
- **实现模块**：checks/release_evidence.py, cli/main.py。
- **对应 §section**：§2d, §3h, §4c/§4d。
- **关联 FR**：FR-0230-03, FR-0231-01/02, FR-0232-01/02/03/04/05, FR-0233-02/03, NFR-0080-01/02。

**有效性校验机制**（design-trace validator 扩展，FR-0140，承自 IF-004 §5）：

- validator 解析本注册表（§5 各 `### IF-XXX-NNN` 标题）与 IF-004 §5 注册表，构建已定义 IF- 标识集合。
- validator 解析 test-plan §8 每条 integration/e2e AC 的「IF- 归属」列，提取逗号分隔的 IF- 标识。
- 每个提取的 IF- 标识必须在已定义集合中；未注册 -> 硬错误（`line:N <IF-id> not defined in interfaces.md §5`）。
- integration/e2e AC 缺 IF- 归属（空值）-> 硬错误（`line:N <AC-id> integration/e2e AC missing IF- attribution`）。
- unit AC 的 IF- 归属为 informational（validator 不校验 unit AC 的 IF- 有效性，但若填写仍须合法）。

IF- 标识一经分配不可变、不可复用（与 FR-0130 ID 不可变规则同构）；删除的 IF- 标识留 tombstone，不被 validator 计为已定义。v0.4 的 8 个 IF- 标识（IF-MTEST-001/002、IF-SHIELD-001、IF-TRACE-001/002、IF-REACH-001/002、IF-VALIDATE-001）在 IF-004 §5 定义，本节列出 cross-reference 条目供 validator 在当前版本注册表中解析；合同定义仍以 IF-004 §5 为准，v0.5 不重新定义。
