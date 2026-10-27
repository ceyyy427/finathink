# 快速开始：从安装到第一份研究报告

准备 Python 3.11 或以上版本，下载并解压仓库，在项目文件夹打开终端。首次体验使用明确标记的 SAMPLE 数据，研究过程不需要模型 API Key。

## 安装与启动

macOS / Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install .
finathink --offline --db .local-state/app.sqlite3 --port 8765
```

Windows PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\finathink.exe --offline --db .local-state/app.sqlite3 --port 8765
```

保持终端窗口运行，打开 [研究任务](http://127.0.0.1:8765/research/jobs)。`.local-state` 保存本地数据库、连接引用、快照与研究档案，已在仓库忽略规则中排除。再次启动时使用相同 `--db` 路径，即可继续查看历史。关闭应用可在终端按 `Ctrl+C`。

## 生成第一份离线报告

1. 在“准备数据”展开“使用明确标记的演示数据”，点击“生成 SAMPLE 快照”。进入页面不会自动生成或替换数据。
2. 在“提交研究”选择“基础复核”模板，核对快照、标的与截止日，保留“本地确定性流程”。模板只填写研究问题，不切换策略算法。
3. 点击“提交研究任务”，在“运行与结果”查看阶段、服务器耗时和执行时限。
4. 完成后打开“完整 HTML 报告”，用浏览器的“保存网页”保存文件。报告自包含，离线打开即可阅读。
5. 刷新页面，或关闭并重新启动应用；从后台任务列表继续打开原报告。列表每页显示 20 项。

演示结果展示研究方法与完整性校验，不是市场收益证明。指标与结论以所选快照、成本、观察条数、PIT 与样本外条件为准。

## 接入自己的数据

打开 [数据接口](http://127.0.0.1:8765/settings/data-connections)，填写连接标识、显示名称、HTTPS 地址、认证方式、记录路径与字段映射。

“离线执行配置”指研究不调用模型；点击获取自有 API 数据，仍会按你的明确操作发起数据请求。

- 认证可选择无需认证、Bearer 或 API Key 请求头。密钥在本地系统凭证存储中保存，页面不回显；配置文件只保存引用。
- 字段映射左侧是平台字段，右侧填你的接口字段路径。日频研究需要 `instrument`、`timestamp`、`open`、`high`、`low`、`close`、`volume` 和 `available_at`。支持简单 JSON 记录路径与分页；时间语义、日历与复权信息应随数据声明。
- 保存后返回研究任务页，选择接口、标的、起止日期与截止日，明确点击“获取并保存快照”。保存配置不请求数据。
- 新快照显示用户数据标记与指纹。选择它提交研究，原有 SAMPLE 快照和历史报告仍独立保留。

接口地址、数据权限、购买账号与来源选择由用户自己的服务决定。平台按通用 API 合同处理，不要求使用某家数据供应商。系统凭证存储当前有 macOS Keychain 实现；其他平台的凭证后端需单独配置与验证。真实账户验收以你自己的服务为准。

## 按需使用模型分析

离线任务可直接完成确定性研究。需要基本面、新闻、情绪或学习材料分析时，先阅读 [模型研究指南](PHASE6_MODEL_RESEARCH_GUIDE.md)，在 [模型接口](http://127.0.0.1:8765/settings/providers) 保存接口与凭证引用。

允许网络模型调用时，以同一数据库路径重新启动并显式启用：

```bash
FINAHINKING_OFFLINE=0 finathink --db .local-state/app.sqlite3 --port 8765
```

PowerShell 中先执行 `$env:FINAHINKING_OFFLINE="0"`，再使用上述启动命令；完成后移除此变量或使用 `--offline`。

回到研究任务页，选择已配置模型，提供自己有权使用的材料摘要，阅读并勾选本轮发送内容与费用授权后提交。角色观察保留证据引用，数值回测与风险判断仍由确定性引擎计算。用量未确认时显示“未知”，不是零费用。保存模型配置不等于外部连接验收。

## 遇到异常时

| 页面提示 | 接下来做什么 |
| --- | --- |
| 尚无快照 | 显式生成演示快照，或配置接口后获取自己的数据。 |
| 无法连接本地服务 | 检查启动终端和端口，重新打开本地地址，再点“重新读取记录”。 |
| 响应的数据格式异常 | 保留表单，重新读取；先确认原任务是否已创建，再决定是否新建，避免重复提交。 |
| 输入校验失败 | 核对字段映射、标的、日期、数据可用时间与预算；问题中不要粘贴凭证。 |
| 报告完整性异常 | 保留原始历史与备份；恢复原报告包，或新建研究任务，不把损坏文件当成有效报告。 |
| 模型或数据调用失败 | 在自己的服务侧检查权限、额度及网络；先用离线流程确认本地安装。 |

可在 [诊断与恢复](http://127.0.0.1:8765/diagnostics) 下载脱敏诊断。诊断包含本地模式、结构状态与有界任务摘要，不含数据行、完整问题、接口地址、私人文件名或凭证值。分享前请再自行检查。

## 升级、备份和恢复

先等待或取消所有后台任务，再关闭应用。保持虚拟环境激活，使用明确的数据库路径与新的备份文件名：

```bash
finathink-state backup --db .local-state/app.sqlite3 --output finathink-private-backup.zip
python3 -m pip install --upgrade .
finathink --offline --db .local-state/app.sqlite3 --port 8765
```

升级使用现有的幂等、增量 SQLite 迁移，保留原有记录。备份范围是指定的主数据库、相邻数据/模型引用配置及同级 `artifacts`，不是整个项目文件夹；不会读取 Keychain 或环境变量中的密钥值。备份包含你的数据与研究材料，应作为私密文件保管。

恢复到一个尚不存在的新目录：

```bash
finathink-state restore --archive finathink-private-backup.zip --to recovered-state
finathink --offline --db recovered-state/app.sqlite3 --port 8765
```

恢复先校验文件清单、指纹与 SQLite 完整性，不覆盖原目录。打开研究时继续校验报告 manifest。其他机器上的凭证需要重新配置；不要把私密备份当成脱敏诊断上传。维护工具当前上限为 20,000 个文件、总计 1 GiB、单文件 64 MiB；有未终结任务、链接、损坏包或同名输出时拒绝操作。它是关闭应用后的维护工具，不是在线热备服务。

安装与完整旅程门禁见 [发布清单](RESEARCH_RUNTIME_RELEASE_CHECKLIST.md)，小范围试用与反馈步骤见 [试用指南](PHASE9_USER_TRIAL_GUIDE.md)。
