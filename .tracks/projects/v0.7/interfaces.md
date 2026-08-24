---
interfaces_id: IF-007
spec_ref: SPEC-007
arch_ref: ARCH-007
created: 2026-08-24
status: draft
sha:
---

# v0.7-A — 接口与类型化 Schema

## 0. 延续性（什么不变）

- IF-001/003/004/005/006 的 `EventEnvelope`、`Command`、Assignment/Outcome、State 投影、D-41 selection/evidence/ledger/FULL、hotfix、doc-gap 与 CLI 合同全部继承；本版只追加成员。
- 既有 `test.baseline_captured`、`test.selected`、`red.validated`、`full.executed`、`evidence.*` 与 `.tracks/projects/project.toml` 三层命令合同不变。
- 既有 IF 标识不可重定义；§5 重列 inherited headings 供 validator 解析，正文定义仍以 IF-006 及其上游文档为准。
- feature/hotfix 的 test-plan trace 与版本解析不变；v0.7 candidate-bound 是 `--version v0.7` 的追加闭环。
- 外部 CLI 顶层命令集合不变；本版只扩展现有命令输出/校验。

## 1. 跨模块合同

### 1a. 新事件类型

事件类型是封闭集；payload 未列字段不得作为通过证据。`modules` 含 2+ 模块的行必须有 integration 覆盖。

| # | event | payload | producer | modules |
|:--|:--|:--|:--|:--|
| 1 | `phase0.baseline_repaired` | `ac: str`, `bound_node: {node_id,digest}`, `selection_id: str`, `evidence_id: str`, `command_echo: list[str]`, `status: "repaired"` | phase0 executor | executor/phase0, adapters, kernel/phase0, report |
| 2 | `phase0.coverage` | `status: "passed"\|"failed"`, `ratio: float`, `threshold: float`, `by: "collected"`, `exclude: "none"`, `excluded_sources: []`, `outcomes_ref: str` | phase0 executor | executor/phase0, guard_registry, kernel/phase0, report |
| 3 | `phase0.guard_hardened` | `status: "passed"\|"blocked"`, `violations: int`, `revised: int`, `guard_evidence_refs: list[str]`, `parity_event_seq: int` | phase0 executor | guard_registry, kernel/phase0, report |
| 4 | `phase0.sealed` | `baseline_version: "v0.6"`, `seal_id: str`, `seal_manifest_blob: str`, `marks: "registered"`, `env_contract: "pass"`, `frozen_tests_blob: str` | phase0 executor | executor/phase0, kernel/phase0, m_test, m_impl, report |
| 5 | `phase0.blocked` | `reason: Phase0BlockReason`, `detail: str`, `recoverable: bool` | phase0 executor | executor/phase0, kernel/phase0, cli, report |
| 6 | `guard.parity` | `status: "passed"\|"blocked"`, `registry: str`, `runtime: "match"\|"mismatch"`, `pre_commit: same`, `ci: same`, `mismatches: list[GuardMismatch]`, `evidence_refs: list[str]` | guard executor | executor/guard_registry, kernel/phase0, cli, report |
| 7 | `authenticity.judged` | `ac: str`, `if_refs: list[str]`, `category: "new"\|"existing"`, `baseline: str`, `nodes: list[str]`, `selection_id: str`, `evidence_id: str`, `red: "legal"\|"illegal"\|"none"`, `green: "allowed"\|null`, `counterexample_kill: "verified"\|"missing"\|"none"`, `counterexample_ref: str\|null`, `unrelated: list[UnrelatedFailure]`, `status: "passed"\|"blocked"`, `attempt: int`, `actor: str` | RED_CHECK executor | executor/authenticity, adapters, kernel/m_test, report |
| 8 | `mutation.manifest` | `manifest_digest: str`, `manifest_blob: str`, `status: "declared"\|"blocked"`, `reason: MutationBlockReason\|null` | mutation executor | executor/mutation, kernel/m_test, report |
| 9 | `mutation.experiment` | `manifest_digest: str`, `status: "passed"\|"blocked"`, `baseline: str`, `apply: str`, `target_kill: str`, `controls: str`, `rollback: str`, `command_echo: list[list[str]]`, `env_fingerprint: str`, `node_results_ref: str\|null`, `failure_signatures: list[str]`, `digests: dict` | mutation executor | executor/mutation, adapters, worktree, kernel/m_test, report |
| 10 | `demo.equivalence` | `host: "demo-pytest"`, `equivalent: bool`, `install_method: "wheel"`, `wheel_sha256: str`, `import_path: str`, `venv: "fresh"`, `architecture_path: ".tracks/projects/v0.1/architecture.md"`, `registry_digest: str`, `hooks_path: ".githooks"`, `ci_binding: "declared"`, `adapter: "reference-pytest"`, `checks: list[str]` | demo executor | executor/demo_host, guard_registry, adapters, cli, report |
| 11 | `failclosed.demonstrated` | `host: "tracks"\|"demo-pytest"`, `scenario: FailClosedScenario`, `outcome: "blocked"\|"leaked"`, `evidence_ref: str` | demo executor | executor/demo_host, authenticity, mutation, guard_registry, report |
| 12 | `failclosed.summary` | `host: "tracks"\|"demo-pytest"`, `scenarios_count: 9`, `all_fail_closed: bool`, `crash_recovery: "replay_ok"\|"failed"`, `status: "passed"\|"blocked"` | demo executor | executor/demo_host, kernel/machine, cli, report |

