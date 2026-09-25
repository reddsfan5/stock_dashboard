"""国庆 / 中秋节假日效应 — 纯计算层（不读缓存、不联网）。

事件定义
--------
- 以“休市段”为一个事件：硬编码的节日锚点日期（中秋节当天、国庆 10-01）落在两个相邻
  交易日之间的空档里，且空档跳过了至少一个工作日（区别于普通周末）。
- T0 = 休市前最后一个交易日，T1 = 复牌第一个交易日；同一 (T0, T1) 的中秋与国庆合并为
  一个事件，类型记为“合并”，但节日标签同时保留“中秋+国庆”。
- 所有窗口以交易日计：T-k 为 T0 往前第 k 个交易日，T+k 为 T0 往后第 k 个交易日（T+1 即 T1）。

基准
----
把样本期内“每一个交易日”都当作 T0 计算同一套指标，得到无条件分布，用来判断节日窗口
是否与平常不同。p 值为自助抽样（从基准分布随机抽取同样本量求均值）的双侧近似值，
窗口重叠、样本量小，只作参考。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 节日硬编码（国务院办公厅放假安排；交易所周末调休日不开市）
# 锚点：中秋取中秋节当天，国庆取 10-01；官方休市区间仅作核对展示
# ---------------------------------------------------------------------------
MID_AUTUMN = {
    2017: ("2017-10-04", "2017-10-01~10-08（与国庆合并）"),
    2018: ("2018-09-24", "2018-09-22~09-24"),
    2019: ("2019-09-13", "2019-09-13~09-15"),
    2020: ("2020-10-01", "2020-10-01~10-08（与国庆合并）"),
    2021: ("2021-09-21", "2021-09-19~09-21"),
    2022: ("2022-09-10", "2022-09-10~09-12"),
    2023: ("2023-09-29", "2023-09-29~10-06（与国庆合并）"),
    2024: ("2024-09-17", "2024-09-15~09-17"),
    2025: ("2025-10-06", "2025-10-01~10-08（与国庆合并）"),
    2026: ("2026-09-25", "2026-09-25~09-27"),
}
NATIONAL_DAY = {
    2017: ("2017-10-01", "2017-10-01~10-08"),
    2018: ("2018-10-01", "2018-10-01~10-07"),
    2019: ("2019-10-01", "2019-10-01~10-07"),
    2020: ("2020-10-01", "2020-10-01~10-08"),
    2021: ("2021-10-01", "2021-10-01~10-07"),
    2022: ("2022-10-01", "2022-10-01~10-07"),
    2023: ("2023-10-01", "2023-09-29~10-06"),
    2024: ("2024-10-01", "2024-10-01~10-07"),
    2025: ("2025-10-01", "2025-10-01~10-08"),
    2026: ("2026-10-01", "2026-10-01~10-07"),
}

# 收益类指标（%）；RS（相对强弱）只对这些列做差
RETURN_COLS = ["节前10日%", "节前5日%", "T0当日%", "T1跳空%", "T1当日%", "T1日内%",
               "节后5日%", "节后10日%", "节后20日%"]
RISK_COLS = ["窗口最大回撤%", "窗口最大涨幅%", "节后10日最高%", "节后10日最低%"]
VOLUME_COLS = ["节前量比", "节后量比"]
PRE_COLS = ["节前10日%", "节前5日%", "T0当日%"]
PATH_RANGE = range(-10, 21)

# “全部(剔除2024)”用于稳健性：2024 年 9 月底政策行情会主导小样本均值
GROUPS = ["国庆(单独)", "中秋(单独)", "合并", "国庆(含合并)", "全部休市", "全部(剔除2024)"]


@dataclass
class HolidayEvent:
    year: int
    holidays: str            # "中秋" / "国庆" / "中秋+国庆"
    kind: str                # "国庆(单独)" / "中秋(单独)" / "合并"
    t0: pd.Timestamp
    t1: pd.Timestamp
    closed_weekdays: int     # 空档内被跳过的工作日数
    official: str            # 官方休市区间（核对用）

    def as_dict(self) -> dict:
        return {
            "年份": self.year, "节日": self.holidays, "类型": self.kind,
            "T0": self.t0.date().isoformat(), "T1": self.t1.date().isoformat(),
            "休市自然日": int((self.t1 - self.t0).days - 1),
            "跳过工作日": self.closed_weekdays, "官方休市": self.official,
        }


def identify_events(trading_days: Iterable, start_year: int = 2017,
                    end_year: int = 2026,
                    mid_autumn: Optional[Dict[int, tuple]] = None,
                    national_day: Optional[Dict[int, tuple]] = None) -> List[HolidayEvent]:
    """硬编码节日锚点 × 交易日历空档 → 休市事件列表（合并同一空档）。

    trading_days 需覆盖到锚点之后至少一个交易日（未来日期用交易日历补齐）。
    锚点找不到跨工作日的空档时直接抛错，避免静默漏事件。
    """
    mid_autumn = MID_AUTUMN if mid_autumn is None else mid_autumn
    national_day = NATIONAL_DAY if national_day is None else national_day
    days = pd.DatetimeIndex(sorted(pd.to_datetime(pd.Index(trading_days)).unique()))
    raw = []
    for name, table in (("中秋", mid_autumn), ("国庆", national_day)):
        for year, (anchor, official) in table.items():
            if year < start_year or year > end_year:
                continue
            a = pd.Timestamp(anchor)
            if a in days:
                raise ValueError(f"{year}{name} 锚点 {anchor} 是交易日，节日表或日历有误")
            pos = days.searchsorted(a)
            if pos == 0 or pos >= len(days):
                raise ValueError(f"{year}{name} 锚点 {anchor} 超出交易日历范围")
            t0, t1 = days[pos - 1], days[pos]
            skipped = int(np.busday_count(t0.date() + pd.Timedelta(days=1).to_pytimedelta(),
                                          t1.date()))
            if skipped < 1:
                raise ValueError(f"{year}{name} {t0.date()}→{t1.date()} 只是普通周末，非节日休市")
            raw.append((year, name, t0, t1, skipped, official))

    events: Dict[tuple, dict] = {}
    for year, name, t0, t1, skipped, official in raw:
        key = (t0, t1)
        ev = events.setdefault(key, {"year": year, "names": [], "skipped": skipped,
                                     "official": {}})
        ev["names"].append(name)
        ev["official"][name] = official
    out = []
    for (t0, t1), ev in sorted(events.items()):
        names = sorted(set(ev["names"]), key=lambda n: 0 if n == "中秋" else 1)
        if len(names) == 2:
            kind, label = "合并", "中秋+国庆"
            official = ev["official"]["国庆"]
        else:
            kind, label = f"{names[0]}(单独)", names[0]
            official = ev["official"][names[0]]
        out.append(HolidayEvent(ev["year"], label, kind, t0, t1, ev["skipped"], official))
    return out


# ---------------------------------------------------------------------------
# 逐日指标：把每个交易日都当作 T0（事件取其中几行，基准取全部）
# ---------------------------------------------------------------------------
def _max_drawdown(x: np.ndarray) -> float:
    peak = np.maximum.accumulate(x)
    return float(np.min(x / peak - 1.0))


def _max_runup(x: np.ndarray) -> float:
    trough = np.minimum.accumulate(x)
    return float(np.max(x / trough - 1.0))


def daily_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """df: 日期/开盘/最高/最低/收盘/成交量(手)，按日期升序。返回同索引的指标表（%）。"""
    d = df.sort_values("日期").reset_index(drop=True)
    c, o, h, l = d["收盘"], d["开盘"], d["最高"], d["最低"]
    v = d["成交量(手)"] if "成交量(手)" in d else pd.Series(np.nan, index=d.index)
    m = pd.DataFrame({"日期": d["日期"]})
    m["节前10日%"] = (c / c.shift(10) - 1) * 100
    m["节前5日%"] = (c / c.shift(5) - 1) * 100
    m["T0当日%"] = (c / c.shift(1) - 1) * 100
    m["T1跳空%"] = (o.shift(-1) / c - 1) * 100
    m["T1当日%"] = (c.shift(-1) / c - 1) * 100
    m["T1日内%"] = (c.shift(-1) / o.shift(-1) - 1) * 100
    for k in (5, 10, 20):
        m[f"节后{k}日%"] = (c.shift(-k) / c - 1) * 100
    # ±10 日窗口（T-10..T+10 共 21 根收盘）内的最大回撤 / 最大涨幅
    win = 21
    m["窗口最大回撤%"] = c.rolling(win).apply(_max_drawdown, raw=True).shift(-10) * 100
    m["窗口最大涨幅%"] = c.rolling(win).apply(_max_runup, raw=True).shift(-10) * 100
    # 节后 10 日内相对 T0 收盘的最高 / 最低（用盘中高低点）
    fut_h = h[::-1].rolling(10).max()[::-1].shift(-1)
    fut_l = l[::-1].rolling(10).min()[::-1].shift(-1)
    m["节后10日最高%"] = (fut_h / c - 1) * 100
    m["节后10日最低%"] = (fut_l / c - 1) * 100
    # 量比：T-5..T0（6 日）均量 / T-25..T-6（20 日）均量；节后 T1..T+5 同一分母
    base = v.shift(6).rolling(20, min_periods=20).mean()
    m["节前量比"] = v.rolling(6, min_periods=6).mean() / base
    m["节后量比"] = v[::-1].rolling(5, min_periods=5).mean()[::-1].shift(-1) / base
    return m


def relative_strength(main_m: pd.DataFrame, proxy_m: pd.DataFrame) -> pd.DataFrame:
    """代理指数收益 − 主指数收益（百分点）。两表需先按主指数日期对齐。"""
    out = pd.DataFrame({"日期": main_m["日期"]})
    for col in RETURN_COLS:
        out[col] = proxy_m[col].values - main_m[col].values
    return out


def align_to(main: pd.DataFrame, other: pd.DataFrame) -> pd.DataFrame:
    """把其他指数按主指数交易日左连接，保证 shift 的“第 k 个交易日”口径一致。"""
    cols = ["日期", "开盘", "最高", "最低", "收盘", "成交量(手)"]
    left = main[["日期"]].sort_values("日期").reset_index(drop=True)
    return left.merge(other[[c for c in cols if c in other]], on="日期", how="left")


def cum_paths(df: pd.DataFrame, positions: Sequence[int]) -> pd.DataFrame:
    """以 T0 收盘为 0 的累计收益路径（%），行=事件，列=k。"""
    c = df.sort_values("日期")["收盘"].to_numpy(dtype=float)
    rows = []
    for i in positions:
        row = {}
        for k in PATH_RANGE:
            j = i + k
            row[k] = (c[j] / c[i] - 1) * 100 if 0 <= j < len(c) and not np.isnan(c[j]) else np.nan
        rows.append(row)
    return pd.DataFrame(rows, columns=list(PATH_RANGE))


def baseline_path(df: pd.DataFrame, mask: np.ndarray) -> pd.Series:
    c = df.sort_values("日期").reset_index(drop=True)["收盘"]
    vals = {k: ((c.shift(-k) / c - 1) * 100)[mask].mean() for k in PATH_RANGE}
    return pd.Series(vals)


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def group_mask(events: pd.DataFrame, group: str) -> pd.Series:
    if group == "全部休市":
        return pd.Series(True, index=events.index)
    if group == "全部(剔除2024)":
        return events["年份"].astype(int) != 2024
    if group == "国庆(含合并)":
        return events["类型"].isin(["国庆(单独)", "合并"])
    return events["类型"] == group


def _hit(values: pd.Series, col: str) -> float:
    """胜率：收益类为 >0 占比；量比为 <1（缩量）占比。"""
    if values.empty:
        return np.nan
    if col in VOLUME_COLS:
        return float((values < 1).mean() * 100)
    if col in ("窗口最大回撤%", "窗口最大涨幅%"):
        return np.nan  # 恒负 / 恒正，胜率无意义
    return float((values > 0).mean() * 100)


def bootstrap_p(obs: pd.Series, base: pd.Series, n_boot: int, rng) -> float:
    base = base.dropna().to_numpy()
    n = len(obs)
    if n == 0 or len(base) < 30 or n_boot <= 0:
        return np.nan
    mu = base.mean()
    draws = rng.choice(base, size=(n_boot, n), replace=True).mean(axis=1)
    return float(np.mean(np.abs(draws - mu) >= abs(obs.mean() - mu)))


def summarize(events: pd.DataFrame, baseline: pd.DataFrame, cols: Sequence[str],
              n_boot: int = 5000, seed: int = 20260925,
              groups: Sequence[str] = GROUPS) -> pd.DataFrame:
    """每组 × 每指标：样本数/均值/中位数/胜率，以及基准同口径与自助法 p 值。"""
    rng = np.random.default_rng(seed)
    rows = []
    for col in cols:
        b = baseline[col].dropna()
        for g in groups:
            x = events.loc[group_mask(events, g), col].dropna()
            rows.append({
                "分组": g, "指标": col, "样本数": len(x),
                "均值": x.mean() if len(x) else np.nan,
                "中位数": x.median() if len(x) else np.nan,
                "胜率%": _hit(x, col),
                "基准均值": b.mean(), "基准中位数": b.median(), "基准胜率%": _hit(b, col),
                "均值差": (x.mean() - b.mean()) if len(x) else np.nan,
                "p值": bootstrap_p(x, b, n_boot, rng),
            })
    return pd.DataFrame(rows)


def percentile_of(value: float, dist: pd.Series) -> float:
    d = dist.dropna()
    if d.empty or value is None or np.isnan(value):
        return np.nan
    return float((d < value).mean() * 100 + (d == value).mean() * 50)
