"""v0.9 后台驱动控制层（IF-009）。

持久命令服务、租约/代次防旧、单活动 run 调度与 hotfix 换队、外部等待
（含 quota）持久化与有界退避、崩溃重启恢复。supervisor 是确定性运行控制
层：它消费 kernel.machine.decide 的既有输出，不复制第二套状态机
（interfaces.md §1d/§1i/§1j）。stub：行为体由 Devon 实现任务补全。
"""
