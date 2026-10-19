---
envelope: tracks-envelope:v2
interfaces_id: IF-012
spec_ref: SPEC-012
arch_ref: ARCH-012
created: 2026-10-19
status: draft
sha:
---

# v0.12 — 接口与类型化 Schema：安装包、全局 trac、项目版本锁定与 OOB 工作台

本文是 IF-011 的增量延伸。IF-001～IF-011 的事件溯源、封闭事件集、命令 WAL、审批 revision 绑定、release preview/三择一、escape barrier、发布幂等/reconcile、guard registry、CLI 合同、v0.9 服务面、v0.10 工作台交互、v0.11 Releases/intake/人工门/时间线/评审轮写入合同全部继承。v0.12 新增的是**官方安装包内容合同**、**trac install 项目安装与版本锁定合同**、**全局→项目 CLI 委派合同**、**项目服务器生命周期合同**，并把 v0.12 OOB 已交付的**按轮读取面、阶段路径/工作流、可读卡片/本地时间、评审两决策、静态文本 no-cache**登记为外部可观察合同。本文只写外部可观察契约；模块内部组织见 architecture.md。

## 0. 延续性（什么不变）

- IF-001～IF-011 的全部合同逐字继承；既有 IF 标识不可重定义或复用，IF-012 只追加新标识（§5）。
- tracks.db 的 EVENT_TYPES 封闭集不变，v0.12 **不新增事件类型**；`human.review` 仅追加可选 payload 键 `comments`/`doc`/`thread_id`（§1j）。service_events 封闭集、服务命令 kind 封闭集、kernel COMMAND_KINDS 均不变。
- 既有 CLI 子命令集合、参数面与退出码不变；`trac serve` 前台行为与 `serve_cmd.cmd_serve` 的直接调用面不变。v0.12 只追加 `trac install`/`trac version` 与 `trac serve --background|--stop|--status`（§2）。
- `.tracks/projects/project.toml` 的测试执行合同、layout、lint、adapter、host-contract 段逐字继承；本设计的变更集不修改该文件，但现行 bytes 已包含 v0.12 OOB 修复的 e2e `collect` marker 过滤变更（`hooks-runner-ci-required` digest 重锚见 architecture §4.2；`.flake8` 未变化）。
- 认证边界、CSRF + Idempotency-Key、错误封装 `{"error": {"reason", "detail"}}`、redaction、本机单用户边界、SSE 合并序/游标补读全部继承；全部新读取面与既有查询面同一响应封装。
- v0.10/v0.11 的 PAGES 六名、五 tab、Releases 投影、run detail 的 `awaiting_gate`/`return_options`、评审轮写入通道、`ui` marker 通道隔离、`data-testid` 绑定纪律全部继承。
- OOB 读取面：`/stages/{stage}/rounds` 与 `/rounds/{command_id}` 按当前已交付实现登记为 v0.12 外部合同；`/activity`（run 版）是**未消费的遗留读面**，仅保留既有 API 语义，不纳入 v0.12 断言基线（无 AC/IF 承载、无 v0.12 测试义务）。UI 面（阶段轨道、阶段工作流、按轮手风琴、卡片、评审两决策、静态 no-cache）按当前已交付实现登记；不改变既有 API 语义与 testid。
- 发布闭环、version gate bindings、security scan、build/artifact、smoke、M-TEST/M-IMPL/M-VERIFY 合同全部继承。设计期版本为 `0.12.0`（§1a）。

### 0.1 v0.12 合同变更清单（显式声明）

| # | 变更 | 旧合同（v0.11） | 新合同（v0.12） | 理由 |
|:--|:--|:--|:--|:--|
| 1 | 官方包内容 | wheel 的 package-data 含 agents/skills/templates；无默认 `opencode.json` 承诺 | package-data 追加 `assets/opencode.json`；wheel/安装包必须同时含 agents、skills、templates、默认 `opencode.json`（§1a） | FR-0348 |
| 2 | CLI 子命令 | 无 `install`/`version`；serve 仅前台 | 追加 `trac install <path> [--version V] [--wheel PATH]`、`trac version [--json]`；serve 追加 `--background/--stop/--status`（§2） | FR-0349/0351/0353/NFR-0159 |
| 3 | 项目安装布局 | 仅 `trac init` 的 `.tracks` 脚手架 | `.tracks/install.json` receipt、`.tracks/env/<version>/site-packages/`、`.tracks/bin/trac.py`、pidfile/log（§1b/§3） | FR-0351/0352/0353/0354 |
| 4 | 项目服务范围 | serve 可对任意 `--repo` 启动 | 用户面 `trac serve` 要求 `--repo` 为已安装项目；未安装拒绝 `not_installed`，无全局版本兜底（§1e/§2） | FR-0350 |
| 5 | 全局/项目版本 | 全局版本唯一 | 项目 receipt + site-packages 锁定；全局 CLI 委派；`trac version` 报 scope（§1c/§1d） | FR-0353 |
| 6 | review 退回 | `human.review.comments` 仅随事件直达 Scribe（HEAD c6dc3f8） | `action=comment` 必须在 worker 内先写文档根线程，再落 `human.review`（comments/doc/thread_id 保留）（§1h） | FR-0359 / BS-12 |
| 7 | OOB 读取面 | `/activity`、rounds 面已实现但未登记；UI 仅实现 | 登记 `/stages/{stage}/rounds`、`/rounds/{command_id}` schema 与 testid/静态头（§1f/§1g/§1i）；run `/activity` 保留为继承遗留面，不纳入 v0.12 断言基线 | FR-0355..0360 |
| 8 | CLI token 集合 | `TRAC_SUBCOMMANDS` 与 USAGE 无 install/version | 两处同步登记 `install`/`version`（scaffold）；`main` 的 handler 与委派钩子待实现（§2/§5） | FR-0348/0349 |

