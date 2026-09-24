#!/usr/bin/env python3
"""低频抓取东方财富 / 华尔街见闻公开快讯，并写入本地资讯中心。

安全约定：
- 默认请求间隔 1.5s，单源最多 8 页、整轮最多 20 次请求；
- 不并发、不登录、不绕过付费墙；
- --dry-run 只拉不写；--check 走既有 import 校验路径。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from data.market_news import MarketNewsRepository, SOURCE_CATALOG
from data.news_fetchers import FETCHERS, fetch_source

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _default_date() -> str:
    return datetime.now(SHANGHAI_TZ).date().isoformat()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="礼貌抓取公开资讯并导入本地资讯中心")
    parser.add_argument(
        "--source",
        required=True,
        choices=sorted(FETCHERS) + ["all"],
        help="eastmoney / wallstreetcn / all",
    )
    parser.add_argument(
        "--date", default=_default_date(), help="YYYY-MM-DD，默认今天（北京时间）"
    )
    parser.add_argument(
        "--min-interval",
        type=float,
        default=1.5,
        help="同一主机两次请求最小间隔秒数，默认 1.5，不得低于 1.0",
    )
    parser.add_argument(
        "--max-pages", type=int, default=8, help="单源最多翻页数，默认 8，上限 15"
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=20,
        help="单源最多 HTTP 次数，默认 20，上限 40",
    )
    parser.add_argument("--dry-run", action="store_true", help="只抓取并打印摘要，不写入数据库")
    parser.add_argument("--check", action="store_true", help="规范化校验但不写入")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    return parser


def _run_one(args, source_key: str) -> dict:
    if source_key not in SOURCE_CATALOG:
        raise ValueError(f"SOURCE_CATALOG 缺少 {source_key}")
    result = fetch_source(
        source_key,
        args.date,
        min_interval=args.min_interval,
        max_pages=min(max(args.max_pages, 1), 15),
        max_requests=min(max(args.max_requests, 1), 40),
    )
    payload = {
        "source_key": result.source_key,
        "market_date": args.date,
        "fetched": len(result.items),
        "complete": result.complete,
        "message": result.message,
        "request_count": result.request_count,
        "written": False,
    }
    if args.dry_run:
        payload["sample_titles"] = [item.get("title") for item in result.items[:5]]
        return payload

    repo = MarketNewsRepository()
    if args.check:
        normalized = [
            repo._normalize_import_item(item, source_key) for item in result.items
        ]
        payload["validated"] = len(normalized)
        return payload

    ingest = repo.ingest(
        result.items,
        source_key=source_key,
        complete=result.complete,
        message=result.message,
    )
    payload.update(ingest)
    payload["written"] = True
    return payload


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.min_interval < 1.0:
        print("✗ --min-interval 不得低于 1.0 秒", file=sys.stderr)
        return 2
    sources = sorted(FETCHERS) if args.source == "all" else [args.source]
    outcomes = []
    try:
        for index, source_key in enumerate(sources):
            if index > 0:
                time.sleep(max(args.min_interval, 2.0))
            outcomes.append(_run_one(args, source_key))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                outcomes if len(outcomes) > 1 else outcomes[0],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for row in outcomes:
            flag = "dry-run" if args.dry_run else ("check" if args.check else "ok")
            print(
                f"[{flag}] {row['source_key']} {row['market_date']}: "
                f"{row['fetched']} 条 / 请求 {row['request_count']} 次 / "
                f"{'完整' if row['complete'] else '部分'} — {row['message']}"
            )
            for title in row.get("sample_titles") or []:
                print(f"  · {title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
