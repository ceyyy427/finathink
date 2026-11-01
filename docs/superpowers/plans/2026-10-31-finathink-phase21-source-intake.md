# Phase 21：来源盘点与接入规范实施计划

> **执行方式：Native，按任务顺序推进。本文是待执行计划，不是完成记录。任务内采用测试先行；每阶段统一进行一次独立整阶段审查。**

**Goal：** 把已下载的七个项目变成可审计、可增量维护的参考资产，并对账已有52条资源。

**Architecture：** 只读源码审计器 → 逐文件权利裁定 → 导入提案 → 现有 ResearchAssetCatalog。审计不 import 上游模块，不运行安装器；来源证据与适配批准分离。

**Tech Stack：** Python >=3.11、numpy/pandas、SQLite、原生JS、现有Supervisor/spawn及HTML报告。

**Spec：** [共同设计与合同](../specs/2026-10-31-finathink-quant-knowledge-expansion-design.md)；[总规划书](../../FINATHINK_QUANT_KNOWLEDGE_ROADMAP_2026-10-31.md)。

**依赖：** 已发布Phase20及本轮来源锁定清单。后阶段新增合同不能提前当作现有实现。

**Review Focus：** 路径逃逸、符号链接、嵌套许可证、来源指令越权、旧资源兼容、Apache通知遗漏。

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

- [ ] 七个项目逐一核对完整提交及许可；本轮锁定的62个材料文件均可重算SHA-256。进入后续适配范围的新文件另补摘要。
- [ ] 两个重点项目全部已发现顶层模块有去向；每项记录本地落点、依赖、源文件、证据状态和下一阶段。文档主张、动态注册、外部路径均明确列出。
- [ ] 旧52条资源逐条对账：31已打包、15仅上游、6缺失；8个未解析因子原定义逐条补证据或维持待补，不能静默删除。
- [ ] MIT与经核对的Apache-2.0内容保留版权/NOTICE；未知或冲突许可只记录引用、阻断打包。来源变更不能覆盖旧版本。

## 已下载参考内容

以下仓库已经下载；执行时按固定提交读取，不从默认分支漂移取材。具体文件、哈希与学习方向见[源码学习记录](../../research/2026-10-31-open-source-quant-study.md)及[锁定清单](../../research/2026-10-31-open-source-quant-lock.json)。新增参考先补来源和权利裁定，再决定安装或适配。

