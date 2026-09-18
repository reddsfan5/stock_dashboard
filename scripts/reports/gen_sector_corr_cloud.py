from pathlib import Path as _Path
_parts = _Path(__file__).resolve().parent / "_corr_parts"
_src = "".join((_parts / f"{i}.txt").read_text(encoding="utf-8") for i in range(6))
exec(compile(_src, str(_Path(__file__).resolve()), "exec"), globals())
