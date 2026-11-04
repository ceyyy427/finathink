# 阶段 27 自然语言研究审查

执行日期为 2026 年 11 月 4 日（洛杉矶）。固定来源使用既有[源码学习锁](../research/2026-10-31-open-source-quant-lock.json)。审读 RD-Agent 的假设与实验上下文、DeepSeek Harness 的验证与归档顺序及 Qlib 的训练切片代码，核对三者对应文件及许可证的 SHA-256。复用 Finathink 既有队列、方法路由、因子实验、策略账本和研究池。

| 验收 | 可复核路径 |
| --- | --- |
| 30 个冻结问题、十个因子家族与三种策略方向 | `tests/research/test_discovery_planner.py`，固定期望公式、缺字段和反例 |
| 六个因子路径与两个策略路径 | `tests/research/test_discovery_journeys.py`，真实 HTTP、spawn、报告与审核；数据为合成样例 |
| 五角色、来源、上下文与外部交接 | `tests/research/test_discovery_runtime.py`，分角色证据、授权、取消、重启和旧交接拒绝 |
| 本地兼容模型适配 | 受控传输响应检查角色上下文与引用；实际付费模型语义和市场收益需要用户数据及授权 |
| 三种尺寸的离线报告 | `tests/p7_5/test_phase27_browser.py`，1440×900、1920×1080、375×812，键盘展开与截图 |
| 安装与恢复 | `scripts/check_installed_journey.py`，安装后 HTTP 和 spawn，备份后报告字节一致 |

独立审查指出方法上下文、角色数据可用性、模型授权和总字节上限四处缺口。修正后，角色接收冻结的方法设计和时点字段证据，使用整体上下文上限；策略的供应商调用显式授权，并独立记录模型反馈。样本外因子路径继续使用离线角色，保持冻结窗隔离。
