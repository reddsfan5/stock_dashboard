#!/usr/bin/env python3
"""市场资讯时间轴页面生成器。"""

from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[2]
OUT_HTML = PROJECT_DIR / "output" / "market_news.html"
TEMPLATE_HTML = Path(__file__).resolve().parent / "templates" / "market_news.html"


def build_html() -> str:
    return TEMPLATE_HTML.read_text(encoding="utf-8")


def write_app(path=OUT_HTML):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_html(), encoding="utf-8")
    print(f"✓ 市场资讯时间轴: {target}")
    return target


if __name__ == "__main__":
    write_app()
    print("  动态资讯查询需要服务：python -m scripts.serve start news")
