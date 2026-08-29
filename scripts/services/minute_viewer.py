#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分时图查询器 — 从分钟线 Parquet 按代码/名称和日期查询展示

推荐运行：python -m scripts.services.minute_viewer --serve
浏览器打开：http://127.0.0.1:8765/minute_view.html

不加 --serve 时生成指定标的的静态快照，可直接 file:// 打开：
python -m scripts.services.minute_viewer --code sh600519 --date 2026-08-25
"""

import argparse
import gc
import json
import math
import os
import re
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)

from data.minute import CACHE_FILE as MINUTE_CACHE_FILE
from data.minute import MinuteData

OUT_HTML = os.path.join(PROJECT_DIR, "output", "minute_view.html")
DEFAULT_CODE = "sh600519"


def normalize_code(code: str) -> str:
    """600519/510050/sh600519 → 项目统一的带交易所前缀代码。"""
    value = str(code or "").strip().lower().replace(".", "")
    if value.startswith(("sh", "sz")):
        return value
    digits = re.sub(r"\D", "", value)
    if len(digits) != 6:
        return value
    return ("sh" if digits.startswith(("5", "6")) else "sz") + digits


def _json_for_html(value) -> str:
    """防止嵌入 script 时数据中的 </script> 提前闭合标签。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def _previous_close(code: str, date: str):
    """从日线 Parquet 过滤读取目标日前收，避免每次加载整份日线缓存。"""
    from data.etf import CACHE_FILE as ETF_CACHE_FILE
    from data.kline import CACHE_FILE as STOCK_CACHE_FILE

    target = pd.Timestamp(date)
    raw = code[2:] if code.startswith(("sh", "sz")) else code
    for path, stored_code in ((ETF_CACHE_FILE, raw), (STOCK_CACHE_FILE, code)):
        if not os.path.exists(path):
            continue
        try:
            df = pd.read_parquet(
                path,
                columns=["日期", "收盘"],
                filters=[("代码", "==", stored_code), ("日期", "<", target)],
            )
            if len(df):
                return float(df.sort_values("日期")["收盘"].iloc[-1])
        except Exception:
            continue
    return None


def _query_number(params, name: str, default, integer: bool = False):
    raw = params.get(name, [str(default)])[0]
    try:
        value = int(raw) if integer else float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"参数 {name} 格式不正确")
    if not math.isfinite(value):
        raise ValueError(f"参数 {name} 必须是有限数字")
    return value


def _query_optional_number(params, name: str, integer: bool = False):
    raw = params.get(name, [""])[0]
    if raw in (None, ""):
        return None
    return _query_number(params, name, raw, integer=integer)


def _query_bool(params, name: str, default: bool = False) -> bool:
    raw = str(params.get(name, ["1" if default else "0"])[0]).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"参数 {name} 必须是布尔值")


