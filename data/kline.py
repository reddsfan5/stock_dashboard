"""
A股日K线数据层 — 本地缓存管理 + 增量更新

职责：
  - 维护本地 parquet 缓存（stock_kline_cache.parquet）
  - 增量拉取缺失数据（首次全量，后续只拉增量）
  - 提供统一数据查询接口给上层分析模块

使用示例
--------
>>> from stock_data import StockData

>>> data = StockData()

# 增量更新缓存（首次全量 ~27min，后续增量 ~1-2min）
>>> data.update()

# 查看缓存概况
>>> data.stock_count          # 3042
>>> data.date_range           # (Timestamp('2026-07-22'), Timestamp('2026-07-28'))
>>> data.latest_dates[-5:]    # 最近 5 个交易日

# 查询单只股票 K 线
>>> kline = data.get_kline("sh600519", days=10)
>>> kline.columns             # ['日期', '最高', '最低', '收盘', '成交额']

# 批量查询
>>> df = data.get_multi_kline(days=5)       # 全市场最近 5 日
>>> df = data.get_multi_kline(codes=["sh600519", "sz000001"], days=5)

# 透视表（行=代码, 列=日期, 值=收盘价）
>>> pivot = data.get_pivot(days=5, field="收盘")

# 最新快照
>>> snap = data.get_latest_snapshot()        # 全市场最新收盘价

# 日期范围查询
>>> df = data.get_date_range_data("2026-07-20", "2026-07-28")

# 股票列表
>>> stocks = data.get_stock_list(board="main")   # 仅沪深主板
>>> stocks = data.get_stock_list(board="all")    # 全 A 股
"""

import os
import time
import pandas as pd
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import akshare as ak
from tqdm import tqdm


# ====================================================================
# 配置
# ====================================================================

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(PROJECT_DIR, "cache")
CACHE_FILE = os.path.join(CACHE_DIR, "stock_kline_cache.parquet")
CACHE_DAYS = 60               # 缓存保留的自然日跨度（约40个交易日）
THREADS = 12                  # 并发请求数
DELAY = 0.02                  # 请求间隔（秒）

# 沪深主板代码前缀
MAIN_BOARD_PREFIX = (
    "000", "001", "002", "003",     # 深市主板
    "600", "601", "603", "605",     # 沪市主板
)

# 需要排除的板块前缀
EXCLUDE_PREFIX = (
    "300", "301",     # 创业板
    "688",            # 科创板
)


# ====================================================================
# 数据层
# ====================================================================

