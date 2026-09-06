#!/usr/bin/env python3
"""
整体行情统计页 — output/market_overview.html + output/market_mobile.html

内容：
  · 每日全市场资金量（两市成交额合计）+ 涨跌家数 + 涨停/跌停情绪
  · 大盘指数走势（上证指数/深证成指/沪深300，腾讯日K，ETF代理兜底会标注）
  · 板块强度变化（申万1级 31 个轮动热力图 + 申万2级 131 个强度排行榜）

数据口径：
  · 股票K线缓存（成交额单位元，覆盖沪深主板+创业+科创+北交（仪表盘可再过滤））
  · 涨跌停按 主板10%/创业板20%/ST≈5% 估算（ST 用当前名称近似，历史状态不可回溯）
  · 申万分类为当前分类回填历史，缺失填「未分类」

用法
----
$ python -m scripts.reports.gen_market           # 生成桌面+手机页面并刷新导航
$ python -m scripts.reports.gen_market --no-nav  # 不刷新导航
"""

import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)

from data.kline import StockData
from data.industry import StockInfo
from data.index import IndexData, INDEXES

OUTPUT_HTML = os.path.join(PROJECT_DIR, "output", "market_overview.html")
OUTPUT_MOBILE = os.path.join(PROJECT_DIR, "output", "market_mobile.html")
OUTPUT_HEATMAP = os.path.join(PROJECT_DIR, "output", "market_heatmap.html")

MAT_DAYS = 400      # 板块矩阵嵌入窗口（供排行/热力图 N 日切换）
HEAT_DAYS = [20, 40, 60, 120]   # 热力图页窗口按钮（交易日）
CHART_DAYS = [60, 120, 250, 0]   # 指数/资金/情绪图的时间窗口按钮（0=全部）


# ====================================================================
# 数据计算
# ====================================================================

def prep_cache(cache: pd.DataFrame, info: pd.DataFrame) -> pd.DataFrame:
    """前收/涨跌幅/涨跌停标记/行业映射，剔除无前收（IPO首日）"""
    cache = cache.sort_values(["代码", "日期"])
    g = cache.groupby("代码", sort=False)
    cache["前收"] = g["收盘"].shift(1)
    cache = cache.dropna(subset=["前收"])

    with np.errstate(divide="ignore", invalid="ignore"):
        cache["涨跌幅"] = np.where(cache["前收"] > 0,
                                   (cache["收盘"] / cache["前收"] - 1) * 100, 0.0)
    # 涨跌停：创业板 20%（2020-08-24 注册制后），其余 10%，ST≈5%
    is_cyb = cache["代码"].str.startswith(("sz300", "sz301")).values
    limit = np.where(is_cyb,
                     np.where(cache["日期"].values >= pd.Timestamp("2020-08-24"), 0.20, 0.10),
                     0.10)
    name_map = dict(zip(info["代码"], info["名称"]))
    st = cache["代码"].map(name_map).fillna("").str.contains("ST|退", regex=True).values
    limit = np.where(st, 0.05, limit)
    up_px = np.round(cache["前收"].values * (1 + limit), 2)
    dn_px = np.round(cache["前收"].values * (1 - limit), 2)
    cache["涨停"] = cache["收盘"].values >= up_px - 1e-6
    cache["跌停"] = cache["收盘"].values <= dn_px + 1e-6
    # 0/1 列（聚合快路径）
    cache["涨"] = (cache["涨跌幅"] > 0).astype("int8")
    cache["跌"] = (cache["涨跌幅"] < 0).astype("int8")
    cache["平"] = (cache["涨跌幅"] == 0).astype("int8")
    cache["涨停N"] = cache["涨停"].astype("int8")
    cache["跌停N"] = cache["跌停"].astype("int8")
    # 行业/名称 dict map（比 merge 快一个量级）
    l1_map = dict(zip(info["代码"], info["申万1级"]))
    l2_map = dict(zip(info["代码"], info["申万2级"]))
    cache["申万1级"] = cache["代码"].map(l1_map).fillna("未分类")
    cache["申万2级"] = cache["代码"].map(l2_map).fillna("未分类")
    cache["名称"] = cache["代码"].map(name_map).fillna("")
    cache["领涨名"] = cache["名称"] + " " + cache["代码"]
    return cache


def daily_series(cache: pd.DataFrame) -> pd.DataFrame:
    """全市场日度序列：成交额(元)/涨跌家数/涨停跌停/中位平均涨跌幅"""
    daily = cache.groupby("日期").agg(
        成交额=("成交额", "sum"),
        上涨家数=("涨", "sum"), 下跌家数=("跌", "sum"), 平盘家数=("平", "sum"),
        涨停家数=("涨停N", "sum"), 跌停家数=("跌停N", "sum"),
        中位涨跌幅=("涨跌幅", "median"), 平均涨跌幅=("涨跌幅", "mean"),
    ).reset_index()
    daily["成交额亿"] = daily["成交额"] / 1e8
    n = (daily["上涨家数"] + daily["下跌家数"]).replace(0, np.nan)
    daily["上涨占比"] = daily["上涨家数"] / n * 100
    return daily


def sector_daily(cache: pd.DataFrame, col: str) -> pd.DataFrame:
    """板块日度聚合 + 领涨股（全市场排序后 groupby first 快路径）"""
    agg = cache.groupby(["日期", col], sort=False).agg(
        均涨跌幅=("涨跌幅", "mean"),
        成交额=("成交额", "sum"),
        上涨家数=("涨", "sum"),
        下跌家数=("跌", "sum"),
    ).reset_index()
    tmp = cache[["日期", col, "涨跌幅", "领涨名"]].sort_values("涨跌幅", ascending=False)
    leader = tmp.groupby(["日期", col], sort=False)["领涨名"].first().reset_index()
    agg = agg.merge(leader, on=["日期", col], how="left")
    agg["成交额亿"] = agg["成交额"] / 1e8
    return agg


def index_series(idx_cache: pd.DataFrame):
    """指数收盘/当日涨跌幅透视 + 最新来源标注"""
    pivot = idx_cache.pivot_table(index="日期", columns="代码",
                                  values="收盘", aggfunc="last")
    pivot = pivot[list(INDEXES.keys())]
    chg = pivot.pct_change() * 100
    src = idx_cache.sort_values("日期").groupby("代码")["来源"].last()
    return pivot, chg, src


# ====================================================================
# 数据载荷（桌面/手机共用）
# ====================================================================

