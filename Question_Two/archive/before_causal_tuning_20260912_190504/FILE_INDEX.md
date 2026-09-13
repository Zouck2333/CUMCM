# Question_Two 文件索引

2026-09-12进一步优化：当前模型见 `docs/model_Two_calibrated.md`，采用28天负荷星期效应与二次趋势、14天光伏趋势、全部日期残差85%分位校准及75%日末储备。`docs/model_Two.md`为历史模型参考。本轮修改前备份位于 `archive/before_target_1500_100_20260912_182709`。当前正式费用与两项目标见 `output/optimization_comparison.md`。

本目录按“入口—程序—文档—正式结果—历史备份”整理。日常运行从 `run_question_two.ps1` 开始；提交和论文引用以 `output` 为准；`archive` 只用于追溯旧版本。

## 根目录入口

| 路径 | 作用 | 状态 |
|---|---|---|
| `README.md` | 模型口径、运行方式、参数及输出说明。 | 使用说明 |
| `FILE_INDEX.md` | 当前目录结构和文件用途索引。 | 使用说明 |
| `run_question_two.ps1` | 一键执行输入提取、全年求解、工作簿生成、核验、灵敏度分析和完美信息基准。 | 推荐入口 |
| `requirements.txt` | Python 依赖版本清单。 | 运行配置 |
| `node_modules/` | `@oai/artifact-tool` 等 Node 运行依赖的目录联接；不要手工修改其中内容。 | 运行依赖，可重建 |

## `code/` 程序

| 文件 | 作用 |
|---|---|
| `extract_inputs.py` | 读取附件和结果模板，检查10分钟时序，将功率换算为电量并生成标准输入。 |
| `solve_question_two.py` | 执行滚动预测、净负荷风险校准、安全储备及每日购电—储能联合MILP，是主求解器。 |
| `test_risk_calibration.py` | 检查残差时间边界、分位数样本和零光伏风险购电。 |
| `forecast_calendar.py` | 仅用过去数据学习每个星期的负荷水平、局部趋势和光伏趋势。 |
| `test_calendar_forecast.py` | 检查非标准周周期、零光伏及未来数据扰动不影响预测。 |
| `research_forecast_targets.py` | 固定电池的候选预测筛选实验；不能替代正式联合求解。 |
| `build_optimization_comparison.py` | 比较当前正式结果与更新前备份，生成费用对照及数据处理哈希清单。 |
| `build_result2.mjs` | 把结构化求解结果写入题目模板，生成 `result2.xlsx` 及预览图。 |
| `verify_question_two.py` | 独立核验输入、物理约束、费用汇总以及 JSON/CSV/Excel 映射。 |
| `test_information_boundary.py` | 扰动目标日实际数据，检查日前决策是否偷看未来信息。 |
| `generate_paper_tables.py` | 从正式结果提取题目指定四天的论文表格。 |
| `run_model_comparisons.py` | 批量计算储备分位数、储备算法和效率口径的灵敏度方案。 |
| `assemble_comparison_report.py` | 合并分组或断点计算得到的灵敏度案例并形成统一报告。 |
| `perfect_information_benchmark.py` | 计算完美信息匹配 MILP 和放松 LP 下界，用于评价滚动方案差距。 |
| `build_result_summary.py` | 汇总正式结果、指定日期、灵敏度和基准结果。 |
| `verify_analysis_outputs.py` | 核验灵敏度与基准输出的完整性、序关系及输入哈希。 |

## `docs/` 模型与审查文档

| 文件 | 作用 |
|---|---|
| `model_Two_calibrated.md` | 当前星期效应趋势预测、风险校准和联合MILP模型。 |
| `model_Two.md` | 历史相似日随机情景模型，仅作旧版本参考。 |
| `model_code_consistency_review.md` | 模型与代码一致性审查及修改来源；主体包含历史审查记录。 |

## `output/` 当前正式结果

这里是当前模型对应的唯一正式结果目录。

| 路径 | 作用 | 状态 |
|---|---|---|
| `result2.xlsx` | 按题目模板填写的正式提交工作簿。 | 核心正式结果 |
| `question_two_solution.json` | 模型参数、结构化结果和汇总诊断，是生成工作簿的主数据源。 | 核心正式结果 |
| `question_two_daily.csv` | 全年逐日预测、SOC、储备、费用和求解状态。 | 核心正式结果 |
| `question_two_detail.csv` | 2—12月每10分钟的购电、充放电、SOC及结算明细。 | 核心正式结果 |
| `verification_report.json` | 主模型物理约束和跨文件映射的自动核验结果。 | 正式核验 |
| `information_boundary_test.json` | 防止使用目标日未来实际数据的信息边界测试结果。 | 正式核验，可再生 |
| `analysis_verification.json` | 灵敏度分析与完美信息基准的最终一致性核验。 | 正式核验，可再生 |
| `result_summary.md` | 主结果、指定日期、灵敏度和基准结论的总览。 | 正式汇总，可再生 |
| `previews/` | `result2.xlsx` 三个工作表的图片预览，用于人工检查版式。 | 展示文件，可再生 |
| `paper_tables/` | 四个指定日期的144时段表、4小时汇总、紧急购电区间及总览。 | 论文派生表，可再生 |
| `comparisons/` | 五种储备/效率方案的灵敏度结果（CSV、JSON、Markdown）。 | 分析结果，可再生 |
| `comparisons/source_cases/` | 各灵敏度方案的中间结构化 JSON，供合并报告使用。 | 中间文件，可再生 |
| `benchmarks/` | 完美信息 MILP、LP 下界、逐日/逐时明细及说明报告。 | 基准结果，可再生 |
| `input_data.json` | 从原始附件提取的标准化输入。 | 输入缓存，可再生 |

## `archive/` 历史与缓存

| 路径 | 作用 | 状态 |
|---|---|---|
| `output_baseline_pre_fix/` | 修复公共候选池、月度回退和三级目标之前的旧结果，只用于版本对比。 | 历史备份，禁止作为当前结论 |
| `before_target_1500_100_20260912_182709/` | 本轮降低两项费用之前的代码、模型和正式结果。 | 前后对照基线 |
| `target_runs_20260912/` | 新预测候选筛选、81%与85%购电分位数完整运行及预览核验。 | 开发实验及追溯 |
| `python_cache_legacy/` | 整理前遗留的 `.pyc` 缓存，对运行结果无影响。 | 可删除、可再生 |

## 使用约定

- 提交文件使用 `output/result2.xlsx`。
- 论文数值优先引用 `output/result_summary.md`、`output/question_two_daily.csv` 和 `output/question_two_detail.csv`。
- 判断一次运行是否可信，先查看 `output/verification_report.json` 与 `output/analysis_verification.json` 是否为 `PASS`。
- `archive` 内文件不得与当前正式结果混用；`previews`、`paper_tables`、`comparisons`、`benchmarks` 和缓存均可由程序重新生成。