class StockData:
    """
    A 股日 K 线数据管理器

    封装缓存读写、增量更新、数据查询。上层分析模块只通过此类访问数据，
    不需要关心数据来源和缓存细节。
    """

    def __init__(self, cache_file: str = CACHE_FILE):
        self.cache_file = cache_file
        self._cache: Optional[pd.DataFrame] = None
        self._stock_list: Optional[pd.DataFrame] = None

    # ========== 缓存读写 ==========

    @property
    def cache(self) -> pd.DataFrame:
        """懒加载缓存"""
        if self._cache is None:
            if os.path.exists(self.cache_file):
                df = pd.read_parquet(self.cache_file)
                df["日期"] = pd.to_datetime(df["日期"])
                self._cache = df
            else:
                self._cache = pd.DataFrame(
                    columns=["代码", "日期", "开盘", "最高", "最低", "收盘", "成交额"]
                )
        return self._cache

    def _save_cache(self):
        """写入磁盘"""
        if self._cache is not None:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            self._cache.to_parquet(self.cache_file, index=False)

    @property
    def stock_count(self) -> int:
        return self.cache["代码"].nunique() if len(self.cache) > 0 else 0

    @property
    def date_range(self) -> tuple:
        if len(self.cache) == 0:
            return None, None
        return self.cache["日期"].min(), self.cache["日期"].max()

    @property
    def latest_dates(self) -> List[pd.Timestamp]:
        """最近 N 个交易日（去重排序）"""
        return sorted(self.cache["日期"].unique())[-10:]

    # ========== 股票列表 ==========

    def get_stock_list(self, board: str = "main") -> pd.DataFrame:
        """
        获取股票列表

        board='main'  → 仅沪深主板（默认）
        board='all'   → 全 A 股（含创业/科创，排除 ST/北交所）
        """
        if self._stock_list is not None and getattr(self, '_stock_list_board', '') == board:
            return self._stock_list

        df = ak.stock_zh_a_spot_tx()

        # 排除 ST / 退市 / 风险警示
        df = df[~df["name"].str.contains("ST|退", regex=True, na=False)]
        df = df[df["state"] == ""]

        # 排除北交所 + 科创板
        df = df[~df["code"].str.startswith("bj")]
        df = df[~df["code"].str.startswith("sh688")]

        if board == "main":
            df["code_num"] = df["code"].str[2:]
            df = df[df["code_num"].str.startswith(MAIN_BOARD_PREFIX)]
            df = df.drop(columns=["code_num"])

        df = df[["code", "name"]].rename(columns={"code": "代码", "name": "名称"})
        self._stock_list = df.reset_index(drop=True)
        self._stock_list_board = board
        return self._stock_list

    # ========== 重建/回填 ==========

    def rebuild(self, start_date: str = "20100101",
                stocks: Optional[pd.DataFrame] = None):
        """
        删除缓存，从 start_date 开始全量重建（如 20100101）
        """
        if os.path.exists(self.cache_file):
            os.remove(self.cache_file)
            self._cache = None
        if stocks is None:
            stocks = self.get_stock_list()

        end_date = pd.Timestamp.today().strftime("%Y%m%d")
        print(f"全量重建缓存: {start_date} → {end_date}")
        print(f"{len(stocks)} 只股票，预计 {len(stocks)*5/60:.0f}~{len(stocks)*10/60:.0f} 分钟...")

        self._fetch_range(stocks, start_date, end_date, desc="重建")
        return self

    def backfill(self, stocks: Optional[pd.DataFrame] = None):
        """一次性拉取更长历史（从缓存最早日回溯到 CACHE_DAYS 天前）"""
        if stocks is None:
            stocks = self.get_stock_list(board="all")

        today = pd.Timestamp.today().normalize()
        target_start = today - pd.Timedelta(days=CACHE_DAYS)
        cache_start = self.cache["日期"].min() if len(self.cache) > 0 else today

        if cache_start <= target_start:
            print(f"缓存已覆盖 {CACHE_DAYS} 天，无需回填")
            return self

        print(f"回填历史数据: {str(cache_start)[:10]} → {str(target_start)[:10]} "
              f"({len(stocks)} 只股票，约需 {len(stocks)*3/60:.0f} 分钟)...")

        fetch_end = (cache_start - pd.Timedelta(days=1)).strftime("%Y%m%d")
        fetch_start = target_start.strftime("%Y%m%d")
        self._fetch_range(stocks, fetch_start, fetch_end, desc="回填")
        return self

    def _fetch_range(self, stocks: pd.DataFrame, start: str, end: str, desc: str = ""):
        BATCH_SIZE = 100  # 每攒够 N 只股票就写一次磁盘

        # 确保缓存已初始化
        if self._cache is None:
            self._cache = pd.DataFrame(columns=["代码", "日期", "开盘", "最高", "最低", "收盘", "成交额"])
            if "日期" in self._cache.columns:
                self._cache["日期"] = pd.to_datetime(self._cache["日期"])

        pending = []    # 待写入的数据
        total_new = 0   # 累计新增条数

        def _flush():
            nonlocal total_new
            if not pending:
                return
            new_df = pd.concat(pending, ignore_index=True)
            self._cache = pd.concat([self._cache, new_df], ignore_index=True)
            self._cache = self._cache.drop_duplicates(subset=["代码", "日期"], keep="last")
            self._cache = self._cache.sort_values(["代码", "日期"]).reset_index(drop=True)
            total_new += len(new_df)
            self._save_cache()
            pending.clear()

        def _fetch(code):
            try:
                time.sleep(DELAY)
                df = ak.stock_zh_a_hist_tx(
                    symbol=code, start_date=start, end_date=end,
                    adjust="", timeout=30,
                )
                if len(df) == 0:
                    return None
                df = df[["date", "open", "high", "low", "close", "amount"]]
                df.columns = ["日期", "开盘", "最高", "最低", "收盘", "成交额"]
                df["代码"] = code
                df["日期"] = pd.to_datetime(df["日期"])
                for c in ["开盘", "最高", "最低", "收盘", "成交额"]:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
                return df
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=THREADS) as executor:
            tasks = {executor.submit(_fetch, row["代码"]): row["代码"]
                     for _, row in stocks.iterrows()}
            for future in tqdm(as_completed(tasks), total=len(tasks), desc=desc):
                result = future.result()
                if result is not None:
                    pending.append(result)
                    if len(pending) >= BATCH_SIZE:
                        _flush()

        # 最后一批
        _flush()
        print(f"✓ {desc}完成，新增 {total_new} 条，缓存共 {len(self._cache)} 条")

        return self

    # ========== 增量更新 ==========

    def update(self, stocks: Optional[pd.DataFrame] = None,
               board: str = "all", progress: bool = True) -> "StockData":
        """
        增量更新缓存：只拉取每只股票缺失的近期数据

        首次运行：全量拉取（慢）
        后续运行：只拉增量（快，秒级）
        """
        if stocks is None:
            stocks = self.get_stock_list(board=board)

        today = pd.Timestamp.today().normalize()
        now = pd.Timestamp.now()
        # 收盘前（<15:00）当天 K 线不完整，只期望到昨天；收盘后期望到今天
        market_closed = now.hour >= 15
        expected = today if market_closed else today - pd.Timedelta(days=1)

        need_since = (today - pd.Timedelta(days=CACHE_DAYS)).strftime("%Y%m%d")
        FULL_START = "20100101"  # 缓存中没有的股票，从 2010 年开始全量拉
        codes_to_fetch = []

        # 预计算每只股票的最新日期（一次 groupby，避免 N 次全表扫描）
        cache_latest = (
            self.cache.groupby("代码")["日期"].max() if len(self.cache) > 0
            else pd.Series(dtype="datetime64[ns]")
        )

        for _, row in stocks.iterrows():
            code = row["代码"]
            if code in cache_latest.index:
                latest = cache_latest[code]
                if latest >= expected:
                    continue
                fetch_start = (latest + pd.Timedelta(days=1)).strftime("%Y%m%d")
            else:
                fetch_start = FULL_START  # 新股票：全量拉取

            codes_to_fetch.append((code, fetch_start))

        if not codes_to_fetch:
            if progress:
                print("✓ 缓存已是最新")
            return self

        if progress:
            print(f"更新 {len(codes_to_fetch)} / {len(stocks)} 只...")

        fetch_end = today.strftime("%Y%m%d")
        new_data = []
        total_new = 0

        def _fetch(code, start):
            try:
                time.sleep(DELAY)
                df = ak.stock_zh_a_hist_tx(
                    symbol=code, start_date=start, end_date=fetch_end,
                    adjust="", timeout=5,
                )
                if len(df) == 0:
                    return None
                df = df[["date", "open", "high", "low", "close", "amount"]]
                df.columns = ["日期", "开盘", "最高", "最低", "收盘", "成交额"]
                df["代码"] = code
                df["日期"] = pd.to_datetime(df["日期"])
                for c in ["开盘", "最高", "最低", "收盘", "成交额"]:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
                return df
            except Exception:
                return None

        def _flush():
            nonlocal total_new
            if not new_data:
                return
            new_df = pd.concat(new_data, ignore_index=True)
            self._cache = pd.concat([self.cache, new_df], ignore_index=True)
            self._cache = self._cache.drop_duplicates(subset=["代码", "日期"], keep="last")
            self._cache = self._cache.sort_values(["代码", "日期"]).reset_index(drop=True)
            total_new += len(new_df)
            self._save_cache()
            new_data.clear()

        BATCH = 100  # 每攒够 100 只股票就写一次磁盘
        with ThreadPoolExecutor(max_workers=THREADS) as executor:
            tasks = {executor.submit(_fetch, c, s): c for c, s in codes_to_fetch}
            it = tqdm(as_completed(tasks), total=len(tasks)) if progress else as_completed(tasks)
            for future in it:
                result = future.result()
                if result is not None:
                    new_data.append(result)
                    if len(new_data) >= BATCH:
                        _flush()

        # 最后一批
        _flush()
        if progress:
            print(f"✓ 新增 {total_new} 条")
        return self

    # ========== 数据查询 API ==========

    def get_kline(self, code: str, days: int = 10) -> pd.DataFrame:
        """
        获取单只股票最近 N 个交易日的日K线

        Returns:
            DataFrame with columns: 日期, 最高, 最低, 收盘, 成交额
            不足 N 天返回空 DataFrame
        """
        df = self.cache[self.cache["代码"] == code].copy()
        if len(df) == 0:
            return pd.DataFrame()

        df = df.sort_values("日期").tail(days)
        cols = ["日期", "开盘", "最高", "最低", "收盘", "成交额"]
        cols = [c for c in cols if c in df.columns]
        return df[cols].reset_index(drop=True)

    def get_multi_kline(self, codes: Optional[List[str]] = None,
                        days: int = 10) -> pd.DataFrame:
        """
        批量获取多只股票最近 N 日 K 线

        Args:
            codes: 股票代码列表，None=全部
            days: 取最近几个交易日

        Returns:
            DataFrame with columns: 代码, 日期, 最高, 最低, 收盘, 成交额
        """
        latest_dates = self.latest_dates[-days:]
        df = self.cache[self.cache["日期"].isin(latest_dates)]
        if codes is not None:
            df = df[df["代码"].isin(codes)]
        return df.sort_values(["代码", "日期"]).reset_index(drop=True)

    def get_latest_snapshot(self) -> pd.DataFrame:
        """
        最新交易日全市场收盘快照

        Returns:
            DataFrame indexed by 代码, with columns: 收盘, 最高, 最低, 成交额
        """
        latest = self.latest_dates[-1]
        df = self.cache[self.cache["日期"] == latest].copy()
        return df.set_index("代码")[["收盘", "最高", "最低", "成交额"]]

    def get_date_range_data(self, start_date: str, end_date: str,
                            codes: Optional[List[str]] = None) -> pd.DataFrame:
        """获取指定日期范围内的数据"""
        start = pd.to_datetime(start_date)
        end = pd.to_datetime(end_date)
        df = self.cache[
            (self.cache["日期"] >= start) & (self.cache["日期"] <= end)
        ]
        if codes is not None:
            df = df[df["代码"].isin(codes)]
        return df.sort_values(["代码", "日期"]).reset_index(drop=True)

    def get_pivot(self, days: int = 5, field: str = "收盘") -> pd.DataFrame:
        """
        获取最近 N 日透视表（行=代码，列=日期，值=指定字段）

        Returns:
            DataFrame: index=代码, columns=日期(最近N天), values=field
        """
        latest_dates = self.latest_dates[-days:]
        df = self.cache[self.cache["日期"].isin(latest_dates)]
        return df.pivot_table(
            index="代码", columns="日期", values=field, aggfunc="last"
        )

    # ========== 工具方法 ==========

    def invalidate_cache(self):
        """强制下次访问时重新读取磁盘缓存"""
        self._cache = None

    def get_stock_name(self, code: str) -> str:
        """获取股票名称"""
        stocks = self.get_stock_list()
        match = stocks[stocks["代码"] == code]
        return match.iloc[0]["名称"] if len(match) > 0 else ""


# ====================================================================
# 快速入口
# ====================================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="股票数据层")
    parser.add_argument("--rebuild", type=str, nargs="?", const="20100101",
                        metavar="YYYYMMDD", help="全量重建缓存，默认从2010年开始")
    parser.add_argument("--update", action="store_true", help="增量更新")
    parser.add_argument("--info", action="store_true", help="查看缓存信息")
    args = parser.parse_args()

    print(f"stock_data.py: {args}", flush=True)

    data = StockData()

    if args.rebuild:
        data.rebuild(start_date=args.rebuild)
    elif args.update:
        data.update()
    elif args.info:
        print(f"记录: {len(data.cache)} 条, {data.stock_count} 只")
        print(f"日期: {data.date_range[0]} ~ {data.date_range[1]}")
        k = data.get_kline("sh600519", days=5)
        print(f"\n茅台近5日:\n{k.to_string()}")
    else:
        data.update()
        print(f"\n缓存: {data.stock_count} 只, {len(data.cache)} 条")
        print(f"日期范围: {data.date_range}")
        k = data.get_kline("sh600519", days=5)
        print(f"\n茅台近5日:\n{k.to_string()}")