def build_payload(daily, sd1, sd2, piv, chg, src, latest_date) -> dict:
    """页面前的全部数据：瓷砖值 + 各图 JSON + 板块明细表"""

    def r2(v):
        return None if pd.isna(v) else round(float(v), 2)

    # ---- 最新交易日瓷砖值 ----
    last = daily.iloc[-1]
    amt_wan = last["成交额"] / 1e12
    med = last["中位涨跌幅"]
    idx_tiles = ""
    for code, name in INDEXES.items():
        px = piv[code].iloc[-1]
        c = chg[code].iloc[-1]
        cls = "red" if c >= 0 else "green"
        etf_tag = '<span class="sub">(ETF代理)</span>' if src.get(code) == "etf" else ""
        idx_tiles += (f'<div class="stat"><span class="big {cls}">{px:,.2f}</span>'
                      f'<span class="label">{name} {c:+.2f}%</span>{etf_tag}</div>')

    # ---- 日度序列 JSON ----
    daily_json = json.dumps({
        "dates": [d.strftime("%Y-%m-%d") for d in daily["日期"]],
        "amt": [round(float(v), 0) for v in daily["成交额亿"]],
        "med": [r2(v) for v in daily["中位涨跌幅"]],
        "upRatio": [r2(v) for v in daily["上涨占比"]],
        "limitUp": [int(v) for v in daily["涨停家数"]],
        "limitDown": [int(v) for v in daily["跌停家数"]],
    }, ensure_ascii=False)

    # ---- 指数 JSON ----
    dates = [d.strftime("%Y-%m-%d") for d in piv.index]
    idx_colors = {"sh000001": "#1a73e8", "sz399001": "#d32f2f", "sh000300": "#34a853", "sz399006": "#9333ea", "sh000688": "#0891b2"}
    idx_json = json.dumps({
        "dates": dates,
        "series": [{
            "name": INDEXES[code], "close": [round(float(v), 2) for v in piv[code]],
            "chg": [r2(v) for v in chg[code]],
            "color": idx_colors.get(code, ["#2563eb", "#9333ea", "#0891b2", "#d97706"][sum(code.encode()) % 4]),
        } for code in INDEXES],
    }, ensure_ascii=False)

    # ---- 申万1级矩阵（最近 MAT_DAYS 交易日，热力图页用）----
    d1 = sd1.sort_values("日期")
    h_dates = d1["日期"].drop_duplicates().tail(MAT_DAYS)
    h = d1[d1["日期"].isin(h_dates)].pivot_table(
        index="日期", columns="申万1级", values="均涨跌幅", aggfunc="last")
    l1_json = json.dumps({
        "dates": [d.strftime("%Y-%m-%d") for d in h.index],
        "sectors": list(h.columns),
        # 行优先矩阵 data[日][板块]（与 l2_json 同构，JS 侧统一转热力图三元组）
        "data": [[r2(v) for v in row] for row in h.values],
    }, ensure_ascii=False)

    # ---- 申万2级矩阵（最近 MAT_DAYS 交易日，供排行 N 日切换）----
    d2 = sd2.sort_values("日期")
    mat_dates = d2["日期"].drop_duplicates().tail(MAT_DAYS)
    m = d2[d2["日期"].isin(mat_dates)].pivot_table(
        index="日期", columns="申万2级", values="均涨跌幅", aggfunc="last")
    l2_json = json.dumps({
        "dates": [d.strftime("%Y-%m-%d") for d in m.index],
        "sectors": list(m.columns),
        "data": [[r2(v) for v in row] for row in m.values],
    }, ensure_ascii=False)

    # ---- 申万1级最新日排行（手机版条形图用）----
    s1d = sd1[sd1["日期"] == sd1["日期"].max()].sort_values("均涨跌幅", ascending=False)
    s1_json = json.dumps({
        "sectors": list(s1d["申万1级"]),
        "vals": [r2(v) for v in s1d["均涨跌幅"]],
    }, ensure_ascii=False)

    # ---- 最新日板块明细表（1级 / 2级）----
    def sec_table(sd, col):
        lastd = sd[sd["日期"] == sd["日期"].max()].sort_values("均涨跌幅", ascending=False)
        rows = ""
        for _, r in lastd.iterrows():
            cls = "positive" if r["均涨跌幅"] > 0 else ("negative" if r["均涨跌幅"] < 0 else "")
            leader = str(r["领涨名"]).strip()
            code = leader.split(" ")[-1] if leader else ""
            lname = leader[: -len(code) - 1] if leader else ""
            code_html = (f'<span class="code-sh">{code}</span>' if code.startswith("sh")
                         else f'<span class="code-sz">{code}</span>') if code else ""
            rows += (f"<tr><td>{r[col]}</td>"
                     f"<td class='num {cls}'>{r['均涨跌幅']:+.2f}</td>"
                     f"<td class='num'>{r['成交额亿']:.0f}</td>"
                     f"<td class='num'>{r['上涨家数']}/{r['下跌家数']}</td>"
                     f"<td>{lname} {code_html}</td></tr>")
        return rows

    return {
        "latest_date": latest_date,
        "amt_wan": amt_wan, "med": med,
        "up": int(last["上涨家数"]), "down": int(last["下跌家数"]),
        "lim_up": int(last["涨停家数"]), "lim_down": int(last["跌停家数"]),
        "idx_tiles": idx_tiles,
        # 手机瓷砖：三指数最新点位/涨跌幅
        "sh_px": piv["sh000001"].iloc[-1], "sh_chg": chg["sh000001"].iloc[-1],
        "sz_px": piv["sz399001"].iloc[-1], "sz_chg": chg["sz399001"].iloc[-1],
        "hs_px": piv["sh000300"].iloc[-1], "hs_chg": chg["sh000300"].iloc[-1],
        "daily_json": daily_json, "idx_json": idx_json,
        "l1_json": l1_json, "l2_json": l2_json, "s1_json": s1_json,
        "tbl1": sec_table(sd1, "申万1级"), "tbl2": sec_table(sd2, "申万2级"),
    }


# ====================================================================
# HTML 渲染
# ====================================================================

