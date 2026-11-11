# 安装 Agent on Tracks

Tracks 0.12.1 需要 Python 3.11+、Git 和 macOS/Linux。默认 Agent 后端是 OpenCode；安装 Python 包不会自动配置模型提供方或 API 凭据。

## 安装发行包

下载当前发行 wheel，安装到独立环境：

```bash
python3 -m venv ~/.venvs/tracks
~/.venvs/tracks/bin/pip install /absolute/path/agent_on_tracks-0.12.1-py3-none-any.whl
export PATH="$HOME/.venvs/tracks/bin:$PATH"
trac version
```

`trac version` 显示版本和 global/project 作用域；`trac version --json` 提供机器可读结果。开发 Tracks 本身可在源码仓库使用 `pip install -e '.[dev]'`，普通使用者不需要安装开发工具。

## 创建项目

项目可以是空目录或已有 Git 仓库。安装器会创建受控提交，所以 Git 身份必须可用。以下做法只为该项目配置身份：

```bash
git init -b main my-product
git -C my-product config user.name "Your Name"
git -C my-product config user.email "you@example.com"
trac install ./my-product
cd my-product
trac version --json
```

安装器将 Agent、skill 和模板部署到 `.opencode/`，将锁定运行包放入 `.tracks/env/<version>/site-packages/`，并写入 `.tracks/install.json` 和项目启动器。模板也保留在锁定运行包中，供 Runtime 自身读取。`.tracks/projects/` 保存版本化文档，`.tracks/runtime/` 保存事件、证据和会话，`.tracks/wiki/` 保存跨版本资料。

`trac init` 是旧项目的基础骨架入口；新项目使用 `trac install`，以获得完整资源、项目版本锁定与 Web 服务生命周期支持。

## 模型和 GitHub

先按 OpenCode 的说明配置可用的 provider/model，确认 `opencode` 在 PATH 中。Tracks 默认使用 OpenCode 已配置的模型，也可以显式覆盖：

```bash
export TRAC_AGENT_BACKEND=opencode
export TRAC_AGENT_MODEL=provider/model
export TRAC_GITHUB_REPO=your-org/your-product
git remote add origin git@github.com:your-org/your-product.git
```

在启动服务的终端提供模型凭据和 `GITHUB_TOKEN`。GitHub 完整交付需要目标仓库的 issue、milestone、release 和 Actions 相关权限，以及可推送的 origin。发行包不包含任何凭据；不要把凭据写进 story、spec 或受版本控制的配置。

## Live GitHub 旅程环境前置

| 配置 | 用途 |
| --- | --- |
| `GITHUB_TOKEN` | 目标仓库的 issue、milestone、release 写入及 Actions 读取权限 |
| `TRAC_GITHUB_REPO` | `owner/repo` 形式的目标仓库，与 Git origin 对应 |
| `TRAC_GITHUB_CA_BUNDLE` | 可选的企业 CA 文件；默认使用 certifi CA bundle 校验 TLS |

缺少 token 会报告 `missing_token`；权限不足报告 `auth`；限流报告 `rate_limit`；TLS 或连通性失败报告 `network`；仓库或对象不存在报告 `not_found`。按错误指引补充凭据、核对仓库、等待限额恢复或修复网络后重试，Runtime 不会静默降级为成功。

`TRAC_AGENT_BACKEND=fake` 用于确定性流程测试，不能作为真实 Agent 完成开发的证据。

## 启动 Web 工作台

```bash
trac serve --repo "$PWD" --port 0
```

首次前台启动会提示输入登录密码。浏览器打开输出的 URL，登录并填写显示名称。服务自动注册 `--repo` 指定的已安装项目，Releases 左侧的 **+** 即为需求入口。

后台运行需预先提供密码：

```bash
export TRAC_SERVE_PASSWORD='set-your-own-local-password'
trac serve --repo "$PWD" --port 0 --background
trac serve --repo "$PWD" --status
trac serve --repo "$PWD" --stop
```

`--password-stdin` 可替代环境变量。服务默认绑定 `127.0.0.1`。后台日志位于 `.tracks/service/serve.log`；前台日志输出到终端。运行中的服务使用它启动时的环境；修改模型或凭据后重启服务。

## 升级

全局入口与项目版本相互独立。升级全局包以后，已有项目继续使用 `.tracks/install.json` 锁定的版本。显式升级项目：

```bash
trac serve --repo "$PWD" --stop
trac install "$PWD"
```

安装指定旧版本时，先准备对应的发行 wheel：

```bash
trac install "$PWD" --version 0.11.0 --wheel /absolute/path/agent_on_tracks-0.11.0-py3-none-any.whl
```

升级前保留宿主仓库和 `.tracks/runtime/` 的备份。卸载全局 Python 包不会删除项目文档、事件、证据或 Git 历史。不要通过删除 `.tracks/` 升级。

继续阅读[第一次开发](first-release.md)。
