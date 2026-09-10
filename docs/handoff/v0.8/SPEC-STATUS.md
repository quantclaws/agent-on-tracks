# 按 SPEC-008 对照：已做什么、还缺什么

核对日期：2026-09-10；产品代码为 478bc17 加 `data/state.json` 所列 WIP。本次是正式 spec、当前源码与既有证据的对照，未重跑全套。正式范围为 21 FR（0267–0287）、7 NFR（0143–0149），共 81 AC。

必须区分：**实现缺口**是当前源码能具体指出的缺失或错误；**验收缺口**是已有代码尚未被完整场景证明。不能把后者全部算成未实现，也不能把已有函数或局部绿算成整条 FR 完成。当前没有完成全部 21 FR 的逐条最终认证，不能可靠给出“已完成 X/21”。

## 接手期间实际完成的工作

| 工作 | 对应需求 | 落地状态 |
|---|---|---|
| CI API 回读校验 repo/workflow/run/head/required checks，恢复身份检查 | FR-0270 | 026d75a 已提交；受控 API 定向验收通过，真实双宿主旅程未完成 |
| 单 tag 真实远端对象校验、Runtime 授权与逐操作 WAL；再扩展有序多 tag、全计划预检、失败恢复 | FR-0275 / NFR-0144 的 tag 子集 | f79e19c、3d139bb、314d413 已提交；merge/artifact/release 未随之完成 |
| local gate evidence 包含结果、合同及 gate 身份，恢复时核对完整当前集合 | FR-0269 的 evidence 子集 | 478bc17 已提交；特殊 source gate 的跳过仍未解决 |
| FULL_F 真实执行结果与四元身份复用/重跑；Prism 同 candidate final、结构化返回和 discuss 锚定 | FR-0268、FR-0271 | 有定向验收，仍在分层 WIP；未完成全发布链真实性证明 |
| preview 真实 artifact/contract/plan/blob digest；Human 发布决策的 candidate/stale/gate 拒绝 | FR-0273、FR-0274 | 有定向验收，仍在分层 WIP；上游证明和全部分支未最终认证 |
| declared raw reply 保留、backend 到 Runtime 传播、transport failure 与格式错误区分 | FR-0278/0279 子集 | 32b81c2 已提交；不是所有角色的统一 schema 完成 |
| parity 的默认六面与声明身份修正 | FR-0279 子集 | 外部候选未合入；独立 17 checks 为 13 pass / 4 fail |
| 外部 machine.py 重构审阅合入；doc-gap 19 方法实际拆出；anchor 测试包导入修正 | 工程质量及验证基础 | a4a63b5、0a95893、c8bbf79；不是新增 FR 完成数 |
| AC-ID 节点盘点、隔离 coverage 纠正、失败分类、交接与临时文档清理 | 验收与交付基础 | 建立了可信历史基线与可恢复交接；不是 v0.8 发布完成 |

## 21 条 FR 的具体剩余工作