CSS = """
:root{--bg:#f5f5f5;--card:#fff;--blue:#1a73e8;--red:#d32f2f;--green:#34a853;--text:#333}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--text);font-size:13px}
.header{background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff;padding:20px 32px}
.header h1{font-size:20px}.header .sub{color:#8892b0;font-size:12px;margin-top:4px}
.stats{display:flex;gap:20px;padding:16px 32px;background:var(--card);border-bottom:1px solid #e0e0e0;flex-wrap:wrap}
.stat{text-align:center;min-width:70px}
.stat .big{font-size:24px;font-weight:700;display:block}
.big.red{color:var(--red)}.big.green{color:var(--green)}.big.blue{color:var(--blue)}
.stat .label{font-size:11px;color:#999}
.stat .sub{font-size:10px;color:#bbb}
.panel{margin:12px 32px;background:var(--card);border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.06);overflow:hidden}
.panel h2{font-size:15px;padding:12px 16px;background:#f8f9fa;border-bottom:1px solid #e0e0e0;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.rng-btn{display:inline-block;padding:3px 10px;margin:0 2px;border:1px solid #ccc;border-radius:4px;cursor:pointer;font-size:12px;color:#555;background:#fff}
.rng-btn.active{background:var(--blue);border-color:var(--blue);color:#fff}
.chart{height:380px;width:100%;padding:8px}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(560px,1fr));gap:0}
.panel table{width:100%}
th{background:#f8f9fa;font-weight:600;color:#555;padding:8px 10px;text-align:left;white-space:nowrap}
td{padding:6px 10px;border-bottom:1px solid #eee;white-space:nowrap}
.num{text-align:right;font-variant-numeric:tabular-nums}
.positive{color:var(--red);font-weight:600}.negative{color:var(--green);font-weight:600}
.code-sh{color:var(--red);font-family:"SF Mono",monospace}.code-sz{color:var(--blue);font-family:"SF Mono",monospace}
.scroll{max-height:480px;overflow-y:auto}
.footer{padding:16px 32px;color:#999;font-size:11px;line-height:1.7}
@media(max-width:768px){.header,.stats,.panel,.footer{padding-left:12px;padding-right:12px}
  .panel{margin:8px}.chart{height:320px}}
"""

# 图表 JS（占位符 __DAILY__/__IDX__/__L1__/__L2__ 由 render 注入 JSON）
CHART_JS = r"""
var DAILY = __DAILY__;
var IDX = __IDX__;
var L2 = __L2__;

var idxChart = echarts.init(document.getElementById('idx'));
var amtChart = echarts.init(document.getElementById('amt'));
var limChart = echarts.init(document.getElementById('lim'));
var rankChart = echarts.init(document.getElementById('rank'));

// ---- 指数走势：三种视图（区间累计% / 当日涨跌% / 真实点位）----
var idxView = 'cum', curS = 0, curIdxWin = 120;
var PX_AXES = [
  {type: 'value', name: '上证', position: 'left', axisLabel: {fontSize: 10}, splitLine: {show: false}},
  {type: 'value', name: '深成', position: 'right', axisLabel: {fontSize: 10}, splitLine: {show: false}},
  {type: 'value', name: '沪深300', position: 'right', offset: 70, axisLabel: {fontSize: 10}, splitLine: {show: false}}
];

function setIdxWindow(days){
  var d = IDX.dates, s = days > 0 ? d.length - days : 0;
  if (s < 0) s = 0;
  curS = s;
  var xd = d.slice(s), series = [];
  IDX.series.forEach(function(ix, si){
    var data;
    if (idxView == 'cum'){
      var base = ix.close[s] || 1;
      data = ix.close.slice(s).map(function(v){ return +((v / base - 1) * 100).toFixed(2); });
    } else if (idxView == 'daily'){
      data = ix.chg.slice(s);
    } else {
      data = ix.close.slice(s);
    }
    series.push({name: ix.name, type: 'line', data: data, symbol: 'none',
      yAxisIndex: idxView == 'px' ? si : 0,
      lineStyle: {width: 1.5}, itemStyle: {color: ix.color}});
  });
  idxChart.setOption({
    grid: {left: 55, right: idxView == 'px' ? 105 : 45, top: 32, bottom: 40},
    xAxis: {data: xd},
    yAxis: idxView == 'px' ? PX_AXES :
      [{type: 'value', name: idxView == 'cum' ? '区间累计%' : '当日涨跌%',
        scale: true, axisLabel: {fontSize: 10}}],
    series: series, legend: {data: IDX.series.map(function(x){return x.name})},
    tooltip: {trigger: 'axis', formatter: function(ps){
      var i = ps[0].dataIndex, r = ps[0].axisValue + '<br/>';
      ps.forEach(function(p){
        var ix = IDX.series[p.seriesIndex];
        var main = idxView == 'px' ? ix.close[curS + i].toFixed(2) + ' 点'
                 : (+p.value).toFixed(2) + '%';
        r += p.marker + p.seriesName + ': ' + main +
             '（点位 ' + ix.close[curS + i].toFixed(2) +
             ' · 当日 ' + (ix.chg[curS + i] >= 0 ? '+' : '') + ix.chg[curS + i].toFixed(2) + '%）<br/>';
      });
      return r;}}
  });
}

function switchIdxView(v, btn){
  idxView = v;
  document.querySelectorAll('.rng-idxview button').forEach(function(b){
    b.classList.toggle('active', b.getAttribute('data-view') === v);
  });
  setIdxWindow(curIdxWin);
}

// ---- 成交额 + 上涨占比 ----
function setAmtWindow(days){
  var d = DAILY.dates, s = days > 0 ? d.length - days : 0;
  if (s < 0) s = 0;
  var xd = d.slice(s);
  amtChart.setOption({
    xAxis: {data: xd},
    series: [
      {name: '两市成交额(亿)', type: 'bar', yAxisIndex: 0, barWidth: '60%',
       data: DAILY.amt.slice(s).map(function(v, i){
         return {value: v, itemStyle: {color: DAILY.med[s+i] >= 0 ? '#d32f2f' : '#34a853'}};})},
      {name: '上涨占比%', type: 'line', yAxisIndex: 1, data: DAILY.upRatio.slice(s),
       symbol: 'none', lineStyle: {width: 1.5, color: '#1a73e8'}}
    ]});
}

// ---- 涨停/跌停家数 ----
function setLimWindow(days){
  var d = DAILY.dates, s = days > 0 ? d.length - days : 0;
  if (s < 0) s = 0;
  limChart.setOption({
    xAxis: {data: d.slice(s)},
    series: [
      {name: '涨停', type: 'bar', stack: 'x', data: DAILY.limitUp.slice(s), itemStyle: {color: '#d32f2f'}},
      {name: '跌停', type: 'bar', stack: 'x', data: DAILY.limitDown.slice(s), itemStyle: {color: '#34a853'}}
    ]});
}

function setRange(days){
  curIdxWin = days;
  document.querySelectorAll('.rng-time button').forEach(function(b){
    b.classList.toggle('active', +b.getAttribute('data-days') === days);
  });
  setIdxWindow(days); setAmtWindow(days); setLimWindow(days);
}

// ---- 申万2级强度排行：近 N 日等权复利 ----
function setRankWindow(N, btn){
  document.querySelectorAll('.rng-rank button').forEach(function(b){b.classList.remove('active')});
  if (btn) btn.classList.add('active');
  var mat = L2.data, secs = L2.sectors, dlen = L2.dates.length;
  var s = Math.max(0, dlen - N), res = [];
  for (var si = 0; si < secs.length; si++){
    var acc = 1, cnt = 0;
    for (var di = s; di < dlen; di++){
      var v = mat[di][si];
      if (v == null) continue;
      acc *= (1 + v / 100); cnt++;
    }
    res.push({sec: secs[si], val: +((acc - 1) * 100).toFixed(2), cnt: cnt});
  }
  res.sort(function(a, b){return b.val - a.val});
  var top = res.slice(0, 15).reverse(), bot = res.slice(-10).reverse();
  var cats = [], vals = [];
  top.forEach(function(r){cats.push(r.sec); vals.push(r.val)});
  bot.forEach(function(r){cats.push(r.sec); vals.push(r.val)});
  rankChart.setOption({
    yAxis: {data: cats},
    series: [{name: '累计涨幅%', type: 'bar', data: vals.map(function(v){
      return {value: v, itemStyle: {color: v >= 0 ? '#d32f2f' : '#34a853'}};}),
      label: {show: true, position: 'right', fontSize: 10, formatter: function(p){return p.value > 0 ? '+' + p.value : p.value;}}}],
    title: {text: '近 ' + N + ' 个交易日', left: 'right', top: 0, textStyle: {fontSize: 11, color: '#999'}}
  });
}

// ---- 基础 option ----
function axisCommon(){return {type: 'category', boundaryGap: true, axisLabel: {fontSize: 10}}}
amtChart.setOption({grid: {left: 60, right: 50, top: 30, bottom: 40},
  yAxis: [{type: 'value', name: '亿元'}, {type: 'value', min: 0, max: 100, name: '%'}],
  xAxis: axisCommon(), legend: {top: 0},
  tooltip: {trigger: 'axis', formatter: function(ps){
    var r = ps[0].axisValue;
    ps.forEach(function(p){
      r += '<br/>' + p.marker + p.seriesName + ': ' +
        (p.seriesName.indexOf('占比') >= 0 ? (+p.value).toFixed(1) + '%' : (+p.value).toFixed(0));
    });
    return r;}}});
limChart.setOption({grid: {left: 45, right: 20, top: 30, bottom: 40},
  yAxis: {type: 'value'}, xAxis: axisCommon(), legend: {top: 0},
  tooltip: {trigger: 'axis'}});
rankChart.setOption({grid: {left: 130, right: 55, top: 30, bottom: 30},
  xAxis: {type: 'value'}, yAxis: {type: 'category', data: [], axisLabel: {fontSize: 10}},
  tooltip: {trigger: 'axis'}});

setRange(120);
setRankWindow(20, null);
window.addEventListener('resize', function(){
  [idxChart, amtChart, limChart, rankChart].forEach(function(c){c.resize()});
});

// ---- 板块明细表 tab ----
function switchSector(level, btn){
  document.querySelectorAll('.rng-sector button').forEach(function(b){b.classList.remove('active')});
  btn.classList.add('active');
  document.getElementById('sec1').style.display = level == 1 ? '' : 'none';
  document.getElementById('sec2').style.display = level == 2 ? '' : 'none';
}
"""


