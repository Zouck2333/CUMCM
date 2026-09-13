# C 题第四问：去劣存优后的推荐模型

## 1. 模型定位

本模型融合以下两版模型：

- 附件 `model_Four.md`：价格信息边界清晰、价格预测因果、事前优化和结算闭环完整；
- 当前 `Question_Four_model_combination.md`：增加鲁棒价格带、终端 SOC 机会价值和更丰富的扩展模块。

合并后的主模型为：

> 因果价格预测下的四时点滚动随机 MPC + 负荷/光伏联合残差情景 + CVaR + 字典序多目标

对应结果：

- `result4-2.xlsx`：动态电价下每天 0:00 一次计划；
- `result4-3.xlsx`：动态电价下 0:00、6:00、12:00、18:00 滚动调整。

## 2. 信息边界

主模型采用实时可执行口径：

- 在每个 10 分钟时段开始前，已知该时段的实际电价；
- 在 0:00、6:00、12:00、18:00 求解时，已知已经发生的实际负载、实际光伏和已经公布的价格；
- 当前时段的实际价格可以用于当前求解；
- 未来价格只能使用由历史价格和已发生偏差形成的预测价格；
- 附件 4 的未来实际价格只用于事后结算，不进入当前计划；
- 附件 2 的未来实际负载和实际光伏只用于事后结算，不进入 0:00 或当前时点的计划。

若另行计算“完全预知价格基准”，应把优化中的预测价格替换为全天实际价格，并与实时可执行主结果分开报告。

## 3. 时间与单位

日期为 2025-01-01 至 2025-12-31，记为 \(d=1,\ldots,365\)。每一天有 144 个 10 分钟区间：

\[
t=0,\ldots,143,\qquad
\Delta=\frac16\ \text{小时}.
\]

四个决策时点定义为

\[
(\tau_0,\tau_1,\tau_2,\tau_3,\tau_4)
=
(0,36,72,108,144),
\]

\[
U_k=\{\tau_k,\ldots,143\},\qquad
H_k=\{\tau_k,\ldots,\tau_{k+1}-1\}.
\]

附件 2 的负载和光伏功率是 kW，必须先转换为每时段 kWh：

\[
L^{\mathrm{act}}_{d,t}
=
L^{\mathrm{kW}}_{d,t}\Delta,
\qquad
PV^{\mathrm{act}}_{d,t}
=
PV^{\mathrm{kW}}_{d,t}\Delta.
\]

附件 3 的整点预报映射到 10 分钟区间：

\[
j(k,t)
=
\left\lceil\frac{t-\tau_k+1}{6}\right\rceil,
\]

\[
\widehat{PV}_{d,k,t}
=
\Delta F_{d,k,j(k,t)},
\qquad t\in U_k.
\]

附件 4 的动态实际价格记为

\[
p^{\mathrm{act}}_{d,t}>0,
\]

单位为元/kWh。所有购电量、充放电量、紧急购电量和负载、光伏电量均以 kWh/时段计。

## 4. 附件 2 和附件 3 的因果预测

### 4.1 负载预测

工作日（周一至周五）和周末（周六、周日）作为两类。对目标日 d，先按日期由近到远选取 \(h<d\) 的同类历史日；不足 4 天时，再从其他历史日按日期由近到远补足，最终最多取 4 天，记为 \(\mathcal K_d\)。仅使用目标日之前的数据。按指数权重构造基线：

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

2025-01-01 无历史日时取 \(\bar L_{1,t}=0\)。

对于 k=0：

\[
\widehat L_{d,0,t}
=
\max(0,\bar L_{d,t}).
\]

对于 k>0，使用上一六小时已经观测到的负载偏差：

\[
b_{d,k}
=
\frac1{36}
\sum_{u=\tau_{k-1}}^{\tau_k-1}
\left(
L^{\mathrm{act}}_{d,u}-\bar L_{d,u}
\right),
\]

\[
\widehat L_{d,k,t}
=
\max\left(
0,\bar L_{d,t}+b_{d,k}
\right),
\qquad t\in U_k.
\]

### 4.2 光伏预测

对每个 k=0,1,2,3，直接使用附件 3 的预报：

