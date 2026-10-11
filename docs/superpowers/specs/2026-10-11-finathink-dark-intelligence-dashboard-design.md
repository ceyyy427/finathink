# Finathink Dark Intelligence Dashboard — Phase 1 设计规范

日期：2026-10-11
状态：已获用户确认，等待实现计划
范围：全局 AppShell、Dark Design System、Overview Dashboard

## 1. 目标与边界

Finathink Phase 1 将现有本地优先研究应用升级为 **Dark Intelligence Research
Workspace**。这不是营销网站，也不是把现有页面简单换成黑色；它需要让金融事件、证据、研究运行和量化工作台形成一致的桌面研究环境。

本阶段只改变表现层、交互层和页面组合方式，不改变研究合同、数据来源语义、PAPER-ONLY/READ-ONLY 边界或后端计算逻辑。所有 Overview 指标必须来自现有服务端 payload；缺失数据必须显示可操作的空状态，不得用随机数、虚构的 AI 评分或未经定义的金融指标填充。

## 2. 当前技术约束

- 本地应用由 `src/finahinking/local_app.py` 输出 HTML/CSS，并保留现有路由和 API。
- `frontend/src/research.js`、`frontend/src/knowledge.js` 负责浏览器端 payload 校验和交互。
- 图表依赖使用现有 ECharts、Lightweight Charts；公式继续使用 KaTeX。
- 不引入 React、路由框架或第二套组件库；不改变后端 API 合同。
- 页面必须在离线样例和无数据状态下仍能渲染，并保留来源、as-of、PAPER-ONLY 和 limitation 信息。

## 3. 视觉方向

### 3.1 核心语言

深蓝黑背景承载高密度研究信息，紫色表示 Finathink 的研究主线；蓝色、绿色、琥珀色和红色只承担明确的语义状态。视觉重点是“证据流和研究状态”，不是霓虹装饰。

### 3.2 Design Tokens

所有页面样式必须使用 CSS 自定义属性，不在组件中散落颜色值。

```css
:root {
  --ft-bg-0: #050b18;
  --ft-bg-1: #081326;
  --ft-sidebar: #071122;
  --ft-surface: #0b1730;
  --ft-surface-hover: #112044;
  --ft-border: rgba(140, 160, 210, .16);
  --ft-border-strong: rgba(167, 139, 250, .42);
  --ft-purple: #8b5cf6;
  --ft-purple-hi: #a78bfa;
  --ft-blue: #38bdf8;
  --ft-green: #34d399;
  --ft-amber: #fbbf24;
  --ft-red: #fb7185;
  --ft-text: #f1f5f9;
  --ft-text-2: #cbd5e1;
  --ft-muted: #94a3b8;
  --ft-faint: #64748b;
  --ft-radius-sm: 10px;
  --ft-radius-md: 16px;
  --ft-radius-lg: 22px;
  --ft-space-1: 4px;
  --ft-space-2: 8px;
  --ft-space-3: 12px;
  --ft-space-4: 16px;
  --ft-space-5: 20px;
  --ft-space-6: 24px;
  --ft-space-8: 32px;
  --ft-motion-fast: 160ms;
  --ft-motion-standard: 240ms;
  --ft-ease: cubic-bezier(.22, 1, .36, 1);
}
```

正文使用 `Fira Sans`, `Avenir Next`, system sans-serif 回退；数字、digest 和指标值使用 `Fira Code`, SFMono-Regular, monospace 回退。页面不依赖外部字体网络请求才能可读。

## 4. AppShell

### 4.1 结构

```text
┌───────────────────────────────────────────────────────────┐
│  240px Sidebar  │  Topbar / workspace context              │
│                 ├─────────────────────────────────────────┤
│  Finathink      │  Overview content                         │
│  primary nav    │  metrics → activity → research context   │
│  secondary nav  │                                           │
│  local status   │                                           │
└───────────────────────────────────────────────────────────┘
```

- Sidebar 桌面宽度约 240px，内容区可滚动，页面整体不产生横向滚动。
- 当前路由通过 `aria-current="page"`、背景层和左侧紫色标记同时表达。
- Sidebar 收缩不是 Phase 1 的必需能力；窄屏改为顶部可滚动导航，不能把导航藏在只支持 hover 的交互里。
- Topbar 提供页面标题、数据状态、研究边界徽标、搜索入口和设置入口。不存在真实用户资料时只显示 `Local Workspace`，不虚构套餐或账户状态。
- 所有图标使用同一套 SVG/现有图标资源；不使用 emoji 作为结构图标。

### 4.2 导航信息架构

保留现有业务入口，并按研究顺序分组：

- Workspace：Overview、Event、Research、Quant
- Learn：Knowledge、Personal
- System：Data connections、Provider settings、Research history

若某入口在当前构建中没有真实页面，必须显示为明确的 disabled/planned 状态，不能导航到空白假功能。

## 5. Overview Dashboard

### 5.1 页面职责

