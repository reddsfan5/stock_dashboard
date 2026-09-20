"""Sync Tonghuashun F10 题材 tags into classifications DB.

Usage:
  python -m scripts.tools.sync_ths_themes --codes sh600519,sz300750
  python -m scripts.tools.sync_ths_themes --limit 20
  python -m scripts.tools.sync_ths_themes --retry-errors --workers 2 --sleep 0.25
  python -m scripts.tools.sync_ths_themes --missing --workers 2
"""
from __future__ import annotations

import argparse

from scripts.services.ths_theme_sync import (
    list_error_codes,
    list_missing_codes,
    sync_ths_themes,
)


def main():
    parser = argparse.ArgumentParser(description="同步同花顺 F10 题材到 classifications DB")
    parser.add_argument("--codes", default="", help="逗号分隔代码；默认全市场 security 表")
    parser.add_argument("--limit", type=int, default=None, help="只同步前 N 只（联调）")
    parser.add_argument("--sleep", type=float, default=0.15, help="每只股票请求后额外休眠秒")
    parser.add_argument("--workers", type=int, default=2, help="并发数（建议 ≤3，防 403）")
    parser.add_argument("--retry-errors", action="store_true", help="只重试 sync_state 里 ths=error 的代码")
    parser.add_argument("--missing", action="store_true", help="只同步尚未 ths=ok 的代码")
    args = parser.parse_args()

    if args.codes.strip():
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    elif args.retry_errors:
        codes = list_error_codes()
        print(f"retry-errors: {len(codes)} codes", flush=True)
    elif args.missing:
        codes = list_missing_codes()
        print(f"missing: {len(codes)} codes", flush=True)
    else:
        codes = None

    def progress(i, n, result):
        if i % 50 == 0 or i == n or i <= 5 or result.get("status") == "error":
            print(
                f"{i}/{n} {result.get('code')} {result.get('status')} "
                f"themes={result.get('themes')} {result.get('error', '')}".strip(),
                flush=True,
            )

    stats = sync_ths_themes(
        codes=codes,
        limit=args.limit,
        sleep=args.sleep,
        workers=args.workers,
        progress=progress,
    )
    print(stats, flush=True)


if __name__ == "__main__":
    main()
