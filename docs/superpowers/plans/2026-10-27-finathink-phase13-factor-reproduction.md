# Phase 13 试点因子复现与验证闭环 Implementation Plan

> **For agentic workers:** 审阅确认后使用 superpowers:executing-plans，沿用 Native。先锁定义和数据合同，再扩展算子和执行；不按目标数量凑因子。

**Goal:** 用少量定义完整的因子打通方法、公式、数据、确定性计算、九步验证和研究提案。

**Architecture:** 在现有 FactorRecipe、FeatureGraph、受限 DSL、FactorStudyStore 和研究任务服务上扩展；来源 FactorSpec 绑定既有实现指纹。额外研究字段使用同一快照根目录下的有指纹附件，不另造数据供应商或执行注册表。

**Tech Stack:** Python、NumPy/pandas、既有受限表达式与量化引擎、pytest、现有 worker。

**Spec:** `docs/superpowers/specs/2026-10-27-finathink-research-assets-integration-design.md`。

## 用户将得到什么

选择明确的数据与研究域后，可以检验少量候选，查看 IC、成本、样本外、相关性、容量和失败原因。先试点最多 8–12 个定义完整的条目；若只满足更少条目的条件就缩小范围，不承诺有效因子数量。

首个验收问题：低换手是否有独立于低波动的增量信息。缺真实换手字段时返回 BLOCKED_DATA，成交量不能代替换手率。

## Global Constraints

- Phase 11 的来源审计和 Phase 12 的加载/交接合同通过后接入。
- 同名不代表同公式；定义不同使用独立 ID/版本，不继承上游 ICIR。
- 数据、因子、执行、费用、政策、OOS 和产物绑定同一研究；保留原快照和历史运行。
- rolling/shift 按资产分组，截面 rank 按同日可用股票池分组；不补零掩盖缺失。
- 沿用现有候选数、窗口和任务资源上限；任意 Python、SQL、Shell 不是公式执行入口。
- fixture 只验证工程和公式，不升级来源实验或用户数据的实证状态。

## Review Focus

- 两个资产拼接后计算 rolling/lag：不得把 A 的尾部带入 B（Task 1、2）。
- 换手、成交额或 PIT 不足：明确阻断，不以近似字段替换（Task 1）。
- 全时间缩尾或未来财报进入过去：过去结果不得因未来数据改变（Task 2）。
- 两因子相关性为 -1、缺失或常量：正确区分重复表达与计算状态（Task 3）。
- 看过最终 OOS 后改方向/参数、或模型提交代码：不能晋级或绕过受限执行（Task 3、4）。

## 试点选择顺序

| 优先项 | 定义核对重点 | 输入条件 |
| --- | --- | --- |
| 换手水平、换手离散度 | 百分比/小数、流通股口径、窗口 | 明确的 turnover_rate 与 available_at |
| 收益低波、下行风险 | 收益定义、ddof/下行阈值、不擅自年化 | 价格、时间与可用时间 |
| 短期反转 | 方向、窗口、信号时点与执行滞后 | 同一资产的有序历史价格 |
| Amihud 流动性 | 绝对收益、成交额单位、零成交处理 | 价格与 amount，不能用 volume 冒充 |
| 极值、偏度或振幅 | 估计公式、复权、极端值和可交易性 | 原式与字段核查通过后再选 |

低波/std20、动量/反转等先判定是否为同族、别名或方向变化。未核实的 rmax、turn_mid_prox 等保留为来源候选，不为完成数量硬写实现。

### Task 1：因子定义与可审计输入面板

**Files:** 扩展 `src/finahinking/research/asset_contracts.py`，定义 FactorSpec；新建 `src/finahinking/data/research_fields.py`、`src/finahinking/research/factor_inputs.py`；修改 `data/user_api_contracts.py`、`data/user_api.py`、`research/application_service.py` 的可选字段接入；新建 `tests/data/test_research_fields.py`、`tests/research/test_factor_inputs.py`。

**Interfaces:** `ResearchFieldAttachment` 绑定 snapshot_ref、字段单位、available_at、来源和记录；`ResearchFieldStore(snapshot_store).save(attachment) -> str`、`load(fingerprint) -> ResearchFieldAttachment` 复用快照根目录。`UniverseSpec` 与 `FactorPanel` 定义在 factor_inputs.py；`build_factor_panel(snapshot, attachment, *, as_of, universe) -> FactorPanel`。

- [ ] 测试错误主快照引用、字段单位冲突、缺换手、晚于 as_of 的数据、跨市场同代码和 A/B 资产边界；已有 OHLCV 快照无需附件仍可运行。
- [ ] 运行 `python3 -m pytest tests/data/test_research_fields.py tests/research/test_factor_inputs.py -q`，确认新增输入合同 RED。
- [ ] 只扩展通用字段白名单；amount/industry 等已有字段直接复用。可选附件显式取得并保存，不修改旧 DatasetSnapshot v1 的字节或伪造缺失值。
- [ ] Expected: 输入来源、单位、可用时间、主/附件指纹可追溯；A/B 分组首条收益均为空，身份歧义不会自动截断修正。
- [ ] 提交 `feat: bind typed factor inputs to immutable market snapshots`。