\[
\widehat{PV}_{d,k,t}
=
\Delta F_{d,k,j(k,t)}.
\]

### 4.3 负荷与光伏联合残差情景

对每个历史日 h<d 和更新时点 k，按当时可得数据因果回放：

\[
e^L_{h,k,t}
=
L^{\mathrm{act}}_{h,t}
-
\widehat L_{h,k,t},
\]

\[
e^{PV}_{h,k,t}
=
PV^{\mathrm{act}}_{h,t}
-
\widehat{PV}_{h,k,t}.
\]

候选日先选 \(h<d\) 的同类历史日，再用其他更早的历史日补足，最多取最近 60 天。按剩余时段累计光伏残差高低划分低、中、高三组，每组取中间历史日作为代表；样本不足 3 天时，每个历史日各为一个情景。

对代表日 \(h_s\)，情景路径为

\[
L_{s,t}
=
\max\left(
0,\widehat L_{d,k,t}+e^L_{h_s,k,t}
\right),
\]

\[
PV_{s,t}
=
\max\left(
0,\widehat{PV}_{d,k,t}+e^{PV}_{h_s,k,t}
\right).
\]

基础概率记为 \(\pi_s^{(0)}\)。

当 \(N\ge3\) 时，按剩余时段累计光伏残差

\[
A_{h,k}
=
\sum_{t\in U_k}e^{PV}_{h,k,t}
\]

升序排列候选历史日，残差相同时按日期升序打破并列。排序名次为 \(r=0,\ldots,N-1\)，分组为

\[
s
=
1+\left\lfloor\frac{3r}{N}\right\rfloor.
\]

第 1、2、3 组分别表示光伏残差偏低、居中、偏高。每组取组内中间名次的历史日作为代表日；组内数量为偶数时取靠前名次。若第 s 组包含 \(N_s\) 个历史日，则

\[
\pi_s^{(0)}
=
\frac{N_s}{N},
\qquad s=1,2,3.
\]

因此 \(N\ge3\) 时共有 3 个代表情景，且

\[
\sum_{s=1}^3\pi_s^{(0)}=1.
\]

首先处理历史样本不足的情况：

- 若历史候选样本数 \(N=0\)，使用单一名义情景：
  \[
  L_{1,t}=\widehat L_{d,k,t},\qquad
  PV_{1,t}=\widehat{PV}_{d,k,t},\qquad
  \pi_1=1,
  \]
  并跳过前缀距离和概率加权。
- 若 \(N=1\)，该唯一历史日构成单情景，取 \(\pi_1=1\)。
- 若 \(N=2\)，两个历史日分别构成情景，基础概率为 \(1/2\)，并令前缀距离 \(D_{d,k,s}=0\)。
- 只有 \(N\ge3\) 时，才使用下面的前缀距离和加权概率。

定义光伏基线：

\[
\bar{PV}_{d,u}
=
\sum_{h\in\mathcal K_d}
w_{d,h}PV^{\mathrm{act}}_{h,u},
\qquad u<\tau_k.
\]

定义当前日和历史代表日的负载、光伏前缀偏差：

对 k>0，用当前已经观测到的负载、光伏前缀修正情景概率：

\[
x^L_{d,u}
=
L^{\mathrm{act}}_{d,u}
-
\bar L_{d,u},
\qquad
x^{PV}_{d,u}
=
PV^{\mathrm{act}}_{d,u}
-
\bar{PV}_{d,u},
\qquad u<\tau_k.
\]

\[
x^L_{h_s,u}
=
L^{\mathrm{act}}_{h_s,u}
-
\bar L_{h_s,u},
\qquad
x^{PV}_{h_s,u}
=
PV^{\mathrm{act}}_{h_s,u}
-
\bar{PV}_{h_s,u},
\qquad u<\tau_k.
\]

其中 \(\bar L_{h_s,u}\) 和 \(\bar{PV}_{h_s,u}\) 必须按历史日 \(h_s\) 当时可用的资料因果回放，不能使用目标日信息。

标准差定义为

\[
\sigma^L_u
=
\max\left(
1,
\operatorname{std}_{h\in\mathcal A_{d,k}}
\left(x^L_{h,u}\right)
\right),
\]

