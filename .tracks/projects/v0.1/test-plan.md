---
test_plan_id: TP-001
spec_ref: SPEC-001
arch_ref: ARCH-001
created: 2026-07-30
status: draft
sha:
---

# Agent on Tracks v0.1 — 测试计划

## 1. 分层策略

| 层 | 目录 | 职责 | 运行频率 |
|:---|:-----|:-----|:---------|
| unit | `tests/unit/` | 纯函数逻辑：project()、decide()、validate、events 序列化 | 每次保存 |
| integration | `tests/integration/` | 模块协作：store+project+decide 环路、executor+agents+validate 协作 | 每次提交 |
| e2e | `tests/e2e/` | 用户旅程：通过 CLI 入口驱动完整流程，断言外部可观察事实 | 每次提交 |

比例目标：unit ≈ 50%、integration ≈ 30%、e2e ≈ 20%（按测试数量）。

## 2. 目录结构

```
tests/
├── unit/
│   ├── test_project.py        # State fold 纯函数
│   ├── test_decide.py         # 决策纯函数
│   ├── test_validate.py       # 文档校验器
│   └── test_events.py         # 序列化/反序列化、信封
├── integration/
│   ├── test_runtime_loop.py   # store+project+decide 主循环（mock executor）
│   ├── test_executor.py       # executor+agents+validate 协作（真实 git tmp）
│   └── test_store.py          # JSONL 追加/读取/blob/runs 索引
├── e2e/
│   ├── test_happy_path.py     # 完整用户旅程（见 §4），从真实 init → start 起
│   ├── test_start_guards.py   # M-START 拒绝路径：空 stdin、脏工作区、init→start 干净交接
│   ├── test_rejection.py      # NO-GO / PARK 路径
│   ├── test_retry_escalation.py  # 校验失败 → 重派 → 升级
│   ├── test_respond_paths.py  # sage comment / review revise 的 RESPOND 循环、错误状态命令拒绝
│   ├── test_scope_overflow.py # FR>30 回退
│   └── test_recovery.py       # 中断/恢复、单写者锁（阻塞式 agent 制造锁竞争）
└── conftest.py                # 共享 fixture（tmp git repo、installed trac）
```

## 3. AC → 测试层映射

| AC 范围 | 层 | 测试文件 |
|:--------|:---|:---------|
| AC-N02a（纯函数） | unit | test_project.py, test_decide.py |
| AC-28a（事件格式） | unit | test_events.py |
| AC-11a, AC-19a（校验逻辑） | unit | test_validate.py |
| AC-06a, AC-30a（事件序列） | integration | test_runtime_loop.py |
| AC-13a, AC-14a/b, AC-21a（agent 协作） | integration | test_executor.py |
| AC-N04a（事件重建：删 runs.jsonl 后仍折叠出终态） | integration | test_store.py |
| AC-28b（blob 外置 >8KB） | unit | test_store.py |
| AC-01a/c, AC-04a, AC-05a（init 提交 + start happy） | e2e | test_happy_path.py |
| AC-01b, AC-02b, AC-03a（幂等 / 空 stdin / 脏工作区拒绝——**拒绝路径独立**，不塞 happy） | e2e | test_start_guards.py |
| AC-07a ~ AC-17a 中的通过路径（M-STORY happy path） | e2e | test_happy_path.py |
| AC-08b, AC-14b, AC-16a/b（RESPOND 循环 / revise 闭环 / 错误状态拒绝，happy path 不覆盖） | e2e | test_respond_paths.py |
| AC-18a ~ AC-23a（M-SPEC 全程） | e2e | test_happy_path.py |
| AC-24a/b, AC-25a/b, AC-26a（status/replay） | e2e | test_happy_path.py |
| AC-09a/b（NO-GO/PARK） | e2e | test_rejection.py |
| AC-12a, AC-N03a（重派/升级） | e2e | test_retry_escalation.py |
| AC-20a（scope_overflow） | e2e | test_scope_overflow.py |
| AC-27a/b, AC-29a/b/c（锁/恢复/torn-write） | e2e | test_recovery.py |
| AC-N01a（无网络） | e2e | test_happy_path.py（环境隔离） |
| AC-N05a（无 .track） | e2e | test_happy_path.py |
| AC-N06b（文件 ≤1000 行、无 utils 模块） | e2e | test_happy_path.py（或 CI 脚本） |
| AC-N06a（无重复） | — | 代码审查 / 未来 trac check dup |

## 4. E2E Happy Path 用户旅程

`test_happy_path.py` 覆盖的完整序列（对应 FR-01 ~ FR-26 的 happy 通路）：

```
1. trac init                          → 断言目录结构（AC-01a）
2. echo "需求" | trac start v0.1      → 断言分支、story.md、事件（AC-02a~06a）
3. trac run                           → 断言 TRIAGE 分派、进程退出（AC-07a）
4. trac triage go                     → 断言事件（AC-08a）
5. trac run                           → DRAFT → SAGE_REVIEW → HUMAN_REVIEW（AC-10a~14a）
6. trac review no-comment             → 断言事件（AC-15a 前置）
7. trac run                           → EXIT M-STORY → M-SPEC DRAFT（AC-17a, AC-18a）
8. trac run                           → LEX_REVIEW → HUMAN_REVIEW（AC-21a）
9. trac review no-comment             → 断言事件（AC-22a 前置）
10. trac run                          → EXIT M-SPEC, run.completed（AC-23a）
11. trac status                       → 断言终态（AC-24a）
12. trac replay <run-id>              → 断言事件行 + 终态 ≡ status（AC-25a, AC-26a）
```

