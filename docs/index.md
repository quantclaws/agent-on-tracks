# Agent on Tracks 文档

人类提出 story、参与规格制定并批准关键决定；Agent 编写设计、测试与实现，Runtime 根据证据控制流程推进。Web 工作台提供需求入口、文档、审批、回退与运行状态。

- [安装与项目版本锁定](getting-started/installation.md)
- [第一次开发](getting-started/first-release.md)
- [查看运行与事件证据](how-to/inspect-run.md)
- [恢复中断的运行](how-to/recover-run.md)
- [需求追踪与模块可达性](how-to/check-traceability.md)
- [文档中的讨论](how-to/inline-discussion.md)

使用当前发行包前，需要配置 OpenCode 模型、Git 身份以及完整交付使用的 GitHub 凭据。确定性 fake 流程测试与真实模型验证是不同证据；缺失凭据而跳过的 live 测试不构成完成证明。
