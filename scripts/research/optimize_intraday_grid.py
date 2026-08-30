#!/usr/bin/env python3
"""对 T+0 标的最近若干交易日执行日内网格参数搜索。

默认以 520500 最近 10 个有分钟数据的交易日为样本，将前 7 日用于参数排序、
后 3 日只用于时间外验证。每个交易日都以相同总资产和仓位比例独立启动，避免把
隔夜跳空误当成可成交的日内路径。

示例
----
$ python -m scripts.research.optimize_intraday_grid
$ python -m scripts.research.optimize_intraday_grid --method random --max-evals 80
$ python -m scripts.research.optimize_intraday_grid --step-mode pct \
    --steps 0.10,0.15,0.20,0.30 --turn-values 0,0.05,0.10

输出
----
output/grid_search_520500.csv        参数排名与训练/验证汇总
output/grid_search_520500_daily.csv  每组参数逐日结果
output/grid_search_520500_best.json  最佳参数、数据质量与逐日审计信息
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)

from backtest.intraday_grid import (  # noqa: E402
    IntradayGridConfig,
    PRICE_TRIGGERED,
    TRANSACTION_DRIVEN,
    simulate_intraday_grid,
)
from backtest.validation import chronological_holdout  # noqa: E402
from data.minute import MinuteData  # noqa: E402


DEFAULT_DIFF_STEPS = [0.002, 0.003, 0.004, 0.005, 0.007, 0.010]
DEFAULT_PCT_STEPS = [0.10, 0.15, 0.20, 0.30, 0.50]
DEFAULT_LOTS = [500, 1_000, 2_000]
DEFAULT_DIFF_TURNS = [0.0, 0.001, 0.002]
DEFAULT_PCT_TURNS = [0.0, 0.05, 0.10]
DEFAULT_ORDER_MODES = ["counterparty", "passive"]
DEFAULT_BASE_PRICES = ["grid", "fill"]


@dataclass(frozen=True)
class Candidate:
    """一组可比较的策略参数；未使用的到价型参数保持中性值。"""

    mode: str
    step_mode: str
    step: float
    lot_shares: int
    order_price_mode: str = "-"
    turn_value: float = 0.0
    base_update_price: str = "-"

    @property
    def key(self) -> str:
        mode = "td" if self.mode == TRANSACTION_DRIVEN else "pt"
        return (
            f"{mode}_{self.step_mode}_{self.step:g}_{self.lot_shares}_"
            f"{self.order_price_mode}_{self.turn_value:g}_{self.base_update_price}"
        )


def normalize_code(code: str) -> str:
    """把 520500 / sh520500 统一为分钟缓存使用的带市场前缀代码。"""
    value = str(code).strip().lower()
    if value.startswith(("sh", "sz")):
        return value
    if not value.isdigit() or len(value) != 6:
        raise ValueError("证券代码应为 6 位数字，或 sh/sz + 6 位数字")
    return ("sh" if value.startswith(("5", "6", "9")) else "sz") + value


def parse_number_list(value: str | None, cast=float) -> List:
    if value is None:
        return []
    result = [cast(item.strip()) for item in value.split(",") if item.strip()]
    if not result:
        raise ValueError("参数列表不能为空")
    return result


def validate_minute_frame(
    frame: pd.DataFrame, date: str, min_bars: int = 200
) -> Tuple[pd.DataFrame, Dict]:
    """校验单日 OHLC 完整性，返回排序后的数据和可写入结果的质量摘要。"""
    required = ["时间", "开盘", "最高", "最低", "收盘"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"{date} 分钟数据缺列: {', '.join(missing)}")
    clean = frame.copy()
    clean["时间"] = pd.to_datetime(clean["时间"], errors="coerce")
    for column in ["开盘", "最高", "最低", "收盘"]:
        clean[column] = pd.to_numeric(clean[column], errors="coerce")
    if len(clean) < min_bars:
        raise ValueError(f"{date} 只有 {len(clean)} 根分钟线，低于门槛 {min_bars}")
    if clean[required].isna().any().any():
        bad = clean[required].isna().sum()
        details = ", ".join(f"{name}={count}" for name, count in bad.items() if count)
        raise ValueError(f"{date} 存在空值: {details}")
    duplicate_count = int(clean["时间"].duplicated().sum())
    if duplicate_count:
        raise ValueError(f"{date} 存在 {duplicate_count} 个重复分钟时间")
    prices = clean[["开盘", "最高", "最低", "收盘"]]
    if (prices <= 0).any().any():
        raise ValueError(f"{date} 存在非正价格")
    invalid = (
        (clean["最低"] > clean["最高"])
        | (clean["最高"] < clean[["开盘", "收盘"]].max(axis=1))
        | (clean["最低"] > clean[["开盘", "收盘"]].min(axis=1))
    )
    if invalid.any():
        raise ValueError(f"{date} 存在 {int(invalid.sum())} 根非法 OHLC")

    clean = clean.sort_values("时间").reset_index(drop=True)
    warnings = []
    first_time = clean["时间"].iloc[0].strftime("%H:%M")
    last_time = clean["时间"].iloc[-1].strftime("%H:%M")
    if first_time != "09:31":
        warnings.append(f"首根为 {first_time}，不是 09:31")
    if last_time != "15:00":
        warnings.append(f"末根为 {last_time}，不是 15:00")
    return clean, {
        "date": date,
        "rows": int(len(clean)),
        "first_time": first_time,
        "last_time": last_time,
        "low": round(float(clean["最低"].min()), 4),
        "high": round(float(clean["最高"].max()), 4),
        "range_pct": round(
            (float(clean["最高"].max()) / float(clean["最低"].min()) - 1) * 100, 3
        ),
        "warnings": warnings,
    }


def load_recent_days(
    code: str, days: int, min_bars: int = 200
) -> Tuple[List[Tuple[str, pd.DataFrame]], List[Dict]]:
    minute = MinuteData()
    dates = minute.dates_of(code)
    if len(dates) < days:
        raise ValueError(f"{code} 仅有 {len(dates)} 个交易日分钟数据，少于要求的 {days} 日")
    selected = dates[-days:]
    frames: List[Tuple[str, pd.DataFrame]] = []
    quality = []
    for date in selected:
        clean, report = validate_minute_frame(minute.get_minute(code, date), date, min_bars)
        frames.append((date, clean))
        quality.append(report)
    return frames, quality


def split_dates(dates: Sequence[str], validation_days: int) -> Tuple[List[str], List[str]]:
    """向后兼容的无隔离期切分入口。"""
    split = chronological_holdout(dates, validation_days)
    return list(split.train), list(split.validation)


def build_candidates(
    step_mode: str,
    steps: Sequence[float],
    lots: Sequence[int],
    order_modes: Sequence[str],
    turn_values: Sequence[float],
    base_update_prices: Sequence[str],
) -> List[Candidate]:
    candidates: List[Candidate] = []
    for step in steps:
        for lot in lots:
            candidates.append(Candidate(TRANSACTION_DRIVEN, step_mode, step, lot))
            for order_mode in order_modes:
                for turn_value in turn_values:
                    for base_price in base_update_prices:
                        candidates.append(Candidate(
                            PRICE_TRIGGERED,
                            step_mode,
                            step,
                            lot,
                            order_mode,
                            turn_value,
                            base_price,
                        ))
    return candidates


def filter_candidates_for_tick(
    candidates: Iterable[Candidate], tick_size: float, reference_price: float
) -> Tuple[List[Candidate], List[str]]:
    """剔除小于一个报价单位或固定差价不是报价单位整数倍的参数。"""
    accepted, rejected = [], []
    for candidate in candidates:
        if candidate.step_mode == "diff":
            step_ticks = candidate.step / tick_size
            turn_ticks = candidate.turn_value / tick_size
            valid = (
                abs(step_ticks - round(step_ticks)) <= 1e-8
                and (
                    candidate.turn_value == 0
                    or abs(turn_ticks - round(turn_ticks)) <= 1e-8
                )
            )
            reason = "固定价差或反转确认值不是最小报价单位的整数倍"
        else:
            step_valid = reference_price * candidate.step / 100.0 + 1e-12 >= tick_size
            turn_valid = (
                candidate.turn_value == 0
                or reference_price * candidate.turn_value / 100.0 + 1e-12 >= tick_size
            )
            valid = step_valid and turn_valid
            reason = "按样本最低开盘价换算后，网格或反转确认不足一个最小报价单位"
        if valid:
            accepted.append(candidate)
        else:
            rejected.append(f"{candidate.key}: {reason}")
    return accepted, rejected


def frame_to_points(frame: pd.DataFrame) -> List[Dict]:
    return [
        {
            "time": row["时间"].strftime("%H:%M"),
            "open": float(row["开盘"]),
            "high": float(row["最高"]),
            "low": float(row["最低"]),
            "close": float(row["收盘"]),
        }
        for _, row in frame.iterrows()
    ]


def account_for_day(
    capital: float, inventory_ratio: float, open_price: float
) -> Tuple[float, int, int]:
    shares = math.floor(capital * inventory_ratio / open_price / 100) * 100
    cash = capital - shares * open_price
    max_position = math.floor(capital / open_price / 100) * 100
    return cash, shares, max_position


def config_for_day(candidate: Candidate, frame: pd.DataFrame, args) -> IntradayGridConfig:
    open_price = float(frame.iloc[0]["开盘"])
    cash, shares, max_position = account_for_day(args.capital, args.inventory_ratio, open_price)
    turn_enabled = candidate.mode == PRICE_TRIGGERED and candidate.turn_value > 0
    return IntradayGridConfig(
        mode=candidate.mode,
        initial_cash=cash,
        initial_shares=shares,
        base_price=open_price,
        step_mode=candidate.step_mode,
        buy_step=candidate.step,
        sell_step=candidate.step,
        lot_shares=candidate.lot_shares,
        min_position=0,
        max_position=max_position,
        rebound_enabled=turn_enabled,
        pullback_enabled=turn_enabled,
        rebound_value=candidate.turn_value if turn_enabled else 0.1,
        pullback_value=candidate.turn_value if turn_enabled else 0.1,
        turn_mode=candidate.step_mode,
        order_price_mode=(
            candidate.order_price_mode if candidate.mode == PRICE_TRIGGERED else "counterparty"
        ),
        order_offset_bps=args.passive_offset_bps,
        base_update_timing="filled",
        base_update_price=(
            candidate.base_update_price if candidate.mode == PRICE_TRIGGERED else "grid"
        ),
        commission_rate=args.commission_bps / 10_000.0,
        min_commission=args.min_commission,
        sell_tax_rate=args.sell_tax_bps / 10_000.0,
        slippage_rate=(
            args.slippage_bps / 10_000.0 if candidate.mode == PRICE_TRIGGERED else 0.0
        ),
        max_trades=args.max_trades,
    )


def run_one_day(candidate: Candidate, date: str, frame: pd.DataFrame, args) -> Dict:
    config = config_for_day(candidate, frame, args)
    result = simulate_intraday_grid(frame_to_points(frame), config)
    summary = result["summary"]
    initial_equity = float(summary["initial_equity"])
    final_equity = float(summary["final_equity"])
    turnover = sum(float(trade["amount"]) for trade in result["trades"])
    excess_curve = [
        (float(row["equity"]) - float(row["hold_equity"])) / initial_equity * 100.0
        for row in result["timeline"]
    ]
    excess_peak = 0.0
    excess_drawdown = 0.0
    for value in excess_curve:
        excess_peak = max(excess_peak, value)
        excess_drawdown = min(excess_drawdown, value - excess_peak)
    start_inventory_ratio = config.initial_shares * float(summary["start_price"]) / initial_equity
    end_inventory_ratio = (
        int(summary["final_shares"]) * float(summary["final_price"]) / final_equity
        if final_equity else 0.0
    )
    return {
        "candidate_id": candidate.key,
        "date": date,
        "mode": candidate.mode,
        "step_mode": candidate.step_mode,
        "step": candidate.step,
        "lot_shares": candidate.lot_shares,
        "order_price_mode": candidate.order_price_mode,
        "turn_value": candidate.turn_value,
        "base_update_price": candidate.base_update_price,
        "initial_equity": initial_equity,
        "final_equity": final_equity,
        "return_pct": float(summary["return_pct"]),
        "excess_pct": float(summary["excess_vs_hold"]) / initial_equity * 100.0,
        "max_drawdown_pct": float(summary["max_drawdown_pct"]),
        "excess_drawdown_pct": excess_drawdown,
        "total_fee": float(summary["total_fee"]),
        "fee_bps": float(summary["total_fee"]) / initial_equity * 10_000.0,
        "turnover_pct": turnover / initial_equity * 100.0,
        "trade_count": int(summary["trade_count"]),
        "pending_count": int(summary["pending_count"]),
        "final_shares": int(summary["final_shares"]),
        "inventory_drift_pct": abs(end_inventory_ratio - start_inventory_ratio) * 100.0,
    }


def aggregate(rows: pd.DataFrame, prefix: str) -> Dict:
    if rows.empty:
        raise ValueError(f"{prefix} 统计没有数据")
    excess = rows["excess_pct"]
    return {
        f"{prefix}_days": int(len(rows)),
        f"{prefix}_mean_excess_pct": float(excess.mean()),
        f"{prefix}_median_excess_pct": float(excess.median()),
        f"{prefix}_std_excess_pct": float(excess.std(ddof=0)),
        f"{prefix}_worst_excess_pct": float(excess.min()),
        f"{prefix}_positive_excess_rate": float((excess > 0).mean() * 100.0),
        f"{prefix}_mean_return_pct": float(rows["return_pct"].mean()),
        f"{prefix}_mean_drawdown_pct": float(rows["max_drawdown_pct"].mean()),
        f"{prefix}_mean_excess_drawdown_pct": float(rows["excess_drawdown_pct"].mean()),
        f"{prefix}_avg_trades": float(rows["trade_count"].mean()),
        f"{prefix}_avg_fee_bps": float(rows["fee_bps"].mean()),
        f"{prefix}_avg_turnover_pct": float(rows["turnover_pct"].mean()),
        f"{prefix}_mean_inventory_drift_pct": float(rows["inventory_drift_pct"].mean()),
        f"{prefix}_pending_days": int((rows["pending_count"] > 0).sum()),
    }


def robust_score(metrics: Dict, min_train_trades: float) -> float:
    """只用训练段排名；收益、尾部、波动、回撤、换手与库存漂移共同约束。"""
    if metrics["train_avg_trades"] < min_train_trades:
        return -1_000_000.0
    downside = abs(min(metrics["train_worst_excess_pct"], 0.0))
    return (
        metrics["train_mean_excess_pct"]
        - 0.50 * downside
        - 0.15 * metrics["train_std_excess_pct"]
        - 0.25 * abs(metrics["train_mean_excess_drawdown_pct"])
        - 0.002 * metrics["train_avg_trades"]
        - 0.05 * metrics["train_mean_inventory_drift_pct"]
    )


def rank_candidates(rows: Sequence[Dict]) -> pd.DataFrame:
    """只使用训练段指标排序，验证结果不得参与选优或破除平局。"""
    ranking = pd.DataFrame(rows).sort_values(
        ["score", "train_mean_excess_pct", "candidate_id"],
        ascending=[False, False, True],
    ).reset_index(drop=True)
    ranking.insert(0, "rank", range(1, len(ranking) + 1))
    return ranking


def evaluate_candidate(
    candidate: Candidate,
    frames: Sequence[Tuple[str, pd.DataFrame]],
    train_dates: Sequence[str],
    validation_dates: Sequence[str],
    args,
) -> Tuple[Dict, List[Dict]]:
    daily = [run_one_day(candidate, date, frame, args) for date, frame in frames]
    daily_frame = pd.DataFrame(daily)
    train = daily_frame[daily_frame["date"].isin(train_dates)]
    validation = daily_frame[daily_frame["date"].isin(validation_dates)]
    metrics = asdict(candidate)
    metrics["candidate_id"] = candidate.key
    metrics.update(aggregate(train, "train"))
    metrics.update(aggregate(validation, "validation"))
    metrics["score"] = robust_score(metrics, args.min_train_trades)
    return metrics, daily


def _json_value(value):
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="T+0 标的最近若干交易日网格参数搜索（默认 520500 最近10日）"
    )
    parser.add_argument("--code", default="520500", help="证券代码，默认 520500")
    parser.add_argument("--days", type=int, default=10, help="最近交易日数，默认 10")
    parser.add_argument("--validation-days", type=int, default=3, help="末尾验证日数，默认 3")
    parser.add_argument("--embargo-days", type=int, default=0,
                        help="训练与验证之间的隔离交易日数")
    parser.add_argument("--min-bars", type=int, default=200, help="单日最少分钟线根数")
    parser.add_argument("--method", choices=["grid", "random"], default="grid")
    parser.add_argument("--max-evals", type=int, default=100, help="random 模式最多评估参数数")
    parser.add_argument("--seed", type=int, default=20260829)
    parser.add_argument("--step-mode", choices=["diff", "pct"], default="diff")
    parser.add_argument("--steps", default=None, help="逗号分隔网格差价/比例；比例单位为%%")
    parser.add_argument("--lots", default="500,1000,2000", help="逗号分隔每格份数")
    parser.add_argument(
        "--order-modes", default="counterparty,passive",
        help="到价型委托价: counterparty,trigger,passive",
    )
    parser.add_argument("--turn-values", default=None, help="累计反弹/回落值，0 表示关闭")
    parser.add_argument(
        "--base-update-prices", default="grid,fill",
        help="到价型成交后基准更新价: grid,trigger,fill",
    )
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--inventory-ratio", type=float, default=0.5)
    parser.add_argument("--commission-bps", type=float, default=1.0)
    parser.add_argument("--min-commission", type=float, default=0.0)
    parser.add_argument("--sell-tax-bps", type=float, default=0.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--passive-offset-bps", type=float, default=0.0)
    parser.add_argument("--tick-size", type=float, default=0.001)
    parser.add_argument("--max-trades", type=int, default=2_000)
    parser.add_argument("--min-train-trades", type=float, default=1.0)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--output-prefix", default=None, help="输出前缀，不含扩展名")
    return parser


def validate_args(args) -> None:
    if args.days < 3:
        raise ValueError("总样本至少需要 3 个交易日")
    if not 0 < args.inventory_ratio < 1:
        raise ValueError("初始仓位比例必须在 0 和 1 之间")
    if args.capital <= 0 or args.tick_size <= 0:
        raise ValueError("资金和最小报价单位必须大于 0")
    if args.max_evals < 1 or args.top < 1:
        raise ValueError("max-evals 和 top 必须大于 0")
    if args.embargo_days < 0:
        raise ValueError("embargo-days 不能为负数")
    if args.validation_days + args.embargo_days >= args.days:
        raise ValueError("验证日数+隔离日数必须小于总样本日数")


def main(argv: Sequence[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    try:
        validate_args(args)
        code = normalize_code(args.code)
        steps = parse_number_list(args.steps, float) or (
            DEFAULT_DIFF_STEPS if args.step_mode == "diff" else DEFAULT_PCT_STEPS
        )
        lots = parse_number_list(args.lots, int) or DEFAULT_LOTS
        turn_values = parse_number_list(args.turn_values, float) or (
            DEFAULT_DIFF_TURNS if args.step_mode == "diff" else DEFAULT_PCT_TURNS
        )
        order_modes = parse_number_list(args.order_modes, str) or DEFAULT_ORDER_MODES
        base_prices = parse_number_list(args.base_update_prices, str) or DEFAULT_BASE_PRICES
        if any(step <= 0 for step in steps):
            raise ValueError("所有网格间距必须大于 0")
        if any(lot <= 0 or lot % 100 for lot in lots):
            raise ValueError("每格份数必须是正数且为 100 的整数倍")
        if any(value < 0 for value in turn_values):
            raise ValueError("累计反弹/回落值不能为负")
        if not set(order_modes) <= {"counterparty", "trigger", "passive"}:
            raise ValueError("order-modes 包含不支持的取值")
        if not set(base_prices) <= {"grid", "trigger", "fill"}:
            raise ValueError("base-update-prices 包含不支持的取值")

        frames, quality = load_recent_days(code, args.days, args.min_bars)
        dates = [date for date, _ in frames]
        split = chronological_holdout(
            dates, args.validation_days,
            embargo_size=args.embargo_days,
        )
        train_dates = list(split.train)
        embargo_dates = list(split.embargo)
        validation_dates = list(split.validation)
        candidates = build_candidates(
            args.step_mode, steps, lots, order_modes, turn_values, base_prices
        )
        min_open = min(float(frame.iloc[0]["开盘"]) for _, frame in frames)
        candidates, rejected = filter_candidates_for_tick(candidates, args.tick_size, min_open)
        if not candidates:
            raise ValueError("全部参数都因最小报价单位约束被剔除")
        if args.method == "random" and len(candidates) > args.max_evals:
            candidates = random.Random(args.seed).sample(candidates, args.max_evals)

        print(f"标的: {code}  样本: {dates[0]} ~ {dates[-1]} ({len(dates)}日)")
        print(f"训练: {', '.join(train_dates)}")
        if embargo_dates:
            print(f"隔离: {', '.join(embargo_dates)}")
        print(f"验证: {', '.join(validation_dates)}")
        print(f"搜索: {args.method}，候选 {len(candidates)} 组，报价约束剔除 {len(rejected)} 组")

        ranking_rows: List[Dict] = []
        daily_rows: List[Dict] = []
        for index, candidate in enumerate(candidates, 1):
            metrics, daily = evaluate_candidate(
                candidate, frames, train_dates, validation_dates, args
            )
            ranking_rows.append(metrics)
            daily_rows.extend(daily)
            if index % 25 == 0 or index == len(candidates):
                print(f"  已完成 {index}/{len(candidates)}")

        ranking = rank_candidates(ranking_rows)
        daily_frame = pd.DataFrame(daily_rows)
        split_map = {date: "train" for date in train_dates}
        split_map.update({date: "embargo" for date in embargo_dates})
        split_map.update({date: "validation" for date in validation_dates})
        daily_frame.insert(2, "split", daily_frame["date"].map(split_map))

        prefix = args.output_prefix or os.path.join(
            PROJECT_DIR, "output", f"grid_search_{code[2:]}"
        )
        output_dir = os.path.dirname(os.path.abspath(prefix))
        os.makedirs(output_dir, exist_ok=True)
        ranking_path = prefix + ".csv"
        daily_path = prefix + "_daily.csv"
        best_path = prefix + "_best.json"
        ranking.to_csv(ranking_path, index=False, encoding="utf-8-sig", float_format="%.6f")
        daily_frame.to_csv(daily_path, index=False, encoding="utf-8-sig", float_format="%.6f")

        best = ranking.iloc[0].to_dict()
        best_daily = daily_frame[daily_frame["candidate_id"] == best["candidate_id"]]
        best_by_mode = {}
        for mode in [TRANSACTION_DRIVEN, PRICE_TRIGGERED]:
            mode_rows = ranking[ranking["mode"] == mode]
            if not mode_rows.empty:
                best_by_mode[mode] = mode_rows.iloc[0].to_dict()
        payload = {
            "generated_as_of": dates[-1],
            "code": code,
            "method": args.method,
            "episode_mode": "daily_independent",
            "dates": dates,
            "train_dates": train_dates,
            "embargo_dates": embargo_dates,
            "validation_dates": validation_dates,
            "data_quality": quality,
            "search": {
                "evaluated_candidates": len(candidates),
                "tick_size": args.tick_size,
                "rejected_by_tick_rule": rejected,
                "capital": args.capital,
                "inventory_ratio": args.inventory_ratio,
                "commission_bps": args.commission_bps,
                "min_commission": args.min_commission,
                "sell_tax_bps": args.sell_tax_bps,
                "slippage_bps": args.slippage_bps,
                "score_formula": (
                    "mean_excess - 0.50*downside - 0.15*std_excess "
                    "- 0.25*abs(mean_excess_drawdown) - 0.002*avg_trades "
                    "- 0.05*mean_inventory_drift"
                ),
            },
            "best": best,
            "best_by_mode": best_by_mode,
            "best_daily": best_daily.to_dict(orient="records"),
            "limitations": [
                f"仅使用最近{len(dates)}个可用交易日时，样本很小，结果不能直接外推到未来",
                "一分钟OHLC无法还原分笔先后、盘口排队、部分成交和真实滑点",
                "每天独立重置账户用于公平调参，不模拟隔夜持仓连续演化",
                "验证段未参与排序，但仍应继续做更长样本、滚动前推和实盘仿真",
            ],
        }
        with open(best_path, "w", encoding="utf-8") as file:
            json.dump(_json_value(payload), file, ensure_ascii=False, indent=2, allow_nan=False)

        display = ranking.head(args.top).copy()
        display["类型"] = display["mode"].map({
            TRANSACTION_DRIVEN: "成交驱动", PRICE_TRIGGERED: "到价触发"
        })
        columns = [
            "rank", "类型", "step", "lot_shares", "order_price_mode", "turn_value",
            "base_update_price", "score", "train_mean_excess_pct",
            "validation_mean_excess_pct", "validation_avg_trades",
        ]
        print("\n前列参数（收益率单位：%）")
        print(display[columns].to_string(index=False, float_format=lambda value: f"{value:.4f}"))
        print(f"\n✓ 参数排名: {ranking_path}")
        print(f"✓ 逐日明细: {daily_path}")
        print(f"✓ 最佳参数: {best_path}")
        return 0
    except (ValueError, OSError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
