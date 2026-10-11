# Finathink Dark Intelligence Dashboard — Phase 1 UI Review

日期：2026-10-11  
范围：AppShell、Overview、既有 Research / Knowledge / Quant 路由

## 已验证

- 视觉 Token：深海军蓝背景、紫色主动作、青色证据色、绿色成功色和琥珀/珊瑚边界色均来自共享 CSS 变量。
- 结构：桌面三列壳层在 1440px 可用；`880px` 以下变为单列，导航折行，检查器回流。
- 语义：侧栏分为 Workspace / Learn / System，当前链接使用 `aria-current="page"`；主内容有 skip link 和 `main#main`。
- 数据边界：Overview 由 `GET /api/overview` 服务端投影驱动；空活动、空主题和空学习记录显示明确空状态，不生成随机指标。
- 动效：`prefers-reduced-motion: reduce` 会关闭非必要动画；前端模块把 `data-animation="reduced"` 写入根节点并保留文本摘要。
- 键盘：Overview 图表区域设置为可聚焦，并通过下方文本列表提供非悬停等价内容。
- 隐私：Overview 前端归一化会拒绝 API key、token、prompt、endpoint、路径和 raw response 字段。

## 验证命令与证据

- `PYTHONPATH=src python3 -m pytest -q`：1029 passed, 1 skipped。
- `python3 -m ruff check src tests`：All checks passed。
- `cd frontend && npm test`：21 passed。
- `cd frontend && npm run build`：success。
- Playwright 本地浏览器验收：若 Chromium 可用，覆盖 1440×1000、375×812、无 JS 和 reduced-motion；截图与 HTML 产物写入 `.superpowers/sdd/2026-10-08-research-capability-completion-plan/task-16-artifacts/`。未安装浏览器的主机按既有门禁跳过，不冒充外部验证。

## 已知限制与下一阶段

- Overview Phase 1 不计算金融指标，也不把静态样本提升为实时数据；活动图只有在服务器投影存在活动时才显示记录。
- 当前图表交互采用文本摘要和状态属性，完整的可视化时间序列/主题聚合留给 Phase 2。
- 真实外部数据供应商、模型 API、Keychain 和生产浏览器矩阵仍按项目边界标记为 EXTERNAL_UNVERIFIED / NOT_IN_SCOPE。