\[
\sigma^{PV}_u
=
\max\left(
1,
\operatorname{std}_{h\in\mathcal A_{d,k}}
\left(x^{PV}_{h,u}\right)
\right).
\]

其中 \(\mathcal A_{d,k}\) 是当前节点可用的历史候选集合。

定义联合距离

\[
D_{d,k,s}
=
\frac1{\tau_k}
\sum_{u=0}^{\tau_k-1}
\left[
\left(
\frac{x^L_{d,u}-x^L_{h_s,u}}
{\max(1,\sigma^L_u)}
\right)^2
+
\left(
\frac{x^{PV}_{d,u}-x^{PV}_{h_s,u}}
{\max(1,\sigma^{PV}_u)}
\right)^2
\right].
\]

当 \(k=0\) 时，不计算前缀距离，直接取

\[
q_s=\pi_s^{(0)},\qquad \pi_s=\pi_s^{(0)}.
\]

当 \(k>0\) 且 \(N<3\) 时，同样取基础概率，不进行前缀加权。

当 \(k>0\) 且 \(N\ge3\) 时，为避免指数下溢，采用对数域归一化。定义

\[
\ell_s
=
\log\left(\pi_s^{(0)}\right)
-
0.10D_{d,k,s},
\]

\[
\ell_{\max}
=
\max_j\ell_j.
\]

则

\[
q_s
=
\frac{
\exp\left(\ell_s-\ell_{\max}\right)
}
{
\sum_j
\exp\left(\ell_j-\ell_{\max}\right)
},
\]

\[
\pi_s
=
(1-10^{-6})q_s+10^{-6}\pi_s^{(0)}.
\]

这样所有情景概率严格为正，且只使用当前已经观测到的前缀。

## 5. 因果价格预测

### 5.1 价格基线

使用与负载相同的同类历史日权重，构造价格基线：

\[
\bar p_{d,t}
=
\sum_{h\in\mathcal K_d}
w_{d,h}p^{\mathrm{act}}_{h,t}.
\]

若 2025-01-01 没有历史价格，取

\[
\bar p_{1,t}=p^{\mathrm{act}}_{1,0}.
\]

### 5.2 当前价格偏置

对 k=0：

\[
\delta^p_{d,0}
=
p^{\mathrm{act}}_{d,0}
-
\bar p_{d,0}.
\]

对 k>0，使用刚结束的六小时价格误差中位数：

\[
\delta^p_{d,k}
=
\operatorname{median}_{u\in H_{k-1}}
\left(
p^{\mathrm{act}}_{d,u}
-
\bar p_{d,u}
\right).
\]

### 5.3 优化用价格

\[
\widetilde p_{d,k,t}
=
\begin{cases}
p^{\mathrm{act}}_{d,\tau_k}, & t=\tau_k,\\
\max\left(
\epsilon_p,
\bar p_{d,t}+\delta^p_{d,k}
\right), & t>\tau_k,
\end{cases}
\]

其中 \(\epsilon_p=10^{-6}\) 元/kWh。

主模型使用 \(\widetilde p_{d,k,t}\) 进行计划；实际结算统一使用 \(p^{\mathrm{act}}_{d,t}\)。两者的差值用于评价价格预测误差的代价。

## 6. 共用物理模型

对一次求解的每个 \(t\in U_k\)，定义

\[
G^k_t,\ C^k_t,\ D^k_t,\ Q^k_t,\ E^k_t,\ z^k_t,
\]

以及情景紧急购电 \(R^k_{s,t}\)。

定义域为

\[
G^k_t,C^k_t,D^k_t,Q^k_t,R^k_{s,t}\ge0,
\qquad z^k_t\in\{0,1\}.
\]

计划平衡为

\[
G^k_t+\widehat{PV}_{d,k,t}+D^k_t
=
\widehat L_{d,k,t}+C^k_t+Q^k_t,
\]

\[
0\le Q^k_t\le\widehat{PV}_{d,k,t}.
\]

SOC 递推为

\[
E^k_{\tau_k}=E^{\mathrm{act}}_{d,\tau_k},
\]

