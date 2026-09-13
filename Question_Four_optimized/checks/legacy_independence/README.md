# 第四问解除旧目录依赖核验

2026年9月13日，默认独立核验入口在禁止访问工作区旧 `Question_Four` 目录的条件下执行成功。本次用 Python 审计钩子拦截该目录的文件读取及目录枚举；没有移动、删除或修改旧目录，也没有尝试访问其中任何文件。

结果见同目录 `verification_report.json`。两种策略均通过365天轨迹核验、334天正式期费用重算、48096行CSV对照及工作簿逐单元格核验。原始费用、计划与SOC轨迹未重新优化或改写。

默认报告中的 `original_source_verification.status` 为 `not_requested`，表示未进行可选的历史源文件比对，不表示数学核验失败，也不宣称旧文件已经通过哈希检查。

本次还运行了 `python -B -m unittest discover -s Question_Four_optimized/tests -v`，17项测试全部通过。新增3项测试检查旧归档改名迁移后仍可比对，且显式比对时仍拒绝文件缺失或内容变化。

需要重做数学核验时，在工作区根目录运行：

```powershell
python -B -m Question_Four_optimized.code.verify_optimized --report-file Question_Four_optimized/checks/legacy_independence/verification_report.json
```

需要比对保留的旧归档时，另加 `--original-source-dir` 并指定归档目录。原始附件仍来自同级 `C题/附件`，该目录必须保留。
