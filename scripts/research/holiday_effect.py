#!/usr/bin/env python3
"""A 股节假日效应回测（元旦 / 春节 / 五一 / 中秋 / 国庆，节日由注册表配置驱动）。

用法
----
$ python -m scripts.research.holiday_effect                          # 注册表里全部标的（沪深300、上证指数…），生成页面
$ python -m scripts.research.holiday_effect --targets hs300,sse       # 指定标的（slug 或代码）
$ python -m scripts.research.holiday_effect --index sh000300          # 兼容旧用法：只跑单个主指数
$ python -m scripts.research.holiday_effect --exclude-years 2015,2024  # 覆盖“剔除异常年”口径
$ python -m scripts.research.holiday_effect --no-fetch                # 只用本地缓存，不联网
$ python -m scripts.research.holiday_effect --no-html                 # 不生成页面

新增节日：在 backtest/holiday_effect.py 的 HOLIDAY_REGISTRY 追加一个 HolidaySpec。
新增标的：在 backtest/holiday_effect.py 的 HOLIDAY_TARGETS 追加一个 HolidayTarget（代码、名称、slug、起始年、代理…）。

数据
----
- 主指数与 sh000001/sz399006/sh000688：cache/index_kline_cache.parquet（IndexData）
- 中证500 sh000905 / 中证1000 sh000852 不在日常指数目录里：只补缺失年份，通过项目现有的
  IndexData._fetch_ak（腾讯日K）“一年一请求、间隔 ≥1.5 秒”低频获取，
  存入 cache/index_extra_kline_cache.parquet。
- 未来交易日（如尚未复牌的 T1）用 cache/trading_calendar.parquet 补齐。

输出
----
页面：output/holiday_effect.html（http://127.0.0.1:8765/holiday_effect.html）
明细：output/research/holiday_effect/<slug>/ 下的 CSV / JSON（页面按需懒加载）/ PNG / Markdown 报告；targets.json = 标的清单
总结：output/research/holiday_effect/<slug>/holiday_summary.md（每个标的一份）+ output/research/digests/holiday_effect-<slug>.json（导航页自动收录）
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

from backtest import holiday_effect as he  # noqa: E402
from backtest import research_digest  # noqa: E402
from backtest.holiday_report import build_holiday_page, digest_payload, ticker_payload  # noqa: E402
from data.index import IndexData, INDEXES  # noqa: E402
from data.storage import atomic_write_parquet  # noqa: E402

EXTRA_CACHE = PROJECT_DIR / "cache" / "index_extra_kline_cache.parquet"
CALENDAR_FILE = PROJECT_DIR / "cache" / "trading_calendar.parquet"
DEFAULT_OUT = PROJECT_DIR / "output" / "research" / "holiday_effect"
DEFAULT_HTML = PROJECT_DIR / "output" / "holiday_effect.html"
PAGE_URL = "http://127.0.0.1:8765/holiday_effect.html"
EXTRA_NAMES = {"sh000905": "中证500", "sh000852": "中证1000"}
NAMES = {**INDEXES, **EXTRA_NAMES}
DEFAULT_PROXIES = ",".join(he.DEFAULT_PROXIES)
MIN_FETCH_INTERVAL = 1.5  # 秒；与项目低频抓取约定一致，不得调低
RS_COLS = ["节前10日%", "节前5日%", "T0当日%", "T1跳空%", "T1当日%", "节后5日%", "节后10日%", "节后20日%"]
MATRIX_COLS = ["节前5日%", "T0当日%", "T1跳空%", "T1当日%", "节后5日%", "节后10日%", "节后20日%", "节前量比"]
MATRIX_RS = [("sh000852", "节前5日%"), ("sh000852", "节后5日%"), ("sh000852", "节后10日%"),
             ("sz399006", "节前5日%"), ("sz399006", "节后5日%")]


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------
def _read_extra() -> pd.DataFrame:
    if EXTRA_CACHE.exists():
        df = pd.read_parquet(EXTRA_CACHE)
        df["日期"] = pd.to_datetime(df["日期"])
        return df
    return pd.DataFrame(columns=["代码", "日期", "开盘", "最高", "最低", "收盘", "成交量(手)", "来源"])


def ensure_extra_indexes(codes, start_year: int, target: pd.Timestamp, fetch: bool) -> pd.DataFrame:
    """只补缺失年份（低频：一年一请求，间隔 ≥1.5 秒）。起点前一年起，预留 25 个交易日回看。"""
    cache = _read_extra()
    if not fetch or not codes:
        return cache
    idx = IndexData()
    first_year = start_year - 1
    new = []
    for code in codes:
        have = cache[cache["代码"] == code]
        if have.empty:
            years = list(range(first_year, target.year + 1))
        else:
            years = list(range(first_year, have["日期"].min().year))          # 往前补
            if have["日期"].max() < target:
                years += list(range(have["日期"].max().year, target.year + 1))  # 往后补
        if not years:
            continue
        print(f"  ↻ 低频补齐 {code} {NAMES.get(code, '')}: {years}（{len(years)} 次请求）")
        for y in years:
            df = idx._fetch_ak(code, f"{y}0101", f"{y}1231")
            time.sleep(MIN_FETCH_INTERVAL)
            if df is None:
                print(f"    ✗ {code} {y} 获取失败，保留已有缓存")
                continue
            new.append(df[df["日期"] <= target])
    if new:
        cache = pd.concat([cache] + new, ignore_index=True) if len(cache) else pd.concat(new, ignore_index=True)
        cache = cache.drop_duplicates(["代码", "日期"], keep="last").sort_values(["代码", "日期"])
        cache = cache.reset_index(drop=True)
        atomic_write_parquet(cache, str(EXTRA_CACHE))
        print(f"  ✓ 额外指数缓存已写入 {EXTRA_CACHE.relative_to(PROJECT_DIR)}（{len(cache)} 行）")
    return cache


def load_indexes(main: str, proxies, start_year: int, fetch: bool):
    base = IndexData().cache
    if main in set(base["代码"]):
        target = base.loc[base["代码"] == main, "日期"].max()
    else:  # 不在日常指数目录里的标的：以日常缓存的最新交易日为终点，低频补齐到额外缓存
        target = base["日期"].max()
        if not fetch and main not in set(_read_extra()["代码"]):
            raise SystemExit(f"标的 {main} 既不在 index_kline_cache 也不在额外缓存中；去掉 --no-fetch 以低频补齐")
    extra_codes = [c for c in [main] + list(proxies) if c not in set(base["代码"])]
    extra = ensure_extra_indexes(extra_codes, start_year, target, fetch)
    allc = pd.concat([base, extra], ignore_index=True) if len(extra) else base
    frames, missing = {}, []
    for code in dict.fromkeys([main] + list(proxies) + ["sh000001"]):
        df = allc[(allc["代码"] == code) & (allc["日期"] <= target)].sort_values("日期")
        df = df.drop_duplicates("日期", keep="first").reset_index(drop=True)
        if df.empty:
            missing.append(code)
            continue
        frames[code] = df
    return frames, missing


def trading_days(main_df: pd.DataFrame) -> pd.DatetimeIndex:
    days = pd.DatetimeIndex(main_df["日期"])
    if CALENDAR_FILE.exists():
        cal = pd.to_datetime(pd.read_parquet(CALENDAR_FILE)["日期"])
        days = days.union(pd.DatetimeIndex(cal[cal > days.max()]))
    return days


# ---------------------------------------------------------------------------
# 计算
# ---------------------------------------------------------------------------
def _clean(v):
    if isinstance(v, (float, np.floating)):
        return None if not np.isfinite(v) else round(float(v), 4)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (pd.Timestamp,)):
        return v.date().isoformat()
    return v


def records(df: pd.DataFrame) -> list:
    return [{k: _clean(v) for k, v in r.items()} for r in df.to_dict("records")]


def build(frames, main: str, proxies, start_year: int, exclude_years, n_boot: int):
    main_df = frames[main]
    days = trading_days(main_df)
    last = main_df["日期"].max()
    events, notes = he.identify(days, start_year=start_year, end_year=last.year)

    m = he.daily_metrics(main_df)
    pos_of = {d: i for i, d in enumerate(m["日期"])}
    in_sample = (m["日期"] >= pd.Timestamp(year=start_year, month=1, day=1)).to_numpy()
    rs = {p: he.relative_strength(m, he.daily_metrics(he.align_to(main_df, frames[p])))
          for p in proxies if p in frames}
    sh = he.daily_metrics(he.align_to(main_df, frames["sh000001"])) \
        if "sh000001" in frames and main != "sh000001" else None

    rows, positions = [], []
    for ev in events:
        row = ev.as_dict()
        i = pos_of.get(ev.t0)
        if i is None:
            row["状态"] = "未到"
            rows.append(row)
            positions.append(None)
            continue
        n_after = len(m) - 1 - i
        row["状态"] = "完整" if n_after >= 20 else ("节前已知，未复牌" if n_after == 0 else f"节后仅 {n_after} 日")
        for col in he.RETURN_COLS + he.RISK_COLS + he.VOLUME_COLS:
            row[col] = m.at[i, col]
        if sh is not None:
            row["上证节前量比"], row["上证节后量比"] = sh.at[i, "节前量比"], sh.at[i, "节后量比"]
        for p, r in rs.items():
            for col in he.RETURN_COLS:
                row[f"RS_{NAMES.get(p, p)}_{col}"] = r.at[i, col]
        rows.append(row)
        positions.append(i)
    ev_df = pd.DataFrame(rows)
    groups = he.group_names(ev_df)

    base = m[in_sample]
    done = ev_df[ev_df["状态"] == "完整"]  # 汇总只用已走完 T+20 的历史事件
    summary = he.summarize(done, base, he.RETURN_COLS + he.RISK_COLS + he.VOLUME_COLS,
                           groups, exclude_years, n_boot)
    rs_parts = []
    for p, r in rs.items():
        tmp = done[["类型", "节日", "年份", "休市自然日"]].copy()
        for col in he.RETURN_COLS:
            tmp[col] = done[f"RS_{NAMES.get(p, p)}_{col}"]
        s = he.summarize(tmp, r[in_sample], he.RETURN_COLS, groups, exclude_years, n_boot)
        s.insert(0, "代码", p)
        s.insert(1, "代理", NAMES.get(p, p))
        rs_parts.append(s)
    if sh is not None:
        tmp = done[["类型", "节日", "年份", "休市自然日"]].copy()
        tmp["节前量比"], tmp["节后量比"] = done["上证节前量比"], done["上证节后量比"]
        s = he.summarize(tmp, sh[in_sample], he.VOLUME_COLS, groups, exclude_years, n_boot)
        s.insert(0, "代码", "sh000001")
        s.insert(1, "代理", "上证指数成交量")
        rs_parts.append(s)
    rs_summary = pd.concat(rs_parts, ignore_index=True) if rs_parts else pd.DataFrame()

    # ---- 平均路径（主指数 + 各代理相对强弱）----
    valid_idx = [k for k, i in zip(ev_df.index, positions) if i is not None]
    valid_pos = [i for i in positions if i is not None]
    main_paths = he.cum_paths(main_df, valid_pos)
    main_paths.index = valid_idx
    rs_paths = {}
    for p in rs:
        pp = he.cum_paths(he.align_to(main_df, frames[p]), valid_pos)
        pp.index = valid_idx
        rs_paths[p] = pp - main_paths
    sub = ev_df.loc[valid_idx]
    complete = sub["状态"] == "完整"
    paths = {}
    for g in groups:
        paths[g] = {}
        for vkey in he.VARIANTS:
            if vkey == "ex" and not exclude_years:
                continue
            mask = he.group_mask(sub, g, exclude_years if vkey == "ex" else ()) & complete
            paths[g][vkey] = {
                "n": int(mask.sum()),
                "main": [_clean(v) for v in main_paths[mask].mean()],
                "rs": {p: [_clean(v) for v in rs_paths[p][mask].mean()] for p in rs_paths},
            }
    baseline = {"main": [_clean(v) for v in he.baseline_path(main_df, in_sample)], "rs": {}}
    for p in rs:
        pal = he.align_to(main_df, frames[p])
        baseline["rs"][p] = [_clean(a - b) for a, b in zip(he.baseline_path(pal, in_sample), baseline["main"])]
    live = []
    for k in sub.index[sub["状态"] == "节前已知，未复牌"]:
        live.append({"label": f"{ev_df.at[k, '年份']}{ev_df.at[k, '节日']}(进行中)",
                     "main": [_clean(v) for v in main_paths.loc[k]],
                     "rs": {p: [_clean(v) for v in rs_paths[p].loc[k]] for p in rs_paths}})

    return {"events": ev_df, "notes": notes, "groups": groups, "summary": summary,
            "rs_summary": rs_summary, "paths": paths, "baseline_path": baseline, "live": live,
            "metrics": m, "rs": rs, "sh": sh, "main_df": main_df, "baseline": base, "in_sample": in_sample,
            "last": last, "days": days}


def current_state(res, main: str, exclude_years) -> dict:
    """最新交易日视作 T0：近 5/10 日表现，与每个分组的历史节前分布及基准比较分位。"""
    m, ev = res["metrics"], res["events"]
    i = len(m) - 1
    done = ev[ev["状态"] == "完整"]
    items = [(NAMES.get(main, main), "节前5日%", m.at[i, "节前5日%"], "节前5日%", res["baseline"]["节前5日%"]),
             (NAMES.get(main, main), "节前10日%", m.at[i, "节前10日%"], "节前10日%", res["baseline"]["节前10日%"]),
             (NAMES.get(main, main), "T0当日%", m.at[i, "T0当日%"], "T0当日%", res["baseline"]["T0当日%"]),
             (NAMES.get(main, main), "节前量比", m.at[i, "节前量比"], "节前量比", res["baseline"]["节前量比"])]
    if res["sh"] is not None:
        items.append(("上证指数成交量", "节前量比", res["sh"].at[i, "节前量比"], "上证节前量比",
                      res["sh"]["节前量比"][res["in_sample"]]))
    for p, r in res["rs"].items():
        for col in ["节前5日%", "节前10日%"]:
            items.append((f"{NAMES.get(p, p)}−{NAMES.get(main, main)}", col, r.at[i, col],
                          f"RS_{NAMES.get(p, p)}_{col}", r[col][res["in_sample"]]))
    out = {}
    for g in res["groups"]:
        out[g] = {}
        for vkey in he.VARIANTS:
            if vkey == "ex" and not exclude_years:
                continue
            sel = done[he.group_mask(done, g, exclude_years if vkey == "ex" else ())]
            rows = []
            for name, col, value, key, base_series in items:
                hist = sel[key] if key in sel else pd.Series(dtype=float)
                rows.append({"项目": name, "指标": col, "当前值": value, "样本数": int(hist.notna().sum()),
                             "历史节前均值": hist.mean(), "历史节前中位数": hist.median(),
                             "历史分位%": he.percentile_of(value, hist),
                             "基准均值": base_series.mean(),
                             "基准分位%": he.percentile_of(value, base_series)})
            out[g][vkey] = pd.DataFrame(rows)
    return out


def _stat(summ: pd.DataFrame, g: str, v: str, col: str):
    s = summ[(summ["分组"] == g) & (summ["口径"] == v) & (summ["指标"] == col)]
    return None if s.empty else s.iloc[0]


def _rs_stat(rss: pd.DataFrame, code: str, g: str, v: str, col: str):
    if rss.empty:
        return None
    s = rss[(rss["代码"] == code) & (rss["分组"] == g) & (rss["口径"] == v) & (rss["指标"] == col)]
    return None if s.empty else s.iloc[0]


def matrix(res, exclude_years) -> pd.DataFrame:
    """分组 × 指标总览：均值 / 胜率，一眼看各节日的风险偏好。"""
    rows = []
    for g in res["groups"]:
        for vkey in he.VARIANTS:
            if vkey == "ex" and not exclude_years:
                continue
            r = {"分组": g, "口径": vkey}
            s0 = _stat(res["summary"], g, vkey, "节后5日%")
            r["样本数"] = int(s0["样本数"]) if s0 is not None else 0
            for col in MATRIX_COLS:
                s = _stat(res["summary"], g, vkey, col)
                r[f"{col}·均值"] = s["均值"] if s is not None else np.nan
                r[f"{col}·胜率"] = s["胜率%"] if s is not None else np.nan
            for code, col in MATRIX_RS:
                s = _rs_stat(res["rs_summary"], code, g, vkey, col)
                label = f"{NAMES.get(code, code)}RS {col}"
                r[f"{label}·均值"] = s["均值"] if s is not None else np.nan
                r[f"{label}·胜率"] = s["胜率%"] if s is not None else np.nan
            rows.append(r)
    return pd.DataFrame(rows)


def _pct(v, nd=2):
    return "—" if v is None or (isinstance(v, float) and not np.isfinite(v)) else f"{v:+.{nd}f}"


def conclusions(res, main: str, exclude_years) -> dict:
    """每组 × 口径的自动结论（全部由统计结果拼出，不含主观判断）。"""
    out = {}
    summ, rss = res["summary"], res["rs_summary"]
    for g in res["groups"]:
        out[g] = {}
        for vkey in he.VARIANTS:
            if vkey == "ex" and not exclude_years:
                continue
            lines = []
            n = _stat(summ, g, vkey, "节后5日%")
            if n is None or n["样本数"] == 0:
                out[g][vkey] = ["无完整事件"]
                continue
            parts = []
            for col, label in (("节前5日%", "节前5日"), ("T0当日%", "T0当日"), ("T1跳空%", "复牌跳空"),
                               ("节后5日%", "节后5日"), ("节后10日%", "节后10日"), ("节后20日%", "节后20日")):
                s = _stat(summ, g, vkey, col)
                parts.append(f"{label} {_pct(s['均值'])}%（中位 {_pct(s['中位数'])}%，胜率 {s['胜率%']:.0f}%，"
                             f"基准 {_pct(s['基准均值'])}%）")
            lines.append(f"{NAMES.get(main, main)}，{int(n['样本数'])} 个完整事件：" + "；".join(parts) + "。")
            vol = _stat(summ, g, vkey, "节前量比")
            if vol is not None and vol["样本数"]:
                lines.append(f"节前量比中位数 {vol['中位数']:.2f}，缩量占比 {vol['胜率%']:.0f}%"
                             f"（基准 {vol['基准胜率%']:.0f}%）。")
            for code in ("sh000852", "sh000905", "sz399006", "sh000688"):
                pre = _rs_stat(rss, code, g, vkey, "节前5日%")
                post = _rs_stat(rss, code, g, vkey, "节后5日%")
                post10 = _rs_stat(rss, code, g, vkey, "节后10日%")
                if pre is None or not pre["样本数"]:
                    continue
                pre_txt = "节前去风险" if pre["均值"] < 0 and pre["胜率%"] < 50 else (
                    "节前偏风险偏好" if pre["均值"] > 0 and pre["胜率%"] > 50 else "节前无一致方向")
                post_txt = "节后再风险" if post["均值"] > 0 and post["胜率%"] > 50 else (
                    "节后继续偏防御" if post["均值"] < 0 and post["胜率%"] < 50 else "节后无一致方向")
                lines.append(f"{NAMES.get(code, code)}−{NAMES.get(main, main)}（n={int(pre['样本数'])}）："
                             f"节前5日 {_pct(pre['均值'])}pp（跑赢 {pre['胜率%']:.0f}%）→ {pre_txt}；"
                             f"节后5日 {_pct(post['均值'])}pp（跑赢 {post['胜率%']:.0f}%）、"
                             f"节后10日 {_pct(post10['均值'])}pp（跑赢 {post10['胜率%']:.0f}%）→ {post_txt}。")
            out[g][vkey] = lines
    return out


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def plot(res, out_png: Path, main_name: str) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    plt.rcParams["font.sans-serif"] = ["Hiragino Sans GB", "STHeiti", "Arial Unicode MS",
                                       "PingFang SC", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    x = list(he.PATH_RANGE)
    fig, axes = plt.subplots(2, 1, figsize=(10, 8.5), sharex=True)
    for g in res["groups"]:
        if g in (he.LONG_GROUP,):
            continue
        p = res["paths"][g]["all"]
        lw = 2.6 if g == he.ALL_GROUP else 1.5
        axes[0].plot(x, [np.nan if v is None else v for v in p["main"]], lw=lw, label=f"{g}(n={p['n']})")
    axes[0].plot(x, res["baseline_path"]["main"], color="#555", ls="--", lw=2, label="基准")
    for lv in res["live"]:
        axes[0].plot(x, [np.nan if v is None else v for v in lv["main"]], color="#ff7f0e", ls=":", lw=2.2,
                     marker="o", ms=3, label=lv["label"])
    axes[0].axvline(0, color="#999", lw=1)
    axes[0].axhline(0, color="#999", lw=0.8)
    axes[0].set_ylabel("相对 T0 收盘累计涨跌 %")
    axes[0].set_title(f"{main_name} 节假日平均累计路径（T-10 ~ T+20）")
    axes[0].legend(fontsize=8, ncol=2)
    code = "sh000852" if "sh000852" in res["rs"] else next(iter(res["rs"]), None)
    if code:
        for g in (he.ALL_GROUP, he.LONG_GROUP):
            if g in res["paths"]:
                axes[1].plot(x, [np.nan if v is None else v for v in res["paths"][g]["all"]["rs"][code]],
                             lw=2, label=f"{g}")
        axes[1].plot(x, res["baseline_path"]["rs"][code], color="#555", ls="--", lw=2, label="基准")
        axes[1].set_title(f"风险偏好：{NAMES.get(code, code)} - {main_name} 累计相对强弱（百分点）")
    axes[1].axvline(0, color="#999", lw=1)
    axes[1].axhline(0, color="#999", lw=0.8)
    axes[1].set_xlabel("相对 T0 的交易日（T+1 = 复牌首日）")
    axes[1].legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    plt.close(fig)
    return True


def _f(v, nd=2, sign=True):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    if isinstance(v, (int, np.integer)):
        return str(v)
    return f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"


def md_table(df: pd.DataFrame) -> str:
    lines = ["| " + " | ".join(map(str, df.columns)) + " |", "|" + "|".join("---" for _ in df.columns) + "|"]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(_f(float(v)) if isinstance(v, (float, np.floating)) else str(v)
                                       for v in r.values) + " |")
    return "\n".join(lines)


def write_markdown(res, cur, mat, concl, meta, out_dir: Path):
    ev = res["events"]
    L = [f"# A 股节假日效应回测（{meta['index_name']}）\n",
         f"- 生成：{meta['generated']}；样本 {meta['sample']}；剔除异常年口径：{meta['exclude_years'] or '无'}",
         f"- 页面：{PAGE_URL}" + (f"?target={meta['target']['slug']}" if meta.get("target") else ""), "- 数据覆盖：" + "；".join(meta["coverage"])]
    if meta["notes"]:
        L.append("- 未形成事件：" + "；".join(meta["notes"]))
    L.append("- 总体总结（自动生成）：holiday_summary.md")
    L.append("\n## 事件数\n")
    L.append(md_table(pd.DataFrame([{"分组": g, "全部事件": int(he.group_mask(ev, g).sum()),
                                     "完整事件": int((he.group_mask(ev, g) & (ev["状态"] == "完整")).sum())}
                                    for g in res["groups"]])))
    L.append("\n## 总览矩阵（均值 / 胜率%）\n")
    show = mat.copy()
    cols = ["分组", "口径", "样本数"]
    for c in MATRIX_COLS + [f"{NAMES.get(code, code)}RS {col}" for code, col in MATRIX_RS]:
        show[c] = [f"{_f(a, 2, c != '节前量比')} / {_f(b, 0, False)}" for a, b in zip(show[f"{c}·均值"], show[f"{c}·胜率"])]
        cols.append(c)
    show["口径"] = show["口径"].map(he.VARIANTS)
    L.append(md_table(show[cols]))
    L.append("\n## 自动结论\n")
    for g in res["groups"]:
        for vkey, lines in concl[g].items():
            L.append(f"### {g} · {he.VARIANTS[vkey]}\n")
            L.extend(f"- {x}" for x in lines)
            L.append("")
    L.append("## 事件明细\n")
    keep = ["年份", "节日", "类型", "T0", "T1", "休市自然日", "状态", "节前5日%", "T0当日%", "T1跳空%",
            "节后5日%", "节后10日%", "节后20日%", "节前量比"]
    L.append(md_table(ev[[c for c in keep if c in ev]].astype({"年份": str})))
    L.append(f"\n## 当前位置（{res['last'].date()} 视作 T0，对比“全部”分组）\n")
    L.append(md_table(cur[he.ALL_GROUP]["all"]))
    L.append("\n> 历史统计关联，不构成投资建议。")
    (out_dir / "holiday_effect_report.md").write_text("\n".join(L), encoding="utf-8")


def kline_payload(main_df: pd.DataFrame, days: pd.DatetimeIndex, events: pd.DataFrame,
                  start_year: int) -> dict:
    """主指数日 K（紧凑数组）：起点前留约 3 个月给 MA20，末尾用交易日历补到即将到来休市的 T1 之后，
    未来日期 OHLC 为空，方便在 K 线上提前看到休市色带。成交量单位：万手。"""
    begin = pd.Timestamp(year=start_year - 1, month=10, day=1)
    k = main_df[main_df["日期"] >= begin].sort_values("日期")
    last = k["日期"].max()
    t1s = pd.to_datetime(events["T1"]).max() if len(events) else last
    future = [d for d in days if last < d <= t1s + pd.Timedelta(days=10)]
    r2 = lambda x: None if pd.isna(x) else round(float(x), 2)  # noqa: E731
    return {
        "d": [x.strftime("%Y-%m-%d") for x in k["日期"]] + [x.strftime("%Y-%m-%d") for x in future],
        "o": [r2(x) for x in k["开盘"]] + [None] * len(future),
        "h": [r2(x) for x in k["最高"]] + [None] * len(future),
        "l": [r2(x) for x in k["最低"]] + [None] * len(future),
        "c": [r2(x) for x in k["收盘"]] + [None] * len(future),
        "v": [None if pd.isna(x) else int(round(float(x) / 1e4)) for x in k["成交量(手)"]] + [None] * len(future),
        "last": last.date().isoformat(),
        "n_future": len(future),
    }


def bands_payload(events: pd.DataFrame) -> list:
    """每次休市一条色带（T0..T1），合并休市单独着色。"""
    out = []
    for _, r in events.iterrows():
        out.append({"year": int(r["年份"]), "label": r["节日"], "kind": r["类型"],
                    "tags": r["节日"].split("+"), "t0": r["T0"], "t1": r["T1"], "status": r["状态"],
                    "days": int(r["休市自然日"])})
    return out


def build_payload(res, cur, mat, concl, meta, exclude_years):
    ev = res["events"].copy()
    ev["标签"] = ev["节日"].str.split("+")
    rss = res["rs_summary"]
    return {
        "meta": meta,
        "groups": res["groups"],
        "holidays": [{"key": s.key, "name": s.name, "color": s.color} for s in he.HOLIDAY_REGISTRY],
        "merged_color": he.MERGED_COLOR,
        "bands": bands_payload(res["events"]),
        "kline": kline_payload(res["main_df"], res["days"], res["events"], meta["start"]),
        "variants": {k: v for k, v in he.VARIANTS.items() if k == "all" or exclude_years},
        "events": records(ev),
        "summary": records(res["summary"]),
        "rs_summary": records(rss) if not rss.empty else [],
        "matrix": records(mat),
        "matrix_cols": MATRIX_COLS,
        "matrix_rs": [f"{NAMES.get(code, code)}RS {col}" for code, col in MATRIX_RS],
        "rs_cols": RS_COLS,
        "paths": res["paths"],
        "baseline_path": res["baseline_path"],
        "live": res["live"],
        "path_range": list(he.PATH_RANGE),
        "current": {g: {v: records(df) for v, df in d.items()} for g, d in cur.items()},
        "conclusions": concl,
    }


COMPARE_METRICS = ("节前5日%", "T1跳空%", "节后5日%")
TARGET_DATA_URL = "/research/holiday_effect/{slug}/holiday_effect.json"


def run_target(tgt: "he.HolidayTarget", out_root: Path, fetch: bool, n_boot: int) -> dict:
    """单个标的：回测 → CSV / JSON / PNG / Markdown / 摘要，全部写到 out_root/<slug>/，返回页面 payload。"""
    exclude_years = list(tgt.exclude_years)
    proxies = [p for p in tgt.proxies if p != tgt.code]
    frames, missing = load_indexes(tgt.code, proxies, tgt.start, fetch=fetch)
    proxies = [p for p in proxies if p in frames]
    res = build(frames, tgt.code, proxies, tgt.start, exclude_years, n_boot)
    cur = current_state(res, tgt.code, exclude_years)
    mat = matrix(res, exclude_years)
    concl = conclusions(res, tgt.code, exclude_years)

    ev = res["events"]
    upcoming = []
    for _, r in ev[ev["状态"].isin(["未到", "节前已知，未复牌"])].iterrows():
        gap = int(((res["days"] > res["last"]) & (res["days"] <= pd.Timestamp(r["T0"]))).sum())
        upcoming.append({"节日": f"{r['年份']}{r['节日']}", "T0": r["T0"], "T1": r["T1"],
                         "状态": r["状态"], "距T0交易日": gap})
    meta = {
        "generated": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
        "index": tgt.code, "index_name": tgt.name,
        "sample": f"{tgt.start}-01-01 ~ {res['last'].date()}",
        "last_date": res["last"].date().isoformat(), "start": tgt.start,
        "exclude_years": exclude_years,
        "proxies": [{"code": p, "name": NAMES.get(p, p)} for p in proxies],
        "coverage": [f"{NAMES.get(c, c)} {c}: {df['日期'].min().date()} ~ {df['日期'].max().date()}"
                     for c, df in frames.items()],
        "missing": missing, "notes": res["notes"], "upcoming": upcoming,
        "command": f"python -m scripts.research.holiday_effect --targets {tgt.slug}",
        "target": {"code": tgt.code, "name": tgt.name, "slug": tgt.slug, "note": tgt.note},
    }

    out_dir = out_root / tgt.slug
    out_dir.mkdir(parents=True, exist_ok=True)
    kw = dict(index=False, encoding="utf-8-sig", float_format="%.4f")
    ev.to_csv(out_dir / "holiday_events.csv", **kw)
    res["summary"].to_csv(out_dir / "holiday_summary.csv", **kw)
    res["rs_summary"].to_csv(out_dir / "holiday_rs_summary.csv", **kw)
    mat.to_csv(out_dir / "holiday_matrix.csv", **kw)
    pd.concat([df.assign(分组=g, 口径=v) for g, d in cur.items() for v, df in d.items()]).to_csv(
        out_dir / "holiday_current_state.csv", **kw)
    payload = build_payload(res, cur, mat, concl, meta, exclude_years)
    payload["ticker"] = ticker_payload(payload)
    payload["digest"] = digest_payload(payload)  # 总体总结：页面顶部卡片 + Markdown + 导航页“研究结论速览”
    digest_json = research_digest.save(payload["digest"])
    (out_dir / "holiday_effect.json").write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")), encoding="utf-8")
    plot(res, out_dir / "holiday_effect_paths.png", meta["index_name"])
    write_markdown(res, cur, mat, concl, meta, out_dir)

    print(f"\n[{tgt.name} {tgt.code}] 事件数：{len(ev)}（合并 {int((ev['类型'] == he.MERGED).sum())}，"
          f"完整 {int((ev['状态'] == '完整').sum())}）")
    for g in res["groups"]:
        print(f"  {g}: {int(he.group_mask(ev, g).sum())} 个（完整 {int((he.group_mask(ev, g) & (ev['状态'] == '完整')).sum())}）")
    for n in res["notes"]:
        print(f"  · {n}")
    print(f"✓ 明细输出：{out_dir}")
    print(f"✓ 总体总结：{PROJECT_DIR / payload['digest']['md_path']}（卡片数据 {digest_json}）")
    if missing:
        print(f"⚠ 缺失指数：{missing}")
    return payload


def targets_manifest(payloads: list) -> dict:
    """页面标的切换 + “标的对比”所需的轻量清单（完整数据按需从 data URL 懒加载）。"""
    items, compare = [], {}
    for p in payloads:
        t = p["meta"]["target"]
        items.append({**t, "data": TARGET_DATA_URL.format(slug=t["slug"]), "last_date": p["meta"].get("last_date"),
                      "sample": p["meta"].get("sample")})
        compare[t["slug"]] = [{k: r.get(k) for k in ("分组", "口径", "指标", "样本数", "均值", "中位数", "胜率%")}
                              for r in p["summary"] if r.get("指标") in COMPARE_METRICS]
    return {"default": items[0]["slug"] if items else None, "targets": items, "compare": compare,
            "metrics": list(COMPARE_METRICS)}


def resolve_targets(arg: str, index: str | None, start: int | None, proxies: str | None,
                    exclude_years: str | None) -> list:
    if index:  # 兼容旧参数：--index 指定单个标的（注册表里有就用注册表配置）
        base = he.get_target(index) or he.HolidayTarget(index, NAMES.get(index, index), index)
        chosen = [base]
    elif not arg or arg == "all":
        chosen = list(he.HOLIDAY_TARGETS)
    else:
        chosen = []
        for k in arg.split(","):
            t = he.get_target(k)
            if t is None:
                raise SystemExit(f"未知标的 {k}；可选：" + "、".join(f"{x.slug}({x.code})" for x in he.HOLIDAY_TARGETS))
            chosen.append(t)
    over = {}
    if start is not None:
        over["start"] = start
    if proxies is not None:
        over["proxies"] = tuple(p.strip() for p in proxies.split(",") if p.strip())
    if exclude_years is not None:
        over["exclude_years"] = tuple(int(x) for x in exclude_years.split(",") if x.strip())
    return [replace(t, **over) for t in chosen] if over else chosen


def main():
    ap = argparse.ArgumentParser(description="A 股节假日效应回测（节日注册表 × 标的注册表）")
    ap.add_argument("--targets", default="all",
                    help="标的：all（默认，backtest/holiday_effect.py 的 HOLIDAY_TARGETS 全部）或逗号分隔的 slug/代码，如 hs300,sse")
    ap.add_argument("--index", default=None, help="（兼容）只跑单个主指数代码，如 sh000300")
    ap.add_argument("--start", type=int, default=None, help="覆盖所有标的的起始年份（默认用注册表，2015）")
    ap.add_argument("--proxies", default=None, help=f"覆盖风险偏好代理，逗号分隔（默认 {DEFAULT_PROXIES}）")
    ap.add_argument("--exclude-years", default=None,
                    help="覆盖“剔除异常年”口径的事件年份，逗号分隔（默认 2015,2024；传空串关闭）")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="CSV/报告输出根目录（每个标的一个子目录）")
    ap.add_argument("--html", default=str(DEFAULT_HTML), help="页面输出路径")
    ap.add_argument("--no-html", action="store_true", help="不生成页面与导航")
    ap.add_argument("--no-nav", action="store_true", help="生成页面但不刷新导航页")
    ap.add_argument("--no-fetch", action="store_true", help="只用本地缓存，缺的指数直接跳过")
    ap.add_argument("--bootstrap", type=int, default=5000, help="自助抽样次数（0 关闭 p 值）")
    args = ap.parse_args()

    targets = resolve_targets(args.targets, args.index, args.start, args.proxies, args.exclude_years)
    out_root = Path(args.out)
    out_root = out_root if out_root.is_absolute() else PROJECT_DIR / out_root
    payloads = [run_target(t, out_root, fetch=not args.no_fetch, n_boot=args.bootstrap) for t in targets]
    manifest = targets_manifest(payloads)
    (out_root / "targets.json").write_text(json.dumps(manifest, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    if args.targets == "all" and not args.index:  # 清掉已不在注册表里的标的摘要（含旧版单标的 holiday_effect.json）
        removed = research_digest.prune("holiday_effect", [p["digest"]["id"] for p in payloads])
        for r in removed:
            print(f"  · 移除过期摘要 {r}")

    if not args.no_html:
        html_path = Path(args.html)
        html_path = html_path if html_path.is_absolute() else PROJECT_DIR / html_path
        html_path.write_text(build_holiday_page(payloads[0], manifest), encoding="utf-8")
        print(f"✓ 页面：{html_path} → {PAGE_URL}（标的：" + "、".join(t.name for t in targets) + "）")
        if not args.no_nav:
            for module in ("scripts.reports.gen_index", "scripts.reports.gen_mobile"):
                subprocess.run([sys.executable, "-m", module], cwd=PROJECT_DIR, check=False)


if __name__ == "__main__":
    main()
