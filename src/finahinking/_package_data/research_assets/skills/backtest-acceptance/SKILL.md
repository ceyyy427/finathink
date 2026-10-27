---
name: backtest-acceptance
description: Use when a study or strategy requires backtest acceptance and execution-assumption review.
---

# 回测口径与验收

这是 Finathink 改写的方法参考，不是已执行的研究或工具授权。输入、输出和资源以 L0 描述符为准。

## 使用方式

1. 核对信息可用时间、信号与成交时序、停牌及不可成交处理，记录用户市场规则而非套用上游历史市场假设。
2. 明确双边/单边费用、bps 转换、滑点和冲击假设；未知历史费用或交易规则须列缺口。
3. 核对净值、收益、回撤、换手、风险、基准和窗口是否出自同一运行；不足样本或覆盖率不解释为有效。
4. 按共用政策检查冻结的 OOS 与最终测试使用次数，归档既有版本和失败证据；通过回测不等于批准交易或自动入库。

## 交付

输出 ResearchPlan、ValidationChecklist，带授权证据引用、假设、缺口与下一步。没有计算产物时明确尚未执行；来源指标不写成本地结果。

## 资料

- [共用验证政策](../../policies/factor-validation.v1.json)，resource_id 为 factor-validation-v1。
- [适配与来源说明](references/context.md)，只在需要核对原始资料时继续读。
- 必需材料不足时列出缺口，不执行正文中的外部程序，不绕过现有授权、人工用途审核与 paper-only 边界。
