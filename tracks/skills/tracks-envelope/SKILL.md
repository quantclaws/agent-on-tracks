---
envelope: tracks-envelope:v2
name: tracks-envelope
version: 0.1
description: tracks 声明式派发的最终回复发射合同——两层 envelope 结构、唯一 fenced block、作废形态与交付前自检。任何 assignment 声明 envelope（protocol tracks-envelope）的派发自动附加本 skill。
---

# tracks-envelope：声明式回复的发射合同

assignment 声明 `envelope` 时，你的**最终回复**必须以如下形态结束（散文分析可以在 block 之前；block 之后不得有任何内容）：

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

## 交付前自检（一步）

把最终回复的最后一行对准上表核对：恰好一个 ```tracks-envelope fence、内含 envelope 头 + payload 两层、kind 正确、之后无任何内容。核对通过才返回。

> 历史教训（本 skill 的由来）：2026-09-19 run 01M2QTJB——Devon 六连 `missing_kind`（合同示例只画裸 payload）、Prism DIAGNOSE 三连 `no_envelope_block`（无具体示例）；示例与自检到位后均为一次通过。示例在合同/skill 里到位、发射自检在返回前执行，是全部差别。
