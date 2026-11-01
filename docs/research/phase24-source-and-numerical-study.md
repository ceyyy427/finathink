# Phase24 固定来源与独立数值记录

实际读取：2026-11-01。只解析固定Git对象，不导入或执行上游模块。

本批次 162 张来源卡，80 个独立实现，十家族分布：`{'trend_momentum': 8, 'reversal': 4, 'volatility_tail': 6, 'liquidity_turnover': 6, 'value': 12, 'quality_profitability_accrual': 16, 'growth_investment': 16, 'industry_style': 4, 'event_institutional': 4, 'etf_macro': 4}`。

## 计数与适配边界

每个窗口只登记一项；RSI的上涨/下跌互补项、Alpha158重复窗口、符号翻转不重复计数。上影、下影、实体和收盘位置是来源明确列出的不同OHLC几何量；经营资产、经营负债与净投入是文献明确区分的经济定义，不把单字段机械套算子作为新增定义。行业相对值改变研究比较对象，保留相应独立文献来源。

所有财务币种为声明的同一CNY；这里的固定币种是明确参数合同，不能直接混用美元。经营现金流/净利润等由用户按相同财务期间提供。增长、发行和研发折耗沿观察序列计算；应使用可比财务期观测，日频前向延续值不构成年度增长证据。

上游fillna(0)、伪造高低价、未来全样本标准化和未审查评分均未带入。标准差统一ddof=0。ETF beta修正原始cov/var的自由度不一致。来源样本胜率、来源论文结论与来源方向仅保留为来源主张，不属于本地验证。

独立数值预期在调用生产模块前冻结：八观察点、三资产，按Python标准库标量算术、总体矩与明确行业组计算；无pandas/numpy/生产DSL/上游执行。生成脚本与冻结JSON一并保留以便复核，运行时不生成预期。

## 文件级来源锁

| 项目 | 固定提交 | 路径 | SHA-256 |
|---|---|---|---|
| deepseek-harness-quant | 9f72ab7e64799aecfc79cf3b913359acea1dd9b9 | `factors/classic_indicators.py` | `2d6901559c44ee7edd96d25aa42fbdd176851f9eaf5e1b4959a45802cac97110` |
| qlib | 54355232463878d2eebb91fe0ee5fa7fa1f5976c | `qlib/contrib/data/loader.py` | `814b7f7ab3d418ae3c87ce352220080b239eba2670eac9e38376b794be4075cb` |
| pyanomaly | 59758c913a4146bbfb9d3c056668ccdc5231c8c8 | `pyanomaly/characteristics.py` | `c9ad1d14b61fae774d1a7446609971225fb0a709f0e0da7dc998d7aead32bc20` |
| QUANTAXIS | 232aa7ac9869966108f02a8fff85f1f7b09a9ce2 | `QUANTAXIS/QAIndicator/indicators.py` | `94995068dbe73fdeddfea69bd51c0df52af6fc4567c5c432a265c2e3f9363ff5` |
| deepseek-harness-quant | 9f72ab7e64799aecfc79cf3b913359acea1dd9b9 | `etf/etf_map.py` | `cacefdb73a9b88e733c894a01aa1c4aa0563d53377a9a1f667b2f1d300910f1a` |
| deepseek-harness-quant | 9f72ab7e64799aecfc79cf3b913359acea1dd9b9 | `factors/policy/policy_score.py` | `7073b0a6ce7d78d813afc7516fcabe5b29ce8fb31c9b0809ccad91a07ff9223b` |
| deepseek-harness-quant | 9f72ab7e64799aecfc79cf3b913359acea1dd9b9 | `factors/policy/timing_system.py` | `4ff50b4454233512d108946b5ca67fc61d8d28ca40709ccf576cb2bf4cbd7248` |
| deepseek-harness-quant | 9f72ab7e64799aecfc79cf3b913359acea1dd9b9 | `LICENSE` | `423beebd76cc3b1b060c5ed5992d961fa282263c7f93aa01ae25d7f9a41072b4` |
| qlib | 54355232463878d2eebb91fe0ee5fa7fa1f5976c | `LICENSE` | `c2cfccb812fe482101a8f04597dfc5a9991a6b2748266c47ac91b6a5aae15383` |
| pyanomaly | 59758c913a4146bbfb9d3c056668ccdc5231c8c8 | `LICENSE.txt` | `84dc4ddcfd08d382bbe4ac775ebee4da7e40280b8aba4f841884f0234303f6f4` |
| QUANTAXIS | 232aa7ac9869966108f02a8fff85f1f7b09a9ce2 | `LICENSE` | `96ebf515d25a58bbd8be4543a5c198e576db52738f26c82270ececb1dc273318` |

