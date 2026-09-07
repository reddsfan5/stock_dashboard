"""Persistent vendor memberships, observed history and reported business segments."""
from contextlib import contextmanager
import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / 'state' / 'classifications.sqlite3'


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def normalize(code):
    code = str(code).strip().lower()
    if re.fullmatch(r'\d{6}', code):
        code = ('bj' if code.startswith(('4','8','92')) else 'sh' if code.startswith(('6','5','9')) else 'sz') + code
    if not re.fullmatch(r'(sh|sz|bj)\d{6}', code):
        raise ValueError('请输入有效的证券代码')
    return code


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


class ClassificationRepository:
    def __init__(self, path=DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS security (code TEXT PRIMARY KEY, name TEXT);
            CREATE TABLE IF NOT EXISTS board (
              source TEXT, board_id TEXT, name TEXT NOT NULL, kind TEXT NOT NULL,
              PRIMARY KEY(source,board_id));
            CREATE TABLE IF NOT EXISTS membership (
              code TEXT, source TEXT, board_id TEXT, observed_from TEXT, observed_to TEXT,
              evidence TEXT, source_url TEXT, PRIMARY KEY(code,source,board_id,observed_from));
            CREATE INDEX IF NOT EXISTS membership_lookup ON membership(code,observed_to);
            CREATE TABLE IF NOT EXISTS segment (
              code TEXT, period TEXT, dimension TEXT, item TEXT, revenue REAL, revenue_pct REAL,
              cost REAL, profit REAL, profit_pct REAL, gross_margin_pct REAL, fetched_at TEXT,
              source_url TEXT, PRIMARY KEY(code,period,dimension,item));
            CREATE TABLE IF NOT EXISTS sync_state (
              code TEXT, dataset TEXT, attempted_at TEXT, success_at TEXT, status TEXT, message TEXT,
              PRIMARY KEY(code,dataset));
            CREATE TABLE IF NOT EXISTS business_note (
              code TEXT PRIMARY KEY, payload TEXT, source_url TEXT, fetched_at TEXT);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def status(self, code, dataset, status, message='', db=None):
        stamp = now()
        def write(connection):
            connection.execute('''INSERT INTO sync_state VALUES(?,?,?,?,?,?)
             ON CONFLICT(code,dataset) DO UPDATE SET attempted_at=excluded.attempted_at,
             success_at=COALESCE(excluded.success_at,sync_state.success_at),status=excluded.status,message=excluded.message''',
             (code,dataset,stamp,stamp if status=='ok' else None,status,message))
        if db is not None:
            write(db)
        else:
            with self.connect() as connection:
                write(connection)

    def memberships(self, code, source, rows, url, stamp=None):
        code = normalize(code)
        stamp = stamp or now()
        if not rows:
            raise ValueError('数据源未返回板块，保留上次成功数据')
        with self.connect() as db:
            old = {r['board_id'] for r in db.execute('SELECT board_id FROM membership WHERE code=? AND source=? AND observed_to IS NULL',(code,source))}
            new = {str(r['id']) for r in rows}
            for key in old-new:
                db.execute('UPDATE membership SET observed_to=? WHERE code=? AND source=? AND board_id=? AND observed_to IS NULL',(stamp,code,source,key))
            for row in rows:
                key = str(row['id'])
                db.execute('INSERT INTO board VALUES(?,?,?,?) ON CONFLICT(source,board_id) DO UPDATE SET name=excluded.name,kind=excluded.kind',(source,key,row['name'],row['kind']))
                if key not in old:
                    db.execute('INSERT INTO membership VALUES(?,?,?,?,NULL,?,?)',(code,source,key,stamp,row.get('evidence',''),url))
            self.status(code,source,'ok',db=db)

    def save_segments(self, code, rows, url):
        code = normalize(code)
        if not rows:
            raise ValueError('未获取到主营构成披露，保留已有报告')
        stamp=now()
        cleaned=[]
        for row in rows:
            if str(row.get('SECURITY_CODE','')) != code[2:]:
                raise ValueError('主营构成返回证券代码不匹配')
            period=str(row.get('REPORT_DATE',''))[:10]
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',period) or not row.get('ITEM_NAME'):
                raise ValueError('主营构成字段不完整')
            dim={'1':'行业','2':'产品','3':'地区'}.get(str(row.get('MAINOP_TYPE')))
            if not dim:
                raise ValueError('未知主营构成分类，未覆盖原数据')
            def pct(key):
                value=number(row.get(key))
                return None if value is None else value*100
            cleaned.append((code,period,dim,row['ITEM_NAME'],number(row.get('MAIN_BUSINESS_INCOME')),pct('MBI_RATIO'),number(row.get('MAIN_BUSINESS_COST')),number(row.get('MAIN_BUSINESS_RPOFIT')),pct('MBR_RATIO'),pct('GROSS_RPOFIT_RATIO'),stamp,url))
        with self.connect() as db:
            # Replace only complete report/dimension groups present in this response.
            for period,dim in {(r[1],r[2]) for r in cleaned}:
                db.execute('DELETE FROM segment WHERE code=? AND period=? AND dimension=?',(code,period,dim))
            db.executemany('INSERT INTO segment VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',cleaned)
            self.status(code,'business','ok',db=db)

    def read(self, code):
        code=normalize(code)
        with self.connect() as db:
            boards=[dict(r) for r in db.execute('''SELECT b.*,m.observed_from,m.source_url,m.evidence FROM membership m JOIN board b USING(source,board_id)
             WHERE m.code=? AND m.observed_to IS NULL ORDER BY b.source,b.kind,b.name''',(code,))]
            segments=[dict(r) for r in db.execute('SELECT * FROM segment WHERE code=? ORDER BY period DESC,dimension,revenue DESC',(code,))]
            status=[dict(r) for r in db.execute('SELECT * FROM sync_state WHERE code=?',(code,))]
            note=db.execute('SELECT * FROM business_note WHERE code=?',(code,)).fetchone()
        return dict(code=code,boards=boards,segments=segments,sync=status,business_notes=json.loads(note['payload']) if note else [],concept_revenue_pct=None)

    def members(self, name):
        with self.connect() as db:
            rows=[dict(r) for r in db.execute('''SELECT DISTINCT m.code,b.source,b.name,COALESCE(s.name,m.code) AS security_name FROM membership m JOIN board b USING(source,board_id) LEFT JOIN security s ON s.code=m.code
             WHERE b.name=? AND m.observed_to IS NULL ORDER BY m.code LIMIT 1000''',(name,))]
            total=db.execute("SELECT COUNT(DISTINCT code) FROM sync_state WHERE dataset='eastmoney' AND success_at IS NOT NULL").fetchone()[0]
        return dict(items=rows,synced_symbols=total,complete=False)
