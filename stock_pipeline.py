#!/usr/bin/env python3
"""
一键选股管线 + 仪表盘

设计：
  - 每个分析模块注册到 PIPELINE，统一 find_all(data, **kw) → DataFrame 接口
  - 管线并行执行所有模块，跑一次缓存读一份数据
  - 新增模块只需：实现 find_all() + 注册到 PIPELINE

用法：
  python stock_pipeline.py              # 读缓存，跑全部分析，生成仪表盘
  python stock_pipeline.py --refresh    # 先更新缓存再跑
  python stock_pipeline.py --only continuity,sideways  # 只跑指定模块
"""

import argparse
import importlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
from tqdm import tqdm

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_HTML = os.path.join(PROJECT_DIR, "dashboard.html")
OUTPUT_JSON = os.path.join(PROJECT_DIR, "pipeline_results.json")


# ====================================================================
# 模块注册表（新增模块在此加一行）
# ====================================================================

@dataclass
class PipelineModule:
    """管线模块定义"""
    id: str                          # 唯一标识
    title: str                       # 显示名称
    module: str                      # Python 模块名
    kwargs: dict = field(default_factory=dict)  # 传给 find_all() 的参数
    csv: str = ""                    # 输出 CSV 文件名

    def __post_init__(self):
        if not self.csv:
            self.csv = f"{self.id}.csv"


# ---- 自注册机制 ----
# 每个分析模块在模块级别定义 PIPELINE_META，管线自动发现
# 新增模块只需写代码放目录里，无需改此文件
# 示例见 stock_hammer.py / stock_select.py 等模块顶部的 PIPELINE_META


def discover_modules() -> List[PipelineModule]:
    """扫描目录下所有 stock_*.py，自动发现实现了 find_all + PIPELINE_META 的模块"""
    import glob
    modules = []
    for f in sorted(glob.glob(os.path.join(PROJECT_DIR, "stock_*.py"))):
        name = os.path.splitext(os.path.basename(f))[0]
        if name == "stock_pipeline":
            continue
        try:
            m = importlib.import_module(name)
            meta = getattr(m, "PIPELINE_META", None)
            fn = getattr(m, "find_all", None)
            if not meta or not fn:
                continue
            # 支持 variants：一个模块产出多组结果
            variants = meta.get("variants", [None])
            for v in variants:
                if v:
                    kw = {**meta.get("kwargs", {}), **v.get("kwargs", {})}
                    modules.append(PipelineModule(
                        id=v["id"], title=v["title"], module=name, kwargs=kw,
                    ))
                else:
                    modules.append(PipelineModule(
                        id=meta.get("id", name), title=meta.get("title", name),
                        module=name, kwargs=meta.get("kwargs", {}),
                    ))
        except Exception as e:
            print(f"  ⚠ 加载 {name} 失败: {e}")
    return modules


# ====================================================================
# 执行引擎
# ====================================================================

def run_module(mod: PipelineModule, data) -> pd.DataFrame:
    """动态加载模块并调用 find_all()"""
    m = importlib.import_module(mod.module)
    fn = getattr(m, "find_all", None)
    if fn is None:
        print(f"  ⚠ {mod.title}: 模块缺少 find_all() 接口，跳过")
        return pd.DataFrame()
    return fn(data, **mod.kwargs)


def run_all(data, modules: List[PipelineModule],
            only: Optional[List[str]] = None) -> Dict[str, pd.DataFrame]:
    """并行执行所有管线模块"""
    if only:
        modules = [m for m in modules if m.id in only]

    results = {}
    start = time.time()
    print(f"\n{'='*50}")
    print(f"执行 {len(modules)} 个分析模块（并行）...")
    print(f"{'='*50}")

    with ThreadPoolExecutor(max_workers=len(modules)) as executor:
        futures = {
            executor.submit(run_module, m, data): m
            for m in modules
        }
        for future in as_completed(futures):
            m = futures[future]
            try:
                df = future.result()
                # 关联申万行业
                if len(df) > 0:
                    from stock_info import StockInfo
                    info = StockInfo()
                    info_cols = info.df[["代码", "名称", "申万1级", "申万2级", "申万3级"]]
                    # 避免重复列
                    for col in ["名称", "申万1级", "申万2级", "申万3级"]:
                        if col in df.columns:
                            df = df.drop(columns=[col])
                    df = df.merge(info_cols, on="代码", how="left")
                results[m.id] = df
                print(f"  ✓ {m.title}: {len(df)} 只")
            except Exception as e:
                print(f"  ✗ {m.title}: {e}")
                results[m.id] = pd.DataFrame()

    elapsed = time.time() - start
    print(f"\n总耗时: {elapsed:.1f} 秒")
    return results


