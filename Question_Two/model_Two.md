# C题第二问最终优化模型

## 1. 最终模型定位

最终主模型采用：

> 月度滚动验证预测、联合历史残差情景、日前随机情景 MILP、实际数据结算和 SOC 逐日更新。

主模型不使用以下内容：

- 当天未来实际负载和实际光伏。
- 全年完美信息 LP 的 SOC 边界或影子价格。
- 未定义的价值函数。
- 两日前瞻。

全年完美信息 LP 和 MILP 只作为事后理论基准。

## 2. 信息边界

第 \(d\) 天制定计划时可使用：

\[
\mathcal I_d=
\left\{
p_t,\ 
L_{k,t}^{act},\ 
PV_{k,t}^{act},\ 
E_{d,0}^{act}
\ \middle|\ k<d
\right\}
\]

禁止使用：

\[
L_{d,t}^{act},\quad PV_{d,t}^{act}
\]

以及任意：

\[
L_{k,t}^{act},\ PV_{k,t}^{act},\quad k>d
\]

所有预测、调参、情景、分位数和安全储备必须满足：

\[
\max(\text{使用数据日期})<d
\]

## 3. 时间尺度

\[
d=1,\ldots,365,\qquad t=1,\ldots,144
\]

\[
\Delta t=\frac{1}{6}\text{ h}
\]

功率转电量：

\[
L_{d,t}=L_{d,t}^{kW}\Delta t
\]

\[
PV_{d,t}=PV_{d,t}^{kW}\Delta t
\]

后续负载、光伏、购电、充放电和紧急购电均以 kWh 为单位。

日内电价 (p_t) 取自附件1，并按照题意在2025年每天重复使用。

### 3.1 储能与风险参数

| 参数 | 含义 | 主模型取值 |
|---|---|---:|
| \(E_{min}\) | 最低储电量 | 1200 kWh |
| \(E_{max}\) | 最高储电量 | 10800 kWh |
| \(E_{1,0}\) | 2025年1月1日0:00储电量 | 6000 kWh |
| \(P_{max}\) | 最大充、放电功率 | 5000 kW |
| \(B=P_{max}\Delta t\) | 单时段最大充、放电量 | 833.3333 kWh |
| \(\eta_c\) | 充电效率 | 0.9 |
| \(\eta_d\) | 放电效率 | 0.9 |
| \(\alpha\) | 紧急购电价格倍数 | 5 |
| \(S_{max}\) | 最大历史情景数 | 14 |
| \(H_v\) | 月度验证窗口 | 14天 |

主模型将题目中的90%解释为充电效率和放电效率分别为0.9。若将90%解释为往返效率，则在灵敏度分析中另取：

\[
\eta_c=\eta_d=\sqrt{0.9}\approx0.9487.
\]

两种效率口径分别求解，不在同一次计算中混用。

## 4. 训练和调参窗口

### 4.1 1 月预热期

2025 年 1 月采用固定的保守超参数进行顺序预热，只用于：

- 初始化滚动过程。
- 形成 2 月 1 日实际 SOC。
- 建立第一版历史残差库。
- 为 2 月模型提供训练样本。

一月份形成的SOC轨迹只用于确定2月1日0:00的初始SOC；一月份的购电费用、紧急购电量、预测误差和调度结果均不纳入2月至12月的正式评价指标，也不写入 `result2.xlsx`。一月份实际数据仍可在时间边界允许的条件下用于二月份预测、滚动验证和残差情景生成。

1 月使用固定默认超参数：

\[
\Theta_0=
\{K_L^0,K_{PV}^0,\rho_L^0,\rho_{PV}^0\}
\]

主模型取：

\[
\Theta_0=\{4,7,1.0,1.0\}.
\]

当可用历史日期少于给定窗口时，使用当时全部可用历史日期。

1 月 1 日使用附件 1 中某日的负载和光伏预测曲线作为冷启动先验。该曲线不视为2025年1月1日实测值，也不预设其为全年典型日。

1 月 2 日开始，只能使用已经出现的历史实际数据更新预测。

### 4.2 月度滚动验证

设月份 \(m\) 的第一天为 \(d_m\)，在月初比较以下候选参数：

