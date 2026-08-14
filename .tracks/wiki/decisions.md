---
doc: decisions
status: active
last_updated: 2026-08-07
---

# 已决定事项

> 本文档记录人类裁定（或从语境直接推出）的事项。任何"已决定"必须来自：
>
> - 用户在对话中的明确表态；
> - 已审定的 v0.1 story；或
> - 已审定的 arch/flow 段落。
>
> 技术实现细节（schema、字段命名、模块划分）**不进入本文档**，由 agent 自行推导；本文档只沉淀"用户层与产品边界"的决定。
>
> 与本文件属性不同的待决、暂行、悬置事项，必须迁到 `open_questions.md`，不能并存。

---

## D-01. 目录与运行时命名

- 项目根目录：**`~/workspace/tracks`**
- 命令行命令：**`trac`**
- Python 导入包名：**`tracks`**（2026-07-30 用户裁定：文档与代码一律使用 `tracks`，不用 `track`；与数据目录、PyPI 名一致）
- PyPI 包名：**`agent-on-tracks`**
- 项目数据目录：**`.tracks/`**（与导入包名一致）

> 早期另有 `.track/` 残留以避免与 GitHub Pages 经典项目心智撞车 + 让目录名与命令/包名不冲突。已清理 `.track/`。

## D-02. 真相源与存储：SQLite 事件溯源（自 v0.1 起采用）

**问题（用户提出，2026-07-30）**：events 用 JSONL，是这版实现从简，还是"80% 工程都不必用数据库、JSONL 就够"？考虑到 backlog 要实现、且 **v0.15 质量看板必须落地**，sqlite 事实上不可避免——若无特殊理由，是否应从一开始就用数据库，否则后面有不必要的迁移成本？此外长期多版本（v0.1…v0.15）下，扁平 JSONL 会否出现文件名/归属冲突？

**结论/裁定**：自 v0.1 起即以 **SQLite 事件溯源**为存储，不走"先 JSONL 后迁移"。

- **数据模型（承重、永久）**：事件溯源。`events` 表 = **唯一真相源**，只追加（INSERT，`seq` 每 run 单调递增，主键 `(run_id, seq)`）；`runs` / `backlog` / 当前态等为**派生投影表**，drop 后可从 `events` 完整重建。**禁止**状态式原地改行。
- **物理存储选 SQLite 的理由**：① v0.15 看板需跨 run / 跨版本 SQL 聚合，DB 不可避免——与其"JSONL 真相 + 事后加 sqlite 缓存"两套机制，不如**一套**；② ACID 事务令"落事件 + 更新投影"原子化，**消掉 torn-write 半行恢复**这一类代码；③ 标准库 `sqlite3`，零新增依赖；④ `runtime/` 已整目录 gitignore，事件不进 git，JSONL 的可 diff/可 review 优势不成立。
- **多版本归属**：事件信封与 `runs` 投影均含 **`version` 字段**（发布版本，如 `0.1`），作为长期多版本的一等查询维度（看板 `GROUP BY version`）。**不**靠目录层级或文件名版本号（会切碎日志、反害跨版本聚合）；ULID `run_id` 本已全局唯一，无命名冲突。
- **两根版本轴勿混**：`schema_version` = 事件 payload 结构版本（随 trac 发布演进、供 upcast）；`version` = 宿主项目发布版本。
- **唯一被禁止的动作**：让任何投影/索引成为权威源或持有决策字段——那才会造成真正的迁移代价。
- **blobs**：payload >8KB 仍内容寻址落 `runtime/blobs/{sha256}`，事件里放 `$ref`，保持 db 精简。

## D-03. v0.1 体验范围

- v0.1 范围：**M-START → M-STORY → M-SPEC**，再到 `stage.exited(M-SPEC)`，包含 `spec.md` 落 sha 为止。
- v0.1 不实现：M-ACC+、反 slop 工具、GitHub 集成、Web UI、inline-comments 完整协议、真 LLM Agent。
- **入口澄清（回应 R2-09）**：`trac start <version>` 从 stdin 接收原始需求（D-08），是 v0.1 唯一入口——它走**固定** M-START→M-STORY→M-SPEC 通路。这与"丢任意已有 story 文档直接生成 spec"的**通用入口**是两件事：后者属 **v0.2+**，v0.1 不做（见 story §范围排除）。故 `start <version>` 与 D-08 不冲突：version 参数在 v0.1 仅接受 `v0.1`。

## D-04. v0.1 CLI（人类入口全集）

```text
trac init
trac start v0.1
trac run
trac triage go|no-go|park
trac review no-comment
trac review revise
trac status
trac replay <run-id>
```

- `triage`：`human.triage(...)` 的人类命令入口，命令参数与写入事件类型一一对应。
- `review no-comment | revise`：评审意见落地，纯文本 diff。无完整 inline-comments。
- `status`：状态查询命令；与 `replay` 是不同语义。
- `replay <run-id>`：执行动作——回放事件并打印，不与 `status` 合并。

> flow.md 第 92 行（mermaid RESPOND 状态）的语义等同上述 `review revise`。

## D-05. 状态查询 vs 回放

- `trac status` 只读取当前活跃 run 的最末状态、子状态、待处理事件——**不**回放全部事件。
- `trac replay <run-id>` 是执行类动作：读取该 run 的全部事件并打印，再补终态摘要（选项 B）。
- 这两个命令是**两类语义**，不能合并为一条；任何状态查询不应用 `replay` 的全 fold 实现冒充。

## D-06. NO-GO / PARK / scope_overflow 的分支命运

- **`human.triage(no_go)` / `human.triage(park)`**：记入 backlog + 删除 `releases/v0.1` 分支（flow §4.1 REJECTED）。v0.1 的 backlog 是**最小实现**——`record_backlog` 命令追加 `backlog.recorded` 事件，投影为 `backlog` 表（无独立子系统/UI）；分支删除后 story.md 随之消失，条目仅留存于 backlog。
- **`scope_overflow (FR>30)`**：属于需求调整，**不删分支**；保留 release branch 历史并返回 M-STORY 重新切片。（历史保留的具体机制——是否打 tag、如何命名——尚未经用户裁定，留待 spec 明确，不在此臆造。）

> 用户裁定：NO-GO/PARK 路径下"新建分支只包含 story.md，因此删除分支无可惜"。

## D-07. 错误恢复与单写者