| Spec | 当前已有 | 还缺什么；证据性质 |
|---|---|---|
| FR-0267 candidate 冻结 | clean/dirty 检查、完整 SHA、事件及漂移处理 | **验收缺口**：有效 M-IMPL FULL_F → M-VERIFY 公共入口、status/replay，以及整个后续链使用同一 SHA。旧测试部分只调 helper，walker 停在 M-IMPL，不能证明完整边界。不是说 freeze helper 没写。 |
| FR-0268 FULL_F 复用 | 真实 producer、四元身份、reuse/rerun、stale 消费有定向验收 | WIP 分层提交；**验收缺口**：修复/回拨真正产生 stale 后不复用、恢复不重复，与完整 M-VERIFY 链组合；全来源真实性未关闭。 |
| FR-0269 本地验证 | 合同解析、命令执行、normalized evidence、完整集合恢复检查 | **实现缺口**：guard_registry 无 lint 命令时 continue；version_decl 直接 continue。缺声明的合同还有 Runtime default 路径。需符合正式缺失即阻断、Archer 声明的语义，覆盖质量/trace/reach/反 slop/版本/build/artifact/smoke 全集合。 |
| FR-0270 required CI | repo/workflow/run/head/check 绑定、失败和恢复已修 | **验收缺口**：Tracks 与真实 reference GitHub 的 required CI、凭据恢复及完整发布链；不能将受控 API 验收升级为真实旅程完成。 |
| FR-0271 Prism final | 同 candidate 输入、结构化 verdict、scope 与 discuss 发现锚定有定向验收 | 分层落地；**验收缺口**：实际角色消费完整来源有效的 FULL_F/local/CI/policy 后再放行或阻断，及修复后新 candidate 重走。 |
| FR-0272 安全扫描/深审 | 执行 scan 命令、失败聚合、security.assessed 已存在 | **实现/验收缺口**：正式 policy 版本/阈值/normalized result 的完整绑定及实际深审消费。当前 assess handler 主要执行 scans 并聚合，不能据此证明深审完成；实际阶段测试在历史基线亦失败。按声明策略执行，不新增 spec 排除的架构级安全门。 |
| FR-0273 preview | artifact/contract/plan/evidence bytes 聚合和 stale 检查有定向验收 | 分层落地；**依赖/验收缺口**：所有上游真实证据、Known Issue 完整列表、CLI/report 全字段及新证据/计划变化后的重生成；preview helper 绿不证明上游真实。 |
| FR-0274 Human 三择一 | release 授权的 candidate/preview/gates 校验有定向验收 | 分层落地；**验收缺口**：release/delay/return 全分支身份/stale/状态转移，approve/GitHub comment 不可绕过；安全等上游未闭合。 |
| FR-0275 外部发布 | 单/多 tag 真正远端 readback、WAL、重试 | **明确未实现**：effects/publish.py 的 push_merge、create_release、upload_artifact 仍抛 NotImplementedError；Runtime 执行仅完成 tag 子集。还需三类操作权限、逐项 reconcile、冲突/崩溃行为。无需泛化到多 registry/多 deploy 平台。 |
| FR-0276 归档闭环 | trace/close/seal/clean helper 与事件 wiring | **明确实现缺口**：trace 的 artifact/evidence/release_tag 尚未完整填充或做同一性证明；seal helper 只返回 readonly=true；clean refs 只列举而不删除并返回 remaining=0；恢复若仅见 trace_closed 就返回，会跳过中断后未完成收尾。需要真实封存、清理、逐项恢复。 |
| FR-0277 三类发布 | feature/post-release/dev 场景和操作计划代码 | **实现+验收缺口**：受 FR-0275 阻塞，feature 完整公开发布、post-release patch 与活跃 release 分支同步、dev 只交付预发布且无公开 tag/release 未完成真实旅程。 |
| FR-0278 统一 envelope | declared 回复收集与 Runtime validator 路径修复 | **实现/验收缺口**：所有角色和 assignment/evidence/outcome/verdict kind 的 schema 全覆盖、去除残留启发式回退、样本由 schema 生成或经真实 validator 验证。不能只测 Prism 或一类 declared 输出。 |
| FR-0279 六面 parity/畸形 | raw reply/transport 分类已提交，parity 有未合入候选 | **已复现缺陷**：正常路径没有实际材料 path/hash 审计；agent/skill/template 拒绝路径提前写 dispatch.parity 成功。另需实际六类材料完整性、v0.7 malformed 全语料、format 不计 semantic attempt 且不改业务状态。 |
| FR-0280 失败证据链 | failure_review 等既有设施 | **验收缺口**：产生→保留→选择→注入→消费→ACK→失效→replay 的逐环证明。历史基线 5 条相关测试失败；先查有效前提，再只修证实的缺口，不能预设重写 failure_history。 |
| FR-0281 Archer scaffold | 合同/资产物化、执行与校验设施 | **实现/验收缺口**：缺合同被 Runtime default 替代、特殊 gate skip 与 spec 要求冲突；需 Archer 实际声明的完整 pin/config/scope/threshold 及 install→collect→build→smoke，错误证据回流。 |
| FR-0282 新 reference host | 闭集资产复制与旅程形状比较 | **具体不足**：reference_host._materialize 只 mkdir .venv 却返回 install=non-editable，没有在该函数创建可用环境或安装 wheel；需真实安装和独立 GitHub remote/Issue/CI/发布，不能只比较事件名称。 |
| FR-0283 Issue 权威映射 | 真实 create/readback、映射存盘、fake 识别路径已有 | **实现/验收缺口**：现有持久映射未完整保存 repo/id/url/baseline 全身份；create 成功但持久化前崩溃的去重、全消费者只用权威映射、真实与 fake 分离仍需关闭。不是“没有 Issue API”。 |
| FR-0284 Issue/Project 关闭 | Runtime 调 close helper 再发 closed 事件 | **明确未实现真实副作用**：close_issues_with_comment 仅组装 dict，close_project_milestone 直接返回 state=closed，均未执行关闭/评论 API；需真实 API、映射再校验、失败阻断与 readback，不能靠事件声称已关闭。 |
| FR-0285 权威测试流水线回归 | 既有 WRITE→COLLECT→RED_CHECK→PRISM_REVIEW | **验收缺口**：逐环 Runtime RED_CHECK 与 Prism 消费 red.validated 的隔离 kill、缺环阻断。spec 明确只要求回归，不要求重新发明流程。 |
| FR-0286 就地修复/Known Issue | 分类、修复轮次、stale、Known Issue/preview 接口 | **明确实现缺口**：Known Issue 用进程内列表生成编号和固定 acme/host URL，未创建真实 GitHub issue；需真实持久化/恢复、按 run/candidate 隔离、preview 完整性、waiver→下版 backlog/triage。修复新 candidate 重走和预算边界亦有历史失败待复核。 |
| FR-0287 return/abandon | barrier、late outcome quarantine、stale、abandon 代码已有 | **验收缺口**：所有规定源/目标组合、在飞 outcome 不覆盖回拨、不可逆操作显式确认、cancelled 不再推进。历史 escape 有 3 个失败，未完成当前候选回归，不能说整项没实现。 |

