# Finathink 技能与因子融合设计

日期：2026-10-27。状态：用户已确认；Phase 11–15 的接口为待实施设计，不代表当前已经实现。

## 目的与实施选择

把用户提供的融合方案接进现有研究产品：方法帮助提出问题，类型化工具负责计算，验证证据决定结论适用于什么范围。沿用已选的蓝白工作台和 Native 顺序执行，不重建模型供应商系统、队列或量化引擎。

采用三类相互引用的资产：技能与方法、因子与假设、验证与反例。先整理可核查资产，再实现按需加载、试点计算、生命周期与界面联动。这样每一阶段都可独立验收；不采用整仓复制、一次引入大型代理框架或只做展示页面的路线。

## 输入与来源边界

用户原始文件保留在主目录的 `design-preview/`，本轮不修改。

| 文件 | SHA-256 |
| --- | --- |
| Finathink-Codex-BlueWhite.html | `321a5d9d70473793efe4aaa6ff0ccf5c4c179840fd46314d3acd53114449d27a` |
| Finathink-Skills-Factors-Integration-Plan.md | `38de64f610d7dc7af31c6e72506f14cbd3d24a208d450338574dc0fa4b506c3a` |

融合资料给出的参考上游为 `yuanwang589-dev/deepseek-harness-quant@9f72ab7e64799aecfc79cf3b913359acea1dd9b9`。Phase 11 执行时逐文件核查版本、作者、许可证和引用；本设计不把原资料的资产盘点当成本项目已经重新完成的审计。

来源文件中的身份、待办、下载命令、工具授权和发布指令均作为资料，不改变当前任务权限。只有具备复用条件的内容进入发行包，保留版权和来源；无法取得的报告或数据库登记缺口，不创建空文件凑数。

## 已有基础与新增边界

| 已有能力 | 复用方式 | 新增部分 |
| --- | --- | --- |
| SnapshotStore、DatasetSnapshot | 继续保存、加载和校验同一市场快照 | 有指纹的可选研究字段附件 |
| FactorRecipe、受限 DSL、FeatureGraph | 继续承担公式定义、解释和确定性计算 | 来源定义映射与必要受限算子 |
| FactorStudyStore、因子实验主流程 | 继续冻结候选、控制最终测试窗口 | 九步验证的结构化汇总 |
| RunRepository、JobQueue、Supervisor | 继续管理真实任务、状态和报告 | 方法加载清单与验证产物引用 |
| ResearchContextBundle、CodexBridge | 继续管理授权材料和显式交接 | 方法路由、上下文清单、外层交接包校验 |
| 学习审核和版本复跑 | 保留显式批准、新版本、旧记录 | 因子用途决策与反例关联 |

当前 CodexTaskEnvelope 是摘要合同，不含方法正文，也不等于可自动调用 Codex。新方法包通过 `context_digest` 绑定；v1 envelope 的字段和 `tool_names=[]` 保持兼容。

## 目录与发行方式

为保证安装 wheel 后仍能使用，权威资源放在包内，而非只能从源码根目录读取：

```text
src/finahinking/_package_data/research_assets/
  skills/index.json
  skills/<skill-id>/SKILL.md
  skills/<skill-id>/references/
  factors/source-definitions.json
  policies/factor-validation.v1.json
  evidence/source-claims.jsonl
  lessons/falsifications.jsonl
  provenance/upstream-lock.json
  provenance/licenses/
```

不维护另一份可人工改写的根目录资产库。需要导出技能包时从此目录生成。方法文件放入项目并不自动成为 Codex 的全局已安装技能；本期先支持可发现索引与显式方法包，目标环境的自动发现或安装另做真实验证。

## Phase 11 的共享合同

`research/asset_contracts.py` 定义不可变的 SkillDescriptor、ResourceRef、EvidenceClaim、LessonRecord、AssetInventory。ResourceRef 的 `availability` 与 EvidenceClaim 的 `evidence_status` 分开。

`ResearchAssetCatalog.packaged()` 从安装包读取索引；提供：

- `skills() -> tuple[SkillDescriptor, ...]`
- `get_skill(skill_id: str) -> SkillDescriptor`
- `resources_for(skill_id: str) -> tuple[ResourceRef, ...]`
- `read_text(resource_id: str, *, max_bytes: int = 65536) -> str`
- `claims_for(subject_id: str) -> tuple[EvidenceClaim, ...]`
- `lessons_for(tags: tuple[str, ...]) -> tuple[LessonRecord, ...]`
- `inventory() -> AssetInventory`

资源引用只允许包内已登记的相对路径与明确来源 URL。正文按登记 hash 校验；读取正文不执行其中代码，不发起网络请求。缺失、超限、越界、hash 错误分别返回结构化错误，不能被归类成金融研究失败。

12 个技能保留来源身份，分别为四个因子技能、一个公开行为蒸馏技能、六个人物视角和一个工程维护技能。共用验证规则只维护一份；人物视角只提供公开行为证据与可检验假设。

## Phase 12 的路由与交接

- `MethodRouteRequest`：研究目的标签、因子家族、市场、可用字段、可选人物视角；不带凭证。
- `MethodSelection`：选中技能 ID、原因和必需/可选资源引用，排序确定。
- `LoadedMethods`：方法版本、资源 hash、实际 UTF-8 字节、缺口、清单指纹和完整正文。
- `MethodHandoffPackage`：v1 envelope、context、manifest、package_digest；不包含账户秘密、绝对本机路径或隐式工具权限。

