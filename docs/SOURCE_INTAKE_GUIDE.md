# 开源来源接入与资料核验

在工作台打开“研究方法”，选择“开源来源”，或直接访问本地 `/library/sources`。这里可以检索七个项目，查看固定提交、许可记录、重点文件数量及每个模块的迁移阶段。展开详情可访问原来源和署名材料；查看这些记录不会提交研究任务。

## 当前内容

- 七个项目：QUANTAXIS、DeepSeek HARNESS Quant、Qlib、Alphalens Reloaded、Pyanomaly、RD-Agent、bt。
- 163项模块去向，其中QUANTAXIS为45项、DeepSeek为28项；其余项目90项。去向是适配规划，尚未实施的模块显示待验证或研究参考。
- 原规划锁定的62项文件与许可均已核对固定提交及内容摘要。另对17个DeepSeek对账路径进行了摘要检查，其中有与原清单重叠的路径，不相加宣称79个独立材料。
- 旧52条资源的ID、内容指纹与状态保持不变；31项已打包、15项仅上游、6项缺失都有明确去向。新增许可证、署名、来源记录、对账记录及一份Alphalens只读源码后，目录合计62条资源，其中41项已打包。
- 8个原定义仍保留待补状态。发现同名或简化实现时保留区别，不将它们自动等同原效果主张。

## 如何再次核验下载目录

源码下载目录按来源ID分别放置七个Git工作树。仓库根目录运行：

```sh
python3 scripts/audit_quant_sources.py \
  --reference-root /path/to/source-checkouts \
  --output ./source-audit.json
```

命令只读取固定参数的Git元数据和有界文件内容，不导入上游Python模块、不执行文档指令、不连接数据服务。输出包含提交、模块、摘要、适用许可文件与错误；全部通过返回退出码0。核验范围是提交中的文件清单和锁定材料，不是对整套上游引擎的功能审计。

提交、仓库身份、文件哈希、模块去向、许可材料或安全路径不匹配时返回BLOCKED。工作树中的符号链接不能用于读取树外文件。新增资料要先补固定提交和文件摘要，并更新模块/权利记录，再执行核验；不能直接修改旧材料来覆盖历史版本。

## 许可与来源如何进入产品

`SourceSnapshot`和`audit_source`负责来源完整性；`plan_asset_import`只生成逐文件导入提案。没有文件审核记录、嵌套许可未审、内容/提交变化或许可冲突时，条目进入待补或阻断状态。来源文档中的命令与模型提示保持普通文本身份。

安装目录使用`research-assets.v2`清单：每个打包资源绑定审核记录、内容摘要、来源和许可/通知摘要；只有对应字节的分发审核通过才可读取。依赖的许可或署名审核被撤回也会阻断读取。旧v1目录保持原MIT读取合同。

Alphalens的`performance.py`以`.py.txt`形式作为只读参考打包，未作为运行依赖安装或执行。原版权头、完整Apache-2.0许可证均保留。固定上游树没有NOTICE文件，因此另附明确标为Finathink编写的署名说明。DeepSeek的MIT全文与研究用途附注完整保留。

来源完整性、资料分发权利、算法实现、财务效果与策略使用审核是不同事实。来源页显示固定版本的核验记录；后续研究沿用现有因子验证、范围审核和纸面结果流程。

## 维护资料

- [模块与去向清单](research/quant-source-module-map.json)
- [七项目来源审计结果](research/quant-source-audit.json)
- [逐文件适配提案](research/quant-source-rights-plans.json)
- [旧资源与待补定义对账](research/quant-resource-reconciliation.json)
- [回归台账](REGRESSION.md)
- [Phase21验收记录](reviews/2026-10-31-finathink-phase21-review.md)
