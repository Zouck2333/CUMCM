# 第一问确定性购电与储能调度模型

本文给出 `Question_One` 当前程序对应的完整数学模型、求解流程和结果口径。正式方案采用购电费用最小的混合整数线性规划（MILP）；连续线性规划（LP）用于计算费用下界。代码另提供费用、吞吐量、购电峰值的字典序优化，以及参数灵敏度分析。**鲁棒 MILP 尚未实现，不属于正式求解或已完成的实验。**

本说明依据原第一问模型文档整理，并逐项按本目录的 `solve_question_one.py`、`solver_worker.py` 和 `sensitivity_analysis.py` 修订。当前 `Question_One` 由原优化版目录更名而来，阅读、运行和核验均不需要整理前的旧版目录；输入仍使用同级 `C题/附件` 中的原始附件。

## 1 问题与信息条件

每天 0:00 根据已知的全天电价、小区负载和光伏预测制定当天计划购电及充放电策略，使购电费用最小。计划需要满足负载、储能容量与功率限制，并在 24:00 恢复日初储电量。第一问不设置紧急购电、计划调整结算或售电。

一天分为 144 个时段，记 $t=1,\ldots,144$，每段 $\Delta=1/6$ 小时。第 $t$ 段对应 $[(t-1)\Delta,t\Delta]$，$E_t$ 表示该段结束时的内部储电量，$E_0$ 表示 0:00 状态。程序按附件行顺序计算，明细的“时间”字段保留原始时间标签；汇总按时段序号分组。

假设时段内功率按给定平均值计，忽略线路损耗、网络阻塞、电池老化及运维费用。光伏余量可充电或弃光，不上网。充电、放电的单程效率均取 0.90，因此往返效率为 0.81；这与把往返效率设为 0.90 是不同的参数口径。

## 2 数据与参数

主程序默认读取同级 `C题/附件/附件1.xlsx`，使用“时间”“电价”“小区负载”“光伏发电预测功率”四列，并检查记录数为 144。结果模板为 `C题/附件/附件5/result1.xlsx`。可用 `--input` 和 `--template` 指定其他位置。

| 符号 | 含义 | 默认值或单位 |
|---|---|---|
| $p_t$ | 第 $t$ 段电价 | 元/kWh |
| $L_t,P_t^{PV}$ | 负载、光伏预测功率 | kW |
| $\ell_t,s_t$ | 时段负载、光伏预测电量 | kWh |
| $\eta$ | 充电和放电单程效率 | 0.90 |
| $E_{min},E_{max}$ | 允许的内部储电量边界 | 1200、10800 kWh |
| $E_{ini},E_{end}$ | 日初、日末内部储电量 | 均为 6000 kWh |
| $P_{max}$ | 最大充电、放电功率 | 5000 kW |
| $B=P_{max}\Delta$ | 单时段最大充电、放电电量 | $5000/6$ kWh |
| $a_L,a_{PV}$ | 负载、光伏缩放系数 | 均为 1 |

功率进入模型前换算为时段电量：

$$
\ell_t=a_LL_t\Delta,\qquad s_t=a_{PV}P_t^{PV}\Delta.
$$

SOC 在本文指以 kWh 表示的内部储电量。额定容量为 12000 kWh，但优化允许范围是 1200 至 10800 kWh，不能把 12000 直接作为运行上界。

## 3 决策变量

| 变量 | 定义 | 程序字段 |
|---|---|---|
| $G_t\ge0$ | 外网计划购电量，kWh | `grid` |
| $C_t\ge0$ | 进入充电设备前的电量，kWh | `charge` |
| $D_t\ge0$ | 电池向微网输出的有效放电量，kWh | `discharge` |
| $Q_t\ge0$ | 未利用的光伏电量，kWh | `curtailment` |
| $E_t$ | 时段末内部储电量，kWh | `soc` |
| $z_t\in\{0,1\}$ | 充放电模式，1 允许充电，0 允许放电 | `mode_idx`，仅 MILP |
| $P^{grid}\ge0$ | 购电峰值功率辅助变量，kW | `peak_idx`，仅 MILP |