\[
E^k_{t+1}
=
E^k_t+\eta_cC^k_t-\frac{D^k_t}{\eta_d}.
\]

容量与功率约束为

\[
E_{\min}\le E^k_t\le E_{\max},
\]

\[
0\le C^k_t\le Bz^k_t,
\qquad
0\le D^k_t\le B(1-z^k_t),
\]

\[
B=5000\Delta.
\]

情景供电约束为

\[
G^k_t+PV_{s,t}+D^k_t+R^k_{s,t}
\ge
L_{s,t}+C^k_t.
\]

第四问不继承第一问的日末 SOC 等式约束，正式模型只保留

\[
E_{\min}\le E^k_{144}\le E_{\max}.
\]

实际 SOC 跨日为

\[
E^{\mathrm{act}}_{d+1,0}
=
E^{\mathrm{act}}_{d,144}.
\]

题目给定

\[
E^{\mathrm{act}}_{1,0}=6000\ \text{kWh}.
\]

1 月 1 日至 1 月 31 日作为预热期顺序运行，不写入正式结果；2 月 1 日初始 SOC 取 1 月 31 日的实际末 SOC。

## 7. 4-2：0:00 一次计划

在 0:00 求解整日模型，情景费用为

\[
J_s^{(4-2)}
=
\sum_{t=0}^{143}
\widetilde p_{d,0,t}
\left(
G_t+5R_{s,t}
\right).
\]

引入 CVaR 变量 \(a_s,\zeta\)：

\[
a_s\ge0,\qquad
a_s\ge J_s^{(4-2)}-\zeta.
\]

\[
\operatorname{CVaR}_\alpha(J^{(4-2)})
=
\zeta
+
\frac1{1-\alpha}
\sum_s\pi_sa_s.
\]

主推荐参数：

\[
\alpha=0.90,\qquad
\lambda_{\mathrm{risk}}=0.10.
\]

一级目标为

\[
F_1^{(4-2)}
=
\sum_s\pi_sJ_s^{(4-2)}
+
0.10
\operatorname{CVaR}_{0.90}(J^{(4-2)}).
\]

模型输出的 \(G,C,D\) 是全天固定计划。当天实际负载和实际光伏只在求解完成后用于结算：

\[
r^{\mathrm{act}}_{d,t}
=
\max\left(
0,
L^{\mathrm{act}}_{d,t}
+C_t-G_t-PV^{\mathrm{act}}_{d,t}-D_t
\right),
\]

\[
C_d^{(4-2),\mathrm{act}}
=
\sum_tp^{\mathrm{act}}_{d,t}G_t
+
\sum_t5p^{\mathrm{act}}_{d,t}r^{\mathrm{act}}_{d,t}.
\]

## 8. 4-3：四时点滚动调整

### 8.1 初始计划

0:00 求解与 4-2 相同的整日随机优化，得到初始计划 \(G^0_t\)。

### 8.2 调整量

对 k>0：

\[
G^k_t-G^0_t=u^k_t-v^k_t,
\]

\[
u^k_t,v^k_t\ge0.
\]

用二元变量 \(b^k_t\) 保证二者不同时为正：

\[
u^k_t
\le
\max\left(
0,\widehat L_{d,k,t}+B-G^0_t
\right)b^k_t,
\]

\[
v^k_t
\le
G^0_t(1-b^k_t).
\]

### 8.3 当前剩余时段情景费用

\[
J_s^{(4-3),k}
=
\sum_{t\in U_k}
\widetilde p_{d,k,t}
\left[
G^0_t
+1.5u^k_t
+0.5v^k_t
+5R^k_{s,t}
\right].
\]

对 \(J_s^{(4-3),k}\) 使用相同的 CVaR 和一级风险费用目标。

### 8.4 实际结算

对滚动策略，令

\[
k(t)=\left\lfloor\frac{t}{36}\right\rfloor,
\]

并定义最终执行量为

\[
g_t=G^{k(t)}_t,\qquad
c_t=C^{k(t)}_t,\qquad
d_t=D^{k(t)}_t.
\]

实际 SOC 按执行路径递推：

\[
E^{\mathrm{act}}_{d,t+1}
=
E^{\mathrm{act}}_{d,t}
+\eta_c c_t
-\frac{d_t}{\eta_d}.
\]

