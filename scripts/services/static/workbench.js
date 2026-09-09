(function () {
  'use strict';
  const shell = window.StockAppShell;
  const page = document.body.dataset.page;
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  function button(text, action, primary = false) {
    const el = document.createElement('button'); el.type = 'button'; el.className = 'wb-btn' + (primary ? ' primary' : '');
    el.textContent = text; if (action) el.addEventListener('click', action); return el;
  }
  function details(title, nodes, parent, open = false) {
    const el = document.createElement('details'); el.className = 'wb-details'; el.open = open;
    const summary = document.createElement('summary'); summary.textContent = title; el.append(summary);
    nodes.filter(Boolean).forEach(n => el.append(n)); parent.append(el); return el;
  }
  function tabs(labels, onChange) {
    const nav = document.createElement('div'); nav.className = 'wb-tabs'; nav.setAttribute('role', 'tablist');
    labels.forEach((label, i) => {
      const b = button(label, () => select(i)); b.setAttribute('role', 'tab');
      b.addEventListener('keydown', e => { if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { e.preventDefault(); const n = (i + (e.key === 'ArrowRight' ? 1 : -1) + labels.length) % labels.length; select(n); nav.children[n].focus(); } });
      nav.append(b);
    });
    function select(i) { [...nav.children].forEach((b, j) => { b.setAttribute('aria-selected', String(i === j)); b.tabIndex = i === j ? 0 : -1; }); onChange(i); }
    select(0); return nav;
  }
  function modalFor(title, content, submit) {
    const anchor = document.createComment('dialog-content'); content.before(anchor);
    const dialog = document.createElement('dialog'); dialog.className = 'wb-dialog';
    const head = document.createElement('header'); head.className = 'wb-dialog-head';
    const h = document.createElement('h2'); h.textContent = title; h.id = 'wb-dialog-title-' + $$('.wb-dialog').length;
    dialog.setAttribute('aria-labelledby', h.id);
    head.append(h, button('关闭', () => dialog.close()));
    const body = document.createElement('div'); body.className = 'wb-dialog-content'; dialog.append(head, body);
    const submitAnchor = submit ? document.createComment('dialog-submit') : null;
    if (submit) submit.before(submitAnchor);
    const foot = document.createElement('footer'); foot.className = 'wb-dialog-foot';
    if (submit) dialog.append(foot);
    let trigger;
    dialog.addEventListener('close', () => {
      const notice=$('#app-toast-host');if(notice&&dialog.contains(notice))document.body.append(notice);
      if (submit) submitAnchor.after(submit);
      anchor.after(content); content.hidden = content.dataset.dialogOnly === 'true';
      document.body.classList.remove('wb-modal-open'); if (trigger && trigger.isConnected) trigger.focus();
      window.dispatchEvent(new Event('stockapp:layout'));
    });
    document.body.append(dialog);
    return { dialog, open: () => {
      trigger = document.activeElement; body.append(content); content.hidden = false;
      if (submit) foot.append(submit); dialog.showModal(); document.body.classList.add('wb-modal-open');
      const first = $('input:not([disabled]),select:not([disabled]),textarea', body); if (first) first.focus();
    }};
  }

  // Normalize generated links, including links assigned after selecting a symbol.
  function links(root = document) {
    $$('a[href]', root).forEach(a => {
      const raw = a.getAttribute('href'); if (!raw || raw.startsWith('#')) return;
      let u; try { u = new URL(raw, location.href); } catch (_) { return; }
      if (['127.0.0.1', 'localhost', location.hostname].includes(u.hostname) && /^(http:|https:)$/.test(u.protocol)) {
        const next = shell.webUrl(u.href); if (a.href !== next) a.href = next;
      }
    });
  }
  links();
  function trainingClock() {
    const context=window.StockTrainingContext?.active;
    if(context) shell.setClock({date:context.date,time:context.time,label:'训练上下文',spoilerSafe:true});
  }
  trainingClock();window.addEventListener('stockapp:training-context',trainingClock);
  if(window.StockTrainingContext?.active && page==='symbol') {
    const info=document.createElement('p');info.className='wb-details';info.textContent='当前仅展示本次训练记录。未归档的历史选股、观察池与假设信息暂不展示。';$('.wrap').prepend(info);
    const form=$('#hypoCreate')?.closest('.panel');if(form)form.hidden=true;
  }
  if(page==='symbol') {
    const identity=$('#identityPanel'),codeNode=$('#symbolCode');
    const quote=document.createElement('div');quote.className='wb-symbol-quote';
    const price=document.createElement('strong'),meta=document.createElement('span');
    const retry=button('重试行情',()=>loadQuote(true));retry.hidden=true;
    quote.append(price,meta,retry);$('#quickLinks').before(quote);
    let loaded='',requestId=0;
    async function loadQuote(force=false) {
      const code=codeNode.textContent.trim();if(!/^(sh|sz|bj)\d{6}$/.test(code)||(!force&&code===loaded))return;
      loaded=code;const id=++requestId;price.textContent='—';meta.textContent='正在读取行情…';retry.hidden=true;
      try {
        const response=await fetch('/api/journal/kline?code='+encodeURIComponent(code)+'&days=30',{cache:'no-store'});
        if(!response.ok)throw new Error();const data=await response.json();if(id!==requestId)return;
        const latest=data.latest,value=Number(latest?.close),change=latest?.change_pct;
        if(!Number.isFinite(value)||value<=0)throw new Error();
        price.textContent=value.toFixed(value<10?3:2);price.style.color=change>0?'var(--app-up)':change<0?'var(--app-down)':'var(--app-text)';
        meta.textContent=(change!=null&&Number.isFinite(+change)?(+change>0?'+':'')+(+change).toFixed(2)+'% · ':'')+latest.date+(latest.partial?' · 模拟时刻可见行情':' · 最新缓存行情');
      }catch(_){if(id===requestId){meta.textContent='暂无有效行情';retry.hidden=false;}}
    }
    new MutationObserver(()=>loadQuote()).observe(codeNode,{childList:true,characterData:true,subtree:true});loadQuote();
  }
  new MutationObserver(records => records.forEach(r => {
    if (r.type === 'attributes') { const parent = r.target.parentElement; if (parent) links(parent); }
    else r.addedNodes.forEach(n => { if (n.nodeType === 1) links(n.parentElement || n); });
  })).observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['href'] });

  // Dialog focus handling for the existing transaction/review modals.
  const known = new Map();
  function syncModals() {
    let any = !!$('dialog[open]');
    $$('.modal,[role=dialog],.kline-panel').filter(e => e.tagName !== 'DIALOG').forEach(el => {
      const visible = !el.hidden && getComputedStyle(el).display !== 'none' && el.getBoundingClientRect().width > 0;
      if (visible) any = true;
      if (visible && !known.has(el)) { known.set(el, document.activeElement); el.setAttribute('role', 'dialog'); el.setAttribute('aria-modal', 'true'); const first = $('button,input,select,textarea,a[href]', el); if (first) first.focus(); }
      if (!visible && known.has(el)) { const previous = known.get(el); known.delete(el); if (previous && previous.isConnected) previous.focus(); }
    });
    document.body.classList.toggle('wb-modal-open', any);
  }
  const modalObserver = new MutationObserver(syncModals);
  $$('.modal,.kline-panel,[role=dialog]').forEach(e => modalObserver.observe(e, { attributes: true, attributeFilter: ['hidden','class','style'] }));
  document.addEventListener('keydown', e => {
    if (e.key !== 'Tab') return;
    const active = [...known.keys()].filter(n => n.contains(document.activeElement)).pop(); if (!active) return;
    const fields = $$('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href],[tabindex="0"]', active).filter(n => n.getClientRects().length);
    if (!fields.length) return;
    if (e.shiftKey && document.activeElement === fields[0]) { e.preventDefault(); fields.at(-1).focus(); }
    else if (!e.shiftKey && document.activeElement === fields.at(-1)) { e.preventDefault(); fields[0].focus(); }
  });

  // Charts retain their series, zoom, replay position and selection. Only presentation is merged.
  const observed = new WeakSet(), themed = new WeakMap();
  const observer = new ResizeObserver(entries => entries.forEach(({target, contentRect}) => {
    if (!window.echarts || !contentRect.width || !contentRect.height) return;
    const chart = echarts.getInstanceByDom(target); if (chart && !chart.isDisposed()) chart.resize();
  }));
  function charts(force = false) {
    if (!window.echarts) return;
    const style = getComputedStyle(document.documentElement), value = key => style.getPropertyValue(key).trim();
    const theme = document.documentElement.dataset.theme;
    $$('[_echarts_instance_]').forEach(el => {
      const chart = echarts.getInstanceByDom(el); if (!chart || chart.isDisposed()) return;
      if (!observed.has(el)) { observed.add(el); observer.observe(el); }
      if (themed.get(chart) === theme && !force) return;
      const opt = chart.getOption() || {}; if (!opt.series) return;
      const axes = key => (opt[key] || []).map(() => ({axisLabel:{color:value('--app-muted')},nameTextStyle:{color:value('--app-muted')},axisLine:{lineStyle:{color:value('--app-border')}},splitLine:{lineStyle:{color:value('--app-border')}},splitArea:{areaStyle:{color:[value('--app-surface'),value('--app-surface-2')]}}}));
      chart.setOption({backgroundColor:value('--app-surface'),textStyle:{color:value('--app-text'),fontFamily:value('--app-font')},tooltip:{backgroundColor:value('--app-surface'),borderColor:value('--app-border'),textStyle:{color:value('--app-text')},confine:true},legend:(opt.legend||[]).map(()=>({textStyle:{color:value('--app-muted')}})),xAxis:axes('xAxis'),yAxis:axes('yAxis'),series:(opt.series||[]).map(s=>s.type==='candlestick'?{itemStyle:{color:value('--app-up'),color0:value('--app-down'),borderColor:value('--app-up'),borderColor0:value('--app-down')}}:{})}, {silent:true});
      themed.set(chart, theme);
    });
  }
  let chartTimer;
  const queueCharts = () => { clearTimeout(chartTimer); chartTimer = setTimeout(() => charts(), 80); };
  new MutationObserver(queueCharts).observe(document.body, {childList:true,subtree:true});
  ['theme','density','layout'].forEach(name => window.addEventListener('stockapp:'+name, () => { charts(name === 'theme'); requestAnimationFrame(()=>$$('[_echarts_instance_]').forEach(el=>{const c=window.echarts&&echarts.getInstanceByDom(el);if(c&&el.clientWidth&&el.clientHeight)c.resize();})); }));
  charts();

  if (page === 'daily') {
    const panels = $$('.wrap>.panel');
    const statusPanel = panels.find(p => $('h2',p)?.textContent.includes('缓存'));
    if (statusPanel) {
      statusPanel.classList.add('wb-status'); $('.wrap').prepend(statusPanel);
      const generated = $('.page-head .sub')?.textContent || '';
      statusPanel.replaceChildren();
      const header = document.createElement('div'); header.className = 'wb-section-head';
      const title = document.createElement('h2'); title.textContent = '数据更新状态';
      const refresh = button('刷新状态', load); header.append(title,refresh);
      const state = document.createElement('div'); state.className = 'wb-status-state'; state.setAttribute('role','status');
      const meta = document.createElement('div'); meta.className = 'wb-status-meta';
      const rows = document.createElement('div');
      statusPanel.append(header,state,meta); details('查看更新阶段', [rows], statusPanel);
      let last = null, requestId = 0;
      async function load() {
        const id = ++requestId; refresh.disabled = true; state.textContent = '正在核对最新状态…';
        try {
          const r = await fetch('/api/system/status',{cache:'no-store'}); if (!r.ok) throw new Error();
          const data = await r.json(); if (id !== requestId) return; last = data;
          const labels = {success:'✓ 更新完成',failed:'! 更新未完成',running:'更新中',missing:'尚无更新记录',error:'状态暂不可读',unknown:'状态待确认'};
          state.textContent = labels[data.state] || labels.unknown;
          state.style.color = data.state === 'success' ? 'var(--app-text)' : 'var(--app-warn)';
          meta.textContent = '目标交易日 '+(data.target_date||'—')+' · 状态更新 '+(data.updated_at||'—');
          rows.replaceChildren();
          (data.stages||[]).forEach(s => { const row=document.createElement('p'); row.textContent=(s.ok?'✓ ':'! ')+s.name+' · '+s.message+(s.duration_seconds!=null?' · '+s.duration_seconds+'秒':'');rows.append(row); });
        } catch (_) {
          state.textContent = last ? '连接失败 · 上次读取的状态' : '连接失败 · 仅有生成时快照';
          meta.textContent = last ? '上次更新 '+(last.updated_at||'—')+' · 请重试' : generated+' · 请重试';
        } finally { if (id===requestId) refresh.disabled=false; }
      }
      window.addEventListener('focus',load); load();
    }
    const cmds = panels.find(p=>$('h2',p)?.textContent.includes('命令'));
    if(cmds) { const node = details('维护与运行命令',[$('.cmds',cmds)],$('.wrap'));cmds.remove();node.style.gridColumn='1/-1'; }
  }

  if (page === 'watchlist') {
    const wrap=$('.wrap'), panels=$$('.wrap>.panel');
    if(panels.length>=4) {
      const [add,list,track,sector]=panels;
      add.dataset.dialogOnly='true';add.hidden=true;
      const modal=modalFor('加入观察池',add,$('#addBtn'));
      const main=document.createElement('div');main.className='wb-watch-main';wrap.prepend(main);
      const bar=document.createElement('div');bar.className='wb-section-head';
      bar.append(tabs(['观察池','次日跟踪'],i=>{list.hidden=i!==0;track.hidden=i!==1;}),button('＋ 加入观察',modal.open,true));
      main.append(bar,list,track);
      const sectors=details('板块强度',[sector],wrap,innerWidth>=768);sector.classList.remove('app-reveal');
      window.addEventListener('stockapp:watch-added',()=>modal.dialog.close());
    }
  }

  if (page === 'dashboard') {
    const controls=$('.decision-tools');
    const filterDialog=modalFor('筛选条件',controls,$('#applyFilters'));
    $('#applyFilters').addEventListener('click',()=>{if(filterDialog.dialog.open)filterDialog.dialog.close();});
    const toolbar=document.createElement('div');toolbar.className='wb-screen-toolbar';
    const filterButton=button('筛选条件',filterDialog.open);filterButton.classList.add('wb-mobile-only');toolbar.append(filterButton);
    const groups=['核心指标','量价指标','风险指标','估值指标','全部指标'];
    let selectedGroup=0;
    function columnGroup(dt) {
      dt.columns().every(function(i){
        const label=this.header().textContent.trim();
        const core=['代码','名称','最新价','申万1级'].includes(label);
        const risk=/ATR|波动|回撤|高点|风险/.test(label), valuation=/PE|PB|市值|股息|估值/.test(label);
        const basic=i<12&&!risk&&!valuation;
        const visible=selectedGroup===4||core||(selectedGroup===0&&basic)||(selectedGroup===1&&!risk&&!valuation)||(selectedGroup===2&&risk)||(selectedGroup===3&&valuation);
        this.visible(visible,false);
      });
      dt.columns.adjust().draw(false);
    }
    toolbar.append(tabs(groups,i=>{selectedGroup=i;if(window.jQuery) $$('table.dataTable').forEach(t=>{if(jQuery.fn.dataTable.isDataTable(t))columnGroup(jQuery(t).DataTable());});}));
    const exportTxt=button('复制当前命中',()=>window.copyScreeningList?.());
    exportTxt.classList.add('wb-screen-export');
    exportTxt.title='复制当前策略中经过搜索和指标筛选后的六位代码+名称，便于粘贴到同花顺';
    const exportStatus=document.createElement('span');
    exportStatus.id='screenExportStatus';exportStatus.className='wb-screen-export-status';
    exportStatus.setAttribute('role','status');exportStatus.setAttribute('aria-live','polite');
    toolbar.append(exportTxt,exportStatus);
    const full=button('完整表格',()=>{const on=document.body.classList.toggle('wb-full-table');full.textContent=on?'摘要列表':'完整表格';full.setAttribute('aria-pressed',String(on));});full.classList.add('wb-mobile-only');toolbar.append(full);
    $('.content').before(toolbar);
    function attach(table) {
      if(table.dataset.wbReady)return; table.dataset.wbReady='true';
      const dt=jQuery(table).DataTable();columnGroup(dt);
      const list=document.createElement('div');list.className='wb-screen-cards';table.after(list);
      const labels=dt.columns().header().toArray().map(n=>n.textContent.trim());
      const index=label=>labels.indexOf(label);
      const plain=html=>{const div=document.createElement('div');div.innerHTML=String(html??'');return div.textContent;};
      function draw() {
        list.replaceChildren();
        dt.rows({search:'applied',order:'applied',page:'current'}).data().toArray().forEach(row=>{
          const code=plain(row[index('代码')]),name=plain(row[index('名称')]),price=plain(row[index('最新价')]);
          const item=document.createElement('article');item.className='wb-screen-card';
          const open=button('',()=>window.showKline(code));open.className='';
          const identity=document.createElement('span'),title=document.createElement('strong'),sub=document.createElement('small'),quote=document.createElement('strong');
          title.textContent=name||code;sub.textContent=code+' · '+plain(row[index('申万1级')]);quote.textContent=price;identity.append(title,sub);open.append(identity,quote);item.append(open);
          const metrics=document.createElement('div');metrics.className='wb-card-metrics';
          labels.map((label,i)=>({label,i})).filter(x=>dt.column(x.i).visible()&&!['代码','名称','最新价','申万1级'].includes(x.label)).slice(0,4).forEach(x=>{const metric=document.createElement('span');metric.textContent=x.label+' ';const v=document.createElement('b');v.textContent=plain(row[x.i]);metric.append(v);metrics.append(metric);});
          item.append(metrics);list.append(item);
        });
        if(!list.children.length){list.className='wb-screen-cards app-empty';list.textContent='没有符合条件的标的，请调整筛选。';}else list.className='wb-screen-cards';
      }
      jQuery(table).on('draw.dt',draw);draw();
      if(document.documentElement.classList.contains('screen-pending'))requestAnimationFrame(()=>{
        document.documentElement.classList.remove('screen-pending');performance.mark('screen-ready');
      });
    }
    function setup(){if(!window.jQuery?.fn.dataTable)return;$$('table.dataTable').forEach(t=>{if(jQuery.fn.dataTable.isDataTable(t))attach(t);});}
    jQuery(document).on('init.dt',setup);setup();
  }

  if(page==='trainer') {
    const setup=$('#setup');
    const grid=document.createElement('div');grid.className='wb-fees-grid';
    ['commission','minCommission','sellTax'].forEach(id=>{const input=$('#'+id);if(input)grid.append(input.closest('.field'));});
    details('费用设置',[grid],setup);
    const chartsPanel=$('.charts');if(chartsPanel){$$('.chart-block',chartsPanel).forEach((block,i)=>block.dataset.chartKind=i?'minute':'daily');const nav=tabs(['分时','日 K'],i=>{chartsPanel.dataset.chartTab=i?'daily':'minute';window.dispatchEvent(new Event('stockapp:layout'));});nav.classList.add('wb-chart-tabs');chartsPanel.prepend(nav);}
    const metrics=$('.metrics');
    const extraMetrics=document.createElement('div');extraMetrics.className='wb-account-details';
    ['mRealized','mUnrealized'].forEach(id=>extraMetrics.append($('#'+id).closest('.metric')));
    const accountDetails=details('盈亏明细与费用',[extraMetrics],$('#game'));accountDetails.classList.add('wb-account-disclosure');
    metrics.after(accountDetails);
    // Keep secondary research and planning controls below the live trading workspace.
    const supplementary=details('日计划与行情指标',[$('.plan-bar'),$('#denoiseBar'),$('#factorPanel'),$('.shortcut-hint')],$('#game'));
    supplementary.classList.add('wb-training-research');
    const dock=document.createElement('div');dock.className='wb-training-dock';
    dock.append(button('播放 / 暂停',()=>$('#play').click()),button('推进一格',()=>$('#step').click()),button('交易',()=>$('#openBuy').click(),true));document.body.append(dock);
    const game=$('#game');const sync=()=>document.body.classList.toggle('wb-training-active',!game.hidden);
    new MutationObserver(sync).observe(game,{attributes:true,attributeFilter:['hidden']});sync();
    // Existing panes share a pinned submit row; it must never scroll out of reach.
    const sheet=$('#tradeModal .sheet'), footer=document.createElement('footer');footer.className='wb-trade-footer';
    const manual=$('#manualSubmit'), condition=$('#conditionSubmit');footer.append(manual,condition);sheet.append(footer);
    const syncFooter=()=>{manual.hidden=$('#manualPane').hidden;condition.hidden=$('#conditionPane').hidden;footer.hidden=manual.hidden&&condition.hidden;};
    const panesObserver=new MutationObserver(syncFooter);['manualPane','conditionPane','managePane'].forEach(id=>panesObserver.observe($('#'+id),{attributes:true,attributeFilter:['hidden']}));syncFooter();

  }
  if(page==='journal') {
    const input=$('#reason');const form=input?.closest('.side-card');
    if(form){const submit=$('#saveEntry',form);const dialog=modalFor('记录当时的判断',form,submit);const launch=button('＋ 快速记录',dialog.open,true);launch.classList.add('wb-mobile-only');$('.topbar').append(launch);const check=()=>{if(!dialog.dialog.open)form.hidden=innerWidth<768;};window.addEventListener('resize',check);dialog.dialog.addEventListener('close',check);window.addEventListener('stockapp:journal-saved',()=>{if(dialog.dialog.open)dialog.dialog.close();});check();}
  }
  if(page==='grid') {
    const controls=$('.controls'),side=$('.main>.side');
    [ [controls,'网格参数','wb-grid-parameters'],[side,'委托、成交与持仓','wb-grid-records'] ].forEach(([node,title,cls])=>{
      if(!node)return;const anchor=document.createComment('grid-section');node.before(anchor);
      const disclosure=details(title,[node],anchor.parentElement,innerWidth>=768);disclosure.classList.add(cls);anchor.after(disclosure);
    });
  }
  // Wide tables scroll locally. No body overflow masking: actual layout issues remain detectable.
  $$('table').filter(t=>!t.closest('.dataTables_wrapper,.wb-overflow')).forEach(t=>{const wrap=document.createElement('div');wrap.className='wb-overflow';t.before(wrap);wrap.append(t);});
  $$('input,select,textarea').forEach(input=>{if(input.labels?.length||input.getAttribute('aria-label'))return;const label=input.closest('.field,.filter-field')?.querySelector('label,.field-label');if(label)input.setAttribute('aria-label',label.textContent.trim());});
})();