\[
K_L\in\{2,4,6,8\},\qquad
K_{PV}\in\{3,5,7,10\},
\]

\[
\rho_L,\rho_{PV}\in\{0.80,0.90,0.95,1.00\}.
\]

验证日期为月初以前最近 \(H_v=14\) 天：

\[
\mathcal V_m=\{d_m-H_v,\ldots,d_m-1\}.
\]

对每个候选参数和每个验证日 \(v\in\mathcal V_m\)，必须只使用 \(k<v\) 的数据重新预测日期 \(v\)。验证日自身不能进入该日预测的历史样本。

负载和光伏的参数分别选择，避免两类数据因数值尺度不同而互相干扰。对变量 \(Y\in\{L,PV\}\)，定义验证尺度：

\[
s_{Y,m}=
\max\left(
\varepsilon_s,
\frac1{144|\mathcal V_m|}
\sum_{v\in\mathcal V_m}\sum_{t=1}^{144}|Y_{v,t}^{act}|
\right),
\]

其中 \(\varepsilon_s=10^{-8}\text{ kWh}\)。归一化验证指标为：

\[
NMAE_{Y,m}=\frac{MAE_{Y,m}}{s_{Y,m}},
\qquad
NRMSE_{Y,m}=\frac{RMSE_{Y,m}}{s_{Y,m}}.
\]

分别选择负载参数和光伏参数：

\[
(K_L,\rho_L)_m
=
\arg\min_{K_L,\rho_L}
\left[NMAE_{L,m}+0.2NRMSE_{L,m}\right],
\]

\[
(K_{PV},\rho_{PV})_m
=
\arg\min_{K_{PV},\rho_{PV}}
\left[NMAE_{PV,m}+0.2NRMSE_{PV,m}\right].
\]

最终组合为：

\[
\Theta_m=
\{(K_L,\rho_L)_m,(K_{PV},\rho_{PV})_m\}.
\]

参数确定后，在当月保持不变：

\[
\Theta_d=\Theta_m,\qquad d\in m.
\]

若可用验证日期不足14天，则使用全部可用日期；若样本不足以比较候选参数，则沿用上月参数。禁止使用当前月份当天或未来日期的实际值调参。

## 5. 历史相似日选择

### 5.1 候选日期

将日期分为工作日和非工作日。对目标日 \(d\)，首先构造同类型历史候选集：

\[
\mathcal C_d=
\{k:2\le k<d,\ type(k)=type(d)\}.
\]

从 \(k=2\) 开始，是因为历史候选日的特征需要使用日期 \(k-1\) 的实际数据。若同类型候选日不足，则将所有满足 \(2\le k<d\) 的日期加入候选集。1月初仍无有效候选日期时，按照第4.1节的冷启动规则预测。

### 5.2 计划日0:00已知的特征

记星期序号为 \(w_d\in\{1,\ldots,7\}\)，年内日序号为 \(r_d\in\{1,\ldots,365\}\)。目标日特征向量定义为：

\[
\begin{aligned}
x_d=\bigg[&
\sin\left(\frac{2\pi w_d}{7}\right),
\cos\left(\frac{2\pi w_d}{7}\right),
\sin\left(\frac{2\pi r_d}{365}\right),
\cos\left(\frac{2\pi r_d}{365}\right),\\
&\overline L_{d-1},\ L_{d-1}^{max},\
PV_{d-1}^{sum},\ PV_{d-1}^{max}
\bigg].
\end{aligned}
\]

其中：

\[
\overline L_{d-1}=\frac1{144}\sum_{t=1}^{144}L_{d-1,t}^{act},
\qquad
L_{d-1}^{max}=\max_tL_{d-1,t}^{act},
\]

\[
PV_{d-1}^{sum}=\sum_{t=1}^{144}PV_{d-1,t}^{act},
\qquad
PV_{d-1}^{max}=\max_tPV_{d-1,t}^{act}.
\]

历史候选日 \(k\) 的特征 \(x_k\) 使用相同定义，其中滞后统计量来自日期 \(k-1\)。星期和季节采用周期编码，避免把星期日与星期一、12月与1月错误地视为距离最远。