```python
Phase0BlockReason = Literal[
    "node_missing", "node_uncollectable", "identity_unrecoverable",
    "coverage_below_threshold", "coverage_exclusion", "guard_violation",
    "guard_parity", "marks_missing", "environment_contract",
]

MutationBlockReason = Literal[
    "malformed_manifest", "multi_ac", "no_selectable_diff_identity",
    "tests_in_scope", "wrong_candidate", "stale_patch", "uncollected_node",
    "target_survived", "control_hit", "malformed_result", "rollback_dirty",
]

FailClosedScenario = Literal[
    "broad_mutation", "stale_patch", "wrong_candidate", "uncollected_node",
    "unrelated_red", "target_survived", "control_hit",
    "malformed_adapter_result", "guard_parity_mismatch",
]
```

新事件全部 append-only。`phase0.sealed`、`mutation.experiment(passed)`、`failclosed.summary(passed)` 不得在所引用 blob 缺失时产生。

### 1b. Command kind 追加

| # | kind | params | executor result | modules |
|:--|:--|:--|:--|:--|
| 1 | `phase0_validate` | `baseline_version: "v0.6"`, `candidate_version: "v0.7"` | 一组 `phase0.*`；最终 sealed 或 blocked | kernel/phase0, executor/phase0, adapters, guard_registry |
| 2 | `check_guard_parity` | `architecture_path: str`, `repo: str` | 一个 `guard.parity` | kernel/machine, executor/guard_registry |
| 3 | `mutation_verify` | `manifest_digest: str`, `manifest_blob: str` | `mutation.manifest` + `mutation.experiment` | kernel/m_test, executor/mutation, adapters, worktree |
| 4 | `demonstrate_failclosed` | `hosts: ["tracks","demo-pytest"]` | `demo.equivalence` + 18 detail + 2 summary events | kernel/machine, executor/demo_host, mutation, authenticity, guard_registry |

所有 kind 遵守 command.issued WAL 与 per-kind reconcile。phase0、manifest、scenario 的 digest 是幂等 key。

### 1c. State 投影追加

```python
phase0_status: str | None = None          # UNSEALED | PHASE0_VALIDATING | SEALED | BLOCKED
phase0_seal_id: str | None = None
phase0_blocked_reason: str | None = None
guard_registry_digest: str | None = None
guard_parity_status: str | None = None    # passed | blocked
authenticity_blocked: bool = False
active_mutation_manifest: str | None = None
demo_equivalent: bool | None = None
failclosed_hosts_passed: tuple[str, ...] = ()
```

`phase0_status` 只允许 `UNSEALED→PHASE0_VALIDATING→SEALED|BLOCKED`、`PHASE0_VALIDATING→PHASE0_VALIDATING`。SEALED 不可回退；BLOCKED 只经 Human 修复仓库事实后新一次 `phase0_validate` 回 PHASE0_VALIDATING，不改写历史事件。

### 1d. Phase 0 公共函数（IF-PHASE-001/002/003）

**modules**: `executor/phase0.py` 实现，`executor/executor.py` handler 消费，adapter/guard_registry 提供执行结果，`kernel/phase0.py` 投影。

```python
@dataclass(frozen=True)
class TraceGap:
    ac: str
    reason: Literal["marker_only", "node_missing", "identity_unrecoverable"]
    planned_node_id: str | None

@dataclass(frozen=True)
class CoverageJudgement:
    passed: bool
    ratio: float
    threshold: float
    excluded_sources: tuple[str, ...]
    reason: str | None

@dataclass(frozen=True)
class SealManifest:
    baseline_version: str
    document_digests: Mapping[str, str]
    frozen_test_digests: Mapping[str, str]
    marks: tuple[str, ...]
    environment_contract_digest: str
    seal_id: str

def scan_trace_gaps(
    baseline_version: str,
    approved_acs: Iterable[str],
    planned_bindings: Mapping[str, str],
    collected_node_digests: Mapping[str, str],
    persisted_evidence: Mapping[str, str],
) -> list[TraceGap]: ...

def judge_real_coverage(
    ratio: float, threshold: float, excluded_sources: Sequence[str]
) -> CoverageJudgement: ...

def build_seal_manifest(
    baseline_version: str,
    document_digests: Mapping[str, str],
    frozen_test_digests: Mapping[str, str],
    marks: Sequence[str],
    environment_contract_digest: str,
) -> SealManifest: ...
```

`scan_trace_gaps` 必须把 marker-only 与真实 evidence 分开；计划 node 不在 actual collect inventory 时不得生成 repaired。`judge_real_coverage` 只有 ratio≥threshold 且 source exclusion 为空才 passed。`seal_id=sha256(canonical_json(其余字段))`。

### 1e. Guard registry 与 parity（IF-GUARD-001/002）

**modules**: `executor/guard_registry.py` 实现，validate/phase0/demo/CI deployment 消费。

```python
GUARD_CATEGORIES = (
    "lint_format", "static_analysis", "cognitive_complexity", "file_length",
    "method_length_locals", "duplication", "coverage_threshold",
    "hooks_runner_ci_required_checks",
)

@dataclass(frozen=True)
class GuardEntry:
    guard_id: str
    category: str
    tool: str
    tool_version: str
    command: tuple[str, ...]
    config_paths: tuple[str, ...]
    config_sections: tuple[str, ...]
    config_digest: str
    scope: tuple[str, ...]
    threshold: str
    timeout_seconds: int
    failure_policy: Literal["fail_closed"]
    execution_points: tuple[str, ...]
    required_check: str

@dataclass(frozen=True)
class GuardRegistry:
    version: int
    host: str
    entries: tuple[GuardEntry, ...]
    digest: str

@dataclass(frozen=True)
class GuardMismatch:
    place: Literal["runtime", "pre_commit", "ci"]
    guard_id: str
    kind: Literal["missing", "command", "scope", "threshold", "exit_zero", "digest"]
    detail: str

@dataclass(frozen=True)
class ParityReport:
    registry_digest: str
    runtime_match: bool
    pre_commit_match: bool
    ci_match: bool
    mismatches: tuple[GuardMismatch, ...]

@dataclass(frozen=True)
class GuardDeployment:
    registry_digest: str
    pre_commit_path: Path
    ci_workflow_path: Path
    artifact_digests: Mapping[str, str]

def load_guard_registry(architecture_path: Path) -> GuardRegistry: ...
def validate_guard_registry(registry: GuardRegistry, repo: Path) -> tuple[str, ...]: ...
def check_parity(
    registry: GuardRegistry, runtime_commands: Mapping[str, Sequence[str]],
    pre_commit_path: Path, ci_workflow_path: Path, cwd: Path,
) -> ParityReport: ...
def deploy_guard_configs(registry: GuardRegistry, target_repo: Path) -> GuardDeployment: ...
```

