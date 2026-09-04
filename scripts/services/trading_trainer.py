#!/usr/bin/env python3
"""T+1 无剧透手动交易训练页与服务端会话管理。"""

import math
import os
import re
import secrets
import threading
import time
from typing import Dict

import pandas as pd

from backtest.execution import ExecutionConfig
from backtest.trading_trainer import TrainerConfig, TradingTrainerSession
from data.index_minute import IndexMinuteData, align_trainer_dates
from data.minute import CACHE_FILE as MINUTE_CACHE_FILE
from data.schema import derive_previous_close
from data.training_sessions import (
    EMOTION_TAGS,
    ENTRY_STYLES,
    MINDSET_TAGS,
    TrainingSessionRepository,
    truncate_visible_context,
)


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_HTML = os.path.join(PROJECT_DIR, "output", "trading_trainer.html")
TEMPLATE_HTML = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "templates", "trading_trainer.html"
)


def _normalize_code(code: str) -> str:
    value = str(code or "").strip().lower().replace(".", "")
    if re.fullmatch(r"(sh|sz)\d{6}", value):
        return value
    digits = re.sub(r"\D", "", value)
    if len(digits) != 6:
        raise ValueError("请输入 6 位证券代码")
    return ("sh" if digits.startswith(("5", "6", "9")) else "sz") + digits


def load_daily_history(code: str, before_date: str, days: int) -> list:
    """只读取模拟日之前的日 K；当前日由已播放分钟实时合成。"""
    from data.etf import CACHE_FILE as ETF_CACHE_FILE
    from data.kline import CACHE_FILE as STOCK_CACHE_FILE

    target = pd.Timestamp(before_date)
    raw = code[2:]
    sources = (
        (ETF_CACHE_FILE, raw) if raw.startswith(("1", "5")) else
        (STOCK_CACHE_FILE, code)
    )
    path, stored_code = sources
    if not os.path.exists(path):
        return []
    frame = pd.read_parquet(
        path,
        columns=[
            "日期", "开盘", "最高", "最低", "收盘", "前收",
            "成交量", "成交额", "换手率%",
        ],
        filters=[("代码", "==", stored_code), ("日期", "<", target)],
    )
    if frame.empty:
        return []
    frame["日期"] = pd.to_datetime(frame["日期"], errors="coerce")
    frame = frame.dropna(subset=["日期", "开盘", "最高", "最低", "收盘"])
    frame = frame.drop_duplicates("日期", keep="last").sort_values("日期")
    # 先基于完整可见历史推导前收，再截取展示窗口，避免第一根展示 K 线
    # 因原始“前收”为空而被错误渲染成 +0.00%。
    frame["_previous_close"] = derive_previous_close(frame)
    frame = frame.tail(days)
    return [
        {
            "date": row["日期"].strftime("%Y-%m-%d"),
            "open": round(float(row["开盘"]), 6),
            "high": round(float(row["最高"]), 6),
            "low": round(float(row["最低"]), 6),
            "close": round(float(row["收盘"]), 6),
            "pre_close": (
                round(float(row["_previous_close"]), 6)
                if pd.notna(row["_previous_close"]) else None
            ),
            "change_pct": (
                round(
                    (float(row["收盘"]) / float(row["_previous_close"]) - 1) * 100,
                    4,
                )
                if pd.notna(row["_previous_close"])
                and float(row["_previous_close"]) > 0 else None
            ),
            "volume": (
                round(float(row["成交量"]), 2) if pd.notna(row["成交量"]) else None
            ),
            "amount": round(float(row["成交额"]), 2),
            "turnover_rate": (
                round(float(row["换手率%"]), 4) if pd.notna(row["换手率%"]) else None
            ),
            "partial": False,
        }
        for _, row in frame.iterrows()
    ]


