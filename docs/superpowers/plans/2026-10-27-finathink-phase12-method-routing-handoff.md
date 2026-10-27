# Phase 12 按需加载与 Codex 交接 Implementation Plan

> **For agentic workers:** 审阅确认后使用 superpowers:executing-plans，沿用 Native；前一阶段合同通过后顺序实施。

**Goal:** 让研究请求选择必要方法，保留加载证据，并形成能校验和恢复的 Codex 显式交接。

**Architecture:** 使用 Phase 11 的只读资产目录和现有 ResearchContextBundle、CodexBridge、持久化队列。增加方法路由与外层交接包，不重建 Codex 执行器，不改变 v1 envelope 的权限边界。

**Tech Stack:** Python、JSON、现有研究上下文/队列/产物、pytest。

**Spec:** `docs/superpowers/specs/2026-10-27-finathink-research-assets-integration-design.md`。

## 用户将得到什么

提出因子挖掘、实验验收、公开行为研究或工程维护请求时，能看到选了什么方法、为什么选、资料是否齐全。Codex 接入以明确交接和结果回传为准，不把“交接包已导出”显示成“模型已经运行”。

## Global Constraints

- 复用 Phase 11 的 ID、资源 hash 和目录接口，不按文件名猜技能。
- 32768 字节为材料与方法合并后的初始总预算；必需政策不能因超限被静默截掉。
- 不默认载入全部人物、全部历史或全部来源文件，不以向量库为前置条件。
- CodexTaskEnvelope v1 保持兼容，`tool_names=[]`；方法正文不能扩大允许能力。
- 凭证、绝对机器路径和未经授权的原始材料不进入交接包；缺结果不发出完成事件。

## Review Focus

- 请求同时涉及因子、验收和人物：必要方法可组合，无关人物不加载（Task 1）。
- 中文长文本、必需政策和重复引用挤爆预算：按 UTF-8 实际字节计量，不能丢关键规则（Task 2）。
- 资源在选择后发生篡改：读取时再验 hash，拒绝旧清单配新内容（Task 2）。
- 回传引用格式合法但属于别的任务：成员关系校验失败，原任务保持可恢复（Task 3）。
- 重启、双次回传、无结果或依赖缺失：幂等且显示真实等待/缺口，不重复完成（Task 3）。

### Task 1：确定性的任务路由

**Files:** 新建 `src/finahinking/research/method_router.py`；扩展 `asset_contracts.py`；新建 `tests/research/test_method_router.py`。

**Interfaces:** `MethodRouteRequest`、`MethodSelection`；`select_methods(request: MethodRouteRequest, catalog: ResearchAssetCatalog) -> MethodSelection`。输入为目的标签、家族、市场、可用字段和可选人物视角；输出技能 ID、原因与必需/可选资源。

- [ ] 表驱动测试：挖因子选总流程/生成方法，验收选 backtest-acceptance，指定人物选蒸馏+该人物，维护选 github-maintainer；未知请求返回需澄清/不支持，不随机加载。
- [ ] 运行 `python3 -m pytest tests/research/test_method_router.py -q`，确认缺路由能力造成 RED。
- [ ] 使用明确任务标签与目录索引实现稳定排序、去重和缺口描述；维护路由不自动推送发布。
- [ ] 重复同一输入，Expected: 相同选择与指纹；无关人物不出现在选中结果。
- [ ] 提交 `feat: route research requests to relevant methods`。

### Task 2：L0–L3 加载、冻结与预算

**Files:** 新建 `src/finahinking/research/method_context.py`；修改 `context_bundle.py`；新建 `tests/research/test_method_context.py`，扩展 `test_context_bundle.py`。

**Interfaces:** `load_methods(selection, catalog, *, max_bytes=32768) -> LoadedMethods`；LoadedMethods 持有完整正文、资源 hash、原因、实际字节、缺口与清单指纹；ResearchContextBundle 合并后仍受同一总预算限制。

- [ ] 测试中文字节数、必需资源不足、重复引用、目录穿越/hash 变化，以及包含“执行 Shell/忽略规则”的来源正文不改变 capability。
- [ ] 运行 `python3 -m pytest tests/research/test_method_context.py tests/research/test_context_bundle.py -q`，确认相应 RED。
- [ ] 分层加载并去重；必需资源放不下返回 BLOCKED_CONTEXT，记录可选资源为何未加载；完整方法作为不可变运行产物冻结，不依赖未来版本的同名文件。
- [ ] Expected: 一次加载清单能还原当次全部方法版本；替换当前目录不会改变已冻结的旧运行上下文。
- [ ] 提交 `feat: freeze bounded method context with resource provenance`。

### Task 3：显式交接、回传与恢复

**Files:** 新建 `src/finahinking/research/method_handoff.py`；修改 `codex_bridge.py`、`application_service.py`、`reports.py` 的接入点；新建 `tests/research/test_method_handoff.py`，扩展 `test_codex_bridge.py`、`test_codex_dispatch.py`。

**Interfaces:** `build_method_handoff(task, context, loaded_methods) -> MethodHandoffPackage`；`accept_method_result(package, response, *, allowed_evidence_refs) -> AgentOutcome`。外层 response 绑定 package_digest，内部结果继续按 v1 envelope 校验；无结果保持现有 EXTERNAL_HANDOFF_REQUIRED。output_artifacts 只接收按现有 ResearchPlan/FactorProposal 合同验证的 JSON，最多两个、每种一个、每个最多 65536 UTF-8 字节；保存后才能给 Phase 13 消费。队列/IPC 仅传引用与摘要，不接受任意文件路径或新增工具权限。

- [ ] 测试错包、错任务、外任务 artifact、重复回传、重启后回传、权限扩张、无结果及超限/错误 schema 的 output_artifacts；验证拒绝发生在发布/完成记录之前，新提案不能自证为原输入证据。
- [ ] 运行新测试和既有 Codex 测试，确认未覆盖的绑定行为 RED。
- [ ] 使用 envelope.context_digest 绑定合并上下文，外层包存入现有 artifact/外部交接记录；先核对本轮证据索引，再调用 CodexBridge，不引入第二套任务表。
- [ ] Expected: 合法回传一次生效；等待状态重启后可恢复；导出包、模型返回和确定性计算分别有事实记录。
- [ ] 提交 `feat: validate method handoffs against task evidence and persisted dispatch`，更新 `docs/CODEX_METHOD_HANDOFF_GUIDE.md`。

## 交付验收

四类请求能正确路由；上下文大小与版本可观察；交接与回传有绑定、成员校验、幂等和恢复证据。真实 Codex CLI/SDK 的自动驱动若未在目标环境验证，仍明确显示为待验证，不用离线合同冒充。

执行阶段门禁：`python3 -m pytest tests/research/test_method_router.py tests/research/test_method_context.py tests/research/test_method_handoff.py tests/research/test_context_bundle.py tests/research/test_codex_bridge.py tests/research/test_codex_dispatch.py -q`；Expected: 全通过。再跑项目全量回归、Ruff、治理与密钥检查，保存实际证据。