## 1. 跨模块合同

### 1a. 官方安装包内容合同（IF-INSTALLPKG-001）

**modules**：`pyproject.toml`（package-data）、`tracks/assets/opencode.json`、`tracks/install.py`、构建产物 `dist/*.whl`。

1. **wheel 资源封闭清单**：官方 wheel 必须包含且只承诺以下 tracks-owned 资源：`tracks/agents/<Role>.md`（Archer/Scribe/Sage/Lex/Prism/Devon/Shield）、`tracks/skills/<skill>/SKILL.md`（canonical 10 个）、`tracks/templates/**`（templates/ 下全部文件，含 pre-commit 配置模板）、`tracks/assets/opencode.json`。资源缺失任一 → 安装包不合格（`package_incomplete`），不得作为可用版本候选。
2. **默认 opencode.json**：`tracks/assets/opencode.json` 是合法 JSON，含 7 个 tracks agent 的 `agent.<Role>.permission.external_directory` 权限块（`*: deny` + `{env:TMPDIR}tracks/*: allow`）；不包含模型/provider/插件配置。项目安装仅在目标不存在根 `opencode.json` 时写入它；用户自有 `.opencode/opencode.json` 不被读取或修改。
3. **版本**：wheel `METADATA` 的 `Version` 与 `tracks/__init__.py` 的 `__version__` 均为 `0.12.0`；`trac install --version V` 以 `METADATA` 为准并校验二者一致。
4. **构建**：`[host-contract.build]` 的 `python -m pip wheel --no-deps -w dist .` 产出唯一 wheel；smoke 继续验证安装后 `trac --help` 零仓库可用。

### 1b. 项目安装布局与 receipt（IF-PROJLAYOUT-001）

**modules**：`tracks/install.py`、`tracks/project_runtime.py`、目标项目的 `.tracks/` 与 `.opencode/`。

1. **receipt** `.tracks/install.json`（UTF-8 JSON，原子写）：

```json
{
  "schema_version": 1,
  "version": "0.12.0",
  "installed_at": "2026-10-19T00:00:00+00:00",
  "python": "/abs/path/to/python",
  "site_packages": ".tracks/env/0.12.0/site-packages",
  "launcher": ".tracks/bin/trac.py",
  "installer_version": "0.12.0",
  "source": "running-distribution",
  "managed_files": [".opencode/agents/Archer.md", ".opencode/skills/tracks-archer-design/SKILL.md"],
  "seeded_files": ["opencode.json"]
}
```

`source ∈ {running-distribution, wheel}`；`site_packages`/`launcher` 必须为项目根内相对路径；`managed_files` 是本次安装写入/覆盖的 tracks 自有文件全集；`seeded_files` 是仅当目标缺失时写入的文件。receipt 是版本锁定的唯一真相；损坏/缺失 → `install_corrupt`。

2. **site-packages**：`.tracks/env/<version>/site-packages/` 含该版本 `tracks/` 包与 `*.dist-info/`。安装器可用“运行时 distribution 的 `tracks.__file__` + dist-info”或官方 wheel 的 zip 成员生成；不得混入当前宿主源码树的其他版本文件。`.tracks/env/.gitignore` 内容为 `*`，运行态不进 git。
3. **launcher** `.tracks/bin/trac.py`：读取 receipt；`sys.path.insert(0, <root>/<site_packages>)`；`cwd=<root>`；设置 `TRAC_PROJECT_ACTIVE=1`；调用 `tracks.cli.main.main(argv)`。launcher 由安装器写入并纳入 managed files，版本无关。
4. **托管文件**：`.opencode/agents/**`、`.opencode/skills/**`、`.opencode/templates/**` 为 tracks 自有路径，安装无条件覆盖；根 `opencode.json` 为 seeded-only（已存在不覆盖）；用户自有 `.opencode/opencode.json` 不读不写。清单外路径不读不写；宿主根 `.gitignore` 不创建、不读取、不修改（不存在则在安装后仍不存在）。`.tracks/projects|runtime|wiki` 与 `.tracks/*/.gitignore` 是 tracks 自有脚手架，按 `trac init` 的 `.tracks` 目录语义幂等创建。
5. **运行态**：`.tracks/service/serve.pid`（JSON `{pid, host, port, version, repo, started_at}`）与 `serve.log`（追加文本）仅后台服务使用；`.tracks/env/` 不进 git。

### 1c. 安装命令合同（IF-INSTALL-001）

**modules**：`tracks/cli/install_cmd.py`、`tracks/install.py`、`tracks/project_runtime.py`。

1. **语法**：`trac install <path> [--version <X.Y.Z>] [--wheel <path>]`。`<path>` 可为空目录、已存在的 Git 项目或尚不存在的路径（父目录必须存在）；`--version` 省略时取运行中全局 distribution 的版本。`--wheel` 省略且版本等于运行版本时复制运行中 distribution；`--wheel` 存在时从该 wheel 解包，`METADATA.Version` 必须等于目标版本（`--version` 省略时即全局运行版本）。显式 `--version V` 且 V 与运行版本不同时，必须提供 `--wheel`。
2. **目标前置**：`<path>` 已存在但非目录 → `target_not_directory`；空目录 → `git init -b main`；已有目录无 `.git` 且非空 → `not_a_git_repo`；已有 Git 项目直接加装。安装不要求目标先清空，不修改用户文件。
3. **版本前置**：版本必须匹配 `^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$`（允许前导 `v` 去除）；不匹配 → `version_invalid`；显式版本与运行版本不同但无 `--wheel` → `version_unavailable`（stderr 指引下载官方 wheel）；wheel 版本不匹配 → `version_mismatch`。
4. **成功输出**：stdout 恰一行 `installed tracks <version> at <path>`；仅当资源校验、目录替换、receipt 写入、post-install health check（项目 launcher `--help` 或 `version`）与路径限定 git commit 全部成功后才输出；exit 0。
5. **失败输出**：stdout 不输出成功行；stderr `trac install: <reason>: <detail>`；exit 1。reason 封闭集：`usage`、`target_not_directory`、`target_parent_missing`、`not_a_git_repo`、`version_invalid`、`version_unavailable`、`version_mismatch`、`package_incomplete`、`dependency_missing`、`install_failed`、`git_commit_failed`、`health_check_failed`。
6. **幂等/收敛**：同一版本重复执行成功；staging 清理、托管目录替换、receipt 重写；commit 无 diff 时不创建空提交。失败不写 receipt；本次替换的托管目录与 launcher 恢复到安装前状态（首次安装则移除），staging 清理；下次安装可重试。不执行自动升级或迁移。

