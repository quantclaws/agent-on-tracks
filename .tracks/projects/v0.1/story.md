---
story_id: S-001
created: 2026-07-30
status: draft
sha:
---

## 原始输入

在 ~/workspace/tracks 上实现 Agent on Tracks 的核心引擎，使 M-START -> M-STORY -> M-SPEC 三个阶段可以用 `trac` CLI + FakeAgent 端到端走通。不依赖任何 LLM，全部行为可由事件日志回放验证。

## 用户意图

- 开发者在自己的项目里用一条命令启动 release 流程，系统自动建分支、初始化事件日志、进入 M-START
- 启动后流程按状态机自动推进：dispatch Agent（v1 为 FakeAgent）、跑机器校验、等人类裁决/评审、循环直到通过
- 人类通过 CLI 命令参与：裁决（go/no-go/park）、评审（comment/no-comment）、编辑文档（直接改文件）
- 全流程确定性可回放：同一事件序列永远产出同一状态，进程中途退出后可从事件日志恢复继续
- 人类不需要理解事件溯源——他看到的只是"系统问我要不要继续（带背景知识），我说要，系统接着跑"

## 核心操作路径

1. `trac init`：在当前项目初始化 .tracks/ 目录
2. `trac start v0.1`：检查工作区干净 -> 创建 releases/v0.1 分支 -> 创建 projects/v0.1/story.md（仅原始需求）-> 记录 stage.entered(M-START)
3. `trac run`：启动引擎主循环，进入 M-STORY
   - TRIAGE：dispatch Scribe(FakeAgent) 探索 -> 返回 GO/NO-GO/PARK 建议 -> 等人类裁决
   - `trac triage go`：进入 DRAFT
   - DRAFT：dispatch Scribe(FakeAgent) 按 template 写 story.md -> validate(schema+scope) -> 通过则 commit
   - SAGE_REVIEW：dispatch Sage(FakeAgent) 评审 -> verdict(pass) -> commit -> 进入 HUMAN_REVIEW
   - HUMAN_REVIEW：awaiting_human -> `trac review no-comment` -> 双方通过 -> EXIT
   - EXIT：生成 sha 写入 frontmatter -> commit -> stage.exited(M-STORY)
4. 引擎自动进入 M-SPEC
   - DRAFT：dispatch Sage(FakeAgent) 写 spec.md -> validate(schema+scope+trace) -> commit
   - LEX_REVIEW：dispatch Lex(FakeAgent) 评审 -> verdict(pass) -> commit
   - HUMAN_REVIEW：`trac review no-comment` -> EXIT
   - EXIT：格式终验通过 -> stage.exited(M-SPEC)
5. `trac status`：随时查看当前阶段、子状态、待处理事件
6. `trac replay <run-id>`：从事件日志回放，打印最终状态，与运行时状态一致

> Aaron: trac run 需要检查当前阶段，不一定都是从 M-STORY 开始

## 行为种子

| 行为                             | 验证断言（将来用什么事实验证我）                                                                           |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| start 创建 release               | 执行 start 后，存在以版本号命名的 release 分支，且 projects/ 下出现对应 story.md                           |
| 工作区不干净时 start 被拒绝      | 存在未提交修改时执行 start，系统拒绝并报告原因，不创建任何分支                                             |
| NO-GO 路径                       | 裁决 NO-GO 后，story 进入 backlog，release 分支不再存在                                                    |
| PARK 路径                        | 裁决 PARK 后，story 进入 backlog，release 分支不再存在                                                     |
| 全通过路径走完 M-STORY -> M-SPEC | 所有 Agent 返回 pass、人类均无评论时，流程终止于 M-SPEC 退出，事件日志包含完整的 stage.entered/exited 序列 |
| validate 失败触发重派            | Agent 产出 schema 不合格的文档时，系统不提交、不进入评审，而是重新分派同一 Agent 并附带失败原因            |
| 重派上限后升级 Human             | 同一校验连续失败 3 次后，系统停止重派，转为等待人类介入                                                    |
| scope_overflow 触发回退          | spec 中功能需求超过 30 条时，流程回退到 M-STORY 要求重新拆分，而非尝试压缩                                 |
| 事件回放产出同一状态             | 对同一 run 的事件日志做回放，得到的终态与运行时终态完全一致                                                |
| 可休眠与恢复                     | 流程在等待人类时中断进程，重新启动后恢复到中断前的精确子状态，不丢失上下文                                 |
| 单写者纪律                       | 两个引擎实例同时启动时，第二个拿锁失败并报错，不产生并发写入                                               |
| story.md 完整性绑定              | 流程完成后，story.md frontmatter 包含对文档内容的校验码                                                    |

## 范围排除

- 不接真实 LLM Agent（FakeAgent 是一等公民，不是临时替身）
- 不做 Web UI（CLI + 直接编辑文件 = v1 的全部人机界面）
- 不做 GitHub 集成（不创建 Issue、不关联 Project、不触发 CI）
- 不实现 M-ACC 及之后的阶段（M-DESIGN / M-IMPL / M-TEST / M-VERIFY / M-SECURITY / M-RELEASE / M-PUBLISH / M-MILESTONE）
- 不做反 slop 工具（trac check reach/budget/ratio/dup）——它们随后续 story 引入
- 不做 inline-comments 的完整协议（v1 评审意见以纯文本 diff 传递）
- 不做 Agent 会话状态保留（FakeAgent 无状态；真 Agent 的会话保留是另一个 story）

## 参考文档
- /.tracks/wiki/flow.md
- /.tracks/wiki/decisions.md
- /.tracks/wiki/arch.md
