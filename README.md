# Finathink

## 本地优先的金融研究与量化策略工作台

Finathink 将用户提供的数据、证据、因子、量化检验、风险检查、纸面组合和学习记录组织成一条可追溯的研究流程。运行结果保存为结构化记录和可离线打开的 HTML 报告，适合个人研究、量化学习、策略复盘和多代理协作。

## 研究流程

```text
用户数据连接 → 数据标准化与点时检查
    → 五类分析师并行 → 研究经理汇总证据
    → 受控因子提案 → 回测与 OOS 检验 → 指标、换手、衰减与排名
    → 确定性风险检查 → 纸面组合 → 成本与滑点模拟
    → 纸面决策卡 → HTML 报告 → as-of 学习提案
```

每个阶段都有稳定的运行 ID、输入与输出指纹、角色、状态、证据引用和 checkpoint。相同的 fixture 输入可以重放并得到稳定指纹。

## 可以做什么

金融事件可以进入证据链、知识理解和量化研究；策略想法可以进入因子、回测、OOS、风控和纸面组合流程。

### 用户数据接入

- 保存用户自己的数据 API 连接配置和字段映射。
- 通过显式试连取得 JSON 数据，标准化为 `DataBatch`。
- 记录数据指纹、字段质量、来源说明和 point-in-time 状态。
- 使用环境变量或系统钥匙串引用保存凭证，凭证值不进入研究记录。

### 后台研究任务

- 在“研究任务”页显式获取并保存低频 OHLCV 快照，或选择已保存的快照。
- 通过独立 supervisor 与 spawn worker 运行两观察期动量基线、含费用回测和同信号纸面风险检查。
- 跟踪阶段进度、取消任务，并在刷新页面或重启服务后继续查看。
- 按数据与策略指纹保存已校验的报告；历史记录、Overview 和研究对比读取同一本地库存。
- 本地离线角色流程可直接运行，研究问题与资源预算随任务留档。

### 多代理研究

- 并行运行基本面、技术、情绪、新闻和学习分析师。
- 由 `ResearchManager` 汇总带证据引用的观察，形成研究计划。
- 每个角色都经过统一的任务、能力、状态和错误合同。
- 可使用离线驱动、用户配置的兼容模型接口和 Codex handoff 合同。

### 因子与量化

- 将自然语言假设映射到固定 DSL 模板和受控因子提案。
- 进行训练、验证、隐藏 OOS、冻结和一次性测试。
- 计算 IC、ICIR、换手、多周期衰减并按确定性指标排名。
- 记录来源、许可证、PIT 语义、字段依赖、版本和研究指纹，生成待人工准入的提案。
- 使用内部确定性量化网关执行回测和风险指标计算。

### 风险、组合与纸面交易

- 检查 PIT、流动性、集中度、回撤和压力情景。
- 按用户约束生成 long-only 纸面组合和权重。
- 模拟费用、滑点、现金变化和纸面成交台账。
- 风险、组合或量化阶段未通过时，决策卡保持不可执行状态。

### 队列、恢复与学习

- 本地持久化队列支持幂等、租约、重试、退避、取消和崩溃恢复。
- Worker 使用 checkpoint 和资源限制恢复长任务。
- 结算后的纸面事件可以生成因子权重、风险规则和注册表更新提案。
- 学习提案保留 as-of、结算指纹和证据引用，并经过显式准入记录。

### HTML 报告与研究界面

- 生成自包含的多阶段 HTML 报告、manifest 和 activity 记录。
- 报告包含分析师、证据、研究、量化、风险、纸面决策和学习状态。
- 状态墙提供阶段、角色、重试、checkpoint、工具摘要和 manifest 指纹。
- 可比较多次实验的输入、指标和限制，并保留离线无 JavaScript 阅读能力。

## 快速开始

要求 Python 3.11 或更高版本。

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e '.[dev]'
PYTHONPATH=src python3 -m pytest -q
python3 scripts/run_local_app.py --sample --port 8765
```

打开：

- 首页：<http://127.0.0.1:8765/>
- 后台研究任务：<http://127.0.0.1:8765/research/jobs>
- 研究工作区：<http://127.0.0.1:8765/research>
- 因子策略工作台：<http://127.0.0.1:8765/workbench>
- Provider 设置：<http://127.0.0.1:8765/settings/providers>

低频数据准备、任务接口与验收说明见[本地研究闭环指南](docs/PHASE5_LOCAL_RESEARCH_GUIDE.md)。

## 仓库结构

```text
src/finahinking/        Python 核心包、研究编排、量化与报告引擎
frontend/               研究图表、工作台和交互界面
fixtures/               可复现的样例与测试数据
tests/                  单元、集成、契约和产品流程测试
docs/                   架构、研究指南、数据适配器与验证文档
site/                   静态产品入口与前端构建资源
.github/                CI、安全和自动化配置
```

## 研究文档

- [快速开始](docs/QUICKSTART.md)
- [Factor Strategy Workbench 指南](docs/FACTOR_STRATEGY_WORKBENCH_GUIDE.md)
- [Factor Strategy Workbench 验证记录](docs/FACTOR_STRATEGY_WORKBENCH_VALIDATION.md)
- [研究代理指南](docs/RESEARCH_AGENT_GUIDE.md)
- [策略研究指南](docs/STRATEGY_RESEARCH_GUIDE.md)
- [用户数据 API 指南](docs/USER_DATA_API_GUIDE.md)
- [研究运行时指南](docs/RESEARCH_RUNTIME_GUIDE.md)
- [自主研究运行时发布清单](docs/RESEARCH_RUNTIME_RELEASE_CHECKLIST.md)
- [研究能力最终报告](docs/RESEARCH_CAPABILITY_FINAL_REPORT.md)

## 能力边界矩阵

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 本地数据标准化、PIT 与指纹 | `OFFLINE_PASS` | 使用用户配置连接合同和可重放 fixture。 |
| 五类分析师、研究经理与证据引用 | `OFFLINE_PASS` | 只汇总带 evidence refs 的观察。 |
| 因子提案、回测/OOS、风险与纸面组合 | `OFFLINE_PASS` | 确定性、研究用途、人工准入。 |
| 队列、checkpoint、报告与学习提案 | `OFFLINE_PASS` | 本地持久化；学习只生成提案。 |
| 可选 Qlib/vectorbt/QMT 边界 | `ISOLATED_DEFERRED` | 隔离 smoke 或只读合同，不是默认权威引擎。 |
| 真实供应商、模型、浏览器和生产部署 | `EXTERNAL_UNVERIFIED` | 本仓库没有真实凭证或外部验收证据。 |
| 券商下单、实盘交易、秘密写入、任意执行 | `NOT_IN_SCOPE` | 永久产品边界。 |

研究工作台页面为 `/workbench`，其结构化接口为 `/api/research/workbench`，离线报告使用 `workbench/index.html`。

## 开发检查

```bash
python3 scripts/validate_governance.py .
PYTHONPATH=src python3 -m pytest -q
python3 -m ruff check src tests scripts
python3 -m pip check
git diff --check
```
