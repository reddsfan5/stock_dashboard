"""
统计回测模块 — 基于本地缓存的量化统计分析

设计：
  - StatsConfig：分析参数配置（dataclass）
  - WindowStats：滑动窗口统计基类（模板方法）
  - 子类实现 _check_condition() 和 _check_outcome()
  - StatsReport：结果格式化输出

用法：
  from stats_analysis import *

  config = StatsConfig(condition_days=5, overlap_pct=3.0, gain_pct=2.0)
  engine = StatsEngine(data)

  # 连续重叠后买入策略
  report = engine.run(OverlapBuyStrategy(config))
  report.print()

  # 连续上涨后追涨策略
  report2 = engine.run(RisingStreakStrategy(StatsConfig(condition_days=5)))
  report2.print()
"""

import numpy as np
import pandas as pd
from tqdm import tqdm
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from data.kline import StockData


# ====================================================================
# 配置
# ====================================================================

@dataclass
class StatsConfig:
    """统计分析参数"""
    # 基础
    condition_days: int = 5           # 连续 N 天满足条件
    lookahead_days: int = 2           # 向后看 M 天（N+1 买入, N+M 卖出）

    # 条件参数
    overlap_pct: float = 3.0          # 重叠区 > X%
    min_amount: float = 0             # 最小日均成交额（万元），0=不过滤

    # 结果参数
    gain_pct: float = 2.0             # 目标收益 %


@dataclass
class AnalysisResult:
    """单次分析结果"""
    name: str
    config: StatsConfig
    total_samples: int
    success_count: int
    gains: List[float]
    thresholds: Dict[float, float] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        return self.success_count / self.total_samples * 100 if self.total_samples else 0

    @property
    def mean_gain(self) -> float:
        g = np.array([x for x in self.gains if not np.isnan(x)])
        return float(np.mean(g)) if len(g) > 0 else 0

    @property
    def median_gain(self) -> float:
        g = np.array([x for x in self.gains if not np.isnan(x)])
        return float(np.median(g)) if len(g) > 0 else 0


# ====================================================================
# 数据准备
# ====================================================================

class DataPrepper:
    """从缓存准备分析所需的透视表和衍生数据"""

    def __init__(self, data: StockData, start_date: str = None):
        self.data = data
        cache = data.cache
        if start_date:
            cache = cache[cache["日期"] >= pd.Timestamp(start_date)]

        # 透视表
        self.close = cache.pivot_table(index="代码", columns="日期", values="收盘", aggfunc="last")
        self.high = cache.pivot_table(index="代码", columns="日期", values="最高", aggfunc="last")
        self.low = cache.pivot_table(index="代码", columns="日期", values="最低", aggfunc="last")

        # 取交集
        codes = self.close.index.intersection(self.high.index).intersection(self.low.index)
        self.close = self.close.loc[codes]
        self.high = self.high.loc[codes]
        self.low = self.low.loc[codes]
        self.codes = list(codes)
        self.dates = list(self.close.columns)

        # 衍生数据：每日重叠区%
        self.overlap_pct = pd.DataFrame(index=self.close.index, columns=self.dates[1:], dtype=float)
        for i in range(1, len(self.dates)):
            d = self.dates[i]; dp = self.dates[i - 1]
            oh = np.minimum(self.high[dp].values, self.high[d].values)
            ol = np.maximum(self.low[dp].values, self.low[d].values)
            self.overlap_pct[d] = (oh - ol) / self.close[dp].values * 100

        # 衍生数据：每日涨跌
        self.is_up = self.close.diff(axis=1) > 0

    @property
    def stock_count(self) -> int:
        return len(self.codes)

    @property
    def trading_days(self) -> int:
        return len(self.dates)

    @property
    def date_range(self) -> Tuple[str, str]:
        return str(self.dates[0])[:10], str(self.dates[-1])[:10]


# ====================================================================
# 策略基类（模板方法）
# ====================================================================

