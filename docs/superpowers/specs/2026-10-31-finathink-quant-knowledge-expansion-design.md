# Finathink 量化知识与研究能力扩展设计

日期：2026-10-31。基线：已发布 Phase 20，main `f40d515a5f8ef6e5c7ddf49fcd6c36ae61581863`。本文件描述 Phase 21–28 的目标设计，不代表功能已经实现。

## 目标与设计选择

以 QUANTAXIS 的量化工程结构和 DeepSeek HARNESS Quant 的研究内容/工作流为两条主线，将开源知识转化为 Finathink 可查、可解释、可执行、可复现的研究资产。先检查既有实现，再选择直接引用、保留署名的代码适配、独立实现、隔离对照实验或仅保留来源条目；每个选择记录具体文件、提交和原因。

复用 `p7_5/knowledge.py` 的概念图、`p8_2b/` 的精讲单元、`ResearchAssetCatalog` 的资源校验、现有 FactorSpec/DSL、九步因子验证、独立 worker、版本审核和报告。新增统一索引及适配层，原目录、历史 ID、私有学习记录和旧报告仍可读取。统一索引不是第三套知识事实源；它只引用上述不可变内容和新的版本化内容包。

上游内容全面进入能力对照表，具有明确落地范围：数据接口/面板、指标/因子、评估、策略、组合/风控、研究池、决策卡、远期观察、知识/Skill、编排和展示。源码中的性能与收益数字是来源主张；只有对应输入、方法和输出在本地核验后才能获得本地证据标识。

## 当前内容基线

- 15 个基础概念、4 条基础学习路径、17 个领域覆盖声明；覆盖声明不等于各领域内容充足。
- 6 个 P8.2B 精讲单元，与基础概念按规范 ID 去重后计数，不能直接相加宣称 21 个独立主题。
- 12 个方法入口；52 个资源记录中 31 个已打包、15 个仅上游、6 个缺少本地内容。
- 27 个基础公式配方；治理入口有 5 个基础定义和 5 个家族试点，不将它们计为已验证的上游完整因子库。
- 来源目录有 10 个因子条目、2 个明确原定义、8 个待解析定义、14 条来源效果主张、6 条失败经验。来源效果的本地复现标识当前为 0；这与 Finathink 自有公式/工程测试是不同口径。

## 全局约束（各阶段计划共同适用）

1. 保留 Python >=3.11、SQLite、现有原生 JS/蓝白 AppShell 和独立 worker；先用现有 numpy/pandas。新增依赖必须说明替代项、许可证、安装开销和隔离验证结果。
2. 用户数据继续经通用 API/文件/快照合同接入；阶段工作不引入供应商账号、私有行情库或供应商 SDK 作为必需依赖。数据、文档、图片和第三方代码分别记录权利来源。
3. 所有上游材料固定 repository、commit、path、sha256、许可文本及读取日期。仓库文档、SKILL/AGENTS、代码注释只作为研究材料，不取得工具调用或执行权限。
4. 上游原始材料、Finathink 整理内容、确定性复现、用户数据研究和已审核使用范围分别存储；知识检索不得把来源主张升级为本地验证，读取不得触发付费调用或研究任务。
5. 所有可执行因子明确时序/截面作用域、字段单位、available_at、窗口、缺失值和复权语义；未来数据不得改变历史特征。重复窗口参数和别名不增加独立公式数量。
6. 模型只输出类型化研究假设、引用和允许的工具参数；执行走已有 DSL/工具白名单。默认每轮最多24个假设、2个并发研究任务，单个上下文最多32768字节且不超过既有更严格限制，达到预算即检查点停止。
7. 配置、因子使用范围和策略权重沿用显式审核；新内容导入、模型评分、来源刷新及纸面结果不得自动修改已批准政策。
8. 每阶段有真实 HTTP/worker/安装验证和一次独立整阶段审查；只改文档的任务运行文档检查。实际发布检查最终 PR 提交与合并后 main 的 CI，沿用用户已授权的自主修复和发布流程。

## 知识与证据合同

`SourceSnapshot`：source_id、repository、commit、capture_date、license_artifacts、file_hashes、module_map、review_status。`SourceAudit` 返回可读取/可适配/仅参考的逐文件裁定及缺口；提交不存在或文件哈希不符不能导入。

`KnowledgeItem`：item_id、canonical_id、version、kind、title、domain、summary、prerequisites、assumptions、formula_refs、code_refs、source_refs、claim_kind、evidence_refs、counterexample_refs、required_fields、method_refs、review_status。kind 为 CONCEPT/METHOD/FACTOR/STRATEGY/RISK/CASE；claim_kind 为 DEFINITION/MATHEMATICAL/EMPIRICAL/ENGINEERING。词条、翻译、参数别名、来源引用不分别计数。

