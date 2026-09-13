# 第四问优化版

正式结果和对照结论见 [优化报告](output/optimization_report.md)，数学模型见 [model_Four.md](model_Four.md)。两种策略都从 2025 年 1 月 1 日 6000 kWh 独立计算，正式评价期为 2—12 月，共 334 天。

本目录 `Question_Four_optimized` 保存正式优化版，因 Windows 占用暂保留原名。`reference`、`experiments`、测试参考文件、正式输出与对照结果保留。历史报告中的原目录名仅表示生成时的来源；当前运行命令以本README为准。

## 主要文件

- `output/result4-2.xlsx`：每日 0:00 一次计划的正式结果。
- `output/result4-3.xlsx`：四时点滚动计划的正式结果。
- `output/optimization_report.md`：费用、预测误差、逐月表现、跨日状态和扩展范围。
- `output/paper_selected_dates.md`：四个论文指定日期的汇总。
- `output/detail_4-2.csv`、`detail_4-3.csv`：十分钟执行与结算明细。
- `output/verification_report.json`：独立核验结果。
- `configuration.json`：两种正式策略的明确配置。
- `experiments/`：原预测、仅负荷改进、负荷电价改进及终端价值实验。
- `reference/`：原模型和结果摘要、历史原文件哈希、求解器修正前版本；应保留以支持对照，默认核验不读取旧目录。
- `archive/`：数值精度修正前中止的实验记录，不能作为完整结果使用。

## 复现正式结果

在工作区根目录执行：

```powershell
python -B Question_Four_optimized/run.py
python -B -m Question_Four_optimized.code.verify_optimized
```

独立核验默认检查两种策略的全年轨迹、能量平衡、SOC、容量与功率、充放电互斥、费用、CSV 和工作簿，不要求旧 `Question_Four` 目录存在。报告将历史源文件比对记为 `original_source_verification.status=not_requested`，不会把未执行的比对标成通过。

如需额外确认某份旧版归档仍与原始快照相同，可显式指定其目录：

```powershell
python -B -m Question_Four_optimized.code.verify_optimized --original-source-dir C:/Archives/Question_Four_original
```

该参数接受自行保存的旧版归档目录，上面的路径只是示例。当前 `Question_Four_optimized` 是正式优化版，不能用作旧版归档参数。核验保留原哈希记录，只在给定目录下按相对路径寻找旧文件；显式要求比对时，缺失或哈希不符仍会报错。使用 `--report-file` 可另存本次报告，例如 `--report-file Question_Four_optimized/checks/verification_after_cleanup.json`。

也可先测试：

```powershell
python -B -m unittest discover -s Question_Four_optimized/tests -v
python -B Question_Four_optimized/run.py --max-days 2 --no-workbook --output-dir Question_Four_optimized/checks/smoke
```

正式输出已经存在时，程序核对计算配置和源数据指纹，复用已验证的断点并重建结果表。若修改了核心算法或数据，请用新的 `--output-dir`，不要把旧断点与新算法混用。

正式记录沿用已通过全年独立校验的终端价值实验。该实验求解时尚未加入“仅在原程序本会报错的负流量出现时才触发”的高精度补救分支。365 天全部成功，因此补救分支不改变这些既有轨迹。`output/checkpoint_compatibility.json` 明确保留原生成指纹、当前兼容指纹和完整断点内容哈希，不改写原记录来源；复用时再次检查全年物理约束。任何算法、配置、输入或断点内容变化都会使此特定兼容关系失效。

输入默认取同级 `C题/附件` 中的附件 2、3、4 和附件 5 模板。运行不依赖整理前的旧版工作目录。数值部分需要 NumPy、SciPy；工作簿只读部分需要 openpyxl。XLSX 通过 Codex 捆绑的 Node.js 与 `@oai/artifact-tool` 生成，导出时创建临时依赖链接。没有该运行时的环境仍可使用 `--no-workbook` 生成数值结果与 CSV。

## 独立实验

下面的命令为新目录运行完整年度对照，不覆盖已保存的实验：

```powershell
python -B -m Question_Four_optimized.code.run_question_four --load-mode legacy --price-mode legacy --no-workbook --output-dir Question_Four_optimized/reproduction/baseline
python -B -m Question_Four_optimized.code.run_question_four --load-mode calendar --price-mode legacy --no-workbook --output-dir Question_Four_optimized/reproduction/load_only
python -B -m Question_Four_optimized.code.run_question_four --load-mode calendar --price-mode adaptive --no-workbook --output-dir Question_Four_optimized/reproduction/adaptive_price
python -B -m Question_Four_optimized.code.run_question_four --load-mode calendar --price-mode adaptive --terminal-value-weight 1 --no-workbook --output-dir Question_Four_optimized/reproduction/terminal_value
```

预测回放：`python -B -m Question_Four_optimized.code.forecast_audit`。配置选择及报告为同年模型开发与回测，不构成独立年份验证；运行中的每个预测、评分和决策均遵守历史信息边界。

## 计算口径

购电、光伏、负荷和充放电以 kWh/十分钟计，价格为元/kWh。充放电单程效率均为 0.9，功率上限 5000 kW，SOC 范围 1200—10800 kWh。

调整费用为相对 0:00 计划最终上调部分的 1.5 倍电价加下调部分的 0.5 倍电价；初始计划另行全额结算，紧急购电为 5 倍电价。计划费用使用预测价优化、实际价结算。终端价值只影响优化，不抵扣真实费用。

本轮没有加入区间内电池反馈、备用购电合同或完整多阶段情景树；名义平衡造成的风险应对限制已在模型和报告中说明。
