(function () {
  'use strict';

  var THEME_KEY = 'stockAppTheme';
  var DENSITY_KEY = 'stockAppDensity';

  var NAV = [
    { key: 'trainer', href: '/trading_trainer.html', label: '交易训练' },
    { key: 'daily', href: '/daily_ops.html', label: '每日操盘' },
    { key: 'watchlist', href: '/watchlist.html', label: '观察池' },
    { key: 'minute', href: '/minute_view.html', label: '分时查询' },
    { key: 'journal', href: '/stock_journal.html', label: '选股日记' },
    { key: 'news', href: '/market_news.html', label: '市场资讯' },
    { key: 'symbol', href: '/symbol.html', label: '标的上下文' },
    { key: 'grid', href: '/grid_simulator.html', label: '网格回放' },
    { key: 'home', href: '/index.html', label: '全部导航' }
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

  function readPref(key, fallback) {
    try {
      var v = localStorage.getItem(key);
      return v || fallback;
    } catch (e) {
      return fallback;
    }
  }

  function writePref(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* ignore */ }
  }

  function applyTheme(theme) {
    var next = theme === 'dark' ? 'dark' : 'light';
    document.documentElement.dataset.theme = next;
    writePref(THEME_KEY, next);
    return next;
  }

  function applyDensity(density) {
    var next = density === 'compact' ? 'compact' : 'comfortable';
    document.documentElement.dataset.density = next;
    writePref(DENSITY_KEY, next);
    return next;
  }

  function initPrefs() {
    applyTheme(readPref(THEME_KEY, 'light'));
    applyDensity(readPref(DENSITY_KEY, 'comfortable'));
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

  function ensureToastHost() {
    var host = document.getElementById('app-toast-host');
    if (host) return host;
    if (!document.body) return null;
    host = document.createElement('div');
    host.id = 'app-toast-host';
    host.setAttribute('aria-live', 'polite');
    host.setAttribute('aria-relevant', 'additions');
    document.body.appendChild(host);
    return host;
  }

  function toast(message, opts) {
    opts = opts || {};
    var text = String(message == null ? '' : message).trim();
    if (!text) return;
    var tone = opts.tone || 'info';
    if (['info', 'ok', 'warn', 'error'].indexOf(tone) < 0) tone = 'info';
    var ms = opts.ms != null ? +opts.ms : (tone === 'error' ? 4200 : 3200);
    var host = ensureToastHost();
    if (!host) return;
    var el = document.createElement('div');
    el.className = 'app-toast app-toast--' + tone;
    el.setAttribute('role', tone === 'error' ? 'alert' : 'status');
    el.textContent = text;
    host.appendChild(el);
    requestAnimationFrame(function () { el.classList.add('is-visible'); });
    var remove = function () {
      el.classList.remove('is-visible');
      setTimeout(function () {
        if (el.parentNode) el.parentNode.removeChild(el);
      }, 200);
    };
    if (ms > 0) setTimeout(remove, ms);
    el.addEventListener('click', remove);
    return el;
  }

  function toolButtonsHtml() {
    var theme = document.documentElement.dataset.theme || 'light';
    var density = document.documentElement.dataset.density || 'comfortable';
    var themeLabel = theme === 'dark' ? '浅色' : '深色';
    var densityLabel = density === 'compact' ? '舒适' : '紧凑';
    return (
      '<div class="app-shell__tools" role="group" aria-label="显示设置">' +
        '<button type="button" class="app-shell__tool" data-shell-action="theme" title="切换浅色/深色">' + themeLabel + '</button>' +
        '<button type="button" class="app-shell__tool" data-shell-action="density" title="切换舒适/紧凑密度">' + densityLabel + '</button>' +
      '</div>'
    );
  }

  function syncToolLabels(host) {
    if (!host) return;
    var theme = document.documentElement.dataset.theme || 'light';
    var density = document.documentElement.dataset.density || 'comfortable';
    var themeBtn = host.querySelector('[data-shell-action="theme"]');
    var densBtn = host.querySelector('[data-shell-action="density"]');
    if (themeBtn) themeBtn.textContent = theme === 'dark' ? '浅色' : '深色';
    if (densBtn) densBtn.textContent = density === 'compact' ? '舒适' : '紧凑';
  }

  function bindTools(host) {
    if (!host || host._shellToolsBound) return;
    host._shellToolsBound = true;
    host.addEventListener('click', function (e) {
      var btn = e.target.closest('[data-shell-action]');
      if (!btn) return;
      var action = btn.getAttribute('data-shell-action');
      if (action === 'theme') {
        var cur = document.documentElement.dataset.theme || 'light';
        applyTheme(cur === 'dark' ? 'light' : 'dark');
        syncToolLabels(host);
      } else if (action === 'density') {
        var dens = document.documentElement.dataset.density || 'comfortable';
        applyDensity(dens === 'compact' ? 'comfortable' : 'compact');
        syncToolLabels(host);
      }
    });
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
          toolButtonsHtml() +
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
      if (!host.querySelector('.app-shell__tools')) {
        var shell = host.querySelector('.app-shell');
        if (shell) shell.insertAdjacentHTML('beforeend', toolButtonsHtml());
      }
      syncToolLabels(host);
    }
    bindTools(host);
    if (key) host.setAttribute('data-active', key);
  }

  function escHtml(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }

  function prefersReducedMotion() {
    try {
      return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    } catch (e) {
      return false;
    }
  }

  function ensureTickerHost(shellHost) {
    var host = shellHost || document.getElementById('app-shell');
    if (!host) return null;
    var el = document.getElementById('app-ticker');
    if (el) return el;
    el = document.createElement('div');
    el.id = 'app-ticker';
    el.className = 'app-ticker';
    el.hidden = true;
    el.setAttribute('aria-label', '行情滚动');
    var clock = host.querySelector('.app-clock') || document.getElementById('app-clock');
    if (clock && clock.parentNode === host) {
      if (clock.nextSibling) host.insertBefore(el, clock.nextSibling);
      else host.appendChild(el);
    } else {
      host.appendChild(el);
    }
    return el;
  }

  function formatTickerPct(pct) {
    if (pct == null || pct === '') return '';
    var n = Number(pct);
    if (!isFinite(n)) return '';
    return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
  }


  // 顶栏静态指数板（按显示顺序）
  var INDEX_BOARD = [
    { key: 'sh000300', match: [/沪深300/, /sh000300/i, /^000300$/] },
    { key: 'sh000688', match: [/科创50/, /sh000688/i, /^000688$/] },
    { key: 'HSI', match: [/恒生/, /恒指/, /\bHSI\b/i, /hkHSI/i] },
    { key: 'sh000001', match: [/上证/, /上证指数/, /sh000001/i, /^000001$/] },
    { key: 'IXIC', match: [/纳斯达克/, /纳指/, /\bIXIC\b/i, /NDX/i] },
    { key: 'DJIA', match: [/道琼斯/, /道指/, /\bDJIA\b/i] },
    { key: 'KS11', match: [/韩国/, /KOSPI/, /\bKS11\b/i] }
  ];
  var INDEX_BOARD_LABEL = {
    sh000300: '沪深300',
    sh000688: '科创50',
    HSI: '恒指',
    sh000001: '上证',
    IXIC: '纳指',
    DJIA: '道琼斯',
    KS11: '韩股'
  };

  function matchBoardKey(it) {
    var code = String((it && (it.code || it.key)) || '');
    var label = String((it && (it.label || it.name)) || '');
    var blob = (code + ' ' + label).trim();
    for (var i = 0; i < INDEX_BOARD.length; i++) {
      var row = INDEX_BOARD[i];
      if (code && code.toUpperCase() === row.key.toUpperCase()) return row.key;
      for (var j = 0; j < row.match.length; j++) {
        if (row.match[j].test(blob) || row.match[j].test(code) || row.match[j].test(label)) return row.key;
      }
    }
    return null;
  }

  function normalizeBoardItems(items) {
    items = Array.isArray(items) ? items : [];
    var byKey = {};
    items.forEach(function (it) {
      var key = matchBoardKey(it);
      if (!key) return;
      byKey[key] = {
        code: key,
        label: INDEX_BOARD_LABEL[key] || it.label || it.name || key,
        price: it.price,
        changePct: it.changePct != null ? it.changePct : it.change_pct,
        href: it.href || ''
      };
    });
    return INDEX_BOARD.map(function (row) {
      if (byKey[row.key]) return byKey[row.key];
      return { code: row.key, label: INDEX_BOARD_LABEL[row.key], price: null, changePct: null, empty: true };
    });
  }

  function tickerItemHtml(it) {
    it = it || {};
    var label = it.label || it.name || '';
    var empty = !!it.empty || (it.price == null || it.price === '');
    var price = empty ? '—' : String(it.price);
    var chg = it.chg || it.change || '';
    if (!chg && it.changePct != null && it.changePct !== '' && isFinite(+it.changePct)) {
      var n = +it.changePct;
      chg = (n > 0 ? '+' : '') + n.toFixed(2) + '%';
    }
    if (empty && !chg) chg = '—';
    var cls = empty ? ' is-empty' : '';
    if (!empty) {
      if (it.up || (it.changePct != null && +it.changePct > 0)) cls = ' up';
      else if (it.down || (it.changePct != null && +it.changePct < 0)) cls = ' down';
    }
    var inner =
      '<span class="app-ticker__name">' + escHtml(label) + '</span>' +
      '<span class="app-ticker__price">' + escHtml(price) + '</span>' +
      '<span class="app-ticker__chg">' + escHtml(chg) + '</span>';
    if (it.href && !empty) {
      return '<a class="app-ticker__item' + cls + '" href="' + escHtml(it.href) + '">' + inner + '</a>';
    }
    return '<span class="app-ticker__item' + cls + '">' + inner + '</span>';
  }

  function setTicker(items) {
    var host = ensureShellHost();
    if (!host) return;
    var ticker = ensureTickerHost(host);
    if (!ticker) return;
    var board = normalizeBoardItems(items);
    var hasAny = board.some(function (x) { return !x.empty; });
    if (!hasAny && (!items || !items.length)) {
      // still show empty placeholders so layout is stable on home
    }
    ticker.classList.add('app-ticker--static');
    ticker.innerHTML =
      '<div class="app-ticker__track" role="list">' + board.map(tickerItemHtml).join('') + '</div>';
    ticker.hidden = false;
    ticker.classList.add('is-visible');
    ticker.removeAttribute('hidden');
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
    ensureTickerHost(host);
  }

  function observeReveal(root) {
    root = root || document;
    var nodes = root.querySelectorAll('.app-reveal, [data-reveal]');
    if (!nodes.length) return;
    var list = Array.prototype.slice.call(nodes);
    list.forEach(function (el) {
      if (!el.classList.contains('app-reveal')) el.classList.add('app-reveal');
    });
    if (prefersReducedMotion() || !window.IntersectionObserver) {
      list.forEach(function (el) { el.classList.add('is-in'); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('is-in');
        io.unobserve(entry.target);
      });
    }, { rootMargin: '0px 0px -6% 0px', threshold: 0.06 });
    list.forEach(function (el) {
      if (el.classList.contains('is-in')) return;
      io.observe(el);
    });
  }


  function bindClearOnFocus(input, options) {
    options = options || {};
    if (!input) return null;
    if (input.__clearOnFocusCtl) return input.__clearOnFocusCtl;
    var ctl = { armed: true };
    ctl.arm = function () { ctl.armed = true; };
    ctl.disarm = function () { ctl.armed = false; };
    var clearOnce = function () {
      if (!ctl.armed) return;
      if (!String(input.value || '').trim()) return;
      ctl.armed = false;
      input.value = '';
      if (typeof options.onClear === 'function') options.onClear();
    };
    input.addEventListener('focus', clearOnce);
    input.addEventListener('pointerdown', clearOnce);
    input.__clearOnFocusCtl = ctl;
    input.dataset.clearOnFocus = '1';
    return ctl;
  }

  function bindClearOnFocusAll(root) {
    root = root || document;
    var nodes = root.querySelectorAll('[data-clear-on-focus]');
    Array.prototype.forEach.call(nodes, function (el) {
      bindClearOnFocus(el);
    });
  }

  function mount(options) {
    options = options || {};
    initPrefs();
    var host = ensureShellHost();
    if (!host) return;
    if (options.active) host.setAttribute('data-active', options.active);
    fillShell(host, options.active || host.getAttribute('data-active'));
    ensureTickerHost(host);
    if (options.clock) setClock(options.clock);
    if (options.ticker) setTicker(options.ticker);
    ensureToastHost();
    observeReveal(document);
    bindClearOnFocusAll(document);
  }

  function autoMount() {
    initPrefs();
    var host = document.getElementById('app-shell');
    mount({ active: host && host.getAttribute('data-active') });
  }

  window.StockAppShell = {
    mount: mount,
    setClock: setClock,
    setTicker: setTicker,
    observeReveal: observeReveal,
    toast: toast,
    setTheme: applyTheme,
    setDensity: applyDensity,
    bindClearOnFocus: bindClearOnFocus,
    bindClearOnFocusAll: bindClearOnFocusAll,
    nav: NAV
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', autoMount);
  } else {
    autoMount();
  }
})();
