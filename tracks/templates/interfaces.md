---
envelope: tracks-envelope:v2
interfaces_id: IF-NNN
spec_ref: SPEC-NNN
arch_ref: ARCH-NNN
created: {YYYY-MM-DD}
status: draft
sha:
---

# {版本} — 接口与类型化 Schema

<!-- 模板指引（生成文档时阅读；填写完成后删除本注释，交付的文档只留内容）：
  - 交付文档中出现的 blockquote 一律是 inline-discussion 讨论线程，模板指引不得残留为 blockquote。
  - 职责边界：本文档只写跨模块合同——事件/命令/类型化 schema、CLI 接口、文件与存储契约、
    测试可断言的外部出口。模块内部结构属于 architecture.md；验收标准属于 acceptance.md。
  - 延续性优先：若存在上一版接口文档，§0 必须声明继承或变更；未提及者一律继承。
  - 封闭集原则：事件类型 / Command kind / 枚举取值是封闭集，新增成员必须显式列出，
    不得以「等」「之类」含糊带过。
  - 可观察出口（§4）是 test-plan 断言的唯一基础：AC 需要观察的内部状态，必须在此有对应出口。
  - 决策记录在 inline-discussion 中；只有未 resolved 的 inline-discussion 才能阻塞评审退出。
  - 一般不使用表格，因为它不利于使用 tracks-discuz skill 来评论。如果必须要使用，则表格必须增加行序号列以方便引用。
-->

## 0. 延续性（什么不变）

{上一版接口合同中逐项声明：不变 / 变更（含理由）。首版写「无（首版）」}

## 1. 跨模块合同

{模块间的数据契约：类型化 schema、事件/命令、函数签名；封闭枚举逐一列出取值}

## 2. CLI 接口合同

{每条对外命令：输入、前置校验、成功输出、失败输出、exit code}

## 3. 文件 / 存储契约

{路径、格式、写入者、读取者；临时产物与持久产物的边界}

## 4. 可观察出口（测试断言基础）

{测试可断言的外部出口清单（CLI 输出 / 文件 / 事件 / 日志）；
test-plan 的断言只能落在这里（test-plan §6.5 闭环）}

## 5. IF Registry

{本版接口的 IF- 标识符清单，test-plan §8 AC Coverage 的 IF 归属列只能引用此处的标识符；
每条格式：``### IF-XXX-NNN 名称``}

<!-- Template guidance (delete before delivery; replace with real entries):
  The example below MUST be deleted and replaced with actual `### IF-XXX-NNN 名称`
  entries. An unmaterialized example would make the validator treat the registry
  as non-empty (fail-open); keeping it in a comment ensures the template itself
  never manufactures a fake registry.
### IF-EXAMPLE-001 示例接口
-->
