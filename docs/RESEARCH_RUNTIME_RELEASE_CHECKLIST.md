# Finathink 自主研究运行时发布清单

这份清单记录自主研究运行时的本地发布门禁。所有验收使用确定性 fixture、注入 transport 或 mock provider，可离线重放。

## 组合验收

- [x] 用户数据连接配置可持久化、重启恢复和显式读取。
- [x] JSON 字段映射产生标准化 `DataBatch`，保留数据指纹、质量信息和 PIT 状态。
- [x] fundamentals、technical、sentiment、news、learning 五类分析师并行运行。
- [x] `ResearchManager` 只汇总带 evidence refs 的研究观察。
- [x] 因子研究从受控假设进入 DSL 提案，完成训练、隐藏 OOS、冻结、一次性测试、排名和人工准入提案。
- [x] quant、risk、portfolio、paper trader 均使用确定性输入，风险失败保持纸面决策不可用。
- [x] paper ledger 记录费用、滑点、现金和组合指纹。
- [x] settlement 以 as-of 和 ledger 指纹绑定，`LearningManager` 只生成更新提案。
- [x] JobQueue 和 spawn Supervisor 支持幂等、租约、重试、退避、取消和恢复；旧兼容 worker 也只允许 spawn。
- [x] ProviderAdapter 和 CodexBridge 支持 mock 成功、离线 fallback 和显式 handoff 状态。
- [x] HTML 报告、manifest、activity、状态墙和 checkpoint 共享同一运行 ID。

## 公共 Artifact 检查

- [x] 不写入 API Key、凭证值、endpoint、绝对路径、完整 prompt 或 raw provider object。
- [x] 报告只保留 paper-only、read-only 研究语义和稳定 digest。
- [x] 报告、manifest、activity、checkpoint 和前端资源均可解析。
- [x] 任何必需阶段失败时，`decision` 为空且 `decision_eligible` 为 `False`。

## 可重复门禁

```bash
PYTHONPATH=src python3 -m pytest -q
PYTHONPATH=src python3 -m compileall -q src tests
python3 -m ruff check src tests scripts
python3 scripts/validate_governance.py .
python3 -m pip check
cd frontend && npm test
```

HTML/XML 和 secret/path/raw-object 扫描应覆盖所有生成的报告目录、`manifest.json`、`activity.jsonl`、checkpoint 和前端构建产物。门禁结果与命令输出保存在 Task 9 报告中。

## Task 17 final gate evidence (2026-10-11)

| Check | Result | Evidence / boundary |
| --- | --- | --- |
| `python3 -m compileall -q src tests` | `OFFLINE_PASS` | Exit 0. |
| `python3 scripts/validate_governance.py .` | `OFFLINE_PASS` | `PASS: governance validation passed`. |
| `python3 -m pip check` | `OFFLINE_PASS` | `No broken requirements found.` |
| `cd frontend && npm test` | `OFFLINE_PASS` | 17 tests passed, 0 failed. |
| `python3 -m ruff check src tests scripts` | `OFFLINE_PASS` | Exit 0 after the spawn Supervisor migration. |
| Vertical slice tests | `OFFLINE_PASS` | Full repository suite: 1025 passed, 1 skipped. |
| Concurrent runtime limit test | `OFFLINE_PASS` | 20/20 repeated process-owned metrics runs passed. |
| Local browser acceptance | `OFFLINE_BROWSER_PASS` | Playwright Chromium installed locally; loopback HTTP/browser suite: 4 passed, 0 skipped. |
| Portable browser rerun | `CONDITIONAL` | A clean host may skip if its browser binary is unavailable; this local evidence is complete. |
| External provider/browser/production connections | `EXTERNAL_UNVERIFIED` | No credentials, network acceptance, vendor authorization, real external browser/device matrix, or production evidence. |
| Broker orders, live trading, secrets in artifacts, arbitrary execution | `NOT_IN_SCOPE` | Permanent product boundary. |

The full repository suite was run in the current worktree after the final
Supervisor hardening pass: 1025 passed and 1 skipped. The skip is an
environment-gated non-browser case outside the Python/runtime acceptance
boundary.

## 证据入口

- `tests/research/test_autonomous_runtime_vertical_slice.py`
- `tests/research/test_capability_vertical_slice.py`
- `docs/RESEARCH_CAPABILITY_RELEASE_CHECKLIST.md`
- `docs/RESEARCH_RUNTIME_GUIDE.md`

## Dark Intelligence Dashboard Phase 1 (2026-10-11)

- [x] `GET /api/overview` provides a versioned, read-only, paper-only projection with explicit provenance and offline limitations.
- [x] AppShell uses the dark token system, grouped semantic navigation, topbar context, focus styles, responsive layout, and reduced-motion rules.
- [x] Overview renders server-owned metrics, activity/topics/learning regions, no-data fallbacks, and text-first boundary badges.
- [x] Existing frontend bundle includes Overview normalization, sensitive-field rejection, reduced-motion state, and keyboard text-summary affordances.
- [x] Phase 1 UI review is recorded in `docs/reviews/2026-10-11-finathink-dark-dashboard-ui-review.md`.
- [ ] Full visual regression across external browsers and production data remains `EXTERNAL_UNVERIFIED`; no live provider or trading capability is implied.
