# 第三问优化版完整文件包

本文件夹集中保存更新后的模型、代码、原始输入、正式结果、对照结果与核验资料。通过本目录的`run.py`运行时，输入和输出都位于本文件夹，可整体复制到其他位置；运行环境依赖见下文。

## 先看哪些文件

| 内容 | 位置 |
|---|---|
| 完整数学模型 | [model_Three.md](model_Three.md) |
| 更新后的全部程序代码 | `code/` |
| 正式结果工作簿 | [output_optimized/result3.xlsx](output_optimized/result3.xlsx) |
| 优化效果与费用对照 | [output_optimized/optimization_summary.md](output_optimized/optimization_summary.md) |
| 论文指定四个日期的表格 | [output_optimized/paper_selected_dates.md](output_optimized/paper_selected_dates.md) |
| 每10分钟执行明细 | `output_optimized/question_three_detail.csv` |
| 独立核验报告 | `output_optimized/verification_report.json` |
| 原题、附件1—3与结果模板 | `inputs/` |
| 同配置旧预测对照断点 | `comparison_four_day/checkpoint_rolling.jsonl` |
| 测试及参考求解器 | `tests/`，其中`fixtures/`只用于等价性测试 |
| 原运行日志及整理后核验日志 | `logs/` |
| 文件校验清单 | `MANIFEST.json` |

2025年2月1日至12月31日共334天：旧预测同配置费用21,551,954.71元，优化后15,950,218.82元，节省5,601,735.89元（25.99%）。新预测仅0:00策略费用16,046,713.59元，四时点滚动进一步节省96,494.77元。

## 运行

在本文件夹打开终端：

```powershell
python -B run.py verify
```

上面命令读取本包中的输入附件，独立核验已保存的全年断点、334天结果、Excel与CSV。各操作如下：

```powershell
python -B run.py test       # 运行14项测试
python -B run.py solve      # 完整主策略与仅0:00对照，支持已保存断点续算
python -B run.py compare    # 同配置旧预测对照
python -B run.py report     # 生成优化说明与论文指定日期表格
python -B run.py all        # 顺序执行测试、求解、对照、报告、核验
```

也可在其他目录使用本包`run.py`的绝对路径。入口会自动定位本包，不依赖终端当前工作目录。未指定操作时默认执行`verify`。

已保存完整365天断点，因此`solve`在模型、参数和输入未变时会读取断点并重新导出结果。要从头重算或做新参数实验，请指定一个新的输出目录。例如在本文件夹运行：

```powershell
python -B code/run_question_three.py --base-dir inputs --output-dir output_recomputed
```

直接运行`code/`内底层脚本时，应显式传入`--base-dir inputs`；日常使用推荐统一入口`run.py`。

## 文件与版本说明

- 核心求解代码和数学模型从已通过核验的优化版逐字节复制，保留原断点的SHA256指纹；本次整理没有重新优化或改变计算结果。
- 报告脚本改为读取本包`inputs/`，测试脚本改为读取本包`tests/fixtures/`。这两项只是路径适配。
- 模型文档中的旧文件夹名称属于求解时记录的历史来源，原位置的使用说明另存于`docs/README_before_packaging.md`。本包的运行方法与文件位置以当前README为准。
- 正式结果位于`output_optimized/`；`inputs/附件5/result3.xlsx`是原始空白模板。
- 工作簿旁的`result3.xlsx.inspect.ndjson`与`previews/`为导出和视觉核验资料，均随正式结果保留。
- 原来的`Question_Three`、`Question_Three_cheaper`文件夹仍保留，本包通过复制整理形成。

## 运行环境

本机已使用Python 3.14.2、NumPy 2.4.2、SciPy 1.17.1完成求解。Python库依赖列于`requirements.txt`，需要时可执行：

```powershell
python -m pip install -r requirements.txt
```

核验、测试和报告只需要Python环境。生成Excel还使用Codex捆绑的Node.js与`@oai/artifact-tool`；本机程序会自动定位它们。复制到其他电脑时，需具备该运行时，或设置`QUESTION_THREE_NODE`与`QUESTION_THREE_NODE_MODULES`指向相应Node可执行文件和包含`@oai/artifact-tool`的模块目录。本文件夹包含项目文件，不复制整套运行时。

`MANIFEST.json`记录整理后的文件大小与SHA256，用于检查复制或传输是否完整。重新求解、生成报告或修改文件后，相应文件校验值可能变化。
