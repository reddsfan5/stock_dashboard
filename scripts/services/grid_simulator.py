#!/usr/bin/env python3
"""生成由分时服务驱动的 T+0 网格交易动态回放页面。"""

import os


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_HTML = os.path.join(PROJECT_DIR, "output", "grid_simulator.html")


def build_html() -> str:
    return r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>T+0 网格交易动态回放</title>
<link rel="stylesheet" href="/assets/app.css">
<style>
:root{--bg:var(--app-bg,#f3f5f8);--panel:var(--app-surface,#fff);--text:var(--app-text,#20242c);--muted:var(--app-muted,#737b8c);--border:var(--app-border,#e2e6ed);--blue:var(--app-accent,#3478f6);--red:var(--app-up,#e5484d);--green:var(--app-down,#16a36a);--orange:#dd8b00;--purple:#7656d6;--value-bg:var(--app-surface-2,#f6f8fb)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;font-size:14px}.page{max-width:1440px;margin:0 auto;padding:14px}.top{display:flex;align-items:center;gap:12px;margin-bottom:10px}.top h1{font-size:20px;margin:0}.top .links{margin-left:auto;display:flex;gap:12px}.top a{color:var(--blue);text-decoration:none}.panel{background:var(--panel);border:1px solid var(--border);border-radius:12px;box-shadow:0 2px 8px rgba(30,45,75,.04)}.controls{padding:12px;margin-bottom:10px}.primary{display:grid;grid-template-columns:minmax(240px,1.6fr) repeat(4,minmax(130px,1fr)) auto;gap:8px;align-items:end}.field{display:flex;flex-direction:column;gap:5px;min-width:0}.field>label{font-size:11px;color:#687386;font-weight:600;letter-spacing:.04em}.field:focus-within>label{color:var(--blue)}input:not([type=checkbox]),select,button{height:38px;border:1px solid var(--border);border-radius:8px;background:#fff;color:var(--text);padding:0 10px;font:inherit}input:not([type=checkbox]),select{border-color:#cbd3df;color:#172033;font-size:14px;font-weight:600;box-shadow:inset 0 1px 1px rgba(20,35,60,.035)}input::placeholder{color:#a7afbc;font-weight:400}input:not([type=checkbox]):focus,select:focus{outline:2px solid rgba(52,120,246,.16);outline-offset:0;border-color:var(--blue)}input[type=checkbox]{height:17px;width:17px;padding:0;accent-color:var(--blue)}button{cursor:pointer;font-weight:600}button.primary-btn{background:var(--blue);border-color:var(--blue);color:#fff;padding:0 18px}.search{position:relative}.results{position:absolute;top:62px;left:0;right:0;background:#fff;border:1px solid var(--border);border-radius:8px;box-shadow:0 12px 28px rgba(20,30,50,.16);z-index:30;display:none;max-height:260px;overflow:auto}.result{display:flex;justify-content:space-between;padding:10px;cursor:pointer;border-bottom:1px solid #f0f1f4}.result:hover{background:#eef4ff}.result span:last-child{color:var(--muted);font-size:12px}.setup-block{margin-top:10px;padding-top:10px;border-top:1px solid var(--border)}.section-title{font-size:13px;font-weight:600;margin-bottom:7px}.section-title small{font-weight:400;color:var(--muted);margin-left:8px}.setup-grid{display:grid;grid-template-columns:repeat(7,minmax(110px,1fr));gap:8px;align-items:end}.arrival-grid{grid-template-columns:repeat(6,minmax(125px,1fr))}.check-field{height:38px;display:flex;align-items:center;gap:8px;padding:0 10px;border:1px solid #d7dde7;border-radius:8px;background:#f7f9fc}.check-field:focus-within{border-color:var(--blue);background:#f1f6ff}.check-field label{font-size:12px;font-weight:600;color:#293347;cursor:pointer}[hidden]{display:none!important}details{margin-top:9px;border-top:1px solid var(--border);padding-top:8px}summary{cursor:pointer;color:var(--blue);font-size:12px}.parameter-help{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:8px}.parameter-help div{padding:8px;background:#f7f9fc;border-radius:7px;color:var(--muted);font-size:11px;line-height:1.5}.parameter-help b{color:var(--text)}.advanced{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:8px;margin-top:8px}.warning{font-size:12px;color:#7a5b00;background:#fff7dc;border-radius:7px;padding:8px 10px;margin-top:9px;line-height:1.55}.playbar{display:flex;align-items:center;gap:8px;padding:9px 12px;margin-bottom:10px}.playbar input[type=range]{flex:1;height:auto;padding:0;border:0}.playbar .time{font-variant-numeric:tabular-nums;min-width:48px;font-weight:600}.playbar button{min-width:64px}.metrics{display:grid;grid-template-columns:repeat(7,1fr);gap:1px;background:var(--border);border-bottom:1px solid var(--border)}.metric{background:#fff;padding:10px 12px;min-width:0}.metric .label{display:block;color:var(--muted);font-size:11px;margin-bottom:3px}.metric .quote{display:flex;align-items:baseline;gap:7px;min-width:0}.metric .value{font-size:16px;font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.metric .delta{font-size:12px;font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}.up{color:var(--red)}.down{color:var(--green)}.main{display:grid;grid-template-columns:minmax(0,1fr) 350px;gap:10px}.chart-panel{overflow:hidden}#chart{height:680px}.side{display:flex;flex-direction:column;min-height:680px}.side h2{font-size:14px;margin:0;padding:12px;border-bottom:1px solid var(--border)}.state{padding:0 12px 12px;border-bottom:1px solid var(--border)}.state-header{display:flex;align-items:center;justify-content:space-between;padding:11px 0 9px}.state-mode{display:inline-flex;align-items:center;color:var(--blue);font-size:13px;font-weight:600}.state-time{color:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}.state-section{padding:9px 0;border-top:1px solid #edf0f4}.state-section-title{margin:0 0 7px;color:#4d586a;font-size:11px;font-weight:600;letter-spacing:.08em}.state-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;margin:0}.state-item{min-width:0;padding:7px 8px;background:var(--value-bg);border-radius:7px}.state-item dt{margin:0 0 2px;color:#7d8696;font-size:10px;line-height:1.3}.state-item dd{margin:0;color:#182132;font-size:13px;font-weight:600;line-height:1.35;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}.state-item small{display:block;margin-top:3px;color:#7d8696;font-size:10px;line-height:1.4}.state-wide{grid-column:1/-1}.state-blocked{margin-top:7px;padding:7px 8px;border-radius:7px;background:#fff1ef;color:#9c3028;font-size:11px;line-height:1.45}.trades{overflow:auto;flex:1}.trade{display:grid;grid-template-columns:46px 48px 1fr;gap:6px;padding:9px 10px;border-bottom:1px solid #f0f1f4;font-size:12px}.trade .side-buy{color:var(--red);font-weight:600}.trade .side-sell{color:var(--green);font-weight:600}.trade .event{color:var(--orange);font-weight:600}.trade .detail{line-height:1.5}.trade .why{color:var(--muted)}.empty{padding:30px 12px;text-align:center;color:var(--muted)}.assumptions{padding:9px 12px;font-size:11px;color:var(--muted);border-top:1px solid var(--border);line-height:1.6}.loading{position:fixed;inset:0;background:rgba(243,245,248,.6);display:none;align-items:center;justify-content:center;z-index:50}.loading span{background:#20242c;color:#fff;padding:10px 16px;border-radius:8px}.notice{display:none;padding:9px 12px;background:#ffe9e7;color:#922b22;border-radius:8px;margin-bottom:10px}
@media(max-width:1050px){.primary{grid-template-columns:repeat(3,1fr)}.setup-grid,.advanced,.parameter-help{grid-template-columns:repeat(3,1fr)}.main{grid-template-columns:1fr}.side{min-height:360px}.metrics{grid-template-columns:repeat(4,1fr)}}
.mobile-title{display:none}@media(max-width:650px){.page{padding:8px}.top{align-items:flex-start;gap:8px}.top h1{font-size:15px;white-space:nowrap}.desktop-title{display:none}.mobile-title{display:inline}.top .links{gap:8px}.top a{white-space:nowrap}.primary,.setup-grid,.advanced,.parameter-help{grid-template-columns:repeat(2,1fr)}.search{grid-column:1/-1}.metrics{grid-template-columns:repeat(2,1fr)}input:not([type=checkbox]),select{font-size:16px}#chart{height:520px}.playbar{flex-wrap:wrap}.playbar input[type=range]{order:3;flex-basis:100%}}
</style>
<script src="/assets/app-shell.js" defer></script>
</head><body>
<div id="app-shell" data-active="grid"></div>
<div class="loading" id="loading"><span>正在模拟全天网格交易…</span></div>
<main class="page">
  <div class="top"><h1><span class="desktop-title">T+0 网格交易动态回放</span><span class="mobile-title">网格动态回放</span></h1><div class="links"><a href="minute_view.html">分时查询</a><a href="index.html">返回导航</a></div></div>
  <div class="notice" id="notice"></div>
  <section class="panel controls">
    <div class="primary">
      <div class="field search"><label>标的代码或名称</label><input id="code" value="518880" autocomplete="off" data-clear-on-focus="1" placeholder="例如 518880"><div class="results" id="results"></div></div>
      <div class="field"><label>交易日</label><select id="date"><option value="">自动取最新</option></select></div>
      <div class="field"><label>银河网格模式</label><select id="mode"><option value="transaction_driven">成交驱动型</option><option value="price_triggered">到价触发型</option></select></div>
      <div class="field"><label>初始现金（元）</label><input id="cash" type="number" value="100000" min="0" step="10000"></div>
      <div class="field"><label>初始持仓（份）</label><input id="shares" type="number" value="1000" min="0" step="100"></div>
      <button class="primary-btn" id="run">开始模拟</button>
    </div>
    <div class="setup-block"><div class="section-title">触发条件与委托设置</div><div class="setup-grid">
      <div class="field"><label>初始基准价（留空=开盘价）</label><input id="basePrice" type="number" placeholder="当日开盘价" min="0.001" step="0.001"></div>
      <div class="field"><label>涨跌设置方式</label><select id="stepMode"><option value="pct">比例（%）</option><option value="diff">差价（元）</option></select></div>
      <div class="check-field"><input id="separateSteps" type="checkbox"><label for="separateSteps">上涨/下跌分开</label></div>
      <div class="field"><label id="buyStepLabel">每下跌（%）</label><input id="buyStep" type="number" value="0.10" min="0.001" step="0.01"></div>
      <div class="field conditional" id="sellStepField" hidden><label id="sellStepLabel">每上涨（%）</label><input id="sellStep" type="number" value="0.10" min="0.001" step="0.01"></div>
      <div class="check-field"><input id="separateLots" type="checkbox"><label for="separateLots">买入/卖出分开</label></div>
      <div class="field"><label id="buyLotLabel">每笔委托（份）</label><input id="buyLot" type="number" value="100" min="100" step="100"></div>
      <div class="field conditional" id="sellLotField" hidden><label>每笔卖出（份）</label><input id="sellLot" type="number" value="100" min="100" step="100"></div>
    </div></div>
    <div class="setup-block arrival-only" id="arrivalSetup" hidden>
      <div class="section-title">到价触发型专属设置<small>先监控网格价，触发后才报单</small></div>
      <div class="setup-grid arrival-grid">
        <div class="field"><label>反弹/回落设置方式</label><select id="turnMode"><option value="pct">比例（%）</option><option value="diff">差价（元）</option></select></div>
        <div class="check-field"><input id="reboundEnabled" type="checkbox"><label for="reboundEnabled">累计反弹买入</label></div>
        <div class="field conditional" id="reboundValueField" hidden><label id="reboundValueLabel">最低点反弹（%）</label><input id="reboundValue" type="number" value="0.10" min="0.001" step="0.01"></div>
        <div class="check-field"><input id="pullbackEnabled" type="checkbox"><label for="pullbackEnabled">累计回落卖出</label></div>
        <div class="field conditional" id="pullbackValueField" hidden><label id="pullbackValueLabel">最高点回落（%）</label><input id="pullbackValue" type="number" value="0.10" min="0.001" step="0.01"></div>
        <div class="check-field"><input id="floorTriggerEnabled" type="checkbox"><label for="floorTriggerEnabled">保底价触发</label></div>
        <div class="field"><label>委托价格</label><select id="orderPriceMode"><option value="counterparty">对手价（滑点近似）</option><option value="trigger">触发价限价</option><option value="passive">排队限价</option></select></div>
        <div class="field conditional" id="orderOffsetField" hidden><label>排队价有利偏移（基点）</label><input id="orderOffsetBps" type="number" value="1" min="0" max="1000" step="0.1"></div>
        <div class="field"><label>基准价更新时间点</label><select id="baseUpdateTiming"><option value="filled">委托全部成交后</option><option value="triggered">触发委托后</option></select></div>
        <div class="field"><label>更新后的基准价</label><select id="baseUpdatePrice"><option value="grid">对应网格价</option><option value="trigger">实际触发价</option><option value="fill">实际成交价</option></select></div>
        <div class="check-field conditional" id="autoCancelToggle" hidden><input id="autoCancelEnabled" type="checkbox"><label for="autoCancelEnabled">自动撤单</label></div>
        <div class="field conditional" id="autoCancelMinutesField" hidden><label>未成交等待（分钟）</label><input id="autoCancelMinutes" type="number" value="1" min="1" max="240" step="1"></div>
        <div class="field"><label>监控行情（分钟级代理）</label><select id="monitorPriceMode"><option value="ohlc">分钟OHLC高低价</option><option value="close">仅分钟收盘价</option></select></div>
      </div>
    </div>
    <details class="arrival-only" id="arrivalHelp" hidden><summary>到价触发型参数含义</summary><div class="parameter-help">
      <div><b>初始基准价 / 涨跌比例</b><br>由基准价计算下一买入、卖出网格；可按比例或固定差价。</div>
      <div><b>累计反弹 / 累计回落</b><br>先越过网格，再从最低点反弹买入或从最高点回落卖出。</div>
      <div><b>保底价触发</b><br>反转先回到原网格价时直接触发，避免成交价比原网格更差。</div>
      <div><b>触发价格 / 委托价格</b><br>触发价决定何时报单；委托价决定报单后如何等待成交。</div>
      <div><b>更新时间点 / 更新基准价</b><br>选择触发后或全成后更新，并选择网格价、触发价或成交价。</div>
      <div><b>自动撤单</b><br>排队限价超过等待分钟仍未成交时撤单并释放冻结资券。</div>
      <div><b>倍数委托</b><br>一个监控点跨越多格时按跨格数放大数量，但不超过最大倍数。</div>
      <div><b>有效区间 / 持仓控制</b><br>限制策略价格边界、保留底仓和最大持仓，阻止越界委托。</div>
      <div><b>次日基准 / 监控行情</b><br>次日可改用收盘价；分钟OHLC与收盘监控都是实盘行情代理。</div>
    </div></details>
    <details><summary>高级设置：价格区间、持仓、倍数、价格笼子与有效期</summary><div class="advanced">
      <div class="field"><label>有效价格下限（可空）</label><input id="priceFloor" type="number" placeholder="不限制" min="0.001" step="0.001"></div>
      <div class="field"><label>有效价格上限（可空）</label><input id="priceCeiling" type="number" placeholder="不限制" min="0.001" step="0.001"></div>
      <div class="field"><label>保留底仓（份）</label><input id="minPosition" type="number" value="0" min="0" step="100"></div>
      <div class="field"><label>最大持仓（份）</label><input id="maxPosition" type="number" value="5000" min="100" step="100"></div>
      <div class="check-field"><input id="multiplierEnabled" type="checkbox"><label for="multiplierEnabled">开启倍数委托</label></div>
      <div class="field conditional" id="buyMultiplierField" hidden><label id="buyMultiplierLabel">买入委托倍数</label><input id="buyMultiplier" type="number" value="3" min="1" max="100" step="1"></div>
      <div class="field conditional" id="sellMultiplierField" hidden><label id="sellMultiplierLabel">卖出委托倍数</label><input id="sellMultiplier" type="number" value="3" min="1" max="100" step="1"></div>
      <div class="check-field transaction-only"><input id="cageToMarket" type="checkbox"><label for="cageToMarket">超笼子废单转市价</label></div>
      <div class="field conditional transaction-only" id="priceCageField" hidden><label>模拟价格笼子（%）</label><input id="priceCagePct" type="number" value="2" min="0.1" max="20" step="0.1"></div>
      <div class="check-field"><input id="afterCloseUpdate" type="checkbox"><label for="afterCloseUpdate">次日基准价=当日收盘价</label></div>
      <div class="field"><label>策略有效期</label><select id="validityDays"><option value="90">3个月</option><option value="180">半年</option><option value="365">1年</option><option value="1095">3年</option></select></div>
      <div class="field"><label>单边佣金（万分之）</label><input id="commission" type="number" value="1" min="0" max="200" step="0.1"></div>
      <div class="field"><label>最低佣金（元）</label><input id="minCommission" type="number" value="0" min="0" step="1"></div>
      <div class="field"><label>卖出税费（万分之）</label><input id="sellTax" type="number" value="0" min="0" max="200" step="0.1"></div>
      <div class="field"><label>到价型滑点（基点）</label><input id="slippage" type="number" value="2" min="0" max="100" step="0.1"></div>
    </div></details>
    <div class="warning" id="modeHelp"></div>
  </section>
  <section class="panel playbar"><button id="reset">回到开盘</button><button id="play">▶ 动态分时</button><select id="speed" aria-label="播放速度"><option value="1">1×</option><option value="2">2×</option><option value="5" selected>5×</option><option value="10">10×</option></select><span class="time" id="time">--:--</span><input id="cursor" type="range" min="0" max="0" value="0" aria-label="分钟进度"><span id="progress">0/0</span></section>
  <section class="panel chart-panel">
    <div class="metrics">
      <div class="metric"><span class="label">当前价 · 较昨收</span><span class="quote"><span class="value" id="mPrice">—</span><span class="delta" id="mChangePct">—</span></span></div>
      <div class="metric"><span class="label">现金</span><span class="value" id="mCash">—</span></div>
      <div class="metric"><span class="label">持仓</span><span class="value" id="mShares">—</span></div>
      <div class="metric"><span class="label">账户净值</span><span class="value" id="mEquity">—</span></div>
      <div class="metric"><span class="label">累计收益</span><span class="value" id="mPnl">—</span></div>
      <div class="metric"><span class="label">超越持有</span><span class="value" id="mExcess">—</span></div>
      <div class="metric"><span class="label">已成交</span><span class="value" id="mTrades">—</span></div>
    </div>
    <div class="main"><div id="chart"></div><aside class="side"><h2 id="tradeTitle">成交过程</h2><div class="state" id="state">设置参数后开始模拟</div><div class="trades" id="trades"><div class="empty">尚无成交</div></div><div class="assumptions" id="assumptions"></div></aside></div>
  </section>
</main>
<script src="vendor/echarts.min.js"></script><script>window.echarts||document.write(`<script src='https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js'><\/script>`);</script>
<script>
const $=id=>document.getElementById(id),chart=echarts.init($('chart'));let result=null,index=0,timer=null,searchTimer=null;
const money=v=>(+v).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
const price=v=>+v<10?(+v).toFixed(3):(+v).toFixed(2);const signed=v=>`${v>=0?'+':''}${money(v)}`;const changePct=(value,base)=>base>0?((+value/+base)-1)*100:0;const signedPct=v=>`${v>=0?'+':''}${(+v).toFixed(2)}%`;const pnlClass=v=>v>=0?'up':'down';const marketClass=v=>v>0?'up':v<0?'down':'';
const modeTexts={
  transaction_driven:'成交驱动型：上下两侧预埋限价单，提前占用资金和证券；一侧全部成交后撤销另一侧并重新双挂。',
  price_triggered:'到价触发型：先监控网格价，达到条件后才报单。累计反弹/回落会继续跟踪极值；保底价保证反转回到原网格线时直接触发。'
};
function notice(msg){$('notice').textContent=msg;$('notice').style.display=msg?'block':'none'}
function loading(on){$('loading').style.display=on?'flex':'none'}
async function api(path){const r=await fetch(path,{cache:'no-store'}),body=await r.json();if(!r.ok)throw new Error(body.error||`HTTP ${r.status}`);return body}
function updateModeHelp(){
  const arrival=$('mode').value==='price_triggered';
  $('modeHelp').textContent=modeTexts[$('mode').value]+(arrival?' 页面中的盘口、触发和排队均由分钟OHLC近似，不代表银河柜台真实撮合。':' 价格笼子也只能用分钟端点近似。')+' 有效期和次日基准价属于跨日设置，本页只回放所选交易日。';
}
function updateConditionalControls(){
  const splitSteps=$('separateSteps').checked,splitLots=$('separateLots').checked,isDiff=$('stepMode').value==='diff',arrival=$('mode').value==='price_triggered';
  $('sellStepField').hidden=!splitSteps;$('sellLotField').hidden=!splitLots;
  $('arrivalSetup').hidden=!arrival;$('arrivalHelp').hidden=!arrival;document.querySelectorAll('.transaction-only').forEach(el=>el.hidden=arrival);
  $('buyMultiplierField').hidden=!$('multiplierEnabled').checked;$('sellMultiplierField').hidden=!$('multiplierEnabled').checked;
  $('priceCageField').hidden=arrival||!$('cageToMarket').checked;
  $('reboundValueField').hidden=!arrival||!$('reboundEnabled').checked;$('pullbackValueField').hidden=!arrival||!$('pullbackEnabled').checked;
  const hasTurn=$('reboundEnabled').checked||$('pullbackEnabled').checked;$('floorTriggerEnabled').disabled=!hasTurn;if(!hasTurn)$('floorTriggerEnabled').checked=false;
  const passive=arrival&&$('orderPriceMode').value==='passive';$('orderOffsetField').hidden=!passive;$('autoCancelToggle').hidden=!passive;$('autoCancelMinutesField').hidden=!passive||!$('autoCancelEnabled').checked;
  const fillOption=[...$('baseUpdatePrice').options].find(o=>o.value==='fill'),updateOnTrigger=$('baseUpdateTiming').value==='triggered';fillOption.disabled=updateOnTrigger;if(updateOnTrigger&&$('baseUpdatePrice').value==='fill')$('baseUpdatePrice').value='grid';
  const unit=isDiff?'元':'%';$('buyStepLabel').textContent=splitSteps?`每下跌（${unit}）`:`每上涨/下跌（${unit}）`;$('sellStepLabel').textContent=`每上涨（${unit}）`;$('buyLotLabel').textContent=splitLots?'每笔买入（份）':'每笔委托（份）';
  const turnUnit=$('turnMode').value==='diff'?'元':'%';$('reboundValueLabel').textContent=`最低点反弹（${turnUnit}）`;$('pullbackValueLabel').textContent=`最高点回落（${turnUnit}）`;
  $('buyMultiplierLabel').textContent=arrival?'跳格买入最大倍数':'买入委托倍数';$('sellMultiplierLabel').textContent=arrival?'跳格卖出最大倍数':'卖出委托倍数';
}
function query(){
  const splitSteps=$('separateSteps').checked,splitLots=$('separateLots').checked,buyStep=$('buyStep').value,sellStep=splitSteps?$('sellStep').value:buyStep,buyLot=$('buyLot').value,sellLot=splitLots?$('sellLot').value:buyLot;
  const p=new URLSearchParams({code:$('code').value.trim(),mode:$('mode').value,cash:$('cash').value,shares:$('shares').value,step_mode:$('stepMode').value,grid_pct:buyStep,buy_step:buyStep,sell_step:sellStep,lot:buyLot,buy_lot:buyLot,sell_lot:sellLot,multiplier_enabled:$('multiplierEnabled').checked?'1':'0',buy_multiplier:$('buyMultiplier').value,sell_multiplier:$('sellMultiplier').value,price_floor:$('priceFloor').value,price_ceiling:$('priceCeiling').value,min_position:$('minPosition').value,max_position:$('maxPosition').value,cage_to_market:$('cageToMarket').checked?'1':'0',price_cage_pct:$('priceCagePct').value,rebound_enabled:$('reboundEnabled').checked?'1':'0',rebound_value:$('reboundValue').value,pullback_enabled:$('pullbackEnabled').checked?'1':'0',pullback_value:$('pullbackValue').value,turn_mode:$('turnMode').value,floor_trigger_enabled:$('floorTriggerEnabled').checked?'1':'0',order_price_mode:$('orderPriceMode').value,order_offset_bps:$('orderOffsetBps').value,base_update_timing:$('baseUpdateTiming').value,base_update_price:$('baseUpdatePrice').value,auto_cancel_enabled:$('autoCancelEnabled').checked?'1':'0',auto_cancel_minutes:$('autoCancelMinutes').value,monitor_price_mode:$('monitorPriceMode').value,after_close_update_base:$('afterCloseUpdate').checked?'1':'0',validity_days:$('validityDays').value,commission:$('commission').value,min_commission:$('minCommission').value,sell_tax:$('sellTax').value,slippage_bps:$('slippage').value});
  if($('basePrice').value)p.set('base_price',$('basePrice').value);if($('date').value)p.set('date',$('date').value);return p;
}
async function runSimulation(){stop();loading(true);notice('');try{result=await api('/api/grid/simulate?'+query());$('code').value=result.code;if(window.StockAppShell&&StockAppShell.bindClearOnFocus){const c=StockAppShell.bindClearOnFocus($('code'));if(c)c.arm()}const selected=result.date;$('date').innerHTML=result.dates.map(d=>`<option value="${d}" ${d===selected?'selected':''}>${d}</option>`).join('');$('cursor').max=result.timeline.length-1;index=0;$('cursor').value=0;$('assumptions').innerHTML=result.assumptions.map(x=>`• ${x}`).join('<br>');const u=new URL(location.href);u.searchParams.set('code',result.code);u.searchParams.set('date',result.date);u.searchParams.set('mode',result.config.mode);history.replaceState(null,'',u);render()}catch(e){notice(e.message)}finally{loading(false)}}
function stop(){if(timer){clearInterval(timer);timer=null}$('play').textContent='▶ 动态分时'}
function play(){if(!result)return;if(timer){stop();return}if(index>=result.timeline.length-1)index=0;$('play').textContent='Ⅱ 暂停';timer=setInterval(()=>{index=Math.min(index+(+$('speed').value),result.timeline.length-1);$('cursor').value=index;render();if(index>=result.timeline.length-1)stop()},140)}
function visibleTrades(){return result.trades.filter(t=>t.index<=index)}
function visibleEvents(){return (result.events||[]).filter(e=>e.index<=index)}
function stateItem(label,value,{wide=false,hint='',valueClass=''}={}){
  return `<div class="state-item${wide?' state-wide':''}"><dt>${label}</dt><dd class="${valueClass}">${value}</dd>${hint?`<small>${hint}</small>`:''}</div>`;
}
function render(){
  if(!result)return;const cur=result.timeline[index],trades=visibleTrades(),events=visibleEvents(),pnl=cur.equity-result.summary.initial_equity,excess=cur.equity-cur.hold_equity,marketChange=changePct(cur.price,result.prev_close),isDriven=result.config.mode==='transaction_driven',cfg=result.config,unit=cfg.step_mode==='diff'?'元':'%',buyStep=cfg.buy_step??cfg.grid_pct,sellStep=cfg.sell_step??cfg.grid_pct,range=`${cfg.price_floor?price(cfg.price_floor):'不限'}～${cfg.price_ceiling?price(cfg.price_ceiling):'不限'}`;
  $('time').textContent=cur.time;$('progress').textContent=`${index+1}/${result.timeline.length}`;$('mPrice').textContent=price(cur.price);$('mChangePct').textContent=signedPct(marketChange);$('mChangePct').className='delta '+marketClass(marketChange);$('mCash').textContent='¥'+money(cur.cash);$('mShares').textContent=cur.shares.toLocaleString();$('mEquity').textContent='¥'+money(cur.equity);$('mPnl').textContent=signed(pnl);$('mPnl').className='value '+pnlClass(pnl);$('mExcess').textContent=signed(excess);$('mExcess').className='value '+pnlClass(excess);$('mTrades').textContent=trades.length;
  const tracking=[cur.buy_tracking?`买入：最低 ${price(cur.buy_extreme)} → 反弹 ${price(cur.buy_turn_target)}`:'',cur.sell_tracking?`卖出：最高 ${price(cur.sell_extreme)} → 回落 ${price(cur.sell_turn_target)}`:''].filter(Boolean).join('；')||'尚未进入反弹/回落跟踪';
  const isPassive=cfg.order_price_mode==='passive';
  const pending=cur.pending_count?cur.pending_orders.map(o=>`${o.side==='buy'?'买入':'卖出'} ${o.shares}份 @ ${price(o.price)}（${o.submitted_time}提交）`).join('；'):'当前无排队委托';
  const executionState=isPassive
    ?stateItem('排队中的委托',pending,{wide:true,hint:cur.pending_count?`当前共 ${cur.pending_count} 笔，后续分钟满足限价后成交`:'触发网格后才会出现；5×/10×播放可能跳过只停留一两分钟的排队状态'})
    :stateItem('委托成交方式',cfg.order_price_mode==='counterparty'?'对手价 · 触发后立即成交':'触发价限价 · 模型内立即成交',{wide:true,hint:'当前模式不会产生待成交状态；选择“排队限价”后才会显示排队中的委托'});
  const orderItems=isDriven
    ?stateItem('预埋买单',cur.buy_order_active?`${price(cur.next_buy)} × ${cur.next_buy_shares}`:'未挂',{hint:`冻结现金 ¥${money(cur.reserved_cash)}`})+stateItem('预埋卖单',cur.sell_order_active?`${price(cur.next_sell)} × ${cur.next_sell_shares}`:'未挂',{hint:`冻结证券 ${cur.reserved_shares}份`})
    :stateItem('监控买网格',cur.buy_order_active?`${price(cur.next_buy)} × ${cur.next_buy_shares}`:'不可买')+stateItem('监控卖网格',cur.sell_order_active?`${price(cur.next_sell)} × ${cur.next_sell_shares}`:'不可卖')+stateItem('反弹 / 回落追踪',tracking,{wide:true})+executionState;
  const nextDayItem=cfg.after_close_update_base&&index===result.timeline.length-1?stateItem('次日基准价',price(result.summary.next_day_anchor)):'';
  $('state').innerHTML=`<div class="state-header"><span class="state-mode">${result.mode_name}</span><span class="state-time">${cur.time}</span></div><section class="state-section"><h3 class="state-section-title">行情基准</h3><dl class="state-grid">${stateItem('当前价格',price(cur.price))}${stateItem('当前涨跌',signedPct(marketChange),{valueClass:marketClass(marketChange)})}${stateItem('策略基准价',price(cur.anchor))}${stateItem('昨日收盘',price(result.prev_close))}${stateItem('下跌间隔',`${buyStep}${unit}`)}${stateItem('上涨间隔',`${sellStep}${unit}`)}${stateItem('有效价格区间',range,{wide:true})}${nextDayItem}</dl></section><section class="state-section"><h3 class="state-section-title">网格委托</h3><dl class="state-grid">${orderItems}</dl>${cur.blocked?`<div class="state-blocked">未成交原因：${cur.blocked}</div>`:''}</section>`;
  $('tradeTitle').textContent=isDriven?`${result.mode_name}成交（买${trades.filter(t=>t.side==='buy').length} / 卖${trades.filter(t=>t.side==='sell').length}）`:`触发与成交（触发${events.filter(e=>e.type==='trigger').length} / 成交${trades.length}）`;
  renderActivities(trades,events);renderChart(trades);
}
function renderActivities(trades,events){
  const rows=[...trades.map(t=>({...t,kind:'trade'})),...events.map(e=>({...e,kind:'event'}))].sort((a,b)=>a.sequence-b.sequence);
  if(!rows.length){$('trades').innerHTML='<div class="empty">尚未触发，继续播放观察价格跨格</div>';return}
  $('trades').innerHTML=rows.slice(-16).reverse().map(r=>{
    if(r.kind==='trade')return `<div class="trade"><span>${r.time}</span><span class="side-${r.side}">成交${r.side==='buy'?'买':'卖'}</span><span class="detail">${r.shares}份 @ ${price(r.price)}${r.multiplier>1?`（${r.multiplier}倍）`:''}<br>网格 ${price(r.grid_price)} · 触发 ${price(r.trigger_price)} · 委托 ${price(r.order_price)}<br><span class="why">${r.reason}</span></span></div>`;
    const labels={armed:'跟踪',trigger:'触发',cancel:'撤单',reject:'拒单'};return `<div class="trade"><span>${r.time}</span><span class="event">${labels[r.type]||r.type}</span><span class="detail">${r.side==='buy'?'买入':'卖出'} @ ${price(r.price)}${r.order_price?` · 委托 ${price(r.order_price)}`:''}<br><span class="why">${r.reason}</span></span></div>`;
  }).join('');
}
function renderChart(trades){
  const full=result.timeline,times=full.map(x=>x.time),shown=key=>full.map((x,i)=>i<=index?x[key]:null),buy=trades.filter(t=>t.side==='buy').map(t=>[t.time,t.price,t.id]),sell=trades.filter(t=>t.side==='sell').map(t=>[t.time,t.price,t.id]),prevClose=result.prev_close,priceValues=[prevClose,...full.flatMap(x=>[x.price,x.next_buy,x.next_sell,x.buy_turn_target,x.sell_turn_target])].filter(Number.isFinite),equityValues=full.flatMap(x=>[x.equity,x.hold_equity]),pMin=Math.min(...priceValues),pMax=Math.max(...priceValues),pPad=Math.max((pMax-pMin)*.06,pMin*.001),priceLow=pMin-pPad,priceHigh=pMax+pPad,pctLow=changePct(priceLow,prevClose),pctHigh=changePct(priceHigh,prevClose),eMin=Math.min(...equityValues),eMax=Math.max(...equityValues),ePad=Math.max((eMax-eMin)*.08,1),arrival=result.config.mode==='price_triggered';
  const legend=['价格','下一买入','下一卖出'];if(arrival)legend.push('反弹触发线','回落触发线');legend.push('账户净值','持有不动');
  chart.setOption({animation:false,legend:{top:5,data:legend},tooltip:{trigger:'axis',axisPointer:{type:'cross'},formatter:ps=>{const i=ps[0]?.dataIndex??0,r=full[i];if(i>index)return `${r.time}<br>尚未播放`;const marketChange=changePct(r.price,prevClose),turn=[r.buy_tracking?`买入最低 ${price(r.buy_extreme)} → ${price(r.buy_turn_target)}`:'',r.sell_tracking?`卖出最高 ${price(r.sell_extreme)} → ${price(r.sell_turn_target)}`:''].filter(Boolean).join('<br>');return `${r.time}<br>价格 ${price(r.price)}　<span style="color:${marketChange>0?'#e5484d':marketChange<0?'#16a36a':'#737b8c'}">较昨收 ${signedPct(marketChange)}</span><br>昨收 ${price(prevClose)}<br>买网格 ${price(r.next_buy)}　卖网格 ${price(r.next_sell)}${turn?'<br>'+turn:''}<br>净值 ¥${money(r.equity)}<br>现金 ¥${money(r.cash)}　可用 ¥${money(r.available_cash)}<br>持仓 ${r.shares}　可用 ${r.available_shares}`}},grid:[{left:64,right:72,top:55,height:'54%'},{left:64,right:72,top:'73%',height:'18%'}],xAxis:[{type:'category',data:times,boundaryGap:false,axisLabel:{show:false}},{type:'category',gridIndex:1,data:times,boundaryGap:false,axisLabel:{interval:Math.max(1,Math.floor(times.length/6))}}],yAxis:[{type:'value',gridIndex:0,min:priceLow,max:priceHigh,name:'价格',splitNumber:6,axisLabel:{formatter:v=>price(v)}},{type:'value',gridIndex:0,min:pctLow,max:pctHigh,name:'较昨收',position:'right',splitNumber:6,axisLabel:{formatter:v=>signedPct(v),color:v=>v>0?'#e5484d':v<0?'#16a36a':'#737b8c'},splitLine:{show:false}},{type:'value',gridIndex:1,min:eMin-ePad,max:eMax+ePad,name:'净值',axisLabel:{formatter:v=>Math.round(v).toLocaleString()}}],series:[{name:'价格',type:'line',connectNulls:false,data:shown('price'),showSymbol:false,lineStyle:{width:2,color:'#3478f6'},areaStyle:{color:'rgba(52,120,246,.06)'},markLine:{silent:true,symbol:'none',label:{formatter:`昨收 ${price(prevClose)}`,position:'insideEndTop',color:'#737b8c'},lineStyle:{color:'#9ca3af',type:'dashed'},data:[{yAxis:prevClose}]}},{name:'下一买入',type:'line',connectNulls:false,data:shown('next_buy'),showSymbol:false,lineStyle:{width:1,type:'dashed',color:'#e5484d'}},{name:'下一卖出',type:'line',connectNulls:false,data:shown('next_sell'),showSymbol:false,lineStyle:{width:1,type:'dashed',color:'#16a36a'}},{name:'反弹触发线',type:'line',connectNulls:false,data:shown('buy_turn_target'),showSymbol:false,lineStyle:{width:1.5,type:'dotted',color:'#ff7f50'}},{name:'回落触发线',type:'line',connectNulls:false,data:shown('sell_turn_target'),showSymbol:false,lineStyle:{width:1.5,type:'dotted',color:'#00a6a6'}},{name:'买入成交',type:'scatter',data:buy,symbol:'triangle',symbolSize:12,itemStyle:{color:'#e5484d'}},{name:'卖出成交',type:'scatter',data:sell,symbol:'triangle',symbolRotate:180,symbolSize:12,itemStyle:{color:'#16a36a'}},{name:'账户净值',type:'line',connectNulls:false,xAxisIndex:1,yAxisIndex:2,data:shown('equity'),showSymbol:false,lineStyle:{width:1.5,color:'#7656d6'}},{name:'持有不动',type:'line',connectNulls:false,xAxisIndex:1,yAxisIndex:2,data:shown('hold_equity'),showSymbol:false,lineStyle:{width:1,type:'dashed',color:'#dd8b00'}}]},true);
}
async function search(){const q=$('code').value.trim();if(q.length<2)return;try{const rows=await api('/api/minute/search?q='+encodeURIComponent(q));$('results').innerHTML=rows.map(r=>`<div class="result" data-code="${r.code}"><span>${r.name}　<b>${r.code}</b></span><span>${r.latest_date}</span></div>`).join('')||'<div class="result">未找到分钟缓存</div>';$('results').style.display='block'}catch(e){notice(e.message)}}
$('run').onclick=runSimulation;$('play').onclick=play;$('reset').onclick=()=>{stop();index=0;$('cursor').value=0;render()};$('cursor').oninput=e=>{stop();index=+e.target.value;render()};$('date').onchange=runSimulation;$('mode').onchange=()=>{updateConditionalControls();updateModeHelp();runSimulation()};['separateSteps','separateLots','stepMode','multiplierEnabled','cageToMarket','reboundEnabled','pullbackEnabled','turnMode','orderPriceMode','baseUpdateTiming','autoCancelEnabled'].forEach(id=>$(id).addEventListener('change',updateConditionalControls));$('code').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(search,220)});$('code').addEventListener('keydown',e=>{if(e.key==='Enter')runSimulation()});$('results').onclick=e=>{const row=e.target.closest('[data-code]');if(row){$('code').value=row.dataset.code;if(window.StockAppShell&&StockAppShell.bindClearOnFocus){const c=StockAppShell.bindClearOnFocus($('code'));if(c)c.arm()}$('date').innerHTML='<option value="">自动取最新</option>';$('results').style.display='none';runSimulation()}};document.addEventListener('click',e=>{if(!e.target.closest('.search'))$('results').style.display='none'});window.addEventListener('resize',()=>chart.resize());const u=new URL(location.href);if(u.searchParams.get('code'))$('code').value=u.searchParams.get('code');if(window.StockAppShell&&StockAppShell.bindClearOnFocus){const c=StockAppShell.bindClearOnFocus($('code'));if(c)c.arm()}if(u.searchParams.get('date'))$('date').innerHTML=`<option value="${u.searchParams.get('date')}">${u.searchParams.get('date')}</option>`;if(u.searchParams.get('mode'))$('mode').value=u.searchParams.get('mode');updateConditionalControls();updateModeHelp();runSimulation();
</script></body></html>'''


def write_app(path: str = OUT_HTML) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(build_html())
    return path


if __name__ == "__main__":
    path = write_app()
    print(f"✓ 页面: {path}")
    print("  动态模拟需要服务: python -m scripts.serve start grid")
