#!/usr/bin/env python3
"""每日行情数据库统一更新入口。

阶段：股票日线 → ETF 日线 → 指数日线 → 分钟线 → 字段补齐 → 质量校验 → 行情报告。
详细运行状态写入 ``cache/daily_update_status.json``。

用法
----
python -m scripts.update_cache
python -m scripts.update_cache --validate-only
python -m scripts.update_cache --only minute,enrich,validate
python -m scripts.update_cache --limit 5 --only stocks,etfs,index,minute,enrich
"""

import argparse
from contextlib import contextmanager
import fcntl
import logging
import os
import sys


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from pipeline.daily_update import DailyUpdatePipeline, STAGES


LOG_DIR = os.path.expanduser("~/Library/Logs")
LOG_FILE = os.path.join(LOG_DIR, "stock_cache_update.log")
LOCK_FILE = os.path.join(PROJECT_DIR, "cache", ".daily_update.lock")

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


@contextmanager
def single_instance_lock():
    """阻止手工命令与 launchd 同时写同一批 Parquet。"""
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    with open(LOCK_FILE, "w", encoding="utf-8") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("另一条每日更新任务正在运行")
        stream.write(str(os.getpid()))
        stream.flush()
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def parse_args():
    parser = argparse.ArgumentParser(description="每日行情数据库分阶段更新")
    parser.add_argument(
        "--source", choices=["akshare", "baostock", "auto"], default="auto",
        help="股票历史修复数据源（默认主源失败自动回退）",
    )
    parser.add_argument("--limit", type=int, default=None,
                        help="每类只处理前 N 只，用于小范围验证")
    parser.add_argument(
        "--only", default=None,
        help=f"只运行指定阶段，逗号分隔：{','.join(STAGES)}",
    )
    parser.add_argument("--validate-only", action="store_true",
                        help="不联网更新，只检查各缓存的新鲜度和覆盖率")
    parser.add_argument("--target-date", default=None,
                        help="显式指定目标交易日 YYYY-MM-DD（测试/补跑用）")
    parser.add_argument("--minute-threads", type=int, default=8,
                        help="分钟接口并发数（默认 8）")
    parser.add_argument("--minute-checkpoint", type=int, default=1000,
                        help="分钟线每完成 N 只原子落盘一次（默认 1000）")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.validate_only and args.only:
        raise SystemExit("--validate-only 不能与 --only 同时使用")
    only = [part.strip() for part in args.only.split(",") if part.strip()] \
        if args.only else None
    if args.validate_only:
        only = ["validate"]

    logger.info("=" * 64)
    logger.info("每日更新开始 source=%s stages=%s limit=%s",
                args.source, only or "all", args.limit or "all")
    try:
        with single_instance_lock():
            pipeline = DailyUpdatePipeline(
                source=args.source,
                limit=args.limit,
                only=only,
                minute_threads=args.minute_threads,
                minute_checkpoint=args.minute_checkpoint,
                target_date=args.target_date,
                logger=logger,
            )
            ok = pipeline.run()
    except Exception as exc:
        logger.exception("每日更新启动失败: %s", exc)
        return 1

    logger.info("每日更新%s，目标交易日 %s",
                "成功" if ok else "失败",
                pipeline.target_date.date() if pipeline.target_date is not None else "未知")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