$C_t$ 经过充电损耗后增加的内部储电量为 $\eta C_t$；输出 $D_t$ 需要消耗内部储电量 $D_t/\eta$。弃光量 $Q_t$ 受当段光伏上限限制，不代表任意丢弃已购电量。

## 4 公共物理约束

每个时段满足能量平衡与弃光边界：

$$
G_t+s_t+D_t=\ell_t+C_t+Q_t,\qquad 0\le Q_t\le s_t.
$$

储能状态递推为：

$$
E_t=E_{t-1}+\eta C_t-\frac{D_t}{\eta},\qquad t=1,\ldots,144.
$$

容量、充放电量与首末状态约束为：

$$
E_{min}\le E_t\le E_{max},\qquad 0\le C_t,D_t\le B,
$$

$$
E_0=E_{ini},\qquad E_{144}=E_{end}=E_{ini}.
$$

正式参数下 $E_0=E_{144}=6000$。程序允许通过 `--soc-terminal` 单独指定实验终值；若指定值不等于日初值，该实验就不再满足第一问要求的首末电量相等，不能直接替代正式方案。未指定此参数时，终值自动等于初值。

## 5 标准 LP 与正式 MILP

两种模型均以全天正常购电费用为目标：

$$
\min F_1=\sum_{t=1}^{144}p_tG_t.
$$

LP 使用第 4 节全部约束及连续变量，不强制充放电互斥，因此其最优费用是同参数 MILP 的下界。MILP 在公共约束基础上加入：

$$
0\le C_t\le Bz_t,\qquad 0\le D_t\le B(1-z_t),\qquad z_t\in\{0,1\}.
$$

两式保证同一时段最多只有一种充放电方向，也允许电池闲置。程序还在 MILP 中设置：

$$
P^{grid}\ge G_t/\Delta\quad (t=1,\ldots,144).
$$

该峰值辅助变量没有预设容量上限；在纯费用模式中，它不改变购电和储能决策的可行域。输出的实际峰值按 $\max_t(G_t/\Delta)$ 重算。

正式运行采用 `--method milp --objective-mode cost`；`--objective-mode auto` 也解析为 `cost`。`run_milp.ps1` 明确调用纯费用模式。LP 入口只求解费用目标。

## 6 可选字典序优化

仅在显式指定 `--objective-mode lexicographic` 时，MILP 依次求解三级目标：

1. 最小化费用 $F_1$，得到求解器返回的费用最优值 $F_1^*$。
2. 加入 $F_1\le F_1^*+\varepsilon_1$，最小化电池吞吐量 $F_2=\sum_t(C_t+D_t)$。
3. 再加入 $F_2\le F_2^*+\varepsilon_2$，最小化购电峰值 $F_3=P^{grid}$。

容差与 `solve_dispatch` 一致：

$$
\varepsilon_1=\max(10^{-5},10^{-9}|F_1^*|),\qquad
\varepsilon_2=\max(10^{-5},10^{-9}|F_2^*|).
$$

后级目标允许前级目标在上述容差内变化。因此这里的“字典序”是带数值容差的逐级优化，不能表述为每一级都在精确算术下完全不变。当前正式结果和 45 组灵敏度结果均来自纯费用 MILP，不使用字典序结果替换。

## 7 求解与结果导出

程序先检查输入参数与数据，再构造稀疏约束矩阵。`solver_worker.py` 对 LP 调用 SciPy `linprog(method="highs")`，对 MILP 调用 SciPy `milp`，底层均为 HiGHS。只有返回解且状态为 0 才接受。MILP 默认相对间隙 `--mip-gap=1e-8`，默认不设时间上限；结果是对应容差下的数值解，不据此声称调度轨迹唯一。

运行命令如下，终端位置为工作区根目录：

```powershell
python Question_One/solve_question_one.py --method lp
python Question_One/solve_question_one.py --method milp --objective-mode cost
```

