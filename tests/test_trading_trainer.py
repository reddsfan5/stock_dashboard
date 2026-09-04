import json
import unittest
from unittest.mock import patch

import pandas as pd

from backtest.execution import ExecutionConfig
from backtest.trading_trainer import TrainerConfig, TradingTrainerSession
from scripts.services.trading_trainer import (
    TradingTrainerService, build_html, load_daily_history,
)


DATES = ["2026-08-25", "2026-08-26"]


def day_loader(_code, date):
    prices = {
        "2026-08-25": [10.0, 11.0, 10.5],
        "2026-08-26": [10.6, 12.0],
    }[date]
    points = []
    for index, price in enumerate(prices):
        points.append({
            "time": f"09:{31 + index:02d}",
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": 1_000,
            "amount": price * 1_000,
            "vwap": price - 0.1,
            "intraday_volume_ratio": index + 1,
        })
    return {"points": points, "prev_close": 9.5 if date == DATES[0] else 10.5}


def history_loader(_code, _date, _days):
    # 故意返回未来数据，状态机本身仍必须做最后一道防剧透过滤。
    return [
        {"date": "2026-08-24", "open": 9, "high": 10, "low": 9, "close": 9.5,
         "amount": 1_000, "partial": False},
        {"date": "2026-08-27", "open": 99, "high": 99, "low": 99, "close": 99,
         "amount": 1_000, "partial": False},
    ]


