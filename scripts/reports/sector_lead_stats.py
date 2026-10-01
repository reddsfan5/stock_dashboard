"""板块相关点云的统计内核：滚动 beta 残差、领先—滞后显著性（循环平移零分布 + BH-FDR）
与滚动样本外验证。

设计要点
--------
* 所有阈值来自 ``config/sector_corr_cloud.yaml``（缺省值见 ``DEFAULTS``）。
* 残差用 **滚动窗口 beta**，且 beta/alpha 只用 t-1 及以前的数据估计（无前视）。
* 领先统计量 ``T(a→b) = max_{lag∈1..L} |corr(a_t, b_{t+lag})|``（周频残差）。
  零分布：把跟随序列做循环平移（保留各自自相关），对同样的 1..L 滞后取最大值，
  因而“挑最优滞后”的搜索已计入；所有有序板块对的平移统计量汇总成一个池化零分布。
* 对全部有序板块对（候选对 × 方向）做 Benjamini–Hochberg FDR。
* 条件概率与边的滞后对齐：事件周 t（领先板块周残差处于高/低分位），
  结果 = 跟随板块第 t+lag 周的残差方向。每个事件对应唯一一周结果，窗口互不重叠。
* 要求条件概率方向与 xcorr 符号一致。
* 滚动验证：在训练段选边（阈值、滞后、方向、分位门槛都只用训练段），
  在随后的测试段统计命中率与相对基准的提升。
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_DIR / "config" / "sector_corr_cloud.yaml"

DEFAULTS: dict[str, Any] = {
    "start": "2022-01-01",
    "benchmark": {"code": "sh000300", "name": "沪深300", "fallback_codes": ["sh000001"], "min_coverage": 0.95},
    "beta": {"window": 120, "min_periods": 60},
    "sync": {"corr_thr": 0.42, "max_edges_per_node": 8, "min_year_confirm": 2},
    "lead": {
        "max_lag": 4,
        "xcorr_thr": 0.08,
        "asym": 0.015,
        "max_out": 4,
        "event_q": 0.80,
        "min_events": 15,
        "min_lift": 0.02,
        "min_weeks": 40,
        "fdr_alpha": 0.10,
        "null_min_shift": 8,
    },
    "oos": {"min_train_weeks": 104, "test_weeks": 26, "step_weeks": 26, "min_events": 6},
    "display": {
        "per_core_direction": 3,
        "focus_total": 12,
        "show_failed_default": False,
        "default_mode": "sync",
        "auto_rotate": True,
        "auto_rotate_speed": 0.55,
        "top_movers_labels": 8,
        "label_limit": 18,
        "color_clip_pct": 0.90,
        "min_ret_scale": 0.5,
        "pick_radius_px": 14,
        "pick_radius_touch_px": 28,
        "mobile_panel_vh": 42,
        "legend_open_mobile": False,
        "color_by": "community",
        "rotate_resume_ms": 1500,
        "focus_orbit_speed": 0.3,
        "auto_rotate_reduced_motion": False,
        "web_opacity_min": 0.07,
        "web_opacity_max": 0.6,
        "web_focus_dim": 0.12,
        "transition_ms": 1700,
        "reduced_motion_ms": 350,
        "arc_height": 0.22,
        "pulse_max": 160,
        "trail_max": 12,
        "suggest_neighbors": 3,
        "tour_step_ms": 4200,
        "default_period": 1,
        "replay_step_ms": 800,
        "replay_tween_ms": 650,
        "replay_loop": False,
        "halo_max_scale": 3.4,
        "halo_max_opacity": 0.62,
        "halo_up_color": "#ff4d4f",
        "halo_down_color": "#22d38a",
        "breathe_top_n": 8,
        "breathe_max": 16,
        "breathe_period_ms": 1600,
        "breathe_amp": 0.18,
        "edge_tint": True,
        "edge_tint_mix": 0.85,
    },
    "daily": {"replay_days": 20, "periods": [1, 5, 20], "max_abs_ret": 35.0, "min_members": 1},
    "graph": {
        "min_community_size": 3,
        "resolution": 1.0,
        "bridge_top_k": 8,
        "bridge_min_participation": 0.25,
        "name_min_share": 0.45,
        "name_overrides": {},
    },
    "stale_weeks": 2,
}


def _deep_merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: Path | str | None = None) -> dict:
    p = Path(path) if path else CONFIG_PATH
    extra: dict = {}
    if p.exists():
        import yaml

        extra = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return _deep_merge(DEFAULTS, extra)


# ---------------------------------------------------------------- residuals
def rolling_residualize(piv: pd.DataFrame, mkt: pd.Series, window: int, min_periods: int) -> pd.DataFrame:
    """resid_t = y_t - (alpha_{t-1} + beta_{t-1} * x_t)，beta/alpha 由截至 t-1 的滚动窗口估计。"""
    y = piv.astype(float)
    x = mkt.reindex(y.index).astype(float)
    xm = pd.DataFrame(np.repeat(x.values[:, None], y.shape[1], axis=1), index=y.index, columns=y.columns)
    valid = y.notna() & xm.notna()
    xv, yv = xm.where(valid), y.where(valid)

    def roll(d: pd.DataFrame) -> pd.DataFrame:
        return d.rolling(window, min_periods=min_periods).mean()

    mx, my, mxy, mxx = roll(xv), roll(yv), roll(xv * yv), roll(xv * xv)
    var = mxx - mx ** 2
    beta = (mxy - mx * my) / var.where(var > 1e-12)
    alpha = my - beta * mx
    return y - (alpha.shift(1) + beta.shift(1) * xm)


# ---------------------------------------------------------------- lagged correlation
def masked_corr(a: np.ndarray, b: np.ndarray, min_n: int) -> np.ndarray:
    """逐列对的 Pearson 相关（成对剔除 NaN）。返回 [i, j] = corr(a[:, i], b[:, j])。"""
    ma, mb = np.isfinite(a).astype(float), np.isfinite(b).astype(float)
    a0, b0 = np.where(ma > 0, a, 0.0), np.where(mb > 0, b, 0.0)
    n = ma.T @ mb
    sa, sb = a0.T @ mb, ma.T @ b0
    saa, sbb, sab = (a0 * a0).T @ mb, ma.T @ (b0 * b0), a0.T @ b0
    cov = n * sab - sa * sb
    va, vb = n * saa - sa ** 2, n * sbb - sb ** 2
    with np.errstate(invalid="ignore", divide="ignore"):
        c = cov / np.sqrt(va * vb)
    c[(n < min_n) | (va <= 1e-12) | (vb <= 1e-12) | ~np.isfinite(c)] = np.nan
    return c


def lag_stack(w: np.ndarray, max_lag: int, min_n: int, follower: np.ndarray | None = None) -> np.ndarray:
    """返回形如 (L, N, N) 的数组，[l-1, i, j] = corr(w_i[t], f_j[t+l])。"""
    f = w if follower is None else follower
    out = np.full((max_lag, w.shape[1], w.shape[1]), np.nan)
    for lag in range(1, max_lag + 1):
        out[lag - 1] = masked_corr(w[:-lag], f[lag:], min_n)
    return out


def best_lag(stack: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """每个有序对的最强滞后：返回 (xcorr, lag, |xcorr|)，对角线为 NaN。"""
    absx = np.where(np.isfinite(stack), np.abs(stack), -1.0)
    idx = absx.argmax(axis=0)
    xc = np.take_along_axis(stack, idx[None], axis=0)[0]
    lag = idx + 1
    mx = np.where(np.isfinite(xc), np.abs(xc), np.nan)
    np.fill_diagonal(xc, np.nan)
    np.fill_diagonal(mx, np.nan)
    return xc, lag, mx


def circular_null(w: np.ndarray, max_lag: int, min_shift: int, min_n: int) -> np.ndarray:
    """跟随序列循环平移 s 周后，对 1..L 滞后取 max|corr| 的池化零分布（全部有序对）。"""
    t = w.shape[0]
    shifts = range(min_shift + max_lag, t - min_shift + 1)
    off = ~np.eye(w.shape[1], dtype=bool)
    pool = []
    for s in shifts:
        f = np.roll(w, s, axis=0)
        st = lag_stack(w, max_lag, min_n, follower=f)
        mx = np.nanmax(np.where(np.isfinite(st), np.abs(st), np.nan), axis=0)
        v = mx[off]
        pool.append(v[np.isfinite(v)])
    return np.sort(np.concatenate(pool)) if pool else np.array([])


def perm_pvalues(obs: np.ndarray, pool: np.ndarray) -> np.ndarray:
    """p = (1 + #{null ≥ obs}) / (1 + |null|)。"""
    out = np.full(obs.shape, np.nan)
    ok = np.isfinite(obs)
    if not len(pool):
        return out
    ge = len(pool) - np.searchsorted(pool, obs[ok], side="left")
    out[ok] = (1.0 + ge) / (1.0 + len(pool))
    return out


def bh_qvalues(p: np.ndarray) -> np.ndarray:
    """Benjamini–Hochberg q 值（忽略 NaN；m = 有限 p 值个数）。"""
    q = np.full(p.shape, np.nan)
    ok = np.isfinite(p)
    pv = p[ok]
    m = len(pv)
    if not m:
        return q
    order = np.argsort(pv)
    ranked = pv[order] * m / np.arange(1, m + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    res = np.empty(m)
    res[order] = np.minimum(ranked, 1.0)
    q[ok] = res
    return q


# ---------------------------------------------------------------- events
def event_stats(a: np.ndarray, b: np.ndarray, lag: int, sign: int, *, thr_hi: float, thr_lo: float,
                t0: int = 0, t1: int | None = None) -> dict:
    """事件周 t∈[t0, t1)：a_t ≥ thr_hi（强）或 ≤ thr_lo（弱）；结果 y = b_{t+lag}（单周、互不重叠）。

    p_up = P(y>0 | 强)，p_dn = P(y<0 | 弱)；base_* 为同一时段全部有效周的无条件比例。
    顺向命中：强事件预测 sign，弱事件预测 -sign；基准 = 同样预测方向的无条件比例。
    """
    t_all = len(a)
    t1 = t_all if t1 is None else min(t1, t_all)
    idx = np.arange(t0, max(t0, min(t1, t_all - lag)))
    empty = {"n_up": 0, "n_dn": 0, "p_up": None, "p_dn": None, "base_up": None, "base_dn": None,
             "lift_up": None, "lift_dn": None, "n_events": 0, "hits": 0, "exp_hits": 0.0,
             "hit": None, "base": None, "lift": None}
    if not len(idx):
        return empty
    x, y = a[idx], b[idx + lag]
    ok = np.isfinite(x) & np.isfinite(y) & (y != 0)
    x, y = x[ok], y[ok]
    if not len(y):
        return empty
    base_up, base_dn = float(np.mean(y > 0)), float(np.mean(y < 0))
    up, dn = x >= thr_hi, x <= thr_lo
    n_up, n_dn = int(up.sum()), int(dn.sum())
    p_up = float(np.mean(y[up] > 0)) if n_up else None
    p_dn = float(np.mean(y[dn] < 0)) if n_dn else None
    pred_up = sign > 0
    hits = int(((y[up] > 0) == pred_up).sum() + ((y[dn] > 0) == (not pred_up)).sum())
    b_strong = base_up if pred_up else base_dn
    b_weak = base_dn if pred_up else base_up
    exp_hits = n_up * b_strong + n_dn * b_weak
    n_ev = n_up + n_dn
    return {
        "n_up": n_up, "n_dn": n_dn,
        "p_up": p_up, "p_dn": p_dn, "base_up": base_up, "base_dn": base_dn,
        "lift_up": None if p_up is None else p_up - base_up,
        "lift_dn": None if p_dn is None else p_dn - base_dn,
        "n_events": n_ev, "hits": hits, "exp_hits": float(exp_hits),
        "hit": hits / n_ev if n_ev else None,
        "base": exp_hits / n_ev if n_ev else None,
        "lift": (hits - exp_hits) / n_ev if n_ev else None,
    }


def direction_agrees(ev: dict, sign: int) -> bool:
    """跟涨/跟跌提升的方向必须与 xcorr 符号一致（正向：两者 ≥0；反向：两者 ≤0）。"""
    lu, ld = ev.get("lift_up"), ev.get("lift_dn")
    if lu is None or ld is None:
        return False
    return sign * lu >= 0 and sign * ld >= 0


# ---------------------------------------------------------------- selection
def select_edges(w: np.ndarray, names: list[str], cfg: dict, *, with_fdr: bool = True) -> list[dict]:
    """在给定周频残差矩阵上选领先边（阈值/分位/滞后只用该矩阵自身）。"""
    lc = cfg["lead"]
    max_lag, min_n = int(lc["max_lag"]), int(lc["min_weeks"])
    st = lag_stack(w, max_lag, min_n)
    xc, lag, mx = best_lag(st)
    pv = qv = None
    m_tests = int(np.isfinite(mx).sum())
    if with_fdr:
        pool = circular_null(w, max_lag, int(lc["null_min_shift"]), min_n)
        pv = perm_pvalues(mx, pool)
        qv = bh_qvalues(pv)
    q_lo, q_hi = 1 - float(lc["event_q"]), float(lc["event_q"])
    thr, asym = float(lc["xcorr_thr"]), float(lc["asym"])
    min_ev, min_lift = int(lc["min_events"]), float(lc["min_lift"])
    alpha = float(lc["fdr_alpha"])
    cand = []
    ii, jj = np.where(np.isfinite(mx) & (mx >= thr))
    for i, j in zip(ii, jj):
        rev = mx[j, i] if np.isfinite(mx[j, i]) else 0.0
        if mx[i, j] < rev + asym:
            continue
        a = w[:, i]
        fin = a[np.isfinite(a)]
        if len(fin) < min_n:
            continue
        sgn = 1 if xc[i, j] >= 0 else -1
        ev = event_stats(a, w[:, j], int(lag[i, j]), sgn,
                         thr_hi=float(np.quantile(fin, q_hi)), thr_lo=float(np.quantile(fin, q_lo)))
        if ev["n_up"] < min_ev or ev["n_dn"] < min_ev:
            continue
        if not direction_agrees(ev, sgn) or (ev["lift"] or 0) < min_lift:
            continue
        p = None if pv is None else float(pv[i, j])
        q = None if qv is None else float(qv[i, j])
        cand.append({
            "i": int(i), "j": int(j), "source": names[i], "target": names[j],
            "lag": int(lag[i, j]), "xcorr": float(xc[i, j]), "abs": float(mx[i, j]), "sign": sgn,
            "p_value": p, "q_value": q, "fdr_pass": bool(q is not None and q <= alpha),
            "m_tests": m_tests, "ev": ev,
            "thr_hi": float(np.quantile(fin, q_hi)), "thr_lo": float(np.quantile(fin, q_lo)),
        })
    cand.sort(key=lambda e: (not e["fdr_pass"], e["p_value"] if e["p_value"] is not None else 1.0, -e["abs"]))
    out_deg: dict[str, int] = {}
    kept = []
    for e in cand:
        if out_deg.get(e["source"], 0) >= int(lc["max_out"]):
            continue
        out_deg[e["source"]] = out_deg.get(e["source"], 0) + 1
        kept.append(e)
    return kept


def walk_forward(w: np.ndarray, names: list[str], cfg: dict) -> dict:
    """滚动样本外：训练段 [0, T) 选边 → 测试段 [T, T+test) 统计顺向命中。"""
    oc = cfg["oos"]
    t_all = w.shape[0]
    tr, te, step = int(oc["min_train_weeks"]), int(oc["test_weeks"]), int(oc["step_weeks"])
    per_edge: dict[tuple[str, str], dict] = {}
    groups = {k: {"n_events": 0, "hits": 0, "exp_hits": 0.0, "edges": 0} for k in ("all", "fdr", "nonfdr")}
    folds = []
    t_end = tr
    while t_end < t_all - 1:
        t_stop = min(t_end + te, t_all)
        sel = select_edges(w[:t_end], names, cfg, with_fdr=True)
        f_stat = {"train_weeks": t_end, "test_weeks": t_stop - t_end, "edges": len(sel),
                  "fdr_edges": sum(e["fdr_pass"] for e in sel), "n_events": 0, "hits": 0, "exp_hits": 0.0}
        for e in sel:
            ev = event_stats(w[:, e["i"]], w[:, e["j"]], e["lag"], e["sign"],
                             thr_hi=e["thr_hi"], thr_lo=e["thr_lo"], t0=t_end, t1=t_stop)
            if not ev["n_events"]:
                continue
            for g in ("all", "fdr" if e["fdr_pass"] else "nonfdr"):
                groups[g]["n_events"] += ev["n_events"]
                groups[g]["hits"] += ev["hits"]
                groups[g]["exp_hits"] += ev["exp_hits"]
                groups[g]["edges"] += 1
            f_stat["n_events"] += ev["n_events"]
            f_stat["hits"] += ev["hits"]
            f_stat["exp_hits"] += ev["exp_hits"]
            rec = per_edge.setdefault((e["source"], e["target"]), {"n_events": 0, "hits": 0, "exp_hits": 0.0, "folds": 0})
            rec["n_events"] += ev["n_events"]
            rec["hits"] += ev["hits"]
            rec["exp_hits"] += ev["exp_hits"]
            rec["folds"] += 1
        folds.append(f_stat)
        t_end += step

    def summarize(d: dict) -> dict:
        n = d["n_events"]
        return {**d, "exp_hits": round(d["exp_hits"], 2),
                "hit": round(d["hits"] / n, 4) if n else None,
                "base": round(d["exp_hits"] / n, 4) if n else None,
                "lift": round((d["hits"] - d["exp_hits"]) / n, 4) if n else None}

    return {
        "groups": {k: summarize(v) for k, v in groups.items()},
        "folds": [summarize(f) for f in folds],
        "per_edge": {k: summarize(v) for k, v in per_edge.items()},
    }


def build_lead_edges_sig(resid: pd.DataFrame, sync_corr: pd.DataFrame, cfg: dict) -> tuple[list[dict], dict]:
    """全样本选边 + FDR + 滚动样本外指标。返回 (lead_edges, lead_summary)。"""
    names = list(resid.columns)
    weekly = resid.resample("W-FRI").sum(min_count=2)
    weekly = weekly.loc[weekly.notna().any(axis=1)]
    w = weekly.values.astype(float)
    sel = select_edges(w, names, cfg, with_fdr=True)
    wf = walk_forward(w, names, cfg)
    min_oos = int(cfg["oos"]["min_events"])
    edges = []
    for e in sel:
        ev = e["ev"]
        oos = wf["per_edge"].get((e["source"], e["target"]))
        sync = sync_corr.loc[e["source"], e["target"]] if e["source"] in sync_corr.index else np.nan

        def r(v, k=3):
            return None if v is None else round(float(v), k)

        edges.append({
            "source": e["source"], "target": e["target"], "lag": e["lag"], "lag_unit": "week",
            "xcorr": round(e["xcorr"], 4), "abs": round(e["abs"], 4), "sign": e["sign"],
            "sync": None if not np.isfinite(sync) else round(float(sync), 4),
            "p_value": r(e["p_value"], 5), "q_value": r(e["q_value"], 4), "fdr_pass": e["fdr_pass"],
            "p_up": r(ev["p_up"]), "p_dn": r(ev["p_dn"]), "base_up": r(ev["base_up"]), "base_dn": r(ev["base_dn"]),
            "lift_up": r(ev["lift_up"]), "lift_dn": r(ev["lift_dn"]),
            "n_up": ev["n_up"], "n_dn": ev["n_dn"],
            "hit": r(ev["hit"]), "base": r(ev["base"]), "lift": r(ev["lift"]),
            "horizon": e["lag"], "horizon_unit": "week",
            "oos_n": oos["n_events"] if oos else 0,
            "oos_folds": oos["folds"] if oos else 0,
            "oos_hit": oos["hit"] if oos else None,
            "oos_base": oos["base"] if oos else None,
            "oos_lift": oos["lift"] if oos else None,
            "oos_ok": bool(oos and oos["n_events"] >= min_oos and (oos["lift"] or 0) > 0),
        })
    summary = {
        "weeks": int(w.shape[0]),
        "m_tests": sel[0]["m_tests"] if sel else int(np.isfinite(best_lag(lag_stack(w, int(cfg["lead"]["max_lag"]), int(cfg["lead"]["min_weeks"])))[2]).sum()),
        "fdr_alpha": float(cfg["lead"]["fdr_alpha"]),
        "candidates": len(edges),
        "fdr_pass": sum(e["fdr_pass"] for e in edges),
        "oos": wf["groups"],
        "oos_folds": wf["folds"],
        "oos_cfg": cfg["oos"],
    }
    return edges, summary
