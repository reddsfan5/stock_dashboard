#!/usr/bin/env python3
"""生成手机版导航页 output/mobile.html"""

import os, subprocess, json
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTPUT_DIR = os.path.join(PROJECT_DIR, "output")
MINUTE_VIEW_URL = "http://127.0.0.1:8765/minute_view.html"
GRID_SIMULATOR_URL = "http://127.0.0.1:8765/grid_simulator.html"
TRADING_TRAINER_URL = "http://127.0.0.1:8765/trading_trainer.html"
STOCK_JOURNAL_URL = "http://127.0.0.1:8765/stock_journal.html"


def get_mtime(fname):
    path = os.path.join(OUTPUT_DIR, fname)
    return datetime.fromtimestamp(os.path.getmtime(path)) if os.path.exists(path) else None


def card(icon, title, desc, href, mtime=None):
    t = mtime.strftime("%m/%d %H:%M") if mtime else ""
    return f"""<a href="{href}" class="card">
  <span class="icon">{icon}</span>
  <div class="info"><span class="title">{title}</span>
  <span class="desc">{desc}</span></div>
  <span class="arrow">›</span></a>"""


def generate():
    from scripts.services.ui_shell import mobile_redirect
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(os.path.join(OUTPUT_DIR, 'mobile.html'), 'w', encoding='utf-8') as handle:
        handle.write(mobile_redirect('index.html'))
    return

if __name__ == "__main__":
    generate()
