"""
ETF 日K线数据层 — 独立缓存 cache/etf_kline_cache.parquet

ETF 代码使用原始格式（如 510050），与股票代码无冲突。
数据格式（日期/开/高/低/收/成交额）与 StockData 兼容，
可无缝接入现有选股和回测系统。

使用示例
--------
# 命令行
$ python data/etf.py                           # 更新全部 ETF（首次 ~20min）
$ python data/etf.py --codes 510050,159919     # 只拉指定 ETF
$ python data/etf.py --stats                   # 查看缓存覆盖
$ python data/etf.py --start 20250101          # 指定起始日期

# 代码调用
>>> from data.etf import ETFData

>>> etf = ETFData()
>>> etf.update()                                # 增量更新全部 ETF
>>> etf.update(codes=["510050", "159919"])      # 指定 ETF

>>> etf.count                                   # 1566 只
>>> etf.stats()                                 # 缓存统计
>>> kline = etf.get_kline("510050", days=10)    # 近 10 日 K 线

# 选股时自动包含 ETF
$ python -m scripts.screen                      # 股票+ETF 全部
$ python -m scripts.screen --universe etf       # 仅 ETF
$ python -m scripts.screen --universe stock     # 仅股票

缓存文件: cache/etf_kline_cache.parquet (独立于股票缓存)
代码格式: 纯数字 "510050"（接口自动处理 sh/sz 前缀映射）
"""

import os
import sys
import time
from typing import List, Optional

import akshare as ak
import pandas as pd
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from data.storage import atomic_write_parquet
from data.schema import (
    DAILY_BAR_COLUMNS,
    codes_missing_field_history,
    empty_daily_bars,
    ensure_daily_bar_schema,
)
from data.sources import normalize_source_daily_bars

CACHE_FILE = os.path.join(PROJECT_DIR, "cache", "etf_kline_cache.parquet")

THREADS = 8
DELAY = 0.05
COLUMNS = [column for column in DAILY_BAR_COLUMNS if column != "代码"]


