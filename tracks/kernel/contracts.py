"""Writer manifest contract constants (B28/#30 slim, SC WS-A2).

Assignment-carried output contracts: the exact manifest schema, a filled
example, and the mechanical failure classes — so writers can self-validate
BEFORE returning instead of learning the shape one burned dispatch at a
time (live run 01M0AMKV M-TEST, 2026-08-18: six full-price attempts failed
on manifest shape/scope, none on semantics).
"""

from __future__ import annotations

WRITE_MANIFEST_CONTRACT: dict = {
    "_doc": (
        "输出合同（返回前自检）：最终回复必须以裸 JSON 结尾（Runtime 取最后一条 "
        "text 消息中的 JSON object）；散文/清单/Markdown 不构成交付。"
    ),
    "fields": {
        "artifact_manifest.include": (
            "本次 dispatch 你创建或修改的每一个文件的仓库相对路径，逐项 "
            "{'path': ..., 'kind': ..., 'role': ...}；include 必须覆盖 [layout.shield] "
            "声明目录（如 tests/integration、tests/e2e、tests/counterexamples）下你"
            "实际落盘的全部文件——运行时逐文件比对，失配发 verdict.failed(check=manifest)"
            "（仅当重试时文件全部已存在且为 dirty 才有幂等宽限）"
        ),
        "suggested_commit_message": "非空一行提交信息；空串或缺失判 manifest_malformed",
    },
    "example": {
        "artifact_manifest": {
            "include": [
                {
                    "path": "tests/integration/test_x.py",
                    "kind": "integration_test",
                    "role": "test",
                },
                {
                    "path": "tests/counterexamples/v0.6/fixture.patch",
                    "kind": "support_asset",
                    "role": "test",
                },
            ]
        },
        "suggested_commit_message": "M-TEST: <一句话覆盖摘要>",
    },
    "mechanical_checks_before_return": [
        "manifest 的 include 与 git status/实际产物逐一对照（多报漏报即失败）",
        "至少一个测试模块（test_*.py）或可收集支持资产（*.patch/*.json）——否则判 collection 失败",
        "对全部产出文件运行 .venv/bin/ruff check 与 assignment.commands.guard",
        "宿主 repo 的 pre-commit 钩子（若安装）会在提交时重跑 lint——返回前不绿即有作废风险",
    ],
}


DEVON_EVIDENCE_CONTRACT: dict = {
    "_doc": (
        "输出合同（返回前自检）：Devon 的最终回复必须以裸 evidence JSON object 结尾"
        "（Runtime 取最后一条 text 消息中的 JSON object；tracks-devon-rgr §4）。"
        "缺任一必填字段或类型不符即 impl_defect 判失败、attempt 作废。"
        "本示例由 runtime 消费方校验代码反推生成并与 tracks-devon-rgr §4 对账"
        "（live 教训 T-004：手写示例把 commands 画成 dict，误导 attempt 作废）。"
    ),
    "example": {
        "phase": "green",
        "changed_paths": ["tracks/kernel/hotfix.py"],
        "commands": [
            {
                "cmd": ".venv/bin/python -m pytest -n 4 tests/unit",
                "result": "pass",
                "output_summary": "361 passed",
            }
        ],
        "results": [{"classification": "assertion_failure"}],
        "manifest_compliance": True,
        "pre_identity": "<assignment.pre_dirty_snapshot 提供则回显>",
        "post_identity": "<完工后自行计算>",
        "r_identity": "<GREEN/REFACTOR 必填：assignment.r_tree_identity 或 RED 的 R>",
        "implemented_if_ids": ["IF-HOTFIX-002"],
    },
    "shape_rules": [
        "commands 必须是 LIST（每项 {cmd, result: pass|fail, output_summary}）——不是 dict",
        "manifest_compliance 必须是字面 true（不是字符串）",
        "changed_paths / implemented_if_ids 必须是非空字符串列表",
        "phase 必须等于本次派发相位（red|green|refactor）",
        "r_identity：green/refactor 必填；no_change_reason：green/refactor 无变更时",
        "RED 相位 changed_paths 非空（新增的是测试文件）",
        "no_change（green/refactor 无变更时）：changed_paths 必须为 [] 且 "
        "no_change_reason 非空；禁止声称 changed_paths 而 pre/post identity "
        "相同（runtime 比对并按 B38 fail-closed）",
    ],
}