- **双进程同跑两个 `trac run`**：第二个进程**立即失败并退出**，stderr 报告 lock holder（行为种子 §11）。
- 不存在"等锁"路径——双引擎并跑只在出错时发生，不做轮询退让。
- 单写者锁：`runtime/lock` 文件 + 进程 PID；锁失效（崩溃残留）如何处理由 agent 自决。

## D-08. v0.1 原始需求入口（story.md 来源）

- `trac start v0.1` **从 stdin 接收原始需求**。
- 原始需求被 Runtime 写入 `.tracks/projects/v0.1/story.md`，并设置 frontmatter（含 `story_id`、`created`、`status: draft`、空 `title`、空 `sha`）。
- **`title` 字段**（用户裁定 2026-07-30）：frontmatter 必含 `title`——人类讨论问题时用有意义的名字引用一个 story，而非编号（人不擅长记数字）。start 时留空；由 Scribe 在 DRAFT 起草时写入非空的有意义名，schema 校验其非空（FR-11）。与 `sha`（start 留空、EXIT 填入）同构。
- 之后 `trac run` 启动 M-STORY 主循环。
- 用户无须先自行创建 story.md 文件；也不接受 `trac start` 通过文件路径传原始需求——v0.1 体验以 stdin 入口为准。
- 如未来 v0.2+ 支持 `--story <file>` / 配置路径等通用入口，另立决策条目。

## D-09. 用户参与方式

v0.1 全部人类动作通过 CLI 命令传入（见 D-04）。人类不编辑事件文件、不直接动 `tracks.db`、不绕过 `trac` 直接 push。

## D-10. 文档与流程之间的关系

- arch.md / flow.md：已审核的架构文档，与本文档并列存在，**不互相覆盖**。
- v0.1 story：v0.1 范围与行为种子的唯一真源；它与 arch/flow 不一致时，以 **story 优先** + 进入 `open_questions.md` 待处理。
- new wiki page：`decisions.md`（本文件）沉淀已裁决的产品层决定；`open_questions.md` 沉淀待决与暂行；agent 推导技术细节不入 wiki。

---

## D-11. 取消协议：意图走信号，事实走日志

- 取消一个正在执行的 run 的唯一通道是向持锁进程发 OS 信号（PID 在 `runtime/lock` 中）；由唯一写者（runtime 自己）将其转成事实：终止 Agent 子进程、落 `run.interrupted` 事件、释放锁、exit 130。单写者不变量不破。
- v0.1 前台运行，Ctrl-C 即此通道；**不新增 `trac cancel` 命令**（D-04 CLI 集合不变），`cancel` 推迟到接入真实 LLM Agent 的版本。
- 取消发生在 `command.issued` 已落盘而结果未落盘时：恢复后 decide 重新签发同一 assignment，**不消耗 attempt**（取消是人类决定，不是 Agent 失败）。

---

## D-12. dispatch 事件模型：`command.issued` 即派发事实

（用户裁定 2026-07-30，回应 R2-02）一次 `dispatch_agent` **不产生**独立的 `assignment.dispatched` 事件——删除该事件类型。`command.issued(dispatch_agent)`（其 payload 已含 `assignment`）本身就是**唯一的 write-ahead 派发事实**：它在 executor 阻塞执行 Agent 之前已落盘，Agent 返回后落 `outcome.received`。正常/hang/SIGINT/kill-9 四条时序都由这一对"issued→（阻塞）→outcome"表达，悬挂即 issued 无 outcome。理由：单结果主循环无法一命令产两事件，且 assignment 本就在命令 payload 内，第二个事件冗余。

## D-13. 副作用可恢复性：per-kind reconcile

（用户裁定 2026-07-30，回应 R2-03）稳定 `command_id` 只能**识别**操作、不能使其幂等；崩溃可能发生在 git/文件写已成功、结果事件未落盘之后，SQLite ACID 管不到边界另一侧。故每个 `Command.kind` 定义 **`reconcile（查真实世界事实）→ execute if needed → observe`** 规则：恢复悬挂命令前先查 git/文件系统实际状态，已完成则跳过、仅补记结果事件（如 `commit_document` 先 `git log --grep=<command_id>` 查该提交是否已存在）。杜绝重复提交/空提交/重复删分支。逐 kind 规则见 architecture §5e。

## D-14. 产物正确性的职责划分：形式校验（runtime，可测）vs 语义翻译（LLM + 评审 Agent）

（用户裁定 2026-07-30，回应 R2-01）Agent 产物的"对不对"分两层，责任主体不同，不可混为一谈：

- **形式/schema 校验 = runtime 的确定性职责、可测**：Scribe 产出的 story.md、Sage 产出的 spec.md 若不合规（schema、scope、story→spec 结构化覆盖 trace），`validate` 必然抓住；e2e 测的正是这层管路是否贯通 + 前进性。
- **语义翻译忠实度 = 不可确定性测试**：story→spec 是否"翻译"到位取决于 LLM 能力；引用/slop（"BS"）是否成立，由**评审类 Agent**（Lex/Prism + 反 slop 的引用/trace 检查）把关，**不是** runtime 测试能覆盖的。
- **stdin→story 血缘**已由 AC 断言（start 把 stdin 原文写入 story.md），属形式层、已覆盖。

**结论**：不为 story→spec 语义关联新增确定性 e2e 断言——用"回显哨兵"去测这段只会制造**虚假信心**。R2-01 不采纳。绿灯含义据实限定为"引擎管路贯通 + 形式校验生效 + 前进性"，**不**宣称覆盖语义血缘。（此前 R2-01 内联"LLM 随机"的理由由本条取代为职责划分。）

## D-15. runtime 根解析与测试隔离

（用户裁定 2026-07-30）测试**绝不能**读写 tracks 项目自身的 `.tracks/`，运行时 `runtime/` 目录也不复用，否则污染/损坏本项目数据。

- **runtime 根按运行时 cwd 解析**：`trac` 就地在当前工作目录建立与读取 `.tracks/`（含 `runtime/tracks.db`、`blobs/`、`lock`）；**绝不 hardcode 到项目根**。
- **可注入 override**：支持环境变量 `TRACKS_HOME` 覆盖 runtime 根，供测试与特殊部署使用。
- **测试隔离**：每个 E2E 测试用 pytest `tmp_path` 建全新临时 git repo 并在其中运行 `trac`，`tracks.db`/`blobs`/`lock` 全落临时目录，测试结束即弃。
- 反映到 spec.md FR-01（就地 cwd 建 `.tracks/`）与 interfaces §11（路径相对 cwd/`TRACKS_HOME`，而非项目根）。

