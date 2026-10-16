---
envelope: tracks-envelope:v2
story_id: S-001
title: tracks 安装包与多版本安装：全局 trac 与 trac install <path>
created: 2026-10-13
status: draft
sha:
---

# S-001: tracks 安装包与多版本安装：全局 trac 与 trac install <path>

## 1. 原始输入

> 1. 制作tracks 的安装包，要打包 skills/templates和默认的 opencode.json
> 2. 安装时，要生成全局 shell命令 trac，以便启动、停止服务器
> 3. 允许全局安装和指定目录安装不同版本。即，可能存在这样的情况，全局安装、更新最新版本的 tracks，但正在进行中的开发项目，仍然使用之前的版本。目前不支持开发中的项目升级
> 4. 支持通过 trac install <path> 命令，把 tracks 安装到正在开发的项目（也可以是空目录）

> **Scribe [RESOLVED]:** trac install <path> 落到非空已有项目时如何处理已有文件？A) 缺啥补啥、已有文件不覆盖（最安全、可预期）；B) 冲突即失败退出并指明手工处理。推荐 A，因为 seed 强调装到正在开发的项目，覆盖或失败都会阻塞正常使用；若选 B 则安装成功率显著降低。请 Human 确认。
>> **Scribe:** DRAFT 已将本问题收录为 §6 开放产品决定（推荐 A：缺啥补啥、已有文件不覆盖，见 BS-05），Human 可在评审阶段定夺；本线程关闭，后续以 §6 为准。

> **Scribe [RESOLVED]:** 全局 trac 的服务器启停语义是什么？A) 沿用现有 trac serve 前台运行（Ctrl-C/SIGTERM 停止），全局安装仅保证命令可用；B) 新增后台启停（含 daemon/autostart/log rotation）。推荐 A，因为 serve_cmd 基线明确本版无 daemon/autostart/log rotation（tracks/cli/serve_cmd.py:1-11），seed 仅说启动停止未要求后台化；若选 B 则范围显著扩大。请 Human 确认。
>> **Scribe:** DRAFT 已将本问题收录为 §6 开放产品决定（推荐 A：沿用前台 serve 语义，见 BS-03），Human 可在评审阶段定夺；本线程关闭，后续以 §6 为准。

## 2. 用户意图

- 用户想完成什么：一次装好 tracks 并在任何目录可用 `trac`；把指定版本装进目标项目（含空目录与进行中项目）；全局升级到新版本时，进行中项目仍停留在旧版本继续工作。
- 当前哪里受阻：无官方安装包内容承诺（seed 要求打包 skills/templates 与默认 opencode.json）；无全局 `trac` 可用承诺；无 `trac install <path>` 跨目录安装能力（现有仅 `trac init` 在当前目录脚手架 `.tracks`）；版本号全局唯一，升级即全局生效，进行中项目无法停留旧版本。
- 完成后能看到什么结果：全局 shell 可执行 `trac` 并启停服务器；目标目录具备所装版本的 tracks 工作能力；全局升级后旧项目行为不变，可继续用旧版本工作。

## 3. 核心操作路径

### 3.1. 全局安装与全局 trac 可用

- **变更基线**：修改 — 当前已有 `[project.scripts] trac` 与 `dist/` wheel（0.11.0），`trac --help` 零依赖可用；但无安装包内容清单承诺，无全局命令可用承诺，无 install 子命令（`tracks/cli/main.py:_COMMANDS` 无 install）。本次新增官方安装承诺与全局可用结果，不改变现有子命令语义。
- **入口/触发**：用户按发布指引获取安装包并执行全局安装后，在任意目录执行 `trac`。

1. 获取对应版本的官方安装包（含 skills/templates 与默认的 opencode.json）。
2. 执行全局安装，使 `trac` 在 shell 全局可用。
3. 在任意目录执行 `trac --help` 验证可用，按需执行 `trac serve --repo <path>` 启动服务器。
4. 通过 Ctrl-C/SIGTERM 停止服务器并确认退出。

- **完成结果**：全局 `trac` 可用，`serve` 前台启停语义不变；用户可继续进入 3.2 安装项目版本，或直接服务已有项目。

### 3.2. 把指定版本安装到目标目录（trac install <path>）

- **变更基线**：新增（无新增路径）— 当前仅 `trac init` 在当前工作目录脚手架 `.tracks`（`tracks/cli/run_cmd.py:51-91`，幂等写 gitignore 与路径提交），无跨目录 `install <path>`，无版本选择，无空目录建项目语义。
- **入口/触发**：已装全局 `trac` 的用户在终端执行 `trac install <path>`，`<path>` 为正在开发的项目目录或空目录。

1. 确定目标目录与版本（默认含义与显式版本形式由后续阶段定，本 story 只要求“可指定版本”）。
2. 执行 `trac install <path>`，把对应版本 tracks 安装到目标目录。
3. 在目标目录验证该版本 tracks 可工作（可用性级别，不规定具体断言位置与文案）。

- **完成结果**：目标目录具备所装版本的 tracks 工作能力，可进入 3.3 的共存使用；落到非空已有项目时的已有文件处理见 BS-05 与 §6；失败时明确报错且不伪报成功，用户可重试或手工处理后重试。

### 3.3. 全局与项目多版本共存

- **变更基线**：新增（无新增路径）— 当前无项目级版本锁定，版本号全局唯一（`pyproject.toml:version 0.11.0`），升级即全局生效，进行中项目无法停留旧版本。本次新增“项目锁定版本、全局升级不波及”的结果。
- **入口/触发**：用户全局升级到新版本后，回到仍锁定旧版本的进行中项目继续工作。