def render_html(payload: dict, generated_at: str) -> str:
    """桌面版页面"""
    latest_date = payload["latest_date"]
    med, up, down = payload["med"], payload["up"], payload["down"]
    lim_up, lim_down = payload["lim_up"], payload["lim_down"]
    amt_wan = payload["amt_wan"]

    time_btns = "".join(
        f"<button class='rng-btn{' active' if d == 120 else ''}' data-days='{d}' onclick='setRange({d})'>"
        f"{d if d > 0 else '全部'}</button>"
        for d in CHART_DAYS)
    rank_btns = "".join(
        f"<button class='rng-btn{' active' if n == 20 else ''}' onclick='setRankWindow({n}, this)'>{n}日</button>"
        for n in (5, 10, 20, 60))
    heat_days_text = "/".join(str(d) for d in HEAT_DAYS)

    js = CHART_JS.replace("__DAILY__", payload["daily_json"]).replace("__IDX__", payload["idx_json"]) \
        .replace("__L2__", payload["l2_json"])

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>整体行情统计 — {latest_date}</title>
<style>{CSS}</style></head><body>
<div class="header"><h1>📈 整体行情统计</h1>
<div class="sub">每日资金量 · 大盘指数走势 · 板块强度轮动（申万1/2级） · 数据截至 {latest_date} · 生成 {generated_at}</div></div>
<div class="stats">
<div class="stat"><span class="big blue">{amt_wan:.2f}万亿</span><span class="label">两市成交额</span></div>
<div class="stat"><span class="big red">{up:,}</span><span class="label">上涨家数</span></div>
<div class="stat"><span class="big green">{down:,}</span><span class="label">下跌家数</span></div>
<div class="stat"><span class="big red">{lim_up}</span><span class="label">涨停家数</span></div>
<div class="stat"><span class="big green">{lim_down}</span><span class="label">跌停家数</span></div>
<div class="stat"><span class="big {'red' if med >= 0 else 'green'}">{med:+.2f}%</span><span class="label">中位涨跌幅</span></div>
{payload["idx_tiles"]}
</div>
<div class="panel"><h2>大盘指数走势<span class="rng-idxview">
<button class="rng-btn active" data-view="cum" onclick="switchIdxView('cum', this)">区间累计%</button>
<button class="rng-btn" data-view="daily" onclick="switchIdxView('daily', this)">当日涨跌%</button>
<button class="rng-btn" data-view="px" onclick="switchIdxView('px', this)">点位</button></span>
<span class="rng-time">{time_btns}</span></h2>
<div class="chart" id="idx"></div></div>
<div class="charts">
<div class="panel"><h2>每日成交额与上涨占比<span class="rng-time">{time_btns}</span></h2>
<div class="chart" id="amt"></div></div>
<div class="panel"><h2>涨停 / 跌停家数（市场情绪）<span class="rng-time">{time_btns}</span></h2>
<div class="chart" id="lim"></div></div>
</div>
<div class="charts">
<div class="panel"><h2>申万2级板块强度排行（近N日累计涨幅，最强15 + 最弱10）<span class="rng-rank">{rank_btns}</span></h2>
<div class="chart" id="rank"></div></div>
<div class="panel"><h2>板块轮动热力图（申万1/2级）</h2>
<div style="padding:16px;font-size:13px;line-height:1.8">轮动热力图已移至独立页——板块名完整显示，粒度/窗口（{heat_days_text}日）自由切换，2级可滚动查看全 131 个行业。<br>
<a href="market_heatmap.html" style="color:var(--blue);font-weight:600">打开板块轮动热力图 →</a></div>
</div>
</div>
<div class="panel"><h2>最新交易日板块明细<span class="rng-sector">
<button class="rng-btn active" onclick="switchSector(1, this)">申万1级</button>
<button class="rng-btn" onclick="switchSector(2, this)">申万2级</button></span></h2>
<div id="sec1"><table><tr><th>板块</th><th>均涨跌幅%</th><th>成交额(亿)</th><th>涨/跌家数</th><th>领涨股</th></tr>{payload["tbl1"]}</table></div>
<div id="sec2" style="display:none"><div class="scroll"><table><tr><th>板块</th><th>均涨跌幅%</th><th>成交额(亿)</th><th>涨/跌家数</th><th>领涨股</th></tr>{payload["tbl2"]}</table></div></div>
</div>
<script src="vendor/echarts.min.js"></script>
<script>window.echarts || document.write(`<script src='https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js'><\\/script>`);</script>
<script>{js}</script>
<div class="footer">生成: scripts/reports/gen_market.py · 数据: 个股K线缓存 + 腾讯指数日K（每日 18:30 自动更新）<br>
口径: 涨跌停按 主板10%/创业板20%/ST≈5% 估算（ST 用当前名称近似） · 申万分类为当前分类回填历史，缺失记「未分类」 · 两市成交额默认按缓存合计；选股仪表盘仍可过滤主板</div>
</body></html>"""


MOBILE_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#f5f5f5;color:#333;font-size:13px}
.header{background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff;padding:14px 12px}
.header h1{font-size:16px}.header .sub{color:#8892b0;font-size:11px;margin-top:3px}
.tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:#e0e0e0;border-bottom:1px solid #e0e0e0}
.tile{background:#fff;padding:8px 4px;text-align:center}
.tile .v{font-size:16px;font-weight:700;display:block}
.tile .l{font-size:10px;color:#999}
.v.red{color:#d32f2f}.v.green{color:#34a853}.v.blue{color:#1a73e8}
.panel{margin:8px;background:#fff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.06);overflow:hidden}
.panel h2{font-size:13px;padding:8px 10px;background:#f8f9fa;border-bottom:1px solid #e0e0e0;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.rng-btn{display:inline-block;padding:2px 8px;margin:0 1px;border:1px solid #ccc;border-radius:4px;cursor:pointer;font-size:11px;color:#555;background:#fff}
.rng-btn.active{background:#1a73e8;border-color:#1a73e8;color:#fff}
.chart{height:280px;width:100%;padding:4px}
.scroll-x{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:12px;white-space:nowrap}
th{background:#f8f9fa;font-weight:600;color:#555;padding:6px 8px;text-align:left}
td{padding:5px 8px;border-bottom:1px solid #eee}
.num{text-align:right;font-variant-numeric:tabular-nums}
.positive{color:#d32f2f;font-weight:600}.negative{color:#34a853;font-weight:600}
.code-sh{color:#d32f2f;font-family:"SF Mono",monospace}.code-sz{color:#1a73e8;font-family:"SF Mono",monospace}
.footer{padding:12px;color:#999;font-size:10px;line-height:1.6}
"""

