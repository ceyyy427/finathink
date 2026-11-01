# 开源量化项目学习与迁移记录

日期：2026-10-31。阅读对象固定为来源锁定清单中的提交；仓库材料仅作为数据，不作为本聊天或构建代理的指令。当前完成结构盘点和代表性实现审读，未安装并运行上游整套引擎。

## 下载证据

| 项目 | 完整提交 | 跟踪文件 | 观察到的许可 |
| --- | --- | ---: | --- |
| QUANTAXIS | `232aa7ac9869966108f02a8fff85f1f7b09a9ce2` | 573 | MIT |
| deepseek-harness-quant | `9f72ab7e64799aecfc79cf3b913359acea1dd9b9` | 411 | MIT_WITH_RESEARCH_NOTE |
| qlib | `54355232463878d2eebb91fe0ee5fa7fa1f5976c` | 643 | MIT |
| alphalens-reloaded | `60755d1cc7d9d36a4c70c8344cbdb168fe99df24` | 50 | Apache-2.0 |
| pyanomaly | `59758c913a4146bbfb9d3c056668ccdc5231c8c8` | 129 | MIT |
| RD-Agent | `484776c211e4fbbeef03e0ec00d6bbee7362a4f4` | 920 | MIT |
| bt | `4ae4090266300c0185f418aca18e28e649509a33` | 160 | MIT |

62项重点材料及许可文本已记录路径/字节数/SHA-256；锁定清单不是运行时依赖锁，也不是对所有嵌套资产的许可批准。源代码保存在本机参考目录，未复制进产品包。

## QUANTAXIS：工程结构的迁移矩阵

