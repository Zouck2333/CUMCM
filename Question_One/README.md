# C题第一问：微网储能调度 MILP 求解程序

本目录中的程序用于求解 C 题第一问的日前微网调度问题。程序根据 144 个十分钟时段的电价、小区负载和光伏预测功率，联合确定外网购电量及储能设备的充放电量，使小区电力供需平衡，并使一天的购电费用最小。主程序同时提供标准 LP 和 MILP 两种求解入口。

程序采用混合整数线性规划（MILP）建模，通过 SciPy 调用 HiGHS 求解器。与 SOC 离散动态规划相比，MILP 不需要设置储电量网格步长，可在求解器容差范围内得到全局最优解。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `solve_question_one.py` | 主程序：读取附件、选择 LP/MILP、校验结果并填写提交模板 |
| `milp_solver_worker.py` | 求解模块：建立 LP/MILP 约束矩阵并调用 SciPy/HiGHS |
| `C_problem1_three_layer_operational_model.md` | 第一问数学模型说明 |
| `result1_lp.xlsx`、`result1_milp.xlsx` | 按附件 5 模板生成的 LP/MILP 结果 |
| `question_one_detail_lp.csv`、`question_one_detail_milp.csv` | 两种模型的 144 时段调度明细 |

默认输入文件位于：

```text
C题/附件/附件1.xlsx
```

默认提交模板位于：

```text
C题/附件/附件5/result1.xlsx
```

## 运行环境

主程序需要：

- Python 3.10 或更高版本
- NumPy
- pandas
- openpyxl

MILP 求解环境需要：

- NumPy
- SciPy，且包含 `scipy.optimize.milp`

可使用同一个 Python 环境安装全部依赖：

```powershell
python -m pip install numpy pandas openpyxl scipy
```

程序会依次检查以下 Python 是否支持 `scipy.optimize.milp`：

1. `--solver-python` 指定的 Python；
2. 当前运行主程序的 Python；
3. 系统 `PATH` 中的 `python`。

因此，主程序环境与求解器环境可以是同一个，也可以分开。本机当前由工作区 Python 负责 Excel 数据处理，由系统 Python 3.14 中的 SciPy/HiGHS 负责 MILP 求解。

## 快速运行

在项目根目录 `CUMCM` 下执行：

```powershell
python Question_One\solve_question_one.py --method milp
```

程序成功运行后会覆盖生成：

```text
Question_One/result1_milp.xlsx
Question_One/question_one_detail_milp.csv
```

运行标准 LP：

```powershell
python Question_One\solve_question_one.py --method lp
```

对应输出为 `result1_lp.xlsx` 和 `question_one_detail_lp.csv`。不指定 `--method` 时默认运行 MILP。

如果默认 Python 没有 SciPy，可以显式指定求解器 Python：

```powershell
python Question_One\solve_question_one.py `
  --method milp `
  --solver-python "C:\path\to\python.exe"
```

## 命令行参数

```text
--input          输入数据文件，默认 C题/附件/附件1.xlsx
--template       结果模板，默认 C题/附件/附件5/result1.xlsx
--method         求解模型，可选 lp 或 milp，默认 milp
--output         提交结果路径，默认 Question_One/result1_模型类型.xlsx
--detail         明细结果路径，默认 Question_One/question_one_detail_模型类型.csv
--mip-gap        MILP 相对最优间隙，默认 1e-8
--time-limit     求解时间上限，单位为秒，默认不限制
--solver-python  安装有 SciPy 的 Python 可执行文件路径
```

自定义输入和输出示例：

```powershell
python Question_One\solve_question_one.py `
  --method milp `
  --input "C题\附件\附件1.xlsx" `
  --template "C题\附件\附件5\result1.xlsx" `
  --output "Question_One\result1_question_one.xlsx" `
  --detail "Question_One\question_one_detail.csv" `
  --mip-gap 1e-8 `
  --time-limit 60
