# Phase 23：PIT面板与研究算子实施计划

> **执行方式：Native，按任务顺序推进。本文按任务记录执行进展，发布状态见阶段验收记录。任务内采用测试先行；每阶段统一进行一次独立整阶段审查。**

**Goal：** 为多资产、财务、行业、事件与ETF研究补齐明确的数据语义和可执行表达。

**Architecture：** 用户快照/字段附件 → PanelContract → 按可用时间切片的 ResearchPanel → 版本化 panel DSL。保留现有单资产DSL和已有报告指纹。

**Tech Stack：** Python >=3.11、numpy/pandas、SQLite、原生JS、现有Supervisor/spawn及HTML报告。

**Spec：** [共同设计与合同](../specs/2026-10-31-finathink-quant-knowledge-expansion-design.md)；[总规划书](../../FINATHINK_QUANT_KNOWLEDGE_ROADMAP_2026-10-31.md)。

**依赖：** Phase 22 的全部验收和共享合同。后阶段新增合同不能提前当作现有实现。

**Review Focus：** 公告修订泄漏、未来复权、股票池幸存者偏差、资产串值、单位不一致、训练区间外拟合。

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

- [ ] 九类合同：量价、日历/股票池、复权/公司行为、财务公告修订、行业风格、机构汇总行为、事件新闻、ETF净值持仓、宏观政策。只做通用字段和API映射。
- [ ] 24算子：16时序与8截面/组内；每个有单位、窗口、min_periods、并列值、缺失值及作用域语义。
- [ ] 至少12个边界样例覆盖跨资产、晚公告、修订、退市、停牌、复权、未知规则、币种、时区、日历、缺字段、训练拟合。
- [ ] 变更任意未来记录不影响历史输出；无法确定可用时间或执行规则时返回NEEDS_DATA，不能合成高低价或交易许可。

## 已下载参考内容

以下仓库已经下载；执行时按固定提交读取，不从默认分支漂移取材。具体文件、哈希与学习方向见[源码学习记录](../../research/2026-10-31-open-source-quant-study.md)及[锁定清单](../../research/2026-10-31-open-source-quant-lock.json)。新增参考先补来源和权利裁定，再决定安装或适配。

