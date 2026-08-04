---
interfaces_id: IF-004
spec_ref: SPEC-004
arch_ref: ARCH-004
created: 2026-08-05
status: draft
sha:
---

# v0.4 - 接口与类型化 Schema

本文是 IF-003（v0.2）的增量延伸，并纳入 v0.3 M-DESIGN 既有事件/Command 事实。事件信封（IF-001 §2）、Command 基础结构（IF-001 §4）、State 投影（IF-001 §8）、Workflow 类型（IF-001 §9）等不变，凡未提及者继承 IF-001/IF-003。v0.4 扩范围新增 M-TEST 事件/Command/State 字段、需求追踪工具 CLI 合同与判据包绑定接口。

## 0. 延续性（什么不变）

- `EventEnvelope`（IF-001 §2）字段不变。
- `Command`（IF-001 §4）`kind` Literal 追加 4 个成员（§1b）；既有成员不变。
- `State`（IF-001 §8）追加 M-TEST 字段（§1c）；既有字段不变。
- `StageDef`/`Transition`/`Workflow`（IF-001 §9）不变；M-TEST 注册为 StageDef（initial_substate="DISPATCH"，无 drafting_role/doc/reviewer）。
- 封闭集仍用 `Literal`/`Enum`；`guard`/阶段名仍为受测字符串。
- `Assignment`（IF-003 §3）追加可选字段 `criteria_pack`（§3a）；既有字段不变。
- `Outcome`（IF-003 §4）追加可选字段 `criteria_pack`（§3b）；既有字段不变。
- `discuss/` 旁路类型与 CLI 合同（IF-003 §6/§7a）不变。
- `trac validate` CLI 合同（IF-003 §7b）不变；`trac check` 扩展为三子命令（§2）。
- 既有 `check_trace`（IF-003 §10d，FR-0170 acceptance AC<->FR）行为不变；新增 `check_trace_full`（§1d，FR-0080 全链）是独立函数。
- §5 IF- 标识注册表是 v0.4 新增（FR-0140 变绿条件基础）；IF-001/IF-003 未定义 IF- 标识注册表，v0.4 首次建立。IF-TRACE-001/002 与 IF-REACH-001/002 原仅作为桩代码 NotImplementedError token 存在，v0.4 在 §5 正式定义为接口合同标识。

## 1. 跨模块合同

### 1a. 新事件类型（EVENT_TYPES 追加，IF-MTEST-001 / IF-MTEST-002）

事件由 executor（IF-MTEST-002）产出、kernel reducer（IF-MTEST-001）消费；两者共同实现该跨模块合同。

**modules** 列标注实现/消费该事件的模块（跨模块接口 = 2+ 模块，Shield 须有 integration 覆盖）。

| 事件类型 | payload | 发出者 | modules | 备注 |
|:---|:---|:---|:---|:---|
| `test.collected` | `status: "passed"\|"failed"`, `collected_count: int`, `errors: list[str]` | executor（`_do_collect_tests`） | executor, kernel | SM-01.5/.6；passed -> PRISM_REVIEW，failed -> WRITE |
| `red.validated` | `status: "valid"\|"invalid"`, `findings: list[dict]` | executor（`_do_run_tests`） | executor, kernel | SM-01.9/.10；valid -> EXIT，invalid -> DIAGNOSE。findings 每项 `{test_id, classification, detail}` |
| `test.committed` | `commit_sha: str`, `test_count: int` | executor（`_do_commit_tests`） | executor, kernel | SM-01.14；冻结测试资产，exit 后 stage.exited(M-TEST) |

`prism.verdict` 事件（v0.3 既有，modules: executor, kernel）payload 追加可选字段 `criteria_pack: dict | None`（M-TEST PRISM_REVIEW 时填充，M-DESIGN 时为 None）。`verdict.failed` 事件（v0.1 既有，modules: executor, kernel）payload 的 `check` 字段取值集合扩展（见 §1e）。

### 1b. Command kind 增量（COMMAND_KINDS 追加，IF-MTEST-001 / IF-MTEST-002）

命令由 kernel `_decide_m_test`（IF-MTEST-001）产出、executor handler（IF-MTEST-002）执行；两者共同实现该跨模块合同。

