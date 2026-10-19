---
envelope: tracks-envelope:v2
architecture_id: ARCH-012
spec_ref: SPEC-012
created: 2026-10-19
status: draft
sha:
---

# v0.12 — 架构：安装包、全局 trac、项目版本锁定与 OOB 工作台防回退

本文是 ARCH-011 的增量延伸。v0.1～v0.11 的事件溯源、单写者、canonical 阶段状态机、命令 WAL、supervisor 串行驱动、发布闭环、质量守卫 registry、测试流水线、CLI、v0.9 服务面、v0.10 工作台 chrome、v0.11 Releases/时间线/人工门自驱动全部保持不变。v0.12 在此之上做三件事：(1) 把 wheel 做成可承诺的官方安装包（skills/templates/agents/默认 `opencode.json`），并新增 `trac install <path>` 的离线项目安装；(2) 用项目级 site-packages + receipt 固定项目版本，全局 `trac` 只做安装与委派入口，项目内服务由项目锁定版本提供，macOS/Linux 为后台服务、Windows 为前台；(3) 把 v0.12 OOB 已验证的工作台改进（阶段路径、阶段实际工作流聚合、按轮明细、可读卡片与本地时间、评审两决策、静态文本 no-cache）纳入合同并补齐防回退测试。

核心取舍：**项目安装不建 venv，也不用 pip/网络**。安装器从“正在运行的 distribution”或显式传入的官方 wheel 中复制 `tracks` 包与 `.dist-info` 到 `<project>/.tracks/env/<version>/site-packages/`，项目 launcher 用记录的解释器加载该目录；运行时依赖（starlette/uvicorn/certifi）由该解释器的环境提供。这样安装可离线、可重复、可原子替换，且不依赖包索引；代价是项目版本与全局解释器的依赖集合共享（§5.2）。**项目服务用 detached 子进程**（POSIX `start_new_session`）而非 launchd/systemd：spec 只要求“发起 shell 退出后继续运行直至显式停止”，不要求开机自启与日志轮转。**评审退回恢复文档线程写入**：当前 HEAD 的 `c6dc3f8` 把退回意见改为只随 `human.review` 直达 Scribe，而 FR-0359 要求“意见先记为文档线程”；本设计在 worker 内先写根线程再落 `human.review`（comments 同时保留在事件 payload），既满足 AC 又保留直达 Scribe 的反馈链（§1.0.8）。

## 0. 延续性声明（什么不变）

### 0.1 继承 ARCH-011 及此前各版

- 唯一生产路径 `cli -> kernel.machine.decide -> executor.execute -> store.append`、SQLite append-only events、投影可重建、单写者 writer_lock 与 per-kind reconcile 不变。v0.12 **不新增 tracks.db 事件类型**；`human.review` 仅追加可选 payload 键（§1.0.8）。
- `kernel/` 纯控制、`executor/` 副作用、`checks/` 静态闭合、`effects/` 外部通道、`adapters/` 宿主测试框架语义、`cli/` 交付面、`server/` Web 交付面、`supervisor/` 后台驱动控制层的分层不变。安装器属于 `cli/` 的宿主 provisioning 面，不进入 kernel/executor。
- 既有 CLI 子命令集合与语义不变：`init/start/run/hotfix/triage/review/approve/return/recover/retry/status/replay/report/validate/validate-reply/discuss/check/release/abandon/serve` 的参数面与退出码逐字继承。`trac serve` 的前台行为与 `serve_cmd.cmd_serve` 的直接调用面不变（测试夹具继续直接 import 该函数）；v0.12 只在外层 CLI 入口追加项目安装前置与生命周期 flag。
- `.tracks/projects/project.toml` 的 `[unit]/[integration]/[e2e]/[nightly]/[adapter]/[layout]/[lint]/[host-contract.*]` 段逐字继承；v0.12 不修改该文件。测试并发、JUnit 参数、`ui` marker 通道隔离、质量守卫阈值与 required checks 七名全部继承。
- v0.9 服务面：HTTP API 基底约定（认证、CSRF + Idempotency-Key、错误封装）、命令服务、supervisor 单活动 run 串行、SSE、redaction、本机单用户边界全部继承。v0.10/v0.11 的 PAGES 六名、五 tab、Releases 投影、时间线/ac-chain、awaiting_gate/return_options、评审轮写入通道全部继承。
- v0.12 OOB 已落地的读取面与 UI 面按现状纳入合同：`GET /api/runs/{run_id}/stages/{stage}/rounds`、`GET /api/runs/{run_id}/rounds/{command_id}`；`timeline.js` 的阶段轨道、阶段实际工作流聚合图、按轮手风琴、可读卡片与本地时间、评审两决策面；`_AppModulesNoCache` 的静态文本 no-cache。这些面不重做设计，只补齐可观察合同与测试（§1.0.7/§1.0.8）。`GET /api/runs/{run_id}/activity`（run 版）是未消费遗留读面，仅继承既有 API 语义，不纳入 v0.12 断言基线。
- `tracks/agents`、`tracks/skills`、`tracks/templates` 的 canonical 内容与 agent materialization 语义不变；v0.12 只是把同一份资源纳入官方 wheel 承诺并由项目安装复制到宿主 `.opencode/`（agents/skills/templates），根 `opencode.json` 仅 seeded。
- 发布闭环、version gate bindings、`release-evidence` milestone、reference host、三旅程合同、security scan、build/smoke 合同全部继承。v0.12 设计期把 `pyproject.toml` 与 `tracks/__init__.py` bump 到 `0.12.0`（version gate 绑定对象，§2/§4.2）。
- 质量守卫八类与 canonical registry 机制不变；CI required checks 七名（lint/coverage/test/deliverables/trace/reach/ui-e2e）不变。`pyproject.toml` 的 bytes 变化（版本 + package-data）使六条 pyproject-backed `config_digest` 重锚（§4.2）；`.tracks/projects/project.toml` 与 `.flake8` 未变化，digest 继承。

> **Prism:** 【阻塞 finding PRISM-D13-01 · DESIGN-1/8 合同真实性】本节行 28 称 `.tracks/projects/project.toml` 与 `.flake8` 未变化、digest 继承；但同文档 §4.2（行 243）明确该文件因 OOB 修复 e2e `collect` 的 marker 过滤而 bytes 变更，`hooks-runner-ci-required` 的 `config_digest` 由 `e1735466…` 重锚为 `e534d31d…`，且该 TOML block 的 digest 与工作区文件实测 sha256 一致（`e534d31d9f4ab09153e1151b73b5a87ebab5a8b675ad5eaf714391b13082d50c`）。行 23「v0.12 不修改该文件」与 test-plan §2.3.1 行 103「v0.12 零变更，逐字继承」同样沿用了变更前的前提。请统一三处口径：在 §0.1 与 test-plan §2.3.1 写明 project.toml 在 v0.12 期间因 OOB 修复产生 bytes 变更并重锚 digest（引用 §4.2），`.flake8` 仍为未变化继承；若意图是「本设计不改文件」，请明确限定为设计变更集并同时说明现行字节已含 OOB 修复。其余核验（三向闭包/六元组/命令实跑/scaffold/registry digest/e2e collect 与 ui 选择）通过。

### 0.2 v0.12 变更