# 手机版图表 JS（占位符 __DAILY__/__IDX__/__L2__/__S1__）
MOBILE_JS = r"""
var DAILY = __DAILY__;
var IDX = __IDX__;
var L2 = __L2__;
var S1 = __S1__;

var idxChart = echarts.init(document.getElementById('idx'));
var amtChart = echarts.init(document.getElementById('amt'));
var limChart = echarts.init(document.getElementById('lim'));
var rankChart = echarts.init(document.getElementById('rank'));
var s1Chart = echarts.init(document.getElementById('s1'));

var idxView = 'cum', curS = 0, curIdxWin = 120;
var PX_AXES = [
  {type: 'value', name: '上证', position: 'left', axisLabel: {fontSize: 9}, splitLine: {show: false}},
  {type: 'value', name: '深成', position: 'right', axisLabel: {fontSize: 9}, splitLine: {show: false}},
  {type: 'value', name: '沪深300', position: 'right', offset: 45, axisLabel: {fontSize: 9}, splitLine: {show: false}}
];

function setIdxWindow(days){
  var d = IDX.dates, s = days > 0 ? d.length - days : 0;
  if (s < 0) s = 0;
  curS = s;
  var xd = d.slice(s), series = [];
  IDX.series.forEach(function(ix, si){
    var data;
    if (idxView == 'cum'){
      var base = ix.close[s] || 1;
      data = ix.close.slice(s).map(function(v){ return +((v / base - 1) * 100).toFixed(2); });
    } else if (idxView == 'daily'){
      data = ix.chg.slice(s);
    } else {
      data = ix.close.slice(s);
    }
    series.push({name: ix.name, type: 'line', data: data, symbol: 'none',
      yAxisIndex: idxView == 'px' ? si : 0,
      lineStyle: {width: 1.5}, itemStyle: {color: ix.color}});
  });
  idxChart.setOption({
    grid: {left: 40, right: idxView == 'px' ? 80 : 30, top: 28, bottom: 30},
    xAxis: {data: xd},
    yAxis: idxView == 'px' ? PX_AXES :
      [{type: 'value', name: idxView == 'cum' ? '区间累计%' : '当日涨跌%',
        scale: true, axisLabel: {fontSize: 9}}],
    series: series, legend: {data: IDX.series.map(function(x){return x.name}), top: 0},
    tooltip: {trigger: 'axis', formatter: function(ps){
      var i = ps[0].dataIndex, r = ps[0].axisValue + '<br/>';
      ps.forEach(function(p){
        var ix = IDX.series[p.seriesIndex];
        var main = idxView == 'px' ? ix.close[curS + i].toFixed(2) + ' 点'
                 : (+p.value).toFixed(2) + '%';
        r += p.marker + p.seriesName + ': ' + main +
             '（点位 ' + ix.close[curS + i].toFixed(2) +
             ' · 当日 ' + (ix.chg[curS + i] >= 0 ? '+' : '') + ix.chg[curS + i].toFixed(2) + '%）<br/>';
      });
      return r;}}
  });
}

function switchIdxView(v, btn){
  idxView = v;
  document.querySelectorAll('.rng-idxview button').forEach(function(b){
    b.classList.toggle('active', b.getAttribute('data-view') === v);
  });
  setIdxWindow(curIdxWin);
}

function setAmtWindow(days){
  var d = DAILY.dates, s = days > 0 ? d.length - days : 0;
  if (s < 0) s = 0;
  amtChart.setOption({
    xAxis: {data: d.slice(s)},
    series: [
      {name: '成交额(亿)', type: 'bar', yAxisIndex: 0, barWidth: '60%',
       data: DAILY.amt.slice(s).map(function(v, i){
         return {value: v, itemStyle: {color: DAILY.med[s+i] >= 0 ? '#d32f2f' : '#34a853'}};})},
      {name: '上涨占比%', type: 'line', yAxisIndex: 1, data: DAILY.upRatio.slice(s),
       symbol: 'none', lineStyle: {width: 1.5, color: '#1a73e8'}}
    ]});
}

function setLimWindow(days){
  var d = DAILY.dates, s = days > 0 ? d.length - days : 0;
  if (s < 0) s = 0;
  limChart.setOption({
    xAxis: {data: d.slice(s)},
    series: [
      {name: '涨停', type: 'bar', stack: 'x', data: DAILY.limitUp.slice(s), itemStyle: {color: '#d32f2f'}},
      {name: '跌停', type: 'bar', stack: 'x', data: DAILY.limitDown.slice(s), itemStyle: {color: '#34a853'}}
    ]});
}

function setRange(days){
  curIdxWin = days;
  document.querySelectorAll('.rng-time button').forEach(function(b){
    b.classList.toggle('active', +b.getAttribute('data-days') === days);
  });
  setIdxWindow(days); setAmtWindow(days); setLimWindow(days);
}

function setRankWindow(N, btn){
  document.querySelectorAll('.rng-rank button').forEach(function(b){b.classList.remove('active')});
  if (btn) btn.classList.add('active');
  var mat = L2.data, secs = L2.sectors, dlen = L2.dates.length;
  var s = Math.max(0, dlen - N), res = [];
  for (var si = 0; si < secs.length; si++){
    var acc = 1;
    for (var di = s; di < dlen; di++){
      var v = mat[di][si];
      if (v == null) continue;
      acc *= (1 + v / 100);
    }
    res.push({sec: secs[si], val: +((acc - 1) * 100).toFixed(2)});
  }
  res.sort(function(a, b){return b.val - a.val});
  var top = res.slice(0, 15).reverse(), bot = res.slice(-10).reverse();
  var cats = [], vals = [];
  top.forEach(function(r){cats.push(r.sec); vals.push(r.val)});
  bot.forEach(function(r){cats.push(r.sec); vals.push(r.val)});
  rankChart.setOption({
    grid: {left: 100, right: 40, top: 22, bottom: 25},
    yAxis: {data: cats, axisLabel: {fontSize: 9}},
    series: [{name: '累计涨幅%', type: 'bar', data: vals.map(function(v){
      return {value: v, itemStyle: {color: v >= 0 ? '#d32f2f' : '#34a853'}};}),
      label: {show: true, position: 'right', fontSize: 9, formatter: function(p){return p.value > 0 ? '+' + p.value : p.value;}}}],
    title: {text: '近 ' + N + ' 日', left: 'right', top: 0, textStyle: {fontSize: 10, color: '#999'}}
  });
}

function axisCommon(){return {type: 'category', boundaryGap: true, axisLabel: {fontSize: 9}}}
amtChart.setOption({grid: {left: 45, right: 40, top: 26, bottom: 30},
  yAxis: [{type: 'value', name: '亿元'}, {type: 'value', min: 0, max: 100, name: '%'}],
  xAxis: axisCommon(), legend: {top: 0},
  tooltip: {trigger: 'axis', formatter: function(ps){
    var r = ps[0].axisValue;
    ps.forEach(function(p){
      r += '<br/>' + p.marker + p.seriesName + ': ' +
        (p.seriesName.indexOf('占比') >= 0 ? (+p.value).toFixed(1) + '%' : (+p.value).toFixed(0));
    });
    return r;}}});
limChart.setOption({grid: {left: 38, right: 16, top: 26, bottom: 30},
  yAxis: {type: 'value'}, xAxis: axisCommon(), legend: {top: 0},
  tooltip: {trigger: 'axis'}});
rankChart.setOption({grid: {left: 100, right: 40, top: 22, bottom: 25},
  xAxis: {type: 'value'}, yAxis: {type: 'category', data: [], axisLabel: {fontSize: 9}},
  tooltip: {trigger: 'axis'}});

// 申万1级最新日排行（最强15 + 最弱10）
var cats1 = [], vals1 = [];
S1.sectors.slice(0, 15).reverse().forEach(function(s, i){cats1.push(s); vals1.push(S1.vals.slice(0, 15).reverse()[i])});
S1.sectors.slice(-10).reverse().forEach(function(s, i){cats1.push(s); vals1.push(S1.vals.slice(-10).reverse()[i])});
s1Chart.setOption({
  grid: {left: 100, right: 40, top: 10, bottom: 25},
  xAxis: {type: 'value'}, yAxis: {type: 'category', data: cats1, axisLabel: {fontSize: 9}},
  series: [{name: '今日均涨跌幅%', type: 'bar', data: vals1.map(function(v){
    return {value: v, itemStyle: {color: v >= 0 ? '#d32f2f' : '#34a853'}};}),
    label: {show: true, position: 'right', fontSize: 9, formatter: function(p){return p.value > 0 ? '+' + p.value : p.value;}}}],
  tooltip: {trigger: 'axis'}
});

setRange(120);
setRankWindow(20, null);
window.addEventListener('resize', function(){
  [idxChart, amtChart, limChart, rankChart, s1Chart].forEach(function(c){c.resize()});
});

function switchSector(level, btn){
  document.querySelectorAll('.rng-sector button').forEach(function(b){b.classList.remove('active')});
  btn.classList.add('active');
  document.getElementById('sec1').style.display = level == 1 ? '' : 'none';
  document.getElementById('sec2').style.display = level == 2 ? '' : 'none';
}
"""


