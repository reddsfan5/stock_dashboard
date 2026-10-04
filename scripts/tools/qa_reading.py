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
ARTICLE = "/reading/yangjia-2-emotion.html"   # 带图片、术语与长目录的教程拆分篇，做完整交互检查
OLD_URL = "/reading/chaogu-yangjia.html"        # 拆分前的旧长文：应跳转并迁移进度
SERIES = {  # 系列 id → 有序文章
    "chaogu-yangjia": ["yangjia-1-overview", "yangjia-2-emotion", "yangjia-3-dashi", "yangjia-4-trade", "yangjia-5-position",
                       "yangjia-6-practice", "yangjia-7-thread", "yangjia-8-hotspot", "yangjia-9-gauge", "yangjia-10-media"],
    "asking": ["asking-1-life", "asking-2-longtou", "asking-3-trading", "asking-4-mind"],
    "zhiye": ["zhiye-1-life", "zhiye-2-core", "zhiye-3-mode", "zhiye-4-growth"],
    "gaipian": ["gaipian-1-life", "gaipian-2-heli", "gaipian-3-discipline"],
    "zhang": ["zhang-1-life", "zhang-2-style"],
    "buffett": ["buffett-1-life", "buffett-2-moat", "buffett-3-value", "buffett-4-competence", "buffett-5-cases"],
    "zhao": ["zhao-1-life", "zhao-2-style"],
    "xiaoeyu": ["xiaoeyu-1-life", "xiaoeyu-2-style"],
    "tuixue": ["tuixue-1-life", "tuixue-2-mode", "tuixue-3-xiaoming"],
    "munger": ["munger-1-life", "munger-2-worldly", "munger-3-misjudgment", "munger-4-invert"],
    "duan": ["duan-1-life", "duan-2-benfen", "duan-3-business"],
    "livermore": ["livermore-1-life", "livermore-2-pivotal", "livermore-3-money"],
    "ruihe": ["ruihe-1-life", "ruihe-2-method", "ruihe-3-defense"],
}
SHOT_AS = {"asking-1-life": "asking-article", "zhiye-1-life": "zhiye-article",
           "yangjia-3-dashi": "yangjia-split-article", "yangjia-7-thread": "yangjia-new-article",
           "gaipian-2-heli": "gaipian-article", "zhang-1-life": "zhang-article", "buffett-3-value": "buffett-article",
           "zhao-2-style": "zhao-article", "xiaoeyu-2-style": "xiaoeyu-article", "tuixue-3-xiaoming": "tuixue-article",
           "munger-3-misjudgment": "munger-article", "duan-2-benfen": "duan-article",
           "livermore-2-pivotal": "livermore-article", "ruihe-2-method": "ruihe-article"}
FAB_CLEAR = """() => {
  const fab = document.getElementById('rdTocFab');
  if (!fab || getComputedStyle(fab).visibility === 'hidden') return true;
  const a = fab.getBoundingClientRect();
  return [...document.querySelectorAll('pre.mermaid svg')].every(svg => {
    const b = svg.getBoundingClientRect();
    return a.right <= b.left || a.left >= b.right || a.bottom <= b.top || a.top >= b.bottom;
  });
}"""
MERMAID_OK = """() => {
  const pres = [...document.querySelectorAll('pre.mermaid')];
  return pres.length > 0 && pres.every(p => p.querySelector('svg'));
}"""


