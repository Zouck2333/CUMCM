# C题第二问程序说明

本目录将 `docs/model_Two_calibrated.md` 的滚动预测、净负荷风险校准和日前购电—储能联合MILP转化为可重复运行的程序。当前默认采用28天负荷星期效应与二次趋势、14天光伏趋势、28天全部历史残差与前后30分钟合并、85%购电分位数及75%日末储备。程序严格按日期前推，1月预热，正式结果覆盖2025年2月1日至12月31日。

目录已按用途整理：根目录放运行入口，`code` 放全部程序，`docs` 放模型与审查资料，`output` 放当前正式结果，`archive` 放历史版本。每个文件的作用和使用状态见 [`FILE_INDEX.md`](FILE_INDEX.md)。

```text
Question_Two/
├─ README.md、FILE_INDEX.md、requirements.txt、run_question_two.ps1
├─ code/       程序文件
├─ docs/       模型与审查文档
├─ output/     当前正式结果
├─ archive/    历史结果与旧缓存
└─ node_modules/  工作簿生成所需的运行依赖联接
```

## 主模型口径

- 功率乘以 \(1/6\) 小时后转为每10分钟电量，统一使用kWh。
- 负荷逐时段拟合最近28天的对数值，包含六个星期效应和二次时间趋势，不预设周末低负荷；光伏拟合最近14天的水平与线性趋势。
- 1月保留原相似日预测及情景调度，正式期改用 `calendar_trend`；只有显式选择 `similar_day` 时才使用旧正式期月度K/ρ调参。
- 风险样本合并过去28天所有日期的实际预测残差，排除1月1日，不使用当天或未来实测值。模型结构和固定参数在2025年样本上开发，年度回测不等于未知年份独立验证。
- 正式主方案使用0.75储备分位数、NumPy `linear` 方法、逐时正净负荷误差累积安全储备，以及 \(\eta_c=\eta_d=0.90\)。购电风险分位数另为0.85，采用 `inverted_cdf` 方法。
- 正式期的供能下限为点预测净负荷加历史风险余量，联合优化购电和电池计划。允许未利用的已付费计划电量，不再将预测总富余上限限定为预测光伏量。正式期目标为满足风险下限的计划费用最小；原随机情景目标只用于1月预热或显式选择 `legacy_scenarios` 时。
- 每日正式求解依次最小化主费用、储能吞吐量和正常购电峰值。后两级受前级最优值加数值容差约束，不会实质改变第一阶段费用最优性。
- 词典序锁定默认使用严格容差；若HiGHS仅以 `status=2` 报告数值不可行，程序只对该日按预设上限放宽容差并重试，同时记录重试标志。
- 日前正常购电、充电和放电计划在读取当天实际负载及光伏前确定，并在实际结算阶段固定不变；预测不足由5倍电价的紧急购电补足。

## 时间口径

附件1的时刻是10分钟区间的右端点：`00:10` 对应物理区间 `0:00-0:10`，第24项对应 `3:50-4:00`，`0:00+1` 对应 `23:50-24:00`。因此每连续24项恰好构成一个四小时区间。

题目给定模板的计划购电量标题比物理序列晚10分钟。生成程序沿用模板的工作表和单元格结构，同时把144个标题修正为 `0:00-0:10` 至 `23:50-0:00+1`。逐时CSV、紧急购电区间和核验程序均采用同一物理标签。

## 文件

