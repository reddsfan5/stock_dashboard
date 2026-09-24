#!/usr/bin/env python3
"""生成并发布市场简报（默认规则保底，不依赖大模型）。"""

from __future__ import annotations

import argparse
import json
import sys

from data.brief_builder import generate_and_publish, build_brief
from data.market_briefs import KINDS


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="规则保底生成市场简报")
    parser.add_argument("--kind", required=True, choices=sorted(KINDS))
    parser.add_argument("--date", help="YYYY-MM-DD，默认今天（上海）")
    parser.add_argument("--refresh-news", action="store_true", help="生成前刷新同花顺缓存")
    parser.add_argument("--no-html", action="store_true", help="不刷新 market_brief.html")
    parser.add_argument("--check", action="store_true", help="只生成并校验，不发布")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args(argv)

    try:
        if args.check:
            from data.market_briefs import MarketBriefRepository
            payload = build_brief(
                kind=args.kind,
                brief_date=args.date,
                refresh_news=args.refresh_news,
            )
            clean = MarketBriefRepository.validate(payload, kind=args.kind)
            out = {
                "ok": True,
                "check": True,
                "kind": clean["kind"],
                "brief_date": clean["brief_date"],
                "status": clean["status"],
                "news_count": len((clean.get("sections") or {}).get("news") or []),
                "headline": (clean.get("summary") or {}).get("headline"),
            }
        else:
            out = generate_and_publish(
                kind=args.kind,
                brief_date=args.date,
                refresh_news=args.refresh_news,
                refresh_html=not args.no_html,
            )
    except Exception as exc:  # noqa: BLE001
        print(f"✗ {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(
            f"[ok] {out.get('kind')} {out.get('brief_date')} "
            f"rev={out.get('revision', '-')} status={out.get('status')} "
            f"news={out.get('news_count')} — {out.get('headline')}"
        )
        for w in out.get("warnings") or []:
            title = w.get("title") if isinstance(w, dict) else str(w)
            print(f"  ! {title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
