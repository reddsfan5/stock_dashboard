"""海外主要指数日线缓存 — cache/global_markets_cache.parquet

与 A 股指数 / 股票 K 线缓存分离。数据源：东方财富
主源 ``index_global_hist_em``；港股可回退 ``stock_hk_index_daily_em``，美股回退 ``index_us_stock_sina``（.IXIC/.DJI/.INX），韩国回退 ``index_global_hist_sina(首尔综合指数)``。

时区说明（训练页模拟时钟始终为 Asia/Shanghai）：
- 港股恒生：Asia/Hong_Kong（与上海同区，无夏令时差异）
- 美股纳指/道指/标普：America/New_York（相对中国隔夜）
- 韩国 KOSPI：Asia/Seoul（UTC+9，比上海早 1 小时）

v1 仅日线：盘中不会伪造同步分钟；状态卡标明 open/closed/overnight/not_yet_open。
"""

from __future__ import annotations

import os
import sys
import time
from typing import Dict, List, Optional

import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from data.storage import atomic_write_parquet

CACHE_FILE = os.path.join(PROJECT_DIR, "cache", "global_markets_cache.parquet")
COLUMNS = ["代码", "名称", "区域", "日期", "开盘", "最高", "最低", "收盘"]

# symbol 为东财/akshare index_global_hist_em 的中文名称
OVERSEAS_INDEXES: Dict[str, dict] = {
    "HSI": {
        "name": "恒生指数",
        "symbol": "恒生指数",
        "region": "HK",
        "region_label": "港股",
        "timezone": "Asia/Hong_Kong",
        "session_open": "09:30",
        "session_close": "16:00",
        "sina_us": None,
        "hk_em": "HSI",
        "sina_global": None,
    },
    "IXIC": {
        "name": "纳斯达克综指",
        "symbol": "纳斯达克",
        "region": "US",
        "region_label": "美股",
        "timezone": "America/New_York",
        "session_open": "09:30",
        "session_close": "16:00",
        "sina_us": ".IXIC",
        "hk_em": None,
        "sina_global": None,
    },
    "DJIA": {
        "name": "道琼斯",
        "symbol": "道琼斯",
        "region": "US",
        "region_label": "美股",
        "timezone": "America/New_York",
        "session_open": "09:30",
        "session_close": "16:00",
        "sina_us": ".DJI",
        "hk_em": None,
        "sina_global": None,
    },
    "SPX": {
        "name": "标普500",
        "symbol": "标普500",
        "region": "US",
        "region_label": "美股",
        "timezone": "America/New_York",
        "session_open": "09:30",
        "session_close": "16:00",
        "sina_us": ".INX",
        "hk_em": None,
        "sina_global": None,
    },
    "KS11": {
        "name": "韩国KOSPI",
        "symbol": "韩国KOSPI",
        "region": "KR",
        "region_label": "韩国",
        "timezone": "Asia/Seoul",
        "session_open": "09:00",
        "session_close": "15:30",
        "sina_us": None,
        "hk_em": None,
        "sina_global": "首尔综合指数",
    },
}


