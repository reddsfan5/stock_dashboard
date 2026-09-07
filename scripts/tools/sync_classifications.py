"""python -m scripts.tools.sync_classifications --codes sh600118,sh601698
--all imports the locally covered stock universe; --concept-only omits financial reports.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from data.classifications import ClassificationRepository
from data.industry import StockInfo
from scripts.services.classification_sync import sync


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--codes',default='sh600118,sh601698,sh688568')
    parser.add_argument('--all',action='store_true')
    parser.add_argument('--concept-only',action='store_true')
    args=parser.parse_args()
    repo=ClassificationRepository();info=StockInfo().df
    with repo.connect() as db:
        db.executemany('INSERT OR REPLACE INTO security VALUES(?,?)',[(r['代码'],r['名称']) for r in info.to_dict('records')])
    for row in info.to_dict('records'):
        layers=[dict(id='sw'+str(i)+':'+row['申万'+str(i)+'级'],name=row['申万'+str(i)+'级'],kind='申万'+str(i)+'级') for i in (1,2,3) if isinstance(row.get('申万'+str(i)+'级'),str) and row['申万'+str(i)+'级']]
        if layers:repo.memberships(row['代码'],'shenwan',layers,'https://www.swsresearch.com/')
    codes=info['代码'].tolist() if args.all else args.codes.split(',')
    # Skip successful current-day entries when continuing a large sync.
    from datetime import datetime, timezone
    today=datetime.now(timezone.utc).date().isoformat()
    codes=[c for c in codes if not args.concept_only or not any(s['dataset']=='eastmoney' and (s['success_at'] or '').startswith(today) for s in repo.read(c)['sync'])]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(sync,code,repo,not args.concept_only):code for code in codes}
        for i,future in enumerate(as_completed(futures),1):
            data=future.result()
            if i%100==0 or len(codes)<10:print(f'{i}/{len(codes)} {data["code"]}: {len(data["boards"])} boards, {len(data["segments"])} segments',flush=True)
    with repo.connect() as db:
        print([tuple(r) for r in db.execute('SELECT dataset,status,COUNT(*) FROM sync_state GROUP BY dataset,status')],flush=True)


if __name__=='__main__':main()
