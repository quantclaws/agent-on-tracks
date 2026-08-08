# 校验文档与交付物

## 校验单个文档

Tracks 的文档既要给人读，也要给 Runtime 解析。少一个章节、写错一个 ID，读者可能凭上下文猜出来，状态机却不能靠猜测推进。

校验一个文档：

```bash
trac validate --file .tracks/projects/v0.5/story.md
```

成功输出：

```text
valid
```

失败时，所有问题写入 stderr，退出码为 1：

```text
line:1 missing section '必要性与风险'
```

文档类型只由**文件名**决定，不根据内容猜测。当前支持：

- `story.md`
- `spec.md`
- `acceptance.md`
- `architecture.md`
- `interfaces.md`
- `test-plan.md`
- `prd.md`

因此 `my-story.md` 即使内容与 story 模板相同，也会失败：

```text
line:1 no template mapping for 'my-story.md'
```

基础校验会检查模板要求的 frontmatter 字段名和二级标题。额外章节允许存在；数字章节编号会被忽略，例如 `## 2. 用户意图` 与 `## 用户意图` 视为同一标题。

这不是完整 Markdown AST 或 YAML schema 校验。当前实现使用逐行扫描、正则和集合比较；frontmatter 只检查字段名是否出现，通常不验证值的类型。工具验证的是 Tracks 的文档约定，不是任意 Markdown 的全部语义。

## 校验交付物

Agent prompt 和 skill 是随 Tracks 安装的行为资产。文件漏装或 frontmatter 缺失会让 Runtime 分派出错误角色，因此有单独门禁：

```bash
trac check deliverables
```

成功输出：

```text
deliverables ok
```

这个命令不扫描当前仓库的任意 Markdown。它检查安装包 `tracks/` 中固定的 Agent 和 skill 清单。

所有交付物必须存在，并有非空 `version:`；当前格式规则只要求 version 去除空白后以数字开头。Agent prompt 还必须有 `IQ: S|A|B`，skill 不要求 IQ。

失败会一次列出全部问题：

```text
missing or malformed version in /path/to/tracks/agents/Sage.md
missing or malformed IQ in /path/to/tracks/agents/Lex.md
```

它不检查 SemVer、不比较所有交付物版本是否一致，也不验证内容 digest。`version: 2banana` 在当前实现中会通过；这说明该门禁只保证最小可加载条件，不是完整发布清单验证。

## 门禁中的校验

独立运行 `trac validate` 适合作者在提交前快速检查。Runtime 内部还会在 Agent outcome 后和 stage 退出前执行校验，但两条路径并非完全相同。

Standalone CLI 始终执行模板结构检查，并根据文件名追加规则：

**`story.md`**：BS heading 必须是大写 `BS-XX`、两位数字且唯一。旧版本 story 同时出现多个 legacy 章节信号时，会采用兼容 profile。

**`spec.md`**：FR/NFR heading 必须是 `FR-XXXX` 或 `NFR-XXXX`；每项要求 `来源`，FR 还要求 `交付入口`。CLI 不执行 Runtime 内部的 FR≤30 scope gate。

**`acceptance.md`**：要求同目录有 `spec.md`，并检查 FR/NFR 与 AC 双向覆盖。acceptance 的二级标题随需求变化，因此不按固定模板章节名比较。

**`test-plan.md`**：要求同目录有 `acceptance.md`；每条 AC 必须在同一行声明 `unit`、`integration` 或 `e2e`。若同目录 `interfaces.md` 提供 IF registry，integration/e2e 项还要有存在的 IF attribution。

**设计三文档**：`architecture.md`、`interfaces.md` 与 `test-plan.md` 还会拒绝遗留 guidance blockquote 和不存在的 `trac` 子命令。写计划中的命令时，应明确标为 foundation task，而不是让文档假装它已经存在。

Runtime 的 `validate_document` 还会结合当前 stage、checks 与 discussion readiness 产生 verdict。Standalone CLI 的 `valid` 不等于 stage 一定可以退出；它只说明这一次单文件检查通过。

## 常见问题

**缺少章节**：对照 `tracks/templates/` 中同名模板补齐二级标题。不要只把标题写进 HTML 注释；条件章节只有模板明确标记为可选时才会被忽略。

**缺少 frontmatter 字段**：文件必须从第一行 `---` 开始，并包含模板要求的字段名。当前解析对 BOM、前置空白和不同换行格式不做完整容错。

**Acceptance 要求同目录 spec**：把同一版本的 `spec.md` 与 `acceptance.md` 放在同一项目目录。不要用另一个版本的 spec 临时满足校验。

**Test plan 缺 layer attribution**：让 AC ID 和层级出现在同一可见行：

```markdown
- AC-FR0010-02: integration，归属 IF-CLI-001
```

**未知 trac 子命令**：检查命令是否真的存在。尚未实现的工具应在设计中写成 foundation task，不要把未来接口伪装成当前 CLI。

**Deliverable version 失败**：补充以数字开头的非空 `version:`。当前命令不会判断跨文件版本一致性，不能用它证明发行包所有行为资产来自同一 release。

**Agent IQ 失败**：Agent prompt 的 `IQ:` 只能是 `S`、`A` 或 `B`；skill 不需要该字段。

`trac validate` 与 `trac check deliverables` 都只读文件，不会补章节、更新 SHA、格式化 Markdown 或修改 Runtime state。修复由作者完成，然后重新运行相同命令。
