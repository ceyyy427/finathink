# Phase 15 研究界面与离线报告联动 Implementation Plan

> **For agentic workers:** 审阅确认后使用 superpowers:executing-plans，沿用 Native；视觉沿用已指定的蓝白 HTML，不重新选择风格。

**Goal:** 在同一个工作台中贯通问题、方法、因子、数据、实验、反例与结论，并生成可离线复查的蓝白研究报告。

**Architecture:** 页面读取 Phase 11–14 的统一资产/证据视图，真实阶段仍来自既有任务与报告。报告由同一结构化运行产物生成，新模板版本不重写旧报告。

**Tech Stack:** 既有 Python 服务器渲染、HTML/CSS、vanilla JS/esbuild、本地图表、pytest、Node、Playwright。

**Spec:** `docs/superpowers/specs/2026-10-27-finathink-research-assets-integration-design.md`；视觉沿用 `docs/superpowers/specs/2026-10-27-finathink-blue-white-workbench-design.md` 和 Phase 10 完成后的 DESIGN.md。

## 用户将得到什么

从首页提出问题后，可以看到本次加载的方法、计算用了什么定义和数据、九步验证走到哪里，以及哪些证据支持或推翻了假设。研究报告采用一致的蓝白视觉，下载单文件后仍能查看图表、数据、来源和限制。

## Global Constraints

- Phase 10 外壳已通过验收，Phase 11–14 的所消费合同可用；不为先画页面生成假计数。
- 来源记录、本地结果、证据不足和运行错误分别显示；技能加载不等于研究完成。
- 保留数据/模型配置、任务、因子实验、对比和学习入口，不新增第二套提交流程。
- 维护 CSRF、CSP、凭证保护和显式研究审核；浏览/搜索不隐式触发网络或模型调用。
- 离线报告不依赖 CDN，不暴露密钥或绝对路径；旧报告内容和原 manifest 保持不变。

## Review Focus

- 资料不齐却显示已加载、空目录显示固定数量：以真实合同与清单呈现（Task 1、2）。
- 来源高 ICIR 与本地失败放成同一个榜单：分区显示口径和冲突（Task 2）。
- 长公式、恶意 HTML、超长来源和损坏 artifact：转义、有界、可恢复，不执行材料代码（Task 1、3）。
- 离线报告请求 CDN/本地 API，或图表与 meta 来自不同运行：离线完整、身份一致（Task 3）。
- 移动导航、键盘、减少动态效果与升级/恢复：新增页面不能破坏已验收旅程（Task 4）。

### Task 1：方法、行为与反例工作区

**Files:** 新建 `src/finahinking/research/asset_workspace_page.py`、`frontend/src/asset-workspace.js`、`frontend/test/asset-workspace.test.mjs`；修改 `local_app.py`、`frontend/src/research.js`、`frontend/package.json`；新建 `tests/research/test_asset_workspace.py`。

**Interfaces:** 新增只读 `/library/methods`、`/library/behavior`、`/library/lessons`。GET `/api/research/assets` 接受 kind、受限标签、limit/offset；GET `/api/research/assets/{asset_id}` 返回公开安全视图。分页默认 20、最大 100，不返回任意本地路径。

- [ ] 测试实际目录分页、空/缺失/损坏资源、未知 ID、特殊字符、错误重试和只读无副作用；先运行上述 Python/Node 文件观察 RED。
- [ ] 使用蓝白外壳增加方法卡、六类公开行为档案与反例检索；来源、事实/转述/假设、触发条件与资料缺口可展开检查。
- [ ] 由 ResearchAssetCatalog 与 FactorEvidenceView 计算页面数据，不硬编码示例数量；选择方法后进入既有研究入口，仍需选择数据和授权。
- [ ] Expected: “12 个可发现方法”由索引生成；每张卡都能定位版本与来源；未准备资料不显示就绪。
- [ ] 提交 `feat: expose research methods behavior evidence and lessons`。

### Task 2：因子详情、九步轨道与研究决策

**Files:** 新建 `src/finahinking/research/factor_detail_page.py`；修改 `factor_workspace_page.py`、`local_app.py`、`frontend/src/experiment-workspace.js`、`run-workspace.js`；扩展相应前端测试，新建 `tests/research/test_factor_detail_workspace.py`。

**Interfaces:** 新增 `/research/factors/{factor_id}`，版本以有界 query 明确指定；详情由 FactorSpec、FactorEvidenceView 和 FactorLifecycleView 组成。九步轨道消费 FactorValidationRun，操作调用现有研究/审核服务，不直接改数据库或注册表。