def render_mobile(payload: dict, generated_at: str) -> str:
    """手机版页面（market_mobile.html）"""
    latest_date = payload["latest_date"]
    med, up, down = payload["med"], payload["up"], payload["down"]
    lim_up, lim_down = payload["lim_up"], payload["lim_down"]
    amt_wan = payload["amt_wan"]

    time_btns = "".join(
        f"<button class='rng-btn{' active' if d == 120 else ''}' data-days='{d}' onclick='setRange({d})'>"
        f"{d if d > 0 else '全部'}</button>"
        for d in CHART_DAYS)
    rank_btns = "".join(
        f"<button class='rng-btn{' active' if n == 20 else ''}' onclick='setRankWindow({n}, this)'>{n}日</button>"
        for n in (5, 10, 20))

    def tile(v, label, cls=""):
        return f'<div class="tile"><span class="v {cls}">{v}</span><span class="l">{label}</span></div>'

    js = MOBILE_JS.replace("__DAILY__", payload["daily_json"]).replace("__IDX__", payload["idx_json"]) \
        .replace("__L2__", payload["l2_json"]).replace("__S1__", payload["s1_json"])

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>整体行情 — {latest_date}</title>
<style>{MOBILE_CSS}</style></head><body>
<div class="header"><h1>📈 整体行情</h1>
<div class="sub">资金量 · 指数 · 板块强度 · {latest_date} · 生成 {generated_at}</div></div>
<div class="tiles">
{tile(f'{amt_wan:.2f}万亿', '两市成交额', 'blue')}
{tile(f'{up:,}', '上涨家数', 'red')}
{tile(f'{down:,}', '下跌家数', 'green')}
{tile(str(lim_up), '涨停家数', 'red')}
{tile(str(lim_down), '跌停家数', 'green')}
{tile(f'{med:+.2f}%', '中位涨跌幅', 'red' if med >= 0 else 'green')}
{tile(f'{payload["sh_px"]:,.0f}', f'上证 {payload["sh_chg"]:+.2f}%', 'red' if payload["sh_chg"] >= 0 else 'green')}
{tile(f'{payload["sz_px"]:,.0f}', f'深成 {payload["sz_chg"]:+.2f}%', 'red' if payload["sz_chg"] >= 0 else 'green')}
{tile(f'{payload["hs_px"]:,.0f}', f'沪深300 {payload["hs_chg"]:+.2f}%', 'red' if payload["hs_chg"] >= 0 else 'green')}
</div>
<div class="panel"><h2>大盘指数走势<span class="rng-idxview">
<button class="rng-btn active" data-view="cum" onclick="switchIdxView('cum', this)">区间累计%</button>
<button class="rng-btn" data-view="daily" onclick="switchIdxView('daily', this)">当日涨跌%</button>
<button class="rng-btn" data-view="px" onclick="switchIdxView('px', this)">点位</button></span>
<span class="rng-time">{time_btns}</span></h2>
<div class="chart" id="idx"></div></div>
<div class="panel"><h2>每日成交额与上涨占比<span class="rng-time">{time_btns}</span></h2>
<div class="chart" id="amt"></div></div>
<div class="panel"><h2>涨停 / 跌停家数<span class="rng-time">{time_btns}</span></h2>
<div class="chart" id="lim"></div></div>
<div class="panel"><h2>申万2级强度排行（最强15 + 最弱10）<span class="rng-rank">{rank_btns}</span></h2>
<div class="chart" id="rank"></div></div>
<div class="panel"><h2>申万1级今日涨跌幅排行（最强15 + 最弱10）</h2>
<div class="chart" id="s1"></div></div>
<div class="panel"><h2>最新日板块明细<span class="rng-sector">
<button class="rng-btn active" onclick="switchSector(1, this)">申万1级</button>
<button class="rng-btn" onclick="switchSector(2, this)">申万2级</button></span></h2>
<div id="sec1"><div class="scroll-x"><table><tr><th>板块</th><th>均涨跌%</th><th>成交额(亿)</th><th>涨/跌</th><th>领涨股</th></tr>{payload["tbl1"]}</table></div></div>
<div id="sec2" style="display:none"><div class="scroll-x"><table><tr><th>板块</th><th>均涨跌%</th><th>成交额(亿)</th><th>涨/跌</th><th>领涨股</th></tr>{payload["tbl2"]}</table></div></div>
</div>
<script src="vendor/echarts.min.js"></script>
<script>window.echarts || document.write(`<script src='https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js'><\\/script>`);</script>
<script>{js}</script>
<div class="footer">scripts/reports/gen_market.py · 涨跌停按 主板10%/创业板20%/ST≈5% 估算 · 申万分类为当前分类回填</div>
</body></html>"""


# 板块轮动热力图页 JS（占位符 __L1__/__L2__）
HEATMAP_JS = r"""
var L1 = __L1__;
var L2 = __L2__;
var heatChart = echarts.init(document.getElementById('heat'));
var curLevel = 1, curDays = 60, focusSector = null;

