#!/usr/bin/env python3
"""阅读板块浏览器回归：登录后在桌面 1440 与 iPhone 13 宽度检查书库页和文章页。

    STOCK_QA_USER=... STOCK_QA_PASS=... .venv/bin/python scripts/tools/qa_reading.py [--base http://127.0.0.1:8765]

凭据只从环境变量读取；截图写入 output/ui-qa/reading/。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "output" / "ui-qa" / "reading"
ARTICLE = "/reading/chaogu-yangjia.html"
SERIES = {  # 系列 id → 期望篇数；每个系列第 1 篇截图
    "asking": ["asking-1-life", "asking-2-longtou", "asking-3-trading", "asking-4-mind"],
    "zhiye": ["zhiye-1-life", "zhiye-2-core", "zhiye-3-mode", "zhiye-4-growth"],
}
MERMAID_OK = """() => {
  const pres = [...document.querySelectorAll('pre.mermaid')];
  return pres.length > 0 && pres.every(p => p.querySelector('svg'));
}"""


def check_series(page, base: str, name: str, problems: list) -> None:
    """系列文章：mermaid 全部渲染且无语法错误、系列导航与上一篇/下一篇、无横向溢出。"""
    for sid, ids in SERIES.items():
        for k, aid in enumerate(ids):
            page.goto(f"{base}/reading/{aid}.html", wait_until="networkidle")
            try:
                page.wait_for_function(MERMAID_OK, timeout=20000)
            except Exception:
                problems.append(f"{name}: {aid} mermaid 未全部渲染")
            bad = page.evaluate("[...document.querySelectorAll('pre.mermaid')].filter(p=>/Syntax error|Parse error/i.test(p.textContent)).length")
            check(bad == 0, f"{name}: {aid} 有 {bad} 个 mermaid 语法错误", problems)
            check(page.evaluate("document.documentElement.scrollWidth<=innerWidth+1"), f"{name}: {aid} 横向溢出", problems)
            check(page.locator("nav.rd-series li").count() == len(ids), f"{name}: {aid} 系列导航篇数不对", problems)
            check(page.locator("nav.rd-series li.is-current").inner_text().startswith(str(k + 1)), f"{name}: {aid} 系列当前篇高亮不对", problems)
            check(page.locator(".rd-pager-prev").count() == (1 if k else 0), f"{name}: {aid} 上一篇链接不对", problems)
            check(page.locator(".rd-pager-next").count() == (1 if k < len(ids) - 1 else 0), f"{name}: {aid} 下一篇链接不对", problems)
            for sel in ("aside.rd-callout--summary", "aside.rd-callout--note", "details", "input[type=checkbox]", ".rd-term[data-term]", "mark"):
                check(page.locator(sel).count() > 0, f"{name}: {aid} 缺少 {sel}", problems)
            check(page.locator("#来源").count() == 1, f"{name}: {aid} 缺少来源章节", problems)
            if k == 0:
                page.screenshot(path=str(SHOTS / f"{sid}-article-top-{name}.png"))
                page.locator("pre.mermaid svg").first.scroll_into_view_if_needed()
                page.evaluate("window.scrollBy(0,-120)")
                page.wait_for_timeout(250)
                page.screenshot(path=str(SHOTS / f"{sid}-article-mermaid-{name}.png"))
                page.locator("details").first.scroll_into_view_if_needed()
                page.locator("details > summary").first.click()
                page.evaluate("window.scrollBy(0,-160)")
                page.wait_for_timeout(250)
                page.screenshot(path=str(SHOTS / f"{sid}-article-selfcheck-{name}.png"))
                page.screenshot(path=str(SHOTS / f"{sid}-article-full-{name}.png"), full_page=True)


def check(cond, msg, problems):
    if not cond:
        problems.append(msg)


def run(base: str) -> int:
    user, pwd = os.environ.get("STOCK_QA_USER"), os.environ.get("STOCK_QA_PASS")
    if not user or not pwd:
        print("需要环境变量 STOCK_QA_USER / STOCK_QA_PASS", file=sys.stderr)
        return 2
    SHOTS.mkdir(parents=True, exist_ok=True)
    problems: list = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, opts in {
            "desktop-1440": {"viewport": {"width": 1440, "height": 900}},
            "desktop-1280": {"viewport": {"width": 1280, "height": 800}},
            "desktop-1728": {"viewport": {"width": 1728, "height": 1000}},
            "iphone13": dict(p.devices["iPhone 13"]),
        }.items():
            opts.pop("default_browser_type", None)
            ctx = browser.new_context(**opts)
            resp = ctx.request.post(base + "/api/login", data={"username": user, "password": pwd})
            check(resp.ok, f"{name}: 登录失败 {resp.status}", problems)
            page = ctx.new_page()
            errors, failed = [], []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("response", lambda r: failed.append(f"{r.status} {r.url}") if r.status >= 400 else None)

            page.goto(base + "/reading.html", wait_until="networkidle")
            check(page.locator(".rd-card[data-article], .rd-series-card").count() >= 1, f"{name}: 书库没有文章卡片", problems)
            for sid, ids in SERIES.items():
                card = page.locator(f'.rd-series-card[data-series="{sid}"]')
                check(card.count() == 1 and card.locator(".rd-series-list li").count() == len(ids), f"{name}: 书库 {sid} 系列卡片不对", problems)
            check(page.locator('.rd-series-card[data-series="chaogu-yangjia"]').count() == 1, f"{name}: 书库缺少炒股养家系列", problems)
            check(page.evaluate("document.documentElement.scrollWidth<=innerWidth+1"), f"{name}: 书库页横向溢出", problems)
            page.screenshot(path=str(SHOTS / f"library-{name}.png"), full_page=True)

            check_series(page, base, name, problems)

            page.goto(base + ARTICLE, wait_until="networkidle")
            page.wait_for_function("document.querySelectorAll('pre.mermaid svg').length>=10", timeout=20000)
            check(page.evaluate("document.documentElement.scrollWidth<=innerWidth+1"), f"{name}: 文章页横向溢出", problems)
            page.screenshot(path=str(SHOTS / f"article-top-{name}.png"))
            # 图片全部加载
            for i in range(page.locator(".rd-figure img").count()):
                img = page.locator(".rd-figure img").nth(i)
                img.scroll_into_view_if_needed()
                page.wait_for_function("el=>el.complete&&el.naturalWidth>0", arg=img.element_handle(), timeout=8000)
            # 术语气泡
            term = page.locator(".rd-term[data-term]").first
            term.scroll_into_view_if_needed()
            term.click()
            check(page.locator("#rdTermPop").is_visible(), f"{name}: 术语气泡未出现", problems)
            page.screenshot(path=str(SHOTS / f"term-{name}.png"))
            page.mouse.click(5, 300)
            # 核心提示框截图
            page.locator("aside.rd-callout--core").first.scroll_into_view_if_needed()
            page.evaluate("window.scrollBy(0,-90)")
            page.wait_for_timeout(300)
            page.screenshot(path=str(SHOTS / f"callout-{name}.png"))
            # 图片放大
            page.locator(".rd-zoom").first.scroll_into_view_if_needed()
            page.locator(".rd-zoom").first.click()
            check(page.locator("#rdLightbox").is_visible(), f"{name}: 图片放大失败", problems)
            page.screenshot(path=str(SHOTS / f"lightbox-{name}.png"))
            page.keyboard.press("Escape")
            # 目录
            if name == "iphone13":
                page.locator("#rdTocFab").click()
                page.wait_for_timeout(350)
                check(page.locator("#rdToc").is_visible(), f"{name}: 目录抽屉未打开", problems)
                page.screenshot(path=str(SHOTS / f"toc-{name}.png"))
                page.locator(".rd-toc-list > li > a").nth(6).click()
                page.wait_for_timeout(500)
            else:
                check(page.locator("#rdToc a.is-active").count() == 1, f"{name}: 目录未高亮当前章节", problems)
                width = lambda: page.evaluate("Math.round(document.getElementById('rdArticle').getBoundingClientRect().width)")
                page.locator("#第-3-章-第一性原理交易的本质是群体博弈").scroll_into_view_if_needed()
                page.evaluate("window.scrollBy(0,-80)")
                page.wait_for_timeout(250)
                open_w = width()
                page.screenshot(path=str(SHOTS / f"toc-open-{name}.png"))
                page.locator("#rdTocCollapse").click()
                page.wait_for_timeout(250)
                check(not page.locator("#rdToc").is_visible(), f"{name}: 目录未收起", problems)
                check(page.evaluate("document.activeElement.id") == "rdTocFab", f"{name}: 收起后焦点未移到悬浮目录按钮", problems)
                check(page.evaluate("scrollY") > 500, f"{name}: 收起目录后滚动位置丢失", problems)
                collapsed_w = width()
                check(collapsed_w >= open_w, f"{name}: 收起目录后正文没有变宽 {open_w}->{collapsed_w}", problems)
                page.screenshot(path=str(SHOTS / f"toc-collapsed-{name}.png"))
                page.reload(wait_until="networkidle")
                check(not page.locator("#rdToc").is_visible(), f"{name}: 收起状态未持久化", problems)
                check(page.locator("#rdTocFab").is_visible(), f"{name}: 收起后缺少悬浮目录按钮", problems)
                page.locator("#rdTocFab").click()
                page.wait_for_timeout(200)
                check(page.locator("#rdToc").is_visible(), f"{name}: 悬浮按钮未展开目录", problems)
                page.keyboard.press("t")
                page.keyboard.press("t")
                page.wait_for_timeout(200)
                check(page.locator("#rdToc").is_visible(), f"{name}: 快捷键 T 未切换目录", problems)
                page.locator("#rdWidthToggle").click()
                page.wait_for_timeout(250)
                wide_w = width()
                page.locator("#rdTocToggle").click()
                page.wait_for_timeout(250)
                wide_collapsed_w = width()
                page.locator("#第-3-章-第一性原理交易的本质是群体博弈").scroll_into_view_if_needed()
                page.evaluate("window.scrollBy(0,-80)")
                page.screenshot(path=str(SHOTS / f"wide-collapsed-{name}.png"))
                page.locator("#rdWidthToggle").click()
                page.locator("#rdTocToggle").click()
                page.wait_for_timeout(200)
                fs = page.evaluate("parseFloat(getComputedStyle(document.getElementById('rdArticle')).fontSize)")
                print(f"{name}: 正文栏宽 标准+目录 {open_w}px（约 {open_w / fs / 1.015:.0f} 字/行） · 标准收起目录 {collapsed_w}px · 宽+目录 {wide_w}px · 宽收起目录 {wide_collapsed_w}px（约 {wide_collapsed_w / fs / 1.015:.0f} 字/行）")
                page.screenshot(path=str(SHOTS / f"toc-{name}.png"))
            page.screenshot(path=str(SHOTS / f"article-full-{name}.png"), full_page=True)
            # 深色模式
            page.evaluate("window.StockAppShell&&StockAppShell.setTheme?StockAppShell.setTheme('dark'):(document.documentElement.dataset.theme='dark',window.dispatchEvent(new CustomEvent('stockapp:theme',{detail:'dark'})))")
            page.locator("aside.rd-callout--summary").scroll_into_view_if_needed()
            page.wait_for_timeout(1200)
            page.screenshot(path=str(SHOTS / f"dark-{name}.png"))
            page.evaluate("localStorage.setItem('stockAppTheme','light')")
            check(not errors, f"{name}: 控制台错误 {errors[:3]}", problems)
            check(not failed, f"{name}: 请求失败 {failed[:3]}", problems)
            ctx.close()
        browser.close()
    for msg in problems:
        print("✗", msg)
    print("截图：", SHOTS)
    return 1 if problems else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    sys.exit(run(ap.parse_args().base))