class WindowStats(ABC):
    """
    滑动窗口统计分析基类

    子类实现：
      - _condition_matrix(prep) → DataFrame[bool]  (每天是否满足条件)
      - _calculate_outcome(prep, code, i) → float   (收益%)
    """

    def __init__(self, config: StatsConfig):
        self.config = config

    @abstractmethod
    def _condition_matrix(self, prep: DataPrepper) -> pd.DataFrame:
        """返回每日是否满足条件（行=股票, 列=日期）"""
        ...

    @abstractmethod
    def _calculate_outcome(self, prep: DataPrepper, code: str, day_idx: int) -> Optional[float]:
        """
        计算 outcome: 在 day_idx 买入，day_idx + lookahead 卖出的收益率%
        返回 None 表示无法计算（如数据不足）
        """
        ...

    # ---- 模板方法 ----

    def name(self) -> str:
        return self.__class__.__name__

    def run(self, prep: DataPrepper) -> AnalysisResult:
        """执行统计分析"""
        cfg = self.config
        cond = self._condition_matrix(prep)
        total = 0
        success = 0
        gains = []

        for code in tqdm(prep.codes, desc=self.name(), unit="只"):
            cond_row = cond.loc[code].values
            for i in range(cfg.condition_days, len(prep.dates) - cfg.lookahead_days):
                if cond_row[i - cfg.condition_days:i].all():
                    total += 1
                    g = self._calculate_outcome(prep, code, i)
                    if g is not None:
                        gains.append(g)
                        if g > cfg.gain_pct:
                            success += 1

        # 多阈值统计
        thresholds = {}
        if gains:
            g_arr = np.array(gains)
            for t in [1.0, 2.0, 3.0, 5.0, 10.0]:
                thresholds[t] = float(np.mean(g_arr > t) * 100)

        return AnalysisResult(
            name=self.name(),
            config=cfg,
            total_samples=total,
            success_count=success,
            gains=gains,
            thresholds=thresholds,
        )


# ====================================================================
# 策略 1：连续重叠 → 低买高卖
# ====================================================================

class OverlapBuyStrategy(WindowStats):
    """连续 N 天 K 线重叠区 > X% → 第 N+1 天最低买入, 第 N+2 天最高卖出"""

    def _condition_matrix(self, prep: DataPrepper) -> pd.DataFrame:
        return prep.overlap_pct > self.config.overlap_pct

    def _calculate_outcome(self, prep: DataPrepper, code: str, day_idx: int) -> Optional[float]:
        if day_idx + 1 >= len(prep.dates):
            return None
        buy = prep.low.loc[code].iloc[day_idx]      # 第 N+1 天最低
        sell = prep.high.loc[code].iloc[day_idx + 1]  # 第 N+2 天最高
        if buy <= 0:
            return None
        return (sell - buy) / buy * 100


# ====================================================================
# 策略 2：连续上涨 → 追涨
# ====================================================================

class RisingStreakStrategy(WindowStats):
    """连续 N 天上涨 → 第 N+1 天开盘买入, 第 N+1 天收盘卖出（次日涨跌概率）"""

    def _condition_matrix(self, prep: DataPrepper) -> pd.DataFrame:
        return prep.is_up

    def _calculate_outcome(self, prep: DataPrepper, code: str, day_idx: int) -> Optional[float]:
        if day_idx >= len(prep.dates):
            return None
        close = prep.close.loc[code]
        buy = close.iloc[day_idx - 1]   # 第 N 天收盘买入
        sell = close.iloc[day_idx]       # 第 N+1 天收盘卖出
        if buy <= 0:
            return None
        return (sell - buy) / buy * 100


# ====================================================================
# 策略 3：连续上涨 → 后续创新高
# ====================================================================

class RisingNewHighStrategy(WindowStats):
    """连续 N 天上涨 → 第 N+1 天起 M 天内最高价突破第 N 天收盘价的概率"""

    def __init__(self, config: StatsConfig, horizon: int = 5):
        super().__init__(config)
        self.horizon = horizon

    def _condition_matrix(self, prep: DataPrepper) -> pd.DataFrame:
        return prep.is_up

    def _calculate_outcome(self, prep: DataPrepper, code: str, day_idx: int) -> Optional[float]:
        if day_idx + self.horizon > len(prep.dates):
            return None
        base = prep.high.loc[code].iloc[day_idx]  # 第 N+1 天最高
        peak = prep.high.loc[code].iloc[day_idx:day_idx + self.horizon].max()
        if base <= 0:
            return None
        return (peak - base) / base * 100


