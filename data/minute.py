"""
分时（1分钟）数据层 — 独立缓存 cache/minute_kline_cache.parquet

数据源：腾讯五日分时主源 + 新浪分钟 K 线备源（东财被本机代理阻断）
覆盖：接口只返回最近 5~9 个交易日——无法回补更早历史，靠每日收盘后增量
      "养数据"积累近一年（KEEP_DAYS=370 自然日 ≈ 250 交易日 + 缓冲）

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
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

import pandas as pd
import requests

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from data.storage import atomic_write_parquet

CACHE_FILE = os.path.join(PROJECT_DIR, "cache", "minute_kline_cache.parquet")
KEEP_DAYS = 370         # 保留自然日（近一年 ≈ 250 交易日 + 缓冲）
THREADS = 8
DELAY = 0.02            # 腾讯主源请求的轻量节流
CHECKPOINT_CODES = 1000 # 每完成 N 只原子落盘一次；超时后下次可续跑
COMPLETE_TIME = "14:55:00"
COLUMNS = ["代码", "时间", "开盘", "最高", "最低", "收盘", "成交量", "成交额"]
SINA_URL = "https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData"
TENCENT_DAY_URL = "https://web.ifzq.gtimg.cn/appstock/app/day/query"


class MinuteData:
    """分时数据管理器"""

    def __init__(self):
        self.last_failed: List[str] = []
        self.last_no_data: List[str] = []
        self.last_requested = 0
        self.last_updated = 0
        self.last_new_rows = 0

    # ========== 缓存 ==========

    @property
    def cache(self) -> pd.DataFrame:
        if os.path.exists(CACHE_FILE):
            df = pd.read_parquet(CACHE_FILE)
            df["时间"] = pd.to_datetime(df["时间"])
            return df
        return pd.DataFrame(columns=COLUMNS)

    def _save(self, df: pd.DataFrame):
        atomic_write_parquet(df, CACHE_FILE)

    # ========== 更新 ==========

    def _fetch_tencent(self, code: str, after=None,
                       target=None) -> Optional[pd.DataFrame]:
        """腾讯最近五日分时；量额为累计值，转换为每分钟增量。"""
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
                        parts = value.split()
                        if len(parts) < 4:
                            continue
                        clock = parts[0]
                        if not ("0931" <= clock <= "1130" or
                                "1301" <= clock <= "1500"):
                            continue
                        price = float(parts[1])
                        cumulative_volume = float(parts[2])
                        cumulative_amount = float(parts[3])
                        volume = max(cumulative_volume - previous_volume, 0.0) * 100
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

    def _fetch_sina(self, code: str) -> Optional[pd.DataFrame]:
        """新浪最近约九日分钟 K 线备源。"""
        for attempt in range(3):
            try:
                response = requests.get(
                    SINA_URL,
                    params={"symbol": code, "scale": "1", "ma": "no", "datalen": "1970"},
                    timeout=(5, 15),
                )
                response.raise_for_status()
                text = response.text
                start = text.find("=(")
                end = text.rfind(");")
                if start < 0 or end <= start:
                    raise ValueError("新浪分钟响应格式异常")
                payload = json.loads(text[start + 2:end])
                df = pd.DataFrame(payload)
                if df is None or len(df) == 0:
                    return pd.DataFrame(columns=COLUMNS)
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
                    time.sleep(1 + attempt)
        return None

    def _fetch(self, code: str, after=None, target=None) -> Optional[pd.DataFrame]:
        """腾讯主源；仅在网络/响应异常时回退新浪。"""
        frame = self._fetch_tencent(code, after=after, target=target)
        if frame is not None:
            return frame
        return self._fetch_sina(code)

    @staticmethod
    def _prefix_etf(code: str) -> str:
        return ("sh" if str(code).startswith(("5", "56", "58")) else "sz") + str(code)

    def _active_codes(self, target_date=None):
        """从已更新的日线缓存取得目标交易日实际有交易的股票和 ETF。"""
        from data.etf import CACHE_FILE as ETF_CACHE_FILE
        from data.kline import CACHE_FILE as STOCK_CACHE_FILE

        target = pd.Timestamp(target_date).normalize() if target_date else None
        if target is None:
            latest = []
            for path in (STOCK_CACHE_FILE, ETF_CACHE_FILE):
                if os.path.exists(path):
                    dates = pd.read_parquet(path, columns=["日期"])["日期"]
                    if len(dates):
                        latest.append(pd.to_datetime(dates).max().normalize())
            if not latest:
                raise RuntimeError("股票和 ETF 日线缓存均为空，无法确定分钟线目标日期")
            target = max(latest)

        stocks = pd.read_parquet(
            STOCK_CACHE_FILE, columns=["代码", "日期"],
            filters=[("日期", "==", target)],
        ) if os.path.exists(STOCK_CACHE_FILE) else pd.DataFrame(columns=["代码"])
        etfs = pd.read_parquet(
            ETF_CACHE_FILE, columns=["代码", "日期"],
            filters=[("日期", "==", target)],
        ) if os.path.exists(ETF_CACHE_FILE) else pd.DataFrame(columns=["代码"])
        codes = list(stocks["代码"].drop_duplicates())
        codes.extend(self._prefix_etf(code) for code in etfs["代码"].drop_duplicates())
        return list(dict.fromkeys(codes)), target

    def update(self, codes: List[str] = None, progress: bool = True,
               threads: int = THREADS, target_date=None,
               checkpoint_codes: int = CHECKPOINT_CODES) -> "MinuteData":
        """
        增量更新分时缓存（覆盖式：接口返回最近 ~9 日，按 代码+时间 去重合并）。

        Args:
            codes: 带前缀代码列表（sh600519 / sh510050）；None=目标日实际交易标的
            target_date: 目标交易日；None=股票/ETF 日线缓存的最新日期
            checkpoint_codes: 每完成多少只原子落盘一次
        """
        self.last_failed = []
        self.last_no_data = []
        self.last_requested = 0
        self.last_updated = 0
        self.last_new_rows = 0
        if codes is None:
            codes, target = self._active_codes(target_date)
        else:
            codes = list(dict.fromkeys(codes))
            target = pd.Timestamp(target_date).normalize() if target_date else None

        cache = self.cache
        cutoff = pd.Timestamp.today().normalize() - pd.Timedelta(days=KEEP_DAYS)
        original_rows = len(cache)
        if len(cache):
            cache = cache[cache["时间"] >= cutoff].reset_index(drop=True)
            latest_map = cache.groupby("代码")["时间"].max()
        else:
            latest_map = pd.Series(dtype="datetime64[ns]")

        if target is not None:
            complete_at = target + pd.Timedelta(COMPLETE_TIME)
            codes = [code for code in codes
                     if code not in latest_map.index or latest_map[code] < complete_at]
        codes.sort(key=lambda code: latest_map.get(code, pd.Timestamp.min))
        self.last_requested = len(codes)
        if not codes:
            if len(cache) != original_rows:
                self._save(cache)
            if progress:
                print(f"✓ 分时缓存已覆盖 {target.date() if target is not None else '目标日'}")
            return self

        new_data = []
        completed = 0

        def _checkpoint():
            nonlocal cache
            if not new_data:
                return
            ndf = pd.concat(new_data, ignore_index=True)
            ndf = ndf.drop_duplicates(subset=["代码", "时间"], keep="last")
            cache = pd.concat([cache, ndf], ignore_index=True)
            self.last_new_rows += len(ndf)
            self._save(cache)
            new_data.clear()

        with ThreadPoolExecutor(max_workers=threads) as executor:
            tasks = {
                executor.submit(self._fetch, c, latest_map.get(c), target): c
                for c in codes
            }
            it = as_completed(tasks)
            if progress:
                from tqdm import tqdm
                it = tqdm(it, total=len(tasks), desc="分时")
            for future in it:
                code = tasks[future]
                df = future.result()
                if df is not None and len(df) > 0:
                    if code in latest_map.index:
                        df = df[df["时间"] > latest_map[code]]
                    else:
                        df = df[df["时间"] >= cutoff]
                    if target is not None:
                        df = df[df["时间"] < target + pd.Timedelta(days=1)]
                    if len(df):
                        new_data.append(df)
                        self.last_updated += 1
                elif df is not None:
                    self.last_no_data.append(code)
                elif df is None:
                    self.last_failed.append(code)
                completed += 1
                if checkpoint_codes > 0 and completed % checkpoint_codes == 0:
                    _checkpoint()
                    if progress:
                        print(f"  checkpoint {completed}/{len(codes)}，新增 {self.last_new_rows:,} 条")

        _checkpoint()
        if self.last_new_rows:
            if progress:
                print(f"✓ 分时更新: 请求 {self.last_requested} 只, "
                      f"更新 {self.last_updated} 只, 新增 {self.last_new_rows:,} 条, "
                      f"缓存共 {len(cache):,} 条")
        else:
            if len(cache) != original_rows:
                self._save(cache)
            if progress:
                print("⚠ 分时无新增数据")
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

    def get_history(self, code: str, end_date: str = None, days: int = None) -> pd.DataFrame:
        """读取单标的分钟历史，可限制截止日和最近交易日数。"""
        if not os.path.exists(CACHE_FILE):
            return pd.DataFrame(columns=COLUMNS)
        df = pd.read_parquet(CACHE_FILE, filters=[("代码", "==", code)])
        df["时间"] = pd.to_datetime(df["时间"])
        if end_date is not None:
            end = pd.Timestamp(end_date).normalize() + pd.Timedelta(days=1)
            df = df[df["时间"] < end]
        if days is not None and len(df):
            dates = sorted(df["时间"].dt.normalize().unique())[-days:]
            df = df[df["时间"].dt.normalize().isin(dates)]
        return df.sort_values("时间").reset_index(drop=True)

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
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--codes", type=str, default=None, help="逗号分隔（测试）")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--target-date", type=str, default=None, help="目标交易日 YYYY-MM-DD")
    parser.add_argument("--threads", type=int, default=THREADS)
    parser.add_argument("--checkpoint", type=int, default=CHECKPOINT_CODES)
    args = parser.parse_args()

    m = MinuteData()
    if args.stats:
        print(m.stats())
    if args.update or not args.stats:
        codes = args.codes.split(",") if args.codes else None
        m.update(codes=codes, target_date=args.target_date, threads=args.threads,
                 checkpoint_codes=args.checkpoint)
        if m.last_failed:
            print(f"失败 {len(m.last_failed)} 只: {' '.join(m.last_failed[:10])}")
