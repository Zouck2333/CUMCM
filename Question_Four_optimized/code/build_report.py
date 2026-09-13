"""Build reproducible comparisons and paper tables from completed runs."""
from __future__ import annotations
import csv
import json
from pathlib import Path
import numpy as np

CASES=('baseline','load_only','adaptive_price','terminal_value')
LABELS={'baseline':'原预测重算','load_only':'仅改负荷预测','adaptive_price':'负荷改进＋自适应电价','terminal_value':'负荷电价改进＋终端价值'}
SELECTED_DATES=('2025-03-20','2025-06-21','2025-09-23','2025-12-21')


def records(path):
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]


def main():
    package=Path(__file__).resolve().parents[1]
    output=package/'output';output.mkdir(exist_ok=True)
    summaries={case:{s:json.loads((package/'experiments'/case/f'summary_{s}.json').read_text(encoding='utf-8'))
                     for s in ('4-2','4-3')} for case in CASES}
    selected=json.loads((package/'configuration.json').read_text(encoding='utf-8'))
    forecast=json.loads((package/'experiments/forecast_audit.json').read_text(encoding='utf-8'))
    comparison={'cases':summaries,'selected_configuration':selected,'forecast_audit':forecast,
                'selection_disclosure':'Fixed candidate policies were compared on the same 2025 data used for model development. Each executed decision and internal forecast selection is causal; there is no independent-year test.',
                'effect_by_strategy':{}}
    lines=['# 第四问优化结果与逐项对照','',
           '正式评价期为 2025 年 2 月 1 日至 12 月 31 日，共 334 天。每个方案均从 1 月 1 日 6000 kWh 独立顺序运行全年。费用单位为元，电量单位为 kWh。','',
           '## 正式输出','',
           '| 策略 | 原结果总费用 | 优化总费用 | 节省 | 降幅 | 配置 |',
           '|---|---:|---:|---:|---:|---|']
    for s in ('4-2','4-3'):
        old=json.loads((package/'reference'/f'summary_{s}.json').read_text())
        new=json.loads((output/f'summary_{s}.json').read_text())
        a=old['totals']['total_cost_yuan'];b=new['totals']['total_cost_yuan']
        comparison['effect_by_strategy'][s]={'old_cost':a,'new_cost':b,'saving_yuan':a-b,'saving_fraction':(a-b)/a}
        lines.append(f"| {s} | {a:,.2f} | {b:,.2f} | {a-b:,.2f} | {(a-b)/a:.2%} | {LABELS[selected['strategies'][s]['case']]} |")
    lines+=['','方案在回算前固定了预测窗口、候选集合和内部选择规则。正式配置根据本次同年对照确定；这里报告的是 2025 年回测改进，未把同年模型选择后的成绩称为独立样本验证。',
            '最终采用日历负荷预测、自适应电价预测和终端价值权重 χ=1。该固定方案在已测试方案中，两种策略的正式期及全年费用均最低；没有根据当天未来实际结果逐日选择最便宜的方案。',
            '终端价值的额外收益较小：相对仅改负荷预测，4-2 的正式期再节省约 0.99 万元；相对负荷电价改进，4-3 再节省约 4.27 万元。4-2 有四个月略高于仅改负荷方案，4-3 有一个月略高于无终端值方案，因此不声称逐月必然改善或可在其他年份保证这些边际收益。','',
            '## 逐项费用对照','',
            '| 策略 | 方案 | 计划购电费 | 调整费 | 紧急购电费 | 总费用 | 紧急购电量 |',
            '|---|---|---:|---:|---:|---:|---:|']
    for s in ('4-2','4-3'):
        for case in CASES:
            t=summaries[case][s]['totals']
            lines.append(f"| {s} | {LABELS[case]} | {t['plan_cost_yuan']:,.2f} | {t['adjustment_cost_yuan']:,.2f} | {t['emergency_cost_yuan']:,.2f} | {t['total_cost_yuan']:,.2f} | {t['emergency_kwh']:,.2f} |")
    old_rolling=json.loads((package/'reference/summary_4-3.json').read_text())['totals']['total_cost_yuan']
    replay_rolling=summaries['baseline']['4-3']['totals']['total_cost_yuan']
    lines+=['',f'原 4-3 存档费用为 {old_rolling:,.2f} 元，连续调整变量下的原预测重算为 {replay_rolling:,.2f} 元，相差 {old_rolling-replay_rolling:,.2f} 元。连续化在优化意义上等价，但可选择不同的同优或容差内计划，因而实际结算不要求逐项完全相同。预测改进的同配置对照使用“原预测重算”列。',
            '', '## 全年同初始状态及风险核对','',
            '| 策略 | 方案 | 1—12 月总费用 | 2 月 1 日 SOC | 12 月 31 日 SOC | 日费用 CVaR90 |',
            '|---|---|---:|---:|---:|---:|']
    for s in ('4-2','4-3'):
        for case in CASES:
            x=summaries[case][s]
            lines.append(f"| {s} | {LABELS[case]} | {x['annual_total_cost_yuan']:,.2f} | {x['formal_start_soc_kwh']:,.4f} | {x['year_end_soc_kwh']:,.4f} | {x['daily_cost_cvar_90_yuan']:,.2f} |")
    lines+=['','日费用 CVaR90 按等权经验分布的最贵 10% 概率质量精确计算，边界样本使用部分权重。它是事后评价指标，区别于优化器中三个情景的 CVaR。',
            '2 月 1 日的 SOC 来自各自 1 月的实际执行，因此正式期费用包含预热状态影响。终端价值只进入优化目标，实际总费用不扣除任何虚拟 SOC 收益。','',
            '## 预测效果','',
            f"四时点执行区间的负荷预测 MAE：{forecast['load_old_execution_mae_kw']:.4f} → {forecast['load_calendar_execution_mae_kw']:.4f} kW。",'',
            '| 电价预测 | 0:00 剩余未知时段 MAE | 四时点执行段未知价格 MAE |',
            '|---|---:|---:|']
    for name in ('legacy','calendar','calendar_decay','adaptive'):
        lines.append(f"| {name} | {forecast['price_zero_stage_remaining_mae'][name]:.6f} | {forecast['price_execution_mae'][name]:.6f} |")
    lines+=['','电价误差单位为元/kWh。自适应选择仅用过去最多 28 个已完成日期的预测误差，最少 7 天，否则使用原预测。全期 1336 次选择中没有使用当天或未来日期的训练评分。',
            '预测 MAE 更小并不保证套利调度费用更低；正式方案以完整实际费用、月份表现与边界状态共同判断。','',
            '## 正式结果逐月对照','',
            '| 月份 | 原 4-2 费用 | 优化 4-2 费用 | 原 4-3 费用 | 优化 4-3 费用 |',
            '|---|---:|---:|---:|---:|']
    old_s={s:json.loads((package/'reference'/f'summary_{s}.json').read_text()) for s in ('4-2','4-3')}
    final_s={s:json.loads((output/f'summary_{s}.json').read_text()) for s in ('4-2','4-3')}
    for month in old_s['4-2']['monthly']:
        values=[old_s['4-2']['monthly'][month]['total_cost_yuan'],final_s['4-2']['monthly'][month]['total_cost_yuan'],
                old_s['4-3']['monthly'][month]['total_cost_yuan'],final_s['4-3']['monthly'][month]['total_cost_yuan']]
        lines.append('| '+month+' | '+' | '.join(f'{x:,.2f}' for x in values)+' |')
    lines+=['','## 风险结构与扩展范围','',
            '本轮保留原名义平衡、购电结算、四个决策时点和电池物理约束。减少的是调整方向的冗余二元变量，电池充放电互斥仍由二元变量保证。',
            '当预测和实际光伏都为零时，紧急购电量等于正负荷预测误差；调高风险系数无法直接消除这一结构限制，已用算例验证。备用购电合同、区间内实时电池反馈以及预见后续调整费用的完整多阶段情景树均未加入本轮正式模型。',
            '原固定轨迹价格压力测试模块保留，但没有将其称为鲁棒重优化。本轮电价预测是因果点预测，CVaR 情景仍为负荷与光伏残差。','',
            '## 验证与复现','',
            '见 verification_report.json：365 天 SOC 递推、容量、功率、充放电互斥、实际缺口、费用分项、正式期 48096 条 CSV 记录及两份工作簿逐单元格一致性。tests 包含四个决策时点的未来信息屏蔽、历史残差重建和原整数模型等价性测试。',
            '运行入口为包根目录 run.py；配置为 configuration.json；预测回放与四组对照保留在 experiments 中。原 Question_Four 的源文件和结果哈希单独核验。']
    (output/'optimization_report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    (output/'optimization_comparison.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2),encoding='utf-8')
    paper=['# 第四问论文指定日期结果','','| 策略 | 日期 | 初始购电 | 最终正常购电 | 紧急购电 | 计划费 | 调整费 | 紧急费 | 总费用 |','|---|---|---:|---:|---:|---:|---:|---:|---:|']
    table=[]
    for s in ('4-2','4-3'):
        for r in records(output/f'checkpoint_{s}.jsonl'):
            if r['date'] not in SELECTED_DATES:continue
            values=[sum(r['initial_purchase']),sum(r['final_purchase']),sum(r['emergency']),r['plan_cost'],r['adjustment_cost'],r['emergency_cost'],r['total_cost']]
            paper.append('| '+s+' | '+r['date']+' | '+' | '.join(f'{v:,.4f}' for v in values)+' |')
            table.append([s,r['date'],*values])
    (output/'paper_selected_dates.md').write_text('\n'.join(paper)+'\n',encoding='utf-8')
    with (output/'paper_selected_dates.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(['strategy','date','initial_kwh','final_kwh','emergency_kwh','plan_yuan','adjustment_yuan','emergency_yuan','total_yuan']);writer.writerows(table)
    print(json.dumps(comparison['effect_by_strategy'],ensure_ascii=False,indent=2))


if __name__=='__main__': main()
