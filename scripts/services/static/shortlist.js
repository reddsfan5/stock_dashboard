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

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initCopyButton);
  else initCopyButton();
}());
