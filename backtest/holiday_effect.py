"""A 股节假日效应 — 纯计算层（不读缓存、不联网），节日由注册表配置驱动。

事件定义
--------
- 以“休市段”为一个事件：注册表中的节日锚点日期落在两个相邻交易日之间的空档里，且空档
  跳过至少 ``min_weekdays`` 个工作日（区别于普通周末）。只落在周末、没有额外休市的锚点
  （如 2015 中秋 09-27 周日）记为“仅周末”，不形成事件。
- T0 = 休市前最后一个交易日，T1 = 复牌第一个交易日；多个节日落在同一 (T0, T1) 时合并为
  一个事件，类型“合并”，节日标签同时保留（如“中秋+国庆”）。
- 所有窗口以交易日计：T-k 为 T0 往前第 k 个交易日，T+k 为 T0 往后第 k 个交易日（T+1 即 T1）。

扩展新节日
----------
在 ``HOLIDAY_REGISTRY`` 追加一个 ``HolidaySpec`` 即可（如清明、端午），分组、统计、页面
都会自动出现该节日。

基准
----
把样本期内“每一个交易日”都当作 T0 计算同一套指标，得到无条件分布。p 值为自助抽样
双侧近似值，窗口重叠、样本量小，只作参考。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# 节日注册表
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class HolidaySpec:
    key: str                     # 英文键（JSON / CSS 用）
    name: str                    # 中文名（表格、分组名）
    anchors: Dict[int, str]      # 年份 → 锚点日期（该日必须处于休市空档内）
    official: Dict[int, str] = field(default_factory=dict)  # 官方休市区间，仅核对展示
    min_weekdays: int = 1        # 空档至少跳过的工作日数（元旦可短至 1 天）
    color: str = ""              # 页面日 K 休市色带颜色（留空则页面按调色板自动分配）


def fixed_date(month: int, day: int, years: Iterable[int]) -> Dict[int, str]:
    """公历固定日期节日的锚点（元旦、五一、国庆）。"""
    return {y: f"{y}-{month:02d}-{day:02d}" for y in years}


YEARS = range(2015, 2028)

# 春节（农历正月初一）公历日期，来自万年历，2015–2027
SPRING_FESTIVAL = {
    2015: "2015-02-19", 2016: "2016-02-08", 2017: "2017-01-28", 2018: "2018-02-16",
    2019: "2019-02-05", 2020: "2020-01-25", 2021: "2021-02-12", 2022: "2022-02-01",
    2023: "2023-01-22", 2024: "2024-02-10", 2025: "2025-01-29", 2026: "2026-02-17",
    2027: "2027-02-06",
}
# 中秋（农历八月十五）公历日期，2015–2027
MID_AUTUMN = {
    2015: "2015-09-27", 2016: "2016-09-15", 2017: "2017-10-04", 2018: "2018-09-24",
    2019: "2019-09-13", 2020: "2020-10-01", 2021: "2021-09-21", 2022: "2022-09-10",
    2023: "2023-09-29", 2024: "2024-09-17", 2025: "2025-10-06", 2026: "2026-09-25",
    2027: "2027-09-15",
}
MID_AUTUMN_OFFICIAL = {
    2015: "09-26~09-27（仅周末）", 2016: "09-15~09-17", 2017: "10-01~10-08（与国庆合并）",
    2018: "09-22~09-24", 2019: "09-13~09-15", 2020: "10-01~10-08（与国庆合并）",
    2021: "09-19~09-21", 2022: "09-10~09-12", 2023: "09-29~10-06（与国庆合并）",
    2024: "09-15~09-17", 2025: "10-01~10-08（与国庆合并）", 2026: "09-25~09-27",
}
NATIONAL_DAY_OFFICIAL = {
    2015: "10-01~10-07", 2016: "10-01~10-07", 2017: "10-01~10-08", 2018: "10-01~10-07",
    2019: "10-01~10-07", 2020: "10-01~10-08", 2021: "10-01~10-07", 2022: "10-01~10-07",
    2023: "09-29~10-06", 2024: "10-01~10-07", 2025: "10-01~10-08", 2026: "10-01~10-07",
}

HOLIDAY_REGISTRY: List[HolidaySpec] = [
    HolidaySpec("new_year", "元旦", fixed_date(1, 1, YEARS), color="#0ea5e9"),
    HolidaySpec("spring", "春节", SPRING_FESTIVAL, color="#f97316"),
    HolidaySpec("labor", "五一", fixed_date(5, 1, YEARS), color="#eab308"),
    HolidaySpec("mid_autumn", "中秋", MID_AUTUMN, MID_AUTUMN_OFFICIAL, color="#8b5cf6"),
    HolidaySpec("national", "国庆", fixed_date(10, 1, YEARS), NATIONAL_DAY_OFFICIAL, color="#e11d48"),
    # 以后扩展示例（补齐各年锚点即可）：
    # HolidaySpec("qingming", "清明", {2015: "2015-04-05", ...}),
]

MERGED = "合并"
MERGED_COLOR = "#14b8a6"
ALL_GROUP = "全部"
LONG_GROUP = "长假(休市≥5天)"
LONG_DAYS = 5

# 收益类指标（%）；RS（相对强弱）只对这些列做差
RETURN_COLS = ["节前10日%", "节前5日%", "T0当日%", "T1跳空%", "T1当日%", "T1日内%",
               "节后5日%", "节后10日%", "节后20日%"]
RISK_COLS = ["窗口最大回撤%", "窗口最大涨幅%", "节后10日最高%", "节后10日最低%"]
VOLUME_COLS = ["节前量比", "节后量比"]
PATH_RANGE = range(-10, 21)


@dataclass
class HolidayEvent:
    year: int
    tags: Tuple[str, ...]    # 节日标签（合并时多个）
    t0: pd.Timestamp
    t1: pd.Timestamp
    closed_weekdays: int
    official: str

    @property
    def kind(self) -> str:
        return MERGED if len(self.tags) > 1 else self.tags[0]

    @property
    def holidays(self) -> str:
        return "+".join(self.tags)

    def as_dict(self) -> dict:
        return {
            "年份": self.year, "节日": self.holidays, "类型": self.kind,
            "T0": self.t0.date().isoformat(), "T1": self.t1.date().isoformat(),
            "休市自然日": int((self.t1 - self.t0).days - 1),
            "跳过工作日": self.closed_weekdays, "官方休市": self.official,
        }


def identify(trading_days: Iterable, start_year: int, end_year: int,
             registry: Optional[Sequence[HolidaySpec]] = None
             ) -> Tuple[List[HolidayEvent], List[str]]:
    """注册表锚点 × 交易日历空档 → (休市事件列表, 未形成事件的说明)。

    锚点本身是交易日时抛错（节日表或日历有误，不能静默吞掉）；锚点超出日历范围或只落在
    周末时跳过并写入说明。
    """
    registry = HOLIDAY_REGISTRY if registry is None else registry
    order = {spec.name: i for i, spec in enumerate(registry)}
    days = pd.DatetimeIndex(sorted(pd.to_datetime(pd.Index(trading_days)).unique()))
    notes: List[str] = []
    grouped: Dict[tuple, dict] = {}
    for spec in registry:
        for year, anchor in sorted(spec.anchors.items()):
            if year < start_year or year > end_year:
                continue
            a = pd.Timestamp(anchor)
            if a in days:
                raise ValueError(f"{year}{spec.name} 锚点 {anchor} 是交易日，节日表或日历有误")
            pos = days.searchsorted(a)
            if pos == 0 or pos >= len(days):
                notes.append(f"{year}{spec.name}（{anchor}）超出交易日历范围，跳过")
                continue
            t0, t1 = days[pos - 1], days[pos]
            skipped = int(np.busday_count((t0 + pd.Timedelta(days=1)).date(), t1.date()))
            if skipped < spec.min_weekdays:
                notes.append(f"{year}{spec.name}（{anchor}）只落在周末，未形成额外休市")
                continue
            ev = grouped.setdefault((t0, t1), {"year": year, "tags": [], "skipped": skipped,
                                               "official": []})
            ev["tags"].append(spec.name)
            if year in spec.official:
                ev["official"].append(f"{spec.name} {spec.official[year]}")
    events = []
    for (t0, t1), ev in sorted(grouped.items()):
        tags = tuple(sorted(set(ev["tags"]), key=lambda n: order[n]))
        events.append(HolidayEvent(ev["year"], tags, t0, t1, ev["skipped"],
                                   "；".join(ev["official"])))
    return events, notes


def identify_events(trading_days: Iterable, start_year: int = 2015, end_year: int = 2026,
                    registry: Optional[Sequence[HolidaySpec]] = None) -> List[HolidayEvent]:
    return identify(trading_days, start_year, end_year, registry)[0]


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
    win = 21  # T-10..T+10
    m["窗口最大回撤%"] = c.rolling(win).apply(_max_drawdown, raw=True).shift(-10) * 100
    m["窗口最大涨幅%"] = c.rolling(win).apply(_max_runup, raw=True).shift(-10) * 100
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
    """把其他指数按主指数交易日左连接，保证“第 k 个交易日”口径一致。"""
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
    return pd.Series({k: ((c.shift(-k) / c - 1) * 100)[mask].mean() for k in PATH_RANGE})


# ---------------------------------------------------------------------------
# 分组与汇总
# ---------------------------------------------------------------------------
def group_names(events: pd.DataFrame, registry: Optional[Sequence[HolidaySpec]] = None) -> List[str]:
    """全部 → 长假 → 注册表各节日（含合并事件）→ 合并。只返回有事件的分组。"""
    registry = HOLIDAY_REGISTRY if registry is None else registry
    out = [ALL_GROUP]
    if "休市自然日" in events and (events["休市自然日"] >= LONG_DAYS).any():
        out.append(LONG_GROUP)
    for spec in registry:
        if group_mask(events, spec.name).any():
            out.append(spec.name)
    if (events["类型"] == MERGED).any():
        out.append(MERGED)
    return out


def group_mask(events: pd.DataFrame, group: str,
               exclude_years: Sequence[int] = ()) -> pd.Series:
    """节日分组包含带该标签的合并事件（如“国庆”含 2017/2020/2023/2025 的中秋+国庆）。"""
    if group == ALL_GROUP:
        mask = pd.Series(True, index=events.index)
    elif group == LONG_GROUP:
        mask = events["休市自然日"] >= LONG_DAYS
    elif group == MERGED:
        mask = events["类型"] == MERGED
    else:
        mask = events["节日"].astype(str).str.split("+").apply(lambda tags: group in tags)
    if exclude_years:
        mask &= ~events["年份"].astype(int).isin(list(exclude_years))
    return mask


def _hit(values: pd.Series, col: str) -> float:
    """胜率：收益类为 >0 占比；量比为 <1（缩量）占比；窗口极值恒正/负时无意义。"""
    if values.empty or col in ("窗口最大回撤%", "窗口最大涨幅%"):
        return np.nan
    if col in VOLUME_COLS:
        return float((values < 1).mean() * 100)
    return float((values > 0).mean() * 100)


def bootstrap_p(obs: pd.Series, base: pd.Series, n_boot: int, rng) -> float:
    base = base.dropna().to_numpy()
    n = len(obs)
    if n == 0 or len(base) < 30 or n_boot <= 0:
        return np.nan
    mu = base.mean()
    draws = rng.choice(base, size=(n_boot, n), replace=True).mean(axis=1)
    return float(np.mean(np.abs(draws - mu) >= abs(obs.mean() - mu)))


VARIANTS = {"all": "全部年份", "ex": "剔除异常年"}


def summarize(events: pd.DataFrame, baseline: pd.DataFrame, cols: Sequence[str],
              groups: Sequence[str], exclude_years: Sequence[int] = (),
              n_boot: int = 5000, seed: int = 20260925) -> pd.DataFrame:
    """每组 × 口径（全部年份 / 剔除异常年）× 指标：样本数、均值、中位数、胜率与基准对照。"""
    rng = np.random.default_rng(seed)
    rows = []
    for col in cols:
        b = baseline[col].dropna()
        for g in groups:
            for vkey, vname in VARIANTS.items():
                if vkey == "ex" and not exclude_years:
                    continue
                ex = exclude_years if vkey == "ex" else ()
                x = events.loc[group_mask(events, g, ex), col].dropna()
                rows.append({
                    "分组": g, "口径": vkey, "指标": col, "样本数": len(x),
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
