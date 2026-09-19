#!/usr/bin/env python3
"""刷新每日精选收益结果和收益监控页面。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from data.shortlist_monitor import MONITOR_CACHE, ShortlistMonitorService
from scripts.services.shortlist_monitor import write_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="刷新每日精选收益监控")
    parser.add_argument("--as-of", default=None, help="结果截止日期 YYYY-MM-DD")
    parser.add_argument("--rebuild", action="store_true", help="重新计算全部历史短名单")
    parser.add_argument("--db", type=Path, default=None, help="短名单 SQLite 路径")
    parser.add_argument("--html-out", type=Path, default=None, help="页面输出路径")
    args = parser.parse_args(argv)

    repository = None
    try:
        if args.db:
            from data.shortlist_monitor import ShortlistMonitorRepository

            repository = ShortlistMonitorRepository(args.db)
            service = ShortlistMonitorService(repository=repository)
        else:
            service = ShortlistMonitorService()
            repository = service.repository
        result = service.refresh(as_of=args.as_of, rebuild=args.rebuild)
    except Exception as exc:  # noqa: BLE001
        if repository is not None:
            repository.record_failure(exc)
        raise
    finally:
        write_app(args.html_out or Path(__file__).resolve().parents[2] / "output" / "shortlist_monitor.html")
    print(json.dumps(result, ensure_ascii=False))
    print(f"✓ 监控缓存: {MONITOR_CACHE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
