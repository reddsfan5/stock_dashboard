"""MCP 输入约束、分页和统一返回契约。"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


SCHEMA_VERSION = "1.0"
TIMEZONE = "Asia/Shanghai"


class QueryModel(BaseModel):
    """禁止静默接收未声明字段，收紧 MCP 到业务层的边界。"""

    model_config = ConfigDict(extra="forbid")


class SymbolQuery(QueryModel):
    query: str = ""
    asset_type: Optional[str] = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=50)


class DailyBarsQuery(QueryModel):
    symbol: str
    start: Optional[str] = None
    end: Optional[str] = None
    limit: int = Field(default=120, ge=1, le=500)
    offset: int = Field(default=0, ge=0)
    asset_type: Optional[str] = None


class MinuteBarsQuery(QueryModel):
    symbol: str
    market_date: str
    as_of: Optional[str] = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=500, ge=1, le=500)
    asset_type: Optional[str] = None


class FeatureSnapshotQuery(QueryModel):
    symbols: list[str] = Field(min_length=1, max_length=50)
    as_of: Optional[str] = None


class ScreenerQuery(QueryModel):
    strategy_id: str
    as_of: Optional[str] = None
    params: Optional[dict[str, Any]] = None
    universe: str = "all"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)


class MarketQuery(QueryModel):
    market_date: str
    as_of: Optional[str] = None


class NewsQuery(MarketQuery):
    phase: Optional[str] = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)


class ArtifactQuery(QueryModel):
    artifact_id: Optional[str] = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)


def json_safe(value: Any) -> Any:
    """把 pandas/numpy 值递归转换成严格 JSON 可序列化对象。"""
    # 延迟导入，避免 MCP 工具发现阶段加载 pandas/pyarrow。
    import numpy as np
    import pandas as pd

    if value is None:
        return None
    if is_dataclass(value):
        return json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if isinstance(value, (pd.Timestamp, datetime)):
        timestamp = pd.Timestamp(value)
        if pd.isna(timestamp):
            return None
        return timestamp.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if value is pd.NA or value is pd.NaT:
        return None
    return value


@dataclass(frozen=True)
class Page:
    offset: int
    limit: int
    returned: int
    total: int
    has_more: bool


def paginate(items: list, *, offset: int = 0, limit: int = 50, maximum: int = 100) -> tuple[list, Page]:
    try:
        offset = int(offset)
        limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("offset 和 limit 必须是整数") from exc
    if offset < 0:
        raise ValueError("offset 不能小于 0")
    if limit < 1 or limit > maximum:
        raise ValueError(f"limit 必须在 1 到 {maximum} 之间")
    total = len(items)
    selected = items[offset: offset + limit]
    return selected, Page(offset, limit, len(selected), total, offset + len(selected) < total)


def response_envelope(
    *,
    summary: str,
    request: dict,
    data: Any,
    requested_as_of: Optional[str] = None,
    resolved_as_of: Optional[str] = None,
    temporal_scope: str = "current",
    freshness: Optional[dict] = None,
    provenance: Optional[list | dict] = None,
    warnings: Optional[list[str]] = None,
    pagination: Optional[Page | dict] = None,
) -> dict:
    return json_safe({
        "schema_version": SCHEMA_VERSION,
        "summary": summary,
        "request": request,
        "requested_as_of": requested_as_of,
        "resolved_as_of": resolved_as_of,
        "temporal_scope": temporal_scope,
        "freshness": freshness or {},
        "provenance": provenance or [],
        "warnings": warnings or [],
        "pagination": asdict(pagination) if isinstance(pagination, Page) else pagination,
        "data": data,
    })
