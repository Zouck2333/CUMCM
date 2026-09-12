# C题第三问：滚动购电与储能优化

本目录按 [model_Three.md](model_Three.md) 实现第三问。程序在每天 0:00、6:00、12:00、18:00 根据当时可得的预报、历史数据和当前储电量求解剩余日三情景 MILP；每次只执行接下来的六小时。仅将 2025-01-01 0:00 的 SOC 初始化为 6000 kWh，之后在 1200-10800 kWh 内跨日连续传递，不固定每日终点。6:00 以后的情景概率由已观测负载前缀因果加权。1 月用于历史预热，正式结果覆盖 2025 年 2 月 1 日至 12 月 31 日。

## 运行

在工作区根目录运行：

~~~powershell
python Question_Three/code/run_question_three.py
~~~

系统 Python 需安装 NumPy、SciPy 和 openpyxl。当前工作区的运行环境中，系统 Python 提供 NumPy/SciPy，程序在缺少 openpyxl 时会读取 Codex 捆绑运行时的纯 Python 包。生成 Excel 使用 Codex 捆绑的 Node.js 和 @oai/artifact-tool。需要改用其他运行时，可设置 QUESTION_THREE_NODE 和 QUESTION_THREE_NODE_MODULES 环境变量。

默认同时运行四次更新的主策略与“只用 0:00 预报”的对照策略，使用 CVaR 风险费用、储能吞吐量、购电峰值三级字典序目标。完整运行需要为 365 天分别求解多次 MILP，可能持续较长时间。每完成一天便写入断点；用相同命令再次运行会核对断点并继续，模型参数不同则需使用新输出目录。阶段求解若仅有可行解而未达到所设 MIP gap，会报告当前解、下界和 gap，并停止正式计算。

调试或指定选项示例：

~~~powershell
python Question_Three/code/run_question_three.py --max-days 5 --output-dir Question_Three/tmp_smoke_free_soc
python Question_Three/code/run_question_three.py --strategy rolling
python Question_Three/code/run_question_three.py --scenario-count 5 --output-dir Question_Three/experiment_s5
python Question_Three/code/run_question_three.py --terminal-mode reserve --reserve-quantile 0.90 --output-dir Question_Three/experiment_reserve
~~~

--max-days 小于 365 时仅写断点，不生成正式 result3.xlsx。主工作簿只在无附加日末储备、三情景、前缀权重 0.10、CVaR 置信度 0.90、风险权重 0.10 和字典序目标的默认组合下导出。其他参数只用于独立实验，需使用不同输出目录；`--objective-mode primary` 仅供性能诊断。`--scenario-count` 支持 1/3/5 分支，`--distance-weight`、`--cvar-alpha`、`--risk-weight` 和 `--reserve-quantile` 对应模型中的灵敏度参数。默认结果写入 `Question_Three/output_free_soc/`；之前的 `Question_Three/output/` 保留每日固定 SOC 口径的旧版结果，不参与本次断点续算。

## 输入与结果

输入来自 C题/附件/附件1.xlsx、附件2.xlsx、附件3.xlsx 和附件5/result3.xlsx。附件2的每 10 分钟右端点功率除以 6 后作为该区间 kWh；附件3的每个整点预报按模型的右端点分段常数规则映射到六个 10 分钟时段。

完整运行后，Question_Three/output_free_soc/ 中主要文件为：

- result3.xlsx：按题目四张表填入正式 334 天结果，修正两张购电表错位的时段标题。
- question_three_detail.csv：每 10 分钟的最终执行量、实际值、SOC、紧急购电及费用。
- result_summary.json：逐日、逐月与评价期汇总。
- strategy_comparison.json：滚动策略与仅 0:00 策略的同口径对比。
- checkpoint_rolling.jsonl、checkpoint_zero_only.jsonl：全年顺序计算断点和阶段求解诊断。
- verification_report.json：独立重读源附件、CSV、工作簿和双策略断点后的核验结果。

0:00 全日购电量是初始计划；各 10 分钟时段实际执行的正常购电量来自最近一次已发布预报时点的计划。调整费用只比较这两个量一次，紧急购电在实际负载与光伏出现缺口后结算。result3.xlsx 的“调整购电量”表 EQ 列按模型约定填写包含初始计划费、调整费、紧急购电费的全天总费用；该表 EP 列只合计最终正常购电量，不含紧急购电。

两策略从 1 月 1 日相同的 SOC 出发，但各自的 1 月末 SOC 可能不同。因此 2–12 月的费用对比包含 1 月决策带入的储能状态差异；解释预报更新价值时，还应同时比较 1 月 1 日至 12 月 31 日的完整成本和年末 SOC，不把正式评价期费用差直接当作等初始状态的纯策略收益。

## 核验

程序在每个日期结束时独立核对数据长度、非负性、储能功率与互斥、SOC 上下界与递推、2025-01-01 的 6000 kWh 初始值、实际供电平衡、紧急购电最小缺口、三类费用和跨日 SOC 连续性。输出模块再核对工作簿的日期、时间列、汇总列、每日实际 0:00/24:00 SOC 与动态行数。历史残差只使用目标日以前的日期，当天未来实际负载、光伏及未发布预报不进入阶段求解函数。

完整计算后再运行：

~~~powershell
python Question_Three/code/verify_question_three.py
~~~

核验程序独立重读附件和输出文件，逐段核对 334 天明细、四张结果表、费用汇总与双策略断点。对照文件还给出逐月及论文指定四个日期的结果。断点保存决策输入、模型文档和核心代码的 SHA256 指纹；修改这些内容后需要另设输出目录重算。

先运行 `python -B -m unittest discover -s Question_Three/tests -v`，可检查概率冷启动、零方差、五情景分组、自由日末 SOC 和求解状态。第 14 节的完整参数灵敏度重算尚未执行；效率解释、负载与光伏压力、预报插值实验尚未自动化。正式工作簿和对照结果使用文档给出的固定主参数，不能据评价期结果反选参数。