```

## 输入数据要求

输入 Excel 必须正好包含 144 行数据，对应一天内每 10 分钟一个时段，并至少包含以下列：

| 列名 | 单位 | 含义 |
| --- | --- | --- |
| `时间` | — | 时段时间标记 |
| `电价` | 元/kWh | 从外网购电的单位价格 |
| `小区负载` | kW | 小区平均负载功率 |
| `光伏发电预测功率` | kW | 光伏平均预测功率 |

程序将功率乘以时段长度 `1/6 h`，转换成每个时段的电量（kWh）。缺少列、数据不能转成数值或行数不是 144 时，程序会停止并给出错误信息。

## 数学模型

### 决策变量

对每个时段 \(t\)，LP 和 MILP 都设置：

- \(G_t\)：外网购电量；
- \(C_t\)：储能充电量，指进入储能设备前的电量；
- \(D_t\)：储能放电量，指输送到负载侧的有效电量；
- \(W_t\)：弃光或未利用的光伏电量；
- \(E_t\)：时段结束时的储电量；

MILP 额外设置 \(u_t\) 作为充放电状态二进制变量，1 表示允许充电，0 表示允许放电。标准 LP 不包含该变量。

### 目标函数

最小化全天购电费用：

$$
\min \sum_{t=1}^{144} p_tG_t,
$$

其中 \(p_t\) 为时段电价。

### 电力平衡

$$
G_t+PV_t+D_t=L_t+C_t+W_t.
$$

购电、光伏和储能放电共同满足小区负载、储能充电及弃光需求。

### 储能状态递推

$$
E_t=E_{t-1}+\eta C_t-\frac{D_t}{\eta}.
$$

程序取充放电效率 \(\eta=0.9\)。充电时只有 \(90\%\) 的电量进入储能，向负载提供 \(D_t\) 时储能内部需要减少 \(D_t/0.9\)。

### 容量和功率约束

$$
1200\leq E_t\leq10800,
$$

$$
0\leq C_t,D_t\leq5000\times\frac{1}{6}=833.3333\ \text{kWh}.
$$

### MILP 充放电互斥

$$
C_t\leq Mu_t,
$$

$$
D_t\leq M(1-u_t),
$$

其中 \(M=833.3333\) kWh。二进制变量确保同一时段不能同时充电和放电。标准 LP 删除二进制变量和这两项互斥约束，其他约束保持一致。

### 首尾储电量约束

$$
E_0=E_{144}=6000\ \text{kWh}.
$$

这一约束避免程序通过在一天结束时耗尽储能来人为降低当日费用，使调度方案具有日循环可比性。

### 弃光约束

$$
0\leq W_t\leq PV_t.
$$

该约束保证弃光量不会超过当前时段的光伏发电量，并避免出现购电后再作为“弃光”丢弃的不合理结果。

## 输出说明

两种 `question_one_detail_模型类型.csv` 文件均包含：

| 字段 | 含义 |
| --- | --- |
| `时段`、`时间` | 时段编号与时间 |
| `电价` | 当前时段购电价格 |
| `负载电量_kWh` | 时段负载电量 |
| `光伏电量_kWh` | 时段光伏电量 |
| `计划购电量_kWh` | MILP 给出的外网购电量 |
| `充电量_kWh` | 储能充电量 |
| `放电量_kWh` | 储能向负载提供的电量 |
| `弃光或富余电量_kWh` | 未利用的光伏电量 |
| `时段初储电量_kWh` | 时段开始时 SOC |
| `时段末储电量_kWh` | 时段结束时 SOC |
| `购电费用` | 当前时段购电费用 |

`result1_lp.xlsx` 和 `result1_milp.xlsx` 均保留附件 5 的原有工作表和格式，并写入：

- `计划购电量`：144 个时段的购电计划；
- `充放电量`：每 4 小时汇总一次充电量和放电量；
- 储能初始电量和结束电量。

## 结果校验

每次求解后，主程序会在终端输出以下检查指标：

- 求解器状态与最优间隙；
- 总费用、总购电量、总充电量和总放电量；
- 最大电力平衡误差；
- 最大 SOC 递推误差；
- 初始、结束、最低和最高 SOC；
- 充放电功率越界时段数；
- 弃光越界时段数；
- 同时充放电时段数。

正常结果应满足：求解状态为 `Optimal`，各类违反约束时段数均为 0，平衡误差和 SOC 误差接近 0。

当前附件数据中，LP 解没有出现同时充放电，因此 LP 与 MILP 的参考结果相同：

```text
求解状态：Optimal
MILP 最优间隙：0
总购电费用：35126.948589
总购电量：59482.698998 kWh
总充电量：20740.666132 kWh
总放电量：16799.939567 kWh
最低 SOC：1200 kWh
最高 SOC：10800 kWh
结束 SOC：6000 kWh
```

不同求解器版本可能给出不同但等价的充放电时序，目标函数值和约束检查应在数值容差范围内一致。

## 常见问题

### 提示找不到支持 MILP 的 Python

错误信息：

```text
未找到支持 scipy.optimize.milp 的 Python
```

在一个 Python 环境中安装 SciPy，然后通过 `--solver-python` 指定其路径：

```powershell
C:\path\to\python.exe -m pip install scipy
python Question_One\solve_question_one.py `
  --method milp `
  --solver-python "C:\path\to\python.exe"
```

### 提示输入文件缺少列

检查附件 1 的表头是否与程序要求完全一致，尤其注意空格、括号和列名是否被手动修改。

### 求解达到时间上限

本题规模较小，通常可在数秒内求得最优解。如果设置了较短的 `--time-limit`，可提高或移除该参数。程序只接受求解状态为最优的结果，不会把未证明最优的中间可行解写入提交文件。

### Excel 文件无法保存

如果输出文件正在 Excel 中打开，Windows 可能阻止程序覆盖它。关闭相应的 `result1_lp.xlsx` 或 `result1_milp.xlsx` 后重新运行即可。
