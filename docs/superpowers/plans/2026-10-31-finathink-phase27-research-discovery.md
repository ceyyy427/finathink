# Phase 27：自然语言研究与分析师闭环实施计划

> **执行方式：Native，按任务顺序推进。本文是待执行计划，不是完成记录。任务内采用测试先行；每阶段统一进行一次独立整阶段审查。**

**Goal：** 补全问题到因子候选之前的步骤，让五类分析师用知识、反例与量化证据协作。

**Architecture：** 知识检索 → 类型化StudyBlueprint → 原数据就绪/方法路由/授权 → 原worker和五分析师 → 原审核/纸面复盘。RD-Agent和上游Skill用于借鉴研究结构，不引入第二个任务调度器。

**Tech Stack：** Python >=3.11、numpy/pandas、SQLite、原生JS、现有Supervisor/spawn及HTML报告。

**Spec：** [共同设计与合同](../specs/2026-10-31-finathink-quant-knowledge-expansion-design.md)；[总规划书](../../FINATHINK_QUANT_KNOWLEDGE_ROADMAP_2026-10-31.md)。

**依赖：** Phase 26 的全部验收和共享合同。后阶段新增合同不能提前当作现有实现。

**Review Focus：** 文档提示注入、模型任意代码、来源引用造假、缺数据跳过、预算失控、历史审批/迟到结果复用。

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

- [ ] 30个冻结回归研究问题覆盖十类因子、策略及失败条件，其中8个完成真实本地闭环。模型外部调用另标授权与提供商证据。
- [ ] 每次研究经历问题/风格、检索反例、可证伪假设、数据检查、预注册设计、DSL候选、去重、训练验证、冻结测试、综合、审核、纸面观察、知识建议。
- [ ] 基本面/技术/情绪/新闻/学习五类分析师消费按角色裁剪且有出处的知识包，经理保留分歧和证据不足状态。
- [ ] 每轮最多24假设、2并发研究任务、上下文<=32768字节且遵守更严限制；超预算保存检查点。取消、重启、提供商失败与代码/输入变更均可回归。

## 已下载参考内容

以下仓库已经下载；执行时按固定提交读取，不从默认分支漂移取材。具体文件、哈希与学习方向见[源码学习记录](../../research/2026-10-31-open-source-quant-study.md)及[锁定清单](../../research/2026-10-31-open-source-quant-lock.json)。新增参考先补来源和权利裁定，再决定安装或适配。

