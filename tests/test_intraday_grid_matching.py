"""Regression scenarios for chronological, tick-aligned grid matching."""
from dataclasses import replace
from decimal import Decimal
import unittest

from backtest.intraday_grid import (
    IntradayGridConfig, IntradayGridError, MATCHING_MODEL_VERSION,
    PRICE_TRIGGERED, TRANSACTION_DRIVEN, simulate_intraday_grid,
)


def bar(time, o, h=None, l=None, c=None):
    return dict(time=time, open=o, high=o if h is None else h,
                low=o if l is None else l, close=o if c is None else c)


class GridMatchingTest(unittest.TestCase):
    def run_grid(self, bars, **kwargs):
        cfg = IntradayGridConfig(mode=PRICE_TRIGGERED, base_price=10,
                                 step_mode='diff', buy_step=1, sell_step=1,
                                 initial_cash=100000, initial_shares=1000,
                                 commission_rate=0)
        return simulate_intraday_grid(bars, replace(cfg, **kwargs))

    def test_gap_never_creates_intermediate_fills(self):
        for mode in (PRICE_TRIGGERED, TRANSACTION_DRIVEN):
            for price, side in ((13, 'sell'), (7, 'buy')):
                with self.subTest(mode=mode, price=price):
                    result = self.run_grid([bar('09:30', 10), bar('09:31', price)], mode=mode)
                    self.assertEqual([(t['side'], t['price']) for t in result['trades']], [(side, price)])

    def test_initial_base_is_not_a_market_path(self):
        result = self.run_grid([bar('09:30', 13)])
        self.assertEqual([t['price'] for t in result['trades']], [13])

    def test_pending_fill_cannot_reuse_earlier_extreme(self):
        for first, o, h, l, c, forbidden in (
            (11, 10, 12, 9, 12, 'buy'),
            (9, 10, 11, 8, 8, 'sell'),
        ):
            result = self.run_grid([bar('09:30', first), bar('09:31', o, h, l, c)],
                                   order_price_mode='passive', order_offset_bps=100)
            self.assertEqual(len(result['trades']), 1)
            self.assertFalse(any(e['side'] == forbidden and e['type'] == 'trigger'
                                 for e in result['events']))

    def test_limit_does_not_fill_at_submission(self):
        result = self.run_grid([bar('09:30', 11)], order_price_mode='trigger')
        self.assertEqual(result['trades'], [])
        self.assertEqual(result['summary']['pending_count'], 1)
        self.assertEqual(result['timeline'][0]['reserved_shares'], 100)

    def test_new_limit_fills_later_in_same_minute(self):
        result = self.run_grid([bar('09:30', 10, 11.5, 10, 11.5)], order_price_mode='trigger')
        self.assertEqual(len(result['trades']), 1)
        trigger = result['events'][0]
        fill = result['trades'][0]
        self.assertLess(trigger['sequence'], fill['sequence'])
        self.assertEqual(trigger['index'], fill['index'])
        self.assertGreaterEqual(fill['price'], fill['order_price'])

    def test_full_fill_and_trigger_anchor_timing(self):
        for timing, anchor in (('filled', 10), ('triggered', 11)):
            result = self.run_grid([bar('09:30', 11)], order_price_mode='trigger',
                                   base_update_timing=timing)
            self.assertEqual(result['summary']['closing_anchor'], anchor)

    def test_cancel_uses_elapsed_time_and_wins_at_deadline(self):
        result = self.run_grid([bar('09:30', 11), bar('09:35', 12)],
                               order_price_mode='passive', order_offset_bps=100,
                               auto_cancel_enabled=True, auto_cancel_minutes=5)
        self.assertEqual(result['summary']['cancel_count'], 1)
        self.assertEqual(result['trades'], [])
        self.assertEqual(result['events'][1]['type'], 'cancel')

    def test_cancel_does_not_treat_seconds_as_minutes(self):
        result = self.run_grid([bar('09:30:00', 11), bar('09:30:10', 11), bar('09:30:20', 11)],
                               order_price_mode='passive', order_offset_bps=100,
                               auto_cancel_enabled=True, auto_cancel_minutes=1)
        self.assertEqual(result['summary']['cancel_count'], 0)
        self.assertEqual(result['timeline'][-1]['reserved_shares'], 100)

    def test_cancel_requires_valid_ordered_time(self):
        for times in ((None, '09:31'), ('bad', '09:31'), ('09:31', '09:30'), ('09:31', '09:31')):
            with self.subTest(times=times), self.assertRaises(IntradayGridError):
                self.run_grid([bar(times[0], 10), bar(times[1], 11)], auto_cancel_enabled=True)

    def test_price_ticks_and_tiny_grids(self):
        for tick in (.001, .01):
            for mode in (PRICE_TRIGGERED, TRANSACTION_DRIVEN):
                result = self.run_grid([bar('09:30', 10, 10.02, 9.98, 10.02)],
                                       tick_size=tick, mode=mode, buy_step=.0005,
                                       sell_step=.0005, slippage_rate=.00013)
                self.assertTrue(result['trades'])
                self.assertLess(len(result['trades']), 100)
                for trade in result['trades']:
                    for key in ('order_price', 'price'):
                        self.assertEqual(Decimal(str(trade[key])) % Decimal(str(tick)), 0)
                self.assertEqual(result['matching_model_version'], MATCHING_MODEL_VERSION)

    def test_decimal_grid_boundary_does_not_shift_one_tick(self):
        result = self.run_grid([bar('09:30', .3), bar('09:31', .2)],
                               mode=TRANSACTION_DRIVEN, base_price=.3, buy_step=.1, sell_step=.1)
        self.assertEqual(result['trades'][0]['order_price'], .2)

    def test_close_monitor_is_discrete(self):
        result = self.run_grid([bar('09:30', 10), bar('09:31', 13, 15, 8, 13)],
                               monitor_price_mode='close')
        self.assertEqual([t['price'] for t in result['trades']], [13])

    def test_continuous_multiplier_cannot_peek_at_endpoint(self):
        result = self.run_grid([bar('09:30', 10, 13, 10, 13)],
                               multiplier_enabled=True, sell_multiplier=5)
        self.assertTrue(all(t['multiplier'] == 1 for t in result['trades']))
        jump = self.run_grid([bar('09:30', 13)], multiplier_enabled=True, sell_multiplier=5)
        self.assertEqual(jump['trades'][0]['shares'], 300)

    def test_pending_orders_and_fees_never_overreserve(self):
        result = self.run_grid([bar('09:30', 9), bar('09:31', 8), bar('09:32', 7),
                               bar('09:33', 10, 12, 6, 12)], initial_cash=1500,
                               initial_shares=200, max_position=400, min_position=100,
                               order_price_mode='passive', order_offset_bps=100,
                               base_update_timing='triggered', min_commission=5)
        for row in result['timeline']:
            self.assertGreaterEqual(row['available_cash'], 0)
            self.assertGreaterEqual(row['available_shares'], 100)
            self.assertLessEqual(row['shares'], 400)
        for trade in result['trades']:
            self.assertGreaterEqual(trade['cash'], 0)
            self.assertGreaterEqual(trade['position'], 100)
            self.assertLessEqual(trade['position'], 400)

    def test_unsupported_cage_and_invalid_tick_rejected(self):
        for config in ({'cage_to_market': True}, {'tick_size': 0}, {'tick_size': .002}):
            with self.assertRaises(IntradayGridError):
                self.run_grid([bar('09:30', 10)], **config)

    def test_data_must_match_requested_tick(self):
        with self.assertRaisesRegex(IntradayGridError, '报价单位'):
            self.run_grid([bar('09:30', 10.001)], tick_size=.01)


if __name__ == '__main__':
    unittest.main()