def build_grid_payload(repository, params: dict) -> dict:
    """从查询参数和分钟缓存构造可逐分钟播放的网格模拟结果。"""
    from backtest.intraday_grid import IntradayGridConfig, simulate_intraday_grid

    minute_payload = repository.payload(
        params.get("code", [""])[0], params.get("date", [None])[0]
    )
    config = IntradayGridConfig(
        mode=params.get("mode", ["transaction_driven"])[0],
        initial_cash=_query_number(params, "cash", 100_000),
        initial_shares=_query_number(params, "shares", 1_000, integer=True),
        base_price=_query_optional_number(params, "base_price"),
        step_mode=params.get("step_mode", ["pct"])[0],
        grid_pct=_query_number(params, "grid_pct", 0.1),
        buy_step=_query_optional_number(params, "buy_step"),
        sell_step=_query_optional_number(params, "sell_step"),
        lot_shares=_query_number(params, "lot", 100, integer=True),
        buy_lot_shares=_query_optional_number(params, "buy_lot", integer=True),
        sell_lot_shares=_query_optional_number(params, "sell_lot", integer=True),
        multiplier_enabled=_query_bool(params, "multiplier_enabled"),
        buy_multiplier=_query_number(params, "buy_multiplier", 1, integer=True),
        sell_multiplier=_query_number(params, "sell_multiplier", 1, integer=True),
        price_floor=_query_optional_number(params, "price_floor"),
        price_ceiling=_query_optional_number(params, "price_ceiling"),
        min_position=_query_number(params, "min_position", 0, integer=True),
        max_position=_query_number(params, "max_position", 5_000, integer=True),
        cage_to_market=_query_bool(params, "cage_to_market"),
        price_cage_pct=_query_number(params, "price_cage_pct", 2),
        rebound_enabled=_query_bool(params, "rebound_enabled"),
        rebound_value=_query_number(params, "rebound_value", 0.1),
        pullback_enabled=_query_bool(params, "pullback_enabled"),
        pullback_value=_query_number(params, "pullback_value", 0.1),
        turn_mode=params.get("turn_mode", ["pct"])[0],
        floor_trigger_enabled=_query_bool(params, "floor_trigger_enabled"),
        order_price_mode=params.get("order_price_mode", ["counterparty"])[0],
        order_offset_bps=_query_number(params, "order_offset_bps", 0),
        base_update_timing=params.get("base_update_timing", ["filled"])[0],
        base_update_price=params.get("base_update_price", ["grid"])[0],
        auto_cancel_enabled=_query_bool(params, "auto_cancel_enabled"),
        auto_cancel_minutes=_query_number(params, "auto_cancel_minutes", 1, integer=True),
        monitor_price_mode=params.get("monitor_price_mode", ["ohlc"])[0],
        after_close_update_base=_query_bool(params, "after_close_update_base"),
        validity_days=_query_number(params, "validity_days", 90, integer=True),
        commission_rate=_query_number(params, "commission", 1) / 10_000,
        min_commission=_query_number(params, "min_commission", 0),
        sell_tax_rate=_query_number(params, "sell_tax", 0) / 10_000,
        slippage_rate=_query_number(params, "slippage_bps", 0) / 10_000,
    )
    result = simulate_intraday_grid(minute_payload["points"], config)
    result.update({
        "code": minute_payload["code"],
        "name": minute_payload["name"],
        "date": minute_payload["date"],
        "dates": minute_payload["dates"],
        "prev_close": minute_payload["prev_close"],
    })
    return result


