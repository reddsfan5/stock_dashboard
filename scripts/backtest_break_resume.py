#!/usr/bin/env python3
"""
回测：K线连续性中断后次日恢复概率

研究问题
--------
「本来连续的标的，当某天断开后，其后的一天能继续连续的概率有多大？」

事件定义（以中断日 i 为锚，等价于 近n日→近n-b日 的记号）：
  · 连续段：i 之前 K = b+1 个交易日（近n日到近n-b日，共 b+1 天），每天
        最高价 > 前一天最低价 + 前一天收盘价 × m%
    且 K 天日均成交额 ≥ min_amount 万元
  · 中断日 i（近n-b-1日）：最高价 < 前一天最低价 + 前一天收盘价 × m%
        （不满足即视为中断，含恰好等于）
  · 统计日 i+1（近n-b-2日）：统计 最高价 > 前一天最低价 + 前一天收盘价 × m% 的概率

说明
----
全历史滚动扫描时 n 只是锚点偏移，实际只有 K = b+1 起作用，故以 --streak K 参数化。
股票池：沪深主板（000/001/002/003/600/601/603/605），按 stock_info 名称剔除
ST/退市，不含创业板/科创板/北交所。

对照基线
--------
  全体基线      P(任意一天的次日满足连续性条件)      —— 自然延续率
  任意中断基线  P(前一天中断 → 次日恢复)             —— 与「连续K天后再中断」直接对照
  若恢复概率 ≈ 任意中断基线，说明连续 K 天本身并不改变次日的恢复概率。

用法
----
$ python scripts/backtest_break_resume.py                        # K=5, m=1.5%, 5000万
$ python scripts/backtest_break_resume.py --streak 10 --gap 2
$ python scripts/backtest_break_resume.py --streaks 2,3,5,8,10   # 多档连续天数对比
$ python scripts/backtest_break_resume.py --start 2010-01-01     # 全历史

输出
----
output/backtest_break_resume.html — 汇总报告
output/break_resume_events.csv    — 中断事件明细（含 K 列）
"""

import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd
from tqdm import tqdm

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from data.kline import StockData
from data.sources import MAIN_BOARD_PREFIX
from data.industry import StockInfo

OUTPUT_HTML = os.path.join(PROJECT_DIR, "output", "backtest_break_resume.html")
OUTPUT_CSV = os.path.join(PROJECT_DIR, "output", "break_resume_events.csv")

# 沪深主板代码前缀（带交易所前缀）
BOARD_TUPLE = (
    "sh600", "sh601", "sh603", "sh605",
    "sz000", "sz001", "sz002", "sz003",
)


# ====================================================================
# 数据准备
# ====================================================================

def load_series(cache: pd.DataFrame, start_date: str) -> dict:
    """过滤股票池 → 按代码分组，预取 numpy 数组（供多档 K 复用）"""
    codes = cache["代码"].unique()
    codes = codes[pd.Series(codes).str.startswith(BOARD_TUPLE).values]

    # 按名称剔除 ST/退市
    info = StockInfo().df
    st_codes = set(info[info["名称"].str.contains("ST|退", regex=True, na=False)]["代码"])
    codes = [c for c in codes if c not in st_codes]

    df = cache[cache["代码"].isin(codes) & (cache["日期"] >= pd.Timestamp(start_date))]
    df = df.sort_values(["代码", "日期"])

    series = {}
    for code, grp in df.groupby("代码", sort=False):
        series[code] = (
            grp["最高"].to_numpy(float),
            grp["最低"].to_numpy(float),
            grp["收盘"].to_numpy(float),
            # 缓存成交额单位为元，转万元（与 screen/*.py 的 /10000 约定一致）
            np.nan_to_num(grp["成交额"].to_numpy(float), nan=0.0) / 10000,
            grp["日期"].to_numpy(),
        )
    return series


def build_cond(hi, lo, cl, gap: float) -> np.ndarray:
    """连续性条件：cond[i] = 今日最高 > 昨低 + 昨收 × m%（NaN → False）"""
    cond = np.zeros(len(hi), dtype=bool)
    cond[1:] = hi[1:] > lo[:-1] + cl[:-1] * (gap / 100)
    return cond


# ====================================================================
# 核心扫描
# ====================================================================

def scan_stock(hi, lo, cl, amt, dt, K: int, gap: float, min_amount: float):
    """
    扫描单只股票的「连续K天 → 中断 → 次日」事件。

    Returns: (中断日, 是否恢复, 中断日成交额, 次日涨幅, 次日成交额) 各数组
    """
    L = len(hi)
    if L < K + 3:
        return None
    cond = build_cond(hi, lo, cl, gap)

    # 前缀和：cs[i] = sum(cond[0..i-1])
    cs = np.concatenate([[0], np.cumsum(cond)])
    acs = np.concatenate([[0], np.cumsum(amt)])

    i = np.arange(K, L - 1)  # 中断日候选：前有 K 日窗口，后有次日
    win_ok = (cs[i] - cs[i - K]) == K          # i-K..i-1 连续 K 天满足
    amt_ok = (acs[i] - acs[i - K]) >= K * min_amount  # K 天日均成交额达标
    event = win_ok & amt_ok & ~cond[i]         # 当日中断

    idx = i[event]
    if len(idx) == 0:
        return None
    ret = np.where(cl[idx] > 0, cl[idx + 1] / cl[idx] - 1, 0.0) * 100
    return (dt[idx], cond[idx + 1], amt[idx], ret, amt[idx + 1])


