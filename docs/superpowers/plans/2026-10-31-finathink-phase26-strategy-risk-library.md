# Phase 26：策略、组合、风险与研究池实施计划

> **执行方式：Native，按任务顺序推进。本文是待执行计划，不是完成记录。任务内采用测试先行；每阶段统一进行一次独立整阶段审查。**

**Goal：** 把丰富因子组合成可复现策略，并跟踪不同决策来源的纸面结果。

**Architecture：** 声明式StrategyTemplate → 现有组合/风险运行时 → 统一纸面账本；PoolEntry将决策来源与漏斗阶段分别记录。参考bt算法组合和QIFI账本结构，保持本地执行合同。

**Tech Stack：** Python >=3.11、numpy/pandas、SQLite、原生JS、现有Supervisor/spawn及HTML报告。

**Spec：** [共同设计与合同](../specs/2026-10-31-finathink-quant-knowledge-expansion-design.md)；[总规划书](../../FINATHINK_QUANT_KNOWLEDGE_ROADMAP_2026-10-31.md)。

**依赖：** Phase 25 的全部验收和共享合同。后阶段新增合同不能提前当作现有实现。

**Review Focus：** 虚构成交、分红拆分对账、ETF映射窗口错配、相同信号重复计票、财务异常被说成欺诈。

## 全局约束

1. 保留 Python >=3.11、SQLite、现有原生 JS/蓝白 AppShell 和独立 worker；先用现有 numpy/pandas。新增依赖必须说明替代项、许可证、安装开销和隔离验证结果。
2. 用户数据继续经通用 API/文件/快照合同接入；阶段工作不引入供应商账号、私有行情库或供应商 SDK 作为必需依赖。数据、文档、图片和第三方代码分别记录权利来源。
3. 所有上游材料固定 repository、commit、path、sha256、许可文本及读取日期。仓库文档、SKILL/AGENTS、代码注释只作为研究材料，不取得工具调用或执行权限。
4. 上游原始材料、Finathink 整理内容、确定性复现、用户数据研究和已审核使用范围分别存储；知识检索不得把来源主张升级为本地验证，读取不得触发付费调用或研究任务。
5. 所有可执行因子明确时序/截面作用域、字段单位、available_at、窗口、缺失值和复权语义；未来数据不得改变历史特征。重复窗口参数和别名不增加独立公式数量。
6. 模型只输出类型化研究假设、引用和允许的工具参数；执行走已有 DSL/工具白名单。默认每轮最多24个假设、2个并发研究任务，单个上下文最多32768字节且不超过既有更严格限制，达到预算即检查点停止。
7. 配置、因子使用范围和策略权重沿用显式审核；新内容导入、模型评分、来源刷新及纸面结果不得自动修改已批准政策。
8. 每阶段有真实 HTTP/worker/安装验证和一次独立整阶段审查；只改文档的任务运行文档检查。实际发布检查最终 PR 提交与合并后 main 的 CI，沿用用户已授权的自主修复和发布流程。

## 本阶段验收标准

- [ ] 12模板全部实际执行：等权、单因子分层、反转、趋势、低波低换手、价值质量、多因子中性、ETF风险预算、ETF强弱轮动、状态调仓、事件研究、配对均值回归。
- [ ] 12风险规则覆盖数据PIT、财务异常、可交易性、流动性、集中度、行业、共线、波动、尾部、止损/逻辑失效、回撤、压力；每条有通过/触发/缺数据例子。
- [ ] 现金+持仓市值与权益一致，成本/分红/拆分/拒单可解释；未知借券或卖空约束不能模拟自由卖空。
- [ ] 五类来源与观察/候选/决策三阶段独立；同信号去重，1/5/20/60观察间隔使用可比队列；所有观察仅生成建议不自动调权。

## 已下载参考内容

