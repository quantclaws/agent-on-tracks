---
envelope: tracks-envelope:v2
name: tracks-shield
version: 0.1
description: Shield 集成/e2e 测试编写方法——test-plan→测试资产、合法 Red 自检、counterexample、WRITE/SHIELD_FIX 分流。当 Shield 按 M-TEST WRITE 全量编写或按 M-IMPL SHIELD_FIX 定点修复时使用。
---

# tracks-shield：Shield 测试编写方法

本 skill 详载 Shield 的编写方法；身份、authority、阶段路由与条件式 envelope 合同在 core，输出格式以 assignment 为权威。本 skill 不复制 manifest schema、不固化宿主框架/命令/路径——一切具体取值来自 assignment / project contract。

## 1. 逻辑层与宿主布局

- 逻辑 integration/e2e 两层是合同层标识：integration 覆盖跨模块接口合同的忠实性与错误语义；e2e 仅覆盖面向用户的主成功旅程（happy path），边界/错误情形一律归 integration。
- 实际落盘路径、命名与目录布局由 assignment / project contract 的 layout 声明；不假设宿主语言或测试框架，不发明合同外路径。
- 测试数据离线可复现（数据资产路径由 layout 声明），敏感数据不入库。

## 2. test-plan → 测试资产方法

1. 从 test-plan 建立覆盖矩阵：每条归属 integration/e2e 的 AC → 观察出口（interfaces 合同）→ 变绿条件（桩 token / 合同归属）。矩阵中的任何缺口（AC 无出口、跨模块接口未覆盖、层归属矛盾）先返回 advisory，不猜。
2. 逐接口写 integration：经接口桩的公开入口进入，覆盖 happy + 关键错误/边界路径；错误路径的期望行为以合同条款为准。每条跨模块接口以 interfaces 合同的 modules 列为清单（含 2+ 模块的条目），只读该列，不自行推断模块边界。
3. 逐主旅程写 e2e：只写 happy path；从合同声明的交付入口进入，断言落在用户可见结果与合同出口上。
4. 断言值与预期行为只从合同推导（AC / interfaces / test-plan，或经合同声明的 ground truth 在运行期计算），不从任何代码输出抄写，不硬编码拍脑袋的期望值。

### 锚点可满足性三条硬规则（M-TEST 冻结前静态检查，违反即拒绝冻结；M4，收敛改革 2026-09-05）

三条规则的共同背景：walk/repair 会**合法地**产 commit 与事件——对移动目标的相等期望在重放/修复窗口内结构性不可满足。T-042 的 :78（walk 前锚定 HEAD→错树误诊、约 12 次空转）与 :150（计数相等↔rewalk 互斥、ping-pong 烧穿 budget）均在这三条规则下于出生时被拦下。

1. **移动目标禁相等期望**：walk 驱动的 CLI 半不得对 `rev-parse HEAD` 捕获值或事件计数快照做相等断言（`len(after) == len(before)`）。Runtime 在 M-TEST WRITE 与 SHIELD_FIX 提交前执行 anchor_static 静态检查（tracks/checks/anchor_lint.py），命中即拒。真正的稳定性契约（幂等不重冻等）必须用**判别器**承载（如修复溯源 trailer 区分 drift 与 repair），不得用裸计数相等。
2. **CLI 半断言三形**：事件存在性（`assert events_of_type` / `any(...)`）、payload 谓词（事件字段的谓词断言）、status 渲染（CLI 输出的渲染断言）——三形之一。HEAD 锚定必须在断言邻域内捕获（walk 之后、无运行时活动间隔），不得跨窗口携带。
3. **互斥锚点拒冻**：同一运行时路径被两个锚点断言相反结果（一禁一允）= 互斥，任何实现都不可能同时满足。冻结前自查本批测试与既有冻结锚点的运行时路径；发现互斥立即回报 gap，不提交。运行时的 S1 振荡检测器是事后兜底，不替代本出生检查。

抑制逃生口：确有合同依据的稳定性断言可在该行追加 `# tracks-anchor-ok` 注释通过静态检查——该注释进入 diff，Prism 评审有权挑战；它是自我声明的例外，不是常规手段。