def scan_universe(series: dict, K: int, gap: float, min_amount: float):
    """全市场扫描，返回事件明细 DataFrame"""
    rows = []
    for code, (hi, lo, cl, amt, dt) in tqdm(series.items(), desc=f"K={K} 扫描", unit="只"):
        r = scan_stock(hi, lo, cl, amt, dt, K, gap, min_amount)
        if r is None:
            continue
        bd, resumed, bd_amt, ret, nd_amt = r
        rows.append(pd.DataFrame({
            "代码": code,
            "中断日": bd,
            "是否恢复": resumed,
            "中断日成交额": bd_amt,
            "次日涨幅%": ret.round(2),
            "次日成交额": nd_amt,
        }))
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


def compute_baselines(series: dict, gap: float, min_amount: float) -> dict:
    """
    全市场基线：
      all        P(任意一天的次日满足条件)
      brk        P(前一天中断 → 次日恢复)
      brk_amt    P(前一天中断 → 次日恢复 | 前一天成交额 ≥ 下限)
    """
    n_all = n_true = n_brk = n_brk_true = n_brk_amt = n_brk_amt_true = 0
    for hi, lo, cl, amt, _ in series.values():
        cond = build_cond(hi, lo, cl, gap)
        if len(cond) < 3:
            continue
        nxt = cond[1:]              # 次日是否满足（对应 i=0..L-2 的次日）
        n_all += len(nxt)
        n_true += int(nxt.sum())
        brk_mask = ~cond[:-1]       # i 日中断
        n_brk += int(brk_mask.sum())
        n_brk_true += int(nxt[brk_mask].sum())
        amt_mask = brk_mask & (amt[:-1] >= min_amount)
        n_brk_amt += int(amt_mask.sum())
        n_brk_amt_true += int(nxt[amt_mask].sum())
    return {
        "all": (n_true, n_all),
        "brk": (n_brk_true, n_brk),
        "brk_amt": (n_brk_amt_true, n_brk_amt),
    }


# ====================================================================
# 断前预警 —— 连续段最后一天的特征能否预判次日断开
# ====================================================================

# 各特征的分桶边界与标签（右闭区间）
FEATURE_BUCKETS = {
    "当日涨跌幅%": ([-np.inf, -1, -0.5, 0, 0.5, 1, 2, 4, 7, np.inf],
                    ["≤-1", "-1~-0.5", "-0.5~0", "0~0.5", "0.5~1", "1~2", "2~4", "4~7", "≥7"]),
    "当日振幅%": ([-np.inf, 1, 2, 3, 5, 8, np.inf],
                  ["<1", "1~2", "2~3", "3~5", "5~8", "≥8"]),
    "收盘位置%": ([-np.inf, 20, 40, 60, 80, np.inf],
                  ["<20", "20~40", "40~60", "60~80", "≥80"]),
    "量比": ([-np.inf, 0.7, 1, 1.5, 2.5, np.inf],
             ["<0.7", "0.7~1", "1~1.5", "1.5~2.5", "≥2.5"]),
    "连续段累计涨幅%": ([-np.inf, 0, 3, 6, 10, 20, np.inf],
                       ["≤0", "0~3", "3~6", "6~10", "10~20", "≥20"]),
}


def analyze_break_warning(series: dict, K: int, gap: float, min_amount: float) -> pd.DataFrame:
    """
    样本：某交易日 j 处于「含当日共 K 天连续 + 日均成交额达标」状态，
    记录当日特征，标注次日 j+1 是否断开。
    Returns 列: 当日涨跌幅% / 当日振幅% / 收盘位置% / 量比 / 连续段累计涨幅% / 次日断开
    """
    rows = []
    for hi, lo, cl, amt, _ in tqdm(series.values(), desc=f"K={K} 断前预警", unit="只"):
        L = len(hi)
        if L < K + 3:
            continue
        cond = build_cond(hi, lo, cl, gap)
        cs = np.concatenate([[0], np.cumsum(cond)])
        acs = np.concatenate([[0], np.cumsum(amt)])

        j = np.arange(K, L - 1)
        win_ok = (cs[j + 1] - cs[j - K + 1]) == K      # 含当日 K 天全连续
        amt_ok = (acs[j + 1] - acs[j - K + 1]) >= K * min_amount
        jj = j[win_ok & amt_ok]
        if len(jj) == 0:
            continue

        prev_cl = cl[jj - 1]
        with np.errstate(divide="ignore", invalid="ignore"):
            ret = np.where(prev_cl > 0, cl[jj] / prev_cl - 1, 0) * 100          # 当日涨跌幅
            amp = np.where(prev_cl > 0, (hi[jj] - lo[jj]) / prev_cl, 0) * 100   # 当日振幅
            rng = hi[jj] - lo[jj]
            pos = np.where(rng > 0, (cl[jj] - lo[jj]) / rng * 100, 50)          # 收盘位置
            base = (acs[jj + 1] - acs[jj - K + 1]) / K                          # K日均成交额
            volr = np.where(base > 0, amt[jj] / base, 1)                        # 量比
            gain0 = cl[jj - K + 1]
            gain = np.where(gain0 > 0, cl[jj] / gain0 - 1, 0) * 100             # 连续段累计涨幅

        rows.append(pd.DataFrame({
            "当日涨跌幅%": ret, "当日振幅%": amp, "收盘位置%": pos,
            "量比": volr, "连续段累计涨幅%": gain, "次日断开": ~cond[jj + 1],
        }))
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