每步断言：exit code、stdout 关键字、事件日志新增行、git 状态、文件存在/内容。

> **run-loop 停止语义**（回应 R1-09）：每次 `trac run` 消费队列直至遇到 Human 门（`awaiting_human`，释放锁退出 0）或 run 终结（`run.completed`/`run.parked`）；空队列即停，不忙等。上表每个 `trac run` 步骤对应一次"跑到下一个门"。

### 4.2 前进性不变量（核心断言）

FakeAgent 的价值在于**穷举重要路径**，据此保证 tracks 部署到宿主后各情况下工作流都能前进。故每条 e2e 路径（happy 与所有 `simulate` 分支）末态必须落在三个干净状态之一，**绝不**停在挂起/无恢复崩溃：

- `stage.exited` — 阶段推进（前进）；
- `run.completed` / `run.parked` — run 终结（终态）；
- `awaiting_human` — 干净停等人工（可恢复的暂停，锁已释放）。

`test_recovery.py` 额外断言：`simulate=hang` 制造锁竞争后，第二个 `trac run` 被单写者锁拒绝（退出非 0、无写入），锁释放后恢复仍能前进——挂起不等于死锁。

### 4.1 E2E 测试数据来源

- **原始需求**（stdin）：一个 fixture 占位字符串；FakeAgent 不解析其内容，不影响确定性。
- **story.md / spec.md 正文**：定值文档**烘焙进 `effects/agents.py`**（不放 `tests/fixtures/`——FakeAgent 是生产一等公民，生产代码不依赖 tests/），按 `(role, simulate)` 取用。happy-path 文档须刚好通过 `validate`。FakeAgent 把文档**真写到磁盘**（经 executor 白名单路径），validate 对真实文件运行——副作用链不 mock。
- **分支选择**：`TRAC_FAKE_SIMULATE` → assignment.simulate（见 §6）。
- **断言主源**：事件日志 JSONL；辅以 git 状态、文件、退出码。

> e2e 证明的是引擎管路（状态机/事件时序/锁/恢复/git/dispatch→validate→commit 循环），**不**证明 validate 规则对（归 `test_validate.py`）或真 agent 产出质量（无 LLM，超范围）。FakeAgent 摘掉 LLM 不确定性，使"runtime 本身对不对"可被单独回答。

## 5. 共享 Fixture 设计（conftest.py）

```python
@pytest.fixture
def host_repo(tmp_path):
    """初始化一个干净的 git 仓库作为宿主项目。"""
    # git init, 初始 commit, 返回路径

@pytest.fixture
def trac(host_repo):
    """返回一个 callable，在 host_repo 中执行 trac 子命令。"""
    # 封装 subprocess.run(["trac", ...], cwd=host_repo, ...)
    # 返回 (exit_code, stdout, stderr)

@pytest.fixture
def event_log(host_repo):
    """返回读取 .tracks/runtime/events/run-*.jsonl 的辅助函数。"""
```

公共断言辅助（提取为 `tests/helpers.py`）：

```python
def assert_event(log_lines, event_type, **payload_fields): ...
def assert_exit(result, code, stderr_contains=None): ...
def parse_frontmatter(path) -> dict: ...
```

## 6. FakeAgent 测试控制

FakeAgent 通过 assignment 中的 `simulate` 字段控制行为：

| simulate 值 | 行为 | 覆盖路径 |
|:------------|:-----|:---------|
| `None`（默认） | 产出合格文档，verdict(pass) | happy path |
| `"schema_fail"` | 产出 schema 不合格文档 | 重派路径 |
| `"scope_overflow"` | 产出 >30 FR 的 spec | 回退路径 |
| `"comment"` | 返回 verdict(comment) + diff | RESPOND 路径 |
| `"hang"` | dispatch 后阻塞不返回（持有 agent 锁） | 恢复路径：制造锁竞争（AC-27a） |

E2E 测试通过环境变量 `TRAC_FAKE_SIMULATE=schema_fail` 注入（仅测试用，生产忽略）。

> `simulate` 是**路径选择器**而非文档作者：它选择 FakeAgent 走哪条确定性分支，e2e 据此断言轨迹与前进性，不断言文档语义。

## 7. 测试纪律

- 禁止 `assert False` 占位测试。
- 禁止仅断言源码字符串存在的测试。
- 每个测试必须至少触达一个生产模块（非纯 fixture 操作）。
- 不为每句话建一个文件；同概念测试聚合在同一文件。
- E2E 测试通过 subprocess 调用真实 CLI 入口，不 import 内部模块。
- Integration 测试可 import 生产模块，但必须经过真实 I/O（tmp 文件系统、真实 git）。

## 8. 规划阶段可用检查

当前（无业务代码）可运行的验证：

```bash
# 脚手架结构完整性
python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"
# 目录存在
test -d src/tracks && test -d tests/unit && test -d tests/integration && test -d tests/e2e
# 无 .track 残留
! test -e .track
```
