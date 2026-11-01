# Phase 24：十类因子与来源卡实施计划

> **执行方式：Native，按任务顺序推进。本阶段已按任务实现，执行事实与发布门禁见末尾记录。任务内采用测试先行；每阶段统一进行一次独立整阶段审查。**

**Goal：** 形成至少120张可追溯来源卡及80个独立可执行公式，让研究范围超出少量价量指标。

**Architecture：** Phase21来源裁定 + Phase22知识解释 + Phase23面板DSL → 现有FactorSpec/注册表/因子工作区。每个公式与来源、依赖、数值例子及治理范围绑定。

**Tech Stack：** Python >=3.11、numpy/pandas、SQLite、原生JS、现有Supervisor/spawn及HTML报告。

**Spec：** [共同设计与合同](../specs/2026-10-31-finathink-quant-knowledge-expansion-design.md)；[总规划书](../../FINATHINK_QUANT_KNOWLEDGE_ROADMAP_2026-10-31.md)。

**依赖：** Phase 23 的全部验收和共享合同。后阶段新增合同不能提前当作现有实现。

**Review Focus：** 重复公式凑数、Alpha158列数误当独立公式、财务分母/单位、方向写死、复杂公式没有数值预期。

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

- [x] 至少120张来源卡，分别显示原式明确/待解析、适配状态及证据状态；缺公式的卡不计入可执行数。
- [x] 至少80个独立可执行公式，10个家族各至少4个：趋势动量、反转、波动尾部、流动性换手、价值、质量盈利应计、成长投资、行业风格、事件机构行为、ETF宏观。
- [x] 80公式逐项有来源/版本、字段、时序截面范围、单位、窗口、PIT、缺失值、数值样例、反例；窗口变体/同式别名不计新公式。
- [x] 原10项治理试点及历史FactorSpec继续可读，内容入库不自动批准金融用途；推荐方向有明确研究范围。

## 已下载参考内容

以下仓库已经下载；执行时按固定提交读取，不从默认分支漂移取材。具体文件、哈希与学习方向见[源码学习记录](../../research/2026-10-31-open-source-quant-study.md)及[锁定清单](../../research/2026-10-31-open-source-quant-lock.json)。新增参考先补来源和权利裁定，再决定安装或适配。