以上特征在目标日 \(d\) 的0:00均已知。禁止在 \(x_d\) 中加入目标日实际负载、实际光伏、实际峰值或实际日总量。

### 5.3 标准化距离

设特征维数为 \(J=8\)。根据当天可用候选集计算各特征的标准差：

\[
\sigma_{d,j}=
\sqrt{
\frac1{|\mathcal C_d|}
\sum_{k\in\mathcal C_d}
\left(x_{k,j}-\overline x_{d,j}\right)^2
},
\]

其中：

\[
\overline x_{d,j}=
\frac1{|\mathcal C_d|}
\sum_{k\in\mathcal C_d}x_{k,j}.
\]

定义当天的有效特征集合：

\[
\mathcal J_d=
\{j:\sigma_{d,j}>\varepsilon_D\},
\qquad
\varepsilon_D=10^{-8}.
\]

零方差特征不参与当天的距离计算。将基础权重在有效特征集合上重新归一化：

\[
\widetilde\omega_{d,j}
=
\frac{\omega_j}
{\sum_{h\in\mathcal J_d}\omega_h},
\qquad j\in\mathcal J_d.
\]

当 \(\mathcal J_d\ne\varnothing\) 时，目标日 \(d\) 与候选日 \(k\) 的标准化距离定义为：

\[
D(d,k)=
\sum_{j\in\mathcal J_d}
\widetilde\omega_{d,j}
\left(
\frac{x_{d,j}-x_{k,j}}
{\sigma_{d,j}}
\right)^2,
\]

其中主模型取：

\[
\omega_j=\frac1J,\qquad
\sum_{j=1}^{J}\omega_j=1.
\]

若 \(\mathcal J_d=\varnothing\)，所有候选特征均无变化，模型直接按照日期由近到远选择历史日。等权重作为主模型固定口径，其他权重仅用于灵敏度分析。

### 5.4 相似日集合

按照 \(D(d,k)\) 从小到大排序，分别选择前 \(K_L\) 个和前 \(K_{PV}\) 个候选日期：

\[
\mathcal K_d^L=
\operatorname{TopK}_{k\in\mathcal C_d}^{min}
\left(D(d,k),K_L\right),
\]

\[
\mathcal K_d^{PV}=
\operatorname{TopK}_{k\in\mathcal C_d}^{min}
\left(D(d,k),K_{PV}\right).
\]

候选数小于 \(K_L\) 或 \(K_{PV}\) 时使用全部候选日期。距离相同时优先选择距目标日更近的日期。

## 6. 负载与光伏预测

### 6.1 负载预测

\[
\widehat L_{d,t}
=
\sum_{k\in\mathcal K_d^L}
w_{d,k}^L L_{k,t}^{act}
\]

\[
w_{d,k}^L
=
\frac{\rho_L^{d-k}}
{\sum_{j\in\mathcal K_d^L}\rho_L^{d-j}}
\]

### 6.2 光伏预测

\[
\widehat{PV}_{d,t}
=
\max\left(
0,
\sum_{k\in\mathcal K_d^{PV}}
w_{d,k}^{PV}PV_{k,t}^{act}
\right)
\]

## 7. 滚动残差库

历史日期 \(k\) 的预测必须只使用：

\[
\mathcal I_k=
\left\{
p_t,\ 
L_{j,t}^{act},\ 
PV_{j,t}^{act}
\ \middle|\ j<k
\right\}
\]

\[
\widehat L_{k,t}=f_L(\mathcal I_k)
\]

\[
\widehat{PV}_{k,t}=f_{PV}(\mathcal I_k)
\]

残差：

\[
e_{k,t}^{L}=L_{k,t}^{act}-\widehat L_{k,t}
\]

\[
e_{k,t}^{PV}=PV_{k,t}^{act}-\widehat{PV}_{k,t}
\]

第 \(d\) 天只能使用：

\[
k<d
\]

预测残差库不能由全年单一模型统一生成。

## 8. 情景生成

记第 \(d\) 天可用的全部历史残差日期集合及同类型子集分别为：

\[
\mathcal A_d=\{k:k<d,\ k\text{存在完整滚动残差}\},
\]

\[
\mathcal A_d^{same}=\{k\in\mathcal A_d:type(k)=type(d)\}.
\]

