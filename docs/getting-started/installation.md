# 安装 Agent on Tracks

## 系统要求

当前仓库声明 Python `>=3.10`，但完整流程使用标准库 `tomllib`，实际建议使用 **Python 3.11 或更新版本**。

还需要：

- Git，且宿主项目已经初始化仓库；
- POSIX 系统。Inline discussion 使用 `fcntl.flock`，当前不支持 Windows；
- 默认 Agent backend 所需的 `opencode` CLI；使用 fake backend 时不需要 harness。

Tracks 运行时没有第三方 Python dependency。测试、lint 与开发工具位于 `dev` extra。

宿主项目最好有 `main` 分支，并配置 Git 用户名；`trac init` 会创建一次受控 commit。

## 安装发行包

当前唯一经过 CI 与端到端测试验证的方式是从仓库做 editable install：

```bash
git clone <tracks-repository>
cd tracks
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

验证 console script：

```bash
.venv/bin/trac status
```

未初始化项目时应输出：

```text
no runs yet
```

当前 CLI 没有 `trac --version`，无参数也会打印 usage 并返回 1。不要用它们作为安装成功检查。

仓库中的旧 `dist/` wheel 不包含当前全部功能，不能代表 HEAD；本文也不承诺包索引中的版本等于当前源码。

开发本项目时可以启用 hooks：

```bash
git config core.hooksPath .githooks
```

## 初始化宿主项目

切换到需要使用 Tracks 的宿主 Git 仓库：

```bash
cd <host-project>
trac init
```

命令创建 `.tracks/` 的基础骨架并提交：

```text
.tracks/
├── projects/
├── runtime/
└── wiki/
```

Projects 保存版本化文档；wiki 保存跨 release 决策；runtime 保存 SQLite、blob 和 lock。`.tracks/project/` 及其中的宿主测试合同会在流程进入相应设计阶段后产生，不由初始 scaffold 预先创建。

Runtime 数据通过 `.tracks/runtime/.gitignore` 排除，讨论锁和临时文件通过 `.tracks/projects/.gitignore` 排除。目录骨架与配置进入版本控制，运行时数据库不进入。

`trac init` 幂等。重复运行不会覆盖用户修改过的 `.gitignore`，没有 staged diff 时也不会创建空 commit。

命令要求当前目录是 Git 仓库。非 Git 项目或 Git commit 失败会返回错误，不会退化成无版本控制模式。

## Agent 后端

默认 backend 是 opencode：

```bash
export TRAC_AGENT_BACKEND=opencode
```

Runtime 调用 PATH 中的 `opencode run`，并在 assignment 期间把所需 Agent、skill 和 template 物化到宿主 `.opencode/`。结束后清理或还原原内容。

测试和流程演练可以选择确定性的 fake backend：

```bash
export TRAC_AGENT_BACKEND=fake
```

常用环境变量：

| 变量 | 用途 |
|---|---|
| `TRAC_AGENT_BACKEND` | `fake` 或 `opencode`；默认 opencode |
| `TRAC_AGENT_MODEL` | 传给 opencode 的可选模型名 |
| `TRAC_AGENT_CONSOLE_INPUT` | 传给 Agent 子进程 stdin 的文本 |
| `TRAC_FAKE_SIMULATE` | 测试用 fake 场景注入；设置后强制 fake |

`TRAC_LIVE_*` 是仓库 e2e_live 测试变量，不是产品配置。当前也没有生产用 `TRAC_AGENT_TIMEOUT`；等待过久时由操作员 Ctrl-C 取消。

GitHub issue 后端另使用 `GITHUB_TOKEN`、`TRAC_GITHUB_REPO` 与 `TRAC_GITHUB_PROJECT`。没有 token 时会使用 fake issue backend。

## 升级与卸载

当前 package 版本为 `0.1.0`，Runtime schema version 为 1，尚无正式数据库迁移机制。因此升级前应保留宿主仓库和 `.tracks/runtime/` 的备份，并阅读目标版本说明。

`trac check deliverables` 可以检查安装包内固定 Agent/skill 是否存在，以及最小 version/IQ frontmatter。它不验证 SemVer、跨文件版本一致性或 digest，不能替代完整发行包验证。

卸载 Python 包不会删除宿主项目中的 `.tracks/`、文档、事件数据库或 Git 历史。重新安装前先确认新版本是否理解现有 schema；不要靠删除 `.tracks/` 完成升级。

需要把 Runtime 数据移到默认目录之外时，可以设置：

```bash
export TRACKS_HOME=/absolute/path/to/tracks-home
```

若未设置，Tracks 使用当前工作目录下的 `.tracks`。该变量改变 projects、runtime、project 与 wiki 的共同根目录，应在发起 run 前固定，避免同一宿主项目产生两套互不相见的状态。
