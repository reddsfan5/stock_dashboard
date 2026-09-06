"""Local ETF identity metadata, independent of quote and replay prices."""
import json
from pathlib import Path

CACHE_FILE = Path(__file__).resolve().parents[1] / 'cache' / 'etf_names.json'


def load_names(path=CACHE_FILE):
    try:
        rows = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(rows, dict):
        return {}
    return {code: name for code, name in rows.items()
            if isinstance(code, str) and isinstance(name, str) and name.strip()
            and len(code) == 8 and code[:2] in ('sh', 'sz') and code[2:].isdigit()}


def save_names(names, path=CACHE_FILE):
    path = Path(path)
    merged = load_names(path)
    merged.update(names)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
