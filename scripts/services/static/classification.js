(() => {
 'use strict';
 const esc=value=>String(value??'').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
 const pct=v=>v==null?'未披露':Number(v).toFixed(2)+'%';
 const money=v=>v==null?'未披露':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:2});
 const plain=value=>new DOMParser().parseFromString(String(value??''),'text/html').body.textContent;
 const endpoint='/api/classification';let active='',version=0,timer;
 const panel=document.getElementById('classificationPanel');
 const compactName=value=>String(value??'').replace(/[_\sⅠⅡⅢⅣⅤⅥIVX]+/gi,'');
 function boardDisplayLevel(board,industries){
  const name=String(board.name||''),compact=compactName(name);
  const industryNames=industries.map(item=>compactName(item.name)).filter(Boolean);
  if(industryNames.some(industry=>compact===industry||compact.includes(industry)||industry.includes(compact)))return {tier:3,kind:'core',label:'行业主线'};
  if(/(?:HS\d|MSCI|上证|深证|中证|央视|富时|标普|标准普尔|指数|成份|成分)/i.test(name))return {tier:1,kind:'index',label:'指数成分'};
  if(/(?:沪股通|深股通|融资融券|转融券|大盘股|中盘股|小盘股|权重股|高价股|低价股|百元股|风格|龙头|超级品牌|茅指数)/.test(name))return {tier:1,kind:'style',label:'风格资格'};
  if(/(?:板块)$/.test(name))return {tier:2,kind:'region',label:'地域板块'};
  return {tier:2,kind:'theme',label:'主题概念'};
 }
 function boardCloud(boards,industries){
  return boards.map((board,index)=>Object.assign({board,index},boardDisplayLevel(board,industries)))
   .sort((a,b)=>b.tier-a.tier||a.index-b.index)
   .map(item=>`<a class="classification-cloud-tag classification-cloud-tier-${item.tier}" data-kind="${item.kind}" href="${esc(item.board.source_url)}" target="_blank" rel="noopener" title="${esc(item.label)} · 首次观察 ${esc(item.board.observed_from)} · 字号仅表示资料层级，不代表强弱">${esc(item.board.name)}</a>`).join('');
 }
 async function request(url,options){const response=await fetch(url,options);const data=await response.json();if(!response.ok)throw new Error(data.error||'读取失败');return data;}
 function draw(data){
  const selectedPeriod=panel.querySelector('#businessPeriod')?.value,selectedDimension=panel.querySelector('#businessDimension')?.value;
  const stock=data.code,industry=data.boards.filter(x=>x.source==='shenwan'),boards=data.boards.filter(x=>x.source!=='shenwan');
  const statuses=data.sync.filter(x=>x.dataset!=='shenwan');
  const statusText=statuses.map(x=>(x.dataset==='business'?'主营构成':'所属板块')+'：'+(x.success_at?'成功同步 '+new Date(x.success_at).toLocaleString():'尚无成功数据')+(x.status==='error'?' · 最近同步失败，保留缓存':'')).join('；');
  panel.innerHTML=`<h2>资料明细 <button id="classificationRefresh" ${data.pending?'disabled':''}>${data.pending?'同步中…':'刷新资料'}</button></h2><p class="meta">${esc(statusText||'尚未同步，正在请求资料')}</p><h3>申万行业</h3><div class="classification-tags">${industry.map(x=>`<span>${esc(x.kind)} · ${esc(x.name)}</span>`).join('')||'暂无行业分类'}</div><div class="classification-cloud-heading"><div><h3>东方财富所属板块</h3><p class="meta">行业主线优先，主题概念居中，指数与风格等长尾信息弱化展示。</p></div><div class="classification-cloud-legend" aria-label="标签云图例"><span class="is-core">主线</span><span class="is-theme">主题</span><span class="is-tail">长尾</span></div></div><div class="classification-tag-cloud" aria-label="东方财富所属板块标签云">${boardCloud(boards,industry)||'<span class="meta">尚无缓存 / 源站未返回</span>'}</div><div class="classification-cloud-actions"><a href="/sector_corr_cloud.html?stock=${encodeURIComponent(stock)}&amp;from=symbol">查看板块相关点云 <span aria-hidden="true">→</span></a><span>申万二级同步相关与领先传导 · 当前结构参考</span></div><p class="classification-cloud-note">标签大小只表示资料层级，不代表板块强弱、相关度或收益预测；首次观察时间也不是概念实际纳入日期。点云使用独立的申万二级关系口径，不等同于东方财富概念标签。</p><p class="meta">概念收入占比：未披露（当前数据源未单列）。主营业务分项不会自动归算到概念。</p><h3>主营构成</h3><p class="meta">金额单位：元；收入、利润比例按源站报告口径。产品、行业、地区分别查看，不能跨维度相加。</p><div class="classification-selectors"><label>报告期<select id="businessPeriod"></select></label><label>分类维度<select id="businessDimension"><option>产品</option><option>行业</option><option>地区</option></select></label></div><div class="classification-table" id="businessTable"></div><a class="meta" href="https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/Index?code=${stock.toUpperCase()}" target="_blank" rel="noopener">东方财富主营构成原文 ↗</a><details class="classification-notes"><summary>数据商业务解析与依据</summary><p class="meta">以下为源站核心题材文本，未逐条绑定概念标签。</p>${data.business_notes.map(x=>`<details><summary>${esc(x.KEYWORD||'业务说明')}</summary><p>${esc(plain(x.MAINPOINT_CONTENT))}</p></details>`).join('')||'<p>暂无业务解析</p>'}<a href="https://emweb.securities.eastmoney.com/PC_HSF10/CoreConception/Index?code=${stock.toUpperCase()}" target="_blank" rel="noopener">东方财富原文 ↗</a></details><p class="classification-message" role="status"></p>`;
  const period=panel.querySelector('#businessPeriod'),dimension=panel.querySelector('#businessDimension');
  [...new Set(data.segments.map(x=>x.period))].forEach(value=>period.add(new Option(value,value)));
  if(selectedPeriod&&[...period.options].some(x=>x.value===selectedPeriod))period.value=selectedPeriod;
  if(selectedDimension)dimension.value=selectedDimension;
  const dimensions=data.segments.filter(x=>x.period===period.value).map(x=>x.dimension);if(dimensions.length&&!dimensions.includes(dimension.value))dimension.value=dimensions[0];
  function table(){const rows=data.segments.filter(x=>x.period===period.value&&x.dimension===dimension.value);panel.querySelector('#businessTable').innerHTML=rows.length?'<table><thead><tr><th>业务名称</th><th>收入（元）</th><th>收入占比</th><th>主营利润（元）</th><th>利润占比</th><th>毛利率</th></tr></thead><tbody>'+rows.map(x=>`<tr><td>${esc(x.item)}</td><td>${money(x.revenue)}</td><td>${pct(x.revenue_pct)}</td><td>${money(x.profit)}</td><td>${pct(x.profit_pct)}</td><td>${pct(x.gross_margin_pct)}</td></tr>`).join('')+'</tbody></table>':'<p class="empty">该报告期 / 维度尚无披露缓存。可刷新重试。</p>';}
  period.onchange=dimension.onchange=table;table();
  panel.querySelector('#classificationRefresh').onclick=()=>synchronize(stock,true,version);
 }
 async function synchronize(code,force,token){
  try{const data=await request(endpoint+'/sync',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code,force})});if(token!==version)return;if(data.pending){panel.querySelector('#classificationRefresh').disabled=true;panel.querySelector('.classification-message').textContent='正在后台同步，已有内容保持可读…';timer=setTimeout(()=>poll(code,token,0),1800);}else panel.querySelector('.classification-message').textContent='已检查缓存；刚同步过的数据将在冷却期后更新。';}
  catch(e){if(token===version)panel.querySelector('.classification-message').textContent=e.message;}
 }
 async function poll(code,token,count){try{const data=await request(endpoint+'?code='+encodeURIComponent(code));if(token!==version)return;if(data.pending&&count<200){timer=setTimeout(()=>poll(code,token,count+1),1800);return;}draw(data);}catch(e){if(token===version)panel.querySelector('.classification-message').textContent='同步状态读取失败，可点击刷新重试。';}}
 async function load(code,training){if(!panel)return;clearTimeout(timer);const token=++version;active=code;
  if(training||window.StockTrainingContext?.active){panel.hidden=true;return;}panel.hidden=false;
  panel.innerHTML='<h2>行业、概念与业务构成</h2><p>正在读取本地数据库…</p>';
  try{const data=await request(endpoint+'?code='+encodeURIComponent(code));if(token!==version)return;draw(data);const relevant=data.sync.filter(x=>x.dataset==='eastmoney'||x.dataset==='business');if(data.pending)timer=setTimeout(()=>poll(code,token,0),1800);else if(relevant.length<2||relevant.some(x=>Date.now()-Date.parse(x.attempted_at)>86400000))await synchronize(code,false,token);}catch(e){if(token===version){panel.innerHTML='<h2>行业、概念与业务构成</h2><p>'+esc(e.message)+'</p><button id="classificationRetry">重试读取</button>';panel.querySelector('button').onclick=()=>load(code,training);}}
 }
 window.addEventListener('stock:classification',e=>load(e.detail.code,e.detail.training));
 if(panel){const code=new URL(location.href).searchParams.get('code');if(code)load(code,false);}
 const members=document.getElementById('atlasMemberships');
 if(members){request(endpoint+'/members?name='+encodeURIComponent(members.dataset.board)).then(data=>{members.innerHTML='<p class="meta">已同步 '+data.synced_symbols+' 只股票中的关联结果，共 '+data.items.length+' 条；同步覆盖尚非全市场认证成分表。</p><div class="classification-tags">'+data.items.map(x=>`<a href="/symbol.html?code=${encodeURIComponent(x.code)}">${esc(x.security_name||x.code)} · ${esc(x.code)} →</a>`).join('')+'</div>';if(!data.items.length)members.innerHTML+='<p>本地尚未同步到该同名板块成员。</p>';}).catch(()=>{members.innerHTML='<p>数据库读取失败，请刷新页面重试。正文示例仍可阅读。</p>';});}
})();
