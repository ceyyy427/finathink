# 策略研究、统一纸面账本与来源池

从「因子定义库」进入「策略与纸面账本」，选择模板以及固定合成样例或已保存的用户面板。提交会冻结模板、风险规则、参数、面板和批准政策的指纹。后台任务完成后，可查看订单、成交、持仓、现金、费用、公司行动、基准和风险判断。

模板包括等权、单因子分层、短期反转、趋势突破、低波低换手、价值质量、多因子中性、ETF 风险预算、ETF 强弱轮动、状态调仓、事件研究和配对均值回归。事件模板返回事件统计。中性与配对模板保留理论研究结果，成交模式为受规则约束的多头纸面模拟。

股票和 ETF 在同币种账本中对账。上一观察点形成目标，最早下一观察点检查成交资格。报告单列整手、参与率、涨跌限制、停牌与结算约束。权益由现金和按已知价格标记的持仓构成。原始价格下的拆分与现金分红分别入账，无法核验的公司行动保留缺数据状态。

风险目录覆盖 PIT、财务异常、可交易性、流动性、集中度、行业、共线、波动、尾部、止损、回撤与压力。财务异常提示用于进一步核对。风险规则的结果和订单拒绝原因分别保留。

## 本地接口

| 操作 | 请求与必要字段 |
| --- | --- |
| 读取目录 | `GET /api/research/strategies` |
| 显式保存固定样例 | `POST /api/research/strategies/sample` |
| 运行策略 | `POST /api/research/strategies`，提供 `template_id`、`contract`、`attachment_refs`、`idempotency_key`，可选 `parameters` 和 `policy_ref` |
| 登记来源 | `POST /api/research/pools`，提供 `job_id`、`pool_ref`、`instrument`、`decision_origin` 和 `stage`，可选共同截止时间 `decision_at` |
| 查询来源 | `GET /api/research/pools?pool_ref=标识` |
| 冻结比较 | `POST /api/research/pools/freeze`，提供 `pool_ref` 与返回的 `decision_at` |
| 观察冻结队列 | `POST /api/research/pools/observe`，提供 `cohort_ref` 和用于后续观察的 `snapshot_ref` |
| ETF 同窗口映射 | `POST /api/research/etf-mapping`，提供 `snapshot_ref`、`equity`、`etf`、`start`、`end` 及持仓披露 `holdings` |

写入接口使用既有本地 CSRF 校验。来源登记由服务端读取完整性已核验的报告，查验用户提交、等权基准、自动规则、模型研究或公共方法的实际关联。来源记录和 WATCH、CANDIDATE、DECISION 阶段是两个独立维度。DECISION 表示明确纳入冻结研究队列。模型记录绑定已验证的经理假设，公共方法记录绑定已完成的冻结测试候选，自动扫描绑定已评估的规则结果。目标权重只来自实际策略产物。同一经济目标的来源关联可以合并观察样本。

冻结队列使用 1／5／20／60 个共同观察点，同一次观察使用一致的时间和价格口径。停牌、缺标记或未到观察期会显示缺数据。结果提供复盘建议，权重变更仍走已有审核流程。ETF 映射同时记录收益区间、流动性和持仓披露可用时间。

执行证据见[阶段验收](reviews/2026-10-31-finathink-phase26-review.md)和[固定来源核对](research/phase26-source-study.md)。