- 官方包内容：`pyproject.toml` package-data 追加 `assets/opencode.json`；wheel 必须包含 `tracks/agents/*.md`、`tracks/skills/*/SKILL.md`、`tracks/templates/**` 与 `tracks/assets/opencode.json`（FR-0348）。
- 新 CLI：`trac install <path> [--version <v>] [--wheel <path>]`、`trac version [--json]`；`trac serve` 追加 `--background/--stop/--status`（FR-0349/0350/0351/0353/0354）。`USAGE` 与 `TRAC_SUBCOMMANDS` 同步登记，属声明性 scaffold（§2）。
- 新项目布局：`.tracks/install.json`（版本锁定 receipt）、`.tracks/env/<version>/site-packages/`（项目 distribution）、`.tracks/bin/trac.py`（项目 launcher）、`.tracks/service/serve.pid`/`serve.log`（后台服务）。`.tracks/env/` 自忽略，不进 git；receipt、launcher、根 `opencode.json`（seeded 时）与 `.opencode/` 托管文件由安装提交（§1.0.2/§1.0.5）。
- 全局 CLI 委派：非 `install/version/--help` 命令在已安装项目内委派给项目 launcher；项目 launcher 以 `TRAC_PROJECT_ACTIVE=1` 阻止二次委派。项目外 `serve` 拒绝，不落全局版本兜底（FR-0349/0350/0353）。
- 项目服务生命周期：前台 `trac serve --repo <path>` 语义不变；`--background` 仅 POSIX，启动 detached 子进程并写 pidfile；`--stop/--status` 读 pidfile 并做 PID 归属校验；Windows 仅前台，`--background` 返回 `unsupported_platform`（FR-0350/NFR-0159）。
- `record_review` 的 `action=comment` 在 worker 内先写文档根线程（锚定当前阶段文档首个非空行），再落 `human.review`（payload 追加 `comments/doc/thread_id`）；`action=no_comment` 不写线程。UI 仍只发一次 review 命令，失败内联呈现（FR-0359）。
- OOB 合同补齐：阶段路径/工作流/按轮/卡片/评审/静态缓存的 testid、HTTP schema 与观测点写入 interfaces §1/§4；v0.11 遗留的 `reason/impact` 与 `gate-action-review` 断言按 FR-0359-02 与 OOB 现状更新（test-plan §11）。
- 设计期 scaffold：`tracks/assets/opencode.json`（默认 harness 配置）、`pyproject.toml`（0.12.0 + package-data）、`tracks/__init__.py`（0.12.0）、`tracks/executor/validate.py`（`TRAC_SUBCOMMANDS` 登记 `install/version`）、`tracks/cli/main.py`（`USAGE` 登记新命令与 serve flag）。这些是声明/配置，不是业务实现；安装器、委派、生命周期、review 线程写入的行为体均为待实现 Devon 任务（§2）。

## 1. 模块边界

### 1.0.1 增长轴归属

| # | 包/文件 | v0.12 增长 | 触发 | IF |
|:--|:--|:--|:--|:--|
| 1 | `tracks/install.py`（新，**待实现 Devon**） | 安装计划与执行：wheel/运行中 distribution 的资源发现、版本校验、staging、托管文件物化、receipt 写入、安装后 health check | FR-0348/0351/0352/0354 | IF-INSTALLPKG-001, IF-INSTALL-001, IF-PROJLAYOUT-001 |
| 2 | `tracks/project_runtime.py`（新，**待实现 Devon**） | 项目根发现、receipt 读写/校验、launcher、PID 归属与后台服务辅助 | FR-0349/0350/0353/NFR-0159 | IF-PROJECTCLI-001, IF-SERVERLIFE-001, IF-VERSIONVIS-001 |
| 3 | `tracks/cli/install_cmd.py`（新，**待实现 Devon**） | `cmd_install`/`cmd_version` 的命令面与参数/错误映射 | FR-0348..FR-0354 | IF-INSTALL-001, IF-GLOBALCLI-001, IF-VERSIONVIS-001 |
| 4 | `tracks/cli/serve_cmd.py` | `cmd_serve` 外层追加安装前置、委派与 `--background/--stop/--status`；前台 uvicorn 核心不变 | FR-0349/0350/NFR-0159 | IF-SERVERLIFE-001, IF-GLOBALCLI-001 |
| 5 | `tracks/cli/main.py` | USAGE 登记（scaffold）+ 非 serve 命令的项目委派钩子（**待实现 Devon**） | FR-0349/0353 | IF-GLOBALCLI-001, IF-PROJECTCLI-001 |
| 6 | `tracks/executor/validate.py` | `TRAC_SUBCOMMANDS` 登记 `install/version`（scaffold，避免设计文档被判 fabricated tooling） | FR-0348/0349 | IF-GLOBALCLI-001 |
| 7 | `tracks/assets/opencode.json`（新，scaffold） | 默认 harness 配置（7 个 tracks agent 的 per-agent external_directory 权限） | FR-0348 | IF-INSTALLPKG-001 |
| 8 | `pyproject.toml` / `tracks/__init__.py` | 0.12.0 + `assets/opencode.json` package-data（scaffold） | FR-0348/NFR-0159 | IF-INSTALLPKG-001, IF-VERSION-001 |
| 9 | `tracks/supervisor/gate_exec.py` + `tracks/cli/run_cmd.py` + `tracks/executor/result_checkpoint.py` | review 退回的文档线程写入 + comments 保留（**待实现 Devon**） | FR-0359 | IF-REVIEWDECISION-001, IF-DOCSWRITE-001 |
| 10 | `tracks/server/static/app/timeline.js` | review 决策面提示文案（线程 + Scribe 双通道）；OOB 其余面不变 | FR-0359 | IF-REVIEWDECISION-001, IF-WEBUI-001 |
| 11 | `tracks/server/run_rounds.py` | OOB 按轮读取面（已交付，本版只补合同/测试） | FR-0357/NFR-0160 | IF-ROUNDS-001, IF-QUERY-001 |
| 12 | `tracks/server/app.py` | `_AppModulesNoCache` 静态文本 no-cache（已交付，本版只补测试） | FR-0360/NFR-0160 | IF-STATICCACHE-001 |
| 13 | `tracks/server/projections.py` | `awaiting_gate` 的 reason/impact/pending threads（已交付，继承）；v0.12 无新增字段 | FR-0358/0359 | IF-AWAIT-001, IF-RUNCARD-001 |
| 14 | `.tracks/projects/project.toml` | 不变（测试执行合同与 layout 逐字继承） | — | IF-MTEST-001 |

### 1.0.2 安装包、项目布局与版本锁定

官方包 = `[host-contract.build]` 产出的 wheel（`dist/*.whl`）。wheel 的资源合同是封闭清单：`tracks/agents/<Role>.md`（7 个角色）、`tracks/skills/<skill>/SKILL.md`（10 个 skill）、`tracks/templates/**`（全部文档与 pre-commit 配置模板）、`tracks/assets/opencode.json`（默认 harness 配置）。安装器只从**被安装版本自己的** `tracks` 包读取资源，不从当前宿主源码树读取，保证“装哪个版本就得到哪个版本的内容”。

项目安装的持久布局：

- `.tracks/install.json`：版本锁定的唯一真相。字段见 interfaces §3；包含 `version`、`python`（绝对路径）、`site_packages`（相对项目根的路径）、`managed_files`、`seeded_files`、`installer_version`、`installed_at`。receipt 以 temp+rename 原子写入，成功写入才算安装成功。
- `.tracks/env/<version>/site-packages/`：该版本的 `tracks` 包与 `.dist-info`。同版本重装复用/替换该目录；不同版本各占一个版本目录。`.tracks/env/.gitignore` 写入 `*`，运行态不进 git。
- `.tracks/bin/trac.py`：launcher。读取 receipt，把 `site_packages` 插入 `sys.path[0]`，设置 `TRAC_PROJECT_ACTIVE=1`，以项目根为 cwd 调用 `tracks.cli.main.main`。launcher 与 receipt 一起提交。
- `.tracks/service/serve.pid` / `serve.log`：仅后台服务使用；pidfile 是 JSON `{pid, host, port, version, repo, started_at}`，日志追加写。
- `.opencode/agents/**`、`.opencode/skills/**`、`.opencode/templates/**`：tracks 托管文件，每次安装无条件覆盖为所装版本内容。
- 根 `opencode.json`：**seeded-only**——仅当目标不存在时写入默认配置；已存在则原样保留（用户 harness 配置不被安装覆盖）。默认配置包含 7 个 tracks agent 的 per-agent `external_directory` 权限，不写模型/provider。用户自有 `.opencode/opencode.json` 不被读取或修改。
- `.tracks/` 脚手架：`projects/`、`runtime/`、`wiki/` 与 `.tracks/*/.gitignore` 由安装器按 `trac init` 同款目录语义幂等创建；**不创建、不读取、不修改宿主根 `.gitignore`**，也不调用会写根 `.gitignore` 的 `cmd_init` 路径。用户项目文件（含既有或缺失的根 `.gitignore`）逐字节保留。
- 安装提交：只 `git add`/`git commit --only` 本次创建/修改的 tracks 托管路径与 receipt/launcher；不扫入用户已 staged 的改动。非 git 空目录先 `git init -b main`；非 git 非空目录拒绝（`not_a_git_repo`）。

版本解析：默认版本 = 全局 `tracks.__version__`，默认来源 = **正在运行的 distribution**（复制其 `tracks` 包与 `.dist-info`，离线）。`--version V` 指定版本；当 `V` 与运行版本不同（或显式给了 `--wheel`）时，必须从 `--wheel <path>` 读取官方 wheel（stdlib `zipfile` 解包，`METADATA` 版本必须等于 `V`）。不访问包索引、不做隐式网络下载；找不到 wheel 即 `version_unavailable` 并指引下载官方 release artifact。这样全局升级后的旧项目无需任何网络即可继续运行，显式装旧版本也只需要一个官方 wheel 文件。