实际紧急购电量为

\[
r^{\mathrm{act}}_{d,t}
=
\max\left(
0,
L^{\mathrm{act}}_{d,t}
+c_t
-g_t
-PV^{\mathrm{act}}_{d,t}
-d_t
\right).
\]

因此 4-3 的费用必须使用这组 \(g_t,c_t,d_t\) 计算，不能直接套用 4-2 中固定的 \(G_t,C_t,D_t\) 所对应的缺口。

定义

\[
u_t=(g_t-G^0_t)^+,\qquad
v_t=(G^0_t-g_t)^+.
\]

实际结算为

\[
C_d^{(4-3),\mathrm{act}}
=
\sum_t p^{\mathrm{act}}_{d,t}
\left[
G^0_t
+1.5u_t
+0.5v_t
+5r^{\mathrm{act}}_{d,t}
\right].
\]

结算解释必须写清：

- 初始计划量 \(G^0_t\) 仍按实际价格全额付款；
- 上调部分 \(u_t\) 按 \(1.5p^{\mathrm{act}}_{d,t}\) 付费；
- 下调部分 \(v_t\) 除原计划费用外，另按 \(0.5p^{\mathrm{act}}_{d,t}\) 支付违约费用；
- 中间多次临时计划不重复收费，只按最终执行量和初始计划比较一次；
- 紧急购电量按 \(5p^{\mathrm{act}}_{d,t}\) 单独结算。

### 8.5 滚动执行

在 0:00 求解并执行 H0；在 6:00、12:00、18:00 使用新观测值和新价格预测重新求解，只执行 H1、H2、H3。实际 SOC 按执行路径递推，1 月作为预热期，2 月 1 日继承 1 月 31 日实际末 SOC。

## 9. 字典序优化

主模型采用以下顺序：

1. 最小化风险调整费用 \(F_1\)；
2. 在
   \[
   F_1\le F_1^*+\varepsilon_1
   \]
   下最小化电池吞吐量
   \[
   F_2=\sum_{t\in U_k}(C^k_t+D^k_t);
   \]
3. 再在
   \[
   F_2\le F_2^*+\varepsilon_2
   \]
   下最小化峰值购电功率
   \[
   F_3=P^{\mathrm{peak}},
   \]

\[
P^{\mathrm{peak}}
\ge
\frac{G^k_t+R^k_{s,t}}{\Delta}.
\]

容差只用于处理求解器数值误差，不允许为了降低峰值而实质牺牲主费用。

具体取值为

\[
\varepsilon_1
=
\max\left(
10^{-4},
10^{-7}|F_1^*|
\right)
\ \text{元},
\]

\[
\varepsilon_2
=
\max\left(
10^{-6},
10^{-7}|F_2^*|
\right)
\ \text{kWh}.
\]

每次求解记录第一阶段可行值、对偶界或 MIP gap；若第一阶段未证明最优，不把后续字典序结果声称为严格最优解。

## 10. 电池机会成本与终端价值

主模型正式结果保持每日 24:00 边界，不额外强制日末储备或终端价值，以对应题目第二问和第三问的原始边界。以下实验各自从 1 月 1 日的 6000 kWh 初始 SOC 独立顺序重跑，不能沿用主模型或其他实验的跨日 SOC。

### 10.1 历史安全储备

对 \(d<365\)，先选 \(h<d\) 中与次日 \(d+1\) 同类型的最近历史日，再按日期由近到远补入其他历史日，最多取 60 天，记为 \(\mathcal H_d^+\)。历史日 \(h\) 的 0:00 预测残差已经由第 4.3 节的因果回放定义。次日清晨六小时的净正缺口样本为

\[
Z_h
=
\sum_{u=0}^{35}
\max\left(0,e^L_{h,0,u}-e^{PV}_{h,0,u}\right),
\qquad h\in\mathcal H_d^+.
\]

使用线性插值的经验分位数 \(Q^{\mathrm{linear}}_{0.90}\)，定义固定于当日 0:00 的安全储备

