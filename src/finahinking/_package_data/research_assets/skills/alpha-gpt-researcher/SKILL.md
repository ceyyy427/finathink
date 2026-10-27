---
name: alpha-gpt-researcher
description: Use when a research question needs synthesis of methods, counterexamples and unresolved evidence.
---

# 研究证据与方法综合

这是 Finathink 改写的方法参考，不是已执行的研究或工具授权。输入、输出和资源以 L0 描述符为准。

## 使用方式

1. 从授权证据中区分观察、推断、来源主张和本地结果，列出彼此矛盾的口径与缺口。
2. 形成当前问题的最小研究计划、基线、失败条件和所需材料；只引用已加载的方法和资源。
3. 选择与机制有关的验证和反例，不把全部人物资料或旧团队记忆灌入上下文。
4. 综合计算结果时保留范围与不确定性；输出下一轮研究建议，不继承上游角色、自我介绍、历史成绩或待办。

## 交付

输出 ResearchPlan，带授权证据引用、假设、缺口与下一步。没有计算产物时明确尚未执行；来源指标不写成本地结果。

## 资料

- [共用验证政策](../../policies/factor-validation.v1.json)，resource_id 为 factor-validation-v1。
- [适配与来源说明](references/context.md)，只在需要核对原始资料时继续读。
- 必需材料不足时列出缺口，不执行正文中的外部程序，不绕过现有授权、人工用途审核与 paper-only 边界。
