#!/usr/bin/env python3
"""
每日缓存定时更新 — 由 launchd 每个工作日 18:30 调用

职责：拉取当日增量数据，写入 stock_kline_cache.parquet
数据源：akshare(腾讯) 主源 + baostock 备源（主源失败自动回退）
后续步骤：更新指数缓存（data/index.py）→ 重新生成行情统计页（gen_market.py）
日志：~/Library/Logs/stock_cache_update.log

用法
----
$ python scripts/update_cache.py                    # 自动双源（launchd 默认）
$ python scripts/update_cache.py --source baostock  # 强制 baostock
$ python scripts/update_cache.py --source akshare   # 仅 akshare（无回退）
$ python scripts/update_cache.py --limit 5          # 只更新前 5 只（测试）
"""

import sys
import os
import time
import logging
import argparse
import subprocess
from datetime import datetime, timedelta

# 日志写到用户库
LOG_DIR = os.path.expanduser("~/Library/Logs")
LOG_FILE = os.path.join(LOG_DIR, "stock_cache_update.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ----- 确保项目路径可导入 -----
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from data.kline import StockData
from data.index import IndexData
from data import sources


def get_stock_list_with_fallback(data: StockData, source: str) -> "pd.DataFrame":
    """
    获取股票列表（双源）。

    akshare/auto: 3 次重试（腾讯接口偶尔 SSL 抖动），全挂后 baostock 兜底
    baostock:    直接走 baostock
    双源全挂抛异常 → main 捕获后 exit 1（保持原语义）
    """
    if source == "baostock":
        return sources.bs_get_stock_list(board="all")

    for attempt in range(3):
        try:
            return data.get_stock_list(board="all")
        except Exception:
            if attempt < 2:
                logger.warning("股票列表拉取失败, 30 秒后重试 (%d/3)", attempt + 1)
                time.sleep(30)

    if source == "akshare":
        raise RuntimeError("股票列表连续 3 次拉取失败")
    logger.warning("akshare 股票列表连续 3 次失败, baostock 兜底")
    return sources.bs_get_stock_list(board="all")


def main():
    parser = argparse.ArgumentParser(description="每日缓存更新（akshare 主源 + baostock 备源）")
    parser.add_argument("--source", choices=["akshare", "baostock", "auto"],
                        default="auto", help="数据源（默认 auto=主源失败自动回退）")
    parser.add_argument("--limit", type=int, default=None,
                        help="只更新前 N 只（测试用）")
    args = parser.parse_args()

    start = datetime.now()
    logger.info("=" * 50)
    logger.info("开始缓存更新 (source=%s)", args.source)

    try:
        data = StockData()
        stocks = get_stock_list_with_fallback(data, args.source)
        if args.limit:
            stocks = stocks.head(args.limit)
        total = len(stocks)

        # 第一遍：主源
        if args.source == "baostock":
            with sources.baostock_session():
                data.update(stocks, progress=False,
                            fetch_fn=sources.bs_fetch_kline, threads=1)
            ak_failed, bs_failed = [], list(data.last_failed)
        else:
            data.update(stocks, progress=False)  # akshare 主源
            ak_failed, bs_failed = list(data.last_failed), []

        # 第二遍：baostock 回退（仅重拉 akshare 失败的代码）
        if ak_failed and args.source == "auto":
            logger.warning("akshare 失败 %d 只, baostock 回退", len(ak_failed))
            failed_df = stocks[stocks["代码"].isin(ak_failed)]
            with sources.baostock_session():
                data.update(failed_df, progress=False,
                            fetch_fn=sources.bs_fetch_kline, threads=1)
            bs_failed = list(data.last_failed)

        # 指数行情更新（独立步骤：失败不阻塞主流程，下次任务自愈）
        try:
            idx = IndexData()
            idx.update(progress=False)
            logger.info("指数更新 — %d 条记录, 失败: %s",
                        len(idx.cache), idx.last_failed or "无")
        except Exception:
            logger.warning("指数更新失败, 跳过", exc_info=True)

        # 分时缓存更新（子进程跑，独立内存；接口只给最近~9日，
        # 靠每日积累凑近两个月。失败不阻塞主流程，次日自愈）
        try:
            r = subprocess.run([sys.executable, "data/minute.py", "--update"],
                               cwd=PROJECT_DIR, capture_output=True, text=True,
                               timeout=1800)
            out_lines = [x for x in (r.stdout or "").strip().splitlines() if x]
            if r.returncode == 0:
                logger.info("分时更新完成 — %s", out_lines[-1] if out_lines else "")
            else:
                logger.warning("分时更新失败(退出码 %s): %s",
                               r.returncode, (r.stderr or "").strip()[-300:])
        except Exception:
            logger.warning("分时更新失败, 跳过", exc_info=True)

        # 整体行情统计页刷新（子进程跑，独立内存——本进程已持有全量缓存，
        # 页内聚合再读一份 11.8M 行会内存翻倍被系统杀；失败不阻塞主流程）
        try:
            r = subprocess.run([sys.executable, "scripts/gen_market.py"],
                               cwd=PROJECT_DIR, capture_output=True, text=True,
                               timeout=600)
            out_lines = [x for x in (r.stdout or "").strip().splitlines() if x]
            if r.returncode == 0:
                logger.info("行情统计页刷新完成 — %s", out_lines[-1] if out_lines else "")
            else:
                logger.warning("行情统计页刷新失败(退出码 %s): %s",
                               r.returncode, (r.stderr or "").strip()[-300:])
        except Exception:
            logger.warning("行情统计页刷新失败, 跳过", exc_info=True)

        elapsed = (datetime.now() - start).total_seconds()
        logger.info(
            "更新完成 — %d 只股票, %d 条记录, 耗时 %.0f 秒",
            data.stock_count, len(data.cache), elapsed,
        )
        if ak_failed:
            logger.info("akshare 失败 %d 只 → baostock 回退", len(ak_failed))
        if bs_failed:
            logger.warning(
                "双源后仍失败 %d 只: %s",
                len(bs_failed), " ".join(bs_failed[:20]),
            )
            if len(bs_failed) > total * 0.5:
                # 大部分失败通常是数据源级故障，下次任务自愈即可
                logger.error("超过一半标的双源失败（%d/%d），等下次任务自愈", len(bs_failed), total)
    except Exception as e:
        logger.error("更新失败: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