```python
kind: Literal[...,               # IF-001/IF-003 原有成员不变
      "collect_tests",           # SM-01.5：Runtime 独立执行 collection
      "run_tests",               # SM-01.9：Runtime 独立复跑 integration/e2e
      "check_trace",             # SM-01.14：Runtime 在 EXIT 调 trac check trace
      "commit_tests"]            # SM-01.14：Runtime 创建受控测试 commit
```

`dispatch_agent` / `validate_document` / `commit_document` / `rollback_stage` / `write_frontmatter` 复用不变；新 kind 同样遵守写前日志（FR-30）+ 每 kind execute/reconcile（D-13）。

### 1c. State 字段增量（IF-001 §8 追加，IF-MTEST-001）

```python
# M-TEST (SM-01, FR-0010~0070)
m_test_attempt: int = 0           # WRITE/PRISM_REVIEW/EXIT 复跑共享 <=3 预算
test_collected: bool = False      # COLLECT 成功
red_validated: bool = False       # RED_CHECK 全部合法 Red
red_findings: list | None = None  # RED_CHECK 分类结果（每条失败的 classification）
trace_passed: bool = False        # EXIT trace 闭合
test_committed: bool = False      # 测试资产已冻结
criteria_pack_loaded: dict | None = None  # Prism 实际加载的判据包 identity
diagnose_classification: str | None = None  # DIAGNOSE 路由分类
```

- `stage` 取值追加 `M-TEST`。
- M-TEST substate 封闭集：`DISPATCH | WRITE | COLLECT | PRISM_REVIEW | RED_CHECK | EXIT | DIAGNOSE`（SM-01）。
- `_STAGES` 增补 `StageDef(stage="M-TEST", initial_substate="DISPATCH")`（无 drafting_role/doc/reviewer--M-TEST 的 Shield/Prism dispatch 由 `_decide_m_test` 显式控制）。
- `_NEXT_STAGE` 增补 `"M-DESIGN": "M-TEST"`。`"M-TEST"` 不在表中 -> `run.completed(terminal_state="boundary")`（M-IMPL 未注册）。

### 1d. checks/trace.py 纯函数（FR-0080，IF-TRACE-001 / IF-TRACE-002）

> **Prism [RESOLVED]:** BLOCKER-001 [severity=blocker, artifact=interfaces.md, anchor=§1d/§1f/§1g/§1h 行74/123/154/182, 关联=M-DESIGN 退出收敛门禁]：interfaces.md 四处跨模块接口标注使用 `> modules: ...` blockquote 格式（§1d 行74、§1f 行123、§1g 行154、§1h 行182）。inline-discussion parser 将 `> modules:` 解析为 speaker='modules' 的 open 根评论（当前 T-001~T-004），导致 `trac discuss query --check-ready` 报 is_ready=false、ready_blockers=[T-001,T-002,T-003,T-004]，阻塞 M-DESIGN 退出收敛校验。这些 blockquote 是 Archer 的跨模块接口标注（非真实讨论），但格式 `> <ASCII-identifier>:` 与讨论协议 `> Speaker: body` 冲突（tracks-discuz SKILL：`> Name: body` Name 为 ASCII identifier 即被识别为线程）。Prism 无法自行 resolve（set-status resolved 要求 operator==initiator='modules'，冒充违反协议）。预期修订：Archer 将四处 `> modules:` 标注重排为 parser 不识别的格式——去掉 `>` 改为普通段落（如 `**modules**: ...`），或加说明标签前缀（`> Note: modules: ...`，parser 不识别 Note/Warning/Tip 说明标签），清除 4 个 false-positive open 线程，使 is_ready=true。
>> **Archer:** 已修订：将 interfaces.md §1d/§1f/§1g/§1h 四处 `> modules:` blockquote 标注（及 §1a 表前说明 `> modules 列...`）全部重排为普通段落 `**modules**: ...`（去掉 `>` 前缀）。重排后 `trac discuss query` 已确认原 T-002~T-005 四个 false-positive open 线程消失，仅剩本线程 T-001 待 Prism resolve。重排不改变跨模块接口标注的语义内容（modules 列仍标注实现/消费方，Shield 仍据此识别 integration 覆盖义务），只调整格式使 parser 不再误识别为讨论线程。

**modules**: checks/trace.py（实现）、cli/main.py（消费）、executor/executor.py（消费，M-TEST EXIT 门禁）--跨模块接口，须有 integration 覆盖。

