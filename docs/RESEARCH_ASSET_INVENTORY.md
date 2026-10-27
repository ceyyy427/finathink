# 研究方法与来源资产清单

权威目录为安装包内的 `finahinking/_package_data/research_assets/`。源码与 wheel 使用同一份索引，不依赖原作者的电脑位置，不自动安装到用户全局技能目录，也不执行资料中的程序。

## 当前盘点

| 类别 | 数量与含义 |
| --- | --- |
| 可发现方法 | 12：4 个因子方法、1 个公开行为蒸馏框架、6 个人物研究视角、1 个工程检查方法 |
| 包内已登记资源 | 31：方法、适配说明、共用政策、许可与结构化资料；不含清单自身 |
| 可定位但未原样分发的上游资源 | 15：12 个方法原文、trial 摘要、2 个代码文件 |
| 缺失外部依赖 | 6：原手册、完整证伪报告、完整 trial 报告，以及 bars / finance_quality / finance 数据集 |
| 来源因子身份 | 10：8 个摘要线索、2 个另行定名的来源公式变体 |
| 可定位公式定义 | 2，仅表示找到来源定义，不表示本地实现、PIT 合格或绩效复现 |
| 未解决定义 | 8；别名 0，不将猜测别名或参数变体当成新因子 |
| 来源主张 | 14；含指标、口径冲突与原项目目录数量，均不是本地结果 |
| 来源教训卡 | 6；附失败类型、缓解、回归检查和重访条件，不计为本地金融证伪 |
| 本目录生成的本地复现 / 金融证伪 | 0 / 0；计算与用途审核属于后续运行记录 |

数量由 `ResearchAssetCatalog.packaged().inventory()` 的实际资源和去重条目计算。包内文件存在不等于研究运行完成。

## 方法身份

- 因子流程：`factor-mining-workflow`、`alpha-gpt-factor-mining`、`alpha-gpt-researcher`、`backtest-acceptance`。
- 公开行为：`niu-san-distillation`，以及 `niu-san-linyuan`、`niu-san-chenxiaoqun`、`niu-san-zhangmengzhu`、`niu-san-zhaolaoge`、`niu-san-chaoguyangjia`、`niu-san-fengliu`。
- 工程检查：`github-maintainer`。

人物名称用于检索研究视角；不认证身份归属、当前持仓、业绩或媒体指控。输出是有出处的观察与待检验假设，不是背书。工程方法不获得发布、删除、推送或凭证权限。

共用验证政策只维护在 `policies/factor-validation.v1.json`。九个阶段为：定域/数据审计、语义、IC、去重、组合、OOS/稳定性、增量、容量、归档。政策本身不执行任何阶段。

## 同名与复现边界

摘要中的 `turn_mid_prox`、`f_score`、`sue` 等指标保持 SOURCE_REPORTED，未绑定完整原式、样本和实验版本。

找到的 `factors/fscore.py` 是可用项求和的减配变体，包含 ROE 近似、两个缺项及最新期间选择，不被当成完整九项评分或摘要 `f_score` 的实现。`factors/fundamental.py` 中 `sue_factor_at` 是单季净利润同比比率，使用固定报告期延迟近似，不被当成标准化盈余意外或摘要 `sue` 的绩效复现。

这两个变体分别登记为 `dshq-fscore-available-items` 和 `dshq-sue-profit-yoy-v2`，保留明确的单位、时间与缺口。未导入或执行上游 Python。

原维护资料的“123+”对应外部研究报告份数；trial 摘要的“126”对应其自述注册项。二者都不增加 Finathink 的有效因子或已复现数量。早期有符号相关示例与后续绝对相关澄清并列保存；本地政策明确使用绝对相关，不静默覆盖来源冲突。

## 来源、许可与读取

来源固定为 [deepseek-harness-quant 的指定提交](https://github.com/yuanwang589-dev/deepseek-harness-quant/tree/9f72ab7e64799aecfc79cf3b913359acea1dd9b9)。`provenance/upstream-lock.json` 记录作者、原始文件 SHA-256、许可声明、改写版 hash 和资源状态。原仓库声明为 MIT，许可正文随包保留；不由此推定第三方论文、原始报道或行情数据的分发权。

PACKAGED 表示包内有经 hash 校验的正文；UPSTREAM_ONLY 表示固定源可定位但未分发；MISSING 表示依赖未取得；UNLICENSED 不可读取为本地可用内容。来源 URL 和旧位置只是元数据，不成为文件读取入口。

单资源读取默认最多 65536 UTF-8 字节，拒绝路径越界、Windows 盘符、软链接、hash 不符和无许可引用；错误是结构化资源错误，不是金融假设被证伪。方法路由、上下文预算、Codex 回传、试点计算和工作台页面按 Phase 12–15 接入。