// 行优先矩阵 data[日][板块] → 热力图三元组 [日期, 板块, 值]
function matToData(mat, since){
  var out = [], mx = 1;
  for (var di = 0; di < mat.data.length; di++){
    var date = mat.dates[di];
    if (since && date < since) continue;
    var row = mat.data[di];
    for (var si = 0; si < mat.sectors.length; si++){
      var v = row[si];
      if (v == null) continue;
      out.push([date, mat.sectors[si], v]);
      if (Math.abs(v) > mx) mx = Math.abs(v);
    }
  }
  return {cells: out, mx: Math.min(Math.max(mx, 0.5), 5)};
}

function heatOption(mat, days){
  var dlen = mat.dates.length;
  var s = Math.max(0, dlen - days);
  var dates = mat.dates.slice(s);
  var conv = matToData(mat, dates[0]);   // 色阶按窗口内数据动态取对称上下界（±0.5~±5）
  return {
    tooltip: {position: 'top', formatter: function(p){
      return p.value[1] + '<br/>' + p.value[0] + '<br/>均涨跌幅: ' + p.value[2] + '%';}},
    grid: {left: 105, right: 105, top: 75, bottom: 30},
    xAxis: {type: 'category', data: dates, position: 'top',
      axisLabel: {fontSize: 10, rotate: 45,
        interval: Math.max(0, Math.ceil(dates.length / 16) - 1),
        formatter: function(v){return v.slice(5)}}},
    yAxis: {type: 'category', data: mat.sectors, triggerEvent: true,
      axisLabel: {fontSize: 12, cursor: 'pointer'}},
    visualMap: {min: -conv.mx, max: conv.mx, calculable: true, orient: 'vertical',
      right: 20, top: 75, bottom: 30, itemWidth: 14,
      inRange: {color: ['#34a853', '#f5f5f5', '#d32f2f']}},
    series: [{name: '均涨跌幅', type: 'heatmap', data: conv.cells,
      itemStyle: {borderWidth: 0.5, borderColor: '#eee'},
      emphasis: {itemStyle: {shadowBlur: 4, shadowColor: 'rgba(0,0,0,.3)'}}}]
  };
}

