# ---- 管线自注册 ----
PIPELINE_META = {"id": "continuity", "title": "K线连续性", "kwargs": {}}

"""
K线连续性选股 — 基于本地缓存

条件：
1. 仅沪深主板（000/001/002/003/600/601/603/605），排除创业板/科创板/北交所/ST
2. 近10日，每天最高价 > 前一天最低价 + 前一天收盘价×1%
3. 近10日日均成交额 >= 5000万

逻辑说明：
  不要求K线实际重叠——允许向上跳空（今天最低 > 昨天最高），
  只要今天最高价仍高于昨天最低价 1% 以上，就认为价格有连续性。
  这样可以保留"稳步上涨但日间有小缺口"的标的。

筛选出的标的：
  - 稳步攀升的慢牛（每日小阳线、偶有跳空）
  - 横盘震荡的平台（每日重叠）
  - 上升通道中的回调再上涨

使用示例
--------
# 命令行
$ python stock_select.py

# 代码调用
>>> from data.kline import StockData
>>> from stock_select import check_continuity, analyze

>>> data = StockData()
>>> data.update()

# 分析单只
>>> result = analyze("sh600519", data)
>>> print(result["最小接续区%"], result["平均重叠率%"])

输出文件
--------
kline_overlap_10d.csv  — 按最小接续区%降序排列

依赖：stock_data.py（数据层）
"""

import pandas as pd
from typing import Dict, Optional, Any
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed

from data.kline import StockData


# ======================
# 参数
# ======================

LOOKBACK = 10
MIN_AMOUNT = 5000   # 万元，日均成交额下限
MIN_GAP_PCT = 1.5    # 最小接续区%（今日最高 > 昨日最低 + 收盘价×1.5%）
STRICT = False       # True=严格连续（K线必须重叠，不允许跳空）
THREADS = 12


# ======================
# 核心逻辑
# ======================

def check_continuity(df: pd.DataFrame) -> bool:
    """
    strict=False: 今日最高 > 昨日最低 + 昨日收盘 × min_gap_pct%（允许跳空上涨）
    strict=True:  在上面的基础上，还要求 K线必须重叠（今日最低 ≤ 昨日最高）
    """
    for i in range(1, len(df)):
        y_high = df.iloc[i - 1]["最高"]
        y_low = df.iloc[i - 1]["最低"]
        y_close = df.iloc[i - 1]["收盘"]
        t_high = df.iloc[i]["最高"]
        t_low = df.iloc[i]["最低"]

        # 接续性：今日最高 > 昨日最低 + 昨日收盘 × min_gap_pct%
        if t_high <= y_low + y_close * MIN_GAP_PCT / 100:
            return False

        # 严格模式：K线必须重叠，不允许跳空
        if STRICT and t_low > y_high:
            return False

    return True


def calc_metrics(df: pd.DataFrame) -> Dict[str, float]:
    """计算单只股票的全部指标"""

    # ---- 最小接续区（每日 high - prev_low 相对 prev_close 的最小比例） ----
    gaps = []
    for i in range(1, len(df)):
        y_low = df.iloc[i - 1]["最低"]
        y_close = df.iloc[i - 1]["收盘"]
        t_high = df.iloc[i]["最高"]
        if y_close > 0:
            gaps.append((t_high - y_low) / y_close * 100)

    min_gap = min(gaps) if gaps else 0

    # ---- 平均重叠率（仅统计有重叠的相邻日） ----
    overlap_rates = []
    for i in range(1, len(df)):
        h1, l1 = df.iloc[i - 1]["最高"], df.iloc[i - 1]["最低"]
        h2, l2 = df.iloc[i]["最高"], df.iloc[i]["最低"]

        oh = min(h1, h2)
        ol = max(l1, l2)
        uh = max(h1, h2)
        ul = min(l1, l2)

        denom = uh - ul
        if denom > 0 and oh > ol:        # 有重叠才统计
            overlap_rates.append((oh - ol) / denom)

    avg_overlap = (sum(overlap_rates) / len(overlap_rates) * 100
                   if overlap_rates else 0)

    # ---- 振幅、区间位置 ----
    high = df["最高"].max()
    low = df["最低"].min()
    close = df.iloc[-1]["收盘"]

    amplitude = (high - low) / low * 100
    position = (close - low) / (high - low) * 100 if high != low else 50

    return {
        "最新价": round(close, 2),
        "10日最高": high,
        "10日最低": low,
        "10日振幅%": round(amplitude, 2),
        "区间位置%": round(position, 1),
        "最小接续区%": round(min_gap, 2),
        "平均重叠率%": round(avg_overlap, 2),
    }


