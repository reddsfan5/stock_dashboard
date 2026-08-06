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

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ====================================================================
# 选股仪表盘 HTML（DataTables + ECharts K线）
# ====================================================================

def build_screening_html(
    pipeline_modules: List,
    results: Dict[str, pd.DataFrame],
    data=None,
    title: str = "A股选股仪表盘",
) -> str:
    """生成选股仪表盘 HTML（桌面版）"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

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
                    changes = [round(v, 2) if not pd.isna(v) else 0 for v in grp["涨跌%"].values]
                    prevs = [round(v, 2) if not pd.isna(v) else 0 for v in grp["前收"].values]
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
        header = "".join(f"<th>{c}</th>" for c in cols)
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

    # 自动刷新导航页
    try:
        from scripts.gen_index import generate; generate()
    except Exception:
        pass

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — {now}</title>
<link rel="stylesheet" href="https://cdn.datatables.net/1.13.6/css/jquery.dataTables.min.css">
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
.kline-panel .chart {{ width:100%; height:600px; }}
.kline-overlay {{ display:none; position:fixed; inset:0; background:rgba(0,0,0,.2); z-index:999; }}
.kline-overlay.active {{ display:block; }}
.code-clickable {{ cursor:pointer; }}
.code-clickable:hover {{ background:#e8f0fe !important; }}
footer {{ text-align:center; color:#999; font-size:11px; padding:20px; }}
@media(max-width:768px){{ .kline-panel{{width:100vw}} }}
</style>
</head>
<body>
<div class="kline-overlay" id="overlay" onclick="closeKline()"></div>
<div class="kline-panel" id="klinePanel">
  <button class="close" onclick="closeKline()">✕ <span id="klineTitle"></span><span style="float:right;opacity:.6;font-size:11px" id="klineNav"></span></button>
  <div class="chart" id="klineChart"></div>
</div>
<div class="header">
  <h1>{title}</h1>
  <div class="date">{now} · 申万一级行业分类 · 点击代码查看K线</div>
</div>
<div class="stats">{stats}</div>
<div class="tabs">{tab_buttons}</div>
<div class="content">{tables_html}</div>
<footer>数据每日 18:30 自动更新 · 点击股票代码查看K线图</footer>

<script src="https://code.jquery.com/jquery-3.7.0.min.js"></script>
<script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>
<script>
function switchTab(id){{
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
  document.querySelector(`[onclick="switchTab('${{id}}')"]`).classList.add('active');
  document.getElementById('tab-'+id).classList.add('active');
  try{{ $('#'+id).DataTable().columns.adjust().draw(); }}catch(e){{}}
}}
$(document).ready(function(){{
  var ids={tab_ids};
  ids.forEach(function(id){{
    $('#tbl-'+id).DataTable({{
      pageLength:25,
      language:{{ url:'https://cdn.datatables.net/plug-ins/1.13.6/i18n/zh.json' }},
      order:[], layout:{{ topStart:'search', topEnd:'pageLength' }},
    }});
  }});
}});
</script>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<script>
var KLINES={kline_json};
var TAB_CODES={tab_codes_json};
var klineChart=null;
var currentKlineCode=null;

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
  setTimeout(function(){{
    if(klineChart){{klineChart.dispose();klineChart=null;}}
    klineChart=echarts.init(document.getElementById("klineChart"));
    var dates=d.dates,ohlc=d.data,changes=d.changes||[],prevs=d.prevs||[],vols=d.volumes||[],ma5=[],ma10=[];
    for(var i=0;i<ohlc.length;i++){{
      ma5.push(i>=4?(ohlc.slice(i-4,i+1).reduce(function(s,x){{return s+x[1]}},0)/5).toFixed(2):"-");
      ma10.push(i>=9?(ohlc.slice(i-9,i+1).reduce(function(s,x){{return s+x[1]}},0)/10).toFixed(2):"-");
    }}
    klineChart.setOption({{
      tooltip:{{trigger:"axis",axisPointer:{{type:"cross"}},confine:true,
        formatter:function(ps){{
          var r=ps[0].axisValue,chg=null,hh=0,ll=0,idx=-1,vol=0;
          for(var i=0;i<ps.length;i++){{
            var p=ps[i];
            if(p.seriesName=="K线"&&p.dataIndex!=null){{
              var raw=ohlc[p.dataIndex];
              var o=raw[0],c=raw[1],l=raw[2],h=raw[3];
              hh=h; ll=l; idx=p.dataIndex;
              r+="<br/>开: "+o+"  收: "+c+"  高: "+h+"  低: "+l;
            }}
            if(p.seriesName=="成交量") vol=p.value;
            if(p.seriesName=="涨跌%") chg=p.value;
          }}
          if(vol>0) r+="<br/>成交额: "+(vol/1e8).toFixed(2)+"亿";
          var prev=prevs[idx]||0,ampUp=0,ampDown=0;
          if(prev>0){{ampUp=((hh-prev)/prev*100);ampDown=((ll-prev)/prev*100);}}
          if(chg!=null) r+="<br/>涨跌幅: "+(chg>0?"+":"")+chg.toFixed(2)+"%  振幅: "+((ampUp-ampDown)).toFixed(2)+"% (↑"+ampUp.toFixed(1)+"% ↓"+ampDown.toFixed(1)+"%)";
          return r;
        }}
      }},
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
        {{name:"成交量",type:"bar",data:vols,xAxisIndex:1,yAxisIndex:1,
          itemStyle:{{color:function(p){{var i=p.dataIndex,o=ohlc[i][0],c=ohlc[i][1];return c>=o?"#d32f2f":"#34a853";}}}}}},
        {{name:"涨跌%",type:"bar",data:changes,xAxisIndex:2,yAxisIndex:2,
          itemStyle:{{color:function(p){{return p.value>=0?"#d32f2f":"#34a853";}}}}}}
      ]
    }});
    klineChart.resize();
  }},100);
}}
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
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
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
