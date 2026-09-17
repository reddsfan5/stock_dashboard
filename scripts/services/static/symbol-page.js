const $ = id => document.getElementById(id);
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function notice(msg,isErr){
  if(window.StockAppShell&&StockAppShell.toast){
    StockAppShell.toast(msg,{tone:isErr?'error':'ok'});
    return;
  }
  console.log(msg);
}
async function api(path, opts){
  const r = await fetch(path, Object.assign({cache:'no-store'}, opts||{}));
  const body = await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body;
}
function pct(v){if(v==null||v==='')return '—';const n=+v; const cls=n>0?'up':n<0?'down':'';return `<span class="num ${cls}">${n>0?'+':''}${n.toFixed(2)}</span>`}
function badge(status,label){return `<span class="badge ${esc(status)}">${esc(label||status)}</span>`}

let currentCode = '';

function updateTabs(code){
  const tabs=$('symbolTabs');
  if(!code){tabs.hidden=true;return}
  tabs.hidden=false;
  const q=encodeURIComponent(code);
  const map={
    overview:'/symbol.html?code='+q,
    minute:'/minute_view.html?code='+q,
    trainer:'/trading_trainer.html?code='+q,
    journal:'/stock_journal.html?code='+q,
    watchlist:'/watchlist.html'
  };
  tabs.querySelectorAll('[data-tab]').forEach(a=>{
    const key=a.getAttribute('data-tab');
    a.href=map[key]||'#';
    a.classList.toggle('is-active', key==='overview');
  });
}

