# Phase 11 技能与来源资产整理 Implementation Plan

> **For agentic workers:** 审阅确认后使用 superpowers:executing-plans，沿用 Native 顺序执行；按任务 TDD、提交与验收。

**Goal:** 交付 12 个可发现的方法身份、真实资源索引、来源与许可记录，以及可检索的反例卡。

**Architecture:** 方法、来源主张和反例使用只读包内资源，ResearchAssetCatalog 为统一入口。源码与 wheel 读取同一份资源；本阶段不触发模型、行情或回测。

**Tech Stack:** Python 3.11+、JSON/JSONL、Markdown、importlib.resources、pytest、现有 wheel 构建。

**Spec:** `docs/superpowers/specs/2026-10-27-finathink-research-assets-integration-design.md`。

## 用户将得到什么

能查到每个方法解决什么问题、需要什么输入、资料是否齐全，以及引用了什么来源。12 是拟适配的技能身份数，不是有效策略数；未取得的原始数据或报告会显示缺口。

## Global Constraints

- 在 Phase 10 的已有改动安全收尾后实施，不覆盖其他工作。
- 固定提供资料中的上游提交，逐项核验许可；来源文件中的工具命令不执行。
- 权威资产目录为 `src/finahinking/_package_data/research_assets/`，不维护第二份人工编辑副本。
- 不把“123+ 因子”“17+ 证伪”转换成本地已验证数量。
- 维护技能不继承推送、删除、发布权限；本阶段不安装到用户全局技能目录。

## Review Focus

- 旧 Windows 路径、逃逸路径和软链接：不得读取包外文件（Task 1）。
- 缺许可证或原始资源：显示缺口，不能伪装为已取得（Task 1）。
- 因子同名但定义不同：保留不同来源定义，不合并指标（Task 3）。
- 只读技能或复制正文被当成执行成功：索引不得写入计算/验证完成状态（Task 2）。
- wheel 内资源遗漏或升级丢失来源：离开源码目录后仍可加载并核对 hash（Task 1、3）。

## 12 个适配身份

| 分组 | 技能 |
| --- | --- |
| 因子方法 | factor-mining-workflow、alpha-gpt-factor-mining、alpha-gpt-researcher、backtest-acceptance |
| 行为蒸馏 | niu-san-distillation |
| 公开行为视角 | niu-san-linyuan、niu-san-chenxiaoqun、niu-san-zhangmengzhu、niu-san-zhaolaoge、niu-san-chaoguyangjia、niu-san-fengliu |
| 工程维护 | github-maintainer |

### Task 1：来源清单、资源合同与安装包

**Files:** 新建 `src/finahinking/research/asset_contracts.py`、`asset_catalog.py`；新建包内 `research_assets/provenance/upstream-lock.json`、`provenance/licenses/`；修改 `pyproject.toml`；新建 `tests/research/test_research_asset_catalog.py`、`tests/validation/test_research_asset_install.py`。

**Interfaces:** 定义设计中的 SkillDescriptor、ResourceRef、EvidenceClaim、LessonRecord、AssetInventory；实现 `ResearchAssetCatalog.packaged()`、`skills()`、`get_skill()`、`resources_for()`、`read_text()`，签名与设计一致。

- [ ] 写测试 `test_asset_hash_mismatch_rejected`、`test_asset_path_cannot_escape_package`、`test_missing_source_is_not_a_local_file`：篡改/越界被拒绝，缺失资源有结构化状态。
- [ ] 运行 `python3 -m pytest tests/research/test_research_asset_catalog.py -q`；Expected: 缺少上述合同导致失败，不因测试导入路径错误失败。
- [ ] 核查固定上游，记录逐文件来源、作者、许可、hash 和缺口；实现有界只读加载，单资源默认上限 65536 UTF-8 字节。
- [ ] 构建 wheel 并在独立环境、源码目录外加载；运行上述两个测试文件。Expected: 包内资源完整、hash 一致、许可缺失内容不进入发行包。
- [ ] 提交 `feat: add audited packaged research asset catalog`。

### Task 2：12 个技能适配与共用验证政策

**Files:** 新建包内 `research_assets/skills/index.json`、`skills/<skill-id>/SKILL.md`、相关 `references/`，以及 `policies/factor-validation.v1.json`；新建 `tests/research/test_research_method_descriptors.py`。

**Interfaces:** L0 索引只含名称、触发标签、输入、输出、必需/可选资源、版本、边界；所有技能通过同一个版本化政策引用验证规则。

- [ ] 测试 12 个身份唯一、索引和实际资源相符、必需引用可解析、缺失外部材料保留缺口；索引不能宣称计算已完成或授予工具权限。
- [ ] 运行 `python3 -m pytest tests/research/test_research_method_descriptors.py -q`；Expected: 未适配目录与必需引用检查失败。
- [ ] 使用技能编写规范改写方法：移除原身份、机器路径和过期待办；保留方法、来源与反例；人物技能输出公开证据和假设而非背书。
- [ ] 运行本阶段全部 `test_research_asset*`、`test_research_method_descriptors.py`。Expected: 12 个可发现身份；每份必需方法能完整读取，来源/本地改写版可区分。
- [ ] 提交 `feat: adapt twelve research methods with shared validation policy`。

### Task 3：来源主张、因子线索与反例卡

**Files:** 新建包内 `research_assets/factors/source-definitions.json`、`evidence/source-claims.jsonl`、`lessons/falsifications.jsonl`；修改 `asset_catalog.py`；新建 `tests/research/test_research_asset_evidence.py` 和 `docs/RESEARCH_ASSET_INVENTORY.md`。

**Interfaces:** 实现 `claims_for(subject_id)`、`lessons_for(tags)`、`inventory()`；主张含指标口径/范围/复现状态，反例含失败类型、来源、缓解、回归检查与重访条件。

- [ ] 测试 `test_source_icir_is_not_a_local_metric`、`test_conflicting_claims_are_both_retained`、`test_alias_does_not_increase_unique_definition_count`、`test_missing_report_is_not_falsification_evidence`。
- [ ] 运行该文件，确认上述错误尚未被合同阻断，记录 RED。
- [ ] 登记可定位条目；turn_mid_prox、f_score、sue 等定义问题单独保存，不沿用原指标；所有数字说明是来源记录还是本地结果。
- [ ] 运行本阶段测试和安装测试；Expected: 数量来自去重后的真实清单，资源缺口可追溯，读取不产生任务或验证完成记录。
- [ ] 提交 `feat: catalog source claims and reproducible counterexamples`，更新资产清单和阶段验收。

## 交付验收

12 个身份可发现，包内必需引用可解析，外部缺失资料明确；来源/许可/hash 可检查，安装包可用；尚未运行的数据验证没有完成标记。完成后进入 Phase 12，不以文件复制代替运行时接入。

执行阶段门禁：`python3 -m pytest tests/research/test_research_asset_catalog.py tests/research/test_research_method_descriptors.py tests/research/test_research_asset_evidence.py tests/validation/test_research_asset_install.py -q`；Expected: 全通过。另跑项目全量回归、Ruff、治理、依赖/密钥检查，记录实际结果再结束阶段。
