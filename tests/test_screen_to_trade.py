"""选股→交易回测的合成样本单测（unittest）。"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from backtest.execution import ExecutionConfig
from backtest.screen_to_trade import (
    ScreenToTradeConfig,
    build_equity_curve,
    buy_hold_index_baseline,
    industry_neutral_placeholder,
    link_trades,
    prepare_panel,
    random_pick_baseline,
    regime_slices,
    run_screen_to_trade,
    scan_signals,
    split_in_out_sample,
)


def _dates(n: int, start: str = "2024-01-02"):
    start_ts = pd.Timestamp(start)
    out = []
    cur = start_ts
    while len(out) < n:
        if cur.weekday() < 5:
            out.append(cur)
        cur += timedelta(days=1)
    return out


def _make_stock_frame(codes=None, n_days=80):
    codes = codes or ["sh600001", "sh600002", "sz000001", "sz000002"]
    dates = _dates(n_days)
    rows = []
    rng = np.random.default_rng(7)
    for code in codes:
        price = 10.0 + rng.random()
        for i, date in enumerate(dates):
            # 让前半段偏弱、后半段造出若干锤子线与连续形态
            if code == "sh600001" and 40 <= i <= 42:
                open_p = price
                low = price * 0.92
                high = price * 1.01
                close = price * 1.005
            elif code == "sh600002" and i >= 30:
                # 小幅连涨、高重叠，便于 continuity/sideways
                open_p = price
                close = price * 1.003
                high = max(open_p, close) * 1.002
                low = min(open_p, close) * 0.998
            else:
                ret = float(rng.normal(0, 0.01))
                open_p = price
                close = price * (1 + ret)
                high = max(open_p, close) * (1 + abs(float(rng.normal(0, 0.005))))
                low = min(open_p, close) * (1 - abs(float(rng.normal(0, 0.005))))
            amount = 80_000_000 + float(rng.random()) * 20_000_000
            rows.append({
                "代码": code,
                "日期": date,
                "开盘": round(open_p, 3),
                "最高": round(high, 3),
                "最低": round(low, 3),
                "收盘": round(close, 3),
                "前收": round(price, 3),
                "成交额": amount,
            })
            price = close
    return pd.DataFrame(rows)


def _make_index_frame(n_days=80):
    dates = _dates(n_days)
    rows = []
    price = 3000.0
    for i, date in enumerate(dates):
        price *= 1.001 if i % 7 else 0.999
        rows.append({
            "代码": "sh000300",
            "日期": date,
            "开盘": price,
            "最高": price * 1.01,
            "最低": price * 0.99,
            "收盘": price,
            "成交量(手)": 1e8,
        })
    return pd.DataFrame(rows)


class ScreenToTradeTest(unittest.TestCase):
    def setUp(self):
        self.stocks = _make_stock_frame()
        self.index = _make_index_frame()
        self.config = ScreenToTradeConfig(
            module="hammer",
            start_date="2024-01-15",
            end_date=None,
            hold_days=3,
            max_signals_per_day=5,
            min_avg_amount=10_000_000,
            validation_ratio=0.3,
            embargo_size=1,
            random_seed=1,
            capital=50_000,
            notional_per_trade=5_000,
            execution=ExecutionConfig(
                commission_rate=0.0003,
                stamp_tax_rate=0.0005,
                slippage_bps=2.0,
            ),
        )

    def test_prepare_panel_has_future_path_without_mutating_input(self):
        original_cols = set(self.stocks.columns)
        panel = prepare_panel(self.stocks, self.config)
        self.assertEqual(set(self.stocks.columns), original_cols)
        self.assertIn("未来1日开盘", panel.columns)
        self.assertIn(f"未来{self.config.hold_days}日收盘", panel.columns)
        self.assertTrue(panel["流动性合格"].any())

    def test_hammer_signals_do_not_use_next_day_return_for_entry(self):
        panel = prepare_panel(self.stocks, self.config)
        signals = scan_signals(panel, self.config)
        # 信号列不得包含“后续涨%”一类前视字段
        self.assertNotIn("后续涨%", signals.columns)
        trades = link_trades(signals, panel, self.config)
        if not trades.empty:
            # 入场日必须严格晚于信号日
            ok = trades.dropna(subset=["入场日"])
            self.assertTrue((ok["入场日"] > ok["信号日"]).all())

    def test_link_trades_applies_costs(self):
        panel = prepare_panel(self.stocks, self.config)
        # 强制造一条可成交信号
        sample = panel.loc[panel["研究期"]].iloc[10:11][["代码", "日期"]].copy()
        sample = sample.rename(columns={"日期": "信号日"})
        sample["信号排名"] = 1
        # 附带收盘等字段供受阻判断
        merged = sample.merge(
            panel[["代码", "日期", "收盘"]],
            left_on=["代码", "信号日"], right_on=["代码", "日期"], how="left",
        ).drop(columns=["日期"])
        trades = link_trades(merged, panel, self.config)
        self.assertEqual(len(trades), 1)
        if not trades.iloc[0]["受阻"]:
            self.assertTrue(np.isfinite(trades.iloc[0]["净收益%"]))
            # 有成本时净收益应不同于裸价差
            buy = trades.iloc[0]["买入参考价"]
            sell = trades.iloc[0]["卖出参考价"]
            raw = (sell / buy - 1.0) * 100
            self.assertNotAlmostEqual(trades.iloc[0]["净收益%"], raw, places=4)

    def test_baselines_and_report_card_run_end_to_end(self):
        result = run_screen_to_trade(self.stocks, self.index, self.config)
        self.assertIn("report_card", result)
        card = result["report_card"]
        self.assertIn("event_stats", card)
        self.assertIn("portfolio_metrics", card)
        self.assertIn("baselines", card)
        self.assertTrue(any(b.get("baseline") == "指数买入持有" for b in card["baselines"]))
        self.assertTrue(any(b.get("baseline") == "同日随机等权" for b in card["baselines"]))
        placeholder = industry_neutral_placeholder()
        self.assertFalse(placeholder["available"])

        bh = buy_hold_index_baseline(self.index, self.config)
        self.assertTrue(bh["available"])

    def test_random_pick_matches_signal_day_counts(self):
        panel = prepare_panel(self.stocks, self.config)
        signals = scan_signals(panel, self.config)
        trades = link_trades(signals, panel, self.config)
        filled = trades.loc[~trades["受阻"]] if not trades.empty else trades
        if filled is None or filled.empty:
            self.skipTest("合成数据未产生可成交 hammer 信号")
        dates = sorted(filled["信号日"].unique())
        counts = filled.groupby("信号日").size().to_dict()
        baseline = random_pick_baseline(panel, dates, counts, self.config)
        self.assertTrue(baseline["available"])
        self.assertGreater(baseline["trades"], 0)

    def test_in_out_sample_split_respects_time_order(self):
        # 构造足够多的假交易跨越多个信号日
        dates = _dates(40, start="2024-03-01")
        rows = []
        for i, d in enumerate(dates):
            rows.append({
                "代码": "sh600001",
                "信号日": d,
                "入场日": d + timedelta(days=1),
                "退出日": d + timedelta(days=4),
                "受阻": False,
                "净收益%": 0.1 if i % 2 == 0 else -0.2,
            })
        trades = pd.DataFrame(rows)
        cfg = ScreenToTradeConfig(
            module="hammer", hold_days=3, validation_ratio=0.25, embargo_size=2,
            min_avg_amount=1, start_date="2024-03-01",
        )
        split_info = split_in_out_sample(trades, cfg)
        self.assertIsNotNone(split_info["split"])
        self.assertGreater(split_info["in_sample"]["trades"], 0)
        self.assertGreater(split_info["out_of_sample"]["trades"], 0)
        self.assertLess(
            split_info["split"]["train_end"],
            split_info["split"]["validation_start"],
        )

    def test_regime_slices_flag_weak_years(self):
        dates = _dates(30, start="2023-01-03") + _dates(30, start="2024-01-02")
        rows = []
        for d in dates:
            year = d.year
            rows.append({
                "代码": "sh600001",
                "信号日": d,
                "入场日": d + timedelta(days=1),
                "退出日": d + timedelta(days=3),
                "受阻": False,
                "净收益%": -2.0 if year == 2023 else 1.0,
            })
        trades = pd.DataFrame(rows)
        cfg = ScreenToTradeConfig(
            module="continuity", hold_days=2, weak_slice_threshold_pct=-0.5,
            min_avg_amount=1, start_date="2023-01-01",
        )
        # 扩展指数覆盖两年
        idx_dates = _dates(80, start="2022-11-01")
        idx_rows = []
        px = 3000.0
        for i, d in enumerate(idx_dates):
            px *= 1.002 if i > 40 else 0.998
            idx_rows.append({"代码": "sh000300", "日期": d, "收盘": px})
        slices = regime_slices(trades, pd.DataFrame(idx_rows), cfg)
        self.assertTrue(any(s["slice"] == "2023" for s in slices["by_year"]))
        self.assertTrue(any(w["slice"] == "2023" for w in slices["weak_slices"]))

    def test_continuity_and_sideways_modules_run(self):
        for module in ("continuity", "sideways"):
            cfg = ScreenToTradeConfig(
                module=module,
                start_date="2024-01-15",
                hold_days=2,
                max_signals_per_day=10,
                min_avg_amount=10_000_000,
                capital=50_000,
                notional_per_trade=5_000,
                continuity_lookback=5,
                sideways_days=5,
                max_amplitude_pct=25.0,
                max_slope_pct=2.0,
                min_overlap_pct=10.0,
                r2_max=0.0,
            )
            result = run_screen_to_trade(self.stocks, self.index, cfg)
            self.assertEqual(result["diagnostics"]["module"], module)
            self.assertIn("report_card", result)

    def test_equity_curve_metrics_finite(self):
        panel = prepare_panel(self.stocks, self.config)
        signals = scan_signals(panel, self.config)
        trades = link_trades(signals, panel, self.config)
        # 若没有成交，注入两条手工成交
        if trades.empty or trades["受阻"].all():
            d0 = pd.Timestamp("2024-02-01")
            trades = pd.DataFrame([
                {
                    "代码": "sh600001", "信号日": d0, "入场日": d0 + timedelta(days=1),
                    "退出日": d0 + timedelta(days=4), "受阻": False,
                    "买入成交价": 10.0, "卖出成交价": 10.5, "净收益%": 4.0,
                },
                {
                    "代码": "sh600002", "信号日": d0 + timedelta(days=2),
                    "入场日": d0 + timedelta(days=3),
                    "退出日": d0 + timedelta(days=6), "受阻": False,
                    "买入成交价": 11.0, "卖出成交价": 10.5, "净收益%": -5.0,
                },
            ])
        curve, closed, final_eq = build_equity_curve(trades, self.config)
        self.assertTrue(len(curve) >= 1)
        self.assertTrue(np.isfinite(final_eq))


if __name__ == "__main__":
    unittest.main()
