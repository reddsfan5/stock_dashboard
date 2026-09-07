"""
统一报告引擎 — 生成自包含 HTML 仪表盘

支持的输出模式：
  - screening: DataTables 表格 + ECharts K线弹窗（选股结果展示）
  - backtest:  Chart.js 图表 + 统计表格（策略回测展示）
  - mobile:    卡片式手机版页面
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from features.snapshot import METRIC_DEFINITIONS, snapshot_lookup

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

KLINE_TOOLTIP_JS = r"""
function klinePickIndex(ps){
  var idx=-1;
  for(var i=0;i<ps.length;i++){
    if(ps[i].seriesName==="K线"&&ps[i].dataIndex!=null){idx=ps[i].dataIndex;break;}
  }
  if(idx<0&&ps.length&&ps[0].dataIndex!=null)idx=ps[0].dataIndex;
  return idx;
}
function klineFmtPx(v){
  v=+v; if(!Number.isFinite(v))return '—';
  return v.toFixed(v<10?3:2);
}
function klineFmtAmt(vol){
  vol=+vol||0; if(vol<=0)return '';
  if(vol>=1e8)return (vol/1e8).toFixed(2)+'亿';
  if(vol>=1e4)return (vol/1e4).toFixed(0)+'万';
  return String(Math.round(vol));
}
function klineRangePctToIndex(ohlc,idx){
  if(!ohlc||!ohlc.length||idx==null||idx<0||!ohlc[idx])return null;
  var start=+ohlc[idx][0], last=ohlc[ohlc.length-1], end=last?+last[1]:NaN;
  if(!(start>0)||!Number.isFinite(end))return null;
  return (end-start)/start*100;
}
function klineTooltipCompact(idx,ohlc,dates,prevs,volumes,changes){
  if(idx==null||idx<0||!ohlc[idx])return '';
  var raw=ohlc[idx],o=+raw[0],c=+raw[1],l=+raw[2],h=+raw[3];
  var chg=changes[idx],vol=+(volumes[idx]||0);
  var dt=String(dates[idx]||''); if(dt.length>=10)dt=dt.slice(5,10);
  var chgTxt=chg==null?'':((chg>0?'+':'')+(+chg).toFixed(2)+'%');
  var rangePct=klineRangePctToIndex(ohlc,idx);
  var rangeTxt=rangePct==null?'':('区间'+(rangePct>=0?'+':'')+rangePct.toFixed(2)+'%');
  var amt=klineFmtAmt(vol);
  var parts=[dt,'开'+klineFmtPx(o),'高'+klineFmtPx(h),'低'+klineFmtPx(l),'收'+klineFmtPx(c)];
  if(chgTxt)parts.push(chgTxt);
  if(rangeTxt)parts.push(rangeTxt);
  if(amt)parts.push('额'+amt);
  return parts.join(' ');
}
function klineTooltipHtml(ps,ohlc,dates,prevs,volumes,changes,endLabel){
  var idx=klinePickIndex(ps);
  if(idx<0||!ohlc[idx])return "";
  var raw=ohlc[idx],o=+raw[0],c=+raw[1],l=+raw[2],h=+raw[3],prev=+(prevs[idx]||0);
  var chg=changes[idx],vol=+(volumes[idx]||0),ampUp=0,ampDown=0;
  var r="<b>"+dates[idx]+"</b><br>开: "+o+"　收: "+c+"<br>高: "+h+"　低: "+l;
  if(vol>0)r+="<br>成交额: "+(vol/1e8).toFixed(2)+"亿";
  if(prev>0){ampUp=(h-prev)/prev*100;ampDown=(l-prev)/prev*100;}
  if(chg!=null)r+="<br>涨跌幅: "+(chg>0?"+":"")+(+chg).toFixed(2)+"%　振幅: "+(ampUp-ampDown).toFixed(2)+"%";
  var last=ohlc[ohlc.length-1],start=o,end=last?+last[1]:NaN;
  if(start>0&&Number.isFinite(end)){
    var delta=end-start,pct=delta/start*100,color=delta>0?"#d32f2f":delta<0?"#159568":"#758096";
    r+='<div style="margin-top:6px;padding-top:6px;border-top:1px solid rgba(128,128,128,.35)">区间涨跌 <b style="color:'+color+'">'+(delta>=0?'+':'')+delta.toFixed(start<10?3:2)+'（'+(pct>=0?'+':'')+pct.toFixed(2)+'%）</b><br><span style="color:#8a94a6">'+dates[idx]+' 开 '+start.toFixed(start<10?3:2)+' → '+dates[dates.length-1]+' '+(endLabel||'最新收')+' '+end.toFixed(end<10?3:2)+' · '+(ohlc.length-idx)+'根K线</span></div>';
  }
  return r;
}
"""


# ====================================================================
# 选股仪表盘 — 手机版（卡片式 + ECharts K线弹窗）
# ====================================================================

def build_screening_mobile(
    pipeline_modules: List,
    results: Dict[str, pd.DataFrame],
    data=None,
    decision_snapshot: Optional[pd.DataFrame] = None,
    title: str = "A股选股",
) -> str:
    """旧手机版地址使用同一响应式页面，避免筛选逻辑分叉。"""
    from scripts.services.ui_shell import mobile_redirect
    return mobile_redirect('dashboard.html')


# Desktop and mobile share the same report model.

def build_screening_html(
    pipeline_modules: List,
    results: Dict[str, pd.DataFrame],
    data=None,
    decision_snapshot: Optional[pd.DataFrame] = None,
    title: str = "A股选股仪表盘",
) -> str:
    """生成选股仪表盘 HTML（桌面版）"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    decision_map = snapshot_lookup(decision_snapshot)
    market_date = "—"
    if decision_snapshot is not None and "指标日期" in decision_snapshot.columns:
        dates = pd.to_datetime(
            decision_snapshot["指标日期"], errors="coerce"
        ).dropna()
        if len(dates):
            market_date = dates.max().strftime("%Y-%m-%d")

    # 首屏只嵌入轻量的标的元数据。日 K 在用户点开侧栏时通过现有只读
    # API 按代码加载，避免把最多 1000 × 60 根 K 线塞入 HTML。
    all_codes = []
    seen_codes = set()
    kline_meta = {}
    for df in results.values():
        if len(df) > 0 and "代码" in df.columns:
            for _, row in df.head(500).iterrows():
                code = str(row.get("代码", ""))
                if not code or code in seen_codes or len(all_codes) >= 1000:
                    continue
                seen_codes.add(code)
                all_codes.append(code)
                name = row.get("名称", "")
                sector = row.get("申万1级", "")
                if pd.isna(name):
                    name = ""
                if pd.isna(sector):
                    sector = ""
                kline_meta[code] = {
                    "name": str(name),
                    "sector": str(sector),
                    "metrics": decision_map.get(code, {}),
                }
    kline_meta_json = json.dumps(kline_meta, ensure_ascii=False)

    tab_buttons = ""
    tables_html = ""

    for i, mod in enumerate(pipeline_modules):
        df = results.get(mod.id)
        if df is None or len(df) == 0:
            continue

        active = "active" if not tab_buttons else ""
        tab_buttons += f"""
            <button class="tab-btn {active}" onclick="switchTab('{mod.id}')">{mod.title}
              <span class="count">{len(df)}</span>
            </button>"""

        cols = []
        for c in df.columns:
            cols.append(c.strftime("%Y-%m-%d") if isinstance(c, pd.Timestamp) else str(c))
        header = "".join(
            f'<th title="{METRIC_DEFINITIONS.get(c, "")}">{c}</th>'
            for c in cols
        )
        orig_cols = list(df.columns)
        rows = ""
        for _, row in df.head(2000).iterrows():
            cells = ""
            for orig_c, disp_c in zip(orig_cols, cols):
                val = row[orig_c]
                if isinstance(val, float) and not pd.isna(val):
                    if any(k in disp_c for k in ("振幅", "位置", "重叠", "累计", "接续",
                                                   "涨跌", "涨幅", "换手", "量比", "斜率", "评分")):
                        cells += f'<td class="num">{val:.2f}</td>'
                    elif any(k in disp_c for k in ("成交", "金额", "额")):
                        cells += f'<td class="num">{val:,.0f}</td>'
                    elif any(k in disp_c for k in ("价", "最高", "最低", "收", "开")):
                        cells += f'<td class="num">{val:.2f}</td>'
                    else:
                        cells += f'<td class="num">{val:.3f}</td>'
                elif pd.isna(val):
                    cells += "<td></td>"
                elif isinstance(val, str) and val.startswith("sh"):
                    cells += f'<td class="code-sh code-clickable" onclick="showKline(\'{val}\')">{val}</td>'
                elif isinstance(val, str) and val.startswith("sz"):
                    cells += f'<td class="code-sz code-clickable" onclick="showKline(\'{val}\')">{val}</td>'
                else:
                    cells += f"<td>{str(val)}</td>"
            rows += f"<tr>{cells}</tr>"

        tables_html += f"""
            <div id="tab-{mod.id}" class="tab-content {active}">
              <table id="tbl-{mod.id}" class="display">
                <thead><tr>{header}</tr></thead>
                <tbody>{rows}</tbody>
              </table>
            </div>"""

    stats = "".join(
        f'<div class="stat"><span class="stat-num">{len(results.get(m.id, pd.DataFrame()))}</span><span class="stat-label">{m.title}</span></div>'
        for m in pipeline_modules if m.id in results
    )

    tab_ids = json.dumps([m.id for m in pipeline_modules if m.id in results])

    # 每个 tab 的股票代码列表（用于 K 线键盘导航限在当前 tab 内）
    tab_codes = {}
    for m in pipeline_modules:
        df = results.get(m.id)
        if df is not None and len(df) > 0 and "代码" in df.columns:
            tab_codes[m.id] = df["代码"].head(500).tolist()
    tab_codes_json = json.dumps(tab_codes, ensure_ascii=False)
    definitions_html = "".join(
        f"<li><b>{label}</b><span>{definition}</span></li>"
        for label, definition in METRIC_DEFINITIONS.items()
    )

    # 自动刷新导航页
    try:
        from scripts.reports.gen_index import generate; generate()
    except Exception:
        pass

    return f"""<!DOCTYPE html>
<html lang="zh-CN" class="screen-pending">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<script>try{{var root=document.documentElement;root.dataset.theme=localStorage.getItem('stockAppTheme')||'light';root.dataset.density=localStorage.getItem('stockAppDensity')||'comfortable';root.dataset.sidebar=localStorage.getItem('stockAppSidebar')||(innerWidth<1280?'collapsed':'expanded');}}catch(_){{}}</script>
<title>{title} — 行情 {market_date}</title>
<link rel="stylesheet" href="vendor/jquery.dataTables.min.css">
<link rel="stylesheet" href="/assets/app.css">
<script src="/assets/app-shell.js" defer></script>
<script id="workbench-js" src="/assets/workbench.js" defer></script>
<style>
.screen-pending .content,.screen-pending .wb-screen-toolbar,.screen-pending footer {{visibility:hidden}}
.screen-pending .content {{max-height:360px;overflow:hidden}}
#screen-loading {{position:absolute;top:100px;left:calc(var(--wb-side,216px) + 24px);right:24px;padding:24px;border:1px solid var(--app-border);border-radius:12px;background:var(--app-surface);color:var(--app-muted)}}
#screen-loading .placeholder {{height:44px;margin-top:16px;border-radius:8px;background:var(--app-surface-2)}}
html:not(.screen-pending) #screen-loading {{display:none}}
@media(max-width:767px){{#screen-loading {{left:12px;right:12px}}}}
</style>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; background:#f5f5f5; color:#333; }}
.header {{ background:linear-gradient(135deg,#1a1a2e,#16213e); color:white; padding:20px 32px; }}
.header h1 {{ font-size:22px; font-weight:600; }}
.header .date {{ color:#8892b0; font-size:13px; margin-top:2px; }}
.stats {{ display:flex; gap:16px; padding:16px 32px; background:white; border-bottom:1px solid #e0e0e0; flex-wrap:wrap; }}
.stat {{ display:flex; flex-direction:column; align-items:center; min-width:80px; }}
.stat-num {{ font-size:24px; font-weight:700; color:#1a73e8; }}
.stat-label {{ font-size:11px; color:#999; }}
.tabs {{ display:flex; gap:0; background:white; padding:0 32px; border-bottom:2px solid #e0e0e0; }}
.tab-btn {{ padding:12px 20px; border:none; background:none; cursor:pointer; font-size:13px; color:#666; border-bottom:3px solid transparent; transition:all .2s; display:flex; align-items:center; gap:6px; }}
.tab-btn:hover {{ color:#1a73e8; }}
.tab-btn.active {{ color:#1a73e8; border-bottom-color:#1a73e8; font-weight:600; }}
.tab-btn .count {{ background:#1a73e8; color:white; font-size:10px; padding:2px 8px; border-radius:10px; }}
.decision-tools {{ display:flex;align-items:end;gap:8px;padding:10px 32px;background:#fff;border-bottom:1px solid #e4e7ec;flex-wrap:wrap }}
.decision-tools .filter-field {{ display:flex;flex-direction:column;gap:3px }}.decision-tools label {{ color:#7b8495;font-size:10px;font-weight:600 }}
.decision-tools input {{ width:105px;height:34px;border:1px solid #ccd3df;border-radius:6px;padding:0 8px;font:inherit }}.decision-tools button {{ height:34px;border:1px solid #bfc9d8;border-radius:6px;background:#fff;padding:0 12px;cursor:pointer }}.decision-tools button.primary {{ background:#1a73e8;color:#fff;border-color:#1a73e8 }}
.metric-guide {{ margin-left:auto;position:relative }}.metric-guide summary {{ height:34px;display:flex;align-items:center;color:#1a73e8;cursor:pointer;font-size:12px }}.metric-guide ul {{ position:absolute;right:0;top:37px;width:430px;z-index:20;background:#fff;border:1px solid #dbe1ea;border-radius:8px;box-shadow:0 10px 28px rgba(20,30,50,.16);padding:8px 14px;list-style:none }}.metric-guide li {{ padding:6px 0;border-bottom:1px solid #eef1f5 }}.metric-guide li:last-child {{ border:0 }}.metric-guide li b {{ display:block;font-size:11px }}.metric-guide li span {{ color:#737e91;font-size:10px;line-height:1.45 }}
.content {{ padding:20px 24px; }}
.tab-content {{ display:none; }}
.tab-content.active {{ display:block; }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
.code-sh {{ color:#d32f2f; font-family:"SF Mono",monospace; }}
.code-sz {{ color:#1976d2; font-family:"SF Mono",monospace; }}
table.dataTable {{ font-size:12px; }}
.dataTables_wrapper {{ overflow-x:auto; }}
.kline-panel {{ display:none; position:fixed; right:0; top:0; width:520px; height:100vh; background:white; box-shadow:-4px 0 20px rgba(0,0,0,.15); z-index:1000; overflow-y:auto; }}
.kline-panel.active {{ display:block; }}
.kline-head {{ position:sticky; top:0; z-index:3; display:flex; align-items:center; gap:6px; padding:6px 8px; background:#1a73e8; color:#fff; min-height:40px; box-sizing:border-box; }}
.kline-back,.kline-nav-btn {{ appearance:none; border:0; background:rgba(255,255,255,.14); color:#fff; border-radius:8px; cursor:pointer; font:inherit; font-size:12px; font-weight:650; line-height:1; min-height:36px; min-width:36px; padding:0 10px; flex:0 0 auto; }}
.kline-back {{ padding:0 10px; }}
.kline-nav-btn {{ font-size:18px; padding:0; width:36px; }}
.kline-back:active,.kline-nav-btn:active {{ background:rgba(255,255,255,.28); }}
.kline-nav-btn:disabled {{ opacity:.35; cursor:default; }}
.kline-head-title {{ flex:1 1 auto; min-width:0; font-size:12px; font-weight:650; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; line-height:1.25; }}
.kline-head-nav {{ display:flex; align-items:center; gap:2px; flex:0 0 auto; }}
.kline-nav-count {{ font-size:11px; opacity:.9; min-width:2.8em; text-align:center; font-variant-numeric:tabular-nums; }}
.kline-metrics {{ display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:#e0e5ed }}.kline-metric {{ background:#fff;padding:8px 10px }}.kline-metric span {{ display:block;color:#818b9d;font-size:9px;margin-bottom:3px }}.kline-metric b {{ font-size:13px;font-variant-numeric:tabular-nums }}
.kline-actions {{ display:flex;flex-wrap:wrap;gap:6px;padding:8px 12px; }}
.journal-action {{ display:inline-flex;align-items:center;justify-content:center;margin:0;padding:6px 10px;text-align:center;text-decoration:none;background:#edf4ff;color:#1a73e8;border:1px solid #bfd2f7;border-radius:999px;font-weight:650;width:auto;flex:1 1 calc(50% - 6px);min-width:0;cursor:pointer;font:inherit;font-size:12px;line-height:1.2;box-sizing:border-box }}
.kline-chart-wrap {{ position:relative; width:100%; }}
.kline-panel .chart {{ width:100%; height:600px; touch-action:pan-y; overscroll-behavior:contain; }}
.kline-panel .chart.is-scrubbing, #klineChart.is-scrubbing {{ touch-action:none; }}
.kline-tip-overlay {{ position:absolute; top:0; left:0; right:0; z-index:5; pointer-events:none; margin:0; padding:4px 8px; font-size:11px; line-height:1.25; color:#f8fafc; background:rgba(15,23,42,.78); font-variant-numeric:tabular-nums; box-sizing:border-box; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; border-radius:0 0 6px 6px; }}
.kline-tip-overlay[hidden] {{ display:none !important; }}
.kline-tip-overlay b {{ font-weight:700; }}
.kline-state {{ margin:8px 12px;padding:10px 12px;border:1px solid var(--app-border);border-radius:8px;background:var(--app-surface-2);color:var(--app-muted);font-size:12px }}
.kline-state[hidden] {{ display:none }}
.kline-overlay {{ display:none; position:fixed; inset:0; background:rgba(0,0,0,.2); z-index:999; }}
.kline-overlay.active {{ display:block; }}
.code-clickable {{ cursor:pointer; }}
.code-clickable:hover {{ background:#e8f0fe !important; }}
footer {{ text-align:center; color:#999; font-size:11px; padding:20px; }}
@media(max-width:768px){{
  .kline-panel{{width:100vw}}
  .kline-head{{padding:5px 6px;min-height:38px;gap:4px}}
  .kline-back{{padding:0 8px;font-size:11px;min-height:34px}}
  .kline-nav-btn{{width:34px;min-width:34px;min-height:34px;font-size:17px}}
  .kline-head-title{{font-size:11px}}
  .kline-metrics{{grid-template-columns:repeat(4,minmax(0,1fr));gap:0;background:transparent;padding:4px 6px 2px;border-bottom:1px solid #e8edf5}}
  .kline-metric{{padding:3px 4px;background:transparent;border-radius:0}}
  .kline-metric span{{font-size:8px;margin-bottom:1px;color:#8a94a6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .kline-metric b{{font-size:11px;line-height:1.15}}
  .kline-actions{{gap:4px;padding:4px 8px 6px}}
  .journal-action{{flex:1 1 calc(25% - 4px);padding:5px 4px;font-size:11px;font-weight:600;border-radius:8px}}
  .kline-panel .chart{{height:min(62vh,560px);min-height:360px;touch-action:pan-y}}
  .decision-tools{{padding:8px 12px}}
  .metric-guide{{margin-left:0}}
  .metric-guide ul{{position:fixed;left:10px;right:10px;top:150px;width:auto}}
}}
</style>
<link id="workbench-css" rel="stylesheet" href="/assets/workbench.css">
</head>
<body class="app-workbench" data-page="dashboard">
<div id="app-shell" data-active="dashboard"></div>
<div id="screen-loading" role="status"><span>正在准备选股列表…</span><div class="placeholder"></div><div class="placeholder"></div><button id="screen-retry" hidden onclick="location.reload()">重新加载</button></div>
<div class="kline-overlay" id="overlay" onclick="closeKline()"></div>
<div class="kline-panel" id="klinePanel">
  <div class="kline-head">
    <button type="button" class="kline-back" onclick="closeKline()" aria-label="返回上层">← 返回</button>
    <div class="kline-head-title" id="klineTitle"></div>
    <div class="kline-head-nav">
      <button type="button" class="kline-nav-btn" id="klinePrevBtn" onclick="navKline(-1)" aria-label="上一只">‹</button>
      <span class="kline-nav-count" id="klineNav">0/0</span>
      <button type="button" class="kline-nav-btn" id="klineNextBtn" onclick="navKline(1)" aria-label="下一只">›</button>
    </div>
  </div>
  <div class="kline-metrics" id="klineMetrics"></div>
  <div class="kline-actions">
    <a class="journal-action" id="klineSymbolLink" href="http://127.0.0.1:8765/symbol.html" title="标的上下文">上下文</a>
    <a class="journal-action" id="klineJournalLink" href="http://127.0.0.1:8765/stock_journal.html" title="选股日记">日记</a>
    <button class="journal-action" id="klineWatchBtn" type="button" title="加入观察池">观察+</button>
    <a class="journal-action" id="klineWatchLink" href="http://127.0.0.1:8765/watchlist.html" title="打开观察池">观察池</a>
  </div>
  <div class="notice" id="klineWatchNotice" style="margin:0 12px 4px;color:#7b8495;font-size:11px"></div>
  <div class="kline-state" id="klineState" role="status" hidden></div>
  <div class="kline-chart-wrap">
    <div id="klineTipBar" class="kline-tip-overlay" hidden></div>
    <div class="chart" id="klineChart"></div>
  </div>
</div>
<div class="header">
  <h1>{title}</h1>
  <div class="date">行情数据日 {market_date} · 页面生成 {now} · 点击代码按需加载日 K · <a href="http://127.0.0.1:8765/watchlist.html" style="color:#9ec1ff">观察池</a></div>
</div>
<div class="stats">{stats}</div>
<div class="tabs">{tab_buttons}</div>
<div class="decision-tools">
  <div class="filter-field"><label>成交量比20 ≥</label><input id="fVolume" type="number" step="0.1" placeholder="不限"></div>
  <div class="filter-field"><label>换手率% ≥</label><input id="fTurnover" type="number" step="0.1" placeholder="不限"></div>
  <div class="filter-field"><label>20日动量% ≥</label><input id="fMomentum" type="number" step="1" placeholder="不限"></div>
  <div class="filter-field"><label>ATR14% ≤</label><input id="fAtr" type="number" step="0.1" placeholder="不限"></div>
  <div class="filter-field"><label>动态PE ≤</label><input id="fPe" type="number" step="1" placeholder="不限"></div>
  <button class="primary" id="applyFilters">应用筛选</button><button id="resetFilters">清空</button>
  <details class="metric-guide"><summary>指标口径</summary><ul>{definitions_html}</ul></details>
</div>
<div class="content">{tables_html}</div>
<footer>行情数据日 {market_date} · 每个工作日 18:30 自动更新 · 点击股票代码按需加载 K 线</footer>

<script src="vendor/jquery.min.js"></script>
<script>window.jQuery || document.write(`<script src='https://code.jquery.com/jquery-3.7.0.min.js'><\/script>`);</script>
<script src="vendor/jquery.dataTables.min.js"></script>
<script>window.jQuery && jQuery.fn.dataTable || document.write(`<script src='https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js'><\/script>`);</script>
<script>
function switchTab(id){{
  currentTabId=id;
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
  document.querySelector(`[onclick="switchTab('${{id}}')"]`).classList.add('active');
  document.getElementById('tab-'+id).classList.add('active');
  initStrategyTable(id).columns.adjust().draw();
}}
var currentTabId=({tab_ids})[0]||null;
function filterValue(id){{var raw=document.getElementById(id).value;return raw===''?null:+raw}}
function metricValue(settings,row,label){{var idx=-1;for(var i=0;i<settings.aoColumns.length;i++){{if(settings.aoColumns[i].nTh.textContent.trim()===label){{idx=i;break}}}}if(idx<0)return null;var raw=String(row[idx]??'').replace(/,/g,'').trim();if(raw==='')return null;var value=+raw;return Number.isFinite(value)?value:null}}
$.fn.dataTable.ext.search.push(function(settings,row){{if(!currentTabId||settings.nTable.id!=='tbl-'+currentTabId)return true;var rules=[['fVolume','成交量比20','min'],['fTurnover','换手率%','min'],['fMomentum','20日动量%','min'],['fAtr','ATR14%','max'],['fPe','动态PE','max']];for(var i=0;i<rules.length;i++){{var wanted=filterValue(rules[i][0]);if(wanted===null)continue;var actual=metricValue(settings,row,rules[i][1]);if(actual===null)return false;if(rules[i][2]==='min'&&actual<wanted)return false;if(rules[i][2]==='max'&&actual>wanted)return false}}return true}});
function applyDecisionFilters(){{if(currentTabId)$('#tbl-'+currentTabId).DataTable().draw()}}
document.getElementById('applyFilters').onclick=applyDecisionFilters;document.getElementById('resetFilters').onclick=function(){{['fVolume','fTurnover','fMomentum','fAtr','fPe'].forEach(function(id){{document.getElementById(id).value=''}});applyDecisionFilters()}};
function initStrategyTable(id){{
  var table=$('#tbl-'+id);
  if($.fn.dataTable.isDataTable(table[0]))return table.DataTable();
  return table.DataTable({{
      pageLength:25,
      autoWidth:false,
      language:{{search:'搜索:',lengthMenu:'显示 _MENU_ 条记录',info:'第 _START_ 至 _END_ 条，共 _TOTAL_ 条',infoEmpty:'暂无记录',infoFiltered:'（筛选自 _MAX_ 条记录）',zeroRecords:'没有符合条件的标的',emptyTable:'暂无标的',paginate:{{first:'首页',previous:'上页',next:'下页',last:'末页'}}}},
      order:[], layout:{{ topStart:'search', topEnd:'pageLength' }},
    }});
}}
$(document).ready(function(){{
  if(currentTabId)initStrategyTable(currentTabId);
  else document.documentElement.classList.remove('screen-pending');
}});
setTimeout(function(){{if(document.documentElement.classList.contains('screen-pending')){{document.querySelector('#screen-loading span').textContent='列表加载尚未完成，请重试';document.getElementById('screen-retry').hidden=false;}}}},8000);
</script>
<script>
var KLINE_META={kline_meta_json};
var TAB_CODES={tab_codes_json};
var klineChart=null;
var currentKlineCode=null;
var currentKlineData=null;
var klineRequestId=0;
var klineCache={{}};
{KLINE_TOOLTIP_JS}
function metricHtml(m){{if(!m)return'';var defs=[['成交量比20','成交量比20','×'],['成交额比20','成交额比20','×'],['换手率%','换手率','%'],['20日动量%','20日动量','%'],['市场相对强弱20%','市场相对强弱','%'],['ATR14%','ATR14','%'],['20日年化波动%','20日年化波动','%'],['距60日高点%','距60日高点','%'],['动态PE','动态PE',''],['流通市值(亿)','流通市值','亿'],['供应商量比','供应商量比','×']];return defs.map(function(x){{var v=m[x[0]],text=v===null||v===undefined?'—':(+v).toFixed(2)+x[2];return'<div class="kline-metric"><span>'+x[1]+'</span><b>'+text+'</b></div>'}}).join('')}}
function rangeReturnPctFromBars(bars){{
  if(!bars||!bars.length)return null;
  var start=+bars[0].open, end=+bars[bars.length-1].close;
  if(!(start>0)||!Number.isFinite(end))return null;
  return (end-start)/start*100;
}}
function upsertRangeReturnMetric(bars){{
  var box=document.getElementById('klineMetrics');
  if(!box)return;
  var old=box.querySelector('[data-metric="range-return"]');
  if(old)old.remove();
  var pct=rangeReturnPctFromBars(bars);
  var text='—', color='#758096';
  if(pct!=null&&Number.isFinite(pct)){{
    text=(pct>=0?'+':'')+pct.toFixed(2)+'%';
    color=pct>0?'#d32f2f':pct<0?'#34a853':'#758096';
  }}
  var el=document.createElement('div');
  el.className='kline-metric';
  el.setAttribute('data-metric','range-return');
  el.innerHTML='<span>区间涨幅</span><b style="color:'+color+'">'+text+'</b>';
  box.insertBefore(el, box.firstChild);
}}

function getCodeList(){{
  var btns=document.querySelectorAll('.tab-btn.active');
  if(btns.length>0){{
    var tabId=btns[0].getAttribute('onclick').match(/'(.*?)'/)[1];
    if(TAB_CODES[tabId]) return TAB_CODES[tabId];
  }}
  return Object.keys(KLINE_META).sort();
}}

function updateKlineNavChrome(code){{
  var list=getCodeList();
  var idx=list.indexOf(code);
  var nav=document.getElementById('klineNav');
  var prev=document.getElementById('klinePrevBtn');
  var next=document.getElementById('klineNextBtn');
  if(nav)nav.textContent=(idx>=0?idx+1:0)+"/"+list.length;
  if(prev)prev.disabled=!(idx>0);
  if(next)next.disabled=!(idx>=0&&idx<list.length-1);
}}
function navKline(dir){{
  if(!currentKlineCode)return;
  var list=getCodeList();
  var idx=list.indexOf(currentKlineCode);
  if(idx<0)return;
  var next=idx+dir;
  if(next<0||next>=list.length)return;
  showKline(list[next]);
}}

document.addEventListener('keydown',function(e){{
  if(!currentKlineCode)return;
  if(e.key=='ArrowLeft'){{e.preventDefault();navKline(-1);}}
  if(e.key=='ArrowRight'){{e.preventDefault();navKline(1);}}
  if(e.key=='Escape'){{e.preventDefault();closeKline();}}
}});

function loadEcharts(){{
  if(window.echarts)return Promise.resolve(window.echarts);
  if(window.klineEchartsPromise)return window.klineEchartsPromise;
  window.klineEchartsPromise=new Promise(function(resolve,reject){{
    var script=document.createElement('script');script.src='vendor/echarts.min.js';script.onload=function(){{window.echarts?resolve(window.echarts):reject(new Error('图表组件未就绪'))}};script.onerror=function(){{reject(new Error('图表组件加载失败'))}};document.head.appendChild(script);
  }});
  return window.klineEchartsPromise;
}}
function loadKline(code){{
  if(klineCache[code])return Promise.resolve(klineCache[code]);
  return fetch('/api/journal/kline?code='+encodeURIComponent(code)+'&days=60',{{cache:'no-store'}}).then(function(r){{return r.json().then(function(body){{if(!r.ok)throw new Error(body.error||('HTTP '+r.status));return body}})}}).then(function(body){{klineCache[code]=body;return body}});
}}
var klineScrubActive=false;
var klineLongPressTimer=null;
var klineTouchStartXY=null;
var klineScrubMoveHandler=null;
var klineScrubCtx=null;
function clearKlineTipBar(){{
  var bar=document.getElementById('klineTipBar');
  if(bar){{bar.hidden=true;bar.textContent='';bar.innerHTML='';}}
}}
function setKlineTipOverlayText(text){{
  var bar=document.getElementById('klineTipBar');
  if(!bar)return;
  if(!text){{clearKlineTipBar();return;}}
  bar.textContent=text;
  bar.hidden=false;
}}
function setKlineTipBarHtml(html){{
  var bar=document.getElementById('klineTipBar');
  if(!bar)return;
  if(!html){{clearKlineTipBar();return;}}
  bar.innerHTML=html;
  bar.hidden=false;
}}
function exitKlineScrub(){{
  if(klineLongPressTimer){{clearTimeout(klineLongPressTimer);klineLongPressTimer=null;}}
  klineTouchStartXY=null;
  if(!klineScrubActive&&!klineScrubMoveHandler){{
    var el0=document.getElementById('klineChart');
    if(el0)el0.classList.remove('is-scrubbing');
    return;
  }}
  klineScrubActive=false;
  var el=document.getElementById('klineChart');
  if(el){{
    el.classList.remove('is-scrubbing');
    if(klineScrubMoveHandler){{
      el.removeEventListener('touchmove',klineScrubMoveHandler);
      klineScrubMoveHandler=null;
    }}
  }}
  var panel=document.getElementById('klinePanel');
  if(panel)panel.classList.remove('is-scrubbing');
  if(klineChart){{
    try{{klineChart.dispatchAction({{type:'hideTip'}});}}catch(e){{}}
    var coarse=window.matchMedia&&window.matchMedia('(pointer:coarse)').matches;
    try{{klineChart.setOption({{tooltip:{{trigger:coarse?'none':'axis',triggerOn:coarse?'none':'mousemove|click',showContent:!coarse}},axisPointer:{{show:!coarse,type:'cross'}}}},false);}}catch(e){{}}
  }}
  clearKlineTipBar();
  // Keep klineScrubCtx so the next long-press can scrub again without re-render.
}}
function ensureKlineScrubCtx(){{
  if(klineScrubCtx)return klineScrubCtx;
  var d=currentKlineData, bars=d&&d.bars;
  if(!bars||!bars.length)return null;
  klineScrubCtx={{
    dates:bars.map(function(x){{return x.date}}),
    ohlc:bars.map(function(x){{return [x.open,x.close,x.low,x.high]}}),
    prevs:bars.map(function(x){{return x.pre_close}}),
    vols:bars.map(function(x){{return x.amount||0}}),
    changes:bars.map(function(x){{return x.change_pct}})
  }};
  return klineScrubCtx;
}}
function enterKlineScrub(touch){{
  if(klineScrubActive)return;
  if(!ensureKlineScrubCtx())return;
  klineScrubActive=true;
  var el=document.getElementById('klineChart');
  var panel=document.getElementById('klinePanel');
  if(el)el.classList.add('is-scrubbing');
  if(panel)panel.classList.add('is-scrubbing');
  try{{navigator.vibrate&&navigator.vibrate(10);}}catch(e){{}}
  if(el&&!klineScrubMoveHandler){{
    klineScrubMoveHandler=function(e){{
      if(!klineScrubActive)return;
      e.preventDefault();
      var t=e.touches&&e.touches[0];
      if(t)updateKlineScrubFromTouch(t);
    }};
    el.addEventListener('touchmove',klineScrubMoveHandler,{{passive:false}});
  }}
  if(klineChart){{
    try{{klineChart.setOption({{tooltip:{{trigger:'axis',triggerOn:'none',show:true,showContent:false}},axisPointer:{{show:true,type:'cross'}}}},false);}}catch(e){{}}
  }}
  if(touch)updateKlineScrubFromTouch(touch);
}}
function updateKlineScrubFromTouch(touch){{
  if(!klineChart||!ensureKlineScrubCtx())return;
  var el=document.getElementById('klineChart');
  if(!el)return;
  var rect=el.getBoundingClientRect();
  var x=touch.clientX-rect.left, y=touch.clientY-rect.top;
  var idx=-1;
  try{{
    var pt=klineChart.convertFromPixel({{gridIndex:0}},[x,y]);
    if(pt&&typeof pt[0]==='number')idx=Math.round(pt[0]);
  }}catch(e){{}}
  if(idx<0){{
    try{{
      var pt2=klineChart.convertFromPixel({{xAxisIndex:0}},[x]);
      if(typeof pt2==='number')idx=Math.round(pt2);
      else if(pt2&&typeof pt2[0]==='number')idx=Math.round(pt2[0]);
    }}catch(e){{}}
  }}
  var n=klineScrubCtx.dates.length;
  if(idx<0||idx>=n)return;
  setKlineTipOverlayText(klineTooltipCompact(idx,klineScrubCtx.ohlc,klineScrubCtx.dates,klineScrubCtx.prevs,klineScrubCtx.vols,klineScrubCtx.changes));
  try{{
    klineChart.dispatchAction({{type:'showTip',seriesIndex:0,dataIndex:idx}});
  }}catch(e){{}}
}}
function bindKlineScrub(chartEl){{
  if(!chartEl||chartEl.dataset.klineScrubBound==='1')return;
  chartEl.dataset.klineScrubBound='1';
  chartEl.addEventListener('touchstart',function(e){{
    if(!e.touches||e.touches.length!==1)return;
    if(klineScrubActive)exitKlineScrub();
    var t=e.touches[0];
    klineTouchStartXY={{x:t.clientX,y:t.clientY}};
    if(klineLongPressTimer)clearTimeout(klineLongPressTimer);
    klineLongPressTimer=setTimeout(function(){{
      klineLongPressTimer=null;
      if(!klineTouchStartXY)return;
      enterKlineScrub({{clientX:klineTouchStartXY.x,clientY:klineTouchStartXY.y}});
    }},350);
  }},{{passive:true}});
  chartEl.addEventListener('touchmove',function(e){{
    if(klineScrubActive)return;
    if(!klineLongPressTimer||!klineTouchStartXY||!e.touches||!e.touches[0])return;
    var t=e.touches[0];
    var dx=Math.abs(t.clientX-klineTouchStartXY.x), dy=Math.abs(t.clientY-klineTouchStartXY.y);
    if(dx>10||dy>10){{
      clearTimeout(klineLongPressTimer);
      klineLongPressTimer=null;
      klineTouchStartXY=null;
    }} else {{
      klineTouchStartXY={{x:t.clientX,y:t.clientY}};
    }}
  }},{{passive:true}});
  chartEl.addEventListener('touchend',function(){{exitKlineScrub();}},{{passive:true}});
  chartEl.addEventListener('touchcancel',function(){{exitKlineScrub();}},{{passive:true}});
}}
function renderKline(d){{
  var bars=d.bars||[],dates=bars.map(function(x){{return x.date}}),ohlc=bars.map(function(x){{return [x.open,x.close,x.low,x.high]}}),changes=bars.map(function(x){{return x.change_pct}}),prevs=bars.map(function(x){{return x.pre_close}}),vols=bars.map(function(x){{return x.amount||0}}),ma5=[],ma10=[];
  if(!bars.length)throw new Error('本地缓存中没有日 K 数据');
  upsertRangeReturnMetric(bars);
  for(var i=0;i<ohlc.length;i++){{
    ma5.push(i>=4?(ohlc.slice(i-4,i+1).reduce(function(s,x){{return s+x[1]}},0)/5).toFixed(2):'-');
    ma10.push(i>=9?(ohlc.slice(i-9,i+1).reduce(function(s,x){{return s+x[1]}},0)/10).toFixed(2):'-');
  }}
  exitKlineScrub();
  clearKlineTipBar();
  if(klineChart){{klineChart.dispose();klineChart=null;}}
  var chartEl=document.getElementById('klineChart');
  if(chartEl){{chartEl.classList.remove('chart-touch-lock','is-scrubbing');}}
  bindKlineScrub(chartEl);
  klineChart=echarts.init(chartEl);
  klineScrubCtx={{dates:dates,ohlc:ohlc,prevs:prevs,vols:vols,changes:changes}};
  var coarse=window.matchMedia&&window.matchMedia('(pointer:coarse)').matches;
  function tipHtmlFromParams(ps){{return klineTooltipHtml(ps,ohlc,dates,prevs,vols,changes,'最新收');}}
  function tipCompactFromIndex(idx){{return klineTooltipCompact(idx,ohlc,dates,prevs,vols,changes);}}
  function tipHtmlFromIndex(idx){{
    if(idx==null||idx<0||!ohlc[idx])return '';
    return tipHtmlFromParams([{{seriesName:'K线',dataIndex:idx}}]);
  }}
  function indexFromAxisEvent(ev){{
    if(!ev)return -1;
    if(ev.dataIndex!=null)return ev.dataIndex;
    var axes=ev.axesInfo||[];
    for(var i=0;i<axes.length;i++){{
      var v=axes[i]&&axes[i].value;
      if(v==null)continue;
      if(typeof v==='number')return v;
      var ix=dates.indexOf(v);
      if(ix>=0)return ix;
    }}
    return -1;
  }}
  klineChart.setOption({{
    tooltip:{{
      trigger:coarse?'none':'axis',
      triggerOn:coarse?'none':'mousemove|click',
      axisPointer:{{type:'cross'}},
      confine:true,
      showContent:!coarse,
      position:function(pos,params,el,elRect,size){{
        var viewW=size.viewSize[0],viewH=size.viewSize[1],tipW=size.contentSize[0],tipH=size.contentSize[1];
        var x=Math.min(Math.max(pos[0]-tipW/2,8),Math.max(8,viewW-tipW-8));
        var y=(pos[1]<viewH*0.45)?Math.max(8,viewH-tipH-8):8;
        return [x,y];
      }},
      formatter:function(ps){{
        var html=tipHtmlFromParams(ps);
        if(!coarse)setKlineTipBarHtml(html);
        else {{
          var idx=klinePickIndex(ps);
          if(idx>=0)setKlineTipOverlayText(tipCompactFromIndex(idx));
        }}
        return html;
      }}
    }},
    axisPointer:{{link:[{{xAxisIndex:'all'}}],show:!coarse}},
    grid:[{{left:'8%',right:'2%',top:'8%',height:'44%'}},{{left:'8%',right:'2%',top:'58%',height:'12%'}},{{left:'8%',right:'2%',top:'76%',height:'12%'}}],
    xAxis:[{{data:dates,axisLabel:{{rotate:30,fontSize:10}},gridIndex:0}},{{data:dates,axisLabel:{{show:false}},gridIndex:1}},{{data:dates,axisLabel:{{show:false}},gridIndex:2}}],
    yAxis:[{{scale:true,gridIndex:0,splitArea:{{show:true}}}},{{gridIndex:1,splitNumber:2,axisLabel:{{formatter:function(v){{return (v/1e8).toFixed(1)+'亿'}}}}}},{{gridIndex:2,splitNumber:3,axisLabel:{{formatter:'{{value}}%'}}}}],
    series:[
      {{name:'K线',type:'candlestick',data:ohlc,xAxisIndex:0,yAxisIndex:0,dimensions:['open','close','lowest','highest'],itemStyle:{{color:'#d32f2f',color0:'#34a853',borderColor:'#d32f2f',borderColor0:'#34a853'}},barWidth:'60%'}},
      {{name:'MA5',type:'line',data:ma5,xAxisIndex:0,yAxisIndex:0,smooth:true,lineStyle:{{width:1,color:'#ff9800'}},symbol:'none'}},
      {{name:'MA10',type:'line',data:ma10,xAxisIndex:0,yAxisIndex:0,smooth:true,lineStyle:{{width:1,color:'#2196f3'}},symbol:'none'}},
      {{name:'成交额',type:'bar',data:vols,xAxisIndex:1,yAxisIndex:1,itemStyle:{{color:function(p){{var i=p.dataIndex,o=ohlc[i][0],c=ohlc[i][1];return c>=o?'#d32f2f':'#34a853'}}}}}},
      {{name:'涨跌%',type:'bar',data:changes,xAxisIndex:2,yAxisIndex:2,itemStyle:{{color:function(p){{return p.value>=0?'#d32f2f':'#34a853'}}}}}}
    ]
  }});
  klineChart.off('updateAxisPointer');
  klineChart.off('showTip');
  klineChart.off('hideTip');
  klineChart.on('updateAxisPointer',function(ev){{
    if(coarse&&!klineScrubActive)return;
    var idx=indexFromAxisEvent(ev);
    if(idx<0)return;
    if(coarse)setKlineTipOverlayText(tipCompactFromIndex(idx));
    else setKlineTipBarHtml(tipHtmlFromIndex(idx));
  }});
  klineChart.on('showTip',function(ev){{
    if(coarse&&!klineScrubActive)return;
    var idx=indexFromAxisEvent(ev);
    if(idx<0)return;
    if(coarse)setKlineTipOverlayText(tipCompactFromIndex(idx));
    else setKlineTipBarHtml(tipHtmlFromIndex(idx));
  }});
  klineChart.on('hideTip',function(){{if(!klineScrubActive)clearKlineTipBar();}});
  klineChart.resize();
}}
async function showKline(code){{
  var meta=KLINE_META[code]; if(!meta) return;
  currentKlineCode=code;
  currentKlineData=null;
  var requestId=++klineRequestId;
  updateKlineNavChrome(code);
  document.getElementById("overlay").classList.add("active");
  document.getElementById("klinePanel").classList.add("active");
  var sector=meta.sector||"";
  document.getElementById("klineTitle").textContent=code+" "+(meta.name||"")+(sector?" · "+sector:"");
  document.getElementById("klineJournalLink").href=StockAppShell.webUrl("/stock_journal.html")+"?code="+encodeURIComponent(code);
  var sym=document.getElementById("klineSymbolLink"); if(sym) sym.href=StockAppShell.webUrl("/symbol.html")+"?code="+encodeURIComponent(code);
  document.getElementById("klineWatchLink").href=StockAppShell.webUrl("/watchlist.html");
  document.getElementById("klineWatchNotice").textContent="";
  document.getElementById("klineMetrics").innerHTML=metricHtml(meta.metrics);
  var state=document.getElementById('klineState');state.hidden=false;state.textContent='正在读取本地日 K…';
  document.getElementById('klineChart').setAttribute('aria-busy','true');
  try{{
    var loaded=await Promise.all([loadKline(code),loadEcharts()]);
    if(requestId!==klineRequestId||currentKlineCode!==code)return;
    currentKlineData=loaded[0];renderKline(loaded[0]);state.hidden=true;
  }}catch(e){{if(requestId===klineRequestId){{state.hidden=false;state.textContent='K 线加载失败：'+e.message;}}}}
  finally{{if(requestId===klineRequestId)document.getElementById('klineChart').removeAttribute('aria-busy')}}
}}
async function addToWatchlist(){{
  if(!currentKlineCode)return;
  var d=KLINE_META[currentKlineCode]||{{}}, notice=document.getElementById("klineWatchNotice");
  notice.textContent="提交中…";
  try{{
    var screenDate=currentKlineData?.latest?.date||d.metrics?.['指标日期']||"";
    var r=await fetch(StockAppShell.webUrl("/api/watchlist/add"),{{
      method:"POST",headers:{{"Content-Type":"application/json"}},
      body:JSON.stringify({{code:currentKlineCode,name:d.name||"",status:"watching",
        source_module:currentTabId||"dashboard",screen_date:screenDate,
        thesis:"来自选股仪表盘"}})
    }});
    var body=await r.json();
    if(!r.ok)throw new Error(body.error||("HTTP "+r.status));
    notice.textContent="已加入观察池（"+ (body.status_label||body.status) +"）";
  }}catch(e){{notice.textContent="加入失败："+e.message+"（需先 python -m scripts.serve start web）"}}
}}
document.getElementById("klineWatchBtn").onclick=addToWatchlist;
function closeKline(){{
  currentKlineCode=null;
  exitKlineScrub();
  clearKlineTipBar();
  document.getElementById("overlay").classList.remove("active");
  document.getElementById("klinePanel").classList.remove("active");
}}
</script>
</body></html>"""