# ====================================================================
# 策略 4：连续重叠 → 收价买入次日高卖
# ====================================================================

class OverlapCloseStrategy(WindowStats):
    """连续 N 天 K 线重叠区 > X% → 第 N+1 收盘买入, 第 N+2 最高卖出"""

    def _condition_matrix(self, prep: DataPrepper) -> pd.DataFrame:
        return prep.overlap_pct > self.config.overlap_pct

    def _calculate_outcome(self, prep: DataPrepper, code: str, day_idx: int) -> Optional[float]:
        if day_idx + 1 >= len(prep.dates):
            return None
        buy = prep.close.loc[code].iloc[day_idx]     # 第 N+1 收盘
        sell = prep.high.loc[code].iloc[day_idx + 1]  # 第 N+2 最高
        if buy <= 0:
            return None
        return (sell - buy) / buy * 100


# ====================================================================
# 策略 5：基准 — 任意一天低买次日高卖
# ====================================================================

class BaselineStrategy(WindowStats):
    """无条件基准：任意一天最低买入，次日最高卖出"""

    def _condition_matrix(self, prep: DataPrepper) -> pd.DataFrame:
        return pd.DataFrame(True, index=prep.close.index, columns=prep.dates)

    def _calculate_outcome(self, prep: DataPrepper, code: str, day_idx: int) -> Optional[float]:
        if day_idx + 1 >= len(prep.dates):
            return None
        buy = prep.low.loc[code].iloc[day_idx]
        sell = prep.high.loc[code].iloc[day_idx + 1]
        if buy <= 0:
            return None
        return (sell - buy) / buy * 100

    def run(self, prep: DataPrepper) -> AnalysisResult:
        """基线不需要 condition_days 循环，直接遍历所有天"""
        total = 0
        success = 0
        gains = []

        for code in tqdm(prep.codes, desc="基准策略", unit="只"):
            highs = prep.high.loc[code].values
            lows = prep.low.loc[code].values
            for i in range(len(prep.dates) - 1):
                buy = lows[i]
                if buy > 0:
                    total += 1
                    g = (highs[i + 1] - buy) / buy * 100
                    gains.append(g)
                    if g > self.config.gain_pct:
                        success += 1

        g_arr = np.array(gains)
        thresholds = {}
        for t in [1.0, 2.0, 3.0, 5.0, 10.0]:
            thresholds[t] = float(np.mean(g_arr > t) * 100)

        return AnalysisResult(
            name="任意一天低买次日高卖（基准）",
            config=self.config,
            total_samples=total,
            success_count=success,
            gains=gains,
            thresholds=thresholds,
        )


# ====================================================================
# 策略 6：放量上涨 — 价涨量增 vs 缩量上涨
# ====================================================================

class VolumeUpStrategy(WindowStats):
    """连续 N 天价涨量增 → 次日收益率"""

    def __init__(self, config: StatsConfig, volume_increase: bool = True):
        super().__init__(config)
        self.volume_increase = volume_increase  # True=放量上涨, False=缩量上涨

    def _condition_matrix(self, prep: DataPrepper) -> pd.DataFrame:
        price_up = prep.is_up
        # 成交量日增
        vol_data = prep.data.cache.pivot_table(
            index="代码", columns="日期", values="成交额", aggfunc="last"
        )
        vol_data = vol_data.loc[prep.codes, prep.dates]
        vol_up = vol_data.diff(axis=1) > 0
        if not self.volume_increase:
            vol_up = ~vol_up
        # 对齐日期
        common = price_up.columns.intersection(vol_up.columns)
        return (price_up[common]) & (vol_up[common])

    def _calculate_outcome(self, prep: DataPrepper, code: str, day_idx: int) -> Optional[float]:
        if day_idx >= len(prep.dates):
            return None
        close = prep.close.loc[code]
        buy = close.iloc[day_idx - 1]
        sell = close.iloc[day_idx]
        return (sell - buy) / buy * 100 if buy > 0 else None

    def name(self) -> str:
        return f"连续上涨({'放量' if self.volume_increase else '缩量'})"