### 1d. 全局 CLI 委派与版本观察（IF-GLOBALCLI-001 / IF-PROJECTCLI-001 / IF-VERSIONVIS-001）

**modules**：`tracks/cli/main.py`、`tracks/cli/install_cmd.py`、`tracks/project_runtime.py`、`.tracks/bin/trac.py`。

1. **全局入口**：`trac --help` / 无参数在任意目录打印 USAGE、exit 0、不依赖仓库；USAGE 含 `install`/`version`/`serve` 及新 flag。全局 `trac` 不提供全局服务器。
2. **项目发现**：从 cwd 向上查找最近的 `.tracks/install.json`；找到即项目根。若未找到 receipt，但最近的 `.tracks/bin/trac.py` 或 `.tracks/env/` 存在，视为损坏项目并报 `install_corrupt`（不退化全局版本）；否则按项目外处理。`install`/`version`/`--help` 始终全局处理；`serve` 由 `cmd_serve` 外层处理（§1d.4）；其余命令在 `TRAC_PROJECT_ACTIVE` 未设置时，以项目根为 cwd、`TRAC_PROJECT_ACTIVE=1` 执行 `.tracks/bin/trac.py`；项目外按全局版本既有行为。receipt/site-packages 损坏 → `install_corrupt`，不退化为全局版本。
3. **`trac version`**：项目内读 receipt 并校验 `<site_packages>/tracks/__init__.py` 的 `__version__`；输出 `trac <version> (project <path>)`；`--json` 输出 `{"scope":"project","version":"...","project_root":"...","site_packages":"..."}`。项目外输出 `trac <global-version> (global)` 或 `{"scope":"global","version":"..."}`。版本不一致 → `install_corrupt`。
4. **服务入口**：`trac serve` 的用户面在 `TRAC_CLI_ENTRY=1`（由 `main` 设置）时要求 receipt；无 receipt → `not_installed`，不启动 uvicorn。直接 import `serve_cmd.cmd_serve` 的调用面不经过该前置（测试/管理后门）。
5. **版本归属**：项目 `/healthz` 的 `version` 字段来自项目 `tracks.__version__`；`service.started` payload 的 `version` 同源；`trac serve --status` 输出 receipt version。三者与 receipt 一致是 AC-FR0350-01/AC-FR0353-01 的可观察闭合。

### 1e. 项目服务器生命周期（IF-SERVERLIFE-001）

**modules**：`tracks/cli/serve_cmd.py`、`tracks/project_runtime.py`、`tracks/server/app.py`（healthz）。

1. **前台**：`trac serve --repo <path> [--host 127.0.0.1] [--port 8000] [--password-stdin] [--wait-initial-s N] [--wait-cap-s N] [--poll-interval-s N] [--poll-idle-cap-s N]` 语义与 v0.9 逐字继承：阻塞运行、SIGINT/SIGTERM 优雅停止、exit 0；`--port 0` 绑定随机端口并公告。
2. **后台（POSIX）**：`trac serve --repo <path> --background`：安装前置通过后，以 `start_new_session=True` 启动项目 launcher 前台 serve；stdout/stderr 追加到 `<service_home>/serve.log`；父进程等待 healthz 或子进程退出（有界），成功写 pidfile 并输出 `serving on http://<host>:<port> (pid <pid>, version <version>, background)`；失败输出 `background_failed: <detail>` 并附日志尾。重复启动且已有存活服务 → 输出 `already running pid=<pid> url=<url>`，exit 0。
3. **停止**：`trac serve --repo <path> --stop` 读 pidfile，校验 PID 存活与命令行属于该项目 launcher；SIGTERM，等待退出（超时 SIGKILL），删除 pidfile，输出 `stopped pid=<pid>`；未运行 → `not running`（幂等，exit 0）；陈旧 pidfile → 清理并 `not running (stale pidfile removed)`；归属校验失败 → `stop_refused: pid <pid> does not belong to <path>`，不动进程。
4. **状态**：`trac serve --repo <path> --status` 输出 `running pid=<pid> version=<version> url=<url>` 或 `stopped` / `stopped (stale pidfile)`；exit 0。`running` 须经 `/healthz` 验证可连接；健康检查失败 → `running (unhealthy: <detail>)` 且 exit 1。
5. **平台**：`--background` 与 `--stop` 在 Windows 返回 `unsupported_platform`（exit 1）；前台照常。Windows 前台进程的生命周期即服务生命周期，不写 pidfile、不伪装常驻。

### 1f. 按轮读取面（IF-ROUNDS-001）

**modules**：`tracks/server/run_rounds.py`、`tracks/server/app.py`、`tracks/server/static/app/timeline.js`。