class MinuteRepository:
    """分钟缓存查询门面；分钟数据始终按代码过滤读取。"""

    def __init__(self, build_search_index: bool = False):
        self.minute = MinuteData()
        self.name_map = self._load_names()
        self.search_rows = []
        self._search_index_signature = None
        self._search_index_lock = threading.Lock()
        if build_search_index:
            self.build_search_index()

    @staticmethod
    def _load_names() -> dict:
        try:
            from data.industry import StockInfo
            info = StockInfo().df
            return dict(zip(info["代码"], info["名称"]))
        except Exception:
            return {}

    @staticmethod
    def _minute_cache_signature():
        """用修改时间和文件大小识别原子替换后的新版分钟缓存。"""
        if not os.path.exists(MINUTE_CACHE_FILE):
            return None
        stat = os.stat(MINUTE_CACHE_FILE)
        return stat.st_mtime_ns, stat.st_size

    def build_search_index(self):
        """缓存变化时重建代码搜索索引；未变化时不重复扫描大文件。"""
        signature = self._minute_cache_signature()
        if signature == self._search_index_signature:
            return False

        with self._search_index_lock:
            signature = self._minute_cache_signature()
            if signature == self._search_index_signature:
                return False
            if signature is None:
                self.search_rows = []
                self._search_index_signature = None
                return False

            slim = pd.read_parquet(MINUTE_CACHE_FILE, columns=["代码", "时间"])
            slim["时间"] = pd.to_datetime(slim["时间"])
            latest = slim.groupby("代码", sort=False)["时间"].max()
            rows = [
                {
                    "code": code,
                    "name": self.name_map.get(code, code),
                    "latest_date": ts.strftime("%Y-%m-%d"),
                }
                for code, ts in latest.items()
            ]
            self.search_rows = rows
            self._search_index_signature = signature
            del slim, latest
        gc.collect()
        return True

    def search(self, query: str, limit: int = 20) -> list:
        self.build_search_index()
        q = str(query or "").strip().lower()
        if not q:
            return []
        normalized = normalize_code(q)
        digits = re.sub(r"\D", "", q)
        ranked = []
        for row in self.search_rows:
            code = row["code"].lower()
            raw = code[2:]
            name = row["name"].lower()
            if q not in code and q not in raw and q not in name and normalized != code:
                continue
            if normalized == code or q == code or (digits and digits == raw):
                score = 0
            elif name == q:
                score = 1
            elif code.startswith(q) or raw.startswith(q) or name.startswith(q):
                score = 2
            else:
                score = 3
            ranked.append((score, row))
        ranked.sort(key=lambda item: (item[0], item[1]["code"]))
        return [row for _, row in ranked[: max(1, min(limit, 50))]]

    def available_dates(self, code: str) -> list:
        code = normalize_code(code)
        if not os.path.exists(MINUTE_CACHE_FILE):
            return []
        try:
            df = pd.read_parquet(
                MINUTE_CACHE_FILE,
                columns=["时间"],
                filters=[("代码", "==", code)],
            )
        except Exception:
            return []
        if not len(df):
            return []
        return sorted(pd.to_datetime(df["时间"]).dt.strftime("%Y-%m-%d").unique().tolist())

    def payload(self, code: str, date: str = None):
        code = normalize_code(code)
        if not re.fullmatch(r"(sh|sz)\d{6}", code):
            raise ValueError("代码格式不正确，请输入 600519、sh600519 或股票名称")

        dates = self.available_dates(code)
        if not dates:
            raise LookupError(f"{code} 在分钟缓存中没有数据")
        selected = date or dates[-1]
        if selected not in dates:
            raise LookupError(f"{code} 在 {selected} 没有分钟数据")

        df = self.minute.get_minute(code, selected).sort_values("时间").reset_index(drop=True)
        if not len(df):
            raise LookupError(f"{code} 在 {selected} 没有分钟数据")

        prev_close = _previous_close(code, selected)
        fallback = float(df["开盘"].iloc[0])
        base_price = prev_close if prev_close and prev_close > 0 else fallback
        cum_volume = df["成交量"].cumsum()
        with np.errstate(divide="ignore", invalid="ignore"):
            vwap = df["成交额"].cumsum() / cum_volume.replace(0, np.nan)
        vwap = vwap.fillna(df["收盘"])
        minute_prev = df["收盘"].shift(1).fillna(df["开盘"])

        points = []
        for i, row in df.iterrows():
            close = float(row["收盘"])
            points.append({
                "time": row["时间"].strftime("%H:%M"),
                "open": round(float(row["开盘"]), 4),
                "high": round(float(row["最高"]), 4),
                "low": round(float(row["最低"]), 4),
                "close": round(close, 4),
                "vwap": round(float(vwap.iloc[i]), 4),
                "change_pct": round((close / base_price - 1) * 100, 3),
                "volume": float(row["成交量"]),
                "amount": float(row["成交额"]),
                "direction": 1 if close >= float(minute_prev.iloc[i]) else -1,
            })

        last = points[-1]
        return {
            "code": code,
            "name": self.name_map.get(code, code),
            "date": selected,
            "dates": dates,
            "prev_close": round(base_price, 4),
            "points": points,
            "summary": {
                "last": last["close"], "change_pct": last["change_pct"],
                "high": round(float(df["最高"].max()), 4),
                "low": round(float(df["最低"].min()), 4),
                "open": round(float(df["开盘"].iloc[0]), 4),
                "volume": float(df["成交量"].sum()),
                "amount": float(df["成交额"].sum()),
            },
        }


def build_html(initial_payload: dict) -> str:
    initial_json = _json_for_html(initial_payload)
    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>分时行情查询</title>