\[
r_d
=
\min\left(
E_{\max}-E_{\min},
\frac{Q^{\mathrm{linear}}_{0.90}
(\{Z_h:h\in\mathcal H_d^+\})}{\eta_d}
\right),
\qquad
E^k_{144}\ge E_{\min}+r_d.
\]

若 \(\mathcal H_d^+\) 为空或 \(d=365\)，取 \(r_d=0\)。四次求解使用同一个 \(r_d\)，不引入当前日尚未发生的负载或光伏。可独立比较无储备、0.75 和 0.90 分位数方案；0.75 方案只替换上式的分位数下标。

### 10.2 预测价格终端价值

终端价值只用于 \(d<365\) 且 \(\mathcal H_d^+\) 非空的实验。以历史权重

\[
\omega^+_{d,h}
=
\frac{0.9^{d-h}}
{\sum_{v\in\mathcal H_d^+}0.9^{d-v}},
\qquad h\in\mathcal H_d^+,
\]

估计次日同类时段的清晨价格基线：

\[
\bar p^+_{d,u}
=
\sum_{h\in\mathcal H_d^+}
\omega^+_{d,h}p^{\mathrm{act}}_{h,u},
\qquad u=0,\ldots,35.
\]

第 \(k\) 次求解仅用已经观测到的当前日价格偏置 \(\delta^p_{d,k}\)，得到

\[
\widetilde p^{\mathrm{term}}_{d,k}
=
\frac1{36}
\sum_{u=0}^{35}
\max\left(\epsilon_p,\bar p^+_{d,u}+\delta^p_{d,k}\right),
\]

\[
\Phi_{d,k}(E^k_{144})
=
\eta_d\left(E^k_{144}-E_{\min}\right)
\widetilde p^{\mathrm{term}}_{d,k}.
\]

若 \(\mathcal H_d^+\) 为空或 \(d=365\)，直接取 \(\Phi_{d,k}=0\)，不引用未定义的 \(\widetilde p_{d,k,u}\) 早间时段。该价格只是对次日潜在放电价值的代理，不使用当前日未来或次日实际价格。

在一级目标中使用 \(F_1^\chi=F_1-\chi\Phi_{d,k}(E^k_{144})\)，分别测试 \(\chi=0,0.5,1\)；字典序后续目标锁定的是 \(F_1^\chi\) 的最优值。终端价值不加入情景费用 \(J_s\)，也不计为实际结算收入。正式主结果保持 \(\chi=0\)。

## 11. 价格带实验与鲁棒重优化

价格不确定性只作为独立扩展。对每个目标日，选取 \(h<d\) 中最近最多 60 个同类型历史日；不足 60 天时用其他更早历史日补足。记此历史集合为 \(\mathcal H_d^p\)。每个时段的 0.10、0.90 经验价格分位数 \(q_{0.10,d,t}\)、\(q_{0.90,d,t}\) 均采用线性插值；没有历史日时，不构造价格波动区间。

### 11.1 决策时点的价格不确定集合

对第 \(k\) 次优化，令中心价 \(m_{d,k,t}=\widetilde p_{d,k,t}\)，仅定义于 \(t\in U_k\)。当前时段 \(t=\tau_k\) 的价格已经公布，令

\[
\underline p_{d,k,\tau_k}
=
\overline p_{d,k,\tau_k}
=
m_{d,k,\tau_k}
=
p^{\mathrm{act}}_{d,\tau_k}.
\]

对未来 \(t>\tau_k\)，若 \(\mathcal H_d^p\) 非空，则定义

\[
\underline p_{d,k,t}
=
\max\left(
\epsilon_p,
\min(m_{d,k,t},q_{0.10,d,t})
\right),
\qquad
\overline p_{d,k,t}
=
\max(m_{d,k,t},q_{0.90,d,t}).
\]

若没有历史日，令两端都等于 \(m_{d,k,t}\)。这样价格严格为正，中心价位于价格带内，零宽度价格带也允许存在。用预算变量表示可选价格路径：

\[
p_{d,t}
=
m_{d,k,t}
+(\overline p_{d,k,t}-m_{d,k,t})z^+_{d,t}
-(m_{d,k,t}-\underline p_{d,k,t})z^-_{d,t},
\qquad t\in U_k,
\]

