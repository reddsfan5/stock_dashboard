/**
 * Shared mobile chart long-press scrub for ECharts.
 * Coarse/mobile: pan-y scroll by default; long-press enters scrub (tip + horizontal drag).
 * Desktop: leave mousemove tooltips to the page's own option.
 *
 * Usage: ChartTouch.bindLongPressScrub({ el, getChart, getCount, ... })
 *
 * Opt-in gestures (default off, so existing pages are unchanged):
 *   pinchZoom: true   two-finger pinch zooms the x-axis dataZoom around the pinch centre
 *                     (pinch out = fewer bars / more detail, pinch in = wider range)
 *   panX: true        one-finger horizontal drag pans the zoomed window; a vertical swipe
 *                     still scrolls the page (direction is locked on the first 10px)
 *   minSpan: 20       smallest visible bar count when zooming (max = full range)
 *   onGestureEnd(type) called after a pinch / pan ends ('pinch' | 'pan'), e.g. to ignore the
 *                     synthetic click that may follow
 * Works on ECharts category x-axes by dispatching { type: 'dataZoom', start, end } (percent),
 * so every dataZoom bound to that axis (inside + slider) stays in sync.
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
    // 'cross' 只能写在 tooltip.axisPointer 里；顶层 axisPointer 组件只认 line / shadow / none，
    // 写成 'cross' 会让 ECharts 在悬停/showTip 时抛错（pN[s] is not a function），整张图的悬停和滚轮缩放都失效。
    var axisType = axisPointerType === 'cross' ? 'line' : axisPointerType;
    var tipPointer = { type: axisPointerType };

    if (!coarse) {
      return {
        tooltip: {
          trigger: 'axis',
          triggerOn: 'mousemove|click',
          showContent: showContent,
          axisPointer: tipPointer
        },
        axisPointer: {
          show: true,
          type: axisType
        }
      };
    }

    if (scrubbing) {
      return {
        tooltip: {
          trigger: 'axis',
          triggerOn: 'none',
          show: true,
          showContent: showContent,
          axisPointer: tipPointer
        },
        axisPointer: {
          show: true,
          type: axisType,
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
        type: axisType
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
        showContent: showEchartsTipContent
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
      if (e.touches && e.touches.length > 1) {
        // 第二根手指落下：取消长按计时，避免双指操作时误进入滑看
        if (longPressTimer) { clearTimeout(longPressTimer); longPressTimer = null; }
        touchStart = null;
        return;
      }
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

    /* ---- 可选手势：双指缩放 / 单指横向平移 ---- */
    var pinchOn = !!options.pinchZoom, panOn = !!options.panX;
    var minSpan = options.minSpan == null ? 20 : +options.minSpan;
    var onGestureEnd = typeof options.onGestureEnd === 'function' ? options.onGestureEnd : null;
    var gesture = null; // {type:'pinch'|'pan'|'undecided'|'scroll', ...}

    function zoomState() {
      var chart = getChart(), n = +getCount() || 0;
      if (!chart || n < 2) return null;
      var dz = (chart.getOption().dataZoom || [])[0];
      if (!dz) return null;
      var s = dz.start == null ? 0 : dz.start, e = dz.end == null ? 100 : dz.end;
      var s0 = s / 100 * (n - 1), e0 = e / 100 * (n - 1);
      // 每根 K 线的像素宽度：用 x 轴两个相邻刻度换算
      var px = 0;
      try {
        var i0 = Math.max(0, Math.round(s0)), i1 = Math.min(n - 1, i0 + 1);
        px = chart.convertToPixel({ xAxisIndex: 0 }, i1) - chart.convertToPixel({ xAxisIndex: 0 }, i0);
      } catch (err) {}
      if (!(px > 0)) px = el.clientWidth / Math.max(1, e0 - s0);
      return { chart: chart, n: n, s: s0, e: e0, px: px };
    }
    function applyWindow(st, a, b) {
      var full = st.n - 1, span = Math.max(Math.min(b - a, full), Math.min(minSpan, full));
      if (a < 0) a = 0;
      if (a + span > full) a = full - span;
      try {
        st.chart.dispatchAction({ type: 'dataZoom', start: a / full * 100, end: (a + span) / full * 100 });
      } catch (err) {}
    }
    function cancelLongPress() {
      if (longPressTimer) { clearTimeout(longPressTimer); longPressTimer = null; }
      touchStart = null;
    }
    function localX(t) { return t.clientX - el.getBoundingClientRect().left; }
    function onGestureStart(e) {
      if (!e.touches) return;
      if (pinchOn && e.touches.length === 2) {
        cancelLongPress();
        if (active) exit();
        var st = zoomState(); if (!st) return;
        var a = e.touches[0], b = e.touches[1];
        var cx = (localX(a) + localX(b)) / 2, d0 = Math.max(10, Math.abs(a.clientX - b.clientX));
        var anchor = st.s;
        try { var v = st.chart.convertFromPixel({ xAxisIndex: 0 }, cx); if (typeof v === 'number') anchor = v; } catch (err) {}
        anchor = Math.max(st.s, Math.min(st.e, anchor));
        gesture = { type: 'pinch', st: st, d0: d0, cx0: cx, anchor: anchor, frac: (anchor - st.s) / Math.max(1e-6, st.e - st.s) };
      } else if (panOn && e.touches.length === 1 && !active) {
        var t = e.touches[0];
        gesture = { type: 'undecided', x0: t.clientX, y0: t.clientY };
      }
    }
    function onGestureMove(e) {
      if (!gesture || !e.touches) return;
      if (gesture.type === 'pinch') {
        if (e.touches.length < 2) return;
        e.preventDefault();
        var a = e.touches[0], b = e.touches[1], g = gesture, st = g.st;
        var d = Math.max(10, Math.abs(a.clientX - b.clientX)), cx = (localX(a) + localX(b)) / 2;
        var span = (st.e - st.s) * g.d0 / d;
        span = Math.max(Math.min(minSpan, st.n - 1), Math.min(st.n - 1, span));
        var shift = -(cx - g.cx0) / st.px * (span / Math.max(1e-6, st.e - st.s));
        var start = g.anchor - g.frac * span + shift;
        applyWindow(st, start, start + span);
        return;
      }
      if (active || e.touches.length !== 1) return;
      var t = e.touches[0];
      if (gesture.type === 'undecided') {
        var dx = t.clientX - gesture.x0, dy = t.clientY - gesture.y0;
        if (Math.hypot(dx, dy) < MOVE_CANCEL_PX) return;
        if (Math.abs(dx) > Math.abs(dy) * 1.2) {
          var st2 = zoomState(); if (!st2) { gesture = null; return; }
          cancelLongPress();
          gesture = { type: 'pan', st: st2, x0: t.clientX };
        } else {
          gesture = { type: 'scroll' }; // 交给浏览器滚动页面
          return;
        }
      }
      if (gesture.type === 'pan') {
        e.preventDefault();
        var g2 = gesture, shiftBars = -(t.clientX - g2.x0) / g2.st.px;
        applyWindow(g2.st, g2.st.s + shiftBars, g2.st.e + shiftBars);
      }
    }
    function onGestureEndEvt(e) {
      if (!gesture) return;
      if (gesture.type === 'pinch' && e.touches && e.touches.length >= 2) return;
      var type = gesture.type;
      gesture = null;
      if ((type === 'pinch' || type === 'pan') && onGestureEnd) {
        try { onGestureEnd(type); } catch (err) {}
      }
    }
    function onIosGesture(e) { if (pinchOn) e.preventDefault(); }
    if (pinchOn || panOn) {
      el.addEventListener('touchstart', onGestureStart, { passive: true });
      el.addEventListener('touchmove', onGestureMove, { passive: false });
      el.addEventListener('touchend', onGestureEndEvt, { passive: true });
      el.addEventListener('touchcancel', onGestureEndEvt, { passive: true });
      if (pinchOn) el.addEventListener('gesturestart', onIosGesture, { passive: false }); // iOS Safari 原生缩放手势
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
        el.removeEventListener('touchstart', onGestureStart);
        el.removeEventListener('touchmove', onGestureMove);
        el.removeEventListener('touchend', onGestureEndEvt);
        el.removeEventListener('touchcancel', onGestureEndEvt);
        el.removeEventListener('gesturestart', onIosGesture);
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
