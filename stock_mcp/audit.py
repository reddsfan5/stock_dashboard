"""对正式缓存执行 stock-data MCP 只读完整性审计。

用法：``python -m stock_mcp.audit``。脚本不写审计文件，只在内存中比较调用前后的
SHA-256、大小和纳秒修改时间。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from stock_mcp.research_service import ResearchService


PROJECT_DIR = Path(__file__).resolve().parents[1]
AUDITED_FILES = (
    "cache/stock_kline_cache.parquet",
    "cache/etf_kline_cache.parquet",
    "cache/index_kline_cache.parquet",
    "cache/minute_kline_cache.parquet",
    "cache/index_minute_cache.parquet",
    "cache/daily_basic_cache.parquet",
    "cache/stock_info.parquet",
    "state/market_news.sqlite3",
    "state/stock_journal.sqlite3",
    "state/watchlist.sqlite3",
    "state/training_sessions.sqlite3",
    "state/hypotheses.sqlite3",
)


def fingerprint(path: Path) -> tuple:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    stat = path.stat()
    return digest.hexdigest(), stat.st_size, stat.st_mtime_ns


def snapshot() -> dict[str, tuple]:
    return {
        relative: fingerprint(PROJECT_DIR / relative)
        for relative in AUDITED_FILES
        if (PROJECT_DIR / relative).exists()
    }


def call_all_tools() -> None:
    service = ResearchService()
    service.get_data_status()
    service.search_symbols("520500")
    service.get_catalog("all")
    service.get_daily_bars("sh600519", end="2026-08-25", limit=5)
    service.get_minute_bars("etf:sh520500", "2026-08-25", as_of="10:15")
    service.get_feature_snapshot(["sh600519", "etf:sh520500"], as_of="2026-08-25")
    service.run_screener(
        "continuity", as_of="2026-08-25", universe="etf", limit=3
    )
    service.get_market_context("2026-08-25", as_of="10:15")
    service.get_market_news("2026-08-25", as_of="10:15")
    service.get_symbol_context("sh600519")
    service.get_research_artifacts(limit=3)


def main() -> int:
    before = snapshot()
    call_all_tools()
    after = snapshot()
    changed = [name for name in before if before[name] != after.get(name)]
    if changed:
        print("只读审计失败，以下文件发生变化：")
        for name in changed:
            print(f"- {name}")
        return 1
    print(f"只读审计通过：11 个工具未修改 {len(before)} 个正式缓存/SQLite 文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