完整性错误、重复 category、unpinned tool、缺 config、非 fail_closed、`--exit-zero`、required check 缺失都返回 hard error。`deploy_guard_configs` 只能消费已 validate 的 registry，固定写入 target repo `.githooks/pre-commit` 与 `.github/workflows/ci.yml`；hook 中 `TRACKS_GUARD_REGISTRY=<registry.digest>`、CI 顶层 `env.TRACKS_GUARD_REGISTRY=<registry.digest>`、返回对象三者必须一致。`artifact_digests` 是部署后两份输出 raw bytes 的审计值，不是 registry 中 `config_digest` 的 expected value，不得回填 registry。parity 对 argv0 使用同一解析规则，但不忽略其余 argv。

### 1f. Authenticity Gate（IF-AUTH-001/002）

**modules**: `executor/authenticity.py` 实现，m_test RED_CHECK、mutation 与 report 消费。

```python
BehaviourCategory = Literal["new", "existing"]
NodeOutcome = Literal["passed", "failed", "error", "skipped"]

@dataclass(frozen=True)
class AuthenticityJudgement:
    ac: str
    category: BehaviourCategory
    red: Literal["legal", "illegal", "none"]
    green_allowed: bool
    counterexample_kill: Literal["verified", "missing", "none"]
    unrelated_nodes: tuple[str, ...]
    blocked_reason: str | None

def classify_behaviour(
    ac_ref: str,
    if_refs: Sequence[str],
    baseline_acs: AbstractSet[str],
    baseline_ifs: AbstractSet[str],
) -> BehaviourCategory: ...

def judge_authenticity(
    ac_ref: str,
    category: BehaviourCategory,
    bound_nodes: Sequence[str],
    outcomes: Mapping[str, TestRunResult],
    legal_failure_kinds: AbstractSet[str],
    counterexample_experiment: Mapping | None,
) -> AuthenticityJudgement: ...
```

每个 R2 node 必须已有 AC trace；没有绑定是 trace error，不进入 unrelated。`new` 只接受行为失败/桩 token/symbol missing；`existing` 只在同 AC manifest、target killed、controls green、Prism semantic review 在位时 kill=verified。skipped/xfail 不作真实执行。

### 1g. Mutation manifest 与实验（IF-MUTATION-001/002）

manifest blob 的字段集恰为：

```json
{
  "protocol_version": 1,
  "ac": "AC-FR0261-01@v0.7",
  "if_ref": "IF-AUTH-002",
  "candidate_digest": "sha256:...",
  "patch_digest": "sha256:...",
  "target_nodes": ["opaque-node-1"],
  "control_nodes": ["opaque-node-2"],
  "runner_identity": "runtime:mutation-v1",
  "allowed_change_scope": ["tracks/..."],
  "expected_result": {"target": "killed", "controls": "green"}
}
```

不得增加 language/framework/test-body/candidate semantics。event envelope 的 status/reason 不属于 manifest blob。

```python
MUTATION_PROTOCOL_VERSION = 1

@dataclass(frozen=True)
class MutationManifest:
    protocol_version: int
    ac: str
    if_ref: str
    candidate_digest: str
    patch_digest: str
    target_nodes: tuple[str, ...]
    control_nodes: tuple[str, ...]
    runner_identity: str
    allowed_change_scope: tuple[str, ...]
    expected_result: Mapping[str, str]

@dataclass(frozen=True)
class MutationExperimentResult:
    status: Literal["passed", "blocked"]
    baseline: str
    apply: str
    target_kill: str
    controls: str
    rollback: str
    blocked_reason: MutationBlockReason | None
    node_results_ref: str | None

def build_manifest(
    ac: str, if_ref: str, candidate_digest: str, patch_digest: str,
    target_nodes: Sequence[str], control_nodes: Sequence[str],
    runner_identity: str, allowed_change_scope: Sequence[str],
) -> MutationManifest: ...
def validate_manifest(manifest: Mapping, patch_paths: Sequence[str]) -> MutationManifest: ...
def run_mutation_experiment(
    manifest: MutationManifest, repo: Path,
    open_worktree: Callable[[str], Path],
    apply_patch: Callable[[Path, str], str],
    run_nodes: Callable[[Path, Sequence[str]], Mapping[str, TestRunResult]],
    close_worktree: Callable[[Path], None],
) -> MutationExperimentResult: ...
```

target/control 都非空且不重叠；manifest 只能单 AC/IF；patch actual diff 非空且 digest 精确匹配；任何 `tests/` 路径拒绝。rollback clean 是 passed 必要条件。

### 1h. Host adapter protocol（IF-ADAPTER-001/002/003）

**modules**: `adapters/base.py` 定义、`adapters/reference_pytest.py` 实现、project loader 与所有 executor test handlers 消费。