class TradingTrainerSessionTest(unittest.TestCase):
    def make_session(self):
        return TradingTrainerSession(
            code="sh600000",
            name="测试股",
            dates=DATES,
            start_date=DATES[0],
            day_loader=day_loader,
            history_loader=history_loader,
            config=TrainerConfig(
                initial_capital=10_000,
                execution=ExecutionConfig(
                    commission_rate=0,
                    stamp_tax_rate=0,
                    lot_size=100,
                    price_tick=0.01,
                ),
            ),
        )

    def test_state_only_exposes_revealed_market_data(self):
        session = self.make_session()
        state = session.state()
        self.assertEqual(len(state["minute_points"]), 1)
        self.assertEqual([row["date"] for row in state["daily_bars"]], [
            "2026-08-24", "2026-08-25",
        ])
        self.assertTrue(state["daily_bars"][-1]["partial"])
        self.assertEqual(state["daily_bars"][-1]["pre_close"], 9.5)
        self.assertAlmostEqual(state["daily_bars"][-1]["change_pct"], 5.2632)
        self.assertEqual(state["market"]["indicators"]["intraday_volume_ratio"], 1.0)
        self.assertAlmostEqual(
            state["market"]["indicators"]["vwap_deviation_pct"],
            (10.0 / 9.9 - 1) * 100,
            places=4,
        )
        state = session.advance(1)
        self.assertEqual(len(state["minute_points"]), 2)
        self.assertEqual(state["daily_bars"][-1]["high"], 11.0)
        self.assertEqual(state["market"]["indicators"]["intraday_volume_ratio"], 2.0)

    def test_t_plus_one_locks_same_day_purchase(self):
        session = self.make_session()
        bought = session.place_order("buy", 100, "练习进场")
        self.assertEqual(bought["account"]["total_shares"], 100)
        self.assertEqual(bought["account"]["available_shares"], 0)
        rejected = session.place_order("sell", 100)
        self.assertEqual(rejected["orders"][-1]["status"], "rejected")
        self.assertEqual(rejected["orders"][-1]["reason"], "t_plus_one_locked")

        with self.assertRaisesRegex(ValueError, "收盘"):
            session.next_day()
        session.advance(99)
        next_state = session.next_day()
        self.assertEqual(next_state["account"]["available_shares"], 100)
        sold = session.place_order("sell", 100, "练习离场")
        self.assertEqual(sold["account"]["total_shares"], 0)
        self.assertAlmostEqual(sold["account"]["realized_pnl"], 60.0)

    def test_rejects_non_board_lot_without_changing_account(self):
        session = self.make_session()
        with self.assertRaisesRegex(ValueError, "100 股"):
            session.place_order("buy", 150)
        self.assertEqual(session.cash, 10_000)
        self.assertEqual(session.orders, [])

    def test_result_is_json_serializable(self):
        state = self.make_session().state()
        json.dumps(state, ensure_ascii=False, allow_nan=False)

    def test_price_condition_triggers_at_intermediate_minute(self):
        session = self.make_session()
        created = session.create_conditional_order(
            "buy", "price_above", 10.8, 100, "突破跟进", "day"
        )
        self.assertEqual(created["conditional_orders"][-1]["status"], "active")

        # 一次快进两格，仍应在中间的 11.0 触发，而不是漏掉或用末尾 10.5 成交。
        advanced = session.advance(2)
        condition = advanced["conditional_orders"][-1]
        self.assertEqual(condition["status"], "triggered")
        self.assertEqual(condition["triggered_time"], "09:32")
        self.assertEqual(advanced["orders"][-1]["source"], "conditional")
        self.assertEqual(advanced["orders"][-1]["fill_price"], 11.0)
        self.assertEqual(advanced["account"]["locked_shares"], 100)

    def test_cancel_condition_prevents_trigger(self):
        session = self.make_session()
        created = session.create_conditional_order(
            "buy", "price_above", 10.8, 100
        )
        condition_id = created["conditional_orders"][-1]["id"]
        cancelled = session.cancel_conditional_order(condition_id)
        self.assertEqual(cancelled["conditional_orders"][-1]["status"], "cancelled")
        state = session.advance(2)
        self.assertEqual(state["orders"], [])

    def test_day_condition_expires_before_next_day(self):
        session = self.make_session()
        session.create_conditional_order("buy", "price_below", 8.0, 100)
        session.advance(99)
        state = session.next_day()
        self.assertEqual(state["conditional_orders"][-1]["status"], "expired")

    def test_sell_condition_obeys_t_plus_one_at_trigger(self):
        session = self.make_session()
        session.place_order("buy", 100)
        condition = session.create_conditional_order(
            "sell", "price_above", 10.8, 100, validity="session"
        )
        self.assertEqual(condition["conditional_orders"][-1]["status"], "active")
        triggered = session.advance(1)
        self.assertEqual(
            triggered["conditional_orders"][-1]["status"],
            "waiting_execution",
        )
        self.assertEqual(triggered["orders"][-1]["reason"], "t_plus_one_locked")

    def test_rebound_buy_requires_drop_then_rebound(self):
        session = self.make_session()
        session.points[1].update(open=9.0, high=9.0, low=9.0, close=9.0)
        session.points[2].update(open=9.3, high=9.3, low=9.3, close=9.3)
        created = session.create_conditional_order(
            "buy", "rebound_buy", 5, 100,
            secondary_trigger_value=2,
        )
        self.assertEqual(
            created["conditional_orders"][-1]["stage"],
            "waiting_activation",
        )
        state = session.advance(1)
        self.assertEqual(state["conditional_orders"][-1]["status"], "active")
        self.assertEqual(
            state["conditional_orders"][-1]["stage"], "tracking_rebound"
        )
        self.assertEqual(state["conditional_orders"][-1]["extreme_price"], 9.0)
        state = session.advance(1)
        self.assertEqual(state["conditional_orders"][-1]["status"], "triggered")
        self.assertEqual(state["orders"][-1]["fill_price"], 9.3)

    def test_stop_loss_waits_for_t_plus_one_then_executes_next_day(self):
        session = self.make_session()
        session.advance(1)  # 11.0 买入
        session.place_order("buy", 100)
        session.create_conditional_order(
            "sell", "stop_loss", 1, 100, validity="session"
        )
        waiting = session.advance(1)  # 10.5 已触发，但当日不可卖
        condition = waiting["conditional_orders"][-1]
        self.assertEqual(condition["status"], "waiting_execution")
        self.assertEqual(condition["result_reason"], "t_plus_one_locked")
        self.assertEqual(waiting["progress"]["active_condition_count"], 1)

        executed = session.next_day()
        self.assertEqual(executed["conditional_orders"][-1]["status"], "triggered")
        self.assertEqual(executed["account"]["total_shares"], 0)
        self.assertEqual(executed["orders"][-1]["fill_price"], 10.6)

    def test_combined_take_profit_stop_loss_uses_risk_first(self):
        session = self.make_session()
        session.place_order("buy", 100)
        session.advance(99)
        session.next_day()
        session.points[1].update(open=9.7, high=12.5, low=9.7, close=9.7)
        session.create_conditional_order(
            "sell", "take_profit_stop_loss", 20, 100,
            validity="session", secondary_trigger_value=2,
        )
        state = session.advance(1)
        condition = state["conditional_orders"][-1]
        self.assertEqual(condition["status"], "triggered")
        self.assertEqual(condition["matched_branch"], "stop_loss")
        self.assertEqual(state["account"]["total_shares"], 0)

    def test_pullback_sell_requires_rise_then_pullback(self):
        session = self.make_session()
        session.place_order("buy", 100)
        session.advance(99)
        session.next_day()
        session.points[1].update(open=11.2, high=12.0, low=11.2, close=11.5)
        session.create_conditional_order(
            "sell", "pullback_sell", 5, 100,
            validity="session", secondary_trigger_value=3,
        )
        state = session.advance(1)
        condition = state["conditional_orders"][-1]
        self.assertEqual(condition["status"], "triggered")
        self.assertEqual(condition["activation_price"], 12.0)
        self.assertEqual(state["account"]["total_shares"], 0)

    def test_limit_buy_freezes_cash_and_cancel_releases_it(self):
        session = self.make_session()
        created = session.place_order(
            "buy", 100, "低吸等待", "limit", 9.5, "session"
        )
        pending = created["pending_orders"][-1]
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(created["account"]["cash"], 10_000)
        self.assertEqual(created["account"]["reserved_cash"], 950)
        self.assertEqual(created["account"]["available_cash"], 9_050)
        self.assertEqual(created["progress"]["pending_order_count"], 1)

        cancelled = session.cancel_pending_order(pending["id"])
        self.assertEqual(cancelled["pending_orders"][-1]["status"], "cancelled")
        self.assertEqual(cancelled["account"]["reserved_cash"], 0)
        self.assertEqual(cancelled["account"]["available_cash"], 10_000)

    def test_limit_order_fills_when_intermediate_minute_touches_price(self):
        session = self.make_session()
        session.advance(1)  # 11.0
        state = session.place_order("buy", 100, order_type="limit", limit_price=10.7)
        self.assertEqual(state["pending_orders"][-1]["status"], "pending")
        filled = session.advance(1)  # 10.5，向下触及限价
        self.assertEqual(filled["pending_orders"][-1]["status"], "filled")
        self.assertEqual(filled["pending_orders"][-1]["filled_price"], 10.5)
        self.assertEqual(filled["orders"][-1]["source"], "limit")

    def test_limit_sell_freezes_settled_shares_then_fills(self):
        session = self.make_session()
        session.place_order("buy", 100)
        session.advance(99)
        session.next_day()
        created = session.place_order(
            "sell", 100, order_type="limit", limit_price=11.5,
            validity="session",
        )
        self.assertEqual(created["account"]["settled_shares"], 100)
        self.assertEqual(created["account"]["reserved_shares"], 100)
        self.assertEqual(created["account"]["available_shares"], 0)
        filled = session.advance(1)
        self.assertEqual(filled["pending_orders"][-1]["status"], "filled")
        self.assertEqual(filled["account"]["total_shares"], 0)

    def test_day_limit_order_expires_and_releases_cash(self):
        session = self.make_session()
        session.place_order("buy", 100, order_type="limit", limit_price=9.0)
        session.advance(99)
        state = session.next_day()
        self.assertEqual(state["pending_orders"][-1]["status"], "expired")
        self.assertEqual(state["account"]["reserved_cash"], 0)


