# 时点面板与研究算子

面板把用户快照和字段附件按资产、观察时点对齐。每个历史时点只读取当时已可获得的记录；公告的修订从其可用时间起生效，原始附件和旧快照保持不变。所有接口使用本地数据和现有 numpy/pandas。

```mermaid
flowchart LR
    A[用户 API / 文件] --> B[不可变快照与字段附件]
    B --> C[九类字段合同]
    C --> D[逐时点可用性与规则检查]
    D --> E[多资产研究面板]
    E --> F[24 个受控算子]
    F --> G[数值、单位、诊断与指纹]
    classDef data fill:#dbeafe,stroke:#2563eb,color:#172554;
    classDef check fill:#fef3c7,stroke:#d97706,color:#451a03;
    classDef research fill:#dcfce7,stroke:#16a34a,color:#14532d;
    class A,B,C data;
    class D check;
    class E,F,G research;
```

## 数据合同

| 字段包 | 可提供的内容 | 时间含义 |
| --- | --- | --- |
| 量价 | 原始 OHLCV、成交额、停牌、真实换手率 | 本资产声明时区内的当前会话；量价来自原快照 |
| 日历与股票池 | is_session、in_universe、trade_allowed、lot_size、settlement_days、涨跌停声明 | 日历逐时点；成员和规则自已知且生效时点起有效 |
| 公司行为 | split_factor、cash_dividend | 拆分比例按已知状态保留，现金分红逐时点；数值不自动回改历史价格 |
| 财务 | 利润、资产、净资产、EPS、增长率 | timestamp 为报告观察期，available_at 为公告或修订实际可用时刻 |
| 行业风格 | 历史行业、规模、style_beta | 分类和暴露必须为历史声明；市值逐时点 |
| 机构汇总 | 净买入、汇总持仓比例、席位识别说明 | 净买入逐时点、持仓按已知状态；保留来源声明 |
| 事件新闻 | 新闻数、情绪分数、事件类别 | 已由用户整理的时点汇总；原始文本不作为执行指令 |
| ETF | NAV、holdings_scope、holdings_weight、tracking_error | 指定篮子（如 top10）的持仓汇总权重与净值，按实际公布时间生效 |
| 宏观政策 | 利率、宏观指数、政策类别 | 用户将适用地域/口径绑定到资产及 scope_id，修订同样受可用时间限制 |

`PanelFieldAttachment`（`panel-fields.v1`）包含 snapshot_ref、units、records、source_declaration。每条记录必须有 instrument、timestamp、available_at；未知可用时间可写 null，此记录保持不可用于研究。日期必须携带时区，统一规范成 UTC。单位从受控目录选择；币种和比例/百分比保持原值。

`PanelContract`（`research-panel-contract.v1`）包含快照指纹、资产清单、observed_at 决策网格、字段单位、每资产币种/时区、scope_id 和四个规则附件引用（calendar_ref、universe_ref、adjustment_ref、trade_rule_ref）。规则引用为实际附件的 SHA-256，且必须提供对应字段。快照可用时间是额外上限。格点上缺数据保留空值并记录 NEEDS_DATA；不得反向填充。

`timestamp` 与 `available_at` 同时不晚于决策网格时点才可选。状态字段先选择最新观察期，再选该期最新已知修订；更旧报告的晚到修订不会覆盖更新报告。旧 ResearchFieldAttachment 可继续使用，announcement_at/revision_at 会进一步推迟可用时间，effective_until 到期后不恢复旧分类。每格的选择依据、单位、覆盖数和缺失原因进入 audit。

快照捕获接口可显式提供 `adjustment: "RAW"`；默认仍是 UNKNOWN。它是用户对原始价格口径的声明。快照中的单位与面板声明冲突时拒绝输入，未知时输出诊断。币种不同的数据保持各自口径，不隐含汇率转换。历史退市资产仍保留在网格内，in_universe 记录当时的成员状态。

## 24 个算子

显式使用 `evaluate_panel_expression(expression, panel)`；版本为 `bounded-panel-dsl.v1`。旧 `evaluate_expression`、旧表达式及指纹继续原有语义。新表达式只接受下表函数、字段名和有限常数。

| 作用域 | 函数及参数 | 输出单位 |
| --- | --- | --- |
| 时序 3 项 | ts_lag(x,n)、ts_return(x,n)、ts_diff(x,n) | 原单位、比例、原单位 |
| 时序 6 项 | ts_sum / ts_mean / ts_std / ts_min / ts_max / ts_rank(x,w,m) | 前五项为原单位，rank 为比例 |
| 时序 2 项 | ts_corr / ts_cov(x,y,w,m) | 比例 / 两输入单位乘积 |
| 时序 5 项 | ts_slope / ts_linear_decay / ts_argmin / ts_argmax / ts_zscore(x,w,m) | 原单位/会话、原单位、会话位置、会话位置、比例 |
| 截面 5 项 | cs_rank / cs_zscore / cs_demean(x,m)、cs_winsorize(x,lo,hi,m)、cs_neutralize(y,exposure,m) | rank/zscore 为比例，其余保留输入单位 |
| 组内 3 项 | group_rank / group_zscore / group_demean(x,industry,m) | 比例、比例、原单位 |