首先从 \(\mathcal A_d^{same}\) 中按日期由近到远选取最多 \(S_{max}\) 个残差日，得到初始情景日期集合 \(\mathcal S_d^{(0)}\)。若：

\[
|\mathcal S_d^{(0)}|<\min(5,|\mathcal A_d|),
\]

则从 \(\mathcal A_d\setminus\mathcal S_d^{(0)}\) 中按日期由近到远补充，直到达到 \(\min(5,|\mathcal A_d|)\) 个情景。补充后的最终情景日期集合记为 \(\mathcal S_d\)，每日实际情景数定义为：

\[
S_d=|\mathcal S_d|.
\]

每个情景的负载与光伏残差必须来自 \(\mathcal S_d\) 中的同一个历史日期，以保留两类误差的相关性和144个时段的日内连续性。对 \(k_s\in\mathcal S_d\)，第 \(s\) 个情景为：

\[
L_{d,t,s}
=
\max\left(
0,
\widehat L_{d,t}+e_{k_s,t}^{L}
\right)
\]

\[
PV_{d,t,s}
=
\max\left(
0,
\widehat{PV}_{d,t}+e_{k_s,t}^{PV}
\right)
\]

其中情景来源日期必须满足 \(k_s<d\)。

情景概率：

\[
\pi_{d,s}\ge0,\qquad
\sum_{s=1}^{S_d}\pi_{d,s}=1
\]

基础模型使用等概率：

\[
\pi_{d,s}=\frac{1}{S_d}
\]

主模型不增加人为压力情景，避免引入未经滚动验证的情景概率。压力情景只作为独立的灵敏度扩展，不进入正式主结果。

冷启动阶段尚无可用残差时，设置一个确定性情景：

\[
S_d=1,\qquad
L_{d,t,1}=\widehat L_{d,t},\qquad
PV_{d,t,1}=\widehat{PV}_{d,t},\qquad
\pi_{d,1}=1.
\]

## 9. 日前 MILP 主模型

### 9.1 决策变量

| 变量 | 含义 | 定义域 |
|---|---|---|
| \(G_{d,t}\) | 正常计划购电量 | \(G_{d,t}\ge0\) |
| \(C_{d,t}\) | 计划充电量 | \(C_{d,t}\ge0\) |
| \(D_{d,t}\) | 计划放电量 | \(D_{d,t}\ge0\) |
| \(Q_{d,t}\) | 点预测下未利用光伏电量 | \(Q_{d,t}\ge0\) |
| \(E_{d,t}\) | 时段结束SOC | 连续变量 |
| \(z_{d,t}\) | 充放电模式 | 二进制变量 |

以上日前变量对全部情景相同。

### 9.2 预测条件下的不失供约束

\[
G_{d,t}+\widehat{PV}_{d,t}+D_{d,t}
=
\widehat L_{d,t}+C_{d,t}+Q_{d,t}
\]

\[
0\le Q_{d,t}\le\widehat{PV}_{d,t}
\]

该约束中不出现紧急购电。日前计划必须使用预测数据满足负载。

### 9.3 SOC递推

\[
E_{d,t}
=
E_{d,t-1}
+
\eta_cC_{d,t}
-
\frac{D_{d,t}}{\eta_d}
\]

初始条件：

\[
E_{d,0}=E_{d-1,144}^{act}
\]

\[
E_{1,0}=6000
\]

### 9.4 容量和功率约束

\[
1200\le E_{d,t}\le10800
\]

\[
B=5000\Delta t=\frac{5000}{6}=833.3333
\]

### 9.5 充放电互斥

\[
0\le C_{d,t}\le Bz_{d,t}
\]

\[
0\le D_{d,t}\le B(1-z_{d,t})
\]

\[
z_{d,t}\in\{0,1\}
\]

### 9.6 跨日SOC安全储备

\[
E_{d,144}\ge E_{min}+E_d^{res}
\]

\[
E_d^{res}
=
\min\left(
E_{max}-E_{min},
\frac{1}{\eta_d}
q_{0.9}
\left(
\left\{
\max_{\tau}
\sum_{t=1}^{\tau}
\max(0,e_{k,t}^{L}-e_{k,t}^{PV})
\ \middle|\ k<d
\right\}
\right)
\right)
\]