## D-16. v0.1 不实现格式校验判据（validate_document 恒 pass）

（用户裁定 2026-07-30）现有 spec 反复引用 `validate_document(schema/scope/trace)`，却从未定义"合法格式"，属欠规格。v0.1 决定：

- **v0.1 不实现真实格式校验**：`validate_document` 退化为**恒 pass**，至多做"文件存在 + frontmatter 可解析"最小检查。FR-11/FR-19 的 schema/scope/trace 具体判据标记为 **v0.2+**。
- **保留 NFR-03 结构**：verdict 仍由 Runtime 产出（不信 Agent 自述），v0.1 恒真；FakeAgent 仍可用 `simulate=*_bad` 注入失败，走通 ≤3 次重派 + escalation（FR-12），使该支路可测。
- **与 D-14 关系**：D-14 确立"形式校验归 runtime、可测"的**职责归属**不变；D-16 只是把该职责的**判据实现**推迟到 v0.2+，v0.1 先以恒 pass 打通管路。
- **sha 落地不受影响**：sha 是完整性锚点、与格式校验无关，FR-17/FR-23/AC-23b 全部保留。

## D-17. Devon R-G-R

在 Devon 单元测试通过后，运行质量检测工具（认知复杂度，超长文件、超长方法、重复代码检测），执行重构。
质量检测工具，特别是认知复杂度，超长文件，越长方法都不针对测试文件

## D-18. 真实外部依赖的三层验证机制（fake 每次跑 / live 周期跑 / milestone 硬门禁）

**问题（用户提出，2026-08-01）**：像真实 opencode、真实 GitHub、真实网络这类**外部依赖**，不应在每次测试跑（慢、需凭据、污染），但**MILESTONE 之前必须至少真实跑一次**，否则 live 通道腐烂而无人察觉。要如何设计，才能既"平时不阻塞"、又"发布前强制验证"？更进一步——tracks 要运行在**宿主项目**时，Archer 必须能**自动为宿主项目也设计出同样的三层机制**，并**只在恰当的时候触发**，而不是靠碰巧。

**业界标准：三层机制（当作测试设计模式固化）**

这是"真实外部依赖"类的通用解法（可复用、可判据化），**机制语义与具体工具解耦**——下方用本项目的 pytest/GitHub 作为具体实例，但宿主项目落地时由 Archer 依据其既定技术栈选择等价工具（测试框架的 exclude/only 过滤、CI 平台的 secrets + tag/cron 触发器、mock 框架等）：

1. **通道隔离**——live 测试标记为独立通道，默认套件排除（本项目：`@pytest.mark.live` + `addopts -m 'not live'`；先例：`performance` marker）。
2. **环境探测 + skip（不 fail）**——live 测试的 fixture 检查外部凭据/可执行文件是否齐备；缺失则 skip 并输出显式 `LIVE_SKIPPED: missing <X>`（对齐 AC-0105"缺凭据 skip 不 fail"）。
3. **周期/里程碑 job 强制**——CI 独立 job 只跑 live 通道，**配真凭据**（secrets），触发策略二者其一或兼有：① release/tag 触发（milestone 门禁，job 必须 pass 才能发版，**硬 gate**）；② nightly cron（回归真实通道，防止腐烂）。

**关键纪律**：
- **skip ≠ silent**：skip 时输出显式 `LIVE_SKIPPED: missing <X>`，milestone 前人工核对"非空跑"（空跑 = live job 全 skip 仍绿，等于没验证）。
- **fake 是每次、live 是周期**：二者 AC 不重叠；fake 保证日常回归 + 前进性，live 只保证"真实通道不烂"。
- **单测站岗、live 验证**：`mock` 外部依赖的单测（**不走网络**）每次跑，覆盖分类/边界逻辑；`live` 网络测试只在里程碑/夜间跑。两者互补，单测不能被 live 替代，live 也不能被单测替代。

**判据——何时才需要 live 通道（宿主项目 Archer 据此判定）**：

- **触发模型 = B + A 兜底**：主判据（B）触发——**spec 中出现宿主自身技术栈之外的外部依赖**（外部服务 API、模型 provider、子进程可执行文件、真实网络/凭据握手，或任何"只能由真实环境验证的行为"：权限实际生效、凭据握手、真实产物格式）；辅以（A）Archer 交付 test-plan 时必跑 checklist：**扫描 spec 是否存在外部依赖 → 存在则必须已产出三层机制**，作为强制兜底。
- **技术栈无关**：opencode、GitHub、pytest 都**不是**触发依据——它们只是本项目宿主的技术栈偶合。判据只看"是否存在超出宿主自身技术栈、仅在真实环境可验证的依赖"。宿主是 python 或非 python、依赖 GitHub 或 GitLab/内部服务，不影响判据成立与否。
- **方案的生成绑定 Archer 的技术栈决策**：三层机制的**具体实现**（测试框架过滤、CI secrets/tag/cron、mock 框架）由 Archer 依据其之前为宿主定下的技术栈选择，不预设 python/GitHub。
- 判定在**本质上是 Agent 的主观判断**——高能力 Agent 依据 spec 内容自行判正误，不依赖外部检查器；上述判据是可判定的启发式锚点，不是机械规则。
- 仅当**确实没有任何外部依赖**（纯本地确定性逻辑）时，才允许跳过 live 通道。

**本项目的落点**：tracks 自己的 live 通道 = `pytest -m live`（`e2e_live/test_live_agent.py` 真实 opencode + `test_github_live_network.py` 真实 GitHub）。`test_github_live.py`（mock urlopen）是"单测站岗"那层，每次跑；新增的 network 测试是"live 验证"那层，里程碑/夜间跑。

**要求 M-DESIGN 固化（细化待 M-DESIGN）**：这条三层机制要在 M-DESIGN 阶段作为**可复用测试设计模式**进入 Archer 的能力——当宿主 spec 出现"宿主技术栈之外的外部依赖"判据，Archer 自动产出（通道隔离 + 环境探测 skip + CI milestone 硬门禁）三件套，实现层依据宿主技术栈选择等价工具，而不是依赖遇到新项目现场想。状态机泛化（StageDef 表驱动）应把"外部依赖 → 该阶段测试计划模板"作为可推导规则绑定，使 O(1) 接入时 live 通道测试也被自动生成。