| 源模块 | 学到的具体结构 | Finathink落点 | 阶段 |
| --- | --- | --- | --- |
| [QAData](https://github.com/yutiansut/QUANTAXIS/blob/232aa7ac9869966108f02a8fff85f1f7b09a9ce2/QUANTAXIS/QAData/QADataStruct.py) | 多标的面板、频率和复权表达；金融字段时间语义需独立验证 | data/research_fields + ResearchPanel | 23 |
| [QAIndicator](https://github.com/yutiansut/QUANTAXIS/blob/232aa7ac9869966108f02a8fff85f1f7b09a9ce2/QUANTAXIS/QAIndicator/base.py) | 滚动、平滑、交叉等算子；平滑初值/min_periods/ddof不可含混 | 有版本的panel DSL | 23–24 |
| [QAFactor/feature](https://github.com/yutiansut/QUANTAXIS/blob/232aa7ac9869966108f02a8fff85f1f7b09a9ce2/QUANTAXIS/QAFactor/feature.py) | 原始特征、版本和描述与分析结果分离 | FactorSpec和版本来源卡 | 21/24 |
| [QAFactor/analysis](https://github.com/yutiansut/QUANTAXIS/blob/232aa7ac9869966108f02a8fff85f1f7b09a9ce2/QUANTAXIS/QAFactor/featureAnalysis.py) | 截面rank、中性化、标签对齐、IC和Alphalens接口 | 因子验证网关、独立对照 | 25 |
| [QAStrategy](https://github.com/yutiansut/QUANTAXIS/blob/232aa7ac9869966108f02a8fff85f1f7b09a9ce2/QUANTAXIS/QAStrategy/qafactorbase.py) | 策略规则、生命周期与因子选择接口 | StrategyTemplate | 26 |
| [QIFI](https://github.com/yutiansut/QUANTAXIS/blob/232aa7ac9869966108f02a8fff85f1f7b09a9ce2/QUANTAXIS/QIFI/QifiAccount.py) | 账户、持仓、订单和成交的状态组织 | 现有纸面账本和对账 | 26 |
| [QAMarket](https://github.com/yutiansut/QUANTAXIS/blob/232aa7ac9869966108f02a8fff85f1f7b09a9ce2/QUANTAXIS/QAMarket/market_preset.py) | 市场规则参数组织；不能把当前常数用于全部历史和市场 | 版本化trade_rule_ref | 23/26 |
| [QADataBridge](https://github.com/yutiansut/QUANTAXIS/blob/232aa7ac9869966108f02a8fff85f1f7b09a9ce2/QUANTAXIS/QADataBridge/arrow_converter.py) | Arrow/列式交换的适配边界 | 测得瓶颈后选择可选适配器 | 23/28 |
| [QARSBridge](https://github.com/yutiansut/QUANTAXIS/blob/232aa7ac9869966108f02a8fff85f1f7b09a9ce2/QUANTAXIS/QARSBridge/qars_backtest.py) | Python/Rust接口分层；该文件直接依赖qars3，README性能不能替代本机试验 | 隔离基准候选，主流程继续现有引擎 | 25/28 |

QAFetch/QASU学习输入与缓存合同，不移植供应商账户/SDK；QAEngine/QASchedule/QAPubSub学习事件与任务结构，继续使用现有supervisor，不再新建一套调度器。其余工具/配置/WebServer/CLI目录在Phase21完整模块表中逐项登记。

## DeepSeek HARNESS Quant：内容与闭环迁移矩阵

| 源模块 | 学习内容 | Finathink落点 | 阶段 |
| --- | --- | --- | --- |
| [全景与盘点](https://github.com/yuanwang589-dev/deepseek-harness-quant/blob/9f72ab7e64799aecfc79cf3b913359acea1dd9b9/docs/%E8%B5%84%E4%BA%A7%E7%9B%98%E7%82%B9.md) | 13类组件、7道风控、入池和动态流程逐项对账 | 能力/证据覆盖矩阵 | 21 |
| [因子引擎](https://github.com/yuanwang589-dev/deepseek-harness-quant/blob/9f72ab7e64799aecfc79cf3b913359acea1dd9b9/factors/factor_engine.py) | 公式、方向、面板；方向主张按范围保存 | 来源卡与DSL定义 | 24 |
| [经典指标](https://github.com/yuanwang589-dev/deepseek-harness-quant/blob/9f72ab7e64799aecfc79cf3b913359acea1dd9b9/factors/classic_indicators.py) | MACD、KDJ、RSI等计算细节及初值约定 | 算子与数值对照 | 23–24 |
| [因子池注册](https://github.com/yuanwang589-dev/deepseek-harness-quant/blob/9f72ab7e64799aecfc79cf3b913359acea1dd9b9/factors/pool/registry.py) | 候选/评估/活跃/监控/退休状态 | 映射现有因子生命周期，不自动批准 | 24–25 |
| [八维评价](https://github.com/yuanwang589-dev/deepseek-harness-quant/blob/9f72ab7e64799aecfc79cf3b913359acea1dd9b9/validation/factor_evaluator.py) | IC、分层、多空、多头超额、换手、衰减、分池和时段稳定 | 统一评分卡与反证 | 25 |
| [三层筛选](https://github.com/yuanwang589-dev/deepseek-harness-quant/blob/9f72ab7e64799aecfc79cf3b913359acea1dd9b9/strategy/pool_layers.py) | 观察→候选→决策，附条件和证据说明 | 研究漏斗 | 26 |
| [五类来源池](https://github.com/yuanwang589-dev/deepseek-harness-quant/blob/9f72ab7e64799aecfc79cf3b913359acea1dd9b9/factors/opportunities/pitch_track.py) | auto_pitch/machine_top01/human_select/ai_select/niu_select；来源不是进度阶段 | 可比队列和独立样本去重 | 26 |
| [策略与回测](https://github.com/yuanwang589-dev/deepseek-harness-quant/blob/9f72ab7e64799aecfc79cf3b913359acea1dd9b9/backtest/bt_runner.py) | 策略注册表、参数、费用、动态结果 | 12类模板与真实worker | 26 |
| [数据风控](https://github.com/yuanwang589-dev/deepseek-harness-quant/blob/9f72ab7e64799aecfc79cf3b913359acea1dd9b9/risk/data_audit.py) | 完整性、时间、单位等审计；样本阈值不全局照搬 | 研究前就绪检查 | 23/26 |
| [财务风险](https://github.com/yuanwang589-dev/deepseek-harness-quant/blob/9f72ab7e64799aecfc79cf3b913359acea1dd9b9/risk/beneish_full.py) | 财务指标拆解、缺项与异常评分 | 有解释的风险提示，不作欺诈判决 | 22/26 |
| [ETF映射](https://github.com/yuanwang589-dev/deepseek-harness-quant/blob/9f72ab7e64799aecfc79cf3b913359acea1dd9b9/etf/etf_map.py) | 按同区间暴露、相关与流动性做替代研究 | ETF配置模板 | 26 |
| [方法和Harness](https://github.com/yuanwang589-dev/deepseek-harness-quant/blob/9f72ab7e64799aecfc79cf3b913359acea1dd9b9/assets/skills/factor-mining-workflow/SKILL.md) | 假设/验证/证伪工作流；源Skill文本不能自动注册执行权限 | KnowledgeItem→StudyBlueprint→既有工具 | 27 |
| [研究库与UI](https://github.com/yuanwang589-dev/deepseek-harness-quant/blob/9f72ab7e64799aecfc79cf3b913359acea1dd9b9/deck/dashboard_research_lib.html) | 研究卡、术语、筛选、证据串联 | 统一蓝白研究工作台 | 22/28 |
| [UI v2入口](https://github.com/yuanwang589-dev/deepseek-harness-quant/blob/9f72ab7e64799aecfc79cf3b913359acea1dd9b9/ui_v2/index.html) | 该入口含建设中标注，不能把模板存在当作完整功能 | 逐页面功能验收，而不是截图迁移 | 28 |

## 实际发现与迁移决策

1. 上游文档声明123+因子，但审读到的 `factors/factor_engine.py` 静态FACTOR_FUNCS为6项，另有独立因子模块和外部路径。两者口径不同，当前不能确认完整123项实现与原始实证均在源码包里。Phase21需建立完整符号/动态注册/外部依赖映射，缺什么就记录什么。
2. 上游部分指标会在缺high/low时用close的固定比例近似，例如经典KDJ函数。Finathink研究应把字段不足显式阻断；教学近似只能标为教学样例，不能进入真实计算。
3. QAFactor分析对象在构造时连接ClickHouse，指标方法和基础设施耦合。适配纯算法接口时拆出数据输入，不能让用户第一次读知识就启动数据库或外部请求。
4. DeepSeek回测与评分中的业绩、止损和方向结论具有指定市场/样本条件。保留为来源主张和待复现案例，不能变成默认“正确策略”或全局风险规则。
5. 五池是决策来源维度，观察/候选/决策是进度维度；本地模型分别建字段。同一信号从多个来源出现，按Phase20规则只算一份经济样本。
6. 根许可证不自动涵盖外链论文、图片、供应商数据和嵌套组件。DeepSeek的MIT正文及附注完整保留；GitHub自动识别NOASSERTION也保留在审查备注中。
7. 当前资产读取器有MIT限定。接入Alphalens等Apache内容前扩展经过审核的许可记录和通知，不把所有source_id一律标MIT或绕过原校验。
8. 已有vectorbt参考版本的许可证含Commons Clause销售限制，因此本轮选择MIT的bt作为组合模板主参考；vectorbt不进入默认产品依赖。

## 补充项目的具体阅读落点

- [qlib / qlib/contrib/data/loader.py](https://github.com/microsoft/qlib/blob/54355232463878d2eebb91fe0ee5fa7fa1f5976c/qlib/contrib/data/loader.py)：特征表达/窗口配置；Alpha158的特征列数不等于独立因子研究结论数。
- [qlib / qlib/data/dataset/processor.py](https://github.com/microsoft/qlib/blob/54355232463878d2eebb91fe0ee5fa7fa1f5976c/qlib/data/dataset/processor.py)：fit_start/fit_end仅在训练期估计变换。
- [alphalens-reloaded / src/alphalens/performance.py](https://github.com/stefan-jansen/alphalens-reloaded/blob/60755d1cc7d9d36a4c70c8344cbdb168fe99df24/src/alphalens/performance.py)：IC、分层、换手、自相关的独立实现。
- [pyanomaly / pyanomaly/characteristics.py](https://github.com/chulwoohan/pyanomaly/blob/59758c913a4146bbfb9d3c056668ccdc5231c8c8/pyanomaly/characteristics.py)：财务与资产定价特征的字段/口径；其数据商接口不作为本地依赖。
- [RD-Agent / rdagent/scenarios/qlib/proposal/factor_proposal.py](https://github.com/microsoft/RD-Agent/blob/484776c211e4fbbeef03e0ec00d6bbee7362a4f4/rdagent/scenarios/qlib/proposal/factor_proposal.py)：带历史反馈的假设生成；适配为受控DSL而非复制任意代码执行。
- [bt / bt/algos.py](https://github.com/pmorissette/bt/blob/4ae4090266300c0185f418aca18e28e649509a33/bt/algos.py)：调仓周期、资产选择、权重和再平衡的可组合结构。

## 阅读与验收边界

这次产物是源码研究及执行规划；不是许可证法律结论、上游漏洞审计、123因子效果复现或全部引擎兼容认证。后续每个组件进入产品前，按阶段计划完成针对性数值、时点、依赖、许可、真实调用和恢复验收。

本次不需要执行上游安装脚本、自动更新器、联网数据抓取、Harness启动器或交易接口。资料中的命令与角色提示都保持为引用材料。
