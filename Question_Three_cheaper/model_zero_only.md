# C题第三问：仅 0:00 预报策略模型

## 1. 模型定位

本模型是第三问的对照策略模型。每天只在 0:00 使用附件 3 的 0:00 光伏预报制定一次全天计划：

\[
G^0_t,\ C^0_t,\ D^0_t,\ Q^0_t,\ E^0_t.
\]

0:00 之后不再使用 6:00、12:00、18:00 的光伏预报，也不再根据已观测的当天实际负载和光伏重优化计划。全天执行 0:00 得到的计划，因此正常购电调整量为零。

该模型保留以下条件：

- 每日 0:00 决策；
- 使用附件 1 的电价和附件 3 的 0:00 光伏预报；
- 使用附件 2 的实际负载和实际光伏进行事后结算；
- 电池容量、功率、充放电互斥和跨日 SOC 连续性；
- 第三问不继承“0:00 与 24:00 储电量相同”约束；
- 计划购电不足时按 5 倍实时电价购买紧急电量；
- 总费用按计划购电费用和实际紧急购电费用结算。

## 2. 时间与单位

\[
t=0,\ldots,143
\]

表示 144 个连续的 10 分钟时段，时间步长为

\[
\Delta=\frac16\ \text{小时}.
\]

附件 2 的功率数据为 kW，转换为每时段电量：

\[
L^{\mathrm{act}}_{d,t}=\frac{L^{\mathrm{kW}}_{d,t}}6,
\qquad
PV^{\mathrm{act}}_{d,t}=\frac{PV^{\mathrm{kW}}_{d,t}}6.
\]

附件 3 的 0:00 预报为未来 24 个整点预报值 \(F_{d,0,j}\)，转换为每 10 分钟电量：

\[
\widehat{PV}_{d,0,t}
=
\frac{F_{d,0,j(t)}}6,
\qquad
j(t)=\left\lceil\frac{t+1}{6}\right\rceil.
\]

电池参数为

\[
E_{\min}=1200,\qquad
E_{\max}=10800,\qquad
B=\frac{5000}{6},
\]

\[
\eta_c=\eta_d=0.9.
\]

2025-01-01 的初始 SOC 为

\[
\operatorname{SOC}_{1,0}=6000\ \text{kWh}.
\]

## 3. 0:00 负载预测

对目标日 \(d\)，使用 \(h<d\) 的同类历史日形成日基线预测：

\[
\bar L_{d,t}
=
\sum_{h\in\mathcal K_d}
w_{d,h}L^{\mathrm{act}}_{h,t},
\]

\[
w_{d,h}
=
\frac{0.9^{d-h}}
{\sum_{v\in\mathcal K_d}0.9^{d-v}}.
\]

其中 \(\mathcal K_d\) 最多包含最近四个同类型历史日；同类型日期不足时，从其余历史日按最近优先补齐。

0:00 没有当天的实测偏置，因此

\[
\widehat L_{d,0,t}
=
\max\left(0,\bar L_{d,t}\right).
\]

## 4. 0:00 情景生成

对历史日 \(h<d\)，定义负载和光伏残差：

\[
e^L_{h,0,t}
=
L^{\mathrm{act}}_{h,t}
-
\widehat L_{h,0,t},
\]

\[
e^{PV}_{h,0,t}
=
PV^{\mathrm{act}}_{h,t}
-
\widehat{PV}_{h,0,t}.
\]

历史预测必须使用因果回放规则，只使用该历史日之前的可用资料。

从最近最多 60 个同类型历史日中选择代表日：

1. 计算累计光伏残差

\[
A_{h,0}
=
\sum_{t=0}^{143}e^{PV}_{h,0,t};
\]

2. 按 \(A_{h,0}\) 升序分出低、中、高三组；
3. 每组取中间名次的历史日作为代表日；
4. 组概率取组内历史日数量占总候选数量的比例。

如果历史日不足三天，则直接将可用历史日分别作为情景，概率为 \(1/N\)。若无历史日，则使用单一名义情景。

情景路径为

\[
L_{s,t}
=
\max\left(
0,\widehat L_{d,0,t}+e^L_{h_s,0,t}
\right),
\]

\[
PV_{s,t}
=
\max\left(
0,\widehat{PV}_{d,0,t}+e^{PV}_{h_s,0,t}
\right).
\]

情景概率满足

\[
\pi_s>0,\qquad
\sum_s\pi_s=1.
\]

## 5. 全天计划模型

### 5.1 变量

对每个时段 \(t=0,\ldots,143\)：

\[
G^0_t,\ C^0_t,\ D^0_t,\ Q^0_t,\ E^0_t\ge0,
\]

\[
z^0_t\in\{0,1\}.
\]

对每个情景 \(s\) 定义紧急购电量

\[
R^0_{s,t}\ge0.
\]

### 5.2 计划功率平衡

\[
G^0_t+\widehat{PV}_{d,0,t}+D^0_t
=
\widehat L_{d,0,t}+C^0_t+Q^0_t,
\]

\[
0\le Q^0_t\le\widehat{PV}_{d,0,t}.
\]

### 5.3 电池约束

