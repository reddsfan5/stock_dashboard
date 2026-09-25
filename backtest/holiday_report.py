"""节假日效应回测页面（output/holiday_effect.html）——“专业终端风”（设计 A）。

页面完全由嵌入的一份 JSON 驱动：分组（全部 / 长假 / 各节日 / 合并）、休市色带、沪深300 日 K、
顶部行情条（分位 + 休市倒计时）都来自回测结果；注册表新增节日后重跑即可自动出现在切换条、
热力矩阵、图表、日 K 色带、行情条和表格里。

设计要点：
- 深色优先：本页默认深色（页面级偏好 localStorage.holidayTheme，默认 dark）；点工作台顶栏的
  “浅色/深色”按钮即切换并记住，浅色同样可用。颜色全部取 app.css 设计令牌（--app-*），
  ECharts 在主题切换时重绘；
- 布局：顶部行情条（桌面吸顶，手机随页面滚走）+ 吸顶分组条（手机始终一行）；桌面左侧大幅日 K、
  右侧窄栏放“当前位置”分位条与休市倒计时；手机单列、日 K 排第一；
- 行情条 / 倒计时由 ticker_payload() 在生成时从 current / upcoming / kline 计算，不写死数字；
- 桌面端日 K 容器随右侧栏拉伸，ResizeObserver 触发 ECharts resize；
- 日 K：红涨绿跌、MA5/MA20、成交量副图；移动端复用共享 chart-touch.js，
  普通滑动滚动页面，长按进入十字光标并在信息条显示 OHLC / 涨跌幅 / 成交量。
"""

from __future__ import annotations

import json

# 行情条 / 当前位置里主指数的指标与显示名（顺序即显示顺序）
MAIN_ITEMS = (("节前5日%", "近5日"), ("节前10日%", "近10日"), ("T0当日%", "最新一日"), ("节前量比", "量比"))
VOL_METRICS = ("节前量比", "节后量比")
PENDING = ("未到", "节前已知，未复牌")


def _r(x, nd=4):
    return None if x is None else round(float(x), nd)


def _band_of(payload: dict, t0: str) -> dict:
    for b in payload.get("bands") or []:
        if b.get("t0") == t0:
            return b
    return {}


def _color_of(payload: dict, band: dict) -> str:
    if len(band.get("tags") or []) > 1:
        return payload.get("merged_color") or "#14b8a6"
    for h in payload.get("holidays") or []:
        if h.get("name") == band.get("kind"):
            return h.get("color") or "#64748b"
    return "#64748b"


def _group_of(payload: dict, band: dict) -> str:
    """即将到来的休市对应哪个分组：单一节日取自身；合并休市取标签里最后一个存在的分组（中秋+国庆 → 国庆）。"""
    groups = payload.get("groups") or []
    for tag in reversed(band.get("tags") or []):
        if tag in groups:
            return tag
    return groups[0] if groups else "全部"


def _ticker_items(rows: list, index_name: str) -> list:
    by = {(r.get("项目"), r.get("指标")): r for r in rows}
    short = index_name.replace("沪深", "").replace("中证", "") or index_name

    def item(r, label, unit, sub=""):
        return {"label": label, "sub": sub, "unit": unit, "vol": r.get("指标") in VOL_METRICS,
                "value": _r(r.get("当前值")), "pct": _r(r.get("历史分位%")),
                "median": _r(r.get("历史节前中位数")), "base_pct": _r(r.get("基准分位%")),
                "n": r.get("样本数")}

    out = []
    for metric, label in MAIN_ITEMS:
        r = by.get((index_name, metric))
        if r:
            out.append(item(r, label, "" if metric in VOL_METRICS else "%"))
    for r in rows:
        name = str(r.get("项目") or "")
        if "−" in name and r.get("指标") == "节前10日%":
            out.append(item(r, name.split("−")[0] + "−" + short, "pp", "近10日"))
    return out


def _last_quote(k: dict) -> dict | None:
    c = k.get("c") or []
    idx = [i for i, x in enumerate(c) if x is not None]
    if not idx:
        return None
    i = idx[-1]
    prev = c[idx[-2]] if len(idx) > 1 else None
    return {"date": k["d"][i], "o": k["o"][i], "h": k["h"][i], "l": k["l"][i], "c": c[i],
            "chg": _r((c[i] / prev - 1) * 100) if prev else None}