# ====================================================================
# 策略 7：地量后突破
# ====================================================================

class VolumeDryUpStrategy(WindowStats):
    """成交量缩至N日均量的X%以下 → 未来M天最高涨幅"""

    def __init__(self, config: StatsConfig, vol_ratio: float = 0.5, horizon: int = 5):
        super().__init__(config)
        self.vol_ratio = vol_ratio
        self.horizon = horizon

    def _condition_matrix(self, prep: DataPrepper) -> pd.DataFrame:
        vol = prep.data.cache.pivot_table(
            index="代码", columns="日期", values="成交额", aggfunc="last"
        )
        vol = vol.loc[prep.codes, prep.dates]
        avg_vol = vol.T.rolling(window=self.config.condition_days).mean().T
        return vol < avg_vol * self.vol_ratio

    def _calculate_outcome(self, prep: DataPrepper, code: str, day_idx: int) -> Optional[float]:
        if day_idx + self.horizon > len(prep.dates):
            return None
        close = prep.close.loc[code].iloc[day_idx]
        peak = prep.high.loc[code].iloc[day_idx:day_idx + self.horizon].max()
        return (peak - close) / close * 100 if close > 0 else None

    def name(self) -> str:
        return f"地量突破(量缩至{self.vol_ratio:.0%})"


# ====================================================================
# 策略 8：跳空缺口回补
# ====================================================================

class GapFillStrategy(WindowStats):
    """向上跳空(今低>昨高) → 未来M天内最低价跌破今低(补缺口)的概率"""

    def __init__(self, config: StatsConfig, horizon: int = 10):
        super().__init__(config)
        self.horizon = horizon

    def _condition_matrix(self, prep: DataPrepper) -> pd.DataFrame:
        gap_up = pd.DataFrame(index=prep.codes, columns=prep.dates[1:], dtype=bool)
        for i in range(1, len(prep.dates)):
            d, dp = prep.dates[i], prep.dates[i - 1]
            gap_up[d] = prep.low[d] > prep.high[dp]  # 今低 > 昨高
        return gap_up

    def _calculate_outcome(self, prep: DataPrepper, code: str, day_idx: int) -> Optional[float]:
        """返回补缺收益%(负值=补缺) 或 正数=缺口不补"""
        if day_idx + self.horizon > len(prep.dates):
            return None
        gap_day_low = prep.low.loc[code].iloc[day_idx]
        future_low = prep.low.loc[code].iloc[day_idx + 1:day_idx + self.horizon].min()
        # 补缺 = 未来最低 < 跳空日最低
        filled = future_low < gap_day_low
        return 100.0 if filled else -100.0  # 用正负表示补/不补

    def run(self, prep: DataPrepper) -> AnalysisResult:
        cfg = self.config
        cond = self._condition_matrix(prep)
        total = 0
        filled = 0

        for code in tqdm(prep.codes, desc="跳空缺口回补", unit="只"):
            cond_row = cond.loc[code].values
            for i in range(len(prep.dates)):
                if i >= len(cond_row) or not cond_row[i]:
                    continue
                if i + self.horizon > len(prep.dates):
                    continue
                total += 1
                gap_low = prep.low.loc[code].iloc[i]
                future_low = prep.low.loc[code].iloc[i + 1:i + self.horizon].min()
                if future_low < gap_low:
                    filled += 1

        return AnalysisResult(
            name=f"跳空缺口回补({self.horizon}日内)",
            config=cfg,
            total_samples=total,
            success_count=filled,
            gains=[],
            thresholds={},
        )


# ====================================================================
# 策略 9：超跌反弹
# ====================================================================

