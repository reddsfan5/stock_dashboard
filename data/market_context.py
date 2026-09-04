"""训练页「市场情境」组装：A 股宽基指数 + 海外已知时刻卡片。

防剧透约定与模拟资讯一致：只返回模拟日 ``as_of``（中国 Asia/Shanghai）及之前
可知的数据。A 股优先用指数分钟线与分时同步；海外 v1 为日线，按各市场收盘时刻
判断是否已可知，不伪造全球同分钟同步。
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from data.global_markets import OVERSEAS_INDEXES, GlobalMarketsData
from data.index import INDEXES, IndexData
from data.index_minute import IndexMinuteData


SHANGHAI = ZoneInfo("Asia/Shanghai")

STATUS_LABELS = {
    "open": "交易中",
    "closed": "已收盘",
    "overnight": "隔夜/休市",
    "not_yet_open": "未开盘",
}

A_SHARE_SESSION_OPEN = time(9, 30)
A_SHARE_SESSION_CLOSE = time(15, 0)


def _parse_clock(value: Optional[str], default: str = "15:00:00") -> time:
    text = str(value or default).strip()
    if len(text) == 5:
        text += ":00"
    return time.fromisoformat(text)


def _shanghai_moment(market_date: str, as_of: Optional[str]) -> datetime:
    day = datetime.fromisoformat(str(market_date)).date()
    clock = _parse_clock(as_of)
    return datetime.combine(day, clock, tzinfo=SHANGHAI)


def _pct(price: Optional[float], prev: Optional[float]) -> Optional[float]:
    if price is None or prev is None or prev == 0:
        return None
    return round((price / prev - 1.0) * 100.0, 4)


def _round_price(value) -> Optional[float]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def market_session_status(
    as_of_shanghai: datetime,
    timezone_name: str,
    session_open: str,
    session_close: str,
) -> str:
    """根据模拟中国时刻，推断目标市场当时状态。"""
    tz = ZoneInfo(timezone_name)
    local = as_of_shanghai.astimezone(tz)
    open_t = _parse_clock(session_open)
    close_t = _parse_clock(session_close)
    # 粗略：周末一律 overnight；盘中 open；开盘前 not_yet_open；收盘后 closed
    if local.weekday() >= 5:
        return "overnight"
    clock = local.time().replace(microsecond=0)
    if clock < open_t:
        return "not_yet_open"
    if clock >= close_t:
        return "closed"
    # 港股午休：12:00-13:00 仍标交易中（信息可知性与上午一致），避免过度细分
    return "open"


def last_known_daily_bar(
    bars: pd.DataFrame,
    as_of_shanghai: datetime,
    timezone_name: str,
    session_close: str,
) -> Optional[pd.Series]:
    """返回在 as_of 时刻已可知的最近一根完整日 K（收盘后才揭示当日收盘）。"""
    if bars is None or len(bars) == 0:
        return None
    tz = ZoneInfo(timezone_name)
    local = as_of_shanghai.astimezone(tz)
    close_t = _parse_clock(session_close)
    frame = bars.copy()
    frame["日期"] = pd.to_datetime(frame["日期"]).dt.normalize()
    known_rows = []
    for _, row in frame.iterrows():
        bar_day = row["日期"].date()
        if local.date() > bar_day:
            known_rows.append(row)
        elif local.date() == bar_day and local.time().replace(microsecond=0) >= close_t:
            known_rows.append(row)
    if not known_rows:
        return None
    return known_rows[-1]


def previous_close_from_bars(bars: pd.DataFrame, bar_date) -> Optional[float]:
    if bars is None or len(bars) == 0 or bar_date is None:
        return None
    frame = bars.copy()
    frame["日期"] = pd.to_datetime(frame["日期"]).dt.normalize()
    earlier = frame[frame["日期"] < pd.Timestamp(bar_date).normalize()]
    if earlier.empty:
        return None
    return float(earlier.iloc[-1]["收盘"])


class MarketContextService:
    """组装训练页市场情境载荷。"""

    def __init__(
        self,
        index_data: Optional[IndexData] = None,
        index_minute: Optional[IndexMinuteData] = None,
        global_markets: Optional[GlobalMarketsData] = None,
    ):
        self.index_data = index_data or IndexData()
        self.index_minute = index_minute or IndexMinuteData()
        self.global_markets = global_markets or GlobalMarketsData()

    def context(self, market_date: str, as_of: Optional[str] = None) -> dict:
        moment = _shanghai_moment(market_date, as_of)
        clock = moment.strftime("%H:%M:%S")
        a_share = self._a_share_cards(market_date, clock, moment)
        overseas = self._overseas_cards(moment)
        return {
            "market_date": str(market_date),
            "as_of": clock[:5] if len(clock) >= 5 else clock,
            "as_of_full": moment.replace(tzinfo=None).isoformat(timespec="seconds"),
            "timezone": "Asia/Shanghai",
            "a_share": a_share,
            "overseas": overseas,
            "notes": (
                "A股优先按指数分钟与模拟时刻对齐；港股恒生有分钟时同步截断，"
                "美股/韩国仍为日线（仅当地已收盘可知），不伪造全球同分钟同步。"
            ),
        }

    def _daily_index_frame(self) -> pd.DataFrame:
        cache = self.index_data.cache
        if cache is None or len(cache) == 0:
            return pd.DataFrame(columns=["代码", "日期", "开盘", "最高", "最低", "收盘"])
        frame = cache.copy()
        frame["日期"] = pd.to_datetime(frame["日期"]).dt.normalize()
        return frame

    def _a_share_cards(self, market_date: str, clock: str, moment: datetime) -> list:
        daily = self._daily_index_frame()
        day = pd.Timestamp(market_date).normalize()
        cards = []
        status = market_session_status(
            moment, "Asia/Shanghai", "09:30", "15:00"
        )
        for code, name in INDEXES.items():
            code_daily = daily[daily["代码"] == code].sort_values("日期")
            prev_rows = code_daily[code_daily["日期"] < day]
            prev_close = float(prev_rows.iloc[-1]["收盘"]) if len(prev_rows) else None
            same_day = code_daily[code_daily["日期"] == day]
            same_day_row = same_day.iloc[-1] if len(same_day) else None

            minute_points = self.index_minute.points_as_of(code, market_date, clock)
            price = None
            point_time = None
            source = "empty"
            open_price = None

            if len(minute_points):
                last = minute_points.iloc[-1]
                price = float(last["收盘"])
                point_time = pd.Timestamp(last["时间"]).strftime("%H:%M")
                source = "minute"
                open_price = float(minute_points.iloc[0]["开盘"])
            elif same_day_row is not None and moment.time().replace(microsecond=0) >= A_SHARE_SESSION_CLOSE:
                # 收盘后允许揭示当日日线
                price = float(same_day_row["收盘"])
                open_price = float(same_day_row["开盘"])
                point_time = "15:00"
                source = "daily_close"
            elif same_day_row is not None and moment.time().replace(microsecond=0) >= A_SHARE_SESSION_OPEN:
                # 盘中无分钟时只揭示开盘价，不剧透高低收
                open_price = float(same_day_row["开盘"])
                price = open_price
                point_time = "09:30"
                source = "daily_open"
            elif prev_close is not None:
                price = prev_close
                point_time = None
                source = "prev_close"

            cards.append({
                "code": code,
                "name": name,
                "region": "CN",
                "region_label": "A股",
                "status": status,
                "status_label": STATUS_LABELS.get(status, status),
                "price": _round_price(price),
                "open": _round_price(open_price),
                "prev_close": _round_price(prev_close),
                "change_pct": _pct(price, prev_close),
                "time": point_time,
                "source": source,
                "bar_date": (
                    market_date if source in {"minute", "daily_close", "daily_open"}
                    else (
                        prev_rows.iloc[-1]["日期"].strftime("%Y-%m-%d")
                        if len(prev_rows) else None
                    )
                ),
            })
        return cards

    def _overseas_cards(self, as_of_shanghai: datetime) -> list:
        cache = self.global_markets.cache
        cards = []
        for code, meta in OVERSEAS_INDEXES.items():
            bars = (
                cache[cache["代码"] == code].sort_values("日期")
                if len(cache) else pd.DataFrame()
            )
            status = market_session_status(
                as_of_shanghai,
                meta["timezone"],
                meta["session_open"],
                meta["session_close"],
            )
            local = as_of_shanghai.astimezone(ZoneInfo(meta["timezone"]))
            known = last_known_daily_bar(
                bars, as_of_shanghai, meta["timezone"], meta["session_close"]
            )
            price = float(known["收盘"]) if known is not None else None
            bar_date = (
                pd.Timestamp(known["日期"]).strftime("%Y-%m-%d")
                if known is not None else None
            )
            prev = previous_close_from_bars(bars, known["日期"]) if known is not None else None
            point_time = None
            source = "daily"
            note = "日线；收盘后才更新当日点位"
            if known is None and len(bars):
                note = "缓存中尚无在该模拟时刻可知的收盘"

            # 港股恒生：有分钟缓存时按当地时刻截断，与 A 股分时同步
            if code == "HSI":
                hk_date = local.strftime("%Y-%m-%d")
                hk_clock = local.strftime("%H:%M:%S")
                minute_points = self.index_minute.points_as_of(
                    "hkHSI", hk_date, hk_clock
                )
                if len(minute_points):
                    last = minute_points.iloc[-1]
                    price = float(last["收盘"])
                    point_time = pd.Timestamp(last["时间"]).strftime("%H:%M")
                    bar_date = hk_date
                    source = "minute"
                    note = "分时；按模拟时刻截断"
                    # 涨跌相对前一完整日收盘
                    prev = previous_close_from_bars(bars, hk_date)
                    if prev is None and known is not None:
                        prev = previous_close_from_bars(bars, known["日期"])

            cards.append({
                "code": code,
                "name": meta["name"],
                "region": meta["region"],
                "region_label": meta["region_label"],
                "status": status,
                "status_label": STATUS_LABELS.get(status, status),
                "price": _round_price(price),
                "prev_close": _round_price(prev),
                "change_pct": _pct(price, prev),
                "bar_date": bar_date,
                "time": point_time,
                "local_time": local.strftime("%Y-%m-%d %H:%M"),
                "timezone": meta["timezone"],
                "source": source,
                "note": note,
            })
        return cards


def build_market_context(market_date: str, as_of: Optional[str] = None, **kwargs) -> dict:
    return MarketContextService(**kwargs).context(market_date, as_of)
