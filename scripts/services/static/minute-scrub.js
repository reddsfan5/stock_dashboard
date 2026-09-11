/**
 * Shared minute-chart long-press scrub HUD.
 * Depends on ChartTouch. Provides finger crosshair, top detail bar, right-edge Y price.
 *
 * MinuteScrub.bind({ chartEl, getChart, getCount, getPoint, getPrevClose })
 */
(function (global) {
  'use strict';

  var CSS_ID = 'minute-scrub-hud-css';
  var WRAP_CLASS = 'minute-scrub-wrap';

  function $(root, sel) {
    return root.querySelector(sel);
  }

  function ensureCss() {
    if (document.getElementById(CSS_ID)) return;
    var style = document.createElement('style');
    style.id = CSS_ID;
    style.textContent =
      '.' + WRAP_CLASS + '{position:relative;width:100%;height:100%;min-height:inherit}' +
      '.' + WRAP_CLASS + '>.minute-scrub-chart{height:100%;width:100%}' +
      '.minute-scrub-tip{position:absolute;top:0;left:0;right:0;z-index:6;pointer-events:none;margin:0;padding:5px 8px;font-size:11px;line-height:1.3;color:#f8fafc;background:rgba(15,23,42,.82);font-variant-numeric:tabular-nums;box-sizing:border-box;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;border-radius:0 0 6px 6px}' +
      '.minute-scrub-tip[hidden]{display:none!important}' +
      '.minute-scrub-tip b{font-weight:700}' +
      '.minute-scrub-price{position:absolute;z-index:7;pointer-events:none;right:2px;left:auto;transform:translateY(-50%);padding:3px 7px;border-radius:6px;font-size:11px;font-weight:700;font-variant-numeric:tabular-nums;color:#fff!important;-webkit-text-fill-color:#fff;background:rgba(15,23,42,.92);box-shadow:0 2px 8px rgba(0,0,0,.35);white-space:nowrap;max-width:42%;border:1px solid rgba(255,255,255,.35)}' +
      '.minute-scrub-price[hidden]{display:none!important}' +
      '.minute-scrub-price.up{background:rgba(196,48,58,.96);border-color:rgba(255,255,255,.4)}' +
      '.minute-scrub-price.down{background:rgba(18,120,88,.96);border-color:rgba(255,255,255,.4)}' +
      '.minute-scrub-cross{position:absolute;inset:0;z-index:5;pointer-events:none}' +
      '.minute-scrub-cross[hidden]{display:none!important}' +
      '.minute-scrub-cross-v,.minute-scrub-cross-h{position:absolute;background:rgba(55,65,81,.92);pointer-events:none}' +
      '.minute-scrub-cross-v{top:0;bottom:0;width:1px;left:0;transform:translateX(-50%)}' +
      '.minute-scrub-cross-h{left:0;right:0;height:1px;top:0;transform:translateY(-50%)}' +
      '.minute-scrub-cross-dot{position:absolute;width:7px;height:7px;margin:-3px 0 0 -3px;border-radius:50%;background:#3478f6;border:1.5px solid #fff;box-shadow:0 0 0 1px rgba(15,23,42,.35);pointer-events:none}';
    (document.head || document.documentElement).appendChild(style);
  }

  function fmtPrice(v) {
    if (v === null || v === undefined || Number.isNaN(+v)) return '—';
    var n = +v;
    return n < 10 ? n.toFixed(3) : n.toFixed(2);
  }
  function fmtCompact(v) {
    var n = +v || 0;
    if (n >= 1e8) return (n / 1e8).toFixed(2) + '亿';
    if (n >= 1e4) return (n / 1e4).toFixed(1) + '万';
    return Math.round(n).toLocaleString();
  }
  function fmtSigned(v) {
    return (v >= 0 ? '+' : '') + (+v).toFixed(2) + '%';
  }

  function ensureWrap(chartEl) {
    if (!chartEl) return null;
    var parent = chartEl.parentElement;
    if (parent && parent.classList.contains(WRAP_CLASS)) return parent;
    var wrap = document.createElement('div');
    wrap.className = WRAP_CLASS;
    // Preserve explicit height from chart if wrap would otherwise collapse
    var cs = global.getComputedStyle ? global.getComputedStyle(chartEl) : null;
    if (cs && cs.height && cs.height !== '0px' && cs.height !== 'auto') {
      wrap.style.height = cs.height;
    }
    chartEl.parentNode.insertBefore(wrap, chartEl);
    wrap.appendChild(chartEl);
    chartEl.classList.add('minute-scrub-chart');
    return wrap;
  }

  function ensureHud(wrap) {
    var tip = $(wrap, '.minute-scrub-tip');
    if (!tip) {
      tip = document.createElement('div');
      tip.className = 'minute-scrub-tip';
      tip.hidden = true;
      wrap.insertBefore(tip, wrap.firstChild);
    }
    var cross = $(wrap, '.minute-scrub-cross');
    if (!cross) {
      cross = document.createElement('div');
      cross.className = 'minute-scrub-cross';
      cross.hidden = true;
      cross.innerHTML =
        '<div class="minute-scrub-cross-v"></div>' +
        '<div class="minute-scrub-cross-h"></div>' +
        '<div class="minute-scrub-cross-dot"></div>';
      wrap.insertBefore(cross, tip.nextSibling);
    }
    var price = $(wrap, '.minute-scrub-price');
    if (!price) {
      price = document.createElement('div');
      price.className = 'minute-scrub-price';
      price.hidden = true;
      wrap.appendChild(price);
    }
    return {
      tip: tip,
      cross: cross,
      v: $(cross, '.minute-scrub-cross-v'),
      h: $(cross, '.minute-scrub-cross-h'),
      dot: $(cross, '.minute-scrub-cross-dot'),
      price: price
    };
  }

  function clearHud(hud) {
    if (!hud) return;
    hud.tip.hidden = true;
    hud.tip.innerHTML = '';
    hud.price.hidden = true;
    hud.price.textContent = '';
    hud.price.classList.remove('up', 'down');
    hud.cross.hidden = true;
  }

  function yPriceFromTouch(chart, chartEl, touch) {
    if (!chart || !chartEl || !touch) return null;
    var rect = chartEl.getBoundingClientRect();
    var x = touch.clientX - rect.left;
    var y = touch.clientY - rect.top;
    try {
      var pt = chart.convertFromPixel({ gridIndex: 0 }, [x, y]);
      if (pt && typeof pt[1] === 'number' && Number.isFinite(pt[1])) return pt[1];
    } catch (e) {}
    try {
      var v = chart.convertFromPixel({ yAxisIndex: 0 }, [y]);
      if (typeof v === 'number' && Number.isFinite(v)) return v;
      if (v && typeof v[0] === 'number' && Number.isFinite(v[0])) return v[0];
    } catch (e2) {}
    return null;
  }

  function bind(options) {
    options = options || {};
    var chartEl = options.chartEl;
    if (!chartEl || !global.ChartTouch) return null;
    if (chartEl._minuteScrubCtrl) {
      try { chartEl._minuteScrubCtrl.destroy(); } catch (e) {}
      chartEl._minuteScrubCtrl = null;
    }

    ensureCss();
    if (global.ChartTouch.ensureHostCss) ChartTouch.ensureHostCss();

    var wrap = ensureWrap(chartEl);
    var hud = ensureHud(wrap);
    var getChart = typeof options.getChart === 'function' ? options.getChart : function () { return null; };
    var getCount = typeof options.getCount === 'function' ? options.getCount : function () { return 0; };
    var getPoint = typeof options.getPoint === 'function' ? options.getPoint : function () { return null; };
    var getPrevClose = typeof options.getPrevClose === 'function' ? options.getPrevClose : function () { return null; };
    var delay = options.delay == null ? 320 : options.delay;

    function setTip(p) {
      if (!p) return;
      var color = +p.change_pct >= 0 ? '#ff6b6b' : '#41d49a';
      hud.tip.innerHTML =
        '<span style="opacity:.9">' + (p.time || '') + '</span>　' +
        '<b style="color:' + color + '">' + fmtPrice(p.close) + '</b>　' +
        '<span style="color:' + color + '">' + fmtSigned(p.change_pct) + '</span>　' +
        '<span style="opacity:.85">高 ' + fmtPrice(p.high) + ' 低 ' + fmtPrice(p.low) + '</span>　' +
        '<span style="opacity:.85">量 ' + fmtCompact(p.volume) + ' 均 ' + fmtPrice(p.vwap) + '</span>';
      hud.tip.hidden = false;
    }

    function setCross(touch) {
      if (!touch) return;
      var rect = wrap.getBoundingClientRect();
      var x = touch.clientX - rect.left;
      var y = touch.clientY - rect.top;
      var tipH = !hud.tip.hidden ? (hud.tip.offsetHeight || 0) : 0;
      x = Math.max(0, Math.min(x, rect.width));
      y = Math.max(tipH, Math.min(y, rect.height));
      hud.v.style.left = x + 'px';
      hud.h.style.top = y + 'px';
      hud.dot.style.left = x + 'px';
      hud.dot.style.top = y + 'px';
      hud.cross.hidden = false;
    }

    function setEdgePrice(touch, p) {
      if (!touch) return;
      var wrapRect = wrap.getBoundingClientRect();
      var chartRect = chartEl.getBoundingClientRect();
      var tipH = !hud.tip.hidden ? (hud.tip.offsetHeight || 0) : 0;
      var y = touch.clientY - wrapRect.top;
      y = Math.max(tipH + 14, Math.min(y, wrapRect.height - 14));
      hud.price.style.left = 'auto';
      hud.price.style.right = Math.max(2, wrapRect.right - chartRect.right + 2) + 'px';
      hud.price.style.top = y + 'px';
      var chart = getChart();
      var yv = yPriceFromTouch(chart, chartEl, touch);
      hud.price.textContent = yv == null ? (p ? fmtPrice(p.close) : '—') : fmtPrice(yv);
      var base = getPrevClose();
      base = base == null ? null : +base;
      var ref = yv != null ? yv : (p ? +p.close : null);
      var up = base != null && ref != null ? ref >= base : (p ? +p.change_pct >= 0 : true);
      hud.price.classList.toggle('up', up);
      hud.price.classList.toggle('down', !up);
      hud.price.hidden = false;
    }

    var touchCtrl = ChartTouch.bindLongPressScrub({
      el: chartEl,
      getChart: getChart,
      getCount: getCount,
      seriesIndex: options.seriesIndex == null ? 0 : options.seriesIndex,
      delay: delay,
      axisPointerType: 'line',
      showEchartsTipContent: false,
      onEnter: function () {
        try { getChart() && getChart().setOption({ axisPointer: { show: false } }, false); } catch (e) {}
      },
      onIndex: function (idx, ctx) {
        var p = getPoint(idx);
        if (p) setTip(p);
        else {
          hud.tip.hidden = true;
          hud.tip.innerHTML = '';
        }
        try { getChart() && getChart().setOption({ axisPointer: { show: false } }, false); } catch (e) {}
        if (ctx && ctx.touch) {
          setCross(ctx.touch);
          setEdgePrice(ctx.touch, p);
        }
      },
      onExit: function () { clearHud(hud); }
    });

    var ctrl = {
      isActive: function () { return !!(touchCtrl && touchCtrl.isActive()); },
      exit: function () { if (touchCtrl) touchCtrl.exit(); clearHud(hud); },
      destroy: function () {
        clearHud(hud);
        if (touchCtrl) {
          try { touchCtrl.destroy(); } catch (e) {}
          touchCtrl = null;
        }
        chartEl._minuteScrubCtrl = null;
      },
      refreshHosts: function () { if (touchCtrl) touchCtrl.refreshHosts(); },
      wrap: wrap
    };
    chartEl._minuteScrubCtrl = ctrl;
    return ctrl;
  }

  global.MinuteScrub = { bind: bind, ensureCss: ensureCss };
})(typeof window !== 'undefined' ? window : this);
