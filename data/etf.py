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
$ python scripts/screen.py                      # 股票+ETF 全部
$ python scripts/screen.py --universe etf       # 仅 ETF
$ python scripts/screen.py --universe stock     # 仅股票

缓存文件: cache/etf_kline_cache.parquet (独立于股票缓存)
代码格式: 纯数字 "510050"（接口自动处理 sh/sz 前缀映射）
"""

import os
import time
from typing import List, Optional

import akshare as ak
import pandas as pd
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_FILE = os.path.join(PROJECT_DIR, "cache", "etf_kline_cache.parquet")

THREADS = 8
DELAY = 0.05
COLUMNS = ["日期", "开盘", "最高", "最低", "收盘", "成交额"]


class ETFData:
    """ETF 数据管理器"""

    def __init__(self):
        self._list: Optional[pd.DataFrame] = None

    # ========== 缓存 ==========

    @property
    def cache(self) -> pd.DataFrame:
        """读取 ETF 缓存"""
        if os.path.exists(CACHE_FILE):
            df = pd.read_parquet(CACHE_FILE)
            df["日期"] = pd.to_datetime(df["日期"])
            return df
        return pd.DataFrame(columns=["代码"] + COLUMNS)

    def _save(self, df: pd.DataFrame):
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        df.to_parquet(CACHE_FILE, index=False)

    # ========== ETF 列表 ==========

    def get_list(self, refresh: bool = False) -> pd.DataFrame:
        """获取全市场 ETF 列表"""
        if self._list is not None and not refresh:
            return self._list
        df = ak.fund_etf_spot_em()
        df = df[["代码", "名称"]].copy()
        df["代码"] = df["代码"].astype(str)
        self._list = df.reset_index(drop=True)
        return self._list

    # ========== 更新 ==========

    def update(self, start_date: str = "20240101", codes: List[str] = None) -> "ETFData":
        """
        增量更新 ETF K 线。

        Args:
            start_date: 起始日期 YYYYMMDD
            codes: 指定代码列表，None=全部
        """
        etfs = self.get_list()
        if codes:
            etfs = etfs[etfs["代码"].isin(codes)]
            print(f"指定 {len(etfs)} 只 ETF")

        today = pd.Timestamp.today().normalize()
        end_date = today.strftime("%Y%m%d")

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
                if latest >= today:
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
            # ETF 用股票API: 5xxxxx→sh, 1xxxxx→sz
            prefix = "sh" if code.startswith(("5", "56", "58")) else "sz"
            symbol = prefix + code
            for attempt in range(3):
                try:
                    time.sleep(DELAY)
                    df = ak.stock_zh_a_hist_tx(
                        symbol=symbol, start_date=start, end_date=end_date,
                        adjust="", timeout=15,
                    )
                    if len(df) == 0:
                        return None
                    df = df.rename(columns={
                        "date": "日期", "open": "开盘", "high": "最高",
                        "low": "最低", "close": "收盘", "amount": "成交额",
                    })
                    df = df[["日期", "开盘", "最高", "最低", "收盘", "成交额"]].copy()
                    df["代码"] = code
                    df["日期"] = pd.to_datetime(df["日期"])
                    for c in ["开盘", "最高", "最低", "收盘", "成交额"]:
                        df[c] = pd.to_numeric(df[c], errors="coerce")
                    return df
                except Exception:
                    if attempt < 2:
                        time.sleep(2)
            return None

        with ThreadPoolExecutor(max_workers=THREADS) as executor:
            tasks = {executor.submit(_fetch, c, s): c for c, s in to_fetch}
            for future in tqdm(as_completed(tasks), total=len(tasks), desc="ETF"):
                result = future.result()
                if result is not None:
                    new_data.append(result)

        if new_data:
            ndf = pd.concat(new_data, ignore_index=True)
            cache = pd.concat([cache, ndf], ignore_index=True)
            cache = cache.drop_duplicates(subset=["代码", "日期"], keep="last")
            cache = cache.sort_values(["代码", "日期"]).reset_index(drop=True)
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
        return df[COLUMNS].reset_index(drop=True)

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
    parser.add_argument("--update", action="store_true", default=True, help="更新")
    parser.add_argument("--codes", type=str, default=None, help="逗号分隔的代码（测试用）")
    parser.add_argument("--stats", action="store_true", help="统计")
    parser.add_argument("--start", type=str, default="20240101", help="起始日期")
    args = parser.parse_args()

    etf = ETFData()
    print(f"ETF 列表: {etf.count} 只")

    if args.stats:
        s = etf.stats()
        print(f"缓存: {s['cached']}/{s['total']} 只, {s['records']:,} 条")

    if args.update:
        codes = args.codes.split(",") if args.codes else None
        etf.update(start_date=args.start, codes=codes)