class GlobalMarketsData:
    """海外指数日线管理器。"""

    def __init__(self, indexes: Optional[Dict[str, dict]] = None):
        self.indexes = indexes or OVERSEAS_INDEXES
        self.last_failed: List[str] = []
        self.last_new_rows = 0

    @property
    def cache(self) -> pd.DataFrame:
        if os.path.exists(CACHE_FILE):
            df = pd.read_parquet(CACHE_FILE)
            df["日期"] = pd.to_datetime(df["日期"])
            return df
        return pd.DataFrame(columns=COLUMNS)

    def _save(self, df: pd.DataFrame) -> None:
        atomic_write_parquet(df, CACHE_FILE)

    def _normalize(self, df: pd.DataFrame, code: str, meta: dict,
                     mapping: dict) -> Optional[pd.DataFrame]:
        if df is None or len(df) == 0:
            return None
        out = df.rename(columns=mapping).copy()
        out["日期"] = pd.to_datetime(out["日期"])
        out["代码"] = code
        out["名称"] = meta["name"]
        out["区域"] = meta["region"]
        for col in ("开盘", "最高", "最低", "收盘"):
            out[col] = pd.to_numeric(out[col], errors="coerce")
        return out[COLUMNS].dropna(subset=["日期", "收盘"])

    def _fetch_one(self, code: str, meta: dict) -> Optional[pd.DataFrame]:
        """东财全球指数为主源；失败时按市场回退港股东财 / 新浪美股 / 新浪全球。"""
        import akshare as ak

        attempts = []
        attempts.append(("em_global", lambda: ak.index_global_hist_em(symbol=meta["symbol"])))
        if meta.get("hk_em"):
            attempts.append((
                "hk_em",
                lambda: ak.stock_hk_index_daily_em(symbol=meta["hk_em"]),
            ))
        if meta.get("sina_us"):
            attempts.append((
                "sina_us",
                lambda: ak.index_us_stock_sina(symbol=meta["sina_us"]),
            ))
        if meta.get("sina_global"):
            attempts.append((
                "sina_global",
                lambda: ak.index_global_hist_sina(symbol=meta["sina_global"]),
            ))

        mappings = {
            "em_global": {
                "日期": "日期", "今开": "开盘", "最高": "最高",
                "最低": "最低", "最新价": "收盘",
            },
            "hk_em": {
                "date": "日期", "open": "开盘", "high": "最高",
                "low": "最低", "latest": "收盘",
            },
            "sina_us": {
                "date": "日期", "open": "开盘", "high": "最高",
                "low": "最低", "close": "收盘",
            },
            "sina_global": {
                "date": "日期", "open": "开盘", "high": "最高",
                "low": "最低", "close": "收盘",
            },
        }

        for source, fetcher in attempts:
            for attempt in range(2):
                try:
                    raw = fetcher()
                    frame = self._normalize(raw, code, meta, mappings[source])
                    if frame is not None and len(frame):
                        return frame
                except Exception:
                    if attempt == 0:
                        time.sleep(1)
        return None

    def update(self, progress: bool = True,
               codes: Optional[List[str]] = None) -> "GlobalMarketsData":
        """拉取并合并海外指数日线（非关键；失败保留旧缓存）。"""
        self.last_failed = []
        self.last_new_rows = 0
        cache = self.cache
        frames = []
        selected = codes or list(self.indexes)
        for code in selected:
            meta = self.indexes.get(code)
            if not meta:
                continue
            df = self._fetch_one(code, meta)
            if df is None:
                self.last_failed.append(code)
                if progress:
                    print(f"  ✗ 海外指数 {code} ({meta['symbol']}) 拉取失败")
                continue
            frames.append(df)
            if progress:
                print(f"  ✓ {code} {meta['name']}: {len(df)} 条")

        if not frames:
            if progress:
                print("✓ 海外指数无新增（或全部失败，保留旧缓存）")
            return self

        ndf = pd.concat(frames, ignore_index=True)
        if len(cache):
            # 仅替换本次成功拉取的代码，避免失败代码被清空
            keep = cache[~cache["代码"].isin(ndf["代码"].unique())]
            cache = pd.concat([keep, ndf], ignore_index=True)
        else:
            cache = ndf
        cache = cache.drop_duplicates(subset=["代码", "日期"], keep="last")
        cache = cache.sort_values(["代码", "日期"]).reset_index(drop=True)
        before = 0 if not os.path.exists(CACHE_FILE) else len(pd.read_parquet(CACHE_FILE))
        self._save(cache)
        self.last_new_rows = max(len(cache) - before, 0)
        if progress:
            print(f"✓ 海外指数缓存共 {len(cache)} 条, 代码 {cache['代码'].nunique()} 个")
        return self


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="海外指数日线管理")
    parser.add_argument("--update", action="store_true", help="增量/全量刷新")
    parser.add_argument("--info", action="store_true", help="查看缓存")
    args = parser.parse_args()
    data = GlobalMarketsData()
    if args.info:
        cache = data.cache
        print(f"缓存: {len(cache)} 条")
        if len(cache):
            print(cache.groupby(["代码", "名称"]).agg(
                行数=("日期", "size"), 起始=("日期", "min"), 截止=("日期", "max"),
                最新收盘=("收盘", "last"),
            ).to_string())
    if args.update or not args.info:
        data.update()
