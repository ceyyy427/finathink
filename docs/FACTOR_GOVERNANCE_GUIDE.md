# 因子家族与研究治理

来源评价、本地计算和用途审核是三个独立事实。来源 ICIR 不会进入本地指标；相同显示名称不会关联不同公式。工程样本不等于来源实验复现，研究批准也不是生产交易许可。

## 证据和用途

`ResearchApplicationService.factor_evidence(factor_ref)` 汇总包内来源和当前工作区的已校验报告。默认扫描最近 200 条索引；显式报告引用可以优先读取旧运行，仍不超过总上限。缺少原报告、接口失败和损坏产物有单独原因，未知指标不填零。

`FactorUseDecision` 绑定公式指纹、市场、股票池指纹、用途、政策、评估、报告和审核人。决定保存在现有队列数据库；既有最终测试审核与用途事件同一事务提交。重复审核幂等，冲突决定拒绝；历史不覆盖。旧 `FactorCatalogEntry.status` 继续保持旧合同，新的生命周期通过 `project_factor_lifecycle` 查询。

公式样本只可批准 `FORMULA_RESEARCH`，不能升级为 `PAPER_SCREENING` 或真实投资用途。缺数据和执行错误可留观察记录，不能因此认定金融失效；最终测试不佳可以明确拒绝研究用途。`use_is_approved` 按精确范围查询，不把一个市场、股票池或用途的决定套到别处。

## 五类新增工程试点

| 家族 | 本地定义 | 必要语义 |
| --- | --- | --- |
| 财务低频 | 已公告账面权益 / 当日市值 | 同为 CNY；市值逐日；公告和修订在信号时已知；报告期不晚于公告；不是 f_score/sue 代理 |
| 行业 | 单观察期收益减去同刻同历史行业样本均值 | 有生效时间的历史分类；至少两只同刻同组标的；不使用现在的分类回填历史 |
| 涨跌停 | 收盘价 / 已声明历史涨停价 | 同为 CNY；逐日限价与已知规则版本、生效区间；不从股票代码猜规则 |
| 机构汇总 | 汇总净买入额 / 当日成交额 | 同为 CNY；逐日净额；用户数据明确声明机构汇总席位归属；不推断人物真实持仓或收益 |
| Alpha101 单式参考 | 日内实体 /（当日振幅 + 0.001 CNY） | OHLC 同为 CNY；核对原文 Appendix A 的第 101 式；独立编写受限算术实现 |

Alpha101 定义参考 [Kakushadze 原论文 v3](https://arxiv.org/abs/1601.00991v3)。原文保留 WorldQuant 对 Appendix A 的权利，本项目没有因此获得第三方代码的宽松许可；没有复制该附录代码或完整 Alpha101 库。这里只引用一项数学定义，并单列独立实现、原式身份和权利边界，不宣称复现论文效果。

每批当前只有表中这一项可执行定义，不等于整个家族实现。原 f_score、标准化 SUE、limup_ex、turn_mid_prox、其他 Alpha101 成员及未核定原式仍是来源线索；不按窗口、反向和近似变体凑数量。

## 类型化研究入口

```python
from finahinking.research.factor_families import (
    audit_family_inputs, evaluate_family_factor, family_factor_specs, family_for_spec,
)
from finahinking.research.factor_validation import (
    FactorValidationGateway, factor_validation_policy, run_factor_validation,
)

spec = next(item for item in family_factor_specs()
            if family_for_spec(item) == "financial")
audit = audit_family_inputs("financial", panel, {"factor_spec": spec.to_dict()})
values = evaluate_family_factor(spec, panel)  # 审计不通过时先阻断
run = run_factor_validation(spec, panel, factor_validation_policy(),
                            gateway=FactorValidationGateway())
```

`panel` 来自既有不可变数据快照和 `ResearchFieldAttachment`。通用连接器只负责用户授权数据的字段映射，不安装数据供应商 SDK。行业需要分资产的多标的面板；当前单标的 method-study 入口缺同刻同组样本时会阻断，不能假装完成行业去均值。分组最终测试仍要求每个标的的原冻结/预约控制。

字段附件的 `industry` 可携带历史分类，但不得与快照中的非空行业值冲突。财务事件用原观察/报告时间作为 `timestamp`，以实际可用时间和修订时间控制版本；不得把季末当公告日。规则和分类使用 `effective_from/effective_until`，未知区间不猜。单位声明是用户提供的语义，不是本平台独立认证来源。

五项均为固定每观察期定义，不展示虚构参数热力图。OOS 折仍实际计算，比较基线预先固定为 5 观察期低波动；候选、基线和固定组合沿用同一成本与成交约束。家族输入审计冻结在九步运行绑定中，旧报告不随当前定义变化而重算。

## 观察、审核和版本

`service.refresh_factor_monitoring(factor_id, {"as_of": "2026-10-27"})` 显式刷新当前工作区已校验报告。只接受截止日、窗口数量、阈值、已有报告引用和父配置引用，不能提交客户端 IC 或指标。没有新常驻进程、隐式模型调用、付费数据请求或第二套任务队列。

默认至少三个互不重叠的最终测试窗口，最近两个相对早期均值均下降至少 0.1 才建议复核。公式、市场/股票池、政策、成本、代码、证据类型不同，不合并比较；重复指纹只算一次，未来、未完成、未知指标和错误运行不算衰减窗口。阈值属于冻结观察政策，不保证统计显著，也不能解释为金融失效。

建议进入已有复盘页，明确标为“因子最终测试观察，非纸面成交结算”。预览只登记范围限定的研究复核注记，保持原权重、风险规则和用途批准。审核通过会保存新的不可变研究配置版本；拒绝不创建版本，旧版本与默认选择不变，其他范围的已有注记保留。

观察记录的 `settlement`、`settlement_digest` 均为空；没有用回测 IC 伪造已实现纸面收益。需要依据真实纸面结算调整预算时，继续通过原 `learning.settle` 的后续快照、真实 `PaperLedger`、预览与显式审核入口。新版本仍需用户明确选择用于下一轮；工程执行自主性不取消产品审核。

当前范围是描述性复核与版本化研究注记，不是自动调权策略。单条坏结果、缺数据或程序错误不会自动退役或拒绝因子，退出用途仍有独立审核事件。工程样本的变化也始终带 `FORMULA_FIXTURE` 标签，不升级为外部效果证据。