```python
TEST_RESULT_PROTOCOL = "tracks-test-result"
TEST_RESULT_PROTOCOL_VERSION = 1

@dataclass(frozen=True)
class TestNode:
    node_id: str            # opaque to kernel/executor
    layer: Literal["unit", "integration", "e2e"]
    source_digest: str

@dataclass(frozen=True)
class TestRunResult:
    node_id: str
    status: Literal["passed", "failed", "error", "skipped"]
    detail: str | None

class Adapter(Protocol):
    adapter_id: str
    protocol: str
    protocol_version: int
    def collect(self, collect_command: str, layer: str, cwd: Path) -> list[TestNode]: ...
    def run_selected(
        self, command_template: str, nodes: Sequence[str], result_path: Path, cwd: Path
    ) -> tuple[str, ...]: ...
    def normalize_result(
        self, result_path: Path, selected: Sequence[str]
    ) -> list[TestRunResult]: ...

def resolve_adapter(adapter_id: str, protocol: str, version: int) -> Adapter: ...
```

`UnknownAdapterError` 对未知 id/protocol/version；`AdapterResultError` 对缺文件、畸形、重复 node、selected 覆盖不精确、状态未知。reference adapter 迁移 v0.6 `parse_junit_result`/exact coverage；这些符号不再存在于 executor。禁止区为 `tracks/kernel/**/*.py`、`tracks/executor/**/*.py`、`tracks/cli/**/*.py` 的运行时代码，token 词边界 case-insensitive：`pytest|junit|java`；comments/docstrings 同样禁止，避免隐藏语义。

project contract 追加：

```toml
[adapter]
id = "reference-pytest"
protocol = "tracks-test-result"
version = 1
```

### 1i. Candidate-bound closure（IF-CLOSURE-001）

**modules**: `checks/trace.py` 实现，`cli/main.py` 与 `executor/m_impl_runtime.py` 消费。

`trac check trace --version v0.7 --json` 每 AC 输出：

```json
{
  "ac": "AC-FR0260-01",
  "outlet": "IF-AUTH-001",
  "nodes": ["opaque-node"],
  "baseline_evidence": "evidence-id",
  "mutation_evidence": "manifest-digest",
  "candidate_digest": "sha256:...",
  "full_pass_evidence": "evidence-id",
  "status": "pass"
}
```

顶层 `status=pass`, `closure=candidate-bound` 仅当全部 required AC pass。hard error 封闭集：`node_missing | skip_xfail | identity_drift | control_failure | baseline_missing | mutation_missing | full_pass_missing | foreign_candidate`。同一函数供 CLI 与 ISLAND_GATE_2 调用。

### 1j. Demo host 与演证（IF-DEMO-001/IF-FAILCLOSED-001）

**modules**: `executor/demo_host.py` 实现，guard/mutation/authenticity/adapters 提供真实 gates，kernel/cli/report 消费 events。

```python
FAIL_CLOSED_SCENARIOS: tuple[str, ...]  # 恰好 §1a FailClosedScenario 九成员

@dataclass(frozen=True)
class DemoHostReport:
    repo: Path
    venv: Path
    wheel_sha256: str
    import_path: str
    architecture_path: Path
    registry_digest: str
    hooks_path: str
    ci_binding: str
    adapter_id: str

@dataclass(frozen=True)
class ScenarioFixture:
    scenario: str
    host: str
    manifest_or_config_ref: str
    expected_block_reason: str

def create_demo_host(template_dir: Path, target_dir: Path, wheel: Path) -> DemoHostReport: ...
def verify_path_equivalence(report: DemoHostReport) -> tuple[bool, tuple[str, ...]]: ...
def synthesize_scenario_fixture(
    scenario: str, host_repo: Path, candidate_digest: str
) -> ScenarioFixture: ...
```

真实路径必须：fresh venv、non-editable wheel、import path 不在 source tree、`trac init`；然后逐字节部署 asset `architecture.md`→`.tracks/projects/v0.1/architecture.md`、`tracks-project.toml`→`.tracks/projects/project.toml`、`flake8.ini`→repo 根，再经 §1k 同一 `load_guard_registry`/validate/deploy 路径生成 hook/CI，最后读取 hooksPath、CI binding 与 adapter。`DemoHostReport.registry_digest` 必须等于 deployment、hook、CI 与 `guard.parity` 的 digest。演证必须有九个不同 scenario detail event；一条 broad fixture 不能代替多个场景。

### 1k. Architecture machine registry block schema

canonical source 是调用方给定 architecture_path 的 §4.2 下、下一个 heading 前唯一的 `toml` fence；其第一个 table 必须是 `[quality_registry]`，随后恰好八个 `[[quality_guard]]`。零个/多个 block、其它 table 名、缺 `config_digest` 均 fail-closed：

```toml
[quality_registry]
version = 1
host = "tracks"

[[quality_guard]]
id = "lint-format"
category = "lint_format"
tool = "ruff"
tool_version = "0.16.0"
command = ".venv/bin/ruff check tracks tests"
config_paths = ["pyproject.toml"]
config_sections = ["tool.ruff", "tool.ruff.lint"]
config_digest = "sha256:<64 lowercase hex>"
scope = ["tracks", "tests"]
threshold = "line-length=100; violations=0"
timeout_seconds = 300
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"
```

八个 category 每个恰好一次。单个 `config_paths` 项时，`config_digest = "sha256:" + sha256(raw file bytes).hexdigest()`；多个项时，对每个 repo-relative POSIX path 计算 raw bytes 的 lowercase hex，按 path 排序并用 UTF-8 `json.dumps(mapping, sort_keys=True, separators=(",", ":"))` 序列化，再 sha256 该 JSON bytes并加前缀。`config_sections` 只验证声明 section 存在并参与 parity 语义，不进入 config digest。registry digest 是 TOML 解析后 `[quality_registry]` 与八个 `quality_guard` 的 canonical JSON sha256（排除 Markdown/frontmatter，object key 排序、array 顺序保持、无空白）；registry 不自含其自身 digest，避免循环。