- `docs/model_Two_calibrated.md`：当前第二问数学模型及信息边界；`docs/model_Two.md`保留历史模型。
- `code/test_risk_calibration.py`：风险余量、日界及零光伏风险购电回归检查。
- `code/forecast_calendar.py`、`code/test_calendar_forecast.py`：历史星期效应和趋势预测，以及时间边界与星期规律检查。
- `docs/model_code_consistency_review.md`：模型与代码一致性审查及修正记录。
- `code/extract_inputs.py`：只读附件1、附件2和结果模板，检查右端点时序并把功率换算为电量。
- `code/solve_question_two.py`：滚动预测、月度参数选择、残差情景、安全储备、每日三级词典序MILP、实际结算和SOC更新。
- `code/build_result2.mjs`：按物理时段顺序写入并修正题目模板的时段标题。
- `code/verify_question_two.py`：对输入、结构化结果和最终工作簿执行独立核验。
- `code/test_information_boundary.py`：扰动目标日实际数据，检查日前计划的信息边界。
- `code/generate_paper_tables.py`：自动提取题目指定四天的论文表格。
- `code/run_model_comparisons.py`：批量比较安全储备分位数、储备算法和效率口径。
- `code/assemble_comparison_report.py`：合并并行或断点完成的分组对照结果。
- `code/perfect_information_benchmark.py`：计算匹配约束的完美信息MILP与放松后的LP下界。
- `code/build_result_summary.py`：汇总主结果、指定日期、灵敏度和完美信息基准。
- `code/verify_analysis_outputs.py`：核对五组对照、基准物理检查、成本序关系和输入文件哈希。
- `run_question_two.ps1`：主流程的一键运行入口。

## 运行环境

Python需要 `numpy`、`scipy` 和 `openpyxl`，结果工作簿由 `@oai/artifact-tool` 生成。可先安装Python依赖：

```powershell
pip install -r .\Question_Two\requirements.txt
```

## 正式运行

在项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\Question_Two\run_question_two.ps1
```

默认一键流程依次执行输入提取、全年滚动主求解、结果工作簿生成、独立核验、四个指定日期论文表、信息边界扰动测试、五组灵敏度对照，以及两个完美信息基准。任何计算或核验失败都会终止流程；若Node只在工作簿保存后的退出阶段异常，流程会继续执行独立核验，并仅在工作簿逐项通过后接受结果。

可调整MILP相对间隙和每日求解时间上限：

```powershell
powershell -ExecutionPolicy Bypass -File .\Question_Two\run_question_two.ps1 -MipGap 1e-5 -TimeLimit 60
```

调试时可只求解前若干天。少于365天时不会覆盖正式 `result2.xlsx`：

```powershell
powershell -ExecutionPolicy Bypass -File .\Question_Two\run_question_two.ps1 -MaxDays 32
```

正式主方案默认采用 `lexicographic` 目标模式。一键脚本可直接配置：

```powershell
powershell -ExecutionPolicy Bypass -File .\Question_Two\run_question_two.ps1 `
  -EtaCharge 0.9 -EtaDischarge 0.9 `
  -ReserveQuantile 0.75 -ReserveMode positive_steps `
  -PurchaseStrategy calibrated_quantile -PurchaseQuantile 0.85 `
  -ForecastMethod calendar_trend -LoadWindowDays 28 -LoadTrendDegree 2 `
  -PvWindowDays 14 -RiskGrouping all `
  -RiskWindowDays 28 -RiskRadiusPeriods 3 `
  -QuantileMethod linear -ObjectiveMode lexicographic
```

如只需重新生成主结果和核验文件，可跳过三个耗时或独立的分析步骤：

```powershell
powershell -ExecutionPolicy Bypass -File .\Question_Two\run_question_two.ps1 `
  -SkipBoundaryTest -SkipComparisons -SkipBenchmarks
