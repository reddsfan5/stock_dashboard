"""板块点云「异动观察」：按回放日挑出最值得看的 5–8 个板块，并给出一句话理由。

五类描述性信号（只用当日及之前的日收益；同步图的 ρ / 社区 / 桥梁为全样本结构估计）：
- contra   逆簇而行：板块方向与所在社区多数相反（社区上涨占比低而它涨，或反之）。
- decouple 和老搭档脱钩：长期高 ρ 的一对板块近 N 日累计收益背离，按两者历史 N 日价差的标准差标准化。
- streak   持续性：同向连涨/连跌且逐日放大；或连涨/连跌后的首个反向日。
- bridge   桥梁先动：桥梁板块大幅波动，远端社区是否在其后 1–2 日跟随（当日事件标记「观察中」）。
- disperse 簇内分化：社区中位数接近 0，但内部离散度高，点出领涨与领跌。
- style    风格切换：进攻−防御 等权价差，一方连续 ≥N 天占优后另一方首次反超；或价差为近期极值。

各类分数都折算成「当日横截面稳健 σ」的倍数，再乘类型权重后统一排序；参数见 config signals:。
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

SIGNAL_DEFAULTS: dict[str, Any] = {
    "max_items": 8,
    "min_items": 5,
    "relax": 0.7,             # 严格阈值下不足 min_items 时，阈值乘以该系数补足
    "pin_types": ["style"],   # 市场层面的信号优先占位（仍受每类上限约束）
    "sigma_floor": 0.35,      # 当日横截面稳健 σ（%）下限，避免极平静日放大噪声
    "history_days": 120,      # 生成器读取的日收益历史长度（供脱钩的历史价差），不嵌入页面
    "type_caps": {"contra": 2, "decouple": 2, "streak": 2, "bridge": 2, "disperse": 1, "style": 1},
    "weights": {"contra": 1.0, "decouple": 1.0, "streak": 0.9, "bridge": 1.0, "disperse": 0.9, "style": 1.1},
    "contra": {"min_comm": 4, "breadth": 0.35, "min_z": 1.2, "min_abs": 0.6},
    "decouple": {"min_rho": 0.6, "window": 5, "lookback": 60, "min_hist": 30, "min_z": 2.0, "min_gap": 2.0},
    "streak": {"min_len": 3, "min_abs": 0.5, "reversal_min_len": 3, "reversal_min_z": 1.2},
    "bridge": {"min_z": 1.5, "min_abs": 1.0, "follow_days": 2, "follow_min": 0.3, "decay": 0.8, "follow_bonus": 1.15},
    "disperse": {"min_comm": 5, "max_med": 0.4, "min_spread_z": 2.5, "min_tail": 1.0},
    "style": {"a": "进攻", "b": "防御", "min_days": 3, "min_abs": 0.3, "lookback": 60, "min_hist": 20, "extreme_z": 2.0, "extreme_abs": 0.8},
}
TYPE_ORDER = ["style", "contra", "decouple", "streak", "bridge", "disperse"]
TYPE_LABEL = {"contra": "逆簇", "decouple": "脱钩", "streak": "持续", "bridge": "桥梁", "disperse": "分化", "style": "风格"}


def _merge(base: dict, extra: dict | None) -> dict:
    out = dict(base)
    for k, v in (extra or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def _robust_sigma(x: np.ndarray, floor: float) -> float:
    v = x[np.isfinite(x)]
    if v.size < 5:
        return floor
    return max(floor, 1.4826 * float(np.median(np.abs(v - np.median(v)))))


def _pct(x: float, nd: int = 2) -> str:
    return f"{x:+.{nd}f}%"


def build_context(ids: list[str], graph: dict | None, edges: list[dict], cfg: dict, styles: dict | None = None) -> dict:
    """把同步图结构整理成索引形式（与日期无关）。"""
    graph = graph or {}
    pos = {s: i for i, s in enumerate(ids)}
    comms = []
    for c in graph.get("communities", []):
        idx = [pos[m] for m in c.get("members", []) if m in pos]
        if idx:
            comms.append({"id": c["id"], "name": c["name"], "idx": np.array(idx)})
    comm_of = {}
    for c in comms:
        for i in c["idx"]:
            comm_of[int(i)] = c
    cname = {c["id"]: c["name"] for c in comms}
    pairs = []
    adj_w: dict[int, dict[int, float]] = {}
    for e in edges:
        a, b = pos.get(e["source"]), pos.get(e["target"])
        if a is None or b is None:
            continue
        rho = float(e.get("corr", 0.0))
        if rho > 0:
            adj_w.setdefault(a, {})[b] = rho
            adj_w.setdefault(b, {})[a] = rho
        if rho >= cfg["decouple"]["min_rho"] and not e.get("soft"):
            pairs.append((a, b, rho))
    bridges = []
    for br in graph.get("bridges", []):
        i = pos.get(br["id"])
        if i is None or i not in comm_of:
            continue
        own = comm_of[i]
        # 远端社区：与桥梁连接强度 Σρ 最大的「其他」社区（纯结构，与日期无关）
        w: dict[int, float] = {}
        for j, rho in adj_w.get(i, {}).items():
            cj = comm_of.get(j)
            if cj is not None and cj["id"] != own["id"]:
                w[cj["id"]] = w.get(cj["id"], 0.0) + rho
        if not w:
            continue
        far_id = max(sorted(w), key=lambda k: w[k])
        far = next(c for c in comms if c["id"] == far_id)
        bridges.append({"i": i, "own": own, "far": far})
    sa, sb = cfg["style"]["a"], cfg["style"]["b"]
    tags = (styles or {}).get("tags", styles or {}) if styles else {}
    st_a = np.array([pos[k] for k, t in tags.items() if k in pos and t.get("p") == sa], dtype=int)
    st_b = np.array([pos[k] for k, t in tags.items() if k in pos and t.get("p") == sb], dtype=int)
    return {"ids": ids, "comms": comms, "comm_of": comm_of, "cname": cname, "pairs": pairs, "bridges": bridges,
            "st_a": st_a, "st_b": st_b}


def day_candidates(R: np.ndarray, dates: list[str], ctx: dict, cfg: dict, relax: float = 1.0) -> list[dict]:
    """R: (t+1)×N 日收益（%），最后一行即当日；只用到这些行，因此天然无未来数据。"""
    ids = ctx["ids"]
    t = R.shape[0] - 1
    r = R[t]
    sig = _robust_sigma(r, float(cfg["sigma_floor"]))
    W = cfg["weights"]
    out: list[dict] = []

    # 1. 逆簇而行
    c1 = cfg["contra"]
    for c in ctx["comms"]:
        if len(c["idx"]) < c1["min_comm"]:
            continue
        for i in c["idx"]:
            ri = r[i]
            if not np.isfinite(ri):
                continue
            oth = r[[j for j in c["idx"] if j != i]]
            oth = oth[np.isfinite(oth)]
            if oth.size < c1["min_comm"] - 1:
                continue
            med, br = float(np.median(oth)), float(np.mean(oth > 0))
            up = ri > 0 and br <= c1["breadth"]
            dn = ri < 0 and br >= 1 - c1["breadth"]
            z = (ri - med) / sig
            if (up or dn) and abs(z) >= c1["min_z"] * relax and abs(ri) >= c1["min_abs"] * relax:
                lead = "仅 " if up else " "
                out.append({"t": "contra", "id": ids[i], "p": [], "c": int(c["id"]), "d": int(np.sign(ri)),
                            "s": abs(z) * (0.5 + abs(br - 0.5)) * W["contra"],
                            "x": f"簇内{lead}{round(br * 100)}% 上涨（{c['name']} 中位 {_pct(med)}），它 {_pct(ri)}"})

    # 2. 和老搭档脱钩
    c2 = cfg["decouple"]
    w, L = int(c2["window"]), int(c2["lookback"])
    if t + 1 >= w + int(c2["min_hist"]):
        win = R[t - w + 1:t + 1]
        cum = (np.prod(1 + np.nan_to_num(win) / 100.0, axis=0) - 1) * 100.0
        valid = np.isfinite(win).sum(axis=0) >= w - 1
        hist = R[max(0, t - w - L + 1):t - w + 1]
        for a, b, rho in ctx["pairs"]:
            if not (valid[a] and valid[b]):
                continue
            dd = hist[:, a] - hist[:, b]
            dd = dd[np.isfinite(dd)]
            if dd.size < c2["min_hist"]:
                continue
            s5 = float(np.std(dd, ddof=1)) * np.sqrt(w)
            gap = float(cum[a] - cum[b])
            if s5 <= 0:
                continue
            z = gap / s5
            if abs(z) >= c2["min_z"] * relax and abs(gap) >= c2["min_gap"] * relax:
                p, q = (a, b) if abs(cum[a]) >= abs(cum[b]) else (b, a)
                out.append({"t": "decouple", "id": ids[p], "p": [ids[q]], "c": None, "d": int(np.sign(cum[p])),
                            "s": abs(z) * rho * W["decouple"],
                            "x": f"与 {ids[q]}（ρ{rho:.2f}）近{w}日背离 {abs(gap):.1f}%（{_pct(cum[p], 1)} / {_pct(cum[q], 1)}）"})

    # 3. 持续性
    c3 = cfg["streak"]
    for i in range(R.shape[1]):
        col = R[:, i]
        if not np.isfinite(col[t]) or col[t] == 0:
            continue
        sgn = np.sign(col[t])
        n = 0
        while n <= t and np.isfinite(col[t - n]) and np.sign(col[t - n]) == sgn:
            n += 1
        word = "涨" if sgn > 0 else "跌"
        if n >= c3["min_len"]:
            last3 = np.abs(col[t - 2:t + 1])
            if last3[0] < last3[1] < last3[2] and abs(col[t]) >= c3["min_abs"] * relax:
                tot = (np.prod(1 + col[t - n + 1:t + 1] / 100.0) - 1) * 100.0
                out.append({"t": "streak", "id": ids[i], "p": [], "c": None, "d": int(sgn), "sub": "grow",
                            "s": abs(col[t]) / sig * (1 + 0.15 * (n - c3["min_len"])) * W["streak"],
                            "x": f"连{word} {n} 天且逐日放大（{'→'.join(f'{v:+.1f}' for v in col[t - 2:t + 1])}%），累计 {_pct(tot, 1)}"})
        elif n == 1 and t >= 1:
            m = 0
            while m < t and np.isfinite(col[t - 1 - m]) and np.sign(col[t - 1 - m]) == -sgn:
                m += 1
            if m >= c3["reversal_min_len"] and abs(col[t]) / sig >= c3["reversal_min_z"] * relax \
                    and abs(col[t]) >= c3["min_abs"] * relax:
                prior = (np.prod(1 + col[t - m:t] / 100.0) - 1) * 100.0
                act = "首日反弹" if sgn > 0 else "首日回落"
                out.append({"t": "streak", "id": ids[i], "p": [], "c": None, "d": int(sgn), "sub": "rev",
                            "s": abs(col[t]) / sig * (0.7 + 0.1 * m) * W["streak"],
                            "x": f"连{'跌' if sgn > 0 else '涨'} {m} 天（{_pct(prior, 1)}）后{act} {_pct(col[t])}"})

    # 4. 桥梁先动（远端跟随只看事件日之后、且不晚于当日的数据）
    c4 = cfg["bridge"]
    fd = int(c4["follow_days"])
    for br in ctx["bridges"]:
        i = br["i"]
        for e in range(t, max(-1, t - fd - 1), -1):
            re_ = R[e, i]
            if not np.isfinite(re_):
                continue
            se = _robust_sigma(R[e], float(cfg["sigma_floor"]))
            z = re_ / se
            if abs(z) < c4["min_z"] * relax or abs(re_) < c4["min_abs"] * relax:
                continue
            own, far = br["own"]["name"], br["far"]["name"]
            head = f"桥接 {own}↔{far}"
            score = abs(z) * (c4["decay"] ** (t - e)) * W["bridge"]
            if e == t:
                x = f"{head}，今日 {_pct(re_)}；{far} 是否跟随：观察中"
            else:
                obs = t - e
                fm = R[e + 1:t + 1][:, br["far"]["idx"]]
                cum = float(np.nansum(np.nanmedian(fm, axis=1))) if np.isfinite(fm).any() else 0.0
                when = f"{int(dates[e][5:7])}月{int(dates[e][8:10])}日"
                if np.sign(cum) == np.sign(re_) and abs(cum) >= c4["follow_min"]:
                    score *= c4["follow_bonus"]
                    x = f"{head}，{when} 先动 {_pct(re_)}，{far} {obs} 日内跟随 {_pct(cum)}"
                elif obs < fd:
                    x = f"{head}，{when} 先动 {_pct(re_)}，{far} 观察中（{_pct(cum)}）"
                else:
                    x = f"{head}，{when} 先动 {_pct(re_)}，{far} 未跟随（{_pct(cum)}）"
            out.append({"t": "bridge", "id": ids[i], "p": [], "c": int(br["own"]["id"]), "d": int(np.sign(re_)),
                        "s": score, "x": x})
            break

    # 5. 簇内分化
    c5 = cfg["disperse"]
    for c in ctx["comms"]:
        idx = c["idx"]
        vals = r[idx]
        ok = np.isfinite(vals)
        if ok.sum() < c5["min_comm"]:
            continue
        v, ii = vals[ok], idx[ok]
        med = float(np.median(v))
        spread = float(np.percentile(v, 90) - np.percentile(v, 10))
        lead, lag = int(ii[np.argmax(v)]), int(ii[np.argmin(v)])
        if abs(med) <= c5["max_med"] / relax and spread / sig >= c5["min_spread_z"] * relax \
                and r[lead] >= c5["min_tail"] * relax and r[lag] <= -c5["min_tail"] * relax:
            out.append({"t": "disperse", "id": c["name"], "p": [ids[lead], ids[lag]], "c": int(c["id"]), "d": 0,
                        "s": spread / sig / 2 * W["disperse"],
                        "x": f"中位 {_pct(med)} 但内部分化：{ids[lead]} {_pct(r[lead])} / {ids[lag]} {_pct(r[lag])}"})
    # 6. 风格切换（进攻 − 防御 等权价差；只用截至当日的行）
    c6 = cfg["style"]
    if len(ctx.get("st_a", [])) and len(ctx.get("st_b", [])):
        sub = R[max(0, t - int(c6["lookback"]) - 40):t + 1]
        with np.errstate(all="ignore"):
            A = np.nanmean(sub[:, ctx["st_a"]], axis=1)
            B = np.nanmean(sub[:, ctx["st_b"]], axis=1)
        spr = A - B
        st = spr[-1]
        if np.isfinite(st) and st != 0:
            m = 0
            while m + 2 <= len(spr) and np.isfinite(spr[-2 - m]) and np.sign(spr[-2 - m]) == -np.sign(st):
                m += 1
            hist = spr[-1 - int(c6["lookback"]):-1]
            hist = hist[np.isfinite(hist)]
            z = st / float(np.std(hist, ddof=1)) if hist.size >= c6["min_hist"] and np.std(hist) > 0 else 0.0
            win, lose = (c6["a"], c6["b"]) if st > 0 else (c6["b"], c6["a"])
            parts, score = [], 0.0
            if m >= c6["min_days"] and abs(st) >= c6["min_abs"] * relax:
                parts.append(f"{lose}连续 {m} 天跑赢{win}后，{win}首次反超 {_pct(abs(st))}")
                score = max(abs(z), 1.0) * (1 + 0.1 * m)
            if abs(z) >= c6["extreme_z"] * relax and abs(st) >= c6["extreme_abs"] * relax:
                parts.append(f"价差 {_pct(st)} 为近{int(c6['lookback'])}日 {abs(z):.1f}σ")
                score = max(score, abs(z))
            if parts:
                tail = f"（{c6['a']} {_pct(A[-1])} / {c6['b']} {_pct(B[-1])}）"
                mood = "风险偏好升温" if st > 0 else "避险升温"
                out.append({"t": "style", "id": f"{c6['a']} vs {c6['b']}", "p": [], "c": None, "d": int(np.sign(st)),
                            "sub": "switch" if m >= c6["min_days"] else "extreme",
                            "s": score * W.get("style", 1.0), "x": "，".join(parts) + f"{tail}，{mood}"})
    for o in out:
        o["s"] = round(float(o["s"]), 3)
    return out


def _key(o: dict) -> str:
    if o["t"] == "decouple":
        return "decouple:" + "|".join(sorted([o["id"], *o["p"]]))
    if o["t"] == "disperse":
        return f"disperse:{o['c']}"
    if o["t"] == "streak":  # 连涨放大 与 反转 是不同事件，不连续计天
        return f"streak:{o.get('sub', '')}:{o['id']}"
    return f"{o['t']}:{o['id']}"


def select_day(strict: list[dict], loose: list[dict], cfg: dict) -> list[dict]:
    caps, cnt, used, picked = cfg["type_caps"], {}, set(), []
    order = {t: k for k, t in enumerate(TYPE_ORDER)}

    def take(pool: list[dict]) -> None:
        for o in sorted(pool, key=lambda o: (-o["s"], order[o["t"]], o["id"])):
            if len(picked) >= cfg["max_items"]:
                return
            names = {f"comm:{o['c']}"} if o["t"] == "disperse" else ({"style"} if o["t"] == "style" else {o["id"], *o["p"]})
            if cnt.get(o["t"], 0) >= caps.get(o["t"], 0) or names & used or _key(o) in {_key(p) for p in picked}:
                continue
            picked.append(o)
            used.update(names)
            cnt[o["t"]] = cnt.get(o["t"], 0) + 1

    pins = set(cfg.get("pin_types") or [])
    take([o for o in strict if o["t"] in pins])
    take(strict)
    if len(picked) < cfg["min_items"]:
        keys = {_key(o) for o in strict}
        take([dict(o, weak=True) for o in loose if _key(o) not in keys])
    return picked


def compute_signals(daily: pd.DataFrame, graph: dict | None, edges: list[dict], out_dates: list[str],
                    cfg: dict | None = None, styles: dict | None = None) -> dict:
    """daily: 交易日 × 板块的日收益（%）。对 out_dates 中每一天，只把截至当天的行交给 day_candidates。"""
    cfg = _merge(SIGNAL_DEFAULTS, cfg)
    ids = [str(c) for c in daily.columns]
    ctx = build_context(ids, graph, edges, cfg, styles)
    all_dates = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in daily.index]
    pos = {d: k for k, d in enumerate(all_dates)}
    R = daily.to_numpy(dtype=float)
    days, prev = [], {}
    for d in out_dates:
        t = pos.get(d)
        if t is None:
            days.append([])
            prev = {}
            continue
        sub, sd = R[:t + 1], all_dates[:t + 1]
        strict = day_candidates(sub, sd, ctx, cfg, 1.0)
        loose = day_candidates(sub, sd, ctx, cfg, float(cfg["relax"])) if len(strict) < cfg["min_items"] else []
        picked = select_day(strict, loose, cfg)
        cur = {}
        for o in picked:
            k = _key(o)
            o["n"] = prev.get(k, 0) + 1
            cur[k] = o["n"]
        prev = cur
        days.append(picked)
    return {"dates": list(out_dates), "days": days, "labels": TYPE_LABEL}
