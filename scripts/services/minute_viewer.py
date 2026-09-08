#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分时图查询器 — 从分钟线 Parquet 按代码/名称和日期查询展示

推荐运行：python -m scripts.serve start web
浏览器打开：http://127.0.0.1:8765/minute_view.html

不加 --serve 时生成指定标的的静态快照，可直接 file:// 打开：
python -m scripts.services.minute_viewer --code sh600519 --date 2026-08-25
"""

import argparse
import gc
import json
import math
import mimetypes
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
from features.intraday import add_intraday_volume_ratio
from scripts.services.intraday_replay import inject_intraday_replay

OUT_HTML = os.path.join(PROJECT_DIR, "output", "minute_view.html")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
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
        tick_size=_query_number(params, "tick_size", 0.001),
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
            names = dict(zip(info["代码"], info["名称"]))
        except Exception:
            names = {}
        from data.etf_names import load_names
        names.update(load_names())
        return names

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

    def availability(self, code: str, selected_date: str) -> dict:
        """轻量检查某个交易日是否存在分钟缓存。"""
        code = normalize_code(code)
        selected_date = str(selected_date or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", selected_date):
            raise ValueError("交易日必须是 YYYY-MM-DD")
        try:
            pd.Timestamp(selected_date)
        except (TypeError, ValueError):
            raise ValueError("交易日不合法")
        dates = self.available_dates(code)
        return {
            "code": code,
            "date": selected_date,
            "available": selected_date in dates,
            "latest_date": dates[-1] if dates else None,
        }

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

        history = self.minute.get_history(code, end_date=selected, days=6)
        featured = add_intraday_volume_ratio(history, lookback=5, min_periods=3)
        selected_date = pd.Timestamp(selected).normalize()
        featured = featured[featured["时间"].dt.normalize() == selected_date]
        ratio_by_time = featured.set_index("时间")["盘中量比"]
        intraday_ratio = df["时间"].map(ratio_by_time)

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
                "intraday_volume_ratio": (
                    round(float(intraday_ratio.iloc[i]), 3)
                    if pd.notna(intraday_ratio.iloc[i]) else None
                ),
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
                "intraday_volume_ratio": last["intraday_volume_ratio"],
            },
        }


def build_html(initial_payload: dict) -> str:
    initial_json = _json_for_html(initial_payload)
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>分时行情查询</title>
<link rel="stylesheet" href="/assets/app.css">
<script src="/assets/training-context.js"></script>
<style>
:root{{--bg:var(--app-bg,#f4f6f9);--card:var(--app-surface,#fff);--text:var(--app-text,#202124);--muted:var(--app-muted,#7b8190);--border:var(--app-border,#e5e8ef);--blue:var(--app-accent,#3478f6);--red:var(--app-up,#e5484d);--green:var(--app-down,#16a36a);--gold:#e5a000}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font-family:var(--app-font,-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif)}}
.page{{max-width:1280px;margin:0 auto;padding:18px}}.topbar{{display:flex;gap:14px;align-items:center;justify-content:space-between;margin-bottom:12px}}.brand{{font-size:20px;font-weight:700;white-space:nowrap}}
.search-wrap{{position:relative;flex:1;max-width:620px}}.search-box{{display:flex;background:#fff;border:1px solid var(--border);border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(30,45,75,.05)}}
#searchInput{{flex:1;border:0;outline:0;padding:12px 14px;font-size:14px;min-width:0}}#searchBtn{{border:0;background:var(--blue);color:#fff;padding:0 20px;font-weight:600;cursor:pointer}}
.results{{position:absolute;top:48px;left:0;right:0;background:#fff;border:1px solid var(--border);border-radius:10px;box-shadow:0 12px 30px rgba(23,34,58,.15);z-index:20;display:none;max-height:360px;overflow:auto}}
.result{{display:flex;justify-content:space-between;gap:14px;padding:11px 14px;cursor:pointer;border-bottom:1px solid #f1f2f5}}.result:hover{{background:#edf4ff}}.result-code{{color:var(--blue);font-variant-numeric:tabular-nums}}.result-date{{color:var(--muted);font-size:12px}}
.toolbar{{display:flex;align-items:center;gap:8px}}.toolbar button,.toolbar a,#dateSelect{{height:38px;border:1px solid var(--border);background:#fff;border-radius:8px;padding:0 10px;color:var(--text)}}.toolbar a{{display:flex;align-items:center;text-decoration:none;color:var(--blue);font-size:12px;font-weight:650}}.toolbar button{{cursor:pointer;font-size:16px}}.toolbar button:disabled{{opacity:.35;cursor:not-allowed}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:12px;box-shadow:0 3px 12px rgba(30,45,75,.05)}}.quote{{padding:16px 18px 8px;display:flex;align-items:flex-end;gap:16px;flex-wrap:wrap}}
.identity{{min-width:220px}}.identity h1{{font-size:20px;margin:0 0 4px}}.identity .sub{{color:var(--muted);font-size:13px}}.last{{font-size:30px;font-weight:700;font-variant-numeric:tabular-nums}}.change{{font-size:15px;font-weight:600}}
.stats{{display:grid;grid-template-columns:repeat(6,minmax(90px,1fr));gap:10px;flex:1}}.stat{{border-left:1px solid var(--border);padding-left:14px}}.stat .label{{display:block;color:var(--muted);font-size:11px;margin-bottom:3px}}.stat .value{{font-variant-numeric:tabular-nums;font-size:14px}}
#chart{{height:650px;width:100%}}.status{{padding:0 18px 14px;color:var(--muted);font-size:12px;display:flex;justify-content:space-between;gap:10px}}.notice{{display:none;margin-bottom:12px;padding:10px 14px;border-radius:8px;background:#fff6dc;color:#765a00;font-size:13px}}
.loading{{position:fixed;inset:0;background:rgba(244,246,249,.55);display:none;align-items:center;justify-content:center;z-index:50}}.loading span{{background:#1f2937;color:#fff;padding:10px 18px;border-radius:8px}}.up{{color:var(--red)}}.down{{color:var(--green)}}
__INTRADAY_REPLAY_CSS__
@media(max-width:760px){{.page{{padding:10px}}.topbar{{align-items:stretch;flex-direction:column}}.brand{{font-size:17px}}.search-wrap{{max-width:none}}.toolbar{{justify-content:flex-end}}.identity{{min-width:100%}}.stats{{grid-template-columns:repeat(3,1fr);min-width:100%}}.stat{{border-left:0;padding:8px;background:#f7f8fa;border-radius:8px}}#chart{{height:520px}}.status{{flex-direction:column}}}}
</style>
<script src="/assets/app-shell.js" defer></script>
</head><body>
<div id="app-shell" data-active="minute"></div>
<div class="loading" id="loading"><span>正在读取分钟缓存…</span></div>
<main class="page"><div class="topbar"><div class="brand">分时行情查询</div><div class="search-wrap"><div class="search-box"><input id="searchInput" autocomplete="off" data-clear-on-focus="1" placeholder="输入股票名称或代码，如 贵州茅台 / 600519"><button id="searchBtn">查询</button></div><div class="results" id="results"></div></div><div class="toolbar"><a id="journalLink" href="/stock_journal.html">📓 记日记</a><button id="prevDay" title="前一交易日">‹</button><select id="dateSelect" aria-label="选择交易日"></select><button id="nextDay" title="后一交易日">›</button></div></div>
<div class="notice" id="notice"></div><section class="card"><div class="quote"><div class="identity"><h1 id="symbolName">—</h1><div class="sub" id="symbolMeta">—</div></div><div><span class="last" id="lastPrice">—</span> <span class="change" id="changePct">—</span></div><div class="stats"><div class="stat"><span class="label">今开</span><span class="value" id="openPrice">—</span></div><div class="stat"><span class="label">最高</span><span class="value" id="highPrice">—</span></div><div class="stat"><span class="label">最低</span><span class="value" id="lowPrice">—</span></div><div class="stat"><span class="label">成交量</span><span class="value" id="totalVolume">—</span></div><div class="stat"><span class="label">成交额</span><span class="value" id="totalAmount">—</span></div><div class="stat"><span class="label">盘中量比(5日)</span><span class="value" id="intradayVolumeRatio">—</span></div></div></div><div class="intraday-replay" aria-label="动态分时回放"><button class="replay-primary" id="replayPlay">▶ 动态分时</button><button id="replayStep">推进1分钟</button><button id="replayReset">回到开盘</button><select id="replaySpeed" aria-label="动态分时速度"><option value="1">1×</option><option value="2">2×</option><option value="5" selected>5×</option><option value="10">10×</option></select><button id="replayAll">查看全日</button><span class="replay-clock" id="replayClock">全日</span><span class="replay-progress" id="replayProgress">—</span></div><div id="chart"></div><div class="status"><span>动态分时固定全天时间轴，只向右揭示已播放行情；鼠标悬停查看分钟价格</span><span>数据源：本地 minute_kline_cache.parquet</span></div></section></main>
<script src="vendor/echarts.min.js"></script><script>window.echarts||document.write(`<script src='https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js'><\\/script>`);</script>
<script>__INTRADAY_REPLAY_JS__</script>
<script>
const INITIAL_DATA={initial_json};const API_AVAILABLE=location.protocol==='http:'||location.protocol==='https:';const chart=echarts.init(document.getElementById('chart'));let current=INITIAL_DATA,searchTimer=null;const $=id=>document.getElementById(id);const minuteReplay=createIntradayReplay({{onFrame:renderReplayFrame,initialSpeed:5}});
function price(v){{if(v===null||v===undefined||Number.isNaN(+v))return '—';const n=+v;return n<10?n.toFixed(3):n.toFixed(2)}}function compact(v){{const n=+v||0;if(n>=1e8)return(n/1e8).toFixed(2)+'亿';if(n>=1e4)return(n/1e4).toFixed(1)+'万';return Math.round(n).toLocaleString()}}function signed(v){{return`${{v>=0?'+':''}}${{(+v).toFixed(2)}}%`}}function cls(v){{return+v>=0?'up':'down'}}function showNotice(msg,always=false){{$('notice').textContent=msg;$('notice').style.display=msg?'block':'none';if(msg&&!always)setTimeout(()=>{{$('notice').style.display='none'}},3500)}}function setLoading(on){{$('loading').style.display=on?'flex':'none'}}function escapeHtml(s){{return String(s).replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]))}}
function render(data){{current=data;$('symbolName').textContent=data.name;$('searchInput').value=`${{data.name}}  ${{data.code}}`;if(window.StockAppShell&&StockAppShell.bindClearOnFocus){{const c=StockAppShell.bindClearOnFocus($('searchInput'));if(c)c.arm()}}$('journalLink').href='/stock_journal.html?code='+encodeURIComponent(data.code);renderDates(data);updateUrl(data);minuteReplay.load(data.points.length,{{showAll:true}})}}
function renderReplayFrame(frame){{
  if(!current)return;
  const pts=current.points.slice(0,frame.visibleCount),last=pts[pts.length-1];
  if(!last)return;
  const summary={{last:last.close,change_pct:last.change_pct,open:pts[0].open,high:Math.max(...pts.map(x=>+x.high)),low:Math.min(...pts.map(x=>+x.low)),volume:pts.reduce((n,x)=>n+(+x.volume||0),0),amount:pts.reduce((n,x)=>n+(+x.amount||0),0),intraday_volume_ratio:last.intraday_volume_ratio}};
  $('symbolMeta').textContent=`${{current.code}} · ${{current.date}} · ${{frame.dynamic?last.time:'全日'}} · 前收 ${{price(current.prev_close)}}`;
  $('lastPrice').textContent=price(summary.last);$('lastPrice').className='last '+cls(summary.change_pct);$('changePct').textContent=signed(summary.change_pct);$('changePct').className='change '+cls(summary.change_pct);
  $('openPrice').textContent=price(summary.open);$('highPrice').textContent=price(summary.high);$('lowPrice').textContent=price(summary.low);$('totalVolume').textContent=compact(summary.volume);$('totalAmount').textContent=compact(summary.amount);$('intradayVolumeRatio').textContent=summary.intraday_volume_ratio==null?'—':(+summary.intraday_volume_ratio).toFixed(2)+'×';
  $('replayPlay').textContent=frame.playing?'Ⅱ 暂停':frame.complete?'↻ 重新播放':frame.dynamic?'▶ 继续':'▶ 动态分时';$('replayClock').textContent=frame.dynamic?last.time:'全日';$('replayProgress').textContent=frame.dynamic?`${{frame.visibleCount}}/${{frame.total}} 分钟`:`全日 ${{frame.total}} 分钟`;$('replayStep').disabled=frame.complete;$('replayReset').disabled=!frame.total;$('replayAll').disabled=!frame.dynamic;
  const base=+current.prev_close;
  const chartData=frame.dynamic?{{...current,points:current.points.map((x,i)=>i<frame.visibleCount?x:{{...x,open:base,high:base,low:base,close:null,vwap:null,volume:null,amount:null,change_pct:null,intraday_volume_ratio:null,direction:0}})}}:current;
  renderChart(chartData);
}}
function renderDates(data){{$('dateSelect').innerHTML=data.dates.map(d=>`<option value="${{d}}" ${{d===data.date?'selected':''}}>${{d}}</option>`).join('');const i=data.dates.indexOf(data.date);$('prevDay').disabled=i<=0;$('nextDay').disabled=i<0||i>=data.dates.length-1}}
function renderChart(data){{const pts=data.points,times=pts.map(p=>p.time),closes=pts.map(p=>p.close),vwaps=pts.map(p=>p.vwap),vols=pts.map(p=>p.volume),base=data.prev_close,extrema=pts.flatMap(p=>[p.high,p.low]);let dev=Math.max(...extrema.map(v=>Math.abs(v-base)),Math.abs(base)*.002)*1.08;const ymin=base-dev,ymax=base+dev,pct=Math.max(dev/base*100,.2);chart.setOption({{animation:false,grid:[{{left:68,right:72,top:35,height:'62%'}},{{left:68,right:72,top:'76%',height:'15%'}}],axisPointer:{{link:[{{xAxisIndex:[0,1]}}],label:{{backgroundColor:'#586174'}}}},tooltip:{{trigger:'axis',axisPointer:{{type:'cross',snap:true}},backgroundColor:'rgba(27,31,40,.94)',borderWidth:0,textStyle:{{color:'#fff',fontSize:12}},formatter:params=>{{if(!params.length)return'';const i=params[0].dataIndex,p=pts[i],color=p.change_pct>=0?'#ff6b6b':'#41d49a',ratio=p.intraday_volume_ratio==null?'—':(+p.intraday_volume_ratio).toFixed(2)+'×';return`<div style="min-width:190px"><b>${{data.date}} ${{p.time}}</b><br><span style="color:${{color}};font-size:16px;font-weight:700">${{price(p.close)}} (${{signed(p.change_pct)}})</span><br>开 ${{price(p.open)}}　高 ${{price(p.high)}}<br>低 ${{price(p.low)}}　均价 ${{price(p.vwap)}}<br>成交量 ${{compact(p.volume)}}　量比 ${{ratio}}<br>成交额 ${{compact(p.amount)}}</div>`}}}},xAxis:[{{type:'category',gridIndex:0,data:times,boundaryGap:false,axisLabel:{{show:false}},axisTick:{{show:false}},axisLine:{{lineStyle:{{color:'#d9dde6'}}}}}},{{type:'category',gridIndex:1,data:times,boundaryGap:true,axisLabel:{{color:'#7b8190',formatter:(v,i)=>i%30===0?v:''}},axisTick:{{show:false}},axisLine:{{lineStyle:{{color:'#d9dde6'}}}}}}],yAxis:[{{type:'value',gridIndex:0,min:ymin,max:ymax,scale:true,splitNumber:6,axisLabel:{{formatter:v=>price(v),color:v=>v>=base?'#e5484d':'#16a36a'}},splitLine:{{lineStyle:{{color:'#edf0f5',type:'dashed'}}}}}},{{type:'value',gridIndex:0,min:-pct,max:pct,position:'right',splitNumber:6,axisLabel:{{formatter:v=>`${{v>=0?'+':''}}${{v.toFixed(2)}}%`,color:v=>v>=0?'#e5484d':'#16a36a'}},splitLine:{{show:false}}}},{{type:'value',gridIndex:1,scale:true,axisLabel:{{formatter:v=>compact(v),color:'#7b8190'}},splitLine:{{show:false}}}}],dataZoom:[{{type:'inside',xAxisIndex:[0,1],filterMode:'none'}},{{type:'slider',xAxisIndex:[0,1],bottom:4,height:18,showDetail:false,borderColor:'#e5e8ef'}}],series:[{{name:'价格',type:'line',xAxisIndex:0,yAxisIndex:0,data:closes,showSymbol:false,lineStyle:{{width:1.4,color:'#3478f6'}},areaStyle:{{color:{{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{{offset:0,color:'rgba(52,120,246,.18)'}},{{offset:1,color:'rgba(52,120,246,.01)'}}]}}}},markLine:{{silent:true,symbol:'none',label:{{formatter:`前收 ${{price(base)}}`,position:'insideEndTop',color:'#8a909e'}},lineStyle:{{color:'#9ca3af',type:'dashed'}},data:[{{yAxis:base}}]}}}},{{name:'均价',type:'line',xAxisIndex:0,yAxisIndex:0,data:vwaps,showSymbol:false,lineStyle:{{width:1,color:'#e5a000'}}}},{{name:'成交量',type:'bar',xAxisIndex:1,yAxisIndex:2,data:vols,itemStyle:{{color:p=>pts[p.dataIndex].direction>=0?'#e5484d':'#16a36a'}}}}]}},true)}}
async function api(path){{const r=await fetch(path,{{cache:'no-store'}}),body=await r.json();if(!r.ok)throw new Error(body.error||`HTTP ${{r.status}}`);return body}}async function loadData(code,date=''){{if(!API_AVAILABLE){{showNotice('当前是静态快照。要查询其他股票，请运行：python -m scripts.serve start web',true);return}}setLoading(true);try{{const q=new URLSearchParams({{code}});if(date)q.set('date',date);render(await api('/api/minute/data?'+q))}}catch(e){{showNotice(e.message,true)}}finally{{setLoading(false)}}}}async function doSearch(){{const q=$('searchInput').value.trim();if(!q)return;if(!API_AVAILABLE){{showNotice('查询需要本地服务：python -m scripts.serve start web',true);return}}try{{const rows=await api('/api/minute/search?q='+encodeURIComponent(q));showResults(rows);if(rows.length===1)loadData(rows[0].code)}}catch(e){{showNotice(e.message,true)}}}}function showResults(rows){{const box=$('results');if(!rows.length){{box.innerHTML='<div class="result">未找到缓存标的</div>';box.style.display='block';return}}box.innerHTML=rows.map(r=>`<div class="result" data-code="${{r.code}}"><span><b>${{escapeHtml(r.name)}}</b>　<span class="result-code">${{r.code}}</span></span><span class="result-date">${{r.latest_date}}</span></div>`).join('');box.style.display='block'}}function updateUrl(data){{if(!API_AVAILABLE)return;const u=new URL(location.href);u.searchParams.set('code',data.code);u.searchParams.set('date',data.date);history.replaceState(null,'',u)}}
$('replayPlay').onclick=()=>minuteReplay.toggle();$('replayStep').onclick=()=>minuteReplay.step();$('replayReset').onclick=()=>minuteReplay.reset();$('replayAll').onclick=()=>minuteReplay.showAll();$('replaySpeed').onchange=e=>minuteReplay.setSpeed(e.target.value);
$('searchBtn').onclick=doSearch;$('searchInput').addEventListener('keydown',e=>{{if(e.key==='Enter')doSearch();if(e.key==='Escape')$('results').style.display='none'}});$('searchInput').addEventListener('input',()=>{{clearTimeout(searchTimer);const q=$('searchInput').value.trim();if(!API_AVAILABLE||q.length<2){{$('results').style.display='none';return}}searchTimer=setTimeout(async()=>{{try{{showResults(await api('/api/minute/search?q='+encodeURIComponent(q)))}}catch(e){{}}}},220)}});$('results').addEventListener('click',e=>{{const row=e.target.closest('[data-code]');if(row){{$('results').style.display='none';loadData(row.dataset.code)}}}});document.addEventListener('click',e=>{{if(!e.target.closest('.search-wrap'))$('results').style.display='none'}});$('dateSelect').onchange=e=>loadData(current.code,e.target.value);$('prevDay').onclick=()=>{{const i=current.dates.indexOf(current.date);if(i>0)loadData(current.code,current.dates[i-1])}};$('nextDay').onclick=()=>{{const i=current.dates.indexOf(current.date);if(i<current.dates.length-1)loadData(current.code,current.dates[i+1])}};window.addEventListener('resize',()=>chart.resize());const requestedUrl=new URL(location.href),requestedCode=requestedUrl.searchParams.get('code'),requestedDate=requestedUrl.searchParams.get('date');if(!window.StockTrainingContext.active)render(INITIAL_DATA);if(API_AVAILABLE)loadData(requestedCode||INITIAL_DATA.code,requestedDate||'');else showNotice('这是可离线打开的静态快照；启动本地服务后可查询全部缓存标的。',true);
</script></body></html>'''
    return inject_intraday_replay(html)


def _safe_static_path(url_path: str):
    """Map /assets/... to scripts/services/static/... with path-traversal guards."""
    rel = url_path[len("/assets/"):].lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    root = os.path.realpath(STATIC_DIR)
    candidate = os.path.realpath(os.path.join(root, *rel.split("/")))
    if candidate != root and not candidate.startswith(root + os.sep):
        return None
    if not os.path.isfile(candidate):
        return None
    return candidate


def _cache_policy(url_path: str) -> str:
    """Keep market/API data fresh while allowing reusable UI assets to stay warm."""
    path = urlparse(url_path).path
    if path.startswith("/vendor/"):
        return "public, max-age=604800, immutable"
    if path.startswith("/assets/"):
        return "public, max-age=300, stale-while-revalidate=86400"
    if path.endswith(".html"):
        return "no-cache"
    return "no-store"



class MinuteRequestHandler(SimpleHTTPRequestHandler):
    repository = None
    trainer = None
    journal = None
    news = None
    market_context = None
    watchlist = None
    symbol_context = None

    def end_headers(self):
        """Cache versioned UI dependencies, but never cache market/API payloads."""
        policy = _cache_policy(self.path)
        self.send_header("Cache-Control", policy)
        if policy == "no-store":
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        super().end_headers()

    def _serve_static_asset(self, url_path: str):
        file_path = _safe_static_path(url_path)
        if not file_path:
            self.send_error(404, "Asset not found")
            return
        ctype = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        try:
            with open(file_path, "rb") as handle:
                payload = handle.read()
        except OSError:
            self.send_error(404, "Asset not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if params.get('training_session') and parsed.path == '/minute_view.html':
            from scripts.services.training_context import context_state, minute_payload
            try:
                body = build_html(minute_payload(context_state(self.trainer, params))).encode('utf-8')
            except (ValueError, LookupError) as exc:
                return self._send_json({'error': str(exc)}, status=409)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if params.get('training_session') and parsed.path.endswith('.html') and parsed.path not in (
            '/trading_trainer.html', '/stock_journal.html', '/symbol.html', '/market_news.html'):
            return self._send_json({'error': '该页面包含最新行情，请结束训练后查看'}, status=409)
        if params.get('training_session') and parsed.path.startswith('/api/') and not parsed.path.startswith('/api/trainer/'):
            from scripts.services.training_context import read_context
            try:
                return self._send_json(read_context(self, parsed.path, params))
            except (ValueError, LookupError) as exc:
                return self._send_json({'error': str(exc)}, status=409)
        if parsed.path in ('/api/classification', '/api/classification/members'):
            from data.classifications import ClassificationRepository, normalize
            from scripts.services.classification_sync import pending
            try:
                repo = ClassificationRepository()
                if parsed.path.endswith('/members'):
                    return self._send_json(repo.members(params.get('name',[''])[0]))
                code = normalize(params.get('code',[''])[0])
                result = repo.read(code)
                result['pending'] = pending(code)
                return self._send_json(result)
            except ValueError as exc:
                return self._send_json({'error': str(exc)}, status=400)
        if parsed.path == "/api/system/status":
            from scripts.services.system_status import public_status
            return self._send_json(public_status())
        if parsed.path == "/api/health":
            return self._send_json({
                "status": "ok",
                "service": "stock-interactive-web",
                "version": 1,
                "listen_host": self.server.server_address[0],
                "features": ["minute", "grid", "trainer", "journal", "news", "market_context", "training_loop", "watchlist", "symbol_context", "hypotheses"],
                "pid": os.getpid(),
            })
        if parsed.path == "/api/minute/search":
            params = parse_qs(parsed.query)
            try:
                limit = int(params.get("limit", ["20"])[0])
            except ValueError:
                limit = 20
            return self._send_json(self.repository.search(params.get("q", [""])[0], limit))
        if parsed.path == "/api/minute/available":
            params = parse_qs(parsed.query)
            try:
                return self._send_json(self.repository.availability(
                    params.get("code", [""])[0], params.get("date", [""])[0]
                ))
            except ValueError as exc:
                return self._send_json({"error": str(exc)}, status=400)
            except Exception as exc:
                return self._send_json({"error": f"检查分钟缓存失败: {exc}"}, status=500)
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
        if parsed.path == "/api/trainer/dates":
            params = parse_qs(parsed.query)
            try:
                return self._send_json(
                    self.trainer.dates(params.get("code", [""])[0])
                )
            except LookupError as exc:
                return self._send_json({"error": str(exc)}, status=404)
            except ValueError as exc:
                return self._send_json({"error": str(exc)}, status=400)
        if parsed.path == "/api/trainer/state":
            params = parse_qs(parsed.query)
            try:
                return self._send_json(
                    self.trainer.state(params.get("session_id", [""])[0])
                )
            except LookupError as exc:
                return self._send_json({"error": str(exc)}, status=404)
            except ValueError as exc:
                return self._send_json({"error": str(exc)}, status=400)
            except Exception as exc:
                return self._send_json({"error": f"读取训练会话失败: {exc}"}, status=500)
        if parsed.path == "/api/trainer/meta":
            return self._send_json(self.trainer.meta_options())
        if parsed.path == "/api/trainer/review":
            params = parse_qs(parsed.query)
            try:
                return self._send_json(self.trainer.review(
                    params.get("session_id", [""])[0],
                    market_date=params.get("date", [None])[0],
                ))
            except LookupError as exc:
                return self._send_json({"error": str(exc)}, status=404)
            except ValueError as exc:
                return self._send_json({"error": str(exc)}, status=400)
            except Exception as exc:
                return self._send_json({"error": f"读取训练复盘失败: {exc}"}, status=500)
        if parsed.path == "/api/trainer/plan":
            params = parse_qs(parsed.query)
            try:
                state = self.trainer.state(params.get("session_id", [""])[0])
                return self._send_json({
                    "session_id": state.get("session_id"),
                    "run_id": state.get("run_id"),
                    "day_plan": state.get("day_plan"),
                    "plan_violations": state.get("plan_violations") or [],
                })
            except LookupError as exc:
                return self._send_json({"error": str(exc)}, status=404)
            except ValueError as exc:
                return self._send_json({"error": str(exc)}, status=400)
            except Exception as exc:
                return self._send_json({"error": f"读取日计划失败: {exc}"}, status=500)
        if parsed.path.startswith("/api/news/"):
            return self._news_get(parsed)
        if parsed.path in ("/api/trainer/market-context", "/api/market/context"):
            return self._market_context_get(parsed)
        if parsed.path.startswith("/api/journal/"):
            return self._journal_get(parsed)
        if parsed.path.startswith("/api/watchlist/"):
            return self._watchlist_get(parsed)
        if parsed.path in ("/api/symbol/context", "/api/symbol/hypothesis"):
            return self._symbol_get(parsed)
        if parsed.path.startswith("/assets/"):
            return self._serve_static_asset(parsed.path)
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/classification/sync':
            from scripts.services.classification_sync import enqueue
            try:
                payload = self._read_json()
                if payload.get('training_session') or parse_qs(parsed.query).get('training_session'):
                    return self._send_json({'error':'训练过程中不加载当前板块资料'}, status=409)
                return self._send_json(enqueue(payload.get('code',''), force=bool(payload.get('force'))))
            except ValueError as exc:
                return self._send_json({'error':str(exc)}, status=400)
        if parsed.path.startswith("/api/news/"):
            return self._news_post(parsed)
        if parsed.path.startswith("/api/journal/"):
            return self._journal_post(parsed)
        if parsed.path.startswith("/api/watchlist/"):
            return self._watchlist_post(parsed)
        if parsed.path.startswith("/api/symbol/"):
            return self._symbol_post(parsed)
        if not parsed.path.startswith("/api/trainer/"):
            return self._send_json({"error": "接口不存在"}, status=404)
        try:
            payload = self._read_json()
            if parsed.path == "/api/trainer/session":
                result = self.trainer.create(payload)
            elif parsed.path == "/api/trainer/advance":
                result = self.trainer.advance(
                    payload.get("session_id", ""), int(payload.get("steps", 1))
                )
            elif parsed.path == "/api/trainer/next-day":
                result = self.trainer.next_day(payload.get("session_id", ""))
            elif parsed.path == "/api/trainer/order":
                result = self.trainer.order(
                    payload.get("session_id", ""), payload.get("side", ""),
                    payload.get("shares", 0),
                    payload.get("note") or payload.get("reason") or "",
                    payload.get("order_type", "market"),
                    payload.get("limit_price"), payload.get("validity", "day"),
                    emotion=payload.get("emotion", ""),
                    planned_stop=payload.get("planned_stop"),
                    planned_target=payload.get("planned_target"),
                    context=payload.get("context"),
                    require_decision=bool(payload.get("require_decision", False)),
                )
            elif parsed.path == "/api/trainer/order/cancel":
                result = self.trainer.cancel_pending_order(
                    payload.get("session_id", ""), payload.get("order_id", ""),
                    note=payload.get("note") or payload.get("reason") or "",
                    emotion=payload.get("emotion", ""),
                    context=payload.get("context"),
                )
            elif parsed.path == "/api/trainer/condition":
                result = self.trainer.conditional_order(
                    payload.get("session_id", ""), payload.get("side", ""),
                    payload.get("condition_type", ""),
                    payload.get("trigger_value", ""), payload.get("shares", 0),
                    payload.get("note", ""), payload.get("validity", "day"),
                    payload.get("secondary_trigger_value"),
                )
            elif parsed.path == "/api/trainer/condition/cancel":
                result = self.trainer.cancel_conditional_order(
                    payload.get("session_id", ""),
                    payload.get("condition_id", ""),
                )
            elif parsed.path == "/api/trainer/plan":
                result = self.trainer.set_day_plan(payload.get("session_id", ""), payload)
            elif parsed.path == "/api/trainer/mindset":
                result = self.trainer.add_mindset_marker(
                    payload.get("session_id", ""), payload
                )
            elif parsed.path == "/api/trainer/review":
                result = self.trainer.review(
                    payload.get("session_id", ""),
                    market_date=payload.get("market_date") or payload.get("date"),
                )
            else:
                return self._send_json({"error": "接口不存在"}, status=404)
            return self._send_json(result)
        except LookupError as exc:
            return self._send_json({"error": str(exc)}, status=404)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            return self._send_json({"error": f"交易训练失败: {exc}"}, status=500)


    def _market_context_get(self, parsed):
        params = parse_qs(parsed.query)
        try:
            session_id = params.get("session_id", [None])[0]
            market_date = params.get("date", [None])[0]
            as_of = params.get("as_of", [None])[0]
            if session_id:
                state = self.trainer.state(session_id)
                market_date = state.get("date")
                as_of = state.get("time")
            if not market_date:
                raise ValueError("请提供 date 或 session_id")
            result = self.market_context.context(market_date, as_of=as_of)
            if session_id:
                result["session_id"] = session_id
            return self._send_json(result)
        except LookupError as exc:
            return self._send_json({"error": str(exc)}, status=404)
        except (TypeError, ValueError) as exc:
            return self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            return self._send_json({"error": f"读取市场情境失败: {exc}"}, status=500)

    def _news_get(self, parsed):
        params = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/news/day":
                result = self.news.day(
                    params.get("date", [""])[0],
                    as_of=params.get("as_of", [None])[0],
                    refresh=(
                        params.get("refresh", ["0"])[0].lower()
                        in {"1", "true", "yes"}
                    ),
                    include_announcements=(
                        params.get("include_announcements", ["0"])[0].lower()
                        in {"1", "true", "yes"}
                    ),
                )
            elif parsed.path == "/api/news/impacts":
                result = self.news.impacts(
                    market_date=params.get("date", [""])[0],
                    code=params.get("code", [None])[0],
                    include_deleted=(
                        params.get("include_deleted", ["0"])[0].lower()
                        in {"1", "true", "yes"}
                    ),
                )
            else:
                return self._send_json({"error": "接口不存在"}, status=404)
            return self._send_json(result)
        except LookupError as exc:
            return self._send_json({"error": str(exc)}, status=404)
        except (TypeError, ValueError) as exc:
            return self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            return self._send_json({"error": f"读取市场资讯失败: {exc}"}, status=500)

    def _news_post(self, parsed):
        try:
            payload = self._read_json()
            if parsed.path == "/api/news/impact":
                result = self.news.add_impact(
                    news_id=payload.get("news_id", ""),
                    market_date=payload.get("market_date", ""),
                    decision_time=payload.get("decision_time"),
                    code=payload.get("code", ""),
                    action=payload.get("action", ""),
                    stance=payload.get("stance", ""),
                    note=payload.get("note", ""),
                )
            elif parsed.path == "/api/news/impact/delete":
                result = self.news.delete_impact(payload.get("impact_id", ""))
            else:
                return self._send_json({"error": "接口不存在"}, status=404)
            return self._send_json(result)
        except LookupError as exc:
            return self._send_json({"error": str(exc)}, status=404)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            return self._send_json({"error": f"保存资讯影响失败: {exc}"}, status=500)

    def _journal_get(self, parsed):
        params = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/journal/search":
                result = self.journal.search(
                    params.get("q", [""])[0], params.get("limit", [20])[0]
                )
            elif parsed.path == "/api/journal/kline":
                result = self.journal.kline(
                    params.get("code", [""])[0], params.get("days", [180])[0]
                )
            elif parsed.path == "/api/journal/cases":
                result = self.journal.list_cases(
                    code=params.get("code", [None])[0],
                    status=params.get("status", [None])[0],
                    query=params.get("q", [None])[0],
                    limit=params.get("limit", [200])[0],
                )
            elif parsed.path == "/api/journal/case":
                result = self.journal.get_case(params.get("case_id", [""])[0])
            elif parsed.path == "/api/journal/entries":
                result = self.journal.list_entries(
                    code=params.get("code", [None])[0],
                    case_id=params.get("case_id", [None])[0],
                    include_deleted=(
                        params.get("include_deleted", ["0"])[0].lower()
                        in {"1", "true", "yes"}
                    ),
                    limit=params.get("limit", [2000])[0],
                )
            elif parsed.path == "/api/journal/due":
                result = self.journal.due_reviews(
                    as_of=params.get("as_of", [None])[0],
                    limit=params.get("limit", [100])[0],
                )
            else:
                return self._send_json({"error": "接口不存在"}, status=404)
            return self._send_json(result)
        except LookupError as exc:
            return self._send_json({"error": str(exc)}, status=404)
        except (TypeError, ValueError) as exc:
            return self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            return self._send_json({"error": f"读取选股日记失败: {exc}"}, status=500)

    def _journal_post(self, parsed):
        try:
            payload = self._read_json()
            params = parse_qs(parsed.query)
            if params.get('training_session'):
                from scripts.services.training_context import context_state, journal_source
                state = context_state(self.trainer, params)
                source = journal_source(state)
                if parsed.path == '/api/journal/case':
                    payload['code'] = state['code']
                    payload['source'] = source
                elif parsed.path == '/api/journal/entry':
                    case = self.journal.get_case(payload.get('case_id'))
                    if case.get('source') != source or payload.get('market_date', '') > state['date']:
                        raise ValueError('日记必须属于本次训练，且行情日期不能晚于模拟日期')
                else:
                    raise ValueError('训练模式仅支持追加案例和记录')
            if parsed.path == "/api/journal/case":
                result = self.journal.create_case(payload)
            elif parsed.path == "/api/journal/entry":
                result = self.journal.add_entry(payload)
            elif parsed.path == "/api/journal/entry/delete":
                result = self.journal.delete_entry(payload)
            elif parsed.path == "/api/journal/entry/restore":
                result = self.journal.restore_entry(payload)
            elif parsed.path == "/api/journal/backup":
                result = self.journal.backup()
            else:
                return self._send_json({"error": "接口不存在"}, status=404)
            return self._send_json(result)
        except LookupError as exc:
            return self._send_json({"error": str(exc)}, status=404)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            return self._send_json({"error": f"保存选股日记失败: {exc}"}, status=500)


    def _watchlist_get(self, parsed):
        params = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/watchlist/items":
                status = params.get("status", [None])[0] or None
                code = params.get("code", [None])[0] or None
                result = self.watchlist.list_items(
                    status=status, code=code, limit=params.get("limit", [200])[0]
                )
            elif parsed.path == "/api/watchlist/tracks":
                result = self.watchlist.tracks(
                    track_date=params.get("track_date", [None])[0] or None,
                    screen_date=params.get("screen_date", [None])[0] or None,
                    limit=params.get("limit", [200])[0],
                )
            elif parsed.path == "/api/watchlist/sectors":
                result = self.watchlist.sector_strength(
                    market_date=params.get("date", [None])[0] or None,
                    top_n=int(params.get("top_n", ["15"])[0]),
                )
            else:
                return self._send_json({"error": "接口不存在"}, status=404)
            return self._send_json(result)
        except LookupError as exc:
            return self._send_json({"error": str(exc)}, status=404)
        except (TypeError, ValueError) as exc:
            return self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            return self._send_json({"error": f"读取观察池失败: {exc}"}, status=500)

    def _watchlist_post(self, parsed):
        try:
            payload = self._read_json()
            if parsed.path == "/api/watchlist/add":
                result = self.watchlist.add(payload)
            elif parsed.path == "/api/watchlist/status":
                result = self.watchlist.set_status(payload)
            elif parsed.path == "/api/watchlist/delete":
                result = self.watchlist.delete(payload)
            elif parsed.path == "/api/watchlist/refresh":
                result = self.watchlist.refresh_tracking(
                    as_of=payload.get("as_of"),
                    fail_threshold_pct=float(payload.get("fail_threshold_pct", -3.0)),
                )
            else:
                return self._send_json({"error": "接口不存在"}, status=404)
            return self._send_json(result)
        except LookupError as exc:
            return self._send_json({"error": str(exc)}, status=404)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            return self._send_json({"error": f"保存观察池失败: {exc}"}, status=500)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Content-Length 格式错误")
        if length <= 0 or length > 256 * 1024:
            raise ValueError("请求体为空或过大")
        return json.loads(self.rfile.read(length).decode("utf-8"))

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


    def _symbol_get(self, parsed):
        params = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/symbol/context":
                result = self.symbol_context.context(params.get("code", [""])[0])
            elif parsed.path == "/api/symbol/hypothesis":
                code = params.get("code", [""])[0]
                try:
                    limit = int(params.get("limit", ["50"])[0])
                except ValueError:
                    limit = 50
                items = self.symbol_context.hypotheses.list_for_code(
                    code, limit=limit
                )
                result = {"items": items, "count": len(items)}
            else:
                return self._send_json({"error": "接口不存在"}, status=404)
            return self._send_json(result)
        except LookupError as exc:
            return self._send_json({"error": str(exc)}, status=404)
        except (TypeError, ValueError) as exc:
            return self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            return self._send_json({"error": f"读取标的上下文失败: {exc}"}, status=500)

    def _symbol_post(self, parsed):
        try:
            payload = self._read_json()
            if parsed.path == "/api/symbol/hypothesis":
                result = self.symbol_context.create_hypothesis(payload)
            elif parsed.path == "/api/symbol/hypothesis/status":
                result = self.symbol_context.update_hypothesis(payload)
            else:
                return self._send_json({"error": "接口不存在"}, status=404)
            return self._send_json(result)
        except LookupError as exc:
            return self._send_json({"error": str(exc)}, status=404)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            return self._send_json({"error": f"保存假设失败: {exc}"}, status=500)




def write_app(path: str, payload: dict):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_html(payload))
    print(f"✓ 页面: {path} ({os.path.getsize(path) / 1024:.0f} KB, {len(payload['points'])} 根分钟线)")


def serve(repository: MinuteRepository, trainer, journal, news, market_context, watchlist, symbol_context, html_path: str, host: str, port: int):
    directory = os.path.dirname(os.path.abspath(html_path))
    handler = lambda *args, **kwargs: MinuteRequestHandler(*args, directory=directory, **kwargs)
    MinuteRequestHandler.repository = repository
    MinuteRequestHandler.trainer = trainer
    MinuteRequestHandler.journal = journal
    MinuteRequestHandler.news = news
    MinuteRequestHandler.market_context = market_context
    MinuteRequestHandler.watchlist = watchlist
    MinuteRequestHandler.symbol_context = symbol_context
    server = ThreadingHTTPServer((host, port), handler)
    print(f"✓ 分时查询服务已启动: http://{host}:{port}/{os.path.basename(html_path)}")
    print(f"✓ 网格动态回放: http://{host}:{port}/grid_simulator.html")
    print(f"✓ T+1 交易训练: http://{host}:{port}/trading_trainer.html")
    print(f"✓ 选股日记工作台: http://{host}:{port}/stock_journal.html")
    print(f"✓ 市场资讯复盘: http://{host}:{port}/market_news.html")
    print(f"✓ 观察池跟踪: http://{host}:{port}/watchlist.html")
    print(f"✓ 标的上下文: http://{host}:{port}/symbol.html")
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
        from scripts.services.trading_trainer import (
            TradingTrainerService, write_app as write_trainer_app,
        )
        from scripts.services.stock_journal import (
            StockJournalService, write_app as write_journal_app,
        )
        from data.market_news import MarketNewsRepository
        from data.market_context import MarketContextService
        from scripts.services.market_news import write_app as write_news_app
        from scripts.services.watchlist import (
            WatchlistService, write_app as write_watchlist_app,
        )
        from scripts.services.symbol_context import (
            SymbolContextService, write_app as write_symbol_app,
        )
        write_grid_app()
        write_trainer_app()
        write_journal_app()
        write_news_app()
        write_watchlist_app()
        write_symbol_app()
        from scripts.reports.gen_sector_atlas import generate as generate_sector_atlas
        generate_sector_atlas()
        try:
            from scripts.reports.gen_daily_ops import generate as generate_daily_ops
            generate_daily_ops(skip_sector=True)
        except Exception as exc:
            print(f"! 每日操盘清单刷新失败（不影响交互服务）: {exc}")
        trainer = TradingTrainerService(repository)
        journal = StockJournalService(repository.name_map)
        news = MarketNewsRepository()
        market_context = MarketContextService()
        watchlist = WatchlistService(name_map=repository.name_map)
        symbol_context = SymbolContextService(name_map=repository.name_map)
        try:
            from scripts.reports.gen_index import generate as generate_index
            generate_index()
        except Exception as exc:
            print(f"! 导航页刷新失败（不影响交互服务）: {exc}")
        serve(repository, trainer, journal, news, market_context, watchlist, symbol_context, args.out, args.host, args.port)
    else:
        print("  查询全部标的请运行: python -m scripts.serve start web")


if __name__ == "__main__":
    main()
