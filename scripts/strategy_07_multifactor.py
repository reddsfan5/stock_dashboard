#!/usr/bin/env python3
"""
策略7: 月度多因子选股 — 振幅+动量+量+强势度综合排名，每月调仓

结果: -21.5% / 167笔 / 胜率44.3%
教训: 简单等权多因子在A股选股上无效，需要更精细的因子模型
"""

import argparse, os, sys, time
import numpy as np, pandas as pd
from tqdm import tqdm
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
from data.kline import StockData
from backtest.sim_types import Trade, EquityPoint
from backtest.sim_core import print_stats
from backtest.renderer import render_dip_buy_report
OUTPUT_HTML = os.path.join(PROJECT_DIR, "output", "strategy_07.html")

MAIN = ("sh600","sh601","sh603","sh605","sz000","sz001","sz002","sz003")

def run(capital=50000, start_date="2022-01-01", hold_days=20, top_n=10):
    data = StockData()
    cache = data.cache; cache = cache[cache["代码"].str.startswith(MAIN)]
    all_dates = sorted(cache["日期"].unique())
    start = pd.Timestamp(start_date)
    dates = [d for d in all_dates if d >= start][::hold_days]
    cash = capital; pos = {}; trades = []; eq = []

    for date in tqdm(dates, desc="月度调仓"):
        for code, pd_ in list(pos.items()):
            row = cache[(cache["代码"]==code)&(cache["日期"]==date)]
            if len(row)>0:
                sp=float(row["收盘"].iloc[0]); cash+=pd_["shares"]*sp*0.9994
                ret=(pd_["shares"]*sp*0.9994-pd_["cost"])/pd_["cost"]*100
                trades.append(Trade(code=code,name="",buy_date=str(pd_["date"])[:10],buy_price=pd_["bp"],sell_date=str(date)[:10],sell_price=round(sp,2),return_pct=round(ret,2),pnl=round(pd_["shares"]*sp*0.9994-pd_["cost"],2),filled=True,lots=pd_["shares"]//100))
                del pos[code]
        prev = cache[(cache["日期"]<date)&(cache["日期"]>=date-pd.Timedelta(days=45))]
        if len(prev)==0: continue
        scores = {}
        for c, g in prev.groupby("代码"):
            if len(g)<20: continue; g=g.sort_values("日期")
            cl=g["收盘"].values; hi=g["最高"].values; lo=g["最低"].values; vo=g["成交额"].values
            if cl[-1]<=0: continue
            h20,l20=np.max(hi[-20:]),np.min(lo[-20:])
            amp=(h20-l20)/l20*100
            if amp>50 or amp<3: continue
            mom=(cl[-1]-cl[-20])/cl[-20]*100
            if mom>50 or mom<-30: continue
            avgv=np.mean(vo[-20:])
            if avgv<5e7: continue
            s_amp=max(0,1-amp/30)
            s_mom=mom/30+0.5
            s_vol=min(avgv/5e8,1.0)
            s_high=max(0,1-(h20-cl[-1])/h20*100/20)
            scores[c]=s_amp*0.3+s_mom*0.25+s_vol*0.15+s_high*0.3
        top=sorted(scores,key=scores.get,reverse=True)[:top_n]
        if not top: continue
        per=cash/len(top)
        for c in top:
            row=cache[(cache["代码"]==c)&(cache["日期"]==date)]
            if len(row)==0: continue
            bp=float(row["收盘"].iloc[0]); lots=int(per/(bp*100*1.0003))
            if lots<=0: continue; cost=lots*100*bp*1.0003
            if cost>cash: continue
            cash-=cost; pos[c]={"shares":lots*100,"cost":cost,"bp":bp,"date":date}
        pv=sum(p["shares"]*p["bp"] for p in pos.values())
        eq.append(EquityPoint(date=str(date)[:10],equity=cash+pv,cash=cash,positions=len(pos)))

    ld=all_dates[-1]
    for c,pd_ in list(pos.items()):
        row=cache[(cache["代码"]==c)&(cache["日期"]==ld)]
        if len(row)>0:
            sp=float(row["收盘"].iloc[0]); cash+=pd_["shares"]*sp*0.9994
            trades.append(Trade(code=c,name="",buy_date=str(pd_["date"])[:10],buy_price=pd_["bp"],sell_date=str(ld)[:10],sell_price=round(sp,2),return_pct=round((pd_["shares"]*sp*0.9994-pd_["cost"])/pd_["cost"]*100,2),pnl=round(pd_["shares"]*sp*0.9994-pd_["cost"],2),filled=False,lots=pd_["shares"]//100))
    print_stats(trades,cash,capital,start_date,str(ld)[:10],title="月度多因子选股",extra_info=f"4因子等权×{top_n}只×{hold_days}天")
    html=render_dip_buy_report(capital=capital,target_pct=0,overlap_pct=0,commission_rate=0.0003,stamp_tax=0.0003,start_date=start_date,lookback=hold_days,board="main",max_gain=99,limit_down=99,max_range_20d=99,trades=trades,equity_curve=eq,final_equity=cash,kline_map={})
    os.makedirs(os.path.dirname(OUTPUT_HTML),exist_ok=True)
    with open(OUTPUT_HTML,"w") as f: f.write(html)
    print(f"✓ HTML: {OUTPUT_HTML}")

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--capital",type=float,default=50000);p.add_argument("--start",type=str,default="2022-01-01")
    a=p.parse_args();t0=time.time();run(capital=a.capital,start_date=a.start)
    print(f"耗时: {time.time()-t0:.0f}秒")
