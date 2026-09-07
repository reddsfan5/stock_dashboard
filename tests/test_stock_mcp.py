from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from data.instruments import AmbiguousInstrumentError, InstrumentResolver
from data.market_news import MarketNewsRepository
from stock_mcp.contracts import json_safe, paginate
from stock_mcp.repositories import LocalResearchRepository
from stock_mcp.research_service import ResearchService


def _daily(code, dates, closes):
    return pd.DataFrame({
        "代码": [code] * len(dates),
        "日期": pd.to_datetime(dates),
        "开盘": closes,
        "最高": [value + 0.1 for value in closes],
        "最低": [value - 0.1 for value in closes],
        "收盘": closes,
        "前收": [np.nan, *closes[:-1]],
        "成交量": [1000 + index * 10 for index in range(len(dates))],
        "成交额": [10000 + index * 100 for index in range(len(dates))],
        "换手率%": [1.0] * len(dates),
    })


@pytest.fixture()
def local_project(tmp_path: Path) -> Path:
    cache = tmp_path / "cache"
    state = tmp_path / "state"
    output = tmp_path / "output"
    cache.mkdir()
    state.mkdir()
    output.mkdir()

    pd.DataFrame({
        "代码": ["sh600519", "sz000001"],
        "名称": ["贵州茅台", "平安银行"],
    }).to_parquet(cache / "stock_info.parquet", index=False)
    (cache / "etf_names.json").write_text(
        json.dumps({"sh520500": "恒生创新药ETF"}, ensure_ascii=False), encoding="utf-8"
    )
    dates = ["2026-08-21", "2026-08-24", "2026-08-25"]
    _daily("sh600519", dates, [1400.0, 1410.0, 1420.0]).to_parquet(
        cache / "stock_kline_cache.parquet", index=False
    )
    _daily("520500", dates, [1.40, 1.42, 1.43]).to_parquet(
        cache / "etf_kline_cache.parquet", index=False
    )
    _daily("sh000001", dates, [3300.0, 3310.0, 3320.0]).assign(来源="ak").to_parquet(
        cache / "index_kline_cache.parquet", index=False
    )
    minutes = pd.DataFrame({
        "代码": ["sh520500"] * 3,
        "时间": pd.to_datetime([
            "2026-08-25 10:14:00", "2026-08-25 10:15:00", "2026-08-25 10:16:00"
        ]),
        "开盘": [1.42, 1.43, 1.44], "最高": [1.43, 1.44, 1.45],
        "最低": [1.41, 1.42, 1.43], "收盘": [1.42, 1.43, 1.44],
        "成交量": [100, 200, 300], "成交额": [142, 286, 432],
    })
    minutes.to_parquet(cache / "minute_kline_cache.parquet", index=False)
    pd.DataFrame(columns=minutes.columns).to_parquet(
        cache / "index_minute_cache.parquet", index=False
    )
    pd.DataFrame({
        "代码": ["sh600519"], "日期": pd.to_datetime(["2026-08-25"]),
        "供应商量比": [1.2], "市盈率_动态": [22.0], "流通市值": [1e11],
    }).to_parquet(cache / "daily_basic_cache.parquet", index=False)
    (cache / "daily_update_status.json").write_text(
        json.dumps({"state": "success", "ok": True, "details": {"path": "/secret/cache"}}),
        encoding="utf-8",
    )

    news = MarketNewsRepository(state / "market_news.sqlite3")
    with news._connect() as connection:
        connection.execute(
            """INSERT INTO market_news
               (id,title,digest,published_at,market_date,phase,tags_json,cached_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            ("n1", "早盘消息", "摘要", "2026-08-25T10:14:08", "2026-08-25", "midday", "[]", "2026-08-25T10:20:00"),
        )
        connection.execute(
            """INSERT INTO market_news
               (id,title,digest,published_at,market_date,phase,tags_json,cached_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            ("n2", "未来消息", "摘要", "2026-08-25T10:16:00", "2026-08-25", "midday", "[]", "2026-08-25T10:20:00"),
        )
        connection.execute(
            """INSERT INTO market_news_fetch_log
               (market_date,fetched_at,complete,item_count,message) VALUES (?,?,?,?,?)""",
            ("2026-08-25", "2026-08-25T10:20:00", 1, 2, "cached"),
        )
    (output / "screen_to_trade_report.json").write_text(
        json.dumps({"report_card": {"trades": 3}}), encoding="utf-8"
    )
    return tmp_path


def _service(project: Path) -> ResearchService:
    resolver = InstrumentResolver(project)
    return ResearchService(LocalResearchRepository(project, resolver=resolver))


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_symbol_resolution_and_ambiguity(local_project):
    resolver = InstrumentResolver(local_project)
    etf = resolver.resolve("520500")
    assert etf.instrument_id == "etf:sh520500"
    assert etf.asset_type == "etf"
    with pytest.raises(AmbiguousInstrumentError) as error:
        resolver.resolve("000001")
    assert {item.instrument_id for item in error.value.candidates} == {
        "stock:sz000001", "index:sh000001"
    }


def test_daily_non_trading_day_resolves_previous(local_project):
    result = _service(local_project).get_daily_bars(
        "sh600519", end="2026-08-26", limit=2
    )
    assert result["requested_as_of"] == "2026-08-26"
    assert result["resolved_as_of"] == "2026-08-25"
    assert len(result["data"]) == 2
    assert result["warnings"]
    assert set(result["data"][0]).issubset({
        "code", "date", "open", "high", "low", "close", "previous_close",
        "volume", "volume_lots", "amount", "turnover_rate_pct", "source",
    })


def test_minute_as_of_is_strict_and_missing_day_does_not_fallback(local_project):
    service = _service(local_project)
    result = service.get_minute_bars(
        "etf:sh520500", "2026-08-25", as_of="10:15", limit=500
    )
    assert [row["time"] for row in result["data"]] == [
        "2026-08-25T10:14:00", "2026-08-25T10:15:00"
    ]
    missing = service.get_minute_bars("etf:sh520500", "2026-08-24")
    assert missing["data"] == []
    assert missing["freshness"]["status"] == "missing"
    assert "未回退" in missing["warnings"][0]


def test_cached_news_never_fetches_and_does_not_modify_sqlite(local_project):
    path = local_project / "state" / "market_news.sqlite3"
    before = (_digest(path), path.stat().st_mtime_ns)

    class ExplodingClient:
        def fetch_day(self, _):
            raise AssertionError("network fetch must not run")

    repository = MarketNewsRepository(path, client=ExplodingClient(), read_only=True)
    result = repository.cached_day("2026-08-25", as_of="10:15")
    after = (_digest(path), path.stat().st_mtime_ns)
    assert [item["id"] for item in result["items"]] == ["n1"]
    assert after == before
    with pytest.raises(PermissionError, match="禁止刷新"):
        repository.day("2026-08-25", refresh=True)


def test_artifact_path_is_allowlisted(local_project):
    service = _service(local_project)
    result = service.get_research_artifacts("screen_to_trade_report.json")
    assert result["data"][0]["summary"]["report_card"]["trades"] == 3
    with pytest.raises(ValueError, match="白名单"):
        service.get_research_artifacts("../config/openai.yaml")


def test_status_does_not_expose_internal_path(local_project):
    payload = _service(local_project).get_data_status()
    assert "/secret/cache" not in json.dumps(payload, ensure_ascii=False)


def test_contract_pagination_and_nan_cleanup():
    selected, page = paginate([1, 2, 3], offset=1, limit=1)
    assert selected == [2]
    assert page.has_more is True
    assert json_safe({"nan": np.nan, "inf": np.inf, "nat": pd.NaT}) == {
        "nan": None, "inf": None, "nat": None,
    }


def test_mcp_discovers_exact_read_only_tool_set():
    from mcp import Client
    from stock_mcp.server import mcp

    async def inspect():
        async with Client(mcp) as client:
            return await client.list_tools()

    result = asyncio.run(inspect())
    names = {tool.name for tool in result.tools}
    assert names == {
        "get_data_status", "search_symbols", "get_catalog", "get_daily_bars",
        "get_minute_bars", "get_feature_snapshot", "run_screener",
        "get_market_context", "get_market_news", "get_symbol_context",
        "get_research_artifacts",
    }
    for tool in result.tools:
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.idempotent_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.open_world_hint is False


def test_mcp_tool_errors_have_stable_json_shape():
    from mcp import Client
    from stock_mcp.server import mcp

    async def call_invalid():
        async with Client(mcp) as client:
            return await client.call_tool(
                "get_daily_bars", {"symbol": "not-a-symbol"}
            )

    result = asyncio.run(call_invalid())
    text = result.content[0].text
    payload = json.loads(text[text.index("{"):])
    assert result.is_error is True
    assert payload["schema_version"] == "1.0"
    assert payload["error"]["type"] == "InstrumentError"


def test_stdio_server_can_be_launched_and_called():
    from mcp import Client, StdioServerParameters

    async def inspect():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "stock_mcp.server"],
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        async with Client(parameters) as client:
            result = await client.call_tool("search_symbols", {"query": "520500"})
            return result.structured_content

    payload = asyncio.run(inspect())
    assert payload["data"][0]["instrument_id"] == "etf:sh520500"