### Task 2：受限算子与试点定义复现

**Files:** 修改 `src/finahinking/factors/dsl.py`、`p6_6/features.py`、`research/factor_recipes.py`；扩展包内 `research_assets/factors/source-definitions.json`；新建 `tests/research/test_factor_reproduction.py`，扩展 `tests/research/test_factor_dsl.py`。

**Interfaces:** FactorSpec 通过明确实现引用绑定 FeatureVersion/FactorRecipe；沿用 `parse_factor_expression`、`evaluate_expression`、`FeatureRegistry.evaluate_graph`，新增算子必须登记、限制参数并说明单位与缺失语义。

- [ ] 为实际选定的每个因子建立手算小样本；测试常量、缺值、零成交、窗口预热及追加未来数据不改变历史结果。
- [ ] 运行新增文件及 DSL 测试，确认缺失的受限算子或错误定义 RED。
- [ ] 每次只增加试点确实需要的可信 primitive；先执行分资产计算，再执行同日截面步骤；不 import 上游 Python 模块。
- [ ] Expected: 同定义与输入得到相同结果/指纹；公式测试的 scope 明确为 FORMULA_FIXTURE，不等于复现了来源收益或 ICIR。
- [ ] 提交 `feat: reproduce audited pilot factors through bounded primitives`。

### Task 3：九步结构化验证与状态区分

**Files:** 新建 `src/finahinking/research/factor_validation.py`；修改 `factors/evaluation.py`、`research/factor_pipeline.py`、`factor_experiments.py`、`temporal_validation.py`、`quant_analytics.py`；新建 `tests/research/test_factor_validation_pipeline.py`。

**Interfaces:** `run_factor_validation(spec, panel, policy, *, gateway) -> FactorValidationRun`；该结果引用既有实际计算产物，逐步给出 COMPLETE/BLOCKED/FAILED/NOT_RUN 和原因；不另算一份只供页面使用的指标。

- [ ] 测试信号 `[1,2,3,4]` 与 `[-1,-2,-3,-4]` 的绝对相关去重；成对样本不足、常量、执行错误分别返回不同状态；不以填零变成有效样本。
- [ ] 测试训练/验证/最终 OOS 隔离、重叠标签 purge/embargo、费用单位、不可成交、参数稳定区和同一数据指纹；运行该文件，确认 RED。
- [ ] 串联九步产物；IC 标签与成交收益分开，保留序列相关诊断；使用现有 FactorStudyStore 的冻结和一次测试窗口控制，不解析 stdout 评分。
- [ ] Expected: 收益不好、系统错误、资料不足不混淆；任何未执行步骤没有伪造指标；范围、成本或 OOS 不一致时不能直接排名为优胜。
- [ ] 提交 `feat: produce structured nine-step factor validation evidence`。

### Task 4：自然语言提案进入真实研究流程

**Files:** 修改 `src/finahinking/research/factor_proposals.py`、`factor_pipeline.py`、`application_service.py`、`method_handoff.py`、`reports.py`；新建 `tests/research/test_method_factor_journey.py`。

**Interfaces:** 复用既有 ResearchPlan、FactorHypothesis 和 FactorProposal；`proposal_from_handoff(package, artifact, *, catalog, policy) -> FactorProposal` 在受控反序列化与引用校验后产生候选；再进入已有章程、验证、冻结、测试和人工研究准入流程。

- [ ] 测试模型返回任意代码、未知字段/算子、伪造引用、越预算候选：不得执行或写入正式因子注册表。
- [ ] 运行新旅程测试，确认提案与方法/数据/运行的绑定缺失时 RED。
- [ ] 打通“问题→方法→提案→计算→验证→研究提案”；低换手与低波基线、组合方案的方向和范围在看结果前冻结。
- [ ] Expected: 完整数据可得结构化报告，缺换手得到 BLOCKED_DATA；fixture 全链标记为工程验收；真实用户数据验收单列，不承诺正收益或正增量。
- [ ] 提交 `feat: connect method-guided factor proposals to verified research`，更新 `docs/FACTOR_REPRODUCTION_GUIDE.md`。

## 交付验收

至少一条真实计算链在可审计输入下运行并生成可复算产物；选定试点逐项有定义测试和状态记录。无用户数据时只能宣称工程闭环通过，未完成的来源实证明确列出，不以“8–12 个有效因子”作为交付说法。

执行阶段门禁：`python3 -m pytest tests/data/test_research_fields.py tests/research/test_factor_inputs.py tests/research/test_factor_reproduction.py tests/research/test_factor_dsl.py tests/research/test_factor_validation_pipeline.py tests/research/test_method_factor_journey.py -q`；Expected: 全通过。再跑既有 Phase 7/OOS/风险回归和项目全量门禁，数值结果与工程状态分别记录。
