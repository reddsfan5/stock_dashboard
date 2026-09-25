"""节假日效应回测页面（output/holiday_effect.html）。

页面完全由嵌入的一份 JSON 驱动：分组（全部 / 长假 / 各节日 / 合并）、休市色带和沪深300 日 K
都来自回测结果；注册表新增节日后重跑即可自动出现在切换条、热力矩阵、图表、日 K 色带和表格里。

设计要点（参考 modern-web-guidance：size-aware-styling / defer-rendering-heavy-content /
dark-mode / interactions-in-complex-layouts）：
- 颜色全部取 app.css 设计令牌（--app-*），跟随工作台浅色 / 深色主题；ECharts 在主题切换时重绘；
- 卡片内部用容器查询（Baseline 广泛可用）按卡片宽度切换 2 / 4 列指标；
- 首屏以下的大表格和方法说明用 content-visibility:auto + contain-intrinsic-size 延迟渲染
  （不支持的浏览器自动忽略，不影响功能）；图表容器不做延迟，避免 ECharts 初始化尺寸为 0；
- 日 K 与其他页面一致：红涨绿跌、MA 线、成交量副图；移动端复用共享 chart-touch.js，
  普通滑动滚动页面，长按进入十字光标并在信息条显示 OHLC / 涨跌幅 / 成交量。
"""

from __future__ import annotations

