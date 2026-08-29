"""每日行情数据更新管线。

把股票、ETF、指数、分钟线、质量校验和报告刷新拆成可观测阶段。每个阶段
都会写入 ``cache/daily_update_status.json``，避免“主进程返回成功，但某类数据
其实没有更新”的假成功。
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
import gc
import logging
import os
import subprocess
import sys
import time
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from data.etf import CACHE_FILE as ETF_CACHE_FILE, ETFData
from data.index import CACHE_FILE as INDEX_CACHE_FILE, INDEXES, IndexData
from data.kline import CACHE_FILE as STOCK_CACHE_FILE, StockData
from data.minute import CACHE_FILE as MINUTE_CACHE_FILE, MinuteData
from data.sources import (
    ak_fetch_kline,
    bs_fetch_kline,
    bs_get_stock_list,
    baostock_session,
    tx_fetch_daily_snapshot,
)
from data.storage import atomic_write_json


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS_FILE = os.path.join(PROJECT_DIR, "cache", "daily_update_status.json")
STAGES = ("stocks", "etfs", "index", "minute", "validate", "reports")


@dataclass
class StageResult:
    name: str
    ok: bool
    critical: bool
    duration_seconds: float
    message: str
    details: Dict = field(default_factory=dict)


def _date_text(value) -> Optional[str]:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _prefix_etf(code: str) -> str:
    value = str(code)
    return ("sh" if value.startswith(("5", "56", "58")) else "sz") + value


class DailyUpdatePipeline:
    """单进程阶段编排；数据层自身负责原子落盘和分钟线断点续跑。"""

    def __init__(
        self,
        source: str = "auto",
        limit: Optional[int] = None,
        only: Optional[Sequence[str]] = None,
        minute_threads: int = 8,
        minute_checkpoint: int = 1000,
        target_date=None,
        logger: Optional[logging.Logger] = None,
    ):
        self.source = source
        self.limit = limit
        self.only = set(only or STAGES)
        unknown = self.only.difference(STAGES)
        if unknown:
            raise ValueError(f"未知阶段: {', '.join(sorted(unknown))}")
        self.minute_threads = minute_threads
        self.minute_checkpoint = minute_checkpoint
        self.target_date = (pd.Timestamp(target_date).normalize()
                            if target_date is not None else None)
        self.logger = logger or logging.getLogger(__name__)
        self.started_at = datetime.now()
        self.results: List[StageResult] = []
        self.current_stage = None
        self.stock_codes: List[str] = []
        self.etf_codes: List[str] = []

    # ------------------------------------------------------------------
    # 状态与阶段执行
    # ------------------------------------------------------------------

    def _write_status(self, state: str, ok: Optional[bool] = None) -> None:
        payload = {
            "state": state,
            "ok": ok,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "target_date": _date_text(self.target_date),
            "current_stage": self.current_stage,
            "source": self.source,
            "limit": self.limit,
            "stages": [asdict(result) for result in self.results],
        }
        atomic_write_json(payload, STATUS_FILE)

    def _run_stage(self, name: str, critical: bool, function) -> StageResult:
        self.current_stage = name
        self._write_status("running")
        self.logger.info("[%s] 开始", name)
        started = time.monotonic()
        try:
            ok, message, details = function()
        except Exception as exc:
            self.logger.exception("[%s] 异常", name)
            ok, message, details = False, str(exc), {"error": repr(exc)}
        result = StageResult(
            name=name,
            ok=bool(ok),
            critical=critical,
            duration_seconds=round(time.monotonic() - started, 2),
            message=message,
            details=details or {},
        )
        self.results.append(result)
        marker = "完成" if result.ok else "失败"
        self.logger.info("[%s] %s — %s (%.1fs)", name, marker, message,
                         result.duration_seconds)
        self._write_status("running")
        gc.collect()
        return result

    # ------------------------------------------------------------------
    # 股票日线
    # ------------------------------------------------------------------

    def _get_stock_list(self, data: StockData) -> pd.DataFrame:
        if self.source == "baostock":
            stocks = bs_get_stock_list(board="all")
        else:
            stocks = None
            for attempt in range(3):
                try:
                    stocks = data.get_stock_list(board="all")
                    break
                except Exception:
                    if attempt < 2:
                        self.logger.warning("股票列表失败，%d 秒后重试 (%d/3)",
                                            5 * (attempt + 1), attempt + 1)
                        time.sleep(5 * (attempt + 1))
            if stocks is None:
                if self.source == "akshare":
                    raise RuntimeError("腾讯股票列表连续 3 次失败")
                self.logger.warning("腾讯股票列表失败，改用 baostock 股票列表")
                stocks = bs_get_stock_list(board="all")
        if self.limit:
            stocks = stocks.head(self.limit)
        return stocks.reset_index(drop=True)

    def _legacy_stock_update(self, data: StockData, stocks: pd.DataFrame,
                             target) -> Tuple[List[str], List[str]]:
        if len(stocks) == 0:
            return [], []
        if self.source == "baostock":
            with baostock_session():
                data.update(stocks, progress=False, fetch_fn=bs_fetch_kline,
                            threads=1, target_date=target)
            return [], list(data.last_failed)

        data.update(stocks, progress=False, fetch_fn=ak_fetch_kline,
                    target_date=target)
        primary_failed = list(data.last_failed)
        final_failed = primary_failed
        if primary_failed and self.source == "auto":
            failed_df = stocks[stocks["代码"].isin(primary_failed)]
            self.logger.warning("股票日线主源失败 %d 只，baostock 回退", len(failed_df))
            with baostock_session():
                data.update(failed_df, progress=False, fetch_fn=bs_fetch_kline,
                            threads=1, target_date=target)
            final_failed = list(data.last_failed)
        return primary_failed, final_failed

    @staticmethod
    def _reference_missing_dates(cache_dates: Iterable, start, end):
        """用一只高流动性股票确认两个日期之间有几个真实交易日。"""
        if start is None or pd.Timestamp(start) >= pd.Timestamp(end):
            return []
        frame = ak_fetch_kline(
            "sh600519",
            (pd.Timestamp(start) + pd.Timedelta(days=1)).strftime("%Y%m%d"),
            pd.Timestamp(end).strftime("%Y%m%d"),
            timeout=15,
        )
        if frame is None:
            return None
        known = {pd.Timestamp(value).normalize() for value in cache_dates}
        return [date for date in pd.to_datetime(frame["日期"]).dt.normalize().unique()
                if pd.Timestamp(date) not in known]

    def _stock_stage(self):
        data = StockData()
        stocks = self._get_stock_list(data)
        self.stock_codes = list(stocks["代码"])

        if self.source == "baostock":
            target = self.target_date or pd.Timestamp.today().normalize()
            primary_failed, failed = self._legacy_stock_update(data, stocks, target)
            self.target_date = max(self.target_date or target, target)
            ok = len(failed) <= max(2, int(len(stocks) * 0.01))
            return ok, f"baostock 增量，失败 {len(failed)} 只", {
                "expected_codes": len(stocks), "failed": failed[:20],
            }

        quotes, quote_failed = tx_fetch_daily_snapshot(stocks["代码"])
        if len(quotes) == 0:
            raise RuntimeError("腾讯批量收盘快照为空")
        target = pd.to_datetime(quotes["日期"]).max().normalize()
        if self.target_date is not None:
            target = min(target, self.target_date)
        quotes = quotes[pd.to_datetime(quotes["日期"]).dt.normalize() == target]
        self.target_date = target

        cache = data.cache
        latest_map = (cache.groupby("代码")["日期"].max() if len(cache)
                      else pd.Series(dtype="datetime64[ns]"))
        global_latest = cache["日期"].max() if len(cache) else None
        cache_dates = cache["日期"].unique() if len(cache) else []
        repair = {code for code in stocks["代码"]
                  if code not in latest_map.index or
                  (global_latest is not None and latest_map[code] < global_latest)}

        missing_dates = self._reference_missing_dates(cache_dates, global_latest, target)
        if missing_dates is None and global_latest is not None and target > global_latest:
            repair.update(stocks["代码"])
        elif missing_dates and len(missing_dates) > 1:
            repair.update(stocks["代码"])
        repair.update(quote_failed)

        primary_failed: List[str] = []
        final_failed: List[str] = []
        if repair:
            repair_df = stocks[stocks["代码"].isin(repair)]
            primary_failed, final_failed = self._legacy_stock_update(
                data, repair_df, target
            )
        if global_latest is not None and target <= global_latest:
            rows_to_upsert = quotes[
                quotes["代码"].map(latest_map).fillna(pd.Timestamp.min) < target
            ]
        else:
            rows_to_upsert = quotes
        inserted = data.upsert(rows_to_upsert)
        quote_coverage = len(quotes["代码"].unique()) / max(len(stocks), 1)
        ok = quote_coverage >= 0.95 and len(final_failed) <= max(2, int(len(stocks) * 0.01))
        return ok, (f"{target.date()} 快照 {len(quotes):,} 只，"
                    f"历史修复 {len(repair)} 只，最终失败 {len(final_failed)} 只"), {
            "target_date": _date_text(target),
            "expected_codes": len(stocks),
            "snapshot_codes": int(quotes["代码"].nunique()),
            "snapshot_coverage": round(quote_coverage, 6),
            "inserted_rows": inserted,
            "repair_codes": len(repair),
            "primary_failed": primary_failed[:20],
            "failed": final_failed[:20],
        }

    # ------------------------------------------------------------------
    # ETF / 指数
    # ------------------------------------------------------------------

    def _etf_stage(self):
        data = ETFData()
        try:
            etfs = data.get_list()
        except Exception:
            cache_codes = data.cache["代码"].drop_duplicates().astype(str)
            if len(cache_codes) == 0:
                raise
            self.logger.warning("ETF 列表接口失败，使用缓存中的 %d 只代码", len(cache_codes))
            etfs = pd.DataFrame({"代码": cache_codes, "名称": ""})
            data._list = etfs
        if self.limit:
            etfs = etfs.head(self.limit)
        self.etf_codes = list(etfs["代码"].astype(str))

        symbols = [_prefix_etf(code) for code in self.etf_codes]
        quotes, quote_failed = tx_fetch_daily_snapshot(symbols)
        if len(quotes) == 0:
            raise RuntimeError("ETF 批量收盘快照为空")
        quote_target = pd.to_datetime(quotes["日期"]).max().normalize()
        target = self.target_date or quote_target
        quotes = quotes[pd.to_datetime(quotes["日期"]).dt.normalize() == target].copy()
        quotes["代码"] = quotes["代码"].str[2:]

        cache = data.cache
        latest_map = (cache.groupby("代码")["日期"].max() if len(cache)
                      else pd.Series(dtype="datetime64[ns]"))
        global_latest = cache["日期"].max() if len(cache) else None
        repair = {code for code in self.etf_codes
                  if code not in latest_map.index or
                  (global_latest is not None and latest_map[code] < global_latest)}
        if global_latest is not None and target > global_latest:
            stock_dates = pd.read_parquet(STOCK_CACHE_FILE, columns=["日期"])["日期"]
            missing = sorted({pd.Timestamp(value).normalize() for value in stock_dates
                              if global_latest < pd.Timestamp(value) <= target})
            if len(missing) > 1:
                repair.update(self.etf_codes)
        repair.update(code[2:] for code in quote_failed)

        if repair:
            data.update(codes=sorted(repair), target_date=target)
        final_failed = list(data.last_failed)
        if global_latest is not None and target <= global_latest:
            rows_to_upsert = quotes[
                quotes["代码"].map(latest_map).fillna(pd.Timestamp.min) < target
            ]
        else:
            rows_to_upsert = quotes
        inserted = data.upsert(rows_to_upsert)
        coverage = len(quotes["代码"].unique()) / max(len(etfs), 1)
        ok = coverage >= 0.90 and len(final_failed) <= max(5, int(len(etfs) * 0.03))
        return ok, (f"{target.date()} 快照 {len(quotes):,} 只，"
                    f"历史修复 {len(repair)} 只，失败 {len(final_failed)} 只"), {
            "target_date": _date_text(target),
            "expected_codes": len(etfs),
            "snapshot_codes": int(quotes["代码"].nunique()),
            "snapshot_coverage": round(coverage, 6),
            "inserted_rows": inserted,
            "repair_codes": len(repair),
            "failed": final_failed[:20],
        }

    def _index_stage(self):
        target = self.target_date
        if target is None:
            cache = pd.read_parquet(STOCK_CACHE_FILE, columns=["日期"])
            target = pd.to_datetime(cache["日期"]).max().normalize()
            self.target_date = target
        data = IndexData()
        data.update(progress=False, target_date=target)
        cache = data.cache
        latest = cache.groupby("代码")["日期"].max() if len(cache) else pd.Series()
        fresh = [code for code in INDEXES if latest.get(code) is not None and
                 pd.Timestamp(latest[code]).normalize() >= target]
        ok = len(fresh) == len(INDEXES) and not data.last_failed
        return ok, f"{len(fresh)}/{len(INDEXES)} 个指数覆盖 {target.date()}", {
            "target_date": _date_text(target),
            "fresh": fresh,
            "failed": list(data.last_failed),
        }

    # ------------------------------------------------------------------
    # 分钟线、质量门禁与报告
    # ------------------------------------------------------------------

    def _minute_stage(self):
        target = self.target_date
        if target is None:
            dates = pd.read_parquet(STOCK_CACHE_FILE, columns=["日期"])["日期"]
            if len(dates) == 0:
                raise RuntimeError("股票日线缓存为空，无法确定分钟线目标日")
            target = pd.to_datetime(dates).max().normalize()
            self.target_date = target
        data = MinuteData()
        codes = None
        if self.limit:
            codes = list(self.stock_codes)
            codes.extend(_prefix_etf(code) for code in self.etf_codes)
            if not codes:
                codes, _ = data._active_codes(target)
                codes = codes[:self.limit]
        data.update(
            codes=codes,
            target_date=target,
            progress=True,
            threads=self.minute_threads,
            checkpoint_codes=self.minute_checkpoint,
        )
        if codes:
            frame = pd.read_parquet(MINUTE_CACHE_FILE, columns=["代码", "时间"])
            dates = pd.to_datetime(frame["时间"]).dt.normalize()
            available = set(frame.loc[dates == target, "代码"].astype(str))
            expected_codes = set(codes)
            missing = sorted(expected_codes - available)
            covered = len(expected_codes & available)
            metrics = {
                "covered": covered,
                "expected": len(expected_codes),
                "coverage": covered / max(len(expected_codes), 1),
                "missing_sample": missing[:20],
            }
        else:
            metrics = self._minute_coverage(target)
        failure_ratio = len(data.last_failed) / max(data.last_requested, 1)
        ok = metrics["coverage"] >= 0.95 and failure_ratio <= 0.05
        return ok, (f"目标日覆盖 {metrics['covered']}/{metrics['expected']} "
                    f"({metrics['coverage']:.1%})，请求失败 {len(data.last_failed)} 只"), {
            **metrics,
            "requested": data.last_requested,
            "updated": data.last_updated,
            "new_rows": data.last_new_rows,
            "request_failed": data.last_failed[:20],
            "no_data": data.last_no_data[:20],
        }

    @staticmethod
    def _daily_coverage(path: str, target, prefix=None) -> Dict:
        if not os.path.exists(path):
            return {"covered": 0, "expected": 0, "coverage": 0.0, "codes": set()}
        frame = pd.read_parquet(path, columns=["代码", "日期"])
        frame["日期"] = pd.to_datetime(frame["日期"]).dt.normalize()
        counts = frame.groupby("日期")["代码"].nunique().sort_index()
        recent = counts[counts.index <= target].tail(5)
        expected = int(recent.max()) if len(recent) else 0
        target_codes = set(frame.loc[frame["日期"] == target, "代码"].astype(str))
        if prefix:
            target_codes = {prefix(code) for code in target_codes}
        covered = len(target_codes)
        return {
            "covered": covered,
            "expected": expected,
            "coverage": covered / max(expected, 1),
            "codes": target_codes,
        }

    def _minute_coverage(self, target) -> Dict:
        if target is None:
            return {"covered": 0, "expected": 0, "coverage": 0.0,
                    "missing_sample": []}
        stock = self._daily_coverage(STOCK_CACHE_FILE, target)
        etf = self._daily_coverage(ETF_CACHE_FILE, target, prefix=_prefix_etf)
        expected_codes = stock["codes"] | etf["codes"]
        if not os.path.exists(MINUTE_CACHE_FILE):
            minute_codes = set()
        else:
            frame = pd.read_parquet(MINUTE_CACHE_FILE, columns=["代码", "时间"])
            dates = pd.to_datetime(frame["时间"]).dt.normalize()
            minute_codes = set(frame.loc[dates == target, "代码"].astype(str))
        missing = sorted(expected_codes - minute_codes)
        covered = len(expected_codes & minute_codes)
        return {
            "covered": covered,
            "expected": len(expected_codes),
            "coverage": covered / max(len(expected_codes), 1),
            "missing_sample": missing[:20],
        }

    def _validate_stage(self):
        if self.target_date is None:
            candidates = []
            for path in (STOCK_CACHE_FILE, ETF_CACHE_FILE, INDEX_CACHE_FILE):
                if os.path.exists(path):
                    dates = pd.read_parquet(path, columns=["日期"])["日期"]
                    if len(dates):
                        candidates.append(pd.to_datetime(dates).max().normalize())
            if not candidates:
                raise RuntimeError("日线缓存为空")
            self.target_date = max(candidates)
        target = self.target_date

        stock = self._daily_coverage(STOCK_CACHE_FILE, target)
        etf = self._daily_coverage(ETF_CACHE_FILE, target)
        minute = self._minute_coverage(target)
        index = pd.read_parquet(INDEX_CACHE_FILE, columns=["代码", "日期"])
        index["日期"] = pd.to_datetime(index["日期"]).dt.normalize()
        index_fresh = int(index.loc[index["日期"] == target, "代码"].nunique())

        checks = {
            "stocks": stock["coverage"] >= 0.95,
            "etfs": etf["coverage"] >= 0.90,
            "index": index_fresh == len(INDEXES),
            "minute": minute["coverage"] >= 0.95,
        }
        details = {
            "target_date": _date_text(target),
            "checks": checks,
            "stocks": {k: v for k, v in stock.items() if k != "codes"},
            "etfs": {k: v for k, v in etf.items() if k != "codes"},
            "index": {"covered": index_fresh, "expected": len(INDEXES)},
            "minute": minute,
        }
        failed = [name for name, passed in checks.items() if not passed]
        return not failed, ("全部数据通过新鲜度门禁" if not failed else
                            f"未通过: {', '.join(failed)}"), details

    @staticmethod
    def _reports_stage():
        result = subprocess.run(
            [sys.executable, "-m", "scripts.reports.gen_market"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=900,
        )
        lines = [line for line in (result.stdout or "").splitlines() if line.strip()]
        if result.returncode != 0:
            error = (result.stderr or result.stdout or "").strip()[-500:]
            return False, f"退出码 {result.returncode}: {error}", {}
        return True, lines[-1] if lines else "行情报告已刷新", {}

    # ------------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------------

    def run(self) -> bool:
        self._write_status("running")
        stage_functions = [
            ("stocks", True, self._stock_stage),
            ("etfs", True, self._etf_stage),
            ("index", True, self._index_stage),
            ("minute", True, self._minute_stage),
            ("validate", True, self._validate_stage),
            ("reports", False, self._reports_stage),
        ]
        for name, critical, function in stage_functions:
            if name not in self.only:
                continue
            if name == "reports" and any(
                result.critical and not result.ok for result in self.results
            ):
                result = StageResult(
                    name="reports", ok=False, critical=False,
                    duration_seconds=0.0,
                    message="数据质量门禁未通过，跳过报告刷新",
                    details={},
                )
                self.results.append(result)
                self.logger.warning("[reports] %s", result.message)
                self._write_status("running")
                continue
            self._run_stage(name, critical, function)

        self.current_stage = None
        ok = not any(result.critical and not result.ok for result in self.results)
        self._write_status("success" if ok else "failed", ok=ok)
        return ok