function render(data){
  currentCode = data.code;
  window.dispatchEvent(new CustomEvent("stock:classification",{detail:{code:data.code,training:!!data.training_time}}));
  updateTabs(data.code);
  $('symbolName').textContent = data.name || data.code;
  $('symbolCode').textContent = data.code;
  const L = data.links || {};
  $('quickLinks').innerHTML = [
    ['分时', L.minute || ('/minute_view.html?code='+encodeURIComponent(data.code))],
    ['日记', L.journal || ('/stock_journal.html?code='+encodeURIComponent(data.code))],
    ['训练', L.trainer || ('/trading_trainer.html?code='+encodeURIComponent(data.code))],
    ['观察池', L.watchlist || '/watchlist.html'],
    ['仪表盘', L.dashboard || '/dashboard.html']
  ].map(([t,h])=>`<a href="${esc(h)}">${t}</a>`).join('');

  const hits = data.screen_hits || {};
  $('screenMeta').textContent = hits.dashboard_mtime ? `仪表盘 ${hits.dashboard_mtime}` : '';
  if((hits.modules||[]).length){
    $('screenHits').className='chips';
    $('screenHits').innerHTML = hits.modules.map(m=>`<span class="chip">${esc(m.title||m.module)}</span>`).join('');
  } else {
    $('screenHits').className='empty';
    $('screenHits').textContent = hits.message || '暂无命中';
  }

  const w = data.watchlist || {};
  if((w.items||[]).length){
    $('watchBlock').className='';
    $('watchBlock').innerHTML = `<table class="app-table"><thead><tr><th>状态</th><th>来源</th><th>筛出日</th><th>论点</th></tr></thead><tbody>${
      w.items.map(it=>`<tr><td>${badge(it.status, it.status_label)}</td><td>${esc(it.source_module||'—')}</td><td>${esc(it.screen_date||'—')}</td><td>${esc(it.thesis||it.note||'—')}</td></tr>`).join('')
    }</tbody></table>`;
  } else {
    $('watchBlock').className='empty';
    $('watchBlock').textContent = '观察池中暂无该代码';
  }
  const tracks = (data.tracks||{}).items||[];
  if(tracks.length){
    $('trackBlock').innerHTML = `<table class="app-table"><thead><tr><th>筛出日</th><th>跟踪日</th><th class="num">次日%</th><th>失效</th></tr></thead><tbody>${
      tracks.map(t=>`<tr><td>${esc(t.screen_date)}</td><td>${esc(t.track_date)}</td><td class="num">${pct(t.return_pct)}</td><td>${t.pattern_failed?'是':'否'}</td></tr>`).join('')
    }</tbody></table>`;
  } else {
    $('trackBlock').innerHTML = '<div class="empty">暂无次日跟踪记录</div>';
  }

  const tr = data.training || {};
  if((tr.runs||[]).length){
    $('trainBlock').className='';
    $('trainBlock').innerHTML = `<div class="meta" style="margin-bottom:8px">会话 ${tr.count} · 决策合计 ${tr.decision_count||0}</div>
      <table class="app-table"><thead><tr><th>起始日</th><th>状态</th><th class="num">决策</th><th class="num">日计划</th><th>更新</th></tr></thead><tbody>${
        tr.runs.map(r=>`<tr><td>${esc(r.start_date)}</td><td>${esc(r.status)}</td><td class="num">${r.decision_count||0}</td><td class="num">${r.plan_count||0}</td><td>${esc((r.updated_at||'').slice(0,16))}</td></tr>`).join('')
      }</tbody></table>`;
  } else {
    $('trainBlock').className='empty';
    $('trainBlock').textContent = '暂无训练会话';
  }

  const j = data.journal || {};
  if((j.cases||[]).length){
    $('journalBlock').className='';
    $('journalBlock').innerHTML = `<table class="app-table"><thead><tr><th>案例</th><th>状态</th><th class="num">记录</th><th>最近行情日</th><th></th></tr></thead><tbody>${
      j.cases.map(c=>`<tr><td><b>${esc(c.title)}</b></td><td>${esc(c.status)}</td><td class="num">${c.entry_count||0}</td><td>${esc(c.latest_market_date||'—')}</td>
      <td><a href="/stock_journal.html?code=${encodeURIComponent(data.code)}&case=${c.id}">打开</a></td></tr>`).join('')
    }</tbody></table>`;
  } else {
    $('journalBlock').className='empty';
    $('journalBlock').textContent = '暂无日记案例';
  }

  const h = data.hypotheses || {};
  if((h.items||[]).length){
    $('hypoBlock').className='';
    $('hypoBlock').innerHTML = `<table class="app-table"><thead><tr><th>标题</th><th>状态</th><th>论点</th><th>更新</th><th>推进</th></tr></thead><tbody>${
      h.items.map(it=>{
        const opts = (h.statuses||[]).map(s=>`<option value="${s}" ${s===it.status?'selected':''}>${esc((h.status_labels||{})[s]||s)}</option>`).join('');
        return `<tr>
          <td><b>${esc(it.title)}</b></td>
          <td>${badge(it.status, it.status_label)}</td>
          <td>${esc(it.thesis||it.note||'—')}</td>
          <td>${esc((it.updated_at||'').slice(0,16))}</td>
          <td><select data-id="${it.id}" class="hypo-advance">${opts}</select></td>
        </tr>`;
      }).join('')
    }</tbody></table>`;
    document.querySelectorAll('.hypo-advance').forEach(sel=>{
      sel.onchange = async ()=>{
        try{
          await api('/api/symbol/hypothesis/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:+sel.dataset.id,status:sel.value})});
          notice('假设状态已更新');
          loadCode(currentCode);
        }catch(e){notice(e.message,true)}
      };
    });
  } else {
    $('hypoBlock').className='empty';
    $('hypoBlock').textContent = '暂无假设，可在下方新建';
  }

  const stt = data.screen_to_trade || {};
  $('sttMeta').textContent = stt.generated_at ? `报告 ${stt.generated_at}` : '';
  if(stt.available && (stt.recent_trades||[]).length){
    $('sttBlock').className='';
    $('sttBlock').innerHTML = `<div class="meta" style="margin-bottom:8px">${esc(stt.module_title||stt.module||'')} · 样本 ${stt.trade_count} · 平均净收益 ${stt.avg_net_return_pct??'—'}%</div>
      <table class="app-table"><thead><tr><th>信号日</th><th>入场</th><th>退出</th><th class="num">净收益%</th><th>原因</th></tr></thead><tbody>${
        stt.recent_trades.map(t=>`<tr><td>${esc(t.signal_date)}</td><td>${esc(t.entry_date)}</td><td>${esc(t.exit_date)}</td><td class="num">${pct(t.net_return_pct)}</td><td>${esc(t.exit_reason)}</td></tr>`).join('')
      }</tbody></table>`;
  } else {
    $('sttBlock').className='empty';
    $('sttBlock').textContent = stt.message || '暂无选股→交易笔记';
  }
}

async function loadCode(code){
  const raw = (code||'').trim();
  if(!raw){$('loadErr').textContent='请输入代码';return}
  $('loadErr').textContent='';
  try{
    const data = await api('/api/symbol/context?code='+encodeURIComponent(raw));
    render(data);
    const u = new URL(location.href);
    u.searchParams.set('code', data.code);
    history.replaceState(null,'',u);
    $('codeInput').value = data.code;
  }catch(e){
    $('loadErr').textContent = e.message;
    notice(e.message,true);
  }
}

$('loadBtn').onclick = ()=>loadCode($('codeInput').value);
$('codeInput').addEventListener('keydown', e=>{ if(e.key==='Enter') loadCode($('codeInput').value) });
$('hypoCreate').onclick = async ()=>{
  $('hypoErr').textContent='';
  if(!currentCode){$('hypoErr').textContent='请先加载标的';return}
  try{
    await api('/api/symbol/hypothesis',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      code: currentCode,
      title: $('hypoTitle').value,
      status: $('hypoStatus').value,
      thesis: $('hypoThesis').value,
    })});
    $('hypoTitle').value=''; $('hypoThesis').value='';
    notice('已创建假设');
    loadCode(currentCode);
  }catch(e){$('hypoErr').textContent=e.message; notice(e.message,true)}
};

const init = new URL(location.href).searchParams.get('code') || 'sh600519';
$('codeInput').value = init;
updateTabs(init);
loadCode(init);
