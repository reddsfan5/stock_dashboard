"""A 股 / 港股指数分钟线缓存 — cache/index_minute_cache.parquet

覆盖训练页市场情境所需的少数宽基指数，与股票分钟缓存分离。
数据源：腾讯五日分时（web.ifzq.gtimg.cn），与 data/minute.py 主源一致。
接口只返回最近约 5 个交易日，靠每日增量积累；本地保留约一年（KEEP_DAYS=370）。

港股恒生（hkHSI）共用同一缓存与解析器；美股/韩国分钟源不可用，仍走日线。
"""

from __future__ import annotations

import os
import sys
import time
from typing import List, Optional, Sequence, Tuple

import pandas as pd
import requests

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from data.index import INDEXES
from data.storage import atomic_write_parquet

CACHE_FILE = os.path.join(PROJECT_DIR, "cache", "index_minute_cache.parquet")
TENCENT_DAY_URL = "https://web.ifzq.gtimg.cn/appstock/app/day/query"
KEEP_DAYS = 370
DELAY = 0.05
COLUMNS = ["代码", "时间", "开盘", "最高", "最低", "收盘", "成交量", "成交额"]

# 腾讯 code → 会话时钟过滤（字符串 HHMM 比较）
A_SHARE_CLOCK = ("0931", "1130", "1301", "1500")
HK_CLOCK = ("0930", "1200", "1300", "1600")
HK_MINUTE_CODES = ("hkHSI",)


def _clock_allowed(clock: str, code: str) -> bool:
    if code.startswith("hk"):
        lo1, hi1, lo2, hi2 = HK_CLOCK
    else:
        lo1, hi1, lo2, hi2 = A_SHARE_CLOCK
    return lo1 <= clock <= hi1 or lo2 <= clock <= hi2


def align_trainer_dates(
    stock_dates: Sequence[str],
    index_dates: Sequence[str],
) -> Tuple[List[str], bool, Optional[str]]:
    """训练可选日以股票完整分时为准（放宽对齐）。

    不再与指数分钟求交；若部分/全部训练日缺少指数分钟，仅附带 warning，
    市场情境在那些日子可能回退为开盘价/日线。
    """
    stock = [str(d) for d in stock_dates]
    index = [str(d) for d in index_dates]
    if not stock:
        return [], False, None
    if not index:
        return stock, True, "index_minute_empty"
    index_set = set(index)
    covered = [d for d in stock if d in index_set]
    if not covered:
        return stock, True, "index_minute_no_overlap"
    if len(covered) < len(stock):
        return stock, True, "index_minute_partial"
    return stock, False, None


