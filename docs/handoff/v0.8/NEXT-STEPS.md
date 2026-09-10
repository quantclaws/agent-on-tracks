# 后继实施顺序与任务边界

这里列的是工作包，不是正式 task/AC 的替代。先从 `.tracks/projects/v0.8/acceptance.md` 和 test-plan 为每包定位所有相关 AC ID、完整子句与测试节点；不凭文件名猜测完成度。可由低成本 Agent 分别实施独立包，协调者控制共享文件合入并独立验收。

## 1. 复核并完成六面 parity（当前最接近的小切片）

先运行 verify-parity.py 确认 Micro-1 的实际结果，阅读候选与 root 的差异，只移植审核通过的函数。重点文件：kernel/envelope.py、effects/dispatch_parity.py、effects/backend.py、effects/opencode.py、effects/fake.py、Executor dispatch wiring，以及实际 agent/skill/template 资产。

下一小任务只完成 Runtime 完整门禁：实际材料准备完后、外部 Agent 调用前校验；成功事件仅一次并持久化实际消费文件的 path/hash/token；静态检查成功不能先记为完整 dispatch.parity。失败不 spawn、不写成功 outcome，不留下篡改后的用户文件。需覆盖 agent、skill、template 与 fake_backend 篡改以及正常路径。不能把 expected token 写进日志当实际读取证明。

再处理六类完整覆盖：默认 prompt/agent/skill/fake_backend/real_backend/validator 均必需；prompt digest 不是版本声明；没有必需材料应 fail closed；动态 artifact map 不得悄悄绕过类别完整性。Fake 必须走实际可审计材料契约，不能伪造完整 provenance。

最后独立审核 writer/authority schema、声明缺失/错型/冲突、`TRAC_ENVELOPE_DECLARE=0` 的正式兼容边界。Micro-1 的显式 legacy opt-out 只是候选实现，不代表正式全部兼容要求已关闭。验收通过四文件 17 checks 后还要跑旧 backend/envelope regressions，审阅每项 AC，不凭 17 绿宣布模块全完。

## 2. 把已有 FULL_F → Prism → Preview → Authorization 分层验收提交

按 CURRENT-STATE 对应证据取 hunks，先在 clean HEAD 候选跑相应 unit/integration，再跨层回归；不能把混合 Executor/CLI 整文件提交。复用过去的工作而非重写。与 parity 修改 opencode/envelope/Executor 冲突时由协调者串行合并。

## 3. 完成发布 effects 与 Runtime（独立设计、可交另一个 harness）

边界：effects/git.py、effects/github.py、executor/publish.py、publish_runtime.py 及明确 wiring。先定位 `push_merge`、create_release、upload_artifact 的 NotImplemented 与 Runtime op allowlist。tag batch 已实现，但 merge/release/artifact 尚缺。

实施前锁定 approved preview/candidate/target/result 身份，覆盖真正远端（测试可用 local bare git + controlled API service）、失败/重试、每 op WAL、崩溃后 readback reconcile、已完成项不重复、不支持操作全计划预检且零副作用。分叉远端不能静默 FF-only 跳过，也不能引入未验收 merge 产物。具体 merge policy 以正式 spec 裁定；预览变化重新走授权。完成条件包括 artifact bytes/hash 与真实远端 release/tag/commit 的绑定，非 mock 返回 success。

## 4. 关闭证据与门禁缺口（可独立分小包）

- `guard_registry.py`、host_contract/version_decl：移除不符合正式契约的 skip/default-true，版本化配置、原始 bytes digest 与实际执行结果一致；config drift 先修正确资产/声明，不能重算假 hash 掩盖。
- FULL_F/source authenticity 与 canonical security：真实执行、候选身份、有效来源、结果完整性；`test.selected` 或手写 full.executed 不能替代执行。过期/缺失/错 candidate/错 scope/畸形聚合需拒绝。
- `checks/trace.py`、test-plan/实际 test run：AC ID → 具体 test node → 实际执行与完整行为断言。空 trace warning PASS 不足以发布。
- RGR/test isolation：验证实施者无法访问冻结验收文件及 Git 历史/共享路径；分别留真实 RED 与 GREEN 的实现/test hash 和执行记录。共享账号目录加角色提示不是物理隔离。

## 5. 修复旅程前提，再完成真实生命周期

`data/obsolete-stub-assertion-audit-2026-09-10.json` 定位 17 个函数、23 个旧 expected NotImplementedError 块。先修有效 bootstrap（IF registry/extension/config/workflow 资产及阶段条件），保留原 AC 行为断言，再让真实 Runtime 到目标路径。不能删 stub guard 就宣称验收通过；也不能把生产代码改回 stub。

沿第一失败事件定位 M-DESIGN/M-IMPL 等上游阻塞，不要为下游每个缺事件分别打补丁。Devon 3 条旧 escalation 数量断言要对照明确的 M1-S3 policy（2→RULING、5→human）复核，不能无依据改变策略。

补齐 milestone/issue mapping & closing/ref cleanup，验证 Tracks 和 reference host 各 feature/post-release/dev 共六条真实旅程。正确权限缺失需要可解释的阻塞，不能产出 FAKE issue 当 real evidence。最终 live CI、发布、远端 readback 仍需真实验证；本地 standin 不替代这些要求。

## 6. 质量收口并发布

先按 coverage priorities 与真实需求缺口加行为测试，同时继续拆 Executor/m_impl 等超长模块。machine 与 docgap 已有成果不能重复改。每次拆分职责清楚、显式窄依赖、实际移走旧实现，禁止仅增加转发层、复制方法或巨型 host/mixin。

失败分类稳定、整树候选冻结后才做一次完整质量测量。全部 AC 证据、全套测试、coverage≥95%、长度/复杂度/重复检查与六旅程通过才推进 release；保存 exact commit/contract/evidence/remote identity。任何修复改变候选需重验受影响证据，不得沿用陈旧 preview/authorization。