def bucket_break_rate(df: pd.DataFrame, col: str) -> list:
    """按特征分桶统计次日断开概率，返回 [{区间, 样本数, 断开概率%, 差pp}]"""
    edges, labels = FEATURE_BUCKETS[col]
    b = pd.cut(df[col], bins=edges, labels=labels)
    base = df["次日断开"].mean() * 100
    out = []
    for lab in labels:
        m = (b == lab)
        n = int(m.sum())
        if n == 0:
            continue
        p = df.loc[m, "次日断开"].mean() * 100
        out.append({"区间": lab, "样本数": n, "断开概率%": p, "差pp": p - base})
    return out


# ====================================================================
# 示范标的
# ====================================================================

def pick_examples(events: pd.DataFrame, name_map: dict, n: int = 10) -> list:
    """
    选取示范标的：恢复 / 未恢复各 n 只 —— 按中断日最新、代码去重。
    返回 [{代码, 名称, 中断日, 是否恢复, 次日涨幅%, 中断日成交额}, ...]
    """
    examples = []
    used = set()
    for resumed in (True, False):
        grp = events[events["是否恢复"] == resumed].sort_values("中断日", ascending=False)
        grp = grp[~grp["代码"].isin(used)].drop_duplicates("代码").head(n)
        used |= set(grp["代码"])
        for _, r in grp.iterrows():
            examples.append({
                "代码": r["代码"],
                "名称": name_map.get(r["代码"], ""),
                "中断日": pd.Timestamp(r["中断日"]).strftime("%Y-%m-%d"),
                "是否恢复": bool(r["是否恢复"]),
                "次日涨幅%": float(r["次日涨幅%"]),
                "中断日成交额": float(r["中断日成交额"]),
            })
    return examples


def collect_example_klines(data: StockData, examples: list):
    """
    采集示范标的 K 线：以中断日为中心前后约 30 个交易日。
    返回 {code: {dates, data, volumes, changes, prevs, name,
                break_date, resume_date, resumed}}
    （与 backtest/sim_core.collect_kline_for_trades 同构，复用弹窗 JS 约定）
    """
    kline_map = {}
    for ex in tqdm(examples, desc="收集K线", unit="只"):
        code, center = ex["代码"], pd.Timestamp(ex["中断日"])
        kdf = data.cache[(data.cache["代码"] == code) &
                         (data.cache["日期"] >= center - pd.Timedelta(days=50)) &
                         (data.cache["日期"] <= center + pd.Timedelta(days=50))].sort_values("日期")
        if len(kdf) < 10:
            continue
        kdf_dates = kdf["日期"].dt.strftime("%Y-%m-%d")
        center_idx = kdf_dates.tolist().index(ex["中断日"]) if ex["中断日"] in kdf_dates.tolist() else None
        if center_idx is None:
            continue
        s = max(0, center_idx - 30)
        e = min(len(kdf), center_idx + 30)
        if e - s < 60:
            s = max(0, e - 60) if e >= 60 else 0
            e = min(len(kdf), s + 60)
        kdf = kdf.iloc[s:e]
        # 中断日在切片内的新下标
        sl_dates = kdf["日期"].dt.strftime("%Y-%m-%d").tolist()
        bi = sl_dates.index(ex["中断日"])

        kdf = kdf.copy()
        kdf["涨跌%"] = kdf["收盘"].pct_change() * 100
        kdf["前收"] = kdf["收盘"].shift(1)
        changes = [round(v, 2) if not pd.isna(v) else 0 for v in kdf["涨跌%"].values]
        prevs = [round(v, 2) if not pd.isna(v) else 0 for v in kdf["前收"].values]
        volumes = [float(v) if not pd.isna(v) else 0 for v in kdf["成交额"].values]
        ohlc = [[float(r["开盘"]), float(r["收盘"]), float(r["最低"]), float(r["最高"])]
                for _, r in kdf.iterrows()]
        kline_map[code] = {
            "dates": sl_dates,
            "data": ohlc, "volumes": volumes, "changes": changes, "prevs": prevs,
            "name": ex["名称"],
            "break_date": ex["中断日"],
            "resume_date": sl_dates[bi + 1] if bi + 1 < len(sl_dates) else None,
            "resumed": ex["是否恢复"],
        }
    return kline_map


# ====================================================================
# 报告
# ====================================================================

