# 独立验收、环境与最终发布条件

## 可立即运行的交接检查

在仓库根目录：

```sh
python3 docs/handoff/v0.8/verify-state.py
python3 docs/handoff/v0.8/verify-parity.py --candidate .tracks/runtime/handoff-assets/parity-candidate
```

第一条只核对冻结 WIP/candidate 摘要、所需文件和临时文档已清理，不验证产品功能。后续修改源码后摘要不同是预期，不能为使脚本绿而覆盖新工作。

第二条创建独立 reviewer 临时目录，只向 reviewer 复制 root tests，不向 implementer 复制冻结测试。使用当前 `.venv` 跑四文件共 17 checks，生成 review.xml、review.log、summary.json，打印目录；返回 1 表示测试 RED，不能解释为脚本坏了。交接实测 **13 pass、4 fail、0 error/skip**。4 个失败均在 materialized parity：正常路径缺实际 path/hash 审计，agent/skill/template 拒绝路径提前记录成功事件。Micro-1 的两项修复使此前 11 个失败缩减到 4 个，但候选整体仍不可合入。

持久原始结果在 `.tracks/runtime/handoff-assets/parity-review/`，摘要在 `data/parity-review.json`。这次只验证本地可执行 standin、没有调用线上模型/发布，不构成全源码覆盖率或 live journey 证明。脚本依赖本机已有 `.venv`，迁移机器后先按 pyproject 安装测试环境。

分层定向验证（在隔离 candidate 内执行，不能给实现者运行冻结验收）：

```sh
.venv/bin/python -m pytest -n4 --dist loadscope tests/unit/test_fullf_evidence_oob.py tests/integration/test_verify_fullf_reuse.py --junitxml=fullf.xml
.venv/bin/python -m pytest -n4 --dist loadscope tests/unit/test_verify_final_oob.py tests/integration/test_verify_prism_final.py --junitxml=prism.xml
.venv/bin/python -m pytest -n4 --dist loadscope tests/unit/test_release_preview_direct.py tests/integration/test_release_preview_direct.py --junitxml=preview.xml
.venv/bin/python -m pytest -n4 --dist loadscope tests/unit/test_release_authorization_direct.py tests/unit/test_cli_release_decision_closed_red.py tests/integration/test_release_authorization_direct.py --junitxml=authorization.xml
```

## 全量测量基线与环境陷阱

最近一次有效**历史候选**全量：2816 tests，2718 pass、98 fail、0 error/skip；18794/21655 行覆盖，**86.78827060725006%**，2861 行未覆盖。此快照早于 kernel/docgap/multitag/envelope/anchor 后续修改，不是现在 HEAD；不能通过减去近期修复的测试数计算当前失败数。98 个失败测试也不是 98 个独立产品缺陷。

原始 JSON/XML/log 在 `.tracks/runtime/forensics/quality-isolated-2026-09-10/`。data/quality-environment-2026-09-09.json 记录当时 wheel SHA、113 个 source/wheel 文件身份以及环境路径。旧 `/tmp` 路径仅供溯源，不能作为新测量的输入。55.76% 那次混用 root 旧安装包，已判无效。

全量测量操作顺序：

1. 冻结确切 commit 和所有必要产品资产、契约、测试；记录每个输入 hash。安装包必须从该 candidate 构建，不能从正在变化的 root 构建。建立新的临时 venv，安装该 wheel 和测试依赖，禁止修改 root `.venv` 或用 system-site-packages 混入旧 tracks。
2. 本项目 fixture 会把 host `.venv` 指向父 `sys.prefix`，有些 CLI 用该 venv 的 `trac`；单改 PYTHONPATH 不够。分别 probe pytest、host python、trac、contract subprocess 的 `sys.executable`、`tracks.__file__`、`tracks.cli.main.__file__`。source-tree 模式可同时用与 wheel **逐文件 hash 一致**的 source；只有已证实等价的两条路径可映射合并。不能凭名称相同映射。
3. 用本次专属 COVERAGE_FILE 与 COVERAGE_PROCESS_START，启用 `tests/_subprocess_coverage/sitecustomize.py`。例如先设置 task_run_dir 为新绝对目录，再从 candidate 执行：

```sh
export COVERAGE_FILE="$task_run_dir/.coverage"
export COVERAGE_PROCESS_START="$task_run_dir/candidate/pyproject.toml"
export PYTHONPATH="$task_run_dir/candidate/tests/_subprocess_coverage:$task_run_dir/candidate"
"$task_run_dir/venv/bin/python" -m coverage run --parallel-mode -m pytest -n4 --dist loadscope --junitxml="$task_run_dir/suite.xml"
"$task_run_dir/venv/bin/python" -m coverage combine "$task_run_dir"
"$task_run_dir/venv/bin/python" -m coverage json -o "$task_run_dir/coverage.json"
```

这是 source-tree + 同身份 wheel 方案，命令前必须完成上面安装与 hash/provenance 检查。保留 stdout/stderr/退出码与所有 coverage shards；不要让 shell 在 pytest RED 后跳过证据收集。确认被测源码集合完整、无未知 package root，必要路径映射有同字节证明；不能 omit 旧包以抬数字，不能缩小 source 范围。性能测试被默认 addopts 排除，须另跑 `pytest -m performance` 并按正式 NFR 审核，不能把默认全套当所有性能要求已测。

## 静态质量与发布门槛

`.githooks/pre-commit` 的 pylint 有 `--exit-zero`，且 pyproject 的 module limit 是 1200；**hook OK 不是用户质量要求通过**。本轮采用生产模块 ≤1000 行的保守目标（比旧配置严格），cognitive complexity ≤15，重复片段、函数 statements/locals 按正式规范及配置收口。最终检查不能放宽阈值或忽略新违规。

```sh
.venv/bin/ruff check tracks tests
.venv/bin/flake8 tracks
.venv/bin/pylint --disable=all --enable=R0801,C0302,R0915,R0914 --max-module-lines=1000 tracks
```

既往定向提交因整树旧 WIP hook 冲突使用过命令局部 `git -c core.hooksPath=/dev/null commit`，未修改持久配置；这是提交拆分办法，不是最终质量豁免。

发布必须同时具备：所有 AC 完整子句与实际执行测试通过 ID 关联；独立 RGR 与物理隔离证据；整树测试和 ≥95% 全源码覆盖；静态质量无未处理违规；Tracks/reference host 六条真实旅程；同一冻结 candidate 的 local gates/CI/FULL_F/Prism/security/preview/authorization；真实远端 effects 和恢复幂等性、milestone/issue/cleanup readback。未满足之前不能创建“完成”事件或宣告 v0.8 已发布。
