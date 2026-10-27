# Finathink 能力与交付验收

日期：2026-10-27。范围：Phase 4–9 的本地研究产品与 Task 9 中文交付质量；当前轮次的门禁和审查见[发布审查记录](reviews/2026-10-27-finathink-user-trial-release-review.md)。

## 当前能力矩阵

| 模块 | 已接入的能力 | 验证范围 |
| --- | --- | --- |
| 用户数据接口 | 通用 JSON API、认证与凭证引用、字段映射、显式获取、持久化快照和数据指纹 | 受控接口、PIT/质量合同、真实本地浏览器与 worker；真实用户服务另验。 |
| 研究任务 | 持久化队列、独立 Supervisor/spawn、幂等、取消、预算、恢复和同一输入贯穿计算 | 真实进程与 loopback HTTP；刷新/重启找回报告。 |
| 模型与分析师 | 用户选择兼容模型配置、角色映射、逐轮授权、五类分析师材料研究与研究经理综合 | 受控兼容模型传输、证据归属与失败闭合；Codex 为显式 handoff 合同。 |
| 量化与稳健性 | 24 个有来源和公式的自有研究配方、有界参数搜索、purged walk-forward、冻结测试、可比性与成本检查 | 确定性、低频研究；可计算的 OOS 不等于盈利证明。 |
| 组合与风险 | 同快照的约束组合、行业/信号暴露、历史 CVaR、压力情景、容量和纸面归因 | 历史样本和确定性纸面引擎，保留实际成交/未成交及费用口径。 |
| 复盘与版本 | 固定观察期、衰减、提案预览、显式批准/拒绝、新配置版本与按版本复跑 | as-of/PIT 约束；追溯比较标记、旧版本与原报告保留。 |
| 中文交付 | 中文导航、首次使用流程、问题模板、数据/模型准备状态、耗时预算和有界历史分页 | Chromium 桌面/移动布局、键盘与可访问树；人工屏幕阅读器验收另列。 |
| 异常与恢复 | 安全中文提示、输入保留、重试、损坏报告告警、可下载脱敏诊断 | 包括不完整 JSON、解析/网络错误、服务器异常不回显私人细节；不确定提交保留幂等标识。 |
| 安装与维护 | 新 wheel 安装到首份报告、增量迁移、私密备份、校验后恢复新目录 | 独立虚拟环境、真实进程；关闭应用后维护，非在线热备。 |

## 本轮证据入口

- `tests/research/test_phase9_delivery.py`：中文页面、无隐式数据/任务、诊断脱敏、服务器异常及新状态权限。
- `tests/validation/test_artifact_install.py` 与 `scripts/check_installed_journey.py`：wheel 而非源码导入，真实 HTTP/spawn/报告，再恢复和重复迁移。
- `tests/validation/test_maintenance.py`：旧理解库迁移、原数据保留、活动任务拒绝、清单/指纹/SQLite 校验，以及路径、链接、同名输出与损坏包拒绝。
- `tests/p7_5/test_phase9_browser.py`：从空状态到报告、离线文件、备份恢复、损坏报告、显式数据连接和 4 倍 CPU 限速分页。
- `frontend/test/local-request.test.mjs` 与 Phase 8 浏览器错误恢复用例：中文提示保留安全校验，不暴露英文解析器细节或私密响应。
- CI 的必跑浏览器 job 保存 `frontend-browser-evidence`，包括截图、可访问树、布局/性能数据和错误日志。

## 外部验证与试用状态

内部合同与浏览器验收记为 `OFFLINE_PASS / OFFLINE_BROWSER_PASS / CONTROLLED_TRANSPORT_PASS`。真实服务账号、实际系统凭证授权、外部网络质量、真实辅助技术与设备矩阵仍为 `EXTERNAL_UNVERIFIED`。本次交付[小范围试用步骤和反馈模板](PHASE9_USER_TRIAL_GUIDE.md)，人类参与者试用为 `NOT_RUN`，不以模拟接口或 CPU 限速替代真实证据。

---

## 2026-10-11 历史验收存档

下文保留原 Task 17 的验收证据与当时环境，不作为当前能力或本轮测试数量的依据。

### Research Capability Final Report（历史原文）

日期：2026-10-11
范围：Task 17 最终发布门禁（本地、研究用途、离线优先）

## 结论

本 checkout 的可重放本地合同和前端测试通过；发布状态按证据类别记录。
外部供应商、用户模型 endpoint 和生产连接仍保持未验证，不把离线证据扩展为生产承诺。

最新完整回归：`1025 passed, 1 skipped`。

## OFFLINE_PASS

- `python3 -m compileall -q src tests`：exit 0。
- `python3 scripts/validate_governance.py .`：`PASS: governance validation passed`。
- `python3 -m pip check`：`No broken requirements found.`
- `python3 -m ruff check src tests scripts`：exit 0（Ruff 清理提交
  `dabe0a2`）。
- `cd frontend && npm test`：17 passed，0 failed。
- 垂直切片与数据入口定向检查：25 passed；离线 fixture 通过显式 resolver
  注入，默认 resolver 仍对未验证目标 fail-closed（修复提交 `f5d1a61`）。
- 本地数据合同、PIT/指纹、五类分析师、证据汇总、受控因子 DSL、回测/OOS、
  风险检查、纸面组合、队列恢复、checkpoint、报告树和学习提案均保持研究用途
  与人工准入边界。

## OFFLINE_BROWSER_PASS

- Task 16 的本地 Playwright/Chromium 验收为 `OFFLINE_BROWSER_PASS`：真实 loopback
  `create_server()`、内存研究数据和本机 Chromium 覆盖 no-JS、失败状态、secret
  masking、键盘焦点、报告/运行状态、桌面与 390px 移动布局、stream-fetch 错误和
  page/console error 检查；首次运行 `4 passed`，保存 7 个 HTML/PNG 证据文件及
  `SHA256SUMS.tsv`，且哈希校验通过。
- 可移植重跑在当前 checkout 因 Playwright bundled Chromium 未安装而明确跳过真实
  浏览器用例，HTTP/parser 用例为 `3 passed, 1 skipped`。这只改变重跑环境状态，
  不撤销已有本地 Chromium 证据；复现命令和证据路径见 Task 16 报告。

## ISOLATED_DEFERRED

- Supervisor 已使用独立的非 daemon spawn 进程，worker 仅由 Supervisor 创建；控制 IPC
  有大小上限和精确 schema，未知 stage、快照摘要不匹配、超限和停止清理均 fail-closed。
- Qlib/vectorbt/QMT 等可选边界保持隔离或只读，不能提升为默认权威引擎。

## EXTERNAL_UNVERIFIED

- 真实供应商、用户模型 endpoint、凭证授权、网络行为和生产部署均没有本次门禁所
  需的外部证据；生产浏览器/设备矩阵、真实网络浏览器行为和屏幕阅读器 conformance
  也仍未验证。
- mock provider、fixture 和离线 fallback 只证明合同与失败闭合，不证明外部服务
  可用性或供应商合规。

## NOT_IN_SCOPE

- 券商交易、实盘下单、真实资金、投资建议、秘密值进入 artifact、任意代码或 URL
  执行、无人值守自我改进均不属于本版本。

## 安全与研究完整性审查

报告、状态墙和前端合同继续要求 paper-only/read-only 语义、稳定 digest、PIT、
证据引用、人工准入和失败时不可决策。没有把外部连接或失败检查标为通过。

## 复现命令

```bash
python3 -m compileall -q src tests
python3 scripts/validate_governance.py .
python3 -m pip check
python3 -m ruff check src tests scripts
cd frontend && npm test
```
