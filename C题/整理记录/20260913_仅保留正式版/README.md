# 目录整理结果

工作区现仅保留五个可见目录：`Question_One`、`Question_Two_updated`、`Question_Three_optimized`、`Question_Four_optimized`、`C题`。隐藏的 `.git` 保留。第一问名称已去掉后缀；其余三个目录因 Windows 占用仍保留原名，程序与说明已恢复为实际路径。

五个旧版工作目录及暂存目录已删除。优化版中 332 个原有文件全部存在，其中 63 个参考、实验、测试参考、灵敏度及对照文件逐字节保持不变。修改仅涉及运行路径、使用说明及相应清单校验值；正式计算结果未改动。第一问完整模型已保留，明确鲁棒 MILP 尚未实现。第四问默认独立核验无需旧目录。

`cleanup_result.json` 为最终状态，`preservation_check.json` 为保留文件核验，`rename_snapshot.json` 为整理前快照。其中快照的 after 字段记录原计划名称；实际最终名称见 cleanup_result.json。带 pending 字样的文件记录中间状态。

各问核验结果保存在本目录。旧第四问的 21 个源文件在删除前通过历史哈希核验。第三、四问在旧目录删除后再次通过全年独立核验，第四问 17 项测试通过。