- [RD-Agent](https://github.com/microsoft/RD-Agent/tree/484776c211e4fbbeef03e0ec00d6bbee7362a4f4)：`484776c211e4fbbeef03e0ec00d6bbee7362a4f4`。
- [deepseek-harness-quant](https://github.com/yuanwang589-dev/deepseek-harness-quant/tree/9f72ab7e64799aecfc79cf3b913359acea1dd9b9)：`9f72ab7e64799aecfc79cf3b913359acea1dd9b9`。
- [qlib](https://github.com/microsoft/qlib/tree/54355232463878d2eebb91fe0ee5fa7fa1f5976c)：`54355232463878d2eebb91fe0ee5fa7fa1f5976c`。

## Task 1：研究问题模板与类型化计划器

**Files：**

- Create: `src/finahinking/research/discovery_planner.py`
- Create: `tests/fixtures/quant_discovery/questions.json`
- Create: `tests/research/test_discovery_planner.py`
- Create: `src/finahinking/_package_data/research_assets/knowledge/research-questions.json`
- Modify: `src/finahinking/research/method_router.py`
- Modify: `src/finahinking/research/method_context.py`

**接口与输出：** prepare_discovery(question, profile, snapshot_ref, budget) -> StudyBlueprint；在预算/字段检查通过前只生成可审阅计划。NEEDS_DATA含字段与修复入口；提出hypothesis、反证条件、split_spec、cost/risk引用及fingerprint。

**先写的行为测试：**

- test_thirty_frozen_questions_have_expected_method_and_fields：固定问题对应期望方法、反例、数据依赖，不能只断言返回非空。
- test_missing_high_low_or_fundamentals_cannot_be_invented：上游近似做法不进入真实研究数据；缺失阻断相关步骤。
- test_retrieved_instructions_cannot_authorize_tools：引用中的命令、系统提示和付款指令均不改变权限。
- test_preregistered_blueprint_has_bounded_context_and_trial_count：24/32768限制和更严既有限制都生效；引用截断不得伪造完整证据。

**执行步骤：**

- [ ] 读取上述现有文件及前置阶段产物；确认共享合同。新文件如有等价现成实现，先记录证据并合并复用，不能创建第二套事实源。
- [ ] 写上述失败用例及独立预期；执行以下命令，确认因目标行为缺失而失败，保存具体失败，不把环境/导入拼写错误当作有效红灯。
- [ ] 把上游因子挖掘流程及RD-Agent假设/反馈结构转成自己的类型化模板；研究问题与知识条目双向关联。先检索正反证再选模板，冻结数据、政策和测试设计后才生成候选。输出只允许已有DSL与工具参数，不能执行模型自由代码。
- [ ] 重跑同一用例并确认通过；执行受影响的旧入口回归，记录实际HTTP/worker或安装路径的证据及限制。
- [ ] 更新本阶段完成清单、内容数量和接口说明，定向暂存本任务文件并提交；不要包含其他工作区的修改。

**命令（仓库根目录执行）：**

```sh
python3 -m pytest tests/research/test_discovery_planner.py tests/research/test_method_context.py -q
```

**预期：** 首次为上述缺失行为失败；完成后相关用例全部通过。真实外部账户测试与离线受控样例分别记录。

## Task 2：五分析师知识消费与持久研究闭环

**Files：**

- Create: `src/finahinking/research/discovery_runtime.py`
- Create: `tests/research/test_discovery_runtime.py`
- Create: `tests/research/test_discovery_journeys.py`
- Modify: `src/finahinking/research/application_service.py`
- Modify: `src/finahinking/research/workflow.py`
- Modify: `src/finahinking/research/analyst_runtime.py`
- Modify: `src/finahinking/research/method_handoff.py`

**接口与输出：** run_discovery(blueprint, *, authorization, cancel_token) -> dict；提交现有持久任务并返回job_ref。2并发研究任务与单研究内现有五分析师并行是不同额度；结果含逐角色source/evidence refs、分歧、预算消费、待审核提案。

**先写的行为测试：**

- test_eight_end_to_end_questions_produce_real_artifacts：真实HTTP→spawn→验证→综合→显式审核→纸面结果→知识提案，不用静态mock替代整个引擎。
- test_five_roles_consume_scoped_evidence_and_manager_keeps_conflicts：五角色引用可追溯，证据不足不能默认同意。
- test_cancel_restart_provider_failure_and_budget_stop：失败有界重试，终止不产生迟到批准，重启恢复同一冻结身份。
- test_changed_inputs_or_policy_invalidate_old_handoff：修改知识、数据或政策后旧模型回传不能提交新目标；没有隐式调用当前Codex聊天。

**执行步骤：**

- [ ] 读取上述现有文件及前置阶段产物；确认共享合同。新文件如有等价现成实现，先记录证据并合并复用，不能创建第二套事实源。
- [ ] 写上述失败用例及独立预期；执行以下命令，确认因目标行为缺失而失败，保存具体失败，不把环境/导入拼写错误当作有效红灯。
- [ ] 将计划器接现有队列/模型provider及handoff授权；离线验收使用确定性分析器和受控模型传输适配器，并清楚标记不等同真实付费模型语义验收。五分析师并行读取相关证据，经理综合，风险/组合经理沿用已实现运行时；学习产物只形成审核建议。
- [ ] 重跑同一用例并确认通过；执行受影响的旧入口回归，记录实际HTTP/worker或安装路径的证据及限制。
- [ ] 更新本阶段完成清单、内容数量和接口说明，定向暂存本任务文件并提交；不要包含其他工作区的修改。

**命令（仓库根目录执行）：**

```sh
python3 -m pytest tests/research/test_discovery_runtime.py tests/research/test_discovery_journeys.py tests/research/test_analyst_runtime.py tests/research/test_workflow.py -q
```

**预期：** 首次为上述缺失行为失败；完成后相关用例全部通过。真实外部账户测试与离线受控样例分别记录。

## 阶段收尾与发布

- [ ] 创建或更新 `docs/reviews/2026-10-31-finathink-phase27-review.md`，列出验收项→测试/产物→结果→限制；数量由当前内容清单产生。该日期是计划批次，记录实际执行日期。
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