## D-19. Harness 配置与 Agent 提示词分离；`trac init` 改写 harness 配置

（用户裁定 2026-08-01）Agent 提示词（`tracks/agents/*.md`）保持 **harness 无关**——不含 permission 块、不引用特定 harness 的配置文件名。harness 特定的权限与目录访问控制放在 **harness 自己的项目级配置文件**中，且以 **per-agent** 方式设置（不写全局 `permission`，避免与宿主项目设置冲突）。`trac init`（adoption 阶段）负责改写宿主项目的 harness 配置。

**当前 harness = opencode 时**，`trac init` 改写项目根 `opencode.json` 的 `agent` 节（若已有 opencode.json 则合并，不覆盖宿主既有配置）：

```json
{
  "agent": {
    "Scribe": { "permission": { "external_directory": { "*": "deny", "{env:TMPDIR}tracks/*": "allow" } } },
    "Sage":   { "permission": { "external_directory": { "*": "deny", "{env:TMPDIR}tracks/*": "allow" } } },
    "Lex":    { "permission": { "external_directory": { "*": "deny", "{env:TMPDIR}tracks/*": "allow" } } }
  }
}
```

- `external_directory: {"*": "deny", "{env:TMPDIR}tracks/*": "allow"}`——阻断 agent 访问项目 worktree 与 `$TMPDIR/tracks/` 之外的一切目录。`{env:TMPDIR}` 是 opencode 环境变量替换语法；**注意**：`$TMPDIR` 在 macOS 以 `/` 结尾，故 pattern 用 `{env:TMPDIR}tracks/*`（不加额外 `/`）；Linux 上 `$TMPDIR` 可能无尾 `/`，`trac init` 生成配置时应解析并规范化路径（确保有且仅有一个 `/`），或写入字面值。
- per-agent 设置仅影响 Scribe/Sage/Lex，不干扰宿主项目的其它 agent 或全局配置（spike 已验证）。
- Runtime 将 command_id 专属临时目录创建在 `$TMPDIR/tracks/trac-{command_id}/` 下。

**Agent 提示词的权限约定**（harness 无关，写在提示词正文中）：

- 读：不限。
- 写：仅 assignment 指定的目标文档（Scribe = story.md；Sage = story/spec/acceptance；Lex = spec/acceptance via discuss）。
- bash：不限（harness 层已阻断外部目录；项目内越权写由 Runtime 后置 git 审计检出并撤销）。
- 临时目录：assignment 提供的 command_id 专属路径。

**换 harness 时**（如将来的 pi）：只需替换配置文件（`opencode.json` → `pi.json` 或等价物），agent 提示词不动。临时目录路径可能需要适配新 harness 的内置放行路径——这是 Runtime 的 harness 适配层职责。

**Spike 依据**（opencode 1.18.1，2026-08-01）：

- 全局 `external_directory: "deny"`（`opencode.json`）对无 permission 块的 agent 生效，同时阻断 read 工具和 bash。但全局设置会影响宿主项目所有 agent，故 tracks 采用 **per-agent** 设置（`agent.Scribe.permission` 等），仅约束 Scribe/Sage/Lex（spike 已验证：per-agent deny 阻断目标 agent，maestro 等不受影响）。
- agent frontmatter 中的 permission 会覆盖 opencode.json 的 per-agent 配置（如 maestro 的 `external_directory: ask`）。tracks agent 不含 permission 块，故 opencode.json 的 per-agent 设置生效。
- `$TMPDIR/opencode/*` 是 opencode 内置放行路径，deny 不影响。
- `mode: subagent` 不能被 `opencode run --agent` 调用（fallback 到默认 agent）；tracks agent 已移除 mode 字段（默认 `all`）。
- object-style permission pattern 在 **frontmatter** 中可被解析但执行不可靠（`edit` 的 sub-pattern 匹配工具名而非文件路径）；array-style 报配置错误且阻塞所有 agent 发现。但在 **opencode.json** 中，`external_directory` 的 object-style pattern（glob → action）经 spike 验证可靠执行（`{env:TMPDIR}*` 放行 + `*` deny 阻断）。frontmatter 仅用 shorthand `allow/deny`；细粒度目录控制放 opencode.json。

## D-20. 交付面完整性（surface completeness）

（用户裁定 2026-08-04，移植自 louke STR-1406 D-02）**每个 FR 必须有命名的交付面（UI / API / CLI / public library 皆算）与可观察出口；FR 无交付面不是设计问题，而是需求缺口。**

责任链（前移防线，不靠单点兜底）：

1. **M-STORY**：Scribe interview 将 delivery surface 列为必问槽位——seed 未说明交付面时必须问清并记入 story.md 产品决定。
2. **M-SPEC**：Sage 立硬规则——spec 起草/评审中遇到无交付面的 FR 即 revise 并退回需求路径，不得放行给下游补猜。
3. **M-DESIGN**：Archer 不发明 UI 补产品缺口，缺口回传 spec；Prism 评审维度含"FR 无命名交付面 → REVISE"。

判词出处：louke v0.14-004 事故复盘——实现未接入 http route 致 e2e/int 覆盖为零，复盘判词（STR-1406 D-02）明确"真实表面由宿主声明，UI/API/CLI/library 适用同一真实性原则"。tracks v0.2 及之前未移植该判词，v0.3 补齐。

## D-21. ISLAND_GATE_1 六元组前置到设计期

（用户裁定 2026-08-04）**flow.md §10 ISLAND_GATE_1 的六元组（owner / surface / composition / wiring / test / evidence）是 Archer 的设计期义务，不是 M-IMPL 才第一次出现的检查。**

- Archer 对每条 required AC 填齐六项事实并在设计文档中可定位；architecture.md 必须包含 composition root 一节；任何无法在六元组中定位的模块是设计缺陷（接回入口→AC 路径或从设计删除，不得设计"只被测试调用的模块"）。
- Prism 的 M-DESIGN 评审判据即六元组逐项闭合，缺项 REVISE。
- M-IMPL 的 ISLAND_GATE_1/2 是该合同的复核，不是首次建立。