class TradingTrainerService:
    """线程安全的本地训练会话仓库。"""

    def __init__(self, minute_repository, max_sessions: int = 100, training_store=None):
        self.repository = minute_repository
        self.max_sessions = max_sessions
        self.sessions: Dict[str, Dict] = {}
        self.lock = threading.RLock()
        self.training_store = training_store or TrainingSessionRepository()

    def dates(self, code: str) -> dict:
        normalized = _normalize_code(code)
        stock_dates = self._complete_dates(normalized)
        if not stock_dates:
            raise LookupError(f"{normalized} 在分钟缓存中没有完整交易日数据")
        index_dates = IndexMinuteData().available_dates()
        dates, warned, warning_code = align_trainer_dates(stock_dates, index_dates)
        payload = {
            "code": normalized,
            "name": self.repository.name_map.get(normalized, normalized),
            "dates": dates,
            "index_minute_aligned": bool(index_dates) and not warned,
            "index_minute_warning": warned,
        }
        if warning_code:
            payload["index_minute_warning_code"] = warning_code
        if warned and index_dates:
            payload["index_minute_dates"] = index_dates
        return payload

    @staticmethod
    def _complete_dates(code: str) -> list:
        """训练只使用覆盖开盘至收盘且至少 200 根的分钟日。"""
        if not os.path.exists(MINUTE_CACHE_FILE):
            return []
        frame = pd.read_parquet(
            MINUTE_CACHE_FILE, columns=["时间"], filters=[("代码", "==", code)]
        )
        if frame.empty:
            return []
        frame["时间"] = pd.to_datetime(frame["时间"])
        frame["日期"] = frame["时间"].dt.strftime("%Y-%m-%d")
        quality = frame.groupby("日期")["时间"].agg(["count", "min", "max"])
        usable = quality[
            (quality["count"] >= 200)
            & (quality["min"].dt.strftime("%H:%M") <= "09:35")
            & (quality["max"].dt.strftime("%H:%M") >= "14:55")
        ]
        return usable.index.sort_values().tolist()

    def create(self, payload: dict) -> dict:
        meta = self.dates(payload.get("code", ""))
        requested_date = str(payload.get("start_date") or "random")
        if requested_date == "random":
            candidates = meta["dates"][:-1] or meta["dates"]
            start_date = secrets.choice(candidates)
        elif requested_date in meta["dates"]:
            start_date = requested_date
        else:
            raise ValueError("起始交易日不在该标的分钟缓存中")

        capital = _number(payload, "capital", 100_000, minimum=1)
        commission_bps = _number(payload, "commission_bps", 3, minimum=0)
        min_commission = _number(payload, "min_commission", 5, minimum=0)
        is_etf = meta["code"][2:].startswith(("1", "5"))
        sell_tax_bps = _number(
            payload, "sell_tax_bps", 0 if is_etf else 5, minimum=0
        )
        slippage_bps = _number(payload, "slippage_bps", 0, minimum=0)
        session = TradingTrainerSession(
            code=meta["code"], name=meta["name"], dates=meta["dates"],
            start_date=start_date,
            day_loader=self.repository.payload,
            history_loader=load_daily_history,
            config=TrainerConfig(
                initial_capital=capital,
                execution=ExecutionConfig(
                    commission_rate=commission_bps / 10_000,
                    min_commission=min_commission,
                    stamp_tax_rate=sell_tax_bps / 10_000,
                    slippage_bps=slippage_bps,
                    lot_size=100,
                    price_tick=0.001 if is_etf else 0.01,
                ),
            ),
        )
        session_id = secrets.token_urlsafe(24)
        run = self.training_store.create_run(
            session_token=session_id,
            code=meta["code"],
            name=meta["name"],
            start_date=start_date,
            capital=capital,
            meta={
                "commission_bps": commission_bps,
                "min_commission": min_commission,
                "sell_tax_bps": sell_tax_bps,
                "slippage_bps": slippage_bps,
            },
        )
        day_plan_payload = payload.get("day_plan") or {}
        if any(day_plan_payload.get(key) not in (None, "") for key in (
            "thesis", "max_loss_pct", "max_loss_amount", "entry_style", "notes",
        )):
            self.training_store.upsert_day_plan(
                run_id=run["id"],
                market_date=start_date,
                thesis=day_plan_payload.get("thesis", ""),
                max_loss_pct=day_plan_payload.get("max_loss_pct"),
                max_loss_amount=day_plan_payload.get("max_loss_amount"),
                entry_style=day_plan_payload.get("entry_style", ""),
                notes=day_plan_payload.get("notes", ""),
            )
        with self.lock:
            self._prune()
            self.sessions[session_id] = {
                "session": session,
                "run_id": run["id"],
                "last_access": time.time(),
            }
        return self._enriched_state(session_id, session.state())

    def state(self, session_id: str) -> dict:
        with self.lock:
            session = self._get(session_id)
            return self._enriched_state(session_id, session.state())

    def advance(self, session_id: str, steps: int) -> dict:
        with self.lock:
            session = self._get(session_id)
            return self._enriched_state(session_id, session.advance(steps))

    def next_day(self, session_id: str) -> dict:
        with self.lock:
            item = self._get_item(session_id)
            session = item["session"]
            before = session.current_date
            state = session.next_day()
            # 换日后若已有计划则保留；UI 可继续写新一天计划
            item["last_access"] = time.time()
            enriched = self._enriched_state(session_id, state)
            enriched["previous_date"] = before
            return enriched

    def order(
        self,
        session_id: str,
        side: str,
        shares,
        note: str = "",
        order_type: str = "market",
        limit_price=None,
        validity: str = "day",
        emotion: str = "",
        planned_stop=None,
        planned_target=None,
        context: dict = None,
        require_decision: bool = False,
    ) -> dict:
        note = str(note or "").strip()
        emotion = str(emotion or "").strip()
        if require_decision and not note:
            raise ValueError("请填写交易理由，便于复盘")
        if require_decision and not emotion:
            raise ValueError("请选择情绪标签")
        with self.lock:
            item = self._get_item(session_id)
            session = item["session"]
            before_orders = len(session.orders)
            before_pending = len(session.pending_orders)
            state = session.place_order(
                side, shares, note, order_type, limit_price, validity
            )
            self._persist_order_decision(
                item,
                state,
                side=side,
                note=note,
                emotion=emotion,
                planned_stop=planned_stop,
                planned_target=planned_target,
                context=context,
                before_orders=before_orders,
                before_pending=before_pending,
            )
            return self._enriched_state(session_id, state)

    def cancel_pending_order(
        self,
        session_id: str,
        order_id: str,
        note: str = "",
        emotion: str = "",
        context: dict = None,
    ) -> dict:
        with self.lock:
            item = self._get_item(session_id)
            session = item["session"]
            state = session.cancel_pending_order(order_id)
            self._persist_cancel_decision(
                item, state, order_ref=order_id, note=note, emotion=emotion, context=context
            )
            return self._enriched_state(session_id, state)

    def conditional_order(
        self,
        session_id: str,
        side: str,
        condition_type: str,
        trigger_value,
        shares,
        note: str = "",
        validity: str = "day",
        secondary_trigger_value=None,
    ) -> dict:
        with self.lock:
            session = self._get(session_id)
            return self._with_id(
                session_id,
                session.create_conditional_order(
                    side, condition_type, trigger_value, shares, note, validity,
                    secondary_trigger_value,
                ),
            )

    def cancel_conditional_order(self, session_id: str, condition_id: str) -> dict:
        with self.lock:
            session = self._get(session_id)
            return self._with_id(
                session_id, session.cancel_conditional_order(condition_id)
            )

    def _get_item(self, session_id: str) -> Dict:
        item = self.sessions.get(str(session_id or ""))
        if item is None:
            raise LookupError("训练会话不存在或服务已经重启，请重新开始")
        item["last_access"] = time.time()
        return item

    def _get(self, session_id: str) -> TradingTrainerSession:
        return self._get_item(session_id)["session"]

    def set_day_plan(self, session_id: str, payload: dict) -> dict:
        with self.lock:
            item = self._get_item(session_id)
            session = item["session"]
            market_date = str(payload.get("market_date") or session.current_date)
            plan = self.training_store.upsert_day_plan(
                run_id=item["run_id"],
                market_date=market_date,
                thesis=payload.get("thesis", ""),
                max_loss_pct=payload.get("max_loss_pct"),
                max_loss_amount=payload.get("max_loss_amount"),
                entry_style=payload.get("entry_style", ""),
                notes=payload.get("notes", ""),
            )
            state = self._enriched_state(session_id, session.state())
            state["day_plan"] = plan
            return state

    def add_mindset_marker(self, session_id: str, payload: dict) -> dict:
        with self.lock:
            item = self._get_item(session_id)
            session = item["session"]
            marker = self.training_store.add_mindset_marker(
                run_id=item["run_id"],
                market_date=payload.get("market_date") or session.current_date,
                as_of=payload.get("as_of") or session.current_point["time"],
                tag=payload.get("tag", ""),
                note=payload.get("note", ""),
            )
            # 同步写入决策流，便于复盘时间线
            self.training_store.add_decision(
                run_id=item["run_id"],
                event_type="mindset",
                market_date=marker["market_date"],
                as_of=marker["as_of"],
                reason=marker.get("note") or marker["tag"],
                emotion=marker["tag"],
                require_reason=False,
                context=truncate_visible_context(
                    market=session.state().get("market"),
                    account=session.state().get("account"),
                ),
            )
            state = self._enriched_state(session_id, session.state())
            state["mindset_marker"] = marker
            return state

    def review(self, session_id: str, market_date: str = None) -> dict:
        with self.lock:
            item = self._get_item(session_id)
            state = item["session"].state()
            payload = self.training_store.build_review(
                item["run_id"],
                market_date=market_date or None,
                account=state.get("account"),
                orders=state.get("orders"),
            )
            payload["session_id"] = session_id
            payload["current_date"] = state.get("date")
            payload["current_time"] = state.get("time")
            return payload

    def meta_options(self) -> dict:
        return {
            "emotion_tags": list(EMOTION_TAGS),
            "mindset_tags": list(MINDSET_TAGS),
            "entry_styles": list(ENTRY_STYLES),
        }

    def _persist_order_decision(
        self,
        item,
        state,
        *,
        side,
        note,
        emotion,
        planned_stop,
        planned_target,
        context,
        before_orders,
        before_pending,
    ):
        order_ref = ""
        price = state.get("market", {}).get("price")
        shares = None
        if len(state.get("orders") or []) > before_orders:
            order = state["orders"][-1]
            order_ref = order.get("pending_order_id") or f"F{len(state['orders']):04d}"
            price = order.get("fill_price") or price
            shares = order.get("filled_shares") or order.get("requested_shares")
        elif len(state.get("pending_orders") or []) > before_pending:
            order = state["pending_orders"][-1]
            order_ref = order.get("id") or ""
            price = order.get("limit_price") or price
            shares = order.get("shares")
        visible = context if isinstance(context, dict) else None
        if visible is None:
            visible = truncate_visible_context(
                market=state.get("market"),
                account=state.get("account"),
            )
        self.training_store.add_decision(
            run_id=item["run_id"],
            event_type=side,
            market_date=state["date"],
            as_of=state["time"],
            side=side,
            shares=shares,
            price=price,
            order_ref=order_ref,
            reason=note,
            emotion=emotion,
            planned_stop=planned_stop,
            planned_target=planned_target,
            context=visible,
            require_reason=False,
        )

    def _persist_cancel_decision(self, item, state, *, order_ref, note, emotion, context):
        visible = context if isinstance(context, dict) else truncate_visible_context(
            market=state.get("market"),
            account=state.get("account"),
        )
        self.training_store.add_decision(
            run_id=item["run_id"],
            event_type="cancel",
            market_date=state["date"],
            as_of=state["time"],
            order_ref=str(order_ref or ""),
            reason=note or "撤销限价委托",
            emotion=emotion,
            context=visible,
            require_reason=False,
        )

    def _enriched_state(self, session_id: str, state: dict) -> dict:
        state = self._with_id(session_id, state)
        item = self.sessions.get(session_id) or {}
        run_id = item.get("run_id")
        if not run_id:
            return state
        plan = self.training_store.get_day_plan(run_id=run_id, market_date=state["date"])
        decisions = self.training_store.list_decisions(run_id, market_date=state["date"])
        markers = self.training_store.list_mindset_markers(run_id, market_date=state["date"])
        violations = []
        if plan:
            from data.training_sessions import check_plan_violations
            violations = check_plan_violations(
                plan, decisions, day_return_pct=state.get("account", {}).get("return_pct")
            )
        state["run_id"] = run_id
        state["day_plan"] = plan
        state["decisions"] = decisions
        state["mindset_markers"] = markers
        state["plan_violations"] = violations
        state["training_meta"] = self.meta_options()
        return state

    def _prune(self) -> None:
        expired_before = time.time() - 6 * 60 * 60
        expired = [
            key for key, item in self.sessions.items()
            if item["last_access"] < expired_before
        ]
        for key in expired:
            del self.sessions[key]
        while len(self.sessions) >= self.max_sessions:
            oldest = min(
                self.sessions, key=lambda key: self.sessions[key]["last_access"]
            )
            del self.sessions[oldest]

    @staticmethod
    def _with_id(session_id: str, state: dict) -> dict:
        state["session_id"] = session_id
        return state


def _number(payload: dict, key: str, default: float, minimum: float) -> float:
    try:
        value = float(payload.get(key, default))
    except (TypeError, ValueError):
        raise ValueError(f"{key} 必须是数字")
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{key} 不能小于 {minimum:g}")
    return value


def build_html() -> str:
    """读取独立页面模板。"""
    with open(TEMPLATE_HTML, "r", encoding="utf-8") as file:
        return file.read()


def write_app(path: str = OUT_HTML):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        file.write(build_html())
    print(f"✓ T+1 训练页: {path}")


if __name__ == "__main__":
    write_app()
    print("  交互训练需要服务端状态，请运行: python -m scripts.serve start trainer")
