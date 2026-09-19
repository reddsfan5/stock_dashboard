(function () {
  "use strict";

  function readExportText() {
    var node = document.getElementById("shortlistExportData");
    if (!node) return "";
    try { return JSON.parse(node.textContent || '""'); }
    catch (error) { return ""; }
  }

  function legacyCopy(text) {
    var input = document.createElement("textarea");
    input.value = text;
    input.setAttribute("readonly", "");
    input.style.position = "fixed";
    input.style.opacity = "0";
    document.body.appendChild(input);
    input.select();
    var ok = document.execCommand("copy");
    input.remove();
    if (!ok) throw new Error("copy failed");
  }

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    try { legacyCopy(text); return Promise.resolve(); }
    catch (error) { return Promise.reject(error); }
  }

  function initCopyButton() {
    var button = document.getElementById("copyShortlist");
    var status = document.getElementById("copyStatus");
    if (!button) return;
    button.addEventListener("click", function () {
      var text = readExportText();
      if (!text) {
        if (status) status.textContent = "当前没有可复制的标的";
        return;
      }
      copyText(text).then(function () {
        var count = text.split(/\n/).filter(Boolean).length;
        button.textContent = "已复制 " + count + " 只";
        if (status) status.textContent = "已按“六位代码 + 股票名称”格式写入剪贴板，可直接粘贴到同花顺。";
        window.setTimeout(function () { button.textContent = "复制全部标的"; }, 2600);
      }).catch(function () {
        if (status) status.textContent = "复制失败，请刷新页面后重试。";
      });
    });
  }

  function initHistoryPicker() {
    var select = document.getElementById("historyDateSelect");
    if (!select) return;
    select.addEventListener("change", function () {
      if (!select.value) return;
      var url = new URL(window.location.href);
      url.pathname = "/shortlist.html";
      url.search = "";
      url.searchParams.set("date", select.value);
      window.location.assign(url.toString());
    });
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return {"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[char];
    });
  }

  function formatPct(value) {
    if (value == null || value === "") return "—";
    var number = Number(value);
    return (number >= 0 ? "+" : "") + number.toFixed(2) + "%";
  }

  function initOutcomePreview() {
    var marketDate = window.SHORTLIST_DATE;
    if (!marketDate || !window.fetch || window.location.protocol === "file:") return;
    var url = "/api/shortlist/monitor?from=" + encodeURIComponent(marketDate) +
      "&to=" + encodeURIComponent(marketDate) + "&horizon=5&limit=500";
    fetch(url, {cache: "no-store"}).then(function (response) {
      return response.ok ? response.json() : null;
    }).then(function (payload) {
      if (!payload) return;
      var byCode = {};
      (payload.items || []).forEach(function (item) { byCode[item.code] = item; });
      document.querySelectorAll("[data-outcome-code]").forEach(function (node) {
        var item = byCode[node.getAttribute("data-outcome-code")];
        if (!item) {
          node.querySelector(".outcome-placeholder").textContent = "尚未生成结果";
          return;
        }
        var status = {complete:"已完成", partial:"观察中", pending:"等待入场", unavailable:"无数据"}[item.status] || item.status;
        var tone = Number(item.peak_return_pct) > 0 ? " is-up" : Number(item.peak_return_pct) < 0 ? " is-down" : "";
        node.innerHTML = "<span class='outcome-label'>5日收益监控</span>" +
          "<strong class='outcome-peak" + tone + "'>峰值 " + escapeHtml(formatPct(item.peak_return_pct)) + "</strong>" +
          "<span>收盘 " + escapeHtml(formatPct(item.close_return_pct)) + "</span>" +
          "<span>回撤 " + escapeHtml(formatPct(item.adverse_return_pct)) + "</span>" +
          "<span class='outcome-status'>" + escapeHtml(status) + "</span>" +
          "<a href='/shortlist_monitor.html?from=" + encodeURIComponent(marketDate) + "&to=" + encodeURIComponent(marketDate) + "&horizon=5'>查看当日</a>";
      });
    }).catch(function () {
      document.querySelectorAll(".outcome-placeholder").forEach(function (node) {
        node.textContent = "收益监控尚未刷新";
      });
    });
  }

  function init() {
    initCopyButton();
    initHistoryPicker();
    initOutcomePreview();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
}());