### 1.0.3 全局 CLI 委派与版本可观察

全局 `trac` 是安装与项目级服务入口，不是全局服务器。命令分派规则：

1. `trac --help`/无参数：全局 USAGE，exit 0（零仓库依赖，继承 AC-FR0349-01）。
2. `trac install ...`：全局安装器；不委派。
3. `trac version`：全局读当前项目 receipt（若在已安装项目内）或全局 `tracks.__version__`，输出 `scope=global|project`。
4. `trac serve ...`：由 `cmd_serve` 的外层逻辑处理（§1.0.4）；项目外拒绝，项目内前台委派。
5. 其余命令：若 cwd 位于已安装项目（向上查找最近的 `.tracks/install.json`），把 cwd 切到项目根并以 `TRAC_PROJECT_ACTIVE=1` 执行 `.tracks/bin/trac.py`；若未找到 receipt 但最近的 `.tracks/bin/trac.py` 或 `.tracks/env/` 存在，视为损坏项目并拒绝；否则按全局版本执行（既有行为保留）。项目 launcher 运行的是 receipt 指向的 site-packages，因此 `run/status/triage/...` 全部使用项目锁定版本。receipt 损坏或 site-packages 缺失时返回 `install_corrupt` 并指引重装，不退化为全局版本。

`trac version --json` 的项目形态输出 `{"scope":"project","version":"0.11.0","project_root":"...","site_packages":"..."}`；全局形态输出 `{"scope":"global","version":"0.12.0"}`。项目服务连接后 `/healthz` 的 `version` 字段来自该项目 `tracks.__version__`，是“连接后版本归属”的第二出口（AC-FR0350-01/AC-FR0353-01）。

### 1.0.4 项目内服务器生命周期

前台：`trac serve --repo <path> [--host] [--port] ...` 保持 v0.9 语义——阻塞运行、SIGINT/SIGTERM 优雅停止、exit 0；直接 import `serve_cmd.cmd_serve` 的测试面不变。外层 CLI 在 `TRAC_CLI_ENTRY=1`（由 `main` 设置）且非 `TRAC_PROJECT_ACTIVE` 时，先要求 `--repo` 指向已安装项目（无 receipt → `not_installed`），再委派项目 launcher 执行前台 serve。

后台（macOS/Linux）：`trac serve --repo <path> --background` 在已完成安装前置检查后，`subprocess.Popen(..., start_new_session=True)` 启动项目 launcher 的前台 serve，stdout/stderr 写 `serve.log`；父进程轮询 `/healthz`（有界，`--wait-initial-s`）与子进程存活，成功后写 pidfile 并打印 `serving on http://host:port (pid N, version V, background)`。子进程检测到 `TRAC_PROJECT_ACTIVE=1`，直接运行前台 serve，不再递归。Windows 下 `--background` 返回 `unsupported_platform`，只提供前台。

停止/状态：`--stop` 读 pidfile，校验 `pid` 存活且命令行属于该项目的 launcher（防 PID 复用），SIGTERM 后等待退出（超时再 SIGKILL），删除 pidfile；未运行时幂等返回 `not running`。`--status` 读 pidfile + `/healthz`，输出 `running pid=N version=V url=...` 或 `stopped`；陈旧 pidfile 清理并报 `stopped (stale pidfile)`。`--stop/--status` 不要求密码、不启动服务。

### 1.0.5 覆盖安装、文件权属与幂等

安装器维护一个**精确的托管清单**（`managed_files`）：agents、skills、templates 的逐文件路径、launcher、receipt 自身。托管文件无条件覆盖；未列入清单的路径一律不读不写。用户项目文件（代码、文档、未跟踪改动、既有 `.gitignore`、既有根 `opencode.json`、用户自有 `.opencode/opencode.json`）不被安装修改；测试以逐字节 sha 对比证明。

安装事务顺序：(1) 解析目标与版本；(2) 在 `.tracks/.install-staging-<ulid>/` 准备新的 site-packages 与托管文件树；(3) 校验 wheel/资源/版本/依赖；(4) 把新 site-packages 换入 `.tracks/env/<version>/`，把 agents/skills/templates 三个目录以“旧目录备份→新目录换入→删备份”的方式替换；(5) 原子写 receipt；(6) 用项目 launcher 跑 `--help`（或 `version`）做 post-install health check；(7) 路径限定的 git commit。任一步失败：删除 staging、恢复被换出的目录/旧 receipt 与 launcher（首次安装则移除），返回非零与封闭 reason，不留下半成品 receipt。重复执行同一版本：staging 清理、目录替换、receipt 重写、commit 无 diff 时成功返回，状态收敛。

### 1.0.6 安装失败语义

失败统一写 stderr：`trac install: <reason>: <detail>`，stdout 不输出成功行，exit 非零。reason 封闭集：`usage`、`target_not_directory`、`target_parent_missing`、`not_a_git_repo`、`version_invalid`、`version_unavailable`、`version_mismatch`、`package_incomplete`、`dependency_missing`、`install_failed`、`git_commit_failed`、`health_check_failed`。成功只在 post-install health check 与 receipt 写入都通过后输出 `installed tracks <version> at <path>`。运行 `trac install` 期间不删除用户文件；失败重试是唯一恢复路径。

### 1.0.7 OOB 继承面（不重做设计）

- 阶段路径：`timeline.js` 的 `stage-track-carousel` 承载 13 阶段横向状态轨道（`timeline-stage-order`），节点 `data-state ∈ {done, active, pending, attention, void}`；当前阶段在门控时为 `attention`。节点详情（`detail-pane`/`node-detail`）显示该阶段的 `start/end`（浏览器本地时间）、状态、owner、artifact/evidence 与降级文本。OOB 系列用该轨道替代了早期的静态 13 阶段 mermaid；本版把它作为 FR-0355 的“状态图”交付面，不恢复已移除的静态图。
- 阶段实际工作流：`stage-path` 面板的 `stage-workflow-summary` + `stage-workflow-diagram` 以 mermaid `stateDiagram-v2` 呈现所选阶段窗口（最后一次 `stage.entered` → 下一 `stage.exited`）的实际派发/人工动作/结论；重复派发折叠为 `重派 ×K`/边标签 `×N`/`第N轮`，末端为人工门或完成；渲染失败降级为可读文本。数据源是既有 timeline/run detail 事件流，无第二来源。
- 按轮：`GET /api/runs/{run_id}/stages/{stage}/rounds` 与 `GET /api/runs/{run_id}/rounds/{command_id}` 由 `run_rounds.py` 提供；输入/输出来自内容寻址 blobs，思考来自 `.opencode/logs` 的增量缓存，日志轮转时 `thinking_available=false` 并给出可读说明；工具计数来自 outcome 输出与实时行。UI 的 `stage-rounds` 首次展开才请求，单轮首次展开才取详情并按 command_id 缓存。
- 运行卡片与时间：`current-node-card` 的 `card-owner/card-attempt/card-duration/card-action` 使用中文标签、人类单位耗时；门控等待以最后一个非心跳业务事件为起点（`lease.*`/`command.accepted` 不重置）；节点起止时间用 `fmtTs` 的浏览器本地 `YYYY-MM-DD HH:mm:ss`。
- 评审两决策：`gate-panel` 的 review 面只呈现 `review-approve`（通过，空意见即可）与 `review-return`（退回，意见必填）；异常门（escalation/rollback/hotfix_triage/review_pending_threads）保留 `gate-reason/gate-impact`，正常流门不堆模板文案；失败在 `review-feedback` 内联显示，`data-failed=true`，不伪报成功。
- 静态文本：`_AppModulesNoCache` 对 `/static/app/*` 与 `/static/` 下 `.css/.js/.mjs/.html/.json/.txt/.map` 追加 `Cache-Control: no-cache`；ETag 保留，未变更文件仍回 304；API 路由不受影响。

### 1.0.8 评审退回的线程写入修订（FR-0359）

`record_review` 的封闭行为：`action=no_comment` 要求 `comments` 缺省/空，落 `human.review`（不写文档）；`action=comment` 要求非空 `comments`，worker 在 writer_lock 内先以该 review 命令的 actor（与 `human.review.payload.actor` 同一值）向当前阶段文档写一个根线程（锚定文档首个非空行，正文即 `comments`），再落 `human.review`，payload 追加 `comments`、`doc`、`thread_id`。实现上复用 `discuss.writer.start` 的纯文本变换，并在已持有的 writer_lock 内做 tmp+replace；不得再调用 discuss CLI 的 flock 写路径（避免同一进程双重加锁）。线程写失败 → 命令 `command.failed(reason=review_thread_write_failed)`，不落 `human.review`、不推进门；UI 内联呈现失败。这样既满足“意见先记为文档线程”，又保留 `c6dc3f8` 的“意见直达 Scribe”链（RESPOND 仍从 `human.review.comments` 注入）。重复提交由命令幂等键 + awaiting 前置去重；崩溃重放可能产生重复线程的风险记入 §5.2。

