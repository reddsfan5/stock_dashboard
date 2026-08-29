import unittest

from backtest.intraday_grid import (
    IntradayGridConfig,
    IntradayGridError,
    PRICE_TRIGGERED,
    TRANSACTION_DRIVEN,
    simulate_intraday_grid,
)


def point(time, price):
    return {"time": time, "open": price, "high": price,
            "low": price, "close": price}


class IntradayGridTest(unittest.TestCase):
    def test_buy_then_sell_completes_one_grid_cycle(self):
        result = simulate_intraday_grid(
            [point("09:31", 10.0), point("09:32", 9.9), point("09:33", 10.0)],
            IntradayGridConfig(
                initial_cash=10_000,
                initial_shares=100,
                grid_pct=1,
                lot_shares=100,
                min_position=0,
                max_position=200,
                commission_rate=0,
            ),
        )
        self.assertEqual([t["side"] for t in result["trades"]], ["buy", "sell"])
        self.assertEqual(result["summary"]["final_shares"], 100)
        self.assertGreater(result["summary"]["total_pnl"], 0)

    def test_position_and_cash_limits_prevent_invalid_trades(self):
        result = simulate_intraday_grid(
            [point("09:31", 10.0), point("09:32", 9.0), point("09:33", 11.0)],
            IntradayGridConfig(
                initial_cash=0,
                initial_shares=100,
                grid_pct=1,
                lot_shares=100,
                min_position=100,
                max_position=100,
                commission_rate=0,
            ),
        )
        self.assertEqual(result["summary"]["trade_count"], 0)
        self.assertTrue(any(row["blocked"] for row in result["timeline"]))

    def test_rejects_non_board_lot(self):
        with self.assertRaises(IntradayGridError):
            simulate_intraday_grid(
                [point("09:31", 10.0)],
                IntradayGridConfig(lot_shares=50),
            )

    def test_transaction_driven_reserves_cash_and_shares(self):
        result = simulate_intraday_grid(
            [point("09:31", 10.0)],
            IntradayGridConfig(
                mode=TRANSACTION_DRIVEN,
                initial_cash=10_000,
                initial_shares=200,
                grid_pct=1,
                lot_shares=100,
                max_position=500,
                commission_rate=0,
            ),
        )
        row = result["timeline"][0]
        self.assertEqual(row["reserved_cash"], 990.0)
        self.assertEqual(row["reserved_shares"], 100)
        self.assertEqual(row["available_cash"], 9010.0)
        self.assertEqual(row["available_shares"], 100)

    def test_price_triggered_does_not_reserve_and_applies_slippage(self):
        result = simulate_intraday_grid(
            [point("09:31", 10.0), point("09:32", 9.9)],
            IntradayGridConfig(
                mode=PRICE_TRIGGERED,
                initial_cash=10_000,
                initial_shares=100,
                grid_pct=1,
                lot_shares=100,
                max_position=300,
                commission_rate=0,
                slippage_rate=0.001,
            ),
        )
        row = result["timeline"][0]
        self.assertEqual(row["reserved_cash"], 0)
        self.assertEqual(row["reserved_shares"], 0)
        self.assertAlmostEqual(result["trades"][0]["trigger_price"], 9.9)
        self.assertAlmostEqual(result["trades"][0]["price"], 9.9099)
        self.assertEqual(result["trades"][0]["order_type"], "到价触发委托")
        self.assertTrue(all(row["pending_count"] == 0 for row in result["timeline"]))

    def test_rejects_unknown_mode(self):
        with self.assertRaises(IntradayGridError):
            simulate_intraday_grid(
                [point("09:31", 10.0)],
                IntradayGridConfig(mode="unknown"),
            )

    def test_custom_base_and_separate_difference_steps(self):
        result = simulate_intraday_grid(
            [point("09:31", 10.0)],
            IntradayGridConfig(
                base_price=10.2,
                step_mode="diff",
                buy_step=0.2,
                sell_step=0.3,
                initial_cash=10_000,
                initial_shares=500,
                max_position=1_000,
                commission_rate=0,
            ),
        )
        row = result["timeline"][0]
        self.assertEqual(row["anchor"], 10.0)
        self.assertEqual(result["trades"][0]["side"], "buy")
        self.assertEqual(result["trades"][0]["trigger_price"], 10.0)
        self.assertEqual(row["next_sell"], 10.3)

    def test_split_quantities_and_multipliers_affect_orders(self):
        result = simulate_intraday_grid(
            [point("09:31", 10.0)],
            IntradayGridConfig(
                initial_cash=20_000,
                initial_shares=1_000,
                grid_pct=1,
                buy_lot_shares=200,
                sell_lot_shares=300,
                multiplier_enabled=True,
                buy_multiplier=2,
                sell_multiplier=3,
                max_position=3_000,
                commission_rate=0,
            ),
        )
        row = result["timeline"][0]
        self.assertEqual(row["next_buy_shares"], 400)
        self.assertEqual(row["next_sell_shares"], 900)
        self.assertEqual(row["reserved_shares"], 900)

    def test_effective_price_range_disables_outside_order(self):
        result = simulate_intraday_grid(
            [point("09:31", 10.0)],
            IntradayGridConfig(
                initial_cash=10_000,
                initial_shares=500,
                grid_pct=1,
                price_floor=9.95,
                price_ceiling=10.05,
                max_position=1_000,
                commission_rate=0,
            ),
        )
        row = result["timeline"][0]
        self.assertFalse(row["buy_order_active"])
        self.assertFalse(row["sell_order_active"])

    def test_price_triggered_waits_for_cumulative_rebound(self):
        result = simulate_intraday_grid(
            [point("09:31", 10.0), point("09:32", 9.4),
             point("09:33", 9.3), point("09:34", 9.4)],
            IntradayGridConfig(
                mode=PRICE_TRIGGERED,
                initial_cash=10_000,
                initial_shares=100,
                base_price=10,
                step_mode="diff",
                buy_step=0.5,
                sell_step=0.5,
                rebound_enabled=True,
                rebound_value=1,
                turn_mode="pct",
                lot_shares=100,
                max_position=300,
                commission_rate=0,
            ),
        )
        self.assertEqual(result["events"][0]["type"], "armed")
        self.assertEqual(result["events"][1]["reason"], "累计反弹触发买入")
        self.assertAlmostEqual(result["trades"][0]["price"], 9.393)
        self.assertAlmostEqual(result["trades"][0]["anchor_after"], 9.5)

    def test_floor_trigger_uses_original_grid_before_large_rebound_target(self):
        result = simulate_intraday_grid(
            [point("09:31", 10.0), point("09:32", 9.4), point("09:33", 9.5)],
            IntradayGridConfig(
                mode=PRICE_TRIGGERED,
                initial_cash=10_000,
                initial_shares=100,
                base_price=10,
                step_mode="diff",
                buy_step=0.5,
                sell_step=0.5,
                rebound_enabled=True,
                rebound_value=5,
                floor_trigger_enabled=True,
                lot_shares=100,
                max_position=300,
                commission_rate=0,
            ),
        )
        self.assertEqual(result["events"][1]["reason"], "保底价触发买入")
        self.assertAlmostEqual(result["trades"][0]["trigger_price"], 9.5)

    def test_price_triggered_waits_for_cumulative_pullback(self):
        result = simulate_intraday_grid(
            [point("09:31", 10.0), point("09:32", 10.6),
             point("09:33", 10.7), point("09:34", 10.59)],
            IntradayGridConfig(
                mode=PRICE_TRIGGERED,
                initial_cash=10_000,
                initial_shares=200,
                base_price=10,
                step_mode="diff",
                buy_step=0.5,
                sell_step=0.5,
                pullback_enabled=True,
                pullback_value=1,
                turn_mode="pct",
                lot_shares=100,
                max_position=300,
                commission_rate=0,
            ),
        )
        self.assertEqual(result["events"][0]["type"], "armed")
        self.assertEqual(result["events"][1]["reason"], "累计回落触发卖出")
        self.assertAlmostEqual(result["trades"][0]["price"], 10.593)
        self.assertAlmostEqual(result["trades"][0]["anchor_after"], 10.5)

    def test_passive_order_reserves_after_trigger_and_updates_on_fill(self):
        result = simulate_intraday_grid(
            [point("09:31", 10.0), point("09:32", 9.5), point("09:33", 9.48)],
            IntradayGridConfig(
                mode=PRICE_TRIGGERED,
                initial_cash=10_000,
                initial_shares=100,
                base_price=10,
                step_mode="diff",
                buy_step=0.5,
                sell_step=0.5,
                order_price_mode="passive",
                order_offset_bps=10,
                base_update_timing="filled",
                base_update_price="fill",
                lot_shares=100,
                max_position=300,
                commission_rate=0,
            ),
        )
        pending_row = result["timeline"][1]
        self.assertEqual(pending_row["pending_count"], 1)
        self.assertGreater(pending_row["reserved_cash"], 0)
        self.assertEqual(pending_row["anchor"], 10.0)
        self.assertEqual(result["summary"]["trade_count"], 1)
        self.assertAlmostEqual(result["summary"]["closing_anchor"], 9.4905)

    def test_trigger_update_can_move_base_before_passive_order_fills(self):
        result = simulate_intraday_grid(
            [point("09:31", 10.0), point("09:32", 9.5)],
            IntradayGridConfig(
                mode=PRICE_TRIGGERED,
                initial_cash=10_000,
                initial_shares=100,
                base_price=10,
                step_mode="diff",
                buy_step=0.5,
                sell_step=0.5,
                order_price_mode="passive",
                order_offset_bps=10,
                base_update_timing="triggered",
                base_update_price="grid",
                lot_shares=100,
                max_position=300,
                commission_rate=0,
            ),
        )
        self.assertEqual(result["summary"]["trade_count"], 0)
        self.assertEqual(result["summary"]["pending_count"], 1)
        self.assertAlmostEqual(result["summary"]["closing_anchor"], 9.5)

    def test_passive_order_auto_cancel_releases_reservation(self):
        result = simulate_intraday_grid(
            [point("09:31", 10.0), point("09:32", 9.5),
             point("09:33", 9.6), point("09:34", 9.7)],
            IntradayGridConfig(
                mode=PRICE_TRIGGERED,
                initial_cash=10_000,
                initial_shares=100,
                base_price=10,
                step_mode="diff",
                buy_step=0.5,
                sell_step=0.5,
                order_price_mode="passive",
                order_offset_bps=10,
                auto_cancel_enabled=True,
                auto_cancel_minutes=1,
                lot_shares=100,
                max_position=300,
                commission_rate=0,
            ),
        )
        self.assertEqual(result["summary"]["trade_count"], 0)
        self.assertEqual(result["summary"]["cancel_count"], 1)
        self.assertEqual(result["timeline"][-1]["reserved_cash"], 0)

    def test_price_triggered_multiplier_uses_crossed_grid_count(self):
        result = simulate_intraday_grid(
            [point("09:31", 10.0), point("09:32", 8.8)],
            IntradayGridConfig(
                mode=PRICE_TRIGGERED,
                initial_cash=100_000,
                initial_shares=1_000,
                base_price=10,
                step_mode="diff",
                buy_step=0.5,
                sell_step=0.5,
                multiplier_enabled=True,
                buy_multiplier=3,
                sell_multiplier=3,
                lot_shares=100,
                max_position=5_000,
                commission_rate=0,
            ),
        )
        trade = result["trades"][0]
        self.assertEqual(trade["multiplier"], 2)
        self.assertEqual(trade["shares"], 200)
        self.assertAlmostEqual(trade["grid_price"], 9.0)


if __name__ == "__main__":
    unittest.main()
