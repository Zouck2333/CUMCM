# Question_Two文件索引

当前主模型为 `docs/model_Two.md`，当前参数模式为 `causal_monthly`。以 `output/result2.xlsx` 及其配套JSON、CSV和核验报告为正式结果。

| 路径 | 当前用途 |
|---|---|
| `run_question_two.ps1` | 默认执行历史滚动调参、求解、工作簿和完整核验流程 |
| `docs/model_Two.md` | 已重写的历史信息集、选参评分、风险校准、储备和联合MILP模型 |
| `docs/model_Two_calibrated.md` | 历史固定参数开发回测模型，已标记非当前版本 |
| `docs/model_code_consistency_review.md` | 历史审查记录，不替代当前模型正文 |
| `code/causal_policy.py` | 调参协议、历史候选评分和连续SOC验证 |
| `code/forecast_calendar.py` | 逐日星期效应和趋势预测 |
| `code/solve_question_two.py` | 主求解器，默认causal_monthly，也保留fixed用于历史复现 |
| `code/verify_causal_policy.py` | 不导入调参器或求解器的独立选参核验 |
| `code/verify_question_two.py` | 正式结果物理、费用和Excel映射核验 |
| `code/test_information_boundary.py` | 包含重新调参的未来数据扰动测试 |
| `code/test_causal_policy.py` | 候选分位数、预算选择和未来输入拒绝测试 |
| `code/test_calendar_forecast.py`、`code/test_risk_calibration.py` | 预测和购电风险约束测试 |
| `code/extract_inputs.py`、`code/build_result2.mjs` | 原始附件提取及题目工作簿生成 |
| `code/generate_paper_tables.py` | 四个指定日期的论文表格 |
| `code/perfect_information_benchmark.py` | 仅用于事后评价的离线MILP和LP下界 |
| `code/build_result_summary.py`、`code/build_causal_summary.py` | 统一汇总入口与历史滚动报告 |
| `code/verify_analysis_outputs.py` | 参数模式对应的配套分析核验 |
| `code/run_model_comparisons.py`、`code/assemble_comparison_report.py` | 历史固定参数的灵敏度工具，不用于当前正式选参 |
| `code/research_forecast_targets.py`、`code/analyze_risk_calibration.py` | 历史开发筛选实验，不进入正式滚动流程 |
| `output/tuning_protocol.json` | 全年运行前保存的候选与评分规则、SHA256 |
| `output/question_two_solution.json` | 正式计划、预测历史、11次调参的全部候选评分与验证调度 |
| `output/question_two_daily.csv` | 365天每日实际参数、来源、预测误差、SOC与费用 |
| `output/question_two_detail.csv` | 正式48,096个时段的购电、充放电、风险余量与结算 |
| `output/result2.xlsx` | 正式提交工作簿 |
| `output/verification_report.json` | 正式结果与月度选参独立核验 |
| `output/information_boundary_test.json` | 2月1日初始化选参扰动测试 |
| `output/information_boundary_test_later.json` | 6月1日后续滚动选参扰动测试 |
| `output/analysis_verification.json` | 协议、两次边界测试、基准哈希与报告核验 |
| `output/policy_updates_summary.csv`、`output/causal_tuning_report.md` | 月度历史截止、参数及评分说明 |
| `output/result_summary.md`、`output/optimization_comparison.md` | 当前费用与本轮修改前固定参数结果对照 |
| `output/processing_manifest.json` | 输入、结果、程序与模型正文哈希及金额目标状态 |
| `output/previews`、`output/paper_tables`、`output/benchmarks` | 工作簿预览、指定日期表和离线参照 |
| `archive/before_causal_tuning_20260912_190504` | 本轮历史调参修改之前的代码、文档和结果完整备份 |
| `archive/before_target_1500_100_20260912_182709` | 更早的费用优化基线 |
| `archive/target_runs_20260912` | 历史固定参数候选实验 |

`archive`及明确标记为历史的实验，不得与当前正式结果混用。`output/debug_*`仅供小样本调试，不覆盖正式文件。
