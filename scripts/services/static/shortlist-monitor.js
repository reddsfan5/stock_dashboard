(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const windows = new Set([1, 3, 5, 10, 20]);
  const url = new URL(location.href);
  let horizon = windows.has(Number(url.searchParams.get('horizon'))) ? Number(url.searchParams.get('horizon')) : 5;
  let offset = 0;
  let page = [];
  let pagination = {total: 0, returned: 0, has_more: false};
  let batches = [];
  let loadSequence = 0;
  const pageSize = 100;

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
  }
  function percentage(value) {
    if (value == null || !Number.isFinite(Number(value))) return '—';
    const number = Number(value);
    return (number > 0 ? '+' : '') + number.toFixed(2) + '%';
  }
  function rate(value) { return value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toFixed(2) + '%'; }
  function tone(value) { return value == null ? '' : Number(value) > 0 ? 'monitor-up' : Number(value) < 0 ? 'monitor-down' : ''; }
  function money(value) { return value == null ? '—' : Number(value).toFixed(3); }
  function shortTime(value) { return value ? String(value).replace('T', ' ').slice(0, 16) : '—'; }
  function dateLink(day) { return '/shortlist.html?date=' + encodeURIComponent(day); }
  function selectedBatch() {
    const from = $('fromDate').value;
    return from && from === $('toDate').value ? from : '';
  }
  function selectBatch(day) {
    $('fromDate').value = day;
    $('toDate').value = day;
    offset = 0;
    load();
  }
  function renderBatches(items) {
    batches = items;
    const selected = selectedBatch();
    const known = batches.some(item => item.market_date === selected);
    const custom = ($('fromDate').value || $('toDate').value) && !known;
    $('batchSelect').innerHTML = '<option value="">全部批次</option>' +
      batches.map(item => `<option value="${escapeHtml(item.market_date)}">${escapeHtml(item.market_date)} · ${Number(item.count) || 0} 只</option>`).join('') +
      (custom ? '<option value="custom">自定义日期范围</option>' : '');
    $('batchSelect').value = known ? selected : custom ? 'custom' : '';
    const index = batches.findIndex(item => item.market_date === selected);
    $('olderBatch').disabled = index < 0 || index >= batches.length - 1;
    $('newerBatch').disabled = index <= 0;
  }
  function valueCell(label, value, extraClass) {
    return `<td data-label="${escapeHtml(label)}" class="number monitor-value ${extraClass || ''}">${escapeHtml(value)}</td>`;
  }
  function showNotice(message, type) {
    const node = $('notice');
    node.className = 'monitor-alert' + (type ? ' is-' + type : '');
    node.textContent = message;
  }
  function statusLabel(status) {
    return {complete: '已完成', partial: '观察中', pending: '等待入场', unavailable: '无数据'}[status] || status || '—';
  }
  function fromUrl() {
    for (const [key, id] of [['from', 'fromDate'], ['to', 'toDate'], ['rank_max', 'rankMax'], ['sector', 'sector'], ['status', 'status']]) {
      const value = url.searchParams.get(key);
      if (value) {
        if (id === 'sector') $('sector').add(new Option(value, value));
        $(id).value = value;
      }
    }
    document.querySelectorAll('[data-horizon]').forEach(button => button.classList.toggle('is-active', Number(button.dataset.horizon) === horizon));
    updateBackLink();
  }
  function updateBackLink() {
    const selected = $('fromDate').value && $('fromDate').value === $('toDate').value ? $('fromDate').value : '';
    const href = selected ? dateLink(selected) : '/shortlist.html';
    $('backShortlist').href = href;
    $('heroShortlist').href = href;
    $('contextHorizon').textContent = horizon + ' 个交易日';
    $('contextRange').textContent = selected || ($('fromDate').value || $('toDate').value ? `${$('fromDate').value || '最早'} → ${$('toDate').value || '最新'}` : '全部历史批次');
  }
  function syncUrl() {
    const next = new URL(location.href);
    next.search = '';
    next.searchParams.set('horizon', String(horizon));
    for (const [key, id] of [['from', 'fromDate'], ['to', 'toDate'], ['rank_max', 'rankMax'], ['sector', 'sector'], ['status', 'status']]) {
      const value = $(id).value.trim();
      if (value) next.searchParams.set(key, value);
    }
    history.replaceState(null, '', next);
    updateBackLink();
  }
  function renderSectors(options) {
    const select = $('sector');
    const selected = select.value;
    const names = [...new Set(options.filter(Boolean))].sort((a, b) => a.localeCompare(b, 'zh'));
    if (selected && !names.includes(selected)) names.unshift(selected);
    select.innerHTML = '<option value="">全部板块</option>' + names.map(name => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join('');
    select.value = selected;
  }
  function metric(label, value, hint, valueTone) {
    return `<article class="monitor-metric"><span>${escapeHtml(label)}</span><strong class="${valueTone || ''}">${escapeHtml(value)}</strong><small>${escapeHtml(hint)}</small></article>`;
  }
  function renderSummary(summary) {
    $('peakMedian').textContent = percentage(summary.peak_median_pct);
    $('peakMedian').className = 'monitor-lead-value ' + tone(summary.peak_median_pct);
    $('closeMedian').textContent = percentage(summary.close_median_pct);
    $('closeMedian').className = tone(summary.close_median_pct);
    $('metrics').innerHTML = [
      metric('样本 / 完成度', `${summary.count || 0} 只`, `${summary.completed || 0} 完成 · ${summary.partial || 0} 观察中 · ${summary.pending || 0} 待入场`),
      metric('峰值 ≥ 5%', rate(summary.peak_hit_5pct_rate_pct), '仅已出现价格的样本'),
      metric('已观察收盘为正', rate(summary.close_positive_rate_pct), '观察中数据并非窗口最终结果'),
      metric('最大不利 · 中位', percentage(summary.adverse_median_pct), '窗口内最低价', tone(summary.adverse_median_pct)),
      metric('沪深300收盘 · 中位', percentage(summary.benchmark_close_median_pct), '同期指数表现', tone(summary.benchmark_close_median_pct)),
      metric('收盘超额 · 中位', percentage(summary.close_excess_median_pct), '个股减去沪深300', tone(summary.close_excess_median_pct))
    ].join('');
  }
  function renderCohorts(cohorts) {
    $('cohortCount').textContent = cohorts.length + ' 个批次';
    $('cohortBody').innerHTML = cohorts.map(row => `<tr class="${selectedBatch() === row.market_date ? 'is-selected-batch' : ''}">
      <td data-label="精选日期"><button type="button" class="monitor-batch-link" data-batch="${escapeHtml(row.market_date)}" aria-label="查看 ${escapeHtml(row.market_date)} 批次">${escapeHtml(row.market_date)}</button><small>${row.partial || 0} 只观察中 · ${row.pending || 0} 只待入场</small><a class="monitor-batch-return" href="${dateLink(row.market_date)}">当日精选 ↗</a></td>
      ${valueCell('样本 / 已完成', `${row.count || 0} / ${row.completed || 0}`)}
      ${valueCell('峰值中位', percentage(row.peak_median_pct), tone(row.peak_median_pct))}
      ${valueCell('+5% 命中', rate(row.peak_hit_5pct_rate_pct))}
      ${valueCell('收盘中位', percentage(row.close_median_pct), tone(row.close_median_pct))}
      ${valueCell('沪深300收盘', percentage(row.benchmark_close_median_pct), tone(row.benchmark_close_median_pct))}
      ${valueCell('收盘超额', percentage(row.close_excess_median_pct), tone(row.close_excess_median_pct))}
    </tr>`).join('') || '<tr><td class="monitor-empty" colspan="7">这个范围还没有收益记录。可返回每日精选选择其他日期。</td></tr>';
  }
  function renderItems() {
    const rows = page;
    $('itemCount').textContent = pagination.total + ' 条记录';
    $('itemBody').innerHTML = rows.map(row => `<tr>
      <td data-label="标的 / 精选日期"><a href="/symbol.html?code=${encodeURIComponent(row.code)}">${escapeHtml(row.name || row.code)}</a><small>${escapeHtml(row.code)} · 排名 ${escapeHtml(row.rank)}</small><div class="monitor-date-sub"><a href="${dateLink(row.market_date)}">${escapeHtml(row.market_date)} 每日精选</a></div></td>
      <td data-label="板块">${escapeHtml(row.sector || '—')}</td>
      <td data-label="状态"><span class="monitor-status ${escapeHtml(row.status)}">${escapeHtml(statusLabel(row.status))}</span></td>
      <td data-label="入场价 / 日期">${escapeHtml(money(row.entry_open))}<small>${escapeHtml(row.entry_date || '尚未入场')}</small></td>
      ${valueCell('理论峰值', percentage(row.peak_return_pct), tone(row.peak_return_pct))}
      ${valueCell('窗口收盘', percentage(row.close_return_pct), tone(row.close_return_pct))}
      ${valueCell('最大不利', percentage(row.adverse_return_pct), tone(row.adverse_return_pct))}
      ${valueCell('峰值超额', percentage(row.peak_excess_pct), tone(row.peak_excess_pct))}
    </tr>`).join('') || '<tr><td class="monitor-empty" colspan="8">没有符合筛选条件的候选。</td></tr>';
    $('pageInfo').textContent = pagination.total ? `${offset + 1}–${offset + rows.length} / ${pagination.total}` : '0 条';
    $('prevPage').disabled = offset === 0;
    $('nextPage').disabled = !pagination.has_more;
  }
  async function readJson(response) {
    if (response.redirected && response.url.includes('/login.html')) throw new Error('登录已失效，请重新登录后打开收益监控。');
    const contentType = response.headers.get('content-type') || '';
    if (!contentType.toLowerCase().includes('application/json')) {
      throw new Error('当前网页服务未提供收益监控接口。请重启项目交互服务，再刷新本页。');
    }
    const result = await response.json();
    if (response.status === 401) throw new Error('登录已失效，请重新登录后打开收益监控。');
    if (!response.ok) throw new Error(result.error || `接口请求失败（${response.status}）`);
    return result;
  }
  async function load() {
    const sequence = ++loadSequence;
    syncUrl();
    const query = new URLSearchParams({horizon: String(horizon), offset: String(offset), limit: String(pageSize), sort: $('sort').value});
    for (const [key, id] of [['from', 'fromDate'], ['to', 'toDate'], ['rank_max', 'rankMax'], ['sector', 'sector'], ['status', 'status']]) {
      const value = $(id).value.trim();
      if (value) query.set(key, value);
    }
    showNotice('正在读取本地收益记录…');
    try {
      const response = await fetch('/api/shortlist/monitor?' + query, {cache: 'no-store', headers: {'Accept': 'application/json'}});
      const data = await readJson(response);
      if (sequence !== loadSequence) return;
      page = data.items || [];
      pagination = data.pagination || {total: page.length, returned: page.length, has_more: false};
      renderBatches(data.batches || []);
      renderSectors(data.sectors || []);
      renderSummary(data.summary || {});
      renderCohorts(data.cohorts || []);
      renderItems();
      $('contextAsOf').textContent = data.resolved_as_of || '—';
      $('contextFreshness').textContent = shortTime(data.freshness);
      const lastError = data.meta && data.meta.last_error;
      showNotice(lastError ? `最近一次计算失败：${lastError}。下方保留上次成功结果。` : `已读取 ${pagination.total} 条候选；数据截止 ${data.resolved_as_of || '未知'}。等待入场和观察中的窗口不会显示为 0%。`, lastError ? 'warning' : '');
    } catch (error) {
      if (sequence !== loadSequence) return;
      page = []; pagination = {total: 0, returned: 0, has_more: false};
      $('peakMedian').textContent = '—'; $('closeMedian').textContent = '—';
      $('metrics').innerHTML = '';
      $('cohortBody').innerHTML = '<tr><td class="monitor-empty" colspan="7">暂时无法读取批次表现。</td></tr>';
      $('itemBody').innerHTML = '<tr><td class="monitor-empty" colspan="8">暂时无法读取候选明细。</td></tr>';
      $('cohortCount').textContent = '—'; $('itemCount').textContent = '—';
      $('contextAsOf').textContent = '—'; $('contextFreshness').textContent = '—';
      $('pageInfo').textContent = '—'; $('prevPage').disabled = true; $('nextPage').disabled = true;
      showNotice(error.message, 'error');
    }
  }
  function init() {
    fromUrl();
    $('batchSelect').addEventListener('change', () => {
      const day = $('batchSelect').value;
      if (day === 'custom') return;
      selectBatch(day);
    });
    $('olderBatch').addEventListener('click', () => {
      const index = batches.findIndex(item => item.market_date === selectedBatch());
      if (index >= 0 && index + 1 < batches.length) selectBatch(batches[index + 1].market_date);
    });
    $('newerBatch').addEventListener('click', () => {
      const index = batches.findIndex(item => item.market_date === selectedBatch());
      if (index > 0) selectBatch(batches[index - 1].market_date);
    });
    $('cohortBody').addEventListener('click', event => {
      const button = event.target.closest('[data-batch]');
      if (button) selectBatch(button.dataset.batch);
    });
    document.querySelectorAll('[data-horizon]').forEach(button => button.addEventListener('click', () => {
      horizon = Number(button.dataset.horizon); offset = 0;
      document.querySelectorAll('[data-horizon]').forEach(item => item.classList.toggle('is-active', item === button));
      load();
    }));
    for (const id of ['fromDate', 'toDate', 'rankMax', 'sector', 'status']) $(id).addEventListener('change', () => { offset = 0; load(); });
    $('sort').addEventListener('change', () => { offset = 0; load(); });
    $('resetFilters').addEventListener('click', () => { for (const id of ['fromDate', 'toDate', 'rankMax', 'sector', 'status']) $(id).value = ''; offset = 0; load(); });
    $('prevPage').addEventListener('click', () => { offset = Math.max(0, offset - pageSize); load(); });
    $('nextPage').addEventListener('click', () => { if (pagination.has_more) { offset += pageSize; load(); } });
    load();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
}());
