# Phase 26 固定来源核对

2026-11-03 从既有参考库读取固定 Git 对象，逐文件核对 SHA-256 与许可。策略与风险规则使用项目内原创实现，来源用于说明研究设计。

| 项目 | 固定提交 | 文件 | SHA-256 | 许可和摘要 | 研究用途 |
| --- | --- | --- | --- | --- | --- |
| https://github.com/yutiansut/QUANTAXIS | `232aa7ac9869966108f02a8fff85f1f7b09a9ce2` | `QUANTAXIS/QIFI/QifiAccount.py` | `8b1cae0450d7c3cf9fb4f112191724096f09c84fd6d9c11662d2f13020166a9d` | MIT `LICENSE` `96ebf515d25a58bbd8be4543a5c198e576db52738f26c82270ececb1dc273318` | 订单、成交、持仓与现金状态的分离。 |
| https://github.com/yuanwang589-dev/deepseek-harness-quant | `9f72ab7e64799aecfc79cf3b913359acea1dd9b9` | `strategy/pool_layers.py` | `888dc95a8a8b9053a09d7253353730775ddd063beeae5a02a63ade6aa1f57a75` | MIT_WITH_RESEARCH_NOTE `LICENSE` `423beebd76cc3b1b060c5ed5992d961fa282263c7f93aa01ae25d7f9a41072b4` | 分层记录与候选筛选。 |
| https://github.com/yuanwang589-dev/deepseek-harness-quant | `9f72ab7e64799aecfc79cf3b913359acea1dd9b9` | `risk/beneish_full.py` | `9f1d4f71d02cdb3a45061909955cc266ab8d4e75eb33deac8dd56927be65c0cc` | MIT_WITH_RESEARCH_NOTE `LICENSE` `423beebd76cc3b1b060c5ed5992d961fa282263c7f93aa01ae25d7f9a41072b4` | 财务异常作为核查提示。 |
| https://github.com/yuanwang589-dev/deepseek-harness-quant | `9f72ab7e64799aecfc79cf3b913359acea1dd9b9` | `risk/data_audit.py` | `e9952017cdfac9aea433b07a0d433fb6ce06d2316f8bd98546adb017b441f7bc` | MIT_WITH_RESEARCH_NOTE `LICENSE` `423beebd76cc3b1b060c5ed5992d961fa282263c7f93aa01ae25d7f9a41072b4` | 数据可用性和诊断。 |
| https://github.com/yuanwang589-dev/deepseek-harness-quant | `9f72ab7e64799aecfc79cf3b913359acea1dd9b9` | `etf/etf_map.py` | `cacefdb73a9b88e733c894a01aa1c4aa0563d53377a9a1f667b2f1d300910f1a` | MIT_WITH_RESEARCH_NOTE `LICENSE` `423beebd76cc3b1b060c5ed5992d961fa282263c7f93aa01ae25d7f9a41072b4` | ETF 与策略收益在共同窗口比较。 |
| https://github.com/chulwoohan/pyanomaly | `59758c913a4146bbfb9d3c056668ccdc5231c8c8` | `pyanomaly/characteristics.py` | `c9ad1d14b61fae774d1a7446609971225fb0a709f0e0da7dc998d7aead32bc20` | MIT `LICENSE.txt` `84dc4ddcfd08d382bbe4ac775ebee4da7e40280b8aba4f841884f0234303f6f4` | 价值与质量特征的字段定义。 |
| https://github.com/pmorissette/bt | `4ae4090266300c0185f418aca18e28e649509a33` | `bt/algos.py` | `81b79443e7dc36356293a22bf4c237a76267d77bcfbaeb90e3a5df374425f2ee` | MIT `LICENSE` `8711471566d722d55eca3f09196f342b2ee2960616e68127ec2a17b39ff99949` | 将选取、权重、触发时间与再平衡组合成受控步骤。 |
