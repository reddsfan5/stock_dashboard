"""Bootstrap: load last known-good gen_sector_corr_cloud from git history.

Temporary restore after a failed large-file push. Replace with the real module
once UX fixes are pushed from the Mac checkout.
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

_URL = (
    "https://raw.githubusercontent.com/reddsfan5/stock_dashboard/"
    "d64a092034d092bbe9c80c2844df1d8e0cf54cdc/"
    "scripts/reports/gen_sector_corr_cloud.py"
)
_src = urllib.request.urlopen(_URL, timeout=60).read().decode("utf-8")
exec(compile(_src, str(Path(__file__).resolve()), "exec"), globals())
