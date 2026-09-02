"""回测指标兼容入口。

指标实现已迁移到 :mod:`features.daily`。保留本模块是为了让现有策略脚本无需改变
导入路径；新业务代码应直接从 ``features`` 导入。
"""

from features.daily import compute_daily_features


def compute_all(
    cache,
    board_prefix=None,
    use_cache=True,
    cache_dir=None,
    profile="core",
):
    """兼容旧 API，返回日频特征矩阵字典。"""
    return compute_daily_features(
        cache,
        board_prefix=board_prefix,
        use_cache=use_cache,
        cache_dir=cache_dir,
        profile=profile,
    )


def get_signals_at(indicators: dict, feature: str, date_idx: int):
    """返回某项特征在指定交易日的横截面值。"""
    return indicators[feature].iloc[date_idx]


def scan_consecutive_signal(
    indicators: dict,
    condition_col: str,
    min_days: int = 3,
    max_days: int = 10,
    start_date: str = None,
):
    """扫描连续满足布尔特征的区段，并返回信号触发日。"""
    import pandas as pd

    condition = indicators[condition_col]
    all_dates = condition.index
    start_idx = 0
    if start_date:
        start_idx = max(0, (all_dates >= pd.Timestamp(start_date)).argmax())

    signals = []
    for code in condition.columns:
        values = condition[code].values[start_idx:].astype(bool)
        run = 0
        for offset, matched in enumerate(values):
            if matched:
                run += 1
                continue
            if run >= min_days:
                trigger = start_idx + offset + 2
                if trigger < len(all_dates):
                    signals.append({
                        "代码": code,
                        "触发日": all_dates[trigger],
                        "连续天数": min(run, max_days),
                    })
            run = 0
    return pd.DataFrame(signals)