```python
@dataclass(frozen=True)
class TraceReport:
    status: Literal["pass", "fail"]
    hard_errors: tuple[str, ...]   # 含 line:N 的完整孤儿清单（不短路）
    warnings: tuple[str, ...]      # BS->FR 弱链接 warning

def check_trace_full(
    story_text: str, spec_text: str, acc_text: str,
    test_markers: dict[str, list[str]],  # {ac_id: [test_file_path, ...]}
) -> TraceReport:
    """FR-0080 BS->FR->AC->test 全链双向孤儿检测（纯函数，无 I/O）。
    - FR<->AC 硬错误：spec FR 无 acceptance AC、acceptance AC 回指不存在 FR。
    - AC<->test 硬错误：AC 无长格式 marker、marker 指向不存在 AC。
    - BS->FR warning：BS 无 FR 承接（不改变退出码）。
    - 短格式 marker（缺 @version）-> 硬错误。
    - 重复 FR/AC ID -> 硬错误（指出冲突双方 line:N）。
    - tombstone ID 不计为孤儿。
    完整清单不短路；顺序稳定可复现（NFR-0020）。"""

def check_trace_full_file(
    version_dir: Path, tests_dir: Path, baseline: dict | None = None,
) -> TraceReport:
    """FR-0080 文件读取包装：从 version_dir 读 story/spec/acceptance，
    从 tests_dir 扫描 .py 文件中的长格式 marker，调用 check_trace_full。
    baseline 为存量基线豁免（FR-0100），被列入基线的 ID/文档不计孤儿。"""
```

`check_trace_full` 复用 `executor/validate.py` 的 `_spec_items`/`_acc_scan` 扫描助手（import，不重复实现）。新增 BS 扫描（`### BS-XX` heading regex）与 test marker 扫描（`AC-(N?FR)\d{4}-\d{2}@(v\d+\.\d+)` regex）。

### 1e. verdict.failed check 字段扩展

`verdict.failed` 事件 payload 的 `check` 字段取值集合追加 M-TEST 特有成员：

```python
check: Literal[...,  # 既有：schema/scope/trace/scope_overflow/format/template/discussion_ready
       "criteria_pack_mismatch",  # M-TEST PRISM_REVIEW 反自述回读失败
       "test_defect",             # M-TEST DIAGNOSE 路由：测试缺陷
       "stub_gap",                # M-TEST DIAGNOSE 路由：桩/接口缺口
       "ac_gap",                  # M-TEST DIAGNOSE 路由：AC 缺口
       "spec_gap"]                # M-TEST DIAGNOSE 路由：Spec 缺口
```

`verdict.failed(test_defect|stub_gap|ac_gap|spec_gap)` 事件 payload 另含 `target_stage: str`（M-DESIGN/M-ACC/M-SPEC）与 `artifact_disposition: str`（FR-0060）。

### 1f. checks/reach.py 纯函数（FR-0090，IF-REACH-001 / IF-REACH-002）

**modules**: checks/reach.py（实现）、cli/main.py（消费）--跨模块接口，须有 integration 覆盖。

```python
@dataclass(frozen=True)
class ReachReport:
    status: Literal["pass", "fail"]
    islands: tuple[str, ...]       # 不可达生产模块名（排序稳定）
    entrypoints: tuple[str, ...]   # 发现的入口模块名
    errors: tuple[str, ...]        # 无入口声明等错误

def check_reach(
    entrypoints: list[str],          # 入口模块名（如 ["tracks.cli.main"]）
    import_graph: dict[str, set[str]],  # {module: {imported_module, ...}}
    production_modules: set[str],    # 生产模块集合（排除 tests/）
    baseline: dict | None = None,    # 存量基线豁免模块
) -> ReachReport:
    """FR-0090 模块级 import 图孤岛检测（纯函数，无 I/O）。
    从 entrypoints 做 BFS，不可达的生产模块为孤岛。
    无入口声明 -> errors 非空、status=fail。
    baseline 中的模块不计孤岛。顺序稳定可复现（NFR-0020）。"""

def check_reach_file(
    repo: Path, baseline: dict | None = None,
) -> ReachReport:
    """FR-0090 文件读取包装：解析 pyproject.toml [project.scripts]、
    扫描包目录 __main__.py、读取 .tracks/reach-entries.txt 白名单，
    ast 解析 .py 文件构建 import 图，调用 check_reach。"""
```

### 1g. Red 分类封闭集（FR-0050，IF-MTEST-002）

