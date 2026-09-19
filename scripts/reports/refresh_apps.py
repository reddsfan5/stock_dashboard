#!/usr/bin/env python3
"""刷新交互页 shell、板块图谱与每日操盘清单。

与 ``minute_viewer --serve`` 启动时的再生集合对齐，供日更 reports 阶段调用，
避免必须重启 HTTP 进程才能看到新的静态壳与清单页。
"""

from __future__ import annotations

import sys


def _run_write_app(label: str, import_path: str, attr: str = "write_app") -> None:
    module = __import__(import_path, fromlist=[attr])
    getattr(module, attr)()
    print(f"✓ {label}")


def _refresh_daily_ops() -> None:
    """刷新每日操盘清单及当日每日精选历史。

    每日更新时主动刷新板块快照；短名单生成器会复用该快照，
    不再重复计算。
    """
    from scripts.reports.gen_daily_ops import generate as generate_daily_ops

    generate_daily_ops(skip_sector=False)


def main() -> int:
    critical_jobs = (
        ("网格模拟器", "scripts.services.grid_simulator"),
        ("T+1 训练", "scripts.services.trading_trainer"),
        ("选股日记", "scripts.services.stock_journal"),
        ("市场资讯", "scripts.services.market_news"),
        ("观察池", "scripts.services.watchlist"),
        ("标的上下文", "scripts.services.symbol_context"),
    )
    failed = []
    for label, import_path in critical_jobs:
        try:
            _run_write_app(label, import_path)
        except Exception as exc:
            print(f"✗ {label} 刷新失败: {exc}", file=sys.stderr)
            failed.append(label)

    try:
        # 页面生成是旁路任务，不计入原有关键页计数，也不应阻断每日更新。
        from scripts.services.watchlist_monitor import write_app as write_watchlist_monitor_app
        write_watchlist_monitor_app()
        print("✓ 观察池收益监控")
    except Exception as exc:
        print(f"! 观察池收益监控页面刷新失败（非关键）: {exc}")

    try:
        from scripts.reports.gen_sector_atlas import generate as generate_sector_atlas
        generate_sector_atlas()
        print("✓ 板块图谱")
    except Exception as exc:
        print(f"! 板块图谱刷新失败（非关键）: {exc}")

    try:
        _refresh_daily_ops()
        print("✓ 每日操盘清单")
    except Exception as exc:
        print(f"✗ 每日操盘清单与每日精选历史刷新失败: {exc}", file=sys.stderr)
        failed.append("每日操盘清单/每日精选历史")

    if failed:
        print(f"✗ 关键交互页刷新失败: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("✓ 交互页面与日常清单已刷新")
    return 0


if __name__ == "__main__":
    sys.exit(main())
