import json
import re
import tempfile
import textwrap
import unittest
from pathlib import Path

from scripts.reports import gen_reading
from scripts.reports.gen_reading import ReadingConfigError, build_toc, generate, load_config, load_glossary
from scripts.reports.reading_markdown import MarkdownRenderer, render_markdown, slugify

ROOT = Path(__file__).resolve().parents[1]
CALLOUTS = {"core": {"label": "核心心法", "icon": "◆", "tone": "core"}, "tip": {"label": "解读", "icon": "💡", "tone": "tip"},
            "warn": {"label": "警示", "icon": "⚠️", "tone": "warn"}, "note": {"label": "说明", "icon": "ⓘ", "tone": "note"}}
GLOSSARY = {"龙头": {"def": "领涨股", "aliases": ["龙头股"]}, "情绪周期": {"def": "情绪循环", "aliases": []}}


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


class ReadingConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write(self.root / "content/reading/demo/article.md", "# 标题\n\n## 第一章\n\n正文\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _config(self, articles: str, categories: str = "  - {id: masters, name: 前辈心法}\n  - {id: tips, name: 技巧}\n"):
        return write(self.root / "config/reading.yaml", "categories:\n" + categories + "articles:\n" + articles)

    def test_repo_config_is_valid_and_first_article_is_masters(self):
        cfg = load_config()
        self.assertEqual([c["name"] for c in cfg["categories"]][:4], ["前辈心法", "价值投资", "技巧", "买卖逻辑"])
        first = cfg["articles"][0]
        self.assertEqual(first["id"], "yangjia-1-overview")
        self.assertEqual(first["category"], "masters")
        self.assertTrue(first["source_path"].is_file())
        self.assertIn("core", cfg["callouts"])
        self.assertGreater(len(load_glossary()), 10)

    def test_articles_sorted_by_category_then_order(self):
        write(self.root / "content/reading/b/article.md", "# b\n")
        write(self.root / "content/reading/c/article.md", "# c\n")
        path = self._config("  - {id: c, title: C, category: tips, order: 1}\n"
                            "  - {id: b, title: B, category: masters, order: 2}\n"
                            "  - {id: demo, title: D, category: masters, order: 1}\n")
        cfg = load_config(path, self.root)
        self.assertEqual([a["id"] for a in cfg["articles"]], ["demo", "b", "c"])

    def test_rejects_unknown_category_duplicate_id_and_missing_source(self):
        with self.assertRaises(ReadingConfigError):
            load_config(self._config("  - {id: demo, title: D, category: nope}\n"), self.root)
        with self.assertRaises(ReadingConfigError):
            load_config(self._config("  - {id: demo, title: D, category: masters}\n  - {id: demo, title: E, category: masters}\n"), self.root)
        with self.assertRaises(ReadingConfigError):
            load_config(self._config("  - {id: ghost, title: G, category: masters}\n"), self.root)
        with self.assertRaises(ReadingConfigError):
            load_config(self._config("  - {id: Bad_ID, title: G, category: masters}\n"), self.root)

    def _series_config(self, series: str, articles: str):
        for aid in ("s1", "s2", "s3", "solo"):
            write(self.root / f"content/reading/{aid}/article.md", f"# {aid}\n\n## 一\n\n正文\n")
        return write(self.root / "config/reading.yaml",
                     "categories:\n  - {id: masters, name: 前辈心法}\n  - {id: tips, name: 技巧}\n"
                     "series:\n" + series + "articles:\n" + articles)

    def test_series_groups_articles_in_order_and_inherits_category(self):
        path = self._series_config(
            "  - {id: ask, name: A 系列, category: masters}\n  - {id: empty, name: 空系列, category: tips}\n",
            "  - {id: s2, title: S2, series: ask, order: 2}\n"
            "  - {id: solo, title: Solo, category: masters, order: 5}\n"
            "  - {id: s1, title: S1, series: ask, order: 1}\n"
            "  - {id: s3, title: S3, category: masters, series: ask, order: 3}\n")
        cfg = load_config(path, self.root)
        self.assertEqual([s["id"] for s in cfg["series"]], ["ask"])  # 空系列不展示
        ser = cfg["series"][0]
        self.assertEqual([a["id"] for a in ser["articles"]], ["s1", "s2", "s3"])
        self.assertEqual(cfg["articles"][0]["category"], "masters")
        by_id = {a["id"]: a for a in cfg["articles"]}
        self.assertEqual(gen_reading.series_members(cfg, by_id["s2"]), ser["articles"])
        self.assertEqual(gen_reading.series_members(cfg, by_id["solo"]), [])

    def test_series_validation(self):
        bad = [
            ("  - {id: ask, name: A, category: nope}\n", "  - {id: s1, title: S1, series: ask}\n"),
            ("  - {id: ask, category: masters}\n", "  - {id: s1, title: S1, series: ask}\n"),
            ("  - {id: ask, name: A, category: masters}\n  - {id: ask, name: B, category: masters}\n", "  - {id: s1, title: S1, series: ask}\n"),
            ("  - {id: ask, name: A, category: masters}\n", "  - {id: s1, title: S1, series: ghost}\n"),
            ("  - {id: ask, name: A, category: masters}\n", "  - {id: s1, title: S1, category: tips, series: ask}\n"),
        ]
        for series, articles in bad:
            with self.subTest(series=series, articles=articles), self.assertRaises(ReadingConfigError):
                load_config(self._series_config(series, articles), self.root)

    def test_series_library_card_nav_and_pager(self):
        path = self._series_config(
            "  - {id: ask, name: A 系列, category: masters, description: 简介}\n",
            "  - {id: s1, title: S1, series: ask, order: 1}\n  - {id: s2, title: S2, short_title: 第二篇, series: ask, order: 2}\n"
            "  - {id: s3, title: S3, series: ask, order: 3}\n  - {id: solo, title: Solo, category: masters, order: 9}\n")
        write(self.root / "config/reading_glossary.yaml", "terms: {}\n")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            generate(path, self.root / "config/reading_glossary.yaml", out, self.root)
            index = (out / "reading.html").read_text(encoding="utf-8")
            self.assertEqual(index.count('class="rd-card rd-series-card"'), 1)
            self.assertIn("A 系列 <small>（3 篇）</small>", index)
            self.assertIn('data-series-articles="s1,s2,s3"', index)
            self.assertLess(index.index("/reading/s1.html"), index.index("/reading/s2.html"))
            self.assertIn('data-article="solo"', index)
            s2 = (out / "reading/s2.html").read_text(encoding="utf-8")
            self.assertIn('class="rd-series"', s2)
            self.assertIn("第 2 / 3 篇", s2)
            self.assertIn('aria-current="page"><span class="rd-series-no">2</span><span>第二篇</span>', s2)
            self.assertIn('rd-pager-prev" href="/reading/s1.html"', s2)
            self.assertIn('rd-pager-next" href="/reading/s3.html"', s2)
            s3 = (out / "reading/s3.html").read_text(encoding="utf-8")
            self.assertNotIn("rd-pager-next", s3)  # 系列末篇不跳到系列外
            solo = (out / "reading/solo.html").read_text(encoding="utf-8")
            self.assertNotIn('class="rd-series"', solo)
            self.assertNotIn("rd-pager-prev", solo)

    def test_repo_series_have_sources_disclaimer_and_interactive_blocks(self):
        cfg = load_config()
        names = {s["id"]: s for s in cfg["series"]}
        self.assertEqual(len(names["asking"]["articles"]), 4)
        self.assertEqual(len(names["zhiye"]["articles"]), 4)
        self.assertEqual(len(names["chaogu-yangjia"]["articles"]), 10)
        self.assertEqual([a["id"] for a in names["gaipian"]["articles"]],
                         ["gaipian-1-life", "gaipian-2-heli", "gaipian-3-discipline"])
        self.assertEqual([a["id"] for a in names["zhang"]["articles"]], ["zhang-1-life", "zhang-2-style"])
        self.assertEqual([a["id"] for a in names["buffett"]["articles"]],
                         ["buffett-1-life", "buffett-2-moat", "buffett-3-value", "buffett-4-competence", "buffett-5-cases"])
        self.assertEqual([a["id"] for a in names["zhao"]["articles"]], ["zhao-1-life", "zhao-2-style"])
        self.assertEqual([a["id"] for a in names["xiaoeyu"]["articles"]], ["xiaoeyu-1-life", "xiaoeyu-2-style"])
        self.assertEqual([a["id"] for a in names["tuixue"]["articles"]],
                         ["tuixue-1-life", "tuixue-2-mode", "tuixue-3-xiaoming"])
        self.assertIn("value", [c["id"] for c in cfg["categories"]])  # 价值投资：巴菲特，后续芒格、段永平
        expected_cat = {"buffett": "value"}
        for sid in ("asking", "zhiye", "chaogu-yangjia", "gaipian", "zhang", "buffett", "zhao", "xiaoeyu", "tuixue"):
            for art in names[sid]["articles"]:
                text = art["source_path"].read_text(encoding="utf-8")
                with self.subTest(article=art["id"]):
                    self.assertEqual(art["category"], expected_cat.get(sid, "masters"))
                    self.assertIn("::: note", text)
                    self.assertIn("不构成任何投资建议", text)
                    self.assertIn("::: summary", text)
                    self.assertIn("```mermaid", text)
                    self.assertIn("<details>", text)
                    self.assertIn("- [ ]", text)
                    self.assertIn("## 来源", text)
                    self.assertRegex(text.split("## 来源", 1)[1], r"https?://")


class RedirectTest(unittest.TestCase):
    def test_repo_redirect_keeps_old_url_anchors_progress_and_tasks(self):
        cfg = load_config()
        red = {r["from"]: r for r in cfg["redirects"]}["chaogu-yangjia"]
        self.assertEqual(red["to"], "yangjia-1-overview")
        self.assertEqual(len(red["parts"]), 6)
        self.assertEqual(red["tasks_to"], "yangjia-6-practice")
        self.assertFalse((ROOT / "content/reading/chaogu-yangjia").exists())
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "reading/chaogu-yangjia/images").mkdir(parents=True)  # 旧图片目录会被清掉
            generate(out_dir=out)
            page = (out / "reading/chaogu-yangjia.html").read_text(encoding="utf-8")
            self.assertFalse((out / "reading/chaogu-yangjia").exists())
            data = json.loads(re.search(r"var C=(\{.*?\});var S=", page, flags=re.S).group(1))
            self.assertEqual(data["anchors"]["第-9-章-仓位与赢面胜率--涨跌空间比"], "yangjia-5-position")
            self.assertEqual(data["anchors"]["第-0-章-一句话读懂这份资料"], "yangjia-1-overview")
            self.assertEqual(data["anchors"]["附录-原文结构与页码索引"], "yangjia-6-practice")
            spans = data["spans"]
            self.assertEqual([s_[0] for s_ in spans], red["parts"])
            self.assertEqual(spans[0][1], 0)
            self.assertAlmostEqual(spans[-1][2], 100, delta=0.1)
            for a, b in zip(spans, spans[1:]):
                self.assertAlmostEqual(a[2], b[1], delta=0.02)
            self.assertEqual(data["tasks"], "yangjia-6-practice")
            self.assertIn('url=/reading/yangjia-1-overview.html', page)  # 无 JS 回退
            self.assertIn("location.replace", page)
            self.assertIn("rdMigrated:", page)
            # 旧清单的勾选序号能原样对应到新文章（第 ⑥ 篇的清单前没有新增复选框）
            old_tasks = 13
            sixth = (out / "reading/yangjia-6-practice.html").read_text(encoding="utf-8")
            self.assertEqual(sixth.count('type="checkbox"'), old_tasks)

    def test_redirect_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "content/reading/a/article.md", "# a\n")
            base = "categories:\n  - {id: masters, name: 前辈心法}\narticles:\n  - {id: a, title: A, category: masters}\n"
            for redirects in ("redirects:\n  - {from: a, to: a}\n",            # 与现有文章同名
                              "redirects:\n  - {from: old, to: ghost}\n",      # 指向不存在
                              "redirects:\n  - {from: old, to: a, parts: [a, nope]}\n",
                              "redirects:\n  - {from: Old_X, to: a}\n",
                              "redirects:\n  - {from: old, to: a}\n  - {from: old, to: a}\n"):
                with self.subTest(redirects=redirects), self.assertRaises(ReadingConfigError):
                    load_config(write(root / "config/reading.yaml", base + redirects), root)
            cfg = load_config(write(root / "config/reading.yaml", base + "redirects:\n  - {from: old, parts: [a]}\n"), root)
            self.assertEqual(cfg["redirects"][0]["to"], "a")


