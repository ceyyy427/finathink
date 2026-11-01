# Phase 22：统一量化知识库实施计划

> **执行方式：Native，按任务顺序推进。本文是待执行计划，不是完成记录。任务内采用测试先行；每阶段统一进行一次独立整阶段审查。**

**Goal：** 让用户查得到原理、看得懂公式和反例，并能从知识进入研究准备。

**Architecture：** 桥接 p7_5 基础目录、p8_2b 精讲目录和 ResearchAssetCatalog；新增内容包是事实源，SQLite全文索引是可重建投影。现有个人掌握度/笔记继续私有存储。

**Tech Stack：** Python >=3.11、numpy/pandas、SQLite、原生JS、现有Supervisor/spawn及HTML报告。

**Spec：** [共同设计与合同](../specs/2026-10-31-finathink-quant-knowledge-expansion-design.md)；[总规划书](../../FINATHINK_QUANT_KNOWLEDGE_ROADMAP_2026-10-31.md)。

**依赖：** Phase 21 的全部验收和共享合同。后阶段新增合同不能提前当作现有实现。

**Review Focus：** 概念去重、历史ID兼容、中文检索评测污染、来源引文丢失、私有笔记泄露。

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

- [ ] 至少80个审核后的规范知识条目、8个主题域、12条学习/研究路径、40个确定性例子、24条误区解释；翻译/别名不重复计数。
- [ ] 先冻结60题及人工可核对的相关条目集合，再实现检索；Top-5命中率至少85%，每域至少75%。题集覆盖全部主题，保留后续优化测试集。
- [ ] 全部词条包含出处、条件、所需字段和反例；经验性主张显示其真实证据级别。
- [ ] 旧15基础概念、4路径和6精讲单元的URL/ID与用户学习记录保持可读；任何知识GET请求不创建研究任务或模型调用。

## 已下载参考内容

以下仓库已经下载；执行时按固定提交读取，不从默认分支漂移取材。具体文件、哈希与学习方向见[源码学习记录](../../research/2026-10-31-open-source-quant-study.md)及[锁定清单](../../research/2026-10-31-open-source-quant-lock.json)。新增参考先补来源和权利裁定，再决定安装或适配。

