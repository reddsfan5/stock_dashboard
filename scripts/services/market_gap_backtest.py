"""指数开盘缺口回测交互页与只读查询门面。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from backtest.market_gap import MarketGapConfig, run_market_gap_study
from data.index import CACHE_FILE, INDEXES


PROJECT_DIR = Path(__file__).resolve().parents[2]
TEMPLATE = Path(__file__).resolve().parent / "templates" / "market_gap_backtest.html"
OUT_HTML = PROJECT_DIR / "output" / "market_gap_backtest.html"


def query(params: dict) -> dict:
    code = str(params.get("code", ["sh000001"])[0]).strip().lower()
    if code not in INDEXES:
        raise ValueError("仅支持项目宽基指数：" + "、".join(INDEXES))
    start = str(params.get("start", ["2020-01-01"])[0]).strip()
    end = str(params.get("end", [""])[0]).strip() or None
    try:
        threshold = float(params.get("threshold", ["0.7"])[0])
        horizon = int(params.get("horizon", ["3"])[0])
        pd.Timestamp(start)
        if end:
            pd.Timestamp(end)
    except (TypeError, ValueError):
        raise ValueError("日期、阈值或持有窗口格式不正确") from None
    try:
        daily = pd.read_parquet(
            CACHE_FILE,
            columns=["代码", "日期", "开盘", "最高", "最低", "收盘", "来源"],
            filters=[("代码", "==", code)],
        )
    except FileNotFoundError:
        raise LookupError("指数日线缓存不存在，请先更新每日数据") from None
    if daily.empty:
        raise LookupError(f"指数缓存中没有 {code}（{INDEXES[code]}）")
    result = run_market_gap_study(daily, MarketGapConfig(
        threshold_pct=threshold, horizon=horizon, start_date=start, end_date=end,
    ))
    result["instrument"] = {"code": code, "name": INDEXES[code]}
    return result


def write_app(path: str | Path = OUT_HTML) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    return target


if __name__ == "__main__":
    print(f"✓ 页面: {write_app()}")
