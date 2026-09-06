"""Small, public status projection; never expose pipeline tracebacks or paths."""
import json
import math
from pathlib import Path

STATUS_PATH = Path(__file__).resolve().parents[2] / 'cache' / 'daily_update_status.json'


def public_status(path=None):
    try:
        raw = json.loads(Path(path or STATUS_PATH).read_text(encoding='utf-8'))
        if not isinstance(raw, dict):
            raise ValueError('invalid status')
    except FileNotFoundError:
        return dict(state='missing', ok=None, target_date=None, updated_at=None, stages=[])
    except (OSError, ValueError):
        return dict(state='error', ok=False, target_date=None, updated_at=None, stages=[])
    stages = []
    for stage in raw.get('stages') if isinstance(raw.get('stages'), list) else []:
        if not isinstance(stage, dict):
            continue
        ok = stage.get('ok') if isinstance(stage.get('ok'), bool) else None
        duration = stage.get('duration_seconds')
        stages.append(dict(name=str(stage.get('name', ''))[:60], ok=ok,
                           duration_seconds=duration if isinstance(duration, (int, float)) and math.isfinite(duration) and duration >= 0 else None,
                           message='已完成' if ok is True else '未完成，请查看本机运行日志'))
    failed = any(s['ok'] is False for s in stages)
    state = raw.get('state') if raw.get('state') in ('running', 'success', 'failed', 'missing', 'error') else 'unknown'
    if failed and state == 'success':
        state = 'failed'
    return dict(state=state, ok=False if failed else raw.get('ok'),
                target_date=raw.get('target_date'), updated_at=raw.get('updated_at') or raw.get('started_at'), stages=stages)