# ====================================================================
# 回测统计 HTML（Chart.js 图表）
# ====================================================================

def build_backtest_html(results: List[dict], title: str = "策略回测报告") -> str:
    """
    生成回测统计 HTML 报告

    results 格式: [{
        "id": "overlap",
        "title": "重叠>3%低买高卖",
        "results": [{"label": "5", "samples": 1000, "success_rate": 65.2,
                      "mean_gain": 2.1, "median_gain": 1.5,
                      "thresholds": {"1.0": 80.5, "2.0": 65.2, ...}}]
    }]
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    total_samples = sum(sum(rr["samples"] for rr in r["results"]) for r in results)

    # 散点数据
    scatter_data = []
    for r in results:
        best = max(r["results"], key=lambda x: x["samples"])
        scatter_data.append({"label": r["title"][:12], "samples": best["samples"],
                             "rate": best["success_rate"], "gain": best["mean_gain"]})
    scatter_json = json.dumps(scatter_data, ensure_ascii=False)

    tabs = ""; panels = ""
    for i, r in enumerate(results):
        active = "active" if i == 0 else ""
        tabs += f'<button class="tab-btn {active}" onclick="switchTab(\'{r["id"]}\')">{r["title"]}</button>'

        row_data = r["results"]
        has_thr = any(row.get("thresholds") for row in row_data)
        thr_keys = list(row_data[0]["thresholds"].keys()) if has_thr and row_data else []

        thr_headers = "".join(f"<th>>{k}%</th>" for k in thr_keys)
        rows_html = ""
        for row in row_data:
            thr_cells = "".join(f'<td>{row["thresholds"].get(k,"-")}%</td>' for k in thr_keys)
            rows_html += (f'<tr><td>{row["label"]}</td><td>{row["samples"]:,}</td>'
                          f'<td>{row["success_rate"]}%</td>{thr_cells}'
                          f'<td>{row["mean_gain"]:+.1f}%</td><td>{row["median_gain"]:+.1f}%</td></tr>')

        labels = json.dumps([row["label"] for row in row_data])
        rates = json.dumps([row["success_rate"] for row in row_data])
        means = json.dumps([row["mean_gain"] for row in row_data])
        medians = json.dumps([row["median_gain"] for row in row_data])
        samples = json.dumps([row["samples"] for row in row_data])

        heatmap_js = ""
        if has_thr and len(row_data) >= 3:
            hm_labels = json.dumps([row["label"] for row in row_data])
            hm_keys = json.dumps(thr_keys)
            hm_data = json.dumps([[row["thresholds"].get(k, 0) for k in thr_keys] for row in row_data])
            heatmap_js = f'''
            <div class="chart-box hm"><canvas id="heatmap-{r["id"]}"></canvas></div>
            <script>
            (function(){{
              var hmData={hm_data},hmLabels={hm_labels},hmKeys={hm_keys};
              var datasets=hmKeys.map(function(k,ki){{return{{label:">"+k+"%",data:hmData.map(function(r){{return r[ki]}}),
                backgroundColor:"hsl("+(210-ki*18)+",70%,"+(60-ki*8)+"%)",borderWidth:1}}}});
              new Chart(document.getElementById("heatmap-{r['id']}").getContext("2d"),{{
                type:"bar",data:{{labels:hmLabels,datasets:datasets}},
                options:{{responsive:true,indexAxis:"y",scales:{{x:{{max:100,title:{{display:true,text:"概率%"}}}}}},
                  plugins:{{title:{{display:true,text:"目标收益vs成功率"}},legend:{{position:"top"}}}}}}
              }});
            }})();
            </script>'''

        panels += f'''
        <div class="tab-panel {active}" id="panel-{r["id"]}">
          <div class="charts">
            <div class="chart-box"><canvas id="chart-rate-{r["id"]}"></canvas></div>
            <div class="chart-box"><canvas id="chart-combo-{r["id"]}"></canvas></div>
            {heatmap_js}
          </div>
          <table><thead><tr><th>N</th><th>样本</th><th>成功率</th>{thr_headers}<th>均值</th><th>中位</th></tr></thead>
          <tbody>{rows_html}</tbody></table>
          <script>
          (function(){{
            var labels={labels},rates={rates},means={means},medians={medians},samples={samples};
            new Chart(document.getElementById("chart-rate-{r['id']}").getContext("2d"),{{
              type:"bar",data:{{labels:labels,datasets:[{{label:"成功率%",data:rates,
                backgroundColor:rates.map(function(v){{return v>70?"#34a853":v>45?"#1a73e8":"#ea4335"}})}}]}},
              options:{{responsive:true,plugins:{{title:{{display:true,text:"成功率(%)(绿>70/蓝>45/红≤45)"}},legend:{{display:false}}}}}}
            }});
            new Chart(document.getElementById("chart-combo-{r['id']}").getContext("2d"),{{
              type:"bar",data:{{labels:labels,datasets:[
                {{label:"样本量(右轴)",data:samples,backgroundColor:"#e8eaed",order:2,yAxisID:"y1"}},
                {{label:"均值%",data:means,backgroundColor:"#1a73e8",order:1}},
                {{label:"中位%",data:medians,backgroundColor:"#34a853",order:1}}
              ]}},
              options:{{responsive:true,
                plugins:{{title:{{display:true,text:"收益 vs 样本量"}}}},
                scales:{{y:{{title:{{display:true,text:"收益%"}}}},y1:{{position:"right",grid:{{display:false}},title:{{display:true,text:"样本量"}}}}}}
              }}
            }});
          }})();
          </script>
        </div>'''

    return f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{title} — {now}</title>
<script src="vendor/chart.umd.min.js"></script>
<script>window.Chart || document.write(`<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"><\/script>`);</script>
<style>
:root{{--blue:#1a73e8;--green:#34a853;--red:#ea4335;--bg:#f0f2f5;--card:#fff;--text:#333;--muted:#8892b0}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,"PingFang SC","Helvetica Neue",sans-serif;background:var(--bg);color:var(--text)}}
.header{{background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff;padding:18px 24px}}
.header h1{{font-size:20px}}.header .date{{color:var(--muted);font-size:12px;margin-top:2px}}
.summary{{display:flex;gap:16px;padding:12px 24px;background:var(--card);border-bottom:1px solid #e0e0e0}}
.card{{text-align:center;min-width:80px;font-size:11px;color:var(--muted)}}
.card .big{{font-size:22px;font-weight:700;color:var(--blue);display:block}}
.tabs{{display:flex;flex-wrap:wrap;gap:2px;padding:8px 24px;background:var(--card);border-bottom:2px solid #e0e0e0;position:sticky;top:0;z-index:10}}
.tab-btn{{padding:8px 14px;border:none;background:none;cursor:pointer;font-size:12px;color:#666;border-radius:6px 6px 0 0;white-space:nowrap;transition:all .15s}}
.tab-btn:hover{{background:#e8f0fe;color:var(--blue)}}
.tab-btn.active{{background:var(--blue);color:#fff}}
.content{{padding:20px 24px;max-width:1400px}}
.tab-panel{{display:none}}
.tab-panel.active{{display:block}}
.charts{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px}}
.chart-box{{background:var(--card);border-radius:8px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,.06);height:320px;position:relative}}
.chart-box canvas{{max-height:280px}}
.chart-box.hm{{grid-column:1/-1;height:260px}}
.chart-box.hm canvas{{max-height:220px}}
table{{width:100%;font-size:12px;border-collapse:collapse;background:var(--card);border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06);table-layout:fixed}}
th,td{{padding:7px 6px;text-align:center;border-bottom:1px solid #eee;white-space:nowrap;overflow:hidden}}
th{{background:#f8f9fa;font-weight:600;color:#555}}
tr:hover{{background:#f0f7ff}}
@media(max-width:768px){{.charts{{grid-template-columns:1fr}}.chart-box.hm{{grid-column:1}}}}
</style></head><body>
<div class="header"><h1>{title}</h1><div class="date">{now} · 全A股历史回测</div></div>
<div class="summary">
  <div class="card"><span class="big">{len(results)}</span>策略</div>
  <div class="card"><span class="big">{total_samples:,}</span>总样本</div>
</div>
<div class="tabs"><button class="tab-btn active" onclick="switchTab('overview')">📊 策略对比</button>{tabs}</div>
<div class="content">
  <div class="tab-panel active" id="panel-overview">
    <div class="charts">
      <div class="chart-box"><canvas id="scatter"></canvas></div>
      <div class="chart-box"><canvas id="rank"></canvas></div>
    </div>
  </div>
  {panels}
</div>
<script>
function switchTab(id){{
  document.querySelectorAll(".tab-btn").forEach(b=>b.classList.remove("active"));
  document.querySelectorAll(".tab-panel").forEach(p=>p.classList.remove("active"));
  event.target.classList.add("active");document.getElementById("panel-"+id).classList.add("active");
}}
(function(){{
  var scData={scatter_json};
  new Chart(document.getElementById("scatter").getContext("2d"),{{
    type:"bubble",data:{{datasets:[{{label:"策略对比",data:scData.map(function(d){{return{{x:d.samples,y:d.rate,r:d.gain*3}}}}),
      backgroundColor:scData.map(function(_,i){{return"hsl("+(i*30)+",70%,60%,0.7)"}})}}]}},
    options:{{responsive:true,
      plugins:{{title:{{display:true,text:"策略对比(气泡=收益, X=样本量, Y=成功率)"}},tooltip:{{callbacks:{{label:function(c){{var d=scData[c.dataIndex];return d.label+": 样本"+d.samples.toLocaleString()+" 成功率"+d.rate+"% 收益"+d.gain+"%";}}}}}}}},
      scales:{{x:{{title:{{display:true,text:"样本量"}},type:"logarithmic"}},y:{{title:{{display:true,text:"成功率%"}}}}}}
    }}
  }});
  var sorted=scData.slice().sort(function(a,b){{return b.rate-a.rate}});
  new Chart(document.getElementById("rank").getContext("2d"),{{
    type:"bar",data:{{labels:sorted.map(function(d){{return d.label}}),datasets:[{{label:"成功率%",data:sorted.map(function(d){{return d.rate}}),
      backgroundColor:sorted.map(function(d){{return d.rate>50?"#34a853":"#ea4335"}})}}]}},
    options:{{indexAxis:"y",responsive:true,plugins:{{title:{{display:true,text:"策略成功率排名"}},legend:{{display:false}}}}}}
  }});
}})();
</script>
</body></html>'''