1. 全局安装并升级到新版本。
2. 回到锁定旧版本的进行中项目，用其锁定版本继续工作（serve/run 等行为与升级前一致）。
3. 可观察到该项目仍用旧版本（展示位置与文案由后续阶段定）。

- **完成结果**：新旧版本共存：全局新、旧项目旧；进行中项目不被强制升级；需要升级时由用户显式发起（升级迁移工具本身不在本次范围，见 §5）。

## 4. 行为种子

### BS-01 安装包内容完整

- EARS: `WHEN 安装包构建完成, THE 系统 SHALL 提供包含 skills/templates 与默认 opencode.json 的官方安装包`
- 来源: [3.1]
- 说明: 保证用户一次装好后全局与项目安装有完整来源，不缺能力定义与默认配置。

### BS-02 全局 trac 可用

- EARS: `WHEN 全局安装完成, THE 系统 SHALL 在 shell 全局提供可执行的 trac 命令（含 --help 可用）`
- 来源: [3.1]
- 说明: 保护“到处可用”的安装结果，后续 install/serve 都依赖它。

### BS-03 服务启停语义不变

- EARS: `WHEN 用户用全局 trac 启动服务器, THE 系统 SHALL 以前台运行提供服务并以 Ctrl-C/SIGTERM 停止（沿用现有 serve 语义）`
- 来源: [3.1 / 重要推导：tracks/cli/serve_cmd.py:1-11 明确本版无 daemon/autostart/log rotation]
- 说明: 防止把“启动停止”误展开成后台化；若 Human 在 §6 选后台化，本条需重写。

### BS-04 安装目标兼容空目录与已有项目

- EARS: `WHEN 用户执行 trac install <path>, THE 系统 SHALL 同时支持空目录与正在开发的已有项目目录`
- 来源: [3.2]
- 说明: 保护 seed #4 的核心场景：空目录建项目与老项目加装同一命令完成。

### BS-05 已有文件不覆盖

- EARS: `WHEN 安装目标为非空已有项目且存在同名文件, THE 系统 SHALL 缺啥补啥、已有文件不覆盖并明确报告安装结果`
- 来源: [3.2 / 重要推导：seed 强调装到正在开发的项目，覆盖或失败都会阻塞正常使用]
- 说明: 保护进行中项目不被安装破坏；备选“冲突即失败”见 §6，选 B 则本条改为失败退出语义。

### BS-06 全局升级不改变锁定项目行为

- EARS: `WHEN 全局 tracks 升级到新版本, THE 系统 SHALL 保持已锁定旧版本的进行中项目仍用旧版本工作且行为不变`
- 来源: [3.3 / 约束：seed #3 明示]
- 说明: 多版本共存的核心价值：新版本试用与旧项目稳定互不干扰。

### BS-07 不强制升级进行中项目

- EARS: `WHERE 项目锁定旧版本, THE 系统 SHALL 不自动升级该项目且不阻塞其正常使用`
- 来源: [3.3 / 约束：seed #3“目前不支持开发中的项目升级”]
- 说明: 把“不支持升级”从现状描述转为产品承诺：旧项目可安心停留，需要升级由用户显式发起。

## 5. 范围、约束与例外

- **必须保持的产品约束**：进行中项目不自动升级（seed #3 明示）；安装到已有项目不得破坏已有开发内容（推荐默认见 BS-05，待 §6 确认）；全局 `trac` 必须能启动与停止服务器（seed #2，语义默认前台，待 §6 确认）。
- **非常规要求**：无。
- **Out-of-Scope**：进行中项目的一键升级/迁移工具（seed 明示目前不支持，本次只保共存不做升级器）；serve 后台化含 daemon/autostart/log rotation（维持 `serve_cmd` 基线，不在本 story 展开）。

## 6. 开放产品决定

**trac install 落到非空已有项目时如何处理已有文件** — 缺啥补啥还是冲突即失败，会改变安装成功率与用户手工负担。方向：A) 缺啥补啥、已有文件不覆盖 B) 冲突即失败退出并指明手工处理。推荐 A，因为 seed 强调装到正在开发的项目，覆盖或失败都会阻塞正常使用。

**全局 trac 的服务器启停语义是否新增后台化** — 沿用前台还是新增后台启停，会显著改变 serve 范围与可观测性负担。方向：A) 沿用现有 trac serve 前台运行（Ctrl-C/SIGTERM 停止），全局安装仅保证命令可用 B) 新增后台启停（含 daemon/autostart/log rotation）。推荐 A，因为 serve_cmd 基线明确本版无 daemon/autostart/log rotation（tracks/cli/serve_cmd.py:1-11），seed 仅说启动停止未要求后台化。

## 7. 必要性与风险

- **既有能力**：`[project.scripts] trac` 与 `dist/` wheel 安装基线；`trac init` 的 `.tracks` 脚手架幂等（`run_cmd.py:51-91`）；`trac serve` 前台运行与启停退出码（`serve_cmd.py`）；`tracks/skills`、`tracks/templates`、`tracks/agents` 与包数据清单（`pyproject.toml:package-data`）；仓库根默认 `opencode.json`。
- **冲突**：无（多版本共存是对“全局唯一版本”现状的增强，不推翻既有子命令语义）。
- **重要风险**：§6 两项长期未决会改变范围（已有文件覆盖策略、serve 后台化）；若安装包内容清单中 skills/templates/默认 opencode.json 的具体所指在后续阶段无确切来源绑定，安装包完整性不可验证。
