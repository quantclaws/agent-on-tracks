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
│   ├── test_happy_path.py     # 完整用户旅程（见 §4）
│   ├── test_rejection.py      # NO-GO / PARK 路径
│   ├── test_retry_escalation.py  # 校验失败 → 重派 → 升级
│   ├── test_scope_overflow.py # FR>30 回退
│   └── test_recovery.py       # 中断/恢复、单写者锁
└── conftest.py                # 共享 fixture（tmp git repo、installed trac）
```

## 3. AC → 测试层映射

| AC 范围 | 层 | 测试文件 |
|:--------|:---|:---------|
| AC-N03a（纯函数） | unit | test_project.py, test_decide.py |
| AC-28a（事件格式） | unit | test_events.py |
| AC-11a, AC-19a（校验逻辑） | unit | test_validate.py |
| AC-06a, AC-30a（事件序列） | integration | test_runtime_loop.py |
| AC-13a, AC-14a/b, AC-21a（agent 协作） | integration | test_executor.py |
| AC-N05a（store 重建） | integration | test_store.py |
| AC-01a/b ~ AC-05a（init/start） | e2e | test_happy_path.py |
| AC-07a ~ AC-17a（M-STORY 全程） | e2e | test_happy_path.py |
| AC-18a ~ AC-23a（M-SPEC 全程） | e2e | test_happy_path.py |
| AC-24a/b, AC-25a/b, AC-26a（status/replay） | e2e | test_happy_path.py |
| AC-09a/b（NO-GO/PARK） | e2e | test_rejection.py |
| AC-12a, AC-N04a（重派/升级） | e2e | test_retry_escalation.py |
| AC-20a（scope_overflow） | e2e | test_scope_overflow.py |
| AC-27a, AC-29a（锁/恢复） | e2e | test_recovery.py |
| AC-N01a（无网络） | e2e | test_happy_path.py（环境隔离） |
| AC-N02a（模块计数） | e2e | test_happy_path.py（或 CI 脚本） |
| AC-N06a（无 .track） | e2e | test_happy_path.py |
| AC-N07a（无重复） | — | 代码审查 / 未来 trac check dup |

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

E2E 测试通过环境变量 `TRAC_FAKE_SIMULATE=schema_fail` 注入（仅测试用，生产忽略）。

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
test -d src/track && test -d tests/unit && test -d tests/integration && test -d tests/e2e
# 无 .track 残留
! test -e .track
```
