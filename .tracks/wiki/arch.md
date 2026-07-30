# Agent on Tracks — 架构决定记录

> 本文档记录已讨论并定案的架构决定。每条决定附带理由（多数源自 louke 项目的尸检教训）。
> 状态：初稿，随讨论持续追加。最后更新：2026-07-30

---

## 0. 项目背景与动机

前身 louke 项目失控的尸检结论，作为本项目全部设计决定的反面依据：

- 源码 7.3 万行 + 测试 11.2 万行（测试/源码比 1.5:1，健康值约 0.5–1）；
- `runtime/` 117 个模块中 **79 个（67%）从未被生产代码 import**，唯一消费者是自己的测试——"测试驱动的幽灵功能"；
- 病根：Agent 按 story 生成"模块 + 测试"，gate 只看"测试绿"，从不问"是否被产品可达"；每次纠偏又以"新增模块 + 新增 gate"的方式制造新孤岛，永不收敛；
- 30 天 806 次提交，人类无法履行 review 职责。

**结论：重写（而非重构 louke），但流程必须同时重造，否则会复现同样的结果。**

---

## 1. 命名（已定）

| 项                      | 名称                         |
| :---------------------- | :--------------------------- |
| 项目名                  | Agent on Tracks              |
| Python 发行包名（PyPI） | `agent-on-tracks`            |
| Python 导入包名         | `tracks`                     |
| 命令行命令              | `trac`                       |
| 项目数据目录            | `.tracks/`（与导入包名一致） |

已知风险（有意识接受）：PyPI 上 `trac`/`Trac` 是老牌项目管理系统，心智上可能撞车；`tracks` 作为导入名较通用。

---

## 2. 总体架构：事件溯源三层（已定）

**Runtime 的一切状态都是事件日志的投影；分派决策可从日志回放复现。**

```
事件日志（事实） → 投影/决策（纯函数） → 执行器（唯一副作用边界）
```

引擎主循环：

```python
while True:
    state = project(read_events(run_id))          # 纯投影 fold
    commands = decide(state, workflow)             # 纯函数
    if not commands: break
    for cmd in commands:
        result = execute(cmd)                      # 唯一有副作用的地方
        append_event(run_id, result.to_event())
```

关键原则：

1. **事件是"事实"（过去式），不是"指令"**。如 `assignment.dispatched`、`outcome.received`、`verdict.failed`。若把指令写进事件文件，它就退化成任务队列，回放会重复执行副作用，可调试/可测试性全部作废。"流程驱动"的准确说法：**事件驱动状态，状态+规则驱动决策**。
2. **事实与规则分离**：事件日志（事实，位于宿主项目 `.tracks/runtime/`）+ workflow 流程定义（状态机+守卫+预算，**随安装包分发**，如 `site-packages/tracks/workflow.py`）。流程是 Agent on Tracks 的产品逻辑，不是宿主项目的用户资产，因此不放 `.tracks/`；换流程/加阶段/改重试策略 = 发布新版本的 trac。引擎本体目标几百行。
3. `project()` 与 `decide()` 是纯函数：禁止调 `time.now()`、禁止读文件系统。一切不确定性（时钟、Agent 输出、工具结果）只能以事件形式进入系统。
4. 回放模式 = 跳过 `execute()` 只做 fold，一行代码的区别。
5. **command 也入日志**：`decide()` 产出的 command 执行前先落 `command.issued` 事件（write-ahead），结果再落一条。崩溃恢复时投影自然看到"已下令但无结果"的悬挂命令，恢复逻辑复用同一个 loop，无需额外机制。

**反向警告**：不做通用工作流引擎（YAML DSL + 表达式 + 循环 = 蹩脚 BPM 产品）。第一版 workflow 用 Python typed dataclass 描述（状态、转移、守卫、预算）；守卫只允许引用状态中已有字段（工具 verdict、尝试次数、预算余量）。

---

## 3. Runtime ↔ Agent 交互契约（已定框架）

**总原则：契约是带 schema 的数据结构（pydantic / JSON Schema），不是 markdown 散文。** Runtime 对每条进出消息做机器校验，不合 schema 直接拒收。（louke 教训：契约写在协议文档里靠 Agent"理解"，每个 Agent 理解得都不一样。）