来源卡含经AST规范化的原始源码摘录，属于ATTRIBUTED_SOURCE_EXCERPTS而非纯原创叙述；四个MIT原文及版权声明随第三方许可目录保留，DeepSeek附加研究教育说明同时保留。Finathink DSL、中文解释与独立数值样例是本项目整理实现。

所有文件的逐项起止行、原始公式片段、固定提交链接、许可证路径及读取日期保存在 `factors/library.json`。参考项没有 `factor_id`，只有已独立实现的项才绑定公式。

## 80 项经济定义与差异

| ID | 经济定义 | 来源卡 |
|---|---|---|
| `lib-trend-roc` | 本期价格相对上一观察价格的比例变化 | `deepseek-harness-quant-roc` |
| `lib-trend-ma-spread` | 短均线相对长均线的价格趋势 | `deepseek-harness-quant-ma-bull` |
| `lib-trend-slope` | 价格线性拟合每观察期斜率相对当前价格 | `qlib-get-feature-config-beta` |
| `lib-trend-body` | 开盘到收盘的有符号实体比例 | `qlib-get-feature-config-kmid` |
| `lib-trend-upper-shadow` | 当期最高价超过实体上沿的比例 | `qlib-get-feature-config-kup` |
| `lib-trend-lower-shadow` | 实体下沿超过当期最低价的比例 | `qlib-get-feature-config-klow` |
| `lib-trend-close-location` | 收盘相对高低区间中点的有符号距离 | `qlib-get-feature-config-ksft` |
| `lib-trend-aroon-spread` | 窗口最高价出现位置与最低价出现位置的差 | `qlib-get-feature-config-imxd` |
| `lib-reversal-rsi` | 上涨均幅相对下跌均幅的振荡强度 | `deepseek-harness-quant-rsi` |
| `lib-reversal-boll-lower` | 当前价格相对均值减两倍波动的下轨偏离 | `deepseek-harness-quant-boll-lower-dev` |
| `lib-reversal-williams` | 收盘到窗口高点的距离相对高低区间 | `deepseek-harness-quant-wr` |
| `lib-reversal-cci` | 典型价格相对均值按历史滚动绝对偏离尺度标准化 | `deepseek-harness-quant-cci` |
| `lib-volatility-return-std` | 窗口内简单收益率总体离散度 | `pyanomaly-c-retvol` |
| `lib-volatility-maximum-return` | 近期最大的单期上涨尾部收益 | `pyanomaly-c-rmax1-21d` |
| `lib-volatility-intraday-range` | 当期高低价跨度相对开盘价格 | `qlib-get-feature-config-klen` |
| `lib-volatility-atr` | 日内区间与隔夜跳空共同决定的平均真实价格波幅 | `quantaxis-qa-indicator-atr` |
| `lib-volatility-price-dispersion` | 价格水平标准差相对现价，与收益率波动不同 | `qlib-get-feature-config-std` |
| `lib-volatility-volume-weighted-move` | 绝对价格变动乘成交股数后的相对离散度 | `qlib-get-feature-config-wvma` |
| `lib-liquidity-amihud` | 每单位成交金额对应的绝对价格变动 | `pyanomaly-c-ami-126d` |
| `lib-liquidity-turnover` | 按同一自由流通股口径计算的成交占比 | `pyanomaly-c-turnover-126d` |
| `lib-liquidity-turnover-volatility` | 流通换手需求在观察期内的不稳定程度 | `pyanomaly-c-std-turn` |
| `lib-liquidity-dollar-volume` | 成交容量的金额尺度 | `pyanomaly-c-dolvol-126d` |
| `lib-liquidity-price-volume-correlation` | 价格水平与对数成交量同步程度 | `qlib-get-feature-config-corr` |
| `lib-liquidity-change-volume-correlation` | 价格相邻比值与对数量比的同步性 | `qlib-get-feature-config-cord` |
| `lib-value-book-market` | 普通股账面权益相对股权市值 | `pyanomaly-c-be-me` |
| `lib-value-assets-market` | 总资产相对股权市值 | `pyanomaly-c-at-me` |
| `lib-value-earnings-yield` | 可比期间净利润相对股权市值 | `pyanomaly-c-ni-me` |
| `lib-value-sales-yield` | 可比期间销售收入相对股权市值 | `pyanomaly-c-sale-me` |
| `lib-value-cashflow-yield` | 经营现金流相对股权市值 | `pyanomaly-c-ocf-me` |
| `lib-value-rd-yield` | 研发费用相对股权市值 | `pyanomaly-c-rd-me` |
| `lib-value-debt-market` | 有息债务相对股权市值 | `pyanomaly-c-debt-me` |
| `lib-value-free-cashflow-yield` | 经营现金流扣资本开支后相对市值 | `pyanomaly-c-fcf-me` |
| `lib-value-enterprise-book-market` | 净经营资产账面值相对企业市场价值 | `pyanomaly-c-bev-mev` |
| `lib-value-payout-yield` | 现金分红与股份回购合计相对市值 | `pyanomaly-c-eqpo-me` |
| `lib-value-enterprise-ebitda-yield` | 息税折旧摊销前利润相对企业价值 | `pyanomaly-c-ebitda-mev` |
| `lib-value-netdebt-market` | 扣现金后的债务相对股权市值 | `pyanomaly-c-netdebt-me` |
| `lib-quality-operating-margin` | 息税前利润相对销售收入 | `pyanomaly-c-ebit-sale` |
| `lib-quality-gross-profit-assets` | 毛利润相对总资产 | `pyanomaly-c-gp-at` |
| `lib-quality-roe` | 净利润相对账面权益 | `pyanomaly-c-ni-be` |
| `lib-quality-return-enterprise` | 息税前利润相对净经营资产 | `pyanomaly-c-ebit-bev` |
| `lib-quality-accrual-assets` | 经营应计金额相对总资产 | `pyanomaly-c-oaccruals-at` |
| `lib-quality-operating-leverage` | 经营成本相对资产规模 | `pyanomaly-c-opex-at` |
| `lib-quality-enterprise-turnover` | 销售收入相对净经营资产 | `pyanomaly-c-sale-bev` |
| `lib-quality-rd-sales` | 研发投入相对销售收入 | `pyanomaly-c-rd-sale` |
| `lib-quality-cash-assets` | 现金相对总资产 | `pyanomaly-c-cash-at` |
| `lib-quality-current-ratio` | 流动资产对流动负债覆盖 | `pyanomaly-c-currat` |
| `lib-quality-receivables-turnover` | 销售收入对期末应收账款尺度 | `pyanomaly-c-salerec` |
| `lib-quality-depreciation-ppe` | 折旧费用相对固定资产净额 | `pyanomaly-c-depr` |
| `lib-quality-cash-profit-assets` | 息税折旧摊销前利润加研发减应计后对资产的贡献 | `pyanomaly-c-cop-at` |
| `lib-quality-accrual-profit` | 经营应计金额相对净利润绝对规模 | `pyanomaly-c-oaccruals-ni` |
| `lib-quality-asset-turnover` | 销售收入相对相邻两期平均资产 | `pyanomaly-c-at-turnover` |
| `lib-quality-quick-ratio` | 排除存货后的流动资产对流动负债覆盖 | `pyanomaly-c-quick` |
| `lib-growth-asset-growth` | 资产观察期增长衡量对应经济规模的扩张 | `pyanomaly-c-at-gr1` |
| `lib-growth-sales-growth` | 销售观察期增长衡量对应经济规模的扩张 | `pyanomaly-c-sale-gr1` |
| `lib-growth-capex-growth` | 资本支出观察期增长衡量对应经济规模的扩张 | `pyanomaly-c-capx-gr1` |
| `lib-growth-inventory-investment` | 存货投资变化相对当期总资产 | `pyanomaly-c-inv-gr1a` |
| `lib-growth-long-investment` | 长期投资变化相对当期总资产 | `pyanomaly-c-lti-gr1a` |
| `lib-growth-short-investment` | 短期投资变化相对当期总资产 | `pyanomaly-c-sti-gr1a` |
| `lib-growth-current-operating-assets` | 流动资产扣现金后经营资本的变化 | `pyanomaly-c-coa-gr1a` |
| `lib-growth-current-operating-liabilities` | 流动负债扣短债后经营融资的变化 | `pyanomaly-c-col-gr1a` |
| `lib-growth-working-capital` | 经营流动资产减经营流动负债的净投入变化 | `pyanomaly-c-cowc-gr1a` |
| `lib-growth-noncurrent-operating-assets` | 资产扣流动资产和长期金融投资后的变化 | `pyanomaly-c-ncoa-gr1a` |
| `lib-growth-noncurrent-operating-liabilities` | 负债扣流动负债与长期金融债后的变化 | `pyanomaly-c-ncol-gr1a` |
| `lib-growth-net-noncurrent-operating` | 非流动经营资产扣经营负债后的净变化 | `pyanomaly-c-nncoa-gr1a` |
| `lib-growth-financial-liabilities` | 债务加优先股金融融资余额变化 | `pyanomaly-c-fnl-gr1a` |
| `lib-growth-net-financial-assets` | 金融投资扣金融负债后的净持有变化 | `pyanomaly-c-nfna-gr1a` |
| `lib-growth-rd-capital` | 最近五个观察期研发投入按线性折耗组成的资本存量 | `pyanomaly-c-rd5-at` |
| `lib-growth-abnormal-capex` | 本期资本支出收入比相对前三期均值的异常程度 | `pyanomaly-c-capex-abn` |
| `lib-industry-book-market` | 同业账面市值比均值之外的相对估值 | `pyanomaly-c-bm-ia` |
| `lib-industry-cashflow-market` | 同业现金流收益率均值之外的现金回报差 | `pyanomaly-c-cfp-ia` |
| `lib-industry-employment-growth` | 同业就业扩张均值之外的雇员规模变化 | `pyanomaly-c-chempia` |
| `lib-industry-size` | 股权规模相对同业均值的金额差 | `pyanomaly-c-mve-ia` |
| `lib-event-equity-finance` | 增发收入扣回购和分红后的外部股权融资 | `pyanomaly-c-eqnetis-at` |
| `lib-event-debt-finance` | 债券发行扣偿还并加短债变化的融资流量 | `pyanomaly-c-dbnetis-at` |
| `lib-event-share-issuance` | 拆股同口径股数的观察期增长 | `pyanomaly-c-chcsho-12m` |
| `lib-event-composite-issuance` | 市值变化扣含现金分红总收益后的复合融资变化 | `pyanomaly-c-eqnpo-60m` |
| `lib-etf-macro-correlation` | 已实现策略收益与ETF基准已实现收益的同步程度 | `deepseek-harness-quant-corr-stats` |
| `lib-etf-macro-beta` | 策略收益对ETF基准收益的线性敏感度 | `deepseek-harness-quant-corr-stats-beta` |
| `lib-etf-macro-term-spread` | 长期债券收益率减短期债券收益率 | `deepseek-harness-quant-load-panel` |
| `lib-etf-macro-social-finance-trend` | 近期社融增量均值相对较长窗口均值 | `deepseek-harness-quant-macro-score` |