# K线弹窗脚本（与 backtest/templates/dip_buy_report.html 同一机制，
# 新增 中断日/次日 标记点）。__KLINES__/__CODES__ 由 render_html 注入 JSON。
KLINE_JS = """
// K线弹窗（示范标的）
var KLINES=__KLINES__;
var klineChart=null;
var CODE_LIST=__CODES__;
var currentKlineCode=null;

function navKline(dir){
  if(!currentKlineCode)return;
  var idx=CODE_LIST.indexOf(currentKlineCode);
  if(idx<0)return;
  var next=idx+dir;
  if(next<0)next=CODE_LIST.length-1;
  if(next>=CODE_LIST.length)next=0;
  showKline(CODE_LIST[next]);
}

document.addEventListener('keydown',function(e){
  if(!currentKlineCode)return;
  if(e.key=='ArrowLeft'){e.preventDefault();navKline(-1);}
  if(e.key=='ArrowRight'){e.preventDefault();navKline(1);}
  if(e.key=='Escape'){e.preventDefault();closeKline();}
});

function showKline(key){
  var d=KLINES[key];if(!d)return;
  currentKlineCode=key;
  var idx=CODE_LIST.indexOf(key);
  document.getElementById("klineNav").textContent=(idx>=0?idx+1:'?')+"/"+CODE_LIST.length;
  document.getElementById("klineOverlay").classList.add("active");
  document.getElementById("klinePanel").classList.add("active");
  document.getElementById("klineTitle").textContent=key+" "+(d.name||"");
  setTimeout(function(){
    if(klineChart){klineChart.dispose();klineChart=null;}
    klineChart=echarts.init(document.getElementById("klineChart"));
    var dates=d.dates,ohlc=d.data,changes=d.changes||[],prevs=d.prevs||[],vols=d.volumes||[],ma5=[],ma10=[];
    for(var i=0;i<ohlc.length;i++){
      ma5.push(i>=4?(ohlc.slice(i-4,i+1).reduce(function(s,x){return s+x[1]},0)/5).toFixed(2):"-");
      ma10.push(i>=9?(ohlc.slice(i-9,i+1).reduce(function(s,x){return s+x[1]},0)/10).toFixed(2):"-");
    }
    var pts=[];
    var bi=d.dates.indexOf(d.break_date);
    if(bi>=0)pts.push({name:"中断",coord:[d.break_date,+(ohlc[bi][3]*1.04).toFixed(2)],value:"断",symbol:"pin",symbolSize:20,symbolRotate:180,label:{offset:[0,-12]},itemStyle:{color:"#9e9e9e"}});
    var ri=d.resume_date?d.dates.indexOf(d.resume_date):-1;
    if(ri>=0)pts.push({name:"次日",coord:[d.resume_date,+(ohlc[ri][3]*1.04).toFixed(2)],value:d.resumed?"复":"✗",symbol:"pin",symbolSize:20,symbolRotate:180,label:{offset:[0,-12]},itemStyle:{color:d.resumed?"#34a853":"#ea4335"}});
    klineChart.setOption({
      tooltip:{trigger:"axis",axisPointer:{type:"cross"},confine:true,
        formatter:function(ps){
          var r=ps[0].axisValue,chg=null,hh=0,ll=0,idx=-1,vol=0;
          for(var i=0;i<ps.length;i++){
            var p=ps[i];
            if(p.seriesName=="K线"&&p.dataIndex!=null){
              var raw=ohlc[p.dataIndex];
              var o=raw[0],c=raw[1],l=raw[2],h=raw[3];
              hh=h; ll=l; idx=p.dataIndex;
              r+="<br/>开: "+o+"  收: "+c+"  高: "+h+"  低: "+l;
            }
            if(p.seriesName=="成交量") vol=p.value;
            if(p.seriesName=="涨跌%") chg=p.value;
          }
          if(vol>0) r+="<br/>成交额: "+(vol/1e8).toFixed(2)+"亿";
          var prev=prevs[idx]||0,ampUp=0,ampDown=0;
          if(prev>0){ampUp=((hh-prev)/prev*100);ampDown=((ll-prev)/prev*100);}
          if(chg!=null) r+="<br/>涨跌幅: "+(chg>0?"+":"")+chg.toFixed(2)+"%  振幅: "+((ampUp-ampDown)).toFixed(2)+"% (↑"+ampUp.toFixed(1)+"% ↓"+ampDown.toFixed(1)+"%)";
          return r;
        }
      },
      axisPointer:{link:[{xAxisIndex:"all"}]},
      grid:[
        {left:"8%",right:"2%",top:"5%",height:"46%"},
        {left:"8%",right:"2%",top:"57%",height:"13%"},
        {left:"8%",right:"2%",top:"76%",height:"12%"}
      ],
      xAxis:[
        {data:dates,axisLabel:{rotate:30,fontSize:10},gridIndex:0},
        {data:dates,axisLabel:{show:false},gridIndex:1},
        {data:dates,axisLabel:{show:false},gridIndex:2}
      ],
      yAxis:[
        {scale:true,gridIndex:0,splitArea:{show:true}},
        {gridIndex:1,splitNumber:2,axisLabel:{formatter:function(v){return (v/1e8).toFixed(1)+"亿"}}},
        {gridIndex:2,splitNumber:3,axisLabel:{formatter:"{value}%"}}
      ],
      series:[
        {name:"K线",type:"candlestick",data:ohlc,xAxisIndex:0,yAxisIndex:0,
          dimensions:["open","close","lowest","highest"],
          itemStyle:{color:"#d32f2f",color0:"#34a853",borderColor:"#d32f2f",borderColor0:"#34a853"},barWidth:"60%",
          markPoint:{data:pts,label:{show:true,formatter:function(p){return p.value;},color:"#fff",fontWeight:"bold",fontSize:10}}},
        {name:"MA5",type:"line",data:ma5,xAxisIndex:0,yAxisIndex:0,smooth:true,lineStyle:{width:1,color:"#ff9800"},symbol:"none"},
        {name:"MA10",type:"line",data:ma10,xAxisIndex:0,yAxisIndex:0,smooth:true,lineStyle:{width:1,color:"#2196f3"},symbol:"none"},
        {name:"成交量",type:"bar",data:vols,xAxisIndex:1,yAxisIndex:1,
          itemStyle:{color:function(p){
            var i=p.dataIndex,o=ohlc[i][0],c=ohlc[i][1];
            return c>=o?"#d32f2f":"#34a853";
          }}},
        {name:"涨跌%",type:"bar",data:changes,xAxisIndex:2,yAxisIndex:2,
          itemStyle:{color:function(p){return p.value>=0?"#d32f2f":"#34a853"}}}
      ]
    });
    klineChart.resize();
  },100);
}
function closeKline(){
  currentKlineCode=null;
  document.getElementById("klineOverlay").classList.remove("active");
  document.getElementById("klinePanel").classList.remove("active");
}
"""