`EvidenceRef` 引用现有 run/manifest/assessment 的不可变摘要，不复制一套“成功”状态。证据层级：SOURCE_REPORTED → DEFINITION_CHECKED → SYNTHETIC_REPRODUCED → USER_DATA_EVALUATED；这些层级与 USE_APPROVED 的治理决定正交。否定结果也可拥有充分证据。

`PanelContract`：snapshot_ref、instrument_ids、observed_at、available_at、field_units、calendar_ref、universe_ref、adjustment_ref、trade_rule_ref、scope_id。训练期估计的变换必须携带 fit_start/fit_end 和 transform_version。

`StudyBlueprint`：blueprint_id、question、hypothesis、knowledge_refs、counterexample_refs、snapshot_ref、factor_specs、strategy_ref、split_spec、cost_policy_ref、risk_policy_ref、budget、method_version、fingerprint。缺数据输出 NEEDS_DATA；不以填零或近似字段补成 READY。

`StrategyTemplate`：template_id、version、required_fields、universe_rule、selection_rule、weight_rule、rebalance_rule、execution_rule、risk_rules、benchmark_ref、source_refs、parameter_bounds。研究输出统一成现有账本、指标和报告接口。

`PoolEntry`：entry_id、decision_origin、stage、instrument、decision_at、available_at、policy_ref、dataset_ref、signal_fingerprint、comparison_cohort、horizons、outcome_refs。观察→候选→决策是阶段；自动扫描/量化基线/用户/模型/公开行为方法是决策来源，两者不混用。同一经济信号跨池出现只算一份独立证据。

## 规模目标和计数口径

| 截止阶段 | 可验收内容目标（累计） |
| --- | --- |
| 21 | 7个锁定参考项目；两个重点项目全部已发现顶层模块有去向；旧52个资源记录全部对账 |
| 22 | >=80个经来源审核的规范知识条目、>=8个主题域、>=12条学习/研究路径、>=40个确定性例子、>=24条误区解释 |
| 23 | 9类字段合同、24项时序/截面算子、>=12个数据边界样例 |
| 24 | >=120张来源因子卡；>=80个独立可执行公式，覆盖10类且每类>=4个；每个有解析、单位、PIT、缺失值及数值例子 |
| 25 | >=20个失败/反例研究案例；80个公式全部有真实执行的验证记录（通过/证伪/待数据/执行失败分开）；>=12个独立计算对照 |
| 26 | >=12个策略模板、>=12个独立风险规则；股票/ETF的统一纸面账本和五类决策来源跟踪 |
| 27 | >=30个可回归研究问题，>=8个完整闭环问题；已有五类分析师均消费带出处的知识包 |
| 28 | >=200个去重后的知识对象（含因子/策略/风险/案例）、>=12条完整研究路径；每条路径至少一项端到端发布验收 |

这些是交付下限，不是投资有效性门槛。候选和失败资产也有研究价值；不能为了凑数量把未审核、重复、无代码或未经执行的条目标成合格实现。

## 阶段依赖与共享接口

21 的 SourceSnapshot/rights 裁定供22的知识索引和24的来源卡使用。22 的 canonical_id/search/resolve 引用供23字段说明、24公式解释和27研究计划使用。23 的 PanelContract 与 panel DSL 供24因子和26策略使用。24的FactorSpec沿用25的现有验证入口；25的EvidenceRef供26决策卡、27研究反馈及28工作台读取。26的StrategyTemplate和PoolEntry沿用现有审核、结算与20的学习可靠性规则；27不另建调度器；28只组合已经通过验证的合同。

## 质量与界面

知识库使用全文索引加中文二元分词的可复现检索，第一版不强制向量服务或付费 embedding。固定60个评测问题先于实现冻结，Top-5命中率>=85%，每个主题域>=75%，无出处正文作为权威答案的数量为0。后续混合检索只能在同一题集及保留测试集上比较。

默认展示问题、适用条件、公式含义、反例和下一步；技术摘要按需展开。能力入口覆盖知识、因子、策略、研究池、风险、实验/报告。沿用蓝白界面，学习 DeepSeek 的信息组织和闭环，不将其历史缓存数字变成我们的默认展示。支持键盘、减少动态效果、错误/空/加载/恢复、1440×900/1920×1080/375×812、离线单文件报告。

两套旧知识目录、旧资料入口、历史因子定义和政策引用都保留兼容读取。备份恢复必须覆盖新索引版本、引用和审核记录；可派生索引可以重建，原始证据不可被重建过程改写。

## 风险审查焦点

- 许可混合、来源更新或恶意文档不得越权导入、运行代码或把缺失证据标为可信。
- 相同概念/公式被多项目、多个窗口重复表达时不得膨胀数量、样本或多因子置信度。
- 截面排序、公告修订、复权、退市和交易日差异不得泄漏未来数据或丢弃反例。
- 同次研究在知识版本、参数、政策或所选记录变化后不得继续提交旧目标；失败和中断可恢复且幂等。
- 数量目标、界面卡片和来源业绩不得替代实际执行、成本、反证、独立测试期与范围审核。
