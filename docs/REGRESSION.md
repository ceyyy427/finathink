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
| 排队/取消浏览器fixture → 复跑任务绑定 | `FINATHINK_REQUIRE_BROWSER=1 python3 -m pytest tests/p7_5/test_phase8_browser.py -q` | `create_server`会启动owner，测试需在服务初始化后停止owner再入队；保留取消绑定及两任务状态断言 |
| 未逐项列举的下游 | `python3 -m pytest`；`python3 -m ruff check src tests scripts` | 交付前运行，不以定向绿灯替代 |

上述命令在当前任务授权内执行。浏览器输出使用阶段专属目录，wheel测试的隔离临时目录按测试流程管理；只读审计命令另需显式提供参考源码根目录。命令退出码和实际结果记入[Phase21验收记录](reviews/2026-10-31-finathink-phase21-review.md)。


## Phase22统一知识库传播检查

基线为已发布Phase21 `c2b50d5`。`rg -n 'KnowledgeIndex|knowledge_content|quant_knowledge|knowledge_workspace' src tests scripts`定位新合同、资料读取、应用路由、安装探针与浏览器；`rg -n 'asset_catalog|asset_contracts' src/finahinking/research src/finahinking/local_app.py`继续覆盖方法路由、worker和报告的共享资料读取。

| 模块与下游 | 回归命令 | 必须保留的行为 |
| --- | --- | --- |
| 规范合同 → 中文FTS5投影/旧ID | `python3 -m pytest tests/research/test_knowledge_index.py tests/p7_5/test_knowledge.py tests/p8_2b/test_local_knowledge.py -q` | 别名去重、循环检查、缓存重建、私有库隔离、FTS5诊断 |
| 内容包/权利 → 公式/数值/来源 | `python3 scripts/validate_quant_knowledge.py`；`python3 -m pytest tests/research/test_quant_knowledge_content.py tests/research/test_asset_rights.py -q` | 独立预期、字段白名单、来源条件与证据等级 |
| 冻结题集 → 生产检索 | `python3 scripts/evaluate_knowledge_search.py --split all` | 固定60题摘要；整体与逐域Top-5 |
| 路由/JS → 实际HTTP页面 | `FINATHINK_REQUIRE_BROWSER=1 python3 -m pytest tests/p7_5/test_phase22_browser.py -q`；`python3 -m pytest tests/research/test_knowledge_workspace.py -q`；`npm --prefix frontend test` | GET无研究副作用；分页、条件、错误/空/迟到结果、三尺寸及无JS |
| 打包目录 → 全新wheel安装 | `python3 -m pytest tests/validation/test_research_asset_install.py -q` | 离开checkout可读取80词条、16路径和旧ID；完整包读取显式使用1MiB上限，常规读取默认64KiB保持 |
| 资料指纹 → 真实worker/因子验证/报告 | `python3 -m pytest tests/research/test_method_factor_journey.py -q` | 现有方法上下文、spawn及九步检验不因知识资料新增改变执行权限 |
| 全部间接下游 | `python3 -m pytest -q`；`python3 -m ruff check src tests scripts` | 不以定向门禁替代全量回归 |

实际结果、截图与本轮发现的缺陷见[Phase22验收](reviews/2026-10-31-finathink-phase22-review.md)。

## Phase23 时点面板与算子传播

基线为已发布Phase22 `1388a5c`。依赖定位：`rg -n 'ResearchFieldAttachment|ResearchFieldStore|normalize_records|SnapshotStore|evaluate_expression' src/finahinking`；新增调用：`rg -n 'build_panel|evaluate_panel_request|evaluate_panel_expression' src tests scripts`。数据附件读取影响方法研究、因子验证及持久快照；新HTTP复用既有本地CSRF和研究服务。

| 模块与下游 | 回归命令 | 传播要求 |
| --- | --- | --- |
| 数据合同/映射/存储 → 因子研究与面板 | `python3 -m pytest tests/data tests/research/test_method_factor_journey.py -q` | 旧附件、字段单位、可用时间与真实worker均覆盖 |
| 24算子 → 版本表达式与研究结果 | `python3 -m pytest tests/research/test_panel_operators.py tests/research/test_panel_dsl.py tests/research/test_factor_dsl.py -q` | 手算、未来变更、资产重排、训练边界、旧指纹 |
| 面板API → 实际HTTP/spawn | `python3 -m pytest tests/research/test_panel_journey.py -q` | CSRF、绑定、结果重建、错误、无隐式排队 |
| 应用外壳 → 既有浏览器入口 | `FINATHINK_REQUIRE_BROWSER=1 python3 -m pytest tests/p7_5/test_phase22_browser.py -q` | 三尺寸、键盘、控制台、现有知识到准备路径 |
| 新模块与API → 全新安装 | `python3 scripts/clean_install.py` | 独立wheel环境真实HTTP，离开checkout运行 |
| 未逐项列举下游 | `FINATHINK_REQUIRE_BROWSER=1 python3 -m pytest -q`；`python3 -m ruff check src tests scripts` | 冻结生产源码后运行全量；前端build/test/perf保留 |

命令在已授权的隔离工作树内执行，使用本地合成数据。安装门禁下载公开运行依赖；不调用用户API或模型账户。结果见[Phase23验收](reviews/2026-10-31-finathink-phase23-review.md)。

## Phase24 因子库传播检查

基线为已发布Phase23 `2678d1a`。依赖证据：`rg -n 'FactorSpec|resolve_factor_spec|run_factor_validation|ResearchFieldStore' src/finahinking/research`；新增目录/执行入口实现后复核 `rg -n 'FactorLibrary|factor_library|library_study' src tests scripts`。来源、内容和可执行定义分别计数，旧10治理试点继续作为独立兼容回归。

| 模块与下游 | 回归命令 | 必须验证 |
| --- | --- | --- |
| 因子来源/公式 → 目录/旧引用 | `python3 -m pytest tests/research/test_factor_library.py tests/research/test_factor_catalog.py tests/research/test_factor_provenance.py -q` | AST/单位/作用域去重、缺公式只读、旧ID与治理 |
| 公式/面板 → 确定性数值 | `python3 -m pytest tests/research/test_factor_library_numerics.py tests/research/test_panel_operators.py tests/research/test_panel_dsl.py -q` | 独立预期、逐字段缺失、错误单位、未来变更 |
| 因子卡 → 真实任务/验证/报告 | `python3 -m pytest tests/research/test_factor_library_journey.py tests/research/test_factor_validation_pipeline.py tests/research/test_method_factor_journey.py -q` | 十家族worker、绑定、取消/恢复与旧流程 |
| 资源登记 → 全新wheel | `python3 -m pytest tests/validation/test_research_asset_install.py tests/validation/test_artifact_install.py -q` | 逐字节许可/来源、安装后真实HTTP与worker |
| 所有间接下游 | `FINATHINK_REQUIRE_BROWSER=1 python3 -m pytest -q`；`python3 -m ruff check src tests scripts`；`npm --prefix frontend run build`；`npm --prefix frontend test`；`npm --prefix frontend run perf` | 最终生产源码冻结后运行完整门禁；新增浏览器测试加入CI |

命令在本任务已授权的隔离工作树执行；测试数据为本地合成数据，安装只获取公开依赖。实际结果和限制在Phase24验收记录中按执行事实填写，不以预计数量或文档存在代替通过证据。