def pct(n, d):
    return f"{n / d * 100:.1f}%" if d > 0 else "—"


def print_summary(Ks, stats, baselines):
    print("\n" + "=" * 66)
    print("K线连续性中断后次日恢复概率（沪深主板）")
    print("=" * 66)
    for K in Ks:
        if K not in stats:
            continue
        s = stats[K]
        print(f"\n连续 {K} 天 → 中断 → 次日:")
        print(f"  事件数 {s['total']}    恢复 {s['resumed']}    "
              f"恢复概率 {pct(s['resumed'], s['total'])}")
        print(f"  次日有量(≥下限)子样本: 事件 {s['total_amt']}    "
              f"恢复概率 {pct(s['resumed_amt'], s['total_amt'])}")
        print(f"  次日平均涨幅: 恢复 {s['ret_resumed']:+.2f}% / "
              f"未恢复 {s['ret_not']:+.2f}%")
    print("\n基线对照:")
    print(f"  全体基线（任意一天次日满足）        : {pct(*baselines['all'])}"
          f"  ({baselines['all'][0]}/{baselines['all'][1]})")
    print(f"  任意中断基线（前一天断→次日恢复）   : {pct(*baselines['brk'])}"
          f"  ({baselines['brk'][0]}/{baselines['brk'][1]})")
    print(f"  任意中断基线（中断日有量）          : {pct(*baselines['brk_amt'])}"
          f"  ({baselines['brk_amt'][0]}/{baselines['brk_amt'][1]})")