## 3. trace 标记语义

- 每条测试函数上方写标记注释行，声明该测试绑定的 AC；同一函数绑定多条 AC 时每条 AC 各占一行。
- 标记必须含 Runtime trace 扫描器的特征词（`TRACKS-TRACE`）——缺特征词的 AC 引用对扫描器不可见，等价于无标记。
- 标记的具体语法、位置与文法校验以 assignment 注入的合同为准；本 skill 只固定语义：AC 与测试用例的绑定关系必须可被 Runtime 程序校验复原。

## 4. 合法 Red：分类与自检执行

### 分类

- 合法 Red：行为断言失败、桩合同 token 抛错（桩只抛合同未实现标记，不实现业务）、合同声明的 symbol 缺失。
- 非法 Red：collection 错误、语法错误、fixture 错误、测试侧 import 错误——测试自身有缺陷，交付前自行消除。
- 测试意外通过是异常：桩只抛错时通过意味着断言空洞、mock 掉了被测对象、或测了别的东西——修好它，不收下这份绿色。

### 自检执行（两步，outcome 前必做）

- **第一层·全量 collection**：按 run contracts 对全部测试（继承与新增节点）执行 collection，无 import/语法/fixture 错误；变更的 support/fixture 同样参与 collection 验证。
- **第二层·选集执行**：只对当前版本新增/修改的测试节点执行（选集构成与执行命令由 run contracts / assignment 声明），逐条确认失败是合法 Red——失败栈落在行为断言或桩合同 token 上，而非测试自身的装配问题。抽掉被测调用或断言后测试应失去意义；一条测试若删掉断言仍"通过原样"，说明它什么都没测。
- 绝不把普通全量套件当自检执行手段：未变的历史节点不在本角色自检执行范围（其回归归 M-IMPL 全链与 CI）。把全量套件当 Shield 自检是合同违规。

## 5. Counterexample

对每条 required 测试构造一个只偏离目标合同的最小行为补丁（例如把某出口的错误语义改成合同之外的行为），按合同声明的方式临时应用后运行该测试：

- 补丁只偏离目标合同条款，不夹带其它变更。
- **killed**：测试按预期失败 → 断言有区分力；恢复工作区，记录 killed。
- **survived**：测试仍通过 → 断言空洞或没命中合同，修测试后重验。
- 验证后必须完全恢复你本次应用的 counterexample 补丁与临时变更；派发时工作区已存在的未提交内容（如评审 verdict）不属你的产物范畴，禁止 reset/checkout/clean 它们。补丁与 kill 记录存放在合同声明的证据路径（layout 声明），不进入产品代码。

## 6. 派发首步：evidence 与模式分流

failure evidence 以 **assignment 为权威**：Runtime 注入的 evidence 字段形态、校验与词汇一律按 assignment schema，本 skill 不固化字段名。每次派发开始时第一步读取 evidence 并分流：

- **存在任意未决 finding（代码类评审 finding）→ SHIELD_FIX 定点修订**：本轮不是全量编写。禁止全量盘点测试树、禁止重新映射全部测试任务；只读 finding 指名的测试路径及其直接依赖，修复后运行定点 contract，在 outcome 中逐条回应（引用 finding id，说明修复动作与验证证据），然后返回。SHIELD_FIX 的来源是 M-IMPL 的 SHIELD_FIX 重派——只修复被诊断为缺陷的测试，不动其它测试与产品代码。
- **无任何未决 finding 的首次编写 → WRITE 全量编写**：按第 2 节执行完整覆盖矩阵盘点与编写，完成后做第 4 节自检。
- **非首次编写且无未决 finding**：不重做全量盘点——资产已就绪，直接返回 manifest。
- **finding 已在 HEAD 满足且无合法 diff → 立即返回 gap**：声明 finding 已在 HEAD 满足、本轮无对应写动作；不循环探索、不制造 no-op diff、不为凑变更重写已合规的资产。

定点修订与全量编写完成后均须重做第 4 节自检。

### 文档类 finding 与讨论协议

