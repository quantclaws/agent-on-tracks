# Agent on Tracks

Tracks 把一条 story 推进为可验证的软件交付。人类提出需求、参与规格讨论和批准关键决定；Agent 编写设计、测试和实现，Runtime 根据测试、评审与 Git 证据决定能否进入下一阶段。Web 工作台提供需求入口、文档阅读与编辑、审批、回退和运行状态。

需要 macOS 或 Linux、Python 3.11+、Git、配置好模型和凭据的 [OpenCode](https://opencode.ai/)。完整 GitHub 交付还需要目标仓库、可推送的 origin、`GITHUB_TOKEN` 和 `TRAC_GITHUB_REPO=owner/repo`。Windows 尚不支持。

## 安装并创建项目

用下载的发行 wheel 安装全局入口；将下方文件名替换为实际路径：

```bash
python3 -m venv ~/.venvs/tracks
~/.venvs/tracks/bin/pip install /absolute/path/agent_on_tracks-0.12.1-py3-none-any.whl
export PATH="$HOME/.venvs/tracks/bin:$PATH"
trac version

git init -b main my-product
git -C my-product config user.name "Your Name"
git -C my-product config user.email "you@example.com"
trac install ./my-product
cd my-product
trac version --json
```

`trac install` 将运行包锁定在项目内，部署 Agent、skill 和模板，并创建受控提交。升级全局入口不会自动升级已有项目。不要移动或删除 `.tracks/env/`。

启动前让 `opencode run` 能使用已配置的模型。可以设置 `TRAC_AGENT_MODEL=provider/model`，也可以使用 OpenCode 自身的默认配置。发行包不携带模型凭据。真实运行使用 `TRAC_AGENT_BACKEND=opencode`；`fake` 只用于确定性流程测试。

```bash
export TRAC_AGENT_BACKEND=opencode
export TRAC_GITHUB_REPO=your-org/your-product
# 在当前终端提供 GITHUB_TOKEN 和模型凭据。
git remote add origin git@github.com:your-org/your-product.git
trac serve --repo "$PWD" --port 0
```

首次前台启动会提示设置 Web 密码。打开输出的本机 URL，登录并填写显示名称。在 Releases 左侧点击 **+**，选择功能开发，输入 story，查看 Scribe 的评估并选择“立项开发”。后台模式使用 `--background`，需预先通过 `TRAC_SERVE_PASSWORD` 或 `--password-stdin` 提供密码。

## 使用工作台

- **Releases**：选择运行，查看当前阶段、阻塞原因、任务进度、阶段工作流和每轮输入、输出、工具调用。审批与回退入口位于当前运行卡片。
- **Docs**：阅读各版本的 story、spec、acceptance、architecture、interfaces 和 test-plan；修改自动保存，冲突时选择重载或覆盖。从运行进入文档后，点击“返回运行”恢复原运行与阶段选择。
- **Todos**：查看等待人类处理的事项，点击进入相应运行。
- **Settings**：配置工作台支持的网络选项；修改后依界面指引重启服务。

需求依次经过 story、spec、acceptance 的评审和需求基线批准。此后 Runtime 驱动设计、独立测试、实现、验证、安全审查，最后等待发布授权。缺少凭据、测试失败或证据过期时会停下并给出原因。确认交付完成应查看 `terminal=released`，仅有一次命令退出码为 0 不代表项目完成。

```bash
trac status
trac replay <run-id>
trac report --run-id <run-id> --format html
trac check trace --version <product-version> --json
trac check reach --json
trac serve --repo "$PWD" --status
trac serve --repo "$PWD" --stop
```

生产模块与测试之间的追踪、同一候选提交的测试证据、反例实验和独立评审共同支撑交付判断。测试无法证明任意自然语言需求的数学完备性；`reach` 当前检查 Python 模块可达性，其他语言需要对应的宿主测试与工具合同。

详细步骤见[安装](docs/getting-started/installation.md)、[第一次开发](docs/getting-started/first-release.md)和[查看运行](docs/how-to/inspect-run.md)。

## 开发与验证 Tracks

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/playwright install chromium
.venv/bin/python -m pytest tests/unit tests/integration tests/e2e -n 4 --dist loadscope
.venv/bin/python -m pytest tests/e2e -m ui -n 4 --dist loadscope
```

确定性测试使用 fake Agent 和隔离的外部服务替身。`tests/e2e_live` 另行验证真实 Agent 与外部通道；缺少凭据而跳过的 live 测试不构成已验证证据。