`hooks_runner_ci_required_checks` 的 `config_paths` 在 tracks 与 demo 都必须绑定部署前已存在且可现场验证的 `.tracks/projects/project.toml`，sections 至少为 `unit/integration/e2e/adapter`。该项的 command/scope/threshold/required_check 仍是 hook/CI 输出的唯一语义真相；`deploy_guard_configs` 据此生成输出，`check_parity` 比对 registry digest header、规范化命令、scope、threshold、required checks 与禁 `--exit-zero`。生成输出 raw bytes 不进入 `config_digest`，否则输出内嵌 registry digest 会形成自引用；格式不同但上述语义完全相等可以 parity pass，任何语义或 digest-header 漂移均返回 `GuardMismatch`。

demo-pytest 的正式 source 是 wheel asset `tracks/assets/demo_host/architecture.md`，在 fresh repo 的唯一落点是 `.tracks/projects/v0.1/architecture.md`；它使用同一 schema/loader/validator，且 host 必须为 `demo-pytest`。其八项 config digest 分别绑定部署后 root `pyproject.toml`、`flake8.ini` 或 `.tracks/projects/project.toml` 的真实 bytes。仓库 inherited legacy `tracks/assets/demo_host/guards.toml` 不得修改，且不在 package-data allowlist、wheel/fresh repo、loader 输入或 deploy 输入中；其中旧表名/伪 section 永不被消费。`create_demo_host` 必须先 load+validate，再把同一 `GuardRegistry` 对象传给 `deploy_guard_configs`。AC-FR0258-03 的 “same registry-digest” 是**单宿主内** deployment record、Runtime、pre-commit、CI 四者相等；tracks 与 demo 因 scope、file-length=1200/500、required checks 六/三不同而预期 digest 不同，二者相等反而是错误 fixture。

> **Prism [RESOLVED]:** PRISM-ARCH007-R2-01 [blocker|判据7 可实现性 + 判据8 合同真实性]：§1k 规定 config_digest 公式为 `sha256(path bytes + section names)`，但 ARCH-007 §4.2 中六个引用 pyproject.toml 的条目（lint-format/static-semantic/file-length/method-length-locals/duplication/coverage-threshold）config_sections 两两不同却声明同一 digest sha256:a39b3d62…；实测该值恰为 sha256(pyproject.toml 文件字节)（.flake8 条目 f899995c… 同理为整文件字节摘要）。architecture §4.2 前言自述'目标配置 bytes 的预期 digest（多文件为 path→sha256 canonical JSON）'，与声明值一致；即本节公式与 ARCH §4.2 前言及声明值互斥——按 §1k 字面公式六者必须两两不同。Devon 实现 validate_guard_registry/check_parity 的 digest 校验时被迫在两种语义间选择，同一仓库状态将产生不同 pass/fail（digest 是 GuardMismatch 的法定 kind 之一）。请二选一修订：(a) 把 §1k 公式改为'config 文件字节 sha256（多文件 path→sha256 canonical JSON）'，与 §4.2 前言及现有声明值对齐；(b) 按 §1k 现行公式重算并改声明全部 per-(file,sections) 真实 digest。两者取一后须保证三处（§1k 公式、§4.2 前言、八条 config_digest 值）一致。
>> **Archer:** 采用 (a) 并已统一：IF §1k 与 ARCH §4.2 现在都规定单文件 config_digest=sha256(raw file bytes)；多文件为按 repo-relative POSIX path 排序的 path→raw-file-sha256-hex 无空白 canonical JSON 再 sha256。config_sections 只做 section/语义校验，不进入摘要。已用 shasum 核对 tracks pyproject=a39b3d62…、.flake8=f899995c…，并核对 demo pyproject/flake8/project contract 的八条声明；现有 registry 值与公式一致。三份 trac validate 均 valid。
>> **Archer:** 补充 Runtime rollback 后的一致性核对：为从 wheel 排除 inherited legacy guards.toml，pyproject package-data 改为显式 allowlist，故其 raw bytes digest 合法变化为 sha256:898db7a141555f1607a5370c83ffbdeca38d3d176687f4a8725fe976b788b627；ARCH §4.2 六条 pyproject-backed config_digest 已同步。程序核对确认 allowlist 含 architecture/flake8、不匹配 guards.toml，tracks/demo 均恰好八项；digest 公式仍为 IF §1k 的 raw-file bytes / multi-file canonical JSON，三文档 validate valid。
>> **Prism:** 复核通过：公式矛盾已消除——§1k 与 ARCH §4.2 前言现统一为'单文件 sha256(raw file bytes)、多文件 path→raw-file-sha256 canonical JSON（sort_keys/separators=(',',':')）再 sha256，config_sections 只做语义校验'。实测六条 pyproject-backed 条目=898db7a1…（与现行 pyproject.toml bytes 精确一致，allowlist 变更后同步）、.flake8=f899995c… 一致；demo registry 八条 digest 亦全部实测匹配。本线程所述七条单文件值与公式三处一致，resolved。但第 8 条（hooks-runner-ci-required 多文件 e25583b8…）经穷举核验无任何可推导来源：与现行 hook+CI 按新公式=1b61a873…，任何公式变体、任一 git tracked 文件代入均不匹配——该值声称是 deploy_guard_configs 未来生成物的目标 digest，而生成物字节在设计三件套中无任何规定，属未落地的占位值被当作机器真源冻结。此为不同缺陷类别（目标值不可推导 vs 公式互斥），另开 R3-01 于 architecture.md §4.2 锚定，不并入本线程。

## 2. CLI 接口合同

### 2a. `trac run`

调用与 exit 语义不变。新增输出行示例：