**modules**: executor/executor.py（实现 `_do_run_tests` 内分类）、kernel/machine.py（消费 `red.validated` 事件）--跨模块接口，须有 integration 覆盖。

```python
RedClass = Literal[
    "assertion_failure",     # 行为断言失败（合法 Red）
    "stub_token_failure",    # NotImplementedError("IF-...") 桩合同 token 失败（合法 Red）
    "symbol_missing",        # 合同声明的 symbol 缺失（合法 Red）
    "collection_error",      # collection/语法/fixture/import 错误（非法 Red）
    "unexpected_pass",       # 测试意外通过（非法 Red）
    "unclassified",          # 未分类（非法 Red，进 DIAGNOSE）
]

def classify_red(
    test_id: str, returncode: int, stdout: str, stderr: str,
) -> RedClass:
    """FR-0050 将单条测试失败分类为合法/非法 Red（纯函数）。
    基于 returncode + 关键字匹配（不依赖完整 traceback 解析）：
    - stderr 含 NotImplementedError("IF- -> stub_token_failure
    - stderr 含 AssertionError/assert -> assertion_failure
    - stderr 含 ImportError/ModuleNotFoundError/SyntaxError/FixtureLookupError
      /collection error -> collection_error
    - returncode == 0 且测试本应失败 -> unexpected_pass
    - 其余 -> unclassified
    """
```

### 1h. 存量基线 schema（FR-0100，IF-TRACE-002 / IF-REACH-002）

**modules**: checks/trace.py + checks/reach.py（读取）、cli/main.py（CLI 传递）、`.tracks/legacy-baseline.json`（存储）--跨模块接口，须有 integration 覆盖。

```python
# .tracks/legacy-baseline.json（由 trac init adoption 声明，Archer 设计 schema）
@dataclass(frozen=True)
class LegacyBaseline:
    adopted_at: str                    # ISO date（采纳时刻）
    version: str                       # 采纳时的 tracks 版本
    trace_exemptions: dict             # {"documents": [path...], "ids": [id...]}
    reach_exemptions: dict             # {"modules": [module...]}
```

基线只冻结采纳时刻的存量、不回填历史（AC-FR0100-03）。基线之后新增的同形内容仍正常报错（AC-FR0100-04）。`trac check trace` / `trac check reach` 读取 `.tracks/legacy-baseline.json`（若存在），被列入基线的 ID/文档/模块不计孤儿/孤岛。

## 2. CLI 接口合同

### 2a. `trac check`（扩展为三子命令）

IF-003 §7b 既有 `trac check deliverables` 不变。新增：

| 命令 | 输入 | 前置校验 | 成功输出 | 失败输出 | exit |
|:---|:---|:---|:---|:---|:---|
| `trac check trace [--json] [--version <ver>]` | 当前 repo；`--version` 缺省取最新 | `.tracks/projects/<ver>/` 存在 story/spec/acceptance | stdout: 人类可读孤儿清单（含 line:N）或 `trace ok`；`--json`: `TraceReport` JSON | stderr: 错误原因 | 0=无硬错误 / 1=有硬错误 |
| `trac check reach [--json] [--entry <module>]...` | 当前 repo | pyproject.toml 或 `__main__.py` 或 `--entry` 存在 | stdout: 人类可读孤岛清单或 `reach ok`；`--json`: `ReachReport` JSON | stderr: 错误原因 | 0=无孤岛 / 1=有孤岛或无入口 |

- `trac check trace` 默认检查最新版本目录；`--version v0.4` 指定版本。测试 marker 从 `tests/` 目录扫描（所有版本共存于同一棵 tests/ 树，marker 长格式 `AC-FRXXXX-YY@<version>` 定位 AC 所属版本）。
- `trac check trace --json` 输出 `{"status":"fail","hard_errors":[...],"warnings":[...]}`（AC-FR0110-01/.02）。
- `trac check reach` 默认从 `pyproject.toml` `[project.scripts]` + 包目录 `__main__.py` 发现入口；`--entry tracks.cli.main` 显式追加入口。
- 两个工具只报告不改写（NFR-0010）；同一输入多次运行输出完全一致（NFR-0020）。
- CLI 与引擎双消费者：人类经默认格式读证据；引擎调用读取 `--json` 与退出码（AC-FR0110-03）。

### 2b. `trac validate`（扩展校验范围，IF-VALIDATE-001）

IF-003 §7b 既有 `trac validate --file <path>` 不变。v0.4 扩展：

