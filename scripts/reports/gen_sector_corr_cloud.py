#!/usr/bin/env python3
"""BOOTSTRAP_EXPAND_V1 — first import/run restores real generator from _gen_parts/z*.txt."""
from __future__ import annotations
import base64, pathlib, shutil, zlib
_ROOT = pathlib.Path(__file__).resolve().parent
_PARTS = _ROOT / "_gen_parts"
_SELF = pathlib.Path(__file__).resolve()
def _expand() -> bytes:
    parts = sorted(_PARTS.glob("z*.txt"))
    if not parts:
        raise ImportError(f"missing {_PARTS}/z*.txt")
    raw = "".join(p.read_text(encoding="utf-8") for p in parts)
    data = zlib.decompress(base64.b64decode(raw))
    _SELF.write_bytes(data)
    shutil.rmtree(_PARTS, ignore_errors=True)
    return data
_data = _expand()
exec(compile(_data, str(_SELF), "exec"), globals())