```text
[run <id>] phase0.baseline_repaired (ac=AC-FR0250-03@v0.6 bound_node=<node>)
[run <id>] phase0.coverage(passed) (ratio=0.95 by=collected exclude=none)
[run <id>] phase0.guard_hardened (violations=0 revised=0)
[run <id>] phase0.sealed (v0.6 baseline=readonly)
[run <id>] authenticity.judged (ac=<AC> category=new red=legal)
[run <id>] mutation.experiment(passed) (target_kill=verified controls=green rollback=clean)
[run <id>] host=tracks 9_scenarios=all_fail_closed crash_recovery=replay_ok
[run <id>] host=demo-pytest 9_scenarios=all_fail_closed crash_recovery=replay_ok
```

Phase 0 BLOCKED、parity blocked、authenticity blocked、experiment blocked 或演证 leak：exit 非零/park（按现有 awaiting 机制），不派发后续阶段。

### 2b. `trac status`

新增稳定字段：

```text
phase0=unsealed|validating|sealed|blocked baseline=v0.6-readonly|- coverage=<ratio|-> guards=hardened|blocked marks=registered|missing
guard_parity=passed|blocked registry=<sha256> mismatch=<place/guard/kind|->
authenticity=passed|blocked ac=<AC|-> reason=<reason|->
acceptance host=tracks all_fail_closed=true|false crash_recovery=replay_ok|failed
acceptance host=demo-pytest all_fail_closed=true|false equivalent=true|false crash_recovery=replay_ok|failed
```

### 2c. `trac check trace --version v0.7`

成功 stdout：`status=pass closure=candidate-bound`，exit 0。失败 stdout/json：`status=fail closure=candidate-bound hard_errors=[...]`，exit 1。`--json` 追加 §1i 每 AC 记录。v0.6 及早期版本输出语义不变。

### 2d. `trac validate`

既有文档/contract checks 追加：

1. architecture.md §4.2 registry block 八 category、pinned、config digest、failure policy、required checks 完整；
2. pre-commit/CI 的 `--exit-zero` 与静态 parity；
3. project `[adapter]` id/protocol/version 已知；
4. kernel/executor/cli 语言 token scan；
5. pytest marks `integration/e2e/performance` 注册；
6. demo package-data 显式 allowlist 含正式 `architecture.md` registry 与 `flake8.ini`、不含 inherited legacy `guards.toml`；wheel/临时 repo 中 `guards.toml` 必须不存在，物化后的正式 asset 必须通过同一 schema/config-digest validator。

任一失败 stderr 精确指出 check/path/guard/token，exit 1；不得 warning-only。

### 2e. `trac replay` / `trac report`

按 seq 展示 §1a 全 payload；report 新增 `Phase 0`、`Guard parity`、`Authenticity`、`Mutation experiments`、`Candidate closure`、`Dual-host acceptance` sections。它们只渲染事件/blob，不重算通过结论。

### 2f. 操作者交互闭合

| # | surface/context | 状态 | 动作/可用条件 | 可见结果与继续路径 |
|:--|:--|:--|:--|:--|
| 1 | v0.7 M-DESIGN EXIT 后宿主 repo | phase0 未封存 | `trac run`；工作树/contract 可校验 | sealed 后自动进入 M-TEST；blocked 时修复事实后重跑 |
| 2 | M-TEST RED_CHECK | frozen baseline 已存在 | `trac run` | 每 AC 显示 new legal Red 或 existing green+kill；blocked 不进 Prism |
| 3 | M-IMPL 出口 | tasks 完成且 FULL 绿 | `trac check trace --version v0.7` / `trac run` | candidate-bound pass 后进入双宿主演证 |
| 4 | v0.7-A 验收 | closure pass | `trac run` | 两个 host summary 全 true 才 boundary；任一 leak 留在 blocked |
| 5 | 任意审计时点 | 有 run id | `trac replay <id>` / `trac report --run-id <id>` | 可追溯命令、环境、node、digest、failure 与恢复 |

## 3. 文件 / 存储契约

| # | 路径 | 格式/写入者 | 读取者/生命周期 |
|:--|:--|:--|:--|
| 1 | `.tracks/projects/v0.7/architecture.md` §4.2 registry block | fenced TOML；Archer；git tracked | validate/Runtime/deploy/Prism；canonical |
| 2 | `.tracks/projects/project.toml [adapter]` | TOML；Archer；git tracked | project loader/adapters/Runtime |
| 3 | `.tracks/runtime/blobs/phase0/{run}/{seq}-seal.json` | canonical JSON；Runtime | m_test/m_impl/trace/report；append-only ref |
| 4 | `.tracks/runtime/blobs/mutation/{run}/{seq}-manifest.json` | §1g exact JSON；Runtime | experiment/replay/report |
| 5 | `.tracks/runtime/blobs/mutation/{run}/{seq}-results.json` | normalized node outcomes/digests；Runtime | authenticity/closure/report |
| 6 | `.tracks/runtime/demo/{run}/` | fresh git repo+venv；Runtime | demo experiment；after summary 可清理，events/blobs 保留 |
| 7 | `tracks/assets/demo_host/architecture.md` | 正式 demo architecture §4.2 registry；Archer；wheel package data | Runtime 逐字节部署到 `.tracks/projects/v0.1/architecture.md` 后由 `load_guard_registry` 消费 |
| 8 | `tracks/assets/demo_host/{pyproject.toml,flake8.ini,tracks-project.toml,demo_calc.py,tests/**}` | demo config/data；Archer；wheel package data | installed Runtime copies；config bytes 供 registry digest 校验；never imports as product module |
| 9 | `tests/counterexamples/v0.7/*.patch` | Shield；git tracked | Runtime mutation_verify；Devon read-only |
| 10 | `.githooks/pre-commit` | tracks: existing soft Phase 0 input；demo: absent before deploy；Runtime 由所选 host registry 生成/更新 | Runtime activates/readbacks via hooksPath；嵌入 registry digest |
| 11 | `.github/workflows/ci.yml` | tracks: existing workflow；demo: absent before deploy | parity validator/CI；由所选 host registry 生成/验证并嵌入 registry digest |