每个模式导出 `output/result1_lp.xlsx` 或 `output/result1_milp.xlsx`，同时导出对应的 `question_one_detail_*.csv`。模板“计划购电量”工作表写入 144 段计划购电量；“充放电量”按每 24 段汇总为六个四小时区间，并填写日初、日末储电量。程序明细保留原始数值，模板中的购电量、充放电汇总及首末储电量按 4 位小数写入。

数学核验由 `validate_solution` 计算逐段能量平衡误差、SOC 递推误差、终端误差、容量与功率越界、弃光边界及同时充放电段数。`verify_optimized.py` 可重新求解 LP、纯费用 MILP 和可选字典序 MILP，并打印这些指标；灵敏度程序对硬约束检查失败直接报错。核验脚本不写回正式工作簿。

## 8 正式基准与灵敏度结果

当前保存的基准结果如下，来源为本目录 `output/question_one_detail_milp.csv`；LP 与 MILP 的费用、总购电量相同。

| 指标 | 数值 |
|---|---:|
| 全天购电量 | 59482.698998 kWh |
| 全天购电费用 | 35126.948589 元 |
| 峰值购电功率 | 8458.827300 kW |
| 最大单段充电量 | 833.333333 kWh |
| 最大单段放电量 | 715.653033 kWh |
| 最低、最高储电量 | 1200、10800 kWh |
| 日初、日末储电量 | 6000、6000 kWh |
| 全天弃光量 | 0 kWh |

`sensitivity_analysis.py` 已保存 45 组独立确定性 MILP 情景，改变首末 SOC、效率、负载与光伏缩放、功率上限、SOC 区间及组合参数。每组实验均重新优化，且终端 SOC 等于该组初始 SOC。完整结果在 `output/sensitivity/sensitivity_results.csv`，解释见同目录 `sensitivity_summary.md`。

保存的灵敏度核验中，最大能量平衡误差为 $3.752\times10^{-12}$ kWh，最大 SOC 递推误差为 $3.638\times10^{-12}$ kWh，终端 SOC 误差为 0；功率、弃光、容量及互斥约束违反次数均为 0。负载增加 10% 且光伏减少 10% 时费用为 46572.764642 元；光伏降至 80% 时费用增加约 20.332%；效率降至 0.80 时费用增加约 8.816%。这些都是对应参数下重新求解的结果。

## 9 鲁棒模型的实现边界

旧文档提出过光伏安全折减的鲁棒扩展。当前 `--method` 只接受 `lp`、`milp`，没有鲁棒入口、正式不确定集合或在同一计划下对所有不确定情景施加的可行性约束。

`--pv-factor` 只把一组确定性光伏输入乘以给定系数，然后重新优化；多组系数产生多套独立计划。这属于参数灵敏度分析，不能称为已实现的鲁棒优化，也不能宣称一个计划同时保证所有光伏误差情景下可行。

若后续实现鲁棒模型，需要另外定义光伏不确定集合、共同决策及最坏情形目标或约束，并单独提供代码、结果与核验。当前论文中的第一问主结果应仅使用已经实现的 LP/MILP 模型。

## 10 模型与文件对应

| 内容 | 本目录文件或函数 |
|---|---|
| 参数、功率转电量、输入检查 | `solve_question_one.py` 的 `load_day_data`、`validate_parameters` |
| 公共约束、LP/MILP、充放电互斥 | `solver_worker.py` 的 `solve` |
| 纯费用与字典序入口、容差 | `solve_question_one.py` 的 `solve_dispatch`、`main` |
| 结果明细与工作簿映射 | `solve_question_one.py` 的 `_solve_stage`、`fill_template` |
| 物理约束核验 | `solve_question_one.py` 的 `validate_solution` |
| 基础与可选模式复算 | `verify_optimized.py` |
| 45 组参数实验 | `sensitivity_analysis.py`、`output/sensitivity/` |
| 当前正式结果 | `output/result1_milp.xlsx`、`output/question_one_detail_milp.csv` |
| 已保存的验证记录 | `verification_report.md` |

因此，保留本目录及公共原始附件即可继续使用第一问的模型说明、程序、正式结果和灵敏度资料。旧目录不再承担唯一模型文档来源的作用。
