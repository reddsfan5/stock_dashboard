#!/usr/bin/env python3
"""
策略9: 缩量回调买入 — 前期放量涨+近期缩量跌=洗盘信号

同花顺/散户经典战术："放量涨缩量跌是洗盘，放量跌缩量涨是出货"
本策略量化了这个思路：
  1. 前段(10天前到5天前)涨幅>3%（主力拉升至阶段高点）
  2. 近5天回调3-12%（缩量回踩，散户被洗出）
  3. 成交量萎缩至前段均量80%以下（确认缩量）

结果: +71% 总收益 / +15%年化 / 70%胜率 / -39%最大回撤 / 43笔交易
vs 纯动量(策略8): +60%/+13%/53%/-12%/100笔

用法:
  python -m scripts.strategies.strategy_09_pullback
  python -m scripts.strategies.strategy_09_pullback --hold 20 --top 5
"""

import argparse, os, sys, time
import numpy as np, pandas as pd
from tqdm import tqdm
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)
from data.kline import StockData; from data.etf import ETFData
from backtest.sim_types import Trade, EquityPoint
from backtest.sim_core import print_stats
from backtest.renderer import render_dip_buy_report
OUTPUT_HTML = os.path.join(PROJECT_DIR, "output", "strategy_09.html")

def run(capital=50000, start_date="2022-01-01", hold_days=20, top_n=5):
    stock=StockData(); etf=ETFData()
    cache=pd.concat([stock.cache,etf.cache_with_prefix],ignore_index=True)
    cache=cache[cache['代码'].str.startswith(('sh5','sz1'))]
    all_dates=sorted(cache['日期'].unique()); start=pd.Timestamp(start_date)
    dates=[d for d in all_dates if d>=start][::hold_days]

    cash=capital; pos={}; trades=[]; eq=[]
    code_to_name=dict(zip(etf.get_list()["代码"],etf.get_list()["名称"]))
    code_to_name={("sh" if str(c).startswith("5") else "sz")+str(c):n for c,n in zip(etf.get_list()["代码"],etf.get_list()["名称"])}

    for date in tqdm(dates, desc="调仓"):
        for c,p in list(pos.items()):
            r=cache[(cache['代码']==c)&(cache['日期']==date)]
            if len(r)>0:
                sp=float(r['收盘'].iloc[0]); cash+=p['shares']*sp*0.9994
                ret=(p['shares']*sp*0.9994-p['cost'])/p['cost']*100
                trades.append(Trade(code=c,name=code_to_name.get(c,""),buy_date=str(p['date'])[:10],buy_price=p['bp'],sell_date=str(date)[:10],sell_price=round(sp,2),return_pct=round(ret,2),pnl=round(p['shares']*sp*0.9994-p['cost'],2),filled=True,lots=p['shares']//100))
                del pos[c]
        # 选股
        prev=cache[(cache['日期']<date)&(cache['日期']>=date-pd.Timedelta(days=50))]
        rets={}
        for c,g in prev.groupby('代码'):
            g=g.sort_values('日期'); n=len(g)
            if n<20: continue
            cl=g['收盘'].values; vo=g['成交额'].values
            if cl[n-5]<=cl[n-10]*1.03: continue
            if cl[n-1]>=cl[n-5]*0.97: continue
            if cl[n-1]<=cl[n-5]*0.88: continue
            if np.mean(vo[-3:])>=np.mean(vo[-10:-5])*0.8: continue
            rets[c]=(cl[-1]-cl[-5])/cl[-5]*100
        top=sorted(rets,key=rets.get)[:top_n]
        if not top: continue
        per=cash/len(top)
        for c in top:
            r=cache[(cache['代码']==c)&(cache['日期']==date)]
            if len(r)==0: continue
            bp=float(r['收盘'].iloc[0]); lots=int(per/(bp*100*1.0003))
            if lots<=0: continue
            cost=lots*100*bp*1.0003
            if cost>cash: continue
            cash-=cost; pos[c]={'shares':lots*100,'cost':cost,'bp':bp,'date':date}
        pv=sum(p['shares']*p['bp'] for p in pos.values())
        eq.append(EquityPoint(date=str(date)[:10],equity=cash+pv,cash=cash,positions=len(pos)))

    ld=all_dates[-1]
    for c,p in list(pos.items()):
        r=cache[(cache['代码']==c)&(cache['日期']==ld)]
        if len(r)>0:
            sp=float(r['收盘'].iloc[0]); cash+=p['shares']*sp*0.9994
            trades.append(Trade(code=c,name=code_to_name.get(c,""),buy_date=str(p['date'])[:10],buy_price=p['bp'],sell_date=str(ld)[:10],sell_price=round(sp,2),return_pct=round((p['shares']*sp*0.9994-p['cost'])/p['cost']*100,2),pnl=round(p['shares']*sp*0.9994-p['cost'],2),filled=False,lots=p['shares']//100))
    print_stats(trades,cash,capital,start_date,str(ld)[:10],title="缩量回调买入",extra_info=f"放量涨+缩量跌=洗盘 | {hold_days}天×{top_n}只")
    html=render_dip_buy_report(capital=capital,target_pct=0,overlap_pct=0,commission_rate=0.0003,stamp_tax=0.0003,start_date=start_date,lookback=hold_days,board="etf",max_gain=99,limit_down=99,max_range_20d=99,trades=trades,equity_curve=eq,final_equity=cash,kline_map={})
    os.makedirs(os.path.dirname(OUTPUT_HTML),exist_ok=True)
    with open(OUTPUT_HTML,"w") as f: f.write(html)
    print(f"✓ HTML: {OUTPUT_HTML}")

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--capital",type=float,default=50000);p.add_argument("--hold",type=int,default=20);p.add_argument("--top",type=int,default=5);p.add_argument("--start",type=str,default="2022-01-01")
    a=p.parse_args();t0=time.time();run(a.capital,a.start,a.hold,a.top)
    print(f"耗时:{time.time()-t0:.0f}秒")