以下仓库已经下载；执行时按固定提交读取，不从默认分支漂移取材。具体文件、哈希与学习方向见[源码学习记录](../../research/2026-10-31-open-source-quant-study.md)及[锁定清单](../../research/2026-10-31-open-source-quant-lock.json)。新增参考先补来源和权利裁定，再决定安装或适配。

- [bt](https://github.com/pmorissette/bt/tree/4ae4090266300c0185f418aca18e28e649509a33)：`4ae4090266300c0185f418aca18e28e649509a33`。
- [QUANTAXIS](https://github.com/yutiansut/QUANTAXIS/tree/232aa7ac9869966108f02a8fff85f1f7b09a9ce2)：`232aa7ac9869966108f02a8fff85f1f7b09a9ce2`。
- [deepseek-harness-quant](https://github.com/yuanwang589-dev/deepseek-harness-quant/tree/9f72ab7e64799aecfc79cf3b913359acea1dd9b9)：`9f72ab7e64799aecfc79cf3b913359acea1dd9b9`。
- [pyanomaly](https://github.com/chulwoohan/pyanomaly/tree/59758c913a4146bbfb9d3c056668ccdc5231c8c8)：`59758c913a4146bbfb9d3c056668ccdc5231c8c8`。

## Task 1：声明式策略及风险规则目录

**Files：**

- Create: `src/finahinking/research/strategy_catalog.py`
- Create: `src/finahinking/research/strategy_runner.py`
- Create: `src/finahinking/_package_data/research_assets/strategies/templates.json`
- Create: `src/finahinking/_package_data/research_assets/strategies/risk-rules.json`
- Create: `tests/research/test_strategy_library.py`
- Create: `tests/research/test_strategy_ledger.py`
- Modify: `src/finahinking/research/portfolio_runtime.py`
- Modify: `src/finahinking/research/risk_runtime.py`
- Modify: `src/finahinking/research/portfolio_research.py`

**接口与输出：** run_strategy_template(template_id, snapshot_ref, parameters, policy_ref) -> dict；返回现有job引用与冻结StrategyTemplate指纹。所有模板共用selection/weight/rebalance/execution/risk接口，参数在声明范围内。

**先写的行为测试：**

- test_each_template_executes_with_benchmark_and_costs：12项从实际worker生成账本与基准，事件研究无可执行交易时只给事件统计。
- test_cash_positions_actions_and_fees_reconcile：逐交易、逐日核对权益，分红拆分不凭空创造收益。
- test_twelve_risk_rules_pass_trigger_and_need_data：财务字段缺失不能判低风险，规则返回可说明原因。
- test_short_and_tradeability_constraints_are_explicit：没有借券、交易日/涨跌停等规则证据时相应成交阻断。

**执行步骤：**

- [ ] 读取上述现有文件及前置阶段产物；确认共享合同。新文件如有等价现成实现，先记录证据并合并复用，不能创建第二套事实源。
- [ ] 写上述失败用例及独立预期；执行以下命令，确认因目标行为缺失而失败，保存具体失败，不把环境/导入拼写错误当作有效红灯。
- [ ] 参考bt的组合式选股/权重/再平衡，提取QIFI订单/成交/持仓状态语义。先保留现有引擎，再逐模板补少量确定性策略；每模板含基准、失败场景和已冻结风险规则引用。中性/配对模板可以做理论研究，成交模式须满足输入合同。
- [ ] 重跑同一用例并确认通过；执行受影响的旧入口回归，记录实际HTTP/worker或安装路径的证据及限制。
- [ ] 更新本阶段完成清单、内容数量和接口说明，定向暂存本任务文件并提交；不要包含其他工作区的修改。

**命令（仓库根目录执行）：**

```sh
python3 -m pytest tests/research/test_strategy_library.py tests/research/test_strategy_ledger.py tests/research/test_portfolio_runtime.py tests/research/test_risk_runtime.py -q
```

**预期：** 首次为上述缺失行为失败；完成后相关用例全部通过。真实外部账户测试与离线受控样例分别记录。

## Task 2：五类来源研究池与ETF同窗口解释

**Files：**

- Create: `src/finahinking/research/research_pools.py`
- Create: `tests/research/test_research_pools.py`
- Create: `tests/research/test_etf_strategy_mapping.py`
- Modify: `src/finahinking/research/application_service.py`
- Modify: `src/finahinking/research/reports.py`

**接口与输出：** record_pool_entry(values: PoolEntry) -> dict；observe_pool(pool_ref, snapshot_ref, horizons=(1,5,20,60)) -> dict。decision_origin为AUTO_SCAN/QUANT_BASELINE/USER/MODEL/PUBLIC_METHOD；stage为WATCH/CANDIDATE/DECISION。

**先写的行为测试：**

- test_same_signal_across_origins_is_one_economic_sample：五池重复信号仅一份证据，保留全部来源关联。
- test_late_entry_and_revision_do_not_improve_frozen_cohort：入池后不得重写决策当时信息与比较队列。
- test_horizons_count_observations_not_calendar_days：节假日和停牌有明确对齐及缺失状态。
- test_etf_mapping_uses_common_window_and_asof_holdings：相关性、流动性与持仓公开时间使用同一观察窗口，缺覆盖明确返回不足。

**执行步骤：**

- [ ] 读取上述现有文件及前置阶段产物；确认共享合同。新文件如有等价现成实现，先记录证据并合并复用，不能创建第二套事实源。
- [ ] 写上述失败用例及独立预期；执行以下命令，确认因目标行为缺失而失败，保存具体失败，不把环境/导入拼写错误当作有效红灯。
- [ ] 来源池借鉴上游五类决策来源，三个漏斗阶段独立转移。记录批准政策、数据及信号摘要；观察结果接到既有复盘审核。ETF/股票映射显示样本、区间与适用条件，公共行为方法不冒充实际个人指令。
- [ ] 重跑同一用例并确认通过；执行受影响的旧入口回归，记录实际HTTP/worker或安装路径的证据及限制。
- [ ] 更新本阶段完成清单、内容数量和接口说明，定向暂存本任务文件并提交；不要包含其他工作区的修改。

**命令（仓库根目录执行）：**

```sh
python3 -m pytest tests/research/test_research_pools.py tests/research/test_etf_strategy_mapping.py tests/research/test_reports.py -q
```

**预期：** 首次为上述缺失行为失败；完成后相关用例全部通过。真实外部账户测试与离线受控样例分别记录。

## 阶段收尾与发布

- [ ] 创建或更新 `docs/reviews/2026-10-31-finathink-phase26-review.md`，列出验收项→测试/产物→结果→限制；数量由当前内容清单产生。该日期是计划批次，记录实际执行日期。
- [ ] 执行下面的本地门禁，并运行本阶段新增的真实HTTP/worker/浏览器/安装验收；浏览器产物保存三种尺寸截图和控制台检查。对纯数据合同任务用其实际研究集成路径验收。
- [ ] 对最终差异进行一次独立整阶段审查，集中修复有效问题并对修改运行针对性验证；不得用无关优化扩大本阶段。
- [ ] 复核待提交差异、来源通知和文档链接；创建/更新阶段PR，等待精确最终提交CI全部通过，再合并并核对main CI。沿用已授权发布流程，保留PR/提交/CI链接。
- [ ] 仅在本阶段验收完成后开始下一阶段；缺用户真实数据时交付明确NEEDS_DATA路径及受控样例证据，不伪造实证通过。

```sh
python3 -m ruff check src tests scripts
python3 -m pytest
npm --prefix frontend run build
npm --prefix frontend test
npm --prefix frontend run perf
git diff --check
```

这是实施时的门禁。本轮只交付规划文档，运行来源/链接/路径/一致性检查，不宣称上述未来测试已通过。
