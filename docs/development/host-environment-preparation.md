# 首次测试前的宿主环境准备

新安装项目此前直接进入 M-TEST 的 Phase 0 收集测试，但宿主环境尚不存在。
`host-contract.install` 只在后续 M-VERIFY 构建时执行；测试夹具预置的 `.venv`
符号链接掩盖了这个顺序错误。另一个问题是未声明宿主合同的运行会获得包含
`true` 质量命令的历史默认合同。

现在 `full-v1` 工作流在 Phase 0 前执行独立的 `prepare_host_environment` 命令。
它只执行 Archer 在 `.tracks/projects/project.toml` 中声明的命令：

```toml
[host-contract]
version = 1
language = "python"
toolchain = "cpython>=3.11"
bootstrap = ["python3 -m venv .venv", "git config core.hooksPath .githooks"]
install = ".venv/bin/python -m pip install -e '.[dev]'"
```

该片段只展示准备字段。设计仍需交付项目元数据、hook、质量守卫、测试、构建、
安全和 CI 合同；准备命令不能依赖尚未实现的业务代码。Runtime 使用 `shlex`
和 `shell=False` 执行命令，并复用宿主子进程环境隔离与超时机制。

`command.issued` 写入 WAL 后，`environment.preparation_started` 固定合同摘要和
Git HEAD。每步成功记录 `environment.step_completed`，输出保存于内容寻址的
审计 blob；这些中间事件不会清除 pending 命令。中断后仅复用同一 command ID、
同一输入的成功步骤，未完成的步骤重跑，因此声明必须可安全重试。合同或 HEAD
变化时停止并要求按新输入重试，避免把旧步骤算入新准备过程。

全部步骤通过且输入仍相同时，`environment.prepared` 清除 pending 并允许
Phase 0 继续收集测试、验证基线和质量守卫。失败保留错误、退出码及输出引用，
不会生成就绪事件。重新进入或回退到 M-DESIGN 会撤销环境就绪状态。

新工作流的设计物化和 M-VERIFY 均拒绝缺失的宿主合同。没有工作流标识的历史
运行保留原有回放与默认合同语义。确定性 fake Archer 显式声明自己的模拟合同，
生产 Runtime 没有 fake 放行分支。

`tests/integration/test_host_environment.py` 验证删除夹具 `.venv` 链接后的首次
环境创建及真实 pytest 收集、两种安装失败、合同缺失、历史兼容、重规划、实际
进程 SIGKILL 后的 WAL 恢复，以及中断期间合同或 Git HEAD 变化。Web 浏览器
测试验证环境准备事件无需刷新即可显示中文状态。

这些验证不调用模型，不证明真实 Agent 已完成整个产品开发；真实通道验收仍须
在获准的模型端点和隔离项目中完成。