出处与动机：louke 三个坑的元分析——判词记录得好、强制总落后一代（孤岛判词在 arch.md §8、gate 设计在 flow.md §9、reach 工具在 v0.4，当前阶段裸奔）。本决定把强制提前一代：M-DESIGN 的产物就是 ISLAND_GATE_1 的输入合同。canonical 来源：louke STR-1406（BS-01 接口桩、BS-03 接线义务、D-05 覆盖率非证据）。

## D-22. ground truth 最小化与条件适用；M-DESIGN 脚手架收窄

（用户裁定 2026-08-04）

- ground truth 是**最小可运行验证脚本**（如 code-stats 项目就是 `wc -l` 级别），不是功能实现；是否适用由 test-plan §3 判定，并非每个项目都需要。
- 完整 CI workflow/git hook/linter 配置不属 M-DESIGN 脚手架（归 M-IMPL），Archer 只在 machine contracts 定义合同。
- 证据：run049/050/053 中旧措辞「完整实现参考实现」导致单次 DRAFT >22min（含 .github/.githooks/src 全套脚手架）。

## D-23. live E2E 迭代方法学：基线恢复 + 观测定预算

（用户裁定 2026-08-04）

- 迭代不重跑全程——M-REQ-APPROVED 时刻捕获基线快照，迭代只恢复基线重跑 M-DESIGN（releases/v0.3 已实现 `test_journey_from_req_approved_baseline`）。
- 派发预算不靠猜——用事件时间戳 + opencode 日志观测真实耗时，超时配置只做看门狗（`TRAC_LIVE_DESIGN_AGENT_TIMEOUT` 可配）。

## D-24. 脚手架物化边界两分（修订 D-22 收窄条款）；GT 措辞去项目特例

（用户裁定 2026-08-04）M-DESIGN 物化判据两条：

- ①凡 M-TEST collect 依赖的宿主文件（build/包配置、目录布局、接口桩树、测试 runner 配置、fixtures/data、条件性最小 GT）必须 Archer 物理交付。
- ②声明性质量守卫配置（lint/pre-commit/CI 骨架/hook 脚本）同由 Archer 按 Scaffold 宣言物理交付，**生效副作用**（required check 绑定、hook 安装、CI 关联）只归 Runtime（§8.3-2）。
- Devon 不是脚手架施工方，只执行合同显式标注待实现的 foundation task（守卫脚本行为体、真实 CI workflow），deadline M-VERIFY 门禁链。
- Prism 宣言⇔创建逐项审计不变；GT 最小化（D-22）维持，提示词措辞采用「规模与所验证内容相称」的一般判据，不带 wc -l 等项目特例（d0e2f70 的错误归因一并修正）。

## D-25. foundation task 判据：编写而非执行（补 D-24）

（用户裁定 2026-08-04）

- foundation task = machine contracts 显式标注待实现的宿主项目文件之**编写**任务（守卫脚本行为体、CI workflow 由骨架补全等），作为普通 task 进 task graph、受 scope/RGR 约束、deadline M-VERIFY 门禁链。
- 运行 lint/type/int/e2e、安装 hook、绑定 required check 从来不是 Devon 的任务——Agent 不 commit/push（不变量 2），提交时 hook 自动触发，各阶段门禁由 Runtime 执行并回读（永不信自述）。

## D-26. Harness 无关原则重申：移除迁移残留的 frontmatter permission 块

（用户裁定 2026-08-04，decisions 漂移审计）D-19 的原则维持不变：agent 提示词（`tracks/agents/*.md`）harness 无关，不含 permission 块；harness 特定的权限控制只放 harness 自己的配置文件（`trac init` per-agent 改写）。

- Archer/Prism/Shield frontmatter 中的 permission 块是从 louke 迁移时未处理的残留，已移除；三个 agent 恢复到与 Scribe/Sage/Lex 一致的形态。
- 迁移纪律：今后从 louke（或其它 harness 绑定来源）迁移 agent 提示词，必须剥离 frontmatter permission 块；权限语义以 D-19「Agent 提示词的权限约定」为准（读不限／写仅 assignment 目标文档／bash 不限／临时目录 command_id 专属）。
- 之所以移除而非追认：按 D-19 的 spike 结论，frontmatter permission 优先并覆盖 harness 配置文件的 per-agent 设置——残留块若保留，会静默架空 `trac init` 写入的配置，且把 harness 语义焊死在提示词里，违背「换 harness 只换配置文件」。

## D-27. 取代索引与相容性解释（漂移审计）

（2026-08-04，编辑性裁定）append-only 纪律下旧条目不改写；为防止后出裁定被旧措辞遮蔽，显式登记取代关系：

- D-22 的「M-DESIGN 脚手架收窄」条款（完整 CI workflow/git hook/linter 配置不属脚手架、Archer 只在 machine contracts 定义合同）**已被 D-24 条件②取代**——声明性质量守卫配置由 Archer 按 Scaffold 宣言物理交付，生效副作用归 Runtime。
- D-24 中「Devon……只执行合同显式标注待实现的 foundation task」的措辞**以 D-25 为准**——foundation task 是待实现产物的**编写**任务；运行、安装、绑定归 Runtime。
- D-17 与 D-25 相容：D-17「Devon 跑质量工具并重构」指 RGR 循环中 Devon 对本地工作副本的自检与重构（行为反馈，不是 task、不是门禁）；D-25 指权威门禁的执行与回读归 Runtime。
- 勘误：D-21 正文「flow.md §9 ISLAND_GATE_1」随 TDD 次序重排应为 §10（M-IMPL），已就地修正；本文件 frontmatter `last_updated` 一并更新。另 D-22～D-25 此前仅存在于决策日志表格行，本次升格为正文条目（内容逐字保留），表格行缩为短引用。

## D-28. 输入 identity/完整性校验归 Runtime，Agent 不自校验（对所有 Agent）

（用户裁定 2026-08-04）

- assignment 的 revision/digest 与磁盘文件核对、输入集完整性（如 M-DESIGN 四件套同一 revision）是形式检查，由 Runtime 在派发前完成（可测；职责划分见 D-14），并确保正确调度。
- Agent 对 assignment 列出的输入集直接使用，不设自检阶段——连「读 frontmatter revision 比对」这样的轻量操作也不做。适用于所有 Agent（Archer/Shield/Prism/Devon……），不只是 Prism。
- Agent 侧唯一的 identity 残留是传播：verdict/outcome 引用 assignment 给定的输入 identity 供 Runtime 关联，不是校验。
- 理由：Agent 执行慢、异常罕见；Agent 自校验本身是自述、不构成保障；保障落点与缺陷修复都归 Runtime 测试。

