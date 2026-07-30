---
test_plan_id: TP-003
spec_ref: SPEC-003
arch_ref: ARCH-003
created: 2026-07-31
status: draft
sha:
---

# Agent on Tracks v0.2 — 测试计划

- **Related acceptance**: `.tracks/projects/v0.2/acceptance.md`
- **Related interfaces**: `.tracks/projects/v0.2/interfaces.md`（断言依据，见 §6.5）

> 延续 TP-001（v0.1）的分层与纪律；v0.2 新增**双通道 E2E**（fake deterministic + live opencode）与 **opencode 外部依赖分层**（§5）。凡未提及者继承 TP-001。

## 1. Stance and Boundaries

### 1.1 黑盒立场

测试只声明**系统外可观察**的方法。可观察对象限于：

- CLI 端点（`trac` 各子命令，含新增 `trac discuss` / `trac validate`）的 stdout/stderr/exit code。
- 持久化数据：`.tracks/runtime/tracks.db` 的 `events` 表（断言主源）、投影表。
- 文档文件：`.tracks/projects/<ver>/{story,spec,acceptance}.md`（含 inline-discussion blockquote）。
- `trac discuss query` 的 JSON 输出（`DiscussQuery`，IF-003 §6）。
- git 状态（分支、提交、工作区 diff）。

### 1.2 非对象（测试不直接依赖）

`discuss/parser.py` 内部数据结构、`OpencodeBackend` 内部 prompt 构造、`audit.py` 内部 baseline 表示、State 内部字段——这些不作为断言落点；需观察者由 interfaces.md 提供出口（§6.5）。

### 1.3 作弊模式与纪律

继承 TP-001 §7 与模板 §1.3/§1.4（CI 强制 AC 追踪、断言禁忌、测试变更分类、testability fallback）。**禁止**"impl 与 spec 不符 → 改测试"。

### 1.4 测试分工

- Unit：实现者（Devon，R-G-R 随实现提交）。
- Integration / E2E：测试负责人（Shield），覆盖 interfaces.md 跨模块合同与用户旅程。
- Ground Truth（§3）：未参与被测实现者或第三方参考。

## 2. 分层策略与目录结构

| 层 | 目录 | 职责 | 频率 |
|:---|:-----|:-----|:-----|
| unit | `tests/unit/` | discuss parser/locate/writer、templating、validate（template/discussion_ready）、后端选择逻辑、events 字段扩展 | 每次保存 |
| integration | `tests/integration/` | OpencodeBackend（fake opencode stand-in）+ audit 协作、discuss+validate 门禁协作、runtime gate（discussion_ready verdict） | 每次提交 |
| e2e（fake 通道） | `tests/e2e/` | deterministic 工作流（继承 TP-001）+ discuss CLI 旅程 + validate 门禁 | 每次提交（CI 必跑） |
| e2e（live 通道） | `tests/e2e_live/` | 真 opencode + env provider/model，断言协议/权限/目标 diff/格式/恢复，**不断言文本** | 缺凭据 skip；CI 独立 required job、本地 opt-in |

