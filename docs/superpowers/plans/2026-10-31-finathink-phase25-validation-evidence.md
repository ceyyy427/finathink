# Phase 25：稳健性、反证与独立对照实施计划

> **执行方式：Native，按任务顺序推进。本阶段已实现，实际门禁和发布状态见末尾执行记录。任务内采用测试先行；每阶段统一进行一次独立整阶段审查。**

**Goal：** 让扩充因子库拥有真实执行记录、可解释评分、独立对照与失败研究案例。

**Architecture：** 复用现有九步验证、冻结测试和worker队列；增加批量研究编排、试验家族记录和统一证据投影。Alphalens只在隔离对照环境中运行，不成为默认依赖。

**Tech Stack：** Python >=3.11、numpy/pandas、SQLite、原生JS、现有Supervisor/spawn及HTML报告。

**Spec：** [共同设计与合同](../specs/2026-10-31-finathink-quant-knowledge-expansion-design.md)；[总规划书](../../FINATHINK_QUANT_KNOWLEDGE_ROADMAP_2026-10-31.md)。

**依赖：** Phase 24 的全部验收和共享合同。后阶段新增合同不能提前当作现有实现。

**Review Focus：** 最终测试反复选择、未登记试验、坏数据误报金融证伪、成本单位错误、相关来源被当独立样本。

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

- [x] 80公式全部具有实际验证尝试及结果；PASS/FALSIFIED/NEEDS_DATA/EXECUTION_FAILED分开。确定性工程样例不能标为真实市场验证。
- [x] 至少12个独立计算对照、20个失败/反例案例；评分覆盖IC/ICIR、分层单调性、收益、换手、衰减、池/时段稳定、成本容量、相关性与增量。
- [x] 64标的×756观察日的冻结SAMPLE及边界变体可从头重跑；时间切分、成本和试验家族提前登记。
- [x] 最终测试只在冻结后评估；使用过的测试区间不能继续以“未见过”参与下一轮选优，所有失败与选参路径可查。

## 已下载参考内容

以下仓库已经下载；执行时按固定提交读取，不从默认分支漂移取材。具体文件、哈希与学习方向见[源码学习记录](../../research/2026-10-31-open-source-quant-study.md)及[锁定清单](../../research/2026-10-31-open-source-quant-lock.json)。新增参考先补来源和权利裁定，再决定安装或适配。

