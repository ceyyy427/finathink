# Finathink Phase 10 蓝白工作台 Implementation Plan

> **For agentic workers:** 使用 superpowers:executing-plans，沿用用户选择的 Native 顺序执行。每项任务以 TDD、独立提交和 task-done 验证收尾；不在任务间请求继续许可。

**Goal:** 将用户指定的蓝白 HTML 迁移到真实本地应用，完成统一外壳、真实总览和已有工作区适配。

**Architecture:** 沿用服务器渲染、vanilla JS/esbuild 和现有持久化服务。增加公共主题与渐进增强 shell 模块；首页通过安全 GET 参数预填现有研究表单，所有计算仍走已有主流程。

**Tech Stack:** Python stdlib HTTP、HTML/CSS、vanilla JS、ECharts、Node test runner、pytest、Playwright。

**Spec:** `docs/superpowers/specs/2026-10-27-finathink-blue-white-workbench-design.md`；产品事实见 `PRODUCT.md`，后续阶段见同目录 `2026-10-27-finathink-skills-factors-roadmap.md`。

## 给审阅者的阶段说明

**完成后的体验：** 打开应用即进入用户参考 HTML 的默认蓝白工作台。左侧导航、顶栏、问题输入、实际活动与上下文保持同一视觉；研究、数据接口、实验、对比和复盘仍走已有真实流程。

**当前进度：** Task 1 已提交；Task 2 已恢复验证，18 项 Python、44 项前端通过，进入提交收尾。定向证据不代表全量或浏览器已经通过。Task 3 待执行，本阶段未发布。

**接下来做：** 用户已确认总计划；提交 Task 2 后继续浏览器截图、键盘/移动交互、必要旧主题断言更新、CI 与完整审查。

**本阶段边界：** 不借机引入技能库、改变因子算法、增加付费模型调用或重写历史报告。营销官网只在后续有明确需要时与实际交付状态同步，不单独扩成新的建站项目。

## Global Constraints

- 主目录用户未提交文件与两个输入原件保持不动；在现有隔离 worktree 的 `codex/blue-white-workbench` 实施。
- 原型默认 white 是视觉基准，现有工作区不是被新样例替代的对象。
- 保留全部 API、CSRF/CSP、数据指纹、人工模型授权、学习审核、后台恢复和 paper-only 边界。
- 不引入供应商 SDK、第二套任务存储、隐式样例/模型调用或不真实计数。
- 新增库实际存在前不展示已实现的技能页或来源指标；本轮不改静态营销官网、离线报告内容或因子算法。

## Review Focus

- 问题包含 HTML/特殊字符或过长 GET 参数：安全转义并限制长度（Task 2）。
- 全新空库与已有任务：导航和首页不能生成假研究、默认样例或调用模型（Task 1、2）。
- 关闭的移动抽屉仍可 Tab 到、宽度切换遗留 inert：检查焦点恢复、Escape 与 resize（Task 3）。
- 无 JS、减少动态效果、错误/损坏报告：入口不消失、数据仍可读、错误可恢复（Task 3）。
- 全局换主题使旧图表失去对比、重复挂载、打包缺脚本：保留表格回退和 once mount，测试实际 HTTP 与安装包（Task 1、3）。

### Task 1：公共蓝白外壳与已有页面主题

**Files:** 新增 `src/finahinking/workbench_ui.py`、`frontend/src/app-shell.js`；修改 `local_app.py`、`frontend/src/research.js`、`frontend/package.json`；新增 `tests/research/test_phase10_workbench.py`、`frontend/test/app-shell.test.mjs`。

**Interfaces:** `render_shell` 保持原调用签名；导航的现有路由和 aria-current 保留。所有外壳页只加载一次本地 bundle。新 shell 不依赖网络、数据库写入或模型连接。

- [x] 写并运行回归：shell 的语义地标、全部旧路由、脚本只一次、渲染无副作用；本地导航入口搜索不返回任意 URL。
- [x] 运行 `python3 -m pytest tests/research/test_phase10_workbench.py -q`。Expected: 新主题/导航能力缺失导致断言失败，非测试初始化错误。
- [x] 实施白色侧栏、顶栏、浅色面板/表单/工作区，渐进增强移动导航与入口搜索；现有页面主题跟随。
- [x] 运行该 Python 文件、`npm --prefix frontend test`、构建。Expected: 全通过，打包资产两处相同；提交。

### Task 2：真实总览与问题进入研究主流程

**Files:** 修改 `local_app.py`、`workbench_ui.py`、`frontend/src/overview.js`；扩展 `test_phase10_workbench.py`、`frontend/test/overview.test.mjs`。

**Interfaces:** GET `/research/jobs?question=...` 只预填 1000 字以内问题，不提交。Overview schema 和已有 data 属性不变；图表继续显示实际活动而非合成收益。

- [x] 添加空库/已有记录/恶意文本/超长问题测试，先看新行为失败。
- [x] 迁移 HTML 的问题输入区、四项真实指标、活动/主题/对比区和上下文；保留每日表格与加载/错误/空态。
- [x] 问题模板只预填，数据和模型状态不凭空标绿。上下文链接对应现有任务、数据、模型与知识页面。
- [x] 运行 `python3 -m pytest tests/research/test_phase10_workbench.py tests/research/test_phase9_delivery.py -q` 和 Node。Expected: 转义、长度、无隐式任务、真实记录与旧合同通过；构建并提交。

### Task 3：浏览器、CI、审查与设计记录

**Files:** 新增 `tests/p7_5/test_phase10_browser.py`；修改 `.github/workflows/ci.yml` 和必要旧主题断言；新增 Phase 10 使用/验收说明，完成 `DESIGN.md` 与 `.impeccable/design.json`。

**Interfaces:** CI frontend-browser-evidence 必须包含新旅程截图与诊断；严格浏览器模式不跳过。

- [ ] 浏览器先验证问题跳转、无隐式提交、入口搜索、移动导航焦点和关闭、无 JS 导航以及减少动态效果。
- [ ] 批量检查 1440×900、1920×1080、375/390 移动与代表性现有工作区，允许一次集中修正；图表仍键盘可读、无横向溢出。
- [ ] 将新增浏览器文件加入 CI 显式必跑清单。Expected: 浏览器用例通过，保存真实截图与 console/pageerror 诊断。
- [ ] 运行全量 `python3 -m pytest -q`、Node、构建、性能、Ruff、治理、发布检查、密钥扫描、diff 检查。Expected: 全绿；如有已知条件性 skip 说明准确原因。
- [ ] 执行一次机械设计检测；独立全分支和视觉审查，修复重要问题并回归。按实际建成主题写设计记录和阶段状态，不把后续技能融合记为完成；提交并交付本地分支。