class MarkdownRenderTest(unittest.TestCase):
    def test_github_style_slug_keeps_existing_anchor_links(self):
        self.assertEqual(slugify("第 9 章 仓位与赢面：胜率 × 涨跌空间比"), "第-9-章-仓位与赢面胜率--涨跌空间比")
        self.assertEqual(slugify("8.2 核心工具：“假如我现在空仓”测试（p.19、27）"), "82-核心工具假如我现在空仓测试p1927")

    def test_toc_nests_h3_under_h2_and_dedupes_ids(self):
        r = render_markdown("# T\n\n## 甲\n\n### 甲.1\n\n### 甲.1\n\n## 乙\n")
        toc = build_toc(r.headings)
        self.assertEqual([c["text"] for c in toc], ["甲", "乙"])
        self.assertEqual([c["id"] for c in toc[0]["children"]], ["甲1", "甲1-1"])

    def test_callouts_highlight_and_emoji_blocks(self):
        md = "::: core 核心公式\n> 原文 ==重点== **加粗**\n:::\n\n💡 **讲解**：类比\n\n> ⚠️ **坑**：错误\n> > 嵌套原文\n"
        r = render_markdown(md, callouts=CALLOUTS, emoji_map={"💡": "tip", "⚠️": "warn"})
        h = r.html
        self.assertIn('rd-callout--core', h)
        self.assertIn('核心公式', h)
        self.assertIn('<mark class="rd-mark">重点</mark>', h)
        self.assertIn('<strong>加粗</strong>', h)
        self.assertIn('rd-callout--tip rd-callout--auto', h)
        self.assertEqual(h.count('rd-callout--warn'), 1)  # 引用里的 ⚠️ 段落不再二次套框
        self.assertIn('<blockquote class="rd-quote"><p>嵌套原文</p></blockquote>', h)

    def test_explicit_and_auto_terms_mark_first_occurrence_only(self):
        md = "![龙头](x.png)\n\n龙头股和[[情绪周期]]，再说龙头。\n\n# 龙头标题不标注\n"
        r = MarkdownRenderer(glossary=GLOSSARY, glossary_auto=True, image_resolver=lambda s: "/img/" + s).render(md)
        self.assertEqual(r.html.count('data-term="龙头"'), 1)
        self.assertIn('>龙头股</dfn>', r.html)          # 别名按最长匹配
        self.assertIn('alt="龙头"', r.html)              # 属性里不插入气泡
        self.assertEqual(r.html.count('data-term="情绪周期"'), 1)
        self.assertEqual(sorted(r.terms_used), sorted(["龙头", "情绪周期"]))

    def test_callout_can_opt_out_of_auto_terms(self):
        callouts = dict(CALLOUTS, summary={"label": "要点", "tone": "summary", "auto_terms": False})
        md = "::: summary\n龙头在要点卡里不标\n:::\n\n正文里的龙头要标。\n"
        r = MarkdownRenderer(callouts=callouts, glossary=GLOSSARY, glossary_auto=True).render(md)
        self.assertEqual(r.html.count('data-term="龙头"'), 1)
        self.assertLess(r.html.index('</aside>'), r.html.index('data-term="龙头"'))

    def test_tables_tasks_details_and_mermaid(self):
        md = ("| a | b |\n|---|---|\n| 1 | 2 |\n\n- [ ] 待办\n- [x] 完成\n\n<details>\n<summary>参考</summary>\n\n- 要点\n</details>\n\n"
              "```mermaid\nflowchart LR\n  A-->B\n```\n")
        r = render_markdown(md)
        self.assertIn('<table class="rd-table">', r.html)
        self.assertEqual(r.html.count('type="checkbox"'), 2)
        self.assertIn('<summary>参考</summary>', r.html)
        self.assertIn('<pre class="mermaid">flowchart LR', r.html)
        self.assertTrue(r.has_mermaid)

    def test_image_caption_and_missing_image(self):
        md = "![图](images/a.png)\n\n*图 1：说明*\n\n![缺](images/none.png)\n"
        r = MarkdownRenderer(image_resolver=lambda s: "/reading/x/" + s if "a.png" in s else None).render(md)
        self.assertIn('src="/reading/x/images/a.png"', r.html)
        self.assertIn('<figcaption>图 1：说明</figcaption>', r.html)
        self.assertNotIn('<p><em>图 1', r.html)
        self.assertEqual(r.missing_images, ["images/none.png"])