其中累计正净负荷误差表示需要从电池输出侧补充的电量，除以 \(\eta_d\) 后转换为电池内部SOC储备。该约束是模型采用的跨日风险控制规则，并非题目给定的固定日终SOC。历史残差不足5天时取 \(E_d^{res}=0\)。正式结果同时比较无储备、0.75分位数储备和0.9分位数储备，检验经济费用与次日供能风险之间的权衡。

### 9.7 情景平衡

计划变量在全部情景中保持不变：

\[
G_{d,t}+PV_{d,t,s}+D_{d,t}+R_{d,t,s}
=
L_{d,t,s}+C_{d,t}+W_{d,t,s}
\]

\[
R_{d,t,s}\ge0
\]

\[
W_{d,t,s}\ge0
\]

### 9.8 目标函数

\[
\min F_d
=
\sum_t p_tG_{d,t}
+
\sum_s\pi_{d,s}
\sum_t5p_tR_{d,t,s}
\]

主目标直接最小化正常购电费与期望紧急购电费，情景期望权重固定为1，不再引入未标定的 \(\beta\) 或电池惩罚系数。

若费用相同的最优解不唯一，采用字典序求解：先得到上述主费用最优值，再在主费用不超过最优值加数值容差的条件下最小化 \(\sum_t(C_{d,t}+D_{d,t})\)，最后最小化峰值购电。次级目标不得改变主费用最优性。

情景紧急购电会通过期望成本影响日前决策，但点预测供需平衡本身不使用紧急购电。

## 10. 实际结算

计划确定后，才能读取第 \(d\) 天实际数据：

\[
R_{d,t}^{act}
=
\max\left(
0,
L_{d,t}^{act}+C_{d,t}
-G_{d,t}-PV_{d,t}^{act}-D_{d,t}
\right)
\]

\[
W_{d,t}^{act}
=
\max\left(
0,
G_{d,t}+PV_{d,t}^{act}+D_{d,t}
-L_{d,t}^{act}-C_{d,t}
\right)
\]

实际平衡：

\[
G_{d,t}+PV_{d,t}^{act}+D_{d,t}+R_{d,t}^{act}
=
L_{d,t}^{act}+C_{d,t}+W_{d,t}^{act}
\]

实际费用：

\[
C_d^{real}
=
\sum_t p_tG_{d,t}
+
\sum_t5p_tR_{d,t}^{act}
\]

即使实际供能需求低于计划，正常购电仍按照已经提交的 \(G_{d,t}\) 计费，不能按实际使用量扣减。

## 11. SOC滚动更新

计划充放电量在实际阶段保持不变：

\[
C_{d,t}^{act}=C_{d,t},
\qquad
D_{d,t}^{act}=D_{d,t}.
\]

因此实际SOC按照已经提交的储能计划更新：

\[
E_{d,t}^{act}
=
E_{d,t-1}^{act}
+
\eta_cC_{d,t}^{act}
-
\frac{D_{d,t}^{act}}{\eta_d}
\]

下一天：

\[
E_{d+1,0}=E_{d,144}^{act}
\]

如果允许实时调整储能，则另建包含 \(C_{d,t}^{act},D_{d,t}^{act}\) 及相应实际SOC、容量、功率和互斥约束的对照模型。该对照模型不与第二问固定日内计划的主结果混用。

\[
C_{d,t}^{act},\ D_{d,t}^{act}
\]

## 12. 完美信息基准

为了公平区分预测信息损失和模型松弛损失，建立两个离线基准。

匹配约束的全年完美信息MILP使用全年实际负载和实际光伏，并保留与滚动模型相同的初始SOC、效率、容量、功率、充放电互斥和每日安全储备约束，记为：

\[
F_{perfect}^{MILP,matched}.
\]

相对信息差距为：

\[
Gap_{info}
=
\frac{F_{rolling}-F_{perfect}^{MILP,matched}}
{F_{perfect}^{MILP,matched}}
\times100\%.
\]

进一步取消滚动策略特有的每日安全储备，并松弛二进制互斥约束，得到更宽松的物理理论下界：

