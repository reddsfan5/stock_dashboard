"""Bounded, cached reads from Eastmoney F10, with no network calls in DB reads."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import requests
from data.classifications import ClassificationRepository, normalize, now

BASE='https://emweb.securities.eastmoney.com/PC_HSF10/'
_pool=ThreadPoolExecutor(max_workers=3)
_jobs={}
_lock=threading.Lock()


def fetch(kind, code):
    response=requests.get(BASE+kind+'/PageAjax',params={'code':code.upper()},timeout=(5,15))
    response.raise_for_status()
    result=response.json()
    if not isinstance(result,dict):
        raise ValueError('数据源格式变化')
    return result


def sync(code, repo=None, business=True):
    code=normalize(code);repo=repo or ClassificationRepository()
    for dataset,part in [('eastmoney','CoreConception')]+([('business','BusinessAnalysis')] if business else []):
        try:
            data=fetch(part,code)
            url=BASE+part+'/Index?code='+code.upper()
            if dataset=='eastmoney':
                rows=data.get('ssbk')
                if not isinstance(rows,list) or not rows:
                    raise ValueError('没有返回板块数据')
                if any(not isinstance(r,dict) or str(r.get('SECURITY_CODE'))!=code[2:] or not r.get('BOARD_CODE') or not r.get('BOARD_NAME') for r in rows):
                    raise ValueError('板块证券代码或字段不匹配')
                # F10 does not identify the taxonomy of every label. Preserve the mixed category.
                repo.memberships(code,'eastmoney',[dict(id=r['BOARD_CODE'],name=r['BOARD_NAME'],kind='数据商板块',evidence='东方财富F10所属板块') for r in rows],url)
                notes=data.get('hxtc',[])
                notes=[r for r in notes if isinstance(r,dict)] if isinstance(notes,list) else []
                with repo.connect() as db:
                    db.execute('INSERT OR REPLACE INTO business_note VALUES(?,?,?,?)',(code,json.dumps(notes,ensure_ascii=False),url,now()))
            else:
                repo.save_segments(code,data.get('zygcfx',[]),url)
        except (requests.RequestException,ValueError,KeyError,TypeError) as exc:
            repo.status(code,dataset,'error','源站暂不可用或未披露该数据；已保留缓存（'+type(exc).__name__+'）')
    return repo.read(code)


def pending(code):
    with _lock:
        return code in _jobs and not _jobs[code].done()


def enqueue(code, force=False):
    code=normalize(code)
    if code[2:].startswith(('5','1')):
        raise ValueError('ETF 不适用上市公司主营构成，请查询成分股')
    repo=ClassificationRepository()
    statuses=repo.read(code)['sync']
    # Failed attempts are cooled down too; manual retries are allowed after one minute.
    cooldown=60 if force else 86400
    recent=[s for s in statuses if s['dataset'] in ('eastmoney','business') and (datetime.now(timezone.utc)-datetime.fromisoformat(s['attempted_at'])).total_seconds()<cooldown]
    with _lock:
        if code in _jobs and not _jobs[code].done():
            return dict(code=code,pending=True)
        if len(recent)==2:
            return dict(code=code,pending=False)
        if sum(not future.done() for future in _jobs.values())>=24:
            raise ValueError('同步队列繁忙，请稍后重试')
        for key in list(_jobs):
            if _jobs[key].done():del _jobs[key]
        _jobs[code]=_pool.submit(sync,code,repo)
    return dict(code=code,pending=True)
