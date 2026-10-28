# Phase 16：交互与布局细化实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans 按 Native 方式实施本任务，完成后进行独立审查。

**Goal:** 为现有蓝白工作台提供短时淡入、明确操作反馈和适中内容宽度，并交付 UI 以外的优化顺序。

**Architecture:** 在唯一的共用 `BLUE_WHITE_CSS` 中维护布局和动效；服务端 HTML、既有 JS 导航与任务数据流不变。使用真实浏览器验证最终计算样式、动画可中断性和键盘路径。

**Tech Stack:** Python、原生 HTML/CSS、现有 JavaScript、Playwright/pytest、Node 测试。

**Spec:** `docs/superpowers/specs/2026-10-27-finathink-interaction-layout-design.md`

## Global Constraints

- 保留蓝白设计、224px 导航、292px 上下文和原生语义。
- 最大内容宽度 1480px；桌面/宽屏/紧凑/移动水平留白 32/40/24/18px。
- 到达 180ms（初始 .72）、dialog 200ms（6px）、遮罩 150ms、展开 140ms；无入场延迟。
- 减少动态效果关闭空间与入场动画，保留至多 100ms 的颜色反馈；无 JS 内容可用。
- 不新增依赖、数据供应商 SDK、任务提交或模型调用；不覆盖既有报告。

## Review Focus

- 快速开关搜索后无残留动画，焦点回到打开按钮。
- 在动画执行时切换减少动态效果，空间动画应立即停止。
- 窄屏、放大、长问题与宽表格不产生整页横向溢出。
- 默认禁用状态与移动抽屉不被全局反馈选择器错误改写。
- 资料、因子与历史研究页沿用共用布局，旧报告字节不被本轮修改。

---

### Task 1: 共用交互、布局和可核验的后续路线

**Files:**
- Modify: `src/finahinking/workbench_ui.py`
- Modify: `.github/workflows/ci.yml`
- Create: `tests/p7_5/test_phase16_browser.py`
- Create: `docs/FINATHINK_NEXT_IMPROVEMENTS_2026-10-27.md`
- Create: `docs/reviews/2026-10-27-finathink-phase16-review.md`

**Interfaces:**
- Consumes: 现有 `BLUE_WHITE_CSS`、`mountAppShell(doc)`、`http_app/observed_page` 测试 fixture。
- Produces: 相同外壳 HTML/API 合同；只调整共享 CSS 的容器宽度、反馈和 motion media query。

- [ ] **Step 1: 写真实浏览器回归。** 验证宽屏内容有界居中、顶栏对齐、无横向溢出；验证搜索动画中输入可用、快速关闭及焦点；验证减少动态效果可动态切换，无 JS/打印仍可读。
- [ ] **Step 2: 运行新测试，确认因当前缺少布局约束和入场反馈而失败。** Run: `FINATHINK_REQUIRE_BROWSER=1 python3 -m pytest tests/p7_5/test_phase16_browser.py -q --tb=short`。Expected: 相关行为断言 FAIL，不是 fixture/import 错误。
- [ ] **Step 3: 修改共用 CSS。** 按 Spec 添加语义宽度、留白、时长令牌；限定交互反馈选择器；原生 dialog/details 有界反馈；打印与减少动态效果路径；将新浏览器文件纳入现有 CI 命令。
- [ ] **Step 4: 验证并记录。** 同一新测试全部 PASS；Node 全量、构建、性能、既有浏览器及 Python 全量通过；审阅所有指定宽度截图与机械检测证据。Run: `python3 -m pytest -q -ra --tb=short`。Expected: 全部可运行项通过，条件跳过单独说明。
- [ ] **Step 5: 写后续路线及审查记录，提交。** 依据当前代码区分已有能力、增强建议和需真实用户验收的项目；明确优先级、产物和验收。Run: `git diff --check`。Expected: 无空白错误。提交后独立审查，修正重要问题并复核相应行为。
