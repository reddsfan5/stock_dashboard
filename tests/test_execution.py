import unittest

from backtest.execution import (
    BUY,
    SELL,
    ExecutionConfig,
    buy_cash_required,
    execute_order,
    max_affordable_shares,
    sell_cash_received,
)


class ExecutionModelTest(unittest.TestCase):
    def test_minimum_commission_changes_affordable_lot(self):
        config = ExecutionConfig(commission_rate=0.0001, min_commission=5)
        self.assertEqual(buy_cash_required(10, 100, config), 1005)
        self.assertEqual(max_affordable_shares(1000, 10, config), 0)
        self.assertEqual(max_affordable_shares(1005, 10, config), 100)

    def test_buy_slippage_rounds_up_and_sell_rounds_down(self):
        config = ExecutionConfig(slippage_bps=2, price_tick=0.01)
        buy = execute_order(BUY, 100, 10.0, config, cash=2000)
        sell = execute_order(SELL, 100, 10.0, config, available_shares=100)
        self.assertEqual(buy.fill_price, 10.01)
        self.assertEqual(sell.fill_price, 9.99)

    def test_amount_participation_caps_fill(self):
        config = ExecutionConfig(max_amount_participation=0.1)
        fill = execute_order(
            BUY, 10_000, 10, config, cash=1_000_000,
            bar={"成交额": 200_000},
        )
        self.assertEqual(fill.status, "partial")
        self.assertEqual(fill.filled_shares, 2000)
        self.assertEqual(fill.reason, "amount_capacity")

    def test_one_price_limit_can_reject_order(self):
        config = ExecutionConfig(
            reject_one_price_limit=True, price_limit_pct=10,
        )
        limit_up = {"最高": 11, "最低": 11, "收盘": 11, "前收": 10}
        limit_down = {"最高": 9, "最低": 9, "收盘": 9, "前收": 10}
        buy = execute_order(BUY, 100, 11, config, cash=10_000, bar=limit_up)
        sell = execute_order(
            SELL, 100, 9, config, available_shares=100, bar=limit_down,
        )
        self.assertEqual(buy.reason, "one_price_limit")
        self.assertEqual(sell.reason, "one_price_limit")

    def test_sell_cash_includes_commission_and_stamp_tax(self):
        config = ExecutionConfig(
            commission_rate=0.0003, min_commission=5,
            stamp_tax_rate=0.0005,
        )
        self.assertEqual(sell_cash_received(10, 100, config), 994.5)

    def test_invalid_config_is_rejected(self):
        with self.assertRaises(ValueError):
            ExecutionConfig(max_amount_participation=1.1)
        with self.assertRaises(ValueError):
            ExecutionConfig(lot_size=0)


if __name__ == "__main__":
    unittest.main()