## D-29. Prism 评审管线与判据包 skill 化进入 v0.4 spec

（用户裁定 2026-08-04）

- Prism 单次评审执行管线定为四阶段：①判据提取（上游合同 + assignment 指定的判据包）→ ②逐项符合性检查 → ③反证（anti-slop）→ ④裁决（blocker 经 trac discuss 锚定线程，verdict 绑定输入 identity）。输入校验不列为阶段（见 D-28）。
- 各评审类型的判据集拆为 skill（判据包）；管线、独立性原则与裁决格式常驻 Prism.md。
- 必须的配套（反自述三件套）：assignment 写明应加载判据包的名称+版本（Runtime 决定，Prism 不自选）；verdict outcome 携带实际加载判据包的 identity；Runtime 回读核对，不匹配判失败重派。
- v0.4 必须交付：测试资产判据包（M-TEST 的 PRISM_REVIEW 子状态消费）与配套 Runtime 机制；M-DESIGN 判据抽取、代码评审/争议诊断判据包按 release 节奏（判据与规模相称）。
- 形式校验不得混入判据包（D-14 边界）。

## D-30. v0.4 dogfood 运行记录：钩子拦截死锁缺口与本仓存量标记采纳策略

（Maestro 运行记录 2026-08-05，非用户裁定；供 Aaron 复核）

- **发现 1（kernel 缺口）**：v04 M-DESIGN attempt 2 中 Archer 交付物（GT 参考实现）被 pre-commit 拦截（ruff 6 错误），`_decide_design_commit` 静默退出——钩子输出未转为失败证据重派，形成死锁。当时以 bootstrap 例外人工修复（`291cf0a`）。**后续项 F-1**：commit 被拒后应把钩子输出写为 evidence 并在预算内重派（需 story/spec 立项，D-26）。
- **发现 2（存量标记）**：`trac check trace` 在本仓报 192 hard errors，均为 v0.1–v0.3 短格式 marker 存量，非回归；M-TEST EXIT 门禁按版本过滤 required AC（FR-0070），不受影响。**后续项 F-2**：为 tracks 自身声明 `.tracks/legacy-baseline.json`（FR-0100 设计的采纳路径，故事明言「tracks 自己的 v0.1 即为存量样本」），由 Aaron 决定时点。（结局：Aaron 裁定走声明路线；实施中经 R-1/D-31 语义变更，存量残留自然归零，最终未创建基线文件即达成 trace pass——FR-0100 机制保留备用。）
- **发现 3（边界迁移完成）**：v0.4 后 `run.completed(boundary)` 发生于 M-TEST EXIT 之后（M-IMPL 未注册）；tests/e2e/test_full_journey.py 边界期望已迁移并列入 Task B 交付。
- **进展（2026-08-05，已闭环）**：F-1 已修复（`41a4ed4`：verdict.failed(check=commit) 证据回写 + WRITE 重派 + 预算内重提交，12 测试）。F-2 经 R-1（D-31）闭环（`69cc6f5`）：R-1 语义下旧 marker 不带 TRACKS-TRACE 特征词即普通引用，存量残留自然归零——**无需 legacy-baseline.json 声明**（默认路径 trace/reach 均 pass）；92 条 v0.4 AC 全部绑定；本仓 23 个测试文件完成迁移；Shield/SKILL/template 措辞对齐；reach 无 Python 文件 warning+exit 0。tag v0.4 重指 `69cc6f5`。

## D-31. R-1 需求变更：marker 约定简化与语言中立化（TRACKS-TRACE 特征词）

（用户裁定 2026-08-05）

- 测试与 AC 的绑定 marker 不使用 pytest.mark / 任意位置注释 / 宿主语言文档注释生态，统一为**测试函数定义紧邻上方的独立标记注释行**：`( # | // ) AC-FRXXXX-YY@<version> TRACKS-TRACE 可选人话说明`。
- 特征词 `TRACKS-TRACE` 区分"绑定 marker"与"普通引用"：不带特征词的 AC-ID 提及（任何位置）均为正常引用，不识别、不报错——误报根除。
- 检测 = 单条行级正则，零 AST、零第三方依赖；`#`/`//` 覆盖前 10 名通用开发语言（SQL 除外）；树-sitter/pylint 路线作废。
- Shield 义务：每个测试函数必须有 TRACKS-TRACE 标记行（判据包 + M-TEST EXIT 门禁双重强制，不信自述）。
- reach（FR-0090）语言范围澄清：v0.4 仅 Python；无 Python 文件时 warning + 退出码 0。
- 合同落点：spec/acceptance/test-plan/interfaces 已修订并提交（`4b2ad5f`）；全量实现与存量迁移完成于 `69cc6f5`（scanner/FakeBackend/GT/fixtures/23 文件迁移/Shield 措辞/reach warning；606 passed；tag v0.4 重指该提交）。

## D-32. “大小两个测试循环”正式命名（外圈合同循环 / 内圈 RGR 循环）

（用户裁定 2026-08-05）

- tracks 测试体系的两大循环正式命名：**外圈合同循环**——Shield 在 M-TEST 对着接口桩与验收标准编写 integration/e2e 合同测试，先行变红、冻结为基线；**内圈 RGR 循环**——M-IMPL 中 Devon 逐 task 走 Red→Green→Refactor（Runtime 校验合法 Red、Prism 评 Red checkpoint）。内圈保证每一步的质量，外圈负责最终验收：M-IMPL 的目标即把外圈合同全部变绿。
- 该提法由 flow.md M-TEST/M-IMPL 的既有职责提炼（原文此前无此词），裁定“这个说法成立”后在 flow.md §9/§10 加定位锚点；docs/philosophy/why-evidence-wins.md 的对外表述以此为正名出处。

## D-33. 生产 Runtime Agent 执行所有权 / 模型配置 / 升级可观测性

（用户裁定 2026-08-07）

