# 需求追踪

## ID 链

Tracks 用稳定 ID 把自然语言需求逐层展开：

```text
BS-01
  └─ FR-0010 / NFR-0010
       └─ AC-FR0010-01 / AC-NFR0010-01
            └─ AC-FR0010-01@v0.4 TRACKS-TRACE
```

**Behaviour Seed（BS）** 写在 story 中，描述用户可观察的行为起点。

**FR/NFR** 写在 spec 中。FR 描述功能行为，NFR 描述性能、安全、可靠性等约束；ID 为四位数字，并记录来源。

**Acceptance Criterion（AC）** 写在 acceptance 中，回指一个 FR/NFR，并把需求变成可验证结果。

**Test marker** 写在测试源码的独立注释行，使用长格式 `AC-...@<version> TRACKS-TRACE`。版本限定避免共享 tests 树中的同名 AC 混淆。

ID 不因标题修改而变化，删除保留 tombstone，避免旧测试悄悄指向另一个新需求。

> [!info] EARS
>
> Easy Approach to Requirements Syntax 由 Rolls-Royce 的 Alistair Mavin 等人在 2009 年提出，用少量句型把自然语言需求写得更可检查。Tracks 的 Behaviour Seed 借用 WHEN/IF/WHILE/WHERE 等结构。

## Trace closure

Trace closure 对链路做双向清点：

- FR/NFR 没有 AC，是硬错误；
- AC 回指不存在的 FR/NFR，是硬错误；
- AC 没有长格式 test marker，是硬错误；
- marker 指向不存在的 AC，是硬错误；
- marker 缺 `@version`，是硬错误；
- 重复 FR/AC ID，是硬错误。

检查不会在首错处停止，而会收集完整列表并稳定排序。tombstone 不计入孤儿。

BS→FR 当前是**弱链接**。工具尚未解析 spec 的来源字段来证明实际承接，每个未 tombstone 的 BS 可能产生 warning，但 warning 不改变退出码。因此现阶段不能说整条 BS→FR→AC→test 已被同等强度验证。

M-TEST 退出会消费 trace 证据，关注分配到 integration/e2e 层的 required AC；独立 CLI 则检查所选版本中的完整文档和 tests 树。

Trace closure 只证明绑定存在。marker 是否紧邻真实测试函数、测试是否忠实表达 AC、`@version` 是否与命令选择版本相等，目前仍需评审和其它门禁。

## Reach closure

测试覆盖需求以后，还要证明实现接入产品入口。只有单元测试使用、却从任何交付面都不可达的模块，是测试驱动的孤岛。

`trac check reach` 当前为 Python 仓库构建模块级静态 import 图。入口来自：

- `pyproject.toml [project.scripts]`；
- `__main__.py`；
- `.tracks/reach-entries.txt`；
- CLI `--entry`。

工具从入口做 BFS，访问 import 边和父 package。无法到达、且不在 baseline 的生产模块被报告为 island；`tests.*` 不属于生产模块。

Reach 工具本身已经实现并有独立 ground-truth parity tests。它作为 M-IMPL ISLAND_GATE 和 M-VERIFY 反 slop 门禁的消费仍属于后续阶段，当前可独立运行但不应宣称已经成为完整 release 的 required gate。

## Legacy baseline

Legacy baseline 解决的是采用 Tracks 的切换问题，而不是放宽新代码标准。

`.tracks/legacy-baseline.json` 可以列出采纳时已经存在的 trace ID 与 reach module。checker 自动读取并隐藏这些存量缺口，使团队可以先阻止新增问题，再逐步偿还历史债务。

采纳后新增的相同问题不能加入原 baseline 冒充历史。修复一项存量后，应删除豁免。

当前没有自动生成 baseline 的 CLI，文件由团队审查后维护。Trace 豁免以错误文本中的 ID 子串过滤，reach 豁免按完整模块名精确匹配；两者强度不同，需谨慎填写。

## 当前语言边界

Trace 与 reach 的“多语言”含义不同。

Trace 不解析语言语义，只扫描一组常见源码后缀中的 `#` 或 `//` marker。因此它可以在 Python、JavaScript/TypeScript、Java、Go、Rust、C/C++、C#、Ruby、PHP、Kotlin、Swift 等源码中识别声明绑定，但不理解测试框架和函数结构。

Reach 使用 Python 标准库 `ast`，当前只支持 Python。它不识别动态 import、插件发现、反射、框架路由、subprocess 入口或函数级调用图；`src/` layout 的 package root 也没有专门推断。

CI 中 trace/reach 目前仍是 `continue-on-error` foundation job。工具已经可用，不等于所有宿主项目都已将它们设为 required check。

这些限制应当作为证据解释的一部分，而不是藏在错误发生以后。一个 checker 的价值来自它准确说明自己检查了什么，也说明自己没有检查什么。