def check_redirect(page, base: str, name: str, problems: list) -> None:
    """旧长文 URL：按旧进度跳到续读篇、迁移进度与清单勾选；带 #章节 的旧链接跳到对应新文章。"""
    page.goto(base + "/reading.html", wait_until="domcontentloaded")
    page.evaluate("""() => { Object.keys(localStorage).filter(k => /^rd(Progress|Task|Migrated):/.test(k)).forEach(k => localStorage.removeItem(k));
        localStorage.setItem('rdProgress:chaogu-yangjia', '50'); localStorage.setItem('rdTask:chaogu-yangjia:0', '1'); }""")
    page.goto(base + OLD_URL, wait_until="domcontentloaded")
    page.wait_for_url(lambda u: "chaogu-yangjia" not in u, timeout=10000)
    page.wait_for_load_state("networkidle")
    landed = page.url.split("/reading/")[-1]
    store = page.evaluate("() => ({p1: localStorage.getItem('rdProgress:yangjia-1-overview'), p6: localStorage.getItem('rdProgress:yangjia-6-practice'), t: localStorage.getItem('rdTask:yangjia-6-practice:0')})")
    check(landed.startswith(("yangjia-3-dashi", "yangjia-4-trade")), f"{name}: 旧链接 50% 进度应续读到第 ③/④ 篇，实际 {landed}", problems)
    check(store["p1"] == "100.0", f"{name}: 旧进度未迁移到第 ① 篇 {store}", problems)
    check(store["p6"] in (None, "0.0"), f"{name}: 未读部分不应标记进度 {store}", problems)
    check(store["t"] == "1", f"{name}: 旧清单勾选未迁移 {store}", problems)
    page.goto(base + OLD_URL + "#第-9-章-仓位与赢面胜率--涨跌空间比", wait_until="domcontentloaded")
    page.wait_for_url(lambda u: "yangjia-5-position" in u, timeout=10000)
    check("#" in page.url, f"{name}: 旧锚点跳转丢了 #章节 {page.url}", problems)
    page.wait_for_load_state("networkidle")
    if name in ("desktop-1440", "iphone13"):
        page.screenshot(path=str(SHOTS / f"redirect-anchor-{name}.png"))
    page.evaluate("() => Object.keys(localStorage).filter(k => /^rd(Progress|Task|Migrated):/.test(k)).forEach(k => localStorage.removeItem(k))")


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
            if aid in SHOT_AS:
                tag = SHOT_AS[aid]
                page.screenshot(path=str(SHOTS / f"{tag}-top-{name}.png"))
                # 从顶部一次滚到图示上方 120px（模拟向下阅读），手机端悬浮目录按钮应收起、不压住图示
                page.evaluate("() => { const el = document.querySelector('pre.mermaid svg'); window.scrollTo(0, el.getBoundingClientRect().top + scrollY - 120); }")
                page.wait_for_timeout(450)
                page.screenshot(path=str(SHOTS / f"{tag}-mermaid-{name}.png"))
                if name == "iphone13":
                    check(page.evaluate(FAB_CLEAR), f"{name}: {aid} 悬浮目录按钮压住了 mermaid 图示", problems)
                page.locator("details").first.scroll_into_view_if_needed()
                page.locator("details > summary").first.click()
                page.evaluate("window.scrollBy(0,-160)")
                page.wait_for_timeout(250)
                page.screenshot(path=str(SHOTS / f"{tag}-selfcheck-{name}.png"))
                page.screenshot(path=str(SHOTS / f"{tag}-full-{name}.png"), full_page=True)


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
            check(page.locator('.rd-card[data-article="chaogu-yangjia"], a[href="/reading/chaogu-yangjia.html"]').count() == 0, f"{name}: 书库仍显示旧长文", problems)
            check(page.evaluate("document.documentElement.scrollWidth<=innerWidth+1"), f"{name}: 书库页横向溢出", problems)
            page.screenshot(path=str(SHOTS / f"library-{name}.png"), full_page=True)

            check_series(page, base, name, problems)
            check_redirect(page, base, name, problems)

            page.goto(base + ARTICLE, wait_until="networkidle")
            page.wait_for_function(MERMAID_OK, timeout=20000)
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
                # 悬浮目录按钮：向下滚动收起，向上滚动再出现
                page.evaluate("window.scrollBy(0, 600)")
                page.wait_for_timeout(450)
                check(not page.locator("#rdTocFab").is_visible(), f"{name}: 向下滚动后悬浮目录按钮未收起", problems)
                page.evaluate("window.scrollBy(0, -240)")
                page.wait_for_timeout(450)
                check(page.locator("#rdTocFab").is_visible(), f"{name}: 向上滚动后悬浮目录按钮未出现", problems)
                page.screenshot(path=str(SHOTS / f"toc-fab-shown-{name}.png"))
                page.locator("#rdTocFab").click()
                page.wait_for_timeout(350)
                check(page.locator("#rdToc").is_visible(), f"{name}: 目录抽屉未打开", problems)
                page.screenshot(path=str(SHOTS / f"toc-{name}.png"))
                page.locator(".rd-toc-list a:visible").nth(2).click()
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