- `trac validate --file story.md`：新增 BS-XX 文法校验（`### BS-XX` 两位编号、唯一 ID、不可变/tombstone 规则）。既有行为不回归。
- `trac validate --file test-plan.md`：`check_design_trace` 扩展为校验每条 integration/e2e 的 IF- 归属（FR-0140）。缺 IF- 归属的 integration/e2e AC 判失败并指出位置。
- 三模板（story/spec/acceptance）增补 `@version` 跨版本引用与 tombstone 规则的文法指引；`trac validate` 校验限定引用解析失败（NOT_FOUND）。

## 3. 文件 / 存储契约

### 3a. Assignment 增量（IF-MTEST-001 / IF-SHIELD-001）

`Assignment`（IF-003 §3）追加可选字段：

```python
criteria_pack: dict | None = None  # M-TEST PRISM_REVIEW 时由 Runtime 填入
                                   # {"name": "test-asset-criteria", "version": "0.1"}
                                   # Prism 不自选，按 assignment 加载（D-29 反自述三件套①）
```

Shield assignment 不携带 `criteria_pack`（仅 Prism 在 M-TEST PRISM_REVIEW 时携带）。Shield assignment 携带 `skills: ["tracks-discuz"]` 与 `docs: ["test-plan", "interfaces", "acceptance"]`（只读上下文，非目标文档）。

### 3b. Outcome 增量

`Outcome`（IF-003 §4）追加可选字段：

```python
criteria_pack: dict | None = None  # Prism verdict outcome 携带实际加载判据包 identity
                                   # {"name": "test-asset-criteria", "version": "0.1"}
                                   # Runtime 回读核对（D-29 反自述三件套②③）
```

### 3c. 路径契约

| 路径 | 格式 | 写入者 | 读取者 |
|:---|:---|:---|:---|
| `tracks/checks/trace.py` | Python 模块 | Devon（实现） | cli/cmd_check、executor/_do_check_trace、tests |
| `tracks/checks/reach.py` | Python 模块 | Devon（实现） | cli/cmd_check、tests |
| `tracks/skills/test-asset-criteria/SKILL.md` | opencode skill（frontmatter + body） | 维护者（spec 交付物） | OpencodeBackend（M-TEST PRISM_REVIEW 物化到 Prism 上下文） |
| `tracks/agents/Shield.md` | opencode agent（frontmatter + body） | 维护者（spec 交付物） | OpencodeBackend（物化源）、check_deliverables |
| `.tracks/legacy-baseline.json` | JSON | `trac init` adoption（Human 声明） | checks/trace.py、checks/reach.py |
| `.tracks/reach-entries.txt` | 纯文本（每行一个模块名） | Human（可选白名单） | checks/reach.py |
| `tests/integration/*.py` | pytest 测试 | Shield（M-TEST WRITE） | executor/_do_collect_tests、_do_run_tests、trac check trace（marker 扫描） |
| `tests/e2e/*.py` | pytest 测试 | Shield（M-TEST WRITE） | 同上 |
| `tests/counterexamples/*.patch` | git patch + manifest | Shield（M-TEST WRITE） | Shield 自检（counterexample killed） |
| `<repo>/.opencode/skills/test-asset-criteria/SKILL.md` | 物化的 skill（瞬态） | OpencodeBackend | opencode（Prism 加载）；终态清理 |

### 3d. 测试 marker 格式（FR-0080/FR-0130）

测试文件中 marker 强制长格式 `AC-FRXXXX-YY@<version>`（代码不按版本分目录，所有版本测试共存于同一棵 tests/ 树）：

- marker 位于测试函数 docstring/注释首行（test-plan §1.4 AC mandatory tracing）。
- 长格式 regex：`AC-(N?FR)\d{4}-\d{2}@v\d+\.\d+`（如 `AC-FR0010-01@v0.4`）。
- 短格式（缺 `@<version>`）触发 trace 检查失败并指出位置（AC-FR0080-06）。
- 长格式 marker 引用不存在的 AC -> 显式报错 NOT_FOUND，不静默回退（AC-FR0080-07）。
- 文档中短格式与长格式均允许（短格式 opt-in 消歧）；测试中只允许长格式（不对称约束，AC-FR0130-04）。

### 3e. tombstone 规则（FR-0130）

ID 一经分配不可变、不可复用；删除的 ID 留 tombstone：

