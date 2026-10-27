# 方法加载与 Codex 显式交接

Phase 12 将“选择方法、冻结材料、导出交接、接收 JSON、确定性计算”分开记录。交接使用现有 `codex-task.v1`，工具列表始终为空；方法里的示例和来源文字不授予任何执行权限。

## 选择和冻结

`MethodRouteRequest` 接收目的标签、因子家族、市场、可用字段及指定的人物视角。`select_methods` 只从 Phase 11 目录选择对应 ID；未知目的返回需要澄清，不随机加载人物。字段缺口可以在规划阶段展示，例如没有真实换手率就记录 `field:turnover_rate`，不拿成交量代替。

`load_methods` 重新验证选中描述符和每项资源的版本、大小与 SHA-256。共享政策只加载一次；资源没有打包或属于可选项时，清单说明缺口。必需正文放不下则 `BLOCKED_CONTEXT`，不会截断政策。方法与研究材料合起来最多 32768 个 UTF-8 字节。旧的、不带方法的上下文调用保持原有合同和指纹。

冻结清单保存当次完整正文、方法版本、加载原因、资源摘要和输入缺口。以后修改同名方法，不会改变旧交接包。

## 本地应用入口

```python
from finahinking.research.asset_contracts import MethodRouteRequest

# service 是已有 ResearchApplicationService；job_id 来自同一用户的研究任务。
exported = service.export_method_handoff(job_id, MethodRouteRequest(
    purposes=("factor-mining", "backtest-acceptance"),
    factor_families=("momentum",),
    market="CN", available_fields=("close", "volume"),
))
package = exported["package"]
reference = exported["handoff_ref"]
```

这是显式服务接口，不会自动调用 Codex、启动新主研究任务、访问数据供应商或触发付费模型。保存包后，外部任务处于 `EXTERNAL_HANDOFF_REQUIRED`。可以通过 `get_method_handoff(job_id, reference)` 查看；其他用户的任务不可读取。

## 回传合同

外层是 `method-result.v1`，必须带原包的 `package_digest`；`result` 是原有 v1 结果，明确携带同一 `task_id`、`role`、`input_digest`、`paper_only: true`。顶层和输出内的证据引用，都必须属于原输入证据索引及调用方允许的集合。新的提案产物不能把自己当作原始输入证据。

可选 `output_artifacts` 最多两项：一种一个 `ResearchPlan`（`research-plan.v1`）和 `FactorProposal`（`factor-proposal.v1`）。每项是封闭的 JSON 对象：`artifact_type`、`schema_version`、`input_digest`、`content_digest`、`payload`，整个项最多 65536 个 UTF-8 字节。内容摘要使用项目的 `stable_digest(payload)`；因子提案还要通过既有 DSL、字段和纸面研究约束。不能回传代码、任意路径、Pickle 或新工具权限。

```python
received = service.accept_method_handoff(job_id, reference, response)
outcome = received["outcome"]
artifacts = received["artifacts"]
```

`None` 保持等待；绑定错误、超限或非法产物在完成记录和产物发布之前拒绝。合法产物存入现有私有产物目录，队列只保存引用/摘要。相同回传重放幂等，两个产物交换顺序也不改变结果；不同回传不能覆盖已接受结果。

## 恢复与状态含义

先原子保存产物和结果收据，再更新现有外部交接队列。中途退出后，状态读取会核对收据、产物摘要及原包绑定，再恢复队列；不会重跑模型。已完成记录缺失收据、摘要不符或产物损坏时拒绝读取，不静默显示为空结果。

摘要中的 `READY` 只表示结构化回传通过校验。`deterministic_execution: NOT_RUN` 表示还没有执行量化验证；`automatic_codex_driver: NOT_VERIFIED` 表示此阶段没有验证自动 Codex 驱动。Phase 13 只能消费已持久化、经校验的产物，再单独运行确定性计算。

## 验证

阶段测试包括组合路由、字节预算、冻结版本、错任务引用、权限扩张、两类输出、进程重启、断点恢复、重复回传和用户隔离；同时保留原 CodexBridge、上下文及应用服务回归。离线合同测试不代表真实外部模型已经执行。