seal blob schema：`{baseline_version, document_digests, frozen_test_digests, marks, environment_contract_digest, seal_id}`。manifest/result blobs 使用 content-address digest；event ref 缺失 fail-closed。Runtime temp worktree/result file 不进 git/tree identity，清理前必须归一化持久化。

## 4. 可观察出口（测试断言基础）

### 4a. Event 出口

| # | outlet | 关键字段 | AC |
|:--|:--|:--|:--|
| 1 | `phase0.baseline_repaired/blocked` | AC、real node+digest、reason | FR0256 全部 |
| 2 | `phase0.coverage/guard_hardened/sealed` | ratio/by/exclude、violations/revised、seal ref | FR0257 全部 |
| 3 | `guard.parity` | registry digest、runtime/pre_commit/ci、mismatches | FR0258/0259, NFR0141-01 |
| 4 | `authenticity.judged` | category/red/green/kill/baseline/unrelated/status | FR0260/0261, NFR0140 |
| 5 | `mutation.manifest/experiment` | exact manifest ref、五步结果、identity/digests | FR0261～0263, NFR0140 |
| 6 | adapter execution audit（既有 test/full events 扩展） | adapter id、protocol/version、outcomes_ref | FR0264, NFR0141-02 |
| 7 | `demo.equivalence` | wheel/venv/import/hooks/CI/adapter/equivalent | FR0266-02/04, NFR0142-01 |
| 8 | `failclosed.demonstrated/summary` | host/scenario/outcome/all_fail_closed/crash recovery | FR0266, NFR0142-02 |

### 4b. CLI 出口

| # | outlet | assertions |
|:--|:--|:--|
| 1 | `trac status` | §2b exact fields；blocked reason；no later stage |
| 2 | `trac check trace --version v0.6` | Phase 0 binding real-node closure/marker-only failure |
| 3 | `trac check trace --version v0.7 [--json]` | candidate-bound topology/hard errors |
| 4 | `trac validate` | registry/adapter/language/marks/package-data fail-closed |
| 5 | `trac replay/report` | append-only payload、identity、execution evidence、recovery |

### 4c. File/Git 出口

| # | outlet | assertions |
|:--|:--|:--|
| 1 | guards/project contracts | machine-readable complete schema/digest |
| 2 | `.githooks/pre-commit` + CI workflow | no `--exit-zero`; same scope/rules/thresholds |
| 3 | v0.6 docs + frozen tests | post-seal digest unchanged/no write commit |
| 4 | isolated experiment worktree | actual diff identity; rollback clean; real repo unchanged |
| 5 | installed demo repo | non-editable wheel import outside source; hooks/CI/adapter deployed |

## 5. IF Registry

### IF-MTEST-001 M-TEST 测试收集合同（继承 IF-006）

### IF-MTEST-002 M-TEST 测试执行合同（继承 IF-006；v0.7 authenticity 消费其结果）

### IF-SHIELD-001 Shield 测试编写合同（继承 IF-006）

### IF-TRACE-001 trace 需求追踪合同（继承 IF-006）

### IF-TRACE-002 trace 闭合检查合同（继承 IF-006；Phase 0 v0.6 real binding 使用）

### IF-REACH-001 reach 模块可达性合同（继承 IF-006）

### IF-REACH-002 reach 孤岛检查合同（继承 IF-006）

### IF-VALIDATE-001 trac validate 文档校验合同（继承 IF-006；v0.7 追加 registry/adapter/language/marks）

### IF-IMPL-001 M-IMPL kernel 状态机合同（继承 IF-006）

### IF-IMPL-002 M-IMPL executor handler 合同（继承 IF-006）

### IF-IMPL-003 task graph 解析与校验合同（继承 IF-006）

### IF-IMPL-004 RGR git 操作合同（继承 IF-006）

### IF-IMPL-005 质量门禁分层执行合同（继承 IF-006；registry 成为唯一来源）

### IF-IMPL-006 三 worktree 方案合同（继承 IF-006）

### IF-IMPL-007 tasks.json/tasks.md 真相源合同（继承 IF-006）

### IF-DEVON-001 Devon agent 与 manifest 越界审计合同（继承 IF-006）

### IF-LIVE-001 真实外部旅程与 evidence 合同（继承 IF-006）

### IF-RELEASE-001 release-evidence 合同（继承 IF-006；v0.7-B 前不扩展）

### IF-DOCGAP-001 文档评论优先裁定合同（继承 IF-006）

### IF-QUARANTINE-001 outcome 隔离恢复合同（继承 IF-006）

### IF-HOTFIX-001 hotfix CLI 入口合同（继承 IF-006）

### IF-HOTFIX-002 HOTFIX-TRIAGE kernel 合同（继承 IF-006）

### IF-HOTFIX-003 PRECHECK 合同（继承 IF-006）

### IF-HOTFIX-004 Sage 锚定校验合同（继承 IF-006）

### IF-HOTFIX-005 fix 分支与基线继承合同（继承 IF-006）

### IF-HOTFIX-006 run 并存恢复合同（继承 IF-006）

### IF-HOTFIX-007 M-TEST hotfix 变体合同（继承 IF-006）

### IF-HOTFIX-008 M-IMPL hotfix 变体合同（继承 IF-006）

### IF-HOTFIX-009 缺口与退出路由合同（继承 IF-006）

### IF-HOTFIX-010 hotfix 继承文档解析合同（继承 IF-006）

### IF-SELECT-001 节点分类合同（继承 IF-006）

### IF-SELECT-002 选择与 selection identity 合同（继承 IF-006）

### IF-EVIDENCE-001 evidence identity/reuse/stale 合同（继承 IF-006）

### IF-LEDGER-001 失败台账合同（继承 IF-006）

### IF-FULLCHAIN-001 FULL 链合同（继承 IF-006）

