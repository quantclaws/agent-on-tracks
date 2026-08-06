# Runtime 是唯一的 authority

AI 写的代码，你不一定敢上生产。
AI 说的"我跑过了"，你更不敢信。

LLM 没有"事实"的概念。它说"我跑过了"时，它不是在向你汇报，它只是那一瞬间概率最高的下一句话——它看不到 git status、看不到自己改了哪些文件、看不到 production 路径实际跑没跑。

下面两个具体场景能让你直观感受到这事有多严重：

**谎话一：Agent 说"需求都完成了"，但漏了不少**——LLM 没有枚举能力，看到 30 条 AC 的 spec 不会一条一条核对自己实现了哪些，会凭"哪些 AC 比较显眼"挑着做。Agent 自报"全部完成"，但 spec 里的边角案例根本没人写过。

**谎话二：Agent 说"测试过了"，但测的是 mock**——LLM 写代码时先 mock 一个外部 service、再写调用它的代码，测试全绿，但 production 里那个 service 根本没接。这是 TDD 落到 AI Agent 身上最大的风险：同一个 Agent 既写测试又写实现，能在两步之间互相迁就。

为什么 tracks 能拆穿这些谎话？因为它的 Runtime 用五件久经考验的工程装备把自己武装起来。这五件东西**不是 tracks 发明的**，都是几十年前就成熟的方法；tracks 只是把它们用在工作流这件事上。下面逐件说明。

## 装备一：事件溯源

Runtime 把"流程里发生过的每一件事"都写进 SQLite 的 `events` 表——只追加，绝不删改。

Runtime 想知道"现在到哪一步了"，它不读"当前状态文件"，而是把 events 表从头 fold 一遍，用纯函数 `project()` 算出当前状态。同样的事件序列永远产出同样的状态。