class GenerateTest(unittest.TestCase):
    def test_generate_repo_article_writes_pages_and_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            written = generate(out_dir=out)
            self.assertEqual(written[0], out / "reading.html")
            parts = ["yangjia-1-overview", "yangjia-2-emotion", "yangjia-3-dashi", "yangjia-4-trade", "yangjia-5-position", "yangjia-6-practice"]
            total_imgs, total_toc = 0, 0
            for aid in parts:
                page = (out / f"reading/{aid}.html").read_text(encoding="utf-8")
                imgs = re.findall(r'<img src="([^"]+)"', page)
                total_imgs += len(imgs)
                for src in imgs:
                    self.assertTrue(src.startswith(f"/reading/{aid}/images/"), src)
                    self.assertTrue((out / src.lstrip("/")).is_file(), src)
                ids = set(re.findall(r'<h[23] id="([^"]+)"', page))
                toc_links = re.findall(r'data-toc="([^"]+)"', page)
                total_toc += len(toc_links)
                self.assertTrue(set(toc_links) <= ids)
                self.assertTrue(set(re.findall(r'href="#([^"]+)"', page)) <= ids)
                self.assertIn('data-active="reading"', page)
                self.assertIn('rd-callout--summary', page)
                self.assertIn('id="rdGlossary"', page)
                self.assertNotIn("::: ", page)
                self.assertNotIn("==", re.sub(r"<pre class=\"mermaid\">.*?</pre>|<script.*?</script>", "", page, flags=re.S))
            self.assertEqual(total_imgs, 4)  # 原长文的 4 张图全部跟随所在章节
            self.assertGreater(total_toc, 50)
            # 跨篇链接（含锚点）都指向存在的小节
            heading_ids = {p.stem: set(re.findall(r'\sid="([^"]+)"', p.read_text(encoding="utf-8"))) for p in (out / "reading").glob("*.html")}
            for p in (out / "reading").glob("yangjia-*.html"):
                for aid, anchor in re.findall(r'href="/reading/([a-z0-9-]+)\.html(?:#([^"]*))?"', p.read_text(encoding="utf-8")):
                    self.assertIn(aid, heading_ids, f"{p.stem} -> {aid}")
                    if anchor:
                        self.assertIn(anchor, heading_ids[aid], f"{p.stem} -> {aid}#{anchor}")
            index = (out / "reading.html").read_text(encoding="utf-8")
            self.assertIn('/reading/yangjia-1-overview.html', index)
            self.assertNotIn('/reading/chaogu-yangjia.html', index)  # 旧长文只保留跳转页，不进书库
            self.assertIn('待收录', index)  # 技巧 / 买卖逻辑暂无文章

    def test_layout_width_and_toc_toggle_controls(self):
        self.assertEqual(gen_reading._layout_style({"layout": {"measure_chars": 56, "wide_chars": 70}}),
                         ' style="--rd-measure-ch:56;--rd-wide-ch:70"')
        self.assertEqual(gen_reading._layout_style({}), "")
        with self.assertRaises(ReadingConfigError):
            gen_reading._layout_style({"layout": {"measure_chars": 200}})
        with tempfile.TemporaryDirectory() as tmp:
            generate(out_dir=Path(tmp))
            page = (Path(tmp) / "reading/yangjia-1-overview.html").read_text(encoding="utf-8")
        for needle in ('id="rdTocToggle"', 'aria-controls="rdToc"', 'id="rdTocCollapse"', 'id="rdWidthToggle"', "--rd-measure-ch:56",
                       "localStorage.getItem('rdToc')==='collapsed'"):
            self.assertIn(needle, page)

    def test_shared_nav_lists_reading(self):
        shell = (ROOT / "scripts/services/static/app-shell.js").read_text(encoding="utf-8")
        self.assertIn("{key:'reading',href:'/reading.html'", shell)
        from scripts.reports.gen_index import GROUPS, SERVICE_URLS
        self.assertEqual(SERVICE_URLS["reading.html"], "http://127.0.0.1:8765/reading.html")
        self.assertTrue(any(item[0] == "reading.html" for g in GROUPS for item in g["items"]))


if __name__ == "__main__":
    unittest.main()
