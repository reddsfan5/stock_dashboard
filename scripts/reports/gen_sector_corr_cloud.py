#!/usr/bin/env python3
"""申万二级板块相关点云生成器（运行时自解压）。"""
from __future__ import annotations
from pathlib import Path
import base64, zlib
_DIR = Path(__file__).resolve().parent / "_scc_payload"
_blob = "".join(p.read_text() for p in sorted(_DIR.glob("part_*.txt")))
_src = zlib.decompress(base64.b64decode(_blob))
exec(compile(_src, str(Path(__file__).resolve()), "exec"), globals())
