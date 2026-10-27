# Phase 14 因子家族与生命周期治理 Implementation Plan

> **For agentic workers:** 审阅确认后使用 superpowers:executing-plans，沿用 Native。分家族验收，先保持来源、计算和准入状态独立。

**Goal:** 为多类因子建立范围明确的证据、用途批准、观察和退出机制，并逐批扩展数据语义清楚的家族。

**Architecture:** 来源主张读自资产目录，本地结果读自 RunRepository 和报告，研究控制继续使用同一队列数据库。新生命周期是证据与审核事件的投影，不替换原 FactorCatalogEntry 状态或另建生产注册表。

**Tech Stack:** Python、既有 SQLite/产物存储、JSON、受限 FeatureGraph/DSL、pytest。

**Spec:** `docs/superpowers/specs/2026-10-27-finathink-research-assets-integration-design.md`。

## 用户将得到什么

能分别看到来源如何评价、我们实际算出了什么，以及因子在什么范围和用途下获准研究。可以查看财务、行业、机构等家族的输入要求、监控变化和历史决定，不因一次错误自动把因子判为失效。

## Global Constraints

- Phase 13 的 FactorSpec、数据附件与验证产物合同稳定后扩展。
- 来源状态、运行结果、生命周期分开；来源高 ICIR 不直接批准选股用途。
- 来源实验复现、用户数据评估、公式 fixture 分别标记，不相互升级。
- 研究准入有身份、范围和证据指纹；开发自主执行不取消产品内人工审核。
- 仍由用户提供通用数据；不接入供应商账号，不用当前行业分类代替历史分类。

## Review Focus

- 同名因子代理版与原版、来源正指标与本地负结果：并列而非覆盖（Task 1）。
- 季末日期早于公告日、后续修订覆盖历史：不得提前可用（Task 3）。
- 原批准用于另一市场/样本域或已变更公式：不得沿用旧批准（Task 2）。
- 任务崩溃或数据接口失败被当成因子无效：保留运行错误，生命周期不误跳（Task 2、4）。
- 并发审核、旧版本复跑或监控提出调权：不覆盖历史、不重复批准、不自动更新配置（Task 2、4）。

### Task 1：来源与本地证据投影

**Files:** 新建 `src/finahinking/research/factor_provenance.py`；扩展 `asset_contracts.py`；新建 `tests/research/test_factor_provenance.py`。

**Interfaces:** `load_factor_evidence(factor_ref, *, catalog, run_repository) -> FactorEvidenceView`；输出 source_claims、local_runs、conflicts、missing_dependencies、reproduction_scope，不制造新数值事实。

- [ ] 测试同名不同公式、上游 ICIR 与本地 OOS 冲突、缺原始报告、fixture 仅测公式和损坏产物；来源数值不能落入 local_metric。
- [ ] 运行 `python3 -m pytest tests/research/test_factor_provenance.py -q`，确认缺乏隔离/冲突表达时 RED。
- [ ] 按定义版本和评估范围关联证据；原主张保留，附加新的本地记录；未知范围明确表达，不强行对齐比较。
- [ ] Expected: 一份视图可以解释每条证据适用的版本与窗口；来源声明未被本地计算替代或背书。
- [ ] 提交 `feat: separate source claims from local factor evidence`。

### Task 2：范围限定的用途准入与生命周期

**Files:** 新建 `src/finahinking/research/factor_lifecycle.py`；修改 `factor_catalog.py`、`factor_study_store.py`、`application_service.py` 的接入；新建 `tests/research/test_factor_lifecycle.py`、`tests/research/test_factor_use_admission.py`；扩展维护/恢复测试。

**Interfaces:** `FactorUseDecision` 绑定 factor_ref、市场、股票池、用途、政策、评估指纹、reviewer 和 decision；`FactorUseStore(queue, *, scope_id)` 在现有队列数据库保存不可变审核事件；`project_factor_lifecycle(evidence, decisions) -> FactorLifecycleView` 计算投影。

