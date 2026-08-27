#!/usr/bin/env python3
"""
绩效分析报告 — 基于 quantstats

从策略导出的权益/交易 CSV 生成机构级绩效页（Sharpe/Sortino/回撤/收益曲线），
并可选用本地 ETF 日K构造基准（离线可用，不走 yfinance）。

依赖: pip install quantstats（见 demos/requirements.txt）

用法
----
$ python scripts/quant_report.py --equity output/etf_momentum_equity.csv
$ python scripts/quant_report.py --equity output/etf_momentum_equity.csv \\
    --trades output/etf_momentum_trades.csv --benchmark sh510300
$ python scripts/quant_report.py --equity ... --out output/quant_report.html

CSV 来源: scripts/strategy_08_etf_momentum.py 已自动导出
equity CSV 列: date,equity,cash,positions（backtest/sim_types.EquityPoint）
trades CSV 列: code,name,buy_date,...,pnl,filled（backtest/sim_types.Trade）
"""

import argparse
import os
import sys

import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

try:
    import quantstats as qs
except ImportError as e:
    sys.exit(f"quantstats 导入失败: {e}\n"
             f"安装: pip install quantstats IPython")

# quantstats 图内嵌中文标题，macOS 默认字体缺 CJK 字形
import matplotlib
matplotlib.rcParams["font.sans-serif"] = ["Arial Unicode MS", "PingFang SC", "Hiragino Sans GB"]
matplotlib.rcParams["axes.unicode_minus"] = False


# ====================================================================
# 数据构造
# ====================================================================

def build_returns(equity_df: pd.DataFrame) -> pd.Series:
    """
    权益点 → 日频收益率序列。

    策略权益是换仓日记录的点（如月度），按工作日重采样并 ffill：
    换仓日之间权益恒平的阶梯近似——quantstats 日频假设下的标准会计处理。
    """
    eq = equity_df.copy()
    # 兼容英文列名（dataclasses.asdict 序列化输出 date/equity）
    if "日期" not in eq.columns and "date" in eq.columns:
        eq = eq.rename(columns={"date": "日期"})
    eq["日期"] = pd.to_datetime(eq["日期"])
    eq = eq.sort_values("日期").set_index("日期")["equity"].astype(float)
    daily = eq.reindex(pd.bdate_range(eq.index.min(), eq.index.max())).ffill()
    return daily.pct_change(fill_method=None).dropna()


def build_benchmark(code: str, date_index: pd.DatetimeIndex) -> pd.Series:
    """本地 ETF 日K → 对齐策略日频索引的基准收益率（离线）"""
    from data.etf import ETFData
    df = ETFData().get_kline(code, days=10 ** 6)
    if len(df) == 0:
        print(f"✗ 基准 {code} 无数据，跳过基准")
        return None
    px = df.sort_values("日期").set_index("日期")["收盘"].astype(float)
    daily = px.reindex(date_index).ffill()
    return daily.pct_change(fill_method=None).dropna()


def print_trade_stats(trades_df: pd.DataFrame):
    """终端打印交易维度统计（胜率/盈亏比）"""
    wins = trades_df[trades_df["pnl"] > 0]
    losses = trades_df[trades_df["pnl"] <= 0]
    win_rate = len(wins) / len(trades_df) * 100 if len(trades_df) else 0
    avg_win = wins["pnl"].mean() if len(wins) else 0
    avg_loss = losses["pnl"].mean() if len(losses) else 0
    plr = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")
    print(f"\n===== 交易统计 ({len(trades_df)} 笔) =====")
    print(f"胜率: {win_rate:.1f}%   平均盈利: ¥{avg_win:+.0f}   平均亏损: ¥{avg_loss:+.0f}")
    print(f"盈亏比: {plr:.2f}   总盈亏: ¥{trades_df['pnl'].sum():+,.0f}")


# ====================================================================
# 主流程
# ====================================================================

def main():
    parser = argparse.ArgumentParser(description="quantstats 绩效报告")
    parser.add_argument("--equity", required=True, help="权益 CSV（EquityPoint 序列化）")
    parser.add_argument("--trades", default=None, help="交易 CSV（可选，补打胜率/盈亏比）")
    parser.add_argument("--out", default=os.path.join(PROJECT_DIR, "output", "quant_report.html"),
                        help="输出 HTML 路径")
    parser.add_argument("--benchmark", default="sh510300",
                        help="基准 ETF 代码（本地缓存），none 则不使用基准")
    parser.add_argument("--title", default="策略绩效报告")
    args = parser.parse_args()

    if not os.path.exists(args.equity):
        sys.exit(f"✗ 权益 CSV 不存在: {args.equity}\n"
                 f"  先跑 python scripts/strategy_08_etf_momentum.py 生成")

    returns = build_returns(pd.read_csv(args.equity))

    benchmark = None
    if args.benchmark != "none":
        benchmark = build_benchmark(args.benchmark, returns.index)

    # 终端统计
    print(f"===== 绩效指标（日频, {returns.index[0].date()} ~ {returns.index[-1].date()}）=====")
    print(f"Sharpe: {qs.stats.sharpe(returns):.2f}    "
          f"Sortino: {qs.stats.sortino(returns):.2f}")
    print(f"CAGR: {qs.stats.cagr(returns):.2%}     "
          f"最大回撤: {qs.stats.max_drawdown(returns):.2%}")
    print(f"年化波动: {qs.stats.volatility(returns):.2%}     "
          f"最佳日: {qs.stats.best(returns):.2%} / 最差日: {qs.stats.worst(returns):.2%}")

    if args.trades and os.path.exists(args.trades):
        print_trade_stats(pd.read_csv(args.trades))

    # HTML 报告（显式传 benchmark，避免 quantstats 默认走 yfinance 拉 SPY）
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    qs.reports.html(
        returns, benchmark=benchmark, output=args.out,
        title=args.title, compounded=True,
    )
    print(f"\n✓ 报告: {args.out}")


if __name__ == "__main__":
    main()
