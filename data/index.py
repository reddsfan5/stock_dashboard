"""
指数日K线数据层 — 独立缓存 cache/index_kline_cache.parquet

大盘指数：上证 sh000001 / 深成指 sz399001 / 沪深300 sh000300 / 创业板指 sz399006 / 科创50 sh000688
数据源：akshare stock_zh_index_daily_tx（腾讯，与股票主源一致），全挂时用 ETF 代理兜底
代码格式：缓存统一带 sh/sz 前缀（与股票缓存一致）

使用示例
--------
$ python data/index.py --update      # 增量更新（scripts/update_cache.py 也调用）
$ python data/index.py --info        # 缓存覆盖 + 单位自检

数据格式（日期/开/高/低/收/成交量(手)）与 StockData 兼容（指数无成交额）。
成交量列说明：腾讯 fqkline 第 6 列为成交量(手)，akshare 命名为 amount 属误标
（已实测：上证指数 5.7e8 手≈沪市日成交量，与两市成交额差 ~1e3 倍且非固定比例）。
来源列：'ak'=指数真值, 'etf'=ETF代理兜底（点位≠真指数，页面需标注）。
"""

import os
import sys
import time
from typing import Optional

import akshare as ak
import numpy as np
import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from data.storage import atomic_write_parquet

CACHE_FILE = os.path.join(PROJECT_DIR, "cache", "index_kline_cache.parquet")

COLUMNS = ["日期", "开盘", "最高", "最低", "收盘", "成交量(手)"]

# 指数代码（缓存格式）→ 名称
INDEXES = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sh000300": "沪深300",
    "sz399006": "创业板指",
    "sh000688": "科创50",
}

# ETF 兜底映射: 指数代码 → 代理 ETF（ETF 缓存为纯数字格式）
ETF_FALLBACK = {"sh000001": "510050", "sz399001": "159901", "sh000300": "510300", "sz399006": "159915", "sh000688": "588000"}

FULL_START = "20100101"  # 首次拉取起点（与股票缓存对齐）


