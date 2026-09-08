"""stock-data 本地 MCP Server（STDIO + 可选 Streamable HTTP）。

默认 STDIO：stdout 由 MCP SDK 独占；任何诊断输出必须写 stderr。
Codex 仍通过 STDIO 按需启动，不监听网络端口。

可选 HTTP：``python -m stock_mcp.server --http``，默认仅绑定 127.0.0.1:8766，
要求 ``STOCK_MCP_TOKEN``（或本地 mode-600 token 文件）。HTTP 供 Cursor / Grok Bot
经反向隧道以 ``url`` MCP 接入；仍是只读研究服务，无交易能力。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse

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
from stock_mcp.security import (
    DEFAULT_HTTP_HOST,
    DEFAULT_HTTP_PATH,
    DEFAULT_HTTP_PORT,
    TOKEN_ENV,
    create_bearer_auth_middleware,
    default_token_file,
    install_network_guard,
    mint_http_token,
    require_http_token,
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


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "stock-data", "readonly": True})


def build_http_app(*, token: str, host: str = DEFAULT_HTTP_HOST):
    """构建带 Bearer 鉴权的 Streamable HTTP ASGI 应用。

    反向隧道场景下 Host 为 ``*.trycloudflare.com``，因此关闭 DNS rebinding
    主机白名单，改由本机回环绑定 + Bearer Token 承担边界。
    """
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    )
    app = mcp.streamable_http_app(
        streamable_http_path=DEFAULT_HTTP_PATH,
        # 隧道客户端可能跨 Host；鉴权依赖 Bearer，而非 Origin/Host 白名单
        transport_security=transport_security,
        host=host,
        # 便于单测与无状态探活；正式会话仍由 SDK 管理
        stateless_http=True,
        json_response=True,
    )
    app.user_middleware.insert(
        0, Middleware(create_bearer_auth_middleware(token))
    )
    app.middleware_stack = None  # 强制重建中间件栈
    return app


def run_http(host: str, port: int) -> None:
    import uvicorn

    token = require_http_token()
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"拒绝绑定 {host!r}：HTTP MCP 默认仅允许回环地址。"
            "请用云隧道暴露，不要直接监听局域网。",
            file=sys.stderr,
        )
        raise SystemExit(2)

    app = build_http_app(token=token, host=host)
    print(
        f"stock-data MCP HTTP listening on http://{host}:{port}{DEFAULT_HTTP_PATH} "
        f"(Bearer {TOKEN_ENV}; token file {default_token_file()})",
        file=sys.stderr,
    )
    uvicorn.run(app, host=host, port=port, log_level="info")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="stock-data 只读 MCP Server")
    parser.add_argument(
        "--http",
        action="store_true",
        help="启用 Streamable HTTP（默认 STDIO）",
    )
    parser.add_argument("--host", default=DEFAULT_HTTP_HOST, help="HTTP 绑定地址")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_HTTP_PORT, help="HTTP 端口"
    )
    parser.add_argument(
        "--mint-token",
        action="store_true",
        help="生成/保留本地 Bearer Token 文件后退出（不启动服务）",
    )
    parser.add_argument(
        "--overwrite-token",
        action="store_true",
        help="与 --mint-token 联用，强制轮换 Token",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    args = build_parser().parse_args(argv)

    if args.mint_token:
        path = mint_http_token(overwrite=args.overwrite_token)
        print(f"token file ready: {path} (mode 600)", file=sys.stderr)
        return

    install_network_guard()

    if args.http:
        run_http(host=args.host, port=args.port)
        return

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
