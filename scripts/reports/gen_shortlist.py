#!/usr/bin/env python3
"""生成每日精选（JSON + HTML）。

依赖最近一次选股仪表盘 output/dashboard.html。推荐节奏：
  python -m scripts.screen
  python -m scripts.reports.gen_shortlist
  # 或随每日操盘：python -m scripts.reports.gen_daily_ops
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from data.shortlist import DEFAULT_DB_PATH, ShortlistRepository
from scripts.reports.shortlist_cards import build_from_paths, render_shortlist_html

PROJECT_DIR = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_DIR / "output"
CACHE_DIR = PROJECT_DIR / "cache"
DEFAULT_DASHBOARD = OUTPUT_DIR / "dashboard.html"
OUT_JSON = CACHE_DIR / "daily_shortlist.json"
OUT_HTML = OUTPUT_DIR / "shortlist.html"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成每日精选")
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--user-id", type=int, default=1, help="观察池所属用户，默认 xiaodong=1")
    parser.add_argument("--skip-sector-refresh", action="store_true")
    parser.add_argument("--json-out", type=Path, default=OUT_JSON)
    parser.add_argument("--html-out", type=Path, default=OUT_HTML)
    parser.add_argument(
        "--history-db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="每日候选历史库，默认 state/shortlist.sqlite3",
    )
    args = parser.parse_args(argv)

    payload = build_from_paths(
        dashboard_path=args.dashboard,
        limit=max(5, min(30, args.limit)),
        refresh_sector=not args.skip_sector_refresh,
        user_id=args.user_id,
    )
    history = ShortlistRepository(args.history_db)
    saved = history.save(payload)
    history_dates = history.dates()
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.html_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    args.html_out.write_text(
        render_shortlist_html(payload, history_dates=history_dates),
        encoding="utf-8",
    )
    # 收益监控是每日精选的旁路结果：即使行情历史不完整，也不能阻断
    # 每日精选本身的生成。监控刷新会幂等覆盖对应日期的 5 个观察窗口。
    monitor_repository = None
    try:
        from data.shortlist_monitor import ShortlistMonitorRepository, ShortlistMonitorService
        from scripts.services.shortlist_monitor import write_app as write_monitor_app

        monitor_repository = ShortlistMonitorRepository(args.history_db)
        monitor_result = ShortlistMonitorService(repository=monitor_repository).refresh()
        print(
            f"✓ 每日精选收益监控: {monitor_result.get('rows', 0)} 条 / "
            f"{monitor_result.get('dates', 0)} 个交易日"
        )
    except Exception as exc:  # noqa: BLE001
        if monitor_repository is not None:
            try:
                monitor_repository.record_failure(exc)
            except Exception:
                pass
        print(f"! 每日精选收益监控刷新失败（不影响每日精选）: {exc}")
    finally:
        # 即使旁路计算失败，也更新页面模板，让用户看到最近成功时间和失败原因。
        try:
            from scripts.services.shortlist_monitor import write_app as write_monitor_app
            write_monitor_app()
        except Exception as page_exc:  # noqa: BLE001
            print(f"! 每日精选收益监控页面生成失败（不影响每日精选）: {page_exc}")
    print(
        f"✓ 每日精选历史: {args.history_db}（{saved['market_date']}，"
        f"{saved['selected_count']} 只）"
    )
    print(f"✓ 每日精选 JSON: {args.json_out}（{len(payload.get('cards') or [])} 只，候选 {payload.get('candidate_count')}）")
    print(f"✓ 每日精选 HTML: {args.html_out}")
    print(f"  打开: http://127.0.0.1:8765/shortlist.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