\[
F_{perfect}^{LP,lower}.
\]

对应差距为：

\[
Gap_{LP}
=
\frac{F_{rolling}-F_{perfect}^{LP,lower}}
{F_{perfect}^{LP,lower}}
\times100\%
\]

两类基准均只能在滚动策略完成后离线计算。完美信息模型的SOC、影子价格和调度结果不得回填到任何日前预测或计划。

## 13. 求解流程

```text
1. 读取附件1和附件2
2. 将功率转换为10分钟电量
3. 1月使用固定默认参数进行预热
4. 按时间顺序模拟1月并得到2月1日实际SOC
5. 对每个月：
   5.1 月初使用此前数据选择超参数
   5.2 冻结本月超参数
   5.3 对月内每一天：
       5.3.1 只读取k<d的数据
       5.3.2 构造0:00已知特征并按标准化距离选择相似日
       5.3.3 预测负载和光伏
       5.3.4 从滚动残差库生成情景
       5.3.5 计算历史分位数SOC安全储备
       5.3.6 求解日前随机情景MILP
       5.3.7 冻结计划购电和充放电
       5.3.8 读取当天实际数据进行结算
       5.3.9 计算实际紧急购电和剩余电量
       5.3.10 更新SOC
6. 汇总滚动策略费用
7. 计算完美信息LP和MILP基准
8. 输出result2.xlsx
```

## 14. 结果表映射

### 14.1 计划购电量

在“计划购电量”工作表中填写2025年2月1日至12月31日每天144个时段的：

\[
G_{d,t}
\]

### 14.2 充放电量

每24个时段汇总：

\[
C_{d,k}^{block}
=
\sum_{t=24(k-1)+1}^{24k}C_{d,t}
\]

\[
D_{d,k}^{block}
=
\sum_{t=24(k-1)+1}^{24k}D_{d,t}
\]

其中 (k=1,\ldots,6)。

### 14.3 储电量

填写：

\[
E_{d,0}^{act},\qquad E_{d,144}^{act}
\]

### 14.4 紧急购电量

合并连续满足：

\[
R_{d,t}^{act}>\varepsilon_R
\]

的时间段。

### 14.5 论文指定日期

从完整结果中提取以下日期，按照题目表格展示计划购电量、充放电量、SOC和紧急购电情况：

- 2025年3月20日；
- 2025年6月21日；
- 2025年9月23日；
- 2025年12月21日。

## 15. 信息边界自动检查

```python
history = data[data["date"] < d]

assert history["date"].max() < d
assert max(similar_day_dates) < d
assert max(validation_dates) < month_start
assert max(residual_dates) < d
assert max(quantile_dates) < d

for validation_day in validation_dates:
    assert max(data_used_to_predict[validation_day]) < validation_day
```

计划冻结后：

```python
plan_frozen = {
    "G": G.copy(),
    "C": C.copy(),
    "D": D.copy(),
    "E": E.copy(),
    "z": z.copy(),
}

actual_load = load[d]
actual_pv = pv[d]
```

未来数据扰动测试：

```text
修改第d天及以后的负载和光伏
重新运行第d天日前计划
要求G、C、D、E、z完全不变
```

## 16. 最终优点

- 彻底避免未来实际数据泄漏。
- 月度滚动验证能适应季节变化并避免验证日进入自身预测。
- 1 月固定参数避免预热期回算泄漏。
- 相似日标准化距离、特征权重和并列日期处理规则明确，且不使用目标日实际特征。
- 历史残差逐日滚动生成。
- 点预测供需平衡不使用紧急购电，情景紧急购电风险通过期望成本影响计划。
- 紧急购电由实际预测偏差真实触发。
- 月末 SOC 不再强制为 6000。
- 两日前瞻和影子价格不再污染主模型。
- 全年 LP/MILP 仅作为理论基准。

## 17. 最终结论

第二问最终主模型为：

> 月度无泄漏滚动验证、0:00已知特征的标准化距离相似日预测、联合残差轨迹情景、点预测供需约束、日前随机情景MILP、历史分位数跨日储备、实际紧急购电结算和SOC逐日更新模型。

该模型在数学约束、信息边界、结果输出和风险触发方面均满足第二问要求。
