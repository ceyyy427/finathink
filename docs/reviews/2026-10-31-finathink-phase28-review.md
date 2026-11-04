# 阶段 28 量化研究工作台验收

执行日期为 2026 年 11 月 4 日（洛杉矶）。工作台延续蓝白 AppShell，参考了已锁定的 DeepSeek 研究库的目录和证据组织、QUANTAXIS 的因子分类以及 RD-Agent 的研究路径。上游代码和页面保持来源研究用途，工作台由现有目录与报告合同生成。

| 验收 | 证据 |
| --- | --- |
| 独立对象和来源 | 80 个知识条目、80 个公式、12 个策略、12 条风险规则、22 个合成反例，合计 206 个 |
| 十二条研究路径 | `tests/research/test_quant_workbench_journeys.py`，实际 HTTP 与 spawn，覆盖缺字段、样本外冻结、审核、策略账本、研究池和复算 |
| 桌面、移动和离线 | `tests/p7_5/test_phase28_browser.py`，三尺寸截图、焦点、减少动态效果、迟到请求和离线 HTML |
| 来源更新和历史证据 | `tests/research/test_source_updates.py`，固定提交、许可证变更、显式撤回、历史报告字节及复算一致 |
| 安装、备份和重建 | `scripts/check_installed_journey.py`、`tests/validation/test_quant_knowledge_install.py`，干净 wheel 路径和源审阅恢复 |
| 检索性能 | `scripts/benchmark_quant_knowledge.py`，10,000 个合成对象和 30 个固定查询。参考环境暖 p95 目标 500 毫秒，CI 阈值 750 毫秒为共享运行器留出波动空间。 |

固定样例只验证工具行为。便携研究包对策略、发现计划和因子试验显示原工作流依赖。来源审批与使用审批分别记录。