w 为含当前会话的向后窗口，m 为明确给定的最小有效样本数；均为正整数，窗口最多 252。lag/return/diff 取当前与 n 会话前端点，return 为 `x/x_lag-1`；零分母产生空值。声明为非会话的格点不占时序窗口。股票池外值不进入计算，资产之间不串值。

标准差、协方差固定 ddof=0；相关至少需要两个有效配对点，常量列不能产生有效相关。排名用平均并列名次除以有效数。argmin/argmax 返回窗口从旧到新的零起始位置，并列取最早位置；部分窗口按实际窗口位置计。linear_decay 使用从旧到新 1..w 权重，缺失值移除相应权重后重新归一。slope 是数值对会话位置的一元带截距 OLS 斜率。

截面只使用同一 observed_at 的有效成员；组内再按当前已知分类分组。缩尾分位数线性插值。中性化为一项数值暴露加截距的 OLS 残差，至少三项配对样本；常量暴露/奇异矩阵输出空值和 DEGENERATE_EXPOSURE。缺失值不填零。算子的最小样本不足、常量方差等诊断与结果同返。

训练标准化通过 `fit_panel_standardizer` 固定 fit_start/fit_end、版本、均值、标准差和训练数据摘要；`transform_panel_standardizer` 只作用于 fit_end 之后的格点。训练数据变化、参数篡改拒绝复用；未来数据变化不改变已拟合参数。此变换是独立的训练工具，不另计入 24 个算子。

## 使用接口

### 用数据接口准备并复用面板

1. 在 **数据接口** 保存用户自己的行情连接。映射原始开高低收、成交量、`available_at` 和显式停牌状态 `suspended`。日历、财务和 ETF 等字段可以使用独立的连接。
2. 打开 **研究面板**。在 **行情快照** 中选择连接，填写股票或 ETF 标的、日期与币种，确认原始价格和成交量口径，再获取快照。
3. 在 **准备面板** 选择快照、标的、资产时区和日期。展开 **从数据接口添加字段附件**，选择连接并声明单位。每个字段来源至少映射 `instrument`、`timestamp` 和 `available_at`。时点值使用带时区的 ISO 时间。一份附件可以同时提供多项规则。
4. 选择交易日历、历史股票池、公司行动和交易规则。日历必须提供同一组明确的决策时间。面板的日期筛选与覆盖表使用 UTC。财务的公告时间和修订时间若晚于记录的可用时间，以较晚时点为准。
5. 选择想核对的因子或策略，然后预览。表中显示每个字段的单位、覆盖行数和可用时间范围。看到缺项时，先修复连接映射或更换附件，再重新预览。
6. 保存面板。通过 **检验因子**、**研究策略** 或 **从问题开始** 带入该面板。因子定义页也可以直接选择已保存面板。**加载为草稿** 可以复用面板的设置，再按新数据调整。

如果已保存的行情快照支持追加，使用 **为所选快照追加行情** 并填写新截止日。新快照沿用原连接、标的和单位。数据服务改写或补录历史行情时，完整获取一份新快照。这两种操作都会保留已经归档的研究。字段附件明确绑定到各自的快照，更新行情后为新快照获取附件。

纸面策略还需要实际成交额、每个会话的现金分红声明和涨跌停规则版本。已确认无分红的会话可提供 `cash_dividend: 0`。有价格限制的会话在同一规则附件中提供上下限。只有已确认无需价格限制的标的才使用 `limit_rule_version: "UNRESTRICTED"`。

### 通过合同使用研究算子

1. 通过既有用户 API 捕获并保存快照，明确价格、数量、复权口径。
2. 构造字段附件并调用 `POST /api/research/panels/fields`，请求为 `{"attachment": <完整附件>}`，取得不可变引用。
3. 显式提交 `POST /api/research/panels/evaluate`，包含 contract、attachment_refs、expressions 数组。例如同时比较 `ts_rank(close,20,20)` 与 `cs_rank(close,10)`。
4. 返回 panel-research.v1：合同、面板指纹、audit、各表达式数值/单位/语义版本/诊断和总结果指纹。JSON 空值表示不可计算，panel_status 单独表示数据面板就绪度；任何表达式无法计算时，顶层 status 为 NEEDS_DATA 并保留算子诊断。

两个 POST 均使用现有本地来源检查和 `_csrf`；GET 不执行研究。`SnapshotStore.build_panel` 从持久快照和字段引用重建；`evaluate_panel_request` 是 HTTP 与 worker 可调用的同一入口。当前接口为有限的确定性面板计算，不自动新建队列、改动策略政策或授权成交；结果明确 execution_permission=false。长期因子验证和组合工作沿用各自已审核流程。

每轮最多 24 个表达式，请求最多 32768 字节；最多 100 资产、10000 网格行、50000 网格字段或输出数值；总估算计算成本最多 500 万，结果 JSON 最多 4 MiB。达到限制会返回中文提示，请缩小范围。上游源码仅作为阅读参考，计算与用户数据入口不依赖上游安装或供应商 SDK。

本阶段参考与证据见[审查记录](reviews/2026-10-31-finathink-phase23-review.md)和[固定来源记录](research/phase23-source-study.json)。