## 1.1 Composition Root

安装/委派/服务装配的真实接线：

```text
全局 trac（tracks/cli/main.py:main）
  -> USAGE / --help：零仓库依赖，exit 0
  -> install：install_cmd.cmd_install
       -> install.install_project(target, version, wheel)
            -> project_runtime.resolve_target / resolve_version
            -> stage: .tracks/.install-staging-<ulid>/
            -> source: running distribution（tracks.__file__ + dist-info）
                       or zipfile(wheel) -> tracks/ + *.dist-info/
            -> install.materialize_managed(...) -> .opencode/{agents,skills,templates}
            -> install.write_receipt(...) -> .tracks/install.json + .tracks/bin/trac.py
            -> health check: [receipt.python, .tracks/bin/trac.py, "--help"]
            -> git add/commit --only <managed paths>
  -> version：project_runtime.read_receipt -> scope/version/JSON
  -> 其它命令（非 serve，TRAC_PROJECT_ACTIVE 未设置）：
       project_runtime.find_project(cwd)
         -> 有 receipt：subprocess.run([receipt.python, .tracks/bin/trac.py, *argv],
                          cwd=project_root,
                          env={**os.environ, "TRAC_PROJECT_ACTIVE": "1"})
         -> 无 receipt：全局版本既有行为
  -> serve：serve_cmd.cmd_serve（外层）
       -> TRAC_PROJECT_ACTIVE=1：前台 uvicorn（既有核心）
       -> TRAC_CLI_ENTRY=1：
            --status/--stop -> project_runtime.pidfile_status/stop
            --background     -> Popen(project launcher serve, start_new_session)
            foreground       -> 要求 receipt -> 委派项目 launcher 前台 serve
            no receipt       -> not_installed（不落全局版本兜底）

项目 launcher（<root>/.tracks/bin/trac.py）
  -> read .tracks/install.json
  -> sys.path[0] = <root>/<receipt.site_packages>
  -> cwd = <root>; env TRAC_PROJECT_ACTIVE=1
  -> tracks.cli.main.main(argv)

Web 面（继承）
  -> create_app -> _AuthBoundary / _AppModulesNoCache / _StaticGzip
  -> /api/runs/{run_id}/stages/{stage}/rounds -> run_rounds.stage_rounds
  -> /api/runs/{run_id}/rounds/{command_id}   -> run_rounds.round_detail_view
  -> /api/runs/{run_id}/activity              -> run_activity（继承遗留；不属 v0.12 断言基线）
  -> /healthz                                 -> {status, projects, version, uptime_s}
```

## 1.2 Required AC closure (ISLAND_GATE_1)

