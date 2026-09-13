# C题第二问程序说明

当前正式模型已重写至 `docs/model_Two.md`。默认程序采用 `causal_monthly`：2月1日只用1月历史数据选择初始参数，之后每个月初只用截至前一天的数据选择参数，月内每天重新拟合预测并联合优化购电和储能。正式结果为 `output/result2.xlsx`。

## 信息边界与模型

- 原始功率转换为10分钟电量，检查365×144维度、连续日期和右端点时刻，不填补或平滑实测值。
- 1月使用预设的相似日预测与情景调度积累历史。正式评价期为2月1日至12月31日，共334天。
- 正式期通过历史验证选择负荷窗口及趋势阶数、光伏窗口、风险残差窗口、时段半径、购电分位数和储备分位数。
- 候选集与评分规则保存于 `tuning_protocol.json`，不按正式期全年结算结果再回选参数。调参函数只接收截至前一天的实际数据数组。
- 当前月参数下的历史候选预测也按日重建，每个历史日只拟合更早数据；它们用于风险校准和候选验证，不改写已经执行的计划。
- 日前购电、充电、放电全部先确定，当天实测只进入事后结算和后续历史库。紧急费用按正常电价的5倍计。
- 单程充、放电效率均为0.9，功率上限5000kW，SOC为1200—10800kWh，1月1日初始SOC为6000kWh。富余无收益，计划购电全额付费。
- 新流程的参数选择遵守历史截止；由于候选模型设计受到此前研究启发，本次2025年回算不是外部未知数据的独立验证。

## 运行

在项目根目录执行完整流程：

```powershell
powershell -ExecutionPolicy Bypass -File .\Question_Two\run_question_two.ps1
```

完整流程包括原始输入提取、历史滚动调参和全年求解、工作簿生成、独立核验、指定日期表格、2月1日及6月1日的选参扰动测试、完美信息基准与报告一致性核验。固定参数版本的五组灵敏度结果不参与当前参数选择。

查看首个调参节点的调试运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\Question_Two\run_question_two.ps1 -MaxDays 32
```

调试文件写入 `output/debug_32`，不覆盖正式结果。仅需重新求解与生成工作簿时，可以使用 `-SkipBoundaryTest -SkipBenchmarks`，但这不会生成一套新的完整核验汇总。

历史滚动模式可以配置 `-EmergencyBudgetYuan 1000000`、求解容差和时间上限。购电分位数、负荷窗口、储备分位数等固定参数选项只对 `-ParameterMode fixed` 生效；历史模式由候选集和过去数据选择这些参数。

复现此前固定参数开发回测时，显式使用：

```powershell
powershell -ExecutionPolicy Bypass -File .\Question_Two\run_question_two.ps1 -ParameterMode fixed
```

此命令会替换正式输出，原开发回测与旧说明保存在 `archive`。不要把固定参数开发回测误认为历史滚动调参结果。

## 程序与输出

| 文件 | 作用 |
|---|---|
| `code/solve_question_two.py` | 逐日历史信息管理、调用调参、联合MILP和实际结算 |
| `code/causal_policy.py` | 固定候选集、历史预测评分、风险预算评分和连续SOC储备验证 |
| `code/forecast_calendar.py` | 星期效应和局部趋势预测 |
| `code/verify_causal_policy.py` | 独立重算候选分数、参数选择、历史来源与验证调度 |
| `code/verify_question_two.py` | 输入、正式物理约束、费用及JSON/CSV/Excel映射核验 |
| `code/test_information_boundary.py` | 扰动当天及未来数据，重新调参并比较全部候选记录与当天计划 |
| `code/build_result2.mjs` | 通过artifact-tool填写原结果模板并生成预览 |
| `code/build_causal_summary.py` | 历史滚动选参报告、结果摘要和版本对照 |

正式结果位于 `output`：`result2.xlsx`、`question_two_solution.json`、`question_two_daily.csv`、`question_two_detail.csv`。完整的月度候选评分与储备验证调度保存在JSON的 `policy_updates` 中。

优先阅读 `result_summary.md`、`causal_tuning_report.md`、`policy_updates_summary.csv`。`verification_report.json` 和 `analysis_verification.json` 应为PASS，两份 `information_boundary_test*.json` 应为passed=true。`processing_manifest.json` 保存原始附件、结果、代码与模型正文的哈希。

Python求解需要NumPy与SciPy；只读Excel核验需要openpyxl。工作簿由 `@oai/artifact-tool` 生成。运行入口自动定位本机的求解Python、捆绑Python和Node环境。依赖联接 `node_modules` 只用于工作簿生成，不应修改其内容。
