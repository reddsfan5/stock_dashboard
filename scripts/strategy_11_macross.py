#!/usr/bin/env python3
"""策略11: 5/20均线金叉 — 最经典的散户入门指标。结果: -15% / 115笔 / 胜率36%"""
import argparse, os, sys, time; import numpy as np, pandas as pd; from tqdm import tqdm
PROJECT_DIR=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0,PROJECT_DIR)
from data.kline import StockData; from data.etf import ETFData
from backtest.sim_types import Trade,EquityPoint; from backtest.sim_core import print_stats; from backtest.renderer import render_dip_buy_report
OUTPUT_HTML=os.path.join(PROJECT_DIR,"output","strategy_11.html")
def run(capital=50000,start_date="2022-01-01",hold_days=20,top_n=5):
    stock=StockData();etf=ETFData();cache=pd.concat([stock.cache,etf.cache_with_prefix],ignore_index=True);cache=cache[cache['代码'].str.startswith(('sh5','sz1'))]
    all_dates=sorted(cache['日期'].unique());start=pd.Timestamp(start_date);dates=[d for d in all_dates if d>=start][::hold_days]
    cash=capital;pos={};trades=[];eq=[]
    for date in tqdm(dates,desc="MA金叉"):
        for c,p in list(pos.items()):
            r=cache[(cache['代码']==c)&(cache['日期']==date)]
            if len(r)>0:sp=float(r['收盘'].iloc[0]);cash+=p['shares']*sp*0.9994;trades.append(Trade(code=c,name="",buy_date=str(p['date'])[:10],buy_price=p['bp'],sell_date=str(date)[:10],sell_price=round(sp,2),return_pct=round((p['shares']*sp*0.9994-p['cost'])/p['cost']*100,2),pnl=round(p['shares']*sp*0.9994-p['cost'],2),filled=True,lots=p['shares']//100));del pos[c]
        prev=cache[(cache['日期']<date)&(cache['日期']>=date-pd.Timedelta(days=80))]
        rets={}
        for c,g in prev.groupby('代码'):
            g=g.sort_values('日期');n=len(g)
            if n<25:continue
            cl=g['收盘'].values; vo=g['成交额'].values
            ma5=pd.Series(cl).rolling(5).mean().values;ma20=pd.Series(cl).rolling(20).mean().values
            if not(ma5[-2]<=ma20[-2] and ma5[-1]>ma20[-1]):continue
            if cl[-1]<=ma5[-1]:continue
            rets[c]=(cl[-1]-cl[-20])/cl[-20]*100
        top=sorted(rets,key=rets.get,reverse=True)[:top_n]
        if not top:continue
        per=cash/len(top)
        for c in top:
            r=cache[(cache['代码']==c)&(cache['日期']==date)]
            if len(r)==0:continue
            bp=float(r['收盘'].iloc[0]); lots=int(per/(bp*100*1.0003))
            if lots<=0:continue
            cost=lots*100*bp*1.0003
            if cost>cash:continue
            cash-=cost;pos[c]={'shares':lots*100,'cost':cost,'bp':bp,'date':date}
        pv=sum(p['shares']*p['bp'] for p in pos.values());eq.append(EquityPoint(date=str(date)[:10],equity=cash+pv,cash=cash,positions=len(pos)))
    ld=all_dates[-1]
    for c,p in list(pos.items()):
        r=cache[(cache['代码']==c)&(cache['日期']==ld)]
        if len(r)>0:sp=float(r['收盘'].iloc[0]);cash+=p['shares']*sp*0.9994;trades.append(Trade(code=c,name="",buy_date=str(p['date'])[:10],buy_price=p['bp'],sell_date=str(ld)[:10],sell_price=round(sp,2),return_pct=round((p['shares']*sp*0.9994-p['cost'])/p['cost']*100,2),pnl=round(p['shares']*sp*0.9994-p['cost'],2),filled=False,lots=p['shares']//100))
    print_stats(trades,cash,capital,start_date,str(ld)[:10],title="5/20均线金叉",extra_info=f"MA5上穿MA20+价在MA5上方 | {hold_days}天×{top_n}只")
    html=render_dip_buy_report(capital=capital,target_pct=0,overlap_pct=0,commission_rate=0.0003,stamp_tax=0.0003,start_date=start_date,lookback=hold_days,board="etf",max_gain=99,limit_down=99,max_range_20d=99,trades=trades,equity_curve=eq,final_equity=cash,kline_map={})
    os.makedirs(os.path.dirname(OUTPUT_HTML),exist_ok=True);open(OUTPUT_HTML,"w").write(html);print(f"✓ HTML: {OUTPUT_HTML}")
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--capital",type=float,default=50000);p.add_argument("--start",type=str,default="2022-01-01")
    a=p.parse_args();t0=time.time();run(a.capital,a.start);print(f"耗时:{time.time()-t0:.0f}秒")
