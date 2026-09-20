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


PRISM_DIAGNOSE_CONTRACT: dict = {
    "_doc": (
        "输出合同（返回前自检）：DIAGNOSE 的最终回复 = 恰好一个 "
        "fenced ```tracks-envelope block（块外不得有散文），其内容是"
        "{\"envelope\": {\"kind\": \"prism:diagnose\", \"version\": 2}, "
        "\"payload\": {...}} 两层 JSON；payload 的 classification 必须取自"
        " classification_vocabulary。散文分析可以先行，但最后一条消息必须"
        "是该 block（live 教训 2026-09-19 run 01M2QTJB T-006：DIAGNOSE 三次 "
        "attempt 均未发出 envelope——无具体示例时模型停在散文/裸 payload，"
        "示例即最具体的合同；对照 T-004/T-006 教训： Devon 合同示例修复后"
        " missing_kind 立即消失）。"
    ),
    "example": {
        "envelope": {"kind": "prism:diagnose", "version": 2},
        "payload": {
            "classification": "impl_defect",
            "reason": "<one-line attribution of the failed gate>",
            "evidence": "<the observed failure facts>",
        },
    },
}


def result_file_contract(result_path: str, kind: str) -> dict:
    """#174 result-file delivery block injected into declared assignments.

    The agent writes its envelope to ``result_path`` via a Python builder
    (``json.dump`` — machine serialization, escaping correct by
    construction), runs ``verify_command`` (zero exit = the file parses and
    validates against the kind schema) BEFORE replying, and the final reply
    may then be a one-line pointer. Runtime collection prefers the file and
    falls back to the fenced block (backward compatible).

    ``result_path`` is the FULL path, resolved by the executor caller
    against the main repo (absolute). A repo-relative path would land inside
    a writer's isolated worktree — cleaned up after replay — so the
    worktree-resident agent must write through to the main-repo inbox
    regardless of its cwd. Per-dispatch uniqueness (command_id in the file
    name) gives freshness/anti-stale by construction: a reused session can
    only write its own dispatch's file. Live motivation: 19+
    serialization-class burns in run 01M2QTJB (no_envelope_block /
    missing_kind / malformed_json); skill teaching proved a weak lever,
    machine channels a strong one.
    """
    return {
        "result_path": result_path,
        "how": (
            "build the two-layer envelope as a Python dict and write it with"
            f" json.dump to {result_path}; never hand-serialize JSON text"
        ),
        "verify_command": (
            "python -m tracks.cli.main validate-reply"
            f" --file {result_path} --kind {kind}"
        ),
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


# #172 判据包双投（generator self-check before emission）：评审判据与生成
# 判据同源——生成者拿到与评审者相同的 criteria pack（skill 随派发物化）加
# 本发射前自检清单。live 教训 run 01M2QTJB：24 次 PLANNING 派发只有 ~6 次
# 产出可解析评审——「验证者持卷、生成者盲写」是最大的回路损耗源；格式类
# 烧毁在示例到达 agent 后实测归零（3d7d168），语义类判据走同一疗法。
# 清单保持短小并指向 skill 全文（skill 是单一真相源，此处只做逐条点名，
# 不复述全文——复述会产生第三份会漂移的判据副本）。

ARCHER_PLANNING_CRITERIA_CHECKLIST: dict = {
    "_doc": (
        "判据双投（#172）：你的任务图将由 Prism 按 tracks-prism-impl 判据"
        "评审——完整判据已随本次派发物化（skill: tracks-prism-impl，"
        "子状态路由语义一节）。发射前逐条自检下列判据；不满足的项要么修正"
        "计划，要么在 payload 里给出明确理由。机械可判项（覆盖闭包/结构/"
        "双归属）runtime 会在评审前本地拦截——先自检，别把集合运算留给"
        "评审回路。"
    ),
    "self_check_before_emission": [
        "覆盖闭包：全体任务的 acceptance 锚点并集 ⊇ test-plan §8 全部 "
        "integration 行目标；反向脏锚（声明了 §8 无对应行）同样被拒",
        "结构：unit 层锚点全部指向 unit 层路径；acceptance 锚点全部指向 "
        "integration 层路径且非空；e2e 层目标不属 acceptance 覆盖义务",
        "双归属：每个锚点恰好一个 owner 任务（deferred→终局收口任务的"
        "镜像归属除外）；每个 AC 恰好注册一次",
        "依赖批次序：每个任务的依赖都在更早批次；卡片字节预算内的 scope "
        "文件所有权排他（终局收口任务除外）",
        "可满足性（评审核心判据，机器不可判）：每个 acceptance 锚点在其 "
        "owner 任务的 GREEN 时刻必须可行绿——锚点所在层的全部接口已由该"
        "任务或其依赖链前置任务实现；结构性不可能转绿的锚点是排序缺陷",
    ],
}

DEVON_RGR_CRITERIA_CHECKLIST: dict = {
    "_doc": (
        "判据双投（#172）：你的工件将由 Prism 按 tracks-prism-impl 判据"
        "评审（RED 阶段 = PRISM_RED 工件评审；GREEN/REFACTOR 的实现进入"
        "PRISM_FINAL）——完整判据已随本次派发物化（skill: "
        "tracks-prism-impl）。发射前逐条自检；断言与冻结合同相悖、锚错"
        "文件、fixture 错会被判 red_defect 打回重钉。"
    ),
    "self_check_before_emission": [
        "RED 失败形态合法：全部失败节点一致分类为 assertion_failure 或 "
        "symbol_missing（RED_GATE 合法集合；桩触发必须转成断言守卫）",
        "测试反模式（IMPL-3，逐条对照 skill）：修改断言迎合实现 / 无依据 "
        "skip / 断言降级 / 吞异常 / 过度 mock / 从实现取 ground truth / "
        "捏造硬编码值 / 无效断言——任一条会被评审打回",
        "实现遵循锁定设计（IMPL-1）：不偏离 interfaces.md 声明的外部契约，"
        "不引入未声明的模块边界或依赖方向",
        "可读性与职责（IMPL-2）；命名稳定（IMPL-4：diff 中不得出现带版本"
        "号/时间前缀的文件名）；浅层安全（IMPL-5：eval/exec、硬编码 "
        "secret、SQL 拼接、shell=True + 不可信输入）",
    ],
}