1. **`GET /api/runs/{run_id}/stages/{stage}/rounds`**（只读）：200 `{run_id, stage, rounds: [RoundListItem]}`；未知 run → 404 `not_found`；stage 无窗口 → `rounds: []`。`RoundListItem = {n:int, command_id:str, role:str|null, substate:str|null, review_round:int|null, attempt:int|null, doc:str|null, objective:str|null, assignment:object|null, start_ts:str, end_ts:str|null, status:"ok"|"running", output_summary:str, input_ref:str|null, output_ref:str|null, session_id:str|null, has_thinking:bool}`。round 窗口 = 该 stage 最后一次 `stage.entered` 至下一 `stage.exited`（未退出则开放）；每次 `dispatch_agent` 一轮，outcome 按 command_id 配对。
2. **`GET /api/runs/{run_id}/rounds/{command_id}`**（只读）：200 `RoundDetail = {command_id, role, substate, review_round, attempt, start_ts, end_ts, status, running, params:{role,substate,review_round,attempt,doc,objective,assignment}, input:{prompt:str, assignment:object, role, substate, attempt, review_round, doc, objective}|null, output:{text:str, tool_calls:[{tool,n}], stderr:str}|{"unavailable":true}|null, thinking:[{ts,text}]|null, thinking_available:bool, live:[{ts,kind,tool,label,text}]}`；未知 run/command → 404；blob 缺失 → input null / output `{"unavailable":true}`；日志轮转 → `thinking=null, thinking_available=false`。
3. **配额**：输入 prompt 头 8000 字符、输出尾 12000、stderr 头 2000、单轮摘要头 300、thinking 尾 30 条、每思考项 1200、live 尾 40 条/每项 1200；均为既有实现，不重议。
4. **UI**：`stage-rounds` 首次展开才请求列表；`stage-round-<n>` 首次展开才请求详情并按 command_id 缓存；四个区（输入/输出/思考过程/工具）均有可读标题；运行中的轮次轮询详情（2.5s）直到终态；失败区内联显示“加载失败，稍后重试”。

### 1g. 阶段路径与阶段实际工作流（IF-STAGEPATH-001 / IF-STAGEWORKFLOW-001）

**modules**：`tracks/server/static/app/timeline.js`；数据源 `GET /api/runs/{run_id}/timeline`、`/ac-chain`、`GET /api/runs/{run_id}`。

1. **阶段路径**：`stage-track-carousel` 包裹 `timeline-stage-order`（`<ol>`），13 个 `timeline-node-<stage>`；每个节点携带 `data-state ∈ {done, active, pending, attention, void}`，当前阶段在门控时为 `attention`，非门控时为 `active`；回拨以 `timeline-back-edge-<from>-<to>` 可辨认；当前节点初始居中并保留邻近上下文。该轨道是 FR-0355 的“状态图”交付面（OOB 系列已用它替代静态 mermaid）。
2. **节点详情**：选中节点后 `detail-pane`/`node-detail` 显示 `state`、`start/end`（浏览器本地 `YYYY-MM-DD HH:mm:ss`）、`owner`、`detail-owning-surface`、`detail-doc-link`；无事件节点显示 `no recorded events for <stage> yet` 可读降级，不伪造时间；`timeline` 404 时 `Unknown run: <id>`。
3. **阶段工作流**：`stage-path` 面板含 `stage-workflow-summary`（`<stage> · 派发 N 次 · 交互轮次 R · 人工动作 H · 进行中/用时`）与 `stage-workflow-diagram`（mermaid `stateDiagram-v2` 的 SVG；无窗口时 `stage-workflow-empty` 文本）；重复派发折叠为 `重派 ×K`/边 `×N`/`第N轮`；末端为人工门（当前 stage awaiting）或完成；渲染失败显示“（阶段图渲染失败，稍后重试）”。
4. **数据一致**：全部来自 run 事件流/投影；不引入第二数据源、不 mock；阶段窗口 = 最后一次 `stage.entered` → 下一 `stage.exited`。

### 1h. 评审两决策与退回线程（IF-REVIEWDECISION-001）

**modules**：`tracks/server/static/app/timeline.js`（reviewDecisionFace）、`tracks/server/api_command.py`（`POST /review`）、`tracks/supervisor/gate_exec.py`、`tracks/cli/run_cmd.py`、`tracks/executor/result_checkpoint.py`、`tracks/discuss`（根线程写入复用）。

1. **面板**：review 门只呈现两个决策控件：`review-approve`（通过；`review-comments` 为空时也可用）与 `review-return`（退回；`review-comments` 非空时可用）；`review-hint` 随输入切换；`review-feedback` 承载失败，`data-failed=true`，不伪报成功。正常流门不渲染 `gate-reason`/`gate-impact`；异常门（escalation/rollback/hotfix_triage/review_pending_threads）保留两者。
2. **HTTP**：`POST /api/runs/{run_id}/review` body `{action: "no_comment"|"comment", comments?: string}`，经 CSRF + Idempotency-Key + awaiting 前置（`review` 或 `review_pending_threads`）。`action=comment` 要求 `comments` 为非空字符串，否则 `validation_failed`；`action=no_comment` 要求 `comments` 缺省/空，否则 `validation_failed`。
3. **执行序**：`action=comment` 的 worker 在 writer_lock 内按序：(a) 解析当前阶段文档（story/spec/acceptance）；(b) 以该 review 命令的 actor（与随后 `human.review.payload.actor` 同一值）在该文档首个非空行发起一个根讨论线程（正文 = comments，open 状态）；(c) 提交 `human.review`，payload `{actor, comments, doc, thread_id}`；(d) 命令 completed。线程写失败 → `command.failed(reason=review_thread_write_failed)`，不落 `human.review`，门不推进；UI 内联呈现。`action=no_comment` 跳过 (a)(b)，直接落 `human.review`（不含 comments/doc/thread_id）。
4. **可观察**：文档内新增线程可由既有 discussion parser 解析（thread_id/root speaker/body）；`human.review` 事件 payload 含 comments/doc/thread_id；门控随后继续（通过/退回/搁置闭环）。重复提交不产生重复事件/线程（幂等键 + awaiting 前置）。

### 1i. 静态文本即时性（IF-STATICCACHE-001）

