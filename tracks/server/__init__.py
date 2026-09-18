"""v0.9 Web delivery surface (IF-009).

HTTP 交付面：Starlette 应用、认证、命令防护、脱敏、只读投影与事件订阅。
本包不复制 kernel/executor 的任何状态机语义；全部业务校验复用既有合同
（interfaces.md §1b/§1f/§1g）。stub：行为体由 Devon 实现任务补全。
"""
