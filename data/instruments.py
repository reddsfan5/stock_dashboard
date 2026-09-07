"""统一证券标识解析。

本模块只读取本地证券目录，不访问网络。对同一六位代码属于多个资产类型的情况，
解析器返回明确的候选错误，调用方不能依靠市场前缀静默猜测资产类型。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
CODE_PATTERN = re.compile(r"^(?:(stock|etf|index):)?((?:sh|sz|bj)?)(\d{6})$", re.I)
INDEX_CATALOG = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sh000300": "沪深300",
    "sz399006": "创业板指",
    "sh000688": "科创50",
}


class InstrumentError(ValueError):
    """证券标识无法解析。"""


class InstrumentNotFoundError(InstrumentError):
    """本地目录中没有该证券。"""


class AmbiguousInstrumentError(InstrumentError):
    """一个输入对应多个本地证券。"""

    def __init__(self, value: str, candidates: Iterable["Instrument"]):
        self.value = value
        self.candidates = list(candidates)
        choices = ", ".join(item.instrument_id for item in self.candidates)
        super().__init__(f"证券代码 {value!r} 存在歧义，请使用完整标识：{choices}")


@dataclass(frozen=True)
class Instrument:
    instrument_id: str
    code: str
    market: str
    asset_type: str
    name: str
    storage_code: str

    def public_dict(self) -> dict:
        value = asdict(self)
        value.pop("storage_code", None)
        return value


def infer_market(digits: str) -> str:
    """仅用于生成候选；最终资产类型仍必须由本地目录确认。"""
    if digits.startswith(("4", "8")):
        return "bj"
    if digits.startswith(("5", "6", "9")):
        return "sh"
    return "sz"


class InstrumentResolver:
    """基于股票资料、ETF 名称缓存和指数常量建立轻量搜索目录。"""

    def __init__(
        self,
        project_dir: Path | str = PROJECT_DIR,
        *,
        stock_info_path: Optional[Path | str] = None,
        etf_names_path: Optional[Path | str] = None,
    ):
        self.project_dir = Path(project_dir)
        self.stock_info_path = Path(stock_info_path or self.project_dir / "cache" / "stock_info.parquet")
        self.etf_names_path = Path(etf_names_path or self.project_dir / "cache" / "etf_names.json")
        self._signature = None
        self._items: list[Instrument] = []

    @staticmethod
    def _file_signature(path: Path) -> tuple:
        try:
            stat = path.stat()
            return str(path), stat.st_mtime_ns, stat.st_size
        except OSError:
            return str(path), None, None

    def _load(self) -> list[Instrument]:
        signature = (
            self._file_signature(self.stock_info_path),
            self._file_signature(self.etf_names_path),
        )
        if signature == self._signature:
            return self._items

        items: dict[str, Instrument] = {}
        if self.stock_info_path.exists():
            frame = pd.read_parquet(self.stock_info_path, columns=["代码", "名称"])
            for code, name in frame[["代码", "名称"]].itertuples(index=False, name=None):
                prefixed = str(code).strip().lower()
                match = re.fullmatch(r"(sh|sz|bj)(\d{6})", prefixed)
                if not match:
                    continue
                market, _digits = match.groups()
                instrument = Instrument(
                    f"stock:{prefixed}", prefixed, market, "stock", str(name or prefixed), prefixed
                )
                items[instrument.instrument_id] = instrument

        if self.etf_names_path.exists():
            try:
                names = json.loads(self.etf_names_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                names = {}
            for code, name in names.items():
                prefixed = str(code).strip().lower()
                match = re.fullmatch(r"(sh|sz)(\d{6})", prefixed)
                if not match:
                    continue
                market, digits = match.groups()
                instrument = Instrument(
                    f"etf:{prefixed}", prefixed, market, "etf", str(name or prefixed), digits
                )
                items[instrument.instrument_id] = instrument

        for code, name in INDEX_CATALOG.items():
            market = code[:2]
            instrument = Instrument(
                f"index:{code}", code, market, "index", name, code
            )
            items[instrument.instrument_id] = instrument

        self._signature = signature
        self._items = sorted(items.values(), key=lambda item: item.instrument_id)
        return self._items

    def search(self, query: str = "", *, asset_type: Optional[str] = None, limit: int = 20) -> list[Instrument]:
        if asset_type not in (None, "stock", "etf", "index"):
            raise InstrumentError("asset_type 只能是 stock、etf 或 index")
        limit = min(max(int(limit), 1), 50)
        needle = str(query or "").strip().lower()
        compact = re.sub(r"\s+", "", needle)
        candidates = []
        for item in self._load():
            if asset_type and item.asset_type != asset_type:
                continue
            digits = item.code[-6:]
            haystacks = (item.instrument_id.lower(), item.code.lower(), digits, item.name.lower())
            if compact and not any(compact in value for value in haystacks):
                continue
            exact = compact in {item.instrument_id.lower(), item.code.lower(), digits}
            candidates.append((0 if exact else 1, item.name, item))
        candidates.sort(key=lambda row: (row[0], row[1], row[2].instrument_id))
        return [row[2] for row in candidates[:limit]]

    def all(self) -> tuple[Instrument, ...]:
        """返回完整轻量目录快照；不暴露内部可变列表。"""
        return tuple(self._load())

    def resolve(self, value: str, *, asset_type: Optional[str] = None) -> Instrument:
        text = str(value or "").strip().lower().replace(".", "")
        match = CODE_PATTERN.fullmatch(text)
        if not match:
            raise InstrumentError("证券标识须为 600519、sh600519 或 etf:sh520500 等格式")
        declared_type, declared_market, digits = match.groups()
        wanted_type = asset_type or declared_type
        if wanted_type not in (None, "stock", "etf", "index"):
            raise InstrumentError("asset_type 只能是 stock、etf 或 index")
        market = declared_market or None
        candidates = [
            item for item in self._load()
            if item.code.endswith(digits)
            and (wanted_type is None or item.asset_type == wanted_type)
            and (market is None or item.market == market)
        ]
        if not candidates:
            raise InstrumentNotFoundError(f"本地证券目录中未找到 {value!r}")
        if len(candidates) > 1:
            raise AmbiguousInstrumentError(value, candidates)
        return candidates[0]
