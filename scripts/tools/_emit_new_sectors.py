# -*- coding: utf-8 -*-
"""Emit 8 new five-layer sectors + SVG plates. Run from repo root."""
from __future__ import annotations
import html, json, pprint, importlib.util
from pathlib import Path
from copy import deepcopy

ROOT = Path(__file__).resolve().parents[2]
CONTENT = ROOT / "content" / "sectors"
OUT = ROOT / "scripts" / "services" / "static" / "sector-atlas"
ART = ROOT / "scripts" / "tools" / "render_sector_atlas_art.py"
CHECKED = "2026-09-18"
W, H = 1168, 784
BG, CARD, CARD2 = "#0b1b33", "#123056", "#183a66"
CYAN, CYAN_DIM, WHITE, MUTED, LINE = "#40c4ff", "#288cbe", "#ebf2ff", "#a0b4d2", "#326ea0"
FF = "Noto Sans CJK SC, PingFang SC, Hiragino Sans GB, sans-serif"

def esc(s): return html.escape(str(s))
def wrap(text, width=18):
    text = str(text).replace("\\n", "\n")
    lines = []
    for para in text.split("\n"):
        cur = ""
        for ch in para:
            if len(cur) >= width:
                lines.append(cur); cur = ch
            else:
                cur += ch
        lines.append(cur)
    return lines
def tspan(x, y, text, size=16, fill=MUTED, width=18):
    lh = int(size * 1.35)
    parts = "".join(f'<tspan x="{x}" y="{y+i*lh}">{esc(line)}</tspan>' for i, line in enumerate(wrap(text, width)))
    return f'<text font-size="{size}" fill="{fill}" font-family="{FF}">{parts}</text>'
def header(parts, spec):
    parts += [
        f'<rect x="36" y="28" width="174" height="34" rx="10" fill="{CARD2}" stroke="{CYAN_DIM}"/>',
        f'<text x="48" y="50" font-size="18" fill="{CYAN}" font-family="{FF}">{esc(spec["badge"])}</text>',
        f'<text x="230" y="48" font-size="28" fill="{WHITE}" font-family="{FF}">{esc(spec["title"])}</text>',
        f'<text x="230" y="78" font-size="16" fill="{MUTED}" font-family="{FF}">{esc(spec["subtitle"][:50])}</text>',
    ]
def footer(parts, hint):
    parts += [
        f'<line x1="36" y1="{H-42}" x2="{W-36}" y2="{H-42}" stroke="{LINE}"/>',
        f'<text x="36" y="{H-22}" font-size="15" fill="{MUTED}" font-family="{FF}">{esc(hint)}</text>',
    ]