- [QUANTAXIS](https://github.com/yutiansut/QUANTAXIS/tree/232aa7ac9869966108f02a8fff85f1f7b09a9ce2)：`232aa7ac9869966108f02a8fff85f1f7b09a9ce2`。
- [deepseek-harness-quant](https://github.com/yuanwang589-dev/deepseek-harness-quant/tree/9f72ab7e64799aecfc79cf3b913359acea1dd9b9)：`9f72ab7e64799aecfc79cf3b913359acea1dd9b9`。
- [qlib](https://github.com/microsoft/qlib/tree/54355232463878d2eebb91fe0ee5fa7fa1f5976c)：`54355232463878d2eebb91fe0ee5fa7fa1f5976c`。
- [alphalens-reloaded](https://github.com/stefan-jansen/alphalens-reloaded/tree/60755d1cc7d9d36a4c70c8344cbdb168fe99df24)：`60755d1cc7d9d36a4c70c8344cbdb168fe99df24`。
- [pyanomaly](https://github.com/chulwoohan/pyanomaly/tree/59758c913a4146bbfb9d3c056668ccdc5231c8c8)：`59758c913a4146bbfb9d3c056668ccdc5231c8c8`。

## Task 1：桥接现有目录并冻结检索评测

**Files：**

- Create: `src/finahinking/research/knowledge_contracts.py`
- Create: `src/finahinking/research/knowledge_index.py`
- Create: `tests/research/test_knowledge_index.py`
- Create: `tests/fixtures/quant_knowledge/search-goldens.json`
- Modify: `src/finahinking/p7_5/knowledge.py`
- Modify: `src/finahinking/p8_2b/catalog.py`
- Modify: `src/finahinking/research/asset_catalog.py`

**接口与输出：** KnowledgeIndex.from_catalogs(basic, advanced, assets, content)；search(query, filters=None, limit=5) -> list[dict]；resolve(item_id) -> dict。结果含 canonical_id、title、source_refs、evidence_refs、matched_fields；派生索引版本绑定全部内容指纹。

**先写的行为测试：**

- test_legacy_ids_resolve_to_canonical_items：旧入口解析为同一规范对象，不能创建重复概念。
- test_rebuild_preserves_private_learning_state：删除索引并重建后公共检索一致，个人笔记/进度不变且不进入公共导出。
- test_chinese_search_golden_coverage：按冻结题集输出整体及逐域Top-5，不在测评脚本中针对题目硬编码。
- test_read_only_lookup_has_no_job_or_provider_effects：GET检索/详情不产生任务、付费调用或审核变更。

**执行步骤：**

- [ ] 读取上述现有文件及前置阶段产物；确认共享合同。新文件如有等价现成实现，先记录证据并合并复用，不能创建第二套事实源。
- [ ] 写上述失败用例及独立预期；执行以下命令，确认因目标行为缺失而失败，保存具体失败，不把环境/导入拼写错误当作有效红灯。
- [ ] 用现有SQLite能力实现FTS5及中文二元分词；环境缺FTS5时使用经过同题集验收的确定性降级或明确安装诊断。索引引用三类现有内容及新版本包，前置关系循环校验、规范ID冲突报错。先记录60题题集摘要和审核理由。
- [ ] 重跑同一用例并确认通过；执行受影响的旧入口回归，记录实际HTTP/worker或安装路径的证据及限制。
- [ ] 更新本阶段完成清单、内容数量和接口说明，定向暂存本任务文件并提交；不要包含其他工作区的修改。

**命令（仓库根目录执行）：**

```sh
python3 -m pytest tests/research/test_knowledge_index.py tests/p7_5/test_knowledge.py tests/p8_2b/test_local_knowledge.py -q
```

**预期：** 首次为上述缺失行为失败；完成后相关用例全部通过。真实外部账户测试与离线受控样例分别记录。

## Task 2：补齐中文内容并接到知识页面

**Files：**

- Create: `src/finahinking/_package_data/research_assets/knowledge/catalog.json`
- Create: `src/finahinking/_package_data/research_assets/knowledge/examples.json`
- Create: `src/finahinking/_package_data/research_assets/knowledge/paths.json`
- Create: `scripts/validate_quant_knowledge.py`
- Create: `tests/research/test_quant_knowledge_content.py`
- Create: `tests/p7_5/test_phase22_browser.py`
- Modify: `frontend/src/knowledge.js`
- Modify: `frontend/test/knowledge.test.mjs`
- Modify: `src/finahinking/local_app.py`

**接口与输出：** 内容遵循共同设计 KnowledgeItem；研究入口只携带知识ID、字段要求与方法引用进入准备页，用户明确提交后才排队。/knowledge、/knowledge/{id}及旧API保留。

**先写的行为测试：**

- test_content_targets_count_only_reviewed_canonical_items：80/8/12/40/24由清单计算，不计占位符和别名。
- test_examples_have_independent_expected_values：例子包含输入、手算/独立预期及出处，实际执行核对。
- test_search_to_preparation_keeps_source_and_needs_data：知识页跳转后出处和缺字段不丢失，没有后台隐式调用。
- test_keyboard_empty_error_and_stale_results：键盘可达、无结果可修正、请求失败可重试，旧搜索响应不能覆盖新搜索。

**执行步骤：**

- [ ] 读取上述现有文件及前置阶段产物；确认共享合同。新文件如有等价现成实现，先记录证据并合并复用，不能创建第二套事实源。
- [ ] 写上述失败用例及独立预期；执行以下命令，确认因目标行为缺失而失败，保存具体失败，不把环境/导入拼写错误当作有效红灯。
- [ ] 围绕数据/PIT、收益市场、统计、技术价量、财务、因子检验、策略组合、风险复盘编写分层讲解。每条提供入门解释、公式、例子、应用、反例；用验证脚本检查引用、依赖、数量及例子结果；页面沿用蓝白AppShell。
- [ ] 重跑同一用例并确认通过；执行受影响的旧入口回归，记录实际HTTP/worker或安装路径的证据及限制。
- [ ] 更新本阶段完成清单、内容数量和接口说明，定向暂存本任务文件并提交；不要包含其他工作区的修改。

**命令（仓库根目录执行）：**

```sh
python3 scripts/validate_quant_knowledge.py
python3 -m pytest tests/research/test_quant_knowledge_content.py tests/p7_5/test_phase22_browser.py -q
npm --prefix frontend test
```

**预期：** 首次为上述缺失行为失败；完成后相关用例全部通过。真实外部账户测试与离线受控样例分别记录。

## 阶段收尾与发布

- [ ] 创建或更新 `docs/reviews/2026-10-31-finathink-phase22-review.md`，列出验收项→测试/产物→结果→限制；数量由当前内容清单产生。该日期是计划批次，记录实际执行日期。
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