```
tests/
├── unit/
│   ├── test_discuss_parser.py     # 全文扫描、canonical/人工写法等价、说明标签不误判、fenced code 跳过
│   ├── test_discuss_locate.py     # L0-L3、fail-closed 歧义（→ ambiguous 不写）
│   ├── test_discuss_writer.py     # canonical 输出、空行分隔、状态权限一致性
│   ├── test_templating.py         # 模板加载（按 kind）
│   ├── test_validate.py           # template 结构校验 + discussion_ready（~ TP-001 test_validate.py 扩展）
│   └── test_backend_select.py     # TRAC_AGENT_BACKEND / TRAC_FAKE_SIMULATE 边界注入（纯函数不感知）
├── integration/
│   ├── test_opencode_backend.py   # fake opencode stand-in：物化、prompt 构造、diff 为产物、失败矩阵、reconcile
│   ├── test_audit.py              # baseline + 后置 git diff、越权 fail/不回滚 Human 修改、临时目录清理
│   ├── test_discuss_gate.py       # discussion_ready 经 validate_document → verdict → decide 门禁
│   └── test_runtime_loop.py       # ~ TP-001（含 outcome 扩展字段）
├── e2e/                           # fake 通道（deterministic，CI 必跑）
│   ├── test_happy_path.py         # ~ TP-001 + Scribe/Sage 真实后端（fake）起草/评审
│   ├── test_discuss_cli.py        # query/start/reply/edit/set-status 旅程 + blocker 三类别 + check-ready
│   ├── test_validation_gate.py    # M-START 不校验 / outcome 即校验+重派 / 门禁再校验 / trac validate 独立
│   ├── test_over_reach.py         # 越权检测（edit/bash 越界）→ outcome failed / 不提交 / Human 修改不被覆盖
│   └── test_recovery.py           # ~ TP-001 + opencode 失败矩阵 reconcile（物化/临时目录清理）
├── e2e_live/                      # live 通道（真 opencode，缺凭据 skip）
│   └── test_live_agent.py         # 真启动/权限/JSON 协议/目标 diff/格式/恢复，不断言文本
├── ground_truth/
│   └── discuss_reference.py       # inline-discussion 解析/定位参考实现（不 import tracks）
└── conftest.py                    # ~ TP-001 + fake 后端强制（fake 通道）/ live opt-in（live 通道）
```

比例目标延续 TP-001：unit ≈ 50%、integration ≈ 30%、e2e ≈ 20%。

## 3. Ground Truth（discuss 算法正确性）

inline-discussion 解析与 4 级降级定位属"规则/算法正确性"，须用独立参考（模板 §3）：

- `tests/ground_truth/discuss_reference.py`：**不 import `tracks.*`**（CI 静态检查），仅标准库 + `tests/assets/`  fixtures。
- 解析 ground truth：手工标注的 markdown fixture（含 canonical/人工写法、说明标签、fenced code、嵌套回复），参考实现独立产出 thread 集，与 `discuss/parser.py` 输出比对。
- 定位 ground truth：构造行号漂移/anchor 微调/重写/并列候选 fixture，参考实现给出预期 L0-L3 命中或 ambiguous/not_found，与 `locate.py` 比对（**重复 speaker/根文本 → ambiguous，文件逐字节不变**）。

## 4. 双通道 E2E

### 4a. fake 通道（deterministic，CI 必跑）

conftest 强制 fake 后端（`TRAC_AGENT_BACKEND=fake`）。继承 TP-001 §4 前进性不变量：每条路径末态落在 `stage.exited` / `run.completed`·`run.parked` / `awaiting_human` 之一，绝不停在挂起。FakeAgent（FakeBackend）经 `simulate` 选分支，e2e 断言轨迹与事件时序，不断言文档语义。

### 4b. live 通道（真 opencode，缺凭据 skip）

`tests/e2e_live/`，provider/model 由 env 配置（Aaron §3.1）。**只断言**：真实启动、权限白名单生效（目标文档外写被拒）、JSON 协议可解析、目标 diff 为产物且格式合规、退出与恢复；**不断言**具体文本内容。缺凭据 → **skip（不 fail）**，CI 中为独立 required job、本地 opt-in（AC-0105）。

> live 通道证明"真 agent 管路对接正确"，fake 通道证明"runtime 本身正确"；两者 AC 不重叠（模板 §6.3）。

## 5. 外部依赖分层测试（opencode）

opencode 是外部依赖，按模板 §6 三层金字塔：

