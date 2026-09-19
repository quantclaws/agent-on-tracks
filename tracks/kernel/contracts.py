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
        "输出合同（返回前自检）：最终回复必须以 JSON 结尾（Runtime 取最后一条 "
        "text 消息）；散文/清单不构成交付。当 assignment 声明 "
        "tracks-envelope:v2 时，JSON 必须是恰好一个 fenced ```tracks-envelope "
        "block 的 payload（块外不得有散文）；未声明时以裸 JSON object 结尾。"
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
        "manifest 的 include 与你本次实际落盘的产物逐一对照（多报漏报即"
        "失败）——对照范围是你创建/修改的文件，不是整个 git status；派发时"
        "工作区已存在的未提交内容（如评审 verdict）不属你的产物，不得纳入"
        "manifest，也不得 reset/checkout 清理",
        "至少一个测试模块（test_*.py）或可收集支持资产（*.patch/*.json）——否则判 collection 失败",
        "对全部产出文件运行宿主环境的 ruff check 与 assignment.commands.guard",
        "宿主 repo 的 pre-commit 钩子（若安装）会在提交时重跑 lint——返回前不绿即有作废风险",
    ],
}


DEVON_EVIDENCE_CONTRACT: dict = {
    "_doc": (
        "输出合同（返回前自检）：Devon 的最终回复必须以 evidence JSON object 结尾"
        "（Runtime 取最后一条 text 消息；tracks-devon-rgr §4）。"
        "当 assignment 声明 tracks-envelope:v2 时，最终回复 = 恰好一个 "
        "fenced ```tracks-envelope block（块外不得有散文），其内容是"
        "{\"envelope\": {\"kind\": \"devon:<phase>\", \"version\": 2}, "
        "\"payload\": {...}} **两层 JSON**——evidence 字段全部在 payload 里；"
        "把裸 payload 直接放进 fence（无 envelope 头）= reply_format_error "
        "(missing_kind) 作废。未声明时以裸 JSON object 结尾。"
        "缺任一必填字段或类型不符即 impl_defect 判失败、attempt 作废。"
        "本示例由 runtime 消费方校验代码反推生成并与 tracks-devon-rgr §4 对账"
        "（live 教训 T-004：手写示例把 commands 画成 dict，误导 attempt 作废；"
        "live 教训 2026-09-19 T-006：示例只画裸 payload、未画 envelope 包裹，"
        "agent 六次 attempt 全发裸 payload 被 missing_kind 打回——示例即最具体的"
        "合同，必须展示完整两层结构）。"
    ),
    "example": {
        "envelope": {"kind": "devon:green", "version": 2},
        "payload": {
            "phase": "green",
            "changed_paths": ["tracks/kernel/hotfix.py"],
            "commands": [
                {
                    "cmd": "<host contract [unit].run command>",
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
