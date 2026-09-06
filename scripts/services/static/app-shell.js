(function () {
  'use strict';

  var THEME_KEY = 'stockAppTheme';
  var DENSITY_KEY = 'stockAppDensity';

  var NAV = [
    {key:'daily',href:'/daily_ops.html',label:'每日操盘',icon:'◫',group:'日常看盘'},
    {key:'dashboard',href:'/dashboard.html',label:'选股仪表盘',icon:'▦',group:'日常看盘'},
    {key:'watchlist',href:'/watchlist.html',label:'观察池',icon:'☆',group:'日常看盘'},
    {key:'symbol',href:'/symbol.html',label:'标的上下文',icon:'◎',group:'日常看盘'},
    {key:'minute',href:'/minute_view.html',label:'分时查询',icon:'⌁',group:'日常看盘'},
    {key:'news',href:'/market_news.html',label:'市场资讯',icon:'≡',group:'日常看盘'},
    {key:'trainer',href:'/trading_trainer.html',label:'交易训练',icon:'▷',group:'交易训练'},
    {key:'grid',href:'/grid_simulator.html',label:'网格回放',icon:'⊞',group:'交易训练'},
    {key:'journal',href:'/stock_journal.html',label:'选股日记',icon:'▤',group:'研究复盘'},
    {key:'home',href:'/index.html',label:'全部工具',icon:'⋯',group:'研究复盘'}
  ];
  var PATH_KEY = {'/':'home'};
  NAV.forEach(function(item){PATH_KEY[item.href]=item.key;});
  function emit(name, value) { window.dispatchEvent(new CustomEvent('stockapp:'+name, {detail:value})); }
  function webUrl(path) {
    var url = new URL(path, location.href);
    if (url.hostname === '127.0.0.1' || url.hostname === 'localhost') url.hostname = location.hostname;
    if (location.port === '8000') url.port = '8765';
    return window.StockTrainingContext?.active ? window.StockTrainingContext.url(url.href) : url.href;
  }

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
    emit('theme', next);
    return next;
  }

  function applyDensity(density) {
    var next = density === 'compact' ? 'compact' : 'comfortable';
    document.documentElement.dataset.density = next;
    writePref(DENSITY_KEY, next);
    emit('density', next);
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
    // Native dialogs are in the browser top layer; notices must be inside it.
    var noticeParent = document.querySelector('dialog[open]') || document.body;
    if (host.parentElement !== noticeParent) noticeParent.appendChild(host);
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
    document.body.classList.add('app-workbench');
    document.body.dataset.page = key;
    if (!host.querySelector('.wb-sidebar')) {
      var group = '';
      var nav = NAV.map(function(item){
        var heading = item.group === group ? '' : '<div class="wb-nav-group">'+item.group+'</div>';
        group=item.group;
        return heading+'<a class="wb-nav-link" data-nav="'+item.key+'" href="'+webUrl(item.href)+'" title="'+item.label+'"><span class="wb-nav-icon" aria-hidden="true">'+item.icon+'</span><span class="wb-nav-label">'+item.label+'</span></a>';
      }).join('');
      var item=NAV.find(function(n){return n.key===key;});
      host.innerHTML='<aside class="wb-sidebar" id="wb-sidebar"><a class="wb-brand" href="'+webUrl('/daily_ops.html')+'"><span class="wb-logo">Q</span><span class="wb-nav-label">量化工作台<small>研究 · 交易 · 复盘</small></span></a><nav aria-label="主导航">'+nav+'</nav><div class="wb-sidebar-foot">A 股研究工作台</div></aside>'+
        '<button class="wb-scrim" hidden aria-label="关闭导航"></button>'+
        '<header class="wb-topbar"><button class="wb-menu" aria-label="切换导航" aria-controls="wb-sidebar" aria-expanded="false">☰</button><span class="wb-page-name">'+(item?item.label:'量化工作台')+'</span>'+toolButtonsHtml()+'</header>'+
        '<div class="app-clock" id="app-clock" hidden><span class="app-clock__label">模拟时钟</span><span class="app-clock__time" id="app-clock-time">—</span><span class="app-clock__hint" id="app-clock-hint"></span></div>'+
        '<nav class="wb-bottom" aria-label="快捷导航">'+['daily','dashboard','trainer','journal'].map(function(k){var n=NAV.find(function(v){return v.key===k;});return '<a data-nav="'+k+'" href="'+webUrl(n.href)+'"><span aria-hidden="true">'+n.icon+'</span>'+({daily:'操盘',dashboard:'选股',trainer:'训练',journal:'日记'}[k])+'</a>';}).join('')+'</nav>';
      var menu=host.querySelector('.wb-menu'),scrim=host.querySelector('.wb-scrim'),side=host.querySelector('.wb-sidebar');
      function syncLayout(){
        var mobile=innerWidth<768;
        var collapsed=readPref('stockAppSidebar',innerWidth<1280?'collapsed':'expanded')==='collapsed';
        document.documentElement.dataset.sidebar=collapsed?'collapsed':'expanded';
        if(!mobile){document.body.classList.remove('wb-nav-open');scrim.hidden=true;side.inert=false;}
        else side.inert=!document.body.classList.contains('wb-nav-open');
        menu.setAttribute('aria-expanded',String(mobile?document.body.classList.contains('wb-nav-open'):!collapsed));
        emit('layout',{collapsed:collapsed,mobile:mobile});
      }
      function closeNav(){document.body.classList.remove('wb-nav-open');scrim.hidden=true;syncLayout();menu.focus();}
      menu.onclick=function(){
        if(innerWidth<768){var open=document.body.classList.toggle('wb-nav-open');scrim.hidden=!open;syncLayout();if(open)side.querySelector('a').focus();}
        else {writePref('stockAppSidebar',document.documentElement.dataset.sidebar==='collapsed'?'expanded':'collapsed');syncLayout();}
      };
      scrim.onclick=closeNav;
      document.addEventListener('keydown',function(e){
        if(!document.body.classList.contains('wb-nav-open'))return;
        if(e.key==='Escape'){e.preventDefault();closeNav();}
        if(e.key==='Tab'){var links=side.querySelectorAll('a');if(e.shiftKey&&document.activeElement===links[0]){e.preventDefault();links[links.length-1].focus();}else if(!e.shiftKey&&document.activeElement===links[links.length-1]){e.preventDefault();links[0].focus();}}
      });
      window.addEventListener('resize',syncLayout);syncLayout();
    }
    host.querySelectorAll('[data-nav]').forEach(function(el){var on=el.dataset.nav===key;el.classList.toggle('is-active',on);if(on)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');});
    bindTools(host);syncToolLabels(host);
    if(key)host.setAttribute('data-active',key);
    if(!document.getElementById('workbench-css')){var css=document.createElement('link');css.id='workbench-css';css.rel='stylesheet';css.href='/assets/workbench.css';document.head.appendChild(css);}
    if(!document.getElementById('workbench-js')){var js=document.createElement('script');js.id='workbench-js';js.src='/assets/workbench.js';document.head.appendChild(js);}
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
    { key: 'DJIA', match: [/道琼斯/, /道指/, /\bDJIA\b/i] },
    { key: 'IXIC', match: [/纳斯达克/, /纳指/, /\bIXIC\b/i, /NDX/i] },
    { key: 'SPX', match: [/标普500/, /标普/, /\bSPX\b/i, /S&P/i] },
    { key: 'KS11', match: [/韩国/, /韩股/, /KOSPI/, /\bKS11\b/i] },
    { key: 'sh000001', match: [/上证指数/, /上证/, /sh000001/i, /^000001$/] },
    { key: 'sh000300', match: [/沪深300/, /sh000300/i, /^000300$/] },
    { key: 'sh000688', match: [/科创50/, /sh000688/i, /^000688$/] },
    { key: 'HSI', match: [/恒生/, /恒指/, /\bHSI\b/i, /hkHSI/i] }
  ];
  var INDEX_BOARD_LABEL = {
    DJIA: '道琼斯',
    IXIC: '纳指',
    SPX: '标普500',
    KS11: '韩股',
    sh000001: '上证指数',
    sh000300: '沪深300',
    sh000688: '科创50',
    HSI: '恒指'
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
    var value = Number(String(it.price == null ? '' : it.price).replace(/,/g, ''));
    var empty = !!it.empty || it.price == null || it.price === '' || !Number.isFinite(value) || value <= 0;
    var price = empty ? '—' : String(it.price);
    var chg = it.chg || it.change || '';
    if (!chg && it.changePct != null && it.changePct !== '' && isFinite(+it.changePct)) {
      var n = +it.changePct;
      chg = (n > 0 ? '+' : '') + n.toFixed(2) + '%';
    }
    if (empty) chg = '暂无有效数据';
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
    if (!document.querySelector('link[rel="icon"]')) { var icon=document.createElement('link');icon.rel='icon';icon.href='/assets/favicon.svg';document.head.appendChild(icon); }
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
    webUrl: webUrl,
    nav: NAV
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', autoMount);
  } else {
    autoMount();
  }
})();
