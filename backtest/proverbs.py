"""市场口诀的可复现事件回测。

这里不把口语直接当作策略结论，而是先固定信号定义，再观察信号日之后
1/3/5 个交易日的收益。所有信号只使用信号日收盘前已知的数据；未来收益
统一按下一交易日开盘进入、持有期末收盘退出计算。

卖出类口诀（连续大涨离场、急涨慢跌出货）只衡量继续持有的后续表现，
不把结果解释为 A 股可直接实现的做空收益。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


MAIN_BOARD_PREFIXES = (
    "sh600", "sh601", "sh603", "sh605",
    "sz000", "sz001", "sz002", "sz003",
)


@dataclass(frozen=True)
class ProverbBacktestConfig:
    """七条口诀共用的研究口径。百分比参数均使用百分数单位。"""

    start_date: str = "2018-01-01"
    end_date: Optional[str] = None
    horizons: Tuple[int, ...] = (1, 3, 5)
    min_avg_amount: float = 50_000_000.0
    liquidity_window: int = 20

    market_move_pct: float = 1.0
    sideways_days: int = 3
    sideways_return_pct: float = 1.0
    sideways_range_pct: float = 3.0
    small_move_min_pct: float = 0.2
    small_move_max_pct: float = 2.0

    steady_rise_days: int = 5
    steady_rise_daily_max_pct: float = 1.5
    steady_rise_total_min_pct: float = 2.0
    surge_days: int = 3
    surge_daily_min_pct: float = 2.5
    surge_total_min_pct: float = 8.0

    fast_rise_days: int = 3
    fast_rise_min_pct: float = 8.0
    slow_fall_days: int = 5
    slow_fall_min_pct: float = -6.0
    slow_fall_max_pct: float = -0.5
    slow_fall_daily_floor_pct: float = -2.5
    slow_fall_min_down_days: int = 3


@dataclass(frozen=True)
class ProverbSpec:
    id: str
    title: str
    premise: str
    expected: str
    formula: str
    target_label: str
    target_kind: str
    target_low: Optional[float]
    target_high: Optional[float]
    direction: int
    primary_horizon: int
    baseline_key: str


PROVERB_SPECS: Tuple[ProverbSpec, ...] = (
    ProverbSpec(
        "p01", "一直小涨，会有大涨",
        "连续 5 日每天收涨且单日不超过 1.5%，5 日累计至少上涨 2%。",
        "随后出现大涨。",
        "连续5日 0%<日涨幅≤1.5%，且累计涨幅≥2%",
        "未来收益≥5%", "ge", 5.0, None, 1, 5, "all",
    ),
    ProverbSpec(
        "p02", "连续大涨，赶紧离场",
        "连续 3 日每天至少上涨 2.5%，3 日累计至少上涨 8%。",
        "继续持有的后续收益不再为正。",
        "连续3日 日涨幅≥2.5%，且累计涨幅≥8%",
        "未来收益≤0%", "le", None, 0.0, -1, 5, "all",
    ),
    ProverbSpec(
        "p03", "大盘跌，它横着走，会小涨",
        "对应指数当日跌至少 1%，个股近 3 日涨跌绝对值不超过 1%，区间振幅不超过 3%。",
        "随后小幅上涨。",
        "大盘≤-1%；个股3日|涨跌|≤1%、振幅≤3%",
        "未来收益在(0%,3%]", "between", 0.0, 3.0, 1, 3, "market_down",
    ),
    ProverbSpec(
        "p04", "大盘跌，它小涨，会大涨",
        "对应指数当日跌至少 1%，个股当日上涨 0.2%～2%。",
        "随后出现大涨。",
        "大盘≤-1%；个股当日涨幅在[0.2%,2%]",
        "未来收益≥5%", "ge", 5.0, None, 1, 5, "market_down",
    ),
    ProverbSpec(
        "p05", "大盘涨，它横盘，会小涨",
        "对应指数当日涨至少 1%，个股近 3 日涨跌绝对值不超过 1%，区间振幅不超过 3%。",
        "随后小幅上涨。",
        "大盘≥1%；个股3日|涨跌|≤1%、振幅≤3%",
        "未来收益在(0%,3%]", "between", 0.0, 3.0, 1, 3, "market_up",
    ),
    ProverbSpec(
        "p06", "大盘涨，它小跌，会大跌",
        "对应指数当日涨至少 1%，个股当日下跌 0.2%～2%。",
        "随后出现大跌。",
        "大盘≥1%；个股当日涨幅在[-2%,-0.2%]",
        "未来收益≤-5%", "le", None, -5.0, -1, 5, "market_up",
    ),
    ProverbSpec(
        "p07", "急涨慢跌是出货",
        "先在 3 日内上涨至少 8%，随后 5 日缓跌 0.5%～6%，至少 3 日收跌且无单日跌超 2.5%。",
        "慢跌结束后仍有明显下行。",
        "前3日累计≥8%；后5日累计[-6%,-0.5%]且至少3日收跌",
        "未来收益≤-3%", "le", None, -3.0, -1, 5, "all",
    ),
)


def _rolling(series: pd.Series, codes: pd.Series, window: int, method: str) -> pd.Series:
    roller = series.groupby(codes, sort=False).rolling(window, min_periods=window)
    result = getattr(roller, method)()
    return result.reset_index(level=0, drop=True).sort_index()


def _lookup_market(
    values: pd.Series,
    benchmark_codes: pd.Series,
    dates: pd.Series,
) -> np.ndarray:
    keys = pd.MultiIndex.from_arrays(
        [benchmark_codes.astype(str).to_numpy(), pd.to_datetime(dates).to_numpy()],
        names=["代码", "日期"],
    )
    return values.reindex(keys).to_numpy(dtype=float)


def _validate_columns(frame: pd.DataFrame, required: Iterable[str], label: str) -> None:
    missing = sorted(set(required).difference(frame.columns))
    if missing:
        raise ValueError(f"{label}缺少字段: {', '.join(missing)}")


def prepare_proverb_panel(
    stock_data: pd.DataFrame,
    index_data: pd.DataFrame,
    config: ProverbBacktestConfig,
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    """构造信号和未来收益共用面板；返回面板及数据质量诊断。"""

    _validate_columns(
        stock_data,
        ("代码", "日期", "开盘", "最高", "最低", "收盘", "成交额"),
        "股票日线",
    )
    _validate_columns(index_data, ("代码", "日期", "开盘", "收盘"), "指数日线")

    start = pd.Timestamp(config.start_date)
    end = pd.Timestamp(config.end_date) if config.end_date else pd.Timestamp(stock_data["日期"].max())
    buffer_start = start - pd.Timedelta(days=90)

    columns = ["代码", "日期", "开盘", "最高", "最低", "收盘", "成交额"]
    if "前收" in stock_data.columns:
        columns.append("前收")
    panel = stock_data.loc[
        stock_data["代码"].astype(str).str.startswith(MAIN_BOARD_PREFIXES)
        & (pd.to_datetime(stock_data["日期"]) >= buffer_start)
        & (pd.to_datetime(stock_data["日期"]) <= end),
        columns,
    ].copy()
    panel["日期"] = pd.to_datetime(panel["日期"]).dt.normalize()
    panel = panel.drop_duplicates(["代码", "日期"], keep="last")
    panel = panel.sort_values(["代码", "日期"]).reset_index(drop=True)

    numeric = ["开盘", "最高", "最低", "收盘", "成交额"]
    panel[numeric] = panel[numeric].apply(pd.to_numeric, errors="coerce")
    grouped = panel.groupby("代码", sort=False)
    previous_close = grouped["收盘"].shift(1)
    if "前收" in panel.columns:
        stored_previous = pd.to_numeric(panel["前收"], errors="coerce")
        previous_close = stored_previous.where(stored_previous > 0, previous_close)
    panel["日涨跌%"] = np.where(
        previous_close > 0, (panel["收盘"] / previous_close - 1.0) * 100.0, np.nan
    )
    panel["20日平均成交额"] = _rolling(
        panel["成交额"], panel["代码"], config.liquidity_window, "mean"
    )
    panel["流动性合格"] = panel["20日平均成交额"] >= config.min_avg_amount
    panel["基准代码"] = np.where(
        panel["代码"].astype(str).str.startswith("sh"), "sh000001", "sz399001"
    )

    index_frame = index_data[["代码", "日期", "开盘", "收盘"]].copy()
    index_frame["日期"] = pd.to_datetime(index_frame["日期"]).dt.normalize()
    index_frame = index_frame.drop_duplicates(["代码", "日期"], keep="last")
    index_frame = index_frame.sort_values(["代码", "日期"])
    index_frame["市场涨跌%"] = (
        index_frame.groupby("代码", sort=False)["收盘"].pct_change() * 100.0
    )
    market_return = index_frame.set_index(["代码", "日期"])["市场涨跌%"]
    panel["大盘涨跌%"] = _lookup_market(market_return, panel["基准代码"], panel["日期"])

    index_open = index_frame.set_index(["代码", "日期"])["开盘"]
    index_close = index_frame.set_index(["代码", "日期"])["收盘"]
    panel["入场日"] = grouped["日期"].shift(-1)
    panel["入场开盘"] = grouped["开盘"].shift(-1)
    panel["基准入场开盘"] = _lookup_market(index_open, panel["基准代码"], panel["入场日"])
    for horizon in config.horizons:
        exit_date = grouped["日期"].shift(-horizon)
        exit_close = grouped["收盘"].shift(-horizon)
        panel[f"退出日{horizon}"] = exit_date
        panel[f"未来收益{horizon}%"] = (
            exit_close / panel["入场开盘"] - 1.0
        ) * 100.0
        benchmark_exit = _lookup_market(index_close, panel["基准代码"], exit_date)
        panel[f"基准收益{horizon}%"] = (
            benchmark_exit / panel["基准入场开盘"] - 1.0
        ) * 100.0
        panel[f"超额收益{horizon}%"] = (
            panel[f"未来收益{horizon}%"] - panel[f"基准收益{horizon}%"]
        )

    analysis_rows = panel["日期"].between(start, end)
    matched_market = panel.loc[analysis_rows, "大盘涨跌%"].notna()
    diagnostics = {
        "stock_rows": int(analysis_rows.sum()),
        "stock_codes": int(panel.loc[analysis_rows, "代码"].nunique()),
        "start_date": panel.loc[analysis_rows, "日期"].min().strftime("%Y-%m-%d"),
        "end_date": panel.loc[analysis_rows, "日期"].max().strftime("%Y-%m-%d"),
        "duplicate_stock_dates": int(panel.duplicated(["代码", "日期"]).sum()),
        "key_price_missing_rate": round(
            float(panel.loc[analysis_rows, numeric[:-1]].isna().mean().mean()), 8
        ),
        "market_match_rate": round(float(matched_market.mean()), 6),
        "index_codes": int(index_frame["代码"].nunique()),
    }
    # 保留起始日前的缓冲窗口，让 20 日流动性和多日形态在研究首日也有完整历史；
    # 后续统计通过“研究期”过滤，缓冲行绝不会进入样本。
    panel["研究期"] = analysis_rows.to_numpy()
    return panel, diagnostics


def build_signal_masks(
    panel: pd.DataFrame,
    config: ProverbBacktestConfig,
) -> Tuple[Dict[str, pd.Series], Dict[str, pd.Series]]:
    """生成七个信号及各自基线池；信号只在一段连续条件首次成立时记录。"""

    codes = panel["代码"]
    grouped = panel.groupby("代码", sort=False)
    close = panel["收盘"]
    returns = panel["日涨跌%"]

    small_positive = (returns > 0) & (returns <= config.steady_rise_daily_max_pct)
    steady_count = _rolling(small_positive.astype(float), codes, config.steady_rise_days, "sum")
    steady_total = (close / grouped["收盘"].shift(config.steady_rise_days) - 1.0) * 100.0

    large_positive = returns >= config.surge_daily_min_pct
    surge_count = _rolling(large_positive.astype(float), codes, config.surge_days, "sum")
    surge_total = (close / grouped["收盘"].shift(config.surge_days) - 1.0) * 100.0

    sideways_return = (
        close / grouped["收盘"].shift(config.sideways_days - 1) - 1.0
    ) * 100.0
    sideways_high = _rolling(panel["最高"], codes, config.sideways_days, "max")
    sideways_low = _rolling(panel["最低"], codes, config.sideways_days, "min")
    sideways_range = (sideways_high / sideways_low - 1.0) * 100.0
    sideways = (
        sideways_return.abs() <= config.sideways_return_pct
    ) & (sideways_range <= config.sideways_range_pct)

    small_rise = returns.between(config.small_move_min_pct, config.small_move_max_pct)
    small_fall = returns.between(-config.small_move_max_pct, -config.small_move_min_pct)
    market_down = panel["大盘涨跌%"] <= -config.market_move_pct
    market_up = panel["大盘涨跌%"] >= config.market_move_pct

    # 急涨阶段在慢跌阶段之前，信号落在 5 日慢跌完成的当天。
    slow_days = config.slow_fall_days
    fast_days = config.fast_rise_days
    fast_end_close = grouped["收盘"].shift(slow_days)
    fast_start_close = grouped["收盘"].shift(slow_days + fast_days)
    fast_return = (fast_end_close / fast_start_close - 1.0) * 100.0
    slow_return = (close / fast_end_close - 1.0) * 100.0
    slow_down_count = _rolling((returns < 0).astype(float), codes, slow_days, "sum")
    slow_worst_day = _rolling(returns, codes, slow_days, "min")

    raw = {
        "p01": (
            (steady_count == config.steady_rise_days)
            & (steady_total >= config.steady_rise_total_min_pct)
        ),
        "p02": (
            (surge_count == config.surge_days)
            & (surge_total >= config.surge_total_min_pct)
        ),
        "p03": market_down & sideways,
        "p04": market_down & small_rise,
        "p05": market_up & sideways,
        "p06": market_up & small_fall,
        "p07": (
            (fast_return >= config.fast_rise_min_pct)
            & slow_return.between(config.slow_fall_min_pct, config.slow_fall_max_pct)
            & (slow_down_count >= config.slow_fall_min_down_days)
            & (slow_worst_day >= config.slow_fall_daily_floor_pct)
        ),
    }

    eligible = panel["流动性合格"] & panel["大盘涨跌%"].notna()
    signals: Dict[str, pd.Series] = {}
    for key, condition in raw.items():
        condition = condition.fillna(False) & eligible
        previous = condition.groupby(codes, sort=False).shift(1, fill_value=False)
        signals[key] = condition & ~previous

    baselines = {
        "all": eligible,
        "market_down": eligible & market_down,
        "market_up": eligible & market_up,
    }
    return signals, baselines


def _target_hit(values: pd.Series, spec: ProverbSpec) -> pd.Series:
    if spec.target_kind == "ge":
        return values >= float(spec.target_low)
    if spec.target_kind == "le":
        return values <= float(spec.target_high)
    if spec.target_kind == "between":
        return (values > float(spec.target_low)) & (values <= float(spec.target_high))
    raise ValueError(f"未知目标类型: {spec.target_kind}")


def _cluster_confidence_interval(
    values: pd.Series,
    dates: pd.Series,
) -> Tuple[float, float]:
    """按信号日聚类的 95% 正态近似区间，降低同日横截面相关的虚假精度。"""

    valid = values.notna() & dates.notna()
    x = values.loc[valid].astype(float)
    d = dates.loc[valid]
    if len(x) < 2:
        return np.nan, np.nan
    mean = float(x.mean())
    influence = (x - mean).groupby(d).sum()
    groups = len(influence)
    if groups < 2:
        return np.nan, np.nan
    variance = groups / (groups - 1) * float(np.square(influence).sum()) / (len(x) ** 2)
    radius = 1.96 * np.sqrt(max(variance, 0.0))
    return mean - radius, mean + radius


def _evidence_status(row: Mapping[str, float], direction: int) -> str:
    if row["samples"] < 100 or row["signal_dates"] < 30:
        return "样本不足"
    raw_right = direction * row["avg_return_pct"] > 0
    excess_right = direction * row["avg_excess_pct"] > 0
    ci_support = row["excess_ci_low_pct"] > 0 if direction > 0 else row["excess_ci_high_pct"] < 0
    ci_against = row["excess_ci_high_pct"] < 0 if direction > 0 else row["excess_ci_low_pct"] > 0
    lift_right = row["target_lift_pct_point"] > 0
    if raw_right and excess_right and ci_support and lift_right:
        return "支持"
    if excess_right and lift_right:
        return "部分支持"
    if (not raw_right) and ci_against and (not lift_right):
        return "不支持"
    return "证据不足"


def run_proverb_backtest(
    stock_data: pd.DataFrame,
    index_data: pd.DataFrame,
    config: Optional[ProverbBacktestConfig] = None,
) -> Dict[str, object]:
    """运行七项事件回测，返回可直接写 CSV/HTML 的结构化结果。"""

    config = config or ProverbBacktestConfig()
    panel, diagnostics = prepare_proverb_panel(stock_data, index_data, config)
    signals, baselines = build_signal_masks(panel, config)

    summary: List[Dict[str, object]] = []
    recent_events: Dict[str, List[Dict[str, object]]] = {}
    for spec in PROVERB_SPECS:
        signal_mask = signals[spec.id]
        baseline_mask = baselines[spec.baseline_key]
        for horizon in config.horizons:
            return_col = f"未来收益{horizon}%"
            benchmark_col = f"基准收益{horizon}%"
            excess_col = f"超额收益{horizon}%"
            valid = (
                panel["研究期"]
                & panel[return_col].notna()
                & panel[benchmark_col].notna()
                & panel[excess_col].notna()
            )
            events = panel.loc[signal_mask & valid]
            baseline = panel.loc[baseline_mask & valid]
            samples = len(events)
            hit_rate = float(_target_hit(events[return_col], spec).mean() * 100) if samples else np.nan
            baseline_rate = (
                float(_target_hit(baseline[return_col], spec).mean() * 100)
                if len(baseline) else np.nan
            )
            ci_low, ci_high = _cluster_confidence_interval(
                events[excess_col], events["日期"]
            )
            row: Dict[str, object] = {
                "id": spec.id,
                "title": spec.title,
                "horizon": horizon,
                "primary": horizon == spec.primary_horizon,
                "samples": samples,
                "signal_dates": int(events["日期"].nunique()),
                "avg_return_pct": float(events[return_col].mean()) if samples else np.nan,
                "median_return_pct": float(events[return_col].median()) if samples else np.nan,
                "win_rate_pct": float((events[return_col] > 0).mean() * 100) if samples else np.nan,
                "avg_benchmark_pct": float(events[benchmark_col].mean()) if samples else np.nan,
                "avg_excess_pct": float(events[excess_col].mean()) if samples else np.nan,
                "excess_win_rate_pct": (
                    float(((events[excess_col] * spec.direction) > 0).mean() * 100)
                    if samples else np.nan
                ),
                "excess_ci_low_pct": ci_low,
                "excess_ci_high_pct": ci_high,
                "target_hit_rate_pct": hit_rate,
                "baseline_hit_rate_pct": baseline_rate,
                "target_lift_pct_point": hit_rate - baseline_rate,
                "baseline_samples": int(len(baseline)),
                "status": "",
            }
            row["status"] = _evidence_status(row, spec.direction)
            summary.append(row)

        primary = spec.primary_horizon
        ret_col = f"未来收益{primary}%"
        bench_col = f"基准收益{primary}%"
        excess_col = f"超额收益{primary}%"
        cols = [
            "代码", "日期", "入场日", f"退出日{primary}", "日涨跌%", "大盘涨跌%",
            ret_col, bench_col, excess_col,
        ]
        events = panel.loc[
            signal_mask & panel["研究期"] & panel[ret_col].notna(), cols
        ].copy()
        events = events.sort_values("日期", ascending=False).head(100)
        events = events.rename(columns={
            "日期": "信号日",
            f"退出日{primary}": "退出日",
            "日涨跌%": "信号日个股涨跌%",
            "大盘涨跌%": "信号日大盘涨跌%",
            ret_col: "未来收益%",
            bench_col: "基准收益%",
            excess_col: "超额收益%",
        })
        for column in ("信号日", "入场日", "退出日"):
            events[column] = pd.to_datetime(events[column]).dt.strftime("%Y-%m-%d")
        recent_events[spec.id] = events.round(4).to_dict("records")

    return {
        "config": asdict(config),
        "diagnostics": diagnostics,
        "specs": [asdict(spec) for spec in PROVERB_SPECS],
        "summary": summary,
        "recent_events": recent_events,
    }


def summary_frame(result: Mapping[str, object]) -> pd.DataFrame:
    """将结构化结果转换为稳定列顺序的汇总表。"""

    return pd.DataFrame(result["summary"])[[
        "id", "title", "horizon", "primary", "samples", "signal_dates",
        "avg_return_pct", "median_return_pct", "win_rate_pct",
        "avg_benchmark_pct", "avg_excess_pct", "excess_win_rate_pct",
        "excess_ci_low_pct", "excess_ci_high_pct", "target_hit_rate_pct",
        "baseline_hit_rate_pct", "target_lift_pct_point", "baseline_samples", "status",
    ]]
