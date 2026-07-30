---
story_id: S-002
created: 2026-07-30
status: draft
sha:
---

## 原始输入

为 trac 增加需求追踪语法与两个检查工具（trace / reach），使"每条需求可追到测试函数、每个生产模块可达自入口点"成为机器可验证的事实。v0.2 自身豁免 ID 纪律；一旦 v0.2 完成，从 v0.3 起这条追踪链对 tracks 自己的开发变为阻塞门禁。

## 用户意图

- 开发者在 story/spec/acceptance 三文档中用统一语法声明 BS / FR / NFR / AC 编号；**文档即真相源**，不设独立注册表（注册表会与文档漂移）
- **编号文法沿用 louke，不另起炉灶**（Human 裁定）：
  - `BS-01`：两位，按核心操作路径顺序统一编号，不按路径分组
  - `FR-0010` / `NFR-0010`：四位；初稿按 100 间隔预留，首轮评审后按 10 间隔插入，二轮评审后连续编号
  - `AC-FRXXXX-YY`：全称 = AC + FR 四位编号 + FR 内两位序号（YY 从 01 起，跨单元不复用）；所有交叉引用一律用全称
  - acceptance.md 是 AC 中央登记处；spec.md 只留 FR/NFR 描述与元数据
- ID 一经分配不可变、不可复用；删除的 ID 留 tombstone 记录（事件日志与历史证据会引用旧 ID）
- 测试函数通过 pytest marker（如 `@pytest.mark.ac("AC-FR0010-01")`）绑定 AC；无测试绑定的 AC 与指向不存在 AC 的测试都能被机器指出（louke 用 docstring 首行约定，trac 升级为可被 pytest 机器枚举的 marker，ID 文法不变）
- `trac check trace` 对 BS→FR→AC→test 全链做双向孤儿检测：FR↔AC、AC↔test 为硬错误；BS→FR 为 warning-only（行为种子与 FR 不总是 1:1，硬约束会逼人写凑数 FR）
- `trac check reach` 从声明的入口点（pyproject `[project.scripts]`、`__main__`、显式白名单）做**模块级 import 可达分析**，报告从任何入口都不可达的生产模块（孤岛）——louke 尸检中 79/117 幽灵模块的直接对策
- 两个工具既可 CLI 手跑，也可被引擎当 verdict 来源调用：输出人类可读与 JSON 双格式，退出码语义稳定
- 工具只报告、不改写：发现问题时列出证据与位置，修复由作者完成

## 核心操作路径

1. 阅读 `wiki/trace.md`，了解 ID 文法、章节位置约定、不可变与 tombstone 规则（文法承自 louke templates：story/spec/acceptance 三模板）
2. 按更新后的 templates 撰写 story/spec/acceptance，在规定章节声明 BS-XX / FR-XXXX / NFR-XXXX / AC-FRXXXX-YY
3. 写测试时用 `@pytest.mark.ac("AC-FRXXXX-YY")` 绑定验收标准
4. `trac check trace`：解析三文档 + 测试 marker，输出孤儿清单（无 AC 的 FR、回指失败的 AC、无测试的 AC、无主 marker）；`--json` 供机器消费
5. `trac check reach`：构建 import 图，从入口点集合遍历，列出孤岛模块；`--json` 供机器消费
6. v0.3 起：tracks 自身开发把两个检查作为合入前硬 gate；M-ACC 引擎阶段（v0.3 交付）落地时将 trace 检查挂入逐轮 validate

> 豁免条款（Human 裁定）：v0.2 自身的 story/spec/acceptance 不做 ID 追踪；v0.2 期间两个工具只对 fixture 项目和 tracks 源码运行，验证工具本身。

## 行为种子

| 行为                     | 验证断言（将来用什么事实验证我）                                                                     |
| ------------------------ | ---------------------------------------------------------------------------------------------------- |
| ID 唯一性                | 文档中出现重复的 FR/AC 编号时，trace 检查失败，并指出冲突双方的文件与行号                            |
| 编号文法合规             | 出现不合 louke 文法的编号（如 AC 交叉引用未用全称 AC-FRXXXX-YY）时，trace 检查失败并指出位置         |
| ID 不可复用（tombstone） | 复用已删除（tombstone）ID 时检查失败；tombstone 本身不被计为孤儿                                     |
| FR↔AC 双向覆盖           | 存在无 AC 的 FR，或回指不存在 FR 的 AC 时，检查失败并列出完整孤儿清单                                |
| AC↔test 绑定             | 存在无测试绑定的 AC，或 marker 指向不存在 AC 的测试函数时，检查失败                                  |
| BS→FR 弱链接             | 无 FR 承接的 BS 只产生 warning，不改变退出码；warning 内容包含 BS 编号                               |
| 孤岛检测                 | 存在从任何声明入口点 import 不可达的生产模块时，reach 非零退出并列出模块名                           |
| 测试模块豁免             | 纯测试模块（tests/ 下或仅含测试的模块）不计入 reach 分析，不产生误报                                 |
| 入口点声明               | 入口点来自 pyproject scripts、`__main__` 与显式白名单三处；无任何入口声明时 reach 报错而非静默通过   |
| 机器可消费               | 两个检查均支持 `--json`；同一输入多次运行输出完全一致；退出码 0=通过、非 0=有硬错误                  |
| 自举纪律                 | 对 tracks 自身源码运行 reach，结果为零孤岛（arch §6：从第一个 commit 起对自己跑）                    |
| 只报告不修改             | 任何检查运行前后，被检查项目的文件内容无任何变化                                                     |

## 范围排除

- 不做 `trac check ratio / dup / budget`（后续 story 引入）
- 不做函数级调用图可达分析（v2；本版只做模块级 import 图）
- 不实现 M-ACC 引擎阶段（推后到 v0.3；本版 acceptance.md 以 bootstrap 方式撰写，用独立工具校验）
- 不做 GitHub Issue 与 ID 的映射
- 不支持非 Python 宿主项目
- 不做自动修复/自动重编号（工具只报告）
- v0.2 自身文档豁免 ID 追踪（见核心操作路径的豁免条款）
- 不接真实 LLM Agent（延续 v0.1 排除）