- [deepseek-harness-quant](https://github.com/yuanwang589-dev/deepseek-harness-quant/tree/9f72ab7e64799aecfc79cf3b913359acea1dd9b9)：`9f72ab7e64799aecfc79cf3b913359acea1dd9b9`。
- [QUANTAXIS](https://github.com/yutiansut/QUANTAXIS/tree/232aa7ac9869966108f02a8fff85f1f7b09a9ce2)：`232aa7ac9869966108f02a8fff85f1f7b09a9ce2`。
- [qlib](https://github.com/microsoft/qlib/tree/54355232463878d2eebb91fe0ee5fa7fa1f5976c)：`54355232463878d2eebb91fe0ee5fa7fa1f5976c`。
- [pyanomaly](https://github.com/chulwoohan/pyanomaly/tree/59758c913a4146bbfb9d3c056668ccdc5231c8c8)：`59758c913a4146bbfb9d3c056668ccdc5231c8c8`。

## Task 1：来源卡与公式去重目录

**Files：**

- Create: `src/finahinking/research/factor_library.py`
- Create: `src/finahinking/_package_data/research_assets/factors/library.json`
- Create: `tests/research/test_factor_library.py`
- Modify: `src/finahinking/research/factor_catalog.py`
- Modify: `src/finahinking/research/factor_provenance.py`
- Modify: `src/finahinking/research/asset_catalog.py`

**接口与输出：** load_factor_library(catalog) -> tuple[FactorSpec, ...]；来源卡和可执行项分开返回 inventory。formula_fingerprint对规范AST/单位/作用域版本计算，参数实例另记 experiment_id。

**先写的行为测试：**

- test_alias_and_window_grid_do_not_inflate_formula_count：同一公式20个窗口只计一个独立定义。
- test_missing_formula_is_source_card_only：README名称可收录为待解析，但不能运行或升级状态。
- test_cross_source_equivalent_formulas_share_family：同式来自两项目保留双出处，不创建双重独立证据。
- test_legacy_spec_refs_and_approval_scopes_survive：旧ID、审核版本与历史报告解析结果不变。

**执行步骤：**

- [x] 读取上述现有文件及前置阶段产物；确认共享合同。新文件如有等价现成实现，先记录证据并合并复用，不能创建第二套事实源。
- [x] 写上述失败用例及独立预期；执行以下命令，确认因目标行为缺失而失败，保存具体失败，不把环境/导入拼写错误当作有效红灯。
- [x] 逐条提取两重点项目、Qlib与Pyanomaly的原式，分类120张卡；由清单声明适配/独立实现/仅参考决定。单独统计source_card_count、executable_formula_count、alias_count与family_count，不将158特征列直接转成158独立因子。
- [x] 重跑同一用例并确认通过；执行受影响的旧入口回归，记录实际HTTP/worker或安装路径的证据及限制。
- [x] 更新本阶段完成清单、内容数量和接口说明，定向暂存本任务文件并提交；不要包含其他工作区的修改。

**命令（仓库根目录执行）：**

```sh
python3 -m pytest tests/research/test_factor_library.py tests/research/test_factor_provenance.py tests/research/test_factor_catalog.py -q
```

**预期：** 首次为上述缺失行为失败；完成后相关用例全部通过。真实外部账户测试与离线受控样例分别记录。

## Task 2：80公式的真实实现、例子与研究入口

**Files：**

- Create: `src/finahinking/_package_data/research_assets/factors/formulas.json`
- Create: `tests/fixtures/quant_factors/numerical-goldens.json`
- Create: `tests/research/test_factor_library_numerics.py`
- Create: `tests/research/test_factor_library_journey.py`
- Modify: `src/finahinking/research/factor_families.py`
- Modify: `src/finahinking/research/factor_workbench.py`
- Modify: `src/finahinking/research/factor_validation.py`
- Modify: `src/finahinking/factors/registry.py`

**接口与输出：** 沿用现有FactorSpec/因子验证入口执行Phase23表达式；每条numerical-goldens保存独立输入、预期、计算理由和来源。公式有值不等于投资有效；输出同时携带scope、missing_reason和evidence_refs。

**先写的行为测试：**

- test_all_eighty_formulas_match_independent_goldens：逐式执行，预期不能调用被测公式生成。
- test_each_family_has_at_least_four_distinct_formulas：十家族最低覆盖且总独立定义>=80。
- test_every_formula_enforces_pit_units_and_missingness：每条必需字段逐个移除；未来变更和错误单位不得通过。
- test_catalog_to_real_worker_produces_bound_evidence：从因子卡提交实际任务，报告绑定公式/快照/政策指纹；取消及重启可恢复。

**执行步骤：**

- [x] 读取上述现有文件及前置阶段产物；确认共享合同。新文件如有等价现成实现，先记录证据并合并复用，不能创建第二套事实源。
- [x] 写上述失败用例及独立预期；执行以下命令，确认因目标行为缺失而失败，保存具体失败，不把环境/导入拼写错误当作有效红灯。
- [x] 先以纸笔小面板和独立统计方法定义预期，再完成公式注册。财务分母为零、事件延迟、宏观修订、ETF持仓公布时间均显式建例；通过知识引用呈现公式解释与失效场景。真实worker验证至少覆盖每一家族。
- [x] 重跑同一用例并确认通过；执行受影响的旧入口回归，记录实际HTTP/worker或安装路径的证据及限制。
- [x] 更新本阶段完成清单、内容数量和接口说明，定向暂存本任务文件并提交；不要包含其他工作区的修改。

**命令（仓库根目录执行）：**

```sh
python3 -m pytest tests/research/test_factor_library_numerics.py tests/research/test_factor_library_journey.py tests/research/test_factor_validation_pipeline.py -q
```

**预期：** 首次为上述缺失行为失败；完成后相关用例全部通过。真实外部账户测试与离线受控样例分别记录。

## 阶段收尾与发布

- [x] 创建或更新 `docs/reviews/2026-10-31-finathink-phase24-review.md`，列出验收项→测试/产物→结果→限制；数量由当前内容清单产生。该日期是计划批次，记录实际执行日期。
- [x] 执行下面的本地门禁，并运行本阶段新增的真实HTTP/worker/浏览器/安装验收；浏览器产物保存三种尺寸截图和控制台检查。对纯数据合同任务用其实际研究集成路径验收。
- [x] 对最终差异进行一次独立整阶段审查，集中修复有效问题并对修改运行针对性验证；不得用无关优化扩大本阶段。
- [x] 复核待提交差异、来源通知和文档链接并准备阶段PR。远端发布门禁独立跟踪：最终HEAD六项CI → 合并 → 精确main六项CI；状态和链接记录在本阶段PR正文与检查时间线。
- [x] 仅在本阶段验收完成后开始下一阶段；缺用户真实数据时交付明确NEEDS_DATA路径及受控样例证据，不伪造实证通过。

```sh
python3 -m ruff check src tests scripts
python3 -m pytest
npm --prefix frontend run build
npm --prefix frontend test
npm --prefix frontend run perf
git diff --check
```

## 实际执行记录（2026-11-01）

实现提交 `f8e293b77655f03b5162cea1b718a0cc027f1772`：162张来源卡、80个独立公式、十家族各至少4项；每式自带可安装的独立输入与预期。旧10治理试点和27配方兼容，新增定义走显式研究入口及真实spawn worker。

核心合同先落盘，内容提取和集成随后依照该合同推进；最终以统一实现提交保存，未拆出破坏相互依赖的中间提交。使用现有资产目录与FactorSpec桥接，未修改原catalog/provenance/registry事实源；新版本的研究流程在独立模块中复用旧存储、评估器、账本和报告。

测试先行的实际失败及修复见[来源与数值记录](../../research/phase24-source-and-numerical-study.md)。部分最初红灯未保留单独日志，未补造记录；审查发现的单位、共享测试输入、来源展示、政策绑定及安装样例问题均有明确回归。独立整阶段审查一次，随后仅复核有效修正，269项定向复核通过。完整门禁与发布以[阶段验收](../../reviews/2026-10-31-finathink-phase24-review.md)和本阶段PR精确提交结果为准。

本地完整验收：2320项通过，1项可选statsmodels适配器跳过，强制浏览器无跳过；完整耗时774.45秒。前端71项、构建、性能、Ruff与治理通过。远端发布完成状态以本阶段PR的合并与精确main检查记录为准。
