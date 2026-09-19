"""观察池收益监控页面生成器。"""

from __future__ import annotations

from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[2]
OUT_HTML = PROJECT_DIR / "output" / "watchlist_monitor.html"
TEMPLATE_HTML = Path(__file__).resolve().parent / "templates" / "watchlist_monitor.html"


def build_html() -> str:
    return TEMPLATE_HTML.read_text(encoding="utf-8")
def write_app(path: Path = OUT_HTML):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_html(), encoding="utf-8")
    print(f"✓ 观察池收益监控页面: {path}")
