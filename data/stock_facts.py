"""涨跌停 / 停牌标记与股票状态基础信息。

涨跌停优先从日 K 推导（主板约 ±10%，创业板/科创板约 ±20%，ST 约 ±5%），
并可选用东财涨停/跌停池做补充。网络失败时返回空补充，不抛错。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from data.storage import atomic_write_parquet

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parent.parent
LIMIT_CACHE = PROJECT_DIR / "cache" / "daily_limit_facts.parquet"
STATUS_CACHE = PROJECT_DIR / "cache" / "stock_status.parquet"

CODE_RE = re.compile(r"^(sh|sz)\d{6}$")


def normalize_code(code: str) -> str:
    value = str(code or "").strip().lower().replace(".", "")
    if CODE_RE.fullmatch(value):
        return value
    digits = re.sub(r"\D", "", value)
    if len(digits) != 6:
        raise ValueError("证券代码格式无效")
    return ("sh" if digits.startswith(("5", "6", "9")) else "sz") + digits


def limit_pct_for_code(code: str, *, is_st: bool = False) -> float:
    """返回涨跌停幅度（正数百分比）。"""
    raw = normalize_code(code)[2:]
    if is_st:
        return 5.0
    if raw.startswith(("300", "301", "688", "689")):
        return 20.0
    if raw.startswith(("8", "4")):  # 北交所粗略
        return 30.0
    return 10.0


def infer_limit_flags(
    close: float,
    prev_close: float,
    high: float,
    low: float,
    *,
    limit_pct: float,
    amount: float = None,
    volume: float = None,
    tolerance: float = 0.002,
) -> dict:
    """根据当日 OHLC 与昨收推断涨停/跌停/疑似停牌。"""
    result = {
        "limit_up": False,
        "limit_down": False,
        "one_word_limit_up": False,
        "one_word_limit_down": False,
        "suspended": False,
        "limit_pct": float(limit_pct),
    }
    if not prev_close or prev_close <= 0 or not np.isfinite(prev_close):
        return result
    if amount is not None and float(amount) == 0 and volume is not None and float(volume) == 0:
        result["suspended"] = True
        return result
    up = prev_close * (1 + limit_pct / 100)
    down = prev_close * (1 - limit_pct / 100)
    if close >= up * (1 - tolerance):
        result["limit_up"] = True
        if high <= up * (1 + tolerance) and low >= up * (1 - tolerance):
            result["one_word_limit_up"] = True
    if close <= down * (1 + tolerance):
        result["limit_down"] = True
        if high <= down * (1 + tolerance) and low >= down * (1 - tolerance):
            result["one_word_limit_down"] = True
    return result


def build_limit_facts_from_daily(
    daily: pd.DataFrame,
    *,
    st_codes: Optional[set[str]] = None,
) -> pd.DataFrame:
    """从日 K 生成涨跌停事实表。列：代码/日期/涨停/跌停/一字板/停牌/幅度。"""
    if daily is None or len(daily) == 0:
        return pd.DataFrame(columns=[
            "代码", "日期", "涨停", "跌停", "一字涨停", "一字跌停", "停牌", "涨跌幅限制%",
        ])
    frame = daily.copy()
    frame["日期"] = pd.to_datetime(frame["日期"]).dt.normalize()
    st_codes = st_codes or set()
    rows = []
    for _, row in frame.iterrows():
        code = str(row["代码"])
        flags = infer_limit_flags(
            float(row["收盘"]),
            float(row["前收"]) if pd.notna(row.get("前收")) else float("nan"),
            float(row["最高"]),
            float(row["最低"]),
            limit_pct=limit_pct_for_code(code, is_st=code in st_codes),
            amount=row.get("成交额"),
            volume=row.get("成交量"),
        )
        rows.append({
            "代码": code,
            "日期": row["日期"],
            "涨停": flags["limit_up"],
            "跌停": flags["limit_down"],
            "一字涨停": flags["one_word_limit_up"],
            "一字跌停": flags["one_word_limit_down"],
            "停牌": flags["suspended"],
            "涨跌幅限制%": flags["limit_pct"],
        })
    return pd.DataFrame(rows)


def fetch_limit_pool(market_date: str, *, kind: str = "up") -> pd.DataFrame:
    """拉取东财涨停/跌停池；失败返回空表。"""
    empty = pd.DataFrame(columns=["代码", "日期", "名称", "来源"])
    try:
        import akshare as ak
        day = pd.Timestamp(market_date).strftime("%Y%m%d")
        if kind == "up":
            raw = ak.stock_zt_pool_em(date=day)
            source = "zt_pool"
        else:
            raw = ak.stock_zt_pool_dtgc_em(date=day)
            source = "dt_pool"
    except Exception as exc:  # noqa: BLE001
        logger.warning("涨跌停池拉取失败 %s %s: %s", kind, market_date, exc)
        return empty
    if raw is None or len(raw) == 0:
        return empty
    code_col = "代码" if "代码" in raw.columns else ("code" if "code" in raw.columns else None)
    name_col = "名称" if "名称" in raw.columns else ("name" if "name" in raw.columns else None)
    if code_col is None:
        return empty
    out = pd.DataFrame({
        "代码": raw[code_col].map(lambda x: normalize_code(str(x))),
        "日期": pd.Timestamp(market_date).normalize(),
        "名称": raw[name_col] if name_col else "",
        "来源": source,
    })
    return out.drop_duplicates("代码")


def update_limit_facts(
    daily: Optional[pd.DataFrame] = None,
    *,
    market_date: Optional[str] = None,
    enrich_pool: bool = True,
) -> dict:
    """写入/合并日度涨跌停事实。daily 为空时仅尝试拉取涨停池。"""
    pieces = []
    st_codes = set()
    status = load_stock_status()
    if len(status):
        st_codes = set(status.loc[status["ST"], "代码"].astype(str))

    if daily is not None and len(daily):
        subset = daily
        if market_date:
            day = pd.Timestamp(market_date).normalize()
            subset = daily[pd.to_datetime(daily["日期"]).dt.normalize() == day]
        pieces.append(build_limit_facts_from_daily(subset, st_codes=st_codes))

    pool_rows = 0
    if enrich_pool and market_date:
        for kind, flag_col in (("up", "涨停"), ("down", "跌停")):
            pool = fetch_limit_pool(market_date, kind=kind)
            pool_rows += len(pool)
            if pool.empty:
                continue
            extra = pd.DataFrame({
                "代码": pool["代码"],
                "日期": pool["日期"],
                "涨停": flag_col == "涨停",
                "跌停": flag_col == "跌停",
                "一字涨停": False,
                "一字跌停": False,
                "停牌": False,
                "涨跌幅限制%": [
                    limit_pct_for_code(code, is_st=code in st_codes) for code in pool["代码"]
                ],
            })
            pieces.append(extra)

    if not pieces:
        return {"ok": True, "message": "无涨跌停事实可写", "rows": 0, "pool_rows": pool_rows}

    fresh = pd.concat(pieces, ignore_index=True)
    fresh = fresh.drop_duplicates(["代码", "日期"], keep="last")
    if LIMIT_CACHE.exists():
        old = pd.read_parquet(LIMIT_CACHE)
        old["日期"] = pd.to_datetime(old["日期"]).dt.normalize()
        merged = pd.concat([old, fresh], ignore_index=True)
        merged = merged.drop_duplicates(["代码", "日期"], keep="last")
    else:
        merged = fresh
    atomic_write_parquet(merged, str(LIMIT_CACHE))
    return {
        "ok": True,
        "message": f"涨跌停事实 {len(fresh)} 行（池补充 {pool_rows}）",
        "rows": len(fresh),
        "pool_rows": pool_rows,
        "total_rows": len(merged),
    }


def load_limit_facts(
    *, code: Optional[str] = None, market_date: Optional[str] = None
) -> pd.DataFrame:
    if not LIMIT_CACHE.exists():
        return pd.DataFrame(columns=[
            "代码", "日期", "涨停", "跌停", "一字涨停", "一字跌停", "停牌", "涨跌幅限制%",
        ])
    frame = pd.read_parquet(LIMIT_CACHE)
    frame["日期"] = pd.to_datetime(frame["日期"]).dt.normalize()
    if code:
        frame = frame[frame["代码"] == normalize_code(code)]
    if market_date:
        day = pd.Timestamp(market_date).normalize()
        frame = frame[frame["日期"] == day]
    return frame.reset_index(drop=True)


def is_st_name(name: str) -> bool:
    text = str(name or "").upper().replace(" ", "")
    return "ST" in text or text.startswith("*ST") or text.startswith("＊ST")


def update_stock_status(name_map: Optional[dict] = None) -> dict:
    """刷新 ST / 名称基础表；上市日若无法取得则留空。"""
    rows = []
    if name_map:
        for code, name in name_map.items():
            try:
                code = normalize_code(code)
            except ValueError:
                continue
            rows.append({
                "代码": code,
                "名称": str(name or ""),
                "ST": is_st_name(name),
                "上市日": None,
            })
    else:
        try:
            import akshare as ak
            spot = ak.stock_zh_a_spot_tx()
            code_col = "code" if "code" in spot.columns else "代码"
            name_col = "name" if "name" in spot.columns else "名称"
            for _, row in spot.iterrows():
                try:
                    code = normalize_code(str(row[code_col]))
                except ValueError:
                    continue
                name = str(row[name_col] or "")
                rows.append({
                    "代码": code,
                    "名称": name,
                    "ST": is_st_name(name),
                    "上市日": None,
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning("股票状态刷新失败: %s", exc)
            return {"ok": False, "message": str(exc), "rows": 0}

    if not rows:
        return {"ok": False, "message": "股票状态为空", "rows": 0}
    frame = pd.DataFrame(rows).drop_duplicates("代码", keep="last")
    # 尝试用 stock_info 缓存补名称
    info_path = PROJECT_DIR / "cache" / "stock_info.parquet"
    if info_path.exists():
        info = pd.read_parquet(info_path)
        if "代码" in info.columns and "名称" in info.columns:
            names = info.set_index("代码")["名称"].to_dict()
            frame["名称"] = frame.apply(
                lambda r: r["名称"] or names.get(r["代码"], r["名称"]), axis=1
            )
            frame["ST"] = frame["名称"].map(is_st_name)
    atomic_write_parquet(frame, str(STATUS_CACHE))
    return {
        "ok": True,
        "message": f"股票状态 {len(frame)} 只（ST {int(frame['ST'].sum())}）",
        "rows": len(frame),
        "st_count": int(frame["ST"].sum()),
    }


def load_stock_status(code: Optional[str] = None) -> pd.DataFrame:
    if not STATUS_CACHE.exists():
        return pd.DataFrame(columns=["代码", "名称", "ST", "上市日"])
    frame = pd.read_parquet(STATUS_CACHE)
    if code:
        frame = frame[frame["代码"] == normalize_code(code)]
    return frame.reset_index(drop=True)


def lookup_status(code: str) -> dict:
    frame = load_stock_status(code)
    if frame.empty:
        return {"代码": normalize_code(code), "名称": "", "ST": False, "上市日": None}
    row = frame.iloc[0]
    return {
        "代码": row["代码"],
        "名称": row["名称"],
        "ST": bool(row["ST"]),
        "上市日": None if pd.isna(row.get("上市日")) else str(row["上市日"]),
    }