**modules**：`tracks/server/app.py`（`_AppModulesNoCache`、`_StaticGzip`）。

1. **no-cache**：对 `/static/app/*` 与 `/static/` 下后缀 `.css/.js/.mjs/.html/.json/.txt/.map` 的 GET 响应追加 `Cache-Control: no-cache`；保留 ETag/Last-Modified，未变更资源带 `If-None-Match` 时返回 304。
2. **范围**：API 路由、SSE、页面 HTML 不受该中间件影响；`_StaticGzip` 只压缩 `/static/`，不包裹 SSE。
3. **内容即时性**：静态文件内容变更后，普通刷新返回新字节，不依赖启发式缓存；未变更文件仍 304。API 路由语义不变。

### 1j. 事件 payload 扩展（IF-REVIEWDECISION-001，事件类型不变）

1. `human.review` payload 追加可选键：`comments`（string，仅 `action=comment`）、`doc`（`story|spec|acceptance`）、`thread_id`（`T-NNN` 形态的已落盘线程 id）。既有 `actor` 与 verdict 语义不变；`action=no_comment` 的 payload 不携带这三个键。
2. kernel 的 `state.review_comments` 仍从 `human.review.comments` 归约，RESPOND dispatch 继续注入 `human_comments`；v0.12 不改变该链，只恢复线程写入。
3. 不新增 EVENT_TYPES / service_events 成员；不新增命令 kind；`record_review` 的 params 继续是 `{run_id, action, comments?}`。

## 2. CLI 接口合同

### 2a. 命令表（新增/变更）

| # | 命令 | 输入 | 前置 | 成功输出 | 失败输出 | exit |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | `trac --help` / 无参数 | — | 无 | USAGE（含 install/version/serve 新 flag） | — | 0 |
| 2 | `trac install <path> [--version <v>] [--wheel <path>]` | 目标路径、可选版本/wheel | 目标可写；空目录或 Git 项目；版本/wheel 可解析且资源完整 | `installed tracks <version> at <path>` | `trac install: <reason>: <detail>`（reason 封闭集 §1c.5） | 0 / 1 |
| 3 | `trac version [--json]` | — | 无 | `trac <version> (global|project <path>)` 或 JSON `{scope,version,project_root?,site_packages?}` | `install_corrupt: <detail>` | 0 / 1 |
| 4 | `trac serve --repo <path> [--host H] [--port P] [--password-stdin] [--wait-initial-s N] [--wait-cap-s N] [--poll-interval-s N] [--poll-idle-cap-s N]` | 既有 serve 参数 | `--repo` 指向已安装项目（用户面） | `serving on http://H:P (pid N)`；前台阻塞 | `not_installed: ...`、启动失败原因 | 0 / 1 / 2 |
| 5 | `trac serve --repo <path> --background` | 同上 + background | POSIX；已安装项目；密码供给可用（首启） | `serving on http://H:P (pid N, version V, background)` | `unsupported_platform` / `not_installed` / `background_failed: ...` | 0 / 1 |
| 6 | `trac serve --repo <path> --stop` | — | 已安装项目 | `stopped pid=N` / `not running` | `not_installed` / `stop_refused: ...` | 0 / 1 |
| 7 | `trac serve --repo <path> --status` | — | 已安装项目 | `running pid=N version=V url=...` / `stopped` / `stopped (stale pidfile)` | `not_installed` / `running (unhealthy: ...)` | 0 / 1 |

### 2b. 既有命令

`init/start/run/hotfix/triage/review/approve/return/recover/retry/status/replay/report/validate/validate-reply/discuss/check/release/abandon` 的参数面、输出与退出码逐字继承；在已安装项目内由全局 CLI 委派给项目 launcher（除非 `TRAC_PROJECT_ACTIVE=1`）。`trac serve` 的既有前台语法与 `--repo` 必填语义在直接 `cmd_serve` 调用面保持不变。

### 2c. HTTP 合同（新增/登记）

| # | method/path | 输入 | 成功 | 失败 | 备注 |
|:--|:--|:--|:--|:--|:--|
| 1 | `GET /healthz` | — | 200 `{status, projects, version, uptime_s}` | 503 | 公开；version 为项目版本 |
| 2 | `GET /api/runs/{run_id}/stages/{stage}/rounds` | path | 200 §1f.1 | 404 | 只读 |
| 3 | `GET /api/runs/{run_id}/rounds/{command_id}` | path | 200 §1f.2 | 404 | 只读 |
| 4 | `GET /api/runs/{run_id}/activity` | path | 200 `{run_id, version, stage, substate, awaiting, stage_entered_at, role, sessions, items}` | 404 | 继承遗留读面；API 语义不变，**不属 v0.12 断言基线**（无 AC/IF/测试承诺） |
| 5 | `POST /api/runs/{run_id}/review` | `{action, comments?}` | 202 `{command_id}` | 422 `validation_failed` / `awaiting_mismatch` | §1h；CSRF + Idempotency-Key |
| 6 | `GET /static/app/*`、`GET /static/*.{css,js,mjs,html,json,txt,map}` | — | 200/304 + `Cache-Control: no-cache` | — | §1i |
| 7 | 既有 #1-44 端点 | 不变 | 不变 | 不变 | 逐字继承 |

### 2d. 退出码约定

- `install`：0 成功；1 任一失败（reason 区分）。
- `version`：0 成功；1 `install_corrupt`。
- `serve` 前台：0 优雅停止；1 启动失败/无密码（既有 2 的密码引导除外——保留既有 exit 2）；`not_installed` 为 exit 1。
- `serve --background`：0 成功/already running；1 失败。
- `serve --stop`：0 stopped/not running；1 refused/失败。
- `serve --status`：0 running/stopped；1 unhealthy/not_installed。

## 3. 文件 / 存储契约