### IF-RUNCONTRACT-001 测试命令与结果通道合同（继承 IF-006；框架解析迁入 adapter）

### IF-NIGHTLY-001 nightly FULL 回归合同（继承 IF-006）

### IF-PHASE-001 Phase 0 real collected-node binding 合同

- **合同**：§1a/§1d 的 gap scan、run_selected real binding、marker-only 拒绝、BLOCKED 路由与 `phase0.baseline_repaired/blocked`。
- **modules**：kernel/phase0.py, executor/phase0.py, adapters, checks/trace.py, report.py。
- **关联**：FR-0256。

### IF-PHASE-002 Phase 0 coverage/marks/environment 合同

- **合同**：真实 coverage ≥95%、source exclusion 为空、marks 注册与 adapter/environment contract validate。
- **modules**：executor/phase0.py, executor/validate.py, guard_registry.py, pyproject/project contracts。
- **关联**：FR-0257-01/03。

### IF-PHASE-003 v0.6 seal 与只读基线合同

- **合同**：seal blob、SEALED 不可回退、冻结 docs/tests digest、后续 drift/写入 fail-closed。
- **modules**：executor/phase0.py, kernel/phase0.py, m_test.py, m_impl_runtime.py, layout auditor。
- **关联**：FR-0257-04/05, FR-0260-03, FR-0261-03。

### IF-GUARD-001 Canonical quality guard registry 合同

- **合同**：§1e/§1k 八类完整 schema、pinned tools、五要素、raw-file config digest、per-host canonical architecture source、fail_closed、v0.6 迁移与 real required-check evidence。
- **modules**：executor/guard_registry.py, validate.py, tracks/demo architecture.md §4.2 machine blocks。
- **关联**：FR-0257-01/02, FR-0258-01/04, FR-0259。

### IF-GUARD-002 Runtime/pre-commit/CI parity 与部署合同

- **合同**：三处归一化比对、`--exit-zero` 拒绝、`guard.parity`、`GuardDeployment`、第8类 project-contract input digest 与 generated artifact audit 分离、单宿主 deployment/Runtime/hook/CI 同 registry digest、registry 驱动宿主配置生成/验证。
- **modules**：executor/guard_registry.py, phase0.py, `.githooks/pre-commit`, CI workflow, demo_host.py。
- **关联**：FR-0257-02, FR-0258-02/03, FR-0259-02, NFR-0141-01。

### IF-AUTH-001 New-behaviour authenticity/Red 合同

- **合同**：§1f new 分类、冻结 baseline、AC-bound legal Red、unrelated failure 隔离、blocked conditions、D-41 并存。
- **modules**：executor/authenticity.py, kernel/m_test.py, adapters, report.py。
- **关联**：FR-0260, NFR-0140。

### IF-AUTH-002 Existing-green/counterexample 合同

- **合同**：existing 绿允许但同 AC kill 必须 verified；Prism semantic/minimality review；冻结测试角色分离。
- **modules**：executor/authenticity.py, mutation.py, kernel/m_test.py, layout auditor。
- **关联**：FR-0261。

### IF-MUTATION-001 Mutation manifest 合同

- **合同**：§1g exact fields、单 AC/IF、opaque node、no semantics、no-op/broad/multi-AC/test-scope 拒绝。
- **modules**：executor/mutation.py, report.py。
- **关联**：FR-0260-05, FR-0262, FR-0265-03, NFR-0140-02。

### IF-MUTATION-002 Isolated mutation experiment 合同

- **合同**：baseline/apply/diff/target/control/rollback、append-only、WAL/replay、failure matrix。
- **modules**：executor/mutation.py, worktree.py, adapters, kernel/m_test.py, report.py。
- **关联**：FR-0261/0263, NFR-0140。

### IF-ADAPTER-001 Language-neutral host adapter 合同

- **合同**：§1h three methods、`tracks-test-result` v1、project declaration、unknown/malformed fail-closed。
- **modules**：adapters/base.py, project.py, all test handlers。
- **关联**：FR-0257-03, FR-0264-01/03。

### IF-ADAPTER-002 Reference adapter 等价合同

- **合同**：现行 collect/run_selected/result-normalize 与 exact coverage 迁入 reference adapter，同输入同输出。
- **modules**：adapters/reference_pytest.py, project contract。
- **关联**：FR-0264-02。

### IF-ADAPTER-003 Kernel/executor/cli 语言中立不变量

- **合同**：禁止区 token scan + Runtime adapter-only execution 双检查；未知 adapter 不穷举。
- **modules**：executor/validate.py, adapters/base.py, kernel/executor/cli source。
- **关联**：FR-0264-04, NFR-0141-02。

### IF-CLOSURE-001 Candidate-bound executable trace 合同

- **合同**：§1i 全链、candidate digest 一致、hard error 封闭集、CLI 与 ISLAND_GATE_2 同一检查。
- **modules**：checks/trace.py, executor/m_impl_runtime.py, cli/main.py, report.py。
- **关联**：FR-0265。

### IF-DEMO-001 Demo host real-path equivalence 合同

- **合同**：§1j wheel/fresh venv/trac init、demo architecture 固定落点、同 loader/validator/deployer、registry digest 四向关联、contract/hooks/CI+adapter、无私有测试捷径、`demo.equivalence`。
- **modules**：executor/demo_host.py, guard_registry.py, adapters, package assets。
- **关联**：FR-0266-02/04, NFR-0142-01。

### IF-FAILCLOSED-001 双宿主 9 场景与 crash recovery 合同

- **合同**：九成员封闭集×两个 host、每场景独立 detail、summary、任一 leaked 阻断、replay recovery。
- **modules**：executor/demo_host.py, authenticity.py, mutation.py, guard_registry.py, kernel/machine.py, report.py。
- **关联**：FR-0266-01/03/04, NFR-0142-02。