- **per-Agent elapsed 超时取消**：生产 Runtime 对单次 Agent 派发**不设** elapsed-time 超时——工作中的 Agent 不会仅因 N 秒过去而被杀死。活动性由 Runtime/operator 观测；显式 Human Ctrl-C 取消并清理进程组（D-11 信号→事件协议不变）。D-23 历史上只描述 live-E2E harness 的观测/总看门狗做法，**并未**显式规定生产 Runtime 的 per-Agent 超时；本条**澄清 D-23 不适用于生产 Runtime 的 per-Agent elapsed time**，D-23 的 live harness 看门狗实践仍有效（测试 harness/网络/CI 总看门狗属独立测试/IO 关注点，可保留有界超时）。
- **模型选择归 harness 配置**：模型由 opencode agent/project 配置决定；tracks **不得**在应用代码中把 IQ 档位映射到 provider/model。显式 `TRAC_AGENT_MODEL` 仅作 operator override。历史上 D-26 ID 重复，本条所指为决策日志中标题含「模型档位解析延后」/ commit `80e284f` 的记录（非 harness 无关原则那条）；D-33 **关闭该记录遗留的 IQ 路由残留**——在 tracks 代码中**禁止** IQ→model 映射（`80e284f` 的越序 IQ→模型映射不再扩展）。
- **升级可观测与 Human retry**：<=3 次失败后升级时，`trac run` 与 `trac status` 必须暴露 attempt 计数 + 失败类 + 原因。Human 可运行 `trac retry`：追加 `human.retry` 事件、清除升级 gate、重置一份新的 <=3 attempt 预算、保留失败证据，且**不**自动重新派发——后续由显式 `trac run` 恢复。
- **长派发控制台活动**：`trac run` 必须为长 Agent 派发发出简洁、已 flush 的控制台活动（开始：时间戳/Agent/stage(substate)/task(attempt)；完成：状态/失败 + 耗时），不得流式输出海量 Agent stdout。

## 决策日志