| 层 | 名 | 时间 | 覆盖 | 默认 |
|:---|:---|:---|:---|:---|
| L1 | deterministic sim | 虚拟 | fake 后端穷举工作流路径（业务 AC） | ✅ CI |
| L2 | contract sim | 虚拟 | **fake opencode stand-in**（实现 `opencode run --format json` 协议的服务/脚本），OpencodeBackend 与之交互，覆盖物化/JSON 解析/失败矩阵/权限合同 AC | ✅ CI |
| L3 | real env smoke | 真实 | 真 opencode 单往返 smoke（≤1 派发），覆盖真实发现/权限/产物 | ❌ nightly/manual（缺凭据 skip） |

- **L2 stand-in 责任合同**（模板 §6.4）：实现 opencode CLI 协议（读 `--agent`/`--auto`、输出约定 JSON、按 permission 编辑目标文件）；**不实现** tracks 业务。用于 AC-0201..0207（物化/发现/清理）、AC-1801..1803（失败矩阵）等在无真 opencode 时的合同级验证。
- **边界铁律**（模板 §6.2）：可替换 opencode（外部依赖），**不可 mock** tracks 自身的 parser/locate/audit/decide（被测对象）。

## 6. AC → 测试层映射

| AC 范围 | 层 | 测试文件 |
|:---|:---|:---|
| AC-0101/0102（后端选择/fake 强制） | unit + e2e | test_backend_select.py, test_happy_path.py |
| AC-0103（fake deterministic suite） | e2e | test_happy_path.py |
| AC-0104/0105（live suite + 缺凭据 skip） | e2e_live | test_live_agent.py |
| AC-0201..0207（opencode 后端/物化/发现/清理） | integration（L2 stand-in）+ e2e_live（L3） | test_opencode_backend.py, test_live_agent.py |
| AC-0301..0306（越权可观察：edit/bash/临时目录/后置审计/回滚/清理） | integration + e2e | test_audit.py, test_over_reach.py |
| AC-0401..0403（agent 提示词交付物存在/格式/同步） | unit（交付门禁） | 交付门禁检查（pre-commit/CI） |
| AC-0501..0505（inline-discussion 语法） | unit + ground_truth | test_discuss_parser.py |
| AC-0601..0609（thread 结构 + identity 无持久化/全文扫描/重排/回滚/L3 报告 + freshness token stale/缺 token 拒写） | unit + ground_truth | test_discuss_parser.py, test_discuss_locate.py |
| AC-0701..0705（4 级降级 + fail-closed 歧义） | unit + ground_truth | test_discuss_locate.py |
| AC-0801..0804（discuss CLI 5 子命令/blocker/check-ready） | e2e | test_discuss_cli.py |
| AC-0901..0903（状态一致性 + scope gate） | unit + e2e | test_discuss_writer.py, test_discuss_cli.py |
| AC-1001..1003（门禁 ready 判定） | integration + e2e | test_discuss_gate.py, test_discuss_cli.py |
| AC-1101..1104（写操作语义/并发 flock） | unit + integration | test_discuss_writer.py |
| AC-1201..1203（解析边界） | unit + ground_truth | test_discuss_parser.py |
| AC-1301..1304（skill 交付物存在/内容/版本/Sage 加载） | unit + e2e_live | 交付门禁检查, test_live_agent.py |
| AC-1401..1403（模板接入/去硬编码/M-START 骨架不校验） | unit + e2e | test_templating.py, test_validation_gate.py |
| AC-1501..1504（trac validate 独立/outcome 即校验+重派/门禁再校验/D-16 取代） | unit + e2e | test_validate.py, test_validation_gate.py |
| AC-1601（错误信息含 line:N） | unit | test_discuss_parser.py, test_validate.py |
| AC-1701（解析性能 < 1MB/1s） | unit（性能） | test_discuss_parser.py |
| AC-1801..1803（失败矩阵/事件·attempt·清理·reconcile/diff 权威） | integration（L2）+ e2e | test_opencode_backend.py, test_recovery.py |

