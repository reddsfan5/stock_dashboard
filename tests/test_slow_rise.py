import unittest

import numpy as np
import pandas as pd

from backtest.execution import ExecutionConfig
from backtest.slow_rise import (
    SlowRiseConfig,
    build_slow_rise_signals,
    prepare_slow_rise_panel,
    run_slow_rise_backtest,
)
from backtest.slow_rise_report import build_slow_rise_report


def _frame(periods=30, rise_start=1):
    dates = pd.bdate_range("2025-01-02", periods=periods)
    returns = np.zeros(periods)
    returns[rise_start:rise_start + 6] = [0.5, 0.4, 0.3, 0.3, 0.3, -0.2]
    close = np.empty(periods)
    close[0] = 100
    for index in range(1, periods):
        close[index] = close[index - 1] * (1 + returns[index] / 100)
    previous = np.r_[close[0], close[:-1]]
    return pd.DataFrame({
        "代码": "sh600001", "日期": dates, "开盘": previous,
        "最高": np.maximum(previous, close) * 1.002,
        "最低": np.minimum(previous, close) * 0.998,
        "收盘": close, "前收": previous, "成交额": 100_000_000.0,
    })


def _config(**kwargs):
    params = dict(
        start_date="2025-01-02", liquidity_window=1,
        max_relative_position_pct=None,
        execution=ExecutionConfig(commission_rate=0, stamp_tax_rate=0, slippage_bps=0),
    )
    params.update(kwargs)
    return SlowRiseConfig(**params)


class SlowRiseBacktestTests(unittest.TestCase):
    def test_default_rule_enables_thirty_day_low_position_filter(self):
        config = SlowRiseConfig()
        self.assertEqual(config.relative_low_window, 30)
        self.assertEqual(config.max_relative_position_pct, 30.0)

    def test_builds_three_distinct_signal_variants_without_future_data(self):
        stock = _frame()
        config = _config()
        panel, _ = prepare_slow_rise_panel(stock, config)
        signals = build_slow_rise_signals(panel, config)
        self.assertEqual(set(signals), {3, 4, 5})
        self.assertEqual(panel.loc[signals[3], "日期"].iloc[0], stock.iloc[3]["日期"])
        self.assertEqual(panel.loc[signals[4], "日期"].iloc[0], stock.iloc[4]["日期"])
        self.assertEqual(panel.loc[signals[5], "日期"].iloc[0], stock.iloc[5]["日期"])

        changed = stock.copy()
        changed.loc[changed.index > 3, ["开盘", "最高", "最低", "收盘"]] *= 2
        changed_panel, _ = prepare_slow_rise_panel(changed, config)
        changed_signals = build_slow_rise_signals(changed_panel, config)
        self.assertEqual(changed_panel.loc[changed_signals[3], "日期"].iloc[0], stock.iloc[3]["日期"])

    def test_target_is_not_allowed_on_buy_day_but_fills_from_next_day(self):
        stock = _frame()
        # 以第5行为信号：第6行开盘买入；第6行即使冲高也不能卖，第7行才止盈。
        entry_open = float(stock.loc[6, "开盘"])
        stock.loc[6, "最高"] = entry_open * 1.10
        stock.loc[7, "最高"] = entry_open * 1.06
        stock.loc[7, "开盘"] = entry_open * 1.01
        panel, _ = prepare_slow_rise_panel(stock, _config())
        row = panel.iloc[5]
        self.assertEqual(row["持有天数"], 2)
        self.assertEqual(row["退出原因"], "止盈")
        self.assertAlmostEqual(row["卖出参考价"], entry_open * 1.05)
        self.assertEqual(row["卖出日期"], stock.loc[7, "日期"])

    def test_expiry_uses_fifth_day_close(self):
        stock = _frame()
        anchor = 10
        entry_open = float(stock.loc[anchor + 1, "开盘"])
        for day in range(anchor + 1, anchor + 6):
            stock.loc[day, "最高"] = entry_open * 1.02
        stock.loc[anchor + 5, "收盘"] = entry_open * 0.99
        panel, _ = prepare_slow_rise_panel(stock, _config())
        row = panel.iloc[anchor]
        self.assertEqual(row["持有天数"], 5)
        self.assertEqual(row["退出原因"], "第5日尾盘")
        self.assertAlmostEqual(row["卖出参考价"], entry_open * 0.99)

    def test_report_contains_execution_metrics_and_three_panels(self):
        result = run_slow_rise_backtest(_frame(45), _config())
        html = build_slow_rise_report(result)
        for n_days in (3, 4, 5):
            self.assertIn(f"panel-n{n_days}", html)
        self.assertIn("严格 T+1", html)
        self.assertIn("同日基线", html)
        self.assertIn("止盈率", html)

    def test_relative_low_filter_uses_only_trailing_known_prices(self):
        stock = _frame(50, rise_start=31)
        # 给信号日前的 30 日区间放入一个历史高点，使缓涨后的收盘仍位于区间下方。
        stock.loc[5, "最高"] = 130.0
        config = _config(max_relative_position_pct=30.0)
        panel, _ = prepare_slow_rise_panel(stock, config)
        signals = build_slow_rise_signals(panel, config)
        signal_index = panel.index[signals[3]][0]
        self.assertEqual(signal_index, 33)
        position = float(panel.loc[signal_index, "区间相对位置%"])
        self.assertLessEqual(position, 30.0)

        changed = stock.copy()
        changed.loc[changed.index > signal_index, ["最高", "最低"]] *= 2
        changed_panel, _ = prepare_slow_rise_panel(changed, config)
        self.assertAlmostEqual(
            float(changed_panel.loc[signal_index, "区间相对位置%"]), position
        )
        changed_signals = build_slow_rise_signals(changed_panel, config)
        self.assertTrue(bool(changed_signals[3].loc[signal_index]))

    def test_report_compares_low_filter_with_unfiltered_rule(self):
        stock = _frame(50, rise_start=31)
        stock.loc[5, "最高"] = 130.0
        result = run_slow_rise_backtest(
            stock, _config(max_relative_position_pct=30.0)
        )
        unfiltered = run_slow_rise_backtest(stock, _config())
        result["unfiltered"] = unfiltered["summary"]
        html = build_slow_rise_report(result)
        self.assertIn("30日相对低位过滤有没有改善", html)
        self.assertIn("过滤前交易", html)
        self.assertIn("相同低位与流动性门槛", html)


if __name__ == "__main__":
    unittest.main()