function applyHeat(){
  var mat = curLevel == 1 ? L1 : L2;
  var rows = mat.sectors.length;
  var h = rows * (curLevel == 1 ? 26 : 17) + 115;   // 1级每行26px, 2级每行17px
  document.getElementById('heat').style.height = h + 'px';
  var opt = heatOption(mat, curDays);
  // 聚焦模式：除选中板块外其余行变暗
  if (focusSector){
    opt.series[0].data = opt.series[0].data.map(function(c){
      return {value: c, itemStyle: {opacity: c[1] === focusSector ? 1 : 0.12}};
    });
  }
  heatChart.setOption(opt, true);
  heatChart.resize();
  var hint = document.getElementById('focusHint');
  if (focusSector){
    hint.textContent = '🔦 已聚焦: ' + focusSector + ' · 点击其他板块标题切换，再点当前板块取消';
    hint.style.display = '';
  } else {
    hint.style.display = 'none';
  }
}

// 点击板块标题（或该板块任意格子）→ 聚焦/取消
heatChart.on('click', function(p){
  var sec = null;
  if (p.componentType == 'yAxis' && p.targetType == 'axisLabel') sec = p.value;
  else if (p.componentType == 'series' && p.value && p.value.length >= 2) sec = p.value[1];
  if (!sec) return;
  focusSector = (focusSector === sec) ? null : sec;
  applyHeat();
});

function switchLevel(level, btn){
  curLevel = level;
  focusSector = null;   // 切换粒度后板块集合变化，取消聚焦
  document.querySelectorAll('.rng-level button').forEach(function(b){
    b.classList.toggle('active', +b.getAttribute('data-level') === level);
  });
  applyHeat();
}

function switchDays(days, btn){
  curDays = days;
  document.querySelectorAll('.rng-days button').forEach(function(b){
    b.classList.toggle('active', +b.getAttribute('data-days') === days);
  });
  applyHeat();
}

applyHeat();
window.addEventListener('resize', function(){ heatChart.resize(); });
"""


def render_heatmap(payload: dict, generated_at: str) -> str:
    """板块轮动热力图独立页（market_heatmap.html）"""
    latest_date = payload["latest_date"]
    level_btns = (
        "<button class='rng-btn active' data-level='1' onclick='switchLevel(1, this)'>申万1级 (31)</button>"
        "<button class='rng-btn' data-level='2' onclick='switchLevel(2, this)'>申万2级 (131)</button>")
    days_btns = "".join(
        f"<button class='rng-btn{' active' if d == 60 else ''}' data-days='{d}' onclick='switchDays({d}, this)'>{d}日</button>"
        for d in HEAT_DAYS)

    js = HEATMAP_JS.replace("__L1__", payload["l1_json"]).replace("__L2__", payload["l2_json"])

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>板块轮动热力图 — {latest_date}</title>
<style>{CSS}</style></head><body>
<div class="header"><h1>🗺️ 板块轮动热力图</h1>
<div class="sub">申万1级 31 个 / 申万2级 131 个行业 · 每格 = 当日板块均涨跌幅% · 数据截至 {latest_date} · 生成 {generated_at}</div></div>
<div class="panel"><h2 style="position:sticky;top:0;z-index:2">粒度 <span class="rng-level">{level_btns}</span>
窗口 <span class="rng-days">{days_btns}</span>
<span style="color:#999;font-weight:400;font-size:11px">点击板块标题可聚焦该板块（其余变暗），再点取消 · 2级视图较高，向下滚动查看</span></h2>
<div id="focusHint" style="display:none;position:sticky;top:45px;z-index:2;background:#fff8e1;padding:6px 16px;font-size:12px;border-bottom:1px solid #eee"></div>
<div class="chart" id="heat"></div></div>
<div class="footer">生成: scripts/reports/gen_market.py · 每格 = 当日板块内个股涨跌幅等权平均 · 申万分类为当前分类回填历史 · <a href="market_overview.html" style="color:var(--blue)">← 返回整体行情</a></div>
<script src="vendor/echarts.min.js"></script>
<script>window.echarts || document.write(`<script src='https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js'><\\/script>`);</script>
<script>{js}</script>
</body></html>"""


# ====================================================================
# 主程序
# ====================================================================

def main():
    p = argparse.ArgumentParser(description="整体行情统计页生成器（桌面+手机）")
    p.add_argument("--no-nav", action="store_true", help="不刷新导航页")
    a = p.parse_args()
    t0 = time.time()

    print("加载数据...")
    data = StockData()
    info = StockInfo().df
    cache = prep_cache(data.cache, info)
    latest_date = cache["日期"].max().strftime("%Y-%m-%d")
    print(f"  股票池 {cache['代码'].nunique()} 只, 最新交易日 {latest_date}")

    print("聚合全市场日度序列...")
    daily = daily_series(cache)

    print("聚合板块日度序列（申万1级/2级）...")
    sd1 = sector_daily(cache, "申万1级")
    sd2 = sector_daily(cache, "申万2级")

    print("读取指数...")
    piv, chg, src = index_series(IndexData().cache)

    print("渲染 HTML（桌面 + 手机）...")
    generated_at = time.strftime("%Y-%m-%d %H:%M")
    payload = build_payload(daily, sd1, sd2, piv, chg, src, latest_date)

    os.makedirs(os.path.dirname(OUTPUT_HTML), exist_ok=True)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(render_html(payload, generated_at))
    with open(OUTPUT_MOBILE, "w", encoding="utf-8") as f:
        f.write(render_mobile(payload, generated_at))
    with open(OUTPUT_HEATMAP, "w", encoding="utf-8") as f:
        f.write(render_heatmap(payload, generated_at))
    print(f"✓ 桌面: {OUTPUT_HTML} ({os.path.getsize(OUTPUT_HTML)/1024:.0f}KB)")
    print(f"✓ 手机: {OUTPUT_MOBILE} ({os.path.getsize(OUTPUT_MOBILE)/1024:.0f}KB)")
    print(f"✓ 热力图: {OUTPUT_HEATMAP} ({os.path.getsize(OUTPUT_HEATMAP)/1024:.0f}KB)")

    # sanity 打印
    last = daily.iloc[-1]
    print(f"  最新交易日: {latest_date} · 两市成交额 {last['成交额']/1e12:.2f} 万亿 · "
          f"涨 {int(last['上涨家数'])} / 跌 {int(last['下跌家数'])} · "
          f"涨停 {int(last['涨停家数'])} 跌停 {int(last['跌停家数'])}")
    for code, name in INDEXES.items():
        print(f"  {name}: {piv[code].iloc[-1]:,.2f} ({chg[code].iloc[-1]:+.2f}%)"
              + (" [ETF代理]" if src.get(code) == "etf" else ""))

    if not a.no_nav:
        try:
            from scripts.reports.gen_index import generate
            generate()
            from scripts.reports.gen_mobile import generate as generate_mobile
            generate_mobile()
        except Exception as e:
            print(f"  ⚠ 导航刷新跳过: {e}")

    print(f"耗时: {time.time() - t0:.0f}秒")


if __name__ == "__main__":
    main()
