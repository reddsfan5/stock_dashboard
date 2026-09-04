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
function klineTooltipHtml(ps,ohlc,dates,prevs,volumes,changes,endLabel){
  var idx=-1;
  for(var i=0;i<ps.length;i++){
    if(ps[i].seriesName==="K线"&&ps[i].dataIndex!=null){idx=ps[i].dataIndex;break;}
  }
  if(idx<0&&ps.length&&ps[0].dataIndex!=null)idx=ps[0].dataIndex;
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
    """生成手机版选股页面 — 卡片式布局，每模块显示前15只"""
    now = datetime.now().strftime("%m/%d %H:%M")
    decision_map = snapshot_lookup(decision_snapshot)

    all_codes = set()
    for df in results.values():
        if len(df) > 0 and "代码" in df.columns:
            all_codes.update(df["代码"].head(100).tolist())
    all_codes = list(all_codes)[:500]

    kline_json = "{}"
    if data is not None and all_codes:
        from tqdm import tqdm
        import json as _json
        cache = data.cache[data.cache["代码"].isin(all_codes)].copy()
        cache = cache.sort_values(["代码", "日期"])
        cache = cache.groupby("代码").tail(60)
        if "开盘" not in cache.columns:
            cache["开盘"] = cache["收盘"].shift(1).fillna(cache["收盘"])
        try:
            from data.industry import StockInfo
            info = StockInfo()
            sector_map = dict(zip(info.df["代码"], info.df["申万1级"]))
        except:
            sector_map = {}
        kline_map = {}
        for code, grp in tqdm(cache.groupby("代码"), desc="K线", total=len(all_codes), unit="只"):
            try:
                grp = grp.sort_values("日期")
                if len(grp) > 0:
                    grp["涨跌%"] = grp["收盘"].pct_change() * 100
                    grp["前收"] = grp["收盘"].shift(1)
                    changes = [round(v, 2) if not pd.isna(v) else None for v in grp["涨跌%"].values]
                    prevs = [round(v, 2) if not pd.isna(v) else None for v in grp["前收"].values]
                    volumes = [float(v) if not pd.isna(v) else 0 for v in grp["成交额"].values]
                    ohlc = [[float(r["开盘"]), float(r["收盘"]), float(r["最低"]), float(r["最高"])]
                            for _, r in grp.iterrows()]
                    kline_map[code] = {
                        "dates": grp["日期"].dt.strftime("%Y-%m-%d").tolist(),
                        "data": ohlc, "prevs": prevs, "changes": changes,
                        "volumes": volumes, "name": data.get_stock_name(code),
                        "sector": sector_map.get(code, ""),
                        "metrics": decision_map.get(str(code), {}),
                    }
            except Exception:
                pass
        kline_json = _json.dumps(kline_map, ensure_ascii=False)

    tabs_html = ""
    panels_html = ""
    for i, mod in enumerate(pipeline_modules):
        df = results.get(mod.id)
        if df is None or len(df) == 0:
            continue
        active = "active" if i == 0 else ""
        tabs_html += f'<button class="tab {active}" onclick="switchTab(\'{mod.id}\')">{mod.title}<span class="tc">{len(df)}</span></button>'

        rows = ""
        for _, row in df.head(200).iterrows():
            code = row.get("代码", "")
            name = row.get("名称", "")
            sector = row.get("申万1级", "")
            score = ""
            for c in ["综合评分", "平均溢价%", "累计涨幅%", "平均下影比", "长下影天数", "连续天数"]:
                if c in row.index and not pd.isna(row[c]):
                    score = f"{c}={row[c]}"
                    break
            if not score:
                for c in ["最新价", "收盘"]:
                    if c in row.index and not pd.isna(row[c]):
                        score = f"¥{row[c]}"
                        break
            facts = []
            for column, label, suffix in (
                ("成交量比20", "量", "×"),
                ("换手率%", "换", "%"),
                ("ATR14%", "ATR", "%"),
            ):
                value = row.get(column)
                if pd.notna(value):
                    facts.append(f"{label} {float(value):.2f}{suffix}")
            decision = " · ".join(facts)
            name_str = f"{name}" if name else ""
            sector_str = f'<span class="sector">{sector}</span>' if sector else ""
            rows += f"""<div class="stock-row" onclick="showKline('{code}')">
  <span class="code">{code}</span>
  <span class="name">{name_str} {sector_str}</span>
  <span class="score">{score}<small>{decision}</small></span>
</div>"""

        more_btn = ""
        if len(df) > 30:
            more_btn = f'<div class="more-btn" onclick="this.previousElementSibling.classList.toggle(\'expanded\');this.textContent=this.textContent==\'显示全部({len(df)}只)\'?\'收起\':\'显示全部({len(df)}只)\'">显示全部({len(df)}只)</div>'
        panels_html += f"""
<div class="tab-panel {active}" id="panel-{mod.id}">
  <div class="panel-body limited">{rows}</div>
  {more_btn}
</div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no">
<title>{title}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,"PingFang SC",sans-serif;background:#f0f2f5;color:#333;font-size:14px;-webkit-tap-highlight-color:transparent}}
.header{{background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff;padding:14px 16px;position:sticky;top:0;z-index:10;display:flex;justify-content:space-between;align-items:center}}
.header h1{{font-size:17px}}.header .t{{font-size:11px;color:#8892b0}}
.tabs{{display:flex;gap:4px;padding:8px 12px;overflow-x:auto;background:#fff;position:sticky;top:44px;z-index:9;-webkit-overflow-scrolling:touch;border-bottom:1px solid #e0e0e0}}
.tab{{flex-shrink:0;padding:8px 12px;border:none;background:#f0f2f5;border-radius:16px;font-size:12px;color:#666;cursor:pointer;display:flex;align-items:center;gap:4px;white-space:nowrap}}
.tab.active{{background:#1a73e8;color:#fff}}
.tab .tc{{background:rgba(255,255,255,.3);font-size:10px;padding:1px 6px;border-radius:8px}}
.tab-panel{{display:none}}
.tab-panel.active{{display:block}}
.panel-body{{padding:4px 0}}
.stock-row{{display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid #f0f0f0;cursor:pointer}}
.stock-row:active{{background:#e8f0fe}}
.stock-row:last-child{{border-bottom:none}}
.code{{font-family:"SF Mono",monospace;font-size:13px;color:#1a73e8;min-width:80px;font-weight:600}}
.name{{flex:1;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.sector{{font-size:10px;color:#999;margin-left:4px}}
.score{{font-size:12px;color:#374151;white-space:nowrap;text-align:right}}.score small{{display:block;color:#8a94a6;font-size:9px;margin-top:2px}}
.limited{{max-height:65vh;overflow-y:auto}}
.limited .stock-row:nth-child(n+31){{display:none}}
.limited.expanded .stock-row:nth-child(n+31){{display:block}}
.more-btn{{text-align:center;padding:10px;color:#1a73e8;font-size:13px;cursor:pointer;border-top:1px solid #f0f0f0}}
.more-btn:active{{background:#e8f0fe}}
.kline-overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.3);z-index:99}}
.kline-overlay.active{{display:block}}
.kline-panel{{display:none;position:fixed;left:0;top:0;width:100vw;height:100vh;background:#fff;z-index:100;overflow-y:auto}}
.kline-panel.active{{display:block}}
.kline-panel .close{{position:sticky;top:0;background:#1a73e8;color:#fff;border:none;width:100%;padding:12px;font-size:15px;z-index:1}}
.kline-metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:#e3e8f0;border-bottom:1px solid #e3e8f0}}.kline-metric{{background:#fff;padding:8px 9px}}.kline-metric b{{display:block;font-size:13px}}.kline-metric span{{display:block;color:#8992a3;font-size:9px;margin-bottom:2px}}
.kline-panel .chart{{width:100%;height:55vh}}
.journal-action{{display:block;margin:10px 12px;padding:9px;text-align:center;text-decoration:none;background:#edf4ff;color:#1a73e8;border:1px solid #bfd2f7;border-radius:8px;font-weight:650}}
.footer{{text-align:center;padding:20px;font-size:11px;color:#bbb}}
</style>
</head>
<body>
<div class="header"><h1>📊 {title}</h1><span class="t">{now}</span></div>
<div class="tabs">{tabs_html}</div>
<div class="kline-overlay" id="overlay" onclick="closeKline()"></div>
<div class="kline-panel" id="klinePanel">
  <button class="close" onclick="closeKline()">← 返回 <span id="klineTitle"></span></button>
  <div class="kline-metrics" id="klineMetrics"></div>
  <a class="journal-action" id="klineSymbolLink" href="http://127.0.0.1:8765/symbol.html">📎 标的上下文</a>
  <a class="journal-action" id="klineJournalLink" href="http://127.0.0.1:8765/stock_journal.html">📓 在选股日记中打开</a>
  <div class="chart" id="klineChart"></div>
  <div style="text-align:center;padding:16px"><button onclick="closeKline()" style="background:#1a73e8;color:#fff;border:none;padding:10px 40px;border-radius:8px;font-size:15px">关闭K线</button></div>
</div>
{panels_html}
<div class="footer">点击股票代码查看K线 · 电脑端打开 dashboard.html 查看完整版</div>
<script src="vendor/echarts.min.js"></script>
<script>window.echarts || document.write(`<script src='https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js'><\/script>`);</script>
<script>
function switchTab(id){{
  document.querySelectorAll(".tab").forEach(b=>b.classList.remove("active"));
  document.querySelectorAll(".tab-panel").forEach(p=>p.classList.remove("active"));
  document.querySelector("[onclick*='"+id+"']").classList.add("active");
  document.getElementById("panel-"+id).classList.add("active");
}}
var KLINES={kline_json};var klineChart=null;
{KLINE_TOOLTIP_JS}
function metricHtml(m){{if(!m)return'';var defs=[['成交量比20','成交量比','×'],['成交额比20','成交额比','×'],['换手率%','换手率','%'],['20日动量%','20日动量','%'],['ATR14%','ATR14','%'],['距60日高点%','距60日高点','%']];return defs.map(function(x){{var v=m[x[0]],text=v===null||v===undefined?'—':(+v).toFixed(2)+x[2];return'<div class="kline-metric"><span>'+x[1]+'</span><b>'+text+'</b></div>'}}).join('')}}
function showKline(code){{
  var d=KLINES[code];if(!d)return;
  document.getElementById("overlay").classList.add("active");
  document.getElementById("klinePanel").classList.add("active");
  document.getElementById("klineTitle").textContent=code+" "+(d.name||"");
  document.getElementById("klineJournalLink").href="http://127.0.0.1:8765/stock_journal.html?code="+encodeURIComponent(code);
  var sym=document.getElementById("klineSymbolLink"); if(sym) sym.href="http://127.0.0.1:8765/symbol.html?code="+encodeURIComponent(code);
  document.getElementById("klineWatchLink").href="http://127.0.0.1:8765/watchlist.html";
  document.getElementById("klineWatchNotice").textContent="";
  document.getElementById("klineMetrics").innerHTML=metricHtml(d.metrics);
  setTimeout(function(){{
    if(klineChart){{klineChart.dispose();klineChart=null;}}
    klineChart=echarts.init(document.getElementById("klineChart"));
    var dates=d.dates,ohlc=d.data,changes=d.changes||[],prevs=d.prevs||[],vols=d.volumes||[];
    klineChart.setOption({{
      tooltip:{{trigger:"axis",axisPointer:{{type:"cross"}},confine:true,formatter:function(ps){{return klineTooltipHtml(ps,ohlc,dates,prevs,vols,changes,"最新收")}}}},
      grid:[{{left:"8%",right:"2%",top:"5%",height:"50%"}},{{left:"8%",right:"2%",top:"63%",height:"12%"}},{{left:"8%",right:"2%",top:"82%",height:"10%"}}],
      xAxis:[{{data:dates,axisLabel:{{rotate:30,fontSize:9}},gridIndex:0}},{{data:dates,axisLabel:{{show:false}},gridIndex:1}},{{data:dates,axisLabel:{{show:false}},gridIndex:2}}],
      yAxis:[{{scale:true,gridIndex:0}},{{gridIndex:1,splitNumber:2,axisLabel:{{formatter:function(v){{return (v/1e8).toFixed(1)+"亿"}}}}}},{{gridIndex:2,splitNumber:2,axisLabel:{{formatter:"{{value}}%"}}}}],
      series:[
        {{name:"K线",type:"candlestick",data:ohlc,xAxisIndex:0,yAxisIndex:0,itemStyle:{{color:"#d32f2f",color0:"#34a853",borderColor:"#d32f2f",borderColor0:"#34a853"}},barWidth:"60%"}},
        {{name:"成交额",type:"bar",data:vols,xAxisIndex:1,yAxisIndex:1,itemStyle:{{color:function(p){{var i=p.dataIndex;return ohlc[i][1]>=ohlc[i][0]?"#d32f2f":"#34a853"}}}}}},
        {{name:"涨跌%",type:"bar",data:changes,xAxisIndex:2,yAxisIndex:2,itemStyle:{{color:function(p){{return p.value>=0?"#d32f2f":"#34a853"}}}}}}
      ]
    }});klineChart.resize();
  }},100);
}}
function closeKline(){{document.getElementById("overlay").classList.remove("active");document.getElementById("klinePanel").classList.remove("active")}}
</script>
</body></html>"""



# ====================================================================
# 选股仪表盘 HTML（DataTables + ECharts K线）
# ====================================================================

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

    # ---- 收集K线数据（最多1000只） ----
    all_codes = set()
    for df in results.values():
        if len(df) > 0 and "代码" in df.columns:
            all_codes.update(df["代码"].head(500).tolist())
    all_codes = list(all_codes)[:1000]

    kline_json = "{}"
    if data is not None and all_codes:
        from tqdm import tqdm
        # 一次过滤出所有需要的K线，避免逐只全表扫描
        full_cache = data.cache  # 只取一次，避免 CombinedData 重复 concat
        cache = full_cache[full_cache["代码"].isin(all_codes)].copy()
        cache = cache.sort_values(["代码", "日期"])
        # 每只股票只保留最近 60 天
        cache = cache.groupby("代码").tail(60)
        if "开盘" not in cache.columns:
            cache["开盘"] = cache["收盘"].shift(1).fillna(cache["收盘"])

        # 行业信息
        try:
            from data.industry import StockInfo
            info = StockInfo()
            sector_map = dict(zip(info.df["代码"], info.df["申万1级"]))
        except Exception:
            sector_map = {}

        kline_map = {}
        for code, grp in tqdm(cache.groupby("代码"), desc="K线数据",
                              total=len(all_codes), unit="只"):
            try:
                grp = grp.sort_values("日期")
                if len(grp) > 0:
                    grp["涨跌%"] = grp["收盘"].pct_change() * 100
                    grp["前收"] = grp["收盘"].shift(1)
                    changes = [round(v, 2) if not pd.isna(v) else None for v in grp["涨跌%"].values]
                    prevs = [round(v, 2) if not pd.isna(v) else None for v in grp["前收"].values]
                    volumes = [float(v) if not pd.isna(v) else 0 for v in grp["成交额"].values]
                    ohlc = []
                    for _, r in grp.iterrows():
                        ohlc.append([
                            float(r["开盘"]), float(r["收盘"]),
                            float(r["最低"]), float(r["最高"]),
                        ])
                    kline_map[code] = {
                        "dates": grp["日期"].dt.strftime("%Y-%m-%d").tolist(),
                        "data": ohlc,
                        "prevs": prevs,
                        "changes": changes,
                        "volumes": volumes,
                        "name": data.get_stock_name(code),
                        "sector": sector_map.get(code, ""),
                        "metrics": decision_map.get(str(code), {}),
                    }
            except Exception:
                pass
        kline_json = json.dumps(kline_map, ensure_ascii=False)

    tab_buttons = ""
    tables_html = ""

    for i, mod in enumerate(pipeline_modules):
        df = results.get(mod.id)
        if df is None or len(df) == 0:
            continue

        active = "active" if i == 0 else ""
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
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — {now}</title>
<link rel="stylesheet" href="vendor/jquery.dataTables.min.css">
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
.kline-panel .close {{ position:sticky; top:0; background:#1a73e8; color:white; border:none; width:100%; padding:12px; font-size:14px; cursor:pointer; z-index:1; }}
.kline-metrics {{ display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:#e0e5ed }}.kline-metric {{ background:#fff;padding:8px 10px }}.kline-metric span {{ display:block;color:#818b9d;font-size:9px;margin-bottom:3px }}.kline-metric b {{ font-size:13px;font-variant-numeric:tabular-nums }}
.kline-panel .chart {{ width:100%; height:600px; }}
.journal-action {{ display:block;margin:10px 12px;padding:9px;text-align:center;text-decoration:none;background:#edf4ff;color:#1a73e8;border:1px solid #bfd2f7;border-radius:8px;font-weight:650;width:calc(100% - 24px);cursor:pointer;font:inherit }}
.kline-overlay {{ display:none; position:fixed; inset:0; background:rgba(0,0,0,.2); z-index:999; }}
.kline-overlay.active {{ display:block; }}
.code-clickable {{ cursor:pointer; }}
.code-clickable:hover {{ background:#e8f0fe !important; }}
footer {{ text-align:center; color:#999; font-size:11px; padding:20px; }}
@media(max-width:768px){{ .kline-panel{{width:100vw}}.decision-tools{{padding:8px 12px}}.metric-guide{{margin-left:0}}.metric-guide ul{{position:fixed;left:10px;right:10px;top:150px;width:auto}} }}
</style>
</head>
<body>
<div class="kline-overlay" id="overlay" onclick="closeKline()"></div>
<div class="kline-panel" id="klinePanel">
  <button class="close" onclick="closeKline()">✕ <span id="klineTitle"></span><span style="float:right;opacity:.6;font-size:11px" id="klineNav"></span></button>
  <div class="kline-metrics" id="klineMetrics"></div>
  <a class="journal-action" id="klineSymbolLink" href="http://127.0.0.1:8765/symbol.html">📎 标的上下文</a>
  <a class="journal-action" id="klineJournalLink" href="http://127.0.0.1:8765/stock_journal.html">📓 在选股日记中打开</a>
  <button class="journal-action" id="klineWatchBtn" type="button">👀 加入观察池</button>
  <a class="journal-action" id="klineWatchLink" href="http://127.0.0.1:8765/watchlist.html">打开观察池</a>
  <div class="notice" id="klineWatchNotice" style="margin:0 12px 8px;color:#7b8495;font-size:11px"></div>
  <div class="chart" id="klineChart"></div>
</div>
<div class="header">
  <h1>{title}</h1>
  <div class="date">{now} · 申万一级行业分类 · 点击代码查看K线 · <a href="http://127.0.0.1:8765/watchlist.html" style="color:#9ec1ff">观察池</a></div>
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
<footer>数据每日 18:30 自动更新 · 点击股票代码查看K线图</footer>

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
  try{{ $('#tbl-'+id).DataTable().columns.adjust().draw(); }}catch(e){{}}
}}
var currentTabId=({tab_ids})[0]||null;
function filterValue(id){{var raw=document.getElementById(id).value;return raw===''?null:+raw}}
function metricValue(settings,row,label){{var idx=-1;for(var i=0;i<settings.aoColumns.length;i++){{if(settings.aoColumns[i].nTh.textContent.trim()===label){{idx=i;break}}}}if(idx<0)return null;var raw=String(row[idx]??'').replace(/,/g,'').trim();if(raw==='')return null;var value=+raw;return Number.isFinite(value)?value:null}}
$.fn.dataTable.ext.search.push(function(settings,row){{if(!currentTabId||settings.nTable.id!=='tbl-'+currentTabId)return true;var rules=[['fVolume','成交量比20','min'],['fTurnover','换手率%','min'],['fMomentum','20日动量%','min'],['fAtr','ATR14%','max'],['fPe','动态PE','max']];for(var i=0;i<rules.length;i++){{var wanted=filterValue(rules[i][0]);if(wanted===null)continue;var actual=metricValue(settings,row,rules[i][1]);if(actual===null)return false;if(rules[i][2]==='min'&&actual<wanted)return false;if(rules[i][2]==='max'&&actual>wanted)return false}}return true}});
function applyDecisionFilters(){{if(currentTabId)$('#tbl-'+currentTabId).DataTable().draw()}}
document.getElementById('applyFilters').onclick=applyDecisionFilters;document.getElementById('resetFilters').onclick=function(){{['fVolume','fTurnover','fMomentum','fAtr','fPe'].forEach(function(id){{document.getElementById(id).value=''}});applyDecisionFilters()}};
$(document).ready(function(){{
  var ids={tab_ids};
  ids.forEach(function(id){{
    $('#tbl-'+id).DataTable({{
      pageLength:25,
      language:{{ url:'vendor/datatables-zh.json' }},
      order:[], layout:{{ topStart:'search', topEnd:'pageLength' }},
    }});
  }});
}});
</script>
<script src="vendor/echarts.min.js"></script>
<script>window.echarts || document.write(`<script src='https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js'><\/script>`);</script>
<script>
var KLINES={kline_json};
var TAB_CODES={tab_codes_json};
var klineChart=null;
var currentKlineCode=null;
{KLINE_TOOLTIP_JS}
function metricHtml(m){{if(!m)return'';var defs=[['成交量比20','成交量比20','×'],['成交额比20','成交额比20','×'],['换手率%','换手率','%'],['20日动量%','20日动量','%'],['市场相对强弱20%','市场相对强弱','%'],['ATR14%','ATR14','%'],['20日年化波动%','20日年化波动','%'],['距60日高点%','距60日高点','%'],['动态PE','动态PE',''],['流通市值(亿)','流通市值','亿'],['供应商量比','供应商量比','×']];return defs.map(function(x){{var v=m[x[0]],text=v===null||v===undefined?'—':(+v).toFixed(2)+x[2];return'<div class="kline-metric"><span>'+x[1]+'</span><b>'+text+'</b></div>'}}).join('')}}

function getCodeList(){{
  var btns=document.querySelectorAll('.tab-btn.active');
  if(btns.length>0){{
    var tabId=btns[0].getAttribute('onclick').match(/'(.*?)'/)[1];
    if(TAB_CODES[tabId]) return TAB_CODES[tabId];
  }}
  return Object.keys(KLINES).sort();
}}

function navKline(dir){{
  if(!currentKlineCode)return;
  var list=getCodeList();
  var idx=list.indexOf(currentKlineCode);
  if(idx<0)return;
  var next=idx+dir;
  if(next<0)next=list.length-1;
  if(next>=list.length)next=0;
  showKline(list[next]);
}}

document.addEventListener('keydown',function(e){{
  if(!currentKlineCode)return;
  if(e.key=='ArrowLeft'){{e.preventDefault();navKline(-1);}}
  if(e.key=='ArrowRight'){{e.preventDefault();navKline(1);}}
  if(e.key=='Escape'){{e.preventDefault();closeKline();}}
}});

function showKline(code){{
  var d=KLINES[code]; if(!d) return;
  currentKlineCode=code;
  var list=getCodeList();
  var idx=list.indexOf(code);
  document.getElementById("klineNav").textContent=(idx+1)+"/"+list.length;
  document.getElementById("overlay").classList.add("active");
  document.getElementById("klinePanel").classList.add("active");
  var sector=d.sector||"";
  document.getElementById("klineTitle").innerHTML=code+" "+(d.name||"")+(sector?"<br><small style='opacity:.6'>"+sector+"</small>":"");
  document.getElementById("klineJournalLink").href="http://127.0.0.1:8765/stock_journal.html?code="+encodeURIComponent(code);
  var sym=document.getElementById("klineSymbolLink"); if(sym) sym.href="http://127.0.0.1:8765/symbol.html?code="+encodeURIComponent(code);
  document.getElementById("klineWatchLink").href="http://127.0.0.1:8765/watchlist.html";
  document.getElementById("klineWatchNotice").textContent="";
  document.getElementById("klineMetrics").innerHTML=metricHtml(d.metrics);
  setTimeout(function(){{
    if(klineChart){{klineChart.dispose();klineChart=null;}}
    klineChart=echarts.init(document.getElementById("klineChart"));
    var dates=d.dates,ohlc=d.data,changes=d.changes||[],prevs=d.prevs||[],vols=d.volumes||[],ma5=[],ma10=[];
    for(var i=0;i<ohlc.length;i++){{
      ma5.push(i>=4?(ohlc.slice(i-4,i+1).reduce(function(s,x){{return s+x[1]}},0)/5).toFixed(2):"-");
      ma10.push(i>=9?(ohlc.slice(i-9,i+1).reduce(function(s,x){{return s+x[1]}},0)/10).toFixed(2):"-");
    }}
    klineChart.setOption({{
      tooltip:{{trigger:"axis",axisPointer:{{type:"cross"}},confine:true,formatter:function(ps){{return klineTooltipHtml(ps,ohlc,dates,prevs,vols,changes,"最新收")}}}},
      axisPointer:{{link:[{{xAxisIndex:"all"}}]}},
      grid:[{{left:"8%",right:"2%",top:"5%",height:"46%"}},{{left:"8%",right:"2%",top:"57%",height:"13%"}},{{left:"8%",right:"2%",top:"76%",height:"12%"}}],
      xAxis:[{{data:dates,axisLabel:{{rotate:30,fontSize:10}},gridIndex:0}},{{data:dates,axisLabel:{{show:false}},gridIndex:1}},{{data:dates,axisLabel:{{show:false}},gridIndex:2}}],
      yAxis:[{{scale:true,gridIndex:0,splitArea:{{show:true}}}},{{gridIndex:1,splitNumber:2,axisLabel:{{formatter:function(v){{return (v/1e8).toFixed(1)+"亿"}}}}}},{{gridIndex:2,splitNumber:3,axisLabel:{{formatter:"{{value}}%"}}}}],
      series:[
        {{name:"K线",type:"candlestick",data:ohlc,xAxisIndex:0,yAxisIndex:0,
          dimensions:["open","close","lowest","highest"],
          itemStyle:{{color:"#d32f2f",color0:"#34a853",borderColor:"#d32f2f",borderColor0:"#34a853"}},barWidth:"60%"}},
        {{name:"MA5",type:"line",data:ma5,xAxisIndex:0,yAxisIndex:0,smooth:true,lineStyle:{{width:1,color:"#ff9800"}},symbol:"none"}},
        {{name:"MA10",type:"line",data:ma10,xAxisIndex:0,yAxisIndex:0,smooth:true,lineStyle:{{width:1,color:"#2196f3"}},symbol:"none"}},
        {{name:"成交额",type:"bar",data:vols,xAxisIndex:1,yAxisIndex:1,
          itemStyle:{{color:function(p){{var i=p.dataIndex,o=ohlc[i][0],c=ohlc[i][1];return c>=o?"#d32f2f":"#34a853";}}}}}},
        {{name:"涨跌%",type:"bar",data:changes,xAxisIndex:2,yAxisIndex:2,
          itemStyle:{{color:function(p){{return p.value>=0?"#d32f2f":"#34a853";}}}}}}
      ]
    }});
    klineChart.resize();
  }},100);
}}
async function addToWatchlist(){{
  if(!currentKlineCode)return;
  var d=KLINES[currentKlineCode]||{{}}, notice=document.getElementById("klineWatchNotice");
  notice.textContent="提交中…";
  try{{
    var screenDate=(d.dates&&d.dates.length)?d.dates[d.dates.length-1]:"";
    var r=await fetch("http://127.0.0.1:8765/api/watchlist/add",{{
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
