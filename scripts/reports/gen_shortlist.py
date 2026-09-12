#!/usr/bin/env python3
"""生成每日短名单理由卡（JSON + HTML）。

依赖最近一次选股仪表盘 output/dashboard.html。推荐节奏：
  python -m scripts.screen
  python -m scripts.reports.gen_shortlist
  # 或随每日操盘：python -m scripts.reports.gen_daily_ops
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.reports.shortlist_cards import build_from_paths, render_shortlist_html

PROJECT_DIR = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_DIR / "output"
CACHE_DIR = PROJECT_DIR / "cache"
DEFAULT_DASHBOARD = OUTPUT_DIR / "dashboard.html"
OUT_JSON = CACHE_DIR / "daily_shortlist.json"
OUT_HTML = OUTPUT_DIR / "shortlist.html"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成每日短名单理由卡")
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--user-id", type=int, default=1, help="观察池所属用户，默认 xiaodong=1")
    parser.add_argument("--skip-sector-refresh", action="store_true")
    parser.add_argument("--json-out", type=Path, default=OUT_JSON)
    parser.add_argument("--html-out", type=Path, default=OUT_HTML)
    args = parser.parse_args(argv)

    payload = build_from_paths(
        dashboard_path=args.dashboard,
        limit=max(5, min(30, args.limit)),
        refresh_sector=not args.skip_sector_refresh,
        user_id=args.user_id,
    )
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.html_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    args.html_out.write_text(render_shortlist_html(payload), encoding="utf-8")
    print(f"✓ 短名单 JSON: {args.json_out}（{len(payload.get('cards') or [])} 只，候选 {payload.get('candidate_count')}）")
    print(f"✓ 短名单 HTML: {args.html_out}")
    print(f"  打开: http://127.0.0.1:8765/shortlist.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
