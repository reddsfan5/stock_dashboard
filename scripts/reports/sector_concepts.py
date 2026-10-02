"""题材 / 概念叠加层（板块相关点云）。

从本地 classifications DB（state/classifications.sqlite3）读取同花顺题材、东方财富概念等
0/1 成分关系，把每个概念的成分股映射到点云里的申万二级节点。

产物分两份：
- 页面内嵌的轻量索引 ``payload["concepts"]``：概念名、来源、成分数，供搜索提示即时可用；
- 懒加载的 ``output/sector_concepts.json``：概念 → 成分股（下标），以及成分股名称、所属二级、
  最近一日与近 20 日涨跌。第一次搜索概念时才由页面 fetch。

配置见 config/sector_corr_cloud.yaml 的 ``concepts`` 段。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

PROJECT_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_DIR / "config" / "sector_corr_cloud.yaml"
KLINE_PATH = PROJECT_DIR / "cache" / "stock_kline_cache.parquet"

DEFAULTS: dict = {
    "enabled": True,
    "db": "state/classifications.sqlite3",
    "output": "output/sector_concepts.json",
    "min_members": 3,
    "max_members": 0,          # 0 = 不设上限
    "return_days": 20,
    "sources": [
        {"key": "ths", "label": "同花顺", "source": "ths", "kind": "同花顺题材"},
        {"key": "em", "label": "东财", "source": "eastmoney", "kind": "数据商板块",
         "exclude_suffix": ["板块", "Ⅱ", "Ⅲ"], "exclude_shenwan_names": True, "exclude": [], "exclude_regex": []},
    ],
}


def load_concept_config(path: Path | str = CONFIG_PATH) -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raw = {}
    user = raw.get("concepts") or {}
    for k, v in user.items():
        cfg[k] = v
    return cfg


def bare_code(code) -> str:
    s = str(code).strip().lower()
    m = re.search(r"(\d{6})", s)
    return m.group(1) if m else s


def _connect(db_path: Path) -> sqlite3.Connection:
    """只读打开；WAL 库在缺 -shm 时 mode=ro 会失败，退回 immutable（同样不写库）。"""
    uri = Path(db_path).resolve().as_uri()
    try:
        db = sqlite3.connect(f"{uri}?mode=ro", uri=True, timeout=10)
        db.execute("SELECT 1 FROM board LIMIT 1")
        return db
    except sqlite3.Error:
        return sqlite3.connect(f"{uri}?immutable=1", uri=True)


def _excluded(name: str, src: dict, sw_names: set[str] = frozenset()) -> bool:
    """行业 / 地域 / 市场属性类板块不算概念：显式名单、后缀、正则，以及与申万行业同名（东财行业板块）。"""
    if name in set(src.get("exclude") or []):
        return True
    if any(name.endswith(suf) for suf in (src.get("exclude_suffix") or [])):
        return True
    if any(re.search(rx, name) for rx in (src.get("exclude_regex") or [])):
        return True
    return bool(src.get("exclude_shenwan_names")) and name in sw_names


def read_concepts(db_path: Path | str, cfg: dict) -> dict[tuple[str, str], list[str]]:
    """{(source_key, concept_name): [bare_code, ...]}，只取当前有效成分（observed_to IS NULL）。"""
    out: dict[tuple[str, str], list[str]] = {}
    db = _connect(Path(db_path))
    try:
        sw_names = {r[0] for r in db.execute("SELECT name FROM board WHERE source='shenwan'")}
        for src in cfg.get("sources") or []:
            rows = db.execute(
                """SELECT b.name, m.code FROM membership m JOIN board b USING(source, board_id)
                   WHERE b.source=? AND b.kind=? AND m.observed_to IS NULL""",
                (src["source"], src["kind"]),
            ).fetchall()
            for name, code in rows:
                name = str(name).strip()
                if not name or _excluded(name, src, sw_names):
                    continue
                out.setdefault((src["key"], name), []).append(bare_code(code))
    finally:
        db.close()
    lo, hi = int(cfg.get("min_members") or 1), int(cfg.get("max_members") or 0)
    res = {}
    for k, codes in out.items():
        codes = sorted(set(codes))
        if len(codes) < lo or (hi and len(codes) > hi):
            continue
        res[k] = codes
    return res


def stock_returns(path: Path | str = KLINE_PATH, days: int = 20) -> tuple[str | None, dict[str, tuple]]:
    """每只股票 (最近一日涨跌%, 近 days 日累计涨跌%)；用 收盘/前收 逐日复利。"""
    path = Path(path)
    if not path.exists():
        return None, {}
    try:
        dates = pd.read_parquet(path, columns=["日期"])["日期"]
        sessions = pd.Index(pd.to_datetime(dates, errors="coerce").dropna().unique()).sort_values()
        if sessions.empty:
            return None, {}
        recent = sessions[-days:]
        bars = pd.read_parquet(path, columns=["代码", "日期", "收盘", "前收"],
                               filters=[("日期", ">=", pd.Timestamp(recent[0]))])
    except Exception as exc:  # 行情缓存缺失 / 损坏时概念层照常可用，只是没有涨跌
        print(f"  概念层：读取日线缓存失败 {exc}")
        return None, {}
    bars["bare"] = bars["代码"].map(bare_code)
    close = pd.to_numeric(bars["收盘"], errors="coerce")
    prev = pd.to_numeric(bars["前收"], errors="coerce")
    bars["r"] = np.where(prev > 0, close / prev - 1.0, np.nan)
    as_of = pd.Timestamp(sessions[-1])
    last = bars[bars["日期"] == as_of].set_index("bare")["r"]
    cum = bars.groupby("bare")["r"].apply(lambda s: float(np.prod(1.0 + s.dropna())) - 1.0 if s.notna().any() else np.nan)
    out = {}
    for code in set(last.index) | set(cum.index):
        lv = last.get(code)
        cv = cum.get(code)
        out[code] = (
            None if lv is None or not np.isfinite(lv) else round(float(lv) * 100, 2),
            None if cv is None or not np.isfinite(cv) else round(float(cv) * 100, 2),
        )
    return as_of.strftime("%Y-%m-%d"), out


def build_concepts(concepts: dict[tuple[str, str], list[str]], stock_map: dict[str, tuple[str, str]],
                   returns: dict[str, tuple], sector_ids: set[str], cfg: dict, as_of: str | None = None) -> tuple[dict, dict]:
    """返回 (内嵌索引, 懒加载 JSON)。

    stock_map: bare_code → (名称, 申万二级)；returns: bare_code → (last%, r20%)。
    内嵌索引 list 每项 [来源, 名称, 成分数, 落在点云的成分数, 落在几个二级]。
    """
    labels = {s["key"]: s.get("label", s["key"]) for s in cfg.get("sources") or []}
    codes = sorted({c for v in concepts.values() for c in v})
    pos = {c: i for i, c in enumerate(codes)}
    stocks = []
    for c in codes:
        name, sector = stock_map.get(c, (c, ""))
        last, r20 = returns.get(c, (None, None))
        stocks.append([c, name, sector if sector in sector_ids else "", last, r20])
    items, index = {}, []
    for (src, name), cs in sorted(concepts.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        key = f"{src}:{name}"
        items[key] = [pos[c] for c in cs]
        secs = {stocks[pos[c]][2] for c in cs if stocks[pos[c]][2]}
        n_in = sum(1 for c in cs if stocks[pos[c]][2])
        index.append([src, name, len(cs), n_in, len(secs)])
    meta = {
        "v": 1,
        "as_of": as_of,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sources": labels,
    }
    lazy = {**meta, "fields": ["code", "name", "sector", "last", "r20"], "stocks": stocks, "concepts": items}
    embed = {**meta, "file": Path(cfg.get("output") or DEFAULTS["output"]).name, "min_members": int(cfg.get("min_members") or 1),
             "return_days": int(cfg.get("return_days") or 20), "list": index}
    return embed, lazy


def attach_concepts(payload: dict, stock_map: dict[str, tuple[str, str]] | None = None,
                    cfg: dict | None = None, project_dir: Path = PROJECT_DIR, kline: Path | str = KLINE_PATH) -> dict:
    """把概念索引写进 payload["concepts"]，成分明细写到 output/sector_concepts.json。失败时不影响主页面。"""
    cfg = cfg or load_concept_config()
    if not cfg.get("enabled", True):
        payload.pop("concepts", None)
        return payload
    db_path = project_dir / cfg.get("db", DEFAULTS["db"])
    if not db_path.exists():
        print(f"  概念层：缺少 {db_path}，跳过")
        payload.pop("concepts", None)
        return payload
    sector_ids = {n["id"] for n in payload.get("nodes", [])}
    if stock_map is None:
        stock_map = load_stock_map()
    try:
        concepts = read_concepts(db_path, cfg)
    except sqlite3.Error as exc:
        print(f"  概念层：读取分类库失败 {exc}")
        payload.pop("concepts", None)
        return payload
    as_of, rets = stock_returns(kline, int(cfg.get("return_days") or 20))
    embed, lazy = build_concepts(concepts, stock_map, rets, sector_ids, cfg, as_of)
    out = project_dir / (cfg.get("output") or DEFAULTS["output"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(lazy, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    payload["concepts"] = embed
    by_src = {}
    for row in embed["list"]:
        by_src[row[0]] = by_src.get(row[0], 0) + 1
    labels = embed["sources"]
    parts = " · ".join(f"{labels.get(k, k)} {v}" for k, v in by_src.items())
    print(f"  概念层：{parts} 个概念"
          f"（≥{embed['min_members']} 只）· 成分股 {len(lazy['stocks'])} · {out.name} {out.stat().st_size / 1024:.0f} KB")
    return payload


def load_stock_map() -> dict[str, tuple[str, str]]:
    """bare_code → (名称, 申万二级)，与点云 stock_index 同源（data.industry.StockInfo）。"""
    from data.industry import StockInfo
    try:
        info = StockInfo().df
    except Exception as exc:
        print(f"  概念层：StockInfo 不可用 {exc}")
        return {}
    sw2 = next((c for c in ("申万2级", "申万二级", "sw_l2") if c in info.columns), None)
    if sw2 is None or "代码" not in info.columns:
        return {}
    name_col = "名称" if "名称" in info.columns else None
    out = {}
    for code, name, sector in zip(info["代码"], info[name_col] if name_col else info["代码"], info[sw2]):
        b = bare_code(code)
        if b not in out:
            out[b] = (str(name), "" if pd.isna(sector) else str(sector))
    return out
