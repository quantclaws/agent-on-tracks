---
acceptance_id: ACC-001
spec_ref: SPEC-001
created: 2026-07-30
status: draft
sha:
---

# Agent on Tracks v0.1 — 验收标准

## 约定

- 每条 AC 均为外部可观察事实：CLI 退出码、stdout/stderr 内容、文件存在/内容、Git 状态、事件日志内容。
- 不断言私有类/函数内部。
- `$TRAC` 表示已安装的 `trac` CLI 入口。
- `$HOST` 表示一个临时 git 初始化的宿主项目目录。
- "JSONL 字段 X" 指解析 `.tracks/runtime/events/run-*.jsonl` 中对应行。

---

## FR-01 — trac init

| AC | 断言 |
|:---|:---|
| AC-01a | 在空 git 仓库中执行 `$TRAC init` → exit 0；`.tracks/projects/`、`.tracks/runtime/`、`.tracks/wiki/` 目录存在。 |
| AC-01b | 再次执行 `$TRAC init` → exit 0，无报错，结构不变。 |

## FR-02 — stdin 需求输入

| AC | 断言 |
|:---|:---|
| AC-02a | `echo "构建X" \| $TRAC start v0.1` → exit 0。 |
| AC-02b | `$TRAC start v0.1 < /dev/null`（空 stdin）→ exit 1，stderr 包含 "stdin" 或 "空"。 |

## FR-03 — 脏工作区拒绝

| AC | 断言 |
|:---|:---|
| AC-03a | `$HOST` 存在未提交文件时，`echo "x" \| $TRAC start v0.1` → exit 1，stderr 提及 "dirty" 或 "uncommitted"。`releases/v0.1` 分支不存在。 |

## FR-04 — 创建 release 分支

| AC | 断言 |
|:---|:---|
| AC-04a | start 成功后，`git branch --list releases/v0.1` 显示该分支；`git rev-parse --abbrev-ref HEAD` = `releases/v0.1`。 |

## FR-05 — story.md 写入

| AC | 断言 |
|:---|:---|
| AC-05a | `.tracks/projects/v0.1/story.md` 存在；frontmatter 含 `story_id`、`created`、`status: draft`、`sha:`（空）。正文包含 stdin 文本。 |

## FR-06 — M-START 事件

| AC | 断言 |
|:---|:---|
| AC-06a | 事件日志包含 `stage.entered`（payload stage=M-START）及随后的 `stage.exited`（stage=M-START）。`runs.jsonl` 有该 run 的一条记录。 |

## FR-07 — run 进入 TRIAGE

| AC | 断言 |
|:---|:---|
| AC-07a | start 后执行 `$TRAC run` → 事件日志新增 `assignment.dispatched`（role=scribe, substate=TRIAGE）。进程 exit 0（等待人类裁决）。 |

## FR-08 — triage 命令

| AC | 断言 |
|:---|:---|
| AC-08a | state=awaiting_triage 时 `$TRAC triage go` → exit 0，事件 `human.triage(go)` 已追加。 |
| AC-08b | state≠awaiting_triage 时 `$TRAC triage go` → exit 1，stderr 说明原因。 |

## FR-09 — NO-GO / PARK 路径

| AC | 断言 |
|:---|:---|
| AC-09a | `$TRAC triage no-go` + `$TRAC run` 后：分支 `releases/v0.1` 不存在；事件日志含 `run.completed`；backlog 文件记录了拒绝。 |
| AC-09b | `$TRAC triage park` 同理。 |

## FR-10 — GO 进入 DRAFT

| AC | 断言 |
|:---|:---|
| AC-10a | triage go + run 后：事件日志出现 `assignment.dispatched`（role=scribe, substate=DRAFT）。story.md 内容按模板更新。 |

## FR-11 — 校验 + 重派

| AC | 断言 |
|:---|:---|
| AC-11a | FakeAgent 产出 schema 不合格的 story.md 时：事件日志出现 `verdict.failed(schema)` 后跟新的 `assignment.dispatched`（payload 含失败证据）。story.md 未被提交。 |

## FR-12 — 重派上限升级

| AC | 断言 |
|:---|:---|
| AC-12a | 同一校验连续 3 次 `verdict.failed` 后：无更多 `assignment.dispatched`；状态为 `awaiting_human`。`$TRAC status` 报告升级。 |

## FR-13 — SAGE_REVIEW 分派

| AC | 断言 |
|:---|:---|
| AC-13a | DRAFT 校验通过后：事件日志出现 `story.committed` 然后 `assignment.dispatched`（role=sage, substate=SAGE_REVIEW）。 |

## FR-14 — sage verdict 路由

| AC | 断言 |
|:---|:---|
| AC-14a | FakeAgent(sage) 返回 pass → 状态变为 HUMAN_REVIEW（awaiting_human）。 |
| AC-14b | FakeAgent(sage) 返回 comment → 状态变为 RESPOND，新 `assignment.dispatched`（role=scribe）。 |

## FR-15 — human review no-comment → EXIT

| AC | 断言 |
|:---|:---|
| AC-15a | HUMAN_REVIEW 且同轮 sage pass：`$TRAC review no-comment` + `$TRAC run` → 事件日志出现 `stage.exited(M-STORY)`。 |

## FR-16 — review revise → RESPOND

| AC | 断言 |
|:---|:---|
| AC-16a | HUMAN_REVIEW 中 `$TRAC review revise` → 事件 `human.review(comment)` 已追加；下次 `$TRAC run` 分派 Scribe 进入 RESPOND。 |