## 独立数值验收记录

2026-11-01：`python3 -m pytest tests/research/test_factor_library_numerics.py -q`：170项通过。完整日志由阶段审查归档，临时原始日志 `/tmp/phase24-content-numerics.log`。覆盖80逐式预期、每个必需字段逐一移除/全缺失/错误单位、未来变更不影响历史、延迟财报/融资/宏观/行业字段、零分母和零方差。

首次实际数值运行发现3项失败、16项通过（工具输出chunk 598ee3）：slope与Amihud受控维度单位被通用敏感路径过滤拒绝；Aroon索引缩放的输出单位声明错误。前两项由核心修复单位合同，后一项明确为sessions。首次红灯未另存磁盘日志，因此不声称拥有事先留存的完整TDD红灯文件；上述记录来自真实执行工具输出。

可复核标量作者脚本：`python3 docs/research/phase24-build-content.py /path/to/pinned-reference-root`。脚本只使用标准库解析固定Git源码并进行独立标量运算，不导入Finathink或上游模块；重生成会更改资源摘要，需重新走资源登记与审查。

## 安装包内的独立复算输入

整阶段审查后，80式的 `numeric_example.inputs` 均直接包含最多6个观察点的 `times`、`field_units` 和 `instruments` 字段数组；时序例子保留AAA，截面和混合例子保留三资产。每式仅保留所需字段，安装包无需测试目录即可查看完整输入和预期。两个成交量对数式分别解释一股加项与无量纲量比加项，避免股、手、千股之间混用。

追加80项安装包资源独立复算测试，通过逐式对照冻结输入，再仅从资源中的输入构造研究面板并运行生产公式。最终该数值测试文件：250 passed in 11.22s；原始日志 `/tmp/phase24-content-numerics.log`。公式资源288061字节，低于1MiB读取预算。
