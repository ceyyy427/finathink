# Finathink Product Completion Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task with the previously selected Native execution method. Steps use checkbox syntax. This document is a proposed roadmap, not a record of completed work.

**Goal:** 用户能从页面选择数据、提出问题、提交后台研究，查看进度、对比结果、导出报告，并在重启后继续研究。

**Architecture:** 继续使用现有 Python 本地服务、SQLite 队列和 spawn Supervisor。
以不可变数据快照、持久化运行索引和受控研究上下文贯通现有分析师、因子、量化、风控、纸面组合和报告组件。
浏览器展示服务器计算结果，所有页面由同一运行记录生成。

**Tech Stack:** Python、SQLite、现有 HTML/vanilla JS、ECharts、Lightweight Charts、Node test runner、pytest、Playwright。

**Spec:** `docs/FINATHINK_CAPABILITY_ASSESSMENT_2026-10-26.md`；沿用
`docs/superpowers/specs/2026-10-11-finathink-dark-intelligence-dashboard-design.md` 的视觉系统。

## Global Constraints

- 数据接入保持通用用户 API 合同；购买、授权和具体供应商 SDK 由用户选择。
- 复用现有 run、dataset、policy、evidence 指纹，避免另建并行的事实来源。
- 模型接收研究问题和经授权的必要材料；凭证不进入模型上下文、公共报告或日志。
- 结算后学习先产生提案；经用户审核产生新版本配置，旧运行保持可重放。
- 产品继续面向低频研究和纸面组合。
- 实施时从核验后的主分支创建独立分支；保留现有工作区用户改动。

## Review Focus

- 页面选择 A 快照而 worker 使用 B/fixture：逐阶段校验同一 dataset fingerprint（Task 2、4）。
- 重启后列表或报告丢失：从磁盘/数据库重建索引并校验 manifest（Task 3）。
- 模型返回存在但不属于本次运行的 evidence id：拒绝未绑定引用（Task 5）。
- 取消/重试/双击提交创建重复报告或错误归属：验证幂等与租约围栏（Task 4）。
- 不同样本、成本或 OOS 设置被直接比较：先检查可比条件并标注不一致（Task 6、7）。

## 阶段与依赖

| 建议阶段 | 任务 | 用户得到的结果 | 进入下一阶段条件 |
| --- | --- | --- | --- |
| Phase 4：修补发布质量 | Task 1 | 页面一致，交互回归纳入 CI | 关键页面均通过浏览器检查 |
| Phase 5：贯通本地研究 | Task 2–4 | 数据选择、后台任务、进度、报告和重启连续性 | 两份不同数据能端到端产生可区分结果 |
| Phase 6：有效模型研究 | Task 5 | 分析师基于实际材料协作 | 上下文与证据引用校验通过 |
| Phase 7：深化量化研究 | Task 6–7 | 因子实验、可比结果和风险组合解释 | 样本外、成本、风险与指纹一致 |
| Phase 8：复盘与试用 | Task 8–9 | 审核后复跑、安装升级和小范围用户试用 | 完整安装和核心旅程验收通过 |

以下文件为执行时的具体落点；新接口均是建议，不代表当前已存在。

### Task 1：页面一致性、交互修复与前端 CI

**Files:** 修改 `src/finahinking/local_app.py`、`frontend/src/overview.js`、
`frontend/src/research.js`、`.github/workflows/ci.yml`；扩展
`tests/p7_5/test_e2e.py`、`frontend/test/overview.test.mjs`。

**Interfaces:** 保持现有路由；`/research/compare` 输出完整 AppShell。
Overview 只由一个入口初始化；数据摘要独立于 canvas DOM。

- [x] 添加回归：对比页含导航/main；Overview 仅发起一次数据请求；resize 后可读；键盘可访问每个日期的数据。
- [x] 修复当前路由、重复 mount、图表清理、焦点数据回退和主题 CSS 只存在于旧样式的问题。
- [x] 覆盖成功/空/加载/失败、真实主题记录和跨移动断点 resize；比较页也加入截图矩阵。
- [x] CI 新增 Node 测试/构建及安装 Chromium 的必跑浏览器 job，保存截图、错误和性能 JSON。
- [x] 验证 `npm test`、`npm run build`、`python3 -m pytest tests/p7_5 -q`；审查并单独提交。

### Task 2：统一可重放的数据快照

**Files:** 新增 `src/finahinking/data/snapshot_store.py`；修改
`data/user_api.py`、`p8_2/data_sources.py`、`research/workflow.py`、
`research/contracts.py`；新增 `tests/data/test_snapshot_store.py`。