> [!info] Event sourcing
>
> 由 Greg Young 在 2000 年代中期正式命名并发扬；Martin Fowler 2005 年写的 [EAA catalog 条目](https://martinfowler.com/eaaDev/EventSourcing.html) 是另一篇关键文献，他后续在 2011 年的 [CQRS 文章](https://martinfowler.com/bliki/CQRS.html) 里把事件溯源与命令查询分离绑在一起论述。商业 / 开源实现包括 KurrentDB（原 EventStoreDB，Greg Young 本人创建）、Axon Framework（JVM 生态）、Marten（.NET 生态）、Akka Persistence（JVM 生态）。核心承诺：**当前状态 = 历史事件的可重放投影**，而不是某个被原地改写的"当前值"。
>
> 注意：Fowler 描述的是"领域事件溯源"，tracks 借用的是同一套机制（append-only + pure fold + replay），但事件是工作流控制事件（stage.entered / command.issued / outcome.received），被溯源的是开发流程的状态——这是控制流事件溯源 / audit log event sourcing，与 Fowler 那种"按业务动作切 + 配套读模型"的事件溯源不是同一物种。

## 装备二：写前日志

每次 Runtime 要做一件副作用的事（创建分支、commit、写文件、调 Agent），它**先**写一条 `command.issued` 事件进 `events` 表，**再**真正动手做这件事。做完之后再写一条 `outcome.received` 事件。

如果 Runtime 在"写 `command.issued` 之后、`outcome.received` 之前"被 `kill -9` 杀死，重启时它会发现"`command.issued` 已落盘、`outcome.received` 没有"——这条命令处于悬挂状态。Runtime 不重发命令，先去查 git / 文件系统 / 数据库，问"这件事现实世界里做完没"，再决定是补 outcome 还是重新执行。

> [!info] Write-Ahead Logging
>
> 起源于 IBM System R（1974–1979），由 Andreas Reuter 与 Theo Härder 在 1983 年的 ACID 论文中正式命名。PostgreSQL、SQLite、MySQL（InnoDB）、Oracle 等几乎所有关系数据库都用 WAL。核心承诺：**先写日志，再做副作用**——哪怕副作用失败甚至进程消失，日志里至少有"我打算做这件事"的痕迹。

## 装备三：单写者

任何时候，只有一个 Runtime 进程能往 `.tracks/runtime/` 里写。

你开了两个 `trac run`，第二个拿不到锁就立刻退出，并把持锁进程的 PID 打印到 stderr——它不会偷偷写一行、不会"轮询等锁"、不会"先看一眼锁再决定要不要等"。

> [!info] Single-writer discipline
>
> 思想源头是 Leslie Lamport 1978 年的 ["Time, Clocks, and the Ordering of Events in a Distributed System"](https://lamport.azurewebsites.net/pubs/time-clocks.pdf)；工业级实践由 Bolosky 等人 1996 年的 Paxos 工程化文章推到分布式系统。SQLite、Berkeley DB、Git 的 `.git/index`、早期 Linux kernel 的 `big kernel lock` 都是这条纪律的不同变体。单写者承诺：**任意时刻只有一个进程能改变持久化状态**——多写者带来的冲突、丢失更新、半写状态全部消失。

## 装备四：按 kind 调和

每个 `Command.kind` 都有自己的"探测现实世界"动作。Runtime 重启恢复时，对每个悬挂命令先调它的 kind-specific `reconcile()`：先查真实世界（git / 文件系统 / SQLite）是否已经做完，再决定要不要补执行。

| 命令 kind | 调和动作 | 判为已完成的条件 |
|----------|---------|----------------|
| `commit_document` | `git log --grep=<command_id>` 查提交 | 已有 trailer 含该 command_id 的提交 |
| `create_branch` | `git rev-parse --verify <branch>` | 分支已存在且指向 base |
| `dispatch_agent` | 查产物文件是否已存在 | 产物存在（覆盖写天然幂等） |
| `validate_document` | 纯读校验 | 天然幂等 |
| `complete_run` | 查是否已有 `run.completed` | 已有 |

> [!info] Reconciliation
>
> 在分布式系统里指"两份状态不一致时，怎么决定哪份是对的"。Amazon DynamoDB 论文（2007）把 reconciliation 写进了 anti-entropy 子系统；Marc Shapiro 等人的 CRDT 系列工作（2011）给出了数学化的对账理论；Kubernetes controllers 同样贯彻"声明状态 vs. 现实状态，调和到一致"。tracks 没有用 CRDT 或 consensus 协议——它用更朴素的办法：每个 kind 都有专属的"探测现实世界"动作。

这一条把 WAL 与单写者粘合起来：WAL 给你"曾经打算做"的证据、单写者保证不会有第二个人也"打算做"同一件事、按 kind 调和给你"现实世界到底做完没"的查询方式——三者加在一起，Runtime 才能在任意时刻被 SIGINT 打断、又能在重启时精确回到断点。

## 装备五：事后审计 Agent 的副作用

Agent 调用前后，Runtime 会独立做两次 git 检查：

1. Agent 启动前：`git status` 记下干净 baseline。
2. Agent 跑完之后：`git status` + `git diff` 比对 baseline。

如果 Agent 改了"目标文档 + 本次专属临时目录"之外的任何文件——outcome failed、记录路径级证据、不提交、不推进，且只回滚可证明由该 Agent 产生的改动，**绝不覆盖 Human 既有修改**。

> [!info] Principle of least authority
>
> 由 Jerry Saltzer 与 Michael Schroeder 在 1975 年的 [《The Protection of Information in Computer Systems》](https://www.ccs.neu.edu/home/cbw/courses/cs6750/saltzer_1975.pdf) 中提出，是计算机安全最古老的指导原则之一。Google 的 BeyondCorp、Linux 的 capability 系统、OpenSSH 的 `ForceCommand` 都是这条原则的工程化。tracks 的实现不是给 Agent 一份 frontmatter permission 清单（opencode 的 permission 是粗粒度的，无法表达"仅这一份文档 + 仅这一份临时目录"）——而是用 Runtime 层面的事后审计把这件事兜住。

## 合在一起：五件装备如何拆穿 Agent 的谎话

把这五件装备装到 Runtime 上，你得到的是**对自己说的话不信、对 Agent 说的话不信、只信自己能从真实世界重新读到的事实**的运行时。

但单看每件装备只能证明 Runtime 自身健壮。回到开头的两个谎话，把装备映射到具体防御：

| Agent 的谎话 | Runtime 的哪件装备拆穿它 |
|---|---|
| "需求都完成了" | 装备一（事件溯源）+ 双向 trace 清点——每条 AC 都靠 marker 在测试里实际绑定；没绑定的 outcome failed，不进入下一步 |
| "测试过了" | 装备四（per-kind reconcile）+ Shield-first / Devon-blind 结构——测试与实现分两段编、合一段跑，测试只能从接口与 AC 派生 |
| "我没改其它文件" | 装备五（事后 git diff 审计）——Agent 跑完 Runtime 亲自比对 baseline，Agent 自己说什么都不算数 |
| "我跑完了"（在进程层） | 装备二（WAL）+ 装备三（单写者）——`command.issued` 与 `outcome.received` 配对、`events` 表同一时刻只允许一个进程写 |

五件装备是同一种思想的五个表达：**只信程序能从文件系统、git、SQLite 重新读到的事实**。这是 tracks 与一切"prompt + LLM"产品最深的分歧——也是后面所有概念层、架构层文档的总依据。

## 行为种子

| 装备 | 对应 BS |
|------|------|
| 事件溯源 | "对同一 run 的事件日志做回放，得到的终态与运行时终态完全一致"（v0.1 BS-事件回放） |
| 写前日志 | "command.issued 与 outcome.received 共享 command_id，配对不依赖位置顺序"（v0.1 §5d） |
| 单写者 | "两个引擎实例同时启动时，第二个拿锁失败并报错，不产生并发写入"（v0.1 BS-单写者纪律） |
| per-kind reconcile | "恢复悬挂命令时先 reconcile 再决定是否 execute，绝不盲目重跑"（v0.1 §5e） |
| 事后审计 | "目标文档 + 本次专属临时目录之外的 diff → outcome failed、记录路径级证据、不提交、不推进"（v0.2 BS-11） |

每条装备都有可验证的 AC，ac ↔ BS 编号在 acceptance.md 里一对一对得上。

> [!info] Behaviour seed
>
> tracks 自己提出的术语，定义在 `tracks/templates/story.md` §3.x。每条行为种子是一句 EARS（Easy Approach to Requirements Syntax，Alistair Mavin 等人于 2009 年在 Rolls-Royce 提出，2012 年发表于 INCOSE International Symposium）格式的断言，由 spec 与 acceptance 双向追踪、由测试绑定。