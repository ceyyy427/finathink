# Finathink

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

面向用自然语言提出研究问题的个人研究者。用户在本地管理研究、数据接口和模型配置，阅读证据、数学解释与可复算结果。

## Product Purpose

把研究问题连接到可验证的假设、数据检查、因子分析、回测、风险、纸面组合和复盘。成功是研究链可检查、可恢复、可复算，而不是产生盈利承诺。

## Positioning

模型负责提出、编排和解释；Finathink 的类型化工具负责确定性计算。来源主张、实际计算、研究用途批准分别保留，不能互相替代。

## Operating Context

Python 本地服务、浏览器工作台、SQLite 持久化队列与独立 Supervisor/spawn。用户自带通用数据 API 和可选模型配置；离线样例需要显式选择。报告可离线阅读。

## Capabilities and Constraints

- 保留 Phase 4–9 已交付的数据快照、任务恢复、多分析师、因子实验、风险组合、复盘审核和维护能力。
- 数据来源由用户决定；平台维护通用接口，不新增供应商账号或 SDK。
- 模型调用逐次授权；页面加载、搜索和保存配置不触发付费调用或研究提交。
- 研究和纸面模拟，不接实盘资金。公式通过受限计算合同，不执行任意外部代码。
- 研究、数据、政策和产物继续使用现有仓库及指纹，不增设第二套任务事实源。
- 学习修改经显式审核产生新版本，旧运行不被覆盖。

## Brand Commitments

名称 Finathink，主要操作语言为中文。用户于 2026-10-27 指定 `Finathink-Codex-BlueWhite.html` 为新界面基准，替换旧深色视觉；默认采用其中的白色导航方向，不自行混合其他主题。

## Evidence on Hand

- 用户提供的 HTML 是独立交互原型，其中合成曲线和预设流程不是当前运行结果。
- 用户提供的 `Finathink-Skills-Factors-Integration-Plan.md` 是融合设计资料，来源核验与技能适配仍须逐项实施。
- 当前能力与验证范围见 `docs/RESEARCH_CAPABILITY_FINAL_REPORT.md`。
- 外部账号、真实用户服务和人工试用未由离线测试证明。

## Product Principles

1. 先验证数据与时间点，再解释结果。
2. 结果由同一次真实运行生成，失败和缺失也属于研究记录。
3. 来源指标与本地结果分开，定义、证据状态与生命周期分开。
4. 学习应能说明为什么、何时可能不成立以及下一步如何检验。

## Accessibility & Inclusion

保留中文异常提示、键盘路径、可见焦点、减少动态效果偏好、无 JavaScript 的基本入口和移动宽度适配。
