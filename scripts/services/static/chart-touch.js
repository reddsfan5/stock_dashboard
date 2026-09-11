/**
 * Shared mobile chart long-press scrub for ECharts.
 * Coarse/mobile: pan-y scroll by default; long-press enters scrub (tip + horizontal drag).
 * Desktop: leave mousemove tooltips to the page's own option.
 *
 * Usage: ChartTouch.bindLongPressScrub({ el, getChart, getCount, ... })
 */
(function (global) {
  'use strict';

  var CSS_ID = 'chart-touch-host-css';
  var BOUND_KEY = 'chartTouchBound';
  var HOST_CLASS = 'chart-touch-host';
  var SCRUB_CLASS = 'is-scrubbing';
  var MOVE_CANCEL_PX = 10;

  function isCoarse() {
    if (!global.matchMedia) return false;
    return (
      global.matchMedia('(pointer:coarse)').matches ||
      global.matchMedia('(max-width:760px)').matches
    );
  }

  function ensureHostCss() {
    if (document.getElementById(CSS_ID)) return;
    var style = document.createElement('style');
    style.id = CSS_ID;
    style.textContent =
      '.' + HOST_CLASS + '{touch-action:pan-y;overscroll-behavior:contain}' +
      '.' + HOST_CLASS + '.' + SCRUB_CLASS + ',.' + SCRUB_CLASS + ' .' + HOST_CLASS + '{touch-action:none}';
    (document.head || document.documentElement).appendChild(style);
  }

  function tooltipOption(opts) {
    opts = opts || {};
    var scrubbing = !!opts.scrubbing;
    var axisPointerType = opts.axisPointerType || 'line';
    var showContent = opts.showContent !== false;
    var coarse = isCoarse();

    if (!coarse) {
      return {
        tooltip: {
          trigger: 'axis',
          triggerOn: 'mousemove|click',
          showContent: true
        },
        axisPointer: {
          show: true,
          type: axisPointerType
        }
      };
    }

    if (scrubbing) {
      return {
        tooltip: {
          trigger: 'axis',
          triggerOn: 'none',
          show: true,
          showContent: showContent
        },
        axisPointer: {
          show: true,
          type: axisPointerType,
          label: { show: false }
        }
      };
    }

    return {
      tooltip: {
        trigger: 'none',
        triggerOn: 'none',
        showContent: false
      },
      axisPointer: {
        show: false,
        type: axisPointerType
      }
    };
  }

  function indexFromTouch(chart, el, touch) {
    if (!chart || !el || !touch) return -1;
    var rect = el.getBoundingClientRect();
    var x = touch.clientX - rect.left;
    var y = touch.clientY - rect.top;
    var idx = -1;
    try {
      var pt = chart.convertFromPixel({ gridIndex: 0 }, [x, y]);
      if (pt && typeof pt[0] === 'number') idx = Math.round(pt[0]);
    } catch (e) {}
    if (idx < 0) {
      try {
        var pt2 = chart.convertFromPixel({ xAxisIndex: 0 }, [x]);
        if (typeof pt2 === 'number') idx = Math.round(pt2);
        else if (pt2 && typeof pt2[0] === 'number') idx = Math.round(pt2[0]);
      } catch (e2) {}
    }
    return idx;
  }

  function bindLongPressScrub(options) {
    options = options || {};
    var el = options.el;
    if (!el) return null;
    if (el.dataset[BOUND_KEY] === '1') return el._chartTouchController || null;

    var getChart = typeof options.getChart === 'function' ? options.getChart : function () { return null; };
    var getCount = typeof options.getCount === 'function' ? options.getCount : function () { return 0; };
    var seriesIndex = options.seriesIndex == null ? 0 : options.seriesIndex;
    var delay = options.delay == null ? 330 : options.delay;
    var panelEl = options.panelEl || null;
    var axisPointerType = options.axisPointerType || 'line';
    var showEchartsTipContent = options.showEchartsTipContent !== false;
    var onIndex = typeof options.onIndex === 'function' ? options.onIndex : null;
    var onEnter = typeof options.onEnter === 'function' ? options.onEnter : null;
    var onExit = typeof options.onExit === 'function' ? options.onExit : null;

    ensureHostCss();
    el.classList.add(HOST_CLASS);
    el.dataset[BOUND_KEY] = '1';

    var active = false;
    var longPressTimer = null;
    var touchStart = null;
    var scrubMoveHandler = null;
    var destroyed = false;

    function hosts() {
      var list = [el];
      if (panelEl) list.push(panelEl);
      return list;
    }

    function refreshHosts() {
      ensureHostCss();
      el.classList.add(HOST_CLASS);
      if (active) {
        hosts().forEach(function (node) { node.classList.add(SCRUB_CLASS); });
      } else {
        hosts().forEach(function (node) { node.classList.remove(SCRUB_CLASS); });
      }
    }

    function applyTooltip(scrubbing) {
      var chart = getChart();
      if (!chart) return;
      var patch = tooltipOption({
        scrubbing: scrubbing,
        axisPointerType: axisPointerType,
        showContent: scrubbing ? showEchartsTipContent : !isCoarse()
      });
      try {
        chart.setOption(patch, false);
      } catch (e) {}
    }

    function updateFromTouch(touch) {
      var chart = getChart();
      if (!chart) return;
      var n = +getCount() || 0;
      if (n <= 0) return;
      var idx = indexFromTouch(chart, el, touch);
      if (idx < 0 || idx >= n) return;
      if (onIndex) {
        try {
          onIndex(idx, { touch: touch, chart: chart, el: el });
        } catch (e) {}
      }
      try {
        chart.dispatchAction({ type: 'showTip', seriesIndex: seriesIndex, dataIndex: idx });
      } catch (e2) {}
    }

    function exit() {
      if (longPressTimer) {
        clearTimeout(longPressTimer);
        longPressTimer = null;
      }
      touchStart = null;
      if (scrubMoveHandler) {
        el.removeEventListener('touchmove', scrubMoveHandler);
        scrubMoveHandler = null;
      }
      if (!active) {
        hosts().forEach(function (node) { node.classList.remove(SCRUB_CLASS); });
        return;
      }
      active = false;
      hosts().forEach(function (node) { node.classList.remove(SCRUB_CLASS); });
      var chart = getChart();
      if (chart) {
        try { chart.dispatchAction({ type: 'hideTip' }); } catch (e) {}
        applyTooltip(false);
      }
      if (onExit) {
        try { onExit(); } catch (e2) {}
      }
    }

    function enter(touch) {
      if (active || destroyed) return;
      var n = +getCount() || 0;
      if (n <= 0) return;
      active = true;
      hosts().forEach(function (node) { node.classList.add(SCRUB_CLASS); });
      try {
        if (navigator.vibrate) navigator.vibrate(10);
      } catch (e) {}
      applyTooltip(true);
      if (!scrubMoveHandler) {
        scrubMoveHandler = function (e) {
          if (!active) return;
          e.preventDefault();
          var t = e.touches && e.touches[0];
          if (t) updateFromTouch(t);
        };
        el.addEventListener('touchmove', scrubMoveHandler, { passive: false });
      }
      if (onEnter) {
        try { onEnter(); } catch (e2) {}
      }
      if (touch) updateFromTouch(touch);
    }

    function onTouchStart(e) {
      if (!e.touches || e.touches.length !== 1) return;
      if (active) exit();
      var t = e.touches[0];
      touchStart = { x: t.clientX, y: t.clientY };
      if (longPressTimer) clearTimeout(longPressTimer);
      longPressTimer = setTimeout(function () {
        longPressTimer = null;
        if (!touchStart) return;
        enter({ clientX: touchStart.x, clientY: touchStart.y });
      }, delay);
    }

    function onTouchMoveCancel(e) {
      if (active || !longPressTimer || !touchStart || !e.touches || !e.touches[0]) return;
      var t = e.touches[0];
      var dx = t.clientX - touchStart.x;
      var dy = t.clientY - touchStart.y;
      if (Math.hypot(dx, dy) > MOVE_CANCEL_PX) {
        clearTimeout(longPressTimer);
        longPressTimer = null;
        touchStart = null;
      } else {
        touchStart = { x: t.clientX, y: t.clientY };
      }
    }

    function onTouchEnd() {
      exit();
    }

    el.addEventListener('touchstart', onTouchStart, { passive: true });
    el.addEventListener('touchmove', onTouchMoveCancel, { passive: true });
    el.addEventListener('touchend', onTouchEnd, { passive: true });
    el.addEventListener('touchcancel', onTouchEnd, { passive: true });

    var controller = {
      isActive: function () { return !!active; },
      exit: exit,
      destroy: function () {
        if (destroyed) return;
        destroyed = true;
        exit();
        el.removeEventListener('touchstart', onTouchStart);
        el.removeEventListener('touchmove', onTouchMoveCancel);
        el.removeEventListener('touchend', onTouchEnd);
        el.removeEventListener('touchcancel', onTouchEnd);
        el.classList.remove(HOST_CLASS, SCRUB_CLASS);
        delete el.dataset[BOUND_KEY];
        el._chartTouchController = null;
      },
      refreshHosts: refreshHosts
    };

    el._chartTouchController = controller;
    return controller;
  }

  global.ChartTouch = {
    isCoarse: isCoarse,
    ensureHostCss: ensureHostCss,
    tooltipOption: tooltipOption,
    bindLongPressScrub: bindLongPressScrub
  };
})(typeof window !== 'undefined' ? window : this);