class IndexMinuteData:
    """宽基指数分钟线管理器（独立于股票分钟缓存）。"""

    def __init__(self, codes: Optional[List[str]] = None):
        if codes is None:
            self.codes = list(INDEXES.keys()) + list(HK_MINUTE_CODES)
        else:
            self.codes = list(codes)
        self.last_failed: List[str] = []
        self.last_new_rows = 0

    @property
    def a_share_codes(self) -> List[str]:
        return [c for c in self.codes if not str(c).startswith("hk")]

    @property
    def cache(self) -> pd.DataFrame:
        if os.path.exists(CACHE_FILE):
            df = pd.read_parquet(CACHE_FILE)
            df["时间"] = pd.to_datetime(df["时间"])
            return df
        return pd.DataFrame(columns=COLUMNS)

    def _save(self, df: pd.DataFrame) -> None:
        atomic_write_parquet(df, CACHE_FILE)

    def available_dates(self, codes: Optional[Sequence[str]] = None) -> List[str]:
        """返回指定代码（默认 A 股宽基）已有分钟的交易日列表。"""
        cache = self.cache
        if cache.empty:
            return []
        wanted = list(codes) if codes is not None else self.a_share_codes
        if not wanted:
            return []
        frame = cache[cache["代码"].isin(wanted)]
        if frame.empty:
            return []
        return sorted(frame["时间"].dt.strftime("%Y-%m-%d").unique().tolist())

    def _fetch_tencent(self, code: str, after=None,
                       target=None) -> Optional[pd.DataFrame]:
        for attempt in range(3):
            try:
                time.sleep(DELAY)
                response = requests.get(
                    TENCENT_DAY_URL, params={"code": code}, timeout=(5, 15)
                )
                response.raise_for_status()
                item = response.json().get("data", {}).get(code, {})
                days = item.get("data") or []
                if not days:
                    return pd.DataFrame(columns=COLUMNS)

                rows = []
                for day in days:
                    date = str(day.get("date", ""))
                    if len(date) != 8:
                        continue
                    day_date = pd.Timestamp(date)
                    if target is not None and day_date > pd.Timestamp(target).normalize():
                        continue
                    if after is not None and day_date < pd.Timestamp(after).normalize():
                        continue
                    previous_volume = 0.0
                    previous_amount = 0.0
                    for value in day.get("data") or []:
                        parts = str(value).split()
                        if len(parts) < 4:
                            continue
                        clock = parts[0]
                        if not _clock_allowed(clock, code):
                            continue
                        price = float(parts[1])
                        cumulative_volume = float(parts[2])
                        cumulative_amount = float(parts[3])
                        volume = max(cumulative_volume - previous_volume, 0.0)
                        amount = max(cumulative_amount - previous_amount, 0.0)
                        previous_volume = cumulative_volume
                        previous_amount = cumulative_amount
                        rows.append((
                            code,
                            date + clock,
                            price, price, price, price, volume, amount,
                        ))
                if not rows:
                    return pd.DataFrame(columns=COLUMNS)
                frame = pd.DataFrame(rows, columns=COLUMNS)
                frame["时间"] = pd.to_datetime(frame["时间"], format="%Y%m%d%H%M")
                if after is not None:
                    frame = frame[frame["时间"] > pd.Timestamp(after)]
                return frame
            except Exception:
                if attempt < 2:
                    time.sleep(1 + attempt)
        return None

    def update(self, progress: bool = True, target_date=None) -> "IndexMinuteData":
        """增量拉取指数分钟线并裁剪过期数据。"""
        self.last_failed = []
        self.last_new_rows = 0
        cache = self.cache
        target = (pd.Timestamp(target_date).normalize() if target_date is not None
                  else None)
        latest_map = (
            cache.groupby("代码")["时间"].max() if len(cache) else pd.Series(dtype="datetime64[ns]")
        )
        frames = []
        for code in self.codes:
            after = latest_map.get(code)
            # 同一交易日可能尚未收盘，允许覆盖刷新该日
            if after is not None:
                after = pd.Timestamp(after).normalize() - pd.Timedelta(days=1)
            frame = self._fetch_tencent(code, after=after, target=target)
            if frame is None:
                self.last_failed.append(code)
                if progress:
                    print(f"  ✗ 指数分钟 {code} 拉取失败")
                continue
            if len(frame):
                frames.append(frame)

        if frames:
            ndf = pd.concat(frames, ignore_index=True)
            if len(cache):
                # 用新数据覆盖重叠的 代码+时间
                cache = pd.concat([cache, ndf], ignore_index=True)
            else:
                cache = ndf
            cache = cache.drop_duplicates(subset=["代码", "时间"], keep="last")
            cutoff = pd.Timestamp.today().normalize() - pd.Timedelta(days=KEEP_DAYS)
            cache = cache[cache["时间"] >= cutoff]
            cache = cache.sort_values(["代码", "时间"]).reset_index(drop=True)
            self.last_new_rows = len(ndf)
            self._save(cache)
            if progress:
                print(f"✓ 指数分钟更新: 写入约 {len(ndf)} 行, 缓存共 {len(cache)} 行")
        elif progress:
            print("✓ 指数分钟缓存无新增")
        return self

    def points_as_of(self, code: str, market_date: str, as_of: str) -> pd.DataFrame:
        """返回某指数在模拟日不超过 as_of 的分钟点。"""
        cache = self.cache
        if cache.empty:
            return cache
        day = pd.Timestamp(market_date).normalize()
        clock = str(as_of or "15:00").strip()
        if len(clock) == 5:
            clock += ":00"
        cutoff = pd.Timestamp(f"{day.strftime('%Y-%m-%d')} {clock}")
        mask = (
            (cache["代码"] == code)
            & (cache["时间"] >= day)
            & (cache["时间"] < day + pd.Timedelta(days=1))
            & (cache["时间"] <= cutoff)
        )
        return cache.loc[mask].sort_values("时间")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="A股/港股指数分钟线管理")
    parser.add_argument("--update", action="store_true", help="增量更新")
    parser.add_argument("--info", action="store_true", help="查看缓存")
    args = parser.parse_args()
    data = IndexMinuteData()
    if args.info:
        cache = data.cache
        print(f"缓存: {len(cache)} 条, {cache['代码'].nunique() if len(cache) else 0} 个指数")
        if len(cache):
            print(cache.groupby("代码")["时间"].agg(["count", "min", "max"]).to_string())
    if args.update or not args.info:
        data.update()