### 3a. 分派决策的输入（state 字段）

分派是纯函数 `dispatch(state) -> assignment`，state 只来自事件回放 + 显式 store，绝不现场扫描文件系统"感知"状态：

- 任务状态机当前状态
- 上一次 Outcome 的 verdict + 失败分类
- 预算余量（尝试次数 / token / 净行数）
- 工具裁定结果（测试、lint、可达性检查的机器输出）
- 待处理的人类裁定

### 3b. 给 Agent 的 Assignment

- `task_id` + 目标描述 + **机器可验收的完成判据**（能跑的命令，非自然语言 AC）
- **scope 白名单**：允许触碰的文件/目录清单（防 slop 第一道闸）
- **预算**：本次最大净新增行数、最大新文件数（通常为 0）
- 上下文清单：显式给定文件与历史事件摘要，而非"你自己去读"

### 3c. Agent 返回的 Outcome

- status（done / blocked / failed）+ 产物引用（diff 或 commit ref）+ 自述
- **Runtime 永远不信自述**：verdict 由 runtime 拿产物自己跑工具（测试、可达性、预算检查）得出

### 3d. 重派规则

- 按**失败类别**路由：verdict 不通过（带工具原始输出重派同一 Agent）、超预算（重派并明示压缩要求）、blocked（升级人类）
- **硬性尝试上限**（如 3 次），写进 runtime 而非提示词，超限必须转人类。不收敛的本质是无界重试。
- 重派时把上一次失败证据（工具原始输出，非摘要）放进新 Assignment

---

## 4. 目录布局（已定，Human 裁定版）

代码与数据的边界：**流程定义（workflow）随安装包走；`.tracks/` 只放宿主项目的用户资产与本机运行态。** 两轴归类原则（共享真相 vs 本机运行态 × git vs gitignore）继续适用于 `.tracks/` 内部。

部署到宿主项目后，在宿主项目根目录生成：

```
.tracks/
├── handoff/
├── projects/                 # 用户资产，进 git
│   └── v0.1/
│       └── story.md
├── runtime/                  # 本机运行态，整目录 gitignore
│   ├── blobs/                # 大 payload 内容寻址存储（{sha256}）
│   ├── events/               # 驱动流程前进的输入 + 命令执行结果追加（run-{ULID}.jsonl）
│   ├── lock                  # 单写者文件锁
│   ├── runs.jsonl            # run 索引（append-only）
│   ├── server.log
│   └── trac.db
└── wiki/                     # 项目知识文档
    ├── arch.md
    ├── flow.md
    └── ....md
```

语义约定：