- story/spec/acceptance 中删除的 BS/FR/NFR/AC 编号以 tombstone 标记（HTML 注释 `<!-- tombstone: FR-XXXX -->` 或 frontmatter `tombstone` 字段，实现层定）。
- tombstone ID 不被 trace 计为孤儿（AC-FR0080-09）。
- 事件日志与历史证据会引用旧 ID，故 tombstone 保留可追溯性。

## 4. 可观察出口（测试断言基础）

test-plan 的断言只能落在以下外部可观察出口（§6.5 闭环）：

### 4a. 事件出口（events 表）

| 事件 | 可观察字段 | 关联 AC |
|:---|:---|:---|
| `stage.entered(M-TEST)` | stage | AC-FR0010-01/02 |
| `test.collected` | status, collected_count, errors | AC-FR0030-01/02/03 |
| `prism.verdict` (M-TEST) | verdict, criteria_pack, diff_ref | AC-FR0040-02/03 |
| `red.validated` | status, findings[].classification | AC-FR0050-01~05 |
| `verdict.failed` (M-TEST) | check(test_defect\|stub_gap\|ac_gap\|spec_gap\|criteria_pack_mismatch\|trace), target_stage | AC-FR0060-02~06, AC-FR0040-02, AC-FR0070-04 |
| `test.committed` | commit_sha, test_count | AC-FR0070-07 |
| `stage.exited(M-TEST)` | stage | AC-FR0070-07/08 |
| `run.completed` | terminal_state="boundary" | AC-FR0070-08 |
| `stage.rolled_back` | from_stage, to_stage, reason | AC-FR0060-03/04/05 |
| `outcome.received` (shield) | role, status, audit_evidence, failure_class | AC-FR0020-04/05, AC-FR0120-04 |
| `command.issued` (dispatch_agent shield/prism) | command.params.role, .substate, .assignment.criteria_pack | AC-FR0020-01, AC-FR0040-02 |

### 4b. CLI 出口

| 命令 | 可观察输出 | 关联 AC |
|:---|:---|:---|
| `trac check trace` | stdout/stderr（孤儿清单 + line:N）、`--json` TraceReport、exit code | AC-FR0080-01~11, AC-FR0110-01~03 |
| `trac check reach` | stdout/stderr（孤岛清单）、`--json` ReachReport、exit code | AC-FR0090-01~06, AC-FR0110-01~03 |
| `trac check deliverables` | stdout/stderr、exit code | AC-FR0120-02 |
| `trac status` | stdout（stage=M-TEST, substate=DISPATCH/WRITE/...） | AC-FR0010-02/05 |
| `trac validate --file story.md` | stdout/stderr（BS-XX 文法校验）、exit code | AC-FR0130-01/05/06 |
| `trac validate --file test-plan.md` | stdout/stderr（IF- 归属校验）、exit code | AC-FR0140-02/03 |

### 4c. 文件出口

| 路径 | 可观察内容 | 关联 AC |
|:---|:---|:---|
| `tests/integration/*.py`, `tests/e2e/*.py` | 文件存在、可 collect、测试 marker 长格式 | AC-FR0020-02/03, AC-FR0080-06 |
| `tests/counterexamples/*.patch` | patch 文件 + kill manifest | AC-FR0020-05 |
| `.tracks/legacy-baseline.json` | JSON schema 合规、豁免清单 | AC-FR0100-01/02 |
| git log | 受控测试 commit（test.committed 对应） | AC-FR0070-07 |
| git 工作区 | 工具运行前后无文件变化（NFR-0010） | AC-NFR0010-01 |
| `tracks/agents/Shield.md` | frontmatter version + IQ | AC-FR0120-02 |
| `tracks/skills/test-asset-criteria/SKILL.md` | frontmatter name + version；内容为语义判据 | AC-FR0040-05/06 |

## 5. IF- 标识定义（变绿条件基础，FR-0140）

每个 IF- 标识代表一个可独立实现的接口合同，是 test-plan §8「IF- 归属」列的唯一合法取值来源。design-trace validator（`check_design_trace`，FR-0140 扩展）校验 IF- 标识的**有效性**（非仅存在性）：test-plan §8 中出现的每个 IF- 标识必须在此注册表中定义，未注册的标识判失败并指出位置。

IF- 标识的粒度对齐 architecture.md §1.1 增长轴（Devon 的实现 task 边界）：同一增长轴的扩展归为一个 IF- 标识，使 M-IMPL task 变绿子集划分可据此裁剪。

