# v0.1 规划文档评审 — Round 3

结论：**REVISE**

本轮不再重开已由 Human 裁定的事项：FakeAgent 只验证确定性工作流、不限制模块数量、未来阶段允许逐版修改 kernel、`command.issued(dispatch_agent)` 是唯一派发事实。AC-29a 已按 Human 确认修正；D-12、D-13、SQLite、status 终态、happy-path run 边界和 append-only 测试均有实质改善。

## 阻塞问题

### R3-01 当前事件/命令合同仍不足以让用户走完整条状态机

定位：`interfaces.md:39-84,112-134,136-179`；`architecture.md:26-46`；`.tracks/wiki/flow.md:161-178,239-252`。

现有主循环只能执行 `Command` 并追加其单个结果事件，但接口没有说明以下关键事实如何产生：

- `stage.entered` / `stage.exited` 没有对应 command kind；
- `review.round_started` 不在事件全集中，但 `State.review_round` 和“同轮 reviewer pass”依赖它；
- Flow 要求的 `story.requested`、`writelock.granted/released`、`interview.started/ended` 未进入接口；
- Sage/Lex 应返回 `pass|comment`，但通用 `Outcome` 只有 `done|blocked|failed`、artifact 和 self_report，无法可信地产生 `sage.verdict` / `lex.verdict`；Runtime 又明确不能信任 self_report。

结果是文档列出了状态和事件，却没有一条完整、类型化的“当前状态 → command → result event → 下一状态”生产链，实现时只能临场发明。

要求：补一张 v0.1 command→result 矩阵；补齐 stage/review-round 事实的产生方式；为 triage、文档产出、Sage/Lex review 定义不同的 Outcome payload。然后用该矩阵逐步走读 happy path 和 RESPOND path。

### R3-02 NO-GO/PARK 会删除用户需求，但 backlog 事件没有保存需求

定位：`interfaces.md:43-58`；`spec.md:44-48`；`acceptance.md:73-78`；`story.md:46-50`；`.tracks/wiki/flow.md:163-170`。

`backlog.recorded` 目前只有 `version/decision/reason`。NO-GO/PARK 随后删除 release branch，story.md 随分支消失；接口又漏掉了 Flow 已列出的 `story.requested`。因此 backlog 投影最多显示“v0.1 被 park”，无法显示被 park 的原始需求、story identity/title 或内容引用，用户不能再查看或恢复该故事。

要求：在删除分支前，把稳定 story identity、title、原始需求或内容寻址 artifact ref 作为事件事实保存；backlog 只投影该事实。增加 E2E：删除分支后仍能从 backlog 读出原需求。

### R3-03 branch reconcile 的完成判据不满足命令后置条件

定位：`architecture.md:175-192`；`interfaces.md:74-84`；`spec.md:36,46`；`acceptance.md:42-46,73-78,205-208`。

- `create_branch` 的语义是“创建并切换”，但 reconcile 只检查分支存在且指向 base；若崩溃后 HEAD 仍在 main，它会误判完成并跳过 checkout；
- 若同名分支已存在但不指向 base，当前“未完成动作=创建”必然因分支已存在而失败，未定义 conflict/needs_attention；
- `delete_branch` 前必须 checkout main，现有 command 集没有 checkout command，reconcile 也只检查分支是否存在；
- AC-29d 只覆盖 commit，未覆盖最容易破坏用户旅程的建/删分支恢复。

要求：按完整后置条件 reconcile。可增加 `checkout_branch`，或把 create/delete 定义为原子领域命令并显式处理 existing-diverged conflict。补 create 成功未记结果、delete 前后崩溃两类 E2E。

> **Aaron:** 用 create/delete

### R3-04 核心 Schema 仍然是无约束 dict

定位：`interfaces.md:14-19,23-35,39-84`。

信封身份和 State Literal 已修正，但 `EventEnvelope.payload: dict`、`Command.params: dict` 仍把最关键的结构校验推迟到运行期，并明确把 per-event 判别联合留给未来。这与“v0.1 先建立类型化事件/命令契约”的设计目标不符，也使 R3-01 的 command→result 闭环无法由类型系统保证。

要求：v0.1 事件和命令使用具体 dataclass + discriminated union；SQLite 只负责把该类型编码为 JSON。只需覆盖 v0.1 的封闭集合，不需要预定义未来阶段。

## 已确认但尚未落入基线

### R3-05 `trac init` 的新 Human 要求没有进入 Decisions/Spec/AC/Test Plan

定位：`round-2.md:89-97`；`spec.md:33-38`；`acceptance.md:21-27`；`test-plan.md:58-59`；`.tracks/wiki/decisions.md:118-142`。

Human 已明确要求 init 检查：当前目录、Git repo、`gh` 是否存在、gh 是否已授权、repo/project scopes 是否齐备，信息不全时要求用户输入。当前基线仍只描述创建脚手架并自动提交，Decisions 也没有记录该裁定。

此外 `spec.md` 写“runtime 的 `.gitignore`”，而 wiki arch 要求根 `.gitignore` 使用 `/.tracks/runtime/`；若整个 runtime 被忽略，内部 `.gitignore` 本身通常也无法提交。

要求：新增 Decision 并同步 FR/AC/Test Plan；明确这些 gh 检查是 init readiness、不是 v0.1 GitHub 集成副作用；统一根 `.gitignore` 合同；覆盖非 Git repo、缺 gh、未授权、scope 不足、非 main/无 main、Git author 缺失。

## 其他一致性问题

### R3-06 最终 spec 的 sha 合同仍冲突

定位：`.tracks/wiki/decisions.md:44-48`；`spec.md:60-65`；`acceptance.md:154-164`；`interfaces.md:52-53`。

D-03 说 v0.1 包含 `spec.md` 落 sha，但 FR-23/AC-23 只要求格式终验和 stage exit，`spec.committed` 也只有 commit_sha。需要二选一：实现 spec sha 并验收，或修正 D-03。

### R3-07 blob 与 SQLite 之间仍有崩溃窗口

定位：`architecture.md:194-203`；`acceptance.md:193-199`；`.tracks/wiki/decisions.md:35-42`。

payload >8KB 时，blob 文件和 SQLite event 不在同一事务中。若 event `$ref` 已提交而 blob 尚未持久化，replay 会得到悬空引用。当前 AC-28b 只测正常读回，没有测崩溃顺序。

要求：定义“临时文件写入→fsync→按 sha 原子 rename→提交 event”的顺序；允许事务失败留下无害 orphan blob，但不允许已提交 event 指向缺失 blob。补故障注入测试。

## 通过门槛

1. command→result→state-transition 矩阵能逐步走完整 happy/RESPOND/rollback/reject 路径；
2. NO-GO/PARK 删除分支后需求仍可从 backlog 恢复；
3. 所有 Git command reconcile 满足完整后置条件；
4. v0.1 Event/Command 无自由 dict 核心契约；
5. init 的 Human 裁定进入 Decision、FR、AC 和 E2E；
6. spec sha 与 blob 崩溃语义闭合后，再进入脚手架或业务实现。