class OversoldBounceStrategy(WindowStats):
    """连续 N 天下跌 → 第 N+1 天最低买入, 第 N+2 天最高卖出"""

    def _condition_matrix(self, prep: DataPrepper) -> pd.DataFrame:
        return ~prep.is_up  # 下跌日

    def _calculate_outcome(self, prep: DataPrepper, code: str, day_idx: int) -> Optional[float]:
        if day_idx + 1 >= len(prep.dates):
            return None
        buy = prep.low.loc[code].iloc[day_idx]      # N+1天最低
        sell = prep.high.loc[code].iloc[day_idx + 1]  # N+2天最高
        return (sell - buy) / buy * 100 if buy > 0 else None


# ====================================================================
# 策略 10：布林带收敛突破
# ====================================================================

class BollingerSqueezeStrategy(WindowStats):
    """布林带宽度缩至N日最低 → 未来M天突破方向与幅度"""

    def __init__(self, config: StatsConfig, bb_period: int = 20, horizon: int = 5):
        super().__init__(config)
        self.bb_period = bb_period
        self.horizon = horizon

    def _condition_matrix(self, prep: DataPrepper) -> pd.DataFrame:
        """BB 宽度低于 condition_days 日均值的 80%"""
        result = pd.DataFrame(index=prep.codes, columns=prep.dates, dtype=bool)
        for code in prep.codes:
            close = prep.close.loc[code].values
            # 计算布林带宽度 (upper - lower) / middle
            for i in range(self.bb_period, len(close)):
                window = close[i - self.bb_period:i]
                ma = np.mean(window)
                std = np.std(window)
                if ma > 0 and std > 0:
                    bb_width = (2 * std) / ma  # 归一化宽度
                    # BB 宽度 < 过去 N 日均值的 70%（真正收敛）
                    if i >= self.config.condition_days + self.bb_period:
                        past_widths = []
                        for j in range(i - self.config.condition_days, i):
                            w = close[j - self.bb_period:j]
                            m = np.mean(w)
                            s = np.std(w)
                            if m > 0:
                                past_widths.append((2 * s) / m)
                        if past_widths and bb_width < np.mean(past_widths) * 0.7:
                            result.iloc[result.index.get_loc(code), i] = True
        return result

    def _calculate_outcome(self, prep: DataPrepper, code: str, day_idx: int) -> Optional[float]:
        if day_idx + self.horizon > len(prep.dates):
            return None
        close = prep.close.loc[code].iloc[day_idx]
        peak = prep.high.loc[code].iloc[day_idx:day_idx + self.horizon].max()
        return (peak - close) / close * 100 if close > 0 else None


# ====================================================================
# 策略 11：多信号叠加
# ====================================================================

class MultiSignalStrategy(WindowStats):
    """重叠>3% + 缩量 → 双重确认"""

    def __init__(self, config: StatsConfig):
        super().__init__(config)

    def _condition_matrix(self, prep: DataPrepper) -> pd.DataFrame:
        # 信号1: 重叠 > 3%
        overlap_ok = prep.overlap_pct > self.config.overlap_pct
        # 信号2: 缩量 (量 < 前日量)
        vol = prep.data.cache.pivot_table(
            index="代码", columns="日期", values="成交额", aggfunc="last"
        )
        vol = vol.loc[prep.codes, prep.dates]
        vol_down = vol.diff(axis=1) < 0
        # 对齐日期
        common_dates = overlap_ok.columns.intersection(vol_down.columns)
        return (overlap_ok[common_dates]) & (vol_down[common_dates])

    def _calculate_outcome(self, prep: DataPrepper, code: str, day_idx: int) -> Optional[float]:
        if day_idx + 1 >= len(prep.dates):
            return None
        buy = prep.low.loc[code].iloc[day_idx]
        sell = prep.high.loc[code].iloc[day_idx + 1]
        return (sell - buy) / buy * 100 if buy > 0 else None

    def name(self) -> str:
        return "重叠+缩量(双确认)"


# ====================================================================
# 引擎
# ====================================================================