def render_html(Ks, stats, baselines, params, examples=None, kline_map=None,
                warn_tables=None, warn_base=None) -> str:
    """生成自包含 HTML 汇总报告（风格与现有报告一致，含示范标的K线弹窗）"""
    primary = stats[Ks[0]]
    examples = examples or []
    kline_map = kline_map or {}
    warn_tables = warn_tables or {}

    def card(big, label, color=""):
        return (f'<div class="card"><span class="big {color}">{big}</span>'
                f'<span class="label">{label}</span></div>')

    # 年度明细（主档 K）
    yr = primary["events"].copy()
    yr["年份"] = pd.to_datetime(yr["中断日"]).dt.year
    year_rows = ""
    for y, g in yr.groupby("年份"):
        n = len(g)
        r = int(g["是否恢复"].sum())
        year_rows += (
            f"<tr><td>{y}</td><td class='num'>{n}</td>"
            f"<td class='num'>{pct(r, n)}</td>"
            f"<td class='num'>{g['次日涨幅%'].mean():+.2f}</td>"
            f"<td class='num'>{g['中断日成交额'].mean():.0f}</td></tr>"
        )

    # K 对比表
    k_rows = ""
    for K in Ks:
        s = stats[K]
        k_rows += (
            f"<tr><td class='num'>K={K}</td><td class='num'>{s['total']}</td>"
            f"<td class='num'>{pct(s['resumed'], s['total'])}</td>"
            f"<td class='num'>{pct(s['resumed_amt'], s['total_amt'])}</td>"
            f"<td class='num'>{s['ret_resumed']:+.2f}</td>"
            f"<td class='num'>{s['ret_not']:+.2f}</td></tr>"
        )

    # 结论
    bp = baselines["brk"]
    rp = (primary["resumed"], primary["total"])
    diff = rp[0] / rp[1] * 100 - bp[0] / bp[1] * 100 if rp[1] and bp[1] else 0
    verdict = (
        f"连续{params['K0']}天后中断的次日恢复概率 {pct(*rp)}，"
        f"任意中断基线的次日恢复概率 {pct(*bp)}，"
        f"两者相差 {diff:+.1f} 个百分点。"
    )
    if abs(diff) < 2:
        verdict += "差距在噪声范围内——「连续 K 天」本身不改变次日恢复概率，"
        "中断后是否恢复接近随机，不宜据此做次日买点。"
    elif diff > 0:
        verdict += "连续 K 天后中断的次日恢复概率略高于任意中断，"
        "但需要检验显著性后才可作为统计依据。"
    else:
        verdict += "连续 K 天后中断的次日恢复概率反而更低——"
        "长连续后的中断更可能是趋势转折，而非中继。"
    verdict += f"<br>全体基线（任意一天次日满足）为 {pct(*baselines['all'])}，"
    verdict += "反映该条件的自然延续率（连续条件本身就有惯性，基线远高于 50% 属正常）。"

    # ---- 断前预警表 ----
    warn_rows = ""
    warn_verdict = ""
    if warn_tables and warn_base is not None:
        for col, tabs in warn_tables.items():
            for k, t in enumerate(tabs):
                cls = "positive" if t["差pp"] > 0.5 else ("negative" if t["差pp"] < -0.5 else "")
                label = f"<td rowspan='{len(tabs)}'>{col}</td>" if k == 0 else ""
                warn_rows += (
                    f"<tr>{label}<td class='num'>{t['区间']}</td>"
                    f"<td class='num'>{t['样本数']:,}</td>"
                    f"<td class='num {cls}'>{t['断开概率%']:.1f}%</td>"
                    f"<td class='num {cls}'>{t['差pp']:+.1f}</td>"
                    f"<td><span class='bar' style='width:{min(t['断开概率%'] * 4, 200):.0f}px'></span></td></tr>"
                )
        ret_t = warn_tables["当日涨跌幅%"]
        if len(ret_t) >= 2:
            lo = min(ret_t, key=lambda x: x["断开概率%"])
            hi = max(ret_t, key=lambda x: x["断开概率%"])
            direction = "涨幅越大次日越容易断开" if hi is not ret_t[0] and lo is ret_t[0] \
                else ("涨幅越大次日越不容易断开" if lo is ret_t[-1] else "涨跌幅与断开概率呈非线性关系")
            warn_verdict = (
                f"基准断开概率 {warn_base:.1f}%。当日涨跌幅区分度：{lo['区间']}% 桶断开概率最低（{lo['断开概率%']:.1f}%），"
                f"{hi['区间']}% 桶最高（{hi['断开概率%']:.1f}%）——{direction}。"
            )
        vol_t = warn_tables.get("量比", [])
        if len(vol_t) >= 2:
            vl = min(vol_t, key=lambda x: x["断开概率%"])
            vh = max(vol_t, key=lambda x: x["断开概率%"])
            warn_verdict += f" 量比：{vl['区间']} 桶最低 {vl['断开概率%']:.1f}%，{vh['区间']} 桶最高 {vh['断开概率%']:.1f}%。"
        pos_t = warn_tables.get("收盘位置%", [])
        if len(pos_t) >= 2:
            pl = min(pos_t, key=lambda x: x["断开概率%"])
            ph = max(pos_t, key=lambda x: x["断开概率%"])
            warn_verdict += f" 收盘位置：{pl['区间']} 桶最低 {pl['断开概率%']:.1f}%，{ph['区间']} 桶最高 {ph['断开概率%']:.1f}%。"

    # ---- 示范标的表（恢复 / 未恢复 各 n 只）----
    def ex_rows(exs):
        rows = ""
        for ex in exs:
            cls = "positive" if ex["次日涨幅%"] > 0 else "negative"
            rows += (
                f"<tr><td class='code-clickable' onclick=\"showKline('{ex['代码']}')\">{ex['代码']}</td>"
                f"<td>{ex['名称']}</td><td class='num'>{ex['中断日']}</td>"
                f"<td class='num {cls}'>{ex['次日涨幅%']:+.2f}</td>"
                f"<td class='num'>{ex['中断日成交额']:.0f}</td></tr>"
            )
        return rows

    n_ex = len(examples) // 2
    resumed_rows = ex_rows(examples[:n_ex])
    broken_rows = ex_rows(examples[n_ex:])

    # ---- K线弹窗 JS（复用 backtest/templates 的弹窗机制）----
    kline_json = json.dumps(kline_map, ensure_ascii=False)
    code_list_json = json.dumps([ex["代码"] for ex in examples], ensure_ascii=False)
    kline_js = KLINE_JS.replace("__KLINES__", kline_json).replace("__CODES__", code_list_json)

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>连续性中断恢复回测</title>
<style>
:root{{--bg:#f5f5f5;--card:#fff;--blue:#1a73e8;--red:#ea4335;--green:#34a853;--text:#333}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,"PingFang SC","Helvetica Neue",sans-serif;background:var(--bg);color:var(--text);font-size:13px}}
.header{{background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff;padding:20px 32px}}
.header h1{{font-size:20px}}.header .sub{{color:#8892b0;font-size:12px;margin-top:4px}}
.summary{{display:flex;gap:20px;padding:16px 32px;background:var(--card);border-bottom:1px solid #e0e0e0;flex-wrap:wrap}}
.card{{text-align:center;min-width:70px}}
.card .big{{font-size:24px;font-weight:700;display:block}}
.big.green{{color:var(--green)}}.big.red{{color:var(--red)}}.big.blue{{color:var(--blue)}}
.card .label{{font-size:11px;color:#999}}
.params{{padding:12px 32px;background:#e8f0fe;font-size:12px;color:var(--blue);line-height:1.6}}
.panel{{margin:12px 32px;background:var(--card);border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.06);overflow:hidden}}
.panel h2{{font-size:15px;padding:12px 16px;background:#f8f9fa;border-bottom:1px solid #e0e0e0}}
.panel table{{width:100%}}
th{{background:#f8f9fa;font-weight:600;color:#555;padding:8px 10px;text-align:left;white-space:nowrap}}
td{{padding:6px 10px;border-bottom:1px solid #eee;white-space:nowrap}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}
.verdict{{margin:12px 32px;padding:14px 16px;background:#fff8e1;border-radius:8px;line-height:1.8;font-size:13px}}
.footer{{padding:16px 32px;color:#999;font-size:11px}}
.code-clickable{{cursor:pointer;color:var(--blue);font-family:"SF Mono",monospace}}
.code-clickable:hover{{background:#e8f0fe!important}}
.positive{{color:var(--green);font-weight:600}}.negative{{color:var(--red);font-weight:600}}
.kline-overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.25);z-index:999}}
.kline-overlay.active{{display:block}}
.kline-panel{{display:none;position:fixed;right:0;top:0;width:560px;height:100vh;background:rgba(255,255,255,0.92);box-shadow:-4px 0 20px rgba(0,0,0,.15);z-index:1000;overflow-y:auto;backdrop-filter:blur(4px)}}
.kline-panel.active{{display:block}}
.kline-panel .close-btn{{position:sticky;top:0;background:#1a73e8;color:#fff;border:none;width:100%;padding:12px;font-size:14px;cursor:pointer;z-index:1}}
.kline-panel .chart{{width:100%;height:600px}}
.bar{{display:inline-block;height:8px;border-radius:4px;background:var(--blue);opacity:.65;min-width:2px}}
</style></head><body>
<div class="header"><h1>🔗 回测：K线连续性中断后次日恢复概率</h1>
<div class="sub">连续 K 天满足接续条件 → 某日中断 → 统计次日恢复连续的概率（沪深主板）</div></div>
<div class="summary">
{card(pct(*rp), f"恢复概率（K={params['K0']}）", "blue")}
{card(str(primary['total']), "事件数")}
{card(pct(*bp), "任意中断基线", "green")}
{card(pct(*baselines['all']), "全体基线", "")}
{card(pct(primary['resumed_amt'], primary['total_amt']), "恢复概率·次日有量", "")}
</div>
<div class="params">💡 <b>事件定义：</b>中断日之前 K 天每天 最高 &gt; 前日最低 + 前日收盘 × {params['gap']}%，且 K 天日均成交额 ≥ {params['amount']:.0f} 万元；
中断日该条件不成立；统计次日的满足概率。<br>
💡 <b>股票池：</b>仅沪深主板（000/001/002/003/600/601/603/605），剔除 ST/退市，不含创业/科创/北交所 · 区间 {params['start']} 起 · {params['stocks']} 只</div>
<div class="panel"><h2>连续天数对比（K = b+1，即 近n日→近n-b日 的窗口长度）</h2>
<table><tr><th>连续天数</th><th>事件数</th><th>次日恢复概率</th><th>恢复概率（次日有量）</th><th>次日平均涨幅·恢复</th><th>次日平均涨幅·未恢复</th></tr>
{k_rows}</table></div>
<div class="panel"><h2>按年份（K={params['K0']}）</h2>
<table><tr><th>年份</th><th>事件数</th><th>恢复概率</th><th>次日平均涨幅%</th><th>中断日平均成交额(万元)</th></tr>
{year_rows}</table></div>
<div class="verdict"><b>结论：</b>{verdict}</div>
<div class="panel"><h2>断前预警 · 连续段最后一天特征 → 次日断开概率（K={params['K0']}）</h2>
<table><tr><th>指标</th><th>区间</th><th>样本数</th><th>次日断开概率</th><th>相对基准(pp)</th><th></th></tr>
{warn_rows}</table></div>
<div class="verdict"><b>预警结论：</b>{warn_verdict}</div>
<div class="panel"><h2>示范标的 · 中断后次日<b>恢复</b>（最新事件各10只，点击代码查看K线）</h2>
<table><tr><th>代码</th><th>名称</th><th>中断日</th><th>次日涨幅%</th><th>中断日成交额(万元)</th></tr>
{resumed_rows}</table></div>
<div class="panel"><h2>示范标的 · 中断后次日<b>未恢复</b></h2>
<table><tr><th>代码</th><th>名称</th><th>中断日</th><th>次日涨幅%</th><th>中断日成交额(万元)</th></tr>
{broken_rows}</table></div>
<div class="kline-overlay" id="klineOverlay" onclick="closeKline()"></div>
<div class="kline-panel" id="klinePanel">
  <button class="close-btn" onclick="closeKline()">
    ✕ <span id="klineTitle"></span>
    <span style="float:right;opacity:.6;font-size:11px" id="klineNav"></span>
  </button>
  <div class="chart" id="klineChart"></div>
</div>
<script>{kline_js}</script>
<script src="vendor/echarts.min.js"></script>
<script>window.echarts || document.write(`<script src='https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js'><\\/script>`);</script>
<div class="footer">生成: scripts/backtest_break_resume.py · 事件明细: output/break_resume_events.csv · 图上灰pin=中断日，绿pin=次日恢复，红pin=次日未恢复</div>
</body></html>"""


# ====================================================================
# 主程序
# ====================================================================

def main():
    p = argparse.ArgumentParser(description="K线连续性中断后次日恢复概率回测")
    p.add_argument("--streaks", type=str, default="5,6,7,8,9,10,11,12,13,14,15,20,30",
                   help="连续天数 K（=b+1），逗号分隔多档，如 2,3,5,8,10")
    p.add_argument("--streak", type=int, default=None,
                   help="单档连续天数（等同 --streaks）")
    p.add_argument("--gap", type=float, default=2, help="m%：接续区幅度")
    p.add_argument("--amount", type=float, default=5000,
                   help="日均成交额下限（万元，5000=5000万）")
    p.add_argument("--start", type=str, default="2015-01-01", help="回测起始日期")
    p.add_argument("--no-report", action="store_true", help="不输出 HTML/CSV")
    a = p.parse_args()

    Ks = [int(x) for x in (a.streaks if a.streak is None else str(a.streak)).split(",")]
    t0 = time.time()

    print(f"加载缓存并过滤股票池（{a.start} 起）...")
    data = StockData()
    series = load_series(data.cache, a.start)
    print(f"  股票池 {len(series)} 只")

    name_map = dict(zip(StockInfo().df["代码"], StockInfo().df["名称"]))

    stats = {}
    all_events = []
    for K in Ks:
        events = scan_universe(series, K, a.gap, a.amount)
        if len(events) == 0:
            print(f"K={K}: 无事件"); continue
        events.insert(0, "K", K)
        resumed = events[events["是否恢复"]]
        stats[K] = {
            "total": len(events),
            "resumed": len(resumed),
            "total_amt": int((events["次日成交额"] >= a.amount).sum()),
            "resumed_amt": int((resumed["次日成交额"] >= a.amount).sum()),
            "ret_resumed": resumed["次日涨幅%"].mean(),
            "ret_not": events[~events["是否恢复"]]["次日涨幅%"].mean(),
            "events": events,
        }
        all_events.append(events)

    baselines = compute_baselines(series, a.gap, a.amount)

    # 断前预警：连续段最后一天特征 → 次日断开概率
    warn_df = analyze_break_warning(series, Ks[0], a.gap, a.amount)
    warn_tables, warn_base = {}, None
    if len(warn_df):
        warn_base = warn_df["次日断开"].mean() * 100
        for col in FEATURE_BUCKETS:
            warn_tables[col] = bucket_break_rate(warn_df, col)
        print(f"\n断前预警（K={Ks[0]} 连续段最后一天特征 → 次日断开概率）:")
        print(f"  基准（无条件）断开概率: {warn_base:.1f}%  (样本 {len(warn_df):,})")
        for col, tabs in warn_tables.items():
            line = " | ".join(f"{t['区间']} {t['断开概率%']:.1f}%" for t in tabs)
            print(f"  {col}:\n    {line}")

    params = {"K0": Ks[0], "gap": a.gap, "amount": a.amount,
              "start": a.start, "stocks": len(series)}
    print_summary(Ks, stats, baselines)

    if not a.no_report and stats:
        all_df = pd.concat(all_events, ignore_index=True)
        all_df = all_df.rename(columns={
            "中断日成交额": "中断日成交额(万元)", "次日成交额": "次日成交额(万元)",
            "是否恢复": "次日是否恢复"})
        all_df.insert(1, "名称", all_df["代码"].map(name_map).fillna(""))
        all_df["中断日"] = pd.to_datetime(all_df["中断日"]).dt.strftime("%Y-%m-%d")
        all_df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8")
        print(f"\n✓ 事件明细: {OUTPUT_CSV} ({len(all_df)} 条)")

        # 示范标的（主档 K）：恢复/未恢复各 10 只 + K线数据
        examples = pick_examples(stats[Ks[0]]["events"], name_map, n=10)
        kline_map = collect_example_klines(data, examples)

        os.makedirs(os.path.dirname(OUTPUT_HTML), exist_ok=True)
        with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
            f.write(render_html(Ks, stats, baselines, params,
                                examples=examples, kline_map=kline_map,
                                warn_tables=warn_tables, warn_base=warn_base))
        print(f"✓ 报告: {OUTPUT_HTML}（示范标的 {len(examples)} 只）")

    print(f"\n耗时: {time.time() - t0:.0f}秒")


if __name__ == "__main__":
    main()