## 7 条 NFR

| Spec | 尚未关闭的要求 |
|---|---|
| NFR-0143 全链身份审计 | FR-0267–0276 的全部证据、artifact、Human approval、operation、release 同一性；目前只有子链证据。 |
| NFR-0144 精确一次发布 | tag 有定向证明；merge/release/artifact、并发/远端人工修改冲突与完整恢复尚缺。 |
| NFR-0145 合同解析确定性 | FR-0278/0279 全 kind/schema/六面/畸形回归未完成。 |
| NFR-0146 失败证据审计 | FR-0280 全角色消费、ACK、失效和 replay 未最终验收。 |
| NFR-0147 语言中性/未知阻断 | 缺合同 default、gate skip 和完整 normalized result 需收口；不能以 Python reference 实现替代中性 Runtime 合同。 |
| NFR-0148 权威 Issue/fake 拒绝 | FR-0283/0284 的完整映射身份、真实模式阻断和真实 closers 尚缺。 |
| NFR-0149 双宿主六旅程 | 没有六条真实 GitHub 旅程全部成功证据；受未完成发布/归档/host 物化阻塞。缺凭据只能 needs_attention，不计通过。 |

## 代码证据定位与质量边界

当前源码：`tracks/effects/publish.py:14,120,124`（三项未实现）；`tracks/executor/executor.py:5470,5560`（默认合同/skip）；`tracks/executor/milestone.py:46,69,96,107,113`（trace/关闭/封存/清理）；`tracks/executor/executor.py:5919`（收尾恢复提前返回）；`tracks/executor/repair.py:155`（Known Issue 内存模拟）；`tracks/executor/reference_host.py:91`（仅创建目录的安装声明）；`tracks/effects/github.py:618,638` 与 `executor.py:7836`（Issue 映射）。这些是本轮静态核对，不等于新跑的集成测试。

现有定向证据和完整旧基线见 CURRENT-STATE / VERIFICATION。旧基线 86.788% coverage 不是当前 HEAD；当前物理长度 Executor 7975、m_impl_runtime 5982 行，质量收口仍是大工作。用户要求的 ≥95% coverage、无冗余/超长代码是额外交付约束，不应误说成这 7 条 NFR 的原文。物理测试隔离属于继承质量纪律；自举 Runtime 安全边界、第二语言 adapter、多平台部署和新测试流水线明确不在本 spec 范围。
