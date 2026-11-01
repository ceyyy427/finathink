# Phase25 独立计算与隔离参考对照

实际执行日期：2026-11-01。生产运行依赖继续使用现有 numpy/pandas。独立预期使用 Python 标准库标量算术、平均并列秩、样本标准差和逐腿费用计算；预期不调用被测评分函数。

## 生产评分的独立对照

`python3 scripts/run_factor_reference_checks.py` 实跑13项：平均Rank IC、ICIR、最高/最低分组收益、含费最高组收益、分组收益差、单边换手、交易费用、覆盖率、单调性、3观察期衰减IC、参与率容量、Holm校正。容差均为绝对误差1e-12。13项全部通过；固定小样例为8资产、20观察点，验证区间[4,18)，1观察期滞后信号及完整成熟标签。

测试保留了47项证据/数值/既有Phase20学习检查。首次证据合同测试有9项行为缺失失败，日志保存在阶段专属本地证据目录；修复后同一证据入口通过。反例库实跑22种场景，包括方向不符、成本击穿、覆盖/并列/零方差、可交易性、容量、重复信号、坏价格/成本、训练重叠、时区、未登记试验、未来数据更改、晚披露和退市缺口。错误与缺数据独立于经济假设结果。

## 锁定 Alphalens 隔离环境

安装命令（已执行）：

```sh
python3 -m venv /tmp/finathink-phase25-alphalens
/tmp/finathink-phase25-alphalens/bin/python -m pip install /Users/mac/.codex/references/finathink-quant-knowledge-20261031/alphalens-reloaded
python3 scripts/run_factor_reference_checks.py --alphalens-python /tmp/finathink-phase25-alphalens/bin/python
```

参考仓库：`stefan-jansen/alphalens-reloaded`，提交 `60755d1cc7d9d36a4c70c8344cbdb168fe99df24`。实际环境版本：Alphalens 0.5.1、numpy 2.5.4、pandas 3.1.0、scipy 1.18.1、statsmodels 0.15.0。完整环境冻结清单在本地阶段证据目录；该环境不加入产品默认依赖。

| 固定文件 | SHA-256 |
| --- | --- |
| `src/alphalens/performance.py` | `dc4c7c661b19a61b434f67cd5015a986f5aea85fd319e08a3d7fb04469203d25` |
| `src/alphalens/utils.py` | `093e9eb62d2c9cd7d4cf93ffb64998064ec7c67092f496e3b52f118187ed33da` |
| `LICENSE` | `2b651c2d29c644d1c73417cd96e6cfa506d6de102ed96fc3de96d676e089cf29` |
| `pyproject.toml` | `b98934a9959531b17f303afa994e4c86a4db90aaef1ad39d0f3546ca6baa0ab7` |

原 Apache-2.0 许可和本项目明确署名通知已经在资源 `alphalens-reloaded-license`、`alphalens-attribution` 中保存。此次仅隔离执行和数值比较，没有将上游实现复制为新的运行代码。

隔离环境实际跑通12项统计对照：逐日Rank IC、平均IC、ICIR、组内IC、组内去均值IC、逐日分组收益、分组标准误、平均分组收益、半杠杆多空价差、分组成员换手、秩自相关、累乘收益。每项与独立标量预期核对，误差容限1e-12，全部通过。

共同口径直接与本项目生产评分核对；标为 `REFERENCE_ONLY` 的组内去均值IC、标准误、秩自相关、累乘收益只用于理解参考实现，不能宣称是本阶段新增产品功能。该固定提交的Alphalens多空价差采用0.5倍最高减最低收益，本项目保留原始差额；对照显式换算。Alphalens成员换手排除首次建仓，本项目单边换手包含现金项，费用按每条资产买卖腿计算。两者不强行混为一个口径。

实际机器可读结果见 [独立与隔离对照结果](phase25-reference-results.json)。SAMPLE只提供工程复现，来源及合成研究结果不转换为金融用途批准或已结算学习样本。
