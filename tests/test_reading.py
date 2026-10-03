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
        self.assertEqual([c["name"] for c in cfg["categories"]][:3], ["前辈心法", "技巧", "买卖逻辑"])
        first = cfg["articles"][0]
        self.assertEqual(first["id"], "chaogu-yangjia")
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
            page = (out / "reading/chaogu-yangjia.html").read_text(encoding="utf-8")
            imgs = re.findall(r'<img src="([^"]+)"', page)
            self.assertEqual(len(imgs), 4)
            for src in imgs:
                self.assertTrue(src.startswith("/reading/chaogu-yangjia/images/"), src)
                self.assertTrue((out / src.lstrip("/")).is_file(), src)
            ids = set(re.findall(r'<h[23] id="([^"]+)"', page))
            toc_links = re.findall(r'data-toc="([^"]+)"', page)
            self.assertGreater(len(toc_links), 50)
            self.assertTrue(set(toc_links) <= ids)
            self.assertTrue(set(re.findall(r'href="#([^"]+)"', page)) <= ids)
            self.assertIn('data-active="reading"', page)
            self.assertIn('rd-callout--summary', page)
            self.assertIn('id="rdGlossary"', page)
            self.assertNotIn("::: ", page)
            self.assertNotIn("==", re.sub(r"<pre class=\"mermaid\">.*?</pre>|<script.*?</script>", "", page, flags=re.S))
            index = (out / "reading.html").read_text(encoding="utf-8")
            self.assertIn('/reading/chaogu-yangjia.html', index)
            self.assertIn('待收录', index)  # 技巧 / 买卖逻辑暂无文章

    def test_layout_width_and_toc_toggle_controls(self):
        self.assertEqual(gen_reading._layout_style({"layout": {"measure_chars": 56, "wide_chars": 70}}),
                         ' style="--rd-measure-ch:56;--rd-wide-ch:70"')
        self.assertEqual(gen_reading._layout_style({}), "")
        with self.assertRaises(ReadingConfigError):
            gen_reading._layout_style({"layout": {"measure_chars": 200}})
        with tempfile.TemporaryDirectory() as tmp:
            generate(out_dir=Path(tmp))
            page = (Path(tmp) / "reading/chaogu-yangjia.html").read_text(encoding="utf-8")
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
