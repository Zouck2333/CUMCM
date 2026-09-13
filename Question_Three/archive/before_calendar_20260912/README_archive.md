# 优化前代码快照

本目录保存2026-09-12本次正式优化前的第三问代码、测试和模型文档。对应原结果为`Question_Three/output_free_soc/`，本次未覆盖这些结果。

使用归档核验程序验证原结果时，需显式指定工作区根目录和原输出目录，避免归档深度改变默认路径：

```powershell
python -B Question_Three/archive/before_calendar_20260912/code/verify_question_three.py --base-dir . --output-dir Question_Three/output_free_soc
```

归档只作为历史版本留存；当前主程序与优化结果分别位于`Question_Three/code/`和`Question_Three/output_optimized/`。
