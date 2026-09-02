"""带版本与数据指纹的特征缓存。

旧实现只判断 Parquet 是否存在，导致日线更新后仍读取旧指标。本模块把公式版本、
股票池、源数据范围和最新数据摘要写入 manifest，任何一项变化都会自动重算。
"""

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Dict, Iterable, Optional

import pandas as pd

from data.storage import atomic_write_json, atomic_write_parquet


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_DIR = PROJECT_DIR / "cache" / "features"
FEATURE_SCHEMA_VERSION = 4


def _text_hash(values: Iterable[str]) -> str:
    payload = "\n".join(str(value) for value in values).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _latest_value_hash(frame: pd.DataFrame) -> str:
    """对最近两个交易日做轻量摘要，识别同日期的数据修订。"""
    dates = pd.to_datetime(frame["日期"])
    latest_dates = sorted(dates.dropna().unique())[-2:]
    recent = frame.loc[dates.isin(latest_dates)].copy()
    columns = [
        column for column in
        (
            "代码", "日期", "开盘", "最高", "最低", "收盘", "前收",
            "成交量", "成交额", "换手率%",
        )
        if column in recent.columns
    ]
    if not columns:
        return "empty"
    recent = recent[columns].sort_values(["代码", "日期"])
    digest = pd.util.hash_pandas_object(recent, index=False).values.tobytes()
    return hashlib.sha256(digest).hexdigest()[:16]


@dataclass(frozen=True)
class FeatureSignature:
    version: int
    scope: str
    rows: int
    codes: int
    code_hash: str
    first_date: str
    last_date: str
    latest_rows: int
    latest_value_hash: str
    columns: tuple

    def to_json(self) -> dict:
        value = asdict(self)
        value["columns"] = list(self.columns)
        return value


def build_signature(frame: pd.DataFrame, scope: str) -> FeatureSignature:
    if len(frame) == 0:
        return FeatureSignature(
            version=FEATURE_SCHEMA_VERSION,
            scope=scope,
            rows=0,
            codes=0,
            code_hash="empty",
            first_date="",
            last_date="",
            latest_rows=0,
            latest_value_hash="empty",
            columns=tuple(frame.columns),
        )
    dates = pd.to_datetime(frame["日期"])
    codes = sorted(frame["代码"].astype(str).unique())
    latest = dates.max()
    return FeatureSignature(
        version=FEATURE_SCHEMA_VERSION,
        scope=scope,
        rows=len(frame),
        codes=len(codes),
        code_hash=_text_hash(codes),
        first_date=dates.min().isoformat(),
        last_date=latest.isoformat(),
        latest_rows=int(dates.eq(latest).sum()),
        latest_value_hash=_latest_value_hash(frame),
        columns=tuple(sorted(str(column) for column in frame.columns)),
    )


def scope_name(board_prefix: Optional[Iterable[str]]) -> str:
    if not board_prefix:
        return "all"
    prefixes = tuple(str(value) for value in board_prefix)
    return f"universe-{_text_hash(prefixes)}"


class FeatureStore:
    """一个股票池对应一个可校验、可原子替换的特征缓存。"""

    def __init__(self, scope: str, cache_dir: Optional[os.PathLike] = None):
        root = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
        self.directory = root / scope
        self.manifest_path = self.directory / "manifest.json"

    def load(self, signature: FeatureSignature) -> Optional[Dict[str, pd.DataFrame]]:
        if not self.manifest_path.exists():
            return None
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except Exception:
            # 特征缓存属于可再生数据；文件损坏或引擎不兼容时直接回源重算。
            return None
        if manifest.get("signature") != signature.to_json():
            return None
        keys = manifest.get("features") or []
        if not keys:
            return None
        result = {}
        try:
            for key in keys:
                path = self.directory / f"{key}.parquet"
                if not path.exists():
                    return None
                result[key] = pd.read_parquet(path)
            for alias, target in (manifest.get("aliases") or {}).items():
                if target not in result:
                    return None
                result[alias] = result[target]
        except (OSError, ValueError):
            return None
        return result

    def save(
        self,
        features: Dict[str, pd.DataFrame],
        signature: FeatureSignature,
    ) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        stored = []
        aliases = {}
        objects = {}
        for key, frame in features.items():
            if isinstance(frame, pd.DataFrame):
                target = objects.get(id(frame))
                if target is not None:
                    aliases[key] = target
                    continue
                objects[id(frame)] = key
                stored.append(key)
                atomic_write_parquet(
                    frame,
                    str(self.directory / f"{key}.parquet"),
                    index=True,
                )
        manifest = {
            "signature": signature.to_json(),
            "features": sorted(stored),
            "aliases": aliases,
        }
        atomic_write_json(manifest, str(self.manifest_path))