- **projects/**：project 的集合。每个 project 是一个将要发行的版本，以 `v{version}` 命名；每次 release 的 story/spec 及设计文档放在此处。用户资产，需通过 git 版本保存。
- **runtime/**：运行时支撑文件。其中 **events/** 存放驱动流程前进的输入文件，命令执行后的结果追加进来（与 §2 的口径一致：外部输入以事件形式进入系统，事件驱动状态、状态+规则驱动决策）。
- **trac.db**：必须是事件日志的**可抛弃投影/缓存**——删掉后可由事件回放完整重建，绝不充当真相源（真相源只有 events/）。
- 需分享/归档某次 run 的证据：显式 `trac export run <id>` 拷贝到 `projects/` 对应版本目录，日志不天生长在共享区。
- `.gitignore` 对 `.tracks/` 只需 `/.tracks/runtime/` 一条规则。

---

## 5. 事件日志规格（已定）

### 格式与文件名

- **JSONL，先不上二进制**。append-only，一行一个完整 JSON。信封设计使日后切换二进制平滑；`jq` 即查询引擎。
- 一个 run 一个文件：`run-{ULID}.jsonl`。ULID 字典序 = 时间序；无依赖等价方案：`run-{UTC时间戳}-{6位随机}.jsonl`。
- 按 run 分文件的理由：回放的自然单位是 run；并发 run 各写各的文件天然无锁冲突；清理/归档以文件为单位。
- `runs.jsonl` 索引：每启动一个 run 追加一行 `{run_id, started_at, task_id, status, finished_at}`；status 变更追加新行，读时取最后一条。

### 事件信封

```json
{ "seq": 1, "ts": "...", "run_id": "...", "task_id": "...",
  "type": "assignment.dispatched", "schema_version": 1, "payload": { } }
```

- `seq` 在 run 内单调递增，**回放以 seq 为准**（不信任 ts，时钟可能回拨）。
- payload 超过 8KB 外置到 `runtime/blobs/{sha256}`，事件里只放引用——事件文件永远小到秒开秒 grep。

### 写入纪律（三条，现在定死）

1. **单写者**：只有 runtime 进程写事件文件；Agent 一切输出经 runtime 转成事件落盘。启动时拿 `runtime/lock` 文件锁，拿不到拒绝启动第二实例。
2. **Write-ahead**：先落 `command.issued` / `assignment.dispatched` 事件，再执行动作。
3. **追加后 flush**；恢复时最后一行不完整则直接丢弃（JSONL 做 WAL 的标准处理）。

---

## 6. 反 slop 工具（已定，做进 trac 自身）

开发期手动运行，结果作为事件写入日志（"重构指令"与"工具证据"因果链可追溯，为自动化铺路）。**从第一个 commit 起对 trac 自己跑**（自举纪律）。

| 命令                | 功能                                                         | 针对的 louke 死因 |
| :------------------ | :----------------------------------------------------------- | :---------------- |
| `trac check reach`  | 从入口点做 import 可达性分析，报告孤岛模块                   | 79 个孤岛模块     |
| `trac check budget` | 对 diff 报告净行数/新文件数/新模块数，与 Assignment 预算比对 | 无界增长          |
| `trac check ratio`  | 测试/源码行数比预警（健康 0.5–1）                            | 1.5:1 的测试膨胀  |
| `trac check dup`    | 近似重复代码块检测                                           | 复制粘贴式生成    |

每个工具约百行以内。

**合入门槛从"测试绿"改为"被生产代码可达"**（integration liveness 做成 merge 前硬 gate，而非又一个 runtime 模块）。

---

## 7. 构建顺序（已定）

**先用确定性 FakeAgent 跑通全环路，再接真 LLM：**

```
契约 schema → 事件日志 → 状态机/dispatch 纯函数 → FakeAgent → 全环路 e2e → 真 Agent
```

- FakeAgent = 往日志注入预设 `outcome.received` 事件的 executor 替身，可确定性测试所有分支（成功、失败重派、超预算、升级人类）。
- **Runtime 必须在没有任何 AI 参与的情况下 100% 可测。**（louke 从来无法回答"runtime 本身对不对"，因为其行为总与 LLM 不确定性搅在一起。）
- 先跑通流程，再考虑界面。

### 结构预算

- **不限制模块数量**（2026-07-30 用户裁定，废除早期"~10 个模块"上限）：模块多而小、复用率高是被鼓励的方向；预算对象是**行数**。
- 单个生产文件 >1000 行即视为设计问题，必须拆分。
- 总生产行数只增不减需在版本评审中给出理由；`trac check ratio / dup`（后续版本）以行数为度量。
- 不设 `utils/helpers/common` 之类无名杂物模块：共享逻辑放进拥有该概念的模块；无 owner 的概念应建**命名的**专属小模块（如 `hashing.py`），而非塞进杂物袋或挤进大文件。

---

## 8. 从 louke 继承 / 抛弃

**继承：**
- e2e 测试中的行为断言（唯一的真金）→ 萃取为 trac 工作流规格的输入
- STR-1501 两轴目录归类原则
- integration liveness（孤岛检测）概念 → 做成硬 gate

**抛弃：**
- per-story 生成新模块的习惯
- markdown 协议文档当契约
- "测试绿即合入"的 gate 定义

---

## 9. 待定事项

- Assignment / Outcome / Verdict / Event 各 schema 的字段级定义
- 端到端工作流状态机图 + 事件类型清单（`.tracks/wiki/flow.md` 起草中：M-START、M-STORY…）
- `handoff/` 的语义与 git 归属
- `wiki/` 的生成/维护方式（人写 or 蒸馏生成）
- 宿主项目是否允许覆盖/定制包内 workflow（v1 建议不允许，保持流程唯一）