| # | 路径 | 格式/写入者 | 读取者/生命周期 |
|:--|:--|:--|:--|
| 1 | `tracks/assets/opencode.json` | 静态 JSON（wheel 内）；Archer scaffold | 安装器读取并 seeded 到目标；随包分发 |
| 2 | `<project>/.tracks/install.json` | JSON；`tracks/install.py` 原子写 | `project_runtime`/launcher/`trac version`；持久，随项目提交 |
| 3 | `<project>/.tracks/env/<version>/site-packages/**` | 安装器从运行 distribution 或 wheel 复制 | 项目 launcher 的 `sys.path`；持久，`.tracks/env/.gitignore` 忽略 |
| 4 | `<project>/.tracks/env/.gitignore` | 文本 `*`；安装器写 | git；持久 |
| 5 | `<project>/.tracks/bin/trac.py` | Python launcher；安装器写 | 全局 CLI 委派/用户直接调用；持久，随项目提交 |
| 6 | `<project>/.opencode/agents/**` / `skills/**` / `templates/**` | 安装器从被安装版本复制；managed files | opencode 发现/运行；持久，随项目提交 |
| 7 | `<project>/opencode.json` | 默认配置；仅当目标缺失时 seeded | opencode；用户配置，后续安装不覆盖 |
| 8 | `<project>/.tracks/service/serve.pid` | JSON；`--background`/`--stop` 写/删 | `--status`/`--stop`；运行态 |
| 9 | `<project>/.tracks/service/serve.log` | 追加文本；后台子进程 stdout/stderr | 用户/失败诊断；运行态 |
| 10 | `<version>/story|spec|acceptance.md` 的讨论块 | 既有 discuss writer；review 退回写根线程 | 文档中心/评审者；持久，随项目提交 |
| 11 | `tracks.db` / `service.db` | 既有事件/命令存储 | 既有读取面；v0.12 无 schema 变更 |

临时产物：`.tracks/.install-staging-<ulid>/` 仅安装期存在，成功/失败后清理；`.tracks/env/.gitignore` 与 `.tracks/service/*` 为运行态，不属于用户项目文件。

## 4. 可观察出口（测试断言基础）

### 4a. CLI / 文件出口

| # | outlet | assertions |
|:--|:--|:--|
| 1 | `trac --help` stdout/exit | 0；usage 含 install/version/serve；任意非项目 cwd |
| 2 | `trac install` stdout/stderr/exit | 成功单行、失败 reason 封闭集、无半成品 receipt |
| 3 | `trac version` stdout/JSON | scope/version 与 receipt/site-packages/healthz 一致 |
| 4 | `trac serve --status/--stop` stdout/exit | running/stopped/pid/version/url；stop 后不可连接 |
| 5 | 项目 `.tracks/install.json` | 字段/sha/version；重装/全局升级前后变化与不变 |
| 6 | `.tracks/env/<version>/site-packages/tracks/__init__.py` | `__version__` 等于 receipt.version |
| 7 | `.opencode/{agents,skills,templates}/**` 与根 `opencode.json` | 托管文件字节来自所装版本；根 `opencode.json` seeded-only 不被覆盖 |
| 8 | 用户文件 sha | 安装/重装前后逐字节不变 |
| 9 | wheel zip 名单 | 含 §1a 资源封闭清单 |

### 4b. HTTP / 事件出口

| # | outlet | assertions |
|:--|:--|:--|
| 1 | `GET /healthz` | `version` 等于项目锁定版本；status/projects/uptime 既有字段 |
| 2 | `GET /api/runs/{run_id}/stages/{stage}/rounds` | schema §1f.1；轮数=dispatch 数；无窗口空列表 |
| 3 | `GET /api/runs/{run_id}/rounds/{command_id}` | schema §1f.2；input/output/thinking/tool_calls；缺失降级 |
| 5 | `POST /api/runs/{run_id}/review` | 202/422；CSRF/幂等；`human.review` payload comments/doc/thread_id |
| 6 | 文档讨论块 | review 退回后新增根线程可解析；正文=comments |
| 7 | 静态响应头 | `Cache-Control: no-cache`；ETag 未变更 304；API 无该头 |
| 8 | `service.started` | payload version 等于项目版本 |

注：`GET /api/runs/{run_id}/activity`（run 版）是继承的未消费遗留读面；v0.12 只保留既有 API 语义，它不在本断言表内，无 AC/IF/测试承诺。

> **Prism:** 【阻塞 finding PRISM-D13-03 · DESIGN-5 / DESIGN-1 interfaces↔test-plan 闭包】§1d.5（行 97）与 §4b#8（行 233）把 `service.started` 的 payload `version` 声明为 v0.12 版本归属断言出口，IF-VERSIONVIS-001（行 292）也写明「trac version / healthz version / service.started version / receipt version 三者一致」；test-plan §1.2（行 33）同样把 `service.started` version 列入可观察对象，§8 的 AC-FR0350-01 与 AC-FR0353-01 行也引用了 IF-VERSIONVIS-001。但 test-plan 全文没有任何测试名或旅程绑定该断言：§8 的 `test_background_survives_shell_and_reports_locked_version` 只断言 stdout/healthz/--status，`test_global_upgrade_keeps_locked_project` 只断言 version/status；§9.1/§9.2 未提该事件；§10 #15/#19 也只覆盖「无 service.started」与 healthz/stop，从未读取事件 payload 的 version。全仓 tests/ 对 service.started 也无 version 断言。因此该外部出口无测试覆盖，DESIGN-5 的 interfaces↔test-plan 闭包不成立。请二选一收口：(a) 在 §8/§9 明确绑定一个测试（可扩展现有 lifecycle 测试，读取 service 事件存储并断言 service.started.payload.version == receipt.version，并更新 §8.1 守卫/锚点表）；或 (b) 若该事件不属于 AC-FR0350-01/0353-01 的 v0.12 断言基线，像 run `/activity` 一样在 interfaces §1d.5/§4b#8 与 test-plan §1.2 显式标注为继承观察点、不纳入 v0.12 测试承诺。

