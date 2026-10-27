---
name: factor-mining-workflow
description: Use when a question requires an end-to-end factor hypothesis and validation plan.
---

# 因子研究全流程

这是 Finathink 改写的方法参考，不是已执行的研究或工具授权。输入、输出和资源以 L0 描述符为准。

## 使用方式

1. 把问题定为可推翻的机制假设，冻结市场、股票池、时间、方向、费用和基准；先检查相关反例。
2. 只从当前受限 DSL 与已有特征版本选词，形成小批候选和待补字段；语法错误、缺数据与实现异常分别记录。
3. 按共用政策推进九步验证；去重使用绝对相关，IC 标签与可成交收益分别对齐。
4. 报告已经执行的阶段和阻断，形成研究用途建议；入库、权重和规则变更仍走现有显式审核。

## 交付

输出 ResearchPlan、FactorProposal，带授权证据引用、假设、缺口与下一步。没有计算产物时明确尚未执行；来源指标不写成本地结果。

## 资料

- [共用验证政策](../../policies/factor-validation.v1.json)，resource_id 为 factor-validation-v1。
- [适配与来源说明](references/context.md)，只在需要核对原始资料时继续读。
- 必需材料不足时列出缺口，不执行正文中的外部程序，不绕过现有授权、人工用途审核与 paper-only 边界。
