# 检查需求追踪和模块可达性

## `trac check trace`

一项需求写进 acceptance 文档，并不表示代码库里已经有东西验证它。反过来，一条测试天天在 CI 里运行，也不表示有人还能说清它在保护哪项需求。

`trac check trace` 对这两个方向同时清点：

- spec 中每条 FR/NFR 都必须有 AC；
- acceptance 中每条 AC 都必须指向存在的 FR/NFR；
- 每条 AC 都必须有测试源码中的长格式 marker；
- 每条 marker 都必须指向存在的 AC。

默认检查 `.tracks/projects/` 下按字典序排列的最后一个 `v*` 目录：

```bash
trac check trace
```

正式使用时更建议显式指定版本：

```bash
trac check trace --version v0.4
```

marker 是测试源码中的独立注释行，不是 pytest decorator：

```python
# AC-FR0080-01@v0.4 TRACKS-TRACE reports complete traceability chain
def test_complete_traceability_chain():
    ...
```

使用 `//` 注释的语言也受支持：

```typescript
// AC-FR0080-01@v0.4 TRACKS-TRACE reports complete traceability chain
test("complete traceability chain", () => {
  // ...
});
```

短格式 marker 会失败，因为测试证据必须绑定需求版本：

```python
# AC-FR0080-01 TRACKS-TRACE missing version
```

供脚本或 CI 消费时使用 JSON：

```bash
trac check trace --version v0.4 --json
```

```json
{
  "status": "fail",
  "hard_errors": [
    "acceptance line:18 AC-FR0080-01 has no test marker bound"
  ],
  "warnings": []
}
```

检查通过返回 0，存在 hard error 返回 1；warning 不改变退出码。当前 BS 到 FR 的承接仍是弱链接，只会产生 warning，不应把 `trace ok` 理解成 BS→FR 已被完整验证。

> [!info] Requirements Traceability
>
> 需求追踪的经典研究包括 Gotel 与 Finkelstein 1994 年的 *An Analysis of the Requirements Traceability Problem*。Tracks 将传统的人工追踪矩阵变成可重复运行的程序检查。

当前 marker 检查是逐行文本扫描。规范要求 marker 紧邻测试函数，但工具尚未验证邻接关系，也不验证 `@version` 是否与 `--version` 完全相等。因此，trace closure 证明的是“存在声明绑定”，测试是否忠实表达 AC 仍需独立评审和执行证据。

## `trac check reach`

trace 检查需求有没有测试；reach 检查生产模块有没有真正接到产品入口。

一个模块可能有完整单元测试，却从 CLI、API 或 worker 的任何入口都走不到。它在测试里是绿色的，在产品里却不存在。这类模块叫做**孤岛**。

在仓库根目录运行：

```bash
trac check reach
```

Tracks 从以下位置发现入口：

- `pyproject.toml` 的 `[project.scripts]`；
- 所有 `__main__.py`；
- `.tracks/reach-entries.txt`；
- 命令行传入的一个或多个 `--entry`。

例如，有些 worker 由外部调度器启动，无法从 Python package metadata 中自动发现：

```bash
trac check reach \
  --entry app.worker \
  --entry app.jobs.cleanup
```

也可以把这些长期入口写进项目文件：

```text
# .tracks/reach-entries.txt
app.worker
app.jobs.cleanup
```

工具使用 Python 标准库 `ast` 提取 import，构建模块级有向图，再从入口做 BFS。无法到达的生产模块会被列出：

```text
island module: app.legacy
island module: app.unused
```

JSON 输出适合门禁：

```bash
trac check reach --json
```

```json
{
  "status": "fail",
  "islands": ["app.legacy", "app.unused"],
  "entrypoints": ["app.cli"],
  "errors": [],
  "warnings": []
}
```

存在孤岛或没有任何入口时返回 1。仓库没有 Python 文件时返回 0，同时给出 `reach check not applicable` warning。

> [!info] Breadth-first search
>
> BFS 是按距离逐层遍历图的算法，通常归因于 C. Y. Lee 1961 年的迷宫布线工作。Reach 从每个入口逐层访问 import 边，遍历结束后未被访问的模块就是孤岛候选。

Reach 当前**仅支持 Python**，而且只分析静态模块 import。动态加载、插件发现、反射、框架路由和 subprocess 入口必须通过显式入口或后续语言适配器补充。它也不是函数级 dead-code analyzer。

## Legacy baseline

已有项目第一次采用 Tracks 时，可能本来就存在未闭合需求或孤岛模块。如果要求一天之内清零全部历史问题，团队往往无法完成 cutover。

`.tracks/legacy-baseline.json` 用来冻结采纳时刻的存量：

```json
{
  "trace_exemptions": {
    "ids": ["FR-0010", "AC-FR0010-02"]
  },
  "reach_exemptions": {
    "modules": ["app.legacy_adapter"]
  }
}
```

`trac check trace` 与 `trac check reach` 会自动读取它，不需要额外参数。

baseline 的含义不是“这些问题没关系”，而是“这些问题早于 Tracks”。采纳后新增的相同缺口仍然报错。修复一项历史问题后，应从 baseline 删除对应豁免，避免它继续遮住未来回归。

当前 trace 豁免通过错误文本中的 ID 子串过滤，reach 豁免则按完整模块名精确匹配。不要用短小、含义模糊的 ID 或模块名前缀做大范围豁免。

## 修复常见失败

**AC 没有测试 marker**：找到 acceptance 中对应 AC，在真正验证该行为的测试函数上方增加长格式 `TRACKS-TRACE` 注释。不要为了门禁随便给一条无关测试加 marker。

**marker 指向不存在的 AC**：先判断是测试引用了旧版本，还是 AC 被错误删除。需求删除应留下 tombstone；版本变化应更新长格式引用，而不是只去掉 `@version`。

**short format**：把 `AC-FR0080-01` 改成带版本的 `AC-FR0080-01@v0.4`。当前工具只检查 `@` 是否存在，因此版本正确性仍需评审者核对。

**没有入口声明**：为 CLI 添加 `[project.scripts]`，为可执行 package 提供 `__main__.py`，或把外部入口写进 `.tracks/reach-entries.txt`。

**孤岛模块**：先确认它是否真的由框架动态加载。若是，声明真实入口；若不是，把它接入产品路径或删除。不要仅为让检查通过而把普通生产模块加入豁免清单。

Trace 和 reach 都只报告，不改写项目。修复完成后重新运行同一命令；同一输入应得到稳定排序的同一结果。