<style>
:root{{--bg:#f4f6f9;--card:#fff;--text:#202124;--muted:#7b8190;--border:#e5e8ef;--blue:#3478f6;--red:#e5484d;--green:#16a36a;--gold:#e5a000}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}}
.page{{max-width:1280px;margin:0 auto;padding:18px}}.topbar{{display:flex;gap:14px;align-items:center;justify-content:space-between;margin-bottom:12px}}.brand{{font-size:20px;font-weight:700;white-space:nowrap}}
.search-wrap{{position:relative;flex:1;max-width:620px}}.search-box{{display:flex;background:#fff;border:1px solid var(--border);border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(30,45,75,.05)}}
#searchInput{{flex:1;border:0;outline:0;padding:12px 14px;font-size:14px;min-width:0}}#searchBtn{{border:0;background:var(--blue);color:#fff;padding:0 20px;font-weight:600;cursor:pointer}}
.results{{position:absolute;top:48px;left:0;right:0;background:#fff;border:1px solid var(--border);border-radius:10px;box-shadow:0 12px 30px rgba(23,34,58,.15);z-index:20;display:none;max-height:360px;overflow:auto}}
.result{{display:flex;justify-content:space-between;gap:14px;padding:11px 14px;cursor:pointer;border-bottom:1px solid #f1f2f5}}.result:hover{{background:#edf4ff}}.result-code{{color:var(--blue);font-variant-numeric:tabular-nums}}.result-date{{color:var(--muted);font-size:12px}}
.toolbar{{display:flex;align-items:center;gap:8px}}.toolbar button,#dateSelect{{height:38px;border:1px solid var(--border);background:#fff;border-radius:8px;padding:0 10px;color:var(--text)}}.toolbar button{{cursor:pointer;font-size:16px}}.toolbar button:disabled{{opacity:.35;cursor:not-allowed}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:12px;box-shadow:0 3px 12px rgba(30,45,75,.05)}}.quote{{padding:16px 18px 8px;display:flex;align-items:flex-end;gap:16px;flex-wrap:wrap}}
.identity{{min-width:220px}}.identity h1{{font-size:20px;margin:0 0 4px}}.identity .sub{{color:var(--muted);font-size:13px}}.last{{font-size:30px;font-weight:700;font-variant-numeric:tabular-nums}}.change{{font-size:15px;font-weight:600}}
.stats{{display:grid;grid-template-columns:repeat(5,minmax(90px,1fr));gap:10px;flex:1}}.stat{{border-left:1px solid var(--border);padding-left:14px}}.stat .label{{display:block;color:var(--muted);font-size:11px;margin-bottom:3px}}.stat .value{{font-variant-numeric:tabular-nums;font-size:14px}}
#chart{{height:650px;width:100%}}.status{{padding:0 18px 14px;color:var(--muted);font-size:12px;display:flex;justify-content:space-between;gap:10px}}.notice{{display:none;margin-bottom:12px;padding:10px 14px;border-radius:8px;background:#fff6dc;color:#765a00;font-size:13px}}
.loading{{position:fixed;inset:0;background:rgba(244,246,249,.55);display:none;align-items:center;justify-content:center;z-index:50}}.loading span{{background:#1f2937;color:#fff;padding:10px 18px;border-radius:8px}}.up{{color:var(--red)}}.down{{color:var(--green)}}
@media(max-width:760px){{.page{{padding:10px}}.topbar{{align-items:stretch;flex-direction:column}}.brand{{font-size:17px}}.search-wrap{{max-width:none}}.toolbar{{justify-content:flex-end}}.identity{{min-width:100%}}.stats{{grid-template-columns:repeat(3,1fr);min-width:100%}}.stat{{border-left:0;padding:8px;background:#f7f8fa;border-radius:8px}}#chart{{height:520px}}.status{{flex-direction:column}}}}
</style></head><body>
<div class="loading" id="loading"><span>正在读取分钟缓存…</span></div>
<main class="page"><div class="topbar"><div class="brand">🕐 分时行情查询</div><div class="search-wrap"><div class="search-box"><input id="searchInput" autocomplete="off" placeholder="输入股票名称或代码，如 贵州茅台 / 600519"><button id="searchBtn">查询</button></div><div class="results" id="results"></div></div><div class="toolbar"><button id="prevDay" title="前一交易日">‹</button><select id="dateSelect" aria-label="选择交易日"></select><button id="nextDay" title="后一交易日">›</button></div></div>
<div class="notice" id="notice"></div><section class="card"><div class="quote"><div class="identity"><h1 id="symbolName">—</h1><div class="sub" id="symbolMeta">—</div></div><div><span class="last" id="lastPrice">—</span> <span class="change" id="changePct">—</span></div><div class="stats"><div class="stat"><span class="label">今开</span><span class="value" id="openPrice">—</span></div><div class="stat"><span class="label">最高</span><span class="value" id="highPrice">—</span></div><div class="stat"><span class="label">最低</span><span class="value" id="lowPrice">—</span></div><div class="stat"><span class="label">成交量</span><span class="value" id="totalVolume">—</span></div><div class="stat"><span class="label">成交额</span><span class="value" id="totalAmount">—</span></div></div></div><div id="chart"></div><div class="status"><span>鼠标悬停查看对应分钟价格；滚轮缩放，拖拽平移</span><span>数据源：本地 minute_kline_cache.parquet</span></div></section></main>
<script src="vendor/echarts.min.js"></script><script>window.echarts||document.write(`<script src='https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js'><\\/script>`);</script>
<script>
const INITIAL_DATA={initial_json};const API_AVAILABLE=location.protocol==='http:'||location.protocol==='https:';const chart=echarts.init(document.getElementById('chart'));let current=INITIAL_DATA,searchTimer=null;const $=id=>document.getElementById(id);
function price(v){{if(v===null||v===undefined||Number.isNaN(+v))return '—';const n=+v;return n<10?n.toFixed(3):n.toFixed(2)}}function compact(v){{const n=+v||0;if(n>=1e8)return(n/1e8).toFixed(2)+'亿';if(n>=1e4)return(n/1e4).toFixed(1)+'万';return Math.round(n).toLocaleString()}}function signed(v){{return`${{v>=0?'+':''}}${{(+v).toFixed(2)}}%`}}function cls(v){{return+v>=0?'up':'down'}}function showNotice(msg,always=false){{$('notice').textContent=msg;$('notice').style.display=msg?'block':'none';if(msg&&!always)setTimeout(()=>{{$('notice').style.display='none'}},3500)}}function setLoading(on){{$('loading').style.display=on?'flex':'none'}}function escapeHtml(s){{return String(s).replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]))}}
function render(data){{current=data;const s=data.summary;$('symbolName').textContent=data.name;$('symbolMeta').textContent=`${{data.code}} · ${{data.date}} · 前收 ${{price(data.prev_close)}}`;$('lastPrice').textContent=price(s.last);$('lastPrice').className='last '+cls(s.change_pct);$('changePct').textContent=signed(s.change_pct);$('changePct').className='change '+cls(s.change_pct);$('openPrice').textContent=price(s.open);$('highPrice').textContent=price(s.high);$('lowPrice').textContent=price(s.low);$('totalVolume').textContent=compact(s.volume);$('totalAmount').textContent=compact(s.amount);$('searchInput').value=`${{data.name}}  ${{data.code}}`;renderDates(data);renderChart(data);updateUrl(data)}}
function renderDates(data){{$('dateSelect').innerHTML=data.dates.map(d=>`<option value="${{d}}" ${{d===data.date?'selected':''}}>${{d}}</option>`).join('');const i=data.dates.indexOf(data.date);$('prevDay').disabled=i<=0;$('nextDay').disabled=i<0||i>=data.dates.length-1}}
function renderChart(data){{const pts=data.points,times=pts.map(p=>p.time),closes=pts.map(p=>p.close),vwaps=pts.map(p=>p.vwap),vols=pts.map(p=>p.volume),base=data.prev_close,extrema=pts.flatMap(p=>[p.high,p.low]);let dev=Math.max(...extrema.map(v=>Math.abs(v-base)),Math.abs(base)*.002)*1.08;const ymin=base-dev,ymax=base+dev,pct=Math.max(dev/base*100,.2);chart.setOption({{animation:false,grid:[{{left:68,right:72,top:35,height:'62%'}},{{left:68,right:72,top:'76%',height:'15%'}}],axisPointer:{{link:[{{xAxisIndex:[0,1]}}],label:{{backgroundColor:'#586174'}}}},tooltip:{{trigger:'axis',axisPointer:{{type:'cross',snap:true}},backgroundColor:'rgba(27,31,40,.94)',borderWidth:0,textStyle:{{color:'#fff',fontSize:12}},formatter:params=>{{if(!params.length)return'';const i=params[0].dataIndex,p=pts[i],color=p.change_pct>=0?'#ff6b6b':'#41d49a';return`<div style="min-width:190px"><b>${{data.date}} ${{p.time}}</b><br><span style="color:${{color}};font-size:16px;font-weight:700">${{price(p.close)}} (${{signed(p.change_pct)}})</span><br>开 ${{price(p.open)}}　高 ${{price(p.high)}}<br>低 ${{price(p.low)}}　均价 ${{price(p.vwap)}}<br>成交量 ${{compact(p.volume)}}<br>成交额 ${{compact(p.amount)}}</div>`}}}},xAxis:[{{type:'category',gridIndex:0,data:times,boundaryGap:false,axisLabel:{{show:false}},axisTick:{{show:false}},axisLine:{{lineStyle:{{color:'#d9dde6'}}}}}},{{type:'category',gridIndex:1,data:times,boundaryGap:true,axisLabel:{{color:'#7b8190',formatter:(v,i)=>i%30===0?v:''}},axisTick:{{show:false}},axisLine:{{lineStyle:{{color:'#d9dde6'}}}}}}],yAxis:[{{type:'value',gridIndex:0,min:ymin,max:ymax,scale:true,splitNumber:6,axisLabel:{{formatter:v=>price(v),color:v=>v>=base?'#e5484d':'#16a36a'}},splitLine:{{lineStyle:{{color:'#edf0f5',type:'dashed'}}}}}},{{type:'value',gridIndex:0,min:-pct,max:pct,position:'right',splitNumber:6,axisLabel:{{formatter:v=>`${{v>=0?'+':''}}${{v.toFixed(2)}}%`,color:v=>v>=0?'#e5484d':'#16a36a'}},splitLine:{{show:false}}}},{{type:'value',gridIndex:1,scale:true,axisLabel:{{formatter:v=>compact(v),color:'#7b8190'}},splitLine:{{show:false}}}}],dataZoom:[{{type:'inside',xAxisIndex:[0,1],filterMode:'none'}},{{type:'slider',xAxisIndex:[0,1],bottom:4,height:18,showDetail:false,borderColor:'#e5e8ef'}}],series:[{{name:'价格',type:'line',xAxisIndex:0,yAxisIndex:0,data:closes,showSymbol:false,lineStyle:{{width:1.4,color:'#3478f6'}},areaStyle:{{color:{{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{{offset:0,color:'rgba(52,120,246,.18)'}},{{offset:1,color:'rgba(52,120,246,.01)'}}]}}}},markLine:{{silent:true,symbol:'none',label:{{formatter:`前收 ${{price(base)}}`,position:'insideEndTop',color:'#8a909e'}},lineStyle:{{color:'#9ca3af',type:'dashed'}},data:[{{yAxis:base}}]}}}},{{name:'均价',type:'line',xAxisIndex:0,yAxisIndex:0,data:vwaps,showSymbol:false,lineStyle:{{width:1,color:'#e5a000'}}}},{{name:'成交量',type:'bar',xAxisIndex:1,yAxisIndex:2,data:vols,itemStyle:{{color:p=>pts[p.dataIndex].direction>=0?'#e5484d':'#16a36a'}}}}]}},true)}}
async function api(path){{const r=await fetch(path,{{cache:'no-store'}}),body=await r.json();if(!r.ok)throw new Error(body.error||`HTTP ${{r.status}}`);return body}}async function loadData(code,date=''){{if(!API_AVAILABLE){{showNotice('当前是静态快照。要查询其他股票，请运行：python -m scripts.services.minute_viewer --serve',true);return}}setLoading(true);try{{const q=new URLSearchParams({{code}});if(date)q.set('date',date);render(await api('/api/minute/data?'+q))}}catch(e){{showNotice(e.message,true)}}finally{{setLoading(false)}}}}async function doSearch(){{const q=$('searchInput').value.trim();if(!q)return;if(!API_AVAILABLE){{showNotice('查询需要本地服务：python -m scripts.services.minute_viewer --serve',true);return}}try{{const rows=await api('/api/minute/search?q='+encodeURIComponent(q));showResults(rows);if(rows.length===1)loadData(rows[0].code)}}catch(e){{showNotice(e.message,true)}}}}function showResults(rows){{const box=$('results');if(!rows.length){{box.innerHTML='<div class="result">未找到缓存标的</div>';box.style.display='block';return}}box.innerHTML=rows.map(r=>`<div class="result" data-code="${{r.code}}"><span><b>${{escapeHtml(r.name)}}</b>　<span class="result-code">${{r.code}}</span></span><span class="result-date">${{r.latest_date}}</span></div>`).join('');box.style.display='block'}}function updateUrl(data){{if(!API_AVAILABLE)return;const u=new URL(location.href);u.searchParams.set('code',data.code);u.searchParams.set('date',data.date);history.replaceState(null,'',u)}}
$('searchBtn').onclick=doSearch;$('searchInput').addEventListener('keydown',e=>{{if(e.key==='Enter')doSearch();if(e.key==='Escape')$('results').style.display='none'}});$('searchInput').addEventListener('input',()=>{{clearTimeout(searchTimer);const q=$('searchInput').value.trim();if(!API_AVAILABLE||q.length<2){{$('results').style.display='none';return}}searchTimer=setTimeout(async()=>{{try{{showResults(await api('/api/minute/search?q='+encodeURIComponent(q)))}}catch(e){{}}}},220)}});$('results').addEventListener('click',e=>{{const row=e.target.closest('[data-code]');if(row){{$('results').style.display='none';loadData(row.dataset.code)}}}});document.addEventListener('click',e=>{{if(!e.target.closest('.search-wrap'))$('results').style.display='none'}});$('dateSelect').onchange=e=>loadData(current.code,e.target.value);$('prevDay').onclick=()=>{{const i=current.dates.indexOf(current.date);if(i>0)loadData(current.code,current.dates[i-1])}};$('nextDay').onclick=()=>{{const i=current.dates.indexOf(current.date);if(i<current.dates.length-1)loadData(current.code,current.dates[i+1])}};window.addEventListener('resize',()=>chart.resize());const requestedUrl=new URL(location.href),requestedCode=requestedUrl.searchParams.get('code'),requestedDate=requestedUrl.searchParams.get('date');render(INITIAL_DATA);if(API_AVAILABLE)loadData(requestedCode||INITIAL_DATA.code,requestedDate||'');else showNotice('这是可离线打开的静态快照；启动本地服务后可查询全部缓存标的。',true);
</script></body></html>'''


class MinuteRequestHandler(SimpleHTTPRequestHandler):
    repository = None

    def end_headers(self):
        """页面和接口都禁用浏览器缓存，始终读取服务端最新版本。"""
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/minute/search":
            params = parse_qs(parsed.query)
            try:
                limit = int(params.get("limit", ["20"])[0])
            except ValueError:
                limit = 20
            return self._send_json(self.repository.search(params.get("q", [""])[0], limit))
        if parsed.path == "/api/minute/data":
            params = parse_qs(parsed.query)
            try:
                payload = self.repository.payload(
                    params.get("code", [""])[0], params.get("date", [None])[0]
                )
                return self._send_json(payload)
            except (ValueError, LookupError) as exc:
                return self._send_json({"error": str(exc)}, status=404)
            except Exception as exc:
                return self._send_json({"error": f"读取分钟缓存失败: {exc}"}, status=500)
        if parsed.path == "/api/grid/simulate":
            params = parse_qs(parsed.query)
            try:
                return self._send_json(build_grid_payload(self.repository, params))
            except LookupError as exc:
                return self._send_json({"error": str(exc)}, status=404)
            except ValueError as exc:
                return self._send_json({"error": str(exc)}, status=400)
            except Exception as exc:
                return self._send_json({"error": f"网格模拟失败: {exc}"}, status=500)
        return super().do_GET()

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/"):
            sys.stdout.write("  API " + (fmt % args) + "\n")


def write_app(path: str, payload: dict):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_html(payload))
    print(f"✓ 页面: {path} ({os.path.getsize(path) / 1024:.0f} KB, {len(payload['points'])} 根分钟线)")


def serve(repository: MinuteRepository, html_path: str, host: str, port: int):
    directory = os.path.dirname(os.path.abspath(html_path))
    handler = lambda *args, **kwargs: MinuteRequestHandler(*args, directory=directory, **kwargs)
    MinuteRequestHandler.repository = repository
    server = ThreadingHTTPServer((host, port), handler)
    print(f"✓ 分时查询服务已启动: http://{host}:{port}/{os.path.basename(html_path)}")
    print(f"✓ 网格动态回放: http://{host}:{port}/grid_simulator.html")
    print(f"  已索引 {len(repository.search_rows)} 个缓存标的，按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="分时图查询器")
    parser.add_argument("--code", default=DEFAULT_CODE, help="初始代码（默认 sh600519）")
    parser.add_argument("--date", default=None, help="初始交易日 YYYY-MM-DD（默认最近）")
    parser.add_argument("--out", default=OUT_HTML, help="页面输出路径")
    parser.add_argument("--serve", action="store_true", help="启动支持股票查询的本地服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    args = parser.parse_args()

    repository = MinuteRepository(build_search_index=args.serve)
    try:
        payload = repository.payload(args.code, args.date)
    except (ValueError, LookupError) as exc:
        sys.exit(f"✗ {exc}")
    write_app(args.out, payload)
    if args.serve:
        from scripts.services.grid_simulator import write_app as write_grid_app
        write_grid_app()
        serve(repository, args.out, args.host, args.port)
    else:
        print("  查询全部标的请运行: python -m scripts.services.minute_viewer --serve")


if __name__ == "__main__":
    main()