def analyze(code: str, data: StockData) -> Optional[Dict[str, Any]]:
    """分析单只股票，返回结果字典或 None"""
    df = data.get_kline(code, days=LOOKBACK)
    if len(df) < LOOKBACK:
        return None

    if not check_continuity(df):
        return None

    avg_amount = df["成交额"].mean() / 10000
    if avg_amount < MIN_AMOUNT:
        return None

    return {
        "代码": code,
        "名称": data.get_stock_name(code),
        **calc_metrics(df),
        "10日平均成交额(万元)": round(avg_amount, 0),
    }


# ======================
# 管线接口（供 stock_pipeline.py 调用）
# ======================

def find_all(data: StockData, **kwargs) -> pd.DataFrame:
    """统一接口：传入 StockData，返回筛选结果 DataFrame。kwargs 可覆盖全局参数"""
    # 接受配置文件覆盖
    global MIN_GAP_PCT, MIN_AMOUNT, LOOKBACK, STRICT
    MIN_GAP_PCT = kwargs.pop("min_gap_pct", MIN_GAP_PCT)
    MIN_AMOUNT = kwargs.pop("min_amount", MIN_AMOUNT)
    LOOKBACK = kwargs.pop("lookback", LOOKBACK)
    STRICT = kwargs.pop("strict", STRICT)
    tqdm_kwargs = kwargs.pop("_tqdm_kwargs", {})

    main_codes = set(data.cache["代码"].unique())

    # ── 性能优化：一次过滤替代逐只全表扫描 ──
    # 原来：每只股票调用 get_kline() → cache["代码"]==code 扫描全部 1170 万行
    #       3000 只 × 1170 万 = 350 亿次比较
    # 现在：预取最近 N×2 个交易日，日期+代码双重过滤只做 2 次扫描
    all_dates = sorted(data.cache["日期"].unique())
    recent_dates = all_dates[-max(LOOKBACK * 2, 20):]
    cache = data.cache
    mask = cache["日期"].isin(recent_dates) & cache["代码"].isin(main_codes)
    recent = cache[mask]

    # 预先构建代码→名称映射
    all_codes = list(main_codes)
    name_map = {c: data.get_stock_name(c) for c in all_codes}

    results = []
    for code, group in tqdm(recent.groupby("代码"), desc="连续性", **tqdm_kwargs):
        group = group.sort_values("日期").tail(LOOKBACK)
        if len(group) < LOOKBACK:
            continue
        if not check_continuity(group):
            continue
        avg_amount = group["成交额"].mean() / 10000
        if avg_amount < MIN_AMOUNT:
            continue
        results.append({
            "代码": code,
            "名称": name_map.get(code, ""),
            **calc_metrics(group),
            "10日平均成交额(万元)": round(avg_amount, 0),
        })

    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results)


# ======================
# 主程序
# ======================

def main():
    import argparse
    from screen.engine import Screener, add_sort_args, apply_screen_args

    parser = argparse.ArgumentParser(description="K线连续性选股")
    parser.add_argument("--no-update", action="store_true", help="跳过数据更新")
    add_sort_args(parser)
    args = parser.parse_args()

    print("=" * 60)
    print(f"K线连续性选股（近{LOOKBACK}日，最小接续区>{MIN_GAP_PCT}%）")
    print("条件：每天最高价 > 前一天最低价 + 前一天收盘价 × 1%")
    print("=" * 60)

    data = StockData()
    stocks = data.get_stock_list(board="main")
    if not args.no_update:
        data.update(stocks)
    print(f"\n股票池: {len(stocks)} 只")

    print("\n开始筛选...")
    results = []

    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        tasks = {
            executor.submit(analyze, row["代码"], data): row["代码"]
            for _, row in stocks.iterrows()
        }
        for future in tqdm(as_completed(tasks), total=len(tasks)):
            try:
                r = future.result()
                if r:
                    results.append(r)
            except Exception:
                pass

    if not results:
        print("没有找到符合条件股票")
        return

    result = pd.DataFrame(results)

    # ---- 管道：关联行业 → 过滤 → 排序 → 输出 ----
    screener = Screener(result)
    apply_screen_args(screener, args)
    # 默认排序
    if not args.sort:
        screener.sort(["最小接续区%", "10日振幅%", "10日平均成交额(万元)"],
                      ascending=[False, True, False])

    final = screener.to_df()
    out_file = args.out or "kline_overlap_10d.csv"
    screener.to_csv(out_file)

    print(f"\n筛选完成: {len(final)} 只 → {out_file}")
    print()

    print_cols = args.print_cols.split(",") if args.print_cols else None
    screener.print(n=args.head, cols=print_cols)


if __name__ == "__main__":
    main()