class IndexData:
    """大盘指数数据管理器"""

    def __init__(self):
        self.last_failed: list = []  # 本次 update 双源都失败的指数代码

    # ========== 缓存 ==========

    @property
    def cache(self) -> pd.DataFrame:
        if os.path.exists(CACHE_FILE):
            df = pd.read_parquet(CACHE_FILE)
            df["日期"] = pd.to_datetime(df["日期"])
            return df
        return pd.DataFrame(columns=["代码"] + COLUMNS + ["来源"])

    def _save(self, df: pd.DataFrame):
        atomic_write_parquet(df, CACHE_FILE)

    # ========== 更新 ==========

    def _fetch_ak(self, code: str, start: str, end: str) -> Optional[pd.DataFrame]:
        """
        腾讯指数日K（stock_zh_index_daily_tx，与股票主源一致），3 次重试。
        支持 start/end 参数（按年分页），增量更新快；失败返回 None。
        """
        for attempt in range(3):
            try:
                df = ak.stock_zh_index_daily_tx(symbol=code, start_date=start, end_date=end)
                if df is None or len(df) == 0:
                    return None
                df = df.rename(columns={
                    "date": "日期", "open": "开盘", "close": "收盘",
                    "high": "最高", "low": "最低", "amount": "成交量(手)",
                })
                df["日期"] = pd.to_datetime(df["日期"])
                df = df[df["日期"] >= pd.Timestamp(start)]
                df = df[COLUMNS].copy()
                df["代码"] = code
                df["来源"] = "ak"
                for c in COLUMNS[1:]:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
                return df
            except Exception:
                if attempt < 2:
                    time.sleep(2)
        return None

    def _fetch_etf_fallback(self, code: str, since: pd.Timestamp) -> Optional[pd.DataFrame]:
        """ETF 代理兜底：取 ETF 缓存中指数最新日之后的行，代码换成指数代码（无成交量列，置 NaN）"""
        from data.etf import ETFData
        etf_code = ETF_FALLBACK.get(code)
        if not etf_code or not os.path.exists(os.path.join(PROJECT_DIR, "cache", "etf_kline_cache.parquet")):
            return None
        df = ETFData().cache
        df = df[(df["代码"] == etf_code) & (df["日期"] > since)]
        if len(df) == 0:
            return None
        df = df[["日期", "开盘", "最高", "最低", "收盘"]].copy()
        df["成交量(手)"] = np.nan
        df["代码"] = code
        df["来源"] = "etf"
        return df

    def update(self, start_date: str = FULL_START, progress: bool = True,
               target_date=None) -> "IndexData":
        """
        增量更新宽基指数。akshare 失败且缓存落后 ≥2 天时走 ETF 兜底。
        双源全挂只记 last_failed + warning，不抛异常（定时链不因指数中断）。
        """
        self.last_failed = []
        cache = self.cache
        if len(cache) > 0:
            latest_map = cache.groupby("代码")["日期"].max()
        else:
            latest_map = pd.Series(dtype="datetime64[ns]")

        target = (pd.Timestamp(target_date).normalize() if target_date is not None
                  else pd.Timestamp.today().normalize())
        end = target.strftime("%Y%m%d")

        new_data = []
        for code in INDEXES:
            latest = latest_map.get(code)
            fetch_start = (latest + pd.Timedelta(days=1)).strftime("%Y%m%d") \
                if latest is not None else start_date
            if latest is not None and latest >= target:
                continue
            df = self._fetch_ak(code, fetch_start, end)
            # ETF 兜底：akshare 失败且指数缓存落后 ≥2 天（防止用 2024 起的老 ETF 数据覆盖新鲜指数）
            if df is None and (latest is None or latest < target - pd.Timedelta(days=2)):
                if progress:
                    print(f"  ⚠ {code} akshare 失败, 尝试 ETF 兜底 {ETF_FALLBACK.get(code, '')}")
                df = self._fetch_etf_fallback(code, latest if latest is not None else pd.Timestamp(start_date))
            if df is None:
                self.last_failed.append(code)
                print(f"  ✗ {code} 双源失败, 保持旧缓存")
                continue
            new_data.append(df)

        if not new_data:
            if progress:
                print("✓ 指数缓存已是最新")
            return self

        ndf = pd.concat(new_data, ignore_index=True)
        if len(cache) > 0:
            cache = pd.concat([cache, ndf], ignore_index=True)
        else:
            cache = ndf  # 空缓存直接落新数据，避免 all-NA 列 concat 告警
        cache = cache.drop_duplicates(subset=["代码", "日期"], keep="last")
        cache = cache.sort_values(["代码", "日期"]).reset_index(drop=True)
        self._save(cache)
        if progress:
            src = ndf["来源"].value_counts().to_dict()
            print(f"✓ 指数更新: 新增 {len(ndf)} 条, 缓存共 {len(cache)} 条, 来源分布 {src}")
        return self


# ====================================================================
# CLI
# ====================================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="指数数据管理")
    parser.add_argument("--update", action="store_true", help="增量更新")
    parser.add_argument("--info", action="store_true", help="查看缓存覆盖")
    parser.add_argument("--start", type=str, default=FULL_START, help="首次拉取起点")
    args = parser.parse_args()

    idx = IndexData()

    if args.info:
        cache = idx.cache
        print(f"缓存: {len(cache)} 条, {cache['代码'].nunique()} 个指数")
        if len(cache) > 0:
            g = cache.groupby("代码").agg(
                行数=("日期", "size"), 起始=("日期", "min"), 截止=("日期", "max"),
                最新收盘=("收盘", "last"), 来源=("来源", lambda s: s.value_counts().to_dict()),
            )
            print(g.to_string())

    if args.update or not args.info:
        idx.update(start_date=args.start)
