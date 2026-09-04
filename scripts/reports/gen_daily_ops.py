#!/usr/bin/env python3
"""生成每日操盘清单页，并可选刷新板块强度快照。

日常节奏（缓存更新后）：
  1. python -m scripts.update_cache                 # 或 --only stocks,etfs,...
  2. 管线已含 watchlist_track；也可单独：
       python -m scripts.update_cache --only watchlist_track
  3. python -m scripts.reports.gen_daily_ops        # 本脚本：清单 + 可选板块快照
  4. 打开 http://127.0.0.1:8765/daily_ops.html 或工作台入口

也可：python -m scripts.reports.gen_daily_ops --skip-sector
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_DIR = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_DIR / "output"
CACHE_DIR = PROJECT_DIR / "cache"
STATUS_PATH = CACHE_DIR / "daily_update_status.json"
SECTOR_SNAPSHOT = CACHE_DIR / "sector_strength_snapshot.json"
OUT_HTML = OUTPUT_DIR / "daily_ops.html"
SH_TZ = ZoneInfo("Asia/Shanghai")


def _now_label() -> str:
    return datetime.now(tz=SH_TZ).strftime("%Y-%m-%d %H:%M")


def load_update_status() -> dict:
    if not STATUS_PATH.exists():
        return {"state": "missing", "ok": None, "stages": [], "message": "尚无 cache/daily_update_status.json"}
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"state": "error", "ok": False, "stages": [], "message": str(exc)}


def refresh_sector_snapshot(top_n: int = 15) -> dict:
    from scripts.services.watchlist import WatchlistService

    payload = WatchlistService().sector_strength(top_n=top_n)
    payload["generated_at"] = datetime.now(tz=SH_TZ).isoformat(timespec="seconds")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SECTOR_SNAPSHOT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def build_html(*, status: dict, sector: dict | None, generated_at: str) -> str:
    stages = status.get("stages") or []
    stage_rows = ""
    for stage in stages:
        ok = stage.get("ok")
        mark = "✓" if ok else "✗"
        color = "#087443" if ok else "#b42318"
        stage_rows += (
            f"<tr><td>{stage.get('name')}</td>"
            f"<td style='color:{color};font-weight:700'>{mark}</td>"
            f"<td>{stage.get('duration_seconds', '—')}</td>"
            f"<td>{_esc(stage.get('message') or '')}</td></tr>"
        )
    if not stage_rows:
        stage_rows = "<tr><td colspan='4' class='empty'>尚无阶段结果</td></tr>"

    sector_rows = ""
    sectors = (sector or {}).get("sectors") or []
    for row in sectors[:12]:
        chg = row.get("avg_change_pct")
        if chg is None:
            chg = row.get("change_pct")
        cls = "up" if (chg or 0) > 0 else ("down" if (chg or 0) < 0 else "")
        sector_rows += (
            f"<tr><td>{_esc(row.get('sector') or row.get('name') or '')}</td>"
            f"<td class='num {cls}'>{'' if chg is None else f'{chg:+.2f}'}%</td>"
            f"<td class='num'>{row.get('count') or row.get('n') or '—'}</td></tr>"
        )
    if not sector_rows:
        sector_rows = "<tr><td colspan='3' class='empty'>暂无板块强度快照（可去掉 --skip-sector 生成）</td></tr>"

    state = status.get("state") or "—"
    ok = status.get("ok")
    state_color = "#087443" if ok else ("#b42318" if ok is False else "#667085")
    target = status.get("target_date") or "—"
    updated = status.get("updated_at") or status.get("started_at") or "—"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>每日操盘清单</title>
<style>
:root{{--bg:#f3f5f8;--card:#fff;--line:#e3e8f0;--blue:#2563eb;--muted:#758096}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:#182033;font-size:14px}}
.header{{background:linear-gradient(135deg,#121b31,#1d3158);color:#fff;padding:20px 24px}}
.header h1{{font-size:22px}}.header .sub{{opacity:.75;font-size:12px;margin-top:6px;line-height:1.6}}
.wrap{{max-width:980px;margin:0 auto;padding:16px 18px 36px;display:grid;gap:14px}}
.panel{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}}
.panel h2{{font-size:15px;margin-bottom:10px}}
.checklist{{display:grid;gap:8px}}
.item{{display:flex;gap:10px;align-items:flex-start;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:#fafbfe;text-decoration:none;color:inherit}}
.item:hover{{border-color:#b7caf5;background:#f3f7ff}}
.item b{{display:block;font-size:14px}}.item span{{color:var(--muted);font-size:12px;line-height:1.5}}
.icon{{width:28px;text-align:center;font-size:18px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th,td{{padding:7px 6px;border-bottom:1px solid var(--line);text-align:left}}
th{{color:var(--muted)}}.num{{text-align:right;font-variant-numeric:tabular-nums}}
.up{{color:#d32f2f}}.down{{color:#159568}}.empty{{color:var(--muted);text-align:center}}
.meta{{color:var(--muted);font-size:12px;margin-bottom:8px}}
.cmds{{background:#0f172a;color:#e2e8f0;border-radius:10px;padding:12px 14px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;line-height:1.7;overflow:auto}}
a.top{{color:#9ec1ff;text-decoration:none;font-size:13px;margin-right:12px}}
</style>
</head>
<body>
<div class="header">
  <div style="max-width:980px;margin:0 auto;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:flex-end">
    <div>
      <h1>🗓️ 每日操盘清单</h1>
      <div class="sub">生成于 {generated_at}（上海）· 缓存更新后优先走完下列入口</div>
    </div>
    <div><a class="top" href="/index.html">工作台</a><a class="top" href="/symbol.html">标的上下文</a></div>
  </div>
</div>
<div class="wrap">
  <div class="panel">
    <h2>今日检查清单</h2>
    <div class="checklist">
      <a class="item" href="/dashboard.html"><div class="icon">🎛️</div><div><b>选股仪表盘</b><span>查看当日形态命中，感兴趣的代码点进标的上下文</span></div></a>
      <a class="item" href="/watchlist.html"><div class="icon">👀</div><div><b>观察池 / 次日跟踪</b><span>确认待买与次日收益；板块强度可在下方快照核对</span></div></a>
      <a class="item" href="/trading_trainer.html"><div class="icon">🎯</div><div><b>T+1 训练</b><span>对假设状态为「训练中」的标的做无剧透演练</span></div></a>
      <a class="item" href="/stock_journal.html"><div class="icon">📓</div><div><b>选股日记</b><span>把结论推进到「已笔记」，并挂到案例时间线</span></div></a>
      <a class="item" href="/symbol.html?code=sh600519"><div class="icon">📎</div><div><b>标的上下文</b><span>一页汇总选股命中、观察池、训练、日记与假设</span></div></a>
      <a class="item" href="/market_news.html"><div class="icon">📰</div><div><b>市场资讯</b><span>收盘后核对重要消息是否改变判断</span></div></a>
    </div>
  </div>

  <div class="panel">
    <h2>缓存更新状态</h2>
    <div class="meta">状态 <b style="color:{state_color}">{_esc(state)}</b> · 目标日 {_esc(str(target))} · 更新 {_esc(str(updated))}</div>
    <table>
      <thead><tr><th>阶段</th><th>结果</th><th>秒</th><th>说明</th></tr></thead>
      <tbody>{stage_rows}</tbody>
    </table>
  </div>

  <div class="panel">
    <h2>申万一级板块强度快照</h2>
    <div class="meta">市场日 {_esc(str((sector or {}).get('market_date') or '—'))} · 生成 {(sector or {}).get('generated_at') or '—'}</div>
    <table>
      <thead><tr><th>板块</th><th class="num">涨跌%</th><th class="num">样本</th></tr></thead>
      <tbody>{sector_rows}</tbody>
    </table>
  </div>

  <div class="panel">
    <h2>推荐命令</h2>
    <div class="cmds">python -m scripts.update_cache
python -m scripts.update_cache --only watchlist_track
python -m scripts.reports.gen_daily_ops
python -m scripts.serve start web   # 若 8765 未启动</div>
  </div>
</div>
</body>
</html>"""


def _esc(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def generate(*, skip_sector: bool = False, top_n: int = 15) -> Path:
    status = load_update_status()
    sector = None
    if not skip_sector:
        try:
            sector = refresh_sector_snapshot(top_n=top_n)
        except Exception as exc:  # noqa: BLE001
            sector = {"sectors": [], "message": f"板块快照失败: {exc}"}
    elif SECTOR_SNAPSHOT.exists():
        try:
            sector = json.loads(SECTOR_SNAPSHOT.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            sector = None
    generated_at = _now_label()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(
        build_html(status=status, sector=sector, generated_at=generated_at),
        encoding="utf-8",
    )
    print(f"✓ {OUT_HTML}")
    return OUT_HTML


def main():
    parser = argparse.ArgumentParser(description="生成每日操盘清单")
    parser.add_argument("--skip-sector", action="store_true", help="不刷新板块强度快照")
    parser.add_argument("--top-n", type=int, default=15, help="板块强度条数")
    args = parser.parse_args()
    generate(skip_sector=args.skip_sector, top_n=args.top_n)


if __name__ == "__main__":
    main()
