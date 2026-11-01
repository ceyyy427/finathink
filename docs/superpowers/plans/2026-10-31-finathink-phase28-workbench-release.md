# Phase 28：研究工作台、来源更新与发布实施计划

> **执行方式：Native，按任务顺序推进。本文是待执行计划，不是完成记录。任务内采用测试先行；每阶段统一进行一次独立整阶段审查。**

**Goal：** 把前七阶段能力组织成可探索、可追溯、可离线使用的工作台，并形成内容更新机制。

**Architecture：** 现有蓝白AppShell中的知识/因子/策略/风险/研究池/对比页面读取统一合同；不可变内容版本与派生索引分离；上游更新只生成差异提案。

**Tech Stack：** Python >=3.11、numpy/pandas、SQLite、原生JS、现有Supervisor/spawn及HTML报告。

**Spec：** [共同设计与合同](../specs/2026-10-31-finathink-quant-knowledge-expansion-design.md)；[总规划书](../../FINATHINK_QUANT_KNOWLEDGE_ROADMAP_2026-10-31.md)。

**依赖：** Phase 27 的全部验收和共享合同。后阶段新增合同不能提前当作现有实现。

**Review Focus：** 界面数量与实际能力不符、读取触发运行、来源撤回污染旧复现、开发机路径依赖、备份缺审核记录。

## 全局约束

1. 保留 Python >=3.11、SQLite、现有原生 JS/蓝白 AppShell 和独立 worker；先用现有 numpy/pandas。新增依赖必须说明替代项、许可证、安装开销和隔离验证结果。
2. 用户数据继续经通用 API/文件/快照合同接入；阶段工作不引入供应商账号、私有行情库或供应商 SDK 作为必需依赖。数据、文档、图片和第三方代码分别记录权利来源。
3. 所有上游材料固定 repository、commit、path、sha256、许可文本及读取日期。仓库文档、SKILL/AGENTS、代码注释只作为研究材料，不取得工具调用或执行权限。
4. 上游原始材料、Finathink 整理内容、确定性复现、用户数据研究和已审核使用范围分别存储；知识检索不得把来源主张升级为本地验证，读取不得触发付费调用或研究任务。
5. 所有可执行因子明确时序/截面作用域、字段单位、available_at、窗口、缺失值和复权语义；未来数据不得改变历史特征。重复窗口参数和别名不增加独立公式数量。
6. 模型只输出类型化研究假设、引用和允许的工具参数；执行走已有 DSL/工具白名单。默认每轮最多24个假设、2个并发研究任务，单个上下文最多32768字节且不超过既有更严格限制，达到预算即检查点停止。
7. 配置、因子使用范围和策略权重沿用显式审核；新内容导入、模型评分、来源刷新及纸面结果不得自动修改已批准政策。
8. 每阶段有真实 HTTP/worker/安装验证和一次独立整阶段审查；只改文档的任务运行文档检查。实际发布检查最终 PR 提交与合并后 main 的 CI，沿用用户已授权的自主修复和发布流程。

## 本阶段验收标准

- [ ] 至少200个去重且经来源审核的知识对象，含因子/策略/风险/案例；同一对象多入口只计一次。至少12条完整研究路径，每条端到端验收。
- [ ] 知识地图、因子、策略、风险、研究池、实验/报告入口可操作；出处/定义/验证/使用批准分别显示。
- [ ] 1440×900、1920×1080、375×812截图与交互回归，键盘/可见焦点/减少动态效果/加载/空/错误/迟到响应通过；离线单文件报告不依赖外部请求。
- [ ] 来源更新可比对、审核、保留旧版本与撤回说明；wheel安装、备份恢复、索引重建通过；最终PR提交与合并main的CI均通过后发布。

## 已下载参考内容

以下仓库已经下载；执行时按固定提交读取，不从默认分支漂移取材。具体文件、哈希与学习方向见[源码学习记录](../../research/2026-10-31-open-source-quant-study.md)及[锁定清单](../../research/2026-10-31-open-source-quant-lock.json)。新增参考先补来源和权利裁定，再决定安装或适配。