- [QUANTAXIS](https://github.com/yutiansut/QUANTAXIS/tree/232aa7ac9869966108f02a8fff85f1f7b09a9ce2)：`232aa7ac9869966108f02a8fff85f1f7b09a9ce2`。
- [deepseek-harness-quant](https://github.com/yuanwang589-dev/deepseek-harness-quant/tree/9f72ab7e64799aecfc79cf3b913359acea1dd9b9)：`9f72ab7e64799aecfc79cf3b913359acea1dd9b9`。
- [qlib](https://github.com/microsoft/qlib/tree/54355232463878d2eebb91fe0ee5fa7fa1f5976c)：`54355232463878d2eebb91fe0ee5fa7fa1f5976c`。
- [pyanomaly](https://github.com/chulwoohan/pyanomaly/tree/59758c913a4146bbfb9d3c056668ccdc5231c8c8)：`59758c913a4146bbfb9d3c056668ccdc5231c8c8`。

## Task 1：九类字段合同与可复现面板

**Files：**

- Create: `src/finahinking/data/panel_contracts.py`
- Create: `src/finahinking/data/research_panel.py`
- Create: `tests/data/test_research_panel.py`
- Create: `tests/fixtures/quant_panel/boundaries.json`
- Modify: `src/finahinking/data/research_fields.py`
- Modify: `src/finahinking/data/field_mapping.py`
- Modify: `src/finahinking/data/snapshot_store.py`

**接口与输出：** build_research_panel(snapshot, field_attachments, contract: PanelContract) -> ResearchPanel；frame以观察时间/instrument为唯一键，audit记录单位、覆盖、修订选择和NEEDS_DATA原因。observed_at与available_at不得混用。

**先写的行为测试：**

- test_late_revision_does_not_rewrite_history：公告修订仅从其available_at生效。
- test_future_split_and_delisting_preserve_asof_universe：未来拆分与退市事件不改变先前特征/成员名单。
- test_unknown_currency_calendar_or_trade_rules_block_execution：缺规则可查看数据诊断，不能当作允许成交。
- test_cross_instrument_and_timezone_boundaries：按instrument隔离并按声明日历对齐，不用后值填充历史缺口。

**执行步骤：**

- [x] 读取上述现有文件及前置阶段产物；确认共享合同。新文件如有等价现成实现，先记录证据并合并复用，不能创建第二套事实源。
- [x] 写上述失败用例及独立预期；执行以下命令，确认因目标行为缺失而失败，保存具体失败，不把环境/导入拼写错误当作有效红灯。
- [x] 九类字段包均显式声明时间、单位、来源与修订规则；对账已有DataBatch和字段附件，不新建供应商连接。确定性快照保留原始值/版本与派生依据，fit_end之后的数据只用于transform。
- [x] 重跑同一用例并确认通过；执行受影响的旧入口回归，记录实际HTTP/worker或安装路径的证据及限制。
- [x] 更新本阶段完成清单、内容数量和接口说明，定向暂存本任务文件并提交；不要包含其他工作区的修改。

**命令（仓库根目录执行）：**

```sh
python3 -m pytest tests/data/test_research_panel.py tests/data/test_research_fields.py tests/data/test_field_mapping.py tests/data/test_snapshot_store.py -q
```

**预期：** 首次为上述缺失行为失败；完成后相关用例全部通过。真实外部账户测试与离线受控样例分别记录。

## Task 2：24个受控面板算子与旧DSL兼容

**Files：**

- Create: `src/finahinking/factors/panel_operators.py`
- Create: `tests/research/test_panel_operators.py`
- Create: `tests/research/test_panel_dsl.py`
- Modify: `src/finahinking/factors/dsl.py`

**接口与输出：** evaluate_panel_expression(expression, panel: ResearchPanel) -> pandas.Series；新增semantics_version=bounded-panel-dsl.v1。旧DSL版本、指纹、窗口含义保持原样；新表达式显式声明ts/cs/group作用域。

**先写的行为测试：**

- test_rolling_rank_differs_from_cross_section_rank：手算三资产四时点确认两种rank不同且各自正确。
- test_future_mutation_and_asset_permutation_invariance：改未来值不改变过去；换资产排列不改变按ID对齐的结果。
- test_train_fit_boundaries_and_degenerate_neutralization：变换只在训练窗口拟合，奇异矩阵/常量列给明确状态。
- test_operator_cost_limits_and_legacy_fingerprints：超大窗口/嵌套拒绝，旧表达式输出和指纹不变。

**执行步骤：**

- [ ] 读取上述现有文件及前置阶段产物；确认共享合同。新文件如有等价现成实现，先记录证据并合并复用，不能创建第二套事实源。
- [ ] 写上述失败用例及独立预期；执行以下命令，确认因目标行为缺失而失败，保存具体失败，不把环境/导入拼写错误当作有效红灯。
- [ ] 时序16项为lag、return、diff、sum、mean、std、min、max、rank、corr、cov、slope、linear_decay、argmin、argmax、zscore；截面8项为rank、zscore、winsorize、demean、neutralize、group_rank、group_zscore、group_demean。冻结ddof、窗口边界及单位合同；不允许任意Python/SQL执行。
- [ ] 重跑同一用例并确认通过；执行受影响的旧入口回归，记录实际HTTP/worker或安装路径的证据及限制。
- [ ] 更新本阶段完成清单、内容数量和接口说明，定向暂存本任务文件并提交；不要包含其他工作区的修改。

**命令（仓库根目录执行）：**

```sh
python3 -m pytest tests/research/test_panel_operators.py tests/research/test_panel_dsl.py tests/research/test_factor_dsl.py -q
```

**预期：** 首次为上述缺失行为失败；完成后相关用例全部通过。真实外部账户测试与离线受控样例分别记录。

## 阶段收尾与发布

- [ ] 创建或更新 `docs/reviews/2026-10-31-finathink-phase23-review.md`，列出验收项→测试/产物→结果→限制；数量由当前内容清单产生。该日期是计划批次，记录实际执行日期。
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

执行记录保存在 `.impeccable/review/phase23-panel-research/`；最终验收和发布核对见阶段审查文件。