class FakeMinuteRepository:
    name_map = {"sh600000": "测试股"}

    def payload(self, code, date):
        return day_loader(code, date)


class TradingTrainerServiceTest(unittest.TestCase):
    def test_daily_history_derives_previous_close_before_display_window(self):
        dates = pd.bdate_range("2026-08-17", periods=4)
        frame = pd.DataFrame({
            "日期": dates, "开盘": [9.0, 9.5, 10.0, 10.5],
            "最高": [9.2, 9.7, 10.2, 10.7], "最低": [8.8, 9.3, 9.8, 10.3],
            "收盘": [9.1, 9.6, 10.1, 10.6], "前收": [None] * 4,
            "成交量": [1000] * 4, "成交额": [9000, 9600, 10100, 10600],
            "换手率%": [1.0] * 4,
        })
        with patch(
            "scripts.services.trading_trainer.pd.read_parquet", return_value=frame
        ), patch("scripts.services.trading_trainer.os.path.exists", return_value=True):
            bars = load_daily_history("sh600000", "2026-08-25", 2)

        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0]["pre_close"], 9.6)
        self.assertAlmostEqual(
            bars[0]["change_pct"], (10.1 / 9.6 - 1) * 100, places=4
        )

    def test_complete_dates_excludes_partial_sessions(self):
        complete = pd.date_range("2026-08-25 09:31", periods=201, freq="min")
        complete = complete[:-1].append(pd.DatetimeIndex(["2026-08-25 15:00"]))
        partial = pd.date_range("2026-08-26 13:30", periods=30, freq="min")
        with patch(
            "scripts.services.trading_trainer.pd.read_parquet",
            return_value=pd.DataFrame({"时间": complete.append(partial)}),
        ), patch("scripts.services.trading_trainer.os.path.exists", return_value=True):
            dates = TradingTrainerService._complete_dates("sh600000")
        self.assertEqual(dates, ["2026-08-25"])

    def test_service_creates_server_side_session(self):
        service = TradingTrainerService(FakeMinuteRepository())
        with patch.object(service, "_complete_dates", return_value=DATES), patch(
            "scripts.services.trading_trainer.load_daily_history",
            side_effect=history_loader,
        ):
            state = service.create({
                "code": "600000", "start_date": DATES[0], "capital": 10_000,
                "commission_bps": 0, "min_commission": 0, "sell_tax_bps": 0,
            })
        self.assertIn("session_id", state)
        self.assertEqual(service.state(state["session_id"])["date"], DATES[0])

    def test_service_creates_and_cancels_conditional_order(self):
        service = TradingTrainerService(FakeMinuteRepository())
        with patch.object(service, "_complete_dates", return_value=DATES), patch(
            "scripts.services.trading_trainer.load_daily_history",
            side_effect=history_loader,
        ):
            state = service.create({
                "code": "600000", "start_date": DATES[0], "capital": 10_000,
                "commission_bps": 0, "min_commission": 0, "sell_tax_bps": 0,
            })
            session_id = state["session_id"]
            created = service.conditional_order(
                session_id, "buy", "price_above", 12, 100, "等待突破", "day"
            )
            condition_id = created["conditional_orders"][-1]["id"]
            cancelled = service.cancel_conditional_order(session_id, condition_id)
        self.assertEqual(cancelled["conditional_orders"][-1]["status"], "cancelled")

    def test_service_creates_and_cancels_limit_order(self):
        service = TradingTrainerService(FakeMinuteRepository())
        with patch.object(service, "_complete_dates", return_value=DATES), patch(
            "scripts.services.trading_trainer.load_daily_history",
            side_effect=history_loader,
        ):
            state = service.create({
                "code": "600000", "start_date": DATES[0], "capital": 10_000,
                "commission_bps": 0, "min_commission": 0, "sell_tax_bps": 0,
            })
            session_id = state["session_id"]
            created = service.order(
                session_id, "buy", 100, "等待低吸", "limit", 9.5, "day"
            )
            order_id = created["pending_orders"][-1]["id"]
            cancelled = service.cancel_pending_order(session_id, order_id)
        self.assertEqual(cancelled["pending_orders"][-1]["status"], "cancelled")

    def test_page_contains_only_api_driven_empty_shell(self):
        html = build_html()
        self.assertIn("/api/trainer/session", html)
        self.assertIn("/api/trainer/condition", html)
        self.assertIn("/api/trainer/order/cancel", html)
        self.assertIn("secondary_trigger_value", html)
        self.assertIn("stock_journal.html?code=", html)
        self.assertIn("market_news.html?mode=replay", html)
        self.assertIn("模拟实时资讯", html)
        self.assertIn("/api/news/day", html)
        self.assertIn("syncLiveNews(next.date,next.time)", html)
        self.assertIn("已触发，等待执行", html)
        self.assertIn("日 K", html)
        self.assertIn("当前可见行情指标", html)
        self.assertIn("factorVolume", html)
        self.assertIn("区间涨跌", html)
        self.assertIn("当前可见收", html)
        self.assertIn("当日涨跌", html)
        self.assertIn("▶ 动态分时", html)
        self.assertNotIn("2026-08-25 09:31", html)
        self.assertIn("交易理由（选填）", html)
        self.assertIn("无分钟缓存·仅开盘", html)
        self.assertIn("require_decision:false", html)
        self.assertNotIn("请填写交易理由", html)
        self.assertNotIn("require_decision:true", html)


    def test_order_allows_empty_reason_when_not_required(self):
        service = TradingTrainerService(FakeMinuteRepository())
        with patch.object(service, "_complete_dates", return_value=DATES), patch(
            "scripts.services.trading_trainer.load_daily_history",
            side_effect=history_loader,
        ), patch(
            "scripts.services.trading_trainer.IndexMinuteData.available_dates",
            return_value=[],
        ):
            state = service.create({
                "code": "600000", "start_date": DATES[0], "capital": 10_000,
                "commission_bps": 0, "min_commission": 0, "sell_tax_bps": 0,
            })
            session_id = state["session_id"]
            ordered = service.order(
                session_id, "buy", 100, "", "market", None, "day",
                emotion="", require_decision=False,
            )
        self.assertTrue(any(o["side"] == "buy" for o in ordered["orders"]))

    def test_dates_intersects_index_minute_coverage(self):
        service = TradingTrainerService(FakeMinuteRepository())
        with patch.object(
            service, "_complete_dates",
            return_value=["2026-08-25", "2026-09-01", "2026-09-02"],
        ), patch(
            "scripts.services.trading_trainer.IndexMinuteData.available_dates",
            return_value=["2026-09-01", "2026-09-02", "2026-09-03"],
        ):
            meta = service.dates("600000")
        self.assertEqual(meta["dates"], ["2026-09-01", "2026-09-02"])
        self.assertTrue(meta["index_minute_aligned"])
        self.assertFalse(meta["index_minute_warning"])

    def test_dates_falls_back_when_no_index_overlap(self):
        service = TradingTrainerService(FakeMinuteRepository())
        with patch.object(
            service, "_complete_dates", return_value=["2026-08-25", "2026-08-26"]
        ), patch(
            "scripts.services.trading_trainer.IndexMinuteData.available_dates",
            return_value=["2026-09-01"],
        ):
            meta = service.dates("600000")
        self.assertEqual(meta["dates"], ["2026-08-25", "2026-08-26"])
        self.assertTrue(meta["index_minute_warning"])
        self.assertEqual(meta["index_minute_warning_code"], "index_minute_no_overlap")


if __name__ == "__main__":
    unittest.main()
