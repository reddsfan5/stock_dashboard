#!/usr/bin/env python3
"""国庆 / 中秋节假日效应回测（沪深300 为主，含风险偏好代理）。

用法
----
$ python -m scripts.research.holiday_effect                         # 默认 2017 至今，沪深300
$ python -m scripts.research.holiday_effect --start 2017 --index sh000300
$ python -m scripts.research.holiday_effect --no-fetch               # 只用本地缓存，不联网
$ python -m scripts.research.holiday_effect --out output/research/holiday_effect

数据
----
- 主指数与 sh000001/sz399006/sh000688：cache/index_kline_cache.parquet（IndexData）
- 中证500 sh000905 / 中证1000 sh000852 不在日常指数目录里：首次运行时通过项目现有的
  IndexData._fetch_ak（腾讯日K）按“一年一请求、请求间隔 ≥1.5 秒”低频补齐，
  存入 cache/index_extra_kline_cache.parquet，之后只增量补最新年份。
- 未来交易日（如尚未复牌的 T1）用 cache/trading_calendar.parquet 补齐。

输出（默认 output/research/holiday_effect/）
----
holiday_events.csv / holiday_summary.csv / holiday_rs_summary.csv / holiday_paths.csv
holiday_effect_paths.png（若 matplotlib 可用）/ holiday_effect_report.md
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

from backtest import holiday_effect as he  # noqa: E402
from data.index import IndexData, INDEXES  # noqa: E402
from data.storage import atomic_write_parquet  # noqa: E402

EXTRA_CACHE = PROJECT_DIR / "cache" / "index_extra_kline_cache.parquet"
CALENDAR_FILE = PROJECT_DIR / "cache" / "trading_calendar.parquet"
DEFAULT_OUT = PROJECT_DIR / "output" / "research" / "holiday_effect"
EXTRA_NAMES = {"sh000905": "中证500", "sh000852": "中证1000"}
NAMES = {**INDEXES, **EXTRA_NAMES}
DEFAULT_PROXIES = "sz399006,sh000852,sh000905,sh000688"
MIN_FETCH_INTERVAL = 1.5  # 秒；与项目低频抓取约定一致，不得调低


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
    """补齐不在日常指数目录里的指数（低频：一年一请求，间隔 ≥1.5 秒）。"""
    cache = _read_extra()
    if not fetch or not codes:
        return cache
    idx = IndexData()
    first_needed = pd.Timestamp(year=start_year - 1, month=11, day=1)  # 预留 25 个交易日回看
    new = []
    for code in codes:
        have = cache[cache["代码"] == code]
        if have.empty or have["日期"].min() > first_needed:
            years = range(start_year - 1, target.year + 1)
        elif have["日期"].max() < target:
            years = range(have["日期"].max().year, target.year + 1)
        else:
            continue
        print(f"  ↻ 低频补齐 {code} {NAMES.get(code, '')}: {years.start}~{years.stop - 1}（{len(years)} 次请求）")
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
    if main not in set(base["代码"]):
        raise SystemExit(f"主指数 {main} 不在 index_kline_cache 中，请先 python data/index.py --update")
    target = base.loc[base["代码"] == main, "日期"].max()
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
    return frames, missing, target


def trading_days(main_df: pd.DataFrame) -> pd.DatetimeIndex:
    days = pd.DatetimeIndex(main_df["日期"])
    if CALENDAR_FILE.exists():
        cal = pd.to_datetime(pd.read_parquet(CALENDAR_FILE)["日期"])
        days = days.union(pd.DatetimeIndex(cal[cal > days.max()]))
    return days


# ---------------------------------------------------------------------------
# 计算
# ---------------------------------------------------------------------------
def build(frames, main: str, proxies, start_year: int, n_boot: int):
    main_df = frames[main]
    days = trading_days(main_df)
    last = main_df["日期"].max()
    events = he.identify_events(days, start_year=start_year, end_year=max(last.year, start_year))

    m = he.daily_metrics(main_df)
    pos_of = {d: i for i, d in enumerate(m["日期"])}
    in_sample = (m["日期"] >= pd.Timestamp(year=start_year, month=1, day=1)).to_numpy()

    rs = {}
    for p in proxies:
        if p in frames:
            pm = he.daily_metrics(he.align_to(main_df, frames[p]))
            rs[p] = he.relative_strength(m, pm)
    sh = None
    if "sh000001" in frames and main != "sh000001":
        sh = he.daily_metrics(he.align_to(main_df, frames["sh000001"]))

    rows, positions = [], []
    for ev in events:
        row = ev.as_dict()
        i = pos_of.get(ev.t0)
        if i is None:
            row["状态"] = "未到（T0 尚无数据）"
            rows.append(row)
            positions.append(None)
            continue
        n_after = len(m) - 1 - i
        row["状态"] = ("完整" if n_after >= 20 else
                      "节前已知，未复牌" if n_after == 0 else f"节后仅 {n_after} 日")
        for col in he.RETURN_COLS + he.RISK_COLS + he.VOLUME_COLS:
            row[col] = m.at[i, col]
        if sh is not None:
            row["上证节前量比"] = sh.at[i, "节前量比"]
            row["上证节后量比"] = sh.at[i, "节后量比"]
        for p, r in rs.items():
            for col in he.RETURN_COLS:
                row[f"RS_{NAMES.get(p, p)}_{col}"] = r.at[i, col]
        rows.append(row)
        positions.append(i)
    ev_df = pd.DataFrame(rows)

    base = m[in_sample].copy()
    known = ev_df[ev_df["状态"] == "完整"]  # 汇总只用已走完 T+20 的历史事件
    summary = he.summarize(known, base, he.RETURN_COLS + he.RISK_COLS + he.VOLUME_COLS, n_boot)

    rs_rows = []
    for p, r in rs.items():
        tmp_ev = known[["类型", "年份"]].copy()
        cols = []
        for col in he.RETURN_COLS:
            key = f"RS_{NAMES.get(p, p)}_{col}"
            tmp_ev[col] = known[key]
            cols.append(col)
        s = he.summarize(tmp_ev, r[in_sample], cols, n_boot)
        s.insert(0, "代理", NAMES.get(p, p))
        rs_rows.append(s)
    if sh is not None:
        tmp_ev = known[["类型", "年份"]].copy()
        tmp_ev["节前量比"], tmp_ev["节后量比"] = known["上证节前量比"], known["上证节后量比"]
        s = he.summarize(tmp_ev, sh[in_sample], he.VOLUME_COLS, n_boot)
        s.insert(0, "代理", "上证指数成交量")
        rs_rows.append(s)
    rs_summary = pd.concat(rs_rows, ignore_index=True) if rs_rows else pd.DataFrame()

    # 平均路径
    valid = [(k, i) for k, i in zip(ev_df.index, positions) if i is not None]
    paths = he.cum_paths(main_df, [i for _, i in valid])
    paths.index = [k for k, _ in valid]
    path_tbl = {}
    for g in he.GROUPS:
        mask = he.group_mask(ev_df.loc[paths.index], g)
        done = ev_df.loc[paths.index, "状态"] == "完整"
        path_tbl[g] = paths[mask & done].mean()
    path_tbl["基准(全部交易日)"] = he.baseline_path(main_df, in_sample)
    gem = "sz399006" if "sz399006" in frames else None
    if gem:
        gp = he.cum_paths(he.align_to(main_df, frames[gem]), [i for _, i in valid])
        gp.index = paths.index
        rsp = gp - paths
        done = ev_df.loc[paths.index, "状态"] == "完整"
        path_tbl["RS创业板-全部休市"] = rsp[done].mean()
        gem_al = he.align_to(main_df, frames[gem])
        path_tbl["RS创业板-基准"] = he.baseline_path(gem_al, in_sample) - path_tbl["基准(全部交易日)"]
    live = ev_df[ev_df["状态"] == "节前已知，未复牌"]
    live_paths = {}
    for k in live.index:
        live_paths[f"{ev_df.at[k, '年份']}{ev_df.at[k, '节日']}(进行中)"] = paths.loc[k]
        if gem:
            live_paths[f"RS创业板-{ev_df.at[k, '年份']}{ev_df.at[k, '节日']}(进行中)"] = rsp.loc[k]
    path_df = pd.DataFrame({**path_tbl, **live_paths})
    path_df.index.name = "k(T0=0)"

    return {
        "events": ev_df, "summary": summary, "rs_summary": rs_summary, "paths": path_df,
        "metrics": m, "rs": rs, "sh": sh, "baseline": base, "in_sample": in_sample,
        "last": last, "days": days,
    }


def current_state(res, frames, main: str, proxies) -> pd.DataFrame:
    """最新交易日视作 T0：近 5 / 10 日表现，与历史节前分布及基准比较分位。"""
    m, ev = res["metrics"], res["events"]
    i = len(m) - 1
    hist = ev[(ev["状态"] == "完整")]
    rows = []

    def add(name, col_key, value, hist_series, base_series):
        rows.append({
            "项目": name, "指标": col_key, "当前值": value,
            "历史节前均值": hist_series.mean(), "历史节前中位数": hist_series.median(),
            "在历史节前中的分位%": he.percentile_of(value, hist_series),
            "基准均值": base_series.mean(),
            "在全部交易日中的分位%": he.percentile_of(value, base_series),
        })

    for col in ["节前5日%", "节前10日%", "T0当日%"]:
        add(NAMES.get(main, main), col, m.at[i, col], hist[col], res["baseline"][col])
    add(NAMES.get(main, main), "节前量比", m.at[i, "节前量比"], hist["节前量比"], res["baseline"]["节前量比"])
    if res["sh"] is not None:
        add("上证指数成交量", "节前量比", res["sh"].at[i, "节前量比"], hist["上证节前量比"],
            res["sh"]["节前量比"][res["in_sample"]])
    for p, r in res["rs"].items():
        for col in ["节前5日%", "节前10日%"]:
            key = f"RS_{NAMES.get(p, p)}_{col}"
            add(f"{NAMES.get(p, p)}−{NAMES.get(main, main)}", col, r.at[i, col], hist[key],
                r[col][res["in_sample"]])
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def _f(v, nd=2, sign=True):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    if isinstance(v, (int, np.integer)):
        return str(v)
    return f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"


def md_table(df: pd.DataFrame, fmt=None) -> str:
    fmt = fmt or {}
    head = "| " + " | ".join(map(str, df.columns)) + " |"
    sep = "|" + "|".join("---" for _ in df.columns) + "|"
    lines = [head, sep]
    for _, r in df.iterrows():
        cells = []
        for c in df.columns:
            v = r[c]
            if c in fmt:
                cells.append(fmt[c](v))
            elif isinstance(v, (float, np.floating)):
                cells.append(_f(float(v)))
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def plot(path_df: pd.DataFrame, out_png: Path, main_name: str) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    plt.rcParams["font.sans-serif"] = ["Hiragino Sans GB", "STHeiti", "Arial Unicode MS",
                                       "PingFang SC", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(2, 1, figsize=(10, 8.5), sharex=True)
    x = path_df.index.to_numpy()
    style = {"国庆(单独)": ("#d62728", "-"), "中秋(单独)": ("#1f77b4", "-"),
             "合并": ("#9467bd", "-"), "基准(全部交易日)": ("#555555", "--")}
    for col, (color, ls) in style.items():
        if col in path_df:
            axes[0].plot(x, path_df[col], color=color, ls=ls, lw=2, label=col)
    for col in path_df.columns:
        if col.endswith("(进行中)") and not col.startswith("RS"):
            axes[0].plot(x, path_df[col], color="#ff7f0e", ls=":", lw=2.2, marker="o", ms=3, label=col)
    axes[0].axvline(0, color="#999", lw=1)
    axes[0].axvline(1, color="#ccc", lw=1, ls=":")
    axes[0].axhline(0, color="#999", lw=0.8)
    axes[0].set_ylabel("相对 T0 收盘累计涨跌 %")
    axes[0].set_title(f"{main_name} 节假日平均累计路径（T-10 ~ T+20，T0=休市前最后交易日）")
    axes[0].legend(fontsize=9)
    for col, color, ls in (("RS创业板-全部休市", "#2ca02c", "-"), ("RS创业板-基准", "#555555", "--")):
        if col in path_df:
            axes[1].plot(x, path_df[col], color=color, ls=ls, lw=2, label=col)
    for col in path_df.columns:
        if col.startswith("RS创业板-") and col.endswith("(进行中)"):
            axes[1].plot(x, path_df[col], color="#ff7f0e", ls=":", lw=2.2, marker="o", ms=3, label=col)
    axes[1].axvline(0, color="#999", lw=1)
    axes[1].axhline(0, color="#999", lw=0.8)
    axes[1].set_ylabel("创业板指 - 沪深300 累计 百分点")
    axes[1].set_xlabel("相对 T0 的交易日（T+1 = 复牌首日）")
    axes[1].set_title("风险偏好：创业板相对强弱（>0 成长/小盘跑赢）")
    axes[1].legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    plt.close(fig)
    return True


def write_report(res, cur, frames, missing, main, proxies, args, out_dir: Path, png_ok: bool):
    ev, summ, rss = res["events"], res["summary"], res["rs_summary"]
    main_name = NAMES.get(main, main)
    L = []
    L.append(f"# 国庆 / 中秋节假日效应回测（{main_name}）\n")
    L.append(f"- 生成时间：{pd.Timestamp.now():%Y-%m-%d %H:%M}（本机时间）")
    L.append(f"- 样本：{args.start}-01-01 ~ {res['last'].date()}（本地缓存最新交易日）")
    cov = []
    for code, df in frames.items():
        cov.append(f"{NAMES.get(code, code)} {code}: {df['日期'].min().date()} ~ {df['日期'].max().date()}")
    L.append("- 数据覆盖：" + "；".join(cov))
    if missing:
        L.append(f"- 缺失（未参与计算）：{', '.join(missing)}")
    L.append(f"- 命令：`python -m scripts.research.holiday_effect --start {args.start} --index {main}`\n")

    L.append("## 1. 事件列表（一次休市 = 一个事件）\n")
    show = ["年份", "节日", "类型", "T0", "T1", "休市自然日", "官方休市", "状态",
            "节前5日%", "T0当日%", "T1跳空%", "T1当日%", "节后5日%", "节后20日%", "节前量比"]
    gem_pre = f"RS_{NAMES.get('sz399006')}_节前5日%"
    gem_post = f"RS_{NAMES.get('sz399006')}_节后5日%"
    if gem_pre in ev:
        show += [gem_pre, gem_post]
    L.append(md_table(ev[[c for c in show if c in ev]],
                      {"节前量比": lambda v: _f(v, 2, False), "年份": str}))
    L.append("\n> 百分比列单位为 %；量比 = T-5..T0 均量 / T-25..T-6 均量；RS = 代理指数收益 − 主指数收益（百分点）。\n")

    L.append("## 2. 关键统计（全部休市 vs 基准）\n")
    key = ["节前10日%", "节前5日%", "T0当日%", "T1跳空%", "T1当日%", "节后5日%", "节后10日%",
           "节后20日%", "窗口最大回撤%", "窗口最大涨幅%", "节后10日最高%", "节后10日最低%", "节前量比", "节后量比"]
    t = summ[summ["分组"] == "全部休市"].set_index("指标").loc[key].reset_index()
    fmt = {"样本数": str, "胜率%": lambda v: _f(v, 1, False), "基准胜率%": lambda v: _f(v, 1, False),
           "p值": lambda v: _f(v, 3, False)}
    for c in ["均值", "中位数", "基准均值", "基准中位数"]:
        t[c] = [(_f(v, 2, False) if k in he.VOLUME_COLS else _f(v)) for k, v in zip(t["指标"], t[c])]
    L.append(md_table(t[["指标", "样本数", "均值", "中位数", "胜率%", "基准均值", "基准中位数", "基准胜率%", "均值差", "p值"]], fmt))
    L.append("\n> 胜率：收益类为 >0 占比；量比类为 <1（缩量）占比。基准 = 样本期内每个交易日都当作 T0 的同口径分布；"
             "p 值为自助抽样双侧近似，窗口重叠且事件少，仅供参考。\n")

    L.append("## 3. 分组对比（均值 / 胜率%）\n")
    rows = []
    for col in ["节前5日%", "T0当日%", "T1跳空%", "T1当日%", "节后5日%", "节后10日%", "节后20日%", "节前量比"]:
        r = {"指标": col}
        for g in he.GROUPS:
            s = summ[(summ["分组"] == g) & (summ["指标"] == col)].iloc[0]
            sg = col not in he.VOLUME_COLS
            r[g] = f"{_f(s['均值'], 2, sg)} / {_f(s['胜率%'], 0, False)}（n={s['样本数']}）"
        b = summ[summ["指标"] == col].iloc[0]
        r["基准"] = f"{_f(b['基准均值'], 2, sg)} / {_f(b['基准胜率%'], 0, False)}"
        rows.append(r)
    L.append(md_table(pd.DataFrame(rows)))
    L.append("")

    L.append(f"## 4. 风险偏好代理（相对{main_name}）\n")
    if not rss.empty:
        for grp in ("全部休市", "全部(剔除2024)"):
            rows = []
            for proxy, g in rss[rss["分组"] == grp].groupby("代理", sort=False):
                gi = g.set_index("指标")
                vol = proxy == "上证指数成交量"
                r = {"代理": proxy}
                for col in he.RETURN_COLS + he.VOLUME_COLS:
                    if col in gi.index:
                        s = gi.loc[col]
                        r[col] = f"{_f(s['均值'], 2, not vol)} / {_f(s['胜率%'], 0, False)}"
                        r[col + "·基准"] = _f(s["基准均值"], 2, not vol)
                    else:
                        r[col] = r[col + "·基准"] = "—"
                rows.append(r)
            tbl = pd.DataFrame(rows)
            keep = ["代理"] + [c for c in ["节前10日%", "节前10日%·基准", "节前5日%", "节前5日%·基准",
                                           "T0当日%", "T1跳空%", "T1当日%", "节后5日%", "节后5日%·基准",
                                           "节后10日%", "节后20日%", "节前量比", "节后量比"] if c in tbl]
            L.append(f"### {grp}（n={int(rss[(rss['分组'] == grp)]['样本数'].max())}）\n")
            L.append(md_table(tbl[keep]))
            L.append("")
        L.append("> 单元格：均值（百分点）/ 代理跑赢主指数的占比%；·基准 = 全部交易日同口径均值；"
                 "成交量行：量比均值 / 缩量(<1)占比%。\n")
        L.append("### 自动判读\n")
        for proxy, g in rss[rss["分组"] == "全部休市"].groupby("代理", sort=False):
            gi = g.set_index("指标")
            if "节前5日%" not in gi.index:
                continue
            pre, post = gi.loc["节前5日%"], gi.loc["节后5日%"]
            pre_txt = "节前偏去风险" if pre["均值"] < 0 and pre["胜率%"] < 50 else (
                "节前未见明显去风险" if pre["均值"] >= 0 else "节前均值偏弱但胜率不一致")
            post_txt = "节后偏再风险" if post["均值"] > 0 and post["胜率%"] > 50 else (
                "节后未见明显再风险" if post["均值"] <= 0 else "节后均值偏强但胜率不一致")
            L.append(f"- {proxy}：节前5日相对强弱均值 {_f(pre['均值'])}pp（跑赢 {_f(pre['胜率%'], 0, False)}%，"
                     f"基准 {_f(pre['基准均值'])}pp，p={_f(pre['p值'], 3, False)}）→ {pre_txt}；"
                     f"节后5日 {_f(post['均值'])}pp（跑赢 {_f(post['胜率%'], 0, False)}%，基准 {_f(post['基准均值'])}pp，"
                     f"p={_f(post['p值'], 3, False)}）→ {post_txt}。")
        L.append("")

    L.append(f"## 5. 当前状态（以 {res['last'].date()} 为 T0 视角）\n")
    fmt = {"在历史节前中的分位%": lambda v: _f(v, 0, False), "在全部交易日中的分位%": lambda v: _f(v, 0, False)}
    cur_fmt = cur.copy()
    for c in ["当前值", "历史节前均值", "历史节前中位数", "基准均值"]:
        cur_fmt[c] = [(_f(v, 2, False) if k in he.VOLUME_COLS else _f(v)) for k, v in zip(cur["指标"], cur[c])]
    L.append(md_table(cur_fmt, fmt))
    upcoming = ev[ev["状态"].isin(["未到（T0 尚无数据）", "节前已知，未复牌"])]
    if len(upcoming):
        L.append("\n进行中 / 即将到来的休市：")
        for _, r in upcoming.iterrows():
            gap = int(((res["days"] > res["last"]) & (res["days"] <= pd.Timestamp(r["T0"]))).sum())
            L.append(f"- {r['年份']}{r['节日']}：T0={r['T0']}，T1={r['T1']}，状态={r['状态']}，"
                     f"距 T0 还有 {gap} 个交易日（按交易日历）")
    L.append("")

    if png_ok:
        L.append("## 6. 平均路径图\n")
        L.append("![节假日平均路径](holiday_effect_paths.png)\n")

    L.append("## 注意事项\n")
    L.append("- 休市事件数少（每组个位数），均值易被单一年份主导（如 2024 年 9 月底政策行情），务必同时看中位数和胜率。")
    L.append("- 指数缓存只有成交量（手），没有成交额，量比用成交量口径。")
    L.append("- 基准窗口两两重叠，p 值偏乐观；未做多重检验校正。")
    L.append("- 历史统计关联，不构成投资建议。")
    (out_dir / "holiday_effect_report.md").write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="国庆/中秋节假日效应回测")
    ap.add_argument("--start", type=int, default=2017, help="起始年份（默认 2017）")
    ap.add_argument("--index", default="sh000300", help="主指数代码（默认 sh000300 沪深300）")
    ap.add_argument("--proxies", default=DEFAULT_PROXIES,
                    help=f"风险偏好代理指数，逗号分隔（默认 {DEFAULT_PROXIES}）")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录")
    ap.add_argument("--no-fetch", action="store_true", help="只用本地缓存，缺的代理指数直接跳过")
    ap.add_argument("--bootstrap", type=int, default=5000, help="自助抽样次数（0 关闭 p 值）")
    args = ap.parse_args()

    proxies = [p.strip() for p in args.proxies.split(",") if p.strip() and p.strip() != args.index]
    frames, missing, _ = load_indexes(args.index, proxies, args.start, fetch=not args.no_fetch)
    proxies = [p for p in proxies if p in frames]
    res = build(frames, args.index, proxies, args.start, args.bootstrap)
    cur = current_state(res, frames, args.index, proxies)

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = PROJECT_DIR / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    res["events"].to_csv(out_dir / "holiday_events.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    res["summary"].to_csv(out_dir / "holiday_summary.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    res["rs_summary"].to_csv(out_dir / "holiday_rs_summary.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    res["paths"].to_csv(out_dir / "holiday_paths.csv", encoding="utf-8-sig", float_format="%.4f")
    cur.to_csv(out_dir / "holiday_current_state.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    png_ok = plot(res["paths"], out_dir / "holiday_effect_paths.png", NAMES.get(args.index, args.index))
    write_report(res, cur, frames, missing, args.index, proxies, args, out_dir, png_ok)

    ev = res["events"]
    print(f"\n事件数：{len(ev)}（合并 {int((ev['类型'] == '合并').sum())}，"
          f"完整 {int((ev['状态'] == '完整').sum())}）")
    print(ev[["年份", "节日", "类型", "T0", "T1", "状态"]].to_string(index=False))
    print(f"\n✓ 输出目录：{out_dir}")
    if missing:
        print(f"⚠ 缺失指数：{missing}")


if __name__ == "__main__":
    main()
