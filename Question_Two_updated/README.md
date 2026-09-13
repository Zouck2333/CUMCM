# 第二问更新版完整文件包

本目录单独保存当前正式版本：仅用1月历史数据初始化，之后在每个月初使用截至前一天的数据滚动调参，月内逐日更新预测并求解。原始附件、模型、代码、正式结果和核验材料均已放入本目录，运行不依赖原来的 `Question_Two` 或其备份目录。

本目录 `Question_Two_updated` 保存正式更新版。旧版工作目录已另行清理；`reference`、完美信息基准、论文表格和全部正式结果均保留。目录因 Windows 占用暂保留原名。

## 主要文件

| 路径 | 内容 |
|---|---|
| `docs/model_Two.md` | 更新后的完整模型正文，已核对当前代码，并附模型与代码对应表 |
| `docs/题目原文.pdf` | 题目原文 |
| `docs/运行环境.md` | Python、Node及工作簿工具的依赖说明 |
| `code/` | 当前求解、历史调参、数据提取、工作簿生成和核验所需程序 |
| `data/` | 附件1、附件2和空白结果模板 |
| `output/result2.xlsx` | 正式提交结果 |
| `output/question_two_solution.json` | 全部计划、预测历史、11次选参的候选评分和验证调度 |
| `output/question_two_daily.csv`、`output/question_two_detail.csv` | 365天记录和正式期48,096个时段明细 |
| `output/result_summary.md`、`output/causal_tuning_report.md` | 费用汇总和历史信息边界说明 |
| `output/verification_report.json`、`output/analysis_verification.json` | 结果独立核验和分析核验 |
| `output/information_boundary_test*.json` | 2月1日和6月1日重新选参的未来数据扰动测试 |
| `output/paper_tables/`、`output/previews/` | 指定日期论文表和工作簿预览 |
| `output/paper_tables/第二问_指定日期表1表2表3.docx` | 按题目图片格式排版的可编辑Word表格，共5页：四天各一组表1、表2，以及四天并排的表3 |
| `output/benchmarks/` | 只供事后评价的完美信息MILP及LP下界 |
| `reference/previous_fixed_summary.json` | 历史费用对照所需的少量统计和来源哈希，只供报告使用 |
| `checks/` | 本次目录整理后的核验与32天入口复现记录 |
| `PACKAGE_MANIFEST.json` | 整理完成时的相对路径、文件大小和SHA256清单 |

正式评价期为2025年2月1日至12月31日，共334天。保存的总费用为 **14,705,987.34元**，紧急费用为 **926,155.25元**。本次为文件整理及路径适配，未重新优化全年参数或改变正式计划。

## 运行方法

在本目录打开PowerShell，执行完整流程：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_question_two.ps1
```

先检查1月预热和2月1日首次选参：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_question_two.ps1 -MaxDays 32
```

小样本写入 `output/debug_32/`，正式结果写入 `output/`。完整运行会重新生成正式输出，包含两次信息边界测试及离线基准。入口固定使用 `causal_monthly`。需要调整紧急费用预算时使用 `-EmergencyBudgetYuan`；模型候选集见 `code/causal_policy.py` 和 `output/tuning_protocol.json`。

只检查文件包是否与整理完成时一致：

```powershell
python .\code\verify_package.py
```

重新运行程序后，结果或核验报告可能更新，届时文件包快照哈希会变化；这不等同于数学核验失败。数学正确性以本次运行新生成的 `verification_report.json` 等报告为准。

## 整理边界

本目录保留最新版运行所需文件，未复制旧程序备份、旧模型文档及历史筛选实验。核心求解器仍保留复用函数及历史模式分支，其中相似日情景逻辑用于1月预热；不能直接删除这些函数。新入口和汇总工具面向当前历史滚动模式。

已有边界测试和离线基准文件保留原始计算记录，其中部分绝对路径是当时的来源记录，运行时不读取这些旧路径；对应数据通过哈希核对。旧版工作目录已移除，当前处理清单使用包内相对路径。

依赖库不随文件包复制。当前电脑可使用已有Python和Codex运行环境；迁移到其他电脑时，需要配置 `docs/运行环境.md` 所列依赖，尤其是生成Excel所需的 `@oai/artifact-tool`。
