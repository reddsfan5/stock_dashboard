(function () {
  'use strict';

  var NAV = [
    { key: 'trainer', href: '/trading_trainer.html', label: '交易训练' },
    { key: 'minute', href: '/minute_view.html', label: '分时查询' },
    { key: 'journal', href: '/stock_journal.html', label: '选股日记' },
    { key: 'watchlist', href: '/watchlist.html', label: '观察池' },
    { key: 'news', href: '/market_news.html', label: '市场资讯' },
    { key: 'symbol', href: '/symbol.html', label: '标的上下文' },
    { key: 'daily', href: '/daily_ops.html', label: '每日操盘' },
    { key: 'grid', href: '/grid_simulator.html', label: '网格回放' },
    { key: 'home', href: '/index.html', label: '导航' }
  ];

  var PATH_KEY = {
    '/trading_trainer.html': 'trainer',
    '/minute_view.html': 'minute',
    '/stock_journal.html': 'journal',
    '/watchlist.html': 'watchlist',
    '/market_news.html': 'news',
    '/symbol.html': 'symbol',
    '/daily_ops.html': 'daily',
    '/grid_simulator.html': 'grid',
    '/index.html': 'home',
    '/': 'home'
  };

  function resolveActive(active) {
    if (active) return String(active);
    try {
      var path = (location.pathname || '').replace(/\/+$/, '') || '/';
      if (path.endsWith('/')) path = path.slice(0, -1) || '/';
      var base = path.split('/').pop() || '';
      var withSlash = '/' + base;
      return PATH_KEY[withSlash] || PATH_KEY[path] || '';
    } catch (e) {
      return '';
    }
  }

  function ensureShellHost() {
    var host = document.getElementById('app-shell');
    if (host) return host;
    if (!document.body) return null;
    host = document.createElement('div');
    host.id = 'app-shell';
    document.body.insertBefore(host, document.body.firstChild);
    return host;
  }

  function fillShell(host, active) {
    if (!host) return;
    var key = resolveActive(active || host.getAttribute('data-active'));
    if (!host.querySelector('.app-shell')) {
      var navHtml = NAV.map(function (item) {
        var cls = 'app-shell__link' + (item.key === key ? ' is-active' : '');
        return '<a class="' + cls + '" data-nav="' + item.key + '" href="' + item.href + '">' + item.label + '</a>';
      }).join('');
      host.innerHTML =
        '<header class="app-shell" role="banner">' +
          '<a class="app-shell__brand" href="/index.html">股票工作台</a>' +
          '<nav class="app-shell__nav" aria-label="主导航">' + navHtml + '</nav>' +
        '</header>' +
        '<div class="app-clock" id="app-clock" hidden>' +
          '<span class="app-clock__label">模拟时钟</span>' +
          '<span class="app-clock__time" id="app-clock-time">—</span>' +
          '<span class="app-clock__hint" id="app-clock-hint"></span>' +
        '</div>';
    } else {
      host.querySelectorAll('[data-nav]').forEach(function (el) {
        el.classList.toggle('is-active', el.getAttribute('data-nav') === key);
      });
    }
    if (key) host.setAttribute('data-active', key);
  }

  function setClock(opts) {
    opts = opts || {};
    var host = document.getElementById('app-shell');
    if (!host) return;
    var clock = host.querySelector('.app-clock') || document.getElementById('app-clock');
    if (!clock) return;
    var date = opts.date || '';
    var time = opts.time || '';
    var label = opts.label || '模拟时钟';
    var spoilerSafe = opts.spoilerSafe;
    var timeEl = clock.querySelector('#app-clock-time') || clock.querySelector('.app-clock__time');
    var hintEl = clock.querySelector('#app-clock-hint') || clock.querySelector('.app-clock__hint');
    var labelEl = clock.querySelector('.app-clock__label');
    if (labelEl) labelEl.textContent = label;
    if (timeEl) {
      timeEl.textContent = (date && time) ? (date + '  ' + time) : (date || time || '—');
    }
    if (hintEl) {
      if (spoilerSafe === false) hintEl.textContent = '';
      else if (spoilerSafe || (date && time)) hintEl.textContent = '防剧透 · 仅显示截至当前时刻';
      else hintEl.textContent = '';
    }
    var show = !!(date || time);
    clock.hidden = !show;
    clock.classList.toggle('is-visible', show);
    if (show) clock.removeAttribute('hidden');
  }

  function mount(options) {
    options = options || {};
    var host = ensureShellHost();
    if (!host) return;
    if (options.active) host.setAttribute('data-active', options.active);
    fillShell(host, options.active || host.getAttribute('data-active'));
    if (options.clock) setClock(options.clock);
  }

  function autoMount() {
    var host = document.getElementById('app-shell');
    mount({ active: host && host.getAttribute('data-active') });
  }

  window.StockAppShell = {
    mount: mount,
    setClock: setClock,
    nav: NAV
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', autoMount);
  } else {
    autoMount();
  }
})();
