"""Publication-friendly comparison of actual cost components."""
from pathlib import Path
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties


def main():
    package=Path(__file__).resolve().parents[1]
    font=FontProperties(fname='C:/Windows/Fonts/msyh.ttc')
    fig,axes=plt.subplots(1,2,figsize=(10,4.8),sharey=True)
    colors=['#406C92','#D4A34B','#C45F5F']
    names=['计划购电费','调整费','紧急购电费']
    keys=['plan_cost_yuan','adjustment_cost_yuan','emergency_cost_yuan']
    for ax,strategy,title in zip(axes,('4-2','4-3'),('4-2：每日一次计划','4-3：四时点滚动')):
        old=json.loads((package/'reference'/f'summary_{strategy}.json').read_text())['totals']
        new=json.loads((package/'output'/f'summary_{strategy}.json').read_text())['totals']
        bottoms=[0.,0.]
        for key,color,name in zip(keys,colors,names):
            values=[old[key]/10000,new[key]/10000]
            ax.bar([0,1],values,bottom=bottoms,color=color,width=.5,label=name)
            bottoms=[a+b for a,b in zip(bottoms,values)]
        for x,total in enumerate(bottoms):ax.text(x,total+35,f'{total:,.2f}',ha='center',fontsize=11)
        ax.set_xticks([0,1],['原方案','优化方案'],fontproperties=font)
        ax.set_title(title,fontproperties=font,pad=12)
        ax.set_ylim(0,2500)
        ax.spines[['top','right']].set_visible(False)
        ax.grid(axis='y',color='#DDDDDD',linewidth=.6)
        ax.set_axisbelow(True)
    axes[0].set_ylabel('实际费用（万元）',fontproperties=font)
    handles,labels=axes[0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='lower center',ncol=3,frameon=False,prop=font,bbox_to_anchor=(.5,.05))
    fig.suptitle('2025 年 2—12 月购电费用对照',fontproperties=font,fontsize=15)
    fig.subplots_adjust(top=.82,bottom=.23,wspace=.25)
    target=package/'output/figures';target.mkdir(exist_ok=True)
    fig.savefig(target/'cost_comparison.png',dpi=220,bbox_inches='tight',facecolor='white')
    plt.close(fig)


if __name__=='__main__':main()