```

`code/solve_question_two.py` 对应提供以下命令行参数，便于脱离一键脚本单独实验：

```text
--eta-charge FLOAT
--eta-discharge FLOAT
--reserve-quantile FLOAT
--reserve-mode positive_steps|cumulative_net
--quantile-method linear|higher|lower|nearest|midpoint
--objective-mode cost|lexicographic
--purchase-strategy legacy_scenarios|calibrated_quantile
--purchase-quantile FLOAT
--risk-window-days INT
--risk-radius-periods INT
--forecast-method similar_day|calendar_trend
--load-window-days INT
--load-trend-degree 1|2
--pv-window-days INT
--risk-grouping all|legacy
```

其中 `positive_steps` 是正式方案的逐时正误差累积储备口径，`cumulative_net` 是允许正负误差在时间上抵消的对照口径。储备参数与供能风险分位数是不同参数。复现本次之前的校准模型使用 `--forecast-method similar_day --risk-grouping legacy --purchase-quantile 0.8`；复现旧随机情景策略另加 `--purchase-strategy legacy_scenarios --reserve-quantile 0.9`。

完美信息基准会从主结果的 diagnostics 自动读取充、放电效率，因此自定义效率的一键运行也会建立相同效率口径的匹配MILP与LP下界。

## 自动核验和论文表格

主流程通过 `code/verify_question_two.py` 复核日期和时段连续性、有限值与非负性、储能功率和互斥、SOC边界/递推/跨日衔接/日末储备、预测与实际供需平衡、紧急购电与富余电量互斥、逐时至全年费用，以及JSON、CSV、Excel之间的映射。存在历史来源及三级目标字段时，还会核验信息边界、情景数、吞吐量、峰值和目标模式。

`code/test_information_boundary.py` 另以2025年2月4日为目标日，扰动该日的实际负载和光伏，核对预测来源、储备、购电、充放电、SOC及三级目标均保持不变，同时确认事后结算结果会随实际数据改变。测试报告写入 `information_boundary_test.json`。

可单独生成四个题目指定日期的论文表格：

```powershell
python .\Question_Two\code\generate_paper_tables.py `
  --solution .\Question_Two\output\question_two_solution.json `
  --detail .\Question_Two\output\question_two_detail.csv `
  --output-dir .\Question_Two\output\paper_tables
```

默认提取2025年3月20日、6月21日、9月23日和12月21日，结果写入 `Question_Two/output/paper_tables`。

## 批量灵敏度分析

主结果生成后运行：

```powershell
python .\Question_Two\code\run_model_comparisons.py `
  --input .\Question_Two\output\input_data.json `
  --output-dir .\Question_Two\output\comparisons
```

脚本依次比较无储备、0.75分位数、0.90分位数、\(\sqrt{0.9}\) 单程效率解释，以及0.90分位数的累计净误差储备。正式对照默认与主模型一样使用三级词典序模式，使各方案的事后结算结果不受第一阶段多重最优解影响；调试时可显式传入 `--objective-mode cost` 缩短运行时间。

## 完美信息基准

滚动主方案完成后运行：

```powershell
python .\Question_Two\code\perfect_information_benchmark.py
```

匹配MILP使用正式评价期的实际负载和光伏，保留主方案2月1日初始SOC、效率、容量、功率、互斥和逐日安全储备；LP下界进一步取消每日储备并放松互斥。脚本核验 `LP下界 ≤ 匹配MILP ≤ 滚动实际费用`，并计算两个相对差距。基准只作事后评价，其结果不会回填滚动计划。

## 输出

主结果保存在 `Question_Two/output`：

- `result2.xlsx`：按题目结构填写的正式结果，覆盖334个正式评价日。
- `question_two_solution.json`：工作簿生成所需的结构化结果、模型参数和汇总诊断。
- `question_two_daily.csv`：365天的预测误差、SOC、场景数、储备、费用、三级目标指标和求解状态，其中1月标记为预热期。
- `question_two_detail.csv`：2月至12月共 \(334\times144\) 行逐时结果。
- `verification_report.json`：自动核验报告。
- `information_boundary_test.json`：目标日实际数据扰动测试报告。
- `previews`：结果工作表的渲染预览图。
- `paper_tables/paper_selected_dates.md`、汇总CSV及逐日明细CSV：题目指定日期的论文表格。
- `comparisons/comparison_results.csv`、`.json`、`.md`：安全储备与效率对照结果。
- `benchmarks/perfect_information_benchmark.json`、`perfect_information_report.md`、逐日CSV和逐时CSV：完美信息基准结果。
- `result_summary.md`：主结果、灵敏度分析、基准与核验的最终汇总。
- `analysis_verification.json`：附加分析的最终一致性核验报告。

修改前的主结果保存在 `Question_Two/archive/output_baseline_pre_fix`，只用于比较公共候选池、月度回退和三级目标修正造成的变化，不作为当前正式结论。

## 结果解释

`result2.xlsx` 是正式提交表；CSV和JSON用于复核、论文统计与复现实验。四个指定日期的表格应由 `code/generate_paper_tables.py` 从当前结构化结果自动提取，避免手工抄录造成版本不一致。
