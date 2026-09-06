"""Read-only projections from the trainer's already-revealed state.

Replay links must never fall back to current-market data. A stale/expired session
fails closed. Journal cases use the existing source field to isolate session notes.
"""
from copy import deepcopy
from hashlib import sha256


def journal_source(state):
    return 'training:' + sha256(state['session_id'].encode()).hexdigest()[:24]


def context_state(trainer, params):
    state = trainer.state(params['training_session'][0])
    if (params.get('replay_date', [''])[0], params.get('replay_time', [''])[0]) != (state['date'], state['time']):
        raise ValueError('训练时钟已变化，请返回训练页重新打开此页面')
    code = params.get('code', [state['code']])[0]
    if code and code != state['code']:
        raise ValueError('训练上下文仅支持当前标的')
    return state


def journal_bars(state):
    bars = deepcopy(state['daily_bars'])
    for i, bar in enumerate(bars):
        previous = bar.get('pre_close') or (bars[i-1]['close'] if i else None)
        bar['change_pct'] = (bar['close'] / previous - 1) * 100 if previous else None
        for days in (5, 10, 20, 60):
            bar['ma' + str(days)] = sum(b['close'] for b in bars[i-days+1:i+1]) / days if i+1 >= days else None
    return dict(code=state['code'], name=state['name'], bars=bars, latest=bars[-1], days=len(bars))


def minute_payload(state):
    points = deepcopy(state['minute_points'])
    base = state['prev_close']
    for p in points:
        p['change_pct'] = (p['close'] / base - 1) * 100 if base else None
        p['direction'] = 1 if p['close'] >= p['open'] else -1
    last = points[-1]
    return dict(code=state['code'], name=state['name'], date=state['date'], dates=[state['date']],
        prev_close=base, points=points, summary=dict(last=last['close'], change_pct=last['change_pct'],
        high=max(p['high'] for p in points), low=min(p['low'] for p in points), open=points[0]['open'],
        volume=sum(p['volume'] for p in points), amount=sum(p['amount'] for p in points),
        intraday_volume_ratio=last.get('intraday_volume_ratio')))


def read_context(handler, route, params):
    state = context_state(handler.trainer, params)
    source = journal_source(state)
    if route in ('/api/journal/search', '/api/minute/search'):
        return [dict(code=state['code'], name=state['name'], latest_date=state['date'])]
    if route == '/api/journal/kline':
        return journal_bars(state)
    if route in ('/api/minute/data', '/api/minute/available'):
        date = params.get('date', [state['date']])[0]
        if date != state['date']:
            raise ValueError('训练跨页仅展示当前模拟交易日的分时')
        if route.endswith('/available'):
            return dict(available=bool(state['minute_points']), code=state['code'], date=date)
        return minute_payload(state)
    cases = None
    if route.startswith('/api/journal/') or route == '/api/symbol/context':
        cases = [c for c in handler.journal.list_cases(code=state['code']) if c.get('source') == source]
    if route == '/api/journal/cases':
        return cases
    if route == '/api/journal/entries':
        ids = {c['id'] for c in cases}
        return [e for e in handler.journal.list_entries(code=state['code'], include_deleted=True)
                if e['case_id'] in ids and e['market_date'] <= state['date']]
    if route == '/api/journal/due':
        return []
    if route == '/api/symbol/context':
        return dict(code=state['code'], name=state['name'], screen_hits=[],
            watchlist=dict(items=[]), tracks=dict(items=[]), training=dict(runs=[]),
            journal=dict(cases=cases), hypotheses=dict(items=[]), screen_to_trade=None,
            training_time=state['date'] + ' ' + state['time'])
    if route == '/api/news/day':
        return handler.news.day(state['date'], as_of=state['time'])
    if route == '/api/news/impacts':
        return dict(items=[], count=0)
    if route == '/api/market/context':
        return handler.market_context.context(state['date'], as_of=state['time'])
    raise ValueError('当前训练上下文不提供此数据，请返回训练页')
