#!/usr/bin/env python3
"""
每日缓存定时更新 — 由 launchd 每个工作日 18:30 调用

职责：拉取当日增量数据，写入 stock_kline_cache.parquet
日志：~/Library/Logs/stock_cache_update.log
"""

import sys
import os
import time
import logging
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


def main():
    start = datetime.now()
    logger.info("=" * 50)
    logger.info("开始缓存更新")

    try:
        data = StockData()
        # 腾讯行情接口偶尔 SSL 抖动，列表拉取重试 3 次
        stocks = None
        for attempt in range(3):
            try:
                stocks = data.get_stock_list(board="all")
                break
            except Exception:
                if attempt < 2:
                    logger.warning("股票列表拉取失败, 30 秒后重试 (%d/3)", attempt + 1)
                    time.sleep(30)
        if stocks is None:
            raise RuntimeError("股票列表连续 3 次拉取失败")
        data.update(stocks, progress=False)  # launchd 环境不需要 tqdm

        elapsed = (datetime.now() - start).total_seconds()
        logger.info(
            "更新完成 — %d 只股票, %d 条记录, 耗时 %.0f 秒",
            data.stock_count, len(data.cache), elapsed,
        )
    except Exception as e:
        logger.error("更新失败: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
