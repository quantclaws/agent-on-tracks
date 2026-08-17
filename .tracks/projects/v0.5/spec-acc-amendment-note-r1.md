---
doc: spec/acceptance 运营者通道豁免修订说明（review 用）
run: 01KZTHE7RMZE6110PK9C54K1E2
author: operator (aaron) — Human 2026-08-17 晨间指令：「你自己把 spec/acc/design 一路改了……改完 spec/acc 之后，停下来，我找人进行一次 review——你要写出说明」
status: awaiting review
---

# spec.md / acceptance.md 运营者通道豁免修订（r1）

## 1. 为什么有这次修订

run 01KZTHE7（v0.5 M-IMPL）T-017 期间发生两次 over_reach 误判 + 一次 regression 假阳性，Prism DIAGNOSE 四路排除后定性 **spec_gap**（seq 1267）：

> over_reach audit falsely attributed an operator-authored v0.6 planning file to Devon; Devon's reported diff_ref contains only in-scope ci.yml … FR-0233 ACs are closed — the root cause is that v0.5 FR-0170/FR-0237 define no operator/OOB-channel exclusion.

即：现行审计合同把「dispatch 窗口内工作区出现的任何新文件」一律归因 Agent，无运营者通道语义。事件链：

| seq | 时间 (UTC) | 事实 |
|---|---|---|
| 1261 | 08-16 15:04 | regression 假阳性：gate 在主 repo diff 旧 R 提交 c50796f，把运营者基线恢复提交 dcec932 的 5 个 tests/ 文件判为「R unit tests changed」 |
| 1264 | 15:12 | Devon outcome 的 audit_evidence 误标 `over-reach: .tracks/projects/v0.6/pre-story.md`——该文件是运营者 15:13 部署的 v0.6 规划文档（mtime 晚于 Devon outcome 1 分钟），Devon diff 仅含范围内 ci.yml |
| 1267 | 15:18 | Prism 判 spec_gap → awaiting=rollback → Human 批准回退 M-SPEC（现行规则的唯一合法出路） |

## 2. 修订内容（3 处，全部折叠进既有 ID）

### 2.1 spec.md — FR-0170「manifest 越界审计」段落后追加「运营者通道豁免」块
- 归因对象收紧为「该 dispatch 可归因的变化」（快照基线 + outcome 差量）
- 三类豁免：① 非 dispatch 窗口部署且被快照吸收；② `Tracks-OOB` trailer 提交引入；③ 运营者文档路径（`.tracks/wiki/`、`.tracks/projects/*/v0.*/`、`.tracks/runtime/handoff-*`）下 manifest/outcome 均未声明的新增文件
- 豁免留痕：audit_evidence 记 `operator_channel_excluded` + 路径清单
- tests/ 吸收：运营者提交触及冻结 R 树时由下一次 M-IMPL baseline 冻结吸收 r_tree_identity
- 边界保留：窗口内落盘且不属豁免的写入仍 fail-closed → DIAGNOSE 排除责任 → spec_gap/ac_gap 路由（本次回退路径即其落地）

### 2.2 spec.md — FR-0237 段落末追加一句
「Agent 可归因变化」的判定遵循 FR-0170 豁免——运营者通道变化不触发整回合原子拒绝。

### 2.3 acceptance.md — AC-FR0170-03 追加两条验收
对应 2.1 的豁免行为与 tests/ 吸收行为，各附 run 01KZTHE7 实证。

## 3. 明确未改动的部分（与理由）

- **FR/AC 编号零新增**：修订全部折叠进 FR-0170、FR-0237、AC-FR0170-03 既有文本 → trace/AC 覆盖矩阵无增量 → **tasks.json 无需改动**（18 任务定义、AC 引用、DAG 均不变；T-017/T-018 照旧）
- **不引入新事件类型/新 CLI**：`oob.accepted` 事件与 trailer 归因机制属 D-36 深版，留 v0.6 立项；本次只定义语义
- **不改 architecture/interfaces/test-plan**：待本次 spec/acc review 通过后另行修订，再单独 review

## 4. 实现状态对照（review 时请核对）

| 条款 | 现有代码 | 状态 |
|---|---|---|
| 豁免①（快照吸收） | audit.py baseline/pre-dirty 快照机制 | **已满足**（一直如此；两次事故均为违反窗口纪律的窗口内写入） |
| 豁免③（运营者路径） | audit.py `modified_files()` 无路径排除 | **待补丁**（小改：路径前缀排除 + `operator_channel_excluded` 留痕；review 通过后、重启 loop 前部署） |
| tests/ 吸收 | M-IMPL entry 重置 r_tree_identity，BASELINE 从当前 tests/ 重新冻结 | **已满足**（自愈路径，无需代码） |
| 豁免②（trailer 归因） | 无 | **v0.6**（D-36 深版，本次只定语义） |

## 5. 建议 reviewer 关注的问题

1. 豁免③的路径清单是否完备/过宽？`.tracks/projects/*/v0.*/` 含未来版本的规划文档——是否应收窄为「未激活版本目录」？
2. 窗口内写入仍 fail-closed 的边界是否可接受？（替代方案：深版 git 归因可区分 provenance，但引入 trailer 伪造面与实现成本）
3. tests/ 吸收依赖「下一次 M-IMPL baseline 冻结」——若运营者提交发生在 M-IMPL 进行中（无重入），吸收时点是否需要更早？
4. 与 FR-0237 的交叉引用措辞是否足以消除「一切新增文件归 Agent」的旧读法？
5. 【保留或删除：tests/ 吸收条款】用户裁定「tracks 运营者搞出来的矛盾，应该由 tracks 运营者来解决」。本事故触发者（运营者对运行中 runtime 做手术）是纯狗粮场景；宿主侧该场景属罕见的合同违约/人为失误类（v0.5 单写者合同本禁止运行期编辑）。选项 A（保留，建议）：零代码、零用户面，仅陈述既有周期机制对 OOB tests/ 变更的后果，使命中时恢复路径有界（吸收），而非 spec_gap 级联；选项 B（删除，与裁定最一致）：spec 对 OOB tests/ 编辑保持沉默，命中时按未定义行为走诊断路由。另：正文时间线中「T-015 冻结 R」为时间戳标注（T-013/14/15 分别为 live 证据/评论 quarantine/release 检查任务），R 冻结系 M-IMPL 入口机制，非 T-015 引入，已澄清。

## 6. 后续（本次 review 通过后）

1. 修订 architecture/interfaces/test-plan 三件套（同样外科手术式，单独 review）
2. 部署豁免③代码补丁（audit.py，含单测）
3. 重启 loop：M-SPEC 起 confirm-pass 重流（文档已就位，各阶段为确认性通过）→ M-IMPL 重入 → T-017 → T-018 → ISLAND_GATE_2 → v0.5 M-IMPL 收尾