- **FR-0348** owner=tracks/install.py+tracks/assets/opencode.json+pyproject.toml:AC-FR0348-01 surface=官方 wheel artifact（[host-contract.build] dist/*.whl）+ trac install <path> composition=package-data 声明 agents/skills/templates/assets/opencode.json；install_project 从被安装版本复制完整 resource 集 wiring=构建 wheel→zip 名单校验→全局 pip 安装→trac install <path>→目标 .opencode 下物化 agents/skills/templates、根 opencode.json seeded→post-install health check test=integration:tests/integration/test_install_package.py::test_wheel_carries_capability_resources + e2e:tests/e2e/test_install_journey.py::test_install_from_official_wheel evidence=`.venv/bin/python -m pytest -q tests/integration/test_install_package.py` 输出 passed 且 wheel zip 含 tracks/agents/*.md、tracks/skills/*/SKILL.md、tracks/templates/**、tracks/assets/opencode.json IF-INSTALLPKG-001 IF-INSTALL-001
- **FR-0348** owner=tracks/install.py:AC-FR0348-02 surface=trac install <path> --wheel <bad.whl> 的失败出口 composition=package completeness validator（三资源缺一即 package_incomplete） wiring=读取 wheel 名单→校验 agents/skills/templates/assets/opencode.json→缺项返回非零且不写 receipt/目标不进入可用版本候选 test=integration:tests/integration/test_install_package.py::test_incomplete_wheel_rejected evidence=`.venv/bin/python -m pytest -q tests/integration/test_install_package.py` 输出 passed 且 stderr 含 trac install: package_incomplete、目标目录无 .tracks/install.json IF-INSTALLPKG-001 IF-INSTALL-001
- **FR-0349** owner=tracks/cli/main.py+tracks/cli/install_cmd.py:AC-FR0349-01 surface=全局 shell trac --help composition=wheel console_script trac -> tracks.cli.main:main；main 在无项目上下文时零依赖打印 USAGE 并 exit 0 wiring=隔离 prefix pip install wheel→任意非项目目录执行 trac --help→exit 0 且 usage 含 install/serve/version test=integration:tests/integration/test_install_package.py::test_global_trac_help_zero_repo + e2e:tests/e2e/test_install_journey.py::test_global_trac_available_anywhere evidence=`.venv/bin/python -m pytest -q tests/integration/test_install_package.py` 输出 passed 且 stdout 含 usage: trac、cwd 不在仓库 IF-INSTALLPKG-001 IF-GLOBALCLI-001
- **FR-0349** owner=tracks/cli/serve_cmd.py+tracks/project_runtime.py:AC-FR0349-02 surface=trac serve 在未安装目录的拒绝 composition=cmd_serve 外层安装前置（TRAC_CLI_ENTRY 模式）；无 global server 兜底 wiring=在非安装目录执行 trac serve --repo <dir>→stderr not_installed 指引 trac install→非零退出→无监听端口 test=integration:tests/integration/test_server_lifecycle.py::test_serve_outside_project_refused evidence=`.venv/bin/python -m pytest -q tests/integration/test_server_lifecycle.py` 输出 passed 且 stderr 含 not_installed、连接该端口失败 IF-GLOBALCLI-001 IF-SERVERLIFE-001
- **FR-0350** owner=tracks/cli/serve_cmd.py+tracks/project_runtime.py:AC-FR0350-01 surface=项目内 trac serve --background + /healthz version + trac serve --status composition=cmd_serve 生命周期的 POSIX start_new_session/pidfile/log→项目 launcher→项目版本 CLI→uvicorn；healthz 报项目版本 wiring=已安装项目执行 trac serve --repo <root> --background→/healthz 返回 version=<锁定版本>→发起 shell 退出后仍可连接→trac serve --status 显示 running/version；Windows --background 返回 unsupported_platform 并仅以前台运行 test=integration:tests/integration/test_server_lifecycle.py::test_background_survives_shell_and_reports_locked_version + tests/integration/test_server_lifecycle.py::test_windows_foreground_branch evidence=`.venv/bin/python -m pytest -q tests/integration/test_server_lifecycle.py` 输出 passed 且 healthz version 等于 receipt.version、shell 退出后连接仍成功 IF-SERVERLIFE-001 IF-PROJECTCLI-001 IF-VERSIONVIS-001
- **FR-0350** owner=tracks/cli/serve_cmd.py+tracks/project_runtime.py:AC-FR0350-02 surface=未安装目录 serve 拒绝 composition=cmd_serve 外层安装前置 fail-closed（无 receipt 不启动 uvicorn） wiring=非安装目录执行 trac serve→stderr not_installed + 指引→exit 非零→端口不监听；不在全局版本兜底 test=integration:tests/integration/test_server_lifecycle.py::test_serve_outside_project_refused evidence=`.venv/bin/python -m pytest -q tests/integration/test_server_lifecycle.py` 输出 passed 且无 service.started 事件、端口不可连接 IF-SERVERLIFE-001 IF-GLOBALCLI-001
- **FR-0351** owner=tracks/install.py+tracks/project_runtime.py:AC-FR0351-01 surface=trac install <path> --version <v> --wheel <whl> + trac version composition=install.resolve_version（默认=全局运行版本；显式版本必须与 wheel METADATA 一致）；receipt 记录 version；项目 launcher 固定该 site-packages wiring=准备两版本 wheel→install 目标到旧版本→全局 trac version 报新→目标内 trac version 报旧→目标 serve /healthz 报旧 test=integration:tests/integration/test_install_versions.py::test_explicit_version_and_global_project_split + e2e:tests/e2e/test_install_journey.py::test_install_old_version_while_global_new evidence=`.venv/bin/python -m pytest -q tests/integration/test_install_versions.py` 输出 passed 且 receipt.version=旧、全局 version=新、healthz=旧 IF-INSTALL-001 IF-PROJECTCLI-001 IF-VERSIONVIS-001
- **FR-0351** owner=tracks/install.py:AC-FR0351-02 surface=空目录与已有项目安装 composition=install.target_guard（空目录 git init -b main；已有 git 项目直接加装；非 git 非空拒绝） wiring=空目录执行 trac install→scaffold+managed files+receipt→trac status 可用；已有项目（含用户文件）执行同一命令→加装且用户文件不变 test=integration:tests/integration/test_install_targets.py::test_empty_dir_and_existing_project evidence=`.venv/bin/python -m pytest -q tests/integration/test_install_targets.py` 输出 passed 且两目标都含 receipt/launcher、workspace 可用 IF-INSTALL-001 IF-PROJLAYOUT-001
- **FR-0352** owner=tracks/install.py:AC-FR0352-01 surface=重复 install 的覆盖/保留出口 composition=managed file manifest（agents/skills/templates/launcher/receipt）+ user file preservation（install 只写声明路径；不创建/不修改 .gitignore，不修改既有 opencode.json） wiring=安装 vA→对用户代码/文档/未跟踪文件取 sha→重装 vB→tracks 自有文件为 vB 内容→用户文件 sha 不变、无新增冲突/删除 test=integration:tests/integration/test_install_overwrite.py::test_managed_files_replaced_user_files_byte_identical evidence=`.venv/bin/python -m pytest -q tests/integration/test_install_overwrite.py` 输出 passed 且用户文件 sha 前后逐项相等、托管文件字节来自 vB IF-INSTALL-001 IF-PROJLAYOUT-001
- **FR-0352** owner=tracks/install.py:AC-FR0352-02 surface=用户改动 tracks 自有文件后重装 composition=managed files 无条件覆盖；receipt.managed_files 记录所有权 wiring=改 .opencode/skills/tracks-discuz/SKILL.md→重装→文件恢复为所装版本字节→无恢复流程/无“数据丢失”错误 test=integration:tests/integration/test_install_overwrite.py::test_user_modified_managed_file_overwritten evidence=`.venv/bin/python -m pytest -q tests/integration/test_install_overwrite.py` 输出 passed 且修改后的文件在重装后等于 wheel 内字节 IF-INSTALL-001 IF-PROJLAYOUT-001
- **FR-0353** owner=tracks/project_runtime.py+tracks/cli/install_cmd.py:AC-FR0353-01 surface=全局升级后旧项目工作 + trac version / healthz composition=项目 receipt+site-packages 固定；全局 CLI 只按 receipt 委派，不读全局 tracks 版本 wiring=安装旧版本项目→升级全局 prefix 到新版本→项目内 trac version 报旧→trac status 正常→serve /healthz 报旧；全局 trac version 报新 test=integration:tests/integration/test_install_versions.py::test_global_upgrade_keeps_locked_project + e2e:tests/e2e/test_install_journey.py::test_upgrade_global_project_stays_old evidence=`.venv/bin/python -m pytest -q tests/integration/test_install_versions.py` 输出 passed 且项目 version/healthz 保持旧、全局 version 为新 IF-PROJECTCLI-001 IF-VERSIONVIS-001 IF-PROJLAYOUT-001
- **FR-0353** owner=tracks/install.py+tracks/project_runtime.py:AC-FR0353-02 surface=升级前后 receipt/锁定不变 composition=install 是唯一写 receipt/版本目录的路径；全局升级不触碰项目 wiring=全局 prefix 换版本→项目 .tracks/install.json sha/version 不变→无自动迁移写入/无版本锁定变更→项目命令返回可用结果 test=integration:tests/integration/test_install_versions.py::test_no_auto_upgrade_or_lock_change evidence=`.venv/bin/python -m pytest -q tests/integration/test_install_versions.py` 输出 passed 且 receipt sha 前后相等、无新增 material.edited/migration 事件 IF-PROJECTCLI-001 IF-PROJLAYOUT-001
- **FR-0354** owner=tracks/install.py+tracks/cli/install_cmd.py:AC-FR0354-01 surface=install 成功/失败结论与重试 composition=staging→校验→目录替换→receipt→post-install health check；失败 reason 封闭集 wiring=注入缺资源/版本不匹配/非 git 非空/health check 失败→exit 非零+stderr reason→目标无半成品 receipt→修复后重试成功→同版本重复安装幂等 test=integration:tests/integration/test_install_versions.py::test_failure_semantics_and_retry + tests/integration/test_install_overwrite.py::test_reinstall_idempotent evidence=`.venv/bin/python -m pytest -q tests/integration/test_install_versions.py tests/integration/test_install_overwrite.py` 输出 passed 且失败样本 stderr reason 在封闭集内、重试后成功 IF-INSTALL-001 IF-INSTALLPKG-001 IF-PROJLAYOUT-001
- **FR-0355** owner=tracks/server/static/app/timeline.js:AC-FR0355-01 surface=E-04 run detail 阶段路径（stage-track-carousel + timeline-stage-order）+ detail-pane composition=timeline.js stageTrack/foldStageFacts；数据源 timeline/stage_order/run detail；选中节点详情显示 start/end；current card 显示 gate wait wiring=打开 run 详情→13 阶段轨道以 data-state 区分 done/active/pending/attention/void→当前阶段与门控等待可读→选中阶段→detail-pane 显示 start/end→可进入 FR-0356 的工作流面板 test=e2e:tests/e2e/test_run_workflow_ui.py::test_stage_path_states_times_and_gate_wait evidence=`.venv/bin/python -m pytest -q -m ui tests/e2e/test_run_workflow_ui.py` 输出 passed 且 data-state 在五态内、detail-pane 含 start/end、card-action 含等待时长 IF-STAGEPATH-001 IF-WEBUI-001
- **FR-0355** owner=tracks/server/static/app/timeline.js:AC-FR0355-02 surface=数据缺失/渲染失败降级 composition=stageTrack 对无事件 run 仍渲染 13 节点；nodeDetail 输出 no recorded events 可读行；timeline 404 输出 Unknown run（不空白） wiring=seed 无 stage 事件的 run→打开详情→13 阶段标签仍在、无伪造时间→选中节点→可读降级文本；请求失败→Unknown run test=e2e:tests/e2e/test_run_workflow_ui.py::test_stage_path_degradation_readable evidence=`.venv/bin/python -m pytest -q -m ui tests/e2e/test_run_workflow_ui.py` 输出 passed 且降级文本可见、无伪造 timestamp IF-STAGEPATH-001 IF-WEBUI-001
- **FR-0356** owner=tracks/server/static/app/timeline.js:AC-FR0356-01 surface=stage-path 面板（stage-workflow-summary/stage-workflow-diagram）+ 折叠/聚合 composition=timeline.js stageWorkflowSource/foldSubstateGraph；窗口=最后 stage.entered→下一 stage.exited；重复派发/超长折叠；末端人工门或完成 wiring=seed 含重复 dispatch/outcome/verdict/human 的 stage→打开 run→选中阶段→面板呈现聚合图（SVG/text）→摘要含派发/交互轮次/人工动作→重复边显示 ×N/重派→末端显示人工门 test=e2e:tests/e2e/test_run_workflow_ui.py::test_stage_workflow_aggregated_folds evidence=`.venv/bin/python -m pytest -q -m ui tests/e2e/test_run_workflow_ui.py` 输出 passed 且 stage-workflow-diagram 有 SVG/text、摘要含派发/轮次/人工动作 IF-STAGEWORKFLOW-001 IF-WEBUI-001
- **FR-0357** owner=tracks/server/run_rounds.py+tracks/server/static/app/timeline.js:AC-FR0357-01 surface=GET /api/runs/{run_id}/stages/{stage}/rounds + GET /api/runs/{run_id}/rounds/{command_id} + stage-rounds accordion composition=run_rounds.rounds_for_stage/round_detail；blob 输入输出、thinking tail、tool counts；UI 首次展开才 fetch wiring=打开 run→展开执行明细→rounds 列表→展开某轮→输入/输出/思考/工具四区→大轮数懒加载可导航 test=integration:tests/integration/test_run_rounds.py::test_rounds_list_and_detail + e2e:tests/e2e/test_run_workflow_ui.py::test_per_round_detail_lazy_accordion evidence=`.venv/bin/python -m pytest -q tests/integration/test_run_rounds.py` 输出 passed 且 rounds 数与 dispatch 数一致、detail 四区字段齐备 IF-ROUNDS-001 IF-QUERY-001 IF-WEBUI-001
- **FR-0357** owner=tracks/server/run_rounds.py+tracks/server/static/app/timeline.js:AC-FR0357-02 surface=thinking_available=false + 可读说明 composition=run_rounds._detail_thinking 会话缺失返回 (None,false)；UI 输出“该轮的会话日志已轮转清理，仅存输入/输出” wiring=seed 无日志/已轮转 round→展开→四区仍显示输入输出工具→思考区呈现可读说明、不伪造 test=integration:tests/integration/test_run_rounds.py::test_missing_thinking_degrades + e2e:tests/e2e/test_run_workflow_ui.py::test_round_thinking_missing_message evidence=`.venv/bin/python -m pytest -q tests/integration/test_run_rounds.py` 输出 passed 且 thinking_available=false、thinking=null、输入输出仍可读 IF-ROUNDS-001 IF-WEBUI-001
- **FR-0358** owner=tracks/server/static/app/timeline.js:AC-FR0358-01 surface=current-node-card + detail-pane local time composition=timeline.js currentCard/actionOf/lastBusinessEvent/fmtTs；durationOf 人类单位；心跳事件集不重置等待 wiring=打开 run→card-owner/attempt/duration/action 中文可读→门控等待时 card-action 显示“已等 X”→插入 heartbeat 后等待时长继续累计（不重置）→节点 start/end 用浏览器本地时间 test=e2e:tests/e2e/test_run_workflow_ui.py::test_run_card_local_time_and_heartbeat_not_reset evidence=`.venv/bin/python -m pytest -q -m ui tests/e2e/test_run_workflow_ui.py` 输出 passed 且 card 四个 testid 可见、等待文本不等于 heartbeat 后重新计时 IF-RUNCARD-001 IF-WEBUI-001
- **FR-0359** owner=tracks/supervisor/gate_exec.py+tracks/cli/run_cmd.py+tracks/server/static/app/timeline.js:AC-FR0359-01 surface=review gate panel（review-approve/review-return/review-comments）+ POST /api/runs/{run_id}/review composition=reviewDecisionFace；record_review action=comment 在 worker 内先写文档根线程再落 human.review（payload comments/doc/thread_id）；no_comment 直接落事件 wiring=到达 review 门→面板仅两项决策→空意见通过→review-return 必填意见→提交→文档出现该意见线程→human.review 事件落盘→门控继续 test=integration:tests/integration/test_review_decision_contract.py::test_return_writes_thread_then_review_event + e2e:tests/e2e/test_run_workflow_ui.py::test_review_two_decisions_thread_and_continue evidence=`.venv/bin/python -m pytest -q tests/integration/test_review_decision_contract.py` 输出 passed 且文档解析出新根线程、human.review payload 含 comments/doc/thread_id IF-REVIEWDECISION-001 IF-GATEUI-001 IF-DOCSWRITE-001
- **FR-0359** owner=tracks/server/static/app/timeline.js:AC-FR0359-02 surface=gate panel reason/impact + inline feedback composition=GATE_EXCEPTION_TYPES 仅异常门渲染 reason/impact；review-feedback data-failed wiring=正常 triage/review 门→无 gate-reason/gate-impact→异常 escalation/rollback 门→两者呈现→review 写失败注入→review-feedback 可见且 data-failed=true，不伪报成功 test=e2e:tests/e2e/test_run_workflow_ui.py::test_normal_gate_terse_exception_keeps_reason_impact_and_inline_failure evidence=`.venv/bin/python -m pytest -q -m ui tests/e2e/test_run_workflow_ui.py` 输出 passed 且正常门无 reason/impact、异常门有、失败反馈 data-failed=true IF-REVIEWDECISION-001 IF-AWAIT-001 IF-WEBUI-001
- **FR-0360** owner=tracks/server/app.py:AC-FR0360-01 surface=GET /static/app/*.js|*.css + /static 文本资源 composition=_AppModulesNoCache（no-cache + ETag 304）；_StaticGzip 不影响 API wiring=请求静态 JS/CSS→cache-control: no-cache→带 If-None-Match 重验→未变更 304→替换静态文件内容→普通刷新返回新内容；API 路径无 cache-control 变更 test=integration:tests/integration/test_static_cache.py::test_text_assets_no_cache_and_304 + tests/integration/test_static_cache.py::test_api_semantics_unchanged evidence=`.venv/bin/python -m pytest -q tests/integration/test_static_cache.py` 输出 passed 且 200/304 头正确、API 无 no-cache IF-STATICCACHE-001 IF-QUERY-001
- **NFR-0159** owner=tracks/cli/serve_cmd.py+tracks/project_runtime.py:AC-NFR0159-01 surface=POSIX background lifecycle + Windows foreground honest composition=cmd_serve --background spawn 项目 CLI start_new_session + pidfile/log；--stop SIGTERM→不可连接；Windows --background 拒绝、前台随进程退出 wiring=shell 内启动 background→shell 退出→healthz 仍通→stop→连接失败；Windows 分支（平台注入）→background 拒绝、前台进程结束即不可连接 test=integration:tests/integration/test_server_lifecycle.py::test_background_survives_shell_exit_and_stop_unreachable + tests/integration/test_server_lifecycle.py::test_windows_foreground_branch evidence=`.venv/bin/python -m pytest -q tests/integration/test_server_lifecycle.py` 输出 passed 且 shell 退出后 healthz 成功、stop 后连接拒绝 IF-SERVERLIFE-001 IF-PROJECTCLI-001
- **NFR-0160** owner=tracks/server/static/app/timeline.js+tracks/server/app.py:AC-NFR0160-01 surface=rounds 懒加载 + static 304 composition=UI 首次展开才请求 rounds；run_rounds 尾配额；_AppModulesNoCache 304 wiring=大轮数 run 首屏不请求 rounds→展开才 fetch→导航可读；静态未变更 304、变更后新内容；API 语义不变 test=e2e:tests/e2e/test_run_workflow_ui.py::test_rounds_lazy_first_paint + integration:tests/integration/test_static_cache.py::test_text_assets_no_cache_and_304 evidence=`.venv/bin/python -m pytest -q -m ui tests/e2e/test_run_workflow_ui.py` 输出 passed 且首屏无 rounds 请求、展开后请求成功 IF-ROUNDS-001 IF-STATICCACHE-001

## 2. Scaffold 宣言

本节是 M-DESIGN 阶段在宿主项目创建/修改文件的唯一合同。以下文件随本设计物理交付，全部为声明/配置/数据，无业务行为；本节以外不创建 scaffold。`tests/ground_truth/` 不适用（test-plan §3）。

- `tracks/assets/opencode.json` — 官方安装包内默认 harness 配置：7 个 tracks agent 的 per-agent `external_directory` 权限（`*: deny` + `{env:TMPDIR}tracks/*: allow`），不含模型/provider（kind: data）
- `pyproject.toml` — version `0.12.0`；package-data 追加 `assets/opencode.json`（kind: config；bytes 变化使六条 pyproject-backed `config_digest` 重锚，§4.2）
- `tracks/__init__.py` — `__version__ = "0.12.0"`（kind: config；version gate 绑定对象）
- `tracks/executor/validate.py` — `TRAC_SUBCOMMANDS` 登记 `install`/`version`（kind: config；设计文档引用新 CLI 时不被判 fabricated tooling）
- `tracks/cli/main.py` — `USAGE` 登记 `install`/`version` 与 `serve --background|--stop|--status`（kind: config；与 `TRAC_SUBCOMMANDS` 的 parity pin 同步）

**无接口桩**：v0.12 的 Shield 契约测试全部经真实 CLI 子进程、HTTP 面、文件面与 Playwright 进入被测系统，不直接 import 新模块。`tracks/install.py`、`tracks/project_runtime.py`、`tracks/cli/install_cmd.py` 缺席时测试以合法 Red（`trac install` 返回 usage/非零、serve 拒绝）呈现，而非收集失败；`tests/ground_truth/` 不创建。安装器/委派/生命周期/review 线程写入的行为体、以及 `tracks/cli/main.py` 的委派钩子、`tracks/cli/serve_cmd.py` 的生命周期行为，均为**待实现 Devon 任务**；OOB 读取面已交付，只需 Shield 补测试。

## 3. 技术选型

### 3.1 零新运行时依赖

安装器只用标准库：`zipfile`（读取官方 wheel）、`shutil`/`os.replace`（staging/目录替换）、`json`（receipt）、`subprocess`（health check 与 git）、`importlib.metadata`（运行中 distribution 定位）、`hashlib`（文件身份）。不引入 `packaging`、`pip`、`uv`、`virtualenv`、`toml` 新依赖。后台服务复用既有 `uvicorn`/`subprocess`/`signal`；OOB 读取面复用既有 blob/log 缓存。

### 3.2 项目 site-packages 隔离 + 共享全局解释器运行时（关键取舍）

项目只隔离 `tracks` distribution 本身：`site-packages` 由 `.tracks/env/<version>/` 承载，launcher 用 receipt 记录的全局解释器加载它，因此项目 CLI/HTTP/事件行为严格属于所装版本；starlette/uvicorn/certifi 由该解释器环境提供。备选“每项目 venv + pip install”被否：需要包索引/网络、venv 绝对路径使 staging 原子替换复杂、且旧项目在全局升级后可能重复下载依赖。代价：全局解释器的依赖集合是项目运行时的前置（安装时校验 `Requires-Dist` 存在；`dependency_missing` 失败）；若全局依赖被不兼容升级，旧项目可能受影响（§5.2）。v0.12 的依赖集合固定且 wheel metadata 记录，风险有界。

### 3.3 后台服务用 detached 子进程（而非 launchd/systemd）

spec 只要求 macOS/Linux 下“发起 shell 退出后继续运行直至显式停止”，不要求开机自启、日志轮转或服务管理器集成。`start_new_session=True` + pidfile + log 满足该可观察性；停止由 `--stop` 显式发起。备选 launchd/systemd 需要平台专有安装/权限/卸载语义，超出本版。Windows 明确不提供后台，前台生命周期随进程，诚实反映（NFR-0159）。

### 3.4 安装器离线优先（复制运行中 distribution 或解包官方 wheel）

默认安装直接复制当前运行 distribution 的 `tracks/` 与 `.dist-info/`；显式不同版本用 `--wheel` 解包。这样 `trac install` 不依赖网络、不依赖 pip 版本、不会把包索引故障变成安装失败；官方 release artifact 本身就是 wheel。代价：跨版本安装需要用户先取得对应 wheel 文件（`version_unavailable` 会明确指引），不在安装器内实现下载器。

### 3.5 不引入的机制

不引入包索引客户端/依赖解析器、项目级 venv、launchd/systemd/Windows 服务、自动升级/迁移器、全局服务器、后台 SSE 扩容、新的 tracks.db 事件类型、新的前端构建链。OOB 展示数值（轮播/折叠阈值/排版）不重议。

## 4. 交付与运行合同（machine contracts）

### 4.1 测试执行合同（`.tracks/projects/project.toml`）

v0.12 逐字继承现行测试执行合同（不修改文件）：

- `[unit]`：framework=pytest；paths=`["tests/unit/"]`；collect=`.venv/bin/python -m pytest --collect-only tests/unit/`；run=`.venv/bin/python -m pytest tests/unit/ --tb=short -q -n 8 --dist loadscope --junitxml={result}`；run_selected=`.venv/bin/python -m pytest {nodes} --tb=short -q -n 8 --dist loadscope --junitxml={result}`；cwd=`.`
- `[integration]`：framework=pytest；paths=`["tests/integration/"]`；collect=`.venv/bin/python -m pytest --collect-only tests/integration/`；run=`.venv/bin/python -m pytest tests/integration/ --tb=short -q -n 8 --dist loadscope --junitxml={result}`；run_selected=`.venv/bin/python -m pytest {nodes} --tb=short -q -n 8 --dist loadscope --junitxml={result}`；cwd=`.`
- `[e2e]`：framework=pytest；paths=`["tests/e2e/"]`；collect=`.venv/bin/python -m pytest --collect-only tests/e2e/ -m 'not performance'`；run=`.venv/bin/python -m pytest tests/e2e/ --tb=short -q -n 8 --dist loadscope --junitxml={result}`；run_selected=`.venv/bin/python -m pytest {nodes} --tb=short -q -n 8 --dist loadscope -m 'not performance' --junitxml={result}`；cwd=`.`

`ui` marker 默认由 addopts 排除；e2e `collect` / `run_selected` 的 `-m 'not performance'` 覆盖该过滤以收集/选中 UI 节点。`ui-e2e` job 选择语义、nightly FULL 合同、adapter `reference-pytest`、layout 写域与 lint check 全部继承。本版新测试全部落既有 paths，合同无需扩容。

### 4.2 Canonical quality guard registry（v0.12）

以下 TOML block 是 tracks 宿主 guard registry 的 v0.12 canonical 真相。输入变更两处：`pyproject.toml`（version 0.12.0 + package-data）——六条 pyproject-backed `config_digest` 重锚为 `sha256:41faecd6…`；`.tracks/projects/project.toml` 因 OOB 修复 e2e `collect` 的 marker 过滤（默认 addopts 排除 `ui` 节点，基线 §8 的 UI 绑定无法入册，phase0 报 node_missing）bytes 变更，`hooks-runner-ci-required` 的 `config_digest` 重锚为 `sha256:e534d31d…`（原 `e1735466…`）。`.flake8` digest（`f899995c…`）随文件 bytes 不变而继承。字段语义、八类强制、fail_closed、禁 `--exit-zero` 全部继承。

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
config_digest = "sha256:41faecd62142fec0bfd1a1865ff4e15660df7890ccc26af51678aef44a0df605"
scope = ["tracks", "tests"]
threshold = "line-length=100; select=E,F,W,I,B,UP,SIM,C4; ignore=SIM108; violations=0"
timeout_seconds = 300
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "static-semantic"
category = "static_analysis"
tool = "ruff"
tool_version = "0.16.0"
command = ".venv/bin/ruff check tracks tests"
config_paths = ["pyproject.toml"]
config_sections = ["tool.ruff.lint"]
config_digest = "sha256:41faecd62142fec0bfd1a1865ff4e15660df7890ccc26af51678aef44a0df605"
scope = ["tracks", "tests"]
threshold = "F and B semantic rule families; violations=0"
timeout_seconds = 300
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "cognitive-complexity"
category = "cognitive_complexity"
tool = "flake8+flake8-cognitive-complexity"
tool_version = "7.3.0+0.1.0"
command = ".venv/bin/flake8 tracks"
config_paths = [".flake8"]
config_sections = ["flake8"]
config_digest = "sha256:f899995c15557184f3a2d082469fcd7f15ae8f22928823249e6479a611c46382"
scope = ["tracks"]
threshold = "CCR001 max-cognitive-complexity=15; tests exempt"
timeout_seconds = 300
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "file-length"
category = "file_length"
tool = "pylint"
tool_version = "4.0.6"
command = ".venv/bin/pylint --disable=all --enable=C0302 tracks tests"
config_paths = ["pyproject.toml"]
config_sections = ["tool.pylint.format"]
config_digest = "sha256:41faecd62142fec0bfd1a1865ff4e15660df7890ccc26af51678aef44a0df605"
scope = ["tracks", "tests"]
threshold = "C0302 max-module-lines=1200"
timeout_seconds = 600
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "method-length-locals"
category = "method_length_locals"
tool = "pylint"
tool_version = "4.0.6"
command = ".venv/bin/pylint --disable=all --enable=R0915,R0914 tracks"
config_paths = ["pyproject.toml"]
config_sections = ["tool.pylint.design"]
config_digest = "sha256:41faecd62142fec0bfd1a1865ff4e15660df7890ccc26af51678aef44a0df605"
scope = ["tracks"]
threshold = "R0915 max-statements=50; R0914 max-locals=15; tests exempt"
timeout_seconds = 600
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "duplication"
category = "duplication"
tool = "pylint"
tool_version = "4.0.6"
command = ".venv/bin/pylint --disable=all --enable=R0801 tracks"
config_paths = ["pyproject.toml"]
config_sections = ["tool.pylint.similarities"]
config_digest = "sha256:41faecd62142fec0bfd1a1865ff4e15660df7890ccc26af51678aef44a0df605"
scope = ["tracks", "tests"]
threshold = "R0801 min-similarity-lines=5 (product scope; test-file similarity is covered by review, not this release gate)"
timeout_seconds = 600
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "coverage-threshold"
category = "coverage_threshold"
tool = "coverage+pytest"
tool_version = "7.15.2+9.1.1"
command = ".venv/bin/coverage report --fail-under=89"
config_paths = ["pyproject.toml"]
config_sections = ["tool.coverage.run", "tool.coverage.report"]
config_digest = "sha256:41faecd62142fec0bfd1a1865ff4e15660df7890ccc26af51678aef44a0df605"
scope = ["tracks"]
threshold = "line coverage >=89; by=collected; source omit=pure-I/O backends (opencode subprocess/session/pty, fake backends, evidence I/O — pyproject.toml [tool.coverage] omit)"
timeout_seconds = 1800
failure_policy = "fail_closed"
execution_points = ["runtime", "ci"]
required_check = "coverage"

[[quality_guard]]
id = "hooks-runner-ci-required"
category = "hooks_runner_ci_required_checks"
tool = "git-hooks+github-actions"
tool_version = "git-env-fingerprinted+checkout@v4+setup-python@v5"
command = "sh .githooks/pre-commit"
config_paths = [".tracks/projects/project.toml"]
config_sections = ["unit", "integration", "e2e", "adapter", "host-contract"]
config_digest = "sha256:e534d31d9f4ab09153e1151b73b5a87ebab5a8b675ad5eaf714391b13082d50c"
scope = ["local-commit", "pull-request", "main", "releases"]
threshold = "no --exit-zero; required=lint,coverage,test,deliverables,trace,reach,ui-e2e; milestone=release-evidence"
timeout_seconds = 3600
failure_policy = "fail_closed"
execution_points = ["pre_commit", "ci"]
required_check = "lint,coverage,test,deliverables,trace,reach,ui-e2e"
```

### 4.3 CI / pre-commit / release

- Required checks 七名不变（lint/coverage/test/deliverables/trace/reach/ui-e2e）；`release-evidence` milestone 硬门禁不变。v0.12 零 workflow 变更；安装 e2e（非 UI）落 `test` job 的 `tests/e2e`，OOB UI 落 `ui-e2e`。
- pre-commit hook 不变；新增 Python 模块（install/project_runtime/install_cmd、serve/review 修订）自动纳入 ruff/flake8/pylint 与 coverage scope。JS 层守卫空缺显式继承（无 JS linter，UI e2e 与评审承载）。
- 安装命令（副作用归 Runtime）不变：`python -m venv .venv`、`.venv/bin/pip install -e '.[dev]'`、`git config core.hooksPath .githooks`。
- version gate：bindings 声明不变（`pyproject.toml:project.version` 与 `tracks/__init__.py:__version__` 绑定 `{release_version}`）；版本已设计期 bump 至 `0.12.0`（§2），M-VERIFY 前必须等于目标发布版本。
- build/smoke：`[host-contract.build]` 与 smoke 不变；wheel 内容合同（§1.0.2）由测试断言，smoke 继续验证 `trac --help` 零仓库可用。

### 4.4 Integration/e2e 基础设施

- 新 integration 资产落 `tests/integration/`：`test_install_package.py`（wheel 内容/坏 wheel 拒绝/全局 --help/版本 bump/token 登记）、`test_install_targets.py`（空目录/已有项目）、`test_install_overwrite.py`（托管覆盖/用户文件逐字节/幂等）、`test_install_versions.py`（显式版本/全局升级/无自动升级/失败重试）、`test_server_lifecycle.py`（项目内后台/停止/状态/项目外拒绝/Windows 分支）、`test_run_rounds.py`（列表/详情/思考缺失）、`test_review_decision_contract.py`（退回线程+事件）、`test_static_cache.py`（no-cache/304/API 不变）。
- 新 e2e 资产落 `tests/e2e/`：`test_install_journey.py`（隔离 prefix 安装→空目录/已有项目 install→version→后台 serve→全局升级旧项目仍旧；非 `ui` marker，跑在 `test` job）、`test_run_workflow_ui.py`（`@pytest.mark.ui`：阶段路径/工作流聚合/按轮手风琴/卡片与本地时间/评审两决策/失败反馈）。
- 夹具：wheel 构建器（当前源码 wheel；版本覆写 wheel 在临时副本改 `pyproject`/`__init__` 后构建）；隔离 prefix（`lib/python<X.Y>/site-packages` + `bin/trac`；`pip install --no-deps --target` + `PYTHONPATH`，使用当前测试解释器；不建 venv、不访问网络）；项目夹具（临时 git 仓/空目录 + receipt）；服务夹具（start/stop/healthz/pidfile）；事件/轮次种子（Store + blobs + `.opencode/logs`）；浏览器夹具继承。
- 故障注入仅经公开面：坏 wheel、缺资源 wheel、非 git 非空目录、端口占用、陈旧 pidfile、静态文件内容替换、review 线程写失败（版本目录只读）、API route 失败（Playwright route）、日志轮转（无日志种子）。
- 测试数据：`tests/assets/v0.12/`（Shield 写域）固化多阶段事件种子、round blobs/log 种子、坏 wheel 构建配方；canary 密钥纪律继承。
- deterministic suite 默认离线：安装只用本地 wheel/运行中 distribution；serve 只 loopback；UI 只本地 Chromium。电池墙钟有界继承。

### 4.5 Build / artifact

build backend 与 wheel 名不变；package-data 新增 `assets/opencode.json`；`tracks/agents`/`skills`/`templates` 通配已覆盖。M-VERIFY build gate/smoke 不变；版本字段 `0.12.0` 与 `v0.12.0` tag 经 version gate 绑定比对。官方安装包就是该 wheel；`trac install --wheel` 直接消费它。

### 4.6 发布恢复

- 安装：staging 目录在下次安装开始时清理；receipt 原子写；目录替换失败回滚备份；同版本重装收敛。全局升级不写项目。
- 后台服务：崩溃留下的 pidfile 由 `--status` 清理；`--stop` 校验 PID 归属。无自动重启（显式语义）。
- review 线程写入：命令在执行前有 awaiting 前置，崩溃重放可能重复写线程；以命令幂等键 + awaiting 前置降低，风险记录 §5.2。`human.review` 事件与线程在同一 worker 执行序内，事件失败不产生“成功”结论。

## 5. 有意识简化与风险

### 5.1 有意识简化

- 项目安装不建 venv、不解析依赖、不访问包索引；显式跨版本安装要求用户提供官方 wheel。安装器不做下载/升级迁移。
- 后台服务是 detached 进程，不做 launchd/systemd/Windows 服务、不做自动重启/日志轮转；Windows 只前台。
- 安装不创建/不修改宿主根 `.gitignore`，也不修改既有根 `opencode.json` 或用户自有 `.opencode/opencode.json`；用户自行保留配置与 byproduct ignore。需要 init 的 host block 时用户可显式运行既有 `trac init`。
- OOB 阶段路径采用已交付的 stage-track 状态轨道，不恢复早期静态 13 阶段 mermaid；阶段实际工作流仍为 mermaid。纯展示阈值/排版不重议。
- review 退回的线程写入按 spec 恢复；不引入“线程去重表”等第二存储，重复风险由命令幂等与 awaiting 前置兜底。
- 项目 site-packages 共享全局解释器的运行时依赖；不复制/隔离 starlette/uvicorn/certifi。

### 5.2 风险

- **共享运行时依赖**：全局解释器的依赖被移除或不兼容升级时，旧项目可能启动失败。缓解：安装时校验 `Requires-Dist` 存在（`dependency_missing` fail-closed）；v0.12 依赖集合固定；`trac version`/health check 给出可读失败而非伪成功。若未来版本改变依赖集合，需要重新设计项目环境（记录为已知限制）。
- **运行中 distribution 复制**：editable 全局安装会把源码树快照复制进项目；官方使用应安装 wheel。缓解：安装后 health check 与 receipt 记录来源；测试覆盖 wheel 与 editable 两种来源。
- **后台进程归属**：PID 复用可能误杀；pidfile 记录命令行并校验项目 launcher 路径，校验失败拒绝停止。
- **review 线程重复**：线程写成功但 `human.review` 落盘失败/崩溃重放可能重复根线程。缓解：命令幂等键、awaiting 前置、线程正文包含固定 review 标识（可人工识别）；不承诺自动去重（spec 未要求）。
- **安装原子性的边界**：多个托管目录逐个替换，极端崩溃可能留下部分新目录但 receipt 仍指旧版本；下次安装的 staging 清理与覆盖会收敛。receipt 最后写保证“成功”不早于内容。
- **默认 opencode.json 不含 provider/model**：完整工作能力仍要求用户环境已有 opencode 与模型配置；安装包承诺的是默认配置存在与权限正确，不是 provider 凭据。
- **OOB 继承与旧测试漂移**：v0.11 的部分 e2e 断言（正常门 reason/impact、`gate-action-review`、review-doc editor）与 OOB 现状不符；v0.12 必须按 FR-0359-02 与 OOB 面更新（test-plan §11），否则红窗会被陈旧断言污染。
- **版本目录增长**：不同版本 site-packages 各占一份，反复跨版本安装会增长磁盘；v0.12 只保留已安装版本目录（不做自动清理），显式清理不在范围。