- [alphalens-reloaded](https://github.com/stefan-jansen/alphalens-reloaded/tree/60755d1cc7d9d36a4c70c8344cbdb168fe99df24)：`60755d1cc7d9d36a4c70c8344cbdb168fe99df24`。
- [QUANTAXIS](https://github.com/yutiansut/QUANTAXIS/tree/232aa7ac9869966108f02a8fff85f1f7b09a9ce2)：`232aa7ac9869966108f02a8fff85f1f7b09a9ce2`。
- [deepseek-harness-quant](https://github.com/yuanwang589-dev/deepseek-harness-quant/tree/9f72ab7e64799aecfc79cf3b913359acea1dd9b9)：`9f72ab7e64799aecfc79cf3b913359acea1dd9b9`。
- [qlib](https://github.com/microsoft/qlib/tree/54355232463878d2eebb91fe0ee5fa7fa1f5976c)：`54355232463878d2eebb91fe0ee5fa7fa1f5976c`。

## Task 1：完整评分、试验登记与批量验证

**Files：**

- Create: `src/finahinking/research/factor_study_suite.py`
- Create: `src/finahinking/quant/evaluation/robustness.py`
- Create: `tests/research/test_factor_study_suite.py`
- Create: `tests/quant/test_robustness.py`
- Create: `tests/fixtures/quant_validation/sample-spec.json`
- Modify: `src/finahinking/research/factor_study_store.py`
- Modify: `src/finahinking/research/factor_validation.py`
- Modify: `src/finahinking/quant/evaluation/metrics.py`

**接口与输出：** run_factor_study_suite(study_id, specs, panel, validation_policy) -> dict；返回trial_refs、metric_refs、status_counts、frozen_test_ref。adjust_pvalues(values, method="holm") -> list[float]；family/trial账本在运行前持久化。

**先写的行为测试：**

- test_holm_known_example：输入[.01,.03,.04]输出[.03,.06,.06]，未排序输入仍对应原项。
- test_terminal_test_is_not_reused_for_selection：调整参数后旧测试不可再声明独立最终测试。
- test_all_trials_including_failure_and_cancel_are_recorded：没有只保存赢家；取消/重启按幂等键续跑。
- test_cost_turnover_and_lagged_quantile_returns_match_oracles：明确双边成本、再平衡时点、可交易性与手续费计量。

**执行步骤：**

- [x] 读取上述现有文件及前置阶段产物；确认共享合同。新文件如有等价现成实现，先记录证据并合并复用，不能创建第二套事实源。
- [x] 写上述失败用例及独立预期；执行以下命令，确认因目标行为缺失而失败，保存具体失败，不把环境/导入拼写错误当作有效红灯。
- [x] 生成固定种子样例并保留生成规范和哈希。按已预注册切分运行80公式，拆成每轮不超过24候选的有界批次，最多2并发研究任务；所有数据缺口、拟合区间、有效样本数、家族相关性、成本假设进入报告。统计检验记录适用假设，多重检验不替代独立测试。
- [x] 重跑同一用例并确认通过；执行受影响的旧入口回归，记录实际HTTP/worker或安装路径的证据及限制。
- [x] 更新本阶段完成清单、内容数量和接口说明，定向暂存本任务文件并提交；不要包含其他工作区的修改。

**命令（仓库根目录执行）：**

```sh
python3 -m pytest tests/research/test_factor_study_suite.py tests/quant/test_robustness.py tests/research/test_factor_study_store.py -q
```

**预期：** 首次为上述缺失行为失败；完成后相关用例全部通过。真实外部账户测试与离线受控样例分别记录。

## Task 2：独立对照、反例库与证据分级

**Files：**

- Create: `src/finahinking/research/research_evidence.py`
- Create: `src/finahinking/_package_data/research_assets/knowledge/counterexamples.json`
- Create: `tests/quant/test_independent_factor_metrics.py`
- Create: `tests/research/test_research_evidence.py`
- Create: `scripts/run_factor_reference_checks.py`
- Modify: `src/finahinking/research/report_sections.py`
- Modify: `src/finahinking/research/learning_reliability.py`

**接口与输出：** build_research_evidence(record_refs, *, catalogue) -> dict；从现有不可变run/manifest/assessment读取EvidenceRef，不另写成功标记。所有级别独立于USE_APPROVED；否定结果也可具备充分证据。

**先写的行为测试：**

- test_twelve_independent_oracles_agree_with_declared_tolerance：手算、numpy独立实现或锁定Alphalens对照，逐项声明容差/差异原因。
- test_twenty_counterexamples_remain_visible：未来泄漏、幸存者偏差、低覆盖、成本击穿、重复试验等案例有输入/结果/修正/适用边界。
- test_execution_failure_is_not_financial_falsification：错误和缺数据不计证伪，不进入学习有效样本。
- test_corrupt_or_missing_counterevidence_blocks_upgrade：缺反证或证据摘要不匹配时不升级可靠性，保留原版本。

**执行步骤：**

- [x] 读取上述现有文件及前置阶段产物；确认共享合同。新文件如有等价现成实现，先记录证据并合并复用，不能创建第二套事实源。
- [x] 写上述失败用例及独立预期；执行以下命令，确认因目标行为缺失而失败，保存具体失败，不把环境/导入拼写错误当作有效红灯。
- [x] 隔离安装锁定Alphalens依赖，仅对12个适用统计量做数值对照，记录版本和NOTICE。20反例以真正运行的失败实验和解释组成；源收益仍SOURCE_REPORTED，合成仅SYNTHETIC_REPRODUCED。接通现有Phase20样本可靠性检查。
- [x] 重跑同一用例并确认通过；执行受影响的旧入口回归，记录实际HTTP/worker或安装路径的证据及限制。
- [x] 更新本阶段完成清单、内容数量和接口说明，定向暂存本任务文件并提交；不要包含其他工作区的修改。

**命令（仓库根目录执行）：**

```sh
python3 -m pytest tests/quant/test_independent_factor_metrics.py tests/research/test_research_evidence.py tests/research/test_learning_reliability.py -q
python3 scripts/run_factor_reference_checks.py
```

**预期：** 首次为上述缺失行为失败；完成后相关用例全部通过。真实外部账户测试与离线受控样例分别记录。

## 阶段收尾与发布

- [x] 创建或更新 `docs/reviews/2026-10-31-finathink-phase25-review.md`，列出验收项→测试/产物→结果→限制；数量由当前内容清单产生。该日期是计划批次，记录实际执行日期。
- [x] 执行下面的本地门禁，并运行本阶段新增的真实HTTP/worker/浏览器/安装验收；浏览器产物保存三种尺寸截图和控制台检查。对纯数据合同任务用其实际研究集成路径验收。
- [x] 对最终差异进行一次独立整阶段审查，集中修复有效问题并对修改运行针对性验证；不得用无关优化扩大本阶段。
- [x] 复核待提交差异、来源通知和文档链接并准备阶段PR。远端发布门禁在本阶段PR正文及时间线跟踪：最终HEAD六项CI → 合并 → 精确main六项CI。
- [x] 仅在本阶段验收完成后开始下一阶段；缺用户真实数据时交付明确NEEDS_DATA路径及受控样例证据，不伪造实证通过。

```sh
python3 -m ruff check src tests scripts
python3 -m pytest
npm --prefix frontend run build
npm --prefix frontend test
npm --prefix frontend run perf
git diff --check
```

## 实际执行记录（2026-11-01）

实现冻结提交 `bbf9d84e835b81a88f1dd685b05b63f9e8af3e23`。统计合同先明确，试验编排与证据投影在该合同上集成；最终采用统一实现提交。既有指标、队列、FactorStudyStore、报告与审核接口均复用，没有另建调度器或默认引入Alphalens依赖。

64×756固定样例通过四批真实worker完成80项尝试，24通过登记阈值、38阈值不符、18有效数据不足、0执行失败；所有数据为SAMPLE。分片与通用PIT投影18项对照通过，样例和五种边界变体可重跑。13项生产标量对照、12项隔离Alphalens对照、22项反例均有实际输出；参考特有口径明确标注。

只读整阶段审查一次，集中修复审核、方向排名、未来诊断、可信模式和重复试验登记。随后仅窄复核有效修正，并补损坏报告reader门禁的真实红绿回归。先前80个数值producer记录保持不变，新reader重新核验全部原始报告；双方组件摘要和完整结果见[样例机器记录](../../research/phase25-sample-results.json)。完整门禁及远端PR/main检查按[阶段验收](../../reviews/2026-10-31-finathink-phase25-review.md)的实际证据更新。

最终本地完整门禁：2407项通过，1项可选statsmodels适配器跳过，强制浏览器无跳过，848.67秒。前端71项、构建、性能、Ruff、治理与依赖检查通过。远端发布结果以阶段PR记录为准。