class StatsEngine:
    """统计分析引擎"""

    def __init__(self, data: StockData, start_date: str = None):
        self.data = data
        self.prepper = DataPrepper(data, start_date=start_date)

    def run(self, strategy: WindowStats) -> AnalysisResult:
        return strategy.run(self.prepper)

    def run_multi(self, strategy_cls, configs: List[StatsConfig]) -> List[AnalysisResult]:
        """批量运行：同一策略，不同 N 值"""
        results = []
        for cfg in configs:
            results.append(self.run(strategy_cls(cfg)))
        return results

    def summary(self, results: List[AnalysisResult], label: str = "连续N天"):
        """打印汇总表"""
        print(f"数据库: {self.prepper.stock_count}只 × {self.prepper.trading_days}天 "
              f"({self.prepper.date_range[0]}~{self.prepper.date_range[1]})")
        print()
        print(f"{label:<16} {'样本':>10} {'成功%':<7}", end="")
        for t, _ in results[0].thresholds.items():
            print(f">{t:.0f}%   ", end="")
        print(f" {'均值':<8} {'中位':<8}")
        print("-" * 75)

        for r in results:
            if r.config.condition_days > 0:
                row_label = str(r.config.condition_days)
            else:
                row_label = r.name
            print(f"{row_label:<16} {r.total_samples:>10,} {r.success_rate:.0f}%    ", end="")
            for t, pct in r.thresholds.items():
                print(f"{pct:.0f}%   ", end="")
            print(f" {r.mean_gain:+.1f}%   {r.median_gain:+.1f}%")


# ====================================================================
# CLI
# ====================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="统计分析")
    parser.add_argument("--strategy", choices=[
        "overlap", "overlap_close", "rising", "newhigh", "baseline",
        "volume_up", "volume_down", "volume_dry", "gap_fill",
        "oversold", "bollinger", "multi_signal",
    ], default="overlap")
    parser.add_argument("--condition-days", type=int, default=5)
    parser.add_argument("--overlap-pct", type=float, default=3.0)
    parser.add_argument("--gain-pct", type=float, default=2.0)
    parser.add_argument("--horizon", type=int, default=5)
    args = parser.parse_args()

    data = StockData()
    engine = StatsEngine(data)

    strategy_map = {
        "overlap": OverlapBuyStrategy, "overlap_close": OverlapCloseStrategy,
        "rising": RisingStreakStrategy, "newhigh": RisingNewHighStrategy,
        "baseline": BaselineStrategy,
        "volume_up": lambda c: VolumeUpStrategy(c, volume_increase=True),
        "volume_down": lambda c: VolumeUpStrategy(c, volume_increase=False),
        "volume_dry": VolumeDryUpStrategy,
        "gap_fill": GapFillStrategy,
        "oversold": OversoldBounceStrategy,
        "bollinger": BollingerSqueezeStrategy,
        "multi_signal": MultiSignalStrategy,
    }
    cls = strategy_map[args.strategy]

    single_run = (RisingNewHighStrategy, BaselineStrategy, GapFillStrategy)
    if cls in single_run or (isinstance(cls, type) and issubclass(cls, single_run)):
        if cls == BaselineStrategy:
            s = BaselineStrategy(StatsConfig(condition_days=0, gain_pct=args.gain_pct))
        elif cls == GapFillStrategy:
            s = GapFillStrategy(StatsConfig(condition_days=0, gain_pct=args.gain_pct))
        elif cls == RisingNewHighStrategy:
            s = RisingNewHighStrategy(
                StatsConfig(condition_days=args.condition_days, gain_pct=args.gain_pct),
                horizon=args.horizon)
        else:
            s = cls(StatsConfig(condition_days=args.condition_days, gain_pct=args.gain_pct))
        results = [engine.run(s)]
    else:
        configs = [StatsConfig(condition_days=n, overlap_pct=args.overlap_pct,
                               gain_pct=args.gain_pct)
                   for n in range(max(3, args.condition_days), args.condition_days + 6)]
        results = engine.run_multi(cls, configs)

    label = "连续N天" if cls != BaselineStrategy else "策略"
    engine.summary(results, label=label)


if __name__ == "__main__":
    main()