> 交付门禁注：AC-0401..0403 / AC-1301..1304 的存在性 + 版本检查门禁已可执行——三个交付物（Scribe.md/Sage.md/SKILL.md）已补 `version: 0.2`，门禁入口 `trac check deliverables`（pre-commit/CI）与失败输出见 SPEC-003 FR-040 / ACC-003 AC-1303。

> **gpt [OPEN]:** AC-1505 不存在。acceptance.md FR-150 只有 AC-1501..1504（trac validate 独立 / 门禁再校验 / outcome 即校验+重派 / D-16 取代）。请修正映射为 AC-1501..1504，或若确需第五条 AC（例如"validate 报告含 line:N"），先在 acceptance.md 补上再引用。另：AC-0403 与 AC-1303 的交付门禁映射假设版本字段与检查脚本已存在，但对应 OPEN 线程尚未关闭（Scribe.md/Sage.md/SKILL.md 均无 `version`，门禁入口未定义）；建议在映射表标注"待 OPEN 线程关闭后可执行"。
>> **Scribe:** 已修正：(1) AC-1505 不存在 → 映射改为 AC-1501..1504（trac validate 独立/outcome 即校验+重派/门禁再校验/D-16 取代）；(2) 顺带把 FR-060 映射更新为 AC-0601..0609（本轮新增 AC-0608 stale / AC-0609 缺 token 拒写）；(3) AC-0401..0403 / AC-1301..1304 交付门禁已可执行——三个交付物已补 `version: 0.2`，门禁入口 `trac check deliverables`（pre-commit/CI）+ 失败输出见 SPEC FR-040 / ACC AC-1303，映射表已加注。@gpt 请确认是否可标记 [RESOLVED]。

> 每个 AC ≥1 测试、每个测试 ≥1 AC（CI 闭合）；跨模块合同（interfaces.md `modules` 列 ≥2）至少一个 integration 测试。

## 7. 共享 Fixture（conftest.py）

继承 TP-001 §5（`host_repo` / `trac` / `event_log`），新增：

```python
@pytest.fixture
def fake_backend(monkeypatch):
    """fake 通道：强制 TRAC_AGENT_BACKEND=fake，E2E 不触发 opencode。"""

@pytest.fixture
def fake_opencode(monkeypatch, host_repo):
    """L2 contract sim：安装 fake opencode stand-in（实现 run --format json 协议）到 PATH。"""

@pytest.fixture
def live_enabled():
    """live 通道：检测 provider/model 凭据；缺失则 pytest.skip（不 fail，AC-0105）。"""
```

公共断言辅助延续 `tests/helpers.py`（`assert_event` / `assert_exit` / `parse_frontmatter`），新增 `parse_discuss_threads(path)`（经 `trac discuss query` JSON，**不** import tracks 内部）。

## 8. CI Gate

```bash
trac agent archer ci-scan \
  --acceptance .tracks/projects/v0.2/acceptance.md \
  --tests tests/
```

校验项：AC 引用闭合（每 AC ≥1 测试、每测试 ≥1 AC）；反模式静态扫描（§1.3）；覆盖率 ≥95%；ground_truth 隔离（不 import tracks）；交付物存在性 + 版本检查（spec/skill/agent 提示词）。fake 通道（L1/L2）CI 默认跑；live 通道（L3）缺凭据 skip、独立 required job。

## 9. Judge Review Checklist

- [ ] 双通道 AC 不重叠（fake 断言状态机/live 断言协议不断言文本）
- [ ] opencode 外部依赖 L1/L2/L3 分层且标记正确，L3 不以无 issue 的 skip 规避
- [ ] discuss 解析/定位有独立 ground truth（不 import tracks）
- [ ] 越权 AC 有可观察证据（outcome failed/无提交/Human 修改不被覆盖/清理）
- [ ] 失败矩阵各分支断言 command/outcome 事件 + attempt + 子进程组清理 + reconcile
- [ ] 每个 AC 可回溯到测试代码；interfaces.md 出口与测试断言闭合
- [ ] e2e 限于 happy path，边界/错误归 integration/unit