class ETFData:
    """ETF 数据管理器"""

    def __init__(self, cache_file: str = CACHE_FILE):
        self.cache_file = cache_file
        self._list: Optional[pd.DataFrame] = None
        self.last_failed: List[str] = []

    # ========== 缓存 ==========

    @property
    def cache(self) -> pd.DataFrame:
        """读取 ETF 缓存"""
        if os.path.exists(self.cache_file):
            df = pd.read_parquet(self.cache_file)
            df["日期"] = pd.to_datetime(df["日期"])
            return df
        return empty_daily_bars()

    def _save(self, df: pd.DataFrame):
        atomic_write_parquet(df, self.cache_file)

    def upsert(self, rows: pd.DataFrame) -> int:
        """按 ``代码+日期`` 合并一批 ETF 日 K 数据并安全落盘。"""
        if rows is None or len(rows) == 0:
            return 0
        rows = ensure_daily_bar_schema(rows)
        cache = self.cache
        before = len(cache)
        cache = rows.copy() if before == 0 else pd.concat(
            [cache, rows], ignore_index=True
        )
        cache = ensure_daily_bar_schema(
            cache.drop_duplicates(subset=["代码", "日期"], keep="last")
        ).sort_values(["代码", "日期"]).reset_index(drop=True)
        self._save(cache)
        return len(cache) - before

    # ========== ETF 列表 ==========

    def get_list(self, refresh: bool = False) -> pd.DataFrame:
        """获取全市场 ETF 列表"""
        if self._list is not None and not refresh:
            return self._list
        df = ak.fund_etf_spot_em()
        df = df[["代码", "名称"]].copy()
        df["代码"] = df["代码"].astype(str)
        from data.etf_names import save_names
        save_names({('sh' if code.startswith('5') else 'sz') + code.zfill(6): name
                    for code, name in zip(df['代码'], df['名称'])
                    if isinstance(name, str) and name.strip()})
        self._list = df.reset_index(drop=True)
        return self._list

    # ========== 更新 ==========

    @staticmethod
    def _fetch_kline(code: str, start: str, end: str):
        prefix = "sh" if code.startswith(("5", "56", "58")) else "sz"
        symbol = prefix + code
        for attempt in range(3):
            try:
                time.sleep(DELAY)
                df = ak.stock_zh_a_hist_tx(
                    symbol=symbol, start_date=start, end_date=end,
                    adjust="", timeout=15,
                )
                if len(df) == 0:
                    return pd.DataFrame(columns=["代码"] + COLUMNS)
                return normalize_source_daily_bars(df, code, turnover_scale=100.0)
            except Exception:
                if attempt < 2:
                    time.sleep(2)
        return None

    @staticmethod
    def _fetch_field_history(code: str, start: str, end: str):
        """新浪 ETF 全历史接口；单次请求返回完整区间，适合字段迁移。"""
        prefix = "sh" if code.startswith(("5", "56", "58")) else "sz"
        for attempt in range(3):
            try:
                frame = ak.fund_etf_hist_sina(symbol=prefix + code)
                if len(frame) == 0:
                    return pd.DataFrame(columns=["代码"] + COLUMNS)
                dates = pd.to_datetime(frame["date"], errors="coerce")
                start_date = pd.Timestamp(start)
                end_date = pd.Timestamp(end)
                frame = frame.loc[dates.between(start_date, end_date)].copy()
                return normalize_source_daily_bars(frame, code)
            except Exception:
                if attempt < 2:
                    time.sleep(1 + attempt)
        return None

    def _update_existing_fields(self, rows: pd.DataFrame, fields: List[str]) -> int:
        """按键更新非空字段，不改写同日 OHLC，也不创建日期。"""
        if rows is None or len(rows) == 0:
            return 0
        cache = ensure_daily_bar_schema(self.cache)
        incoming = ensure_daily_bar_schema(rows).drop_duplicates(
            ["代码", "日期"], keep="last"
        )
        incoming_index = incoming.set_index(["代码", "日期"])
        cache_keys = pd.MultiIndex.from_arrays([cache["代码"], cache["日期"]])
        changed = 0
        for field in fields:
            if field not in incoming_index:
                continue
            values = incoming_index[field].reindex(cache_keys).to_numpy()
            mask = pd.notna(values)
            changed += int(mask.sum())
            cache.loc[mask, field] = values[mask]
        if changed:
            self._save(cache)
        return changed

    def codes_missing_history(
        self,
        codes: List[str],
        field: str,
        target_date,
        periods: int = 21,
    ) -> List[str]:
        return codes_missing_field_history(
            self.cache, codes, field, target_date, periods=periods
        )

    def backfill_fields(
        self,
        codes: List[str],
        start_date,
        end_date,
        *,
        threads: int = THREADS,
        progress: bool = True,
        checkpoint_codes: int = 100,
    ) -> "ETFData":
        """强制回填新增历史字段，不受缓存最新日期短路逻辑影响。"""
        self.last_failed = []
        requested = list(dict.fromkeys(str(code) for code in codes))
        if not requested:
            return self
        start = pd.Timestamp(start_date).strftime("%Y%m%d")
        end = pd.Timestamp(end_date).strftime("%Y%m%d")
        frames = []

        def _flush():
            if frames:
                self._update_existing_fields(
                    pd.concat(frames, ignore_index=True),
                    ["成交量", "换手率%"],
                )
                frames.clear()

        with ThreadPoolExecutor(max_workers=threads) as executor:
            tasks = {
                executor.submit(self._fetch_field_history, code, start, end): code
                for code in requested
            }
            iterator = as_completed(tasks)
            if progress:
                iterator = tqdm(iterator, total=len(tasks), desc="ETF字段回填")
            for future in iterator:
                result = future.result()
                if result is None:
                    self.last_failed.append(tasks[future])
                elif len(result):
                    frames.append(result)
                    if len(frames) >= checkpoint_codes:
                        _flush()
        _flush()
        return self

    def update(self, start_date: str = "20240101", codes: List[str] = None,
               target_date=None) -> "ETFData":
        """
        增量更新 ETF K 线。

        Args:
            start_date: 起始日期 YYYYMMDD
            codes: 指定代码列表，None=全部
            target_date: 明确的目标交易日；None=今天
        """
        self.last_failed = []
        etfs = self.get_list()
        if codes:
            etfs = etfs[etfs["代码"].isin(codes)]
            print(f"指定 {len(etfs)} 只 ETF")

        target = (pd.Timestamp(target_date).normalize() if target_date is not None
                  else pd.Timestamp.today().normalize())
        end_date = target.strftime("%Y%m%d")

        cache = self.cache
        if len(cache) > 0:
            latest_map = cache.groupby("代码")["日期"].max()
        else:
            latest_map = pd.Series(dtype="datetime64[ns]")

        to_fetch = []
        for _, row in etfs.iterrows():
            code = row["代码"]
            if code in latest_map.index:
                latest = latest_map[code]
                if latest >= target:
                    continue
                fetch_start = (latest + pd.Timedelta(days=1)).strftime("%Y%m%d")
            else:
                fetch_start = start_date
            to_fetch.append((code, fetch_start))

        if not to_fetch:
            print("✓ ETF 缓存已是最新")
            return self

        print(f"ETF 更新: {len(to_fetch)} 只")
        new_data = []

        def _fetch(code, start):
            return self._fetch_kline(code, start, end_date)

        with ThreadPoolExecutor(max_workers=THREADS) as executor:
            tasks = {executor.submit(_fetch, c, s): c for c, s in to_fetch}
            for future in tqdm(as_completed(tasks), total=len(tasks), desc="ETF"):
                result = future.result()
                if result is not None:
                    new_data.append(result)
                else:
                    self.last_failed.append(tasks[future])

        if new_data:
            ndf = pd.concat(new_data, ignore_index=True)
            cache = pd.concat([cache, ndf], ignore_index=True)
            cache = ensure_daily_bar_schema(
                cache.drop_duplicates(subset=["代码", "日期"], keep="last")
            ).sort_values(["代码", "日期"]).reset_index(drop=True)
            self._save(cache)
            print(f"✓ 新增 {len(ndf)} 条, 缓存共 {len(cache)} 条, {cache['代码'].nunique()} 只")
        else:
            print("✗ 无新数据（网络可能异常）")
        return self

    # ========== 查询 ==========

    def get_kline(self, code: str, days: int = 60) -> pd.DataFrame:
        """获取单只 ETF 最近 N 日 K 线，支持 sh510050 或 510050 格式"""
        # 去前缀: sh510050 → 510050
        raw = code[2:] if code.startswith(("sh", "sz")) else code
        cache = self.cache
        df = cache[cache["代码"] == raw].sort_values("日期").tail(days)
        columns = [column for column in COLUMNS if column in df.columns]
        return df[columns].reset_index(drop=True)

    @property
    def cache_with_prefix(self) -> "pd.DataFrame":
        """
        返回带交易所前缀的 ETF 缓存，与股票代码格式兼容。

        510050 → sh510050, 159919 → sz159919
        可直接与 StockData.cache 合并使用。
        """
        df = self.cache.copy()
        if len(df) == 0:
            return df

        def _add_prefix(code: str) -> str:
            if code.startswith(("5", "56", "58")):
                return "sh" + code
            return "sz" + code

        df["代码"] = df["代码"].apply(_add_prefix)
        return df

    # ========== 统计 ==========

    @property
    def count(self) -> int:
        return len(self.get_list())

    def stats(self) -> dict:
        cache = self.cache
        return {
            "cached": cache["代码"].nunique() if len(cache) > 0 else 0,
            "total": self.count,
            "records": len(cache),
        }


# ====================================================================
# CLI
# ====================================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ETF 数据管理")
    parser.add_argument("--update", action="store_true", help="更新")
    parser.add_argument("--codes", type=str, default=None, help="逗号分隔的代码（测试用）")
    parser.add_argument("--stats", action="store_true", help="统计")
    parser.add_argument("--start", type=str, default="20240101", help="起始日期")
    args = parser.parse_args()

    etf = ETFData()
    print(f"ETF 列表: {etf.count} 只")

    if args.stats:
        s = etf.stats()
        print(f"缓存: {s['cached']}/{s['total']} 只, {s['records']:,} 条")

    if args.update or not args.stats:
        codes = args.codes.split(",") if args.codes else None
        etf.update(start_date=args.start, codes=codes)
