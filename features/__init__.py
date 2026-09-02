"""可复用的量化特征层。

事实数据只负责保存市场原始字段；滚动指标、风险指标和横截面指标集中在本包计算。
"""

from features.catalog import FEATURE_CATALOG, FEATURE_PROFILES
from features.daily import compute_daily_features
from features.intraday import add_intraday_volume_ratio
from features.revealed import compute_revealed_indicators
from features.snapshot import build_decision_snapshot, enrich_screening_results

__all__ = [
    "FEATURE_CATALOG",
    "FEATURE_PROFILES",
    "add_intraday_volume_ratio",
    "build_decision_snapshot",
    "compute_daily_features",
    "compute_revealed_indicators",
    "enrich_screening_results",
]