- 代码类 finding 不写文档线程；仅当评审方确实锚定了文档线程（文档类 finding），才按注入的讨论协议（`tracks-discuz` skill）就地回应。评审方发起的线程由评审方设 resolved，你不得代为操作，不手工编辑 blockquote。
- 设计文档正文不可修改：发现设计缺陷（分层缺失、IF 注册遗漏、AC 无出口）→ 返回 gap advisory（interfaces/设计缺口 → M-DESIGN；AC/需求缺口 → M-ACC/M-SPEC），由 Runtime 路由回退，不自行修改设计文档"修复"缺陷。

## 7. 返回 manifest

- 最终回复的 manifest 形态严格以 assignment 注入的 `manifest_contract` 为权威：字段、必填项、路径文法与返回前自检按其执行；本 skill 不复制 manifest schema、不示例化 JSON——assignment 是唯一出处。
- manifest 中的路径必须与实际落盘文件一一对应：引用不存在的路径或遗漏已写文件都判 malformed；manifest 与建议 commit message 只是提议，Runtime 验证后执行 commit。
- 逐条回应 finding 时引用其 id 与修复证据；不做与 finding 无关的顺手修改。

## 8. 命令与工具纪律

- 一切 collection / 测试 / counterexample / 静态检查命令来自 `assignment.commands` / run contracts / project contract——不假设语言、虚拟环境、测试框架或并行参数，不发明命令，不选择新框架，不新增合同外依赖，不改 run contracts。
- bash 仅用于合同声明的读取、collection、选集执行与 counterexample 验证；执行结果只是自检，以 Runtime 复跑为准。
- 不 commit/push、不推进阶段、不运行流程命令、不委托 task/subagent、不向 Human 提问。

## 9. 程序性自审清单（SHIELD-ID）

outcome 前逐项实际执行（动作 + 判据引用）；任一为"否"先补齐再退出：

- SHIELD-A1：覆盖矩阵完成——每条 integration/e2e 归属 AC 有对应测试且标记含 trace 特征词（core SHIELD-Q1）。
- SHIELD-A2：跨模块接口（modules 列 2+）均有 integration 覆盖，happy + 关键错误路径（core SHIELD-Q1）。
- SHIELD-A3：e2e 严格限定 happy path，边界/错误已划入 integration（core SHIELD-Q1）。
- SHIELD-A4：全量 collection 通过，选集执行失败全部为合法 Red，未把普通全量套件当自检（core SHIELD-Q4）。
- SHIELD-A5：断言全部落公开出口，经被测接口公开入口进入，无内部状态窥探（core SHIELD-Q2）。
- SHIELD-A6：每条 required 测试有 killed 的 counterexample，且你的临时变更已恢复、未触碰派发前已存在的未提交内容（core SHIELD-Q3）。
- SHIELD-A7：无作弊模式——空洞断言、无依据 skip、断言降级、吞异常、过度 mock、抄实现输出、硬编码期望值均不存在（core SHIELD-Q5）。
- SHIELD-A8：写域合规——产物只在合同声明的测试资产路径，manifest 与落盘一致（core SHIELD-Q6）。
- SHIELD-A9：锚点可满足性——CLI 半无移动目标相等期望（规则 1）、断言为三形之一（规则 2）、无互斥锚点（规则 3）；anchor_static 静态检查零违规，或每处抑制注释有明确合同依据（M4，收敛改革 2026-09-05）。

## 10. 边界（Shield 特有）

- 不实现产品代码（M-IMPL 实现者的职责）、不修改接口桩（M-DESIGN 的冻结产物）、不编写单元测试、不修改 ground truth 脚本。
- 不把不经被测接口进入的测试当集成测试；不写边界/错误路径的 e2e。
- 不在 SHIELD_FIX 之外触碰已冻结测试；SHIELD_FIX 也不动产品代码。
- 不判定 PASS、不 commit/push、不推进阶段、不把"本地跑过"当交付证据。
- 本 skill 与评审方的测试判据互补：本 skill 讲"如何写"，评审判据讲"如何判"；评审词汇与 classification 以 assignment 注入为权威，不在此复述。