- [QUANTAXIS](https://github.com/yutiansut/QUANTAXIS/tree/232aa7ac9869966108f02a8fff85f1f7b09a9ce2)：`232aa7ac9869966108f02a8fff85f1f7b09a9ce2`。
- [deepseek-harness-quant](https://github.com/yuanwang589-dev/deepseek-harness-quant/tree/9f72ab7e64799aecfc79cf3b913359acea1dd9b9)：`9f72ab7e64799aecfc79cf3b913359acea1dd9b9`。
- [qlib](https://github.com/microsoft/qlib/tree/54355232463878d2eebb91fe0ee5fa7fa1f5976c)：`54355232463878d2eebb91fe0ee5fa7fa1f5976c`。
- [alphalens-reloaded](https://github.com/stefan-jansen/alphalens-reloaded/tree/60755d1cc7d9d36a4c70c8344cbdb168fe99df24)：`60755d1cc7d9d36a4c70c8344cbdb168fe99df24`。
- [pyanomaly](https://github.com/chulwoohan/pyanomaly/tree/59758c913a4146bbfb9d3c056668ccdc5231c8c8)：`59758c913a4146bbfb9d3c056668ccdc5231c8c8`。
- [RD-Agent](https://github.com/microsoft/RD-Agent/tree/484776c211e4fbbeef03e0ec00d6bbee7362a4f4)：`484776c211e4fbbeef03e0ec00d6bbee7362a4f4`。
- [bt](https://github.com/pmorissette/bt/tree/4ae4090266300c0185f418aca18e28e649509a33)：`4ae4090266300c0185f418aca18e28e649509a33`。

## Task 1：只读来源审计与完整模块映射

**Files：**

- Create: `src/finahinking/research/source_contracts.py`
- Create: `src/finahinking/research/source_intake.py`
- Create: `tests/research/test_source_intake.py`
- Create: `docs/research/quant-source-module-map.json`

**接口与输出：** audit_source(root: Path, snapshot: SourceSnapshot) -> dict；返回 source_id、commit、modules、artifacts、errors、status。status 为 VERIFIED/BLOCKED，仅指来源完整性；文件权利仍单独裁定。

**先写的行为测试：**

- test_hash_mismatch_blocks_intake：改动一个字节即阻断，错误定位具体文件。
- test_symlink_or_parent_escape_is_rejected：链接到源码根外和../路径均拒绝，不读取目标。
- test_upstream_instructions_are_inert：含安装命令、AGENTS及提示注入的材料仅作为文本；上游命令执行与网络调用为零。只读git元数据可使用固定参数的受控调用，不能拼接来源文本。
- test_module_inventory_has_total_disposition：真实两个下载树的全部顶层模块都有记录；根文档、子模块及无法解析的动态引用另列。

**执行步骤：**

- [ ] 读取上述现有文件及前置阶段产物；确认共享合同。新文件如有等价现成实现，先记录证据并合并复用，不能创建第二套事实源。
- [ ] 写上述失败用例及独立预期；执行以下命令，确认因目标行为缺失而失败，保存具体失败，不把环境/导入拼写错误当作有效红灯。
- [ ] 实现冻结 SourceSnapshot 与只读目录/哈希审计。从 git 跟踪列表派生模块清单；映射每个模块到已适配/待验证/仅参考/缺证据/范围外，并写明原因。对比本次规划锁，不自动把整仓加入产品。
- [ ] 重跑同一用例并确认通过；执行受影响的旧入口回归，记录实际HTTP/worker或安装路径的证据及限制。
- [ ] 更新本阶段完成清单、内容数量和接口说明，定向暂存本任务文件并提交；不要包含其他工作区的修改。

**命令（仓库根目录执行）：**

```sh
python3 -m pytest tests/research/test_source_intake.py -q
```

**预期：** 首次为上述缺失行为失败；完成后相关用例全部通过。真实外部账户测试与离线受控样例分别记录。

## Task 2：逐文件复用裁定与旧资料对账

**Files：**

- Create: `src/finahinking/research/asset_rights.py`
- Create: `tests/research/test_asset_rights.py`
- Create: `docs/research/quant-resource-reconciliation.json`
- Modify: `src/finahinking/research/asset_catalog.py`
- Modify: `src/finahinking/research/asset_contracts.py`
- Modify: `tests/research/test_research_asset_catalog.py`
- Modify: `tests/validation/test_research_asset_install.py`

**接口与输出：** plan_asset_import(audit: dict, *, existing_catalog: ResearchAssetCatalog) -> dict；返回 eligible、reference_only、blocked、unresolved、required_notices。生成提案，不写入已批准目录。

**先写的行为测试：**

- test_reviewed_apache_asset_requires_notice：明确权利裁定及所需通知存在才允许打包；不能仅放宽MIT字符串判断。
- test_nested_license_overrides_repository_label：文件级权利不明时阻断，即使仓库根标MIT。
- test_all_legacy_resources_reconcile_without_id_changes：52条资源全部有去向，既有方法和报告引用仍可 resolve。
- test_wheel_has_source_notices_without_local_checkout：全新安装目录能读取获准资料和通知，开发机参考路径不存在也成功。

**执行步骤：**

- [ ] 读取上述现有文件及前置阶段产物；确认共享合同。新文件如有等价现成实现，先记录证据并合并复用，不能创建第二套事实源。
- [ ] 写上述失败用例及独立预期；执行以下命令，确认因目标行为缺失而失败，保存具体失败，不把环境/导入拼写错误当作有效红灯。
- [ ] 扩展现有资源许可合同，逐文件记录引用、适配和分发决策。保留旧资源ID/指纹；补入材料必须与内容哈希绑定。对6个缺失材料补齐或给出明确缺口，对15个仅上游材料逐项裁定；报告数量由机器清单计算。
- [ ] 重跑同一用例并确认通过；执行受影响的旧入口回归，记录实际HTTP/worker或安装路径的证据及限制。
- [ ] 更新本阶段完成清单、内容数量和接口说明，定向暂存本任务文件并提交；不要包含其他工作区的修改。

**命令（仓库根目录执行）：**

```sh
python3 -m pytest tests/research/test_asset_rights.py tests/research/test_research_asset_catalog.py tests/validation/test_research_asset_install.py -q
```

**预期：** 首次为上述缺失行为失败；完成后相关用例全部通过。真实外部账户测试与离线受控样例分别记录。

## 阶段收尾与发布

- [ ] 创建或更新 `docs/reviews/2026-10-31-finathink-phase21-review.md`，列出验收项→测试/产物→结果→限制；数量由当前内容清单产生。该日期是计划批次，记录实际执行日期。
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