**Interfaces:** `SnapshotStore.save(snapshot: DatasetSnapshot) -> str` 与
`SnapshotStore.load(fingerprint: str) -> DatasetSnapshot`；请求只绑定 snapshot ref。

- [x] 添加测试：同内容相同指纹；篡改拒绝；缺失 available_at 保持 UNKNOWN；重启后能载入。
- [x] 将 DataBatch 转成既有 DatasetSnapshot，记录字段质量、时区、复权/日历声明、数据截止时间与来源。
- [x] 将用户选中快照显式传给因子、回测、风险、组合计算；数据不满足需求时给出具体缺字段说明。
- [x] 验证 A/B 两份数据分别贯通计算，报告中的 dataset fingerprint 与输入一致；审查并提交。

### Task 3：统一持久化研究索引

**Files:** 新增 `src/finahinking/research/run_repository.py`；修改
`research/run_store.py`、`research/reports.py`、`research/ui.py`、`local_app.py`；
新增 `tests/research/test_run_repository.py`。

**Interfaces:** `RunRepository.list_runs(...)`、`get_run(run_id)`、
`record_result(...)` 统一提供运行、报告、数据/策略版本和执行时间引用。

- [x] 测试：运行完成后重启应用，Overview、历史和对比仍可打开同一报告。
- [x] 用持久化索引替代 `_research_runs` 作为页面事实来源；现有注册方法作为兼容入口。
- [x] 区分 `created_at`、`started_at`、`finished_at` 与研究 `as_of`；按真实发生时间统计活动。
- [x] 记录缺失/损坏报告状态，并校验 manifest 指纹；过滤、分页和排序在服务器完成。
- [x] 运行 repository、report、local-app 测试；审查并提交。

### Task 4：用户研究提交到 Supervisor 的完整旅程

**Files:** 新增 `src/finahinking/research/application_service.py`、
`frontend/src/run-workspace.js`；修改 `local_app.py`、`research/runtime_service.py`、
`research/stage_registry.py`；新增 `tests/research/test_application_service.py`。

**Interfaces:** 建议 `POST /api/research/jobs`、`GET /api/research/jobs/{id}`、
`POST /api/research/jobs/{id}/cancel`；页面提交 connection/snapshot、问题、标的、as-of、模型配置引用及预算。

- [x] 测试：幂等提交、取消、崩溃恢复、两任务并行不串结果、预算超限、失败阶段可定位。
- [x] 由应用服务装配真实 snapshot store、quant gateway、driver 与报告仓库；worker 由引用恢复输入。
- [x] 默认 runner 不再无条件选择缺少量化能力的 gateway；明确所选运行模式和失败原因。
- [x] 页面提供提交、阶段进度、取消、报告入口；先用有界轮询读取持久化状态。
- [x] 验收两份不同输入从页面跑到不同报告，刷新页面和重启服务后可继续查看；组件自查并提交，随后执行一次独立全分支审查。

### Task 5：让模型收到有效研究材料

**Files:** 新增 `src/finahinking/research/context_bundle.py` 与
`provider_transport.py`；修改 `providers.py`、`provider_adapters.py`、
`drivers.py`、`analyst_runtime.py`、`provider_config.py`；扩展相关测试。

**Interfaces:** `ResearchContextBundle` 包含问题、角色、as-of、授权证据摘要、
数据/政策指纹及 evidence index；模型输出引用必须是该 index 的子集。

- [x] 测试：模型传输收到可理解材料而非只有 digest；未来证据、伪造引用和越预算被阻断。
- [x] 分别构建基本面/技术/情绪/新闻/学习的角色材料，缺少某类输入时明确显示资料不足。
- [x] 将配置的 provider/role model 与显式调用服务连接；记录调用量、字节/上下文预算与耗时。
- [x] 研究经理汇总观察、分歧与待验证假设；财务计算继续使用服务器引擎。
- [x] 首先完成一个通用兼容协议的全流程，再按实际需求增加其他协议；Codex 外部任务保持明确回传状态。
- [x] 离线合同测试与用户自行提供连接后的显式端到端验收分别记录；审查并提交。

Phase 6 的受控对端合同、真实 spawn 与浏览器证据，以及用户真实连接 `NOT_RUN` 的独立状态，见 `docs/PHASE6_MODEL_RESEARCH_GUIDE.md`；后者不由离线测试替代。

### Task 6：因子工作区与真正可比的实验

