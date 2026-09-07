"""stock-data 本地 STDIO MCP Server。

stdout 由 MCP SDK 独占；任何诊断输出必须写 stderr。Server 由 Codex 按需启动，
不监听网络端口，也不加入 ``scripts.serve``。
"""

from __future__ import annotations

import json
from typing import Any, Optional

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from stock_mcp.contracts import (
    ArtifactQuery,
    DailyBarsQuery,
    FeatureSnapshotQuery,
    MarketQuery,
    MinuteBarsQuery,
    NewsQuery,
    ScreenerQuery,
    SymbolQuery,
)

INSTRUCTIONS = """本 Server 只查询项目本地缓存，数据不是实时行情。
全部工具只读，不联网、不更新数据、不写日记、不推进训练、不提交委托，也不具备
任何实盘交易能力。历史查询必须严格遵守 as_of；缺失历史数据时返回 missing/partial，
不得回退到当前数据。分钟 K 不是逐笔成交，当前不提供逐笔成交工具。"""

mcp = MCPServer("stock-data", instructions=INSTRUCTIONS)
_service: Any = None

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    idempotent_hint=True,
    destructive_hint=False,
    open_world_hint=False,
)


def get_service():
    global _service
    if _service is None:
        from stock_mcp.research_service import ResearchService

        _service = ResearchService()
    return _service


def _call(method: str, **kwargs) -> dict:
    try:
        return getattr(get_service(), method)(**kwargs)
    except ToolError:
        raise
    except Exception as exc:
        payload = {
            "schema_version": "1.0",
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
        raise ToolError(json.dumps(payload, ensure_ascii=False)) from exc


@mcp.tool(title="查询本地数据状态", annotations=READ_ONLY)
def get_data_status() -> dict[str, Any]:
    """查询每日更新状态、各本地数据集日期范围、行数和新鲜度。"""
    return _call("get_data_status")


@mcp.tool(title="搜索股票、ETF 与指数", annotations=READ_ONLY)
def search_symbols(
    query: str = "", asset_type: Optional[str] = None,
    offset: int = 0, limit: int = 20,
) -> dict[str, Any]:
    """按代码或名称搜索本地证券目录；歧义代码会保留多个资产类型。"""
    payload = SymbolQuery(
        query=query, asset_type=asset_type, offset=offset, limit=limit
    ).model_dump()
    return _call("search_symbols", **payload)


@mcp.tool(title="查询研究目录", annotations=READ_ONLY)
def get_catalog(section: str = "all") -> dict[str, Any]:
    """查询数据集、指标定义、已发现选股策略或白名单研究报告目录。"""
    return _call("get_catalog", section=section)


@mcp.tool(title="查询单标的日K", annotations=READ_ONLY)
def get_daily_bars(
    symbol: str, start: Optional[str] = None, end: Optional[str] = None,
    limit: int = 120, offset: int = 0, asset_type: Optional[str] = None,
) -> dict[str, Any]:
    """查询单标的日K；非交易日 end 解析到此前最近交易日，最多 500 根。"""
    payload = DailyBarsQuery(
        symbol=symbol, start=start, end=end, limit=limit,
        offset=offset, asset_type=asset_type,
    ).model_dump()
    return _call("get_daily_bars", **payload)


@mcp.tool(title="查询单日分钟K", annotations=READ_ONLY)
def get_minute_bars(
    symbol: str, market_date: str, as_of: Optional[str] = None,
    offset: int = 0, limit: int = 500, asset_type: Optional[str] = None,
) -> dict[str, Any]:
    """查询真实存在交易日的分钟K并按 as_of 截断；不回退日期，不冒充逐笔。"""
    payload = MinuteBarsQuery(
        symbol=symbol, market_date=market_date, as_of=as_of,
        offset=offset, limit=limit, asset_type=asset_type,
    ).model_dump()
    return _call("get_minute_bars", **payload)


@mcp.tool(title="查询量价风险估值快照", annotations=READ_ONLY)
def get_feature_snapshot(
    symbols: list[str], as_of: Optional[str] = None,
) -> dict[str, Any]:
    """查询最多 50 个标的在指定日期可知的量价、流动性、风险与估值指标。"""
    payload = FeatureSnapshotQuery(symbols=symbols, as_of=as_of).model_dump()
    return _call("get_feature_snapshot", **payload)


@mcp.tool(title="运行已有只读选股策略", annotations=READ_ONLY)
def run_screener(
    strategy_id: str, as_of: Optional[str] = None,
    params: Optional[dict[str, Any]] = None, universe: str = "all",
    offset: int = 0, limit: int = 50,
) -> dict[str, Any]:
    """运行 PIPELINE_META 已声明策略；仅接受该策略声明参数，最多返回 100 项。"""
    payload = ScreenerQuery(
        strategy_id=strategy_id, as_of=as_of, params=params,
        universe=universe, offset=offset, limit=limit,
    ).model_dump()
    return _call("run_screener", **payload)


@mcp.tool(title="查询历史市场情境", annotations=READ_ONLY)
def get_market_context(
    market_date: str, as_of: Optional[str] = None,
) -> dict[str, Any]:
    """查询指定日期与时刻可知的 A 股宽基和海外市场情境。"""
    payload = MarketQuery(market_date=market_date, as_of=as_of).model_dump()
    return _call("get_market_context", **payload)


@mcp.tool(title="查询缓存市场资讯", annotations=READ_ONLY)
def get_market_news(
    market_date: str, as_of: Optional[str] = None, phase: Optional[str] = None,
    offset: int = 0, limit: int = 50,
) -> dict[str, Any]:
    """只读本地资讯缓存；可按截止时刻和早盘/午间/收盘过滤，绝不联网刷新。"""
    payload = NewsQuery(
        market_date=market_date, as_of=as_of, phase=phase,
        offset=offset, limit=limit,
    ).model_dump()
    return _call("get_market_news", **payload)


@mcp.tool(title="查询当前标的研究上下文", annotations=READ_ONLY)
def get_symbol_context(symbol: str) -> dict[str, Any]:
    """汇总当前选股命中、观察池、日记、训练和假设；不能用于历史无剧透训练。"""
    return _call("get_symbol_context", symbol=symbol)


@mcp.tool(title="查询白名单研究报告", annotations=READ_ONLY)
def get_research_artifacts(
    artifact_id: Optional[str] = None, offset: int = 0, limit: int = 50,
) -> dict[str, Any]:
    """列出白名单 output 报告；JSON 返回摘要，HTML 返回本地报告服务安全链接。"""
    payload = ArtifactQuery(
        artifact_id=artifact_id, offset=offset, limit=limit
    ).model_dump()
    return _call("get_research_artifacts", **payload)


def main() -> None:
    from stock_mcp.security import install_network_guard

    install_network_guard()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
