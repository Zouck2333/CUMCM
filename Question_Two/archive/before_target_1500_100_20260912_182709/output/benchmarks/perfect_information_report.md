# 第二问完美信息基准结果

## 计算口径

评价区间为 2025-02-01 至 2025-12-31，共 334 天。

- 滚动方案费用来自主程序每日结果，包含正常购电费与 5 倍电价的实际紧急购电费。
- 匹配完美信息 MILP 使用评价期实际负载和实际光伏，保留相同首日 SOC、充放电效率、容量、功率、充放电互斥及逐日安全储备。
- 完美信息 LP 下界使用相同首日 SOC，取消逐日安全储备并放松充放电互斥，全部日期在一个连续时域内联合求解。
- 两个离线基准均不回填到滚动预测或计划中。

## 成本与差距

| 方案 | 正式期成本/元 | 相对指标 |
|---|---:|---:|
| 滚动方案 | 15,465,376.55 | — |
| 匹配完美信息 MILP | 12,246,534.38 | Gap_info = 26.2837% |
| 完美信息 LP 下界 | 12,226,042.07 | Gap_LP = 26.4954% |

其中：

$$Gap_{info}=\frac{F_{rolling}-F_{perfect}^{MILP,matched}}{F_{perfect}^{MILP,matched}}\times100\%,$$

$$Gap_{LP}=\frac{F_{rolling}-F_{perfect}^{LP,lower}}{F_{perfect}^{LP,lower}}\times100\%.$$

## 求解与核验

- 匹配 MILP：status=0，MIP gap=4.562870827698168e-16，耗时 232.001 秒。
- LP 下界：status=0，迭代次数=116645，耗时 3.786 秒。
- 匹配 MILP 物理核验：通过。
- LP 下界物理核验：通过。
- 成本序关系 LP下界 ≤ 匹配MILP ≤ 滚动费用：通过。

## 结果文件

- perfect_information_benchmark.json：汇总、差距、求解状态和核验指标；
- perfect_information_daily.csv：两个基准的逐日调度汇总；
- perfect_information_detail.csv：两个基准的逐10分钟调度结果。