- [deepseek-harness-quant](https://github.com/yuanwang589-dev/deepseek-harness-quant/tree/9f72ab7e64799aecfc79cf3b913359acea1dd9b9)：`9f72ab7e64799aecfc79cf3b913359acea1dd9b9`。
- [QUANTAXIS](https://github.com/yutiansut/QUANTAXIS/tree/232aa7ac9869966108f02a8fff85f1f7b09a9ce2)：`232aa7ac9869966108f02a8fff85f1f7b09a9ce2`。
- [RD-Agent](https://github.com/microsoft/RD-Agent/tree/484776c211e4fbbeef03e0ec00d6bbee7362a4f4)：`484776c211e4fbbeef03e0ec00d6bbee7362a4f4`。

## Task 1：内容工作台与12条用户研究路径

**Files：**

- Create: `src/finahinking/research/knowledge_workspace_page.py`
- Create: `frontend/src/quant-workspace.js`
- Create: `frontend/test/quant-workspace.test.mjs`
- Create: `tests/p7_5/test_phase28_browser.py`
- Create: `tests/fixtures/quant_workbench/journeys.json`
- Modify: `frontend/src/knowledge.js`
- Modify: `frontend/src/asset-workspace.js`
- Modify: `src/finahinking/research/asset_workspace_page.py`
- Modify: `src/finahinking/research/factor_workspace_page.py`
- Modify: `src/finahinking/research/report_sections.py`
- Modify: `src/finahinking/local_app.py`
- Modify: `frontend/package.json`

**接口与输出：** 保留/knowledge和/library/{kind}，新工作区通过同源API读取统一目录及EvidenceRef；POST研究/审核保持显式提交与现有CSRF。工作区选中对象变化后清除旧提交目标。

**先写的行为测试：**

- test_twelve_journeys_reach_explainable_report：路径覆盖知识检索、缺数据修复、财务/技术/事件/ETF因子、稳健反证、组合、风险、池比较、学习建议及复现。
- test_three_viewports_keyboard_motion_and_statuses：三个尺寸逐项检查内容可见、键盘操作及状态恢复，不仅截图存在。
- test_changed_selection_rejects_late_response_and_old_approval：切换研究/版本后迟到响应与旧按钮目标不能污染新对象。
- test_offline_html_preserves_sources_and_has_zero_external_requests：单文件报告可读、可键盘展开，全部图表证据来自同一快照。

**执行步骤：**

- [ ] 读取上述现有文件及前置阶段产物；确认共享合同。新文件如有等价现成实现，先记录证据并合并复用，不能创建第二套事实源。
- [ ] 写上述失败用例及独立预期；执行以下命令，确认因目标行为缺失而失败，保存具体失败，不把环境/导入拼写错误当作有效红灯。
- [ ] 借鉴DeepSeek研究成果库、决策卡、池漏斗和因子详情的信息组织；保留现有蓝白视觉、导航及交互规范。优先展示研究问题、所需数据、适用条件、反例和下一步，技术摘要可展开；把新Node测试加入npm test和浏览器CI。
- [ ] 重跑同一用例并确认通过；执行受影响的旧入口回归，记录实际HTTP/worker或安装路径的证据及限制。
- [ ] 更新本阶段完成清单、内容数量和接口说明，定向暂存本任务文件并提交；不要包含其他工作区的修改。

**命令（仓库根目录执行）：**

```sh
npm --prefix frontend run build
npm --prefix frontend test
python3 -m pytest tests/p7_5/test_phase28_browser.py -q
```

**预期：** 首次为上述缺失行为失败；完成后相关用例全部通过。真实外部账户测试与离线受控样例分别记录。

## Task 2：版本化来源更新、安装恢复与发布门禁

**Files：**

- Create: `src/finahinking/research/source_updates.py`
- Create: `tests/research/test_source_updates.py`
- Create: `tests/validation/test_quant_knowledge_install.py`
- Create: `scripts/benchmark_quant_knowledge.py`
- Create: `docs/QUANT_KNOWLEDGE_GUIDE.md`
- Modify: `scripts/check_installed_journey.py`
- Modify: `tests/validation/test_research_asset_install.py`

**接口与输出：** compare_sources(old: SourceSnapshot, new: SourceSnapshot) -> dict；返回added/changed/removed/rights_changed/affected_items/review_required。确认仅创建新内容版本，不覆写旧证据或已批准政策；撤回标记影响后续使用，不篡改历史。

**先写的行为测试：**

- test_license_change_and_deleted_source_require_review：许可变化/内容删除产生待审差异；读取更新不自动下载运行代码。
- test_old_report_and_replay_still_resolve_old_content：新来源版本上线后旧研究继续绑定旧公式/知识/权利记录。
- test_clean_wheel_backup_restore_and_reindex：开发源码和参考目录不可达时，wheel仍完成一条实际研究；恢复后原证据字节不变。
- test_ten_thousand_item_search_benchmark：10k规范条目、固定30查询、记录硬件/冷暖/p50/p95；参考环境暖检索p95目标<=500ms，CI单列防回退阈值及依据。

**执行步骤：**

- [ ] 读取上述现有文件及前置阶段产物；确认共享合同。新文件如有等价现成实现，先记录证据并合并复用，不能创建第二套事实源。
- [ ] 写上述失败用例及独立预期；执行以下命令，确认因目标行为缺失而失败，保存具体失败，不把环境/导入拼写错误当作有效红灯。
- [ ] 增加可审阅来源差异与受影响对象清单，所有更新遵守Phase21逐文件规则；索引重建可恢复，原始证据不可覆写。按12路径验收内容可达与证据链；更新用户指南、能力口径、安装资产及发布记录，只填写实际通过结果。
- [ ] 重跑同一用例并确认通过；执行受影响的旧入口回归，记录实际HTTP/worker或安装路径的证据及限制。
- [ ] 更新本阶段完成清单、内容数量和接口说明，定向暂存本任务文件并提交；不要包含其他工作区的修改。

**命令（仓库根目录执行）：**

```sh
python3 -m pytest tests/research/test_source_updates.py tests/validation/test_quant_knowledge_install.py tests/validation/test_research_asset_install.py -q
python3 scripts/benchmark_quant_knowledge.py
```

**预期：** 首次为上述缺失行为失败；完成后相关用例全部通过。真实外部账户测试与离线受控样例分别记录。

## 阶段收尾与发布

- [ ] 创建或更新 `docs/reviews/2026-10-31-finathink-phase28-review.md`，列出验收项→测试/产物→结果→限制；数量由当前内容清单产生。该日期是计划批次，记录实际执行日期。
- [ ] 执行下面的本地门禁，并运行本阶段新增的真实HTTP/worker/浏览器/安装验收；浏览器产物保存三种尺寸截图和控制台检查。对纯数据合同任务用其实际研究集成路径验收。
- [ ] 对最终差异进行一次独立整阶段审查，集中修复有效问题并对修改运行针对性验证；不得用无关优化扩大本阶段。
- [ ] 复核待提交差异、来源通知和文档链接；创建/更新阶段PR，等待精确最终提交CI全部通过，再合并并核对main CI。沿用已授权发布流程，保留PR/提交/CI链接。
- [ ] 仅在本阶段验收完成后开始下一阶段；缺用户真实数据时交付明确NEEDS_DATA路径及受控样例证据，不伪造实证通过。

```sh
python3 -m ruff check src tests scripts
python3 -m pytest
npm --prefix frontend run build
npm --prefix frontend test
npm --prefix frontend run perf
git diff --check
```

这是实施时的门禁。本轮只交付规划文档，运行来源/链接/路径/一致性检查，不宣称上述未来测试已通过。
