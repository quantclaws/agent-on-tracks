---
envelope: tracks-envelope:v2
name: tracks-envelope
version: 0.4
description: tracks 声明式派发的最终回复发射合同——文件投递优先、两层 envelope 结构、唯一 fenced block、作废形态与交付前自检。任何 assignment 声明 envelope（protocol tracks-envelope）的派发自动附加本 skill。
---

# tracks-envelope：声明式回复的发射合同

## 文件投递（优先形态）

assignment 带 `result_file` 时，**不要手写 fenced block**——按以下三步走：

1. 用 python 构造 dict（两层 envelope 结构，kind 按 assignment 声明），`json.dump` 写到 `result_file.result_path`（机器序列化，转义天然正确）
2. 跑 `result_file.verify_command`（即 `trac validate-reply --file <path> --kind <kind>`）确认 exit 0
3. 最终回复只需一行指针（如 `result: <path>`）；runtime 优先读文件，fenced block 仍是合法回退

示例：

```python
import json
envelope = {"envelope": {"kind": "<assignment 声明的 kind>", "version": 2}, "payload": {...}}
with open("<result_file.result_path>", "w") as fh:
    json.dump(envelope, fh)
```

```sh
trac validate-reply --file <result_file.result_path> --kind <assignment 声明的 kind>
# exit 0 + stdout `ok: <kind> v2` 才可返回
```

## fenced block（回退形态）

无 `result_file` 的派发仍用 fenced block：你的**最终回复**必须以如下形态结束（散文分析可以在 block 之前；block 之后不得有任何内容）：

    ```tracks-envelope
    {"envelope": {"kind": "<assignment 声明的 kind>", "version": 2}, "payload": {...}}
    ```

## 两层结构（缺一作废）

- **envelope 头**：`{"kind": ..., "version": ...}`——kind 必须等于 assignment 声明的 kind；version 按 assignment
- **payload**：字段与类型严格按 assignment 注入的 schema / evidence_contract / verdict_contract——示例即最具体的合同，按示例的形状填

## 作废形态（live 实证，每种烧掉整次 attempt）

| 形态 | 判定 |
|---|---|
| 回复停在散文、没有 fenced block | `no_envelope_block` |
| block 里只有裸 payload、缺 envelope 头 | `missing_kind` |
| block 之后还有散文 / 出现多个 block | 块外散文 / `multiple_envelope_blocks` |
| kind 与 assignment 声明不符 | `schema_violation` |
| block 不是合法 JSON（手写转义错） | `malformed_json` |

## 长字符串纪律（malformed_json 的唯一来源）

payload 字符串值里**不要手工转义嵌套 JSON**：多 K 字符的手写 `\"` 转义必然出错（run 01M2QTJB PRISM_FINAL 六连 `Expecting ',' delimiter`，char 2543-2922，合计烧掉 35+ 分钟）。两条铁律：

1. **字符串值内禁止裸双引号**。最常见触发器：把 pytest 失败输出/源码行原样粘进 evidence——如 `events["command_id"] == "never"` 里的内层 `"` 会提前终止字符串。改写法：内层引号换成单引号（`events['command_id']`）、或转述（"断言 events 下标 command_id 等于 never"）、或删去只留节点 ID。
2. **不逐字复述 assignment/日志原文**：引用关键字段名与值即可；散文细节放在 block **之前**的正文里（runtime 只按 schema 校验结构）。

用工具构造（`json.dumps`）永远安全；手工嵌入多 K 字符永远不安全。文件投递形态下这条铁律由 `json.dump` 自动满足。

## 交付前自检（一步）

文件投递形态：`verify_command` exit 0 + 最终回复只有一行指针。fenced 回退形态：把最终回复的最后一行对准上表核对：恰好一个 ```tracks-envelope fence、内含 envelope 头 + payload 两层、kind 正确、之后无任何内容。核对通过才返回。

> 历史教训（本 skill 的由来）：2026-09-19 run 01M2QTJB——Devon 六连 `missing_kind`（合同示例只画裸 payload）、Prism DIAGNOSE 三连 `no_envelope_block`（无具体示例）；示例与自检到位后均为一次通过。示例在合同/skill 里到位、发射自检在返回前执行，是全部差别。