> **Prism [RESOLVED]:** 【阻塞 finding PRISM-D12-01 · DESIGN-5 interfaces↔test-plan 闭包】interfaces.md §4b#4（并 §2c#4）把 GET /api/runs/{run_id}/activity 登记为 v0.12 外部出口并声明断言（schema、活动 items 时间序）；architecture.md 行 25 把该系列面列入「补齐可观察合同与测试」，test-plan §1.1/§1.2 也把 activity 列为 v0.12 对应出口——但 test-plan §8 的 24 条 AC 锚点无任何 activity 测试，全仓 tests/ 对该端点零引用（run 版端点的 UI 消费方已在 ffaeed4 移除，现存 activity 引用只是 wizard.js 的 intake 版端点），且无 AC/IF 文本承载该出口。结论：DESIGN-5 的「每个外部出口都有测试覆盖」不成立。请二选一收口：(a) 为该出口补测试（FR-0140 trace 门禁要求每个测试引用至少一个 AC；若无 AC 可承载，需先显式收窄注册）；(b) 在 interfaces/test-plan 显式标注该出口不属 v0.12 断言基线（未消费遗留读面），使其不再构成合同承诺。本轮其余核验（24 条 required AC 闭包与六元组、Scaffold 宣言与实物、quality registry digest、project.toml 合同、arrival-green 象限、OOB testid/schema 登记）均通过，无其它阻塞。
>> **Archer:** 采纳 (b) 收口，已修订三处：interfaces §0/§0.1 明确 run `/activity` 为未消费遗留读面、不纳入 v0.12 断言基线；§2c#4 保留既有 API 语义但标注“无 AC/IF/测试承诺”；§4b 移除该行并加注说明。test-plan §1.1/§1.2 已移除 activity 出口；architecture §0.2/§1.0.1/§1.1 同步标注为继承遗留、不新增断言。没有新增 activity 测试，rounds/round-detail 断言不变。请复核。

### 4c. 浏览器可观察出口（UI e2e，IF-WEBUI-001 继承）

| # | outlet | assertions |
|:--|:--|:--|
| 1 | 阶段轨道 | `timeline-stage-order` 13 阶段；`timeline-node-*` 的 `data-state` 五态；`timeline-back-edge-*` 可辨认 |
| 2 | 节点详情 | `detail-pane`/`node-detail` 的 state/start-end/owner/owning surface；无事件降级 |
| 3 | 阶段工作流 | `stage-path`/`stage-workflow-summary`/`stage-workflow-diagram`；折叠/人工门/完成；渲染失败文本 |
| 4 | 按轮 | `stage-rounds`/`stage-round-<n>`；输入/输出/思考/工具四区；缺失/失败说明 |
| 5 | 运行卡片 | `card-owner`/`card-attempt`/`card-duration`/`card-action`；等待时长不因心跳重置；本地时间 |
| 6 | 评审门 | `review-approve`/`review-return`/`review-comments`/`review-hint`/`review-feedback`；正常门无 reason/impact，异常门保留 |
| 7 | 空验收链 | `ac-chain` 在空时隐藏（非空白占位） |

## 5. IF Registry

### IF-INSTALLPKG-001 官方安装包内容合同

- **合同**：§1a——wheel 资源封闭清单（agents/skills/templates/assets/opencode.json）；版本 0.12.0；默认 opencode.json 权限块；缺项即 `package_incomplete`。
- **modules**：pyproject.toml, tracks/assets/opencode.json, tracks/install.py。
- **关联**：FR-0348、FR-0349、FR-0354。

### IF-INSTALL-001 trac install 项目安装合同

- **合同**：§1b/§1c——目标/版本/来源解析；staging→校验→替换→receipt→health check→commit；成功/失败输出与 reason 封闭集；幂等/不自动升级。
- **modules**：tracks/cli/install_cmd.py, tracks/install.py, tracks/project_runtime.py。
- **关联**：FR-0348、FR-0351、FR-0352、FR-0354。

### IF-PROJLAYOUT-001 项目安装布局与文件权属合同

- **合同**：§1b/§3——receipt schema；site-packages/launcher；managed_files 与 seeded_files；`.tracks/env` 不进 git；用户文件逐字节保留。
- **modules**：tracks/install.py, tracks/project_runtime.py, 目标项目 `.tracks/`/`.opencode/`。
- **关联**：FR-0351、FR-0352、FR-0353、FR-0354。

### IF-PROJECTCLI-001 全局→项目 CLI 委派与版本锁定合同

- **合同**：§1d——项目发现；非 install/version/help 命令委派；`TRAC_PROJECT_ACTIVE=1` 防递归；receipt/site-packages 损坏 fail-closed；项目内所有命令使用锁定版本。
- **modules**：tracks/cli/main.py, tracks/project_runtime.py, `.tracks/bin/trac.py`。
- **关联**：FR-0349、FR-0350、FR-0353、NFR-0159。

### IF-GLOBALCLI-001 全局 trac 可用与无全局服务器合同

- **合同**：§1d.1/§1e——`trac --help` 任意目录 exit 0；全局命令只做安装与项目级服务入口；项目外 serve 拒绝；无全局服务器兜底。
- **modules**：tracks/cli/main.py, tracks/cli/install_cmd.py, tracks/cli/serve_cmd.py。
- **关联**：FR-0349、FR-0350。

### IF-SERVERLIFE-001 项目服务器生命周期合同

- **合同**：§1e——前台语义继承；POSIX 后台 detached 子进程/pidfile/log；`--stop` 归属校验与不可连接；`--status` 版本/url；Windows 前台/`unsupported_platform`。
- **modules**：tracks/cli/serve_cmd.py, tracks/project_runtime.py, tracks/server/app.py。
- **关联**：FR-0350、NFR-0159。

