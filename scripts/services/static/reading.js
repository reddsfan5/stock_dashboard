/* 阅读 / 心法板块：目录、阅读进度、术语气泡、图片/图示放大、mermaid 懒加载。 */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var store = {
    get: function (k, d) { try { var v = localStorage.getItem(k); return v == null ? d : v; } catch (e) { return d; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) { /* ignore */ } }
  };
  function readJSON(id, fallback) {
    var el = $(id);
    if (!el) return fallback;
    try { return JSON.parse(el.textContent || ''); } catch (e) { return fallback; }
  }

  // ---------------------------------------------------------------- library
  function initLibrary() {
    document.querySelectorAll('[data-progress-for]').forEach(function (el) {
      var pct = parseFloat(store.get('rdProgress:' + el.dataset.progressFor, '0')) || 0;
      if (pct >= 97) el.textContent = '已读完 ✓';
      else if (pct >= 3) el.textContent = '已读 ' + Math.round(pct) + '% · 继续 →';
    });
  }

  // ---------------------------------------------------------------- article
  function initArticle(article) {
    var page = readJSON('rdPage', {});
    var id = page.id || article.dataset.article;
    var progressBar = $('rdProgress');
    var key = 'rdProgress:' + id;
    var posKey = 'rdPos:' + id;

    // 字号
    var fs = parseFloat(store.get('rdFont', '')) || 0;
    function applyFont() { if (fs) document.body.style.setProperty('--rd-fs', fs + 'px'); }
    applyFont();
    document.querySelectorAll('[data-font]').forEach(function (b) {
      b.addEventListener('click', function () {
        var cur = fs || parseFloat(getComputedStyle(document.body).getPropertyValue('--rd-fs')) || 17;
        fs = Math.max(14, Math.min(22, cur + Number(b.dataset.font)));
        store.set('rdFont', String(fs));
        applyFont();
      });
    });

    // 桌面端：目录收起/展开、正文栏宽（标准/宽），状态存本机
    var body = document.body;
    var tocToggle = $('rdTocToggle'), tocCollapse = $('rdTocCollapse'), widthToggle = $('rdWidthToggle');
    function syncToggles() {
      var collapsed = body.classList.contains('rd-toc-collapsed');
      if (tocToggle) { tocToggle.setAttribute('aria-pressed', collapsed ? 'false' : 'true'); tocToggle.textContent = collapsed ? '☰ 显示目录' : '☰ 目录'; }
      if (widthToggle) widthToggle.setAttribute('aria-pressed', body.classList.contains('rd-wide') ? 'true' : 'false');
    }
    function setTocCollapsed(collapsed, focusTarget) {
      var anchor = currentAnchor();
      body.classList.toggle('rd-toc-collapsed', collapsed);
      store.set('rdToc', collapsed ? 'collapsed' : 'open');
      syncToggles();
      restoreAnchor(anchor);
      if (focusTarget) focusTarget.focus({ preventScroll: true });
    }
    function setWide(wide) {
      var anchor = currentAnchor();
      body.classList.toggle('rd-wide', wide);
      store.set('rdWidth', wide ? 'wide' : 'standard');
      syncToggles();
      restoreAnchor(anchor);
    }
    // 版式变化后保持当前阅读的标题在视口内的相对位置
    function currentAnchor() {
      var hs = article.querySelectorAll('h2[id],h3[id],p');
      for (var i = 0; i < hs.length; i++) { var t = hs[i].getBoundingClientRect().top; if (t >= 60) return { el: hs[i], top: t }; }
      return null;
    }
    function restoreAnchor(a) { if (a && window.scrollY > 0) window.scrollBy(0, a.el.getBoundingClientRect().top - a.top); }
    if (tocToggle) tocToggle.addEventListener('click', function () { setTocCollapsed(!body.classList.contains('rd-toc-collapsed')); });
    if (tocCollapse) tocCollapse.addEventListener('click', function () { setTocCollapsed(true, $('rdTocFab')); });
    if (widthToggle) widthToggle.addEventListener('click', function () { setWide(!body.classList.contains('rd-wide')); });
    document.addEventListener('keydown', function (e) {
      if (e.metaKey || e.ctrlKey || e.altKey || e.defaultPrevented) return;
      var tag = (e.target && e.target.tagName) || '';
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(tag) || (e.target && e.target.isContentEditable)) return;
      if (window.matchMedia('(max-width: 860px)').matches) return;
      if (e.key === 't' || e.key === 'T') { e.preventDefault(); setTocCollapsed(!body.classList.contains('rd-toc-collapsed')); }
      else if (e.key === 'w' || e.key === 'W') { e.preventDefault(); setWide(!body.classList.contains('rd-wide')); }
    });
    syncToggles();

    // 阅读进度 + 续读位置
    var ticking = false;
    function progress() {
      var rect = article.getBoundingClientRect();
      var total = article.offsetHeight - window.innerHeight * 0.6;
      var read = Math.min(Math.max(-rect.top + window.innerHeight * 0.25, 0), Math.max(total, 1));
      var pct = total > 0 ? (read / total) * 100 : 100;
      if (progressBar) progressBar.style.width = pct.toFixed(1) + '%';
      var prev = parseFloat(store.get(key, '0')) || 0;
      if (pct > prev) store.set(key, pct.toFixed(1));
      store.set(posKey, String(Math.round(window.scrollY)));
      ticking = false;
    }
    window.addEventListener('scroll', function () { if (!ticking) { ticking = true; requestAnimationFrame(progress); } }, { passive: true });
    window.addEventListener('resize', progress);
    var savedPos = parseInt(store.get(posKey, '0'), 10) || 0;
    var resume = $('rdResume');
    if (resume && savedPos > window.innerHeight && !location.hash) {
      var savedPct = Math.round(parseFloat(store.get(key, '0')) || 0);
      resume.textContent = '↓ 回到上次读到的位置' + (savedPct ? '（' + savedPct + '%）' : '');
      resume.hidden = false;
      resume.addEventListener('click', function () { window.scrollTo({ top: savedPos, behavior: 'smooth' }); resume.hidden = true; });
      setTimeout(function () { resume.hidden = true; }, 9000);
    }
    progress();

    // 目录：滚动高亮 + 移动端抽屉
    var toc = $('rdToc'), fab = $('rdTocFab'), scrim = $('rdScrim');
    var links = {};
    document.querySelectorAll('[data-toc]').forEach(function (a) { links[a.dataset.toc] = a; });
    var heads = Array.prototype.slice.call(article.querySelectorAll('h2[id],h3[id]'));
    var current = null;
    function setActive(hid) {
      if (hid === current || !links[hid]) return;
      if (current && links[current]) links[current].classList.remove('is-active');
      current = hid;
      var a = links[hid];
      a.classList.add('is-active');
      document.querySelectorAll('.rd-toc-list > li.is-open').forEach(function (li) { li.classList.remove('is-open'); });
      var chapter = a.closest('li[data-chapter]');
      if (chapter) chapter.classList.add('is-open');
      if (toc && !toc.classList.contains('is-open') && toc.scrollHeight > toc.clientHeight) {
        var r = a.getBoundingClientRect(), tr = toc.getBoundingClientRect();
        if (r.top < tr.top + 40 || r.bottom > tr.bottom - 40) toc.scrollTop += r.top - tr.top - tr.height / 3;
      }
    }
    function spy() {
      var line = 110, hit = heads[0];
      for (var i = 0; i < heads.length; i++) { if (heads[i].getBoundingClientRect().top - line <= 0) hit = heads[i]; else break; }
      if (hit) setActive(hit.id);
    }
    window.addEventListener('scroll', function () { requestAnimationFrame(spy); }, { passive: true });
    spy();
    function openToc(open) {
      if (!toc) return;
      toc.classList.toggle('is-open', open);
      if (scrim) scrim.hidden = !open;
      if (fab) fab.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open && current && links[current]) links[current].scrollIntoView({ block: 'center' });
    }
    if (fab) fab.addEventListener('click', function () {
      // 桌面端目录收起时，悬浮按钮用于展开侧栏；手机端打开底部抽屉
      if (!window.matchMedia('(max-width: 860px)').matches) { setTocCollapsed(false, $('rdTocCollapse')); return; }
      openToc(!toc.classList.contains('is-open'));
    });
    if (scrim) scrim.addEventListener('click', function () { openToc(false); });
    var closeBtn = toc && toc.querySelector('.rd-toc-close');
    if (closeBtn) closeBtn.addEventListener('click', function () { openToc(false); });
    if (toc) toc.addEventListener('click', function (e) { if (e.target.closest('a[data-toc]')) openToc(false); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') openToc(false); });

    // 清单勾选保存在本机
    article.querySelectorAll('.rd-task input[type=checkbox]').forEach(function (box, i) {
      var k = 'rdTask:' + id + ':' + i;
      if (store.get(k, '') === '1') box.checked = true;
      box.addEventListener('change', function () { store.set(k, box.checked ? '1' : '0'); });
    });

    initTerms();
    initLightbox(article);
    if (page.mermaid) initMermaid(article);
  }

  // ------------------------------------------------------------------ terms
  function initTerms() {
    var defs = readJSON('rdGlossary', {});
    var pop = $('rdTermPop');
    if (!pop) return;
    var openEl = null, hideTimer = null;
    function place(el) {
      var r = el.getBoundingClientRect();
      pop.hidden = false;
      var w = pop.offsetWidth, h = pop.offsetHeight;
      var left = Math.max(12, Math.min(r.left + r.width / 2 - w / 2, document.documentElement.clientWidth - w - 12));
      var top = r.top - h - 8 < 64 ? r.bottom + 8 : r.top - h - 8;
      pop.style.left = (left + window.scrollX) + 'px';
      pop.style.top = (top + window.scrollY) + 'px';
    }
    function show(el) {
      var term = el.dataset.term, def = defs[term];
      if (!def) return;
      clearTimeout(hideTimer);
      if (openEl && openEl !== el) openEl.classList.remove('is-open');
      openEl = el;
      el.classList.add('is-open');
      pop.innerHTML = '';
      var b = document.createElement('b'); b.textContent = term;
      var p = document.createElement('span'); p.textContent = def;
      pop.appendChild(b); pop.appendChild(p);
      place(el);
    }
    function hide() {
      if (openEl) openEl.classList.remove('is-open');
      openEl = null;
      pop.hidden = true;
    }
    var hover = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
    document.addEventListener('click', function (e) {
      var t = e.target.closest('.rd-term[data-term]');
      if (t) { e.preventDefault(); show(t); return; }  // 点按只打开；点别处关闭（避免 focus/hover 先打开后被 click 关掉）
      if (!e.target.closest('#rdTermPop')) hide();
    });
    document.addEventListener('keydown', function (e) {
      var t = e.target.closest && e.target.closest('.rd-term[data-term]');
      if (t && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); show(t); }
      if (e.key === 'Escape') hide();
    });
    if (hover) {
      document.addEventListener('mouseover', function (e) {
        var t = e.target.closest('.rd-term[data-term]');
        if (t) show(t);
      });
      document.addEventListener('mouseout', function (e) {
        var t = e.target.closest('.rd-term[data-term]');
        if (t) hideTimer = setTimeout(hide, 160);
      });
    }
    document.addEventListener('focusin', function (e) { var t = e.target.closest && e.target.closest('.rd-term[data-term]'); if (t) show(t); });
    window.addEventListener('scroll', function () { if (openEl && !hover) place(openEl); }, { passive: true });
  }

  // --------------------------------------------------------------- lightbox
  function initLightbox(article) {
    var dlg = $('rdLightbox'), body = $('rdLightboxBody'), cap = $('rdLightboxCaption'), zoom = $('rdLightboxZoom'), close = $('rdLightboxClose');
    if (!dlg || !body) return;
    var returnFocus = null;
    function open(node, caption, isDiagram, trigger) {
      body.innerHTML = '';
      body.appendChild(node);
      body.classList.toggle('is-diagram', !!isDiagram);
      cap.textContent = caption || '';
      dlg.classList.remove('is-natural');
      zoom.setAttribute('aria-pressed', 'false');
      zoom.textContent = '原始尺寸';
      returnFocus = trigger;
      if (dlg.showModal) dlg.showModal(); else dlg.setAttribute('open', '');
    }
    article.addEventListener('click', function (e) {
      var img = e.target.closest('.rd-zoom');
      if (img) {
        var el = document.createElement('img');
        el.src = img.dataset.image; el.alt = img.dataset.caption || '';
        open(el, img.dataset.caption, false, img);
        return;
      }
      var dz = e.target.closest('.rd-diagram-zoom');
      if (dz) {
        var svg = dz.closest('.rd-diagram').querySelector('svg');
        if (!svg) return;
        var clone = svg.cloneNode(true);
        clone.removeAttribute('style');
        var vb = (svg.getAttribute('viewBox') || '').split(/\s+/);
        if (vb.length === 4) { clone.setAttribute('width', Math.round(vb[2] * 1.25)); clone.setAttribute('height', Math.round(vb[3] * 1.25)); }
        open(clone, '图示', true, dz);
      }
    });
    zoom.addEventListener('click', function () {
      var on = !dlg.classList.contains('is-natural');
      dlg.classList.toggle('is-natural', on);
      zoom.setAttribute('aria-pressed', on ? 'true' : 'false');
      zoom.textContent = on ? '适应屏幕' : '原始尺寸';
    });
    close.addEventListener('click', function () { dlg.close(); });
    dlg.addEventListener('click', function (e) { if (e.target === dlg) dlg.close(); });
    dlg.addEventListener('close', function () { body.innerHTML = ''; if (returnFocus) returnFocus.focus(); });
  }

  // ---------------------------------------------------------------- mermaid
  function initMermaid(article) {
    var nodes = Array.prototype.slice.call(article.querySelectorAll('pre.mermaid'));
    if (!nodes.length) return;
    nodes.forEach(function (n) { n.dataset.src = n.textContent; });
    var loading = null;
    function load() {
      if (window.mermaid) return Promise.resolve(window.mermaid);
      if (loading) return loading;
      loading = new Promise(function (resolve, reject) {
        var s = document.createElement('script');
        s.src = '/assets/vendor/mermaid/mermaid.min.js';
        s.onload = function () { resolve(window.mermaid); };
        s.onerror = function () { reject(new Error('mermaid 加载失败')); };
        document.head.appendChild(s);
      });
      return loading;
    }
    function theme() { return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'neutral'; }
    function render() {
      return load().then(function (m) {
        m.initialize({ startOnLoad: false, theme: theme(), securityLevel: 'strict', fontFamily: getComputedStyle(document.body).fontFamily, flowchart: { useMaxWidth: true, htmlLabels: true }, mindmap: { useMaxWidth: true } });
        nodes.forEach(function (n) { n.removeAttribute('data-processed'); n.textContent = n.dataset.src; });
        return m.run({ nodes: nodes, suppressErrors: true });
      }).catch(function (err) {
        nodes.forEach(function (n) { n.classList.add('is-fallback'); });
        if (window.console) console.warn('[reading] 图示未能渲染，已显示源码：', err && err.message);
      });
    }
    render();
    var lastTheme = theme();
    window.addEventListener('stockapp:theme', function () { var t = theme(); if (t !== lastTheme) { lastTheme = t; render(); } });
  }

  function boot() {
    var article = $('rdArticle');
    if (article) initArticle(article);
    else if (document.body.classList.contains('rd-library')) initLibrary();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
