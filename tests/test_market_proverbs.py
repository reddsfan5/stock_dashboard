import unittest

import numpy as np
import pandas as pd

from backtest.proverb_report import build_proverb_report
from backtest.proverbs import (
    PROVERB_SPECS,
    ProverbBacktestConfig,
    build_signal_masks,
    prepare_proverb_panel,
    run_proverb_backtest,
)


def _stock_frame(code="sh600001", periods=45):
    dates = pd.bdate_range("2025-01-02", periods=periods)
    close = 10 * np.cumprod(np.r_[1.0, np.repeat(1.01, periods - 1)])
    previous = np.r_[close[0], close[:-1]]
    return pd.DataFrame({
        "代码": code,
        "日期": dates,
        "开盘": previous,
        "最高": close * 1.005,
        "最低": np.minimum(previous, close) * 0.995,
        "收盘": close,
        "前收": previous,
        "成交额": 100_000_000.0,
    })


def _index_frame(dates):
    rows = []
    for code, step in (("sh000001", 1.001), ("sz399001", 1.002), ("sh000300", 1.0015)):
        close = 3000 * np.cumprod(np.r_[1.0, np.repeat(step, len(dates) - 1)])
        rows.append(pd.DataFrame({
            "代码": code,
            "日期": dates,
            "开盘": np.r_[close[0], close[:-1]],
            "收盘": close,
        }))
    return pd.concat(rows, ignore_index=True)


class MarketProverbBacktestTests(unittest.TestCase):
    def test_defines_exactly_seven_items(self):
        self.assertEqual([spec.id for spec in PROVERB_SPECS], [f"p{i:02d}" for i in range(1, 8)])
        self.assertTrue(all(spec.formula and spec.target_label for spec in PROVERB_SPECS))

    def test_future_return_starts_at_next_open_and_aligns_benchmark(self):
        stock = _stock_frame(periods=30)
        index = _index_frame(stock["日期"])
        config = ProverbBacktestConfig(
            start_date="2025-01-02", liquidity_window=1, horizons=(1, 3, 5)
        )
        panel, diagnostics = prepare_proverb_panel(stock, index, config)
        row = panel.iloc[5]
        expected_stock = stock.iloc[8]["收盘"] / stock.iloc[6]["开盘"] * 100 - 100
        sh = index[index["代码"] == "sh000001"].reset_index(drop=True)
        expected_benchmark = sh.iloc[8]["收盘"] / sh.iloc[6]["开盘"] * 100 - 100
        self.assertAlmostEqual(row["未来收益3%"], expected_stock)
        self.assertAlmostEqual(row["基准收益3%"], expected_benchmark)
        self.assertAlmostEqual(row["超额收益3%"], expected_stock - expected_benchmark)
        self.assertEqual(row["入场日"], stock.iloc[6]["日期"])
        self.assertEqual(row["退出日3"], stock.iloc[8]["日期"])
        # 指数首日没有前一日，涨跌幅自然为空；其余日期应完整匹配。
        self.assertGreater(diagnostics["market_match_rate"], 0.95)

    def test_steady_rise_signal_uses_known_history_and_deduplicates_episode(self):
        stock = _stock_frame(periods=20)
        index = _index_frame(stock["日期"])
        config = ProverbBacktestConfig(
            start_date="2025-01-02", liquidity_window=1,
            steady_rise_days=5, steady_rise_daily_max_pct=1.5,
            steady_rise_total_min_pct=2.0,
        )
        panel, _ = prepare_proverb_panel(stock, index, config)
        signals, _ = build_signal_masks(panel, config)
        hits = panel.loc[signals["p01"], "日期"].tolist()
        self.assertEqual(hits, [stock.iloc[5]["日期"]])

        # 改变信号日之后的价格，不应反向改变当日信号。
        changed = stock.copy()
        changed.loc[changed.index > 5, ["开盘", "最高", "最低", "收盘"]] *= 1.8
        changed_panel, _ = prepare_proverb_panel(changed, index, config)
        changed_signals, _ = build_signal_masks(changed_panel, config)
        changed_hits = changed_panel.loc[changed_signals["p01"], "日期"].tolist()
        self.assertEqual(changed_hits[0], stock.iloc[5]["日期"])

    def test_report_exposes_seven_separate_tabs(self):
        stock = _stock_frame(periods=45)
        index = _index_frame(stock["日期"])
        result = run_proverb_backtest(
            stock,
            index,
            ProverbBacktestConfig(start_date="2025-01-02", liquidity_window=1),
        )
        html = build_proverb_report(result)
        for number in range(1, 8):
            self.assertIn(f"panel-p{number:02d}", html)
        self.assertIn("下一交易日开盘", html)
        self.assertIn("95%", html)


if __name__ == "__main__":
    unittest.main()