# ====================================================================
# HTML 生成
# ====================================================================

def build_html(pipeline: List[PipelineModule],
               results: Dict[str, pd.DataFrame], data=None) -> str:
    """生成自包含仪表盘 HTML（data 传入用于提取K线）"""
    # ---- 收集K线数据（最多1000只） ----
    all_codes = set()
    for df in results.values():
        if len(df) > 0 and "代码" in df.columns:
            all_codes.update(df["代码"].head(500).tolist())
    all_codes = list(all_codes)[:1000]

    kline_json = "{}"
    if data is not None and all_codes:
        kline_map = {}
        for code in all_codes:
            try:
                kdf = data.get_kline(code, days=60)
                if len(kdf) > 0:
                    # 旧缓存无开盘列，用前一日收盘近似
                    if "开盘" not in kdf.columns:
                        kdf["开盘"] = kdf["收盘"].shift(1).fillna(kdf["收盘"])
                    kline_map[code] = {
                        "dates": kdf["日期"].dt.strftime("%Y-%m-%d").tolist(),
                        "data": kdf[["开盘", "收盘", "最低", "最高"]].values.tolist(),
                        "name": data.get_stock_name(code),
                    }
            except Exception:
                pass
        kline_json = json.dumps(kline_map, ensure_ascii=False)

    tab_buttons = ""
    tables_html = ""

    for i, mod in enumerate(pipeline):
        df = results.get(mod.id)
        if df is None or len(df) == 0:
            continue

        active = "active" if i == 0 else ""
        tab_buttons += f"""
            <button class="tab-btn {active}" onclick="switchTab('{mod.id}')">{mod.title}
              <span class="count">{len(df)}</span>
            </button>"""

        # Timestamp 列名 → "2026-07-24"
        cols = []
        for c in df.columns:
            if isinstance(c, pd.Timestamp):
                cols.append(c.strftime("%Y-%m-%d"))
            else:
                cols.append(str(c))
        header = "".join(f"<th>{c}</th>" for c in cols)
        orig_cols = list(df.columns)  # 原始列（可能是 Timestamp）
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

    # 汇总栏
    stats = "".join(
        f'<div class="stat"><span class="stat-num">{len(results.get(m.id, pd.DataFrame()))}</span><span class="stat-label">{m.title}</span></div>'
        for m in pipeline if m.id in results
    )

    # 管线的 tab_ids 列表（给 JS）
    tab_ids = json.dumps([m.id for m in pipeline if m.id in results])

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>选股仪表盘 — {datetime.now().strftime('%Y-%m-%d')}</title>
<link rel="stylesheet" href="https://cdn.datatables.net/1.13.6/css/jquery.dataTables.min.css">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f5f5f5; color: #333; }}
.header {{ background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); color: white; padding: 20px 32px; }}
.header h1 {{ font-size: 22px; font-weight: 600; }}
.header .date {{ color: #8892b0; font-size: 13px; margin-top: 2px; }}
.stats {{ display: flex; gap: 16px; padding: 16px 32px; background: white; border-bottom: 1px solid #e0e0e0; flex-wrap: wrap; }}
.stat {{ display: flex; flex-direction: column; align-items: center; min-width: 80px; }}
.stat-num {{ font-size: 24px; font-weight: 700; color: #1a73e8; }}
.stat-label {{ font-size: 11px; color: #999; }}
.tabs {{ display: flex; gap: 0; background: white; padding: 0 32px; border-bottom: 2px solid #e0e0e0; }}
.tab-btn {{ padding: 12px 20px; border: none; background: none; cursor: pointer; font-size: 13px; color: #666; border-bottom: 3px solid transparent; transition: all .2s; display: flex; align-items: center; gap: 6px; }}
.tab-btn:hover {{ color: #1a73e8; }}
.tab-btn.active {{ color: #1a73e8; border-bottom-color: #1a73e8; font-weight: 600; }}
.tab-btn .count {{ background: #1a73e8; color: white; font-size: 10px; padding: 2px 8px; border-radius: 10px; }}
.content {{ padding: 20px 24px; }}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.code-sh a {{ color: #d32f2f; font-family: "SF Mono", monospace; text-decoration: none; }}
.code-sh a:hover {{ text-decoration: underline; }}
.code-sz a {{ color: #1976d2; font-family: "SF Mono", monospace; text-decoration: none; }}
.code-sz a:hover {{ text-decoration: underline; }}
table.dataTable {{ font-size: 12px; }}
.dataTables_wrapper {{ overflow-x: auto; }}
.kline-panel {{ display: none; position: fixed; right: 0; top: 0; width: 520px; height: 100vh; background: white; box-shadow: -4px 0 20px rgba(0,0,0,0.15); z-index: 1000; overflow-y: auto; }}
.kline-panel.active {{ display: block; }}
.kline-panel .close {{ position: sticky; top: 0; background: #1a73e8; color: white; border: none; width: 100%; padding: 12px; font-size: 14px; cursor: pointer; z-index: 1; }}
.kline-panel .chart {{ width: 100%; height: 380px; }}
.kline-panel .info {{ padding: 8px 16px; font-size: 12px; color: #666; }}
.kline-overlay {{ display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.2); z-index: 999; }}
.kline-overlay.active {{ display: block; }}
.code-clickable {{ cursor: pointer; }}
.code-clickable:hover {{ background: #e8f0fe !important; }}
@media (max-width: 768px) {{ .kline-panel {{ width: 100vw; }} }}
footer {{ text-align: center; color: #999; font-size: 11px; padding: 20px; }}
</style>
</head>
<body>
<div class="kline-overlay" id="overlay" onclick="closeKline()"></div>
<div class="kline-panel" id="klinePanel">
  <button class="close" onclick="closeKline()">✕ <span id="klineTitle"></span></button>
  <div class="chart" id="klineChart"></div>
  <div class="info" id="klineInfo"></div>
</div>
<div class="header">
  <h1>A股选股仪表盘</h1>
  <div class="date">{datetime.now().strftime('%Y-%m-%d %H:%M')} · 申万一级行业 · 点击代码查看K线</div>
</div>
<div class="stats">{stats}</div>
<div class="tabs">{tab_buttons}</div>
<div class="content">{tables_html}</div>
<footer>数据每日 18:30 自动更新 · 点击股票代码查看K线图</footer>

<script src="https://code.jquery.com/jquery-3.7.0.min.js"></script>
<script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>
<script>
function switchTab(id) {{
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  document.querySelector(`[onclick="switchTab('${{id}}')"]`).classList.add('active');
  document.getElementById('tab-' + id).classList.add('active');
  try {{ $('#' + id).DataTable().columns.adjust().draw(); }} catch(e) {{}}
}}

$(document).ready(function() {{
  var ids = {tab_ids};
  ids.forEach(function(id) {{
    $('#tbl-' + id).DataTable({{
      pageLength: 25,
      language: {{ url: 'https://cdn.datatables.net/plug-ins/1.13.6/i18n/zh.json' }},
      order: [],
      layout: {{ topStart: 'search', topEnd: 'pageLength' }},
    }});
  }});
}});
</script>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<script>
var KLINES = {kline_json};
var klineChart = null;
function showKline(code) {{
  var d = KLINES[code];
  if (!d) return;
  document.getElementById("overlay").classList.add("active");
  document.getElementById("klinePanel").classList.add("active");
  document.getElementById("klineTitle").textContent = code + " " + (d.name||"");
  setTimeout(function() {{
    if (!klineChart) klineChart = echarts.init(document.getElementById("klineChart"));
    var dates = d.dates, ohlc = d.data;
    var ma5=[], ma10=[], ma20=[];
    for (var i=0; i<ohlc.length; i++) {{
      ma5.push(i>=4 ? (ohlc.slice(i-4,i+1).reduce(function(s,x){{return s+x[1]}},0)/5).toFixed(2) : "-");
      ma10.push(i>=9 ? (ohlc.slice(i-9,i+1).reduce(function(s,x){{return s+x[1]}},0)/10).toFixed(2) : "-");
      ma20.push(i>=19 ? (ohlc.slice(i-19,i+1).reduce(function(s,x){{return s+x[1]}},0)/20).toFixed(2) : "-");
    }}
    klineChart.setOption({{
      tooltip: {{ trigger: "axis" }},
      grid: {{ left:"8%", right:"2%", top:"5%", bottom:"5%" }},
      xAxis: {{ data: dates, axisLabel: {{ rotate: 30, fontSize: 10 }} }},
      yAxis: {{ scale: true }},
      series: [
        {{ name: "K线", type: "candlestick", data: ohlc,
          itemStyle: {{ color: "#d32f2f", color0: "#34a853", borderColor: "#d32f2f", borderColor0: "#34a853" }}, barWidth: "60%" }},
        {{ name: "MA5", type: "line", data: ma5, smooth: true, lineStyle: {{ width: 1, color:"#ff9800" }}, symbol: "none" }},
        {{ name: "MA10", type: "line", data: ma10, smooth: true, lineStyle: {{ width: 1, color:"#2196f3" }}, symbol: "none" }},
        {{ name: "MA20", type: "line", data: ma20, smooth: true, lineStyle: {{ width: 1, color:"#9c27b0" }}, symbol: "none" }}
      ]
    }});
    klineChart.resize();
  }}, 100);
}}
function closeKline() {{
  document.getElementById("overlay").classList.remove("active");
  document.getElementById("klinePanel").classList.remove("active");
}}
</script>
</body>
</html>"""


# ====================================================================
# 主程序
# ====================================================================

# ====================================================================
# 手机版 HTML
# ====================================================================

def build_mobile_html(pipeline: List[PipelineModule],
                      results: Dict[str, pd.DataFrame]) -> str:
    """生成手机端卡片式页面"""

    tabs = ""
    for mod in pipeline:
        df = results.get(mod.id)
        if df is None or len(df) == 0:
            continue

        # 每只股票一张卡，显示核心字段
        cards = ""
        key_cols_raw = [c for c in df.columns
                        if str(c) not in ("代码", "名称", "申万2级", "申万3级")][:6]
        # Timestamp → str
        key_cols = [(c, c.strftime("%Y-%m-%d") if isinstance(c, pd.Timestamp) else str(c))
                    for c in key_cols_raw]

        for _, row in df.head(200).iterrows():
            code = str(row.get("代码", ""))
            name = str(row.get("名称", ""))
            sector = str(row.get("申万1级", ""))
            code_cls = "sh" if code.startswith("sh") else "sz"

            metrics = ""
            for orig_c, label in key_cols:
                val = row[orig_c]
                if isinstance(val, float) and not pd.isna(val):
                    if any(k in label for k in ("振幅", "重叠", "累计", "接续", "涨跌", "评分")):
                        metrics += f'<span class="m">{label}: <b>{val:.1f}%</b></span>'
                    elif "成交" in label or "金额" in label:
                        metrics += f'<span class="m">{label}: <b>{val/10000:.1f}亿</b></span>'
                    elif any(k in label for k in ("价", "最高", "最低")):
                        metrics += f'<span class="m">{label}: <b>{val:.2f}</b></span>'
                    else:
                        metrics += f'<span class="m">{label}: <b>{val:.2f}</b></span>'
                elif not pd.isna(val):
                    metrics += f'<span class="m">{label}: {val}</span>'

            cards += f"""
            <div class="card {code_cls}">
              <div class="card-hd">
                <a class="code" href="https://quote.eastmoney.com/{code}.html" target="_blank">{code}</a>
                <span class="name">{name}</span>
                <span class="sector">{sector}</span>
              </div>
              <div class="card-body">{metrics}</div>
            </div>"""

        active = "active" if len(tabs) == 0 else ""
        tabs += f"""
        <div class="tab-content {active}" id="tab-{mod.id}">
          <div class="tab-title-bar">
            <span>{mod.title}</span><span class="count">{len(df)} 只</span>
          </div>
          <div class="cards">{cards}</div>
        </div>"""

    tab_btns = "".join(
        f'<button class="tab-btn {("active" if i==0 else "")}" onclick="switchTab(\'{m.id}\')">{m.title}</button>'
        for i, m in enumerate(pipeline) if m.id in results
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no">
<title>选股 · 手机版</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:-apple-system,"PingFang SC","Helvetica Neue",sans-serif; background:#f0f2f5; color:#333; font-size:13px; }}
.header {{ background:linear-gradient(135deg,#1a1a2e,#16213e); color:white; padding:14px 16px; position:sticky; top:0; z-index:10; }}
.header h1 {{ font-size:18px; }}
.header .date {{ color:#8892b0; font-size:11px; }}
.tabs {{ display:flex; background:white; border-bottom:1px solid #e0e0e0; position:sticky; top:56px; z-index:9; overflow-x:auto; }}
.tab-btn {{ flex-shrink:0; padding:10px 14px; border:none; background:none; font-size:12px; color:#666; border-bottom:2px solid transparent; }}
.tab-btn.active {{ color:#1a73e8; border-bottom-color:#1a73e8; font-weight:600; }}
.tab-content {{ display:none; }}
.tab-content.active {{ display:block; }}
.tab-title-bar {{ padding:10px 16px; font-size:12px; color:#999; display:flex; justify-content:space-between; }}
.tab-title-bar .count {{ color:#1a73e8; font-weight:600; }}
.cards {{ display:flex; flex-direction:column; gap:6px; padding:0 8px 20px; }}
.card {{ background:white; border-radius:8px; padding:10px 12px; box-shadow:0 1px 3px rgba(0,0,0,0.06); border-left:3px solid #ddd; }}
.card.sh {{ border-left-color:#d32f2f; }}
.card.sz {{ border-left-color:#1976d2; }}
.card-hd {{ display:flex; align-items:baseline; gap:8px; margin-bottom:6px; }}
.card-hd .code {{ font-family:monospace; font-size:11px; color:#1a73e8; text-decoration:none; }}
.card-hd .name {{ font-size:15px; font-weight:600; }}
.card-hd .sector {{ font-size:11px; color:#999; margin-left:auto; }}
.card-body {{ display:flex; flex-wrap:wrap; gap:4px 12px; }}
.m {{ font-size:11px; color:#555; }}
.m b {{ color:#333; }}
footer {{ text-align:center; color:#bbb; font-size:10px; padding:16px; }}
</style>
</head>
<body>
<div class="header"><h1>选股仪表盘</h1><div class="date">{datetime.now().strftime("%Y-%m-%d %H:%M")}</div></div>
<div class="tabs">{tab_btns}</div>
{tabs}
<footer>数据每日18:30自动更新 · 点击标签切换板块</footer>
<script>
function switchTab(id){{
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
  event.target.classList.add('active');
  document.getElementById('tab-'+id).classList.add('active');
}}
</script>
</body>
</html>"""


# ====================================================================
# 配置加载
# ====================================================================

def load_config(path: str = None) -> dict:
    """加载 YAML 配置文件，默认 pipeline_config.yaml"""
    import yaml
    if path is None:
        path = os.path.join(PROJECT_DIR, "pipeline_config.yaml")
    if os.path.exists(path):
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}


def apply_config(config: dict, modules: List[PipelineModule]):
    """把配置文件中的参数注入管线模块的 kwargs"""
    for m in modules:
        if m.id in config:
            m.kwargs = {**m.kwargs, **config[m.id]}


def serve(html_path: str, port: int = 8080):
    """启动简易 HTTP 服务，手机同 WiFi 扫码即可访问"""
    import socket
    import http.server
    import os as _os

    # 获取本机局域网 IP
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    finally:
        s.close()

    html_path = _os.path.abspath(html_path)
    parent = _os.path.dirname(html_path)
    filename = _os.path.basename(html_path)

    # 启动时自动重定向根路径到 HTML 文件
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=parent, **kw)
        def do_GET(self):
            if self.path == "/":
                self.send_response(302)
                self.send_header("Location", f"/{filename}")
                self.end_headers()
            else:
                super().do_GET()

    server = http.server.HTTPServer(("0.0.0.0", port), Handler)

    url = f"http://{ip}:{port}"
    print(f"\n{'='*50}")
    print(f"  手机访问: {url}")
    print(f"  (确保手机和电脑在同一 WiFi)")
    print(f"  按 Ctrl+C 停止服务")
    print(f"{'='*50}\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


def main():
    parser = argparse.ArgumentParser(description="一键选股管线")
    parser.add_argument("--refresh", action="store_true", help="先更新缓存")
    parser.add_argument("--backfill", action="store_true", help="拉取120天历史数据（首次需约2小时）")
    parser.add_argument("--only", type=str, default=None)
    parser.add_argument("--config", type=str, default=None,
                        help="YAML 配置文件路径（默认 pipeline_config.yaml）")
    parser.add_argument("--no-dashboard", action="store_true")
    parser.add_argument("--mobile", action="store_true", help="生成手机版页面")
    parser.add_argument("--serve", type=int, default=0, metavar="PORT",
                        help="生成后启动 HTTP 服务（手机同 WiFi 访问），默认端口 8080")
    args = parser.parse_args()

    only = args.only.split(",") if args.only else None

    # 0. 自动发现模块 + 加载配置
    pipeline = discover_modules()
    config = load_config(args.config)
    apply_config(config, pipeline)
    if config:
        print(f"已加载配置: {len(config)} 个模块")
    print(f"已发现模块: {', '.join(m.id for m in pipeline)}")

    # 1. 数据准备
    from stock_data import StockData
    data = StockData()
    stocks = data.get_stock_list(board="main")
    if args.backfill:
        data.backfill(stocks)
    if args.refresh:
        data.update(stocks)
    if not args.refresh and not args.backfill:
        print("使用已有缓存（加 --refresh 更新 --backfill 拉历史）")
    print(f"股票池: {len(stocks)} 只, 缓存: {data.stock_count} 只, {len(data.cache)} 条")

    # 2. 并行执行分析
    results = run_all(data, pipeline, only=only)

    # 3. 生成仪表盘
    out = None
    if not args.no_dashboard:
        modules = [m for m in pipeline if not only or m.id in only]

        # 桌面版始终生成
        html_pc = build_html(modules, results, data=data)
        with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
            f.write(html_pc)
        pc_kb = os.path.getsize(OUTPUT_HTML) / 1024
        print(f"\n✓ 桌面版: {OUTPUT_HTML} ({pc_kb:.0f}KB)")

        if args.mobile:
            html_m = build_mobile_html(modules, results)
            out = os.path.join(PROJECT_DIR, "dashboard_m.html")
            with open(out, "w", encoding="utf-8") as f:
                f.write(html_m)
            m_kb = os.path.getsize(out) / 1024
            print(f"✓ 手机版: {out} ({m_kb:.0f}KB)")

    # 4. 启动 HTTP 服务（手机访问，默认打开手机版）
    if args.serve:
        port = args.serve or 8080
        html_file = os.path.join(PROJECT_DIR, "dashboard_m.html") if args.mobile else OUTPUT_HTML
        if not os.path.exists(html_file):
            html_file = OUTPUT_HTML
        serve(html_file, port)


if __name__ == "__main__":
    main()