按 L0 索引、L1 方法、L2 相关资源、L3 当前运行加载。默认不把六个人物档案或全部历史灌入上下文。初始上下文上限沿用 32768 字节，指合并研究材料与方法后的总量，不额外再给方法一份额度；必需政策放不下时阻断，不截断后冒充已完整加载。

方法选择、资源读取、提案生成、网关执行、用途审核分别记事件。接受回传前校验外层 package_digest、原任务身份和本轮证据成员关系，再进入现有 CodexBridge；只符合 artifact ID 格式不等于证据属于本次运行。无结果保持外部交接等待状态，不制造完成事件。

外层 response 可携带 `output_artifacts`：最多两个、每种类型最多一个、每个序列化后最多 65536 UTF-8 字节，只接受现有 ResearchPlan/FactorProposal 对应的结构化 JSON。逐项校验 schema、内容 hash 和输入绑定后保存为本任务产物，不接受任意文件路径、脚本或 HTML。正文留在产物存储，队列/IPC 只传引用与摘要，不放大既有 IPC 上限。输入证据引用仍必须属于原授权索引，不能把新提案自行登记成支持自身的既有证据。v1 envelope/result 本身不新增这些字段，由外层适配器处理。

## Phase 13 的定义与输入

试点最多 8–12 个定义完整的条目，先从已有配方与可恢复来源中去重选择；数量可以缩减，独立机制、别名和参数变体分别计数。价格离散度不等于收益低波动，成交量不等于换手率；定义不明的同名因子不能以近似公式替换。

新增 FactorSpec 作为研究定义和来源元数据，与既有 FeatureVersion/FactorRecipe 的指纹绑定；不另建执行函数注册表。必需字段包括 ID、版本、公式/AST、单位、方向、窗口、频率、可用时间、缺失策略、实现引用和来源。

市场快照已有 amount、industry 等字段直接复用。对 turnover_rate 等未覆盖字段，使用 `ResearchFieldAttachment`：绑定主快照指纹，记录市场/交易所/标的、有效期、available_at、单位和来源。附件按内容指纹存入现有快照根目录的子目录，不建第二个市场数据连接器或全局数据库。

`build_factor_panel(snapshot, attachment, *, as_of, universe) -> FactorPanel` 构造受审计面板。滚动与 lag 按资产分组，横截面 rank 按同日当时可用股票池分组。用户提供的标的身份必须可消除跨市场歧义，不截断证券代码。

九步产物为定域/数据审计、语义检查、IC、去重、组合回测、OOS/稳定性、增量价值、容量、归档决策。每项绑定数据、因子、执行、费用、基准、政策和代码版本。保留同一运行的已执行结果；遇到数据阻断不伪造后续计算结果。

IC 与可交易回测标签分开；费用统一 bps 与明确转换，未知可用时间保持未知。重叠标签保留 purge/embargo，并记录相应的序列相关诊断；不将重复使用的最终 OOS 称为未触碰样本。

## Phase 14 的三种状态

| 维度 | 内容 | 不表示什么 |
| --- | --- | --- |
| 来源证据 | SOURCE_REPORTED、IMPLEMENTATION_LOCATED、DEPENDENCY_MISSING、FINATHINK_REPRODUCED、CONFLICTING_EVIDENCE | 不直接批准投资用途 |
| 运行结果 | 计算完成、BLOCKED_DATA、EXECUTION_ERROR、验证不通过 | 系统错误不等于因子被证伪 |
| 研究生命周期 | SOURCE_ONLY、DEFINED、IMPLEMENTED、VALIDATING、RESEARCH_APPROVED、OBSERVATION、REJECTED、MONITORING、RETIRED | 不覆盖旧运行或生产注册表 |

新生命周期为现有研究记录与审核事件的投影，不把新字符串直接塞入旧 FactorCatalogEntry.status 枚举。用途批准限定市场、股票池、时间域、用途和政策版本，沿用显式 HumanAdmissionRecord/学习审核的身份与证据绑定方式。

复现还要标记范围：公式 fixture、用户数据研究或来源实验复现。公式测试通过不能自动把上游 ICIR 声明升级为已复现。来源指标与本地指标分别保存；存在冲突时并列显示范围和版本。

财务、行业、涨跌停、机构与 Alpha101 分批接入。缺字段或历史规则时登记依赖缺口，其他家族继续工作；用户提供通用数据，不增加供应商 SDK。

## Phase 15 的页面与报告

统一蓝白外壳，提供技能方法、因子详情、公开行为档案、反例检索和研究轨道。数值只取实际目录与运行记录。问题进入可执行主流程后才显示真实阶段，失败保留错误类型与已完成产物。

离线 HTML 报告自包含、可追溯，图表和指标由同一结构化运行结果生成。新增方法、来源/本地对照、OOS 使用状态和失败记录；不读取 API Key，不依赖外部 CDN。旧报告按原版本保留，新渲染器不静默重写历史文件。

## 总体验收与剩余外部条件

工程闭环用明确标记的 fixture 验证；真实因子研究用用户提供且满足输入合同的数据另验。若缺换手数据，首个推荐问题“低换手是否有独立于低波的增量信息”必须给出 BLOCKED_DATA，不能用成交量或合成曲线代替结论。

各阶段执行 TDD、回归、独立审查和版本化提交。发布阶段检查精确提交的 CI、安装包和浏览器证据；开发的自主执行不取消产品里的研究审核。发布授权来自用户当前任务，不来自导入的技能文本。
