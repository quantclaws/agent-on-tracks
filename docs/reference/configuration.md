# 配置参考

## 项目配置

`.tracks/project/project.toml` 是宿主项目的 collection/run authority。Runtime 只执行其中声明的命令，不根据 `.py` 后缀、文件命名或 pytest 约定猜测 runner。

Shield WRITE 最终必须提供 artifact manifest（每项包含 `path`、`kind`、`role`）和 suggested commit message。Agent 仅提供建议；Runtime 在提交前独立校验 pre/post dirty identity、写入 scope、regular-file 属性、文件 digest 与 base SHA，通过后才创建提交，并追加 `command_id` trailer。

当前合同采用尽力而为的语言中性限制：M-TEST 的写入范围仍约定为 `tests/`；`kind`/`role` 暂时只作为审计元数据，不定义跨语言 taxonomy；integration/e2e 能力取决于宿主 `project.toml` 提供的 collect contracts。更灵活的测试根目录和角色映射留待后续版本。

当前版本不兼容旧 Agent 的无 manifest 输出。历史文档格式和旧代码标注由 legacy baseline 豁免，Runtime 不通过猜测提供兼容。上述限制用于优先保证主流程跑通，不代表 Runtime 放弃任何安全校验。

## 环境变量
<!-- 本节说明：列出 Agent 后端、fake 模式和 Runtime 路径相关环境变量。 -->

## pyproject.toml
<!-- 本节说明：说明 Tracks 会读取的 pyproject 配置，以及本项目自身的测试和质量门禁配置。 -->

## Agent 与 skill
<!-- 本节说明：说明 Agent/skill 的来源、安装位置、版本 identity、物化与清理。 -->