\[
z^+_{d,t},z^-_{d,t}\ge0,\qquad
z^+_{d,t}+z^-_{d,t}\le1,\qquad
\sum_{t\in U_k}(z^+_{d,t}+z^-_{d,t})\le\Gamma,\qquad
z^+_{d,\tau_k}=z^-_{d,\tau_k}=0.
\]

由此得到 \(\mathcal P_{d,k,\Gamma}\)。无需除以价格带宽度；带宽为零的时段可以直接令 \(z^+_{d,t}=z^-_{d,t}=0\)。当 \(\Gamma=0\) 时仅有预测中心价。比较 \(\Gamma=0,6,12,24\)，并仅对决策时尚未知价的时段报告实际价格落入经验价格带的比例；超出价格带时，最坏费用不是实际费用的保证上界。

### 11.2 固定执行轨迹的价格压力测试

这是直接可编码的价格敏感性实验，不重新优化购电或储能。4-2 对全日取 \(m_{d,t}^{(4-2)}=\widetilde p_{d,0,t}\)；4-3 对 \(t\in H_k\) 取 \(m_{d,t}^{(4-3)}=\widetilde p_{d,k,t}\)。对两种策略分别按第 11.1 节方法构造全日价格集合 \(\mathcal P^{(m),\mathrm{exe}}_{d,\Gamma}\)：4-2 仅在 \(t=0\) 固定已知价格，4-3 在 \(t=0,36,72,108\) 固定当时已知价格；其余时段使用相应执行时点的历史分位数与预测中心价。

固定第 7、8 节已经实际执行的轨迹，令

\[
a^{(4-2)}_{d,t}
=
G_t+5r^{\mathrm{act}}_{d,t},
\qquad
a^{(4-3)}_{d,t}
=
G^0_t+1.5u_t+0.5v_t+5r^{\mathrm{act}}_{d,t}.
\]

压力测试值为线性规划

\[
W^{(m)}_{d,\Gamma}
=
\max_{p\in\mathcal P^{(m),\mathrm{exe}}_{d,\Gamma}}
\sum_{t=0}^{143}p_{d,t}a^{(m)}_{d,t},
\qquad m\in\{4\text{-}2,4\text{-}3\}.
\]

其中 \(r^{\mathrm{act}}_{d,t}\) 是已执行轨迹的实际紧急购电，不是情景变量。\(W\) 只能称为固定轨迹上的价格压力测试，不能称为鲁棒重优化后的运行费用。

### 11.3 可选的鲁棒重优化

若需重新优化调度，保留第 6 至 8 节的全部物理约束、情景概率和调整变量。记相应可行域为 \(\mathcal X^{(m)}_{d,k}\)，决策 \(x\) 包括购电、充放电、SOC、情景紧急购电以及 4-3 的 \(u^k_t,v^k_t\)；4-3 的初始计划 \(G^0_t\) 在后续 \(k>0\) 求解时保持固定。4-2 仅在 \(k=0\) 求解。对给定价格路径，定义

\[
J_s^{(4-2)}(x,p)
=
\sum_{t=0}^{143}
p_{d,t}(G_t+5R_{s,t}),
\]

\[
J_s^{(4-3),k}(x,p)
=
\sum_{t\in U_k}
p_{d,t}
\left(G^0_t+1.5u^k_t+0.5v^k_t+5R^k_{s,t}\right).
\]

对两种模型均以相应 \(J_s^{(m),k}(x,p)\) 定义鲁棒一级目标：

\[
\min_{x\in\mathcal X^{(m)}_{d,k}}
\max_{p\in\mathcal P_{d,k,\Gamma}}
\left[
\sum_s\pi_sJ_s^{(m),k}(x,p)
+\lambda_{\mathrm{risk}}
\operatorname{CVaR}_{\alpha}
\left(\{J_s^{(m),k}(x,p)\}_s;\pi\right)
\right],
\]

其中 \(\alpha=0.90\)、\(\lambda_{\mathrm{risk}}=0.10\)。CVaR 对同一价格路径下的所有情景费用求取，不留自由的情景下标。计算时可利用