def flow(spec):
    boxes, n, gap, top = spec["boxes"], len(spec["boxes"]), 16, 120
    bh, bw = H - 70 - top, (W - 72 - gap * (n - 1)) // n
    parts = [f'<rect width="{W}" height="{H}" fill="{BG}"/>']; header(parts, spec)
    for i, (ht, body) in enumerate(boxes):
        x0, y0 = 36 + i * (bw + gap), top
        parts += [
            f'<rect x="{x0}" y="{y0}" width="{bw}" height="{bh}" rx="16" fill="{CARD}" stroke="{CYAN_DIM}" stroke-width="2"/>',
            f'<rect x="{x0+14}" y="{y0+14}" width="38" height="28" rx="8" fill="{CARD2}" stroke="{CYAN}"/>',
            f'<text x="{x0+33}" y="{y0+34}" text-anchor="middle" font-size="16" fill="{CYAN}">{i+1:02d}</text>',
            f'<text x="{x0+14}" y="{y0+70}" font-size="20" fill="{WHITE}" font-family="{FF}">{esc(ht)}</text>',
            tspan(x0 + 14, y0 + 100, body, size=15, width=max(8, bw // 17)),
        ]
        if i:
            px, mid = 36 + (i - 1) * (bw + gap) + bw, y0 + bh // 2
            parts.append(f'<line x1="{px+2}" y1="{mid}" x2="{x0-2}" y2="{mid}" stroke="{CYAN_DIM}" stroke-width="3"/>')
    footer(parts, spec["hint"])
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">' + "".join(parts) + "</svg>\n"
def grid(spec):
    boxes, cols = spec["boxes"], spec.get("cols", 2)
    n, rows = len(boxes), (len(boxes) + cols - 1) // cols
    top, bottom, left, right, gx, gy = 118, H - 64, 36, W - 36, 16, 14
    bw, bh = (right - left - gx * (cols - 1)) // cols, (bottom - top - gy * (rows - 1)) // rows
    parts = [f'<rect width="{W}" height="{H}" fill="{BG}"/>']; header(parts, spec)
    for i, (ht, body) in enumerate(boxes):
        r, c = divmod(i, cols)
        x0, y0 = left + c * (bw + gx), top + r * (bh + gy)
        parts += [
            f'<rect x="{x0}" y="{y0}" width="{bw}" height="{bh}" rx="14" fill="{CARD}" stroke="{CYAN_DIM}" stroke-width="2"/>',
            f'<rect x="{x0}" y="{y0}" width="8" height="{bh}" fill="{CYAN}"/>',
            f'<text x="{x0+22}" y="{y0+36}" font-size="20" fill="{WHITE}" font-family="{FF}">{esc(ht)}</text>',
            tspan(x0 + 22, y0 + 64, body, size=15, width=max(10, bw // 16)),
        ]
    footer(parts, spec["hint"])
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">' + "".join(parts) + "</svg>\n"
def hub(spec):
    center, sats = spec["center"], spec["satellites"][:6]
    positions = [(80, 130), (W // 2 - 140, 130), (W - 360, 130), (80, H - 250), (W // 2 - 140, H - 250), (W - 360, H - 250)]
    cx, cy, cw, ch, sw, sh = W // 2, H // 2 + 20, 280, 160, 280, 110
    parts = [f'<rect width="{W}" height="{H}" fill="{BG}"/>']; header(parts, spec)
    for (ht, body), (sx, sy) in zip(sats, positions):
        parts += [
            f'<line x1="{sx+sw//2}" y1="{sy+sh//2}" x2="{cx}" y2="{cy}" stroke="{LINE}" stroke-width="2"/>',
            f'<rect x="{sx}" y="{sy}" width="{sw}" height="{sh}" rx="12" fill="{CARD}" stroke="{CYAN_DIM}" stroke-width="2"/>',
            f'<text x="{sx+16}" y="{sy+36}" font-size="18" fill="{WHITE}" font-family="{FF}">{esc(ht)}</text>',
            tspan(sx + 16, sy + 58, body, size=14, width=16),
        ]
    parts += [
        f'<rect x="{cx-cw//2}" y="{cy-ch//2}" width="{cw}" height="{ch}" rx="18" fill="{CARD2}" stroke="{CYAN}" stroke-width="3"/>',
        f'<text x="{cx}" y="{cy-18}" text-anchor="middle" font-size="22" fill="{CYAN}" font-family="{FF}">{esc(center[0])}</text>',
        f'<text x="{cx}" y="{cy+18}" text-anchor="middle" font-size="16" fill="{WHITE}" font-family="{FF}">{esc(center[1].replace(chr(10), " "))}</text>',
    ]
    footer(parts, spec["hint"])
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">' + "".join(parts) + "</svg>\n"
def render_svg(spec):
    return {"flow": flow, "grid": grid, "hub": hub}[spec["kind"]](spec)

def ch(cid, title, eyebrow, image, alt, lead, points, question, note):
    return {"id": cid, "title": title, "eyebrow": eyebrow, "image": image, "alt": alt, "lead": lead, "points": points, "question": question, "note": note}

def five_ch(rows):
    # rows: list of 5 dicts with title,alt,lead,points,question,note
    ids = [("national","01 / 国家","01-national.svg"),("society","02 / 社会","02-society.svg"),
           ("personal","03 / 个人","03-personal.svg"),("market","04 / 股市","04-market.svg"),
           ("industry","05 / 产业","05-industry.svg")]
    out = []
    for (cid, eb, img), r in zip(ids, rows):
        assert len(r["points"]) == 4
        out.append(ch(cid, r["title"], eb, img, r["alt"], r["lead"], r["points"], r["question"], r["note"]))
    return out

def plates5(name, nat, soc, per, mkt, ind):
    return [
        {"file":"01-national.svg","kind":"hub","badge":"01 · 国家","title":f"{name}·国家层","subtitle":nat[0],"center":nat[1],"satellites":nat[2],"hint":nat[3]},
        {"file":"02-society.svg","kind":"grid","badge":"02 · 社会","title":f"{name}·社会层","subtitle":soc[0],"boxes":soc[1],"hint":soc[2],"cols":2},
        {"file":"03-personal.svg","kind":"grid","badge":"03 · 个人","title":f"{name}·个人层","subtitle":per[0],"boxes":per[1],"hint":per[2],"cols":2},
        {"file":"04-market.svg","kind":"flow","badge":"04 · 股市","title":f"{name}·股市层","subtitle":mkt[0],"boxes":mkt[1],"hint":mkt[2],"orientation":"horizontal"},
        {"file":"05-industry.svg","kind":"flow","badge":"05 · 产业","title":f"{name}·产业层","subtitle":ind[0],"boxes":ind[1],"hint":ind[2],"orientation":"horizontal"},
    ]

def save(doc, plates):
    sid = doc["id"]
    (CONTENT / f"{sid}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / sid).mkdir(parents=True, exist_ok=True)
    for pl in plates:
        (OUT / sid / pl["file"]).write_text(render_svg(pl), encoding="utf-8")
    for f in (OUT / sid).glob("*.jpg"):
        f.unlink(missing_ok=True)
    return sid, plates

print("helpers ok", ROOT)
