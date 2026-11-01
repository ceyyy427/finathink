# 量化知识库使用指南

打开本地应用的 **知识学习**，进入 `/knowledge`。知识库包含80个中文规范词条、8个主题域、12条新学习路径、48个可执行手算核对示例和80条误区解释；原有15个基础概念、4条课程路径与6个精讲单元继续保留。

## 从问题到研究准备

```mermaid
flowchart LR
    Q[中文问题 / 英文术语] --> S[主题、类型与来源检索]
    S --> E[解释 → 公式 → 手算 → 反例]
    E --> P[所需字段、前置知识与固定来源]
    P --> R[研究准备：选择自己的数据]
    R --> W[明确提交后进入既有研究流程]
    classDef knowledge fill:#eaf2ff,stroke:#2563eb,color:#17345c;
    classDef evidence fill:#e9f6ef,stroke:#32815a,color:#17482f;
    classDef preparation fill:#fff3df,stroke:#a96712,color:#62410e;
    class Q,S knowledge;
    class E,P evidence;
    class R,W preparation;
```

1. 输入“回撤”“Rank IC”或自然语言问题，使用主题域、内容类型、主张类型及参考来源缩小结果。结果按16项分页，也可直接进入学习路径。
2. 打开词条，先阅读解释与公式口径，再核对条件、所需字段、手算输入与独立预期。来源面板提供固定提交、文件、行号、文件摘要和许可引用。
3. 使用“检查研究所需数据”。准备页保留知识ID、版本、目录指纹、字段要求与方法引用；尚未选择研究数据时显示 `NEEDS_DATA`。
4. 使用“带入研究问题”，到现有研究任务页选择快照、标的与运行设置。页面继续显示知识来源，预填问题带有知识版本、目录指纹、字段和方法；只有明确提交才进入后台任务。
5. 学习路径中的词条都可展开前置知识。已有课程的自测和私人学习记录仍通过原入口使用。

关闭JavaScript后，检索表单、分页、词条、来源与研究准备仍可阅读。桌面与移动宽度支持键盘焦点；减少动态效果偏好会关闭卡片过渡。

## 内容范围

| 主题域 | 规范词条 | 代表内容 |
| --- | ---: | --- |
| 数据与时点 | 10 | 可用时间、修订、样本池、复权、日历、单位与指纹 |
| 收益与市场 | 10 | 简单/对数/总回报、复利、年化、基准、市值与流动性 |
| 统计方法 | 10 | 均值、方差、相关、回归、Beta、区间与稳健统计 |
| 技术与价量 | 10 | MA、EMA、动量、反转、RSI、MACD、ATR、布林带、VWAP |
| 财务分析 | 10 | 价值、盈利、资产增长、应计、杠杆、现金流与盈利意外 |
| 因子检验 | 10 | IC、Rank IC、ICIR、分组、中性化、换手、衰减、回测与OOS |
| 策略与组合 | 10 | 等权、倒波动、风险贡献、成本、滑点、容量与ETF穿透 |
| 风险与复盘 | 10 | 波动、回撤、VaR/ES、集中度、压力、结算、审核与复现 |

中文解释由Finathink整理，51条代码/工作流引用绑定已审核的固定来源版本。示例验证计算定义；经验性因子假设显示“来源中的研究假设”，用户数据上的结果仍由实际研究证据决定。EMA初始化、MACD柱值倍数、ATR平滑、财务比率分母与分组价差归一化等差异均在相关词条和来源说明中列出。

## 公共读取接口

| 接口 | 输出 |
| --- | --- |
| `GET /api/knowledge/search?q=回撤&domain=risk-review` | 结果、命中字段、来源、分页与目录指纹 |
| `GET /api/knowledge/items/drawdown` | 完整规范词条、公式、例子、误区与旧目录映射 |
| `GET /api/knowledge/paths` | 12条新路径与4条原课程 |
| `GET /api/knowledge/preparation?item=drawdown` | 知识版本、来源、所需字段、方法与待绑定数据状态 |
| `GET /api/knowledge/export` | 公开内容及来源的JSON导出 |

筛选参数为 `domain`、`kind`、`claim_kind`、`source_id`，其中 `source_id` 对应具体文件行范围的引用ID。关键词最多200字；页面每页16项。原 `/api/knowledge`、`/api/concepts/{id}`、`/api/knowledge/path/{id}` 和 `/api/p8_2b/knowledge/...` 保留已有合同；新增公共导出不会合并私人笔记、掌握度、API凭证或研究报告。

## 数据与验证方式

内容事实源为打包的 `knowledge/catalog.json`、`examples.json` 与 `paths.json`，经过逐文件许可及哈希校验。SQLite FTS5只是可重建的公开投影，使用中文二元分词和固定字段权重。持久缓存位于用户数据库旁的 `.knowledge-cache/`，与私人学习数据库分开；内存模式不落盘。Python的SQLite若缺少FTS5，页面会给出安装诊断。

数值示例仅运行固定运算清单，不执行文本公式或第三方代码。所有预期值附手算过程；来源摘要可用已下载仓库中的固定Git对象再次核对。

```sh
python3 scripts/validate_quant_knowledge.py
python3 scripts/evaluate_knowledge_search.py --split all
python3 -m pytest tests/research/test_knowledge_index.py tests/research/test_quant_knowledge_content.py tests/research/test_knowledge_workspace.py -q
```

60道题在实现前冻结。首次完整验收Top-5命中58/60（96.67%），各域最低85.71%。开发集39/40；保留集19/20，保留集技术价量子域2/3。后者作为小样本诊断保留，未据此改词条、权重或题集；完整60题是本阶段既定门禁。后续优化应增加独立的新测试集。证据和发布状态见[阶段验收记录](reviews/2026-10-31-finathink-phase22-review.md)。
