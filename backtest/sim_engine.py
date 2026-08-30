"""
模拟引擎 — 接收策略，跑完整回测流程，输出 HTML

策略只需实现 SimStrategy.scan_signals() 和 process_day()。
引擎负责：K线索引、逐日平仓、权益记录、强平、统计、K线采集、HTML 渲染。
"""

import copy
import os
import random
from typing import List, Optional, Union

import pandas as pd
from tqdm import tqdm

from data.kline import StockData
from data.industry import StockInfo
from backtest.metrics import calculate_metrics
from backtest.sim_types import (
    EquityPoint, Position, SimulationResult, Trade,
)
from backtest.sim_core import (
    build_kline_index, group_signals_by_date,
    record_equity, force_close_positions, print_stats,
    collect_kline_for_trades, sell_net_proceeds,
)
from backtest.strategy import SimStrategy, DayContext
from backtest.renderer import render_dip_buy_report


class SimEngine:
    """模拟引擎：策略无关的完整回测流程"""

    def __init__(self, strategy: SimStrategy):
        self.strategy = strategy
        self.last_result: Optional[SimulationResult] = None

    def run(self, capital: float = 50000, start_date: str = "2024-01-01",
            target_pct: float = 1.0, commission_rate: float = 0.0001,
            stamp_tax: float = 0.0005, output_html: str = None,
            data=None, min_commission: float = 0.0,
            random_seed: int = 42,
            intrabar_exit_policy: str = "stop_first",
            return_result: bool = False,
            **strategy_kwargs) -> Union[str, SimulationResult, None]:
        """
        执行完整回测。

        Args:
            capital: 初始资金
            start_date: 起始日期
            target_pct: 止盈目标%
            commission_rate: 佣金率
            stamp_tax: 印花税率
            min_commission: 单笔最低佣金，0 表示关闭（兼容旧结果）
            random_seed: 同日多信号排序的随机种子
            intrabar_exit_policy: 同一根日K同时触及止损和止盈时的处理顺序
            output_html: HTML 输出路径（None=不输出）
            return_result: True 时返回结构化 SimulationResult
            **strategy_kwargs: 传给 strategy.scan_signals 的额外参数

        Returns:
            默认返回 HTML 路径；return_result=True 时返回结构化结果
        """
        if capital <= 0:
            raise ValueError("capital 必须大于 0")
        if intrabar_exit_policy not in {"stop_first", "target_first"}:
            raise ValueError("intrabar_exit_policy 仅支持 stop_first/target_first")

        strat = self.strategy
        rng = random.Random(random_seed)
        if data is None:
            data = StockData()
        info = StockInfo()
        code_to_name = dict(zip(info.df["代码"], info.df["名称"]))

        # 1. 标的过滤。使用浅拷贝数据视图，避免无信号或异常时污染共享缓存。
        board = strat.get_board_filter()
        orig_cache = data.cache
        scoped_data = copy.copy(data)
        scoped_data._cache = orig_cache[
            orig_cache["代码"].str.startswith(board, na=False)
        ].copy()

        # 2. 信号扫描
        print("=" * 60)
        signals = strat.scan_signals(scoped_data, code_to_name, start_date,
                                     **strategy_kwargs)
        if len(signals) == 0:
            print("无信号")
            result = self._empty_result(
                capital, start_date, random_seed, commission_rate,
                stamp_tax, min_commission, intrabar_exit_policy,
            )
            self.last_result = result
            return result if return_result else None

        # 3. K线索引 + 信号分组
        kline_idx = build_kline_index(scoped_data.cache, start_date)
        all_dates, sig_by_date = group_signals_by_date(signals, kline_idx)
        if not all_dates:
            print("回测区间内无K线")
            result = self._empty_result(
                capital, start_date, random_seed, commission_rate,
                stamp_tax, min_commission, intrabar_exit_policy,
            )
            self.last_result = result
            return result if return_result else None
        print(f"逐日模拟 ({len(all_dates)} 天)...")

        # 4. 逐日模拟
        cash = capital
        positions: List[Position] = []
        closed_trades: List[Trade] = []
        equity_curve: List[EquityPoint] = []
        last_prices = {}

        for date in tqdm(all_dates, desc="模拟"):
            # a. 平仓（通用逻辑 — 含止损 + 策略自定义卖出）
            survivors = []
            for pos in positions:
                key = (pos.code, date)
                if key not in kline_idx:
                    survivors.append(pos)
                    continue
                high, low, close, open_ = kline_idx[key]
                last_prices[pos.code] = close

                # 策略自定义卖出判断
                custom_sp = strat.should_sell(pos, date, kline_idx)
                # 持仓到期强制平仓
                force_close = (pos.max_hold_days > 0 and
                               pos.holding_days >= pos.max_hold_days)
                if custom_sp is not None:
                    sp = custom_sp
                    filled = (sp >= pos.target_price)
                    exit_reason = "strategy"
                elif force_close:
                    sp = close
                    filled = False
                    exit_reason = "expiry"
                else:
                    hit_stop = pos.stop_price > 0 and low <= pos.stop_price
                    hit_target = high >= pos.target_price
                    if hit_stop and hit_target:
                        if intrabar_exit_policy == "stop_first":
                            sp, filled, exit_reason = pos.stop_price, False, "stop"
                        else:
                            sp, filled, exit_reason = pos.target_price, True, "target"
                    elif hit_stop:
                        sp, filled, exit_reason = pos.stop_price, False, "stop"
                    elif hit_target:
                        sp, filled, exit_reason = pos.target_price, True, "target"
                    else:
                        # 不卖，继续持有
                        pos.holding_days += 1
                        survivors.append(pos)
                        continue
                net = sell_net_proceeds(
                    sp, pos.shares, commission_rate, stamp_tax,
                    min_commission,
                )
                cash += net
                closed_trades.append(Trade(
                    code=pos.code, name=code_to_name.get(pos.code, ""),
                    buy_date=pd.Timestamp(pos.buy_date).strftime("%Y-%m-%d"),
                    buy_price=round(pos.buy_price, 2),
                    sell_date=pd.Timestamp(date).strftime("%Y-%m-%d"),
                    sell_price=round(sp, 2),
                    return_pct=round((net - pos.total_cost) / pos.total_cost * 100, 2),
                    pnl=round(net - pos.total_cost, 2),
                    filled=filled, lots=pos.shares // 100,
                    streak_days=pos.streak_days,
                    exit_reason=exit_reason,
                ))
            positions = survivors

            # b. 开仓（策略决策）
            if date in sig_by_date:
                ctx = DayContext(
                    date=date, cash=cash, today_start_cash=cash,
                    today_signals=list(sig_by_date[date]),
                    kline_idx=kline_idx, commission_rate=commission_rate,
                    target_pct=target_pct, code_to_name=code_to_name,
                    rng=rng, min_commission=min_commission,
                )
                new_positions = strat.process_day(ctx)
                for pos in new_positions:
                    if pos.total_cost > cash:
                        continue
                    cash -= pos.total_cost
                    positions.append(pos)
                    last_prices[pos.code] = pos.buy_price

            # c. 记录权益
            equity_curve.append(record_equity(
                date, cash, positions, kline_idx=kline_idx,
                last_prices=last_prices,
            ))

        # 5. 期末强平
        fc_trades, cash = force_close_positions(
            positions, all_dates[-1], kline_idx, commission_rate,
            stamp_tax, code_to_name, cash, min_commission,
            last_prices)
        closed_trades.extend(fc_trades)
        if fc_trades:
            equity_curve[-1] = EquityPoint(
                date=pd.Timestamp(all_dates[-1]).strftime("%Y-%m-%d"),
                equity=cash, cash=cash, positions=0, position_value=0.0,
            )

        metrics = calculate_metrics(
            equity_curve, closed_trades, capital, cash,
        )
        result = SimulationResult(
            initial_capital=capital,
            final_equity=cash,
            start_date=start_date,
            end_date=pd.Timestamp(all_dates[-1]).strftime("%Y-%m-%d"),
            trades=closed_trades,
            equity_curve=equity_curve,
            metrics=metrics,
            random_seed=random_seed,
            assumptions={
                "commission_rate": commission_rate,
                "stamp_tax": stamp_tax,
                "min_commission": min_commission,
                "intrabar_exit_policy": intrabar_exit_policy,
                "mark_to_market": "daily_close",
                "end_of_data_liquidation": "last_available_close",
            },
            output_html=output_html,
        )
        self.last_result = result

        # 6. 统计
        print_stats(closed_trades, cash, capital, start_date,
                    pd.Timestamp(all_dates[-1]).strftime("%Y-%m-%d"),
                    title=f"{strat.__class__.__name__} 模拟",
                    extra_info=f"+{target_pct}%止盈",
                    metrics=metrics)

        # 7. K线 + HTML
        if output_html and closed_trades:
            # 尝试加载缓存指标以加速K线采集
            pivots = None
            try:
                from backtest.indicators import _load_cache
                pivots = _load_cache()
            except Exception:
                pass
            kline_map = collect_kline_for_trades(data, closed_trades, code_to_name, pivots=pivots)
            meta = strat.get_report_meta()
            html = render_dip_buy_report(
                capital=capital, target_pct=target_pct, overlap_pct=0,
                commission_rate=commission_rate, stamp_tax=stamp_tax,
                start_date=start_date, lookback=0, board="main",
                max_gain=99, limit_down=99, max_range_20d=99,
                trades=closed_trades, equity_curve=equity_curve,
                final_equity=cash, kline_map=kline_map,
                metrics=metrics,
                title=meta.get("title"),
                subtitle=meta.get("subtitle"),
                params_html=meta.get("params_html"),
                assumptions_html=(
                    f"🔎 <b>回测假设：</b>收盘价逐日盯市 · 同根K线冲突={intrabar_exit_policy}"
                    f" · 随机种子={random_seed} · 最低佣金=¥{min_commission:g}"
                ),
            )
            output_dir = os.path.dirname(output_html)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
            with open(output_html, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"\n✓ HTML: {output_html}")

        return result if return_result else output_html

    @staticmethod
    def _empty_result(capital, start_date, random_seed, commission_rate,
                      stamp_tax, min_commission, intrabar_exit_policy):
        metrics = calculate_metrics([], [], capital, capital)
        return SimulationResult(
            initial_capital=capital,
            final_equity=capital,
            start_date=start_date,
            end_date=None,
            trades=[],
            equity_curve=[],
            metrics=metrics,
            random_seed=random_seed,
            assumptions={
                "commission_rate": commission_rate,
                "stamp_tax": stamp_tax,
                "min_commission": min_commission,
                "intrabar_exit_policy": intrabar_exit_policy,
                "mark_to_market": "daily_close",
                "end_of_data_liquidation": "last_available_close",
            },
        )
