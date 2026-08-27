"""
分时（1分钟）数据层 — 独立缓存 cache/minute_kline_cache.parquet

数据源：新浪 stock_zh_a_minute（东财被本机代理阻断，不可用）
覆盖：接口只返回最近 ~9 个交易日——无法回补历史，靠每日收盘后增量
      "养数据"积累近两个月（KEEP_DAYS=65 自然日 ≈ 44 交易日）

数据格式
--------
代码(带sh/sz前缀) / 时间(datetime) / 开盘 / 最高 / 最低 / 收盘 /
成交量(股) / 成交额(元)

用法
----
$ python data/minute.py --update                # 全量增量（scripts/update_cache.py 也调用）
$ python data/minute.py --codes sh600519,sh510050  # 只抓指定（测试）
$ python data/minute.py --stats                 # 缓存覆盖统计

查询
----
>>> from data.minute import MinuteData
>>> m = MinuteData()
>>> m.get_minute("sh600519", "2026-08-25")   # 单标的单日分时
>>> m.dates_of("sh600519")[-5:]              # 最近 5 个有分时的日期
"""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

import pandas as pd

import akshare as ak

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

CACHE_FILE = os.path.join(PROJECT_DIR, "cache", "minute_kline_cache.parquet")
KEEP_DAYS = 65          # 保留自然日（近两个月 ≈ 44 交易日 + 缓冲）
THREADS = 8
DELAY = 0.05            # 请求间隔（秒）——新浪对高频请求限流，太密会大量失败
COLUMNS = ["代码", "时间", "开盘", "最高", "最低", "收盘", "成交量", "成交额"]


class MinuteData:
    """分时数据管理器"""

    def __init__(self):
        self.last_failed: List[str] = []

    # ========== 缓存 ==========

    @property
    def cache(self) -> pd.DataFrame:
        if os.path.exists(CACHE_FILE):
            df = pd.read_parquet(CACHE_FILE)
            df["时间"] = pd.to_datetime(df["时间"])
            return df
        return pd.DataFrame(columns=COLUMNS)

    def _save(self, df: pd.DataFrame):
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        df.to_parquet(CACHE_FILE, index=False)

    # ========== 更新 ==========

    def _fetch(self, code: str) -> Optional[pd.DataFrame]:
        """新浪 1 分钟线（返回最近 ~9 个交易日），3 次重试"""
        for attempt in range(3):
            try:
                time.sleep(DELAY)
                df = ak.stock_zh_a_minute(symbol=code, period="1", adjust="")
                if df is None or len(df) == 0:
                    return None
                df = df.rename(columns={
                    "day": "时间", "open": "开盘", "high": "最高", "low": "最低",
                    "close": "收盘", "volume": "成交量", "amount": "成交额",
                })
                df["代码"] = code
                df["时间"] = pd.to_datetime(df["时间"])
                df = df[COLUMNS].copy()
                for c in ["开盘", "最高", "最低", "收盘", "成交量", "成交额"]:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
                return df
            except Exception:
                if attempt < 2:
                    time.sleep(2)
        return None

    def update(self, codes: List[str] = None, progress: bool = True,
               threads: int = THREADS) -> "MinuteData":
        """
        增量更新分时缓存（覆盖式：接口返回最近 ~9 日，按 代码+时间 去重合并）。

        Args:
            codes: 带前缀代码列表（sh600519 / sh510050），None=全部（股票+ETF）
        """
        self.last_failed = []
        if codes is None:
            from data.kline import StockData
            from data.etf import ETFData
            codes = (list(StockData().get_stock_list(board="all")["代码"])
                     + list(ETFData().cache_with_prefix["代码"].unique()))
            codes = list(dict.fromkeys(codes))  # 去重保序

        cache = self.cache
        new_data = []
        with ThreadPoolExecutor(max_workers=threads) as executor:
            tasks = {executor.submit(self._fetch, c): c for c in codes}
            it = as_completed(tasks)
            if progress:
                from tqdm import tqdm
                it = tqdm(it, total=len(tasks), desc="分时")
            for future in it:
                df = future.result()
                if df is not None and len(df) > 0:
                    new_data.append(df)
                elif df is None:
                    self.last_failed.append(tasks[future])

        if new_data:
            ndf = pd.concat(new_data, ignore_index=True)
            if len(cache) > 0:
                cache = pd.concat([cache, ndf], ignore_index=True)
            else:
                cache = ndf
            cache = cache.drop_duplicates(subset=["代码", "时间"], keep="last")
            # 清理过期：只保留近 KEEP_DAYS 自然日
            cutoff = pd.Timestamp.today().normalize() - pd.Timedelta(days=KEEP_DAYS)
            cache = cache[cache["时间"] >= cutoff]
            cache = cache.sort_values(["代码", "时间"]).reset_index(drop=True)
            self._save(cache)
            if progress:
                print(f"✓ 分时更新: 新增 {len(ndf)} 条, 缓存共 {len(cache):,} 条, "
                      f"{cache['代码'].nunique()} 只")
        else:
            if progress:
                print("✗ 分时无新数据（网络可能异常）")
        return self

    # ========== 查询 ==========

    def dates_of(self, code: str) -> List[str]:
        """某标的缓存中有分时的日期列表（升序）"""
        df = pd.read_parquet(CACHE_FILE, columns=["代码", "时间"],
                             filters=[("代码", "==", code)])
        return sorted(df["时间"].dt.date.astype(str).unique())

    def get_minute(self, code: str, date: str = None) -> pd.DataFrame:
        """单标的单日分时（date=None 取最近一个有数据的交易日）"""
        if not os.path.exists(CACHE_FILE):
            return pd.DataFrame(columns=COLUMNS)
        df = pd.read_parquet(CACHE_FILE, filters=[("代码", "==", code)])
        df["时间"] = pd.to_datetime(df["时间"])
        days = df["时间"].dt.date.unique()
        if len(days) == 0:
            return pd.DataFrame(columns=COLUMNS)
        target = pd.Timestamp(date).date() if date else max(days)
        return df[df["时间"].dt.date == target].sort_values("时间").reset_index(drop=True)

    def stats(self) -> dict:
        if not os.path.exists(CACHE_FILE):
            return {"cached": 0, "records": 0}
        df = pd.read_parquet(CACHE_FILE, columns=["代码", "时间"])
        return {"cached": df["代码"].nunique(), "records": len(df),
                "start": str(df["时间"].min()), "end": str(df["时间"].max())}


# ====================================================================
# CLI
# ====================================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="分时数据管理")
    parser.add_argument("--update", action="store_true", default=True)
    parser.add_argument("--codes", type=str, default=None, help="逗号分隔（测试）")
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()

    m = MinuteData()
    if args.stats:
        print(m.stats())
    if args.update:
        codes = args.codes.split(",") if args.codes else None
        m.update(codes=codes)
        if m.last_failed:
            print(f"失败 {len(m.last_failed)} 只: {' '.join(m.last_failed[:10])}")