def ticker_payload(payload: dict) -> dict:
    """顶部行情条 + 休市倒计时：全部由回测结果计算（current / meta.upcoming / bands / kline）。

    目标休市 = 最近一个还没到 T0 的休市（没有则取第一个未复牌的）；它对应的分组同时作为页面默认分组。
    """
    meta = payload.get("meta") or {}
    upcoming = meta.get("upcoming") or []
    ahead = [u for u in upcoming if (u.get("距T0交易日") or 0) > 0] or upcoming
    target = ahead[0] if ahead else None
    band = _band_of(payload, target["T0"]) if target else {}
    groups = payload.get("groups") or []
    group = _group_of(payload, band) if band else (groups[0] if groups else "全部")
    index_name = meta.get("index_name") or ""
    items = {v: _ticker_items(rows, index_name)
             for v, rows in ((payload.get("current") or {}).get(group) or {}).items()}

    def sched(u):
        b = _band_of(payload, u.get("T0"))
        return {"name": u.get("节日"), "t0": u.get("T0"), "t1": u.get("T1"), "gap": u.get("距T0交易日"),
                "status": u.get("状态"), "days": b.get("days"), "color": _color_of(payload, b) if b else "#64748b"}

    return {"group": group, "items": items, "quote": _last_quote(payload.get("kline") or {}),
            "countdown": sched(target) if target else None, "schedule": [sched(u) for u in upcoming]}


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>节假日效应回测</title>
<script>
/* 本页深色优先：页面级偏好 holidayTheme（默认 dark），不改动全站的 stockAppTheme */
try{var r=document.documentElement;r.dataset.theme=localStorage.getItem('holidayTheme')||'dark';r.dataset.density=localStorage.getItem('stockAppDensity')||'comfortable';}catch(_){document.documentElement.dataset.theme='dark'}
</script>
<link rel="stylesheet" href="/assets/app.css">
<style>
:root{color-scheme:light;--hx-blue:var(--app-accent,#2563eb);--hx-up:var(--app-up,#e5484d);--hx-down:var(--app-down,#159568);--hx-line:var(--app-border,#e2e8f0);--hx-card:var(--app-surface,#fff);--hx-soft:var(--app-surface-2,#f8fafc);--hx-bg:var(--app-bg,#f4f6f9);--hx-text:var(--app-text,#0f172a);--hx-muted:var(--app-muted,#58677d);
  --hx-line2:color-mix(in srgb,var(--hx-line) 65%,transparent);--hx-strip:color-mix(in srgb,var(--hx-soft) 55%,var(--hx-bg));--hx-text2:color-mix(in srgb,var(--hx-text) 82%,var(--hx-muted));
  --hx-mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,"Roboto Mono",monospace;--hx-radius:8px;--hx-gap:10px;--hx-top:calc(var(--app-nav-h,48px) + var(--app-clock-h,0px))}
:root[data-theme="dark"]{color-scheme:dark}
*{box-sizing:border-box}
body{overflow-x:hidden}
.hx-page{max-width:1480px;margin:auto;padding:10px 14px 56px;color:var(--hx-text)}
.mono,.hx-page table{font-family:var(--hx-mono);font-variant-numeric:tabular-nums}
.up{color:var(--hx-up)}.down{color:var(--hx-down)}.dim{color:var(--hx-muted)}
/* ---- 标题行 ---- */
.hx-title{display:flex;align-items:center;gap:10px;min-width:0;margin:2px 0 8px}
.hx-logo{flex:0 0 auto;width:26px;height:26px;border-radius:6px;background:var(--hx-blue);color:var(--hx-card);display:grid;place-items:center;font-weight:800;font-size:13px}
.hx-title h1{font-size:17px;margin:0;white-space:nowrap}
.hx-title p{margin:0;color:var(--hx-muted);font-size:11px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hx-live{margin-left:auto;flex:0 0 auto;display:inline-flex;align-items:center;gap:6px;font-size:11px;color:var(--hx-muted)}
.hx-live i{width:6px;height:6px;border-radius:50%;background:var(--hx-down);box-shadow:0 0 0 3px color-mix(in srgb,var(--hx-down) 25%,transparent)}
/* ---- 行情条 ---- */
.hx-ticker{display:flex;overflow-x:auto;scrollbar-width:none;overscroll-behavior-x:contain;border:1px solid var(--hx-line);border-radius:var(--hx-radius) var(--hx-radius) 0 0;background:var(--hx-strip)}
.hx-ticker::-webkit-scrollbar{display:none}
.hx-tk{flex:0 0 auto;padding:6px 12px;border-right:1px solid var(--hx-line2);min-width:0}
.hx-tk span{display:block;font-size:10.5px;color:var(--hx-muted);white-space:nowrap}
.hx-tk b{font-family:var(--hx-mono);font-variant-numeric:tabular-nums;font-size:15px;font-weight:650;white-space:nowrap}
.hx-tk small{margin-left:5px;font-family:var(--hx-mono);font-size:10.5px;color:var(--hx-muted)}
.hx-tk small.up{color:var(--hx-up)}.hx-tk small.down{color:var(--hx-down)}
.hx-tk.cd{margin-left:auto;border-right:0;border-left:1px solid var(--hx-line2)}
.hx-tk.cd b{color:var(--hx-blue);font-size:17px}
/* ---- 吸顶分组条 ---- */
.hx-bar{position:sticky;top:calc(var(--hx-top) + 2px);z-index:20;display:flex;gap:8px;align-items:center;margin:0 0 var(--hx-gap);padding:6px 8px;border:1px solid var(--hx-line);border-top:0;border-radius:0 0 var(--hx-radius) var(--hx-radius);background:color-mix(in srgb,var(--hx-card) 94%,transparent);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);box-shadow:0 6px 18px rgba(0,0,0,.08)}
.hx-chips{display:flex;gap:5px;overflow-x:auto;scrollbar-width:none;flex:1 1 auto;min-width:0;overscroll-behavior-x:contain}.hx-chips::-webkit-scrollbar{display:none}
.hx-chip{flex:0 0 auto;display:inline-flex;align-items:center;gap:5px;height:28px;border:1px solid var(--hx-line);background:var(--hx-soft);color:var(--hx-text2);border-radius:6px;padding:0 10px;font-size:12.5px;font-weight:600;cursor:pointer}
.hx-chip i{width:7px;height:7px;border-radius:50%;display:inline-block}
.hx-chip small{opacity:.6;font-weight:500;font-family:var(--hx-mono)}
.hx-chip.active{background:var(--hx-blue);border-color:var(--hx-blue);color:var(--hx-card)}
.hx-seg{display:inline-flex;border:1px solid var(--hx-line);border-radius:6px;overflow:hidden;flex:0 0 auto;background:var(--hx-soft)}
.hx-seg button{border:0;background:transparent;color:var(--hx-text2);height:28px;padding:0 10px;font-size:12px;cursor:pointer;white-space:nowrap}.hx-seg button.active{background:var(--hx-blue);color:var(--hx-card);font-weight:650}
/* ---- 栅格 / 面板 ---- */
.hx-grid{display:grid;gap:var(--hx-gap);grid-template-columns:minmax(0,1fr)}
.hx-panel{min-width:0;border:1px solid var(--hx-line);border-radius:var(--hx-radius);background:var(--hx-card);container-type:inline-size}
.hx-ph{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;padding:8px 12px;border-bottom:1px solid var(--hx-line2)}
.hx-ph h2{font-size:12.5px;margin:0;font-weight:650;letter-spacing:.05em;color:var(--hx-text2)}
.hx-ph .hx-sub{font-size:10.5px;color:var(--hx-muted);font-family:var(--hx-mono)}
.hx-pb{padding:10px 12px}
.note{color:var(--hx-muted);font-size:11px;line-height:1.6;margin:0;padding:6px 12px 9px}
.hx-pair{display:grid;gap:var(--hx-gap);grid-template-columns:minmax(0,1fr)}
@media(min-width:1001px){
  .hx-grid{grid-template-columns:minmax(0,1fr) 360px}
  .hx-kcard{grid-row:span 2}
  .hx-wide{grid-column:1/-1}
  .hx-pair{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}
}
@media(min-width:1001px) and (min-height:720px){
  .hx-ticker{position:sticky;top:var(--hx-top);z-index:21;background:var(--hx-strip)}
  .hx-bar{top:calc(var(--hx-top) + var(--hx-tk-h,52px))}
}
/* ---- 日 K ---- */
.hx-ktools{display:flex;gap:4px;flex-wrap:wrap;align-items:center}
.hx-btn{border:1px solid var(--hx-line);background:var(--hx-soft);color:var(--hx-text2);border-radius:5px;height:26px;padding:0 9px;font-size:11.5px;font-family:var(--hx-mono);cursor:pointer}
.hx-btn:hover{border-color:var(--hx-blue)}.hx-btn.on{background:var(--hx-blue);border-color:var(--hx-blue);color:var(--hx-card);font-weight:650}
.hx-ktip{min-height:38px;display:flex;align-items:center;gap:6px 14px;flex-wrap:wrap;padding:7px 12px;background:var(--hx-strip);border-bottom:1px solid var(--hx-line2);font-size:12.5px;font-family:var(--hx-mono);font-variant-numeric:tabular-nums}
.hx-ktip .kdate{font-size:13px}.hx-ktip .kmode{font-family:inherit;font-size:10.5px;font-weight:700;padding:1px 6px;border-radius:4px;border:1px solid var(--hx-line);color:var(--hx-muted)}
.hx-ktip .k-hover,.hx-ktip .k-scrub{border-color:var(--hx-blue);color:var(--hx-blue)}.hx-ktip .k-sel{border-color:#facc15;color:#ca8a04}
#hx-kline:focus{outline:none}#hx-kline:focus-visible{outline:2px solid var(--hx-blue);outline-offset:-2px}
.hx-ktip b{font-size:12px}.hx-ktip .hol{font-weight:700;padding:1px 7px;border-radius:4px;color:#fff;font-size:10.5px;font-family:inherit}
.hx-focus{font-size:11px;color:var(--hx-muted);margin:0;padding:6px 12px 0}
.hx-kchart{width:100%;height:540px}
@media(min-width:1001px){.hx-kcard{display:flex;flex-direction:column}.hx-kcard .hx-kchart{flex:1 1 auto;min-height:540px;height:auto}}
.hx-more-rows>summary{cursor:pointer;list-style:none;font-size:11.5px;color:var(--hx-blue);padding:7px 0 5px}.hx-more-rows>summary::-webkit-details-marker{display:none}.hx-more-rows>summary::after{content:" ▾"}.hx-more-rows[open]>summary::after{content:" ▴"}
.hx-kleg{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:11px;color:var(--hx-muted);padding:7px 12px;border-top:1px solid var(--hx-line2)}
.hx-kleg span{display:inline-flex;align-items:center;gap:4px}.hx-kleg i{width:12px;height:9px;border-radius:2px;display:inline-block;opacity:.8}
.hx-kcard{scroll-margin-top:calc(var(--hx-top) + 60px)}
.hx-chart{width:100%;height:340px}
/* ---- 当前位置分位条 ---- */
.hx-pos{padding:2px 12px 4px}
.hx-pr{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2px 12px;align-items:center;padding:7px 0;border-bottom:1px solid var(--hx-line2)}
.hx-pr:last-child{border-bottom:0}
.hx-pr .lab{font-size:12px;color:var(--hx-text2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.hx-pr .lab small{color:var(--hx-muted);font-size:10.5px;margin-left:4px}
.hx-pr b{grid-row:span 3;font-family:var(--hx-mono);font-variant-numeric:tabular-nums;font-size:16px;font-weight:650;text-align:right;min-width:74px}
.hx-pbar{position:relative;height:6px;border-radius:999px;margin:3px 0 2px;background:linear-gradient(90deg,color-mix(in srgb,var(--hx-down) 38%,transparent),var(--hx-soft) 50%,color-mix(in srgb,var(--hx-up) 38%,transparent))}
.hx-pbar em{position:absolute;left:50%;top:-2px;bottom:-2px;border-left:1px dashed var(--hx-muted);opacity:.6}
.hx-pbar i{position:absolute;top:-4px;width:3px;height:14px;border-radius:2px;background:var(--hx-text);box-shadow:0 0 6px color-mix(in srgb,var(--hx-text) 55%,transparent);transform:translateX(-1.5px)}
.hx-pr .meta{font-size:10px;color:var(--hx-muted);font-family:var(--hx-mono);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* ---- 休市倒计时 ---- */
.hx-cd{display:grid;grid-template-columns:auto minmax(0,1fr);gap:12px;align-items:center;padding:12px}
.hx-cd .big{font-family:var(--hx-mono);font-size:46px;line-height:1;font-weight:800;color:var(--hx-blue)}.hx-cd .big small{font-size:12px;color:var(--hx-muted);font-weight:500;margin-left:3px}
.hx-cd .txt{font-size:12px;line-height:1.7;font-family:var(--hx-mono)}
.hx-sched{border-top:1px solid var(--hx-line2)}
.hx-sched div{display:flex;justify-content:space-between;gap:8px;padding:7px 12px;font-size:11.5px;font-family:var(--hx-mono);border-bottom:1px solid var(--hx-line2)}
.hx-sched div:last-child{border-bottom:0}
.hx-sched .st{color:var(--app-warn,#b54708);white-space:nowrap}
.hx-facts{margin:0;padding:8px 12px 10px 28px;font-size:11.5px;line-height:1.7;color:var(--hx-text2);border-top:1px solid var(--hx-line2)}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;vertical-align:1px}
/* ---- 表格 ---- */
.tw{overflow:auto;-webkit-overflow-scrolling:touch;max-width:100%}
.tw.tall{max-height:560px}
table{border-collapse:separate;border-spacing:0;width:100%;font-size:11.5px;background:var(--hx-card)}
th,td{padding:6px 8px;border-bottom:1px solid var(--hx-line2);text-align:right;white-space:nowrap}
th{background:var(--hx-card);color:var(--hx-muted);font-weight:550;position:sticky;top:0;z-index:2;border-bottom:1px solid var(--hx-line)}
th:first-child,td:first-child{text-align:left;position:sticky;left:0;z-index:1;background:var(--hx-card);box-shadow:1px 0 0 var(--hx-line2)}
th:first-child{z-index:3}
tr.sel td{background:color-mix(in srgb,var(--hx-blue) 14%,var(--hx-card))}
tr.sel td:first-child{box-shadow:inset 3px 0 0 var(--hx-blue),1px 0 0 var(--hx-line2)}
tr.muted td{opacity:.42}
tr.click{cursor:pointer}tr.click:hover td{background:color-mix(in srgb,var(--hx-blue) 8%,var(--hx-card))}
.tag{display:inline-block;font-size:10px;padding:0 5px;border-radius:4px;border:1px solid var(--hx-line);margin-left:4px;vertical-align:1px;font-family:var(--hx-mono)}
.cell2{display:block;font-size:9.5px;color:var(--hx-muted);font-weight:500}
.hx-heat td.h{min-width:62px;border-left:1px solid var(--hx-card);border-bottom-color:var(--hx-card);padding:4px 8px}
.hx-heat td.h b{display:block;font-size:12px;font-weight:650}
.hx-legend{display:flex;align-items:center;gap:6px;font-size:10.5px;color:var(--hx-muted)}
.hx-legend i{display:inline-block;width:56px;height:8px;border-radius:4px;background:linear-gradient(90deg,rgba(21,149,104,.6),transparent,rgba(229,72,77,.6))}
.hx-legend i.v{width:12px;background:rgba(37,99,235,.5)}
/* ---- 结论 / 方法 ---- */
.hx-kpis{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1px;background:var(--hx-line2);border-bottom:1px solid var(--hx-line2)}
@container (min-width:560px){.hx-kpis{grid-template-columns:repeat(4,minmax(0,1fr))}}
.hx-kpi{background:var(--hx-card);padding:9px 12px}.hx-kpi span{display:block;font-size:10.5px;color:var(--hx-muted)}.hx-kpi b{font-family:var(--hx-mono);font-size:19px;display:block;margin:2px 0}.hx-kpi small{font-size:10.5px;color:var(--hx-muted);font-family:var(--hx-mono)}
details.hx-fold>summary{cursor:pointer;list-style:none}
details.hx-fold>summary::-webkit-details-marker{display:none}
details.hx-fold>summary .chev{color:var(--hx-blue);font-size:11px;transition:transform .15s}
details.hx-fold[open]>summary .chev{transform:rotate(180deg)}
details.hx-fold:not([open])>summary{border-bottom:0}
.hx-concl{margin:0;padding:10px 12px;list-style:none;display:grid;gap:6px}
.hx-concl li{padding-left:10px;border-left:2px solid var(--hx-line);line-height:1.65;font-size:12.5px;color:var(--hx-text2)}
@container (min-width:900px){.hx-concl{grid-template-columns:1fr 1fr}}
.hx-foot{color:var(--hx-muted);font-size:12px;line-height:1.75;padding:4px 12px}
.hx-foot code{font-size:11px;word-break:break-all}
.hx-disclaimer{margin:8px 12px 12px;padding:8px 12px;border-radius:6px;border:1px solid color-mix(in srgb,#b7791f 45%,var(--hx-line));background:color-mix(in srgb,#b7791f 10%,var(--hx-card));font-size:12px;color:var(--hx-text)}
@media(max-width:1000px){.hx-kchart{height:480px}}
@media(max-width:700px){
  .hx-page{padding:8px 8px 40px}
  .hx-title h1{font-size:16px}.hx-title p{display:none}
  .hx-tk{padding:5px 10px}.hx-tk b{font-size:14px}
  /* 手机：分组条压成一行，吸顶时不遮图 */
  .hx-bar{gap:6px;padding:5px}.hx-chip{padding:0 8px;font-size:12px;height:27px}.hx-seg button{padding:0 7px;font-size:11px;height:27px}.hx-exy{display:none}
  .hx-kchart{height:420px}.hx-chart{height:290px}
  .hx-ktip{font-size:11.5px;gap:4px 10px;padding:6px 8px}.hx-ktip .kdate{font-size:12px}
  .hx-ph{padding:7px 10px}.hx-pos{padding:2px 10px 4px}.note{padding:6px 10px 8px}
  .hx-cd .big{font-size:40px}
}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
</style>
</head>
<body>
<main class="hx-page">
  <div class="hx-title"><span class="hx-logo" aria-hidden="true">节</span><h1>节假日效应回测</h1><p id="hx-sub"></p><span class="hx-live"><i></i><span id="hx-gen"></span></span></div>
  <div class="hx-ticker" id="hx-ticker" aria-label="今年节前位置"></div>
  <div class="hx-bar">
    <div class="hx-chips" id="hx-chips" role="tablist" aria-label="节日分组"></div>
    <div class="hx-seg" id="hx-variant" aria-label="年份口径"></div>
  </div>
  <div class="hx-grid">
    <section class="hx-panel hx-kcard" id="hx-kcard">
      <div class="hx-ph"><h2 id="hx-ktitle">沪深300 日 K · 休市色带</h2>
        <div class="hx-ktools"><button class="hx-btn" data-z="60">近3月</button><button class="hx-btn" data-z="120">近半年</button><button class="hx-btn" data-z="250">近1年</button><button class="hx-btn" data-z="750">近3年</button><button class="hx-btn" data-z="all">全部</button><button class="hx-btn" data-z="now" title="跳到即将到来的休市">当前</button></div></div>
      <div class="hx-ktip" id="hx-ktip"></div>
      <p class="hx-focus"><span id="hx-khint"></span> <span id="hx-focus">点击下方“事件明细”任一行，可在日 K 上定位到该次休市。</span></p>
      <div class="hx-kchart" id="hx-kline" tabindex="0" role="img" aria-label="日K线：悬停或点击查看每日详情，选中后可用左右方向键逐日移动"></div>
      <div class="hx-kleg" id="hx-kleg"></div>
      <p class="note">色带 = 休市前最后交易日 T0 到复牌首日 T1，只显示当前分组的休市。红涨绿跌，MA5 / MA20；成交量单位万手。手机上下滑动页面，长按日 K 显示十字光标与信息条。</p>
    </section>
    <section class="hx-panel" id="hx-poscard">
      <div class="hx-ph"><h2 id="hx-cur-title">当前位置</h2><span class="hx-sub" id="hx-cur-sub"></span></div>
      <div class="hx-pos" id="hx-current"></div>
      <p class="note">竖线 = 当前值在历史节前分布中的分位；虚线 = 中位。以本地缓存最新交易日视作 T0。</p>
    </section>
    <section class="hx-panel" id="hx-cdcard">
      <div class="hx-ph"><h2>休市日程</h2><span class="hx-sub">交易日倒计时</span></div>
      <div id="hx-sched"></div>
    </section>
    <section class="hx-panel hx-wide"><div class="hx-ph"><h2 id="hx-mtitle">总览热力矩阵</h2><span class="hx-legend">跌 <i></i> 涨 <i class="v"></i> 缩量</span></div>
      <div class="tw" id="hx-matrix"></div>
      <p class="note">单元格：均值（大字）/ 胜率（小字）；颜色按列内最大绝对值缩放。点击行切换分组。节日分组含带该标签的合并休市；RS = 代理指数收益 − 主指数（百分点），胜率 = 跑赢占比；量比胜率 = 缩量(&lt;1)占比。</p></section>
    <div class="hx-pair hx-wide">
      <section class="hx-panel"><div class="hx-ph"><h2>平均累计路径 T-10 ~ T+20</h2><span class="hx-sub">T0 收盘 = 0 · T+1 复牌首日</span></div>
        <div class="hx-chart" id="hx-path"></div>
        <p class="note">当前分组加粗；灰色虚线为基准（全部交易日）；橙色点线为进行中的休市。长按图表查看数值。</p></section>
      <section class="hx-panel"><div class="hx-ph"><h2>风险偏好：相对强弱</h2><span class="hx-sub">&gt;0 小盘 / 成长跑赢 · pp</span></div>
        <div class="hx-chart" id="hx-rs"></div>
        <p class="note">柱 = 代理指数相对主指数的平均超额（百分点），虚线 = 同口径基准。</p></section>
    </div>
    <section class="hx-panel hx-wide"><div class="hx-ph"><h2 id="hx-etitle">事件明细</h2><span class="hx-sub">点击行在日 K 定位 · 灰行 = 剔除年</span></div><div class="tw tall" id="hx-events"></div>
      <p class="note">量比 = T-5..T0 平均成交量 ÷ T-25..T-6 平均成交量。灰色行为“剔除异常年”口径下被排除的事件。</p></section>
    <section class="hx-panel hx-wide">
      <details class="hx-fold" open><summary class="hx-ph"><h2 id="hx-concl-title">结论</h2><span class="chev">▾</span></summary><div id="hx-concl"></div></details>
    </section>
    <section class="hx-panel hx-wide">
      <details class="hx-fold"><summary class="hx-ph"><h2 id="hx-stats-title">统计 vs 基准</h2><span class="chev">▾</span></summary><div class="tw" id="hx-stats"></div>
        <p class="note">基准 = 样本期内每个交易日都当作 T0 的同口径分布。p 值为自助抽样双侧近似，窗口重叠且事件少，仅供参考。</p></details>
    </section>
    <section class="hx-panel hx-wide">
      <details class="hx-fold"><summary class="hx-ph"><h2>方法、数据口径与重跑方式</h2><span class="chev">▾</span></summary><div class="hx-foot" id="hx-foot"></div></details>
      <div class="hx-disclaimer">以上为历史统计关联，不构成投资建议。</div>
    </section>
  </div>
</main>
<script id="holiday-data" type="application/json">__DATA__</script>
<script src="/vendor/echarts.min.js"></script>
<script src="/assets/app-shell.js"></script>
<script src="/assets/chart-touch.js"></script>
<script>
(function(){
'use strict';
var D=JSON.parse(document.getElementById('holiday-data').textContent);
var TK=D.ticker||{};
var S={g:D.groups.indexOf(TK.group)>=0?TK.group:D.groups[0],v:'all',focus:null};
var $=function(id){return document.getElementById(id)};
var esc=function(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})};
function num(v,nd,sign){if(v==null||!isFinite(v))return'—';nd=nd==null?2:nd;var t=(+v).toFixed(nd);return(sign!==false&&v>0?'+':'')+t}
function cls(v){return v==null?'dim':(v>0?'up':(v<0?'down':''))}
function td(v,nd,sign){return'<td class="num '+(sign===false?'':cls(v))+'">'+num(v,nd,sign)+'</td>'}
var VOL=['节前量比','节后量比'];
var ex=D.meta.exclude_years||[];
var PAL=['#0ea5e9','#f97316','#eab308','#8b5cf6','#e11d48','#22c55e','#ec4899','#64748b'];
var HCOLOR={};D.holidays.forEach(function(h,i){HCOLOR[h.name]=h.color||PAL[i%PAL.length]});HCOLOR['合并']=D.merged_color||'#14b8a6';
function bandColor(b){return b.kind==='合并'?HCOLOR['合并']:(HCOLOR[b.kind]||'#64748b')}
function inGroup(e,g,v){
  if(v==='ex'&&ex.indexOf(+(e['年份']!=null?e['年份']:e.year))>=0)return false;
  var tags=e['标签']||e.tags||[],kind=e['类型']||e.kind,days=e['休市自然日']!=null?e['休市自然日']:e.days;
  if(g==='全部')return true;
  if(g.indexOf('长假')===0)return days==null?false:days>=5;
  if(g==='合并')return kind==='合并';
  return tags.indexOf(g)>=0;
}
function stat(g,v,m){for(var i=0;i<D.summary.length;i++){var s=D.summary[i];if(s['分组']===g&&s['口径']===v&&s['指标']===m)return s}return null}
function rsStat(code,g,v,m){for(var i=0;i<D.rs_summary.length;i++){var s=D.rs_summary[i];if(s['代码']===code&&s['分组']===g&&s['口径']===v&&s['指标']===m)return s}return null}
function tk(){var cs=getComputedStyle(document.documentElement);var g=function(n,f){return(cs.getPropertyValue(n)||'').trim()||f};
  return{text:g('--app-text','#0f172a'),muted:g('--app-muted','#58677d'),line:g('--app-border','#e2e8f0'),card:g('--app-surface','#fff'),soft:g('--app-surface-2','#f8fafc'),
    accent:g('--app-accent','#2563eb'),up:g('--app-up','#e5484d'),down:g('--app-down','#159568'),dark:document.documentElement.dataset.theme==='dark'}}
function coarse(){return window.ChartTouch&&ChartTouch.isCoarse?ChartTouch.isCoarse():matchMedia('(pointer:coarse),(max-width:760px)').matches}
function narrow(){return matchMedia('(max-width:700px)').matches}
function short(g){return g.replace(/\(.*\)/,'')}

/* ---------- 主题：本页深色优先（holidayTheme），顶栏按钮切换后记住 ---------- */
var THEME_KEY='holidayTheme',shellReady=false;
function pageTheme(){try{return localStorage.getItem(THEME_KEY)||'dark'}catch(_){return'dark'}}
function syncThemeLabel(){var b=document.querySelector('[data-shell-action="theme"]');if(b)b.textContent=document.documentElement.dataset.theme==='dark'?'浅色':'深色'}
function applyPageTheme(){var t=pageTheme();if(document.documentElement.dataset.theme!==t)document.documentElement.dataset.theme=t;syncThemeLabel()}
window.addEventListener('stockapp:theme',function(e){
  if(!shellReady){applyPageTheme();return}               // 工作台初始化时会套用全站主题，这里还原为本页偏好
  try{localStorage.setItem(THEME_KEY,e.detail==='light'?'light':'dark')}catch(_){}
  setTimeout(redrawCharts,30);
});
document.addEventListener('DOMContentLoaded',function(){applyPageTheme();shellReady=true;setTimeout(function(){syncThemeLabel();measureTicker()},400)});

$('hx-sub').textContent=D.meta.index_name+' · '+D.meta.sample+' · 休市 '+D.events.length+' 次 / 完整 '+D.events.filter(function(e){return e['状态']==='完整'}).length+' · 节日注册表 × 交易日历空档识别';
$('hx-gen').textContent='生成 '+String(D.meta.generated||'').slice(5);

/* ---------- 顶部行情条（ticker_payload 生成） ---------- */
function unitOf(it){return it.vol?'':it.unit}
function renderTicker(){
  var h='',q=TK.quote;
  if(q)h+='<div class="hx-tk"><span>'+esc(D.meta.index_name)+' · '+esc(String(q.date).slice(5))+'</span><b class="'+cls(q.chg)+'">'+num(q.c,2,false)+'</b><small class="'+cls(q.chg)+'">'+num(q.chg)+'%</small></div>';
  var items=(TK.items||{})[S.v]||[];
  items.forEach(function(it){h+='<div class="hx-tk" title="'+esc(TK.group)+'节前历史中位 '+num(it.median,2,!it.vol)+unitOf(it)+' · n='+it.n+'"><span>'+esc(it.label)+(it.sub?' '+esc(it.sub):'')+'</span><b class="'+(it.vol?'':cls(it.value))+'">'+num(it.value,2,!it.vol)+unitOf(it)+'</b><small>分位'+num(it.pct,0,false)+'</small></div>'});
  var c=TK.countdown;
  if(c)h+='<div class="hx-tk cd"><span>距'+esc(c.name)+'休市</span><b>'+c.gap+'</b><small>交易日</small></div>';
  $('hx-ticker').innerHTML=h;
  measureTicker();
}
function measureTicker(){var t=$('hx-ticker');if(t)document.documentElement.style.setProperty('--hx-tk-h',t.offsetHeight+'px')}

function chips(){
  var h='';D.groups.forEach(function(g){
    var n=D.events.filter(function(e){return inGroup(e,g,S.v)&&e['状态']==='完整'}).length;
    var dot=HCOLOR[g]&&g!==S.g?'<i style="background:'+HCOLOR[g]+'"></i>':'';
    h+='<button class="hx-chip'+(g===S.g?' active':'')+'" data-g="'+esc(g)+'" role="tab" aria-selected="'+(g===S.g)+'" title="'+esc(g)+'">'+dot+esc(short(g))+'<small>'+n+'</small></button>'});
  $('hx-chips').innerHTML=h;
  var vh='';Object.keys(D.variants).forEach(function(k){
    var yrs=k==='ex'?'<span class="hx-exy">（'+esc(ex.join('、'))+'）</span>':'';
    vh+='<button data-v="'+k+'" class="'+(k===S.v?'active':'')+'" title="'+(k==='ex'?'剔除 '+esc(ex.join('、')):'')+'">'+esc(D.variants[k])+yrs+'</button>'});
  $('hx-variant').innerHTML=vh;
  var act=document.querySelector('.hx-chip.active');if(act){var box=$('hx-chips');box.scrollLeft=Math.max(0,act.offsetLeft-box.clientWidth/2+act.clientWidth/2)}
}
document.addEventListener('click',function(e){
  var z=e.target.closest('[data-z]');if(z){zoomPreset(z.getAttribute('data-z'));return}
  var ev=e.target.closest('[data-ev]');if(ev){focusEvent(ev.getAttribute('data-ev'));return}
  var c=e.target.closest('[data-g]');if(c){S.g=c.getAttribute('data-g');render();return}
  var v=e.target.closest('[data-v]');if(v){S.v=v.getAttribute('data-v');render()}
});

/* ---------- 当前位置（跟随分组 / 口径） ---------- */
function curLabel(r){
  var main=r['项目']===D.meta.index_name,m=r['指标'];
  var map={'节前5日%':'近5日','节前10日%':'近10日','T0当日%':'最新一日','节前量比':'量比(6日/20日)'};
  if(main)return esc(map[m]||m);
  return esc(r['项目'])+'<small>'+esc(map[m]||m)+'</small>';
}
function renderCurrent(){
  var rows=((D.current[S.g]||{})[S.v])||[];
  var cd=TK.countdown,tag=cd&&short(S.g)===short(TK.group||'')?'今年'+short(S.g)+'前 vs 历史'+short(S.g)+'节前':'当前位置 vs 历史'+short(S.g)+'节前';
  $('hx-cur-title').textContent=tag;
  $('hx-cur-sub').textContent='截至 '+String(D.meta.last_date).slice(5)+' · '+D.variants[S.v];
  var row=function(r){var vol=VOL.indexOf(r['指标'])>=0,rel=String(r['项目']).indexOf('−')>=0,p=r['历史分位%'];
    var unit=vol?'':(rel?'pp':'%');
    return'<div class="hx-pr"><div class="lab">'+curLabel(r)+'</div><b class="'+(vol?'':cls(r['当前值']))+'">'+num(r['当前值'],2,!vol)+unit+'</b>'+
      '<div class="hx-pbar" title="历史分位 '+num(p,0,false)+'%"><em></em>'+(p!=null?'<i style="left:'+Math.max(1,Math.min(99,p))+'%"></i>':'')+'</div>'+
      '<div class="meta">中位 '+num(r['历史节前中位数'],2,!vol)+unit+' · 分位 '+num(p,0,false)+'% · 平时 '+num(r['基准分位%'],0,false)+'% · n='+r['样本数']+'</div></div>'};
  // 主指数 + 各代理近10日相对强弱常显，其余（近5日相对、上证成交量等）折叠
  var main=function(r){return r['项目']===D.meta.index_name||(String(r['项目']).indexOf('−')>=0&&r['指标']==='节前10日%')};
  var prim=rows.filter(main),rest=rows.filter(function(r){return!main(r)});
  var open=document.querySelector('.hx-more-rows[open]')?' open':'';
  var h=prim.map(row).join('')+(rest.length?'<details class="hx-more-rows"'+open+'><summary>展开其余 '+rest.length+' 项</summary>'+rest.map(row).join('')+'</details>':'');
  $('hx-current').innerHTML=h||'<p class="note">该分组没有可比的历史节前样本。</p>';
}

/* ---------- 休市日程 + 跨节日要点 ---------- */
function renderSched(){
  var c=TK.countdown,h='';
  if(c)h+='<div class="hx-cd"><div class="big">'+c.gap+'<small>交易日</small></div><div class="txt"><div><span class="dot" style="background:'+esc(c.color)+'"></span><b>'+esc(c.name)+'</b>'+(c.days?' · 休市'+c.days+'天':'')+'</div><div class="dim">T0 '+esc(String(c.t0).slice(5))+' → 复牌 '+esc(String(c.t1).slice(5))+'</div></div></div>';
  var rest=(TK.schedule||[]).filter(function(u){return!c||u.t0!==c.t0});
  if(rest.length){h+='<div class="hx-sched">';rest.forEach(function(u){h+='<div><span><span class="dot" style="background:'+esc(u.color)+'"></span>'+esc(u.name)+' '+esc(String(u.t0).slice(5))+' → '+esc(String(u.t1).slice(5))+'</span><span class="st">'+esc(u.status)+'</span></div>'});h+='</div>'}
  if(!c&&!rest.length)h+='<p class="note">近期没有待到来的休市。</p>';
  var f=[],a=stat('全部',S.v,'节前量比');if(a)f.push('全部休市节前缩量占比 <b>'+num(a['胜率%'],0,false)+'%</b>（平时 '+num(a['基准胜率%'],0,false)+'%）');
  var lg=D.groups.filter(function(g){return g.indexOf('长假')===0})[0],p0=D.meta.proxies[0];
  if(lg&&p0){var p1=rsStat(p0.code,lg,S.v,'节前5日%'),p2=rsStat(p0.code,lg,S.v,'节后5日%');if(p1&&p2)f.push('长假'+esc(p0.name)+'：节前5日 <b class="'+cls(p1['均值'])+'">'+num(p1['均值'])+'pp</b>（跑赢 '+num(p1['胜率%'],0,false)+'%），节后5日 <b class="'+cls(p2['均值'])+'">'+num(p2['均值'])+'pp</b>（跑赢 '+num(p2['胜率%'],0,false)+'%）')}
  var t1=stat('全部',S.v,'T1跳空%');if(t1)f.push('复牌跳空均值 <b class="'+cls(t1['均值'])+'">'+num(t1['均值'])+'%</b>，胜率 '+num(t1['胜率%'],0,false)+'%（平时 '+num(t1['基准胜率%'],0,false)+'%）');
  if(f.length)h+='<ul class="hx-facts">'+f.map(function(x){return'<li>'+x+'</li>'}).join('')+'</ul>';
  $('hx-sched').innerHTML=h;
}

function renderConcl(){
  var lines=(D.conclusions[S.g]||{})[S.v]||[];
  $('hx-concl-title').textContent='结论 · '+short(S.g)+' · '+D.variants[S.v];
  var k=['节前5日%','T0当日%','T1跳空%','节后10日%'],kh='';
  k.forEach(function(m){var s=stat(S.g,S.v,m);if(!s)return;
    kh+='<div class="hx-kpi"><span>'+esc(m.replace('%',''))+'</span><b class="'+cls(s['均值'])+'">'+num(s['均值'])+'%</b><small>胜率 '+num(s['胜率%'],0,false)+'% · 基准 '+num(s['基准均值'])+'%</small></div>'});
  $('hx-concl').innerHTML=(kh?'<div class="hx-kpis">'+kh+'</div>':'')+'<ul class="hx-concl">'+lines.map(function(x){return'<li>'+esc(x)+'</li>'}).join('')+'</ul>';
}

function renderMatrix(){
  var cols=D.matrix_cols.concat(D.matrix_rs);
  var rows=D.matrix.filter(function(r){return r['口径']===S.v});
  $('hx-mtitle').textContent='总览热力矩阵 · '+D.meta.index_name+' · '+D.variants[S.v];
  var scale={};cols.forEach(function(c){var vol=VOL.indexOf(c)>=0,mx=0;rows.forEach(function(r){var m=r[c+'·均值'];if(m!=null)mx=Math.max(mx,Math.abs(vol?1-m:m))});scale[c]=mx||1});
  var h='<table class="hx-heat"><thead><tr><th>分组</th><th>n</th>'+cols.map(function(c){return'<th>'+esc(c.replace('%',''))+'</th>'}).join('')+'</tr></thead><tbody>';
  rows.forEach(function(r){
    var dot=HCOLOR[r['分组']]?'<span class="dot" style="background:'+HCOLOR[r['分组']]+'"></span>':'<span class="dot"></span>';
    h+='<tr class="click'+(r['分组']===S.g?' sel':'')+'" data-g="'+esc(r['分组'])+'"><td>'+dot+esc(short(r['分组']))+'</td><td class="num dim">'+r['样本数']+'</td>';
    cols.forEach(function(c){var vol=VOL.indexOf(c)>=0,m=r[c+'·均值'],w=r[c+'·胜率'],bg='';
      if(m!=null){var x=vol?(1-m):m,a=Math.min(Math.abs(x)/scale[c],1)*0.5+0.04;
        bg=vol?(x>0?'rgba(37,99,235,'+a.toFixed(3)+')':'transparent'):(x>0?'rgba(229,72,77,'+a.toFixed(3)+')':'rgba(21,149,104,'+a.toFixed(3)+')')}
      h+='<td class="h num" style="background:'+bg+'"><b>'+num(m,2,!vol)+'</b><span class="cell2">'+num(w,0,false)+'%</span></td>'});
    h+='</tr>'});
  $('hx-matrix').innerHTML=h+'</tbody></table>';
}

function renderStats(){
  var ms=['节前10日%','节前5日%','T0当日%','T1跳空%','T1当日%','T1日内%','节后5日%','节后10日%','节后20日%','窗口最大回撤%','窗口最大涨幅%','节后10日最高%','节后10日最低%','节前量比','节后量比'];
  $('hx-stats-title').textContent='统计 vs 基准 · '+short(S.g)+' · '+D.variants[S.v];
  var h='<table><thead><tr><th>指标</th><th>n</th><th>均值</th><th>中位数</th><th>胜率</th><th>基准均值</th><th>基准中位</th><th>基准胜率</th><th>均值差</th><th>p</th></tr></thead><tbody>';
  ms.forEach(function(m){var s=stat(S.g,S.v,m);if(!s)return;var vol=VOL.indexOf(m)>=0;
    h+='<tr><td>'+esc(m)+'</td><td class="num">'+s['样本数']+'</td>'+td(s['均值'],2,!vol)+td(s['中位数'],2,!vol)+'<td class="num">'+num(s['胜率%'],0,false)+'%</td>'+
      td(s['基准均值'],2,!vol)+td(s['基准中位数'],2,!vol)+'<td class="num">'+num(s['基准胜率%'],0,false)+'%</td>'+td(s['均值差'])+'<td class="num">'+num(s['p值'],3,false)+'</td></tr>'});
  $('hx-stats').innerHTML=h+'</tbody></table>';
}

function evKey(e){return e['T0']}
function renderEvents(){
  var proxy=(D.meta.proxies[0]||{}).name;
  var cols=['节前5日%','T0当日%','T1跳空%','T1当日%','节后5日%','节后10日%','节后20日%'];
  var list=D.events.filter(function(e){return inGroup(e,S.g,'all')});
  $('hx-etitle').textContent='事件明细 · '+short(S.g)+' · '+list.length+' 次';
  var h='<table><thead><tr><th>年份·节日</th><th>T0</th><th>T1</th><th>休市</th>'+cols.map(function(c){return'<th>'+esc(c.replace('%',''))+'</th>'}).join('')+
    '<th>量比</th>'+(proxy?'<th>'+esc(proxy)+'RS 节前5</th><th>'+esc(proxy)+'RS 节后5</th>':'')+'<th>状态</th></tr></thead><tbody>';
  list.slice().reverse().forEach(function(e){
    var muted=S.v==='ex'&&ex.indexOf(+e['年份'])>=0,col=e['类型']==='合并'?HCOLOR['合并']:(HCOLOR[e['类型']]||'#64748b');
    h+='<tr class="click'+(muted?' muted':'')+(S.focus===evKey(e)?' sel':'')+'" data-ev="'+esc(evKey(e))+'" title="点击在日K上定位"><td><span class="dot" style="background:'+col+'"></span><b>'+e['年份']+'</b> '+esc(e['节日'])+(e['类型']==='合并'?'<span class="tag">合并</span>':'')+'</td><td>'+esc(String(e['T0']).slice(5))+'</td><td>'+esc(String(e['T1']).slice(5))+'</td><td class="num">'+e['休市自然日']+'天</td>';
    cols.forEach(function(c){h+=td(e[c])});
    h+=td(e['节前量比'],2,false);
    if(proxy){h+=td(e['RS_'+proxy+'_节前5日%'])+td(e['RS_'+proxy+'_节后5日%'])}
    h+='<td class="dim">'+esc(e['状态'])+'</td></tr>'});
  $('hx-events').innerHTML=h+'</tbody></table>';
}

/* ---------- ECharts 公共 ---------- */
var charts={},touch={};
function mkChart(id,count,opts){if(!window.echarts)return null;if(!charts[id]){charts[id]=echarts.init($(id));
  if(window.ChartTouch){touch[id]=ChartTouch.bindLongPressScrub(Object.assign({el:$(id),getChart:function(){return charts[id]},getCount:count,delay:330,axisPointerType:'line'},opts||{}))}}
  return charts[id]}
function tipPatch(id,type,show){if(!window.ChartTouch)return{};return ChartTouch.tooltipOption({scrubbing:!!(touch[id]&&touch[id].isActive()),axisPointerType:type||'line',showContent:show!==false})}
function axisStyle(t){return{axisLine:{lineStyle:{color:t.line}},axisLabel:{color:t.muted,fontSize:10},splitLine:{lineStyle:{type:'dashed',color:t.line}}}}
function tipStyle(t){return{backgroundColor:t.card,borderColor:t.line,textStyle:{color:t.text,fontSize:12}}}

function renderPath(){
  var c=mkChart('hx-path',function(){return D.path_range.length});if(!c)return;
  var t=tk(),nw=narrow(),series=[],i=0;
  D.groups.forEach(function(g){if(g.indexOf('长假')===0)return;var p=(D.paths[g]||{})[S.v];if(!p)return;
    var sel=g===S.g;series.push({name:g+'(n='+p.n+')',type:'line',data:p.main,showSymbol:false,
      lineStyle:{width:sel?3.2:1.2,opacity:sel?1:.45},itemStyle:{color:g==='全部'?t.text:(HCOLOR[g]||PAL[i++%PAL.length])},z:sel?5:2,emphasis:{focus:'series'}})});
  if(S.g.indexOf('长假')===0){var pl=D.paths[S.g][S.v];series.push({name:short(S.g)+'(n='+pl.n+')',type:'line',data:pl.main,showSymbol:false,lineStyle:{width:3.2},itemStyle:{color:'#b7791f'},z:6})}
  series.push({name:'基准',type:'line',data:D.baseline_path.main,showSymbol:false,lineStyle:{type:'dashed',width:1.8},itemStyle:{color:t.muted}});
  D.live.forEach(function(l){series.push({name:l.label,type:'line',data:l.main,symbolSize:5,lineStyle:{type:'dotted',width:2.4},itemStyle:{color:'#ff9f1c'},z:7})});
  if(series.length)series[0].markLine={silent:true,symbol:'none',label:{show:false},lineStyle:{color:t.muted,opacity:.5},data:[{xAxis:'T0'}]};
  var ax=axisStyle(t);
  c.setOption({backgroundColor:'transparent',animation:false,grid:{left:nw?38:46,right:12,top:nw?56:44,bottom:26},
    legend:{type:'scroll',top:4,textStyle:{fontSize:nw?10:11,color:t.text},pageTextStyle:{color:t.muted},pageIconColor:t.accent,itemWidth:14,itemHeight:8},
    tooltip:Object.assign({trigger:'axis',confine:true,valueFormatter:function(v){return num(v)+'%'}},tipStyle(t)),
    xAxis:Object.assign({type:'category',data:D.path_range.map(function(k){return k===0?'T0':(k>0?'T+'+k:'T'+k)})},ax,{axisLabel:{color:t.muted,fontSize:10,interval:nw?4:1},splitLine:{show:false}}),
    yAxis:Object.assign({type:'value'},ax,{axisLabel:{color:t.muted,fontSize:10,formatter:'{value}%'}}),series:series},true);
  c.setOption(tipPatch('hx-path'),false);
}

function renderRS(){
  var c=mkChart('hx-rs',function(){return D.rs_cols.length},{axisPointerType:'shadow'});if(!c)return;
  var t=tk(),nw=narrow();
  var series=D.meta.proxies.map(function(p,i){
    return{name:p.name,type:'bar',barMaxWidth:12,data:D.rs_cols.map(function(m){var s=rsStat(p.code,S.g,S.v,m);return s?{value:s['均值'],win:s['胜率%'],n:s['样本数']}:null}),
      itemStyle:{color:['#a78bfa','#38bdf8','#f59e0b','#f472b6'][i%4],borderRadius:[2,2,0,0]}}});
  var bcode=(D.meta.proxies[0]||{}).code;
  if(bcode){series.push({name:'基准·'+D.meta.proxies[0].name,type:'line',data:D.rs_cols.map(function(m){var s=rsStat(bcode,S.g,S.v,m);return s?s['基准均值']:null}),symbolSize:4,lineStyle:{type:'dashed',color:t.muted},itemStyle:{color:t.muted}})}
  var ax=axisStyle(t);
  c.setOption({backgroundColor:'transparent',animation:false,grid:{left:nw?40:46,right:10,top:nw?56:40,bottom:nw?56:34},
    legend:{top:4,textStyle:{fontSize:nw?10:11,color:t.text},itemWidth:12,itemHeight:8},
    tooltip:Object.assign({trigger:'axis',confine:true,formatter:function(ps){var h=esc(ps[0].axisValue);ps.forEach(function(p){var d=p.data||{};var v=typeof d==='object'&&d!==null?d.value:d;
      h+='<br>'+p.marker+esc(p.seriesName)+'：'+num(v)+'pp'+(d&&d.win!=null?'（跑赢 '+num(d.win,0,false)+'%，n='+d.n+'）':'')});return h}},tipStyle(t)),
    xAxis:Object.assign({type:'category',data:D.rs_cols.map(function(m){return m.replace('%','')})},ax,{axisLabel:{color:t.muted,rotate:nw?40:0,fontSize:10,interval:0},splitLine:{show:false}}),
    yAxis:Object.assign({type:'value'},ax,{axisLabel:{color:t.muted,fontSize:10,formatter:'{value}pp'}}),series:series},true);
  c.setOption(tipPatch('hx-rs','shadow'),false);
}

/* ---------- 主指数日 K：逐根查看 ----------
   桌面：悬停 = 十字光标 + 浮层 + 信息条；点击 = 选中该日（黄色竖线），←/→ 逐日移动（超出视窗自动平移）；滚轮缩放、拖动平移、底部滑块。
   手机：轻点 = 选中该日；长按拖动 = 逐根滑看，松手后停在最后一根；双指捏合缩放（至少 20 根）、单指左右拖动平移（chart-touch.js 的 pinchZoom / panX）；上下滑动照常滚动页面。
   信息条常驻图上方：悬停/滑看时显示当前根，否则显示选中日，默认最新交易日。 */
var K=D.kline||{d:[],o:[],h:[],l:[],c:[],v:[]},KI={};K.d.forEach(function(d,i){KI[d]=i});
var KN=K.d.length,KLAST=KI[K.last]!=null?KI[K.last]:KN-1;
var OHLC=K.d.map(function(_,i){return K.o[i]==null?'-':[K.o[i],K.c[i],K.l[i],K.h[i]]});
function ma(n){var out=[],s=0;for(var i=0;i<KN;i++){var c=K.c[i];if(c==null){out.push('-');continue}s+=c;if(i>=n)s-=K.c[i-n]||0;out.push(i>=n-1&&K.c[i-n+1]!=null?+(s/n).toFixed(2):'-')}return out}
var MA5=ma(5),MA20=ma(20);
var HOLMAP={};D.bands.forEach(function(b){HOLMAP[b.t0]=(HOLMAP[b.t0]||[]).concat([b.year+b.label+' T0']);HOLMAP[b.t1]=(HOLMAP[b.t1]||[]).concat([b.year+b.label+' T1'])});
var kzoom=null,KSEL=null,kHover=null,kScrubIdx=null,kScrubEnd=0;
function kDefault(){return narrow()?60:120}
function holTag(d){if(!HOLMAP[d])return'';return'<span class="hol" style="background:'+bandColor(D.bands.filter(function(b){return b.t0===d||b.t1===d})[0])+'">'+esc(HOLMAP[d].join(' / '))+'</span>'}
function kFields(i){
  var pc=i>0?K.c[i-1]:null,chg=pc?(K.c[i]/pc-1)*100:null,amp=pc?(K.h[i]-K.l[i])/pc*100:null;
  return{chg:chg,amp:amp,vol:K.v[i]==null?'—':(K.v[i]/1e4).toFixed(2)+'亿手',ma5:MA5[i]==='-'?'—':MA5[i],ma20:MA20[i]==='-'?'—':MA20[i]};
}
function ktip(i,mode){
  if(!KN){$('hx-ktip').innerHTML='<span class="dim">无日 K 数据</span>';return}
  if(i==null||i<0||i>=KN){i=KSEL!=null?KSEL:KLAST;mode=KSEL!=null?'sel':'last'}
  var tag={hover:'悬停',scrub:'滑看',sel:'已选',last:'最新'}[mode||'hover'];
  var d=K.d[i],wk='日一二三四五六'.charAt(new Date(d+'T00:00:00').getDay());
  var head='<span class="kmode k-'+(mode||'hover')+'">'+tag+'</span><b class="kdate">'+esc(d)+' 周'+wk+'</b>';
  if(K.c[i]==null){$('hx-ktip').innerHTML=head+'<span class="dim">未来交易日（休市日历）</span>'+holTag(d);return}
  var f=kFields(i);
  $('hx-ktip').innerHTML=head+'<span>开 <b>'+K.o[i].toFixed(2)+'</b></span><span>高 <b>'+K.h[i].toFixed(2)+'</b></span><span>低 <b>'+K.l[i].toFixed(2)+'</b></span><span>收 <b class="'+cls(f.chg)+'">'+K.c[i].toFixed(2)+'</b></span>'+
    '<span class="'+cls(f.chg)+'"><b>'+num(f.chg)+'%</b></span><span class="dim">振幅 '+num(f.amp,2,false)+'%</span><span class="dim">量 '+f.vol+'</span>'+
    '<span style="color:#f59e0b">MA5 '+f.ma5+'</span><span style="color:'+tk().accent+'">MA20 '+f.ma20+'</span>'+holTag(d);
}
function kTipHtml(i){
  if(i==null||K.c[i]==null)return esc(K.d[i]||'')+'<br>未来交易日';
  var f=kFields(i),r=function(a,b,c){return'<div style="display:flex;justify-content:space-between;gap:14px"><span style="opacity:.7">'+a+'</span><b'+(c?' style="color:'+c+'"':'')+'>'+b+'</b></div>'};
  var t=tk(),col=f.chg>0?t.up:(f.chg<0?t.down:null);
  return'<div style="font-family:var(--hx-mono);font-size:12px;min-width:150px"><div style="font-weight:700;margin-bottom:3px">'+esc(K.d[i])+'</div>'+r('开',K.o[i].toFixed(2))+r('高',K.h[i].toFixed(2))+r('低',K.l[i].toFixed(2))+r('收',K.c[i].toFixed(2),col)+r('涨跌',num(f.chg)+'%',col)+r('振幅',num(f.amp,2,false)+'%')+r('量',f.vol)+r('MA5',f.ma5,'#f59e0b')+r('MA20',f.ma20,t.accent)+(HOLMAP[K.d[i]]?'<div style="margin-top:3px;color:#f59e0b">'+esc(HOLMAP[K.d[i]].join(' / '))+'</div>':'')+'</div>';
}
function selMark(){return KSEL==null?[]:[{xAxis:K.d[KSEL],label:{show:false}}]}
function selectK(i,opts){
  opts=opts||{};if(i==null||!KN)return;
  i=Math.max(0,Math.min(KLAST,i));KSEL=i;
  var c=charts['hx-kline'];
  if(c){
    if(opts.pan&&kzoom&&(i<kzoom[0]||i>kzoom[1])){var w=kzoom[1]-kzoom[0];var a=i<kzoom[0]?i:i-w;kzoom=[Math.max(0,a),Math.min(KN-1,Math.max(0,a)+w)];c.dispatchAction({type:'dataZoom',startValue:kzoom[0],endValue:kzoom[1]});markZoomBtn(null)}
    c.setOption({series:[{id:'ksel',markLine:{data:selMark()}}]});
    if(opts.tip&&!coarse())c.dispatchAction({type:'showTip',seriesIndex:0,dataIndex:i});
  }
  ktip(i,'sel');
}
function visibleBands(){return D.bands.filter(function(b){return KI[b.t0]!=null&&inGroup(b,S.g,S.v)})}
function kSpan(){if(!kzoom)return KN;return kzoom[1]-kzoom[0]}
function bandAreas(showLabel){
  return visibleBands().map(function(b){var t1=KI[b.t1]!=null?b.t1:K.d[KN-1];var col=bandColor(b);
    return[{name:String(b.year).slice(2)+(b.kind==='合并'?b.tags.join(''):b.label),xAxis:b.t0,itemStyle:{color:col,opacity:.24},label:{show:showLabel,color:col,fontSize:10,fontWeight:700,position:'insideTop',distance:2}},{xAxis:t1}]});
}
function focusMarks(){
  if(!S.focus)return{area:[],line:[]};
  var b=D.bands.filter(function(x){return x.t0===S.focus})[0];if(!b)return{area:[],line:[]};
  var i0=KI[b.t0],w0=Math.max(0,i0-10),w1=Math.min(KN-1,i0+20);
  var line=[{xAxis:b.t0,label:{formatter:'T0',color:'#f59e0b',position:'insideStartTop'}}];if(KI[b.t1]!=null)line.push({xAxis:b.t1,label:{formatter:'T1',color:'#f59e0b',position:'insideStartBottom'}});
  return{area:[[{xAxis:K.d[w0],itemStyle:{color:'rgba(245,158,11,.08)'},label:{show:false}},{xAxis:K.d[w1]}]],line:line};
}
function kIndexAt(c,x,y){
  if(!c.containPixel({gridIndex:[0,1]},[x,y]))return null;
  var p=c.convertFromPixel({gridIndex:0},[x,y]);var i=p&&Math.round(p[0]);return(i==null||!isFinite(i))?null:i;
}
function renderKline(keepZoom){
  var c=mkChart('hx-kline',function(){return KN},{axisPointerType:'cross',showEchartsTipContent:false,pinchZoom:true,panX:true,minSpan:20,
    onGestureEnd:function(){kScrubEnd=Date.now();markZoomBtn(null)},
    onIndex:function(i){kScrubIdx=i;ktip(i,'scrub')},
    onExit:function(){kScrubEnd=Date.now();if(kScrubIdx!=null){var i=kScrubIdx;kScrubIdx=null;selectK(i)}else ktip(null)}});
  if(!c||!KN)return;
  var t=tk(),nw=narrow(),co=coarse(),ax=axisStyle(t),fm=focusMarks();
  if(!kzoom){var n=kDefault();kzoom=[Math.max(0,KLAST-n),KN-1];markZoomBtn(String(n))}
  var showLabel=kSpan()<=900;
  var legend=D.holidays.map(function(h){return'<span><i style="background:'+HCOLOR[h.name]+'"></i>'+esc(h.name)+'</span>'}).join('')+'<span><i style="background:'+HCOLOR['合并']+'"></i>合并休市</span><span><i style="background:rgba(245,158,11,.4)"></i>选中事件 T-10~T+20</span><span><i style="background:#facc15;width:3px"></i>选中日</span>';
  $('hx-kleg').innerHTML=legend;
  $('hx-ktitle').textContent=D.meta.index_name+' 日K · '+short(S.g)+'休市色带（'+visibleBands().length+'）';
  $('hx-khint').textContent=co?'轻点选中一根 K 线；长按后左右拖动逐根查看，松手停在该日；双指捏合缩放、单指左右拖动平移；上下滑动照常滚动页面。':'悬停逐根查看 · 点击选中后用 ← / → 逐日移动 · 滚轮缩放、按住拖动平移、底部滑块调范围。';
  c.setOption({backgroundColor:'transparent',animation:false,
    axisPointer:{link:[{xAxisIndex:'all'}],label:{show:false},lineStyle:{color:t.muted}},
    grid:[{left:nw?44:56,right:nw?8:14,top:12,height:nw?'62%':'65%'},{left:nw?44:56,right:nw?8:14,top:nw?'74%':'77%',height:nw?'12%':'12%'}],
    xAxis:[Object.assign({type:'category',data:K.d,gridIndex:0,boundaryGap:true},ax,{axisLabel:{show:false},splitLine:{show:false}}),
           Object.assign({type:'category',data:K.d,gridIndex:1,boundaryGap:true},ax,{axisLabel:{color:t.muted,fontSize:10,formatter:function(v){return kSpan()<=160?v.slice(5):v.slice(0,7)}},splitLine:{show:false}})],
    yAxis:[Object.assign({scale:true,gridIndex:0,splitNumber:4,position:'left'},ax),Object.assign({gridIndex:1,splitNumber:2},ax,{axisLabel:{color:t.muted,fontSize:9,formatter:function(v){return(v/1e4).toFixed(1)+'亿'}}})],
    dataZoom:[{type:'inside',xAxisIndex:[0,1],startValue:kzoom[0],endValue:kzoom[1],disabled:co,minValueSpan:20,zoomOnMouseWheel:true,moveOnMouseMove:true,moveOnMouseWheel:false},
              {type:'slider',xAxisIndex:[0,1],startValue:kzoom[0],endValue:kzoom[1],bottom:4,height:nw?18:18,showDetail:false,borderColor:t.line,backgroundColor:'transparent',fillerColor:t.dark?'rgba(96,165,250,.16)':'rgba(37,99,235,.10)',handleStyle:{color:t.soft,borderColor:t.muted},moveHandleStyle:{color:t.line},dataBackground:{lineStyle:{color:t.muted,opacity:.5},areaStyle:{color:t.muted,opacity:.12}},textStyle:{color:t.muted},minValueSpan:20}],
    series:[
      {id:'k',name:'日K',type:'candlestick',data:OHLC,xAxisIndex:0,yAxisIndex:0,barMaxWidth:14,itemStyle:{color:t.up,color0:t.down,borderColor:t.up,borderColor0:t.down},
        markArea:{silent:true,data:bandAreas(showLabel).concat(fm.area)},
        markLine:{silent:true,symbol:'none',lineStyle:{color:'#f59e0b',type:'solid',width:1.4},label:{fontSize:10},data:fm.line}},
      {id:'ma5',name:'MA5',type:'line',data:MA5,xAxisIndex:0,yAxisIndex:0,smooth:true,symbol:'none',lineStyle:{width:1.1,color:'#f59e0b'}},
      {id:'ma20',name:'MA20',type:'line',data:MA20,xAxisIndex:0,yAxisIndex:0,smooth:true,symbol:'none',lineStyle:{width:1.1,color:t.accent}},
      {id:'vol',name:'成交量',type:'bar',data:K.v,xAxisIndex:1,yAxisIndex:1,barMaxWidth:14,itemStyle:{opacity:.75,color:function(p){var i=p.dataIndex;return K.c[i]>=K.o[i]?t.up:t.down}}},
      {id:'ksel',name:'选中日',type:'line',data:[],xAxisIndex:0,yAxisIndex:0,silent:true,tooltip:{show:false},
        markLine:{silent:true,symbol:'none',animation:false,lineStyle:{color:'#facc15',type:'solid',width:1.6},data:selMark()}}
    ]},true);
  c.setOption(tipPatch('hx-kline','cross',!co),false);
  if(!co)c.setOption({tooltip:Object.assign({confine:true,padding:[6,9],formatter:function(ps){var p=(ps||[])[0];return p?kTipHtml(p.dataIndex):''},
    axisPointer:{type:'cross',label:{show:true,fontSize:10,backgroundColor:t.dark?'#334155':'#475569',formatter:function(o){return o.axisDimension==='x'?String(o.value):(+o.value).toFixed(o.axisIndex?0:2)}},crossStyle:{color:t.muted}}},tipStyle(t))},false);
  if(!c.__hxBound){c.__hxBound=true;
    c.on('updateAxisPointer',function(ev){if(touch['hx-kline']&&touch['hx-kline'].isActive())return;var a=(ev.axesInfo||[])[0];if(a&&a.value!=null){var i=typeof a.value==='number'?a.value:KI[a.value];if(i!=null){kHover=i;ktip(i,'hover')}}});
    c.on('datazoom',function(){var o=c.getOption().dataZoom[0];if(o&&o.startValue!=null){var was=kSpan()<=900;kzoom=[o.startValue,o.endValue];var now=kSpan()<=900;if(was!==now)c.setOption({series:[{id:'k',markArea:{data:bandAreas(now).concat(focusMarks().area)}}]})}});
    c.getZr().on('globalout',function(){kHover=null;if(!(touch['hx-kline']&&touch['hx-kline'].isActive()))ktip(null)});
    // 点击 / 轻点选中一根（长按滑看结束后的那次 click 忽略）
    c.getZr().on('click',function(e){if(Date.now()-kScrubEnd<450)return;var i=kIndexAt(c,e.offsetX,e.offsetY);if(i!=null){selectK(i);if(!coarse())$('hx-kline').focus({preventScroll:true})}});
    $('hx-kline').addEventListener('keydown',function(e){
      if(e.key!=='ArrowLeft'&&e.key!=='ArrowRight'&&e.key!=='Home'&&e.key!=='End')return;e.preventDefault();
      var base=KSEL!=null?KSEL:(kHover!=null?kHover:KLAST);
      var i=e.key==='Home'?kzoom[0]:e.key==='End'?KLAST:base+(e.key==='ArrowRight'?1:-1)*(e.shiftKey?5:1);
      selectK(i,{pan:true,tip:true});
    });
  }
  if(KSEL!=null)ktip(KSEL,'sel');else ktip(null);
}
function setZoom(a,b){kzoom=[Math.max(0,a),Math.min(KN-1,b)];if(charts['hx-kline']){charts['hx-kline'].dispatchAction({type:'dataZoom',startValue:kzoom[0],endValue:kzoom[1]});renderKline(true)}}
function markZoomBtn(z){document.querySelectorAll('[data-z]').forEach(function(b){b.classList.toggle('on',b.getAttribute('data-z')===z)})}
function zoomPreset(z){
  markZoomBtn(z);
  if(z==='all')return setZoom(0,KN-1);
  if(z==='now'){var up=D.bands.filter(function(b){return b.status!=='完整'&&KI[b.t0]!=null});var first=up.length?KI[up[0].t0]:KLAST;return setZoom(first-30,KN-1)}
  var n=+z;setZoom(KLAST-n,KN-1);
}
function stickyOffset(){
  var bar=document.querySelector('.hx-bar');if(!bar)return 70;
  var top=parseFloat(getComputedStyle(bar).top);return(isFinite(top)?top:0)+bar.offsetHeight+8;
}
function focusEvent(t0){
  S.focus=t0;var b=D.bands.filter(function(x){return x.t0===t0})[0];if(!b||KI[b.t0]==null)return;
  var i0=KI[b.t0],i1=KI[b.t1]!=null?KI[b.t1]:KN-1;
  $('hx-focus').innerHTML='已定位：<b>'+b.year+' '+esc(b.label)+'</b>　T0 '+esc(b.t0)+' → T1 '+esc(b.t1)+'（显示 T-20 ~ T+30，浅色底为 T-10 ~ T+20）';
  kzoom=[Math.max(0,i0-20),Math.min(KN-1,i1+30)];
  KSEL=Math.min(i0,KLAST);
  renderEvents();renderKline(true);
  markZoomBtn(null);
  // 滚动时扣除吸顶导航 + 行情条 + 分组条的高度，避免 K 线被遮住
  window.scrollTo({top:$('hx-kcard').getBoundingClientRect().top+window.scrollY-stickyOffset(),behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'auto':'smooth'});
}

function renderFoot(){
  var m=D.meta;
  $('hx-foot').innerHTML='<p>'+(m.coverage||[]).map(esc).join('<br>')+'</p>'+
    ((m.notes||[]).length?'<p>未形成事件：'+m.notes.map(esc).join('；')+'</p>':'')+
    ((m.missing||[]).length?'<p>缺失指数：'+esc(m.missing.join(', '))+'</p>':'')+
    '<p>“剔除异常年”口径排除事件年份：'+esc(ex.join('、')||'无')+'（2015 股灾 / 2024 年 9 月底政策行情主导小样本均值）。</p>'+
    '<p>指标：节前 N 日 = C(T0)/C(T−N)−1；T1 跳空 = O(T1)/C(T0)−1；节后 N 日 = C(T+N)/C(T0)−1；量比 = T−5..T0 均量 ÷ T−25..T−6 均量；基准 = 全部交易日同口径分布。</p>'+
    '<p>顶部行情条与休市倒计时：生成时由 ticker_payload() 从“当前位置 / 休市日程 / 日 K”计算，默认分组 = 最近一次待到来休市所属节日。</p>'+
    '<p>重跑：<code>'+esc(m.command)+'</code><br>新增节日：在 backtest/holiday_effect.py 的 HOLIDAY_REGISTRY 追加一项 HolidaySpec。</p>';
}
function redrawCharts(){renderPath();renderRS();renderKline(true)}
function render(){chips();renderTicker();renderCurrent();renderSched();renderMatrix();renderPath();renderKline(true);renderRS();renderConcl();renderStats();renderEvents()}
renderFoot();render();
function resizeAll(){Object.keys(charts).forEach(function(k){charts[k].resize()})}
var rt;window.addEventListener('resize',function(){clearTimeout(rt);rt=setTimeout(function(){measureTicker();resizeAll();redrawCharts()},150)});
if(window.ResizeObserver&&$('hx-kline')){var ro,rq;ro=new ResizeObserver(function(){clearTimeout(rq);rq=setTimeout(function(){if(charts['hx-kline'])charts['hx-kline'].resize()},60)});ro.observe($('hx-kline'))}
window.addEventListener('stockapp:layout',function(){setTimeout(function(){measureTicker();resizeAll()},220)});
})();
</script>
</body>
</html>
"""


def build_holiday_page(payload: dict) -> str:
    payload = dict(payload)
    payload.setdefault("ticker", ticker_payload(payload))
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("</", "<\\/")
    return TEMPLATE.replace("__DATA__", data)