## FR-17 — EXIT M-STORY sha

| AC | 断言 |
|:---|:---|
| AC-17a | M-STORY 退出后：story.md frontmatter `sha` 字段为 64 位十六进制串，与文件正文 sha256 一致。Git log 含对应提交。 |

## FR-18 — M-SPEC DRAFT

| AC | 断言 |
|:---|:---|
| AC-18a | M-STORY 退出 + `$TRAC run` 后：事件日志出现 `stage.entered(M-SPEC)` 然后 `assignment.dispatched`（role=sage, substate=DRAFT）。`.tracks/projects/v0.1/spec.md` 已创建。 |

## FR-19 — spec 校验 + 重派

| AC | 断言 |
|:---|:---|
| AC-19a | schema 不合格的 spec.md → `verdict.failed(schema)` + 附证据重派。上限同 FR-12（≤3 次）。 |

## FR-20 — scope_overflow 回退

| AC | 断言 |
|:---|:---|
| AC-20a | FakeAgent 产出含 31 条 FR 的 spec → `verdict.failed(scope_overflow)` + `stage.rolled_back` 回 M-STORY。分支 `releases/v0.1` 仍存在。 |

## FR-21 — LEX_REVIEW

| AC | 断言 |
|:---|:---|
| AC-21a | spec 校验通过后：`assignment.dispatched`（role=lex, substate=LEX_REVIEW）。`lex.verdict(pass)` → HUMAN_REVIEW。 |

## FR-22 — M-SPEC 人类评审

| AC | 断言 |
|:---|:---|
| AC-22a | `trac review no-comment` + 同轮 lex pass → EXIT 路径。`trac review revise` → RESPOND（Sage 响应）。 |

## FR-23 — EXIT M-SPEC

| AC | 断言 |
|:---|:---|
| AC-23a | 退出后：事件日志以 `stage.exited(M-SPEC)` + `run.completed` 结尾。`$TRAC status` 报告已完成。 |

## FR-24 — trac status

| AC | 断言 |
|:---|:---|
| AC-24a | 运行中：`$TRAC status` → exit 0，stdout 含当前阶段名、子状态、待处理动作。 |
| AC-24b | 无活跃 run：`$TRAC status` → exit 0，stdout 提示无活跃 run。 |

## FR-25 — trac replay

| AC | 断言 |
|:---|:---|
| AC-25a | `$TRAC replay <run-id>` → exit 0，stdout 逐行打印全部事件 + 终态摘要。 |
| AC-25b | `$TRAC replay nonexistent` → exit 1，stderr 说明。 |

## FR-26 — replay ≡ status

| AC | 断言 |
|:---|:---|
| AC-26a | 对已完成 run：replay 输出的终态字段（stage、substate、run status）与 `trac status` 一致。 |

## FR-27 — 单写者锁

| AC | 断言 |
|:---|:---|
| AC-27a | 后台启动 `$TRAC run`（停在 awaiting_human 持锁）；再启动第二个 `$TRAC run` → exit 1，stderr 含第一个进程 PID。 |

## FR-28 — 事件日志格式

| AC | 断言 |
|:---|:---|
| AC-28a | `run-*.jsonl` 每行为合法 JSON，含字段：seq、ts、run_id、type、schema_version、payload。seq 严格递增。 |

## FR-29 — 中断/恢复

| AC | 断言 |
|:---|:---|
| AC-29a | 在 awaiting_human 时 kill `$TRAC run`；重新 `$TRAC run` → 从精确子状态恢复（不重派已完成工作）。 |

## FR-30 — write-ahead 命令

| AC | 断言 |
|:---|:---|
| AC-30a | 对每条已执行命令，事件日志中 `command.issued`（seq N）在对应结果事件（seq N+k, k≥1）之前。 |

---

## NFR-01 — 无 LLM

| AC | 断言 |
|:---|:---|
| AC-N01a | 完整 happy-path 在零网络环境下跑通（无网络调用）。 |

## NFR-02 — ≤10 模块

| AC | 断言 |
|:---|:---|
| AC-N02a | `find src/track -name '*.py' ! -name '__init__.py'` 计数 ≤ 10。 |

## NFR-03 — 纯函数

| AC | 断言 |
|:---|:---|
| AC-N03a | 单元测试以冻结输入调用 `project()` 和 `decide()`；无需 mock 文件系统或时间。 |

## NFR-04 — 不信任自述

| AC | 断言 |
|:---|:---|
| AC-N04a | FakeAgent outcome 声称 "done" 但产出不合格产物 → Runtime 仍产出 `verdict.failed`。 |

## NFR-05 — 可抛弃 DB

| AC | 断言 |
|:---|:---|
| AC-N05a | 删除 `tracks.db`；`$TRAC status` 仍返回正确状态（从事件重建）。 |

## NFR-06 — 无兼容别名

| AC | 断言 |
|:---|:---|
| AC-N06a | 脚手架和运行时中不存在名为 `.track` 的文件/目录。不存在 `tracks.*` 导入路径。 |

## NFR-07 — 代码精练

| AC | 断言 |
|:---|:---|
| AC-N07a | 模块间无 >5 行的重复逻辑块（代码审查 / `trac check dup` 可用时验证）。 |

---

## 覆盖矩阵摘要

- 30 条 FR → 42 条 AC（每条 FR ≥1 条 AC）
- 7 条 NFR → 7 条 AC（每条 NFR ≥1 条 AC）
- 每条 AC 恰好引用一条 FR 或 NFR
- 无孤立 AC