**Files:** 修改 `research/factor_pipeline.py`、`factor_experiments.py`、
`factor_catalog_loader.py`、`quant_analytics.py`、`p8_2/research_view.py`；
新增 `frontend/src/experiment-workspace.js`；扩展对应单测与浏览器测试。

**Interfaces:** 统一 `ExperimentSummary`：snapshot、假设、参数、样本区间、
train/validation/test、成本、指标、失败原因、版本、证据引用。

- [x] 接通页面：问题 → 研究章程 → 候选 → 验证 → 冻结 → 测试 → 排名 → 审核。
- [x] 测试重复触碰测试集、重叠标签、缺失样本、成本口径不一致和不可比运行。
- [x] 扩展滚动样本外和标签重叠时的 purge/embargo；保留失败实验与多重检验记录。
- [x] 参数稳健区按参数邻接与明确阈值定义；禁止仅凭指标有限或输入顺序称为稳健。
- [x] 先形成 20–30 个来源、字段、公式和复现实验齐全的高质量目录项；效果状态由数据评估决定。
- [x] 对比页展示指标差异和可比条件；不同数据/成本/样本明确分组；审查并提交。

### Task 7：风险与组合进入同一轮研究

**Files:** 修改 `risk_runtime.py`、`portfolio_runtime.py`、`paper_trader.py`、
`workflow.py`、`reports.py`；扩展 `test_risk_exposures.py`、
`test_portfolio_optimizer.py` 和端到端测试。

**Interfaces:** 风险、权重、费用、成交与归因均绑定 Task 2/3 的 snapshot/run/policy。

- [x] 将已有行业/因子暴露、CVaR、压力情景和约束分配装配进 Task 4 默认主路径。
- [x] 测试停牌、低流动性、缺行业、价格跳变、不可行约束和延迟纸面成交。
- [x] 解释每条约束对权重/现金的影响，展示策略、成本、风险缩放对结果的贡献。
- [x] 用已有确定性分配建立基准；需要数值优化时另立目标函数、可行性和基准验证。
- [x] 审查同一快照贯穿整个风险/组合/报告链后提交。

### Task 8：复盘、学习审核与版本化复跑

**Files:** 修改 `learning_manager.py`、`settlement.py`、`application_service.py`、
`local_app.py`；新增 `frontend/src/learning-review.js`；扩展学习/运行测试。

**Interfaces:** 用户选择提案、查看修改预览、批准/拒绝，下一轮请求引用批准后的 policy fingerprint。

- [ ] 将已有 proposal/admission/preview store 接入复盘页面。
- [ ] 展示预测/假设与结算结果差异、因子衰减和规则修改依据。
- [ ] 测试重复结算、拒绝提案、旧政策回滚和未来数据隔离。
- [ ] 验证批准后创建新配置/新运行，原报告保持不变；审查并提交。

### Task 9：中文试用流程与交付质量

**Files:** `README.md`、`docs/QUICKSTART.md`、`docs/RESEARCH_CAPABILITY_FINAL_REPORT.md`、
`docs/RESEARCH_RUNTIME_RELEASE_CHECKLIST.md`、安装/迁移脚本与 CI；扩展 clean-install/E2E 测试。

**Interfaces:** 统一中文导航、首次使用向导、数据/模型状态、研究模板、错误恢复与诊断导出。

- [ ] 覆盖零技术用户首次安装到首份离线报告；再覆盖用户自行配置连接的旅程。
- [ ] 明确缺数据时的下一步，避免空页面；长任务提供耗时和预算信息。
- [ ] 测试新安装、升级迁移、备份/恢复、损坏报告、长列表和弱设备表现。
- [ ] 用真实截图、布局测量、键盘/屏幕阅读器检查及性能日志验收；记录浏览器/数据范围。
- [ ] 执行 Python、Node、构建、治理、依赖、敏感信息、clean-install 与必跑浏览器门禁。
- [ ] 按实际通过范围更新能力矩阵，完成审查后通过 PR 发布，再进入小范围试用。

## 本次规划自审

- 已核对既有优化、风险、学习和队列组件，后续任务优先接入而非重复创建。
- 数据快照 → 运行索引 → 应用服务 → 模型上下文 → 实验/风险/复盘依赖明确。
- 本文新增接口均标记为建议；未把 fixture、历史门禁或旧进度勾选当作完整产品验收。
- 规划编写时没有改产品代码、安装依赖、提交或发布新版本；后续 Task 1 的执行证据见 Phase 4 发布质量报告。