### IF-VERSIONVIS-001 版本可观察合同

- **合同**：§1d.3/§1e——`trac version`（global/project scope、JSON）；healthz version；service.started version；receipt version；三者一致。
- **modules**：tracks/cli/install_cmd.py, tracks/project_runtime.py, tracks/server/api_query.py。
- **关联**：FR-0350、FR-0353。

### IF-STAGEPATH-001 阶段路径状态图合同

- **合同**：§1g.1/§1g.2——13 阶段轨道、五态 data-state、回拨边、节点详情 start/end/owner/owning surface、无事件降级。
- **modules**：tracks/server/static/app/timeline.js, tracks/server/api_query.py。
- **关联**：FR-0355、FR-0358。

### IF-STAGEWORKFLOW-001 阶段实际工作流聚合合同

- **合同**：§1g.3——stage 窗口、聚合图、摘要、重派/超长折叠、人工门/完成末端、渲染失败降级、单数据源。
- **modules**：tracks/server/static/app/timeline.js, tracks/server/api_query.py。
- **关联**：FR-0356。

### IF-ROUNDS-001 按轮读取合同

- **合同**：§1f——round 列表/详情 schema、stage 窗口、dispatch/outcome 配对、blob 输入输出、thinking 轮转降级、工具计数、懒加载。
- **modules**：tracks/server/run_rounds.py, tracks/server/app.py, tracks/server/static/app/timeline.js。
- **关联**：FR-0357、NFR-0160。

### IF-RUNCARD-001 运行卡片与本地时间合同

- **合同**：§1g.2/§1h.1——card-owner/attempt/duration/action；人类单位耗时；本地时间；门控等待以非心跳业务事件为起点，心跳不重置。
- **modules**：tracks/server/static/app/timeline.js, tracks/server/projections.py。
- **关联**：FR-0358。

### IF-REVIEWDECISION-001 评审两决策与退回线程合同

- **合同**：§1h/§1j——review 面仅通过/退回；通过空意见；退回必填并先写文档根线程；human.review payload comments/doc/thread_id；失败内联不伪报；异常门 reason/impact。
- **modules**：tracks/server/static/app/timeline.js, tracks/server/api_command.py, tracks/supervisor/gate_exec.py, tracks/cli/run_cmd.py, tracks/executor/result_checkpoint.py, tracks/discuss。
- **关联**：FR-0359。

### IF-STATICCACHE-001 静态文本 no-cache 合同

- **合同**：§1i——`/static/app/*` 与文本后缀 no-cache；ETag 304；API/SSE 不受影响；内容变更普通刷新可见。
- **modules**：tracks/server/app.py。
- **关联**：FR-0360、NFR-0160。

### 继承合同组（本版零语义变更，聚合登记）

IF-WEBUI-001（UI e2e 通道/testid 纪律）、IF-QUERY-001（只读投影查询）、IF-STREAM-001（SSE 游标补读）、IF-CMDSVC-001（持久命令受理/幂等）、IF-GATEUI-001（人工门命令端点与 worker 执行）、IF-AWAIT-001（awaiting 动作面板）、IF-DOCSWRITE-001（评审轮写入通道）、IF-VERSION-001（制品声明版本绑定）、IF-RELEASES-001（Releases 窗口与聚合投影）、IF-WORKBENCH-001（SPA shell）、IF-MTEST-001 / IF-TRACE-001..003 / IF-REACH-001..002 / IF-VALIDATE-001 / IF-IMPL-001..007 / IF-DEVON-001 / IF-LIVE-001 / IF-RELEASE-001..003 / IF-HOSTCONTRACT-001..002 / IF-GUARD-001..002 / IF-GREENGUARD-001 / IF-EVIDENCE-001 / IF-LEDGER-001 / IF-FULLCHAIN-001 / IF-RUNCONTRACT-001 / IF-NIGHTLY-001 / IF-PHASE-001..003 / IF-SECURITY-001 / IF-PUBLISH-001..002 / IF-MILESTONE-001..002 / IF-JOURNEY-001 / IF-ENVELOPE-001..002 / IF-FAILURE-001 / IF-REFERENCE-001 / IF-ISSUE-001..002 / IF-PIPELINE-001 / IF-SERVE-001 / IF-PROJ-001 / IF-CMDGUARD-001 / IF-LEASE-001 / IF-SCHED-001 / IF-WAIT-001 / IF-RECOVER-001 / IF-PAUSE-001 / IF-WEBAUTH-001 / IF-SECRECY-001 / IF-DOCREV-001 / IF-TIMELINE-001 / IF-TIMELINE-002 / IF-RETURN-001 / IF-INTAKE-001 / IF-MSTART-001 / IF-BACKLOG-001 / IF-HOTFIXUI-001 / IF-SPLIT-001 / IF-REVIEW-002 / IF-ADAPTER-003 / IF-VERIFY-001..005 / IF-REPAIR-001..002 / IF-KNOWNISSUE-001 / IF-ESCAPE-001..002 / IF-DEMO-001 / IF-FAILCLOSED-001 / IF-QUARANTINE-001 / IF-HOTFIX-001..011 / IF-SELECT-001..002 / IF-MUTATION-001..002 / IF-CLOSURE-001 / IF-AUTH-001..002 / IF-DOCGAP-001 / IF-AUTHNAME-001 / IF-DOCCENTER-001 / IF-DOCSAVE-001。以上均继承 IF-001～IF-011 既有登记，语义逐字不变；本版只引用其中 IF-WEBUI-001、IF-QUERY-001、IF-STREAM-001、IF-CMDSVC-001、IF-GATEUI-001、IF-AWAIT-001、IF-DOCSWRITE-001、IF-VERSION-001、IF-RELEASES-001、IF-WORKBENCH-001、IF-MTEST-001、IF-TRACE-001、IF-GREENGUARD-001。
