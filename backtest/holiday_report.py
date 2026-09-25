"""节假日效应回测页面（output/holiday_effect.html）。

页面完全由嵌入的 JSON 驱动：分组（全部 / 长假 / 各节日 / 合并）来自回测结果，
注册表新增节日后重跑即可自动出现在切换条、总览矩阵、图表和表格里。
"""

from __future__ import annotations

import json

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>节假日效应回测</title>
<link rel="stylesheet" href="/assets/app.css">
<style>
:root{--hx-blue:var(--app-accent,#2563eb);--hx-up:var(--app-up,#df3f4b);--hx-down:var(--app-down,#159467);--hx-line:var(--app-border,#e4e8ef);--hx-card:var(--app-surface,#fff);--hx-muted:var(--app-muted,#687386);--hx-soft:var(--app-surface-2,#f6f8fb)}
*{box-sizing:border-box}
.hx-page{max-width:1400px;margin:auto;padding:16px 20px 48px}
.hx-hero h1{font-size:24px;margin:0 0 4px}.hx-hero p{margin:0;color:var(--hx-muted);font-size:13px;line-height:1.6}
.hx-bar{position:sticky;top:calc(var(--app-nav-h,48px) + var(--app-clock-h,0px) + 2px);z-index:12;display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:12px 0;padding:8px 10px;border:1px solid var(--hx-line);border-radius:12px;background:color-mix(in srgb,var(--hx-card) 94%,transparent);backdrop-filter:blur(12px)}
.hx-chips{display:flex;gap:6px;overflow-x:auto;scrollbar-width:none;flex:1 1 auto;min-width:0}.hx-chips::-webkit-scrollbar{display:none}
.hx-chip{flex:0 0 auto;border:1px solid var(--hx-line);background:var(--hx-card);color:inherit;border-radius:999px;padding:7px 12px;font-size:13px;font-weight:650;cursor:pointer}
.hx-chip.active{background:var(--hx-blue);border-color:var(--hx-blue);color:#fff}
.hx-chip small{opacity:.75;font-weight:500;margin-left:3px}
.hx-seg{display:inline-flex;border:1px solid var(--hx-line);border-radius:9px;overflow:hidden;flex:0 0 auto}
.hx-seg button{border:0;background:var(--hx-card);color:inherit;padding:7px 10px;font-size:12px;cursor:pointer}.hx-seg button.active{background:#172033;color:#fff}
.hx-grid{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);gap:12px}
.hx-card{min-width:0;border:1px solid var(--hx-line);border-radius:13px;background:var(--hx-card);padding:14px 16px;margin-bottom:12px}
.hx-card h2{font-size:16px;margin:0 0 8px}.hx-card .note{color:var(--hx-muted);font-size:12px;line-height:1.6;margin:6px 0 0}
.hx-concl li{margin:0 0 7px;line-height:1.65;font-size:13px}.hx-concl ul{padding-left:18px;margin:4px 0}
.hx-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-bottom:10px}
.hx-kpi{border:1px solid var(--hx-line);border-radius:10px;padding:9px 10px;background:var(--hx-soft)}.hx-kpi span{display:block;font-size:11px;color:var(--hx-muted)}.hx-kpi b{font-size:19px;display:block;margin-top:2px}.hx-kpi small{font-size:11px;color:var(--hx-muted)}
.hx-chart{width:100%;height:380px}
.tw{overflow:auto;-webkit-overflow-scrolling:touch;border:1px solid var(--hx-line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:12px;background:var(--hx-card)}
th,td{padding:7px 8px;border-bottom:1px solid var(--hx-line);text-align:right;white-space:nowrap}
th{background:var(--hx-soft);color:var(--hx-muted);font-weight:650;position:sticky;top:0;z-index:1}
th:first-child,td:first-child{text-align:left;position:sticky;left:0;background:inherit;z-index:2}
td:first-child{background:var(--hx-card)}
tr.sel td{background:color-mix(in srgb,var(--hx-blue) 9%,var(--hx-card))}
tr.muted td{opacity:.45}
tr.click{cursor:pointer}
.up{color:var(--hx-up)}.down{color:var(--hx-down)}.dim{color:var(--hx-muted)}
.tag{display:inline-block;font-size:10px;padding:1px 6px;border-radius:999px;background:var(--hx-soft);border:1px solid var(--hx-line);margin-left:4px}
.hx-foot{color:var(--hx-muted);font-size:12px;line-height:1.7;margin-top:8px}
.hx-warn{border-left:3px solid #b7791f;padding:8px 10px;background:color-mix(in srgb,#b7791f 8%,var(--hx-card));border-radius:0 8px 8px 0;font-size:12px;line-height:1.6}
.cell2{display:block;font-size:10px;color:var(--hx-muted)}
@media(max-width:900px){.hx-grid{grid-template-columns:1fr}}
@media(max-width:700px){.hx-page{padding:10px 10px 40px}.hx-hero h1{font-size:20px}.hx-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.hx-chart{height:300px}.hx-card{padding:12px}/* 手机：切换条压成一行，避免吸顶时遮住图表 */.hx-bar{flex-wrap:nowrap;gap:6px;padding:6px}.hx-chip{padding:6px 10px;font-size:12px}.hx-seg button{padding:6px 7px;font-size:11px}.hx-exy{display:none}}
</style>
</head>
<body>
<main class="hx-page">
  <header class="hx-hero">
    <h1>节假日效应回测</h1>
    <p id="hx-sub"></p>
  </header>
  <div class="hx-bar">
    <div class="hx-chips" id="hx-chips" role="tablist" aria-label="节日分组"></div>
    <div class="hx-seg" id="hx-variant"></div>
  </div>
  <section class="hx-grid">
    <div class="hx-card"><h2 id="hx-concl-title">结论</h2><div class="hx-concl" id="hx-concl"></div></div>
    <div class="hx-card"><h2>当前位置</h2><div id="hx-current"></div></div>
  </section>
  <section class="hx-card"><h2>总览矩阵（均值 / 胜率%）</h2><div class="tw" id="hx-matrix"></div>
    <p class="note">点击行切换分组。节日分组含带该标签的合并休市；RS = 代理指数收益 − 主指数收益（百分点），胜率 = 代理跑赢占比；量比胜率 = 缩量(&lt;1)占比。</p></section>
  <section class="hx-grid">
    <div class="hx-card"><h2>平均累计路径（T-10 ~ T+20）</h2><div class="hx-chart" id="hx-path"></div>
      <p class="note">以 T0（休市前最后交易日）收盘为 0；T+1 为复牌首日。手机上下滑动页面，长按图表查看数值。</p></div>
    <div class="hx-card"><h2>风险偏好：相对强弱</h2><div class="hx-chart" id="hx-rs"></div>
      <p class="note">柱 = 各代理相对主指数的平均超额（百分点），&gt;0 表示小盘/成长跑赢；灰色折线为基准（全部交易日）中证1000同口径。</p></div>
  </section>
  <section class="hx-card"><h2>统计 vs 基准</h2><div class="tw" id="hx-stats"></div>
    <p class="note">基准 = 样本期内每个交易日都当作 T0 的同口径分布。p 值为自助抽样双侧近似，窗口重叠且事件少，仅供参考。</p></section>
  <section class="hx-card"><h2>事件明细</h2><div class="tw" id="hx-events"></div>
    <p class="note">量比 = T-5..T0 平均成交量 ÷ T-25..T-6 平均成交量。灰色行为“剔除异常年”口径下被排除的事件。</p></section>
  <section class="hx-card hx-foot" id="hx-foot"></section>
</main>
<script id="holiday-data" type="application/json">__DATA__</script>
<script src="/vendor/echarts.min.js"></script>
<script src="/assets/app-shell.js"></script>
<script src="/assets/chart-touch.js"></script>
<script>
(function(){
'use strict';
var D=JSON.parse(document.getElementById('holiday-data').textContent);
var S={g:D.groups[0],v:'all'};
var $=function(id){return document.getElementById(id)};
var esc=function(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})};
function num(v,nd,sign){if(v==null||!isFinite(v))return'—';nd=nd==null?2:nd;var t=(+v).toFixed(nd);return(sign!==false&&v>0?'+':'')+t}
function cls(v){return v==null?'dim':(v>0?'up':(v<0?'down':''))}
function td(v,nd,sign){return'<td class="'+(sign===false?'':cls(v))+'">'+num(v,nd,sign)+'</td>'}
var VOL=['节前量比','节后量比'];
var ex=D.meta.exclude_years||[];
function inGroup(e,g,v){
  if(v==='ex'&&ex.indexOf(+e['年份'])>=0)return false;
  if(g==='全部')return true;
  if(g.indexOf('长假')===0)return e['休市自然日']>=5;
  if(g==='合并')return e['类型']==='合并';
  return (e['标签']||[]).indexOf(g)>=0;
}
function stat(g,v,m){for(var i=0;i<D.summary.length;i++){var s=D.summary[i];if(s['分组']===g&&s['口径']===v&&s['指标']===m)return s}return null}
function rsStat(code,g,v,m){for(var i=0;i<D.rs_summary.length;i++){var s=D.rs_summary[i];if(s['代码']===code&&s['分组']===g&&s['口径']===v&&s['指标']===m)return s}return null}

$('hx-sub').innerHTML=esc(D.meta.index_name)+' · 样本 '+esc(D.meta.sample)+' · 生成 '+esc(D.meta.generated)+
  ' · 事件由节日注册表 × 交易日历休市空档识别，合并休市只计一次';

function chips(){
  var h='';D.groups.forEach(function(g){
    var n=D.events.filter(function(e){return inGroup(e,g,'all')&&e['状态']==='完整'}).length;
    h+='<button class="hx-chip'+(g===S.g?' active':'')+'" data-g="'+esc(g)+'" role="tab">'+esc(g)+'<small>'+n+'</small></button>'});
  $('hx-chips').innerHTML=h;
  var vh='';Object.keys(D.variants).forEach(function(k){
    var yrs=k==='ex'?'<span class="hx-exy">（'+esc(ex.join('、'))+'）</span>':'';
    vh+='<button data-v="'+k+'" class="'+(k===S.v?'active':'')+'" title="'+(k==='ex'?'剔除 '+esc(ex.join('、')):'')+'">'+esc(D.variants[k])+yrs+'</button>'});
  $('hx-variant').innerHTML=vh;
  var act=document.querySelector('.hx-chip.active');if(act&&act.scrollIntoView)act.scrollIntoView({block:'nearest',inline:'nearest'});
}
document.addEventListener('click',function(e){
  var c=e.target.closest('[data-g]');if(c){S.g=c.getAttribute('data-g');render();return}
  var v=e.target.closest('[data-v]');if(v){S.v=v.getAttribute('data-v');render()}
});

function renderConcl(){
  var lines=(D.conclusions[S.g]||{})[S.v]||[];
  $('hx-concl-title').textContent='结论 · '+S.g+' · '+D.variants[S.v];
  var k=['节前5日%','T0当日%','T1跳空%','节后10日%'],kh='';
  k.forEach(function(m){var s=stat(S.g,S.v,m);if(!s)return;
    kh+='<div class="hx-kpi"><span>'+esc(m.replace('%',''))+'</span><b class="'+cls(s['均值'])+'">'+num(s['均值'])+'%</b><small>胜率 '+num(s['胜率%'],0,false)+'% · 基准 '+num(s['基准均值'])+'%</small></div>'});
  $('hx-concl').innerHTML='<div class="hx-kpis">'+kh+'</div><ul>'+lines.map(function(x){return'<li>'+esc(x)+'</li>'}).join('')+'</ul>';
}

function renderCurrent(){
  var rows=((D.current[S.g]||{})[S.v])||[];
  var up=D.meta.upcoming||[];
  var h='<p class="note" style="margin:0 0 8px">以 '+esc(D.meta.last_date)+'（本地缓存最新交易日）视作 T0，对比“'+esc(S.g)+'”分组的历史节前分布。</p>';
  h+='<div class="tw"><table><thead><tr><th>项目</th><th>当前</th><th>历史均值</th><th>中位数</th><th>历史分位</th><th>全部交易日分位</th></tr></thead><tbody>';
  rows.forEach(function(r){var vol=VOL.indexOf(r['指标'])>=0;
    h+='<tr><td>'+esc(r['项目'])+'<span class="cell2">'+esc(r['指标'])+'（n='+r['样本数']+'）</span></td>'+
      td(r['当前值'],2,!vol)+td(r['历史节前均值'],2,!vol)+td(r['历史节前中位数'],2,!vol)+
      '<td><b>'+num(r['历史分位%'],0,false)+'%</b></td><td>'+num(r['基准分位%'],0,false)+'%</td></tr>'});
  h+='</tbody></table></div>';
  if(up.length){h+='<div class="hx-warn" style="margin-top:8px">'+up.map(function(u){
    return esc(u['节日'])+'：T0 '+esc(u['T0'])+'，T1 '+esc(u['T1'])+'，'+esc(u['状态'])+'，距 T0 还有 '+u['距T0交易日']+' 个交易日'}).join('<br>')+'</div>'}
  $('hx-current').innerHTML=h;
}

function renderMatrix(){
  var cols=D.matrix_cols.concat(D.matrix_rs);
  var h='<table><thead><tr><th>分组</th><th>n</th>'+cols.map(function(c){return'<th>'+esc(c.replace('%',''))+'</th>'}).join('')+'</tr></thead><tbody>';
  D.matrix.filter(function(r){return r['口径']===S.v}).forEach(function(r){
    h+='<tr class="click'+(r['分组']===S.g?' sel':'')+'" data-g="'+esc(r['分组'])+'"><td><b>'+esc(r['分组'])+'</b></td><td>'+r['样本数']+'</td>';
    cols.forEach(function(c){var vol=VOL.indexOf(c)>=0,m=r[c+'·均值'],w=r[c+'·胜率'];
      h+='<td class="'+(vol?'':cls(m))+'">'+num(m,2,!vol)+'<span class="cell2">'+num(w,0,false)+'%</span></td>'});
    h+='</tr>'});
  $('hx-matrix').innerHTML=h+'</tbody></table>';
}

function renderStats(){
  var ms=['节前10日%','节前5日%','T0当日%','T1跳空%','T1当日%','T1日内%','节后5日%','节后10日%','节后20日%','窗口最大回撤%','窗口最大涨幅%','节后10日最高%','节后10日最低%','节前量比','节后量比'];
  var h='<table><thead><tr><th>指标</th><th>n</th><th>均值</th><th>中位数</th><th>胜率</th><th>基准均值</th><th>基准中位</th><th>基准胜率</th><th>均值差</th><th>p</th></tr></thead><tbody>';
  ms.forEach(function(m){var s=stat(S.g,S.v,m);if(!s)return;var vol=VOL.indexOf(m)>=0;
    h+='<tr><td>'+esc(m)+'</td><td>'+s['样本数']+'</td>'+td(s['均值'],2,!vol)+td(s['中位数'],2,!vol)+'<td>'+num(s['胜率%'],0,false)+'%</td>'+
      td(s['基准均值'],2,!vol)+td(s['基准中位数'],2,!vol)+'<td>'+num(s['基准胜率%'],0,false)+'%</td>'+td(s['均值差'])+'<td>'+num(s['p值'],3,false)+'</td></tr>'});
  $('hx-stats').innerHTML=h+'</tbody></table>';
}

function renderEvents(){
  var proxy=(D.meta.proxies[0]||{}).name;
  var cols=['节前5日%','T0当日%','T1跳空%','T1当日%','节后5日%','节后10日%','节后20日%'];
  var h='<table><thead><tr><th>年份·节日</th><th>T0</th><th>T1</th><th>休市</th>'+cols.map(function(c){return'<th>'+esc(c.replace('%',''))+'</th>'}).join('')+
    '<th>量比</th>'+(proxy?'<th>'+esc(proxy)+'RS 节前5</th><th>'+esc(proxy)+'RS 节后5</th>':'')+'<th>状态</th></tr></thead><tbody>';
  D.events.filter(function(e){return inGroup(e,S.g,'all')}).slice().reverse().forEach(function(e){
    var muted=S.v==='ex'&&ex.indexOf(+e['年份'])>=0;
    h+='<tr class="'+(muted?'muted':'')+'"><td><b>'+e['年份']+'</b> '+esc(e['节日'])+(e['类型']==='合并'?'<span class="tag">合并</span>':'')+'</td><td>'+esc(e['T0'])+'</td><td>'+esc(e['T1'])+'</td><td>'+e['休市自然日']+'天</td>';
    cols.forEach(function(c){h+=td(e[c])});
    h+=td(e['节前量比'],2,false);
    if(proxy){h+=td(e['RS_'+proxy+'_节前5日%'])+td(e['RS_'+proxy+'_节后5日%'])}
    h+='<td class="dim">'+esc(e['状态'])+'</td></tr>'});
  $('hx-events').innerHTML=h+'</tbody></table>';
}

var charts={},touch={};
function mkChart(id){if(!window.echarts)return null;if(!charts[id]){charts[id]=echarts.init($(id));
  if(window.ChartTouch){touch[id]=ChartTouch.bindLongPressScrub({el:$(id),getChart:function(){return charts[id]},getCount:function(){return id==='hx-path'?D.path_range.length:D.rs_cols.length},delay:330,axisPointerType:id==='hx-path'?'line':'shadow'})}}
  return charts[id]}
function tipPatch(id){if(!window.ChartTouch)return{};return ChartTouch.tooltipOption({scrubbing:!!(touch[id]&&touch[id].isActive()),axisPointerType:id==='hx-path'?'line':'shadow'})}
var PALETTE=['#d62728','#1f77b4','#2ca02c','#9467bd','#8c564b','#e377c2','#17becf','#bcbd22'];
function renderPath(){
  var c=mkChart('hx-path');if(!c)return;
  var narrow=matchMedia('(max-width:700px)').matches;
  var series=[],i=0;
  D.groups.forEach(function(g){if(g.indexOf('长假')===0)return;var p=(D.paths[g]||{})[S.v];if(!p)return;
    var sel=g===S.g;series.push({name:g+'(n='+p.n+')',type:'line',data:p.main,showSymbol:false,smooth:false,
      lineStyle:{width:sel?3.2:1.3,opacity:sel?1:.55},itemStyle:{color:g==='全部'?'#172033':PALETTE[i++%PALETTE.length]},z:sel?5:2})});
  if(S.g.indexOf('长假')===0){var pl=D.paths[S.g][S.v];series.push({name:S.g+'(n='+pl.n+')',type:'line',data:pl.main,showSymbol:false,lineStyle:{width:3.2},itemStyle:{color:'#b7791f'},z:6})}
  series.push({name:'基准',type:'line',data:D.baseline_path.main,showSymbol:false,lineStyle:{type:'dashed',width:2},itemStyle:{color:'#8a909e'}});
  D.live.forEach(function(l){series.push({name:l.label,type:'line',data:l.main,symbolSize:5,lineStyle:{type:'dotted',width:2.6},itemStyle:{color:'#ff7f0e'},z:7})});
  var opt={animation:false,grid:{left:narrow?38:48,right:12,top:narrow?64:52,bottom:30},
    legend:{type:'scroll',top:0,textStyle:{fontSize:narrow?10:12}},
    tooltip:{trigger:'axis',confine:true,valueFormatter:function(v){return num(v)+'%'}},
    xAxis:{type:'category',data:D.path_range.map(function(k){return k===0?'T0':(k>0?'T+'+k:'T'+k)}),axisLabel:{interval:narrow?4:1,fontSize:10}},
    yAxis:{type:'value',axisLabel:{formatter:'{value}%',fontSize:10},splitLine:{lineStyle:{type:'dashed',color:'#edf0f5'}}},
    series:series};
  series[0].markLine={silent:true,symbol:'none',label:{show:false},lineStyle:{color:'#bbb'},data:[{xAxis:'T0'}]};
  c.setOption(opt,true);c.setOption(tipPatch('hx-path'),false);
}
function renderRS(){
  var c=mkChart('hx-rs');if(!c)return;
  var narrow=matchMedia('(max-width:700px)').matches;
  var series=D.meta.proxies.map(function(p,i){
    return{name:p.name,type:'bar',barMaxWidth:14,data:D.rs_cols.map(function(m){var s=rsStat(p.code,S.g,S.v,m);return s?{value:s['均值'],win:s['胜率%'],n:s['样本数']}:null}),
      itemStyle:{color:['#e5484d','#f59e0b','#2563eb','#7c3aed'][i%4]}}});
  var bcode=(D.meta.proxies[0]||{}).code;
  if(bcode){series.push({name:'基准·'+D.meta.proxies[0].name,type:'line',data:D.rs_cols.map(function(m){var s=rsStat(bcode,S.g,S.v,m);return s?s['基准均值']:null}),symbolSize:4,lineStyle:{type:'dashed',color:'#8a909e'},itemStyle:{color:'#8a909e'}})}
  c.setOption({animation:false,grid:{left:narrow?38:48,right:10,top:narrow?64:40,bottom:narrow?56:40},
    legend:{top:0,textStyle:{fontSize:narrow?10:12}},
    tooltip:{trigger:'axis',confine:true,formatter:function(ps){var h=esc(ps[0].axisValue);ps.forEach(function(p){var d=p.data||{};var v=typeof d==='object'&&d!==null?d.value:d;
      h+='<br>'+p.marker+esc(p.seriesName)+'：'+num(v)+'pp'+(d&&d.win!=null?'（跑赢 '+num(d.win,0,false)+'%，n='+d.n+'）':'')});return h}},
    xAxis:{type:'category',data:D.rs_cols.map(function(m){return m.replace('%','')}),axisLabel:{rotate:narrow?40:0,fontSize:10,interval:0}},
    yAxis:{type:'value',axisLabel:{formatter:'{value}pp',fontSize:10},splitLine:{lineStyle:{type:'dashed',color:'#edf0f5'}}},
    series:series},true);
  c.setOption(tipPatch('hx-rs'),false);
}
function renderFoot(){
  var m=D.meta;
  $('hx-foot').innerHTML='<b>数据与口径</b><br>'+m.coverage.map(esc).join('<br>')+
    (m.notes.length?'<br>未形成事件：'+m.notes.map(esc).join('；'):'')+
    (m.missing.length?'<br>缺失指数：'+esc(m.missing.join(', ')):'')+
    '<br>“剔除异常年”口径排除事件年份：'+esc(ex.join('、')||'无')+'（2015 股灾 / 2024 年 9 月底政策行情主导小样本均值）。'+
    '<br>重跑：<code>'+esc(m.command)+'</code>；新增节日在 backtest/holiday_effect.py 的 HOLIDAY_REGISTRY 追加一项。'+
    '<br><b>以上为历史统计关联，不构成投资建议。</b>';
}
function render(){chips();renderConcl();renderCurrent();renderMatrix();renderStats();renderEvents();renderPath();renderRS()}
renderFoot();render();
var rt;window.addEventListener('resize',function(){clearTimeout(rt);rt=setTimeout(function(){Object.keys(charts).forEach(function(k){charts[k].resize()})},150)});
})();
</script>
</body>
</html>
"""


def build_holiday_page(payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False).replace("</", "<\\/")
    return TEMPLATE.replace("__DATA__", data)
