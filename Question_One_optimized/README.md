# C题第一问优化版求解程序

本目录在原有 LP/MILP 求解器基础上增加了参数化、可选字典序目标和批量灵敏度分析。基础 LP/MILP 的目标与硬约束保持不变。当前版本不包含鲁棒 MILP。

## 文件

| 文件 | 作用 |
| --- | --- |
| `solve_question_one.py` | 参数化主程序，支持 LP、MILP 和字典序优化 |
| `solver_worker.py` | LP/MILP 求解模块 |
| `sensitivity_analysis.py` | 批量灵敏度分析 |
| `requirements.txt` | Python 依赖 |
| `run_lp.ps1` | 运行标准 LP |
| `run_milp.ps1` | 运行字典序 MILP |
| `run_sensitivity.ps1` | 运行全部灵敏度情景 |
| `output/` | 程序生成的结果目录 |

## 环境

```powershell
python -m pip install -r Question_One_optimized\requirements.txt
```

需要 SciPy 提供 `linprog` 和 `milp`。

主程序需要 NumPy、pandas 和 openpyxl；求解模块需要 NumPy 和 SciPy。程序可自动调用另一个安装有 SciPy 的 Python，因此两部分不必位于同一环境。必要时可用 `--solver-python` 指定求解器 Python。

## 运行标准 LP

```powershell
python Question_One_optimized\solve_question_one.py `
  --method lp `
  --output Question_One_optimized\output\result1_lp.xlsx `
  --detail Question_One_optimized\output\question_one_detail_lp.csv
```

LP 不包含充放电互斥约束，用于计算理论下界。

## 运行基础 MILP

```powershell
python Question_One_optimized\solve_question_one.py `
  --method milp `
  --objective-mode cost `
  --output Question_One_optimized\output\result1_milp.xlsx `
  --detail Question_One_optimized\output\question_one_detail_milp.csv
```

MILP 包含充放电互斥约束，并以最低购电费用为默认目标。使用 `--objective-mode lexicographic` 可执行可选字典序扩展，其目标依次为：

1. 最小化全天购电费用。
2. 在近优费用集合中最小化电池吞吐量。
3. 在近优费用和吞吐量集合中最小化峰值购电功率。

批量灵敏度分析始终使用基础的纯费用 MILP，字典序扩展不会改变基础情景结果。

## 参数化

主程序支持以下参数：

| 参数 | 含义 | 默认值 |
| --- | --- | ---: |
| `--eta` | 充放电效率 | 0.9 |
| `--soc-min` | 最低储电量 | 1200 kWh |
| `--soc-max` | 最高储电量 | 10800 kWh |
| `--soc-initial` | 初始储电量 | 6000 kWh |
| `--soc-terminal` | 终端储电量 | 等于初始储电量 |
| `--power-limit-kw` | 最大充放电功率 | 5000 kW |
| `--load-factor` | 负载缩放系数 | 1.0 |
| `--pv-factor` | 光伏缩放系数 | 1.0 |

示例：

```powershell
python Question_One_optimized\solve_question_one.py `
  --method milp `
  --eta 0.85 `
  --load-factor 1.05 `
  --pv-factor 0.95
```

## 灵敏度分析

```powershell
python Question_One_optimized\sensitivity_analysis.py `
  --output-dir Question_One_optimized\output\sensitivity
```

输出：

```text
output/sensitivity/sensitivity_results.csv
output/sensitivity/sensitivity_results.xlsx
output/sensitivity/sensitivity_summary.md
```

每个情景都会重新求解基础 MILP，并强制检查能量平衡、SOC 递推、SOC 边界、终端 SOC、功率上限、弃光边界及充放电互斥。任何硬约束检查失败都会终止批量分析。

分析参数：

- 初始和终端 SOC。
- 充放电效率。
- PV 预测。
- 负载预测。
- 最大充放电功率。
- 储能可用 SOC 区间。
- 负载与 PV 的组合不利情景。

## 当前基准结果

使用附件 1 和默认参数时，LP 与 MILP 的最优费用一致：

```text
全天购电费: 35126.948589 元
全天购电量: 59482.698998 kWh
最大充电量: 833.333333 kWh
最大放电量: 715.653033 kWh
初始储电量: 6000 kWh
终端储电量: 6000 kWh
```

## 说明

当前版本未实现鲁棒 MILP。`--method` 只提供 `lp` 和 `milp` 两种入口。
