# 研究资料模块回归台账

基线：Phase20 `f40d515a5f8ef6e5c7ddf49fcd6c36ae61581863`。本表覆盖Phase21改动及下游，其他模块继续由全量测试覆盖。命令均从仓库根目录执行；本地受控测试不调用用户数据或模型API。

依赖证据：`rg -n 'asset_catalog|asset_contracts' src/finahinking/research src/finahinking/local_app.py`。资料读取器的直接消费者包含method_router/context、factor_recipes/provenance/validation、application_service、asset_workspace和local_app。实际worker与报告路径由下表集成测试覆盖；未枚举的间接依赖仍运行全量测试，不据本扫描豁免。

| 模块与下游 | 回归命令 | 传播规则 |
| --- | --- | --- |
| 来源合同/审计 → 导入提案 | `python3 -m pytest tests/research/test_source_intake.py tests/research/test_asset_rights.py -q` | 来源身份、许可或文件状态改变时全部运行 |
| 目录/权利 → 方法路由/上下文/因子来源 | `python3 -m pytest tests/research/test_research_asset_catalog.py tests/research/test_research_asset_evidence.py tests/research/test_method_context.py tests/research/test_factor_provenance.py -q` | 读取合同改变必须传播到所有使用者 |
| 已审核资料 → 真实spawn/验证/报告 | `python3 -m pytest tests/research/test_method_factor_journey.py -q` | 检查冻结上下文、实际worker及九步验证 |
| 资料目录 → HTTP/工作台/前端 | `python3 -m pytest tests/research/test_asset_workspace.py tests/p7_5/test_phase21_browser.py -q`；`npm --prefix frontend test` | 新类别、旧ID、状态恢复与只读权限都验证 |
| 打包资源 → 全新wheel安装 | `python3 -m pytest tests/validation/test_research_asset_install.py -q` | 新资源或通知必须在独立安装目录可读 |
| 模型调用日志 → 实际worker/调用计量/质量比较 | `python3 -m pytest tests/research/test_model_call_journal.py tests/research/test_quality_research_flow.py tests/research/test_grounded_driver.py tests/research/test_grounded_spawn.py tests/research/test_grounded_application.py tests/research/test_application_service.py -q` | 以 `rg -n "model_call_journal\|ModelCallJournal" src/finahinking` 定位应用入口；初始化变更验证并发spawn、恢复和质量比较 |
| 未逐项列举的下游 | `python3 -m pytest`；`python3 -m ruff check src tests scripts` | 交付前运行，不以定向绿灯替代 |

上述命令在当前任务授权内执行。浏览器输出使用阶段专属目录，wheel测试的隔离临时目录按测试流程管理；只读审计命令另需显式提供参考源码根目录。命令退出码和实际结果记入[Phase21验收记录](reviews/2026-10-31-finathink-phase21-review.md)。
