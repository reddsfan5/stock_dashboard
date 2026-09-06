/* Loaded before page scripts: training links and reads carry a frozen time boundary. */
(function () {
  'use strict';
  const params = new URL(location.href).searchParams;
  let context = params.get('training_session') ? {
    session: params.get('training_session'), code: params.get('code'),
    date: params.get('replay_date'), time: params.get('replay_time')
  } : null;
  const pages = ['/trading_trainer.html','/stock_journal.html','/symbol.html','/minute_view.html','/market_news.html'];
  function url(path) {
    const u = new URL(path, location.href);
    if (!context || u.origin !== location.origin) return u.href;
    u.searchParams.set('training_session',context.session);
    u.searchParams.set('replay_date',context.date);
    u.searchParams.set('replay_time',context.time);
    if (!u.pathname.startsWith('/api/')) {
      u.searchParams.set('code',context.code);
      if (u.pathname === '/market_news.html') {
        u.searchParams.set('mode','replay'); u.searchParams.set('date',context.date); u.searchParams.set('as_of',context.time);
      }
    }
    return u.href;
  }
  const fetchOriginal = window.fetch.bind(window);
  window.fetch = function (input, options) {
    const isRequest = input instanceof Request;
    const u = new URL(isRequest ? input.url : String(input), location.href);
    if (context && u.origin === location.origin && u.pathname.startsWith('/api/') && !u.pathname.startsWith('/api/trainer/')) {
      return fetchOriginal(isRequest ? new Request(url(input.url), input) : url(String(input)), options);
    }
    return fetchOriginal(input, options);
  };
  function link(a) {
    if (!context || !a.getAttribute('href') || a.getAttribute('href').startsWith('#')) return;
    const u = new URL(a.href);
    if (u.origin !== location.origin) return;
    if (pages.includes(u.pathname)) a.href = url(a.href);
    else if (u.pathname.endsWith('.html') || u.pathname === '/') {
      a.dataset.trainingBlocked = 'true'; a.setAttribute('aria-disabled','true');
      a.title = '该页面包含最新数据，请结束训练后查看';
    }
  }
  document.addEventListener('click', e => {
    const a = e.target.closest('a'); if (!a || !context) return;
    link(a);
    if (a.dataset.trainingBlocked) { e.preventDefault(); window.StockAppShell?.toast('该页面包含最新数据，请结束训练后查看'); }
  }, true);
  window.StockTrainingContext = {
    get active() { return context; }, url,
    set(state) {
      context = state ? {session:state.session_id,code:state.code,date:state.date,time:state.time} : null;
      if (!context) { const clean=new URL(location.href);['training_session','replay_date','replay_time'].forEach(k=>clean.searchParams.delete(k));history.replaceState(null,'',clean); }
      document.querySelectorAll('a[href]').forEach(a => { delete a.dataset.trainingBlocked;a.removeAttribute('aria-disabled');link(a); });
      window.dispatchEvent(new Event('stockapp:training-context'));
    }
  };
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('a[href]').forEach(link);
  });
})();
