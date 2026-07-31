# ---- 管线自注册 ----
PIPELINE_META = {"id": "hammer", "title": "金针探底", "kwargs": {}}

"""
金针探底选股 — 近三日出现长下影线探底形态

形态定义：
  - 下影线 ≥ 实体 × 2（经典锤子线）
  - 实体较小（实体/总振幅 < 30%）
  - 出现在近三日
  - 优先收阳（收盘 ≥ 开盘）

评分维度：
  - 下影线/实体比：越大越好
  - 探底深度：最低价在10日区间越低越好
  - 成交量放大：金针日成交 > 前日成交
  - 后续确认：金针日后有上涨
"""

import numpy as np
import pandas as pd
from typing import Dict, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

from stock_data import StockData


# ======================
# 参数
# ======================

MIN_SHADOW_RATIO = 3.0   # 下影线/实体 最小倍数（越大形态越标准）
MAX_BODY_RATIO = 0.20    # 实体/总振幅 最大比例（越小实体越小）
MAX_BOTTOM_POS = 20      # 金针最低价必须在近期区间的底部 N% 以内（越小越严格）
LOOKBACK = 10             # 回看天数（判断探底位置）
HAMMER_DAYS = 3           # 近 N 天内出现即可
MIN_AMOUNT = 5000         # 日均成交额下限（万元）
THREADS = 12


# ======================
# 形态检测
# ======================

def is_hammer(open_p: float, close_p: float, high: float, low: float) -> tuple:
    """
    判断单根 K 线是否为金针探底形态

    Returns:
        (is_hammer: bool, shadow_ratio: float, body_ratio: float, is_yang: bool)
    """
    body = abs(close_p - open_p)
    total_range = high - low
    if total_range == 0 or body == 0:
        return False, 0, 0, False

    lower_shadow = min(open_p, close_p) - low    # 下影线
    upper_shadow = high - max(open_p, close_p)    # 上影线
    is_yang = close_p >= open_p

    # 下影线 ≥ 实体 × 2
    shadow_ratio = lower_shadow / body if body > 0 else 99
    # 实体 / 总振幅 < 30%
    body_ratio = body / total_range

    ok = (
        shadow_ratio >= MIN_SHADOW_RATIO
        and body_ratio <= MAX_BODY_RATIO
        and lower_shadow >= upper_shadow       # 下影线 > 上影线
    )
    return ok, round(shadow_ratio, 1), round(body_ratio, 2), is_yang


def analyze_hammer(code: str, data: StockData) -> Optional[Dict[str, Any]]:
    """检测单只股票近三日是否有金针探底"""
    df = data.get_kline(code, days=LOOKBACK)
    if len(df) < LOOKBACK:
        return None

    closes = df["收盘"].values
    # 旧缓存无开盘列，用前收近似
    has_open = "开盘" in df.columns
    if has_open:
        opens = df["开盘"].values
    else:
        opens = np.concatenate([[closes[0]], closes[:-1]])  # T日开盘≈T-1收盘
    highs = df["最高"].values
    lows = df["最低"].values
    amounts = df["成交额"].values

    # 查找最近 HAMMER_DAYS 天内最好的金针
    best = None
    for i in range(max(0, len(df) - HAMMER_DAYS), len(df)):
        o = opens[i] if opens[i] > 0 else closes[i - 1] if i > 0 else closes[i]
        c = closes[i]
        h = highs[i]
        l = lows[i]

        ok, shadow_ratio, body_ratio, is_yang = is_hammer(o, c, h, l)
        if not ok:
            continue

        # 成交量比前一日放大
        vol_ratio = amounts[i] / amounts[i - 1] if i > 0 and amounts[i - 1] > 0 else 1

        # 探底深度：最低价在10日区间中的位置（0=区间最低点, 100=最高点）
        range_high = highs.max()
        range_low = lows.min()
        bottom_pos = (l - range_low) / (range_high - range_low) * 100 if range_high != range_low else 50

        # 硬过滤：必须处于区间底部
        if bottom_pos > MAX_BOTTOM_POS:
            continue

        # 后续确认：金针日后一天是否上涨
        follow_up = 0
        if i < len(df) - 1:
            follow_up = (closes[i + 1] - c) / c * 100

        # 综合评分
        score = (
            min(shadow_ratio / 5, 1) * 35      # 下影线比例 35%
            + (1 - bottom_pos / 100) * 25       # 探底深度 25%
            + (1 if is_yang else 0) * 15        # 收阳加分 15%
            + min(vol_ratio / 2, 1) * 10        # 放量加分 10%
            + (1 if follow_up > 0 else 0) * 15  # 后续确认 15%
        )

        if best is None or score > best["综合评分"]:
            best = {
                "代码": code,
                "名称": data.get_stock_name(code),
                "金针日": i - len(df),  # 相对位置（-3=三天前, -1=昨天）
                "开盘": round(o, 2),
                "收盘": round(c, 2),
                "最高": round(h, 2),
                "最低": round(l, 2),
                "下影/实体": shadow_ratio,
                "实体/振幅": body_ratio,
                "收阳": is_yang,
                "探底位置%": round(bottom_pos, 1),
                "放量比": round(vol_ratio, 2),
                "后续涨%": round(follow_up, 2),
                "综合评分": round(score * 100, 1),
                "日均成交额(万)": round(amounts.mean() / 10000, 0),
            }

    if best is None:
        return None
    if best["日均成交额(万)"] < MIN_AMOUNT:
        return None

    return best


# ======================
# 管线接口
# ======================

def find_all(data: StockData, **kwargs) -> pd.DataFrame:
    """统一接口。kwargs: min_shadow_ratio, hammer_days, max_bottom_pos, min_amount"""
    global MIN_SHADOW_RATIO, HAMMER_DAYS, MAX_BOTTOM_POS, MIN_AMOUNT
    MIN_SHADOW_RATIO = kwargs.get("min_shadow_ratio", MIN_SHADOW_RATIO)
    HAMMER_DAYS = kwargs.get("hammer_days", HAMMER_DAYS)
    MAX_BOTTOM_POS = kwargs.get("max_bottom_pos", MAX_BOTTOM_POS)
    MIN_AMOUNT = kwargs.get("min_amount", MIN_AMOUNT)

    stocks = data.get_stock_list(board="main")
    results = []

    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        tasks = {
            executor.submit(analyze_hammer, row["代码"], data): row["代码"]
            for _, row in stocks.iterrows()
        }
        for future in tqdm(as_completed(tasks), total=len(tasks), desc="金针探底"):
            try:
                r = future.result()
                if r:
                    results.append(r)
            except Exception:
                pass

    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    return df.sort_values("综合评分", ascending=False).reset_index(drop=True)


# ======================
# CLI
# ======================

def main():
    import argparse
    from stock_screen import Screener, add_sort_args, apply_screen_args

    parser = argparse.ArgumentParser(description="金针探底选股")
    parser.add_argument("--no-update", action="store_true")
    add_sort_args(parser)
    args = parser.parse_args()

    data = StockData()
    if not args.no_update:
        data.update(data.get_stock_list(board="main"))

    result = find_all(data)
    if len(result) == 0:
        print("没有找到符合条件的股票")
        return

    screener = Screener(result)
    apply_screen_args(screener, args)
    if not args.sort:
        screener.sort("综合评分", ascending=False)

    out_file = args.out or "hammer_stocks.csv"
    screener.to_csv(out_file)
    print(f"\n金针探底: {screener.count()} 只 → {out_file}")
    print_cols = args.print_cols.split(",") if args.print_cols else None
    screener.print(n=args.head, cols=print_cols)


if __name__ == "__main__":
    main()