Overview 是研究工作台的状态入口：回答“最近有什么事件、证据覆盖到哪里、研究运行是否完成、学习记录是否有进展”。它不替代 Event、Knowledge 或 Quant 的细节页面。

### 5.2 布局

```text
Page header: Welcome to Finathink / as-of / PAPER-ONLY
┌────────────┬────────────┬────────────┬────────────┐
│ Events     │ Evidence   │ Learning   │ Research   │  metric cards
└────────────┴────────────┴────────────┴────────────┘
┌──────────────────────────────────┬─────────────────┐
│ Event & research activity         │ Top topics      │
│ accessible area/line chart        │ ranked list     │
└──────────────────────────────────┴─────────────────┘
┌──────────────────────────────────┬─────────────────┐
│ Learning activity heatmap         │ Research state  │
│ real activity or empty state      │ stage/status    │
└──────────────────────────────────┴─────────────────┘
```

### 5.3 指标合同

四张 `MetricCard` 只接受已定义的字段：

1. `Events analyzed`：实际事件记录数量。
2. `Evidence coverage`：有 evidence refs 的研究记录占比，必须显示分子/分母或“未绑定”。
3. `Knowledge activity`：来自真实学习事件的活动计数或最近活动时间；不能伪造 mastery score。
4. `Research runs`：持久化研究运行数量和成功/阻塞状态摘要。

每张卡包括标签、值、as-of、来源/状态说明和可选 sparkline。没有数据时使用 `EmptyState`，并提供下一步动作，例如“连接数据”或“运行一次离线研究”。

### 5.4 图表合同

- Activity chart 使用现有 ECharts 或 Lightweight Charts，并提供范围切换、tooltip、键盘可读摘要和文本表格回退。
- Top topics 只从现有事件/研究主题聚合；没有主题时不显示示例主题。
- Learning heatmap 只绘制真实学习事件；没有记录时显示空状态和活动定义。
- Research progress 只能展示已定义的阶段状态或证据比例，不创建没有数学定义的 “AI Research Quality Score”。
- 图表颜色不能是唯一语义；状态必须同时出现在文本、图例或标记中。

## 6. 可复用组件

Phase 1 建立最小组件层，供后续 Event/Knowledge/Quant 复用：

- `AppShell`, `Sidebar`, `Topbar`, `NavItem`
- `Panel`, `MetricCard`, `ChartCard`
- `StatusBadge`, `EvidenceBadge`, `BoundaryBadge`
- `EmptyState`, `LoadingSkeleton`, `ErrorState`
- `DataTable`, `Tooltip`, `SegmentedControl`

组件通过 data attributes 或现有页面选择器与 vanilla JS 绑定，避免引入框架运行时。组件的视觉状态和数据状态分离：服务端负责事实，浏览器只负责校验、渲染和交互。

## 7. 交互和可访问性

- 默认动效只用于页面进入、导航选中和用户触发的 hover/focus；不让所有卡片同时漂浮。
- 使用 160ms/240ms 共享动效 token；`prefers-reduced-motion: reduce` 时直接呈现最终状态。
- 所有按钮、链接、图表切换器和可点击行使用原生语义元素，并有可见的 `:focus-visible`。
- Tooltip 信息必须能通过 focus 或文本摘要获得，不能只依赖 hover。
- 深色背景上的正文、指标和边界徽标满足至少 4.5:1 文本对比度；边框和焦点环保持可见。
- 空状态、加载状态和错误状态要说明当前事实和下一步动作，不使用模糊的“Something went wrong”。
- 保持 375px、768px、1024px、1440px 四个宽度下无横向溢出；桌面布局优先，窄屏采用单列/横向滚动导航。

## 8. 数据与安全边界

- 不改写 `ResearchRun`、workbench、knowledge、provider 或 data connection 的 JSON schema。
- 不在 HTML、manifest、activity 或前端状态中写入 API key、凭证值、完整 prompt、raw provider response、绝对路径或任意 URL。
- Overview 中所有显示值必须可以追溯到服务端 payload 的稳定字段；计算只做允许的显示级聚合。
- 真实供应商、模型 API、浏览器外部环境和生产部署继续标记为 `EXTERNAL_UNVERIFIED`。

## 9. Phase 1 验收

实现完成后必须通过：

1. `npm test` 与前端构建。
2. 现有 Python 测试和治理检查，不改变后端研究边界。
3. 本地浏览器验收：Overview、研究页、Knowledge 和 Quant 不出现 console/page error。
4. 1440×900、1920×1080、768px、375px 截图检查。
5. 键盘 Tab/Enter/Space、focus-visible、reduced-motion 检查。
6. `web-design-guidelines` 逐文件审查并记录未解决项。

## 10. 非目标

- 不迁移 React/TypeScript。
- 不添加真实数据供应商 SDK、券商接口或交易执行。
- 不添加随机指标、虚构账户、订阅层级或未实现的 AI 功能。
- 不把 Overview 变成新的研究计算引擎。
