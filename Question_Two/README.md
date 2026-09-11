# C题第二问程序说明

本目录将 `model_Two.md` 中的滚动预测、历史残差情景和日前随机情景 MILP 转化为可重复运行的程序。程序严格按日期顺序执行：第 `d` 天的预测、调参、情景和安全储备只使用日期早于 `d` 的数据；1 月只用于预热，`result2.xlsx` 仅填写 2025 年 2 月 1 日至 12 月 31 日。

## 文件

- `model_Two.md`：第二问数学模型。
- `extract_inputs.py`：只读附件 1、附件 2 和结果模板，将功率换算为每 10 分钟电量。
- `solve_question_two.py`：滚动预测、月度参数选择、残差情景生成、每日 MILP、实际结算和 SOC 更新。
- `build_result2.mjs`：将求解结果写入题目给定的 `result2.xlsx` 模板。
- `verify_question_two.py`：复核工作簿尺寸、逐时购电量、4 小时充放电汇总及 Excel 错误值。
- `run_question_two.ps1`：一键运行入口。

## 运行环境

求解器需要 Python 3.11 以上版本以及 `numpy`、`scipy`。附件读取和复核需要 `openpyxl`。结果工作簿由 `@oai/artifact-tool` 生成。

```powershell
pip install -r .\Question_Two\requirements.txt
```

## 一键运行

在项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\Question_Two\run_question_two.ps1
```

可调整 MILP 相对间隙和每日求解时间上限：

```powershell
powershell -ExecutionPolicy Bypass -File .\Question_Two\run_question_two.ps1 -MipGap 1e-5 -TimeLimit 60
```

调试时可只求解前若干天。少于 365 天时程序不会覆盖正式 `result2.xlsx`：

```powershell
powershell -ExecutionPolicy Bypass -File .\Question_Two\run_question_two.ps1 -MaxDays 32
```

## 输出

所有结果保存在 `Question_Two/output`：

- `result2.xlsx`：按题目模板填写的正式结果。
- `question_two_solution.json`：工作簿生成所需的结构化结果和汇总诊断。
- `question_two_daily.csv`：365 天的预测误差、SOC、场景数、费用和求解状态，其中 1 月标记为预热期。
- `question_two_detail.csv`：2 月至 12 月共 334×144 行逐时结果。
- `verification_report.json`：工作簿自动核验报告。
- `previews`：三个工作表的渲染预览图。

## 结果口径

- 功率乘以 `1/6 h` 后转为 kWh。
- 充电效率和放电效率均为 0.9。
- 正常购电按附件 1 电价计费，实际紧急购电按同一时段电价的 5 倍计费。
- 日前计划中的购电、充电和放电在实际结算阶段固定不变。
- 计划购电量按 144 个时段输出；充放电量按每 24 个时段汇总为 6 个四小时区间；连续紧急购电时段自动合并。
- 工作日定义为周一至周五。题目未提供法定节假日表，因此未额外调整节假日。

