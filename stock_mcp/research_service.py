"""面向 MCP 工具的只读研究门面。"""

from __future__ import annotations

import contextlib
import dataclasses
import sys
from typing import Any, Optional

import pandas as pd

from data.instruments import AmbiguousInstrumentError, InstrumentResolver
from features.catalog import FEATURE_CATALOG, FEATURE_PROFILES
from features.snapshot import METRIC_DEFINITIONS, SCREENING_METRICS, build_decision_snapshot
from stock_mcp.contracts import paginate, response_envelope
from stock_mcp.repositories import ARTIFACT_ALLOWLIST, DATASETS, LocalResearchRepository


class ResearchService:
    """11 个 MCP 工具共享的业务层；构造时不读取大行情文件。"""

    def __init__(self, repository: Optional[LocalResearchRepository] = None):
        self.repository = repository or LocalResearchRepository()
        self.resolver: InstrumentResolver = self.repository.resolver

    @staticmethod
    def _instrument_payload(instrument) -> dict:
        return instrument.public_dict()

    def get_data_status(self) -> dict:
        status = self.repository.data_status()
        # 更新日志可能含本机绝对路径，只保留稳定、可解释的状态摘要。
        update = status.get("daily_update") or {}
        update_summary = {
            key: update.get(key) for key in
            ("state", "ok", "started_at", "updated_at", "target_date", "source")
            if key in update
        }
        update_summary["stages"] = [
            {
                key: stage.get(key) for key in
                ("name", "ok", "critical", "duration_seconds", "message")
                if key in stage
            }
            for stage in update.get("stages", [])
            if isinstance(stage, dict)
        ]
        status["daily_update"] = update_summary
        available = sum(
            item.get("status") == "available" for item in status["datasets"].values()
        )
        return response_envelope(
            summary=f"{available}/{len(DATASETS)} 个本地数据集可用",
            request={}, data=status, temporal_scope="current_cache_status",
            freshness={"checked_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat()},
            provenance=[{"storage": "local_cache_metadata"}],
        )

    def search_symbols(self, query="", asset_type=None, offset=0, limit=20) -> dict:
        # 先取全量轻目录匹配，再统一分页；不会扫描行情。
        matches = self.resolver.search(query, asset_type=asset_type, limit=50)
        items, page = paginate(
            [self._instrument_payload(item) for item in matches],
            offset=offset, limit=limit, maximum=50,
        )
        return response_envelope(
            summary=f"找到 {page.total} 个本地证券目录匹配项",
            request={"query": query, "asset_type": asset_type},
            data=items, pagination=page, temporal_scope="current_symbol_catalog",
            provenance=[{"dataset": "stock_info+etf_names+index_catalog", "storage": "local"}],
        )

    def get_catalog(self, section="all") -> dict:
        allowed = {"all", "datasets", "features", "screeners", "artifacts"}
        if section not in allowed:
            raise ValueError(f"section 只能是 {', '.join(sorted(allowed))}")
        payload: dict[str, Any] = {}
        if section in {"all", "datasets"}:
            payload["datasets"] = {
                key: {
                    "label": {
                        "stock_daily": "股票日K", "etf_daily": "ETF日K",
                        "index_daily": "指数日K", "minute": "股票/ETF分钟K",
                        "index_minute": "指数分钟K", "daily_basic": "日度估值与量比",
                        "stock_info": "证券名称与行业",
                    }[key],
                    "temporal_field": value[1],
                    "instrument_field": value[2],
                }
                for key, value in DATASETS.items()
            }
        if section in {"all", "features"}:
            feature_values = {
                key: dataclasses.asdict(spec) for key, spec in FEATURE_CATALOG.items()
            }
            for label, key in SCREENING_METRICS.items():
                feature_values.setdefault(key, {
                    "key": key,
                    "group": "screening_snapshot",
                    "description": METRIC_DEFINITIONS.get(label, label),
                    "unit": "%" if "%" in label else ("倍" if "比" in label else ""),
                    "label_zh": label,
                })
            payload["features"] = feature_values
            payload["feature_profiles"] = {
                key: list(value) for key, value in FEATURE_PROFILES.items()
            }
        if section in {"all", "screeners"}:
            payload["screeners"] = self._discover_screeners()
        if section in {"all", "artifacts"}:
            payload["artifacts"] = sorted(ARTIFACT_ALLOWLIST)
        return response_envelope(
            summary=f"返回 {section} 研究目录",
            request={"section": section}, data=payload,
            temporal_scope="catalog", provenance=[{"storage": "local_code_and_catalog"}],
        )

    def _resolve_or_candidates(self, symbol, asset_type=None):
        try:
            return self.resolver.resolve(symbol, asset_type=asset_type)
        except AmbiguousInstrumentError as exc:
            # 候选保留在异常文本之外，方便 MCP 客户端下一次显式选择。
            choices = [item.public_dict() for item in exc.candidates]
            raise ValueError(f"{exc}；candidates={choices}") from exc

    def get_daily_bars(
        self, symbol, start=None, end=None, limit=120, offset=0, asset_type=None,
    ) -> dict:
        instrument = self._resolve_or_candidates(symbol, asset_type)
        frame, resolved = self.repository.daily_bars(
            instrument, start=start, end=end, limit=500
        )
        records = self.repository.public_daily(frame)
        selected, page = paginate(records, offset=offset, limit=limit, maximum=500)
        requested = str(end) if end is not None else None
        warnings = []
        if requested and resolved and pd.Timestamp(requested).normalize() != pd.Timestamp(resolved):
            warnings.append("请求日期不是该标的交易日，已解析为此前最近交易日")
        return response_envelope(
            summary=f"{instrument.name} 返回 {page.returned}/{page.total} 根日K",
            request={
                "symbol": symbol, "instrument": instrument.public_dict(),
                "start": start, "end": end,
            },
            requested_as_of=requested, resolved_as_of=resolved,
            temporal_scope="historical_as_of" if end else "latest_local_cache",
            freshness={"status": "available" if records else "missing", "latest_bar": resolved},
            provenance=[self.repository.provenance(
                {"stock": "stock_daily", "etf": "etf_daily", "index": "index_daily"}[instrument.asset_type]
            )],
            warnings=warnings, pagination=page, data=selected,
        )

    def get_minute_bars(
        self, symbol, market_date, as_of=None, offset=0, limit=500, asset_type=None,
    ) -> dict:
        instrument = self._resolve_or_candidates(symbol, asset_type)
        frame = self.repository.minute_bars(
            instrument, market_date=market_date, as_of=as_of
        )
        if frame.empty:
            warnings = ["该标的在指定真实交易日没有本地分钟缓存；未回退到其他日期"]
            records = []
            resolved = None
        else:
            warnings = []
            records = self.repository.public_minute(frame)
            resolved = records[-1]["time"]
        selected, page = paginate(records, offset=offset, limit=limit, maximum=500)
        dataset = "index_minute" if instrument.asset_type == "index" else "minute"
        requested_as_of = f"{market_date}T{as_of}" if as_of else str(market_date)
        return response_envelope(
            summary=f"{instrument.name} {market_date} 返回 {page.returned}/{page.total} 根分钟K",
            request={
                "symbol": symbol, "instrument": instrument.public_dict(),
                "market_date": market_date, "as_of": as_of,
            },
            requested_as_of=requested_as_of, resolved_as_of=resolved,
            temporal_scope="historical_intraday_as_of",
            freshness={"status": "available" if records else "missing", "latest_returned_point": resolved},
            provenance=[self.repository.provenance(dataset)],
            warnings=warnings, pagination=page, data=selected,
        )

    def get_feature_snapshot(self, symbols, as_of=None) -> dict:
        if not isinstance(symbols, list) or not symbols:
            raise ValueError("symbols 必须是 1 到 50 个证券标识组成的数组")
        if len(symbols) > 50:
            raise ValueError("symbols 最多 50 个")
        instruments = [self._resolve_or_candidates(symbol) for symbol in symbols]
        codes = [item.code for item in instruments]
        daily, basic, resolved = self.repository.feature_inputs(
            as_of=as_of, instrument_codes=codes
        )
        snapshot = build_decision_snapshot(daily, basic, as_of=resolved)
        reverse = {label: key for label, key in SCREENING_METRICS.items()}
        snapshot = snapshot.rename(columns=reverse)
        by_code = {item.code: item for item in instruments}
        records = []
        for row in snapshot.to_dict("records"):
            item = by_code.get(row.get("代码"))
            row["instrument"] = item.public_dict() if item else {"code": row.get("代码")}
            row.pop("代码", None)
            records.append(row)
        warnings = [] if records else ["指定日期之前没有足够的本地日线，无法生成指标快照"]
        return response_envelope(
            summary=f"返回 {len(records)}/{len(symbols)} 个标的的指标快照",
            request={"symbols": symbols, "as_of": as_of},
            requested_as_of=str(as_of) if as_of else None, resolved_as_of=resolved,
            temporal_scope="historical_as_of" if as_of else "latest_local_cache",
            freshness={
                "status": (
                    "missing" if not records else
                    "partial" if len(records) < len(symbols) else "available"
                ),
                "feature_date": resolved,
            },
            provenance=[self.repository.provenance("stock_daily"), self.repository.provenance("daily_basic")],
            warnings=warnings, data=records,
        )

    @staticmethod
    def _discover_screeners() -> list[dict]:
        from pipeline.runner import discover_modules

        with contextlib.redirect_stdout(sys.stderr):
            modules = discover_modules()
        return [
            {"id": module.id, "title": module.title, "parameters": module.kwargs}
            for module in modules
        ]

    def run_screener(
        self, strategy_id, as_of=None, params=None, universe="all", offset=0, limit=50,
    ) -> dict:
        from pipeline.runner import discover_modules, run_module

        with contextlib.redirect_stdout(sys.stderr):
            modules = discover_modules()
        available = {module.id: module for module in modules}
        if strategy_id not in available:
            raise ValueError(f"未知策略；可选：{', '.join(sorted(available))}")
        module = available[strategy_id]
        params = params or {}
        if not isinstance(params, dict):
            raise ValueError("params 必须是对象")
        unknown = set(params).difference(module.kwargs)
        if unknown:
            raise ValueError(f"策略未声明参数：{', '.join(sorted(unknown))}")
        for key, value in params.items():
            if not isinstance(value, (str, int, float, bool)) or isinstance(value, complex):
                raise ValueError(f"参数 {key} 只能是字符串、数字或布尔值")
        selected_module = dataclasses.replace(module, kwargs={**module.kwargs, **params})
        data, resolved = self.repository.screener_data(as_of=as_of, universe=universe)
        # 既有策略可能输出 tqdm/诊断信息；全部转到 stderr，保护 STDIO 协议 stdout。
        with contextlib.redirect_stdout(sys.stderr):
            result = run_module(selected_module, data)
        records = [] if result is None or result.empty else result.to_dict("records")
        selected, page = paginate(records, offset=offset, limit=limit, maximum=100)
        return response_envelope(
            summary=f"{module.title} 在 {resolved} 命中 {page.total} 个标的",
            request={
                "strategy_id": strategy_id, "as_of": as_of,
                "params": params, "universe": universe,
            },
            requested_as_of=str(as_of) if as_of else None, resolved_as_of=resolved,
            temporal_scope="historical_as_of" if as_of else "latest_local_cache",
            freshness={"screening_date": resolved},
            provenance=[{"strategy_id": strategy_id, "source": "PIPELINE_META+find_all"}],
            pagination=page, data=selected,
        )

    def get_market_context(self, market_date, as_of=None) -> dict:
        from data.market_context import MarketContextService

        requested_day = pd.Timestamp(market_date).normalize()
        today = pd.Timestamp.now(tz="Asia/Shanghai").tz_localize(None).normalize()
        if requested_day > today:
            raise ValueError("不能查询未来市场情境")
        payload = MarketContextService().context(market_date, as_of)
        cards = [*(payload.get("a_share") or []), *(payload.get("overseas") or [])]
        context_status = (
            "missing" if not cards or all(item.get("source") == "empty" for item in cards)
            else "partial" if any(item.get("source") == "empty" for item in cards)
            else "available"
        )
        return response_envelope(
            summary=f"返回 {market_date} {payload.get('as_of')} 的本地市场情境",
            request={"market_date": market_date, "as_of": as_of},
            requested_as_of=f"{market_date}T{as_of or '15:00'}",
            resolved_as_of=payload.get("as_of_full"),
            temporal_scope="historical_intraday_as_of",
            freshness={"status": context_status, "market_date": market_date},
            provenance=[self.repository.provenance("index_daily"), self.repository.provenance("index_minute")],
            data=payload,
        )

    def get_market_news(
        self, market_date, as_of=None, phase=None, offset=0, limit=50,
    ) -> dict:
        from data.market_news import MarketNewsRepository

        news = MarketNewsRepository(
            self.repository.state_dir / "market_news.sqlite3", read_only=True
        ).cached_day(market_date, as_of=as_of, phase=phase)
        items, page = paginate(news.pop("items"), offset=offset, limit=limit, maximum=100)
        resolved = items[-1]["published_at"] if items else None
        warning = []
        if not items:
            warning.append("指定截止点之前没有本地资讯缓存；未联网刷新，也未回退到当前资讯")
        news["items"] = items
        return response_envelope(
            summary=f"{market_date} 返回 {page.returned}/{page.total} 条缓存资讯",
            request={"market_date": market_date, "as_of": as_of, "phase": phase},
            requested_as_of=f"{market_date}T{as_of}" if as_of else str(market_date),
            resolved_as_of=resolved, temporal_scope="historical_intraday_as_of",
            freshness={
                "status": "available" if items else "missing",
                "cached_at": news.get("fetched_at"), "complete": news.get("complete"),
            },
            provenance=[{"dataset": "market_news", "storage": "local_sqlite_read_only"}],
            warnings=warning, pagination=page, data=news,
        )

    def get_symbol_context(self, symbol) -> dict:
        instrument = self._resolve_or_candidates(symbol)
        context = self.repository.symbol_context(instrument.code)
        try:
            from scripts.services.symbol_context import load_screen_hits, load_screen_to_trade_note

            context["screen_hits"] = load_screen_hits(instrument.code)
            context["screen_to_trade"] = load_screen_to_trade_note(instrument.code)
        except Exception as exc:
            context["screen_context_warning"] = type(exc).__name__
        context["instrument"] = instrument.public_dict()
        return response_envelope(
            summary=f"返回 {instrument.name} 的当前研究上下文",
            request={"symbol": symbol, "instrument": instrument.public_dict()},
            temporal_scope="current_research_context_only",
            provenance=[{"storage": "local_sqlite_and_current_reports", "mode": "read_only"}],
            warnings=["当前研究上下文含最新观察池/日记/训练/报告，不得用于历史无剧透训练"],
            data=context,
        )

    def get_research_artifacts(self, artifact_id=None, offset=0, limit=50) -> dict:
        values = self.repository.artifacts(artifact_id)
        selected, page = paginate(values, offset=offset, limit=limit, maximum=100)
        return response_envelope(
            summary=f"返回 {page.returned}/{page.total} 个白名单研究报告",
            request={"artifact_id": artifact_id}, temporal_scope="current_artifact_catalog",
            freshness={"reports_service": "http://127.0.0.1:8000"},
            provenance=[{"storage": "allowlisted_output_artifacts"}],
            pagination=page, data=selected,
        )
