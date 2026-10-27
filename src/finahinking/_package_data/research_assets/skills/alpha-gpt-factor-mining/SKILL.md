---
name: alpha-gpt-factor-mining
description: Use when a research question needs bounded symbolic factor candidates.
---

# 受控公式候选生成

这是 Finathink 改写的方法参考，不是已执行的研究或工具授权。输入、输出和资源以 L0 描述符为准。

## 使用方式

1. 将机制映射为显式字段名、单位、窗口与受限算子，使用 Finathink 现有 DSL，不复制上游求值器或训练程序。
2. 给出少量短公式与机制说明，交给实际语法/字段检查；非法项保留错误类别后再改写，重试预算由任务确定。
3. 保留原始与排序通道的语义区别、缺失值与热身区；不将未知或非有限值填成有效信号。
4. 评分只读取受控计算产物，反馈重复机制与失败原因；不生成猜测的 ICIR，也不将论文训练框架描述为已经接入。

## 交付

输出 FactorProposal，带授权证据引用、假设、缺口与下一步。没有计算产物时明确尚未执行；来源指标不写成本地结果。

## 资料

- [共用验证政策](../../policies/factor-validation.v1.json)，resource_id 为 factor-validation-v1。
- [适配与来源说明](references/context.md)，只在需要核对原始资料时继续读。
- 必需材料不足时列出缺口，不执行正文中的外部程序，不绕过现有授权、人工用途审核与 paper-only 边界。