- [ ] 测试缺 reviewer、错评估指纹、跨市场/用途/公式复用、重复/冲突审核及损坏记录；无显式批准不能得到 RESEARCH_APPROVED。
- [ ] 运行两个新增测试文件，确认不正确晋级/覆盖场景 RED。
- [ ] 实现设计中的状态转换；保留旧 FactorCatalogEntry.status 合同；用现有显式准入与审核方式绑定决定，控制状态与已发布结果不混存。
- [ ] Expected: 旧数据库可重复升级，重启/备份恢复保留决定；新决定不覆盖历史版本；缺数据、运行错误不自动成为 REJECTED。
- [ ] 提交 `feat: add scope-bound factor use admission and lifecycle projections`。

### Task 3：多源家族分批适配

**Files:** 新建 `src/finahinking/research/factor_families.py`；扩展 `data/research_fields.py`、`research/factor_inputs.py`、`p6_6/features.py` 和包内定义/政策；新建 `tests/research/test_factor_family_contracts.py`。

**Interfaces:** `FamilyRequirements` 声明字段、单位、可用时间和规则版本；`audit_family_inputs(family_id, panel, policy) -> FamilyInputAudit`；通过后才能交给 Phase 13 的 run_factor_validation，不增加任意代码入口。

- [ ] 表驱动测试公告/修订时间、历史行业成分、涨跌停历史规则、席位归属不确定、Alpha101 原式不一致；每类未知或缺失有单独原因。
- [ ] 运行新文件，确认未检查的时间/定义问题 RED。
- [ ] 分批做财务低频、行业、涨跌停/机构、少量 Alpha101；每批至少选一项定义明确的工程可执行试点，不能只有占位 adapter。先核对原式和复用条件，再扩展受限计算。
- [ ] Expected: 工程 fixture 与真实用户数据评估分别展示；来源 f_score/sue 代理版不冒充原版，聚合席位不冒充人物真实收益；缺规则的条目不晋级。
- [ ] 按批提交 `feat: add audited family input and calculation adapters`；未取得原式的候选保持 SOURCE_ONLY，未实现批次列入剩余工作，不能宣称整个家族完成。

### Task 4：观察、衰减与学习建议

**Files:** 新建 `src/finahinking/research/factor_monitoring.py`；修改 `learning_manager.py`、`learning_review.py`、`application_service.py` 的关联；新建 `tests/research/test_factor_monitoring.py`。

**Interfaces:** `evaluate_factor_monitoring(factor_ref, run_refs, policy) -> MonitoringProposal`；产出观察/复核建议和反例引用，不直接写因子权重或风险规则。监控由已授权研究完成或显式刷新触发，沿用现有队列与预算。

- [ ] 测试单次异常、缺数据、程序错误、跨版本窗口混用、确有衰减证据，以及复盘审核前后旧策略不被改写。
- [ ] 运行该文件及既有 Phase 8 学习流测试，确认缺关联或误更新行为 RED。
- [ ] 基于结算与验证记录生成带 as-of 的观察提案；需要调整时进入现有 preview/approve/reject/新版本流程，未知值保持未知。
- [ ] Expected: 建议有依据、限制和重访条件；旧报告与原批准可复查；无隐式付费调用或新常驻进程。
- [ ] 提交 `feat: derive factor monitoring proposals from settled evidence`，更新 `docs/FACTOR_GOVERNANCE_GUIDE.md`。

## 交付验收

三种状态不会互相替代；准入、观察和退出可追溯；各家族按实际适配覆盖验收，依赖缺口逐项列出。研究结果不佳允许正常归档，不能通过隐藏失败、改窗口或覆盖旧记录获得“已验证”状态。

执行阶段门禁：`python3 -m pytest tests/research/test_factor_provenance.py tests/research/test_factor_lifecycle.py tests/research/test_factor_use_admission.py tests/research/test_factor_family_contracts.py tests/research/test_factor_monitoring.py tests/research/test_phase8_learning_flow.py tests/validation/test_maintenance.py -q`；Expected: 全通过。再跑全量回归与旧状态恢复检查，未适配批次不得标记完成。
