# 方法引导的因子复现与九步验证

本流程把“模型提出假设”和“程序计算证据”分开。模型只能提交经过绑定校验的 `ResearchPlan` 或 `FactorProposal` JSON，不能提交执行代码、修改正式注册表或直接批准用途。计算复用现有受限 DSL、FeatureGraph、评估函数、纸面回测引擎、研究队列与报告。

## 首批定义

首批选择 5 项，复用现有配方而非按窗口或正反方向重复计数：

| 定义 | 计算和单位 | 研究方向 |
| --- | --- | --- |
| 流通股换手均值 | 真实 `turnover_rate` 的滚动均值，流通股口径的小数 | 低值 |
| 流通股换手离散度 | 同一口径的滚动标准差，ddof=0 | 低值 |
| 收益离散度 | 一观察期价格收益的滚动标准差，ddof=0、不年化 | 低值 |
| 短期反转 | 负的窗口价格收益 | 高值 |
| 未缩放 Amihud 风格收益成交额比 | 滚动均值 `abs(return(close,1))/amount`，金额明确为 CNY | 低值假设 |

每项 `FactorSpec` 绑定版本、公式、方向、窗口、频率、缺值规则以及既有 `FactorRecipe` 实现指纹。公式或实现指纹改变后不能冒用旧定义。未核实的来源条目、来源 f_score/sue 代理版和上游 ICIR 均不继承到这 5 项。

## 用户数据入口

原 `DatasetSnapshot.v1` 保持不变。价格、成交额、行业和停牌字段沿用快照；额外字段按指纹保存在同一快照根目录的 `research-fields` 子目录。附件记录主快照、完整标的身份、市场、单位、有效时间、可用时间及来源声明。

- 换手需明确 `ratio_free_float` 或 `percent_free_float`，百分比显式除以 100；总股本口径是另一种输入，不能冒充流通股口径。
- 成交量不能代替换手率，成交量也不能代替成交额。`amount` 复用快照值，附件仅声明其币种。
- 每日字段按同一 UTC 观察日和实际可用时间匹配，不向后填充；财务事件按有效期与公告/修订可用时间保留历史版本。
- 每个资产独立计算 rolling/lag；同日截面操作只在该日给定研究域中计算。静态用户研究域不等于已经核实无幸存者偏差的全市场股票池。
- 回测需明确停牌状态；容量需成交额币种和成交量 `shares` 单位。缺口保留为未知，不补零伪装可计算。

现有 `JsonApiConnector.fetch_research_fields(...)` 使用同一认证、分页和网络安全限制；`DataBatch.to_research_attachment(...)` 可将通用映射结果转换为附件。没有供应商 SDK 或供应商账号前置要求。

## 显式研究入口

先完成 [方法交接](CODEX_METHOD_HANDOFF_GUIDE.md)，再由同一用户、同一主任务消费已接受的产物：

```python
saved = service.attach_research_fields(job_id, attachment)
study = service.create_method_factor_study(
    job_id, handoff_ref, accepted_artifact_ref,
    attachment_ref=saved["attachment_ref"],
    idempotency_key="my-pilot-study",
)
```

`service` 是已有 `ResearchApplicationService`；`attachment` 是 `ResearchFieldAttachment` 或其完整 JSON。研究仍由已有 supervisor/spawn worker 执行。相同请求幂等；未知公式、错包产物、错误内容摘要、超额候选在建研究前拒绝。缺少附件也可以形成章程和阻断报告，不会偷偷用模拟数据补齐。

旧的 OHLCV 配方入口如果选中需要额外字段的配方，会在入队前明确返回 `BLOCKED_DATA`，引导使用上述类型化入口；不会将缺输入的候选算成无效因子。带阻断的九步报告可保存复查，但不能冻结或通过研究批准。

章程在计算前冻结研究问题、假设、因子定义、输入附件、方法清单、方向、窗口、低波基线、训练标准化的等权组合、费用、分割及代码版本。风险政策继续使用原主任务选定版本，不由模型替换。

## 九步证据

九步依次为：定域/数据审计、公式语义、IC 与标签、绝对相关去重、实际组合回测、样本外/稳定性、增量与消融、容量、归档建议。

每步给出 `COMPLETE`、`BLOCKED_DATA`、`EXECUTION_ERROR` 或 `NOT_RUN`，并绑定同一数据、附件、因子、执行、费用、基准、政策和代码。产物摘要可重验；旧运行冻结政策正文，恢复时不依赖后来安装的同名文件。

IC 是统计标签诊断，不是可交易净收益。块 IC 方差不足时 ICIR 为未知，不以“IC/收益波动率”代替。序列相关诊断只在各资产内部计算。负相关为 -1 也属于重复表达，常量或样本不足的相关性保持未知。

回测使用原有引擎的“前一观察期信号、下一观察期收盘执行”，按真实成交名义金额计费：`fraction = bps / 10000`。低波基线和等权组合使用同一时间域、费用、成交限制及训练期估计，保留实际净值/成交记录；固定滑点和参与率是研究假设，不是已经校准的真实市场冲击模型。

训练、验证和最终留出区隔离，重叠标签执行 purge/embargo。验证阶段不输出最终测试指标；参数稳定区只描述已声明、实际测试的网格。最终测试须通过现有 `FactorStudyStore` 的候选冻结和一次预约，跨版本、方向或代码变化不能复用原批准；正常重放读取已有报告。

## 冻结、测试与审核

```python
current = service.factor_study(study["study_id"])
candidate_id = current["validation"]["experiments"][0]["experiment_id"]
service.freeze_factor_study(study["study_id"], candidate_id)
service.test_factor_study(study["study_id"], "one-final-test")
```

等待测试完成后，通过既有 `review_factor_study` 显式审核。开发过程自动执行不意味着产品可以代替用户审核研究用途。数值不佳可正常归档为观察/拒绝；接口缺数据和程序错误不等于因子被金融证伪。

## 验收范围

工程 fixture 验证了公式、受控回传、真实 worker、净费用账本、阻断、冻结、测试及报告链，标记为 `FORMULA_FIXTURE`。用户快照计算标记为 `USER_DATA_EVALUATION`；来源实验复现需要独立的原式、原始数据及口径核对。本流程不把工程通过升级为上游实验复现、正收益或正增量结论。