- [ ] 测试来源/定义/公式/数据/验证/生命周期/反例七个分区；负 OOS、未知容量、BLOCKED_DATA、EXECUTION_ERROR、未批准及跨版本比较都有明确表达。
- [ ] 运行新 Python 文件和既有实验/运行 Node 测试，确认缺失状态绑定时 RED。
- [ ] 显示本次方法、选择原因与 hash；九步按真实状态标记，保留失败与 NOT_RUN；来源指标和本地结果并列，不生成无根据的综合健康分。
- [ ] Expected: 从问题可追溯到方法、公式、数据、运行与结论；未经审核没有批准标签；对比保留数据/费用/OOS 可比性约束。
- [ ] 提交 `feat: connect factor evidence and validation stages to the workbench`。

### Task 3：统一蓝白离线研究报告

**Files:** 新建 `src/finahinking/research/report_sections.py`；修改 `reports.py`、`workbench_ui.py` 的共用主题出口；扩展 `tests/research/test_reports.py`、`test_report_tree.py`，新建 `tests/research/test_method_factor_report.py`。

**Interfaces:** `build_method_factor_sections(validation_run, loaded_methods, evidence_view, lifecycle_view) -> ReportSections`。新模板消费同一运行对象，manifest 增加方法清单、数据附件、政策与验证产物的摘要引用；共用同一主题，不另造暗色模板。

- [ ] 测试内容与运行指纹一致、来源/本地数值不混用、失败实验保留、XSS/不安全链接转义和旧报告不被重写；先观察 RED。
- [ ] 增加研究问题、方法、九步验证、来源对照、实际结果、反例和下一步；图表与 meta 使用同一结构化结果。
- [ ] 导出自包含 HTML：嵌入必要样式与数据图形，保留表格、目录锚点和原生折叠；打开文件不访问本地 API 或 CDN。复杂图形不可用时仍可完整读数。
- [ ] Expected: 离线文件可复查，方法/数据/公式/政策指纹齐全；重启后仍可下载原版本；未计算部分不生成曲线或分数。
- [ ] 提交 `feat: export traceable blue-white offline research reports`。

### Task 4：完整旅程、安装验收与发布候选

**Files:** 新建 `tests/p7_5/test_phase15_browser.py`；扩展 `tests/validation/test_artifact_install.py`、`test_maintenance.py`、`scripts/check_installed_journey.py`、`.github/workflows/ci.yml`；更新中文 README、使用指南与能力报告。

**Interfaces:** 延用 CI 的 `frontend-browser-evidence`；新增页面、离线文件和安装后的工作台进入同一必跑门禁，不以源码 smoke 代替安装包验收。

- [ ] 浏览器验证“选方法→提问题→绑数据→受控提案→计算/阻断→读报告→复盘→显式审核→版本复跑”；真实模型与数据调用另记外部验收状态。
- [ ] 批量检查 1440×900、1920×1080、1024 和 375/390 移动宽度，覆盖键盘、焦点、减少动态效果、无 JS 基本入口、Loading/Error/Empty 和超长内容，保存截图与错误诊断。
- [ ] 验证新 wheel、旧状态升级、重启、备份恢复和历史报告完整性；离线打开报告时监测外部请求为零。
- [ ] 运行全量 Python、Node、构建、性能、Ruff、治理、依赖与密钥扫描；独立代码/视觉审查并修复重要问题。Expected: 门禁通过，剩余外部验证和未完成条目如实列出。
- [ ] 提交并形成发布候选；在既有用户发布授权范围内创建独立 PR，等待精确 head 的 CI 和证据，再合并并核对 main。本次计划审阅轮不推送或发布。

## 交付验收

目录中的方法与因子、实际计算、用途决定、反例和报告是同一条可检查链，安装版也可运行。新增能力按真实证据写入中文说明，版本更新流水不放进首页 README；未完成的外部验证不包装成已通过。

执行阶段门禁：`python3 -m pytest tests/research/test_asset_workspace.py tests/research/test_factor_detail_workspace.py tests/research/test_method_factor_report.py tests/validation/test_artifact_install.py tests/validation/test_maintenance.py -q`；`node --test frontend/test/asset-workspace.test.mjs`；`FINATHINK_REQUIRE_BROWSER=1 python3 -m pytest tests/p7_5/test_phase15_browser.py -q`。Expected: 全通过且真实浏览器不跳过。再跑全量 Python/Node、构建、性能与发布门禁，不能只凭这几个定向文件宣布完成。