| ID   | 决定日期   | 标题                                          | 来源                                                                     |
| :--- | :--------- | :-------------------------------------------- | :----------------------------------------------------------------------- |
| D-01 | 2026-07-30 | 目录与运行时命名                              | 用户表态 `目录名已确定为 .tracks, 数据库名为 tracks.db`                  |
| D-02 | 2026-07-30 | 真相源与存储：SQLite 事件溯源                 | 用户裁定：v0.15 看板必须 + DB 不可避免；arch.md §2、§5                   |
| D-03 | 2026-07-30 | v0.1 体验范围                                 | story §原始输入、§范围排除                                               |
| D-04 | 2026-07-30 | v0.1 CLI 集合                                 | 用户表述 `trac review no-comment` 之外 `trac review revise`              |
| D-05 | 2026-07-30 | status vs replay 不同语义                     | 用户表述 `trac status 是查询动作, replay 是执行动作, 两者怎混`           |
| D-06 | 2026-07-30 | NO-GO/PARK/scope_overflow 分支                | 用户裁定 + flow §4.1                                                     |
| D-07 | 2026-07-30 | 双进程单写者                                  | 用户裁定 + story §行为种子 §11                                           |
| D-08 | 2026-07-30 | v0.1 输入来源：stdin                          | 用户裁定：`从命令行 stdin 接收`                                          |
| D-09 | 2026-07-30 | 用户参与方式                                  | 用户裁定 + flow.md 不变量 1                                              |
| D-10 | 2026-07-30 | 文档层级关系                                  | arch/flow/story/decisions.md 责任分工                                    |
| D-11 | 2026-07-30 | 取消协议与 cancel 推迟                        | 用户裁定：并发取消需求 + 同意 v0.1 仅 Ctrl-C                             |
| D-12 | 2026-07-30 | dispatch 事件模型（删 assignment.dispatched） | 用户裁定（R2-02 内联）：`command.issued(dispatch_agent)` 即派发事实      |
| D-13 | 2026-07-30 | 副作用 per-kind reconcile                     | 用户裁定（R2-03 内联）：reconcile→按需执行→观察结果                      |
| D-14 | 2026-07-30 | 产物正确性职责划分：形式校验 vs 语义翻译      | 用户裁定（R2-01）：形式校验归 runtime，语义翻译归 LLM+评审 Agent         |
| D-15 | 2026-07-30 | runtime 根按 cwd 解析 + 测试隔离              | 用户裁定：测试用临时 repo，不得污染项目 `.tracks/`；可注入 `TRACKS_HOME` |
| D-16 | 2026-07-30 | v0.1 validate_document 恒 pass                | 用户裁定：v0.1 不加格式校验，判据留 v0.2+，先跑 scaffold                 |
| D-17 | 2026-07-30 | Devon R-G-R                                  | 用户裁定：Devon 跑质量工具并重构；复杂度不针对测试文件                  |
| D-18 | 2026-08-01 | 真实外部依赖三层验证机制（fake 每次/live 周期/milestone 硬门禁） | 用户裁定：live 通道不每次跑、里程碑前必跑；B+A 触发（判据为主 + checklist 兜底）；判据技术栈无关、方案绑定 Archer 技术栈；M-DESIGN 固化为可复用测试模式 |
| D-19 | 2026-08-01 | Harness 配置与 Agent 提示词分离；`trac init` 改写 harness 配置 | 用户裁定：agent .md harness 无关；harness 权限放 harness 配置文件；`trac init` 改写 `opencode.json`（`external_directory: deny` + 允许 `$TMPDIR`）；换 harness 只换配置文件 |
| D-20 | 2026-08-04 | 交付面完整性（surface completeness） | 用户裁定：移植 louke STR-1406 D-02；每 FR 必有命名交付面+可观察出口，无面=需求缺口；责任链 M-STORY→M-SPEC→M-DESIGN |
| D-21 | 2026-08-04 | ISLAND_GATE_1 六元组前置到设计期 | 用户裁定：六元组是 Archer 设计期义务、Prism 判据；M-IMPL gate 只做复核；出处 louke STR-1406 BS-01/BS-03/D-05 |
| D-22 | 2026-08-04 | ground truth 最小化与条件适用；M-DESIGN 脚手架收窄 | 用户裁定：GT=最小验证脚本、按 test-plan §3 条件适用；脚手架收窄（收窄条款后被 D-24 修订）；run049/050/053 证据 |
| D-23 | 2026-08-04 | live E2E 迭代方法学：基线恢复 + 观测定预算 | 用户裁定：基线快照恢复重跑 M-DESIGN；事件时间戳+opencode 观测定预算，超时只做看门狗 |
| D-24 | 2026-08-04 | 脚手架物化边界两分（修订 D-22 收窄条款）；GT 措辞去项目特例 | 用户裁定：物化判据两条（collect 依赖文件+声明性守卫配置归 Archer 物理交付；生效副作用归 Runtime）；foundation task 措辞后被 D-25 澄清 |
| D-25 | 2026-08-04 | foundation task 判据：编写而非执行（补 D-24） | 用户裁定：foundation task=编写任务；运行/安装/绑定归 Runtime，Agent 不 commit/push |
| D-26 | 2026-08-04 | 没有 story/spec 不做功能；交付物=构建物+完整规格书；模型档位解析延后 | 用户裁定：任何功能必须经 story/spec 流程才实现（本次 IQ→模型解析 `80e284f` 属越序，保留但不再扩展）；产品交付必须含与实际功能逐一对应的完整规格书，最终用户据此知功能与用法；IQ 档位解析未来是 web UI 拖拽功能，CLI 层解析意义有限；当前焦点=v0.4 测试能力（M-TEST）与需求追踪（trace/reach）——测试能力是 tracks 设计哲学的核心保证 |
| D-26 | 2026-08-04 | Harness 无关原则重申：移除迁移残留 permission 块 | 用户裁定：恢复 harness 无关、无 permission 块；Archer/Prism/Shield 残留块系 louke 迁移遗漏，已移除 |
| D-27 | 2026-08-04 | 取代索引与相容性解释（漂移审计） | 漂移审计：D-22→D-24、D-24→D-25 取代登记；D-17/D-25 相容解释；D-21 节号勘误；D-22~25 升格正文 |
| D-28 | 2026-08-04 | 输入 identity/完整性校验归 Runtime，Agent 不自校验 | 用户裁定：形式检查由 Runtime 派发前完成（适用所有 Agent）；Agent 直接使用输入集，verdict 仅传播 identity |
| D-29 | 2026-08-04 | Prism 评审管线与判据包 skill 化进入 v0.4 spec | 用户裁定：四阶段管线；判据包 skill 化；配套三件套（assignment 指定/outcome 携带/Runtime 回读）；v0.4 交付测试资产判据包与 Runtime 机制 |
| D-30 | 2026-08-05 | v0.4 dogfood 运行记录：钩子拦截死锁缺口与本仓存量标记采纳策略 | Maestro 运行记录（非用户裁定）：F-1 commit 被拒死锁待立项；F-2 本仓 legacy-baseline 采纳时点待 Aaron 决定 |
| D-31 | 2026-08-05 | R-1：marker 约定简化与语言中立化（TRACKS-TRACE 特征词） | 用户裁定：标记注释行 + TRACKS-TRACE 特征词 + 行级正则零依赖检测（spec `4b2ad5f`）；Shield 写测试必带标记行；reach v0.4 仅 Python |
| D-32 | 2026-08-05 | “大小两个测试循环”正式命名（外圈合同循环 / 内圈 RGR 循环） | 用户裁定：“这个说法成立”；flow.md §9/§10 定位锚点；why-evidence-wins.md 对外表述以此为准 |
| D-33 | 2026-08-07 | 生产 Runtime Agent 执行所有权 / 模型配置 / 升级可观测性 | 用户裁定：生产 per-Agent 派发无 elapsed 超时（澄清 D-23 不适用生产 Runtime per-Agent elapsed time，D-23 live-E2E 看门狗有效）；模型选择归 opencode 配置、tracks 禁止 IQ->model 映射（关闭标题含「模型档位解析延后」/ commit `80e284f` 的决策日志记录遗留的 IQ 路由残留，非 harness 无关原则那条 D-26）；升级暴露 attempt+失败类+原因，`trac retry`->`human.retry` 清 gate+重置 <=3 预算+保留证据+不自动派发；`trac run` 简洁 flushed 控制台活动 |
| D-34 | 2026-08-15 | 基础设施类失败证据富化（FR-11 增强）：stderr/上游错误摘要入 last_failure.evidence | 用户裁定（必须做、暂不阻塞、无 backlog 故记录于此）：non_zero_exit 重派 evidence 仅含 "opencode exited 1"，真因（sglang 400 reasoning_effort=xhigh 拒绝；上游截断 JSON Unterminated string）只在 opencode 日志/session.error；run 01KZTHE7RMZE6110PK9C54K1E2 实证 |
| D-35 | 2026-08-15 | 评审意见按阶段分通道持久化：M-DESIGN 保持文档锚定（git 为 source of truth）；M-TEST/M-IMPL 代码评审单独成文入 blobs/，manifest 增加结构化字段 | 用户裁定（必须做、暂不阻塞、无 backlog 故记录于此）：初始 manifest 协议只考虑 M-DESIGN（评论可内嵌 markdown 正文，经 trac discuss 锚定+checkpoint diff 可审）；但 M-TEST/M-IMPL 评审对象是 Shield/Devon 代码，意见无法嵌入代码，必须单独成文。设计：① M-DESIGN 不变；② M-TEST/M-IMPL 的 Prism manifest 增加 review_summary（一句话，≤140 字符，manifest 只放摘要不放正文）、findings[]（id/severity/defect_classification/criterion/artifact/summary）、review_body（正文）——result_checkpoint 将 body 经 write_audit_blob 落 blobs/{sha256}，prism.verdict 事件行内保留 summary+findings+review_ref（引用地址），超 BLOB_THRESHOLD(8KB) 时 store 自动 $ref；③ machine._on_prism_verdict 把 review_ref 一并写入 last_failure 供重派 evidence。实证：run 01KZTHE7RMZE6110PK9C54K1E2 seq664 prism.verdict payload 无任何意见字段（协议未要求），Shield attempt 2 evidence 带旧 non_zero_exit（叠加 42s 版本竞态，trac run 05:22:22 启动早于 f10dca3 05:23:04）；result_checkpoint.py:255 透传与 machine 侧已就位，仅待 Prism.md manifest 约定补字段，DB 零改动；触发条件：进入 M-IMPL 前复核代码评审的文档锚定补偿通道是否成立（M-TEST 有 test-plan.md 天然载体，M-IMPL 锚设计文档语义错位），不成立则本条升级为阻塞项（events 1510 行/672KB，blobs 137MB，容量充裕） |