import json

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>节假日效应回测</title>
<script>
try{var r=document.documentElement;r.dataset.theme=localStorage.getItem('stockAppTheme')||'light';r.dataset.density=localStorage.getItem('stockAppDensity')||'comfortable';}catch(_){}
</script>
<link rel="stylesheet" href="/assets/app.css">
<style>
:root{color-scheme:light;--hx-blue:var(--app-accent,#2563eb);--hx-up:var(--app-up,#e5484d);--hx-down:var(--app-down,#159568);--hx-line:var(--app-border,#e2e8f0);--hx-card:var(--app-surface,#fff);--hx-soft:var(--app-surface-2,#f8fafc);--hx-text:var(--app-text,#0f172a);--hx-muted:var(--app-muted,#58677d);--hx-radius:14px;--hx-gap:12px}
:root[data-theme="dark"]{color-scheme:dark}
*{box-sizing:border-box}
body{overflow-x:hidden}
.hx-page{max-width:1440px;margin:auto;padding:16px 20px 56px;color:var(--hx-text)}
.num,.hx-page table,.hx-kpi b,.hx-hero-row b{font-variant-numeric:tabular-nums}
.hx-title{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;margin-bottom:12px}
.hx-title h1{font-size:24px;margin:0;letter-spacing:.01em}.hx-title p{margin:4px 0 0;color:var(--hx-muted);font-size:12px;line-height:1.6}
.hx-card{min-width:0;border:1px solid var(--hx-line);border-radius:var(--hx-radius);background:var(--hx-card);padding:14px 16px;margin-bottom:var(--hx-gap);container-type:inline-size;box-shadow:0 1px 2px rgba(15,23,42,.03)}
.hx-card>h2,.hx-head h2{font-size:15px;margin:0;font-weight:700}
.hx-head{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:10px}
.hx-sub{color:var(--hx-muted);font-size:12px}
.note{color:var(--hx-muted);font-size:12px;line-height:1.6;margin:8px 0 0}
/* ---- Hero：今年国庆前 vs 历史 ---- */
.hx-hero{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(0,1fr);gap:var(--hx-gap)}
.hx-hero .hx-card{margin-bottom:0}
.hx-hero-main{background:linear-gradient(135deg,color-mix(in srgb,var(--hx-blue) 9%,var(--hx-card)),var(--hx-card) 60%)}
.hx-kicker{font-size:11px;font-weight:750;letter-spacing:.08em;color:var(--hx-blue)}
.hx-hero h2{font-size:19px;margin:3px 0 4px}
.hx-hero-rows{display:grid;gap:9px;margin-top:10px}
.hx-hero-row{display:grid;grid-template-columns:minmax(96px,1.1fr) 76px minmax(120px,2fr);align-items:center;gap:10px;font-size:12px}
.hx-hero-row .lab{color:var(--hx-muted);line-height:1.3}.hx-hero-row .lab small{display:block;font-size:10px}
.hx-hero-row b{font-size:17px;text-align:right}
.hx-pbar{position:relative;height:22px;border-radius:999px;background:linear-gradient(90deg,color-mix(in srgb,var(--hx-down) 22%,transparent),var(--hx-soft) 50%,color-mix(in srgb,var(--hx-up) 22%,transparent));border:1px solid var(--hx-line)}
.hx-pbar i{position:absolute;top:-3px;width:4px;height:26px;border-radius:3px;background:var(--hx-text);transform:translateX(-2px)}
.hx-pbar em{position:absolute;left:50%;top:0;bottom:0;border-left:1px dashed var(--hx-muted);opacity:.5}
.hx-pbar span.l{right:auto;left:8px}.hx-pbar span{position:absolute;right:8px;top:3px;font-size:10px;color:var(--hx-muted);font-style:normal}
.hx-timeline{display:grid;gap:8px;margin:6px 0 10px}
.hx-tl{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:9px 10px;border-radius:10px;background:var(--hx-soft);border:1px solid var(--hx-line);font-size:12px}
.hx-tl b{font-size:13px}.hx-tl .cd{font-size:20px;font-weight:800;color:var(--hx-blue)}
.hx-facts{margin:0;padding-left:16px;font-size:12px;line-height:1.65}
/* ---- 吸顶分组切换 ---- */
.hx-bar{position:sticky;top:calc(var(--app-nav-h,48px) + var(--app-clock-h,0px) + 2px);z-index:20;display:flex;gap:10px;align-items:center;margin:12px 0;padding:7px 8px;border:1px solid var(--hx-line);border-radius:12px;background:color-mix(in srgb,var(--hx-card) 92%,transparent);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);box-shadow:0 4px 16px rgba(15,23,42,.06)}
.hx-chips{display:flex;gap:6px;overflow-x:auto;scrollbar-width:none;flex:1 1 auto;min-width:0;overscroll-behavior-x:contain}.hx-chips::-webkit-scrollbar{display:none}
.hx-chip{flex:0 0 auto;display:inline-flex;align-items:center;gap:5px;border:1px solid var(--hx-line);background:var(--hx-card);color:var(--hx-text);border-radius:999px;padding:6px 12px;font-size:13px;font-weight:650;cursor:pointer}
.hx-chip i{width:8px;height:8px;border-radius:50%;display:inline-block}
.hx-chip small{opacity:.7;font-weight:500;font-variant-numeric:tabular-nums}
.hx-chip.active{background:var(--hx-text);border-color:var(--hx-text);color:var(--hx-card)}
.hx-seg{display:inline-flex;border:1px solid var(--hx-line);border-radius:9px;overflow:hidden;flex:0 0 auto;background:var(--hx-soft)}
.hx-seg button{border:0;background:transparent;color:var(--hx-text);padding:6px 10px;font-size:12px;cursor:pointer}.hx-seg button.active{background:var(--hx-blue);color:#fff}
/* ---- 通用栅格 / 指标 ---- */
.hx-grid{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);gap:var(--hx-gap)}
.hx-kpis{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
@container (min-width:520px){.hx-kpis{grid-template-columns:repeat(4,minmax(0,1fr))}}
.hx-kpi{border:1px solid var(--hx-line);border-radius:11px;padding:9px 10px;background:var(--hx-soft)}.hx-kpi span{display:block;font-size:11px;color:var(--hx-muted)}.hx-kpi b{font-size:20px;display:block;margin:2px 0}.hx-kpi small{font-size:11px;color:var(--hx-muted)}
details.hx-more{margin-top:10px;border-top:1px dashed var(--hx-line);padding-top:8px}
details.hx-more summary{cursor:pointer;font-size:12px;font-weight:650;color:var(--hx-blue);list-style:none}
details.hx-more summary::-webkit-details-marker{display:none}
details.hx-more summary::after{content:' ▾'}details.hx-more[open] summary::after{content:' ▴'}
.hx-concl li{margin:0 0 6px;line-height:1.65;font-size:12.5px}.hx-concl ul{padding-left:18px;margin:8px 0 0}
/* ---- 表格 ---- */
.tw{overflow:auto;-webkit-overflow-scrolling:touch;border:1px solid var(--hx-line);border-radius:10px;max-width:100%}
.tw.tall{max-height:560px}
table{border-collapse:separate;border-spacing:0;width:100%;font-size:12px;background:var(--hx-card)}
th,td{padding:7px 8px;border-bottom:1px solid var(--hx-line);text-align:right;white-space:nowrap}
th{background:var(--hx-soft);color:var(--hx-muted);font-weight:650;position:sticky;top:0;z-index:2}
th:first-child,td:first-child{text-align:left;position:sticky;left:0;z-index:1;background:var(--hx-card);box-shadow:1px 0 0 var(--hx-line)}
th:first-child{z-index:3;background:var(--hx-soft)}
tr.sel td{background:color-mix(in srgb,var(--hx-blue) 10%,var(--hx-card))}
tr.muted td{opacity:.45}
tr.click{cursor:pointer}tr.click:hover td{background:color-mix(in srgb,var(--hx-blue) 6%,var(--hx-card))}
.up{color:var(--hx-up)}.down{color:var(--hx-down)}.dim{color:var(--hx-muted)}
.tag{display:inline-block;font-size:10px;padding:1px 6px;border-radius:999px;border:1px solid var(--hx-line);margin-left:4px;vertical-align:1px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;vertical-align:0}
.cell2{display:block;font-size:10px;color:var(--hx-muted);font-weight:500}
/* 热力矩阵 */
.hx-heat td.h{font-weight:700;min-width:64px}
.hx-heat td.h b{display:block;font-size:12.5px}
.hx-legend{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--hx-muted)}
.hx-legend i{display:inline-block;width:64px;height:10px;border-radius:5px;background:linear-gradient(90deg,rgba(21,149,104,.55),transparent,rgba(229,72,77,.55))}
/* ---- 图表 ---- */
.hx-chart{width:100%;height:380px}
.hx-kchart{width:100%;height:500px}
.hx-ktools{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.hx-btn{border:1px solid var(--hx-line);background:var(--hx-card);color:var(--hx-text);border-radius:8px;padding:5px 10px;font-size:12px;cursor:pointer}
.hx-btn:hover{border-color:var(--hx-blue)}.hx-btn.on,.hx-btn.primary{background:var(--hx-blue);border-color:var(--hx-blue);color:#fff}
.hx-kleg{display:flex;flex-wrap:wrap;gap:10px;font-size:11px;color:var(--hx-muted);margin:2px 0 8px}
.hx-kleg span{display:inline-flex;align-items:center;gap:4px}.hx-kleg i{width:14px;height:10px;border-radius:3px;display:inline-block;opacity:.75}
.hx-ktip{min-height:34px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:6px 10px;border-radius:9px;background:var(--hx-soft);border:1px solid var(--hx-line);font-size:12px;font-variant-numeric:tabular-nums;margin-bottom:6px}
.hx-ktip b{font-size:13px}.hx-ktip .hol{font-weight:700;padding:1px 7px;border-radius:999px;color:#fff;font-size:11px}
.hx-focus{font-size:12px;color:var(--hx-muted);margin:0 0 6px}
#hx-kcard{scroll-margin-top:calc(var(--app-nav-h,48px) + var(--app-clock-h,0px) + 70px)}
.hx-defer{content-visibility:auto;contain-intrinsic-size:auto none auto 640px}
.hx-foot{color:var(--hx-muted);font-size:12px;line-height:1.75}
.hx-foot code{font-size:11px;word-break:break-all}
.hx-disclaimer{margin-top:10px;padding:9px 12px;border-radius:10px;border:1px solid color-mix(in srgb,#b7791f 40%,var(--hx-line));background:color-mix(in srgb,#b7791f 8%,var(--hx-card));font-size:12px;color:var(--hx-text)}
@media(max-width:1000px){.hx-hero,.hx-grid{grid-template-columns:1fr}}
@media(max-width:700px){
  .hx-page{padding:10px 10px 44px}.hx-title h1{font-size:20px}
  .hx-card{padding:12px;border-radius:12px}
  .hx-chart{height:300px}.hx-kchart{height:420px}
  .hx-hero h2{font-size:17px}.hx-hero-row{grid-template-columns:minmax(84px,1fr) 64px minmax(96px,1.6fr);gap:8px}.hx-hero-row b{font-size:15px}
  /* 手机：切换条压成一行，避免吸顶时遮住图表 */
  .hx-bar{gap:6px;padding:5px}.hx-chip{padding:5px 9px;font-size:12px}.hx-seg button{padding:5px 7px;font-size:11px}.hx-exy{display:none}
  .hx-ktip{font-size:11px;gap:7px}
}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
</style>
</head>
<body>
<main class="hx-page">
  <div class="hx-title"><div><h1>节假日效应回测</h1><p id="hx-sub"></p></div></div>
  <section class="hx-hero" aria-label="今年节前位置">
    <div class="hx-card hx-hero-main" id="hx-hero"></div>
    <div class="hx-card" id="hx-hero-side"></div>
  </section>
  <div class="hx-bar">
    <div class="hx-chips" id="hx-chips" role="tablist" aria-label="节日分组"></div>
    <div class="hx-seg" id="hx-variant" aria-label="年份口径"></div>
  </div>
  <section class="hx-grid">
    <div class="hx-card"><div class="hx-head"><h2 id="hx-concl-title">结论</h2></div><div id="hx-concl"></div></div>
    <div class="hx-card"><div class="hx-head"><h2 id="hx-cur-title">当前位置</h2></div><div id="hx-current"></div></div>
  </section>
  <section class="hx-card"><div class="hx-head"><h2>总览热力矩阵</h2><span class="hx-legend">跌 <i></i> 涨 · 蓝色越深缩量越多</span></div>
    <div class="tw" id="hx-matrix"></div>
    <p class="note">单元格：均值（大字）/ 胜率（小字）；颜色按列内最大绝对值缩放。点击行切换分组。节日分组含带该标签的合并休市；RS = 代理指数收益 − 沪深300（百分点），胜率 = 跑赢占比；量比胜率 = 缩量(&lt;1)占比。</p></section>
  <section class="hx-card"><div class="hx-head"><h2>平均累计路径（T-10 ~ T+20）</h2><span class="hx-sub">以 T0 收盘为 0 · T+1 为复牌首日</span></div>
    <div class="hx-chart" id="hx-path"></div>
    <p class="note">当前分组加粗；灰色虚线为基准（全部交易日）；橙色点线为进行中的 2026 休市。手机上下滑动页面，长按图表查看数值。</p></section>
  <section class="hx-card" id="hx-kcard"><div class="hx-head"><h2 id="hx-ktitle">沪深300 日 K · 休市色带</h2>
      <div class="hx-ktools"><button class="hx-btn on" data-z="250">近1年</button><button class="hx-btn" data-z="750">近3年</button><button class="hx-btn" data-z="all">全部</button><button class="hx-btn" data-z="now" title="跳到 2026 中秋/国庆">当前</button></div></div>
    <div class="hx-kleg" id="hx-kleg"></div>
    <p class="hx-focus" id="hx-focus">点击下方“事件明细”任一行，在日 K 上定位到该次休市（T-20 ~ T+30）。</p>
    <div class="hx-ktip" id="hx-ktip"></div>
    <div class="hx-kchart" id="hx-kline"></div>
    <p class="note">色带 = 休市前最后交易日 T0 到复牌首日 T1；只显示当前分组的休市。红涨绿跌，MA5 / MA20；成交量单位万手。手机上长按日 K 显示十字光标与信息条。</p></section>
  <section class="hx-card"><div class="hx-head"><h2>风险偏好：相对强弱</h2><span class="hx-sub">&gt;0 小盘 / 成长跑赢沪深300</span></div>
    <div class="hx-chart" id="hx-rs"></div></section>
  <section class="hx-card hx-defer"><div class="hx-head"><h2>统计 vs 基准</h2></div><div class="tw" id="hx-stats"></div>
    <p class="note">基准 = 样本期内每个交易日都当作 T0 的同口径分布。p 值为自助抽样双侧近似，窗口重叠且事件少，仅供参考。</p></section>
  <section class="hx-card hx-defer"><div class="hx-head"><h2>事件明细</h2><span class="hx-sub">点击行在日 K 上定位</span></div><div class="tw tall" id="hx-events"></div>
    <p class="note">量比 = T-5..T0 平均成交量 ÷ T-25..T-6 平均成交量。灰色行为“剔除异常年”口径下被排除的事件。</p></section>
  <section class="hx-card hx-defer"><details class="hx-more" style="border:0;margin:0;padding:0"><summary>方法、数据口径与重跑方式</summary><div class="hx-foot" id="hx-foot"></div></details>
    <div class="hx-disclaimer">以上为历史统计关联，不构成投资建议。</div></section>
</main>
<script id="holiday-data" type="application/json">__DATA__</script>
<script src="/vendor/echarts.min.js"></script>
<script src="/assets/app-shell.js"></script>
<script src="/assets/chart-touch.js"></script>
<script>
(function(){
'use strict';
var D=JSON.parse(document.getElementById('holiday-data').textContent);
var S={g:D.groups[0],v:'all',focus:null};
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
  return{text:g('--app-text','#0f172a'),muted:g('--app-muted','#58677d'),line:g('--app-border','#e2e8f0'),card:g('--app-surface','#fff'),up:'#d32f2f',down:'#34a853'}}
function coarse(){return window.ChartTouch&&ChartTouch.isCoarse?ChartTouch.isCoarse():matchMedia('(pointer:coarse),(max-width:760px)').matches}
function narrow(){return matchMedia('(max-width:700px)').matches}

$('hx-sub').textContent=D.meta.index_name+' · 样本 '+D.meta.sample+' · 生成 '+D.meta.generated+' · 休市由节日注册表 × 交易日历空档识别，合并休市只计一次';

/* ---------- Hero：今年国庆前 vs 历史 ---------- */
function renderHero(){
  var hg=D.groups.indexOf('国庆')>=0?'国庆':D.groups[0];
  var rows=((D.current[hg]||{})[S.v])||[];
  var pick=function(item,m){for(var i=0;i<rows.length;i++){if(rows[i]['项目'].indexOf(item)===0&&rows[i]['指标']===m)return rows[i]}return null};
  var items=[[D.meta.index_name,'节前5日%','近5日'],[D.meta.index_name,'节前10日%','近10日'],[D.meta.index_name,'T0当日%','最新一日'],[D.meta.index_name,'节前量比','量比(6日/20日)'],['中证1000','节前10日%','中证1000 − 300 近10日'],['科创50','节前10日%','科创50 − 300 近10日']];
  var h='<div class="hx-kicker">今年节前位置 · 截至 '+esc(D.meta.last_date)+'</div><h2>今年国庆前 vs 历史国庆节前（'+esc(D.variants[S.v])+'）</h2>'+
    '<div class="hx-sub">竖线 = 当前值在历史'+esc(hg)+'节前分布中的分位；虚线 = 中位。</div><div class="hx-hero-rows">';
  items.forEach(function(it){var r=pick(it[0],it[1]);if(!r)return;var vol=VOL.indexOf(it[1])>=0,p=r['历史分位%'];
    h+='<div class="hx-hero-row"><div class="lab">'+esc(it[2])+'<small>历史中位 '+num(r['历史节前中位数'],2,!vol)+(vol?'':'%')+' · n='+r['样本数']+'</small></div>'+
      '<b class="'+(vol?'':cls(r['当前值']))+'">'+num(r['当前值'],2,!vol)+(vol?'':(it[2].indexOf('−')>=0?'pp':'%'))+'</b>'+
      '<div class="hx-pbar" title="历史分位 '+num(p,0,false)+'%"><em></em>'+(p!=null?'<i style="left:'+Math.max(1,Math.min(99,p))+'%"></i>':'')+'<span'+(p!=null&&p>70?' class="l"':'')+'>'+num(p,0,false)+'%</span></div></div>'});
  $('hx-hero').innerHTML=h+'</div>';
  var up=D.meta.upcoming||[],sh='<div class="hx-kicker">休市日程</div><div class="hx-timeline">';
  up.forEach(function(u){sh+='<div class="hx-tl"><div><b>'+esc(u['节日'])+'</b><div class="hx-sub">T0 '+esc(u['T0'])+' → T1 '+esc(u['T1'])+' · '+esc(u['状态'])+'</div></div><div style="text-align:right"><span class="cd">'+u['距T0交易日']+'</span><div class="hx-sub">个交易日到 T0</div></div></div>'});
  sh+='</div><div class="hx-kicker" style="margin-bottom:4px">跨节日要点（'+esc(D.variants[S.v])+'）</div><ul class="hx-facts">';
  var f=[],a=stat('全部',S.v,'节前量比');if(a)f.push('全部休市节前缩量占比 <b>'+num(a['胜率%'],0,false)+'%</b>（平时 '+num(a['基准胜率%'],0,false)+'%）');
  var lg=D.groups.filter(function(g){return g.indexOf('长假')===0})[0];
  if(lg){var p1=rsStat('sh000852',lg,S.v,'节前5日%'),p2=rsStat('sh000852',lg,S.v,'节后5日%');if(p1&&p2)f.push('长假中证1000：节前5日 <b class="'+cls(p1['均值'])+'">'+num(p1['均值'])+'pp</b>（跑赢 '+num(p1['胜率%'],0,false)+'%），节后5日 <b class="'+cls(p2['均值'])+'">'+num(p2['均值'])+'pp</b>（跑赢 '+num(p2['胜率%'],0,false)+'%）')}
  var t1=stat('全部',S.v,'T1跳空%');if(t1)f.push('复牌跳空均值 <b class="'+cls(t1['均值'])+'">'+num(t1['均值'])+'%</b>，胜率 '+num(t1['胜率%'],0,false)+'%（平时 '+num(t1['基准胜率%'],0,false)+'%）');
  $('hx-hero-side').innerHTML=sh+f.map(function(x){return'<li>'+x+'</li>'}).join('')+'</ul>';
}

function chips(){
  var h='';D.groups.forEach(function(g){
    var n=D.events.filter(function(e){return inGroup(e,g,S.v)&&e['状态']==='完整'}).length;
    var dot=HCOLOR[g]?'<i style="background:'+HCOLOR[g]+'"></i>':'';
    h+='<button class="hx-chip'+(g===S.g?' active':'')+'" data-g="'+esc(g)+'" role="tab" aria-selected="'+(g===S.g)+'">'+dot+esc(g)+'<small>'+n+'</small></button>'});
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

function renderConcl(){
  var lines=(D.conclusions[S.g]||{})[S.v]||[];
  $('hx-concl-title').textContent='结论 · '+S.g+' · '+D.variants[S.v];
  var k=['节前5日%','T0当日%','T1跳空%','节后10日%'],kh='';
  k.forEach(function(m){var s=stat(S.g,S.v,m);if(!s)return;
    kh+='<div class="hx-kpi"><span>'+esc(m.replace('%',''))+'</span><b class="'+cls(s['均值'])+'">'+num(s['均值'])+'%</b><small>胜率 '+num(s['胜率%'],0,false)+'% · 基准 '+num(s['基准均值'])+'%</small></div>'});
  $('hx-concl').innerHTML='<div class="hx-kpis">'+kh+'</div><details class="hx-more"><summary>展开文字结论（'+lines.length+' 条）</summary><div class="hx-concl"><ul>'+lines.map(function(x){return'<li>'+esc(x)+'</li>'}).join('')+'</ul></div></details>';
}

function renderCurrent(){
  var rows=((D.current[S.g]||{})[S.v])||[];
  $('hx-cur-title').textContent='当前位置 · 对比'+S.g+'节前';
  var h='<div class="tw"><table><thead><tr><th>项目</th><th>当前</th><th>历史均值</th><th>中位数</th><th>历史分位</th><th>平时分位</th></tr></thead><tbody>';
  rows.forEach(function(r){var vol=VOL.indexOf(r['指标'])>=0;
    h+='<tr><td>'+esc(r['项目'])+'<span class="cell2">'+esc(r['指标'])+' · n='+r['样本数']+'</span></td>'+
      td(r['当前值'],2,!vol)+td(r['历史节前均值'],2,!vol)+td(r['历史节前中位数'],2,!vol)+
      '<td class="num"><b>'+num(r['历史分位%'],0,false)+'%</b></td><td class="num">'+num(r['基准分位%'],0,false)+'%</td></tr>'});
  $('hx-current').innerHTML=h+'</tbody></table></div><p class="note">以 '+esc(D.meta.last_date)+'（本地缓存最新交易日）视作 T0。</p>';
}

function renderMatrix(){
  var cols=D.matrix_cols.concat(D.matrix_rs);
  var rows=D.matrix.filter(function(r){return r['口径']===S.v});
  var scale={};cols.forEach(function(c){var vol=VOL.indexOf(c)>=0,mx=0;rows.forEach(function(r){var m=r[c+'·均值'];if(m!=null)mx=Math.max(mx,Math.abs(vol?1-m:m))});scale[c]=mx||1});
  var h='<table class="hx-heat"><thead><tr><th>分组</th><th>n</th>'+cols.map(function(c){return'<th>'+esc(c.replace('%',''))+'</th>'}).join('')+'</tr></thead><tbody>';
  rows.forEach(function(r){
    var dot=HCOLOR[r['分组']]?'<span class="dot" style="background:'+HCOLOR[r['分组']]+'"></span>':'';
    h+='<tr class="click'+(r['分组']===S.g?' sel':'')+'" data-g="'+esc(r['分组'])+'"><td><b>'+dot+esc(r['分组'])+'</b></td><td class="num">'+r['样本数']+'</td>';
    cols.forEach(function(c){var vol=VOL.indexOf(c)>=0,m=r[c+'·均值'],w=r[c+'·胜率'],bg='';
      if(m!=null){var x=vol?(1-m):m,a=Math.min(Math.abs(x)/scale[c],1)*0.5+0.04;
        bg=vol?(x>0?'rgba(37,99,235,'+a.toFixed(3)+')':'transparent'):(x>0?'rgba(229,72,77,'+a.toFixed(3)+')':'rgba(21,149,104,'+a.toFixed(3)+')')}
      h+='<td class="h num" style="background:'+bg+'"><b>'+num(m,2,!vol)+'</b><span class="cell2">'+num(w,0,false)+'%</span></td>'});
    h+='</tr>'});
  $('hx-matrix').innerHTML=h+'</tbody></table>';
}

function renderStats(){
  var ms=['节前10日%','节前5日%','T0当日%','T1跳空%','T1当日%','T1日内%','节后5日%','节后10日%','节后20日%','窗口最大回撤%','窗口最大涨幅%','节后10日最高%','节后10日最低%','节前量比','节后量比'];
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
  var h='<table><thead><tr><th>年份·节日</th><th>T0</th><th>T1</th><th>休市</th>'+cols.map(function(c){return'<th>'+esc(c.replace('%',''))+'</th>'}).join('')+
    '<th>量比</th>'+(proxy?'<th>'+esc(proxy)+'RS 节前5</th><th>'+esc(proxy)+'RS 节后5</th>':'')+'<th>状态</th></tr></thead><tbody>';
  D.events.filter(function(e){return inGroup(e,S.g,'all')}).slice().reverse().forEach(function(e){
    var muted=S.v==='ex'&&ex.indexOf(+e['年份'])>=0,col=e['类型']==='合并'?HCOLOR['合并']:(HCOLOR[e['类型']]||'#64748b');
    h+='<tr class="click'+(muted?' muted':'')+(S.focus===evKey(e)?' sel':'')+'" data-ev="'+esc(evKey(e))+'" title="点击在日K上定位"><td><span class="dot" style="background:'+col+'"></span><b>'+e['年份']+'</b> '+esc(e['节日'])+(e['类型']==='合并'?'<span class="tag">合并</span>':'')+'</td><td>'+esc(e['T0'])+'</td><td>'+esc(e['T1'])+'</td><td class="num">'+e['休市自然日']+'天</td>';
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

function renderPath(){
  var c=mkChart('hx-path',function(){return D.path_range.length});if(!c)return;
  var t=tk(),nw=narrow(),series=[],i=0;
  D.groups.forEach(function(g){if(g.indexOf('长假')===0)return;var p=(D.paths[g]||{})[S.v];if(!p)return;
    var sel=g===S.g;series.push({name:g+'(n='+p.n+')',type:'line',data:p.main,showSymbol:false,
      lineStyle:{width:sel?3.4:1.3,opacity:sel?1:.5},itemStyle:{color:g==='全部'?t.text:(HCOLOR[g]||PAL[i++%PAL.length])},z:sel?5:2,emphasis:{focus:'series'}})});
  if(S.g.indexOf('长假')===0){var pl=D.paths[S.g][S.v];series.push({name:S.g+'(n='+pl.n+')',type:'line',data:pl.main,showSymbol:false,lineStyle:{width:3.4},itemStyle:{color:'#b7791f'},z:6})}
  series.push({name:'基准',type:'line',data:D.baseline_path.main,showSymbol:false,lineStyle:{type:'dashed',width:2},itemStyle:{color:t.muted}});
  D.live.forEach(function(l){series.push({name:l.label,type:'line',data:l.main,symbolSize:5,lineStyle:{type:'dotted',width:2.6},itemStyle:{color:'#ff7f0e'},z:7})});
  series[0].markLine={silent:true,symbol:'none',label:{show:false},lineStyle:{color:t.muted,opacity:.5},data:[{xAxis:'T0'}]};
  var ax=axisStyle(t);
  c.setOption({backgroundColor:'transparent',animation:false,grid:{left:nw?38:48,right:12,top:nw?58:48,bottom:28},
    legend:{type:'scroll',top:0,textStyle:{fontSize:nw?10:12,color:t.text},pageTextStyle:{color:t.muted}},
    tooltip:{trigger:'axis',confine:true,valueFormatter:function(v){return num(v)+'%'}},
    xAxis:Object.assign({type:'category',data:D.path_range.map(function(k){return k===0?'T0':(k>0?'T+'+k:'T'+k)})},ax,{axisLabel:{color:t.muted,fontSize:10,interval:nw?4:1},splitLine:{show:false}}),
    yAxis:Object.assign({type:'value'},ax,{axisLabel:{color:t.muted,fontSize:10,formatter:'{value}%'}}),series:series},true);
  c.setOption(tipPatch('hx-path'),false);
}

function renderRS(){
  var c=mkChart('hx-rs',function(){return D.rs_cols.length},{axisPointerType:'shadow'});if(!c)return;
  var t=tk(),nw=narrow();
  var series=D.meta.proxies.map(function(p,i){
    return{name:p.name,type:'bar',barMaxWidth:14,data:D.rs_cols.map(function(m){var s=rsStat(p.code,S.g,S.v,m);return s?{value:s['均值'],win:s['胜率%'],n:s['样本数']}:null}),
      itemStyle:{color:['#e5484d','#f59e0b','#2563eb','#7c3aed'][i%4],borderRadius:[3,3,0,0]}}});
  var bcode=(D.meta.proxies[0]||{}).code;
  if(bcode){series.push({name:'基准·'+D.meta.proxies[0].name,type:'line',data:D.rs_cols.map(function(m){var s=rsStat(bcode,S.g,S.v,m);return s?s['基准均值']:null}),symbolSize:4,lineStyle:{type:'dashed',color:t.muted},itemStyle:{color:t.muted}})}
  var ax=axisStyle(t);
  c.setOption({backgroundColor:'transparent',animation:false,grid:{left:nw?40:48,right:10,top:nw?58:40,bottom:nw?56:36},
    legend:{top:0,textStyle:{fontSize:nw?10:12,color:t.text}},
    tooltip:{trigger:'axis',confine:true,formatter:function(ps){var h=esc(ps[0].axisValue);ps.forEach(function(p){var d=p.data||{};var v=typeof d==='object'&&d!==null?d.value:d;
      h+='<br>'+p.marker+esc(p.seriesName)+'：'+num(v)+'pp'+(d&&d.win!=null?'（跑赢 '+num(d.win,0,false)+'%，n='+d.n+'）':'')});return h}},
    xAxis:Object.assign({type:'category',data:D.rs_cols.map(function(m){return m.replace('%','')})},ax,{axisLabel:{color:t.muted,rotate:nw?40:0,fontSize:10,interval:0},splitLine:{show:false}}),
    yAxis:Object.assign({type:'value'},ax,{axisLabel:{color:t.muted,fontSize:10,formatter:'{value}pp'}}),series:series},true);
  c.setOption(tipPatch('hx-rs','shadow'),false);
}

/* ---------- 沪深300 日 K ---------- */
var K=D.kline,KI={};K.d.forEach(function(d,i){KI[d]=i});
var KN=K.d.length,KLAST=KI[K.last];
var OHLC=K.d.map(function(_,i){return K.o[i]==null?'-':[K.o[i],K.c[i],K.l[i],K.h[i]]});
function ma(n){var out=[],s=0;for(var i=0;i<KN;i++){var c=K.c[i];if(c==null){out.push('-');continue}s+=c;if(i>=n)s-=K.c[i-n]||0;out.push(i>=n-1&&K.c[i-n+1]!=null?+(s/n).toFixed(2):'-')}return out}
var MA5=ma(5),MA20=ma(20);
var HOLMAP={};D.bands.forEach(function(b){HOLMAP[b.t0]=(HOLMAP[b.t0]||[]).concat([b.year+b.label+' T0']);HOLMAP[b.t1]=(HOLMAP[b.t1]||[]).concat([b.year+b.label+' T1'])});
var kzoom=null;
function ktip(i){
  if(i==null||i<0||i>=KN)i=KLAST;
  var d=K.d[i],hol=HOLMAP[d]?'<span class="hol" style="background:'+bandColor(D.bands.filter(function(b){return b.t0===d||b.t1===d})[0])+'">'+esc(HOLMAP[d].join(' / '))+'</span>':'';
  if(K.c[i]==null){$('hx-ktip').innerHTML='<b>'+esc(d)+'</b><span class="dim">未来交易日（休市日历）</span>'+hol;return}
  var pc=i>0?K.c[i-1]:null,chg=pc?(K.c[i]/pc-1)*100:null;
  $('hx-ktip').innerHTML='<b>'+esc(d)+'</b><span>开 '+K.o[i].toFixed(2)+'</span><span>高 '+K.h[i].toFixed(2)+'</span><span>低 '+K.l[i].toFixed(2)+'</span><span>收 <b class="'+cls(chg)+'">'+K.c[i].toFixed(2)+'</b></span><span class="'+cls(chg)+'">'+num(chg)+'%</span><span class="dim">量 '+(K.v[i]==null?'—':(K.v[i]/1e4).toFixed(2)+'亿手')+'</span>'+hol;
}
function visibleBands(){return D.bands.filter(function(b){return KI[b.t0]!=null&&inGroup(b,S.g,S.v)})}
function kSpan(){if(!kzoom)return KN;return kzoom[1]-kzoom[0]}
function bandAreas(showLabel){
  var t=tk();
  return visibleBands().map(function(b){var t1=KI[b.t1]!=null?b.t1:K.d[KN-1];var col=bandColor(b);
    return[{name:String(b.year).slice(2)+(b.kind==='合并'?'中秋国庆':b.label),xAxis:b.t0,itemStyle:{color:col,opacity:.22},label:{show:showLabel,color:col,fontSize:10,fontWeight:700,position:'insideTop',distance:2}},{xAxis:t1}]});
}
function focusMarks(){
  if(!S.focus)return{area:[],line:[]};
  var b=D.bands.filter(function(x){return x.t0===S.focus})[0];if(!b)return{area:[],line:[]};
  var i0=KI[b.t0],i1=KI[b.t1]!=null?KI[b.t1]:KN-1,w0=Math.max(0,i0-10),w1=Math.min(KN-1,i0+20);
  var line=[{xAxis:b.t0,label:{formatter:'T0',color:'#b7791f',position:'insideStartTop'}}];if(KI[b.t1]!=null)line.push({xAxis:b.t1,label:{formatter:'T1',color:'#b7791f',position:'insideStartBottom'}});
  return{area:[[{xAxis:K.d[w0],itemStyle:{color:'rgba(183,121,31,.08)'},label:{show:false}},{xAxis:K.d[w1]}]],line:line};
}
function renderKline(keepZoom){
  var c=mkChart('hx-kline',function(){return KN},{axisPointerType:'cross',showEchartsTipContent:false,onIndex:function(i){ktip(i)},onExit:function(){ktip(null)}});if(!c)return;
  var t=tk(),nw=narrow(),co=coarse(),ax=axisStyle(t),fm=focusMarks();
  if(!kzoom||!keepZoom){if(!kzoom)kzoom=[Math.max(0,KLAST-250),KN-1]}
  var showLabel=kSpan()<=900;
  var legend=D.holidays.map(function(h){return'<span><i style="background:'+HCOLOR[h.name]+'"></i>'+esc(h.name)+'</span>'}).join('')+'<span><i style="background:'+HCOLOR['合并']+'"></i>中秋+国庆合并</span><span><i style="background:rgba(183,121,31,.35)"></i>选中事件 T-10~T+20</span>';
  $('hx-kleg').innerHTML=legend;
  $('hx-ktitle').textContent=D.meta.index_name+' 日 K · '+S.g+'休市色带（'+visibleBands().length+'）';
  c.setOption({backgroundColor:'transparent',animation:false,
    axisPointer:{link:[{xAxisIndex:'all'}],label:{show:false}},
    grid:[{left:nw?44:56,right:nw?8:14,top:12,height:nw?'62%':'64%'},{left:nw?44:56,right:nw?8:14,top:nw?'74%':'76%',height:nw?'12%':'13%'}],
    xAxis:[Object.assign({type:'category',data:K.d,gridIndex:0,boundaryGap:true},ax,{axisLabel:{show:false},splitLine:{show:false}}),
           Object.assign({type:'category',data:K.d,gridIndex:1,boundaryGap:true},ax,{axisLabel:{color:t.muted,fontSize:10,formatter:function(v){return kSpan()<=160?v.slice(2):v.slice(0,7)}},splitLine:{show:false}})],
    yAxis:[Object.assign({scale:true,gridIndex:0,splitNumber:4},ax),Object.assign({gridIndex:1,splitNumber:2},ax,{axisLabel:{color:t.muted,fontSize:9,formatter:function(v){return(v/1e4).toFixed(1)+'亿'}}})],
    dataZoom:[{type:'inside',xAxisIndex:[0,1],startValue:kzoom[0],endValue:kzoom[1],disabled:co,minValueSpan:20},
              {type:'slider',xAxisIndex:[0,1],startValue:kzoom[0],endValue:kzoom[1],bottom:4,height:nw?16:20,showDetail:false,borderColor:t.line,textStyle:{color:t.muted},minValueSpan:20}],
    series:[
      {name:'日K',type:'candlestick',data:OHLC,xAxisIndex:0,yAxisIndex:0,barMaxWidth:12,itemStyle:{color:t.up,color0:t.down,borderColor:t.up,borderColor0:t.down},
        markArea:{silent:true,data:bandAreas(showLabel).concat(fm.area)},
        markLine:{silent:true,symbol:'none',lineStyle:{color:'#b7791f',type:'solid',width:1.4},label:{fontSize:10},data:fm.line}},
      {name:'MA5',type:'line',data:MA5,xAxisIndex:0,yAxisIndex:0,smooth:true,symbol:'none',lineStyle:{width:1,color:'#ff9800'}},
      {name:'MA20',type:'line',data:MA20,xAxisIndex:0,yAxisIndex:0,smooth:true,symbol:'none',lineStyle:{width:1,color:'#2196f3'}},
      {name:'成交量',type:'bar',data:K.v,xAxisIndex:1,yAxisIndex:1,barMaxWidth:12,itemStyle:{color:function(p){var i=p.dataIndex;return K.c[i]>=K.o[i]?t.up:t.down}}}
    ]},true);
  c.setOption(tipPatch('hx-kline','cross',false),false);
  if(!c.__hxBound){c.__hxBound=true;
    c.on('updateAxisPointer',function(ev){var a=(ev.axesInfo||[])[0];if(a&&a.value!=null){var i=typeof a.value==='number'?a.value:KI[a.value];if(i!=null)ktip(i)}});
    c.on('datazoom',function(){var o=c.getOption().dataZoom[0];if(o&&o.startValue!=null){var was=kSpan()<=900;kzoom=[o.startValue,o.endValue];var now=kSpan()<=900;if(was!==now)c.setOption({series:[{markArea:{data:bandAreas(now).concat(focusMarks().area)}}]})}});
    c.getZr().on('globalout',function(){if(!(touch['hx-kline']&&touch['hx-kline'].isActive()))ktip(null)});
  }
  ktip(null);
}
function setZoom(a,b){kzoom=[Math.max(0,a),Math.min(KN-1,b)];if(charts['hx-kline']){charts['hx-kline'].dispatchAction({type:'dataZoom',startValue:kzoom[0],endValue:kzoom[1]});renderKline(true)}}
function markZoomBtn(z){document.querySelectorAll('[data-z]').forEach(function(b){b.classList.toggle('on',b.getAttribute('data-z')===z)})}
function zoomPreset(z){
  markZoomBtn(z);
  if(z==='all')return setZoom(0,KN-1);
  if(z==='now'){var up=D.bands.filter(function(b){return b.status!=='完整'});var first=up.length?KI[up[0].t0]:KLAST;return setZoom((first==null?KLAST:first)-30,KN-1)}
  var n=+z;setZoom(KLAST-n,KN-1);
}
function focusEvent(t0){
  S.focus=t0;var b=D.bands.filter(function(x){return x.t0===t0})[0];if(!b)return;
  var i0=KI[b.t0],i1=KI[b.t1]!=null?KI[b.t1]:KN-1;
  $('hx-focus').innerHTML='已定位：<b>'+b.year+' '+esc(b.label)+'</b>　T0 '+esc(b.t0)+' → T1 '+esc(b.t1)+'（显示 T-20 ~ T+30，浅色底为 T-10 ~ T+20）';
  kzoom=[Math.max(0,i0-20),Math.min(KN-1,i1+30)];
  renderEvents();renderKline(true);
  markZoomBtn(null);
  // 滚动时扣除吸顶导航 + 分组条的高度，避免 K 线被遮住
  var bar=document.querySelector('.hx-bar'),off=bar?bar.getBoundingClientRect().height+parseFloat(getComputedStyle(bar).top)+10:70;
  window.scrollTo({top:$('hx-ktip').getBoundingClientRect().top+window.scrollY-off,behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'auto':'smooth'});
  ktip(i0);
}

function renderFoot(){
  var m=D.meta;
  $('hx-foot').innerHTML='<p>'+m.coverage.map(esc).join('<br>')+'</p>'+
    (m.notes.length?'<p>未形成事件：'+m.notes.map(esc).join('；')+'</p>':'')+
    (m.missing.length?'<p>缺失指数：'+esc(m.missing.join(', '))+'</p>':'')+
    '<p>“剔除异常年”口径排除事件年份：'+esc(ex.join('、')||'无')+'（2015 股灾 / 2024 年 9 月底政策行情主导小样本均值）。</p>'+
    '<p>指标：节前 N 日 = C(T0)/C(T−N)−1；T1 跳空 = O(T1)/C(T0)−1；节后 N 日 = C(T+N)/C(T0)−1；量比 = T−5..T0 均量 ÷ T−25..T−6 均量；基准 = 全部交易日同口径分布。</p>'+
    '<p>重跑：<code>'+esc(m.command)+'</code><br>新增节日：在 backtest/holiday_effect.py 的 HOLIDAY_REGISTRY 追加一项 HolidaySpec。</p>';
}
function render(){chips();renderHero();renderConcl();renderCurrent();renderMatrix();renderPath();renderKline(true);renderRS();renderStats();renderEvents()}
renderFoot();render();
function resizeAll(){Object.keys(charts).forEach(function(k){charts[k].resize()})}
var rt;window.addEventListener('resize',function(){clearTimeout(rt);rt=setTimeout(function(){resizeAll();renderPath();renderRS();renderKline(true)},150)});
window.addEventListener('stockapp:layout',function(){setTimeout(resizeAll,220)});
window.addEventListener('stockapp:theme',function(){setTimeout(function(){renderPath();renderRS();renderKline(true)},30)});
})();
</script>
</body>
</html>
"""


def build_holiday_page(payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("</", "<\\/")
    return TEMPLATE.replace("__DATA__", data)