| # | IF- 标识 | 合同（实现什么） | 实现模块 | 对应 §section | 关联 FR |
|:---|:---|:---|:---|:---|:---|
| 1 | IF-MTEST-001 | M-TEST kernel 状态机合同：§1a 事件 reducer + §1b Command 产出 + §1c State 字段 + SM-01 控制流（`_decide_m_test`）+ StageDef 注册 + `_NEXT_STAGE` 接续 | kernel/machine.py, kernel/events.py | §1a, §1b, §1c | FR-0010, FR-0020-04, FR-0030-02, FR-0040-01/02/03/04, FR-0050-05, FR-0060-01/02/03/04/05/07, FR-0070-05/06, NFR-0030, NFR-0040 |
| 2 | IF-MTEST-002 | M-TEST executor handler 合同：`_do_collect_tests` / `_do_run_tests` / `_do_check_trace` / `_do_commit_tests` 命令执行 + §1a 事件产出 + §1g Red 分类（`classify_red`）+ verdict.failed 携带 classification/target_stage/artifact_disposition + 判据包物化与回收（Prism dispatch 基础设施） | executor/executor.py | §1a, §1g | FR-0020-01/03, FR-0030-01/03, FR-0040-01/02/05/06, FR-0050-01/02/03/04/06, FR-0060-06, FR-0070-01/02/03/04/07/08, NFR-0040 |
| 3 | IF-SHIELD-001 | Shield agent 接入与写范围审计合同：`AGENT_NAME["shield"]` + Shield.md deliverables 一致性 + Auditor 写范围（四目录 allowed）+ 越权写 git 回滚（`over_reach`） | effects/opencode.py, deliverables.py | §3a | FR-0020-01/02/03/05, FR-0120-01~06 |
| 4 | IF-TRACE-001 | `check_trace_full` 纯函数合同：BS->FR->AC->test 全链双向孤儿检测（纯函数，无 I/O） | checks/trace.py | §1d | FR-0080-01~09 |
| 5 | IF-TRACE-002 | `check_trace_full_file` 文件包装合同 + §1h LegacyBaseline 读取：从 version_dir/tests_dir 读文档与 marker，调用 IF-TRACE-001，应用基线豁免 | checks/trace.py | §1d, §1h | FR-0080-01/10/11, FR-0100, FR-0110, NFR-0010, NFR-0020 |
| 6 | IF-REACH-001 | `check_reach` 纯函数合同：模块级 import 图 BFS 孤岛检测（纯函数，无 I/O） | checks/reach.py | §1f | FR-0090-01~06 |
| 7 | IF-REACH-002 | `check_reach_file` 文件包装合同 + §1h LegacyBaseline 读取：解析 pyproject/scripts + ast import 图，调用 IF-REACH-001，应用基线豁免 | checks/reach.py | §1f, §1h | FR-0090-01, FR-0100, FR-0110, NFR-0010, NFR-0020 |
| 8 | IF-VALIDATE-001 | validate.py M-TEST 扩展合同：`check_design_trace` IF- 归属校验扩展（FR-0140）+ `check_story_items` BS-XX 文法 / `@version` 跨版本引用 / tombstone 规则（FR-0130）+ test-plan 模板变绿条件字段 | executor/validate.py, tracks/templates/ | §2b | FR-0130-01~06, FR-0140-01~04 |

**有效性校验机制**（design-trace validator 扩展，FR-0140）：

- validator 解析本注册表（§5 表格第 2 列 `IF- 标识`），构建已定义 IF- 标识集合。
- validator 解析 test-plan §8 每条 integration/e2e AC 的「IF- 归属」列，提取逗号分隔的 IF- 标识。
- 每个提取的 IF- 标识必须在已定义集合中；未注册 -> 硬错误（`line:N <IF-id> not defined in interfaces.md §5`）。
- integration/e2e AC 缺 IF- 归属（空值）-> 硬错误（`line:N <AC-id> integration/e2e AC missing IF- attribution`）。
- unit AC 的 IF- 归属为 informational（validator 不校验 unit AC 的 IF- 有效性，但若填写仍须合法）。

IF- 标识一经分配不可变、不可复用（与 FR-0130 ID 不可变规则同构）；删除的 IF- 标识留 tombstone，不被 validator 计为已定义。