\[
\operatorname{CVaR}_{\alpha}(J;\pi)
=
\max_{\omega}
\sum_s\omega_sJ_s,
\qquad
\sum_s\omega_s=1,\quad
0\le\omega_s\le\frac{\pi_s}{1-\alpha},
\]

枚举少量情景权重极点，并对每个权重点分离最坏价格路径，采用列与约束生成求解。若未实现这一重优化，只报告第 11.2 节压力测试，不把它称为鲁棒最优解。鲁棒方案必须从年初独立顺序重跑；\(\Gamma=0\) 应退化为对应的确定性主模型。正式结果文件仍使用主模型。

## 12. 结果文件映射

### 12.1 result4-2.xlsx

计划购电量表：

- B:EO 按 \(t=0,\ldots,143\) 写入 \(G_t\)；
- EP 写入 \(\sum_tG_t\)；
- EQ 写入计划费用 \(\sum_tp^{\mathrm{act}}_{d,t}G_t\)；
- 时段标题按 0:00-0:10 至 23:50-0:00+1 连续排列。

充放电量表：

- 每一天占连续 6 行；
- A 列仅在每天第一行写日期；
- B 列依次写 0:00-4:00、4:00-8:00、8:00-12:00、12:00-16:00、16:00-20:00、20:00-24:00；
- C 列写每个四小时区间的充电量；
- D 列写每个四小时区间的放电量；
- E/F 在每天第一行写 0:00 和日初 SOC，在第二行写 24:00 和日末 SOC；
- 其余 E/F 留空。

紧急购电量表：

- 仅写 \(r^{\mathrm{act}}_{d,t}>10^{-8}\) 的连续区间；
- 合并相邻 10 分钟区间；
- A 列写日期，同日后续行留空；
- B 列写起止时刻；
- C 列写合并区间紧急购电量；
- 无紧急购电的日期保留一行日期，B/C 留空。

### 12.2 result4-3.xlsx

计划购电量表：

- B:EO 写入 0:00 初始计划 \(G^0_t\)；
- EP 写入 \(\sum_tG^0_t\)；
- EQ 写入初始计划费用 \(\sum_tp^{\mathrm{act}}_{d,t}G^0_t\)。

调整购电量表：

- B:EO 写入最终执行量 \(g_t\)；
- EP 写入 \(\sum_tg_t\)，不含紧急购电；
- EQ 写入
  \[
  C_d^{(4-3),\mathrm{act}};
  \]

充放电量表：

- 结构同 result4-2；
- C/D 写最终实际执行 \(c_t,d_t\) 的四小时汇总；
- E/F 写实际日初和日末 SOC。

紧急购电量表：

- 使用最终实际执行 \(g_t,c_t,d_t\) 和附件 2 实际负载、光伏计算 \(r^{\mathrm{act}}_{d,t}\)；
- 连续区间和汇总方式同 result4-2。

正式结果只写 2025-02-01 至 2025-12-31；1 月只用于预热和跨日 SOC 传递。

## 13. 推荐参数

| 参数 | 推荐值 |
|---|---:|
| 充放电效率 | \(\eta_c=\eta_d=0.9\) |
| SOC 范围 | 1200–10800 kWh |
| 充放电功率 | 5000 kW |
| 更新时点 | 0:00、6:00、12:00、18:00 |
| 情景数 | 3 |
| CVaR 置信水平 | 0.90 |
| CVaR 权重 | 0.10 |
| 前缀权重 | 0.10 |
| 主尺度下终端价值 | \(\chi=0\) |
| 跨日 SOC | \(\operatorname{SOC}_{d+1,0}=\operatorname{SOC}_{d,144}\) |

## 14. 验证指标

- 附件 2、4 的 144 个右端点与模型时段一一对应；
- 所有 kW 输入均乘以 \(\Delta\) 后进入模型；
- 未来实际负载、光伏和价格不能改变其发生时点之前的计划；
- 计划平衡、实际平衡、\(G_t\ge0\)、充放电互斥和 SOC 边界全部成立；
- 价格预测与完全预知价格基准的费用差单独报告；
- 终端储备和终端价值的灵敏度结果不与主结果混写；
- `result4-2.xlsx`、`result4-3.xlsx` 与逐时明细、费用明细一致。
