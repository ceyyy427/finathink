# Phase 4：发布质量修补与验收

范围：`2026-10-26-finathink-product-completion-roadmap.md` 的 Task 1。
基线：`f2520b9e68c543c236fc0f999ee0e68a110c0ff1`。

## 修复内容与回归证据

| 问题 | 根因与修复 | 验收 |
| --- | --- | --- |
| 对比页缺少页面外壳 | 路由直接返回 body；改走统一 render_page | 文档、main、导航、当前路由及只读状态检查 |
| Overview 两次请求/初始化 | 两个 JS 模块各自动 mount；统一入口并复用同一加载 Promise | Node 并发/重复调用；实际页面请求数等于 1 |
| canvas 初始化后丢失键盘数据 | 数据条位于 ECharts 管理的 DOM 内 | canvas 外保留服务器按日明细表和原生日期按钮，Tab 与 live summary 验证 |
| 缩放、降动效与实例清理 | 缺少尺寸和生命周期监听 | 1920→375→1440 resize、系统降动效切换、pagehide dispose |
| 主题没有热力样式 | 样式只存在于旧 CSS，实际 AppShell 使用 dark CSS | 在启用的主题添加规则；真实 quant/strategy 记录的浏览器样式验证 |
| 无 JS 时仍显示加载中 | 服务端默认 aria-busy=true/loading 可见 | 服务端快照默认可读；JS 加载时才切换 busy；无 JS 旅程验证 |
| 空响应残留旧数据 | 原日期表只在新行非空时更新；活动记录和提示仍保留旧快照 | 日期表和活动记录均按验证后的响应重建；空响应移除旧记录，重复渲染清除已聚焦日期的提示 |

回归先复现了缺外壳、重复请求、丢失日期控件及空响应残留，再分别完成修复和通过验证。

## CI 门禁

`.github/workflows/ci.yml` 新增独立 `frontend-release` job：

1. Node 24 + `npm ci`，使用既有锁文件。
2. 前端单测与 1,000/10,000 点解析性能检查。
3. 构建并检查 site/package 两份已提交 bundle，防止源码与发布资源漂移。
4. 安装 Python dev 依赖及 Chromium 系统依赖。
5. `FINATHINK_REQUIRE_BROWSER=1` 执行既有 HTTP/browser 旅程和新增 Phase 4 回归。
6. 无论成功失败均上传截图、HTML、诊断 JSON、解析性能 JSON、JUnit XML；保留 14 天。

已使用不存在的浏览器目录做负向检查：严格模式返回失败，不能以 skip 通过。
普通 Python 测试环境仍允许明确报告浏览器未安装；独立发布 job 强制安装并执行。

首轮远端校验发现 job 级 env 不允许使用 `runner.temp`，因此没有创建测试 job。
安装 `actionlint 1.7.12` 复现后，改用 `github.workspace` 并让上传步骤引用同一环境变量；
修复后工作流静态检查通过。远端执行和产物记录见 [PR #10](https://github.com/ceyyy427/finathink/pull/10) 的 Checks。

首次实际执行中，前端/浏览器、notebook 和量化迁移通过；Python 矩阵的安全审计发现两项旧锁定依赖问题。
按上游公告最小更新 `certifi` 至 `2026.10.19`、`setuptools` 至 `84.1.0`，并同步构建后端 pin；
来源：[certifi 公告](https://raw.githubusercontent.com/pypa/advisory-database/main/vulns/certifi/PYSEC-2026-4320.yaml)、
[setuptools 公告](https://github.com/advisories/GHSA-xmj3-9gf7-h52w)。未增加忽略项或改变严格审计命令。
本地安全扫描已先复现这两项失败，再验证更新后的锁文件未发现已知漏洞。

## 浏览器与视觉检查

- 本地 Chromium：375×812、768×1024、1024×768、1440×900、1920×1080。
- Overview 和对比页均检查完整 shell、当前导航和文档宽度。
- 包含有数据、多日期、多来源主题、空状态、加载、非法响应错误、无 JS 阅读场景。
- 收集页面/console 错误与 DOMContentLoaded/资源数；本轮代表性 Overview 约 150 ms、2 个资源。
- 性能阈值仍是本地冒烟（DCL < 5 秒、资源 < 40），不将其称为完整 Core Web Vitals 验收。
- 已人工查看桌面 Overview 与移动对比截图，确认图表/数据回退和 AppShell 出现在页面中。

本地证据位于 `.superpowers/sdd/2026-10-26-finathink-product-completion-roadmap/browser-artifacts/`；
新证据目录独立于原阶段证据。CI 中同一组文件保存为 `frontend-browser-evidence` artifact。

已下载首轮成功的前端 CI 产物复查：JUnit 为 14 项通过、0 skipped、0 failures、0 errors，
并包含桌面/移动截图、页面 HTML、诊断与性能 JSON；已查看远端生成的桌面 Overview 截图。

## Web Interface Guidelines 检查

依据 [Vercel 官方规则](https://github.com/vercel-labs/web-interface-guidelines/blob/main/command.md)，
本轮检查聚焦改动页面：语义按钮/表格、可见焦点、文本回退、live 状态、错误恢复、降动效和响应式。
未新增外部资源请求或业务 API 合同。既有应用其他页面的完整无障碍认证不属于本次结论。

## 验证记录

- 前端 Node 单测：24 passed；构建与 bundle 一致性检查通过。
- pytest 全量：`FINATHINK_REQUIRE_BROWSER=1` 配合本地 wheelhouse 运行，1041 passed、1 skipped。
- 独立最终审查：没有 Critical/Minor，发现 1 项 Important P2（空响应残留活动记录和提示）。
  两个浏览器场景分别先复现失败，再修复通过；修复后的全量回归通过。
- `actionlint`、Ruff、compileall、治理检查和 bundle 一致性检查通过。
- 首轮全量：1038 passed、1 failed、1 skipped；失败项是 wheel 安装测试访问 PyPI 时 TLS 连接中断。
- 下载构建/运行依赖到本地 wheelhouse 后，以 `PIP_NO_INDEX=1`、`PIP_FIND_LINKS` 重跑 clean-install：1 passed。
  该处理只影响本次验证的依赖来源，没有修改安装测试或放宽产品约束。

## 范围与保留事项

- 本次仅完成 Phase 4 / Task 1，后续 Task 2–9 仍是待执行规划。
- 保留本轮本地截图与诊断目录，代价是占用额外磁盘空间；不覆盖旧阶段文件。
- 合并前核对实际 CI 及截图产物，不修改 GitHub 分支保护配置；未来 PR 是否强制检查仍取决于仓库策略。
