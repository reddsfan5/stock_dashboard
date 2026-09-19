#!/usr/bin/env python3
"""刷新观察池按加入批次的收益监控。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from data.users import UserRepository, bootstrap_admin
from data.watchlist_monitor import MONITOR_CACHE, WatchlistMonitorRepository, WatchlistMonitorService
from scripts.services.watchlist_monitor import write_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="刷新观察池收益监控")
    parser.add_argument("--as-of", default=None, help="结果截止日期 YYYY-MM-DD")
    parser.add_argument("--rebuild", action="store_true", help="重新计算全部历史观察池")
    parser.add_argument("--db", type=Path, default=None, help="观察池 SQLite 路径")
    parser.add_argument("--html-out", type=Path, default=None, help="页面输出路径")
    args = parser.parse_args(argv)
    bootstrap_admin()
    service = WatchlistMonitorService() if args.db is None else WatchlistMonitorService(
        repository=WatchlistMonitorRepository(args.db)
    )
    results = []
    for user in UserRepository().list_users(enabled_only=True):
        try:
            results.append(service.refresh(user_id=user["id"], as_of=args.as_of, rebuild=args.rebuild))
        except Exception as exc:  # noqa: BLE001
            service.repository.record_failure(user["id"], exc)
            results.append({"user_id": user["id"], "error": str(exc)})
    write_app(args.html_out or Path(__file__).resolve().parents[2] / "output" / "watchlist_monitor.html")
    payload = {"users": results, "generated_at": results[-1].get("generated_at") if results else None}
    print(json.dumps(payload, ensure_ascii=False))
    print(f"✓ 监控缓存: {MONITOR_CACHE}")
    return 0 if not any(item.get("error") for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