SOC 边界状态满足

\[
E^0_0=\operatorname{SOC}_{d,0},
\]

\[
E^0_{t+1}
=
E^0_t+\eta_cC^0_t-\frac{D^0_t}{\eta_d}.
\]

容量和功率约束为

\[
E_{\min}\le E^0_t\le E_{\max},
\qquad t=0,\ldots,144,
\]

\[
0\le C^0_t\le Bz^0_t,
\]

\[
0\le D^0_t\le B(1-z^0_t).
\]

本模型不设置

\[
E^0_{144}=E^0_0
\]

的期末等式。日末 SOC 只受容量边界控制，并作为下一日初始 SOC：

\[
\operatorname{SOC}_{d+1,0}
=
\operatorname{SOC}_{d,144}.
\]

### 5.4 情景供电约束

\[
G^0_t+PV_{s,t}+D^0_t+R^0_{s,t}
\ge
L_{s,t}+C^0_t,
\qquad \forall s,t.
\]

在费用最小的解中，

\[
R^0_{s,t}
=
\max\left(
0,
L_{s,t}+C^0_t-G^0_t-PV_{s,t}-D^0_t
\right).
\]

### 5.5 风险目标

定义场景费用

\[
C^0_s
=
\sum_{t=0}^{143}
\left(
p_tG^0_t+5p_tR^0_{s,t}
\right).
\]

引入

\[
a_s\ge0,\qquad \zeta\in\mathbb R,
\]

\[
a_s\ge C^0_s-\zeta.
\]

则

\[
\operatorname{CVaR}_\alpha(C^0)
=
\zeta+
\frac1{1-\alpha}
\sum_s\pi_sa_s.
\]

主模型取

\[
\alpha=0.90,\qquad
\lambda_{\mathrm{risk}}=0.10.
\]

一级目标为

\[
F_1
=
\sum_s\pi_sC^0_s
+
\lambda_{\mathrm{risk}}
\operatorname{CVaR}_{0.90}(C^0).
\]

本模型只执行一次 0:00 计划，因此不存在 6:00、12:00、18:00 的调整偏差变量，也不需要建立日间调整费用的 MILP。

## 6. 全天实际执行

由于没有后续重优化，实际执行的正常购电量、充电量和放电量为

\[
g_t=G^0_t,\qquad
c_t=C^0_t,\qquad
d_t=D^0_t.
\]

实际 SOC 满足

\[
\operatorname{SOC}_{d,t+1}
=
\operatorname{SOC}_{d,t}
+\eta_cc_t
-\frac{d_t}{\eta_d},
\]

\[
\operatorname{SOC}_{d+1,0}
=
\operatorname{SOC}_{d,144}.
\]

实际供电缺口为

\[
r^{\mathrm{act}}_t
=
\max\left(
0,
L^{\mathrm{act}}_{d,t}
+c_t-g_t-PV^{\mathrm{act}}_{d,t}-d_t
\right).
\]

实际未利用电量为

\[
w^{\mathrm{act}}_t
=
\max\left(
0,
g_t+PV^{\mathrm{act}}_{d,t}+d_t-L^{\mathrm{act}}_{d,t}-c_t
\right).
\]

实际功率平衡为

\[
g_t+PV^{\mathrm{act}}_{d,t}+d_t+r^{\mathrm{act}}_t
=
L^{\mathrm{act}}_{d,t}+c_t+w^{\mathrm{act}}_t.
\]

## 7. 费用

因为没有日内调整量，

\[
u_t=v_t=0.
\]

因此

\[
C_{\mathrm{adj}}=0.
\]

计划购电费用为

\[
C_{\mathrm{plan}}
=
\sum_{t=0}^{143}p_tG^0_t.
\]

紧急购电费用为

\[
C_{\mathrm{em}}
=
\sum_{t=0}^{143}5p_t r^{\mathrm{act}}_t.
\]

总费用为

\[
C_{\mathrm{total}}
=
C_{\mathrm{plan}}+C_{\mathrm{em}}.
\]

## 8. 结果表映射

正式评价期为 2025-02-01 至 2025-12-31，共 334 天。

- 计划购电量：写入 \(G^0_t\)；
- 实际购电量：写入 \(g_t=G^0_t\)；
- 充放电量：写入实际执行的 \(c_t,d_t\) 和 SOC；
- 紧急购电量：写入 \(r^{\mathrm{act}}_t\)，合并连续时段；
- 调整费用为 0；
- `result3.xlsx` 的时段标题使用物理区间

\[
0:00\text{-}0:10,\ldots,23:50\text{-}0:00+1.
\]

## 9. 与四时点滚动模型的区别

| 项目 | 仅 0:00 预报模型 | 四时点滚动模型 |
|---|---|---|
| 决策次数 | 每天 1 次 | 每天 4 次 |
| 使用预报 | 仅 0:00 预报 | 0:00、6:00、12:00、18:00 |
| 调整变量 | 无 | 有 |
| 调整费用 | 0 | 有 |
| 紧急购电 | 较高 | 通过重优化降低 |
| SOC 期末约束 | 无 | 无 |
| 跨日 SOC | 连续 | 连续 |
